#!/usr/bin/env python3
"""Render the golden reference image for each task.

Authoring tool. Run after adding or editing a task's golden recipe:

    python3 tools/generate_references.py            # write references/*.png
    python3 tools/generate_references.py --check    # verify, write nothing
    python3 tools/generate_references.py --svg-dir out/   # also dump the SVGs

The `--check` mode compares the committed reference against a freshly rendered
one using the task's own scorer settings, so a cairo version bump does not fail
the check but an edited recipe with a stale PNG does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.reference import recipe_to_svg, render_reference  # noqa: E402
from harness.scorer import PixelScorer  # noqa: E402
from harness.task import (  # noqa: E402
    REFERENCES_DIR,
    Task,
    load_all_tasks,
    load_golden_recipe,
)


def render(task: Task) -> bytes:
    return render_reference(task.canvas, load_golden_recipe(task.task_id))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed references are current; write nothing",
    )
    parser.add_argument(
        "--svg-dir",
        type=Path,
        default=None,
        help="also write the intermediate SVG documents here",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        default=None,
        help="limit to this task id (repeatable)",
    )
    args = parser.parse_args(argv)

    tasks = load_all_tasks()
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [task for task in tasks if task.task_id in wanted]
        missing = wanted - {task.task_id for task in tasks}
        if missing:
            parser.error(f"unknown task ids: {sorted(missing)}")

    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    if args.svg_dir:
        args.svg_dir.mkdir(parents=True, exist_ok=True)

    scorer = PixelScorer()
    failures = 0

    for task in tasks:
        recipe = load_golden_recipe(task.task_id)
        png = render_reference(task.canvas, recipe)

        if args.svg_dir:
            (args.svg_dir / f"{task.task_id}.svg").write_text(
                recipe_to_svg(task.canvas, recipe)
            )

        if args.check:
            if not task.reference_path.exists():
                print(f"MISSING  {task.task_id}: no reference image")
                failures += 1
                continue
            result = scorer.score(task, png)
            status = "ok" if result.passed else "STALE"
            if not result.passed:
                failures += 1
            print(f"{status:8} {task.task_id}  accuracy={result.score:.6f}")
        else:
            task.reference_path.write_bytes(png)
            try:
                shown = task.reference_path.relative_to(Path.cwd())
            except ValueError:
                shown = task.reference_path
            print(
                f"wrote    {shown} "
                f"({task.canvas.width}x{task.canvas.height}, "
                f"{len(recipe['shapes'])} shapes, {len(png)} bytes)"
            )

    if args.check:
        print(
            f"\n{len(tasks) - failures}/{len(tasks)} references current"
            + (f", {failures} need regenerating" if failures else "")
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
