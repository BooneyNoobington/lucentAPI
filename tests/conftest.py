from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from lucentapi import demo, devdb
from lucentapi.config import Settings
from lucentapi.db import make_engine
from lucentapi.devtokens import AUDIENCE, ISSUER, DevSigner
from lucentapi.main import create_app

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
        demo.load(conn, now=NOW)
    return engine


@pytest.fixture
def app(settings, engine):
    return create_app(settings, engine)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def as_user(signer):
    """Headers for a request as `username` with `roles`."""

    def headers(username: str, *roles: str, **extra) -> dict:
        return {"Authorization": f"Bearer {signer.token(username, list(roles))}", **extra}

    return headers
