"""The application: `uvicorn --factory lucentapi.main:create_app`."""

from importlib.metadata import version

from fastapi import FastAPI
from sqlalchemy import Engine, text

from . import errors
from . import models as m
from .auth import KeySet
from .config import Settings
from .db import make_engine
from .routes import collections, management, reference

DESCRIPTION = """
An HTTPS API for lucentBASE, for lucentLIMS and any other client.

**Authentication.** Send an access token from the OpenID Connect provider as
`Authorization: Bearer <token>`. The token's username has to belong to a person in lucentBASE
(`person.unix_account`); every change is recorded under that person.

**Roles** come from the token: `sampler` registers collections, `analyst` records values,
`reviewer` accepts them (never their own: four-eyes principle), `admin` may do everything.
Reading needs no role.

**Audit trail.** Every write adds a row to the table's log table (`l_<table>`), chained by
SHA-256 checksums.

**Concurrent changes.** Records that can be changed carry an `etag`. Send it in `If-Match`;
if the record changed meanwhile, the answer is 412.

**Errors** look like `{"error": {"code", "message", "request_id", "details"?}}`.

**Times** are UTC.
"""


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="lucentAPI",
        version=version("lucentapi"),
        description=DESCRIPTION,
        root_path=settings.root_path,
    )
    app.state.settings = settings
    app.state.engine = engine or make_engine(settings.database_url)
    app.state.keys = KeySet(settings)
    errors.install(app)

    @app.get("/health", tags=["health"])
    def health() -> m.Health:
        """No token needed. Fails with 500 if the database can't be reached."""
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return m.Health(status="ok", version=app.version)

    app.include_router(reference.router)
    app.include_router(collections.router)
    app.include_router(management.router)
    return app
