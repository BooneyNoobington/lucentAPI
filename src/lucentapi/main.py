"""The application: `uvicorn --factory lucentapi.main:create_app`."""

from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine, text

from . import errors
from . import models as m
from .auth import KeySet
from .config import Settings
from .db import make_engine
from .routes import collections, management, reference

# The logo is a relative link, so it works under any root path (/docs and /redoc sit beside /static).
DESCRIPTION = """
<img src="static/lucentLIMS-logo.svg" alt="lucentLIMS" width="300">

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

# The lucentLIMS logo (from lucentLIMS's Branding/), for the documentation pages.
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="lucentAPI",
        version=version("lucentapi"),
        description=DESCRIPTION,
        docs_url=None,
        redoc_url=None,
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

    # FastAPI's documentation pages, with the lucentLIMS icon as favicon.
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(request: Request):
        root = request.scope.get("root_path", "")
        return get_swagger_ui_html(openapi_url=root + app.openapi_url, title=f"{app.title} · documentation",
                                   swagger_favicon_url=f"{root}/static/lucentLIMS-icon.svg")

    @app.get("/redoc", include_in_schema=False)
    def redoc(request: Request):
        root = request.scope.get("root_path", "")
        return get_redoc_html(openapi_url=root + app.openapi_url, title=f"{app.title} · documentation",
                              redoc_favicon_url=f"{root}/static/lucentLIMS-icon.svg")

    app.include_router(reference.router)
    app.include_router(collections.router)
    app.include_router(management.router)
    return app
