#!/usr/bin/env python3
"""Command line entry point.

    python3 -m flowrunner.cli run flow.yaml prompts.yaml
    python3 -m flowrunner.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
    python3 -m flowrunner.cli validate flow.yaml prompts.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flowrunner.drivers import build as build_driver
from flowrunner.flow import FlowError, load_flow
from flowrunner.authoring import AuthoringError, duplicate_file
from flowrunner.narrate import ClaudeNarrator, narrate_run
from flowrunner.prompts import PromptsError, prompts_for
from flowrunner.report import write_report, write_suite_index
from flowrunner.resolvers import build as build_resolver, credentials_available
from flowrunner.runner import Runner, Status, run_directory, write_csv
from flowrunner.suite import SuiteError, load_suite


def _load(flow_path: str, backend: str):
    flow = load_flow(flow_path)
    prompts = prompts_for(flow)
    flow.validate_for_backend(backend)
    prompts.check_provides(flow.variables())
    return flow, prompts


def command_validate(args) -> int:
    flow, prompts = _load(args.flow, args.backend)
    print(f"flow      {flow.title} ({flow.name}): {len(flow.steps)} step(s), "
          f"{len(flow.reset)} reset step(s), {len(flow.targets)} target(s)")
    print(f"variables {', '.join(sorted(flow.variables())) or 'none'}")
    print(f"prompts   {len(prompts)} variant(s): "
          f"{', '.join(v.id for v in prompts)}")
    print(f"backend   {args.backend}: ok")
    return 0


def command_duplicate(args) -> int:
    path = duplicate_file(
        args.flow, args.destination, name=args.name, title=args.title,
        description=args.description, overwrite=args.force,
    )
    print(f"copied {args.flow} -> {path}")
    return 0


def command_narrate(args) -> int:
    narration = narrate_run(
        args.run_dir, ClaudeNarrator(), cache_path=args.cache, force=args.force
    )
    for key, description in narration.items():
        print(f"  {key:<28} {description}")
    print(f"\n{len(narration)} step(s) -> {Path(args.run_dir) / 'narration.json'}")
    print(f"report -> {write_report(args.run_dir)}")
    return 0


def command_suite(args) -> int:
    suite = load_suite(args.path).select(args.flows, args.tag)

    if not args.run:
        print(f"{suite.name}: {len(suite)} flow(s)")
        if suite.description:
            print(f"  {suite.description}\n")
        for entry in suite:
            summary = entry.summary()
            marks = []
            if summary["skip"]:
                marks.append("SKIPPED")
            if summary["error"]:
                marks.append(f"BROKEN: {summary['error'][:60]}")
            print(
                f"  {summary['title'][:28]:<28} {summary['steps']:>3} steps  "
                f"{summary['variants']:>3} variants  "
                f"{'[' + ','.join(summary['tags']) + ']' if summary['tags'] else '':<20} "
                f"{summary['description'][:44]} {' '.join(marks)}"
            )
        problems = suite.problems()
        if problems:
            print(f"\n{len(problems)} flow(s) could not be loaded:")
            for problem in problems:
                print(f"  {problem}")
        return 1 if problems else 0

    out_dir = Path(args.out) if args.out else run_directory(args.runs_root)
    print(f"{suite.name}: running {len(suite.runnable)} flow(s) -> {out_dir}")
    index: list[dict] = []

    for entry in suite:
        if entry.skip or entry.error:
            index.append({"name": entry.name, "description": entry.description,
                          "tags": list(entry.tags),
                          "error": entry.error or "skipped", "dir": ""})
            print(f"  {entry.name}: {'skipped' if entry.skip else entry.error}")
            continue

        child = argparse.Namespace(
            flow=str(entry.flow_path),
            backend=args.backend, only=",".join(entry.only) if entry.only else None,
            repeat=1, out=str(out_dir / entry.slug), runs_root=args.runs_root,
            headed=args.headed, csv=True, reset_level=1, strict=False,
            agent="off", learned_dir=None, no_report=False, embed_report=False,
            narrate=False, capture_steps=False, record=False,
        )
        print(f"\n--- {entry.name}")
        try:
            command_run(child)
            results = [
                json.loads(line) for line in
                (out_dir / entry.slug / "results.jsonl").read_text().splitlines() if line.strip()
            ]
            index.append({
                "name": entry.name, "description": entry.description,
                "tags": list(entry.tags), "dir": entry.slug,
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "total": len(results),
            })
        except Exception as exc:
            # One broken flow must not end the suite.
            index.append({"name": entry.name, "description": entry.description,
                          "tags": list(entry.tags), "dir": entry.slug,
                          "error": f"{type(exc).__name__}: {exc}"})
            print(f"  failed: {exc}", file=sys.stderr)

    print(f"\nindex -> {write_suite_index(out_dir, index)}")
    return 0


def command_ui(args) -> int:
    from flowrunner.ui import serve

    server = serve(args.workspace, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"FlowRunner UI on {url}  (workspace: {Path(args.workspace).resolve()})")
    print("Ctrl-C to stop.")
    if not args.no_open:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
    return 0


def command_report(args) -> int:
    path = write_report(args.run_dir, embed=args.embed_report)
    print(f"report -> {path}")
    return 0


def command_run(args) -> int:
    flow, prompts = _load(args.flow, args.backend)
    only = args.only.split(",") if args.only else None
    prompts = prompts.select([name.strip() for name in only] if only else None)

    out_dir = Path(args.out) if args.out else run_directory(args.runs_root)

    if args.agent != "off" and not credentials_available():
        print(
            "warning: --agent is on but no Anthropic credentials were found; "
            "agent resolution will fail. Run `ant auth login` or set "
            "ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
    driver = build_driver(
        args.backend,
        headless=not args.headed,
        resolver=build_resolver("claude" if args.agent != "off" else "off"),
        agent_mode=args.agent,
        learned_dir=args.learned_dir or str(Path(args.flow).parent / "learned"),
    )

    print(f"{flow.name}: {len(prompts)} variant(s) x {args.repeat} -> {out_dir}")
    driver.start(flow.app_config(args.backend))
    try:
        runner = Runner(flow, driver, out_dir, reset_level=args.reset_level,
                        capture_steps=args.narrate or args.capture_steps,
                        record=args.record)
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
                if result.agent_resolutions:
                    drift += f"  [agent: {', '.join(result.agent_resolutions)}]"
                if result.learned_anchors:
                    drift += f"  [learned: {', '.join(result.learned_anchors)}]"
                if result.recording:
                    drift += f"  [video: {result.recording}]"
                elif result.recording_error and args.record:
                    drift += f"  [no video: {result.recording_error[:50]}]"
                print(
                    f"  {marker:8} {result.prompt_id:<20} "
                    f"{result.duration_ms:>6}ms  {len(result.response):>5} chars{drift}"
                )
    finally:
        driver.stop()

    if args.csv:
        print(f"csv -> {write_csv(results, out_dir / 'results.csv')}")
    if args.narrate:
        try:
            cache = Path(args.flow).parent / "narration" / f"{Path(args.flow).stem}.json"
            narration = narrate_run(out_dir, ClaudeNarrator(), cache_path=cache)
            print(f"narrated {len(narration)} step(s) (cached in {cache})")
        except Exception as exc:
            print(f"narration skipped: {exc}", file=sys.stderr)
    if not args.no_report:
        print(f"report -> {write_report(out_dir, embed=args.embed_report)}")
    failed = [r for r in results if r.status is not Status.OK]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {runner.results_path}")
    return 1 if failed and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flowrunner", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ui = sub.add_parser("ui", help="open the local authoring and replay UI")
    ui.add_argument("--workspace", default=".", help="folder the UI may read and write")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--no-open", action="store_true")
    ui.set_defaults(handler=command_ui)

    report = sub.add_parser("report", help="rebuild the report for a past run")
    report.add_argument("run_dir")
    report.add_argument("--embed-report", action="store_true")
    report.set_defaults(handler=command_report)

    copy = sub.add_parser("duplicate", help="copy a flow under a new identity")
    copy.add_argument("flow")
    copy.add_argument("destination")
    copy.add_argument("--name", default=None, help="defaults to the new filename")
    copy.add_argument("--title", default=None)
    copy.add_argument("--description", default=None)
    copy.add_argument("--force", action="store_true")
    copy.set_defaults(handler=command_duplicate)

    narrate = sub.add_parser("narrate", help="describe a past run's steps")
    narrate.add_argument("run_dir")
    narrate.add_argument("--cache", default=None)
    narrate.add_argument("--force", action="store_true")
    narrate.set_defaults(handler=command_narrate)

    suite = sub.add_parser("suite", help="list or run a collection of flows")
    suite.add_argument("path")
    suite.add_argument("--run", action="store_true", help="run every flow in the suite")
    suite.add_argument("--tag", action="append", default=None)
    suite.add_argument("--flow", action="append", default=None, dest="flows")
    suite.add_argument("--backend", default="web", choices=["web", "native"])
    suite.add_argument("--out", default=None)
    suite.add_argument("--runs-root", default="runs")
    suite.add_argument("--headed", action="store_true")
    suite.set_defaults(handler=command_suite)

    for name, handler in (("run", command_run), ("validate", command_validate)):
        child = sub.add_parser(name)
        child.add_argument("flow")
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
            child.add_argument(
                "--agent", default="off", choices=["off", "fallback", "only"],
                help="off: deterministic only. fallback: ask the model when "
                     "everything else fails. only: let the model resolve "
                     "everything (no deterministic strategies).",
            )
            child.add_argument(
                "--narrate", action="store_true",
                help="capture every step and have a model describe each one in "
                     "the report; cached per flow, so it costs once",
            )
            child.add_argument(
                "--record", action="store_true",
                help="record each variant to video: ffmpeg on native, "
                     "Playwright on web (which implies a fresh browser context "
                     "per variant)",
            )
            child.add_argument("--capture-steps", action="store_true",
                               help="screenshot after every step, without narrating")
            child.add_argument("--no-report", action="store_true",
                               help="skip the markdown report")
            child.add_argument("--embed-report", action="store_true",
                               help="inline screenshots as data URIs, so the "
                                    "report travels as a single file")
            child.add_argument(
                "--learned-dir", default=None,
                help="where anchors the agent finds are cached "
                     "(default: <flow dir>/learned)",
            )

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (FlowError, PromptsError, SuiteError, AuthoringError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
