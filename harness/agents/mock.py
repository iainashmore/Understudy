"""Mock agents.

Built before the environments, and before any model, so the plumbing can be
exercised end to end without spending a single API call. The spec is explicit
that this comes first.

The set is chosen to cover what a runner has to survive, not just the happy
path -- the failure modes are the interesting output of the whole exercise, so
the loop has to handle them correctly before a real model starts producing them:

    ScriptedAgent   a fixed plan, correct or otherwise
    NoOpAgent       declares victory having drawn nothing
    LoopingAgent    never stops, so the turn budget has to
    CrashingAgent   raises mid-run
    ReactiveAgent   reads the observation and changes its mind
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from harness.agents.base import BaseAgent
from harness.interaction import Action, Observation


class ScriptedAgent(BaseAgent):
    """Replays a fixed list of actions, then declares completion.

    The workhorse. A wrong drawing, an unsupported operation or a malformed
    argument list are all just scripts, so this one class covers most of what
    the runner and the environments need testing against.
    """

    def __init__(
        self,
        actions: Iterable[Action],
        name: str = "scripted",
        *,
        finish: bool = True,
    ) -> None:
        super().__init__(name)
        self.script: tuple[Action, ...] = tuple(actions)
        self.finish = finish
        for index, action in enumerate(self.script):
            if not isinstance(action, Action):
                raise TypeError(f"script entry {index} is not an Action: {action!r}")

    def decide(self, observation: Observation) -> Action:
        index = self.turn - 1
        if index < len(self.script):
            return self.script[index]
        if self.finish:
            return Action.done()
        # Scripts that run out without finishing let a test drive the runner
        # into its turn limit deliberately.
        raise StopIteration(f"{self.name} ran out of script on turn {self.turn}")

    @property
    def exhausted(self) -> bool:
        return self.turn >= len(self.script)


class NoOpAgent(BaseAgent):
    """Declares completion immediately, having drawn nothing.

    The laziest possible failure, and the one a scorer most easily lets through:
    on a mean-error metric an untouched canvas scores deceptively well. Every
    layer must fail this.
    """

    def __init__(self, name: str = "noop") -> None:
        super().__init__(name)

    def decide(self, observation: Observation) -> Action:
        return Action.done()


class LoopingAgent(BaseAgent):
    """Repeats one action forever and never declares completion.

    Exists so the turn budget gets tested. An agent that never stops is a real
    model failure mode, not a hypothetical one, and the runner must end the run
    itself rather than spin.
    """

    def __init__(self, action: Action, name: str = "looping") -> None:
        super().__init__(name)
        self.action = action

    def decide(self, observation: Observation) -> Action:
        return self.action


class CrashingAgent(BaseAgent):
    """Raises part-way through a run.

    A model client can throw -- a timeout, a rate limit, a malformed response.
    The runner has to record that as a failed run with a readable trace, not
    take the whole sweep down with it.
    """

    def __init__(
        self,
        fail_on_turn: int = 1,
        name: str = "crashing",
        error: Exception | None = None,
        before: Sequence[Action] = (),
    ) -> None:
        super().__init__(name)
        if fail_on_turn < 1:
            raise ValueError("fail_on_turn is 1-based")
        self.fail_on_turn = fail_on_turn
        self.error = error or RuntimeError("simulated agent failure")
        self.before = tuple(before)

    def decide(self, observation: Observation) -> Action:
        if self.turn >= self.fail_on_turn:
            raise self.error
        index = self.turn - 1
        return self.before[index] if index < len(self.before) else Action.done()


class ReactiveAgent(BaseAgent):
    """Chooses each action from the observation it was just given.

    The only mock that actually reads what comes back, which makes it the one
    that can prove the feedback loop is wired up. The scripted agents would
    behave identically against a runner that fed them stale or empty
    observations.
    """

    def __init__(
        self,
        policy: Callable[[Observation, int], Action],
        name: str = "reactive",
    ) -> None:
        super().__init__(name)
        self.policy = policy

    def decide(self, observation: Observation) -> Action:
        return self.policy(observation, self.turn)


def retrying_agent(
    first: Action, correction: Action, name: str = "retrying"
) -> ReactiveAgent:
    """Emits `first`; if the environment reports an error, emits `correction`,
    then finishes.

    A round trip through error legibility, which is one of the things being
    measured: an agent can only recover from a message it can actually read.
    """

    def policy(observation: Observation, turn: int) -> Action:
        if turn == 1:
            return first
        if observation.failed:
            return correction
        return Action.done()

    return ReactiveAgent(policy, name=name)


def oracle_agent(
    recipe: dict[str, Any],
    translate: Callable[[list[dict[str, Any]]], Iterable[Action]],
    name: str = "oracle",
) -> ScriptedAgent:
    """An agent handed the answer, for proving an environment can pass at all.

    Not a measurement. If the oracle cannot pass a task through a given layer,
    the layer's implementation is broken; when a real agent then fails there, we
    would be reading a harness bug as a capability result. That check is worth
    having for each new environment, which is why this exists -- but its runs
    are diagnostics and the `is_oracle` flag keeps them out of the results
    table.

    `translate` is supplied by the environment being tested, since only it knows
    how to say "draw this circle" in its own action space.
    """
    agent = ScriptedAgent(translate(list(recipe["shapes"])), name=name)
    agent.is_oracle = True
    return agent
