"""The kernel layer."""

from __future__ import annotations

import numpy as np
import pytest

from harness.agents import oracle_agent
from harness.environment import Environment
from harness.environments.api import APIEnvironment
from harness.environments.api import oracle_actions as api_oracle_actions
from harness.environments.kernel import (
    MAX_ENTRIES,
    OPERATIONS_BY_NAME,
    KernelEnvironment,
    oracle_actions,
)
from harness.image import load_rgb
from harness.interaction import Action, Layer
from harness.runner import Outcome, Runner
from harness.scorer import PixelScorer
from harness.task import load_all_tasks, load_golden_recipe, load_task

RED = "#ff0000"


@pytest.fixture
def task():
    return load_task("t01_red_circle")


@pytest.fixture
def env(task):
    environment = KernelEnvironment()
    environment.reset(task.brief())
    return environment


def test_satisfies_the_environment_protocol():
    assert isinstance(KernelEnvironment(), Environment)
    assert KernelEnvironment().layer is Layer.KERNEL


class TestItIsActuallyTheKernelLayer:
    """The properties that make this lower than the API layer rather than
    merely different from it."""

    def test_there_are_no_shape_primitives(self):
        # If a circle ever appears here the ladder stops being monotone and the
        # comparison loses its meaning.
        for name in OPERATIONS_BY_NAME:
            assert not any(
                shape in name
                for shape in ("circle", "rect", "ellipse", "polygon", "line", "draw")
            ), f"{name} is a shape primitive; this layer must not have one"

    def test_the_vocabulary_is_three_primitive_writes(self):
        assert set(OPERATIONS_BY_NAME) == {"fill", "set_pixels", "write_spans"}

    def test_nothing_is_clipped(self, env):
        error = env.step(
            Action("write_spans", {"spans": [[190, 10, 20, RED]]})
        ).error
        assert "nothing is clipped" in error

    def test_pixel_values_are_exact(self, env):
        """No anti-aliasing: what the agent wrote is what is there, including at
        the edge of a run."""
        env.step(Action("write_spans", {"spans": [[10, 10, 5, RED]]}))
        pixels = load_rgb(env.artifact())
        assert tuple(pixels[10, 10]) == (255, 0, 0)
        assert tuple(pixels[10, 14]) == (255, 0, 0)
        assert tuple(pixels[10, 15]) == (0, 0, 255), "no blend past the run"


class TestInterface:
    def test_preamble_states_the_buffer_geometry(self, env):
        preamble = env.interface().preamble
        assert "200x200" in preamble
        assert "[0, 200)" in preamble

    def test_preamble_states_the_layers_own_semantics(self, env):
        preamble = env.interface().preamble
        assert "no shape primitives" in preamble
        assert "not\ntrimmed" in preamble or "not trimmed" in preamble
        assert "no anti-aliasing" in preamble

    def test_preamble_says_nothing_about_the_task(self, env):
        preamble = env.interface().preamble.lower()
        for leak in ("radius", "circle", "red", "#ff0000"):
            assert leak not in preamble

    def test_every_operation_is_documented(self, env):
        described = env.interface().describe()
        for name in OPERATIONS_BY_NAME:
            assert name in described
        assert "done()" in described


class TestWrites:
    def test_reset_fills_the_buffer_with_the_background(self, env):
        pixels = load_rgb(env.artifact())
        assert (pixels == np.array([0, 0, 255], dtype=np.uint8)).all()

    def test_reset_discards_the_previous_run(self, env, task):
        env.step(Action("fill", {"colour": RED}))
        env.reset(task.brief())
        assert (load_rgb(env.artifact()) == np.array([0, 0, 255])).all()

    def test_fill_sets_every_pixel(self, env):
        observation = env.step(Action("fill", {"colour": RED}))
        assert observation.error is None
        assert "40000" in observation.text
        assert (load_rgb(env.artifact()) == np.array([255, 0, 0])).all()

    def test_set_pixels_sets_exactly_those_pixels(self, env):
        env.step(Action("set_pixels", {"pixels": [[1, 2, RED], [3, 4, RED]]}))
        pixels = load_rgb(env.artifact())
        assert tuple(pixels[2, 1]) == (255, 0, 0)
        assert tuple(pixels[4, 3]) == (255, 0, 0)
        assert tuple(pixels[0, 0]) == (0, 0, 255)

    def test_write_spans_covers_the_stated_run(self, env):
        env.step(Action("write_spans", {"spans": [[5, 7, 4, RED]]}))
        row = load_rgb(env.artifact())[7]
        assert [tuple(p) for p in row[5:9]] == [(255, 0, 0)] * 4
        assert tuple(row[4]) == (0, 0, 255)
        assert tuple(row[9]) == (0, 0, 255)

    def test_writes_overwrite_in_order(self, env):
        env.step(
            Action("set_pixels", {"pixels": [[1, 1, RED], [1, 1, "#00ff00"]]})
        )
        assert tuple(load_rgb(env.artifact())[1, 1]) == (0, 255, 0)

    def test_entries_are_applied_in_one_turn(self, env):
        """A pixel per turn would make the layer measure the turn budget rather
        than the abstraction."""
        spans = [[0, y, 200, RED] for y in range(200)]
        observation = env.step(Action("write_spans", {"spans": spans}))
        assert observation.error is None
        assert (load_rgb(env.artifact()) == np.array([255, 0, 0])).all()

    def test_the_observation_reports_buffer_state(self, env):
        observation = env.step(Action("write_spans", {"spans": [[0, 0, 10, RED]]}))
        assert "Wrote 10 pixel(s)" in observation.text
        assert "10 of 40000 pixels differ" in observation.text

    def test_integral_floats_are_accepted(self, env):
        assert env.step(
            Action("write_spans", {"spans": [[5.0, 7.0, 4.0, RED]]})
        ).error is None

    def test_the_artifact_matches_the_canvas_size(self, env, task):
        assert load_rgb(env.artifact()).shape[:2] == (
            task.canvas.height,
            task.canvas.width,
        )

    def test_artifact_before_reset_is_none(self):
        assert KernelEnvironment().artifact() is None


class TestErrors:
    CASES = [
        (Action("draw_circle", {"cx": 1}), "unknown operation 'draw_circle'"),
        (Action("fill", {}), "missing required argument 'colour'"),
        (Action("fill", {"colour": "red"}), "must be a colour"),
        (Action("fill", {"colour": RED, "alpha": 1}), "unexpected argument 'alpha'"),
        (Action("set_pixels", {"pixels": []}), "'pixels' is empty"),
        (Action("set_pixels", {"pixels": "lots"}), "must be a list"),
        (Action("set_pixels", {"pixels": [[1, 2]]}), "must be [x, y, colour]"),
        (Action("set_pixels", {"pixels": [[1, 250, RED]]}), "outside [0, 200)"),
        (Action("set_pixels", {"pixels": [[-1, 2, RED]]}), "outside [0, 200)"),
        (Action("set_pixels", {"pixels": [[1.5, 2, RED]]}), "whole number of pixels"),
        (Action("set_pixels", {"pixels": [["x", 2, RED]]}), "must be an integer"),
        (Action("write_spans", {"spans": [[1, 2, 3]]}), "must be [x, y, length, colour]"),
        (Action("write_spans", {"spans": [[1, 2, 0, RED]]}), "must be at least 1"),
        (Action("write_spans", {"spans": [[195, 2, 10, RED]]}), "nothing is clipped"),
        (Action("write_spans", {"spans": [[1, 999, 10, RED]]}), "outside [0, 200)"),
    ]

    @pytest.mark.parametrize("action, expected", CASES, ids=[c[1][:26] for c in CASES])
    def test_bad_writes_are_reported_not_raised(self, env, action, expected):
        observation = env.step(action)
        assert observation.error is not None
        assert expected in observation.error

    @pytest.mark.parametrize("action, _", CASES, ids=[c[1][:26] for c in CASES])
    def test_an_error_still_returns_the_buffer(self, env, action, _):
        assert env.step(action).has_image

    def test_errors_name_the_offending_entry(self, env):
        """Terse because the operations are terse -- not vague. Degrading the
        error signal here on purpose would rig the comparison."""
        error = env.step(
            Action("set_pixels", {"pixels": [[1, 1, RED], [2, 2, RED], [3, 900, RED]]})
        ).error
        assert "entry 2" in error
        assert "y=900" in error
        assert "[0, 200)" in error

    def test_a_rejected_batch_writes_nothing_at_all(self, env):
        """Validated in full before anything lands: a half-applied batch would
        leave the agent unable to tell what state the buffer is in."""
        before = env.artifact()
        env.step(
            Action("set_pixels", {"pixels": [[1, 1, RED], [2, 2, RED], [3, 900, RED]]})
        )
        assert env.artifact() == before

    def test_booleans_are_not_coordinates(self, env):
        assert "must be an integer" in env.step(
            Action("set_pixels", {"pixels": [[True, 1, RED]]})
        ).error

    def test_an_oversized_payload_is_rejected(self, env):
        spans = [[0, 0, 1, RED]] * (MAX_ENTRIES + 1)
        assert "limit" in env.step(Action("write_spans", {"spans": spans})).error

    def test_unknown_operations_list_what_is_available(self, env):
        error = env.step(Action("blit")).error
        for name in OPERATIONS_BY_NAME:
            assert name in error
        assert "done" in error

    def test_step_never_raises_on_malformed_input(self, env):
        junk = [
            Action("write_spans", {"spans": None}),
            Action("set_pixels", {"pixels": [None]}),
            Action("set_pixels", {"pixels": [[None, None, None]]}),
            Action("fill", {"colour": 255}),
            Action("write_spans", {"spans": [[0, 0, 1, RED, "extra"]]}),
        ]
        for action in junk:
            assert env.step(action).error is not None


class TestOracle:
    @pytest.mark.parametrize(
        "task", load_all_tasks(), ids=[t.task_id for t in load_all_tasks()]
    )
    def test_the_oracle_passes_every_task(self, task):
        agent = oracle_agent(load_golden_recipe(task.task_id), oracle_actions, task.canvas)
        result = Runner(PixelScorer()).run(task, KernelEnvironment(), agent)

        assert result.outcome is Outcome.COMPLETED
        assert result.passed, (
            f"{task.task_id} cannot be solved at the kernel layer at all "
            f"(score {result.score:.5f}) -- the environment is broken, not the agent"
        )

    @pytest.mark.parametrize(
        "task", load_all_tasks(), ids=[t.task_id for t in load_all_tasks()]
    )
    def test_hard_edges_are_not_penalised(self, task):
        """The layer cannot anti-alias, and the references are anti-aliased. If
        the scorer charged for that, every kernel result would be a measurement
        of the scorer rather than of the layer."""
        agent = oracle_agent(load_golden_recipe(task.task_id), oracle_actions, task.canvas)
        result = Runner(PixelScorer()).run(task, KernelEnvironment(), agent)
        assert result.score == pytest.approx(1.0), (
            "the blur is supposed to absorb the aliasing difference entirely"
        )

    @pytest.mark.parametrize(
        "task", load_all_tasks(), ids=[t.task_id for t in load_all_tasks()]
    )
    def test_the_oracle_fits_well_inside_the_turn_budget(self, task):
        from harness.runner import DEFAULT_TURN_LIMITS

        actions = oracle_actions(load_golden_recipe(task.task_id)["shapes"], task.canvas)
        assert len(actions) < DEFAULT_TURN_LIMITS[Layer.KERNEL]

    def test_the_translator_covers_every_shape_the_tasks_use(self):
        for task in load_all_tasks():
            shapes = load_golden_recipe(task.task_id)["shapes"]
            assert oracle_actions(shapes, task.canvas), task.task_id

    def test_an_unrasterisable_shape_says_so(self, task):
        with pytest.raises(KeyError, match="cannot rasterise"):
            oracle_actions([{"type": "bezier", "fill": RED}], task.canvas)


def test_the_two_layers_reach_the_same_picture_by_different_means(task):
    """Both pass, and neither is scoring its own rasteriser: the pixels differ,
    the verdict does not."""
    recipe = load_golden_recipe(task.task_id)["shapes"]

    api = APIEnvironment()
    api.reset(task.brief())
    for action in api_oracle_actions(recipe, task.canvas):
        api.step(action)

    kernel = KernelEnvironment()
    kernel.reset(task.brief())
    for action in oracle_actions(recipe, task.canvas):
        kernel.step(action)

    scorer = PixelScorer()
    assert scorer.score(task, api.artifact()).passed
    assert scorer.score(task, kernel.artifact()).passed
    assert api.artifact() != kernel.artifact(), "anti-aliased versus hard-edged"
