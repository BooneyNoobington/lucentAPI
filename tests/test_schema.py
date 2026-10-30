import pytest
from sqlalchemy import text

from lucentapi.checks import SchemaMismatch, check_schema
from lucentapi.db import writing


def test_dev_database_passes_the_check(engine):
    check_schema(engine)


def test_missing_column_is_reported(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER trg_task_insert")
        conn.exec_driver_sql("DROP TRIGGER trg_task_update")
        conn.exec_driver_sql("ALTER TABLE task DROP COLUMN done")
    with pytest.raises(SchemaMismatch) as caught:
        check_schema(engine)
    assert "column task.done is missing" in str(caught.value)
    assert "log triggers are missing (trg_task_insert" in str(caught.value)


def test_writing_sets_and_clears_the_editor(engine):
    with writing(engine, 5, "Grund") as conn:
        assert conn.execute(text("SELECT current_user_id, change_note FROM lucentapi_session")).one() == (5, "Grund")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_user_id, change_note FROM lucentapi_session")).one() == (None, None)
