"""The kernel layer: direct writes into a pixel buffer.

Below the API layer, not merely beside it. There are no shapes here -- no
circle, no rectangle, no polygon. The agent computes which pixels it wants and
sets them. Anything it wants to appear it must rasterise itself.

Three decisions define this layer, and each one is the honest reading of "max
control, no guardrails, failures land on the agent":

  * No shape primitives at all. This is what makes the ladder monotone -- raw
    SVG path data would have sat *above* the API layer, not below it.
  * Nothing is clipped. A write that leaves the buffer is rejected rather than
    silently trimmed, so bounds arithmetic is the agent's problem.
  * No anti-aliasing. Pixel values are exactly what the agent wrote.

Actions carry *sequences* of primitive writes, as the spec's "low-level op
sequences" implies. One pixel per turn would make a circle undrawable inside any
sane budget, and the layer would be measuring the turn limit rather than the
abstraction.

What this layer does *not* do is give worse error messages than the others. Its
errors are terse because its operations are, but they name the entry, the value
and the limit -- degrading them on purpose would be putting a thumb on the scale
for the hypothesis under test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from harness.environments.colour import normalise_hex, to_rgb
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

#: A generous ceiling on entries per call -- larger than any canvas in the task
#: set. Present to stop a runaway payload exhausting memory, not to ration what
#: the agent may write.
MAX_ENTRIES = 100_000


class KernelError(ValueError):
    """A rejected write. Reported to the agent, never raised out of `step`."""


@dataclass(frozen=True)
class OpSpec:
    name: str
    summary: str
    parameters: tuple[Parameter, ...] = ()

    def to_operation(self) -> Operation:
        return Operation(self.name, self.summary, self.parameters)


OPERATIONS: tuple[OpSpec, ...] = (
    OpSpec(
        "fill",
        "Set every pixel in the buffer to one colour.",
        (Parameter("colour", "colour", "colour as #rrggbb"),),
    ),
    OpSpec(
        "set_pixels",
        "Set individual pixels. Each entry is [x, y, colour].",
        (
            Parameter(
                "pixels",
                "list",
                "list of [x, y, colour] entries, applied in order",
            ),
        ),
    ),
    OpSpec(
        "write_spans",
        "Set horizontal runs of pixels. Each entry is [x, y, length, colour] "
        "and covers x .. x+length-1 on row y.",
        (
            Parameter(
                "spans",
                "list",
                "list of [x, y, length, colour] entries, applied in order",
            ),
        ),
    ),
)

OPERATIONS_BY_NAME = {spec.name: spec for spec in OPERATIONS}

PREAMBLE = (
    "You are writing directly into a {width}x{height} RGB pixel buffer. There "
    "are no shape primitives: to make something appear you work out which "
    "pixels it covers and set them yourself.\n"
    "Coordinates are integer pixel indices from the top-left corner, x "
    "increasing to the right and y increasing downwards, so x is in [0, "
    "{width}) and y is in [0, {height}). Writes overwrite whatever was there. "
    "Nothing is clipped: a write that falls outside the buffer is rejected, not "
    "trimmed. Pixel values are exact and there is no anti-aliasing. Colours are "
    "hex strings such as '#1a9edb'.\n"
    "Each call carries a list of writes, applied in order, so you can set many "
    "pixels in one turn."
)


def _integer(value: Any, label: str) -> int:
    """Accept 100 and 100.0, reject 100.5.

    A buffer index is an integer; tolerating an integral float is a formatting
    allowance, not a capability one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KernelError(f"{label} must be an integer, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise KernelError(f"{label} must be a whole number of pixels, got {value!r}")
    return int(value)


def _colour(value: Any, label: str) -> tuple[int, int, int]:
    normalised = normalise_hex(value)
    if normalised is None:
        raise KernelError(f"{label} must be a colour like '#ff0000', got {value!r}")
    return to_rgb(normalised)


def _entries(value: Any, field: str, width: int) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise KernelError(f"{field!r} must be a list, got {type(value).__name__}")
    if not value:
        raise KernelError(f"{field!r} is empty")
    if len(value) > MAX_ENTRIES:
        raise KernelError(
            f"{field!r} has {len(value)} entries, over the {MAX_ENTRIES} limit"
        )
    if not all(isinstance(entry, (list, tuple)) for entry in value):
        raise KernelError(f"every entry in {field!r} must be a list")
    return list(value)


class KernelEnvironment:
    """A raw pixel buffer, presented as primitive write operations."""

    layer = Layer.KERNEL

    def __init__(self) -> None:
        self.brief: TaskBrief | None = None
        self.buffer: np.ndarray | None = None
        self.background: tuple[int, int, int] = (0, 0, 0)

    # -- environment protocol -------------------------------------------------

    def reset(self, brief: TaskBrief) -> Observation:
        self.brief = brief
        canvas: Canvas = brief.canvas
        self.background = to_rgb(canvas.background)
        self.buffer = np.empty((canvas.height, canvas.width, 3), dtype=np.uint8)
        self.buffer[:, :] = self.background
        return Observation(
            text=(
                f"Buffer is {canvas.width}x{canvas.height} RGB, every pixel set "
                f"to {canvas.background}. 0 pixels written."
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
            written = self._apply(action)
        except KernelError as exc:
            return Observation(
                text=self._state(),
                image=self.render(),
                error=str(exc),
            )
        return Observation(
            text=f"Wrote {written} pixel(s). {self._state()}",
            image=self.render(),
        )

    def artifact(self) -> bytes | None:
        return self.render() if self.buffer is not None else None

    def close(self) -> None:
        return None

    # -- operations -----------------------------------------------------------

    def _apply(self, action: Action) -> int:
        spec = OPERATIONS_BY_NAME.get(action.name)
        if spec is None:
            available = ", ".join(sorted(OPERATIONS_BY_NAME) + ["done"])
            raise KernelError(
                f"unknown operation {action.name!r}. Available: {available}"
            )

        accepted = {parameter.name for parameter in spec.parameters}
        unexpected = sorted(set(action.args) - accepted)
        if unexpected:
            raise KernelError(
                f"{spec.name}: unexpected argument {unexpected[0]!r}. "
                + (f"Accepted: {', '.join(sorted(accepted))}" if accepted else "")
            )
        missing = sorted(accepted - set(action.args))
        if missing:
            raise KernelError(
                f"{spec.name}: missing required argument {missing[0]!r}"
            )

        if action.name == "fill":
            return self._fill(action.args["colour"])
        if action.name == "set_pixels":
            return self._set_pixels(action.args["pixels"])
        return self._write_spans(action.args["spans"])

    def _fill(self, colour: Any) -> int:
        buffer = self._buffer()
        buffer[:, :] = _colour(colour, "fill: 'colour'")
        return int(buffer.shape[0] * buffer.shape[1])

    def _set_pixels(self, pixels: Any) -> int:
        buffer = self._buffer()
        height, width = buffer.shape[:2]
        entries = _entries(pixels, "pixels", width)

        # Validated in full before anything is written: a half-applied batch
        # would leave the agent unable to tell what state the buffer is in.
        resolved = []
        for index, entry in enumerate(entries):
            if len(entry) != 3:
                raise KernelError(
                    f"set_pixels: entry {index} must be [x, y, colour], "
                    f"got {len(entry)} item(s)"
                )
            x = _integer(entry[0], f"set_pixels: entry {index} x")
            y = _integer(entry[1], f"set_pixels: entry {index} y")
            colour = _colour(entry[2], f"set_pixels: entry {index} colour")
            if not 0 <= x < width:
                raise KernelError(
                    f"set_pixels: entry {index} has x={x}, outside [0, {width})"
                )
            if not 0 <= y < height:
                raise KernelError(
                    f"set_pixels: entry {index} has y={y}, outside [0, {height})"
                )
            resolved.append((x, y, colour))

        for x, y, colour in resolved:
            buffer[y, x] = colour
        return len(resolved)

    def _write_spans(self, spans: Any) -> int:
        buffer = self._buffer()
        height, width = buffer.shape[:2]
        entries = _entries(spans, "spans", width)

        resolved = []
        for index, entry in enumerate(entries):
            if len(entry) != 4:
                raise KernelError(
                    f"write_spans: entry {index} must be [x, y, length, colour], "
                    f"got {len(entry)} item(s)"
                )
            x = _integer(entry[0], f"write_spans: entry {index} x")
            y = _integer(entry[1], f"write_spans: entry {index} y")
            length = _integer(entry[2], f"write_spans: entry {index} length")
            colour = _colour(entry[3], f"write_spans: entry {index} colour")
            if length < 1:
                raise KernelError(
                    f"write_spans: entry {index} has length={length}, must be at least 1"
                )
            if not 0 <= y < height:
                raise KernelError(
                    f"write_spans: entry {index} has y={y}, outside [0, {height})"
                )
            if x < 0 or x + length > width:
                raise KernelError(
                    f"write_spans: entry {index} covers x={x}..{x + length - 1}, "
                    f"outside [0, {width}); nothing is clipped"
                )
            resolved.append((x, y, length, colour))

        written = 0
        for x, y, length, colour in resolved:
            buffer[y, x : x + length] = colour
            written += length
        return written

    # -- state ----------------------------------------------------------------

    def _buffer(self) -> np.ndarray:
        if self.buffer is None:
            raise RuntimeError("environment used before reset")
        return self.buffer

    def _canvas(self) -> Canvas:
        if self.brief is None:
            raise RuntimeError("environment used before reset")
        return self.brief.canvas

    def _state(self) -> str:
        buffer = self._buffer()
        total = buffer.shape[0] * buffer.shape[1]
        changed = int((buffer != np.array(self.background, dtype=np.uint8)).any(axis=2).sum())
        return f"{changed} of {total} pixels differ from the initial fill."

    def render(self) -> bytes:
        return to_png_bytes(self._buffer())


# -- oracle -------------------------------------------------------------------


def _shape_mask(shape: dict[str, Any], canvas: Canvas) -> np.ndarray:
    """Coverage test at pixel centres. Hard edges -- which is all this layer
    can express."""
    rows, columns = np.mgrid[0 : canvas.height, 0 : canvas.width]
    x, y = columns + 0.5, rows + 0.5
    kind = shape["type"]

    if kind == "circle":
        return (x - shape["cx"]) ** 2 + (y - shape["cy"]) ** 2 <= shape["r"] ** 2
    if kind == "ellipse":
        return ((x - shape["cx"]) / shape["rx"]) ** 2 + (
            (y - shape["cy"]) / shape["ry"]
        ) ** 2 <= 1.0
    if kind == "rect":
        return (
            (x >= shape["x"])
            & (x < shape["x"] + shape["width"])
            & (y >= shape["y"])
            & (y < shape["y"] + shape["height"])
        )
    if kind == "polygon":
        points = shape["points"]
        inside = np.zeros(x.shape, dtype=bool)
        previous = len(points) - 1
        for current in range(len(points)):
            xi, yi = points[current]
            xj, yj = points[previous]
            straddles = (yi > y) != (yj > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                crossing = (xj - xi) * (y - yi) / (yj - yi) + xi
            inside ^= straddles & (x < crossing)
            previous = current
        return inside
    if kind == "line":
        x1, y1 = shape["x1"], shape["y1"]
        x2, y2 = shape["x2"], shape["y2"]
        dx, dy = x2 - x1, y2 - y1
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            return np.zeros(x.shape, dtype=bool)
        t = ((x - x1) * dx + (y - y1) * dy) / length_squared
        # Butt caps, matching the reference renderer's stroke-linecap.
        within = (t >= 0) & (t <= 1)
        distance = np.abs((x - x1) * dy - (y - y1) * dx) / np.sqrt(length_squared)
        return within & (distance <= shape["stroke_width"] / 2)

    raise KeyError(f"kernel oracle cannot rasterise shape type {kind!r}")


def mask_to_spans(mask: np.ndarray, colour: str) -> list[list[Any]]:
    """Run-length encode a coverage mask into [x, y, length, colour] spans."""
    spans: list[list[Any]] = []
    for y in range(mask.shape[0]):
        row = mask[y].astype(np.int8)
        if not row.any():
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row, [0]))))
        for start, end in zip(edges[::2], edges[1::2]):
            spans.append([int(start), int(y), int(end - start), colour])
    return spans


def oracle_actions(
    shapes: Sequence[dict[str, Any]], canvas: Canvas
) -> list[Action]:
    """Rasterise a golden recipe into span writes, for the oracle diagnostic.

    One call per shape, so later shapes overwrite earlier ones exactly as the
    recipe's ordering intends.
    """
    actions = []
    for shape in shapes:
        spans = mask_to_spans(_shape_mask(shape, canvas), shape.get("fill") or shape["stroke"])
        if spans:
            actions.append(Action("write_spans", {"spans": spans}))
    return actions
