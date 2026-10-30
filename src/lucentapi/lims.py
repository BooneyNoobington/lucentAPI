"""The LIMS rules on lucentBASE.

- A collection's **values** are its planned effects (one per effect); their **raw values** are
  the readings, oldest first. A further reading of the same value is a repetition.
- Its **status** follows from them: registered (nothing measured), in analysis (some values
  measured), in review (all measured, not all raw values accepted), approved.
- Its **sampling time** is the datetime raw value of the configured sampling-time parameter,
  else the time it was registered (the insert entry in l_collection).
- The **limits** of a raw value are the thresholds of its parameter in the documents of its
  action (j_action_link).
- Nobody accepts a raw value they measured (four-eyes principle).
"""

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import Connection, func, select

from . import models as m
from .db import from_db_time, visible
from .errors import APIError
from .etag import etag
from .schema import action, effect, j_action_link, log_table, parameter, raw_value, threshold, value
from .schema import collection as collection_table

READING_COLUMNS = {
    "decimal": "decimal_raw_value",
    "text": "text_raw_value",
    "boolean": "boolean_raw_value",
    "datetime": "datetime_raw_value",
}


class Catalog:
    """Effects, parameters and limits, loaded once per request."""

    def __init__(self, conn: Connection, sampling_parameter_id: int | None = None):
        self.sampling_parameter_id = sampling_parameter_id
        self.effects = {row["id_effect"]: dict(row) for row in conn.execute(
            select(effect.c.id_effect, effect.c.id_action, effect.c.id_parameter)
            .join(action, action.c.id_action == effect.c.id_action)
            .where(visible(effect))).mappings()}
        self.parameters = {row["id_parameter"]: dict(row) for row in conn.execute(
            select(parameter.c.id_parameter, parameter.c.value_class, parameter.c.id_unit)
            .where(visible(parameter))).mappings()}
        self.documents = defaultdict(set)
        for action_id, link_id in conn.execute(
                select(j_action_link.c.id_action, j_action_link.c.id_link).where(visible(j_action_link))):
            self.documents[action_id].add(link_id)
        self.thresholds = defaultdict(list)
        for row in conn.execute(select(threshold).where(visible(threshold))).mappings():
            self.thresholds[row["id_parameter"]].append(dict(row))

    def parameter_of(self, effect_id: int) -> dict | None:
        found = self.effects.get(effect_id)
        return self.parameters.get(found["id_parameter"]) if found else None

    def limits(self, effect_id: int) -> list[dict]:
        found = self.effects.get(effect_id)
        if found is None:
            return []
        documents = self.documents[found["id_action"]]
        return [t for t in self.thresholds[found["id_parameter"]] if t["id_link"] in documents]

    def exceeds(self, effect_id: int, number: float | None) -> bool:
        if number is None:
            return False
        return any((t["lower_limit"] is not None and number < t["lower_limit"])
                   or (t["upper_limit"] is not None and number > t["upper_limit"])
                   for t in self.limits(effect_id))

    def is_sampling_time(self, effect_id: int) -> bool:
        found = self.effects.get(effect_id)
        return found is not None and found["id_parameter"] == self.sampling_parameter_id


def reading(row: dict) -> m.Reading | None:
    for kind, column in READING_COLUMNS.items():
        if row[column] is not None:
            content = from_db_time(row[column]) if kind == "datetime" else row[column]
            return m.Reading(**{kind: content})
    return None


def number(r: m.Reading | None) -> float | None:
    if r is None:
        return None
    if r.decimal is not None:
        return r.decimal
    if r.boolean is not None:
        return 1.0 if r.boolean else 0.0
    return None


def with_raw(conn: Connection, value_rows) -> list[dict]:
    """Value rows, each with its visible raw values (oldest first) under "raw"."""
    rows = [dict(row) for row in value_rows]
    raws = defaultdict(list)
    if rows:
        for raw in conn.execute(
            select(raw_value).where(raw_value.c.id_value.in_([r["id_value"] for r in rows]), visible(raw_value))
            .order_by(raw_value.c.t, raw_value.c.id_raw_value)
        ).mappings():
            raws[raw["id_value"]].append(dict(raw))
    return [{**row, "raw": raws[row["id_value"]]} for row in rows]


def values_of(conn: Connection, collection_ids: Iterable[int]) -> dict[int, list[dict]]:
    """Visible values per collection, with their raw values."""
    ids = list(collection_ids)
    if not ids:
        return {}
    rows = conn.execute(
        select(value).where(value.c.id_collection.in_(ids), visible(value)).order_by(value.c.id_value)
    ).mappings().all()
    result = defaultdict(list)
    for row in with_raw(conn, rows):
        result[row["id_collection"]].append(row)
    return result


def registrations(conn: Connection, collection_ids: Iterable[int]) -> dict[int, tuple]:
    """When and by whom each collection was registered: its first insert entry in l_collection."""
    ids = list(collection_ids)
    if not ids:
        return {}
    log = log_table(collection_table)
    first = (select(log.c.id_collection, func.min(log.c.id_l_collection).label("first"))
             .where(log.c.id_collection.in_(ids), log.c.modification == "I")
             .group_by(log.c.id_collection).subquery())
    rows = conn.execute(select(log.c.id_collection, log.c.t, log.c.id_editor)
                        .join(first, first.c.first == log.c.id_l_collection))
    return {collection_id: (t, editor) for collection_id, t, editor in rows}


def raw_value_out(raw: dict, effect_id: int, catalog: Catalog) -> m.RawValue:
    r = reading(raw)
    return m.RawValue(
        id=raw["id_raw_value"], value_id=raw["id_value"], t=from_db_time(raw["t"]), person_id=raw["id_person"],
        device_id=raw["id_device"], unit_id=raw["id_unit"], reading=r, accepted=bool(raw["accepted"]),
        exceeds=catalog.exceeds(effect_id, number(r)), etag=etag(raw))


def value_out(row: dict, catalog: Catalog) -> m.Value:
    return m.Value(
        id=row["id_value"], collection_id=row["id_collection"], effect_id=row["id_effect"], name=row["name"],
        limits=[m.Limit(threshold_id=t["id_threshold"], document_id=t["id_link"], lower_limit=t["lower_limit"],
                        upper_limit=t["upper_limit"]) for t in catalog.limits(row["id_effect"])],
        raw_values=[raw_value_out(raw, row["id_effect"], catalog) for raw in row["raw"]])


def status(values: list[dict]) -> tuple[m.Status, int]:
    measured = sum(1 for v in values if v["raw"])
    if measured == 0:
        return "registered", 0
    if measured < len(values):
        return "in_analysis", measured
    accepted = all(raw["accepted"] for v in values for raw in v["raw"])
    return ("approved" if accepted else "in_review"), measured


def sampling(values: list[dict], catalog: Catalog) -> tuple:
    """(time, person) of the latest sampling-time raw value, or (None, None)."""
    for v in values:
        if catalog.is_sampling_time(v["id_effect"]):
            timed = [raw for raw in v["raw"] if raw["datetime_raw_value"] is not None]
            if timed:
                return timed[-1]["datetime_raw_value"], timed[-1]["id_person"]
    return None, None


def collection_out(row: dict, values: list[dict], registered: tuple | None, catalog: Catalog) -> m.Collection:
    state, measured = status(values)
    registered_at, registered_by = registered or (None, None)
    sampled_at, sampled_by = sampling(values, catalog)
    return m.Collection(
        id=row["id_collection"], name=row["name"], matrix_id=row["id_matrix"],
        collection_class=row["collection_class"], spot_id=row["id_spot"], site_id=row["id_site"],
        registered_at=from_db_time(registered_at), registered_by=registered_by,
        sampled_at=from_db_time(sampled_at or registered_at),
        sampled_by=sampled_by if sampled_at else registered_by,
        status=state, planned=len(values), measured=measured,
        exceedances=sum(catalog.exceeds(v["id_effect"], number(reading(raw))) for v in values for raw in v["raw"]),
    )


def check_four_eyes(raws: list[dict], reviewer_id: int) -> None:
    """Nobody accepts a raw value they measured themselves."""
    own = [raw["id_raw_value"] for raw in raws if raw["id_person"] == reviewer_id]
    if own:
        raise APIError(403, "four_eyes", "Raw values have to be accepted by someone other than who measured them.",
                       details={"raw_value_ids": own})
