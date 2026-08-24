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
from flowrunner.report import write_report
from flowrunner.resolvers import build as build_resolver, credentials_available
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
    flow, prompts = _load(args.flow, args.prompts, args.backend)
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
                if result.agent_resolutions:
                    drift += f"  [agent: {', '.join(result.agent_resolutions)}]"
                if result.learned_anchors:
                    drift += f"  [learned: {', '.join(result.learned_anchors)}]"
                print(
                    f"  {marker:8} {result.prompt_id:<20} "
                    f"{result.duration_ms:>6}ms  {len(result.response):>5} chars{drift}"
                )
    finally:
        driver.stop()

    if args.csv:
        print(f"csv -> {write_csv(results, out_dir / 'results.csv')}")
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
            child.add_argument(
                "--agent", default="off", choices=["off", "fallback", "only"],
                help="off: deterministic only. fallback: ask the model when "
                     "everything else fails. only: let the model resolve "
                     "everything (no deterministic strategies).",
            )
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
    except (FlowError, PromptsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
