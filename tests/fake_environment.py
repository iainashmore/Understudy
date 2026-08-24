"""A test double for the environment protocol.

Deliberately not a drawing environment. The runner does not care how an
artifact was produced, so building a real rasterising environment here would
duplicate step 4 and test the wrong thing. This one records actions, returns
whatever observations a test asks for, and yields an artifact chosen by a
factory -- which is enough to drive every path the runner has.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from harness.image import to_png_bytes
from harness.interaction import (
    Action,
    Interface,
    Layer,
    Observation,
    Operation,
    Parameter,
)
from harness.task import Canvas, Task, TaskBrief

OPERATIONS = (
    Operation(
        "draw_circle",
        "Draw a filled circle.",
        (
            Parameter("cx", "number", "centre x"),
            Parameter("cy", "number", "centre y"),
            Parameter("r", "number", "radius"),
            Parameter("fill", "colour", "fill colour as #rrggbb"),
        ),
    ),
    Operation("clear", "Reset the canvas to the background colour."),
)


def blank_png(canvas: Canvas) -> bytes:
    colour = [int(canvas.background[i : i + 2], 16) for i in (1, 3, 5)]
    pixels = np.full((canvas.height, canvas.width, 3), colour, dtype=np.uint8)
    return to_png_bytes(pixels)


def reference_after(task: Task, trigger: str) -> Callable[[Sequence[Action]], bytes]:
    """An artifact factory that returns the task's reference image only once the
    named operation has actually been executed.

    Lets a test assert a genuine pass that depends on what the agent did, rather
    than one handed over regardless.
    """

    def factory(actions: Sequence[Action]) -> bytes:
        if any(action.name == trigger for action in actions):
            return task.reference_bytes()
        return blank_png(task.canvas)

    return factory


class FakeEnvironment:
    """Configurable stand-in for a real layer."""

    def __init__(
        self,
        layer: Layer = Layer.API,
        artifact_factory: Callable[[Sequence[Action]], bytes | None] | None = None,
        raise_on_reset: bool = False,
        raise_on_step: int | None = None,
        raise_on_artifact: bool = False,
        operations: tuple[Operation, ...] = OPERATIONS,
    ) -> None:
        self.layer = layer
        self.artifact_factory = artifact_factory
        self.raise_on_reset = raise_on_reset
        self.raise_on_step = raise_on_step
        self.raise_on_artifact = raise_on_artifact
        self.operations = operations
        self.brief: TaskBrief | None = None
        self.actions: list[Action] = []
        self.resets = 0
        self.closed = False

    def reset(self, brief: TaskBrief) -> Observation:
        if self.raise_on_reset:
            raise RuntimeError("environment failed to start")
        self.brief = brief
        self.actions = []
        self.resets += 1
        return Observation(
            text=f"blank {brief.canvas.width}x{brief.canvas.height} canvas",
            image=blank_png(brief.canvas),
        )

    def interface(self) -> Interface:
        return Interface(
            layer=self.layer,
            preamble="A small drawing interface.",
            operations=self.operations,
        )

    def step(self, action: Action) -> Observation:
        if self.raise_on_step is not None and len(self.actions) + 1 == self.raise_on_step:
            raise RuntimeError("environment exploded")

        assert self.brief is not None, "step before reset"
        if not self.interface().accepts(action):
            # Rejected actions are observations, not exceptions: the agent has
            # to be able to read the message and try again.
            return Observation(
                error=(
                    f"unknown operation {action.name!r}; available: "
                    + ", ".join(sorted(op.name for op in self.operations))
                ),
                image=blank_png(self.brief.canvas),
            )

        self.actions.append(action)
        return Observation(
            text=f"executed {action.name}",
            image=blank_png(self.brief.canvas),
        )

    def artifact(self) -> bytes | None:
        if self.raise_on_artifact:
            raise RuntimeError("could not read back the canvas")
        if self.artifact_factory is None:
            return blank_png(self.brief.canvas) if self.brief else None
        return self.artifact_factory(self.actions)

    def close(self) -> None:
        self.closed = True
