"""The environment registry and the sweep CLI."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from harness.environments import (
    ENVIRONMENTS,
    ORACLE_TRANSLATORS,
    APIEnvironment,
    available_layers,
    build,
)
from harness.interaction import Layer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import run_sweep  # noqa: E402


class TestRegistry:
    def test_the_api_layer_is_available(self):
        assert Layer.API in available_layers()
        assert isinstance(build(Layer.API), APIEnvironment)

    def test_layers_without_an_implementation_say_so(self):
        # Placeholder-free: the registry reports what exists rather than
        # handing back something that silently does nothing.
        for layer in Layer:
            if layer not in ENVIRONMENTS:
                with pytest.raises(KeyError, match="no environment"):
                    build(layer)

    def test_build_returns_a_fresh_environment_each_time(self):
        assert build(Layer.API) is not build(Layer.API)

    def test_every_implemented_layer_has_an_oracle_translator(self):
        """Without one there is no way to show the layer can pass at all, and a
        real agent's failure there could not be attributed."""
        assert set(ENVIRONMENTS) <= set(ORACLE_TRANSLATORS)


class TestSweepCli:
    def test_a_sweep_writes_results_and_traces(self, tmp_path):
        exit_code = run_sweep.main(
            ["--task", "t01_red_circle", "--task", "t09_ring_punchout", "--out", str(tmp_path)]
        )
        assert exit_code == 0

        with (tmp_path / "results.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 4, "two tasks x two agents"

        traces = list((tmp_path / "traces").glob("*.jsonl"))
        assert len(traces) == 4
        assert all((tmp_path / "traces" / row["run_id"] / "final.png").exists() for row in rows)

    def test_the_oracle_passes_and_the_do_nothing_agent_does_not(self, tmp_path):
        run_sweep.main(["--task", "t01_red_circle", "--out", str(tmp_path)])
        with (tmp_path / "results.csv").open() as handle:
            rows = {row["agent"]: row for row in csv.DictReader(handle)}

        assert rows["oracle"]["passed"] == "True"
        assert rows["oracle"]["is_oracle"] == "True"
        assert rows["noop"]["passed"] == "False"

    def test_repeats_produce_distinct_runs(self, tmp_path):
        run_sweep.main(
            ["--task", "t01_red_circle", "--agent", "noop", "--repeats", "3", "--out", str(tmp_path)]
        )
        with (tmp_path / "results.csv").open() as handle:
            run_ids = [row["run_id"] for row in csv.DictReader(handle)]
        assert len(set(run_ids)) == 3

    def test_asking_for_an_unbuilt_layer_is_a_clear_error(self, tmp_path):
        unbuilt = next((l for l in Layer if l not in ENVIRONMENTS), None)
        if unbuilt is None:
            pytest.skip("every layer is implemented")
        with pytest.raises(SystemExit):
            run_sweep.main(["--layer", unbuilt.value, "--out", str(tmp_path)])

    def test_turn_images_are_captured_on_request(self, tmp_path):
        run_sweep.main(
            [
                "--task", "t07_overlap_order",
                "--agent", "oracle",
                "--capture-turn-images",
                "--out", str(tmp_path),
            ]
        )
        images = list((tmp_path / "traces").rglob("turn_*.png"))
        assert len(images) == 2
