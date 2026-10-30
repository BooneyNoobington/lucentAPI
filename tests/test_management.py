from sqlalchemy import select, update

from lucentapi.schema import device, log_table, task


def test_tasks_mine_and_open(client, as_user):
    mine = client.get("/v1/tasks?assignee=me&open=true", headers=as_user("grindel")).json()
    assert [t["request"] for t in mine] == ["lucentLIMS an lucentAPI anbinden"]


def test_create_task(client, as_user):
    response = client.post("/v1/tasks", json={"request": "Kalibrierung prüfen", "assignee_id": 2},
                           headers=as_user("xx121"))
    assert response.status_code == 201
    assert response.json()["assigner_id"] == 6
    assert response.headers["Location"] == f"/v1/tasks/{response.json()['id']}"


def test_patch_task_needs_if_match(client, as_user):
    response = client.patch("/v1/tasks/1", json={"done": True}, headers=as_user("jd"))
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"


def test_patch_task_with_current_etag(client, engine, as_user):
    tasks = {t["id"]: t for t in client.get("/v1/tasks", headers=as_user("jd")).json()}
    response = client.patch("/v1/tasks/1", json={"done": True, "note": "erledigt"},
                            headers=as_user("jd", **{"If-Match": tasks[1]["etag"]}))
    assert response.status_code == 200
    assert response.json()["done"] is True
    assert response.json()["etag"] != tasks[1]["etag"]
    log = log_table(task)
    with engine.connect() as conn:
        entries = conn.execute(select(log.c.modification, log.c.id_editor, log.c.note)
                               .where(log.c.id_task == 1).order_by(log.c.id_l_task)).all()
    assert entries == [("I", 1, "Demo data"), ("U", 2, "erledigt")]


def test_patch_task_after_someone_else_changed_it(client, engine, as_user):
    etag = client.get("/v1/tasks", headers=as_user("jd")).json()[0]["etag"]
    with engine.begin() as conn:  # a change outside the API
        conn.execute(update(task).where(task.c.id_task == 1).values(description="geändert"))
    response = client.patch("/v1/tasks/1", json={"done": True}, headers=as_user("jd", **{"If-Match": etag}))
    assert response.status_code == 412


def test_only_assigner_and_assignee_change_a_task(client, as_user):
    response = client.patch("/v1/tasks/1", json={"done": True}, headers=as_user("worf", **{"If-Match": "*"}))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_your_task"


def test_switch_device(client, engine, as_user):
    etag = next(d["etag"] for d in client.get("/v1/devices", headers=as_user("grindel")).json() if d["id"] == 2)
    assert client.patch("/v1/devices/2", json={"active": True},
                        headers=as_user("jd", "analyst", **{"If-Match": etag})).status_code == 403
    response = client.patch("/v1/devices/2", json={"active": True, "note": "repariert"},
                            headers=as_user("grindel", "admin", **{"If-Match": etag}))
    assert response.status_code == 200
    assert response.json()["active"] is True
    log = log_table(device)
    with engine.connect() as conn:
        assert conn.scalar(select(device.c.active).where(device.c.id_device == 2)) is True
        assert conn.scalar(select(log.c.note).where(log.c.id_device == 2, log.c.modification == "U")) == "repariert"


def test_messages(client, as_user):
    inbox = client.get("/v1/messages", headers=as_user("grindel")).json()
    assert [m["subject"] for m in inbox] == ["Messcontainer", "Nitrat an GWM 12", "Freigaberechte für Ellen"]
    assert inbox[0]["sent_at"] is not None  # from l_message
    unread = inbox[1]
    assert unread["read"] is False
    read = client.post(f"/v1/messages/{unread['id']}/read", headers=as_user("grindel"))
    assert read.json()["read"] is True
    # Somebody else's message doesn't exist for you.
    assert client.post(f"/v1/messages/{unread['id']}/read", headers=as_user("jd")).status_code == 404

    sent = client.post("/v1/messages", json={"receiver_id": 6, "subject": "Export", "body": "Liegt bereit."},
                       headers=as_user("grindel"))
    assert sent.status_code == 201
    assert client.get("/v1/messages", headers=as_user("xx121")).json()[0]["subject"] == "Export"
