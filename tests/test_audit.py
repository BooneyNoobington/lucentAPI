from sqlalchemy import select, update

from lucentapi import audit
from lucentapi.schema import log_table, task


def test_chain_links_the_rows_of_one_record(engine):
    with engine.begin() as conn:
        audit.record(conn, task, 1, 23, before=None, after={"request": "A"})
        audit.record(conn, task, 1, 24, before={"done": False}, after={"done": True}, note="fertig")
        audit.record(conn, task, 2, 24, before=None, after={"request": "B"})
        rows = conn.execute(select(log_table(task)).order_by(log_table(task).c.id_l_task)).mappings().all()
        assert rows[1]["checksum"] == audit.checksum(rows[0]["checksum"], rows[1]["change"], rows[1]["t"], 24, "fertig")
        assert rows[2]["checksum"] == audit.checksum(None, rows[2]["change"], rows[2]["t"], 24, None)
        assert audit.verify_chain(conn, task, 1) == []


def test_tampering_breaks_the_chain(engine):
    with engine.begin() as conn:
        audit.record(conn, task, 1, 23, before=None, after={"request": "A"})
        audit.record(conn, task, 1, 24, before={"done": False}, after={"done": True})
        audit.record(conn, task, 1, 24, before={"done": True}, after={"done": False})
        log = log_table(task)
        first = conn.scalar(select(log.c.id_l_task).order_by(log.c.id_l_task).limit(1))
        conn.execute(update(log).where(log.c.id_l_task == first).values(id_editor=29))
        assert audit.verify_chain(conn, task, 1) == [first]


def test_unchanged_update_is_not_logged(engine):
    with engine.begin() as conn:
        audit.record(conn, task, 1, 23, before={"done": True}, after={"done": True})
        assert conn.execute(select(log_table(task))).first() is None
