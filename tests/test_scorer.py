"""Scorer mechanics: the metrics, the failure paths, the protocol."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from harness.image import load_rgb, to_png_bytes
from harness.scorer import PixelScorer, ScoreResult, Scorer
from harness.task import ScoringConfig, load_task

TASK_ID = "t01_red_circle"


@pytest.fixture
def task():
    return load_task(TASK_ID)


@pytest.fixture
def scorer():
    return PixelScorer()


def test_pixel_scorer_satisfies_the_protocol(scorer):
    assert isinstance(scorer, Scorer)


def test_reference_scores_perfectly_against_itself(task, scorer):
    result = scorer.score(task, task.reference_bytes())
    assert result.passed
    assert result.score == pytest.approx(1.0)
    assert result.metrics["mean_abs_error"] == pytest.approx(0.0)
    assert result.metrics["max_channel_delta"] == pytest.approx(0.0)
    assert result.error is None


def test_wrong_colour_everywhere_fails(task, scorer):
    inverted = 255 - load_rgb(task.reference_bytes())
    result = scorer.score(task, to_png_bytes(inverted))
    assert not result.passed
    assert result.score < 0.5


def test_unreadable_artifact_fails_without_raising(task, scorer):
    result = scorer.score(task, b"this is not a png")
    assert not result.passed
    assert result.score == 0.0
    assert result.error and "could not read artifact" in result.error


def test_empty_artifact_fails_without_raising(task, scorer):
    result = scorer.score(task, b"")
    assert not result.passed
    assert result.error


def test_missing_artifact_file_fails_without_raising(task, scorer, tmp_path):
    result = scorer.score(task, tmp_path / "nothing.png")
    assert not result.passed
    assert result.error


def test_missing_reference_fails_without_raising(task, scorer, tmp_path):
    orphan = dataclasses.replace(task, reference_path=tmp_path / "absent.png")
    result = scorer.score(orphan, task.reference_bytes())
    assert not result.passed
    assert result.error and "reference" in result.error


def test_mismatched_size_is_resampled_and_recorded(task, scorer):
    from PIL import Image
    import io

    with Image.open(io.BytesIO(task.reference_bytes())) as image:
        doubled = image.resize((image.width * 2, image.height * 2), Image.NEAREST)
        buffer = io.BytesIO()
        doubled.save(buffer, format="PNG")

    result = scorer.score(task, buffer.getvalue())
    assert result.details["resized"] is True
    assert result.details["candidate_size"] == [
        task.canvas.width * 2,
        task.canvas.height * 2,
    ]
    # A HiDPI screenshot of a correct drawing is still a correct drawing.
    assert result.passed


def test_matching_size_is_not_resampled(task, scorer):
    result = scorer.score(task, task.reference_bytes())
    assert result.details["resized"] is False


def test_details_record_the_settings_actually_used(task, scorer):
    strict = dataclasses.replace(
        task, scoring=ScoringConfig(pass_threshold=0.999, channel_tolerance=2, blur_sigma=0.0)
    )
    result = scorer.score(strict, strict.reference_bytes())
    assert result.details["pass_threshold"] == 0.999
    assert result.details["channel_tolerance"] == 2
    assert result.details["blur_sigma"] == 0.0


def test_threshold_is_what_decides_pass_fail(task, scorer):
    from tests.independent_renderer import render_blank

    blank = render_blank(task.canvas)
    baseline = scorer.score(task, blank).score

    lenient = dataclasses.replace(
        task, scoring=dataclasses.replace(task.scoring, pass_threshold=baseline - 0.01)
    )
    assert scorer.score(lenient, blank).passed
    assert not scorer.score(task, blank).passed


def test_alpha_is_composited_over_white(task, scorer):
    from PIL import Image
    import io

    transparent = Image.new("RGBA", task.canvas.size, (0, 0, 0, 0))
    buffer = io.BytesIO()
    transparent.save(buffer, format="PNG")
    # Should behave like a white canvas, not crash and not read as black.
    result = scorer.score(task, buffer.getvalue())
    assert result.error is None
    assert not result.passed


def test_as_row_is_flat_and_csv_ready(task, scorer):
    row = scorer.score(task, task.reference_bytes()).as_row()
    assert row["task_id"] == TASK_ID
    assert row["passed"] is True
    assert row["error"] == ""
    assert isinstance(row["pixel_accuracy"], float)
    assert all(not isinstance(value, (dict, list)) for value in row.values())


def test_score_result_is_frozen():
    result = ScoreResult(task_id="x", passed=True, score=1.0)
    with pytest.raises(Exception):
        result.passed = False  # type: ignore[misc]


def test_blur_sigma_zero_compares_raw_pixels(task, scorer):
    sharp = dataclasses.replace(
        task, scoring=dataclasses.replace(task.scoring, blur_sigma=0.0)
    )
    result = scorer.score(sharp, sharp.reference_bytes())
    assert result.score == pytest.approx(1.0)


def test_scoring_is_deterministic(task, scorer):
    from tests.independent_renderer import render
    from harness.task import load_golden_recipe

    artifact = render(task.canvas, load_golden_recipe(TASK_ID)["shapes"])
    scores = {scorer.score(task, artifact).score for _ in range(5)}
    assert len(scores) == 1
