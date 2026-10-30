"""Collections, their values and raw values, review, and time series."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy import Connection, Engine, func, insert, select, update

from .. import lims
from .. import models as m
from ..auth import Caller, CurrentCaller, require
from ..db import db, to_db_time, utcnow, visible, writing
from ..errors import APIError, not_found
from ..etag import check
from ..numbering import LUCENT
from ..schema import (
    action,
    collection,
    device,
    effect,
    j_action_device,
    matrix,
    raw_value,
    site,
    spot,
    unit,
    value,
)
from .reference import sampling_parameter_id

router = APIRouter(prefix="/v1", tags=["collections"])
Database = Annotated[Engine, Depends(db)]
OptionalNote = Annotated[m.Note | None, Body()]


def catalog(conn: Connection, request: Request) -> lims.Catalog:
    return lims.Catalog(conn, sampling_parameter_id(request, conn))


def exists(conn: Connection, table, id_: int | None, what: str) -> None:
    if id_ is None:
        return
    key = table.c[f"id_{table.name}"]
    if conn.scalar(select(func.count()).select_from(table).where(key == id_, visible(table))) == 0:
        raise APIError(422, "unknown_reference", f"There is no {what} {id_}.")


def load_collection(conn: Connection, collection_id: int, lock: bool = False) -> dict:
    query = select(collection).where(collection.c.id_collection == collection_id, visible(collection))
    row = conn.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise not_found("collection", collection_id)
    return dict(row)


def summaries(conn: Connection, rows: list[dict], cat: lims.Catalog) -> list[tuple[m.Collection, list[dict]]]:
    ids = [r["id_collection"] for r in rows]
    values = lims.values_of(conn, ids)
    registered = lims.registrations(conn, ids)
    return [(lims.collection_out(r, values.get(r["id_collection"], []), registered.get(r["id_collection"]), cat),
             values.get(r["id_collection"], [])) for r in rows]


def detail(conn: Connection, row: dict, cat: lims.Catalog) -> m.CollectionDetail:
    summary, values = summaries(conn, [row], cat)[0]
    return m.CollectionDetail(**summary.model_dump(), values=[lims.value_out(v, cat) for v in values])


def matching(conn: Connection, cat: lims.Catalog, *, status=None, matrix_id=None, collection_class=None,
             spot_id=None, site_id=None, sampled_from=None, sampled_until=None, collection_ids=None):
    """Collections (newest first) with their values. Status and sampling time are derived, so
    they're filtered after loading."""
    query = select(collection).where(visible(collection)).order_by(collection.c.id_collection.desc())
    if collection_ids:
        query = query.where(collection.c.id_collection.in_(collection_ids))
    if matrix_id is not None:
        query = query.where(collection.c.id_matrix == matrix_id)
    if collection_class is not None:
        query = query.where(collection.c.collection_class == collection_class)
    if spot_id is not None:
        query = query.where(collection.c.id_spot == spot_id)
    if site_id is not None:
        spots_of_site = select(spot.c.id_spot).where(spot.c.id_site == site_id)
        query = query.where((collection.c.id_site == site_id) | collection.c.id_spot.in_(spots_of_site))
    rows = [dict(r) for r in conn.execute(query).mappings()]
    result = []
    for summary, values in summaries(conn, rows, cat):
        if status is not None and summary.status != status:
            continue
        if sampled_from is not None and (summary.sampled_at is None or summary.sampled_at < sampled_from):
            continue
        if sampled_until is not None and (summary.sampled_at is None or summary.sampled_at >= sampled_until):
            continue
        result.append((summary, values))
    return result


Aware = m.dt.datetime


def aware(moment: Aware | None) -> Aware | None:
    """Query times without an offset are UTC."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=m.dt.UTC)


@router.get("/collections")
def list_collections(
    _: CurrentCaller, request: Request, engine: Database,
    status: m.Status | None = None,
    matrix_id: int | None = None,
    collection_class: m.CollectionClass | None = None,
    spot_id: int | None = None,
    site_id: Annotated[int | None, Query(description="Also includes the site's spots.")] = None,
    sampled_from: Annotated[Aware | None, Query(description="Inclusive.")] = None,
    sampled_until: Annotated[Aware | None, Query(description="Exclusive.")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> m.Page[m.Collection]:
    """Newest first."""
    with engine.connect() as conn:
        found = matching(conn, catalog(conn, request), status=status, matrix_id=matrix_id,
                         collection_class=collection_class, spot_id=spot_id, site_id=site_id,
                         sampled_from=aware(sampled_from), sampled_until=aware(sampled_until))
    return m.Page[m.Collection](items=[s for s, _ in found[offset:offset + limit]], total=len(found), limit=limit,
                                offset=offset)


@router.get("/collections/{collection_id}")
def get_collection(collection_id: int, _: CurrentCaller, request: Request, engine: Database) -> m.CollectionDetail:
    """A collection with its values and their raw values."""
    with engine.connect() as conn:
        return detail(conn, load_collection(conn, collection_id), catalog(conn, request))


def plan(conn: Connection, collection_id: int, action_ids: list[int]) -> None:
    """Adds a value for every effect of the actions that the collection doesn't have yet."""
    for action_id in action_ids:
        active = conn.scalar(select(action.c.active).where(action.c.id_action == action_id, visible(action)))
        if active is None:
            raise APIError(422, "unknown_reference", f"There is no action {action_id}.")
        if not active:
            raise APIError(409, "action_inactive", f"Action {action_id} is no longer active.")
    planned = set(conn.scalars(select(value.c.id_effect).where(value.c.id_collection == collection_id)))
    for effect_id in conn.scalars(select(effect.c.id_effect).where(effect.c.id_action.in_(action_ids),
                                                                   visible(effect)).order_by(effect.c.id_effect)):
        if effect_id not in planned:
            conn.execute(insert(value).values(id_collection=collection_id, id_effect=effect_id, vis=True))
            planned.add(effect_id)


@router.post("/collections", status_code=201)
def create_collection(
    body: m.CollectionIn, response: Response, request: Request, engine: Database,
    caller: Annotated[Caller, Depends(require("sampler"))],
) -> m.CollectionDetail:
    """Registers a collection and plans the effects of the given actions. Its identifier comes
    from the numbering scheme and its id."""
    with writing(engine, caller.person_id, body.note) as conn:
        exists(conn, matrix, body.matrix_id, "matrix")
        exists(conn, spot, body.spot_id, "spot")
        exists(conn, site, body.site_id, "site")
        new_id = conn.execute(insert(collection).values(
            name=None, id_matrix=body.matrix_id, collection_class=body.collection_class, id_spot=body.spot_id,
            id_site=body.site_id, vis=True)).inserted_primary_key[0]
        name = LUCENT.identifier(new_id + request.app.state.settings.numbering_offset)
        if name is None:
            raise APIError(409, "numbering_exhausted", "The numbering scheme has no identifiers left.")
        conn.execute(update(collection).where(collection.c.id_collection == new_id).values(name=name))
        plan(conn, new_id, body.action_ids)

        cat = catalog(conn, request)
        if body.sampled_at is not None:
            sampling = next((v for v in lims.values_of(conn, [new_id]).get(new_id, [])
                             if cat.is_sampling_time(v["id_effect"])), None)
            if sampling is None:
                raise APIError(422, "sampling_time_not_planned",
                               "A sampling time needs an action whose effects include the sampling-time parameter.")
            sampled_at = to_db_time(body.sampled_at)
            insert_raw_value(conn, sampling, cat, m.Reading(datetime=sampled_at), caller,
                             device_id=None, unit_id=None, t=sampled_at)
        result = detail(conn, load_collection(conn, new_id), cat)
    response.headers["Location"] = f"{request.scope.get('root_path', '')}/v1/collections/{new_id}"
    return result


@router.post("/collections/{collection_id}/plan")
def plan_more(
    collection_id: int, body: m.PlanIn, request: Request, engine: Database,
    caller: Annotated[Caller, Depends(require("sampler"))],
) -> m.CollectionDetail:
    """Plans the effects of further actions for a collection."""
    with writing(engine, caller.person_id, body.note) as conn:
        row = load_collection(conn, collection_id, lock=True)
        plan(conn, collection_id, body.action_ids)
        return detail(conn, row, catalog(conn, request))


def insert_raw_value(conn: Connection, target: dict, cat: lims.Catalog, reading: m.Reading, caller: Caller, *,
                     device_id: int | None, unit_id: int | None, t) -> int:
    param = cat.parameter_of(target["id_effect"])
    if param is None:
        raise APIError(409, "effect_gone", "The value's effect or parameter is no longer visible.")
    if reading.kind != param["value_class"]:
        raise APIError(422, "wrong_value_class", f"This parameter takes a {param['value_class']} raw value.")

    action_id = cat.effects[target["id_effect"]]["id_action"]
    if device_id is not None:
        active = conn.scalar(select(device.c.active).where(device.c.id_device == device_id, visible(device)))
        if active is None:
            raise APIError(422, "unknown_reference", f"There is no device {device_id}.")
        if not active:
            raise APIError(409, "device_inactive", "The device is switched off.")
    else:
        device_id = conn.scalar(
            select(device.c.id_device).join(j_action_device, j_action_device.c.id_device == device.c.id_device)
            .where(j_action_device.c.id_action == action_id, visible(j_action_device), visible(device),
                   device.c.active == True)  # noqa: E712
            .order_by(j_action_device.c.id_j_action_device).limit(1))
    exists(conn, unit, unit_id, "unit")

    taken = set(conn.scalars(select(raw_value.c.t).where(raw_value.c.id_value == target["id_value"])))
    if t is None:  # now, or the next free second (lucentBASE allows one raw value per value and time)
        t = utcnow()
        while t in taken:
            t += timedelta(seconds=1)
    elif t in taken:
        raise APIError(409, "time_taken", "The value already has a raw value at this time.")

    column = lims.READING_COLUMNS[reading.kind]
    content = getattr(reading, reading.kind)
    if reading.kind == "datetime":
        content = to_db_time(content)
    return conn.execute(insert(raw_value).values(
        id_value=target["id_value"], t=t, id_person=caller.person_id, id_device=device_id,
        id_unit=unit_id or param["id_unit"], accepted=False, vis=True, **{column: content},
    )).inserted_primary_key[0]


def load_value(conn: Connection, value_id: int) -> dict:
    row = conn.execute(select(value).where(value.c.id_value == value_id, visible(value)).with_for_update()
                       ).mappings().first()
    if row is None:
        raise not_found("value", value_id)
    load_collection(conn, row["id_collection"])
    return lims.with_raw(conn, [row])[0]


@router.post("/values/{value_id}/raw-values", status_code=201)
def record_raw_value(
    value_id: int, body: m.RawValueIn, request: Request, engine: Database,
    caller: Annotated[Caller, Depends(require("analyst"))],
) -> m.RawValue:
    """Records a reading of a value. A further reading of the same value is a repetition."""
    with writing(engine, caller.person_id, body.note) as conn:
        target = load_value(conn, value_id)
        cat = catalog(conn, request)
        raw_id = insert_raw_value(conn, target, cat, body.reading, caller, device_id=body.device_id,
                                  unit_id=body.unit_id, t=to_db_time(body.t))
        raw = next(r for r in load_value(conn, value_id)["raw"] if r["id_raw_value"] == raw_id)
        return lims.raw_value_out(raw, target["id_effect"], cat)


def load_raw_value(conn: Connection, raw_id: int) -> tuple[dict, dict]:
    raw = conn.execute(select(raw_value).where(raw_value.c.id_raw_value == raw_id, visible(raw_value))
                       .with_for_update()).mappings().first()
    if raw is None:
        raise not_found("raw value", raw_id)
    return dict(raw), load_value(conn, raw["id_value"])


def set_accepted(conn: Connection, raws: list[dict], accepted: bool) -> None:
    ids = [r["id_raw_value"] for r in raws if bool(r["accepted"]) != accepted]
    if ids:
        conn.execute(update(raw_value).where(raw_value.c.id_raw_value.in_(ids)).values(accepted=accepted))


def review(raw_id: int, accepted: bool, request: Request, engine: Engine, caller: Caller,
           body: m.Note | None) -> m.RawValue:
    with writing(engine, caller.person_id, body.note if body else None) as conn:
        raw, target = load_raw_value(conn, raw_id)
        check(request, raw, required=False)
        if accepted:
            lims.check_four_eyes([raw], caller.person_id)
        set_accepted(conn, [raw], accepted)
        cat = catalog(conn, request)
        changed, _ = load_raw_value(conn, raw_id)
        return lims.raw_value_out(changed, target["id_effect"], cat)


@router.post("/raw-values/{raw_value_id}/accept", tags=["review"])
def accept_raw_value(raw_value_id: int, request: Request, engine: Database,
                     caller: Annotated[Caller, Depends(require("reviewer"))], body: OptionalNote = None) -> m.RawValue:
    """Accepts a raw value. Not for whoever measured it (four-eyes principle). Honours If-Match."""
    return review(raw_value_id, True, request, engine, caller, body)


@router.post("/raw-values/{raw_value_id}/withdraw", tags=["review"])
def withdraw_raw_value(raw_value_id: int, request: Request, engine: Database,
                       caller: Annotated[Caller, Depends(require("reviewer"))],
                       body: OptionalNote = None) -> m.RawValue:
    """Takes back the acceptance of a raw value. Honours If-Match."""
    return review(raw_value_id, False, request, engine, caller, body)


@router.post("/collections/{collection_id}/accept", tags=["review"])
def accept_collection(collection_id: int, request: Request, engine: Database,
                      caller: Annotated[Caller, Depends(require("reviewer"))],
                      body: OptionalNote = None) -> m.CollectionDetail:
    """Accepts all open raw values of a collection, or none if the caller measured any of them."""
    with writing(engine, caller.person_id, body.note if body else None) as conn:
        row = load_collection(conn, collection_id, lock=True)
        open_raws = [raw for v in lims.values_of(conn, [collection_id]).get(collection_id, [])
                     for raw in v["raw"] if not raw["accepted"]]
        lims.check_four_eyes(open_raws, caller.person_id)
        set_accepted(conn, open_raws, True)
        return detail(conn, row, catalog(conn, request))


@router.get("/values")
def list_values(
    _: CurrentCaller, request: Request, engine: Database,
    collection_id: Annotated[list[int] | None, Query(description="Repeat for several collections.")] = None,
    sampled_from: Annotated[Aware | None, Query(description="Of the collection; inclusive.")] = None,
    sampled_until: Annotated[Aware | None, Query(description="Of the collection; exclusive.")] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> m.Page[m.Value]:
    """Values of many collections at once, with their raw values, for clients that keep a copy."""
    with engine.connect() as conn:
        cat = catalog(conn, request)
        found = matching(conn, cat, collection_ids=collection_id, sampled_from=aware(sampled_from),
                         sampled_until=aware(sampled_until))
        values = sorted((v for _, vs in found for v in vs), key=lambda v: v["id_value"])
        return m.Page[m.Value](items=[lims.value_out(v, cat) for v in values[offset:offset + limit]],
                               total=len(values), limit=limit, offset=offset)


@router.get("/series", tags=["locations"])
def series(
    _: CurrentCaller, request: Request, engine: Database,
    parameter_id: int,
    spot_id: int | None = None,
    site_id: Annotated[int | None, Query(description="Also includes the site's spots.")] = None,
) -> list[m.SeriesPoint]:
    """One numeric parameter over time at a spot or site: the latest raw value per collection."""
    if (spot_id is None) == (site_id is None):
        raise APIError(422, "invalid_request", "Give spot_id or site_id.")
    with engine.connect() as conn:
        cat = catalog(conn, request)
        points = []
        for summary, values in matching(conn, cat, spot_id=spot_id, site_id=site_id):
            for v in values:
                found = cat.effects.get(v["id_effect"])
                if not found or found["id_parameter"] != parameter_id or not v["raw"]:
                    continue
                raw = v["raw"][-1]
                number = lims.number(lims.reading(raw))
                if number is not None:
                    points.append(m.SeriesPoint(raw_value_id=raw["id_raw_value"], value_id=v["id_value"],
                                                collection_id=summary.id, sampled_at=summary.sampled_at,
                                                value=number, exceeds=cat.exceeds(v["id_effect"], number)))
        return sorted(points, key=lambda p: (p.sampled_at is None, p.sampled_at))
