"""Polygons as WKT text: MariaDB stores them spatially, SQLite (for tests) as plain text."""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import Text


class as_text(FunctionElement):
    """Read a polygon column as WKT."""

    type = Text()
    inherit_cache = True
    name = "as_text"


class from_text(FunctionElement):
    """Write WKT into a polygon column."""

    inherit_cache = True
    name = "from_text"


@compiles(as_text)
def _as_text_default(element, compiler, **kw):
    return compiler.process(element.clauses, **kw)


@compiles(as_text, "mysql")
@compiles(as_text, "mariadb")
def _as_text_mysql(element, compiler, **kw):
    return f"ST_AsText({compiler.process(element.clauses, **kw)})"


@compiles(from_text)
def _from_text_default(element, compiler, **kw):
    return compiler.process(element.clauses, **kw)


@compiles(from_text, "mysql")
@compiles(from_text, "mariadb")
def _from_text_mysql(element, compiler, **kw):
    return f"ST_GeomFromText({compiler.process(element.clauses, **kw)})"


def parse_polygon(wkt: str | None) -> list[tuple[float, float]]:
    """The outer ring of `POLYGON((x y, x y, …))` as (x, y) pairs, without the closing point."""
    if not wkt:
        return []
    start, end = wkt.find("(("), wkt.find(")")
    if start < 0 or end < 0:
        return []
    points = [tuple(float(n) for n in pair.split()) for pair in wkt[start + 2 : end].split(",")]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    return [(p[0], p[1]) for p in points]


def polygon_wkt(points: list[tuple[float, float]]) -> str:
    ring = [*points, points[0]] if points and points[0] != points[-1] else points
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"
