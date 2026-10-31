"""People, their roles and their history. Anyone may read; only admins change.

Two rules keep a lucentBASE manageable: nobody deactivates themselves, and the last active
admin keeps the admin role.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import Connection, Engine, insert, select, update

from .. import models as m
from .. import people
from ..auth import Caller, CurrentCaller, require
from ..config import ROLES
from ..db import db, set_editor, visible, writing
from ..errors import APIError, not_found
from ..etag import check
from ..schema import j_person_organization, organization, person, role

router = APIRouter(prefix="/v1", tags=["people"])
Database = Annotated[Engine, Depends(db)]
Admin = Annotated[Caller, Depends(require("admin"))]


def require_organization(conn: Connection, organization_id: int) -> None:
    query = select(organization.c.id_organization).where(organization.c.id_organization == organization_id,
                                                          visible(organization))
    if conn.scalar(query) is None:
        raise APIError(422, "unknown_reference", f"There is no organization {organization_id}.")


def require_free(conn: Connection, linux_user: str, person_id: int | None = None) -> None:
    # The UNIQUE constraint counts hidden people too.
    taken = conn.scalar(select(person.c.id_person).where(person.c.linux_user == linux_user))
    if taken is not None and taken != person_id:
        raise APIError(409, "linux_user_taken", f"Another person already signs in as {linux_user!r}.")


def keep_an_admin(conn: Connection, person_id: int) -> None:
    """Refuses a change that would leave lucentBASE without an active admin."""
    if people.active_admins(conn) == {person_id}:
        raise APIError(409, "last_admin", "This is the last active admin. Make someone else admin first.")


def set_position(conn: Connection, person_id: int, organization_id: int, position: str | None,
                 editor_id: int, reason: str | None) -> None:
    """The person's position in an organization, in j_person_organization."""
    row = conn.execute(
        select(j_person_organization.c.id_j_person_organization, j_person_organization.c.position)
        .where(j_person_organization.c.id_person == person_id,
               j_person_organization.c.id_organization == organization_id, visible(j_person_organization))
    ).first()
    if row is None:
        set_editor(conn, editor_id, people.note("Joined the organization", reason))
        conn.execute(insert(j_person_organization).values(
            id_person=person_id, id_organization=organization_id, position=position, vis=True))
    elif row.position != position:
        set_editor(conn, editor_id, people.note("Changed position", reason))
        conn.execute(update(j_person_organization)
                     .where(j_person_organization.c.id_j_person_organization == row.id_j_person_organization)
                     .values(position=position))


def load(conn: Connection, person_id: int) -> dict:
    data = people.one(conn, person_id, lock=True)
    if data is None:
        raise not_found("person", person_id)
    return data


@router.get("/me")
def me(caller: CurrentCaller, engine: Database) -> m.Me:
    """The person behind the token and their roles."""
    with engine.connect() as conn:
        data = people.one(conn, caller.person_id)
    return m.Me(person=people.person_out(data), roles=data["roles"])


@router.get("/people")
def list_people(_: CurrentCaller, engine: Database) -> list[m.Person]:
    """Everyone, including people who can no longer sign in."""
    with engine.connect() as conn:
        return [people.person_out(data) for data in people.everyone(conn)]


@router.get("/people/{person_id}")
def get_person(person_id: int, _: CurrentCaller, engine: Database) -> m.Person:
    with engine.connect() as conn:
        data = people.one(conn, person_id)
    if data is None:
        raise not_found("person", person_id)
    return people.person_out(data)


@router.get("/roles")
def list_roles(_: CurrentCaller, engine: Database) -> list[m.RoleInfo]:
    """The roles lucentBASE grants and the API checks. admin may do everything."""
    with engine.connect() as conn:
        rows = conn.execute(select(role.c.name, role.c.description).where(visible(role))).all()
    found = {name: description for name, description in rows if name in ROLES}
    return [m.RoleInfo(name=name, description=found[name]) for name in ROLES if name in found]


@router.post("/people", status_code=201)
def create_person(body: m.PersonIn, request: Request, response: Response, caller: Admin,
                  engine: Database) -> m.Person:
    """Adds a person. They can sign in once their identity provider account has `linux_user` as
    username."""
    with writing(engine, caller.person_id, people.note("Created", body.note)) as conn:
        if body.linux_user is not None:
            require_free(conn, body.linux_user)
        if body.organization_id is not None:
            require_organization(conn, body.organization_id)
        elif body.position is not None:
            raise APIError(422, "position_needs_organization", "A position belongs to an organization.")
        person_id = conn.execute(insert(person).values(
            given_name=body.given_name, surname=body.surname, linux_user=body.linux_user,
            id_organization=body.organization_id, active=True, vis=True,
        )).inserted_primary_key[0]
        if body.organization_id is not None:
            set_position(conn, person_id, body.organization_id, body.position, caller.person_id, body.note)
        people.set_roles(conn, person_id, set(body.roles), caller.person_id, body.note)
        data = people.one(conn, person_id)
    response.headers["Location"] = f"{request.scope.get('root_path', '')}/v1/people/{person_id}"
    return people.person_out(data)


@router.patch("/people/{person_id}")
def update_person(person_id: int, body: m.PersonPatch, request: Request, caller: Admin,
                  engine: Database) -> m.Person:
    """Changes names, sign-in, organization, position or whether the person is active. Needs the
    person's ETag in If-Match."""
    with writing(engine, caller.person_id, body.note) as conn:
        current = load(conn, person_id)
        check(request, current, required=True)
        changes, said = {}, []
        for field, column, label in [("given_name", "given_name", "given name"), ("surname", "surname", "surname"),
                                     ("linux_user", "linux_user", "Linux user"),
                                     ("organization_id", "id_organization", "organization")]:
            value = getattr(body, field)
            if value is not None and value != current[field]:
                changes[column] = value
                said.append(label)
        if "linux_user" in changes:
            require_free(conn, changes["linux_user"], person_id)
        if "id_organization" in changes:
            require_organization(conn, changes["id_organization"])
        if body.active is not None and body.active != current["active"]:
            if not body.active:
                if person_id == caller.person_id:
                    raise APIError(409, "cannot_deactivate_yourself", "Nobody can deactivate themselves.")
                keep_an_admin(conn, person_id)
            changes["active"] = body.active
        if changes:
            what = [] if "active" not in changes else ["Reactivated" if body.active else "Deactivated"]
            if said:
                what.append("Changed " + ", ".join(said))
            set_editor(conn, caller.person_id, people.note("; ".join(what), body.note))
            conn.execute(update(person).where(person.c.id_person == person_id).values(changes))
        organization_id = changes.get("id_organization", current["organization_id"])
        if body.position is not None or "id_organization" in changes:
            if organization_id is None:
                raise APIError(422, "position_needs_organization", "A position belongs to an organization.")
            position = body.position if body.position is not None else current["position"]
            set_position(conn, person_id, organization_id, position or None, caller.person_id, body.note)
        return people.person_out(people.one(conn, person_id))


@router.put("/people/{person_id}/roles")
def set_roles(person_id: int, body: m.RolesIn, request: Request, caller: Admin, engine: Database) -> m.Person:
    """Grants and revokes so the person has exactly these roles. Needs the person's ETag in
    If-Match."""
    with writing(engine, caller.person_id, body.note) as conn:
        current = load(conn, person_id)
        check(request, current, required=True)
        if "admin" in current["roles"] and "admin" not in body.roles:
            keep_an_admin(conn, person_id)
        people.set_roles(conn, person_id, set(body.roles), caller.person_id, body.note)
        return people.person_out(people.one(conn, person_id))


@router.get("/people/{person_id}/history")
def person_history(person_id: int, _: CurrentCaller, engine: Database) -> list[m.HistoryEntry]:
    """Changes to the person, their roles and their organization, newest first, from lucentBASE's
    log. Rows from lucentBASE's initial data have no log entries."""
    with engine.connect() as conn:
        if people.one(conn, person_id) is None:
            raise not_found("person", person_id)
        return people.history(conn, person_id)
