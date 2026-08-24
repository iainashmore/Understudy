"""The bracket.

A threshold is only meaningful if it is squeezed from both sides, so for every
task this asserts:

  above -- a correct drawing produced by a rasteriser unrelated to the one that
           made the reference still passes. Otherwise the harness measures which
           renderer an agent happens to use.

  below -- every plausible near-miss fails: nothing drawn, the figure shifted a
           pixel, shapes too small, colours off, circles squared off, a shape
           left out, and (for the occlusion tier) the right shapes stacked in
           the wrong order.

If a task cannot satisfy both, the task is broken, not the agent -- and a broken
task would show up in the results as a capability difference between layers,
which is precisely the conclusion this harness must not get wrong by accident.
"""

from __future__ import annotations

import pytest

from harness.scorer import PixelScorer
from harness.task import Difficulty, Task, load_all_tasks, load_golden_recipe
from tests.independent_renderer import (
    render,
    render_blank,
    reversed_order,
    scaled,
    squarified,
    tinted,
    without_last,
)

TASKS = load_all_tasks()
IDS = [task.task_id for task in TASKS]
SCORER = PixelScorer()


def shapes_for(task: Task) -> list[dict]:
    return load_golden_recipe(task.task_id)["shapes"]


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_correct_drawing_from_an_unrelated_rasteriser_passes(task):
    result = SCORER.score(task, render(task.canvas, shapes_for(task)))
    assert result.passed, (
        f"{task.task_id}: a correct drawing scored {result.score:.5f}, below the "
        f"{task.scoring.pass_threshold} threshold -- the threshold is measuring "
        f"anti-aliasing, not correctness"
    )


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_doing_nothing_fails(task):
    result = SCORER.score(task, render_blank(task.canvas))
    assert not result.passed, (
        f"{task.task_id}: an empty canvas scored {result.score:.5f} and passed"
    )


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_one_pixel_displacement_fails(task):
    # The tightest near-miss in the set, and so the one that sets the ceiling on
    # how high the threshold can go.
    result = SCORER.score(task, render(task.canvas, shapes_for(task), offset=(1, 0)))
    assert not result.passed


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_wrong_size_fails(task):
    result = SCORER.score(task, render(task.canvas, scaled(shapes_for(task), 0.9)))
    assert not result.passed


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_wrong_colour_fails(task):
    result = SCORER.score(task, render(task.canvas, tinted(shapes_for(task), 40)))
    assert not result.passed


@pytest.mark.parametrize(
    "task",
    [t for t in TASKS if any(s["type"] == "circle" for s in shapes_for(t))],
    ids=[t.task_id for t in TASKS if any(s["type"] == "circle" for s in shapes_for(t))],
)
def test_wrong_shape_fails(task):
    result = SCORER.score(task, render(task.canvas, squarified(shapes_for(task))))
    assert not result.passed


@pytest.mark.parametrize(
    "task",
    [t for t in TASKS if len(shapes_for(t)) > 1],
    ids=[t.task_id for t in TASKS if len(shapes_for(t)) > 1],
)
def test_stopping_one_shape_early_fails(task):
    result = SCORER.score(task, render(task.canvas, without_last(shapes_for(task))))
    assert not result.passed


OCCLUSION_TASKS = [t for t in TASKS if t.difficulty is Difficulty.OCCLUSION]


@pytest.mark.parametrize(
    "task", OCCLUSION_TASKS, ids=[t.task_id for t in OCCLUSION_TASKS]
)
def test_occlusion_tasks_actually_depend_on_order(task):
    """The tier only earns its name if drawing the same shapes in the reverse
    order produces a different picture. Otherwise it is a composition task
    wearing a hat."""
    result = SCORER.score(task, render(task.canvas, reversed_order(shapes_for(task))))
    assert not result.passed, (
        f"{task.task_id} is filed under the occlusion tier but scores "
        f"{result.score:.5f} with the draw order reversed, so it does not test "
        f"ordering at all"
    )


def test_the_threshold_sits_between_the_two_brackets():
    """Report the actual gap, and fail if the threshold is jammed against
    either edge of it. A threshold with no headroom will start producing
    spurious results the moment a fourth rasteriser (the browser, when the UI
    environment lands) enters the picture."""
    worst_correct = 1.0
    best_wrong = 0.0

    for task in TASKS:
        shapes = shapes_for(task)
        worst_correct = min(
            worst_correct, SCORER.score(task, render(task.canvas, shapes)).score
        )
        wrong = [
            render_blank(task.canvas),
            render(task.canvas, shapes, offset=(1, 0)),
            render(task.canvas, scaled(shapes, 0.9)),
            render(task.canvas, tinted(shapes, 40)),
        ]
        if len(shapes) > 1:
            wrong.append(render(task.canvas, without_last(shapes)))
        if task.difficulty is Difficulty.OCCLUSION:
            wrong.append(render(task.canvas, reversed_order(shapes)))
        for artifact in wrong:
            best_wrong = max(best_wrong, SCORER.score(task, artifact).score)

    thresholds = {task.scoring.pass_threshold for task in TASKS}
    assert len(thresholds) == 1, "per-task thresholds -- check this by hand"
    threshold = thresholds.pop()

    assert best_wrong < threshold < worst_correct, (
        f"threshold {threshold} is outside the separating gap "
        f"[{best_wrong:.5f}, {worst_correct:.5f}]"
    )
    # Headroom on both sides, not just technically inside the gap.
    assert worst_correct - threshold >= 0.005, "no headroom above the threshold"
    assert threshold - best_wrong >= 0.005, "no headroom below the threshold"
