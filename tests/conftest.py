from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import select

from lucentapi import demo, devdb
from lucentapi.config import ROLES, Settings
from lucentapi.db import make_engine, writing
from lucentapi.devtokens import AUDIENCE, ISSUER, DevSigner
from lucentapi.main import create_app
from lucentapi.people import set_roles
from lucentapi.schema import person

NOW = datetime(2026, 10, 29, 12, 0)


@pytest.fixture(scope="session")
def signer(tmp_path_factory) -> DevSigner:
    return DevSigner(tmp_path_factory.mktemp("keys"))


@pytest.fixture
def settings(signer) -> Settings:
    return Settings(
        database_url="sqlite://",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_file=signer.jwks_file,
        numbering_offset=demo.NUMBERING_OFFSET,
        sampling_time_parameter=demo.SAMPLING_TIME,
        _env_file=None,
    )


@pytest.fixture
def engine(settings):
    """lucentBASE on SQLite, with imitated log triggers and the demo data."""
    engine = make_engine(settings.database_url)
    devdb.create(engine)
    with engine.begin() as conn:
        # The demo data is logged at NOW, what the tests change at the real time after it.
        conn.exec_driver_sql(f"UPDATE lucentapi_session SET frozen_t = '{NOW:%Y-%m-%d %H:%M:%S}'")
        demo.load(conn, now=NOW)
        conn.exec_driver_sql("UPDATE lucentapi_session SET frozen_t = NULL")
    return engine


@pytest.fixture
def app(settings, engine):
    return create_app(settings, engine)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def signed_in(signer, engine):
    """Headers for a request as `username`. With `roles`, the person gets exactly these roles in
    lucentBASE first; without, they keep the ones they have (see demo.DEMO_ROLES)."""

    def headers(username: str, *roles: str, **extra) -> dict:
        if roles:
            with writing(engine, demo.SYSTEM, "Test") as conn:
                person_id = conn.scalar(select(person.c.id_person).where(person.c.linux_user == username))
                if person_id is not None:
                    set_roles(conn, person_id, set(roles) & set(ROLES), demo.SYSTEM)
        return {"Authorization": f"Bearer {signer.token(username)}", **extra}

    return headers


@pytest.fixture
def as_user(signer, engine):
    return signed_in(signer, engine)
