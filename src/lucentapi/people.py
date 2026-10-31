"""People and what they may do, as lucentBASE stores it.

Signing in is the identity provider's job. lucentBASE decides who a token belongs to
(`person.linux_user`), whether that person may still sign in (`person.active`) and what they
may do (`j_person_role`). A role is revoked by hiding its junction row and granted again by
showing it, so `l_j_person_role` keeps the whole story.

lucentBASE's log tables record that a row changed, not what changed. So the notes the API
writes say it: "Granted reviewer", "Deactivated", "Changed surname, position", followed by
the reason the client gave.
"""

from sqlalchemy import Connection, insert, literal, select, union_all, update

from . import models as m
from .config import ROLES
from .db import from_db_time, set_editor, visible
from .etag import etag
from .schema import j_person_organization, j_person_role, log_table, person, role


def note(what: str, reason: str | None) -> str:
    """A log note: what happened, then why."""
    text = f"{what}: {reason}" if reason else what
    return text[:500]


def roles_of(conn: Connection, person_id: int) -> frozenset[str]:
    query = (select(role.c.name).select_from(j_person_role.join(role))
             .where(j_person_role.c.id_person == person_id, visible(j_person_role), visible(role)))
    return frozenset(conn.scalars(query)) & set(ROLES)


def granted(conn: Connection) -> dict[int, set[str]]:
    """Everyone's roles, by person."""
    result: dict[int, set[str]] = {}
    query = (select(j_person_role.c.id_person, role.c.name).select_from(j_person_role.join(role))
             .where(visible(j_person_role), visible(role)))
    for person_id, name in conn.execute(query):
        if name in ROLES:
            result.setdefault(person_id, set()).add(name)
    return result


def people_query():
    """Visible people with their position in their own organization."""
    return (
        select(person, j_person_organization.c.position)
        .outerjoin(j_person_organization,
                   (j_person_organization.c.id_person == person.c.id_person)
                   & (j_person_organization.c.id_organization == person.c.id_organization)
                   & visible(j_person_organization))
        .where(visible(person))
        .order_by(person.c.surname, person.c.given_name, person.c.id_person)
    )


def person_data(row, roles: set[str]) -> dict:
    """What the API shows of a person; its hash is the person's ETag."""
    return {
        "id": row["id_person"], "given_name": row["given_name"], "surname": row["surname"],
        "linux_user": row["linux_user"], "organization_id": row["id_organization"], "position": row["position"],
        "active": bool(row["active"]), "roles": sorted(roles, key=ROLES.index),
    }


def person_out(data: dict) -> m.Person:
    return m.Person(**data, etag=etag(data))


def everyone(conn: Connection) -> list[dict]:
    roles = granted(conn)
    seen, result = set(), []
    for row in conn.execute(people_query()).mappings():
        if row["id_person"] not in seen:
            seen.add(row["id_person"])
            result.append(person_data(row, roles.get(row["id_person"], set())))
    return result


def one(conn: Connection, person_id: int, lock: bool = False) -> dict | None:
    query = people_query().where(person.c.id_person == person_id)
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    return None if row is None else person_data(row, set(roles_of(conn, person_id)))


def active_admins(conn: Connection) -> set[int]:
    """People who can sign in and administer."""
    query = (select(person.c.id_person).select_from(person.join(j_person_role).join(role))
             .where(role.c.name == "admin", visible(role), visible(j_person_role), visible(person),
                    person.c.active == True))  # noqa: E712 (SQL comparison)
    return set(conn.scalars(query))


def set_roles(conn: Connection, person_id: int, wanted: set[str], editor_id: int | None,
              reason: str | None = None) -> list[str]:
    """Grants and revokes so that the person has exactly the `wanted` roles; returns what it did.

    Roles lucentBASE has but the API doesn't know are left alone.
    """
    role_ids = dict(conn.execute(select(role.c.name, role.c.id_role).where(visible(role))).all())
    rows = {name: (junction_id, bool(shown)) for junction_id, name, shown in conn.execute(
        select(j_person_role.c.id_j_person_role, role.c.name, j_person_role.c.vis)
        .select_from(j_person_role.join(role)).where(j_person_role.c.id_person == person_id))}
    done = []
    for name in ROLES:
        if name not in role_ids:
            continue
        junction_id, shown = rows.get(name, (None, False))
        if (name in wanted) == shown:
            continue
        what = f"Granted {name}" if name in wanted else f"Revoked {name}"
        set_editor(conn, editor_id, note(what, reason))
        if junction_id is None:
            conn.execute(insert(j_person_role).values(id_person=person_id, id_role=role_ids[name], vis=True))
        else:
            conn.execute(update(j_person_role).where(j_person_role.c.id_j_person_role == junction_id)
                         .values(vis=name in wanted))
        done.append(what)
    return done


def history(conn: Connection, person_id: int) -> list[m.HistoryEntry]:
    """Changes to the person, their roles and their organizations, newest first, from the log."""
    l_person, l_role, l_organization = log_table(person), log_table(j_person_role), log_table(j_person_organization)
    entries = union_all(
        # rank: what one request writes in the same second, newest first (roles after the person)
        select(l_person.c.t, l_person.c.id_l_person.label("log_id"), l_person.c.id_editor, l_person.c.modification,
               l_person.c.note, literal("person").label("subject"), literal(None).label("role"), literal(2).label("rank"))
        .where(l_person.c.id_person == person_id),
        select(l_role.c.t, l_role.c.id_l_j_person_role, l_role.c.id_editor, l_role.c.modification, l_role.c.note,
               literal("role"), role.c.name, literal(0))
        .select_from(l_role.join(j_person_role).join(role))
        .where(j_person_role.c.id_person == person_id),
        select(l_organization.c.t, l_organization.c.id_l_j_person_organization, l_organization.c.id_editor,
               l_organization.c.modification, l_organization.c.note, literal("organization"), literal(None), literal(1))
        .select_from(l_organization.join(j_person_organization))
        .where(j_person_organization.c.id_person == person_id),
    ).subquery()
    # Mappings, since a Row's .t is SQLAlchemy's typed tuple, not the column t.
    rows = conn.execute(select(entries).order_by(entries.c.t.desc(), entries.c.rank, entries.c.log_id.desc()))
    return [m.HistoryEntry(t=from_db_time(r["t"]), editor_id=r["id_editor"],
                           modification="created" if r["modification"] == "I" else "changed",
                           subject=r["subject"], role=r["role"] if r["role"] in ROLES else None, note=r["note"])
            for r in rows.mappings()]
