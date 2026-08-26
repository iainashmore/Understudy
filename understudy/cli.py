#!/usr/bin/env python3
"""Command line entry point.

    python3 -m understudy.cli run flow.yaml prompts.yaml
    python3 -m understudy.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
    python3 -m understudy.cli validate flow.yaml prompts.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from understudy.drivers import build as build_driver
from understudy.flow import FlowError, load_flow
from understudy.authoring import AuthoringError, duplicate_file
from understudy.narrate import ClaudeNarrator, narrate_run
from understudy.prompts import PromptsError, prompts_for
from understudy.pdf import write_pdf
from understudy.subject import Subject, remember, resolve_subject
from understudy.vcs.backend import Repository
from understudy.vcs.git import GitError
from understudy.transcript import write_transcript, write_suite_index
from understudy.transcript_html import write_html
from understudy.resolvers import build as build_resolver, credentials_available
from understudy.runner import Runner, Status, run_directory, write_csv
from understudy.suite import SuiteError, load_suite


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
    print(f"prompts   {len(prompts)} prompt run(s): "
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
    _write_transcripts(args.run_dir, False, False)
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
                f"{summary['variants']:>3} prompts   "
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
            agent="off", learned_dir=None, no_transcript=False, embed_transcript=False,
            pdf=False, app="", app_version="", model_under_test="",
            model_version="", release="",
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
    from understudy.ui import serve

    server = serve(args.workspace, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"Understudy UI on {url}  (workspace: {Path(args.workspace).resolve()})")
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


def _write_transcripts(run_dir, embed: bool, pdf: bool) -> int:
    """Markdown and the viewable page always; PDF on request, because it costs
    a browser launch."""
    print(f"transcript -> {write_transcript(run_dir, embed=embed)}")
    print(f"transcript -> {write_html(run_dir, embed=embed)}")
    if not pdf:
        return 0
    outcome = write_pdf(run_dir)
    if not outcome.ok:
        print(f"pdf skipped: {outcome.error}", file=sys.stderr)
        return 1
    print(f"transcript -> {outcome.path}")
    return 0


def command_compare(args) -> int:
    from understudy.compare import compare as compare_runs
    from understudy.compare_report import write_comparison

    comparison = compare_runs(args.run_dirs)
    if comparison.mixed_flows:
        print(f"warning: these runs are of different flows "
              f"({', '.join(comparison.flows)})", file=sys.stderr)

    for column in comparison.columns:
        print(f"  {column.label:<26} {column.heading}")
    print(f"\n{comparison.headline()}\n")

    rows = comparison.changed if args.changed_only else comparison.rows
    for row in rows:
        mark = {"asked": "?!", "same": "  ", "reworded": "~ ", "changed": "! ",
                "missing": "? ", "failed": "x "}[row.verdict]
        note = ("the question changed — these answers are to different "
                "questions" if row.verdict == "asked" else row.verdict)
        print(f"{mark}{row.prompt_id:<22} {note}")
        if row.verdict == "asked":
            for column, asked in zip(comparison.columns, row.prompts):
                text = "(no run)" if asked is None else " ".join(asked.split())
                print(f"      {column.heading[:34]:<34} asked {text[:64]}")
        elif row.verdict in ("changed", "missing"):
            for column, response in zip(comparison.columns, row.responses):
                text = "(no run)" if response is None else response.strip()
                print(f"      {column.heading[:34]:<34} {text[:70]}")

    if args.out:
        paths = write_comparison(comparison, args.out)
        for path in paths:
            print(f"\ncomparison -> {path}")
    return 1 if (comparison.changed and args.changed_only) else 0


def command_repo(args) -> int:
    """What the checkout looks like, in the terms the UI shows."""
    state = Repository(args.workspace).state()
    if not state["is_repo"]:
        print(state["note"] or "not a git checkout")
        return 1

    remote = state["remote"]
    print(f"branch    {state['branch']}"
          + (f"  (ahead {state['ahead']}, behind {state['behind']})"
             if state["upstream"] else "  (no upstream)"))
    print(f"remote    {remote['url'] or '(none)'}")
    if remote["provider"] != "unknown":
        print(f"provider  {remote['provider']}"
              + ("  token saved" if state["has_token"] else "  no token saved"))
    if not state["changes"]:
        print("clean")
        return 0
    print(f"\n{len(state['changes'])} change(s):")
    for change in state["changes"]:
        print(f"  {change['state']:<10} {change['path']}")
    return 0


def command_publish(args) -> int:
    """Commit one run's evidence to the repository."""
    repository = Repository(args.workspace)
    run_dir = _relative_to(args.run_dir, repository.root)

    preview = repository.preview_publish(
        run_dir, include_video=args.include_video)
    print(f"{preview['summary']}  ({preview['total_bytes'] / 1024:.0f} KB)")
    for reason, paths in sorted(preview["excluded"].items()):
        for path in paths:
            print(f"  skipped ({reason}): {path}")
    if args.dry_run:
        for path in preview["include"]:
            print(f"  would commit: {path}")
        return 0

    try:
        outcome = repository.publish(
            run_dir, message=args.message or "",
            include_video=args.include_video, push=not args.no_push,
        )
    except GitError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1

    if not outcome.get("committed"):
        print(outcome.get("reason", "nothing to commit"))
        return 0
    print(f"committed {outcome['sha'][:8]}"
          + (f"  {outcome['url']}" if outcome.get("url") else ""))
    if outcome.get("pushed") is False:
        print(f"push failed: {outcome.get('push_error')}", file=sys.stderr)
        return 1
    if outcome.get("pushed"):
        print("pushed")
    return 0


def _relative_to(run_dir: str, root: Path) -> str:
    resolved = Path(run_dir).resolve()
    try:
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        raise SystemExit(
            f"{run_dir} is not inside the workspace {root}. Pass --workspace."
        ) from None


def command_transcript(args) -> int:
    return _write_transcripts(args.run_dir, args.embed_transcript, args.pdf)


def _subject_for(args, flow) -> Subject:
    """What was under test, from the flags, what was recorded last time, and
    the flow -- in that order of authority."""
    given = Subject.from_config({
        "app": args.app, "app_version": args.app_version,
        "model": args.model_under_test, "model_version": args.model_version,
        "release": args.release,
    })
    # Not retyped every morning: the last thing recorded for this flow stands
    # in until somebody says otherwise.
    subject = resolve_subject(flow.name, flow.subject, given)
    if given.recorded:
        remember(flow.name, flow.subject.merged_with(subject))
    return subject


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

    print(f"{flow.name}: {len(prompts)} prompt run(s) x {args.repeat} -> {out_dir}")
    driver.start(flow.app_config(args.backend))
    for note in getattr(driver, "warnings", []):
        print(f"note: {note}")
    try:
        runner = Runner(flow, driver, out_dir, reset_level=args.reset_level,
                        capture_steps=args.narrate or args.capture_steps,
                        record=args.record, subject=_subject_for(args, flow))
        if runner.subject.recorded:
            print(f"under test: {runner.subject.summary()}")
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
    if not args.no_transcript:
        _write_transcripts(out_dir, args.embed_transcript, args.pdf)
    failed = [r for r in results if r.status is not Status.OK]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {runner.results_path}")
    return 1 if failed and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="understudy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ui = sub.add_parser("ui", help="open the local authoring and replay UI")
    ui.add_argument("--workspace", default=".", help="folder the UI may read and write")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--no-open", action="store_true")
    ui.set_defaults(handler=command_ui)

    transcript = sub.add_parser("transcript", help="rebuild the transcript for a past run")
    transcript.add_argument("run_dir")
    transcript.add_argument("--embed-transcript", action="store_true")
    transcript.add_argument("--pdf", action="store_true",
                            help="also print the transcript to PDF")
    transcript.set_defaults(handler=command_transcript)

    compare = sub.add_parser(
        "compare", help="line up the same prompts across two or more runs")
    compare.add_argument("run_dirs", nargs="+")
    compare.add_argument("--out", default="",
                         help="write the comparison here (markdown and html)")
    compare.add_argument("--changed-only", action="store_true",
                         help="only the prompts whose answer moved")
    compare.set_defaults(handler=command_compare)

    repo = sub.add_parser("repo", help="what the workspace checkout looks like")
    repo.add_argument("--workspace", default=".")
    repo.set_defaults(handler=command_repo)

    publish = sub.add_parser(
        "publish", help="commit a run's transcript and screenshots to the repository")
    publish.add_argument("run_dir")
    publish.add_argument("--workspace", default=".")
    publish.add_argument("-m", "--message", default="",
                         help="commit subject; one is written for you if omitted")
    publish.add_argument("--include-video", action="store_true",
                         help="commit the recordings too (they are large and "
                              "permanent; consider Git LFS first)")
    publish.add_argument("--no-push", action="store_true",
                         help="commit but do not push")
    publish.add_argument("--dry-run", action="store_true",
                         help="list what would be committed and stop")
    publish.set_defaults(handler=command_publish)

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
            child.add_argument(
                "--only", default=None, metavar="IDS",
                help="run only these prompts, comma separated (default: all)")
            child.add_argument("--repeat", type=int, default=1)
            child.add_argument("--out", default=None)
            child.add_argument("--runs-root", default="runs")
            child.add_argument("--headed", action="store_true")
            child.add_argument("--csv", action="store_true")
            child.add_argument("--reset-level", type=int, default=1, choices=[1, 2])
            child.add_argument("--strict", action="store_true",
                               help="exit non-zero if any prompt run failed")
            child.add_argument(
                "--agent", default="off", choices=["off", "fallback", "only"],
                help="off: deterministic only. fallback: ask the model when "
                     "everything else fails. only: let the model resolve "
                     "everything (no deterministic strategies).",
            )
            child.add_argument(
                "--narrate", action="store_true",
                help="capture every step and have a model describe each one in "
                     "the transcript; cached per flow, so it costs once",
            )
            child.add_argument(
                "--record", action="store_true",
                help="record each prompt run to video: ffmpeg on native, "
                     "Playwright on web (which implies a fresh browser "
                     "context per run)",
            )
            child.add_argument("--capture-steps", action="store_true",
                               help="screenshot after every step, without narrating")
            child.add_argument("--no-transcript", action="store_true",
                               help="skip the markdown transcript")
            # What was under test. Remembered between runs, so a service pack
            # is typed once and every later run of that flow carries it.
            child.add_argument("--app", default="",
                               help="the application under test, e.g. 'CATIA V5'")
            child.add_argument("--app-version", default="",
                               help="its version, e.g. 'R32 SP4'")
            child.add_argument("--model-under-test", "--assistant", default="",
                               dest="model_under_test",
                               help="the assistant being exercised, e.g. 'LEO'")
            child.add_argument("--model-version", default="",
                               help="the assistant's version, e.g. '2026x FD01'")
            child.add_argument("--release", default="",
                               help="release or build number")
            child.add_argument("--pdf", action="store_true",
                               help="also print the transcript to PDF")
            child.add_argument("--embed-transcript", action="store_true",
                               help="inline screenshots as data URIs, so the "
                                    "transcript travels as a single file")
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
