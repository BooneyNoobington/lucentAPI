"""Reference data: organizations, locations, the scientific catalog, devices and documents."""

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Engine, select

from .. import models as m
from ..auth import CurrentCaller
from ..db import db, from_db_time, visible
from ..etag import etag
from ..geometry import as_text, parse_polygon
from ..schema import (
    COLLECTION_CLASSES,
    action,
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
    link,
    matrix,
    organization,
    parameter,
    site,
    spot,
    street,
    threshold,
    unit,
    unit_notation,
)

router = APIRouter(prefix="/v1", tags=["reference"])
Database = Annotated[Engine, Depends(db)]


def sampling_parameter_id(request: Request, conn) -> int | None:
    name = request.app.state.settings.sampling_time_parameter
    if not name:
        return None
    return conn.scalar(select(parameter.c.id_parameter).where(parameter.c.name == name, visible(parameter)))


@router.get("/configuration")
def configuration(_: CurrentCaller, request: Request, engine: Database) -> m.Configuration:
    """What clients need to know about this lucentBASE."""
    with engine.connect() as conn:
        return m.Configuration(sampling_time_parameter_id=sampling_parameter_id(request, conn),
                               collection_classes=list(COLLECTION_CLASSES))


@router.get("/organizations", tags=["people"])
def organizations(_: CurrentCaller, engine: Database) -> list[m.Organization]:
    with engine.connect() as conn:
        rows = conn.execute(select(organization).where(visible(organization)).order_by(organization.c.name)).mappings()
        return [m.Organization(id=r["id_organization"], name=r["name"], shorthand=r["shorthand"],
                               relation=r["relation"]) for r in rows]


@router.get("/crs", tags=["locations"])
def reference_systems(_: CurrentCaller, engine: Database) -> list[m.ReferenceSystem]:
    with engine.connect() as conn:
        rows = conn.execute(select(crs).where(visible(crs))).mappings()
        return [m.ReferenceSystem(id=r["id_crs"], epsg=r["epsg"], name=r["name"], description=r["description"])
                for r in rows]


@router.get("/sites", tags=["locations"])
def sites(_: CurrentCaller, engine: Database) -> list[m.Site]:
    """Sites with address and border. WKT stores (longitude latitude); the border is converted."""
    query = (
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
    with engine.connect() as conn:
        result = []
        for r in conn.execute(query).mappings():
            address = None
            if r["street"] or r["city"]:
                address = m.Address(street=r["street"], house_number=r["number"], addition=r["number_addition"],
                                    zip=r["zip"], district=r["district"], city=r["city"])
            border = [m.GeoPoint(latitude=lat, longitude=lon) for lon, lat in parse_polygon(r["wkt"])]
            result.append(m.Site(id=r["id_site"], name=r["name"], description=r["description"], address=address,
                                 border=border, crs_id=r["id_crs"]))
        return result


@router.get("/spots", tags=["locations"])
def spots(_: CurrentCaller, engine: Database) -> list[m.Spot]:
    with engine.connect() as conn:
        rows = conn.execute(select(spot).where(visible(spot)).order_by(spot.c.name)).mappings()
        return [m.Spot(id=r["id_spot"], name=r["name"], description=r["description"], x=r["x"], y=r["y"], z=r["z"],
                       crs_id=r["id_crs"], site_id=r["id_site"]) for r in rows]


@router.get("/matrices", tags=["catalog"])
def matrices(_: CurrentCaller, engine: Database) -> list[m.Matrix]:
    with engine.connect() as conn:
        rows = conn.execute(select(matrix).where(visible(matrix)).order_by(matrix.c.name)).mappings()
        return [m.Matrix(id=r["id_matrix"], name=r["name"], description=r["description"]) for r in rows]


@router.get("/collection-classes", tags=["catalog"])
def collection_classes(_: CurrentCaller) -> list[m.CollectionClass]:
    """The classes lucentBASE allows for a collection (its collection_class_choices)."""
    return list(COLLECTION_CLASSES)


@router.get("/dimensions", tags=["catalog"])
def dimensions(_: CurrentCaller, engine: Database) -> list[m.Dimension]:
    with engine.connect() as conn:
        rows = conn.execute(select(dimension).where(visible(dimension))).mappings()
        return [m.Dimension(id=r["id_dimension"], name=r["name"], description=r["description"]) for r in rows]


@router.get("/units", tags=["catalog"])
def units(_: CurrentCaller, engine: Database) -> list[m.Unit]:
    with engine.connect() as conn:
        notations = defaultdict(list)
        for unit_id, notation in conn.execute(
            select(unit_notation.c.id_unit, unit_notation.c.notation)
            .where(visible(unit_notation)).order_by(unit_notation.c.id_unit_notation)
        ):
            notations[unit_id].append(notation)
        rows = conn.execute(select(unit).where(visible(unit))).mappings()
        return [m.Unit(id=r["id_unit"], name=r["name"], description=r["description"],
                       notations=notations[r["id_unit"]], dimension_id=r["id_dimension"]) for r in rows]


@router.get("/parameters", tags=["catalog"])
def parameters(_: CurrentCaller, engine: Database) -> list[m.Parameter]:
    with engine.connect() as conn:
        rows = conn.execute(select(parameter).where(visible(parameter)).order_by(parameter.c.id_parameter)).mappings()
        return [m.Parameter(id=r["id_parameter"], name=r["name"], description=r["description"], unit_id=r["id_unit"],
                            value_class=r["value_class"]) for r in rows]


@router.get("/devices", tags=["catalog"])
def devices(_: CurrentCaller, engine: Database) -> list[m.Device]:
    with engine.connect() as conn:
        rows = conn.execute(select(device).where(visible(device)).order_by(device.c.name)).mappings()
        return [device_out(dict(r)) for r in rows]


def device_out(r: dict) -> m.Device:
    return m.Device(id=r["id_device"], name=r["name"], number=r["number"], manufacturer_id=r["id_manufacturer"],
                    operator_id=r["id_operator"], active=bool(r["active"]), etag=etag(r))


def grouped(conn, table, key: str, column: str) -> dict[int, list[int]]:
    result = defaultdict(list)
    for k, v in conn.execute(select(table.c[key], table.c[column]).where(visible(table))
                             .order_by(table.c[f"id_{table.name}"])):
        result[k].append(v)
    return result


@router.get("/documents", tags=["catalog"])
def documents(_: CurrentCaller, engine: Database) -> list[m.Document]:
    """Links: SOPs, standards, regulations, any file or web page."""
    with engine.connect() as conn:
        actions_by_link = grouped(conn, j_action_link, "id_link", "id_action")
        rows = conn.execute(select(link).where(visible(link)).order_by(link.c.name)).mappings()
        return [m.Document(id=r["id_link"], name=r["name"], uri=r["uri"], file_path=r["file_path"],
                           accepted=bool(r["accepted"]), sha256=r["sha256"], action_ids=actions_by_link[r["id_link"]])
                for r in rows]


@router.get("/actions", tags=["catalog"])
def actions(_: CurrentCaller, engine: Database) -> list[m.Action]:
    """Actions with their effects, documents, devices, people and dependencies."""
    with engine.connect() as conn:
        documents_by_action = grouped(conn, j_action_link, "id_action", "id_link")
        devices_by_action = grouped(conn, j_action_device, "id_action", "id_device")
        people_by_action = defaultdict(list)
        for r in conn.execute(select(j_action_person).where(visible(j_action_person))).mappings():
            people_by_action[r["id_action"]].append(m.ActionPerson(person_id=r["id_person"], role=r["role"]))
        dependencies = defaultdict(list)
        for r in conn.execute(select(j_action_action).where(visible(j_action_action))).mappings():
            dependencies[r["id_action1"]].append(m.Dependency(
                action_id=r["id_action2"], dependency_class=r["dependency_class"], description=r["description"]))
        effects = defaultdict(list)
        for r in conn.execute(select(effect).where(visible(effect)).order_by(effect.c.id_effect)).mappings():
            effects[r["id_action"]].append(m.Effect(
                id=r["id_effect"], action_id=r["id_action"], parameter_id=r["id_parameter"],
                uncertainty=r["uncertainty"], uncertainty_class=r["uncertainty_class"],
                uncertainty_unit_id=r["uncertainty_id_unit"], calculation=r["calculation"]))

        rows = conn.execute(select(action).where(visible(action)).order_by(action.c.id_action)).mappings()
        return [m.Action(
            id=r["id_action"], name=r["name"], iteration=from_db_time(r["iteration"]), active=bool(r["active"]),
            description=r["description"], document_ids=documents_by_action[r["id_action"]],
            device_ids=devices_by_action[r["id_action"]], people=people_by_action[r["id_action"]],
            dependencies=dependencies[r["id_action"]], effects=effects[r["id_action"]],
        ) for r in rows]


@router.get("/coefficients", tags=["catalog"])
def coefficients(_: CurrentCaller, engine: Database) -> list[m.Coefficient]:
    """Pairs of effects that belong together, e.g. conductivity and the temperature it refers to."""
    with engine.connect() as conn:
        rows = conn.execute(select(coefficient).where(visible(coefficient))).mappings()
        return [m.Coefficient(id=r["id_coefficient"], effect1_id=r["id_effect1"], effect2_id=r["id_effect2"],
                              description=r["description"]) for r in rows]


@router.get("/thresholds", tags=["catalog"])
def thresholds(_: CurrentCaller, engine: Database) -> list[m.Threshold]:
    """Limits of parameters, each in the context of a document (e.g. a regulation or SOP)."""
    with engine.connect() as conn:
        rows = conn.execute(select(threshold).where(visible(threshold))).mappings()
        return [m.Threshold(id=r["id_threshold"], parameter_id=r["id_parameter"], document_id=r["id_link"],
                            lower_limit=r["lower_limit"], upper_limit=r["upper_limit"],
                            description=r["description"]) for r in rows]
