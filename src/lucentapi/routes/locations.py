"""Spots (points) and sites (areas with a border): reading them and adding new ones.

lucentBASE keeps a spot's latitude in `x` and its longitude in `y`, and a site's border as a
polygon in WKT order (longitude latitude). The API answers and takes explicit latitudes and
longitudes for borders.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, Engine, insert, select

from .. import models as m
from ..auth import Caller, CurrentCaller, require
from ..db import db, visible, writing
from ..errors import APIError
from ..geometry import as_text, crosses_itself, from_text, parse_polygon, polygon_wkt
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


def site_out(r) -> m.Site:
    address = None
    if r["street"] or r["city"]:
        address = m.Address(street=r["street"], house_number=r["number"], addition=r["number_addition"],
                            zip=r["zip"], district=r["district"], city=r["city"])
    border = [m.GeoPoint(latitude=lat, longitude=lon) for lon, lat in parse_polygon(r["wkt"])]
    return m.Site(id=r["id_site"], name=r["name"], description=r["description"], address=address, border=border,
                  crs_id=r["id_crs"])


def spot_out(r) -> m.Spot:
    return m.Spot(id=r["id_spot"], name=r["name"], description=r["description"], x=r["x"], y=r["y"], z=r["z"],
                  crs_id=r["id_crs"], site_id=r["id_site"])


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
        return [site_out(r) for r in conn.execute(sites_query()).mappings()]


@router.post("/sites", status_code=201)
def create_site(body: m.SiteIn, caller: Sampler, engine: Database) -> m.Site:
    """Adds an area by its border. The border must not cross itself."""
    corners = [(p.longitude, p.latitude) for p in body.border]
    if len(set(corners)) < 3:
        raise APIError(422, "border_too_small", "A border needs at least three different corners.")
    if crosses_itself(corners):
        raise APIError(422, "border_crosses_itself", "The border crosses itself. Draw its corners in order.")
    with writing(engine, caller.person_id, body.note) as conn:
        require_free(conn, site, body.name, "site")
        site_id = conn.execute(insert(site).values(
            name=body.name, description=body.description, borders=from_text(polygon_wkt(corners)),
            id_crs=reference_system(conn, body.crs_id), vis=True,
        )).inserted_primary_key[0]
        return site_out(conn.execute(sites_query().where(site.c.id_site == site_id)).mappings().one())


@router.get("/spots")
def list_spots(_: CurrentCaller, engine: Database) -> list[m.Spot]:
    with engine.connect() as conn:
        rows = conn.execute(select(spot).where(visible(spot)).order_by(spot.c.name)).mappings()
        return [spot_out(r) for r in rows]


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
        return spot_out(conn.execute(select(spot).where(spot.c.id_spot == spot_id)).mappings().one())
