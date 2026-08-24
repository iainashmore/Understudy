"""Results output: the CSV and the success-rate-by-layer summary."""

from __future__ import annotations

import csv

import pytest

from harness.agents.base import Usage
from harness.interaction import Layer
from harness.results import (
    COLUMNS,
    format_summary,
    measured,
    success_rate_by_layer,
    success_rate_by_tier,
    write_csv,
)
from harness.runner import Outcome, RunResult
from harness.task import Difficulty


def make(
    task_id="t01_red_circle",
    layer=Layer.API,
    passed=True,
    agent="mock",
    is_oracle=False,
    turns=3,
    outcome=Outcome.COMPLETED,
):
    return RunResult(
        run_id=f"{agent}.{layer.value}.{task_id}.00",
        task_id=task_id,
        layer=layer,
        agent_name=agent,
        is_oracle=is_oracle,
        outcome=outcome,
        passed=passed,
        score=1.0 if passed else 0.4,
        turns_used=turns,
        turn_limit=20,
        duration_s=1.0,
        agent_seconds=0.5,
        environment_seconds=0.5,
        usage=Usage(10, 5),
        metrics={"pixel_accuracy": 1.0 if passed else 0.4},
    )


def test_csv_has_a_stable_header(tmp_path):
    path = write_csv([make()], tmp_path / "results.csv")
    with path.open() as handle:
        rows = list(csv.DictReader(handle))

    assert list(rows[0]) == COLUMNS
    assert rows[0]["task_id"] == "t01_red_circle"
    assert rows[0]["layer"] == "api"
    assert rows[0]["passed"] == "True"


def test_csv_includes_oracle_rows_but_flags_them(tmp_path):
    path = write_csv([make(), make(agent="oracle", is_oracle=True)], tmp_path / "r.csv")
    with path.open() as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert {row["is_oracle"] for row in rows} == {"True", "False"}


def test_csv_creates_missing_directories(tmp_path):
    path = write_csv([make()], tmp_path / "nested" / "deeper" / "results.csv")
    assert path.exists()


def test_oracle_runs_are_excluded_from_rates():
    """An oracle was handed the answer; counting it would inflate exactly the
    number the exercise turns on."""
    results = [
        make(passed=False),
        make(agent="oracle", is_oracle=True, passed=True),
    ]

    assert len(measured(results)) == 1
    assert success_rate_by_layer(results) == {Layer.API: (0, 1)}


def test_success_rate_is_reported_per_layer():
    results = [
        make(layer=Layer.API, passed=True),
        make(layer=Layer.API, passed=True),
        make(layer=Layer.UI, passed=False),
        make(layer=Layer.UI, passed=True),
        make(layer=Layer.KERNEL, passed=False),
    ]
    assert success_rate_by_layer(results) == {
        Layer.API: (2, 2),
        Layer.UI: (1, 2),
        Layer.KERNEL: (0, 1),
    }


def test_success_rate_splits_by_difficulty_tier():
    """Where a layer stops working is more informative than whether it works."""
    results = [
        make(task_id="t01_red_circle", passed=True),
        make(task_id="t07_overlap_order", passed=False),
    ]
    by_tier = success_rate_by_tier(results)

    assert by_tier[(Layer.API, Difficulty.SIMPLE)] == (1, 1)
    assert by_tier[(Layer.API, Difficulty.OCCLUSION)] == (0, 1)


def test_summary_reports_every_layer_that_ran():
    results = [
        make(layer=Layer.API, passed=True),
        make(layer=Layer.UI, passed=False),
    ]
    summary = format_summary(results)

    assert "api" in summary and "1/1 (100%)" in summary
    assert "ui" in summary and "0/1 (0%)" in summary
    assert "kernel" not in summary


def test_summary_warns_that_turn_counts_do_not_compare_across_layers():
    summary = format_summary([make(layer=Layer.UI), make(layer=Layer.API)])
    assert "do not compare across layers" in summary


def test_summary_says_when_oracle_runs_were_dropped():
    summary = format_summary([make(), make(agent="oracle", is_oracle=True)])
    assert "1 oracle run(s) excluded" in summary


def test_summary_handles_a_run_set_with_nothing_measurable():
    assert "no measured runs" in format_summary([])
    assert "no measured runs" in format_summary([make(is_oracle=True)])
