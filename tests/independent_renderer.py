"""A second, deliberately unrelated rasteriser, plus perturbations of it.

Test support only -- nothing here is part of the harness. Its job is to stand in
for "a competent agent working at some layer that is not the one that produced
the reference". The reference comes out of cairo via SVG; this comes out of
Pillow's polygon scan-converter, supersampled 4x and box-filtered down. If the
scorer's thresholds only work when both sides share a rasteriser, that is a bug
in the thresholds, and this file is what catches it.

The perturbations are the other half of the bracket: each one is a drawing a
real agent might plausibly produce and that must be scored as wrong.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from harness.image import to_png_bytes
from harness.task import Canvas

SUPERSAMPLE = 4

Shape = dict[str, Any]


def render(canvas: Canvas, shapes: list[Shape], offset: tuple[float, float] = (0, 0)) -> bytes:
    """Rasterise a shape list, optionally displacing the whole figure."""
    scale = SUPERSAMPLE
    image = Image.new(
        "RGB", (canvas.width * scale, canvas.height * scale), canvas.background
    )
    draw = ImageDraw.Draw(image)
    dx, dy = offset[0] * scale, offset[1] * scale

    for shape in shapes:
        kind = shape["type"]
        fill = shape.get("fill")
        if kind == "circle":
            cx, cy = shape["cx"] * scale + dx, shape["cy"] * scale + dy
            r = shape["r"] * scale
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        elif kind == "ellipse":
            cx, cy = shape["cx"] * scale + dx, shape["cy"] * scale + dy
            rx, ry = shape["rx"] * scale, shape["ry"] * scale
            draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill)
        elif kind == "rect":
            x, y = shape["x"] * scale + dx, shape["y"] * scale + dy
            draw.rectangle(
                [x, y, x + shape["width"] * scale - 1, y + shape["height"] * scale - 1],
                fill=fill,
            )
        elif kind == "polygon":
            draw.polygon(
                [(px * scale + dx, py * scale + dy) for px, py in shape["points"]],
                fill=fill,
            )
        else:
            raise NotImplementedError(f"independent renderer lacks {kind!r}")

    image = image.resize((canvas.width, canvas.height), Image.LANCZOS)
    return to_png_bytes(np.asarray(image, dtype=np.uint8))


def render_blank(canvas: Canvas) -> bytes:
    """The background and nothing else -- the do-nothing answer, which must
    never pass. Mean-error scoring is bad at catching this; it is the main
    reason the scorer gates on pixel accuracy instead."""
    image = Image.new("RGB", (canvas.width, canvas.height), canvas.background)
    return to_png_bytes(np.asarray(image, dtype=np.uint8))


def _shift_colour(value: str, delta: int) -> str:
    channels = [int(value[index : index + 2], 16) for index in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, channel + delta)) for channel in channels
    )


def scaled(shapes: list[Shape], factor: float) -> list[Shape]:
    """Every shape shrunk about its own centre: right shapes, wrong size."""
    out = []
    for shape in copy.deepcopy(shapes):
        if shape["type"] == "circle":
            shape["r"] *= factor
        elif shape["type"] == "ellipse":
            shape["rx"] *= factor
            shape["ry"] *= factor
        elif shape["type"] == "rect":
            shape["x"] += shape["width"] * (1 - factor) / 2
            shape["y"] += shape["height"] * (1 - factor) / 2
            shape["width"] *= factor
            shape["height"] *= factor
        elif shape["type"] == "polygon":
            xs = [point[0] for point in shape["points"]]
            ys = [point[1] for point in shape["points"]]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            shape["points"] = [
                [cx + (x - cx) * factor, cy + (y - cy) * factor]
                for x, y in shape["points"]
            ]
        out.append(shape)
    return out


def tinted(shapes: list[Shape], delta: int) -> list[Shape]:
    """Right geometry, wrong colour."""
    out = []
    for shape in copy.deepcopy(shapes):
        if "fill" in shape:
            shape["fill"] = _shift_colour(shape["fill"], delta)
        out.append(shape)
    return out


def squarified(shapes: list[Shape]) -> list[Shape]:
    """Circles replaced by their bounding squares: right place, wrong shape."""
    out = []
    for shape in copy.deepcopy(shapes):
        if shape["type"] == "circle":
            out.append(
                {
                    "type": "rect",
                    "x": shape["cx"] - shape["r"],
                    "y": shape["cy"] - shape["r"],
                    "width": 2 * shape["r"],
                    "height": 2 * shape["r"],
                    "fill": shape["fill"],
                }
            )
        else:
            out.append(shape)
    return out


def without_last(shapes: list[Shape]) -> list[Shape]:
    """Stopped one shape early."""
    return copy.deepcopy(shapes[:-1])


def reversed_order(shapes: list[Shape]) -> list[Shape]:
    """Correct shapes, wrong z-order. Only meaningful where they overlap --
    which is the entire point of the occlusion tier."""
    return copy.deepcopy(shapes[::-1])
