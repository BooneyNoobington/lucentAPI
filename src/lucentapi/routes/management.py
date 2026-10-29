"""Tasks, messages and switching devices on and off."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import Connection, Engine, insert, select, update

from .. import audit
from .. import models as m
from ..auth import Caller, CurrentCaller, require
from ..db import db, from_db_time, to_db_time, utcnow, visible
from ..errors import APIError, not_found
from ..etag import check, etag
from ..schema import device, message, person, task
from .reference import device_out

router = APIRouter(prefix="/v1", tags=["management"])
Database = Annotated[Engine, Depends(db)]


def task_out(r: dict) -> m.Task:
    return m.Task(id=r["id_task"], request=r["request"], description=r["description"], assigner_id=r["id_assigner"],
                  assignee_id=r["id_assignee"], deadline=from_db_time(r["deadline"]), done=bool(r["done"]),
                  etag=etag(r))


def load(conn: Connection, table, id_: int, what: str) -> dict:
    key = table.c[f"id_{table.name}"]
    row = conn.execute(select(table).where(key == id_, visible(table)).with_for_update()).mappings().first()
    if row is None:
        raise not_found(what, id_)
    return dict(row)


def require_person(conn: Connection, person_id: int) -> None:
    if conn.scalar(select(person.c.id_person).where(person.c.id_person == person_id, visible(person))) is None:
        raise APIError(422, "unknown_reference", f"There is no person {person_id}.")


@router.get("/tasks")
def list_tasks(
    caller: CurrentCaller, engine: Database,
    assignee: Annotated[Literal["me", "anyone"], Query()] = "anyone",
    open: Annotated[bool | None, Query(description="true: only open, false: only done.")] = None,
) -> list[m.Task]:
    """Ordered by deadline."""
    query = select(task).where(visible(task)).order_by(task.c.deadline, task.c.id_task)
    if assignee == "me":
        query = query.where(task.c.id_assignee == caller.person_id)
    if open is not None:
        query = query.where(task.c.done == (not open))
    with engine.connect() as conn:
        return [task_out(dict(r)) for r in conn.execute(query).mappings()]


@router.post("/tasks", status_code=201)
def create_task(body: m.TaskIn, response: Response, request: Request, caller: CurrentCaller,
                engine: Database) -> m.Task:
    """Anyone can give anyone a task."""
    with engine.begin() as conn:
        require_person(conn, body.assignee_id)
        row = {"request": body.request, "description": body.description, "id_assigner": caller.person_id,
               "id_assignee": body.assignee_id, "deadline": to_db_time(body.deadline), "done": False, "vis": True}
        task_id = conn.execute(insert(task).values(row)).inserted_primary_key[0]
        audit.record(conn, task, task_id, caller.person_id, before=None, after={**row, "id_task": task_id},
                     note=body.note)
        result = task_out(load(conn, task, task_id, "task"))
    response.headers["Location"] = f"{request.scope.get('root_path', '')}/v1/tasks/{task_id}"
    return result


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: m.TaskPatch, request: Request, caller: CurrentCaller,
                engine: Database) -> m.Task:
    """For the assigner, the assignee and admins. Needs the task's ETag in If-Match."""
    with engine.begin() as conn:
        row = load(conn, task, task_id, "task")
        if caller.person_id not in (row["id_assigner"], row["id_assignee"]) and not caller.has("admin"):
            raise APIError(403, "not_your_task", "Only the assigner and the assignee can change a task.")
        check(request, row, required=True)
        changes = {}
        if body.done is not None:
            changes["done"] = body.done
        if body.deadline is not None:
            changes["deadline"] = to_db_time(body.deadline)
        if body.assignee_id is not None:
            require_person(conn, body.assignee_id)
            changes["id_assignee"] = body.assignee_id
        if changes:
            conn.execute(update(task).where(task.c.id_task == task_id).values(changes))
            audit.record(conn, task, task_id, caller.person_id, before=row, after=changes, note=body.note)
        return task_out(load(conn, task, task_id, "task"))


@router.patch("/devices/{device_id}", tags=["catalog"])
def update_device(device_id: int, body: m.DevicePatch, request: Request,
                  caller: Annotated[Caller, Depends(require("admin"))], engine: Database) -> m.Device:
    """Switches a device on or off. Needs the device's ETag in If-Match."""
    with engine.begin() as conn:
        row = load(conn, device, device_id, "device")
        check(request, row, required=True)
        if bool(row["active"]) != body.active:
            conn.execute(update(device).where(device.c.id_device == device_id).values(active=body.active))
            audit.record(conn, device, device_id, caller.person_id, before=row, after={"active": body.active},
                         note=body.note)
        return device_out(load(conn, device, device_id, "device"))


def message_out(r: dict) -> m.Message:
    return m.Message(id=r["id_message"], sender_id=r["sender"], receiver_id=r["reciever"], subject=r["subject"],
                     body=r["message"], read=bool(r["read"]), sent_at=from_db_time(r["sent_at"]))


@router.get("/messages")
def inbox(caller: CurrentCaller, engine: Database) -> list[m.Message]:
    """The caller's messages, newest first."""
    query = (select(message).where(message.c.reciever == caller.person_id, visible(message))
             .order_by(message.c.sent_at.desc(), message.c.id_message.desc()))
    with engine.connect() as conn:
        return [message_out(dict(r)) for r in conn.execute(query).mappings()]


@router.post("/messages", status_code=201)
def send_message(body: m.MessageIn, caller: CurrentCaller, engine: Database) -> m.Message:
    with engine.begin() as conn:
        require_person(conn, body.receiver_id)
        row = {"sender": caller.person_id, "reciever": body.receiver_id, "subject": body.subject,
               "message": body.body, "read": False, "sent_at": utcnow(), "vis": True}
        message_id = conn.execute(insert(message).values(row)).inserted_primary_key[0]
        audit.record(conn, message, message_id, caller.person_id, before=None,
                     after={**row, "id_message": message_id})
        return message_out(load(conn, message, message_id, "message"))


@router.post("/messages/{message_id}/read")
def mark_read(message_id: int, caller: CurrentCaller, engine: Database,
              read: Annotated[bool, Query(description="false marks it unread.")] = True) -> m.Message:
    with engine.begin() as conn:
        row = load(conn, message, message_id, "message")
        if row["reciever"] != caller.person_id:
            raise not_found("message", message_id)
        if bool(row["read"]) != read:
            conn.execute(update(message).where(message.c.id_message == message_id).values(read=read))
            audit.record(conn, message, message_id, caller.person_id, before=row, after={"read": read})
        return message_out(load(conn, message, message_id, "message"))
