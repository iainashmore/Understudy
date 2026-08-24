"""Scoring.

Deterministic, no model in the loop. Takes the final artifact plus the task's
reference and returns pass/fail with the continuous measures behind it.

The primary measure is *pixel accuracy*: the fraction of pixels whose colour
matches the reference within a per-channel tolerance, after a mild Gaussian
blur. Two design notes, both load-bearing:

- The blur exists because each layer rasterises with different machinery
  (cairo for the reference, Pillow, numpy, a browser). Their anti-aliased edges
  disagree by up to half the foreground/background delta, which no per-channel
  tolerance can absorb. A ~1px blur makes that disagreement vanish while a 2px
  position error still shows up.
- Pixel accuracy is used for pass/fail in preference to mean error because mean
  error barely moves when a small shape is missing entirely: omitting a r=40
  circle on a 200x200 canvas costs only ~8% mean error, so any threshold that
  catches it would be violently intolerant of everything else. Mean error is
  still reported -- it is a useful continuous signal, just a bad gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import numpy as np

from harness.image import Artifact, gaussian_blur, load_rgb, resize_to
from harness.task import Task


@dataclass(frozen=True)
class ScoreResult:
    """Outcome for one artifact."""

    task_id: str
    passed: bool
    score: float
    metrics: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_row(self) -> dict[str, Any]:
        """Flat form, for the results CSV."""
        row: dict[str, Any] = {
            "task_id": self.task_id,
            "passed": self.passed,
            "score": round(self.score, 6),
            "error": self.error or "",
        }
        row.update({k: round(v, 6) for k, v in sorted(self.metrics.items())})
        return row


@runtime_checkable
class Scorer(Protocol):
    """Swappable so the SVG scorer can be replaced wholesale by a CAD one
    (watertightness, volume delta) without the runner noticing."""

    def score(self, task: Task, artifact: Artifact) -> ScoreResult: ...


class PixelScorer:
    """Compares a rendered artifact against the task's reference image."""

    name = "pixel"

    def score(self, task: Task, artifact: Artifact) -> ScoreResult:
        try:
            candidate = load_rgb(artifact)
        except Exception as exc:
            # A layer that produced nothing usable fails the task; it must not
            # take the run down with it. The error text is part of the trace.
            return ScoreResult(
                task_id=task.task_id,
                passed=False,
                score=0.0,
                error=f"could not read artifact: {exc}",
            )

        try:
            reference = load_rgb(task.reference_bytes())
        except Exception as exc:
            return ScoreResult(
                task_id=task.task_id,
                passed=False,
                score=0.0,
                error=f"could not read reference: {exc}",
            )

        height, width = reference.shape[:2]
        original_shape = candidate.shape[:2]
        resized = original_shape != (height, width)
        if resized:
            candidate = resize_to(candidate, height, width)

        config = task.scoring
        blurred_reference = gaussian_blur(reference, config.blur_sigma)
        blurred_candidate = gaussian_blur(candidate, config.blur_sigma)

        deviation = np.abs(blurred_candidate - blurred_reference)
        worst_channel = deviation.max(axis=2)
        within_tolerance = worst_channel <= config.channel_tolerance
        pixel_accuracy = float(within_tolerance.mean())

        raw_error = np.abs(
            candidate.astype(np.float64) - reference.astype(np.float64)
        )
        metrics = {
            "pixel_accuracy": pixel_accuracy,
            "mean_abs_error": float(raw_error.mean() / 255.0),
            "l1_similarity": float(1.0 - raw_error.mean() / 255.0),
            "max_channel_delta": float(raw_error.max()),
            "worst_pixel_deviation": float(worst_channel.max()),
        }

        return ScoreResult(
            task_id=task.task_id,
            passed=pixel_accuracy >= config.pass_threshold,
            score=pixel_accuracy,
            metrics=metrics,
            details={
                "scorer": self.name,
                "pass_threshold": config.pass_threshold,
                "channel_tolerance": config.channel_tolerance,
                "blur_sigma": config.blur_sigma,
                "reference_size": [width, height],
                "candidate_size": [original_shape[1], original_shape[0]],
                "resized": resized,
                "pixels_outside_tolerance": int((~within_tolerance).sum()),
            },
        )
