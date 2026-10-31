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


def crosses_itself(points: list[tuple[float, float]]) -> bool:
    """Whether the closed ring through `points` crosses or touches itself (not a simple polygon)."""
    ring = [*points, points[0]]
    edges = list(zip(ring, ring[1:]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue  # neighbours share a corner
            c, d = edges[j]
            if _segments_meet(a, b, c, d):
                return True
    return False


def _orientation(p, q, r) -> int:
    value = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
    return 0 if value == 0 else (1 if value > 0 else -1)


def _on_segment(p, q, r) -> bool:
    """Whether q, collinear with p and r, lies between them."""
    return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])


def _segments_meet(a, b, c, d) -> bool:
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and _on_segment(a, c, b)) or (o2 == 0 and _on_segment(a, d, b))
            or (o3 == 0 and _on_segment(c, a, d)) or (o4 == 0 and _on_segment(c, b, d)))
