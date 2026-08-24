#!/usr/bin/env python3
"""Command line entry point.

    python3 -m flowrunner.cli run flow.yaml prompts.yaml
    python3 -m flowrunner.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
    python3 -m flowrunner.cli validate flow.yaml prompts.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flowrunner.drivers import build as build_driver
from flowrunner.flow import FlowError, load_flow
from flowrunner.prompts import PromptsError, load_prompts
from flowrunner.runner import Runner, Status, run_directory, write_csv


def _load(flow_path: str, prompts_path: str, backend: str):
    flow = load_flow(flow_path)
    prompts = load_prompts(prompts_path)
    flow.validate_for_backend(backend)
    prompts.check_provides(flow.variables())
    return flow, prompts


def command_validate(args) -> int:
    flow, prompts = _load(args.flow, args.prompts, args.backend)
    print(f"flow      {flow.name}: {len(flow.steps)} step(s), "
          f"{len(flow.reset)} reset step(s), {len(flow.targets)} target(s)")
    print(f"variables {', '.join(sorted(flow.variables())) or 'none'}")
    print(f"prompts   {len(prompts)} variant(s): "
          f"{', '.join(v.id for v in prompts)}")
    print(f"backend   {args.backend}: ok")
    return 0


def command_run(args) -> int:
    flow, prompts = _load(args.flow, args.prompts, args.backend)
    only = args.only.split(",") if args.only else None
    prompts = prompts.select([name.strip() for name in only] if only else None)

    out_dir = Path(args.out) if args.out else run_directory(args.runs_root)
    driver = build_driver(args.backend, headless=not args.headed)

    print(f"{flow.name}: {len(prompts)} variant(s) x {args.repeat} -> {out_dir}")
    driver.start(flow.app_config(args.backend))
    try:
        runner = Runner(flow, driver, out_dir, reset_level=args.reset_level)
        results = []
        runner.prepare(prompts)
        for variant in prompts:
            for repeat in range(args.repeat):
                result = runner.run_variant(variant, repeat, args.repeat)
                runner._append(result)
                results.append(result)
                marker = "ok  " if result.status is Status.OK else result.status.value
                drift = (
                    f"  [fallbacks: {', '.join(result.used_fallbacks)}]"
                    if result.used_fallbacks else ""
                )
                print(
                    f"  {marker:8} {result.prompt_id:<20} "
                    f"{result.duration_ms:>6}ms  {len(result.response):>5} chars{drift}"
                )
    finally:
        driver.stop()

    if args.csv:
        print(f"csv -> {write_csv(results, out_dir / 'results.csv')}")
    failed = [r for r in results if r.status is not Status.OK]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {runner.results_path}")
    return 1 if failed and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flowrunner", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("run", command_run), ("validate", command_validate)):
        child = sub.add_parser(name)
        child.add_argument("flow")
        child.add_argument("prompts")
        child.add_argument("--backend", default="web", choices=["web", "native"])
        child.set_defaults(handler=handler)
        if name == "run":
            child.add_argument("--only", default=None, help="comma-separated prompt ids")
            child.add_argument("--repeat", type=int, default=1)
            child.add_argument("--out", default=None)
            child.add_argument("--runs-root", default="runs")
            child.add_argument("--headed", action="store_true")
            child.add_argument("--csv", action="store_true")
            child.add_argument("--reset-level", type=int, default=1, choices=[1, 2])
            child.add_argument("--strict", action="store_true",
                               help="exit non-zero if any variant failed")

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (FlowError, PromptsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
