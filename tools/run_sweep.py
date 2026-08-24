#!/usr/bin/env python3
"""Run a sweep and write the results table.

    python3 tools/run_sweep.py                       # every mock, every task
    python3 tools/run_sweep.py --agent oracle        # is the harness sound?
    python3 tools/run_sweep.py --task t07_overlap_order --capture-turn-images

Writes `results/results.csv` and one JSONL trace per run under
`results/traces/`. Only layers with an implementation are swept.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.agents import NoOpAgent, oracle_agent  # noqa: E402
from harness.environments import (  # noqa: E402
    ORACLE_TRANSLATORS,
    available_layers,
    build,
)
from harness.interaction import Layer  # noqa: E402
from harness.results import format_summary, write_csv  # noqa: E402
from harness.runner import Runner, RunnerConfig  # noqa: E402
from harness.scorer import PixelScorer  # noqa: E402
from harness.task import load_all_tasks, load_golden_recipe, load_task  # noqa: E402

AGENTS = ("oracle", "noop")


def build_agent(kind: str, task_id: str, layer: Layer):
    if kind == "oracle":
        translate = ORACLE_TRANSLATORS.get(layer)
        if translate is None:
            return None
        return oracle_agent(
            load_golden_recipe(task_id), translate, load_task(task_id).canvas
        )
    return NoOpAgent()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", choices=AGENTS, default=None)
    parser.add_argument("--task", action="append", default=None)
    parser.add_argument(
        "--layer", action="append", choices=[layer.value for layer in Layer], default=None
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--capture-turn-images", action="store_true")
    args = parser.parse_args(argv)

    agents = args.agent or list(AGENTS)
    tasks = [load_task(t) for t in args.task] if args.task else load_all_tasks()
    layers = (
        [Layer(value) for value in args.layer] if args.layer else available_layers()
    )

    missing = [layer for layer in layers if layer not in dict.fromkeys(available_layers())]
    if missing:
        parser.error(
            f"no environment for: {', '.join(layer.value for layer in missing)}. "
            f"Available: {', '.join(layer.value for layer in available_layers())}"
        )

    runner = Runner(
        PixelScorer(),
        RunnerConfig(capture_turn_images=args.capture_turn_images),
        trace_dir=args.out / "traces",
    )

    results = []
    for layer in layers:
        for task in tasks:
            for kind in agents:
                for repeat in range(args.repeats):
                    agent = build_agent(kind, task.task_id, layer)
                    if agent is None:
                        print(f"skipping {kind} at the {layer.value} layer: no translator")
                        continue
                    result = runner.run(task, build(layer), agent, repeat=repeat)
                    results.append(result)
                    print(
                        f"{'PASS' if result.passed else 'FAIL'} "
                        f"{result.run_id:44} {result.score:.5f} "
                        f"{result.outcome.value} ({result.turns_used} turns)"
                    )

    if not results:
        print("nothing ran")
        return 1

    csv_path = write_csv(results, args.out / "results.csv")
    print(f"\n{format_summary(results)}\n\n{len(results)} runs -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
