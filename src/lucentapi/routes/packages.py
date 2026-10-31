"""lucent packages: the master data as one document, importing it, and the format's JSON Schema."""

import json
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from .. import models as m
from .. import package, package_import
from ..auth import Caller, CurrentCaller, require
from ..db import db
from ..errors import APIError

router = APIRouter(tags=["packages"])
Database = Annotated[Engine, Depends(db)]
Admin = Annotated[Caller, Depends(require("admin"))]
Note = Annotated[str | None, Query(max_length=400, description="Why, for lucentBASE's log. "
                                                                "Defaults to the package's description.")]

PACKAGE_BODY = {"requestBody": {"required": True, "content": {
    "application/json": {"schema": {"$ref": "#/components/schemas/Package"}},
    "application/yaml": {"schema": {"type": "string"}},
}}}


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
    units, parameters, methods, documents, thresholds, devices and locations, in the order they
    were entered. Measurements are not part of it."""
    with engine.connect() as conn:
        exported = package.export(conn)
    if output == "yaml":
        text = package.as_yaml(exported, schema_url=str(request.url_for("package_schema")))
        return Response(text, media_type="application/yaml",
                        headers={"Content-Disposition": 'inline; filename="lucent-package.yaml"'})
    return exported


async def read_package(request: Request) -> tuple[package.Package, object]:
    """The package in the request, JSON or YAML, and a function from a path in it to its line."""
    text = (await request.body()).decode("utf-8", errors="replace")
    is_yaml = "yaml" in request.headers.get("content-type", "")
    line_of = package.yaml_lines(text) if is_yaml else (lambda _: None)
    try:
        data = package.load_yaml(text) if is_yaml else json.loads(text)
    except (yaml.YAMLError, ValueError) as error:  # ValueError: JSON, or a YAML date that isn't one
        mark = getattr(error, "problem_mark", None)
        line = mark.line + 1 if mark else getattr(error, "lineno", None)
        problem = m.PackageProblem(path="package", line=line, message=str(error))
        raise APIError(422, "package_unreadable", f"The package isn't valid {'YAML' if is_yaml else 'JSON'}.",
                       details=[problem.model_dump()])
    try:
        return package.Package.model_validate(data), line_of
    except ValidationError as error:
        problems = [m.PackageProblem(path=package_import.path_text(e["loc"]) or "package", line=line_of(e["loc"]),
                                     message=e["msg"]).model_dump() for e in error.errors()]
        raise APIError(422, "package_invalid", "The package doesn't follow the format.", details=problems)


@router.post("/v1/package/plan", openapi_extra=PACKAGE_BODY)
async def plan_package(request: Request, caller: CurrentCaller, engine: Database, note: Note = None) -> m.PackagePlan:
    """What importing the package would create and change, and its problems. Changes nothing.

    An entry is found by its natural key; only what it mentions changes; nothing is deleted. A
    changed method (different effects) needs a new iteration."""
    found, line_of = await read_package(request)
    return await run_in_threadpool(package_import.plan, engine, found, caller.person_id,
                                   note or found.description, line_of)


@router.post("/v1/package/apply", openapi_extra=PACKAGE_BODY,
             responses={422: {"description": "The package has problems; nothing was applied."}})
async def apply_package(request: Request, caller: Admin, engine: Database, response: Response,
                        note: Note = None) -> m.PackagePlan:
    """Imports the package (admin): all of it in one transaction, or nothing if there is any
    problem. lucentBASE logs every change under the caller."""
    found, line_of = await read_package(request)
    result = await run_in_threadpool(package_import.apply, engine, found, caller.person_id,
                                     note or found.description, line_of)
    if not result.applied:
        response.status_code = 422
    return result


@router.get(package.SCHEMA_PATH, include_in_schema=False, name="package_schema")
def package_schema() -> dict:
    """The JSON Schema of lucent packages, without a token, so editors can fetch it."""
    return package.json_schema()
