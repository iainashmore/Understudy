"""Cross-layer invariants.

Every rule here exists because breaking it would corrupt the comparison the
harness is built to make. They apply to whichever layers currently exist, so a
new environment has to satisfy them the moment it is registered rather than
whenever someone remembers to check.
"""

from __future__ import annotations

import re

import pytest

from harness.agents import oracle_agent
from harness.environment import Environment
from harness.environments import ORACLE_TRANSLATORS, available_layers, build
from harness.image import load_rgb
from harness.interaction import DONE, Action
from harness.runner import DEFAULT_TURN_LIMITS, Outcome, Runner
from harness.scorer import PixelScorer
from harness.task import load_all_tasks, load_golden_recipe, load_task

LAYERS = available_layers()
IDS = [layer.value for layer in LAYERS]
TASK = load_task("t01_red_circle")

#: Every colour any task asks for. A preamble naming one of these would be
#: handing that task's answer to one layer and not the others.
TASK_COLOURS = {
    str(value).lower()
    for task in load_all_tasks()
    for shape in load_golden_recipe(task.task_id)["shapes"]
    for key, value in shape.items()
    if key in ("fill", "stroke")
} | {task.canvas.background for task in load_all_tasks()}


@pytest.fixture(params=LAYERS, ids=IDS)
def env(request):
    environment = build(request.param)
    environment.reset(TASK.brief())
    return environment


def test_at_least_one_layer_exists():
    assert LAYERS


def test_every_layer_satisfies_the_protocol(env):
    assert isinstance(env, Environment)


def test_every_layer_has_a_turn_budget(env):
    assert DEFAULT_TURN_LIMITS[env.layer] >= 1


class TestPromptParity:
    """The task prompt is identical everywhere, so the only text that varies
    between layers is the preamble. It has to stay task-free or the comparison
    becomes a prompt comparison."""

    def test_no_preamble_names_a_colour_any_task_asks_for(self, env):
        preamble = env.interface().preamble.lower()
        for colour in TASK_COLOURS:
            assert colour not in preamble, (
                f"{env.layer.value} preamble contains {colour}, a colour the "
                f"task set asks for"
            )

    def test_no_preamble_uses_task_vocabulary(self, env):
        preamble = env.interface().preamble.lower()
        for word in ("circle", "triangle", "checkerboard", "house", "ring", "radius"):
            assert not re.search(rf"\b{word}\b", preamble), (
                f"{env.layer.value} preamble mentions {word!r}"
            )

    def test_every_preamble_states_the_canvas_geometry(self, env):
        # Allowed, and necessary: the agent is told the size in the prompt too.
        assert f"{TASK.canvas.width}x{TASK.canvas.height}" in env.interface().preamble

    def test_every_preamble_states_the_coordinate_convention(self, env):
        preamble = env.interface().preamble.lower()
        assert "top-left" in preamble
        assert "downwards" in preamble


class TestUniformContract:
    def test_done_is_offered_everywhere(self, env):
        assert DONE in env.interface().operation_names
        assert env.interface().accepts(Action.done())

    def test_no_layer_exposes_an_operation_called_done(self, env):
        # done is the runner's, not a layer's; a layer implementing it would
        # swallow the completion signal.
        assert DONE not in {op.name for op in env.interface().operations}

    def test_the_opening_observation_shows_the_canvas(self, env):
        observation = env.reset(TASK.brief())
        assert observation.has_image
        assert observation.error is None

    def test_every_layer_returns_the_canvas_after_every_action(self, env):
        """Not just the UI one. Showing the picture to some layers and not
        others would measure sighted-versus-blind, not abstraction."""
        translate = ORACLE_TRANSLATORS[env.layer]
        for action in translate(load_golden_recipe(TASK.task_id)["shapes"], TASK.canvas):
            assert env.step(action).has_image

    def test_rejected_actions_are_observations_not_exceptions(self, env):
        observation = env.step(Action("definitely_not_an_operation", {"x": 1}))
        assert observation.error is not None
        assert observation.has_image, "the agent still needs to see the canvas"

    def test_a_rejected_action_lists_what_is_available(self, env):
        error = env.step(Action("definitely_not_an_operation")).error
        for operation in env.interface().operations:
            assert operation.name in error

    def test_the_artifact_is_a_png_at_canvas_size(self, env):
        pixels = load_rgb(env.artifact())
        assert pixels.shape[:2] == (TASK.canvas.height, TASK.canvas.width)

    def test_reset_discards_the_previous_run(self, env):
        translate = ORACLE_TRANSLATORS[env.layer]
        for action in translate(load_golden_recipe(TASK.task_id)["shapes"], TASK.canvas):
            env.step(action)
        dirty = env.artifact()

        env.reset(TASK.brief())
        assert env.artifact() != dirty
        assert env.artifact() == build(env.layer).reset(TASK.brief()).image


class TestEveryLayerCanActuallyPass:
    """Until the oracle passes, a real agent's failure at a layer cannot be
    attributed to the agent."""

    @pytest.mark.parametrize("task", load_all_tasks(), ids=[t.task_id for t in load_all_tasks()])
    @pytest.mark.parametrize("layer", LAYERS, ids=IDS)
    def test_the_oracle_passes(self, layer, task):
        agent = oracle_agent(
            load_golden_recipe(task.task_id), ORACLE_TRANSLATORS[layer], task.canvas
        )
        result = Runner(PixelScorer()).run(task, build(layer), agent)

        assert result.outcome is Outcome.COMPLETED
        assert result.passed, (
            f"{task.task_id} is unsolvable at the {layer.value} layer "
            f"(score {result.score:.5f}); that is a harness bug, and left in "
            f"place it would read as a capability gap"
        )
