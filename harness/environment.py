"""The environment protocol.

Three implementations will follow -- UI, API, Kernel. An environment presents
the task in one layer's terms, executes actions, and hands back the artifact for
scoring. The runner talks to all three through exactly this surface, which is
what makes swapping one while holding everything else fixed possible.

Note what an environment is *not* given: the reference image or the golden
recipe. It receives a `TaskBrief`. Only the scorer sees the answer, and only the
runner sees both.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from harness.interaction import Action, Interface, Layer, Observation
from harness.task import TaskBrief


@runtime_checkable
class Environment(Protocol):
    """One abstraction layer, presented to an agent."""

    layer: Layer

    def reset(self, brief: TaskBrief) -> Observation:
        """Prepare a fresh run and return the opening observation.

        Must discard everything from the previous run. A canvas that survives a
        reset would let one task's leftovers score another task's run.
        """
        ...

    def interface(self) -> Interface:
        """The operations this layer offers.

        Called after `reset`, so it may describe the canvas geometry -- which
        the agent has been told anyway -- but nothing else about the task. A
        preamble that hints at the answer turns the layer comparison into a
        prompt comparison.
        """
        ...

    def step(self, action: Action) -> Observation:
        """Execute one action and return what the agent sees next.

        A rejected or malformed action is a normal outcome, reported as an
        observation carrying an error, not raised. Error legibility is one of
        the things being measured, and the agent has to be given the chance to
        read the message and recover. Raise only when the environment itself has
        broken.
        """
        ...

    def artifact(self) -> bytes | None:
        """The final canvas as PNG bytes, or None if the layer could not
        produce one."""
        ...

    def close(self) -> None:
        """Release anything held open, such as a browser."""
        ...
