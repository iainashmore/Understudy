"""Tasks load, validate, and stay layer-agnostic."""

from __future__ import annotations

import json
import re

import pytest

from harness.task import (
    Difficulty,
    Task,
    list_task_ids,
    load_all_tasks,
    load_golden_recipe,
    load_task,
    task_path,
)

# Words that would tell the agent *how* to draw rather than what to draw. A
# prompt containing any of them has leaked one layer's action space into a task
# object that all three layers share, which quietly biases the comparison.
LAYER_SPECIFIC_TERMS = (
    "svg",
    "click",
    "drag",
    "keystroke",
    "screenshot",
    "dom",
    "python",
    "javascript",
    "function",
    "api",
    "buffer",
    "code",
)


def test_task_set_is_not_empty():
    assert len(list_task_ids()) >= 9


def test_every_task_loads():
    for task in load_all_tasks():
        assert isinstance(task, Task)
        assert task.task_id
        assert task.description
        assert task.prompt


def test_all_three_difficulty_tiers_are_populated():
    tiers = {task.difficulty for task in load_all_tasks()}
    assert tiers == set(Difficulty), "the difficulty gradient has a gap"


def test_tasks_are_ordered_by_tier():
    tasks = load_all_tasks()
    assert [t.difficulty for t in tasks] == sorted(t.difficulty for t in tasks)


def test_prompts_are_layer_agnostic():
    for task in load_all_tasks():
        lowered = task.prompt.lower()
        for term in LAYER_SPECIFIC_TERMS:
            # Word boundaries matter: "random" contains "dom" and "capital"
            # contains "api".
            assert not re.search(rf"\b{term}\b", lowered), (
                f"{task.task_id} prompt mentions {term!r}, which belongs to one "
                f"layer's action space and must not appear in a shared task"
            )


def test_prompts_state_the_coordinate_convention():
    # Without this every score is a coin flip on whether y points down.
    for task in load_all_tasks():
        assert "top-left" in task.prompt.lower()


def test_prompts_state_the_canvas_size_and_background():
    for task in load_all_tasks():
        lowered = task.prompt.lower()
        assert f"{task.canvas.width}x{task.canvas.height}" in lowered
        assert task.canvas.background in lowered


def test_task_object_does_not_expose_the_golden_recipe():
    # An environment handed the recipe would be handed a shape list, which is
    # roughly the API layer's action space -- i.e. the answer.
    task = load_task("t01_red_circle")
    serialised = json.dumps(task, default=str)
    assert "golden" not in serialised
    assert "shapes" not in serialised
    assert not hasattr(task, "golden")


def test_golden_recipe_is_reachable_for_authoring():
    recipe = load_golden_recipe("t01_red_circle")
    assert recipe["shapes"][0]["type"] == "circle"


def test_tasks_are_frozen():
    task = load_task("t01_red_circle")
    with pytest.raises(Exception):
        task.task_id = "something-else"  # type: ignore[misc]


def test_unknown_task_id_is_a_clear_error():
    with pytest.raises(FileNotFoundError):
        load_task("no_such_task")


def test_id_must_match_filename(tmp_path, monkeypatch):
    import harness.task as task_module

    monkeypatch.setattr(task_module, "TASKS_DIR", tmp_path)
    (tmp_path / "mismatch.json").write_text(json.dumps({"id": "other"}))
    with pytest.raises(ValueError, match="does not match filename"):
        load_task("mismatch")


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"canvas": {"width": 0, "height": 10, "background": "#ffffff"}}, "positive"),
        ({"canvas": {"width": 10, "height": 10, "background": "red"}}, "#rrggbb"),
        ({"difficulty": "impossible"}, "unknown difficulty"),
        ({"scoring": {"pass_threshold": 1.5}}, "pass_threshold"),
        ({"scoring": {"channel_tolerance": 900}}, "channel_tolerance"),
        ({"scoring": {"blur_sigma": -1}}, "blur_sigma"),
    ],
)
def test_malformed_tasks_are_rejected(tmp_path, monkeypatch, mutation, message):
    import harness.task as task_module

    raw = json.loads(task_path("t01_red_circle").read_text())
    raw.update(mutation)
    raw["id"] = "broken"
    monkeypatch.setattr(task_module, "TASKS_DIR", tmp_path)
    (tmp_path / "broken.json").write_text(json.dumps(raw))

    with pytest.raises(ValueError, match=message):
        load_task("broken")


def test_missing_required_key_is_rejected(tmp_path, monkeypatch):
    import harness.task as task_module

    raw = json.loads(task_path("t01_red_circle").read_text())
    del raw["prompt"]
    raw["id"] = "broken"
    monkeypatch.setattr(task_module, "TASKS_DIR", tmp_path)
    (tmp_path / "broken.json").write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="prompt"):
        load_task("broken")
