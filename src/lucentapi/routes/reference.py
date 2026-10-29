"""Reference data: people, locations, the scientific catalog, devices and SOPs. Read-only here."""

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, select

from .. import models as m
from ..auth import CurrentCaller
from ..db import db, visible
from ..etag import etag
from ..geometry import as_text, parse_polygon
from ..schema import (
    action,
    city,
    collection_class,
    crs,
    device,
    dimension,
    district,
    house,
    j_action_collection_class,
    j_action_dependency,
    j_action_device,
    j_action_implication,
    j_action_person,
    j_action_variable,
    j_person_organization,
    j_sop_link,
    j_sop_person,
    link,
    matrix,
    organization,
    person,
    site,
    sop,
    spot,
    street,
    unit,
    unit_notation,
    variable,
)

router = APIRouter(prefix="/v1", tags=["reference"])
Database = Annotated[Engine, Depends(db)]


def person_out(row) -> m.Person:
    return m.Person(id=row["id_person"], given_name=row["given_name"], surname=row["surname"],
                    unix_account=row["unix_account"], organization_id=row["id_organization"],
                    position=row["position"])


def people_query():
    return (
        select(person, j_person_organization.c.position)
        .outerjoin(j_person_organization,
                   (j_person_organization.c.id_person == person.c.id_person) & visible(j_person_organization))
        .where(visible(person))
        .order_by(person.c.surname, person.c.given_name)
    )


@router.get("/me", tags=["people"])
def me(caller: CurrentCaller, engine: Database) -> m.Me:
    """The person behind the token and the roles it grants."""
    with engine.connect() as conn:
        row = conn.execute(people_query().where(person.c.id_person == caller.person_id)).mappings().first()
    return m.Me(person=person_out(row), roles=sorted(caller.roles))


@router.get("/people", tags=["people"])
def people(_: CurrentCaller, engine: Database) -> list[m.Person]:
    with engine.connect() as conn:
        seen, result = set(), []
        for row in conn.execute(people_query()).mappings():
            if row["id_person"] not in seen:
                seen.add(row["id_person"])
                result.append(person_out(row))
        return result


@router.get("/organizations", tags=["people"])
def organizations(_: CurrentCaller, engine: Database) -> list[m.Organization]:
    with engine.connect() as conn:
        rows = conn.execute(select(organization).where(visible(organization)).order_by(organization.c.name)).mappings()
        return [m.Organization(id=r["id_organization"], name=r["name"], shorthand=r["shorthand"]) for r in rows]


@router.get("/crs", tags=["locations"])
def reference_systems(_: CurrentCaller, engine: Database) -> list[m.ReferenceSystem]:
    with engine.connect() as conn:
        rows = conn.execute(select(crs).where(visible(crs))).mappings()
        return [m.ReferenceSystem(id=r["id_crs"], epsg=r["epsg"], name=r["name"]) for r in rows]


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
        return [m.Spot(id=r["id_spot"], name=r["name"], description=r["description"], x=r["x"], y=r["y"],
                       crs_id=r["id_crs"], site_id=r["id_site"]) for r in rows]


@router.get("/matrices", tags=["catalog"])
def matrices(_: CurrentCaller, engine: Database) -> list[m.Matrix]:
    with engine.connect() as conn:
        rows = conn.execute(select(matrix).where(visible(matrix)).order_by(matrix.c.name)).mappings()
        return [m.Matrix(id=r["id_matrix"], name=r["name"], description=r["description"]) for r in rows]


@router.get("/collection-classes", tags=["catalog"])
def collection_classes(_: CurrentCaller, engine: Database) -> list[m.CollectionClass]:
    with engine.connect() as conn:
        planned = defaultdict(list)
        for class_id, action_id in conn.execute(
            select(j_action_collection_class.c.id_collection_class, j_action_collection_class.c.id_action)
            .where(visible(j_action_collection_class)).order_by(j_action_collection_class.c.id_action)
        ):
            planned[class_id].append(action_id)
        rows = conn.execute(select(collection_class).where(visible(collection_class))).mappings()
        return [m.CollectionClass(id=r["id_collection_class"], name=r["name"], description=r["description"],
                                  action_ids=planned[r["id_collection_class"]]) for r in rows]


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
        return [m.Unit(id=r["id_unit"], name=r["name"], notations=notations[r["id_unit"]],
                       dimension_id=r["id_dimension"]) for r in rows]


@router.get("/variables", tags=["catalog"])
def variables(_: CurrentCaller, engine: Database) -> list[m.Variable]:
    with engine.connect() as conn:
        rows = conn.execute(select(variable).where(visible(variable))).mappings()
        return [m.Variable(id=r["id_variable"], name=r["name"], value_class=r["value_class"],
                           description=r["description"], dimension_id=r["id_dimension"]) for r in rows]


@router.get("/devices", tags=["catalog"])
def devices(_: CurrentCaller, engine: Database) -> list[m.Device]:
    with engine.connect() as conn:
        rows = conn.execute(select(device).where(visible(device)).order_by(device.c.name)).mappings()
        return [device_out(dict(r)) for r in rows]


def device_out(r: dict) -> m.Device:
    return m.Device(id=r["id_device"], name=r["name"], number=r["number"], manufacturer_id=r["id_manufacturer"],
                    operator_id=r["id_operator"], active=bool(r["active"]), etag=etag(r))


@router.get("/sops", tags=["catalog"])
def sops(_: CurrentCaller, engine: Database) -> list[m.SOP]:
    with engine.connect() as conn:
        documents = defaultdict(list)
        for r in conn.execute(
            select(j_sop_link.c.id_sop, link).join(link, link.c.id_link == j_sop_link.c.id_link)
            .where(visible(j_sop_link), visible(link)).order_by(link.c.id_link)
        ).mappings():
            documents[r["id_sop"]].append(m.SOPDocument(
                id=r["id_link"], name=r["name"], url=r["url"], file_path=r["file_path"],
                accepted=bool(r["accepted"]), sha256=r["SHA256"]))
        people_by_sop = defaultdict(list)
        for r in conn.execute(select(j_sop_person).where(visible(j_sop_person))).mappings():
            people_by_sop[r["id_sop"]].append(m.Assignment(person_id=r["id_person"], role=r["role"]))
        rows = conn.execute(select(sop).where(visible(sop)).order_by(sop.c.name)).mappings()
        return [m.SOP(id=r["id_sop"], name=r["name"], active=bool(r["active"]), documents=documents[r["id_sop"]],
                      people=people_by_sop[r["id_sop"]]) for r in rows]


@router.get("/actions", tags=["catalog"])
def actions(_: CurrentCaller, engine: Database) -> list[m.Action]:
    """Actions (methods) with what they measure, their devices, people and dependencies."""
    with engine.connect() as conn:
        def grouped(table, key, column):
            result = defaultdict(list)
            for k, v in conn.execute(select(table.c[key], table.c[column]).where(visible(table))):
                result[k].append(v)
            return result

        devices_by_action = grouped(j_action_device, "id_action", "id_device")
        depends_on = grouped(j_action_dependency, "id_dependant", "id_dependency")
        implies = grouped(j_action_implication, "id_implier", "id_implication")
        classes = grouped(j_action_collection_class, "id_action", "id_collection_class")
        people_by_action = defaultdict(list)
        for r in conn.execute(select(j_action_person).where(visible(j_action_person))).mappings():
            people_by_action[r["id_action"]].append(m.Assignment(person_id=r["id_person"], role=r["role"]))
        variables_by_action = defaultdict(list)
        for r in conn.execute(select(j_action_variable).where(visible(j_action_variable))
                              .order_by(j_action_variable.c.id_j_action_variable)).mappings():
            variables_by_action[r["id_action"]].append(m.ActionVariable(
                id=r["id_j_action_variable"], action_id=r["id_action"], variable_id=r["id_variable"],
                unit_id=r["id_unit"], uncertainty=r["uncertainty"], uncertainty_class=r["uncertainty_class"],
                uncertainty_unit_id=r["uncertainty_id_unit"], calculation=r["calculation"], decimals=r["decimals"],
                lower_limit=r["lower_limit"], upper_limit=r["upper_limit"]))

        rows = conn.execute(select(action).where(visible(action)).order_by(action.c.id_action)).mappings()
        return [m.Action(
            id=r["id_action"], name=r["name"], standard=r["standard"], iteration=r["iteration"],
            active=bool(r["active"]), description=r["description"], sop_id=r["id_sop"],
            device_ids=devices_by_action[r["id_action"]], people=people_by_action[r["id_action"]],
            depends_on=depends_on[r["id_action"]], implies=implies[r["id_action"]],
            collection_class_ids=classes[r["id_action"]], variables=variables_by_action[r["id_action"]],
        ) for r in rows]
