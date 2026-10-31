"""Importing lucent packages into lucentBASE: plan what would change, then apply it.

Both run the whole import in one transaction; a plan rolls it back. So a plan and the apply
that follows it can't disagree, and an apply with any problem changes nothing.

The rules:

- An entry is found by its natural key (see package.py), also when it is hidden; a hidden one
  is shown again.
- Only what an entry mentions changes. Fields it leaves out keep their value; a new entry gets
  the defaults.
- Nothing is deleted. A list an entry mentions (a person's roles, a unit's notations, an
  action's documents, devices, people and dependencies) is made exactly so: what's missing is
  added, what's extra is hidden.
- An action's effects are the method. Changing them needs a new iteration, which makes a new
  version beside the old one; a package that changes the effects of an existing iteration is
  refused.
- The rules of the endpoints hold here too: borders don't cross themselves, thresholds have a
  limit, nobody deactivates themselves, and an active admin remains.

Every write is logged by lucentBASE's triggers under the importing person, with a note such as
"Changed unit from a package: <reason>".
"""

import datetime as dt
from collections.abc import Callable

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import DBAPIError

from . import models as m
from . import people
from .db import set_editor, visible
from .geometry import as_text, crosses_itself, from_text, parse_polygon, polygon_wkt
from .package import Package
from .schema import (
    city,
    coefficient,
    crs,
    device,
    dimension,
    district,
    effect,
    house,
    j_action_action,
    j_action_device,
    j_action_link,
    j_action_person,
    j_person_organization,
    link,
    matrix,
    organization,
    parameter,
    person,
    raw_value,
    site,
    spot,
    street,
    threshold,
    unit,
    unit_notation,
    value,
)
from .schema import action as action_table

Path = tuple[str | int, ...]
MISSING = object()  # a reference that couldn't be resolved


def path_text(path: Path) -> str:
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text


def as_datetime(value: dt.date | dt.datetime) -> dt.datetime:
    return value if isinstance(value, dt.datetime) else dt.datetime.combine(value, dt.time())


def same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and float(a) == float(b)
    return a == b


class Importer:
    def __init__(self, conn: Connection, editor_id: int, reason: str | None):
        self.conn, self.editor_id, self.reason = conn, editor_id, reason
        self.changes: list[m.PackageChange] = []
        self.problems: list[tuple[Path, str]] = []
        self.summary: dict[str, dict[str, int]] = {}
        self.actions: dict[tuple[str, dt.datetime], int] = {}

    # Bookkeeping

    def problem(self, path: Path, message: str) -> None:
        self.problems.append((path, message))

    def record(self, section: str, key: str, created: bool, fields: list[str]) -> None:
        change = "create" if created else "update" if fields else "unchanged"
        counts = self.summary.setdefault(section, {"create": 0, "update": 0, "unchanged": 0})
        counts[change] += 1
        if change != "unchanged":
            self.changes.append(m.PackageChange(section=section, key=key, change=change,
                                                fields=[] if created else fields))

    def note(self, what: str) -> None:
        set_editor(self.conn, self.editor_id, people.note(f"{what} from a package", self.reason))

    def result(self, applied: bool, line_of: Callable[[Path], int | None] = lambda _: None) -> m.PackagePlan:
        return m.PackagePlan(applied=applied, summary=self.summary, changes=self.changes,
                             problems=[m.PackageProblem(path=path_text(path), line=line_of(path), message=message)
                                       for path, message in self.problems])

    # Rows

    def find(self, table, key: dict) -> dict | None:
        query = select(table).where(*[table.c[column] == v for column, v in key.items()])
        row = self.conn.execute(query).mappings().first()
        return None if row is None else dict(row)

    def upsert(self, table, key: dict, fields: dict[str, tuple[str, object]], given: set[str]) -> tuple[int, bool, list[str]]:
        """The row with `key`, created or changed: `fields` maps a package field to (column, value); only
        the `given` ones change an existing row. Returns its id, whether it was created and what changed."""
        id_column = f"id_{table.name}"
        row = self.find(table, key)
        if row is None:
            self.note("Created")
            values = {**key, **{column: v for column, v in fields.values()}, "vis": True}
            return self.conn.execute(insert(table).values(values)).inserted_primary_key[0], True, []
        labels = [name for name, (column, v) in fields.items() if name in given and not same(row[column], v)]
        changes = {fields[name][0]: fields[name][1] for name in labels}
        if not row["vis"]:
            changes["vis"] = True
            labels.append("shown again")
        if changes:
            self.note("Changed " + ", ".join(labels))
            self.conn.execute(update(table).where(table.c[id_column] == row[id_column]).values(changes))
        return row[id_column], False, labels

    def sync(self, table, owner: dict, desired: list[dict], extra: tuple[str, ...] = ()) -> bool:
        """Makes the visible rows of a junction table for `owner` exactly `desired`: each a dict of the
        columns that identify a row, plus the `extra` columns it carries. Returns whether anything changed."""
        id_column = f"id_{table.name}"
        rows = [dict(r) for r in self.conn.execute(
            select(table).where(*[table.c[c] == v for c, v in owner.items()]).order_by(table.c[id_column])).mappings()]
        identity = lambda values: tuple(sorted((k, v) for k, v in values.items() if k not in extra and k not in owner))
        by_identity = {}
        for row in rows:
            by_identity.setdefault(identity({k: row[k] for k in row if k not in ("vis", id_column)}), row)
        changed, wanted = False, set()
        for item in desired:
            wanted.add(identity(item))
            row = by_identity.get(identity(item))
            if row is None:
                self.note("Linked")
                self.conn.execute(insert(table).values({**owner, **item, "vis": True}))
                changed = True
                continue
            changes = {k: item[k] for k in extra if k in item and not same(row[k], item[k])}
            if not row["vis"]:
                changes["vis"] = True
            if changes:
                self.note("Linked" if "vis" in changes else "Changed")
                self.conn.execute(update(table).where(table.c[id_column] == row[id_column]).values(changes))
                changed = True
        for key, row in by_identity.items():
            if key not in wanted and row["vis"]:
                self.note("Unlinked")
                self.conn.execute(update(table).where(table.c[id_column] == row[id_column]).values(vis=False))
                changed = True
        return changed

    def lookup(self, table, column="name") -> dict:
        return {row[0]: row[1] for row in self.conn.execute(
            select(table.c[column], table.c[f"id_{table.name}"]).where(visible(table)))}

    def ref(self, mapping: dict, name, what: str, path: Path):
        """The id `name` refers to, None for no reference, or MISSING (and a problem)."""
        if name is None:
            return None
        found = mapping.get(name)
        if found is None:
            self.problem(path, f"There is no {what} {name!r}, neither in lucentBASE nor earlier in the package.")
            return MISSING
        return found

    def duplicates(self, section: str, keys: list) -> set[int]:
        seen, twice = {}, set()
        for index, key in enumerate(keys):
            if key in seen:
                self.problem((section, index), f"This entry appears twice (first at {section}[{seen[key]}]).")
                twice.add(index)
            else:
                seen[key] = index
        return twice

    # Sections, in an order that resolves every reference

    def run(self, package: Package) -> None:
        for section in (self.organizations, self.reference_systems, self.dimensions, self.units, self.matrices,
                        self.parameters, self.devices, self.documents, self.people, self.sites, self.spots,
                        self.actions_and_effects, self.dependencies, self.thresholds, self.coefficients):
            section(package)

    def simple(self, package: Package, section: str, table, fields: Callable) -> None:
        entries = getattr(package, section)
        twice = self.duplicates(section, [e.name for e in entries])
        for index, entry in enumerate(entries):
            if index not in twice:
                _, created, labels = self.upsert(table, {"name": entry.name}, fields(entry), entry.model_fields_set)
                self.record(section, entry.name, created, labels)

    def organizations(self, package: Package) -> None:
        self.simple(package, "organizations", organization,
                    lambda e: {"shorthand": ("shorthand", e.shorthand), "relation": ("relation", e.relation)})

    def reference_systems(self, package: Package) -> None:
        self.simple(package, "reference_systems", crs,
                    lambda e: {"epsg": ("epsg", e.epsg), "description": ("description", e.description)})

    def dimensions(self, package: Package) -> None:
        self.simple(package, "dimensions", dimension, lambda e: {"description": ("description", e.description)})

    def matrices(self, package: Package) -> None:
        self.simple(package, "matrices", matrix, lambda e: {"description": ("description", e.description)})

    def units(self, package: Package) -> None:
        dimensions = self.lookup(dimension)
        twice = self.duplicates("units", [e.name for e in package.units])
        for index, entry in enumerate(package.units):
            path = ("units", index)
            dimension_id = self.ref(dimensions, entry.dimension, "dimension", (*path, "dimension"))
            if index in twice or dimension_id is MISSING:
                continue
            unit_id, created, labels = self.upsert(
                unit, {"name": entry.name},
                {"description": ("description", entry.description), "dimension": ("id_dimension", dimension_id)},
                entry.model_fields_set)
            if created or "notations" in entry.model_fields_set:
                taken = {r.notation: r.id_unit for r in self.conn.execute(
                    select(unit_notation.c.notation, unit_notation.c.id_unit).where(visible(unit_notation)))}
                clash = [n for n in entry.notations if taken.get(n, unit_id) != unit_id]
                for notation in clash:
                    self.problem((*path, "notations"), f"{notation!r} already stands for another unit.")
                if not clash and self.sync(unit_notation, {"id_unit": unit_id},
                                           [{"notation": n} for n in entry.notations]) and not created:
                    labels.append("notations")
            self.record("units", entry.name, created, labels)

    def units_by_reference(self) -> dict:
        """Units by name and by notation."""
        names = self.lookup(unit)
        notations = {n: u for n, u in self.conn.execute(
            select(unit_notation.c.notation, unit_notation.c.id_unit).where(visible(unit_notation))) if n}
        return {**notations, **names}

    def parameters(self, package: Package) -> None:
        units = self.units_by_reference()
        twice = self.duplicates("parameters", [e.name for e in package.parameters])
        for index, entry in enumerate(package.parameters):
            path = ("parameters", index)
            unit_id = self.ref(units, entry.unit, "unit", (*path, "unit"))
            if index in twice or unit_id is MISSING:
                continue
            existing = self.find(parameter, {"name": entry.name})
            if existing and "value_class" in entry.model_fields_set and existing["value_class"] != entry.value_class:
                count = self.conn.scalar(select(func.count()).select_from(
                    raw_value.join(value, value.c.id_value == raw_value.c.id_value)
                    .join(effect, effect.c.id_effect == value.c.id_effect))
                    .where(effect.c.id_parameter == existing["id_parameter"]))
                if count:
                    self.problem((*path, "value_class"), f"{entry.name} has {count} raw values as "
                                 f"{existing['value_class']}; its value class can't change.")
                    continue
            _, created, labels = self.upsert(
                parameter, {"name": entry.name},
                {"description": ("description", entry.description), "unit": ("id_unit", unit_id),
                 "value_class": ("value_class", entry.value_class)},
                entry.model_fields_set)
            self.record("parameters", entry.name, created, labels)

    def devices(self, package: Package) -> None:
        organizations = self.lookup(organization)
        twice = self.duplicates("devices", [(e.name, e.number) for e in package.devices])
        for index, entry in enumerate(package.devices):
            path = ("devices", index)
            manufacturer = self.ref(organizations, entry.manufacturer, "organization", (*path, "manufacturer"))
            operator = self.ref(organizations, entry.operator, "organization", (*path, "operator"))
            if index in twice or MISSING in (manufacturer, operator):
                continue
            _, created, labels = self.upsert(
                device, {"name": entry.name, "number": entry.number},
                {"manufacturer": ("id_manufacturer", manufacturer), "operator": ("id_operator", operator),
                 "active": ("active", entry.active)},
                entry.model_fields_set)
            self.record("devices", f"{entry.name} #{entry.number}", created, labels)

    def documents(self, package: Package) -> None:
        self.simple(package, "documents", link,
                    lambda e: {"uri": ("uri", e.uri), "file_path": ("file_path", e.file_path),
                               "accepted": ("accepted", e.accepted), "sha256": ("sha256", e.sha256)})

    def people(self, package: Package) -> None:
        organizations = self.lookup(organization)
        keys = [e.linux_user or (e.given_name, e.surname) for e in package.people]
        twice = self.duplicates("people", keys)
        for index, entry in enumerate(package.people):
            path = ("people", index)
            organization_id = self.ref(organizations, entry.organization, "organization", (*path, "organization"))
            if index in twice or organization_id is MISSING:
                continue
            if entry.position is not None and organization_id is None:
                self.problem((*path, "position"), "A position belongs to an organization.")
                continue
            given = entry.model_fields_set
            key = {"linux_user": entry.linux_user} if entry.linux_user else \
                {"given_name": entry.given_name, "surname": entry.surname, "linux_user": None}
            existing = self.find(person, key)
            if existing and existing["id_person"] == self.editor_id and "active" in given and not entry.active:
                self.problem((*path, "active"), "Nobody can deactivate themselves.")
                continue
            person_id, created, labels = self.upsert(
                person, key,
                {"given_name": ("given_name", entry.given_name), "surname": ("surname", entry.surname),
                 "organization": ("id_organization", organization_id), "active": ("active", entry.active)},
                given | {"given_name", "surname"})
            org = organization_id if organization_id is not None else (existing or {}).get("id_organization")
            if org is not None and (created or "position" in given or "organization" in given):
                if self.sync(j_person_organization, {"id_person": person_id, "id_organization": org},
                             [{"position": entry.position}] if entry.position else [{"position": None}],
                             extra=("position",)) and not created:
                    labels.append("position")
            if created or "roles" in given:
                if people.set_roles(self.conn, person_id, set(entry.roles), self.editor_id, self.reason) and not created:
                    labels.append("roles")
            self.record("people", entry.linux_user or f"{entry.given_name} {entry.surname}", created, labels)
        if package.people and not people.active_admins(self.conn):
            self.problem(("people",), "After this package, no active person would be admin.")

    def house(self, address, path: Path):
        """The house of an address, with its city, district and street, found or created."""
        city_id, _, _ = self.upsert(city, {"name": address.city}, {}, set())
        found = self.find(district, {"name": address.district, "id_city": city_id})
        if found and found["zip"] != address.zip:
            self.problem((*path, "zip"), f"{address.district} has the zip {found['zip']} in lucentBASE.")
            return MISSING
        district_id, _, _ = self.upsert(district, {"name": address.district, "id_city": city_id},
                                        {"zip": ("zip", address.zip)}, set())
        street_id, _, _ = self.upsert(street, {"name": address.street, "id_district": district_id}, {}, set())
        house_id, _, _ = self.upsert(house, {"number": address.house_number, "number_addition": address.addition,
                                             "id_street": street_id}, {}, set())
        return house_id

    def sites(self, package: Package) -> None:
        systems = self.lookup(crs)
        twice = self.duplicates("sites", [e.name for e in package.sites])
        for index, entry in enumerate(package.sites):
            path = ("sites", index)
            crs_id = self.ref(systems, entry.reference_system, "reference system", (*path, "reference_system"))
            corners = [(p.longitude, p.latitude) for p in entry.border]
            if corners and len(set(corners)) < 3:
                self.problem((*path, "border"), "A border needs at least three different corners.")
                continue
            if corners and crosses_itself(corners):
                self.problem((*path, "border"), "The border crosses itself. Give its corners in order.")
                continue
            if index in twice or crs_id is MISSING:
                continue
            house_id = self.house(entry.address, (*path, "address")) if entry.address else None
            if house_id is MISSING:
                continue
            given = entry.model_fields_set
            existing = self.find(site, {"name": entry.name})
            location = (house_id if "address" in given or not existing else existing["id_house"],
                        corners if "border" in given or not existing else existing["borders"],
                        entry.description if "description" in given or not existing else existing["description"])
            if not any(location):
                self.problem(path, "A site needs an address, a border or a description.")
                continue
            fields = {"description": ("description", entry.description), "reference_system": ("id_crs", crs_id),
                      "address": ("id_house", house_id)}
            if not existing and corners:  # in the insert itself: lucentBASE checks a site has a location
                fields["border"] = ("borders", from_text(polygon_wkt(corners)))
            site_id, created, labels = self.upsert(site, {"name": entry.name}, fields, given)
            if not created and "border" in given:
                current = self.conn.scalar(select(as_text(site.c.borders)).where(site.c.id_site == site_id))
                if parse_polygon(current) != corners:
                    self.note("Changed border")
                    labels.append("border")
                    self.conn.execute(update(site).where(site.c.id_site == site_id).values(
                        borders=from_text(polygon_wkt(corners)) if corners else None))
            self.record("sites", entry.name, created, labels)

    def spots(self, package: Package) -> None:
        systems, sites, units = self.lookup(crs), self.lookup(site), self.units_by_reference()
        twice = self.duplicates("spots", [e.name for e in package.spots])
        for index, entry in enumerate(package.spots):
            path = ("spots", index)
            refs = (self.ref(systems, entry.reference_system, "reference system", (*path, "reference_system")),
                    self.ref(sites, entry.site, "site", (*path, "site")),
                    self.ref(units, entry.z_unit, "unit", (*path, "z_unit")))
            if index in twice or MISSING in refs:
                continue
            given = entry.model_fields_set
            existing = self.find(spot, {"name": entry.name}) or {}
            description = entry.description if "description" in given or not existing else existing["description"]
            x = entry.latitude if "latitude" in given or not existing else existing["x"]
            y = entry.longitude if "longitude" in given or not existing else existing["y"]
            if description is None and (x is None or y is None):
                self.problem(path, "A spot needs a latitude and a longitude, or a description.")
                continue
            crs_id, site_id, z_unit = refs
            _, created, labels = self.upsert(
                spot, {"name": entry.name},
                {"description": ("description", entry.description), "latitude": ("x", entry.latitude),
                 "longitude": ("y", entry.longitude), "z": ("z", entry.z), "z_class": ("z_class", entry.z_class),
                 "z_unit": ("z_id_unit", z_unit), "reference_system": ("id_crs", crs_id), "site": ("id_site", site_id)},
                given)
            self.record("spots", entry.name, created, labels)

    def actions_and_effects(self, package: Package) -> None:
        documents, parameters, units = self.lookup(link), self.lookup(parameter), self.units_by_reference()
        devices = {(r.name, r.number): r.id_device for r in self.conn.execute(
            select(device.c.name, device.c.number, device.c.id_device).where(visible(device)))}
        persons = {}
        for r in self.conn.execute(select(person).where(visible(person))).mappings():
            persons[f"{r['given_name']} {r['surname']}"] = r["id_person"]
            if r["linux_user"]:
                persons[r["linux_user"]] = r["id_person"]
        twice = self.duplicates("actions", [(e.name, as_datetime(e.iteration)) for e in package.actions])
        for index, entry in enumerate(package.actions):
            path = ("actions", index)
            iteration = as_datetime(entry.iteration)
            refs = [self.ref(documents, name, "document", (*path, "documents", i)) for i, name in enumerate(entry.documents)]
            refs += [self.ref(devices, (d.name, d.number), "device", (*path, "devices", i))
                     for i, d in enumerate(entry.devices)]
            refs += [self.ref(persons, a.person, "person", (*path, "people", i, "person")) for i, a in enumerate(entry.people)]
            wanted_effects = []
            for i, e in enumerate(entry.effects):
                wanted_effects.append((self.ref(parameters, e.parameter, "parameter", (*path, "effects", i, "parameter")),
                                       e.uncertainty, e.uncertainty_class,
                                       self.ref(units, e.uncertainty_unit, "unit", (*path, "effects", i, "uncertainty_unit")),
                                       e.calculation))
            if any(MISSING in w for w in wanted_effects):
                continue
            parameter_ids = [w[0] for w in wanted_effects]
            if len(set(parameter_ids)) != len(parameter_ids):
                self.problem((*path, "effects"), "Each parameter can be an effect of an action only once.")
                continue
            if index in twice or MISSING in refs:
                continue
            given = entry.model_fields_set
            existing = self.find(action_table, {"name": entry.name, "iteration": iteration})
            if existing and "effects" in given:
                current = [(r["id_parameter"], r["uncertainty"], r["uncertainty_class"], r["uncertainty_id_unit"],
                            r["calculation"]) for r in self.conn.execute(
                    select(effect).where(effect.c.id_action == existing["id_action"], visible(effect))
                    .order_by(effect.c.id_effect)).mappings()]
                if sorted(current, key=str) != sorted(wanted_effects, key=str):
                    self.problem((*path, "effects"), f"The effects differ from version {entry.iteration} of "
                                 f"{entry.name} in lucentBASE. Give the action a new iteration to make a new version.")
                    continue
            action_id, created, labels = self.upsert(
                action_table, {"name": entry.name, "iteration": iteration},
                {"active": ("active", entry.active), "description": ("description", entry.description)}, given)
            self.actions[(entry.name, iteration)] = action_id
            if created:
                for parameter_id, uncertainty, uncertainty_class, unit_id, calculation in wanted_effects:
                    self.conn.execute(insert(effect).values(
                        id_action=action_id, id_parameter=parameter_id, uncertainty=uncertainty,
                        uncertainty_class=uncertainty_class, uncertainty_id_unit=unit_id, calculation=calculation,
                        vis=True))
            for name, table, desired, extra in [
                ("documents", j_action_link, [{"id_link": documents[n]} for n in entry.documents], ()),
                ("devices", j_action_device, [{"id_device": devices[(d.name, d.number)]} for d in entry.devices], ()),
                ("people", j_action_person, [{"id_person": persons[a.person], "role": a.role} for a in entry.people], ()),
            ]:
                if (created or name in given) and self.sync(table, {"id_action": action_id}, desired, extra) \
                        and not created:
                    labels.append(name)
            self.record("actions", f"{entry.name} ({entry.iteration})", created, labels)

    def dependencies(self, package: Package) -> None:
        """A second pass over the actions, once all of them exist."""
        known = {(r.name, r.iteration): r.id_action for r in self.conn.execute(
            select(action_table.c.name, action_table.c.iteration, action_table.c.id_action).where(visible(action_table)))}
        for index, entry in enumerate(package.actions):
            action_id = self.actions.get((entry.name, as_datetime(entry.iteration)))
            if action_id is None or "depends_on" not in entry.model_fields_set and not entry.depends_on:
                continue
            desired = []
            for i, d in enumerate(entry.depends_on):
                other = self.ref(known, (d.action, as_datetime(d.iteration)), "action",
                                 ("actions", index, "depends_on", i))
                if other is MISSING:
                    break
                desired.append({"id_action2": other, "dependency_class": d.dependency_class,
                                "description": d.description})
            else:
                if self.sync(j_action_action, {"id_action1": action_id}, desired,
                             extra=("dependency_class", "description")):
                    key = f"{entry.name} ({entry.iteration})"
                    earlier = next((c for c in self.changes if c.section == "actions" and c.key == key), None)
                    if earlier is None:  # counted as unchanged in the first pass
                        self.summary["actions"]["unchanged"] -= 1
                        self.record("actions", key, False, ["depends_on"])
                    elif earlier.change == "update":
                        earlier.fields.append("depends_on")

    def thresholds(self, package: Package) -> None:
        parameters, documents = self.lookup(parameter), self.lookup(link)
        twice = self.duplicates("thresholds", [(e.parameter, e.document) for e in package.thresholds])
        for index, entry in enumerate(package.thresholds):
            path = ("thresholds", index)
            parameter_id = self.ref(parameters, entry.parameter, "parameter", (*path, "parameter"))
            document_id = self.ref(documents, entry.document, "document", (*path, "document"))
            if index in twice or MISSING in (parameter_id, document_id):
                continue
            given = entry.model_fields_set
            existing = self.find(threshold, {"id_parameter": parameter_id, "id_link": document_id}) or {}
            lower = entry.lower if "lower" in given or not existing else existing["lower_limit"]
            upper = entry.upper if "upper" in given or not existing else existing["upper_limit"]
            if lower is None and upper is None:
                self.problem(path, "A threshold needs a lower or an upper limit.")
                continue
            if lower is not None and upper is not None and lower > upper:
                self.problem(path, "The lower limit is above the upper one.")
                continue
            _, created, labels = self.upsert(
                threshold, {"id_parameter": parameter_id, "id_link": document_id},
                {"lower": ("lower_limit", entry.lower), "upper": ("upper_limit", entry.upper),
                 "description": ("description", entry.description)}, given)
            self.record("thresholds", f"{entry.parameter} in {entry.document}", created, labels)

    def coefficients(self, package: Package) -> None:
        effects = {(r.id_action, r.id_parameter): r.id_effect for r in self.conn.execute(
            select(effect.c.id_action, effect.c.id_parameter, effect.c.id_effect).where(visible(effect)))}
        actions = {(r.name, r.iteration): r.id_action for r in self.conn.execute(
            select(action_table.c.name, action_table.c.iteration, action_table.c.id_action).where(visible(action_table)))}
        parameters = self.lookup(parameter)
        for index, entry in enumerate(package.coefficients):
            ids = []
            for i, ref in enumerate(entry.effects):
                found = effects.get((actions.get((ref.action, as_datetime(ref.iteration))), parameters.get(ref.parameter)))
                if found is None:
                    self.problem(("coefficients", index, "effects", i),
                                 f"{ref.action} ({ref.iteration}) has no effect {ref.parameter!r}.")
                ids.append(found)
            if None in ids:
                continue
            existing = self.find(coefficient, {"id_effect1": ids[0], "id_effect2": ids[1]}) or \
                self.find(coefficient, {"id_effect1": ids[1], "id_effect2": ids[0]})
            key = {"id_effect1": existing["id_effect1"], "id_effect2": existing["id_effect2"]} if existing else \
                {"id_effect1": ids[0], "id_effect2": ids[1]}
            _, created, labels = self.upsert(coefficient, key, {"description": ("description", entry.description)},
                                             entry.model_fields_set)
            self.record("coefficients", " and ".join(f"{r.action}: {r.parameter}" for r in entry.effects), created, labels)


def run(conn: Connection, package: Package, editor_id: int, reason: str | None = None) -> Importer:
    """Imports into the caller's transaction; the caller commits or rolls back."""
    importer = Importer(conn, editor_id, reason)
    try:
        importer.run(package)
    except DBAPIError as error:  # a constraint the checks above don't know
        importer.problem(("package",), f"lucentBASE refused a change: {error.orig}")
    finally:
        set_editor(conn, None, None)
    return importer


def plan(engine: Engine, package: Package, editor_id: int, reason: str | None = None,
         line_of: Callable[[Path], int | None] = lambda _: None) -> m.PackagePlan:
    """What applying the package would change, without changing anything."""
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            return run(conn, package, editor_id, reason).result(applied=False, line_of=line_of)
        finally:
            transaction.rollback()


def apply(engine: Engine, package: Package, editor_id: int, reason: str | None = None,
          line_of: Callable[[Path], int | None] = lambda _: None) -> m.PackagePlan:
    """Applies the package, all of it or, if there is any problem, nothing."""
    with engine.connect() as conn:
        transaction = conn.begin()
        importer = run(conn, package, editor_id, reason)
        if importer.problems:
            transaction.rollback()
            return importer.result(applied=False, line_of=line_of)
        transaction.commit()
        return importer.result(applied=True, line_of=line_of)
