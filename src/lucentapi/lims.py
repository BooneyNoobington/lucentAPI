"""The LIMS rules: what a collection plans, its status, limits, the four-eyes principle."""

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import Connection, select

from . import models as m
from .db import from_db_time, visible
from .errors import APIError
from .etag import etag
from .schema import action, j_action_collection_class, j_action_variable, raw_value, value, variable

READING_COLUMNS = {
    "decimal": ("decimal_value", "decimal_raw_value"),
    "text": ("text_value", "text_raw_value"),
    "boolean": ("boolean_value", "boolean_raw_value"),
    "datetime": ("datetime_value", "datetime_raw_value"),
}


class Catalog:
    """What the collection classes plan, loaded once per request."""

    def __init__(self, conn: Connection):
        avs = conn.execute(
            select(j_action_variable, variable.c.value_class)
            .join(variable, variable.c.id_variable == j_action_variable.c.id_variable)
            .where(visible(j_action_variable))
            .order_by(j_action_variable.c.id_action, j_action_variable.c.id_j_action_variable)
        ).mappings().all()
        self.action_variables = {row["id_j_action_variable"]: dict(row) for row in avs}

        by_action = defaultdict(list)
        for row in avs:
            by_action[row["id_action"]].append(row["id_j_action_variable"])

        links = conn.execute(
            select(j_action_collection_class.c.id_collection_class, j_action_collection_class.c.id_action)
            .join(action, action.c.id_action == j_action_collection_class.c.id_action)
            .where(visible(j_action_collection_class), visible(action))
            .order_by(j_action_collection_class.c.id_action)
        ).all()
        self.planned: dict[int, list[int]] = defaultdict(list)
        for class_id, action_id in links:
            self.planned[class_id].extend(by_action[action_id])

    def exceeds(self, av_id: int, number: float | None) -> bool:
        av = self.action_variables.get(av_id)
        if av is None or number is None:
            return False
        low, high = av["lower_limit"], av["upper_limit"]
        return (low is not None and number < low) or (high is not None and number > high)


def reading(row: dict) -> m.Reading:
    for kind, (column, _) in READING_COLUMNS.items():
        if row[column] is not None:
            return m.Reading(**{kind: row[column]})
    return m.Reading(text="")


def number(r: m.Reading) -> float | None:
    if r.decimal is not None:
        return r.decimal
    if r.boolean is not None:
        return 1.0 if r.boolean else 0.0
    return None


def values_of(conn: Connection, collection_ids: Iterable[int]) -> dict[int, list[dict]]:
    """Visible values per collection, each with its raw values (oldest first) under "raw"."""
    ids = list(collection_ids)
    if not ids:
        return {}
    rows = conn.execute(
        select(value).where(value.c.id_collection.in_(ids), visible(value))
        .order_by(value.c.id_j_action_variable, value.c.repetition)
    ).mappings().all()
    raws = defaultdict(list)
    if rows:
        for raw in conn.execute(
            select(raw_value).where(raw_value.c.id_value.in_([r["id_value"] for r in rows]), visible(raw_value))
            .order_by(raw_value.c.t, raw_value.c.id_raw_value)
        ).mappings():
            raws[raw["id_value"]].append(dict(raw))
    result = defaultdict(list)
    for row in rows:
        result[row["id_collection"]].append({**row, "raw": raws[row["id_value"]]})
    return result


def value_out(row: dict, catalog: Catalog) -> m.Value:
    r = reading(row)
    latest = row["raw"][-1] if row["raw"] else {}
    plain = {k: v for k, v in row.items() if k != "raw"}
    return m.Value(
        id=row["id_value"],
        collection_id=row["id_collection"],
        action_variable_id=row["id_j_action_variable"],
        repetition=row["repetition"],
        reading=r,
        accepted=bool(row["accepted"]),
        measured_at=from_db_time(latest.get("t")),
        person_id=latest.get("id_person"),
        device_id=latest.get("id_device"),
        exceeds=catalog.exceeds(row["id_j_action_variable"], number(r)),
        etag=etag(plain),
    )


def status(planned: list[int], values: list[dict]) -> tuple[m.Status, int]:
    measured = len({v["id_j_action_variable"] for v in values} & set(planned))
    if measured == 0:
        return "registered", measured
    if measured < len(set(planned)):
        return "in_analysis", measured
    return ("approved" if all(v["accepted"] for v in values) else "in_review"), measured


def collection_out(row: dict, values: list[dict], catalog: Catalog) -> m.Collection:
    planned = catalog.planned.get(row["id_collection_class"], [])
    state, measured = status(planned, values)
    exceedances = sum(catalog.exceeds(v["id_j_action_variable"], number(reading(v))) for v in values)
    return m.Collection(
        id=row["id_collection"],
        name=row["name"],
        matrix_id=row["id_matrix"],
        class_id=row["id_collection_class"],
        spot_id=row["id_spot"],
        site_id=row["id_site"],
        sampled_at=from_db_time(row["sampled_at"]),
        sampler_id=row["id_sampler"],
        status=state,
        measured=measured,
        planned=len(set(planned)),
        exceedances=exceedances,
    )


def check_four_eyes(values: list[dict], reviewer_id: int) -> None:
    """Nobody accepts a value they measured themselves."""
    own = [v["id_value"] for v in values if any(raw["id_person"] == reviewer_id for raw in v["raw"])]
    if own:
        raise APIError(403, "four_eyes", "Values have to be accepted by someone other than who measured them.",
                       details={"value_ids": own})
