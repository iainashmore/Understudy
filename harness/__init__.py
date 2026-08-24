"""Agent abstraction-layer harness."""

from harness.image import load_rgb, to_png_bytes
from harness.scorer import PixelScorer, Scorer, ScoreResult
from harness.task import (
    Canvas,
    Difficulty,
    ScoringConfig,
    Task,
    list_task_ids,
    load_all_tasks,
    load_task,
)

__all__ = [
    "Canvas",
    "Difficulty",
    "PixelScorer",
    "ScoreResult",
    "Scorer",
    "ScoringConfig",
    "Task",
    "list_task_ids",
    "load_all_tasks",
    "load_rgb",
    "load_task",
    "to_png_bytes",
]
