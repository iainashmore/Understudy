"""The API layer."""

from __future__ import annotations

import re

import pytest

from harness.agents import oracle_agent
from harness.environment import Environment
from harness.environments.api import (
    OPERATIONS,
    OPERATIONS_BY_NAME,
    APIEnvironment,
    oracle_actions,
)
from harness.image import load_rgb
from harness.interaction import Action, Layer
from harness.runner import Outcome, Runner
from harness.scorer import PixelScorer
from harness.task import load_all_tasks, load_golden_recipe, load_task

CIRCLE = Action("draw_circle", {"cx": 100, "cy": 100, "r": 40, "fill": "#ff0000"})


@pytest.fixture
def task():
    return load_task("t01_red_circle")


@pytest.fixture
def env(task):
    environment = APIEnvironment()
    environment.reset(task.brief())
    return environment


def pixels(environment):
    return load_rgb(environment.render())


def test_satisfies_the_environment_protocol():
    assert isinstance(APIEnvironment(), Environment)
    assert APIEnvironment().layer is Layer.API


class TestInterface:
    def test_preamble_states_the_canvas_geometry(self, env):
        preamble = env.interface().preamble
        assert "200x200" in preamble
        assert "top-left" in preamble

    def test_preamble_states_the_layers_own_semantics(self, env):
        # Tier 3 turns on paint order, and every layer documents its own
        # semantics. Withholding it here would be the asymmetry.
        assert "later shape covers an earlier one" in env.interface().preamble

    def test_preamble_says_nothing_about_the_task(self, env):
        preamble = env.interface().preamble.lower()
        for leak in ("circle of radius", "red", "40", "blue"):
            assert leak not in preamble

    def test_every_operation_is_documented(self, env):
        described = env.interface().describe()
        for spec in OPERATIONS:
            assert spec.to_operation().signature() in described
        assert "done()" in described

    def test_documentation_and_validation_share_one_source(self, env):
        """Docs that drift from validation would make the layer look worse than
        it is, for reasons that have nothing to do with abstraction."""
        for operation in env.interface().operations:
            spec = OPERATIONS_BY_NAME[operation.name]
            documented = [parameter.name for parameter in operation.parameters]
            assert documented == [param.name for param in spec.params]


class TestDrawing:
    def test_reset_gives_a_blank_canvas(self, env, task):
        observation = env.reset(task.brief())
        assert observation.has_image
        assert "Nothing drawn yet" in observation.text
        assert tuple(pixels(env)[100, 100]) == (0, 0, 255)

    def test_reset_discards_the_previous_run(self, env, task):
        env.step(CIRCLE)
        env.reset(task.brief())
        assert env.shapes == []
        assert tuple(pixels(env)[100, 100]) == (0, 0, 255)

    def test_a_circle_lands_where_it_was_asked_for(self, env):
        observation = env.step(CIRCLE)
        assert observation.error is None
        assert tuple(pixels(env)[100, 100]) == (255, 0, 0)
        assert tuple(pixels(env)[100, 20]) == (0, 0, 255)

    def test_every_shape_operation_marks_the_canvas(self, task):
        drawings = [
            Action("draw_circle", {"cx": 100, "cy": 100, "r": 40, "fill": "#ff0000"}),
            Action("draw_rect", {"x": 10, "y": 10, "width": 50, "height": 50, "fill": "#ff0000"}),
            Action("draw_ellipse", {"cx": 100, "cy": 100, "rx": 60, "ry": 20, "fill": "#ff0000"}),
            Action("draw_polygon", {"points": [[10, 10], [190, 10], [100, 190]], "fill": "#ff0000"}),
            Action("draw_line", {"x1": 0, "y1": 100, "x2": 199, "y2": 100, "stroke": "#ff0000", "stroke_width": 8}),
        ]
        for action in drawings:
            environment = APIEnvironment()
            environment.reset(task.brief())
            before = pixels(environment).copy()
            observation = environment.step(action)
            assert observation.error is None, action.name
            assert (pixels(environment) != before).any(), f"{action.name} drew nothing"

    def test_later_shapes_cover_earlier_ones(self, env):
        env.step(Action("draw_rect", {"x": 0, "y": 0, "width": 200, "height": 200, "fill": "#ff0000"}))
        env.step(Action("draw_rect", {"x": 0, "y": 0, "width": 200, "height": 200, "fill": "#00ff00"}))
        assert tuple(pixels(env)[100, 100]) == (0, 255, 0)

    def test_clear_removes_everything(self, env):
        env.step(CIRCLE)
        observation = env.step(Action("clear"))
        assert "removing 1 shape" in observation.text
        assert tuple(pixels(env)[100, 100]) == (0, 0, 255)

    def test_three_digit_hex_is_accepted(self, env):
        assert env.step(Action("draw_circle", {"cx": 100, "cy": 100, "r": 40, "fill": "#f00"})).error is None
        assert tuple(pixels(env)[100, 100]) == (255, 0, 0)

    def test_the_canvas_is_a_pure_function_of_the_actions(self, task):
        def draw():
            environment = APIEnvironment()
            environment.reset(task.brief())
            environment.step(CIRCLE)
            environment.step(Action("draw_rect", {"x": 5, "y": 5, "width": 20, "height": 20, "fill": "#00ff00"}))
            return environment.artifact()

        assert draw() == draw(), "the trace claims the canvas follows from the actions"

    def test_the_artifact_matches_the_canvas_size(self, env, task):
        assert load_rgb(env.artifact()).shape[:2] == (
            task.canvas.height,
            task.canvas.width,
        )

    def test_artifact_before_reset_is_none(self):
        assert APIEnvironment().artifact() is None

    def test_a_shape_off_the_canvas_is_flagged(self, env):
        observation = env.step(
            Action("draw_circle", {"cx": 900, "cy": 900, "r": 10, "fill": "#ff0000"})
        )
        assert observation.error is None, "off-canvas is legal, just unhelpful"
        assert "entirely outside the canvas" in observation.text

    def test_a_partly_visible_shape_is_not_flagged(self, env):
        observation = env.step(
            Action("draw_circle", {"cx": 0, "cy": 0, "r": 30, "fill": "#ff0000"})
        )
        assert "outside the canvas" not in observation.text


class TestErrors:
    CASES = [
        (Action("draw_square", {}), "unknown operation 'draw_square'"),
        (Action("draw_circle", {"cx": 1, "cy": 1, "r": 1, "fill": "#fff", "z": 2}), "unexpected argument 'z'"),
        (Action("draw_circle", {"cx": 1, "cy": 1, "r": 1}), "missing required argument 'fill'"),
        (Action("draw_circle", {"cx": 1, "cy": 1, "r": "forty", "fill": "#fff"}), "'r' must be a number"),
        (Action("draw_circle", {"cx": 1, "cy": 1, "r": -5, "fill": "#fff"}), "'r' must be greater than 0"),
        (Action("draw_circle", {"cx": 1, "cy": 1, "r": 1, "fill": "red"}), "must be a colour"),
        (Action("draw_polygon", {"points": [[0, 0], [1, 1]], "fill": "#fff"}), "at least three"),
        (Action("draw_polygon", {"points": [[0, 0], [1, 1], [2]], "fill": "#fff"}), "point 2 must be an [x, y] pair"),
        (Action("draw_polygon", {"points": "triangle", "fill": "#fff"}), "must be a list"),
        (Action("clear", {"all": True}), "takes no arguments"),
    ]

    @pytest.mark.parametrize("action, expected", CASES, ids=[c[1][:28] for c in CASES])
    def test_bad_actions_are_reported_not_raised(self, env, action, expected):
        observation = env.step(action)
        assert observation.error is not None
        assert expected in observation.error

    @pytest.mark.parametrize("action, _", CASES, ids=[c[1][:28] for c in CASES])
    def test_an_error_still_returns_the_canvas(self, env, action, _):
        # The agent needs to see that nothing changed.
        assert env.step(action).has_image

    def test_a_rejected_action_leaves_the_canvas_alone(self, env):
        env.step(CIRCLE)
        before = env.artifact()
        env.step(Action("draw_circle", {"cx": 1, "cy": 1, "r": 1, "fill": "puce"}))
        assert env.artifact() == before
        assert len(env.shapes) == 1

    def test_error_messages_name_the_operation_and_the_argument(self, env):
        error = env.step(
            Action("draw_rect", {"x": 0, "y": 0, "width": 5, "height": "tall", "fill": "#fff"})
        ).error
        assert error.startswith("draw_rect:")
        assert "'height'" in error

    def test_unknown_operations_list_what_is_available(self, env):
        error = env.step(Action("paint_bucket")).error
        for name in OPERATIONS_BY_NAME:
            assert name in error
        assert "done" in error

    def test_booleans_are_not_numbers(self, env):
        # bool is an int subclass; a naive check would draw a circle of radius 1.
        error = env.step(
            Action("draw_circle", {"cx": 1, "cy": 1, "r": True, "fill": "#fff"})
        ).error
        assert "must be a number" in error

    def test_step_never_raises_on_malformed_input(self, env):
        junk = [
            Action("draw_circle", {}),
            Action("draw_circle", {"cx": None, "cy": None, "r": None, "fill": None}),
            Action("draw_polygon", {"points": [], "fill": "#ffffff"}),
            Action("draw_line", {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "stroke": "#fff", "stroke_width": 0}),
            Action("draw_rect", {"x": 0, "y": 0, "width": [1], "height": 1, "fill": "#fff"}),
        ]
        for action in junk:
            assert env.step(action).error is not None


class TestOracle:
    """The environment has to be capable of passing before any agent's failure
    here can be read as the agent's."""

    @pytest.mark.parametrize(
        "task", load_all_tasks(), ids=[t.task_id for t in load_all_tasks()]
    )
    def test_the_oracle_passes_every_task(self, task):
        agent = oracle_agent(
            load_golden_recipe(task.task_id), oracle_actions, task.canvas
        )
        result = Runner(PixelScorer()).run(task, APIEnvironment(), agent)

        assert result.outcome is Outcome.COMPLETED
        assert result.passed, (
            f"{task.task_id} cannot be solved through the API layer at all "
            f"(score {result.score:.5f}) -- the environment is broken, not the agent"
        )

    def test_the_translator_covers_every_shape_the_tasks_use(self):
        used = {
            shape["type"]
            for task in load_all_tasks()
            for shape in load_golden_recipe(task.task_id)["shapes"]
        }
        translated = {
            action.name
            for task in load_all_tasks()
            for action in oracle_actions(load_golden_recipe(task.task_id)["shapes"])
        }
        assert used <= {"circle", "rect", "ellipse", "polygon", "line"}
        assert all(name in OPERATIONS_BY_NAME for name in translated)

    def test_oracle_runs_stay_out_of_the_results(self, task):
        agent = oracle_agent(
            load_golden_recipe(task.task_id), oracle_actions, task.canvas
        )
        result = Runner(PixelScorer()).run(task, APIEnvironment(), agent)
        assert result.is_oracle


def test_a_full_run_end_to_end(task):
    """Task -> mock agent -> API environment -> scorer -> result, which is the
    milestone the build order asks for before anything else is built."""
    from harness.agents import ScriptedAgent

    agent = ScriptedAgent([CIRCLE], name="mock")
    result = Runner(PixelScorer()).run(task, APIEnvironment(), agent)

    assert result.outcome is Outcome.COMPLETED
    assert result.passed
    assert result.turns_used == 1
    assert re.match(r"mock\.api\.t01_red_circle\.\d+", result.run_id)
