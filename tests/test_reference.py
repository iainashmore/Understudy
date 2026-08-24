"""The golden references themselves: current, correctly sized, and actually
depicting what the prompt asks for."""

from __future__ import annotations

import pytest

from harness.image import load_rgb
from harness.reference import RecipeError, recipe_to_svg, render_reference
from harness.scorer import PixelScorer
from harness.task import Canvas, load_all_tasks, load_golden_recipe

TASKS = load_all_tasks()
IDS = [task.task_id for task in TASKS]
SCORER = PixelScorer()


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_reference_exists(task):
    assert task.reference_path.exists(), (
        f"no reference for {task.task_id}; run tools/generate_references.py"
    )


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_reference_matches_the_canvas_size(task):
    reference = load_rgb(task.reference_bytes())
    assert reference.shape[:2] == (task.canvas.height, task.canvas.width)


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_committed_reference_is_current(task):
    """Catches a recipe edited without regenerating the PNG. Compared through
    the scorer rather than by file hash, so a cairo upgrade does not fail the
    suite while a genuinely stale image still does."""
    fresh = render_reference(task.canvas, load_golden_recipe(task.task_id))
    result = SCORER.score(task, fresh)
    assert result.score == pytest.approx(1.0, abs=1e-9), (
        f"{task.task_id}: committed reference differs from a fresh render "
        f"(accuracy {result.score:.6f}); rerun tools/generate_references.py"
    )


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_reference_is_not_a_blank_canvas(task):
    """A recipe that silently rendered nothing would give every agent a free
    pass on that task."""
    reference = load_rgb(task.reference_bytes())
    assert len(set(map(tuple, reference.reshape(-1, 3)))) > 1


def test_shapes_are_drawn_in_recipe_order():
    canvas = Canvas(width=10, height=10, background="#ffffff")
    recipe = {
        "shapes": [
            {"type": "rect", "x": 0, "y": 0, "width": 10, "height": 10, "fill": "#ff0000"},
            {"type": "rect", "x": 0, "y": 0, "width": 10, "height": 10, "fill": "#0000ff"},
        ]
    }
    svg = recipe_to_svg(canvas, recipe)
    assert svg.index("#ff0000") < svg.index("#0000ff")

    pixels = load_rgb(render_reference(canvas, recipe))
    assert tuple(pixels[5, 5]) == (0, 0, 255), "the later shape must win"


def test_spot_check_the_worked_example():
    """t01 is the spec's own example; if this is wrong, everything is."""
    task = next(t for t in TASKS if t.task_id == "t01_red_circle")
    pixels = load_rgb(task.reference_bytes())
    assert tuple(pixels[100, 100]) == (255, 0, 0), "centre should be red"
    assert tuple(pixels[0, 0]) == (0, 0, 255), "corner should be blue"
    assert tuple(pixels[100, 20]) == (0, 0, 255), "outside r=40 should be blue"


def test_spot_check_the_ring_punchout():
    """t09 only differs from a plain disc in the middle, so check the middle."""
    task = next(t for t in TASKS if t.task_id == "t09_ring_punchout")
    pixels = load_rgb(task.reference_bytes())
    assert tuple(pixels[100, 100]) == (255, 255, 255), "hole should be white"
    assert tuple(pixels[100, 50]) == (0, 128, 0), "ring body should be green"
    assert tuple(pixels[100, 5]) == (255, 255, 255), "outside should be white"


@pytest.mark.parametrize(
    "recipe, message",
    [
        ({"shapes": []}, "non-empty"),
        ({}, "non-empty"),
        ({"shapes": [{"type": "hexagon", "fill": "#ffffff"}]}, "unsupported shape"),
        ({"shapes": [{"type": "circle", "cx": 1, "cy": 1, "fill": "#ffffff"}]}, "r"),
        ({"shapes": [{"type": "circle", "cx": 1, "cy": 1, "r": 1, "fill": "red"}]}, "#rrggbb"),
        ({"shapes": [{"type": "polygon", "points": [[0, 0]], "fill": "#ffffff"}]}, "3 vertices"),
        ({"shapes": ["not an object"]}, "must be an object"),
    ],
)
def test_malformed_recipes_are_rejected(recipe, message):
    canvas = Canvas(width=10, height=10, background="#ffffff")
    with pytest.raises(RecipeError, match=message):
        recipe_to_svg(canvas, recipe)
