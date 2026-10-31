"""Spots (points) and sites (areas with a border): reading, adding and changing them.

lucentBASE keeps a spot's latitude in `x` and its longitude in `y`, and a site's border as a
polygon in WKT order (longitude latitude). The API answers and takes explicit latitudes and
longitudes for borders.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, Engine, insert, select, update

from .. import models as m
from ..auth import Caller, CurrentCaller, require
from ..db import db, set_editor, visible, writing
from ..errors import APIError, not_found
from ..etag import check, etag
from ..geometry import as_text, crosses_itself, from_text, parse_polygon, polygon_wkt
from ..people import note
from ..schema import city, crs, district, house, site, spot, street

router = APIRouter(prefix="/v1", tags=["locations"])
Database = Annotated[Engine, Depends(db)]
Sampler = Annotated[Caller, Depends(require("sampler"))]


def sites_query():
    return (
        select(site.c.id_site, site.c.name, site.c.description, site.c.id_crs, as_text(site.c.borders).label("wkt"),
               house.c.number, house.c.number_addition, street.c.name.label("street"),
               district.c.name.label("district"), district.c.zip, city.c.name.label("city"))
        .outerjoin(house, house.c.id_house == site.c.id_house)
        .outerjoin(street, street.c.id_street == house.c.id_street)
        .outerjoin(district, district.c.id_district == street.c.id_district)
        .outerjoin(city, city.c.id_city == district.c.id_city)
        .where(visible(site))
        .order_by(site.c.name)
    )


def site_data(r) -> dict:
    """What the API shows of a site; its hash is the site's ETag."""
    address = None
    if r["street"] or r["city"]:
        address = {"street": r["street"], "house_number": r["number"], "addition": r["number_addition"],
                   "zip": r["zip"], "district": r["district"], "city": r["city"]}
    return {"id": r["id_site"], "name": r["name"], "description": r["description"], "address": address,
            "border": [{"latitude": lat, "longitude": lon} for lon, lat in parse_polygon(r["wkt"])],
            "crs_id": r["id_crs"]}


def spot_data(r) -> dict:
    """What the API shows of a spot; its hash is the spot's ETag."""
    return {"id": r["id_spot"], "name": r["name"], "description": r["description"], "x": r["x"], "y": r["y"],
            "z": r["z"], "crs_id": r["id_crs"], "site_id": r["id_site"]}


def site_out(data: dict) -> m.Site:
    return m.Site(**data, etag=etag(data))


def spot_out(data: dict) -> m.Spot:
    return m.Spot(**data, etag=etag(data))


def checked_border(points: list[m.GeoPoint]) -> list[tuple[float, float]]:
    """The corners as (longitude, latitude), refused if they don't make a simple polygon."""
    corners = [(p.longitude, p.latitude) for p in points]
    if len(set(corners)) < 3:
        raise APIError(422, "border_too_small", "A border needs at least three different corners.")
    if crosses_itself(corners):
        raise APIError(422, "border_crosses_itself", "The border crosses itself. Draw its corners in order.")
    return corners


def reference_system(conn: Connection, crs_id: int | None) -> int | None:
    """The given reference system if it exists, else WGS 84 if lucentBASE has it."""
    if crs_id is None:
        return conn.scalar(select(crs.c.id_crs).where(crs.c.epsg == 4326, visible(crs)).order_by(crs.c.id_crs))
    if conn.scalar(select(crs.c.id_crs).where(crs.c.id_crs == crs_id, visible(crs))) is None:
        raise APIError(422, "unknown_reference", f"There is no reference system {crs_id}.")
    return crs_id


def require_free(conn: Connection, table, name: str, what: str) -> None:
    # The UNIQUE constraint counts hidden rows too.
    if conn.scalar(select(table.c[f"id_{table.name}"]).where(table.c.name == name)) is not None:
        raise APIError(409, "name_taken", f"There already is a {what} called {name!r}.")


@router.get("/crs")
def reference_systems(_: CurrentCaller, engine: Database) -> list[m.ReferenceSystem]:
    with engine.connect() as conn:
        rows = conn.execute(select(crs).where(visible(crs))).mappings()
        return [m.ReferenceSystem(id=r["id_crs"], epsg=r["epsg"], name=r["name"], description=r["description"])
                for r in rows]


@router.get("/sites")
def list_sites(_: CurrentCaller, engine: Database) -> list[m.Site]:
    """Sites with address and border. WKT stores (longitude latitude); the border is converted."""
    with engine.connect() as conn:
        return [site_out(site_data(r)) for r in conn.execute(sites_query()).mappings()]


@router.post("/sites", status_code=201)
def create_site(body: m.SiteIn, caller: Sampler, engine: Database) -> m.Site:
    """Adds an area by its border. The border must not cross itself."""
    corners = checked_border(body.border)
    with writing(engine, caller.person_id, body.note) as conn:
        require_free(conn, site, body.name, "site")
        site_id = conn.execute(insert(site).values(
            name=body.name, description=body.description, borders=from_text(polygon_wkt(corners)),
            id_crs=reference_system(conn, body.crs_id), vis=True,
        )).inserted_primary_key[0]
        return site_out(site_data(conn.execute(sites_query().where(site.c.id_site == site_id)).mappings().one()))


@router.get("/spots")
def list_spots(_: CurrentCaller, engine: Database) -> list[m.Spot]:
    with engine.connect() as conn:
        rows = conn.execute(select(spot).where(visible(spot)).order_by(spot.c.name)).mappings()
        return [spot_out(spot_data(r)) for r in rows]


@router.post("/spots", status_code=201)
def create_spot(body: m.SpotIn, caller: Sampler, engine: Database) -> m.Spot:
    """Adds a point where samples are taken."""
    with writing(engine, caller.person_id, body.note) as conn:
        require_free(conn, spot, body.name, "spot")
        if body.site_id is not None and conn.scalar(
                select(site.c.id_site).where(site.c.id_site == body.site_id, visible(site))) is None:
            raise APIError(422, "unknown_reference", f"There is no site {body.site_id}.")
        spot_id = conn.execute(insert(spot).values(
            name=body.name, description=body.description, x=body.x, y=body.y,
            id_crs=reference_system(conn, body.crs_id), id_site=body.site_id, vis=True,
        )).inserted_primary_key[0]
        return spot_out(spot_data(conn.execute(select(spot).where(spot.c.id_spot == spot_id)).mappings().one()))


@router.patch("/sites/{site_id}")
def update_site(site_id: int, body: m.SitePatch, request: Request, caller: Sampler, engine: Database) -> m.Site:
    """Renames a site or redraws its border. Needs the site's ETag in If-Match."""
    corners = checked_border(body.border) if body.border is not None else None
    with writing(engine, caller.person_id, body.note) as conn:
        if conn.execute(select(site.c.id_site).where(site.c.id_site == site_id, visible(site))
                        .with_for_update()).first() is None:
            raise not_found("site", site_id)
        current = site_data(conn.execute(sites_query().where(site.c.id_site == site_id)).mappings().one())
        check(request, current, required=True)
        changes, said = {}, []
        if body.name is not None and body.name != current["name"]:
            require_free(conn, site, body.name, "site")
            changes["name"] = body.name
            said.append("name")
        if "description" in body.model_fields_set and (body.description or None) != current["description"]:
            changes["description"] = body.description or None
            said.append("description")
        if corners is not None and [(p["longitude"], p["latitude"]) for p in current["border"]] != corners:
            changes["borders"] = from_text(polygon_wkt(corners))
            said.append("border")
        if changes:
            set_editor(conn, caller.person_id, note("Changed " + ", ".join(said), body.note))
            conn.execute(update(site).where(site.c.id_site == site_id).values(changes))
        return site_out(site_data(conn.execute(sites_query().where(site.c.id_site == site_id)).mappings().one()))


@router.patch("/spots/{spot_id}")
def update_spot(spot_id: int, body: m.SpotPatch, request: Request, caller: Sampler, engine: Database) -> m.Spot:
    """Renames or moves a spot, or puts it in another site. Needs the spot's ETag in If-Match."""
    with writing(engine, caller.person_id, body.note) as conn:
        row = conn.execute(select(spot).where(spot.c.id_spot == spot_id, visible(spot))
                           .with_for_update()).mappings().first()
        if row is None:
            raise not_found("spot", spot_id)
        check(request, spot_data(row), required=True)
        sent, changes, said = body.model_fields_set, {}, []
        if body.name is not None and body.name != row["name"]:
            require_free(conn, spot, body.name, "spot")
            changes["name"] = body.name
            said.append("name")
        if "description" in sent and (body.description or None) != row["description"]:
            changes["description"] = body.description or None
            said.append("description")
        x = row["x"] if body.x is None else body.x
        y = row["y"] if body.y is None else body.y
        if (x, y) != (row["x"], row["y"]):
            changes["x"], changes["y"] = x, y
            said.append("position")
        if "site_id" in sent and body.site_id != row["id_site"]:
            if body.site_id is not None and conn.scalar(
                    select(site.c.id_site).where(site.c.id_site == body.site_id, visible(site))) is None:
                raise APIError(422, "unknown_reference", f"There is no site {body.site_id}.")
            changes["id_site"] = body.site_id
            said.append("site")
        if changes:
            set_editor(conn, caller.person_id, note("Changed " + ", ".join(said), body.note))
            conn.execute(update(spot).where(spot.c.id_spot == spot_id).values(changes))
        return spot_out(spot_data(conn.execute(select(spot).where(spot.c.id_spot == spot_id)).mappings().one()))
