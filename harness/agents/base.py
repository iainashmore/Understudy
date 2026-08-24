"""The agent protocol.

Abstracting the agent is what lets the same runner drive a scripted stub and a
real model, and it is what keeps "burn API calls" out of the inner development
loop. Two implementations are expected: the mocks in `harness.agents.mock`, and
the model-backed one that comes later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from harness.interaction import Action, Interface, Observation
from harness.task import TaskBrief


@dataclass(frozen=True)
class Usage:
    """Tokens consumed by one agent call.

    Optimising cost is a non-goal, but recording it is nearly free and
    impossible to backfill. Mock agents leave it at zero.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@runtime_checkable
class Agent(Protocol):
    """Something that can be dropped into the runner loop.

    Lifecycle: `reset` once per run, then `act` once per turn until the agent
    returns `done` or the runner's turn budget is spent. Agents keep whatever
    history they need themselves; the runner does not hand it back.
    """

    name: str

    #: True only for agents that were given the answer. An oracle exists to
    #: prove an environment can produce a passing artifact at all; its results
    #: are diagnostics and must never be reported as a capability measurement.
    is_oracle: bool

    def reset(self, brief: TaskBrief, interface: Interface) -> None:
        """Start a fresh run. Must clear any state from the previous one."""
        ...

    #: Tokens used by the most recent `act`, if the implementation knows.
    last_usage: Usage | None

    def act(self, observation: Observation) -> Action:
        """Choose the next action given what the environment just returned."""
        ...


class BaseAgent:
    """Convenience base: identity, the oracle flag, and a record of everything
    the agent was shown.

    The record is not bookkeeping for its own sake. A scripted agent ignores its
    observations by construction, so without it a runner that fed back empty or
    stale observations would still pass every test.
    """

    is_oracle = False

    def __init__(self, name: str) -> None:
        self.name = name
        self.last_usage: Usage | None = None
        self.brief: TaskBrief | None = None
        self.interface: Interface | None = None
        self.observations: list[Observation] = []
        self.turn = 0

    def reset(self, brief: TaskBrief, interface: Interface) -> None:
        self.brief = brief
        self.interface = interface
        self.observations = []
        self.turn = 0

    def act(self, observation: Observation) -> Action:
        self.observations.append(observation)
        self.turn += 1
        return self.decide(observation)

    def decide(self, observation: Observation) -> Action:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r} turn={self.turn}>"
