"""Golden reference rendering. Authoring-side only.

Turns a task's `golden` recipe into the reference PNG the scorer compares
against. This is deliberately its own rasterisation path -- a recipe becomes an
SVG document, rasterised by cairo -- so no environment is ever scored against an
image produced by the same code that produced its own output. If the API layer
drew the reference, the API layer would be matching its own anti-aliasing and
would beat the others for reasons that have nothing to do with abstraction
level.

Shapes are drawn in list order, so the recipe's order *is* the z-order. That is
the whole content of the tier-3 tasks.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from harness.task import Canvas

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SUPPORTED_SHAPES = ("rect", "circle", "ellipse", "polygon", "line")


class RecipeError(ValueError):
    """The golden recipe is malformed. Always an authoring bug."""


def _colour(value: Any, field: str) -> str:
    text = str(value)
    if not _HEX_RE.match(text):
        raise RecipeError(f"{field} must be an #rrggbb colour, got {value!r}")
    return text.lower()


def _number(shape: dict[str, Any], key: str) -> float:
    if key not in shape:
        raise RecipeError(f"{shape.get('type', '?')} shape is missing {key!r}")
    try:
        return float(shape[key])
    except (TypeError, ValueError):
        raise RecipeError(
            f"{shape.get('type', '?')} shape has non-numeric {key}={shape[key]!r}"
        ) from None


def _format(value: float) -> str:
    return f"{value:g}"


def _points(shape: dict[str, Any]) -> str:
    raw = shape.get("points")
    if not isinstance(raw, list) or len(raw) < 3:
        raise RecipeError("polygon needs a 'points' list of at least 3 vertices")
    parts = []
    for vertex in raw:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            raise RecipeError(f"polygon vertex must be [x, y], got {vertex!r}")
        parts.append(f"{_format(float(vertex[0]))},{_format(float(vertex[1]))}")
    return " ".join(parts)


def _shape_to_svg(shape: dict[str, Any]) -> str:
    kind = str(shape.get("type", "")).lower()

    if kind == "rect":
        return (
            f'<rect x="{_format(_number(shape, "x"))}" '
            f'y="{_format(_number(shape, "y"))}" '
            f'width="{_format(_number(shape, "width"))}" '
            f'height="{_format(_number(shape, "height"))}" '
            f'fill="{_colour(shape.get("fill"), "rect fill")}"/>'
        )
    if kind == "circle":
        return (
            f'<circle cx="{_format(_number(shape, "cx"))}" '
            f'cy="{_format(_number(shape, "cy"))}" '
            f'r="{_format(_number(shape, "r"))}" '
            f'fill="{_colour(shape.get("fill"), "circle fill")}"/>'
        )
    if kind == "ellipse":
        return (
            f'<ellipse cx="{_format(_number(shape, "cx"))}" '
            f'cy="{_format(_number(shape, "cy"))}" '
            f'rx="{_format(_number(shape, "rx"))}" '
            f'ry="{_format(_number(shape, "ry"))}" '
            f'fill="{_colour(shape.get("fill"), "ellipse fill")}"/>'
        )
    if kind == "polygon":
        return (
            f'<polygon points="{_points(shape)}" '
            f'fill="{_colour(shape.get("fill"), "polygon fill")}"/>'
        )
    if kind == "line":
        return (
            f'<line x1="{_format(_number(shape, "x1"))}" '
            f'y1="{_format(_number(shape, "y1"))}" '
            f'x2="{_format(_number(shape, "x2"))}" '
            f'y2="{_format(_number(shape, "y2"))}" '
            f'stroke="{_colour(shape.get("stroke"), "line stroke")}" '
            f'stroke-width="{_format(_number(shape, "stroke_width"))}" '
            f'stroke-linecap="butt"/>'
        )

    raise RecipeError(
        f"unsupported shape type {kind!r}; expected one of {list(SUPPORTED_SHAPES)}"
    )


def _shapes(recipe: dict[str, Any]) -> Iterable[dict[str, Any]]:
    shapes = recipe.get("shapes")
    if not isinstance(shapes, list) or not shapes:
        raise RecipeError("golden recipe needs a non-empty 'shapes' list")
    for shape in shapes:
        if not isinstance(shape, dict):
            raise RecipeError(f"each shape must be an object, got {shape!r}")
        yield shape


def recipe_to_svg(canvas: Canvas, recipe: dict[str, Any]) -> str:
    """Render a recipe as an SVG document string."""
    body = "".join(_shape_to_svg(shape) for shape in _shapes(recipe))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{canvas.width}" height="{canvas.height}" '
        f'viewBox="0 0 {canvas.width} {canvas.height}">'
        f'<rect x="0" y="0" width="{canvas.width}" height="{canvas.height}" '
        f'fill="{_colour(canvas.background, "canvas background")}"/>'
        f"{body}</svg>"
    )


def render_reference(canvas: Canvas, recipe: dict[str, Any]) -> bytes:
    """Rasterise a recipe to PNG bytes at exactly the canvas size."""
    import cairosvg  # imported lazily: authoring-only dependency

    return cairosvg.svg2png(
        bytestring=recipe_to_svg(canvas, recipe).encode("utf-8"),
        output_width=canvas.width,
        output_height=canvas.height,
        background_color=canvas.background,
    )
