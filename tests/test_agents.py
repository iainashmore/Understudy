"""Mock agents: the plumbing stand-ins, including the ones that misbehave."""

from __future__ import annotations

import pytest

from harness.agents import (
    Agent,
    BaseAgent,
    CrashingAgent,
    LoopingAgent,
    NoOpAgent,
    ReactiveAgent,
    ScriptedAgent,
    oracle_agent,
    retrying_agent,
)
from harness.interaction import Action, Interface, Layer, Observation, Operation
from harness.task import load_task

INTERFACE = Interface(
    Layer.API,
    "A small drawing interface.",
    (Operation("draw_circle", "Draw a filled circle."),),
)
CIRCLE = Action("draw_circle", {"cx": 100, "cy": 100, "r": 40, "fill": "#ff0000"})


@pytest.fixture
def brief():
    return load_task("t01_red_circle").brief()


def start(agent, brief):
    agent.reset(brief, INTERFACE)
    return agent


ALL_AGENTS = [
    ScriptedAgent([CIRCLE]),
    NoOpAgent(),
    LoopingAgent(CIRCLE),
    CrashingAgent(),
    ReactiveAgent(lambda observation, turn: Action.done()),
]


@pytest.mark.parametrize("agent", ALL_AGENTS, ids=lambda a: a.name)
def test_every_mock_satisfies_the_protocol(agent):
    assert isinstance(agent, Agent)


@pytest.mark.parametrize("agent", ALL_AGENTS, ids=lambda a: a.name)
def test_mocks_are_not_oracles(agent):
    assert agent.is_oracle is False


def test_scripted_agent_replays_then_finishes(brief):
    second = Action("draw_circle", {"cx": 10})
    agent = start(ScriptedAgent([CIRCLE, second]), brief)

    assert agent.act(Observation()) == CIRCLE
    assert agent.act(Observation()) == second
    assert agent.act(Observation()).is_done


def test_scripted_agent_can_refuse_to_finish(brief):
    """Used to drive the runner into its turn limit on purpose."""
    agent = start(ScriptedAgent([CIRCLE], finish=False), brief)
    agent.act(Observation())
    with pytest.raises(StopIteration):
        agent.act(Observation())


def test_scripted_agent_rejects_a_bad_script():
    with pytest.raises(TypeError, match="not an Action"):
        ScriptedAgent(["draw_circle(100, 100, 40)"])  # type: ignore[list-item]


def test_reset_clears_the_previous_run(brief):
    agent = start(ScriptedAgent([CIRCLE]), brief)
    agent.act(Observation("first run"))
    assert agent.turn == 1

    agent.reset(brief, INTERFACE)
    assert agent.turn == 0
    assert agent.observations == []
    assert agent.act(Observation()) == CIRCLE, "the script should replay from the top"


def test_agents_record_what_they_were_shown(brief):
    """Without this a runner feeding back empty observations would still pass
    every scripted test."""
    agent = start(ScriptedAgent([CIRCLE, CIRCLE]), brief)
    first, second = Observation("one", image=b"a"), Observation("two", error="bad")
    agent.act(first)
    agent.act(second)
    assert agent.observations == [first, second]


def test_agents_receive_the_brief_and_interface(brief):
    agent = start(NoOpAgent(), brief)
    assert agent.brief is brief
    assert agent.interface is INTERFACE


def test_noop_agent_finishes_immediately(brief):
    agent = start(NoOpAgent(), brief)
    assert agent.act(Observation()).is_done


def test_looping_agent_never_finishes(brief):
    agent = start(LoopingAgent(CIRCLE), brief)
    for _ in range(50):
        assert not agent.act(Observation()).is_done


def test_crashing_agent_raises_on_the_nominated_turn(brief):
    agent = start(CrashingAgent(fail_on_turn=3, before=[CIRCLE, CIRCLE]), brief)
    agent.act(Observation())
    agent.act(Observation())
    with pytest.raises(RuntimeError, match="simulated agent failure"):
        agent.act(Observation())


def test_crashing_agent_can_raise_a_specific_error(brief):
    agent = start(CrashingAgent(error=TimeoutError("model timed out")), brief)
    with pytest.raises(TimeoutError, match="model timed out"):
        agent.act(Observation())


def test_reactive_agent_reads_the_observation(brief):
    seen = []

    def policy(observation: Observation, turn: int) -> Action:
        seen.append((observation.text, turn))
        return Action.done() if observation.text == "stop" else CIRCLE

    agent = start(ReactiveAgent(policy), brief)
    assert agent.act(Observation("go")) == CIRCLE
    assert agent.act(Observation("stop")).is_done
    assert seen == [("go", 1), ("stop", 2)]


def test_retrying_agent_recovers_from_a_legible_error(brief):
    """Error legibility is one of the things being measured, so at least one
    mock has to close that loop."""
    bad = Action("draw_circle", {"fill": "reddish"})
    good = Action("draw_circle", {"fill": "#ff0000"})
    agent = start(retrying_agent(bad, good), brief)

    assert agent.act(Observation()) == bad
    assert agent.act(Observation(error="unknown colour 'reddish'")) == good
    assert agent.act(Observation(text="ok")).is_done


def test_retrying_agent_does_not_retry_when_nothing_went_wrong(brief):
    bad = Action("draw_circle", {"fill": "reddish"})
    good = Action("draw_circle", {"fill": "#ff0000"})
    agent = start(retrying_agent(bad, good), brief)

    agent.act(Observation())
    assert agent.act(Observation(text="ok")).is_done


def test_oracle_is_flagged_and_uses_the_layer_translator(brief):
    from harness.task import load_golden_recipe

    def translate(shapes, canvas):
        assert canvas.width == 200, "the translator is told the buffer size"
        return [
            Action("draw_circle", {"cx": s["cx"], "cy": s["cy"], "r": s["r"]})
            for s in shapes
        ]

    agent = oracle_agent(
        load_golden_recipe("t01_red_circle"), translate, brief.canvas
    )
    start(agent, brief)

    assert agent.is_oracle is True, "oracle runs must be distinguishable"
    assert agent.act(Observation()) == Action(
        "draw_circle", {"cx": 100, "cy": 100, "r": 40}
    )
    assert agent.act(Observation()).is_done


def test_base_agent_requires_a_decision_rule(brief):
    agent = start(BaseAgent("incomplete"), brief)
    with pytest.raises(NotImplementedError):
        agent.act(Observation())
