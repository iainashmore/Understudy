"""The runner loop: termination, accounting, error handling, traces."""

from __future__ import annotations

import json

import pytest

from harness.agents import (
    CrashingAgent,
    LoopingAgent,
    NoOpAgent,
    ScriptedAgent,
    oracle_agent,
    retrying_agent,
)
from harness.agents.base import BaseAgent, Usage
from harness.interaction import Action, Layer, Observation
from harness.runner import (
    DEFAULT_TURN_LIMITS,
    Outcome,
    Runner,
    RunnerConfig,
)
from harness.scorer import PixelScorer
from harness.task import load_task
from tests.fake_environment import FakeEnvironment, reference_after

CIRCLE = Action("draw_circle", {"cx": 100, "cy": 100, "r": 40, "fill": "#ff0000"})


@pytest.fixture
def task():
    return load_task("t01_red_circle")


@pytest.fixture
def runner():
    return Runner(PixelScorer())


def test_a_correct_run_passes(task, runner):
    environment = FakeEnvironment(
        artifact_factory=reference_after(task, "draw_circle")
    )
    result = runner.run(task, environment, ScriptedAgent([CIRCLE]))

    assert result.outcome is Outcome.COMPLETED
    assert result.passed
    assert result.score == pytest.approx(1.0)
    assert result.error is None


def test_an_agent_that_draws_nothing_fails(task, runner):
    result = runner.run(task, FakeEnvironment(), NoOpAgent())

    assert result.outcome is Outcome.COMPLETED, "it did declare completion"
    assert not result.passed, "but the canvas is blank"
    assert result.turns_used == 0


def test_turns_used_counts_executed_actions_not_the_done_call(task, runner):
    agent = ScriptedAgent([CIRCLE, Action("clear"), CIRCLE])
    result = runner.run(task, FakeEnvironment(), agent)

    assert result.outcome is Outcome.COMPLETED
    assert result.turns_used == 3


def test_the_turn_budget_ends_a_run_that_will_not_stop(task):
    runner = Runner(PixelScorer(), RunnerConfig(turn_limits={Layer.API: 5}))
    environment = FakeEnvironment()
    result = runner.run(task, environment, LoopingAgent(CIRCLE))

    assert result.outcome is Outcome.TURN_LIMIT
    assert result.turns_used == 5
    assert result.turn_limit == 5
    assert len(environment.actions) == 5


def test_turn_budgets_are_per_layer(task, runner):
    # One UI click is not one API call; a shared budget would look like a UI
    # capability gap.
    assert DEFAULT_TURN_LIMITS[Layer.UI] > DEFAULT_TURN_LIMITS[Layer.API]
    for layer, expected in DEFAULT_TURN_LIMITS.items():
        result = runner.run(task, FakeEnvironment(layer=layer), NoOpAgent())
        assert result.turn_limit == expected


def test_unknown_layers_fall_back_to_a_configured_limit(task):
    runner = Runner(PixelScorer(), RunnerConfig(turn_limits={}, fallback_turn_limit=3))
    result = runner.run(task, FakeEnvironment(), LoopingAgent(CIRCLE))
    assert result.turn_limit == 3


def test_a_turn_limited_run_is_still_scored(task):
    """An agent that drew the right thing and never said so is a different
    failure from one that drew the wrong thing."""
    runner = Runner(PixelScorer(), RunnerConfig(turn_limits={Layer.API: 2}))
    environment = FakeEnvironment(artifact_factory=reference_after(task, "draw_circle"))
    result = runner.run(task, environment, LoopingAgent(CIRCLE))

    assert result.outcome is Outcome.TURN_LIMIT
    assert result.passed, "the picture is right even though the agent never stopped"


def test_an_agent_crash_fails_one_run_not_the_process(task, runner):
    result = runner.run(
        task, FakeEnvironment(), CrashingAgent(fail_on_turn=2, before=[CIRCLE])
    )

    assert result.outcome is Outcome.AGENT_ERROR
    assert "simulated agent failure" in result.error
    assert result.turns_used == 1, "work done before the crash is kept"
    assert not result.passed


def test_a_crashed_agents_partial_drawing_is_still_scored(task, runner):
    environment = FakeEnvironment(artifact_factory=reference_after(task, "draw_circle"))
    result = runner.run(
        task, environment, CrashingAgent(fail_on_turn=2, before=[CIRCLE])
    )

    assert result.outcome is Outcome.AGENT_ERROR
    assert result.passed, "it had already drawn the right thing"


def test_an_environment_crash_is_recorded(task, runner):
    result = runner.run(
        task, FakeEnvironment(raise_on_step=2), ScriptedAgent([CIRCLE, CIRCLE])
    )

    assert result.outcome is Outcome.ENVIRONMENT_ERROR
    assert "environment exploded" in result.error


def test_an_environment_that_will_not_start_fails_cleanly(task, runner):
    result = runner.run(task, FakeEnvironment(raise_on_reset=True), NoOpAgent())

    assert result.outcome is Outcome.ENVIRONMENT_ERROR
    assert result.turns_used == 0
    assert result.score == 0.0
    assert "environment failed to start" in result.error


def test_a_missing_artifact_fails_with_a_reason(task, runner):
    result = runner.run(
        task, FakeEnvironment(artifact_factory=lambda actions: None), NoOpAgent()
    )

    assert not result.passed
    assert "no artifact" in result.error


def test_an_unreadable_artifact_fails_with_a_reason(task, runner):
    result = runner.run(task, FakeEnvironment(raise_on_artifact=True), NoOpAgent())

    assert not result.passed
    assert "could not retrieve artifact" in result.error


def test_a_rejected_action_does_not_end_the_run(task, runner):
    """Error legibility is being measured, so the agent gets to read the message
    and try again."""
    environment = FakeEnvironment(artifact_factory=reference_after(task, "draw_circle"))
    agent = retrying_agent(Action("draw_hexagon"), CIRCLE)
    result = runner.run(task, environment, agent)

    assert result.outcome is Outcome.COMPLETED
    assert result.passed
    assert [action.name for action in environment.actions] == ["draw_circle"]


def test_the_agent_sees_the_environments_observations(task, runner):
    """The feedback loop, asserted directly. Scripted agents ignore their
    observations, so nothing else here would catch a runner that fed back
    empty or stale ones."""
    agent = ScriptedAgent([CIRCLE, Action("clear")])
    runner.run(task, FakeEnvironment(), agent)

    first, second, third = agent.observations
    assert "blank 200x200 canvas" in first.text, "reset's observation comes first"
    assert second.text == "executed draw_circle"
    assert third.text == "executed clear"
    assert all(observation.has_image for observation in agent.observations)


def test_every_layer_gets_the_canvas_back(task, runner):
    """Not just the UI one -- otherwise this measures sighted-versus-blind."""
    for layer in Layer:
        agent = ScriptedAgent([CIRCLE])
        runner.run(task, FakeEnvironment(layer=layer), agent)
        assert all(observation.has_image for observation in agent.observations)


def test_the_environment_is_reset_for_each_run(task, runner):
    environment = FakeEnvironment()
    runner.run(task, environment, ScriptedAgent([CIRCLE]))
    runner.run(task, environment, ScriptedAgent([CIRCLE]))

    assert environment.resets == 2
    assert len(environment.actions) == 1, "the previous run's actions are gone"


def test_token_usage_is_accumulated(task, runner):
    class MeteredAgent(BaseAgent):
        def decide(self, observation):
            self.last_usage = Usage(input_tokens=100, output_tokens=10)
            return Action.done() if self.turn > 2 else CIRCLE

    result = runner.run(task, FakeEnvironment(), MeteredAgent("metered"))
    assert result.usage == Usage(input_tokens=300, output_tokens=30)


def test_timings_are_split_between_agent_and_environment(task):
    ticks = iter(range(1000))
    runner = Runner(PixelScorer(), clock=lambda: next(ticks) * 1.0)
    result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

    assert result.agent_seconds > 0
    assert result.environment_seconds > 0
    assert result.duration_s > 0


def test_run_id_identifies_the_cell(task, runner):
    result = runner.run(task, FakeEnvironment(), NoOpAgent(), repeat=3)
    assert result.run_id == "noop.api.t01_red_circle.03"


def test_oracle_runs_are_flagged(task, runner):
    agent = oracle_agent(
        {"shapes": [{"cx": 100, "cy": 100, "r": 40}]},
        lambda shapes: [Action("draw_circle", dict(shape)) for shape in shapes],
    )
    result = runner.run(task, FakeEnvironment(), agent)

    assert result.is_oracle is True
    assert result.as_row()["is_oracle"] is True


def test_the_result_row_is_flat(task, runner):
    row = runner.run(task, FakeEnvironment(), NoOpAgent()).as_row()
    assert row["task_id"] == "t01_red_circle"
    assert row["layer"] == "api"
    assert all(not isinstance(value, (dict, list)) for value in row.values())


class TestTraces:
    def read(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_a_trace_is_written_and_parses(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

        records = self.read(result.trace_path)
        assert [record["type"] for record in records] == [
            "run_start",
            "interface",
            "turn",
            "done",
            "run_end",
        ]

    def test_the_trace_header_records_what_was_run(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

        header = self.read(result.trace_path)[0]
        assert header["task_id"] == "t01_red_circle"
        assert header["layer"] == "api"
        assert header["agent"] == "scripted"
        assert header["prompt"] == task.prompt
        assert header["canvas"]["width"] == 200

    def test_turn_records_carry_the_action_and_the_response(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

        turn = next(r for r in self.read(result.trace_path) if r["type"] == "turn")
        assert turn["action"] == CIRCLE.as_dict()
        assert turn["observation"]["text"] == "executed draw_circle"
        assert turn["observation"]["image_digest"]

    def test_the_trace_never_inlines_an_image(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

        # Traces have to stay readable; a base64 PNG per turn ends that.
        assert len(result.trace_path.read_text()) < 8000

    def test_a_trace_survives_the_run_that_produced_it_dying(self, task, tmp_path):
        """The run worth reading is the one that fell over, so records are
        flushed as they happen rather than assembled at the end."""
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(
            task, FakeEnvironment(raise_on_step=1), ScriptedAgent([CIRCLE])
        )

        types = [record["type"] for record in self.read(result.trace_path)]
        assert "run_start" in types
        assert "environment_error" in types
        assert "run_end" in types

    def test_an_error_record_carries_a_traceback(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        result = runner.run(task, FakeEnvironment(), CrashingAgent())

        record = next(
            r for r in self.read(result.trace_path) if r["type"] == "agent_error"
        )
        assert "Traceback" in record["traceback"]

    def test_the_final_artifact_is_saved(self, task, tmp_path):
        runner = Runner(PixelScorer(), trace_dir=tmp_path)
        environment = FakeEnvironment(
            artifact_factory=reference_after(task, "draw_circle")
        )
        result = runner.run(task, environment, ScriptedAgent([CIRCLE]))

        assert result.artifact_path.exists()
        assert result.artifact_path.read_bytes() == task.reference_bytes()

    def test_per_turn_images_are_optional(self, task, tmp_path):
        quiet = Runner(PixelScorer(), trace_dir=tmp_path / "quiet")
        loud = Runner(
            PixelScorer(),
            RunnerConfig(capture_turn_images=True),
            trace_dir=tmp_path / "loud",
        )
        agent = lambda: ScriptedAgent([CIRCLE, Action("clear")])

        quiet_result = quiet.run(task, FakeEnvironment(), agent())
        loud_result = loud.run(task, FakeEnvironment(), agent())

        assert not list(quiet_result.artifact_path.parent.glob("turn_*.png"))
        assert len(list(loud_result.artifact_path.parent.glob("turn_*.png"))) == 2

    def test_no_trace_directory_means_no_files(self, task, tmp_path):
        runner = Runner(PixelScorer())
        result = runner.run(task, FakeEnvironment(), ScriptedAgent([CIRCLE]))

        assert result.trace_path is None
        assert result.artifact_path is None
        assert not list(tmp_path.iterdir())
