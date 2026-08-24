"""Results output: a CSV of runs and the success-rate-by-layer summary.

No viewer, by design -- a CSV and the raw traces are the whole deliverable.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from harness.interaction import Layer
from harness.runner import RunResult
from harness.task import Difficulty, load_task

COLUMNS = [
    "run_id",
    "agent",
    "layer",
    "task_id",
    "passed",
    "score",
    "outcome",
    "turns_used",
    "turn_limit",
    "duration_s",
    "agent_seconds",
    "environment_seconds",
    "input_tokens",
    "output_tokens",
    "is_oracle",
    "error",
]


def measured(results: Iterable[RunResult]) -> list[RunResult]:
    """Everything except oracle runs.

    An oracle was handed the answer. Its runs prove an environment works; they
    are not evidence about any agent, and letting them into a success rate would
    inflate exactly the number the whole exercise turns on.
    """
    return [result for result in results if not result.is_oracle]


def write_csv(results: Sequence[RunResult], path: Path | str) -> Path:
    """Write every run, oracle rows included and flagged."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(result.as_row())
    return path


def success_rate_by_layer(
    results: Iterable[RunResult],
) -> dict[Layer, tuple[int, int]]:
    """The headline: passes and attempts per layer, oracles excluded."""
    tally: dict[Layer, list[int]] = defaultdict(lambda: [0, 0])
    for result in measured(results):
        tally[result.layer][0] += int(result.passed)
        tally[result.layer][1] += 1
    return {layer: (passes, attempts) for layer, (passes, attempts) in tally.items()}


def success_rate_by_tier(
    results: Iterable[RunResult],
) -> dict[tuple[Layer, Difficulty], tuple[int, int]]:
    """The same cut, split by difficulty tier.

    Where a layer stops working is more informative than whether it works: a
    layer that handles single shapes and collapses on occlusion is a different
    finding from one that fails everywhere.
    """
    tally: dict[tuple[Layer, Difficulty], list[int]] = defaultdict(lambda: [0, 0])
    for result in measured(results):
        key = (result.layer, load_task(result.task_id).difficulty)
        tally[key][0] += int(result.passed)
        tally[key][1] += 1
    return {key: (passes, attempts) for key, (passes, attempts) in tally.items()}


def _percent(passes: int, attempts: int) -> str:
    return f"{passes}/{attempts} ({100 * passes / attempts:.0f}%)" if attempts else "-"


def format_summary(results: Sequence[RunResult]) -> str:
    """A plain-text summary for the terminal."""
    kept = measured(results)
    skipped = len(results) - len(kept)
    if not kept:
        return "no measured runs" + (f" ({skipped} oracle runs excluded)" if skipped else "")

    lines = ["success rate by layer", "---------------------"]
    for layer in Layer:
        counts = success_rate_by_layer(kept).get(layer)
        if counts:
            passes, attempts = counts
            mean_turns = sum(r.turns_used for r in kept if r.layer is layer) / attempts
            lines.append(
                f"  {layer.value:7} {_percent(passes, attempts):>14}"
                f"   mean turns {mean_turns:.1f}"
            )

    by_tier = success_rate_by_tier(kept)
    if by_tier:
        lines += ["", "by difficulty tier", "------------------"]
        for layer in Layer:
            row = [
                f"{tier.name.lower()} {_percent(*by_tier[(layer, tier)])}"
                for tier in Difficulty
                if (layer, tier) in by_tier
            ]
            if row:
                lines.append(f"  {layer.value:7} " + "   ".join(row))

    # Turn counts are comparable within a layer and not across them: one UI
    # click is not one API call.
    lines += ["", "mean turns are within-layer only; they do not compare across layers"]
    if skipped:
        lines.append(f"{skipped} oracle run(s) excluded from these rates")
    return "\n".join(lines)
