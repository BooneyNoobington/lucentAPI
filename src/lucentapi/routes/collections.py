"""Collections, their values, review, and time series."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from sqlalchemy import Connection, Engine, func, insert, select, update

from .. import audit, lims
from .. import models as m
from ..auth import Caller, CurrentCaller, require
from ..db import db, from_db_time, to_db_time, utcnow, visible
from ..errors import APIError, not_found
from ..etag import check
from ..numbering import LUCENT
from ..schema import (
    collection,
    collection_class,
    device,
    j_action_device,
    j_action_variable,
    matrix,
    person,
    raw_value,
    site,
    spot,
    value,
)

router = APIRouter(prefix="/v1", tags=["collections"])
Database = Annotated[Engine, Depends(db)]
OptionalNote = Annotated[m.Note | None, Body()]


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


def detail(conn: Connection, row: dict) -> m.CollectionDetail:
    catalog = lims.Catalog(conn)
    values = lims.values_of(conn, [row["id_collection"]]).get(row["id_collection"], [])
    summary = lims.collection_out(row, values, catalog)
    return m.CollectionDetail(**summary.model_dump(), values=[lims.value_out(v, catalog) for v in values])


@router.get("/collections")
def list_collections(
    _: CurrentCaller,
    engine: Database,
    status: m.Status | None = None,
    matrix_id: int | None = None,
    class_id: int | None = None,
    spot_id: int | None = None,
    site_id: Annotated[int | None, Query(description="Also includes the site's spots.")] = None,
    sampled_from: Annotated[m.dt.datetime | None, Query(description="Inclusive.")] = None,
    sampled_until: Annotated[m.dt.datetime | None, Query(description="Exclusive.")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> m.CollectionPage:
    """Newest first. Status is derived from the values, so filtering by it reads every match."""
    query = select(collection).where(visible(collection)).order_by(collection.c.id_collection.desc())
    if matrix_id is not None:
        query = query.where(collection.c.id_matrix == matrix_id)
    if class_id is not None:
        query = query.where(collection.c.id_collection_class == class_id)
    if spot_id is not None:
        query = query.where(collection.c.id_spot == spot_id)
    if site_id is not None:
        spots_of_site = select(spot.c.id_spot).where(spot.c.id_site == site_id)
        query = query.where((collection.c.id_site == site_id) | collection.c.id_spot.in_(spots_of_site))
    if sampled_from is not None:
        query = query.where(collection.c.sampled_at >= to_db_time(sampled_from))
    if sampled_until is not None:
        query = query.where(collection.c.sampled_at < to_db_time(sampled_until))

    with engine.connect() as conn:
        catalog = lims.Catalog(conn)
        if status is None:
            total = conn.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
            rows = conn.execute(query.limit(limit).offset(offset)).mappings().all()
            values = lims.values_of(conn, [r["id_collection"] for r in rows])
            items = [lims.collection_out(dict(r), values.get(r["id_collection"], []), catalog) for r in rows]
        else:
            rows = conn.execute(query).mappings().all()
            values = lims.values_of(conn, [r["id_collection"] for r in rows])
            matching = [c for r in rows
                        if (c := lims.collection_out(dict(r), values.get(r["id_collection"], []), catalog)).status == status]
            total, items = len(matching), matching[offset:offset + limit]
    return m.CollectionPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/collections/{collection_id}")
def get_collection(collection_id: int, _: CurrentCaller, engine: Database) -> m.CollectionDetail:
    """A collection with all its values."""
    with engine.connect() as conn:
        return detail(conn, load_collection(conn, collection_id))


@router.post("/collections", status_code=201)
def create_collection(
    body: m.CollectionIn, response: Response, engine: Database,
    caller: Annotated[Caller, Depends(require("sampler"))], request: Request,
) -> m.CollectionDetail:
    """Registers a collection. Its identifier comes from the numbering scheme and its id."""
    with engine.begin() as conn:
        exists(conn, matrix, body.matrix_id, "matrix")
        exists(conn, collection_class, body.class_id, "collection class")
        exists(conn, spot, body.spot_id, "spot")
        exists(conn, site, body.site_id, "site")
        exists(conn, person, body.sampler_id, "person")

        row = {
            "name": f"pending-{uuid4().hex}",
            "id_matrix": body.matrix_id,
            "id_collection_class": body.class_id,
            "id_spot": body.spot_id,
            "id_site": body.site_id,
            "sampled_at": to_db_time(body.sampled_at) or utcnow(),
            "id_sampler": body.sampler_id or caller.person_id,
            "vis": True,
        }
        new_id = conn.execute(insert(collection).values(row)).inserted_primary_key[0]
        name = LUCENT.identifier(new_id + request.app.state.settings.numbering_offset)
        if name is None:
            raise APIError(409, "numbering_exhausted", "The numbering scheme has no identifiers left.")
        conn.execute(update(collection).where(collection.c.id_collection == new_id).values(name=name))
        audit.record(conn, collection, new_id, caller.person_id, before=None,
                     after={**row, "id_collection": new_id, "name": name}, note=body.note)
        result = detail(conn, load_collection(conn, new_id))
    response.headers["Location"] = f"{request.scope.get('root_path', '')}/v1/collections/{new_id}"
    return result


@router.post("/collections/{collection_id}/values", status_code=201)
def record_value(
    collection_id: int, body: m.ValueIn, engine: Database,
    caller: Annotated[Caller, Depends(require("analyst"))],
) -> m.Value:
    """Records a reading. A further reading of the same variable becomes the next repetition."""
    with engine.begin() as conn:
        target = load_collection(conn, collection_id, lock=True)
        catalog = lims.Catalog(conn)
        av = catalog.action_variables.get(body.action_variable_id)
        if av is None:
            raise APIError(422, "unknown_reference", f"There is no action variable {body.action_variable_id}.")
        if body.action_variable_id not in catalog.planned.get(target["id_collection_class"], []):
            raise APIError(422, "not_planned", "The collection's class doesn't plan this variable.")
        if body.reading.kind != av["value_class"]:
            raise APIError(422, "wrong_value_class", f"This variable takes a {av['value_class']} reading.")

        device_id = body.device_id
        if device_id is not None:
            active = conn.scalar(select(device.c.active).where(device.c.id_device == device_id, visible(device)))
            if active is None:
                raise APIError(422, "unknown_reference", f"There is no device {device_id}.")
            if not active:
                raise APIError(409, "device_inactive", "The device is switched off.")
        else:
            device_id = conn.scalar(
                select(device.c.id_device)
                .join(j_action_device, j_action_device.c.id_device == device.c.id_device)
                .where(j_action_device.c.id_action == av["id_action"], visible(j_action_device),
                       visible(device), device.c.active == True)  # noqa: E712
                .order_by(j_action_device.c.id_j_action_device).limit(1)
            )

        repetition = (conn.scalar(
            select(func.max(value.c.repetition))
            .where(value.c.id_collection == collection_id, value.c.id_j_action_variable == av["id_j_action_variable"],
                   visible(value))
        ) or 0) + 1
        column, raw_column = lims.READING_COLUMNS[body.reading.kind]
        reading = getattr(body.reading, body.reading.kind)
        if body.reading.kind == "datetime":
            reading = to_db_time(reading)

        value_row = {"id_collection": collection_id, "id_j_action_variable": av["id_j_action_variable"],
                     "repetition": repetition, column: reading, "accepted": False, "vis": True}
        value_id = conn.execute(insert(value).values(value_row)).inserted_primary_key[0]
        audit.record(conn, value, value_id, caller.person_id, before=None,
                     after={**value_row, "id_value": value_id}, note=body.note)

        raw_row = {"id_value": value_id, "t": to_db_time(body.measured_at) or utcnow(), "id_person": caller.person_id,
                   "id_device": device_id, raw_column: reading, "accepted": False, "vis": True}
        raw_id = conn.execute(insert(raw_value).values(raw_row)).inserted_primary_key[0]
        audit.record(conn, raw_value, raw_id, caller.person_id, before=None,
                     after={**raw_row, "id_raw_value": raw_id}, note=body.note)

        values = lims.values_of(conn, [collection_id])[collection_id]
        return lims.value_out(next(v for v in values if v["id_value"] == value_id), catalog)


def load_value(conn: Connection, value_id: int) -> dict:
    row = conn.execute(
        select(value).where(value.c.id_value == value_id, visible(value)).with_for_update()
    ).mappings().first()
    if row is None:
        raise not_found("value", value_id)
    values = lims.values_of(conn, [row["id_collection"]])[row["id_collection"]]
    return next(v for v in values if v["id_value"] == value_id)


def set_accepted(conn: Connection, rows: list[dict], accepted: bool, caller: Caller, note: str | None) -> None:
    for row in rows:
        if bool(row["accepted"]) == accepted:
            continue
        conn.execute(update(value).where(value.c.id_value == row["id_value"]).values(accepted=accepted))
        audit.record(conn, value, row["id_value"], caller.person_id,
                     before={"accepted": bool(row["accepted"])}, after={"accepted": accepted}, note=note)


@router.post("/values/{value_id}/accept", tags=["review"])
def accept_value(
    value_id: int, request: Request, engine: Database,
    caller: Annotated[Caller, Depends(require("reviewer"))], body: OptionalNote = None,
) -> m.Value:
    """Accepts a value. Not allowed for whoever measured it (four-eyes principle). Honours If-Match."""
    with engine.begin() as conn:
        row = load_value(conn, value_id)
        check(request, {k: v for k, v in row.items() if k != "raw"}, required=False)
        lims.check_four_eyes([row], caller.person_id)
        set_accepted(conn, [row], True, caller, body.note if body else None)
        return lims.value_out(load_value(conn, value_id), lims.Catalog(conn))


@router.post("/values/{value_id}/withdraw", tags=["review"])
def withdraw_value(
    value_id: int, request: Request, engine: Database,
    caller: Annotated[Caller, Depends(require("reviewer"))], body: OptionalNote = None,
) -> m.Value:
    """Takes back the acceptance of a value. Honours If-Match."""
    with engine.begin() as conn:
        row = load_value(conn, value_id)
        check(request, {k: v for k, v in row.items() if k != "raw"}, required=False)
        set_accepted(conn, [row], False, caller, body.note if body else None)
        return lims.value_out(load_value(conn, value_id), lims.Catalog(conn))


@router.post("/collections/{collection_id}/accept", tags=["review"])
def accept_collection(
    collection_id: int, engine: Database,
    caller: Annotated[Caller, Depends(require("reviewer"))], body: OptionalNote = None,
) -> m.CollectionDetail:
    """Accepts all values of a collection, or none if the caller measured any of the open ones."""
    with engine.begin() as conn:
        target = load_collection(conn, collection_id, lock=True)
        rows = [v for v in lims.values_of(conn, [collection_id]).get(collection_id, []) if not v["accepted"]]
        lims.check_four_eyes(rows, caller.person_id)
        set_accepted(conn, rows, True, caller, body.note if body else None)
        return detail(conn, target)


@router.get("/series", tags=["locations"])
def series(
    _: CurrentCaller, engine: Database,
    action_variable_id: int,
    spot_id: int | None = None,
    site_id: Annotated[int | None, Query(description="Also includes the site's spots.")] = None,
) -> list[m.SeriesPoint]:
    """One numeric variable over time at a spot or site: the last repetition per collection."""
    if (spot_id is None) == (site_id is None):
        raise APIError(422, "invalid_request", "Give spot_id or site_id.")
    query = (
        select(value, collection.c.sampled_at)
        .join(collection, collection.c.id_collection == value.c.id_collection)
        .where(value.c.id_j_action_variable == action_variable_id, visible(value), visible(collection))
        .order_by(collection.c.sampled_at, value.c.repetition)
    )
    if spot_id is not None:
        query = query.where(collection.c.id_spot == spot_id)
    else:
        spots_of_site = select(spot.c.id_spot).where(spot.c.id_site == site_id)
        query = query.where((collection.c.id_site == site_id) | collection.c.id_spot.in_(spots_of_site))
    with engine.connect() as conn:
        if conn.scalar(select(func.count()).select_from(j_action_variable)
                       .where(j_action_variable.c.id_j_action_variable == action_variable_id)) == 0:
            raise not_found("action variable", action_variable_id)
        catalog = lims.Catalog(conn)
        last = {}
        for row in conn.execute(query).mappings():
            last[row["id_collection"]] = dict(row)  # later repetitions replace earlier ones
        points = []
        for row in last.values():
            number = lims.number(lims.reading(row))
            if number is not None:
                points.append(m.SeriesPoint(value_id=row["id_value"], collection_id=row["id_collection"],
                                            sampled_at=from_db_time(row["sampled_at"]), value=number,
                                            exceeds=catalog.exceeds(action_variable_id, number)))
        return points
