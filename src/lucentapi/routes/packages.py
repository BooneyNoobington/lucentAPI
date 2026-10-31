"""lucent packages: the master data as one document, and the format's JSON Schema."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import Engine

from .. import package
from ..auth import CurrentCaller
from ..db import db

router = APIRouter(tags=["packages"])
Database = Annotated[Engine, Depends(db)]


@router.get(
    "/v1/package",
    responses={200: {"content": {"application/yaml": {"schema": {"type": "string"}}},
                     "description": "The package; YAML with `format=yaml`."}},
)
def export_package(
    _: CurrentCaller, request: Request, engine: Database,
    output: Annotated[Literal["json", "yaml"], Query(alias="format")] = "json",
) -> package.Package:
    """The master data of this lucentBASE as a lucent package: organizations, people and roles,
    units, parameters, methods, documents, thresholds, devices and locations. Measurements are
    not part of it."""
    with engine.connect() as conn:
        exported = package.export(conn)
    if output == "yaml":
        text = package.as_yaml(exported, schema_url=str(request.url_for("package_schema")))
        return Response(text, media_type="application/yaml",
                        headers={"Content-Disposition": 'inline; filename="lucent-package.yaml"'})
    return exported


@router.get(package.SCHEMA_PATH, include_in_schema=False, name="package_schema")
def package_schema() -> dict:
    """The JSON Schema of lucent packages, without a token, so editors can fetch it."""
    return package.json_schema()
