"""Demo data for development and tests, in lucentBASE's model.

The master data (people, places, methods, parameters, documents and their thresholds) is a
lucent package, data/demo.lucent.yaml. `load(conn, now)` imports it into a lucentBASE that holds
only its initial data (the "System User" with linux_user grindel, the organization
Redhsoft.org, the four roles and grindel as admin, as INITIAL_DATA.SQL inserts them; on SQLite
they're added here), then generates collections, values and raw values with a fixed seed
relative to `now`, and adds tasks and messages. Everything is written as the System User, so the
log triggers record it.

Regulations are documents too: an action that refers to the TrinkwV makes the TrinkwV's
thresholds apply to its raw values.

Ellen Ripley takes over quality assurance, and Picard asks for her to get the reviewer role:
granting it is left to whoever tries the people management.
"""

import random
from datetime import datetime, timedelta
from functools import cache
from importlib.resources import files

from sqlalchemy import Connection, insert, select

from . import package_import
from .db import set_editor, utcnow
from .numbering import LUCENT
from .package import Package, load_yaml
from .schema import (
    action,
    collection,
    device,
    effect,
    j_person_role,
    matrix,
    message,
    organization,
    parameter,
    person,
    raw_value,
    role,
    site,
    spot,
    task,
    value,
)

# People and the lab, by id: the package's order gives them these ids in a fresh lucentBASE.
SYSTEM, DOE, DORN, SIMPSON, PICARD, RIPLEY = 1, 2, 3, 4, 5, 6
LAB = 2
NUMBERING_OFFSET = 3000
SAMPLING_TIME = "Probenahmezeitpunkt"

# As in lucentBASE's INITIAL_DATA.SQL.
INITIAL_ROLES = [
    ("sampler", "Registers collections."),
    ("analyst", "Records raw values."),
    ("reviewer", "Accepts and withdraws raw values, except their own."),
    ("admin", "May do everything, including managing people, roles and devices."),
]


@cache
def master_data() -> Package:
    """The demo's master data, as a lucent package."""
    return Package.model_validate(load_yaml((files("lucentapi") / "data" / "demo.lucent.yaml").read_text()))


# Who samples what where, and which actions that plans:
# (spot, site, matrix, class, actions, every n days, first offset, sampler)
SAMPLING, SENSORY, PH, CONDUCTIVITY = "Probenahme Wasser", "Sensorische Prüfung", "pH-Wert", "Elektrische Leitfähigkeit"
ANIONS, METALS, BACTERIA = "Anionen (IC)", "Metalle (ICP-MS)", "E. coli und coliforme Bakterien"
DUST, NO2 = "Feinstaub (gravimetrisch)", "Stickstoffdioxid"
WATER_ROUTINE = [SAMPLING, SENSORY, PH, CONDUCTIVITY, BACTERIA]
WATER_FULL = [SAMPLING, SENSORY, PH, CONDUCTIVITY, ANIONS, METALS, BACTERIA]
GROUND_WATER, AIR = [SAMPLING, PH, CONDUCTIVITY, ANIONS], [DUST, NO2]
SCHEDULES = [
    ("Garten", None, "Luft", "time series", AIR, 7, 1, "s7e23"),
    ("Messcontainer Innenstadt", None, "Luft", "time series", AIR, 7, 2, "s7e23"),
    ("Zapfstelle Labor 2.14", None, "Leitungswasser", "spot sample", WATER_FULL, 14, 3, "worf"),
    ("Hochbehälter Nord", None, "Trinkwasser", "spot sample", WATER_ROUTINE, 7, 0, "worf"),
    ("Brunnen B3", None, "Grundwasser", "spot sample", GROUND_WATER, 14, 2, "worf"),
    ("GWM 12", None, "Grundwasser", "spot sample", GROUND_WATER, 7, 3, "xx121"),
    (None, "Messfeld Rheinaue", "Grundwasser", "mixed sample", GROUND_WATER, 28, 12, "xx121"),
]
WORKERS = {SAMPLING: "worf", SENSORY: "worf", PH: "jd", CONDUCTIVITY: "jd", ANIONS: "jd", METALS: "p47at",
           BACTERIA: "xx121", DUST: "jd", NO2: "s7e23"}
DEVICES = {SAMPLING: ("Einstichthermometer", 42), PH: ("pH-Meter Lab 1", 2207114),
           CONDUCTIVITY: ("Leitfähigkeitsmessgerät LF 4", 2105033), ANIONS: ("Ionenchromatograph IC-3", 98),
           METALS: ("ICP-MS Q2", 4411), BACTERIA: ("Brutschrank 36 °C", 2), NO2: ("NO₂-Analysator", 500221)}
GROUND = {"Brunnen B3", "GWM 12", None}  # groundwater, by spot; None: a site's mixed sample


def mock_reading(parameter_name: str, spot_name: str | None, progress: float, rng: random.Random):
    """(column, content) for a plausible reading; progress runs from 0 (oldest) to 1 (now)."""
    gauss = rng.gauss
    ground, traffic = spot_name in GROUND, spot_name == "Messcontainer Innenstadt"
    match parameter_name:
        case "Temperatur": return "decimal", round(gauss(11.5 if ground else 14, 1), 1)
        case "Färbung": return "text", "farblos" if rng.random() < 0.93 else "schwach gelblich"
        case "Geruch auffällig": return "boolean", rng.random() < 0.04
        case "pH-Wert": return "decimal", round(gauss(7.15 if ground else 7.6, 0.08), 2)
        case "Elektrische Leitfähigkeit": return "decimal", round(gauss(880 if spot_name == "GWM 12" else 650, 30))
        case "Nitrat":
            rising = spot_name == "GWM 12"  # the story: nitrate rising above the limit
            return "decimal", round(38 + 18 * progress + gauss(0, 2) if rising else gauss(18, 2), 1)
        case "Chlorid": return "decimal", round(gauss(45, 4), 1)
        case "Sulfat": return "decimal", round(gauss(62, 5), 1)
        case "Blei": return "decimal", round(max(0.1, gauss(12.5, 1) if progress > 0.8 else gauss(7.5, 2.8)), 1)
        case "Kupfer": return "decimal", round(max(0.01, gauss(0.45, 0.12)), 2)
        case "Nickel": return "decimal", round(max(0.1, gauss(2.5, 0.8)), 1)
        case "Escherichia coli" | "Coliforme Bakterien": return "decimal", float(rng.choice([0] * 30 + [1, 2]))
        case "Feinstaub PM10": return "decimal", float(round(max(3, gauss(31 if traffic else 19, 9))))
        case "Feinstaub PM2,5": return "decimal", float(round(max(2, gauss(19 if traffic else 12, 5))))
        case _: return "decimal", float(round(max(3, gauss(36 if traffic else 18, 6))))


def ensure_initial_data(conn: Connection) -> None:
    """What lucentBASE's INITIAL_DATA.SQL inserts, if it isn't there (as on SQLite)."""
    if conn.scalar(select(person.c.id_person).where(person.c.linux_user == "grindel")) is None:
        conn.execute(insert(organization).values(id_organization=1, name="Redhsoft.org", shorthand="redhsoft", vis=True))
        conn.execute(insert(person).values(id_person=SYSTEM, given_name="System", surname="User",
                                           linux_user="grindel", id_organization=1, vis=True))
        for name, description in INITIAL_ROLES:
            conn.execute(insert(role).values(name=name, description=description, vis=True))
        admin = conn.scalar(select(role.c.id_role).where(role.c.name == "admin"))
        conn.execute(insert(j_person_role).values(id_person=SYSTEM, id_role=admin, vis=True))


def by_name(conn: Connection, table, column="name") -> dict:
    return dict(conn.execute(select(table.c[column], table.c[f"id_{table.name}"])).all())


def load(conn: Connection, now: datetime | None = None, weeks: int = 8) -> None:
    """Imports the demo's master data and generates its measurements, as the System User."""
    now = now or utcnow()
    ensure_initial_data(conn)
    imported = package_import.run(conn, master_data(), SYSTEM, "Demo data")
    if imported.problems:
        raise ValueError(f"the demo package doesn't import: {imported.problems}")
    set_editor(conn, SYSTEM, "Demo data")

    spots, sites, matrices = by_name(conn, spot), by_name(conn, site), by_name(conn, matrix)
    people = by_name(conn, person, "linux_user")
    actions = dict(conn.execute(select(action.c.name, action.c.id_action).where(action.c.active == True)).all())  # noqa: E712
    devices = {(r.name, r.number): r.id_device for r in conn.execute(select(device.c.name, device.c.number,
                                                                           device.c.id_device))}
    effects = [dict(r) for r in conn.execute(
        select(effect.c.id_effect, effect.c.id_action, parameter.c.name, parameter.c.id_unit)
        .join(parameter, parameter.c.id_parameter == effect.c.id_parameter).order_by(effect.c.id_effect)).mappings()]

    rng = random.Random(1997)
    horizon = weeks * 7
    drafts = []
    for spot_name, site_name, matrix_name, collection_class, planned, every, offset, sampler in SCHEDULES:
        for days_ago in range(offset, horizon + 1, every):
            sampled_at = (now - timedelta(days=days_ago)).replace(hour=rng.randint(7, 10), minute=rng.randint(0, 59),
                                                                  second=0, microsecond=0)
            if sampled_at <= now:
                drafts.append((sampled_at, spot_name or "", site_name or "", matrix_name, collection_class,
                               tuple(planned), sampler))
    drafts.sort()

    for collection_id, draft in enumerate(drafts, start=1):
        sampled_at, spot_name, site_name, matrix_name, collection_class, planned, sampler = draft
        spot_name, site_name = spot_name or None, site_name or None
        conn.execute(insert(collection).values(
            id_collection=collection_id, name=LUCENT.identifier(collection_id + NUMBERING_OFFSET),
            id_matrix=matrices[matrix_name], collection_class=collection_class, id_spot=spots.get(spot_name),
            id_site=sites.get(site_name), vis=True))
        values = {}
        for row in effects:
            if row["id_action"] in [actions[name] for name in planned]:
                values[row["id_effect"]] = conn.execute(insert(value).values(
                    id_collection=collection_id, id_effect=row["id_effect"], vis=True)).inserted_primary_key[0]

        age = (now - sampled_at).total_seconds() / 86_400
        progress = 1 - age / horizon
        if age >= 10:
            done, accepted = len(planned), len(planned)
        elif age >= 4:
            done, accepted = len(planned), len(planned) if rng.random() < 0.5 else len(planned) // 2
        elif age >= 1.5:
            done, accepted = rng.randint(1, len(planned)), 0
        else:
            done, accepted = rng.choice([0, 1]), 0

        for index, action_name in enumerate(planned[:done]):
            measured_at = min(now, sampled_at + timedelta(hours=5 * index, minutes=rng.randint(0, 59)))
            for row in effects:
                if row["id_action"] != actions[action_name]:
                    continue
                if row["name"] == SAMPLING_TIME:  # recorded by whoever sampled
                    readings, column, worker, t = [sampled_at], "datetime", people[sampler], sampled_at
                else:
                    column, content = mock_reading(row["name"], spot_name, progress, rng)
                    readings, worker, t = [content], people[WORKERS[action_name]], measured_at
                    if row["name"] == "Blei" and content > 10:  # lead above the limit is measured again
                        readings.append(round(content * rng.gauss(1, 0.04), 1))
                if action_name == DUST:  # one dust sampler per station
                    device_key = ("Feinstaubsammler LVS-3", 1192 if spot_name == "Messcontainer Innenstadt" else 1187)
                else:
                    device_key = DEVICES.get(action_name)
                for repetition, content in enumerate(readings):
                    conn.execute(insert(raw_value).values(
                        id_value=values[row["id_effect"]], t=t + timedelta(hours=3 * repetition), id_person=worker,
                        id_device=devices.get(device_key), id_unit=row["id_unit"], accepted=index < accepted,
                        vis=True, **{f"{column}_raw_value": content}))

    day = now.replace(hour=16, minute=0, second=0, microsecond=0)
    for task_id, request, description, assigner, assignee, days, done in [
        (1, "pH-Meter Lab 1 kalibrieren", "Drei-Punkt-Kalibrierung mit Puffer 4, 7 und 10.", PICARD, DOE, -1, False),
        (2, "Blei an Zapfstelle 2.14 nachprüfen", "Stagnationsproben S0 und S1 nehmen.", RIPLEY, DORN, 2, False),
        (3, "Offene Kollektionen freigeben", "Vier-Augen-Prinzip nach SOP-009.", PICARD, RIPLEY, 1, False),
        (4, "Nitrat-Trend an GWM 12 melden", "Bericht an die Wasserbehörde.", PICARD, PICARD, 5, False),
        (5, "lucentLIMS an lucentAPI anbinden", "Die Mock-Daten durch die API ersetzen.", SYSTEM, SYSTEM, 30, False),
        (6, "Probenflaschen nachbestellen", "500 mL PE und 250 mL Glas, steril.", DORN, SYSTEM, -6, True),
    ]:
        conn.execute(insert(task).values(id_task=task_id, request=request, description=description,
                                         id_assigner=assigner, id_assignee=assignee,
                                         deadline=day + timedelta(days=days), done=done, vis=True))
    for message_id, sender, subject, body, new in [
        (1, PICARD, "Freigaberechte für Ellen", "Kannst du Ellen die Rechte für die Freigabe geben?", True),
        (2, RIPLEY, "Nitrat an GWM 12", "Kannst du mir die Zeitreihe exportieren?", True),
        (3, SIMPSON, "Messcontainer", "Filterwechsel ist erledigt. Die Donuts im Pausenraum leider auch.", False),
    ]:
        conn.execute(insert(message).values(id_message=message_id, id_sender=sender, id_receiver=SYSTEM,
                                            subject=subject, message=body, new=new, vis=True))
    set_editor(conn, None, None)
