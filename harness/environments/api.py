"""The API layer: a small drawing API exposed as structured operations.

Structured calls rather than executed Python, as decided up front: no sandbox,
and the error signal stays clean, which matters because error legibility is one
of the properties being compared. It is also the honest analogue of the eventual
CAD target, where the API layer is CadQuery-style calls.

Shapes paint in the order they are drawn. That is stated in the preamble because
it is a fact about the interface, and every layer's preamble states its own
semantics -- withholding it here while the UI's canvas makes it obvious would be
the asymmetry, not the other way round.

Rendered with Pillow, supersampled and filtered down. Reference images come out
of cairo, so a correct drawing here is never matching its own anti-aliasing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from harness.environments.colour import normalise_hex
from harness.image import to_png_bytes
from harness.interaction import (
    Action,
    Interface,
    Layer,
    Observation,
    Operation,
    Parameter,
)
from harness.task import Canvas, TaskBrief

#: Supersampling factor. Pillow's scan converter is aliased, and hard edges
#: against a soft reference would cost accuracy everywhere a shape has an
#: outline.
SUPERSAMPLE = 4

class ActionError(ValueError):
    """A rejected action. Reported to the agent, never raised out of `step`."""


@dataclass(frozen=True)
class ParamSpec:
    """One argument, described once and used for both documentation and
    validation, so the two cannot drift apart."""

    name: str
    kind: str
    description: str
    positive: bool = False

    def to_parameter(self) -> Parameter:
        return Parameter(name=self.name, type=self.kind, description=self.description)


@dataclass(frozen=True)
class OpSpec:
    name: str
    summary: str
    params: tuple[ParamSpec, ...] = ()
    shape: str | None = None

    def to_operation(self) -> Operation:
        return Operation(
            name=self.name,
            summary=self.summary,
            parameters=tuple(param.to_parameter() for param in self.params),
        )


def _xy(axis: str, what: str) -> ParamSpec:
    return ParamSpec(axis, "number", f"{what} ({axis} in pixels)")


OPERATIONS: tuple[OpSpec, ...] = (
    OpSpec(
        "draw_circle",
        "Draw a filled circle.",
        (
            _xy("cx", "centre"),
            _xy("cy", "centre"),
            ParamSpec("r", "number", "radius in pixels", positive=True),
            ParamSpec("fill", "colour", "fill colour as #rrggbb"),
        ),
        shape="circle",
    ),
    OpSpec(
        "draw_rect",
        "Draw a filled axis-aligned rectangle.",
        (
            _xy("x", "top-left corner"),
            _xy("y", "top-left corner"),
            ParamSpec("width", "number", "width in pixels", positive=True),
            ParamSpec("height", "number", "height in pixels", positive=True),
            ParamSpec("fill", "colour", "fill colour as #rrggbb"),
        ),
        shape="rect",
    ),
    OpSpec(
        "draw_ellipse",
        "Draw a filled axis-aligned ellipse.",
        (
            _xy("cx", "centre"),
            _xy("cy", "centre"),
            ParamSpec("rx", "number", "horizontal radius", positive=True),
            ParamSpec("ry", "number", "vertical radius", positive=True),
            ParamSpec("fill", "colour", "fill colour as #rrggbb"),
        ),
        shape="ellipse",
    ),
    OpSpec(
        "draw_polygon",
        "Draw a filled polygon through the given vertices.",
        (
            ParamSpec("points", "points", "list of at least three [x, y] pairs"),
            ParamSpec("fill", "colour", "fill colour as #rrggbb"),
        ),
        shape="polygon",
    ),
    OpSpec(
        "draw_line",
        "Draw a straight line.",
        (
            _xy("x1", "start"),
            _xy("y1", "start"),
            _xy("x2", "end"),
            _xy("y2", "end"),
            ParamSpec("stroke", "colour", "line colour as #rrggbb"),
            ParamSpec("stroke_width", "number", "line width in pixels", positive=True),
        ),
        shape="line",
    ),
    OpSpec("clear", "Remove every shape, leaving the background."),
)

OPERATIONS_BY_NAME = {spec.name: spec for spec in OPERATIONS}

PREAMBLE = (
    "You are drawing through a small shape API on a {width}x{height} pixel "
    "canvas. Coordinates are in pixels from the top-left corner, x increasing "
    "to the right and y increasing downwards.\n"
    "Call one operation per turn. Shapes are painted in the order you draw "
    "them, so a later shape covers an earlier one where they overlap. Colours "
    "are hex strings such as '#1a9edb'; colour names are not accepted."
)


def parse_colour(value: Any, field: str, operation: str) -> str:
    """Hex only.

    Named colours look friendlier but several in the task set do not mean what
    a reader expects -- the prompts' "brown" is saddlebrown and their "dark red"
    is firebrick -- so accepting names would silently draw the wrong colour.
    A rejected name produces a legible error instead, which the agent can act on.
    """
    normalised = normalise_hex(value)
    if normalised is None:
        raise ActionError(
            f"{operation}: {field!r} must be a colour like '#ff0000' or '#f00', "
            f"got {value!r}"
        )
    return normalised


def parse_number(value: Any, spec: ParamSpec, operation: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionError(
            f"{operation}: {spec.name!r} must be a number, "
            f"got {value!r} ({type(value).__name__})"
        )
    number = float(value)
    if spec.positive and number <= 0:
        raise ActionError(
            f"{operation}: {spec.name!r} must be greater than 0, got {number:g}"
        )
    return number


def parse_points(value: Any, operation: str) -> list[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        raise ActionError(
            f"{operation}: 'points' must be a list of [x, y] pairs, "
            f"got {type(value).__name__}"
        )
    if len(value) < 3:
        raise ActionError(
            f"{operation}: 'points' needs at least three [x, y] pairs, "
            f"got {len(value)}"
        )
    parsed = []
    for index, vertex in enumerate(value):
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            raise ActionError(
                f"{operation}: point {index} must be an [x, y] pair, got {vertex!r}"
            )
        try:
            parsed.append((float(vertex[0]), float(vertex[1])))
        except (TypeError, ValueError):
            raise ActionError(
                f"{operation}: point {index} must be two numbers, got {vertex!r}"
            ) from None
    return parsed


def parse_action(action: Action) -> tuple[OpSpec, dict[str, Any]]:
    """Validate an action against its operation spec.

    Every message names the operation, the argument and what was expected --
    an agent can only recover from an error it can read, and how well it does
    so is part of what the layer is being measured on.
    """
    spec = OPERATIONS_BY_NAME.get(action.name)
    if spec is None:
        available = ", ".join(sorted(OPERATIONS_BY_NAME) + ["done"])
        raise ActionError(
            f"unknown operation {action.name!r}. Available: {available}"
        )

    accepted = {param.name for param in spec.params}
    unexpected = sorted(set(action.args) - accepted)
    if unexpected:
        raise ActionError(
            f"{spec.name}: unexpected argument {unexpected[0]!r}. "
            + (
                f"Accepted: {', '.join(sorted(accepted))}"
                if accepted
                else "This operation takes no arguments."
            )
        )

    missing = [param.name for param in spec.params if param.name not in action.args]
    if missing:
        raise ActionError(
            f"{spec.name}: missing required argument {missing[0]!r}. "
            f"Required: {', '.join(param.name for param in spec.params)}"
        )

    parsed: dict[str, Any] = {}
    for param in spec.params:
        value = action.args[param.name]
        if param.kind == "colour":
            parsed[param.name] = parse_colour(value, param.name, spec.name)
        elif param.kind == "points":
            parsed[param.name] = parse_points(value, spec.name)
        else:
            parsed[param.name] = parse_number(value, param, spec.name)
    return spec, parsed


def _bounds(shape: str, args: dict[str, Any]) -> tuple[float, float, float, float]:
    if shape == "circle":
        return (
            args["cx"] - args["r"], args["cy"] - args["r"],
            args["cx"] + args["r"], args["cy"] + args["r"],
        )
    if shape == "ellipse":
        return (
            args["cx"] - args["rx"], args["cy"] - args["ry"],
            args["cx"] + args["rx"], args["cy"] + args["ry"],
        )
    if shape == "rect":
        return (
            args["x"], args["y"],
            args["x"] + args["width"], args["y"] + args["height"],
        )
    if shape == "polygon":
        xs = [x for x, _ in args["points"]]
        ys = [y for _, y in args["points"]]
        return (min(xs), min(ys), max(xs), max(ys))
    half = args["stroke_width"] / 2
    return (
        min(args["x1"], args["x2"]) - half, min(args["y1"], args["y2"]) - half,
        max(args["x1"], args["x2"]) + half, max(args["y1"], args["y2"]) + half,
    )


class APIEnvironment:
    """A drawing API, presented as structured operations."""

    layer = Layer.API

    def __init__(self, supersample: int = SUPERSAMPLE) -> None:
        if supersample < 1:
            raise ValueError("supersample must be at least 1")
        self.supersample = supersample
        self.brief: TaskBrief | None = None
        self.shapes: list[tuple[str, dict[str, Any]]] = []

    # -- environment protocol -------------------------------------------------

    def reset(self, brief: TaskBrief) -> Observation:
        self.brief = brief
        self.shapes = []
        return Observation(
            text=(
                f"Blank {brief.canvas.width}x{brief.canvas.height} canvas, "
                f"background {brief.canvas.background}. Nothing drawn yet."
            ),
            image=self.render(),
        )

    def interface(self) -> Interface:
        canvas = self._canvas()
        return Interface(
            layer=self.layer,
            preamble=PREAMBLE.format(width=canvas.width, height=canvas.height),
            operations=tuple(spec.to_operation() for spec in OPERATIONS),
        )

    def step(self, action: Action) -> Observation:
        try:
            spec, args = parse_action(action)
        except ActionError as exc:
            # Rejected, not fatal: the agent reads the message and tries again.
            return Observation(
                text=f"{len(self.shapes)} shape(s) on the canvas.",
                image=self.render(),
                error=str(exc),
            )

        if spec.name == "clear":
            removed = len(self.shapes)
            self.shapes = []
            return Observation(
                text=f"Cleared the canvas, removing {removed} shape(s).",
                image=self.render(),
            )

        assert spec.shape is not None
        self.shapes.append((spec.shape, args))
        return Observation(text=self._confirm(spec, args), image=self.render())

    def artifact(self) -> bytes | None:
        return self.render() if self.brief else None

    def close(self) -> None:
        return None

    # -- rendering ------------------------------------------------------------

    def _canvas(self):
        if self.brief is None:
            raise RuntimeError("environment used before reset")
        return self.brief.canvas

    def render(self) -> bytes:
        """Repaint from the shape list.

        Re-rendering rather than accumulating keeps `clear` trivial and makes
        the canvas a pure function of the actions taken, which is what the
        trace claims it is.
        """
        canvas = self._canvas()
        scale = self.supersample
        image = Image.new(
            "RGB", (canvas.width * scale, canvas.height * scale), canvas.background
        )
        draw = ImageDraw.Draw(image)

        for shape, args in self.shapes:
            if shape == "circle":
                cx, cy, r = args["cx"] * scale, args["cy"] * scale, args["r"] * scale
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=args["fill"])
            elif shape == "ellipse":
                cx, cy = args["cx"] * scale, args["cy"] * scale
                rx, ry = args["rx"] * scale, args["ry"] * scale
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=args["fill"])
            elif shape == "rect":
                x, y = args["x"] * scale, args["y"] * scale
                draw.rectangle(
                    [x, y, x + args["width"] * scale - 1, y + args["height"] * scale - 1],
                    fill=args["fill"],
                )
            elif shape == "polygon":
                draw.polygon(
                    [(x * scale, y * scale) for x, y in args["points"]],
                    fill=args["fill"],
                )
            elif shape == "line":
                draw.line(
                    [
                        args["x1"] * scale, args["y1"] * scale,
                        args["x2"] * scale, args["y2"] * scale,
                    ],
                    fill=args["stroke"],
                    width=max(1, round(args["stroke_width"] * scale)),
                )

        if scale > 1:
            image = image.resize((canvas.width, canvas.height), Image.LANCZOS)
        return to_png_bytes(np.asarray(image, dtype=np.uint8))

    def _confirm(self, spec: OpSpec, args: dict[str, Any]) -> str:
        rendered = ", ".join(
            f"{key}={value:g}" if isinstance(value, float) else f"{key}={value}"
            for key, value in args.items()
        )
        message = f"Drew {spec.shape} ({rendered}). {len(self.shapes)} shape(s) total."

        canvas = self._canvas()
        left, top, right, bottom = _bounds(spec.shape or "", args)
        if right < 0 or bottom < 0 or left > canvas.width or top > canvas.height:
            # Interface feedback, not a hint about the task: a shape nobody can
            # see is almost always a mistake, and a good API says so.
            message += " Note: this shape lies entirely outside the canvas."
        return message


def oracle_actions(
    shapes: Sequence[dict[str, Any]], canvas: Canvas | None = None
) -> list[Action]:
    """Translate a golden recipe into API calls, for the oracle diagnostic.

    Only used to prove this environment can produce a passing artifact at all.
    The canvas is part of the translator signature because lower layers need it
    to rasterise; this layer has shape primitives and does not.
    """
    translation = {
        "circle": ("draw_circle", ("cx", "cy", "r", "fill")),
        "rect": ("draw_rect", ("x", "y", "width", "height", "fill")),
        "ellipse": ("draw_ellipse", ("cx", "cy", "rx", "ry", "fill")),
        "polygon": ("draw_polygon", ("points", "fill")),
        "line": ("draw_line", ("x1", "y1", "x2", "y2", "stroke", "stroke_width")),
    }
    actions = []
    for shape in shapes:
        name, keys = translation[shape["type"]]
        actions.append(Action(name, {key: shape[key] for key in keys}))
    return actions
