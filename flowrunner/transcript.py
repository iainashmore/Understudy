"""Markdown transcript of a run.

results.jsonl is what a machine reads. This is what a person reads: prompts and
responses side by side, the screenshots that prove the flow did what it was
meant to, and enough diagnostics to work out why a variant failed.

Written into the run directory alongside the screenshots it links, with
relative paths, so the whole folder can be zipped, committed or attached and
still renders. `embed=True` inlines the images as data URIs instead, for when
the transcript has to travel as a single file.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flowrunner.narrate import load_narration, steps_of

#: Screenshots are full application windows; at full width a transcript is
#: unreadable. HTML img tags because markdown has no width syntax.
THUMBNAIL_WIDTH = 460
RESPONSE_IMAGE_WIDTH = 320
STATUS_MARK = {"ok": "pass", "timeout": "TIMEOUT", "error": "FAIL", "skipped": "skipped"}


@dataclass(frozen=True)
class RunSummary:
    flow_name: str
    backend: str
    variants: int
    passed: int
    failed: int
    timed_out: int


def load_results(run_dir: Path | str) -> list[dict[str, Any]]:
    path = Path(run_dir) / "results.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no results.jsonl in {run_dir}")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _flow_heading(run_dir: Path) -> tuple[str, str]:
    """Title for the heading, description for the line under it."""
    flow = run_dir / "flow.yaml"
    fields: dict[str, str] = {}
    if flow.exists():
        for line in flow.read_text(encoding="utf-8").splitlines():
            for key in ("name", "title", "description"):
                if line.startswith(f"{key}:") and key not in fields:
                    fields[key] = line.split(":", 1)[1].strip().strip('"')
    return (
        fields.get("title") or fields.get("name") or run_dir.name,
        fields.get("description", ""),
    )


def _flow_name(run_dir: Path) -> str:
    return _flow_heading(run_dir)[0]


def summarise(run_dir: Path, results: list[dict[str, Any]]) -> RunSummary:
    statuses = [r.get("status") for r in results]
    return RunSummary(
        flow_name=_flow_name(run_dir),
        backend=results[0].get("backend", "?") if results else "?",
        variants=len(results),
        passed=statuses.count("ok"),
        failed=statuses.count("error"),
        timed_out=statuses.count("timeout"),
    )


def _escape(text: str) -> str:
    """Keep response text from being read as markdown."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _fence(text: str) -> list[str]:
    """A fence longer than anything inside the text, so a response containing
    backticks cannot break out of its own block."""
    longest = 0
    run = 0
    for character in text:
        run = run + 1 if character == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return [f"{ticks}text", text, ticks]


def _image(run_dir: Path, relative: str, width: int, embed: bool) -> str:
    path = run_dir / relative
    if not path.exists():
        return f"_(missing: {relative})_"
    if embed:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        source = f"data:image/png;base64,{data}"
    else:
        source = relative
    return f'<img src="{source}" width="{width}" alt="{relative}">'


def _label_of(relative: str) -> str:
    stem = Path(relative).stem
    number, _, rest = stem.partition("-")
    return (rest or stem).replace("-", " ") if number.isdigit() else stem.replace("-", " ")


def _walkthrough(results: list[dict[str, Any]], narration: dict[str, str]) -> list[str]:
    """The click path in plain language, once.

    It belongs at the top and only once because every variant walks the same
    path -- that is the premise of the whole tool.
    """
    if not narration:
        return []
    sample = next((r for r in results if r.get("status") == "ok"), results[0] if results else None)
    if sample is None:
        return []

    lines = ["", "## What the flow does", ""]
    number = 0
    for ref in steps_of(sample):
        description = narration.get(ref.key)
        if not description:
            continue
        number += 1
        prefix = "_(reset)_ " if ref.phase == "reset" else ""
        lines.append(f"{number}. {prefix}{description}")
    return lines + ["", "_Descriptions written from the run's own screenshots._"] if number else []


def _steps_table(result: dict[str, Any], narration: dict[str, str]) -> list[str]:
    lines = [
        "| # | action | target | what it does | status | ms | via | note |",
        "|---|--------|--------|--------------|--------|----|-----|------|",
    ]
    described = {ref.key: narration.get(ref.key, "") for ref in steps_of(result)}
    for step in result.get("step_statuses", []):
        resolution = step.get("resolution") or {}
        detail = step.get("detail") or {}
        note = step.get("error") or detail.get("signal") or resolution.get("note") or ""
        if "waited_ms" in detail:
            note = f"waited {detail['waited_ms']}ms, {detail.get('samples', '?')} samples"
        # Reset and main steps both number from 1; without the phase the table
        # looks like it repeats itself.
        phase = step.get("phase", "steps")
        number = f"reset {step.get('index', '')}" if phase == "reset" else step.get("index", "")
        key = f"{phase}:{step.get('index')}:{step.get('action')}"
        lines.append(
            f"| {number} | {step.get('action', '')} "
            f"| {step.get('target') or ''} | {_escape(described.get(key, ''))} "
            f"| {STATUS_MARK.get(step.get('status'), step.get('status'))} "
            f"| {step.get('duration_ms', '')} | {resolution.get('via') or ''} "
            f"| {_escape(str(note))[:80]} |"
        )
    return lines


def render_markdown(
    run_dir: Path | str, results: list[dict[str, Any]] | None = None, embed: bool = False
) -> str:
    run_dir = Path(run_dir)
    results = load_results(run_dir) if results is None else results
    summary = summarise(run_dir, results)
    narration = load_narration(run_dir)

    title, description = _flow_heading(run_dir)
    out: list[str] = [
        f"# {title}",
        "",
    ] + ([f"{description}", ""] if description else []) + [
        f"- **Run** `{run_dir.name}`",
        f"- **Backend** {summary.backend}",
        f"- **Variants** {summary.variants} — "
        f"{summary.passed} ok, {summary.failed} failed, {summary.timed_out} timed out",
    ]
    if results:
        out.append(f"- **Started** {results[0].get('timestamp', '?')}")
    for name in ("flow.yaml", "prompts.yaml", "prompts.csv", "results.jsonl", "results.csv"):
        if (run_dir / name).exists():
            out.append(f"- [`{name}`]({name})")
    out += ["", "## Summary", "",
            "| variant | status | duration | response | notes |",
            "|---------|--------|----------|----------|-------|"]

    for result in results:
        notes = []
        if result.get("used_fallbacks"):
            notes.append(f"fallbacks: {', '.join(result['used_fallbacks'])}")
        if result.get("agent_resolutions"):
            notes.append(f"agent: {', '.join(result['agent_resolutions'])}")
        if result.get("learned_anchors"):
            notes.append(f"learned: {', '.join(result['learned_anchors'])}")
        if result.get("error"):
            notes.append(_escape(result["error"])[:70])
        response = result.get("response") or ""
        out.append(
            f"| [{result['prompt_id']}](#{_slug(result)}) "
            f"| {STATUS_MARK.get(result.get('status'), result.get('status'))} "
            f"| {result.get('duration_ms', 0)} ms "
            f"| {len(response)} chars | {_escape('; '.join(notes))[:90]} |"
        )

    out += _walkthrough(results, narration)

    # Prompts against responses, one table, because comparing them is the whole
    # reason the run happened.
    out += ["", "## Prompts and responses", ""]
    for result in results:
        out += [
            f"**{result['prompt_id']}** — {_escape(result.get('prompt', ''))}", "",
            f"> {_escape(result.get('response', '')) or '_(no text captured)_'}", "",
        ]

    for result in results:
        out += _variant_section(run_dir, result, embed, narration)

    out += ["", "---", "",
            "_Generated by flowrunner. Screenshots are the run's own captures; "
            "response text is whatever the flow's `read` step extracted._"]
    return "\n".join(out) + "\n"


def _slug(result: dict[str, Any]) -> str:
    suffix = f"-{result['repeat_index'] + 1}" if result.get("repeat_index") else ""
    return f"{result['prompt_id']}{suffix}".lower().replace(" ", "-").replace(".", "")


def _variant_section(run_dir: Path, result: dict[str, Any], embed: bool,
                     narration: dict[str, str] | None = None) -> list[str]:
    title = result["prompt_id"]
    if result.get("repeat_index"):
        title += f" (repeat {result['repeat_index'] + 1})"

    out = ["", f"## {title}", "",
           f"**Status** {STATUS_MARK.get(result.get('status'), result.get('status'))} "
           f"· {result.get('duration_ms', 0)} ms"]
    if result.get("error"):
        out += ["", f"> **Error** {_escape(result['error'])}"]

    variables = {k: v for k, v in (result.get("variables") or {}).items()}
    out += ["", "### Prompt", ""] + _fence((variables.pop("prompt", "") or "").strip())
    if variables:
        out += ["", "Other variables:", ""]
        out += [f"- `{k}` = {v}" for k, v in sorted(variables.items())]

    out += ["", "### Response", ""]
    response = (result.get("response") or "").strip()
    out += _fence(response) if response else [
        "_No text captured._ "
        "The response was recorded as pixels; the region image is below."
    ]

    for name, relative in (result.get("read_images") or {}).items():
        out += ["", f"Recorded `{name}` region:", "",
                _image(run_dir, relative, RESPONSE_IMAGE_WIDTH, embed)]

    if result.get("recording"):
        out += ["", "### Recording", "",
                f"[{result['recording']}]({result['recording']})",
                "",
                "<video controls width=\"460\" src=\"" + result["recording"] +
                "\"></video>",
                "",
                "_Video players differ; if it does not play inline, the link "
                "above opens the file._"]
    elif result.get("recording_error"):
        out += ["", f"_No recording: {_escape(result['recording_error'])}_"]

    screenshots = [
        shot for shot in result.get("screenshots", [])
        if shot not in (result.get("read_images") or {}).values()
    ]
    if screenshots:
        out += ["", "### Screenshots", ""]
        for relative in screenshots:
            out += [f"**{_label_of(relative)}**", "",
                    _image(run_dir, relative, THUMBNAIL_WIDTH, embed), ""]

    out += ["", "<details><summary>Step detail</summary>", ""]
    out += _steps_table(result, narration or {})
    out += ["", "</details>"]
    return out


def write_transcript(
    run_dir: Path | str, embed: bool = False, filename: str = "transcript.md"
) -> Path:
    run_dir = Path(run_dir)
    path = run_dir / filename
    path.write_text(render_markdown(run_dir, embed=embed), encoding="utf-8")
    return path


def write_suite_index(out_dir: Path | str, runs: list[dict[str, Any]]) -> Path:
    """One page linking every flow's transcript, so a suite run has a front door.

    `runs` entries: name, description, tags, dir (relative), ok, total, error.
    """
    out_dir = Path(out_dir)
    lines = [
        f"# Suite run — {out_dir.name}", "",
        f"{len(runs)} flow(s).", "",
        "| flow | result | description | transcript |",
        "|------|--------|-------------|--------|",
    ]
    for run in runs:
        if run.get("error"):
            result = f"FAILED — {_escape(run['error'])[:60]}"
            link = ""
        else:
            passed, total = run.get("ok", 0), run.get("total", 0)
            result = f"{passed}/{total} ok" if passed == total else f"**{passed}/{total} ok**"
            link = f"[transcript]({run['dir']}/transcript.md)"
        lines.append(
            f"| {run['name']} | {result} | {_escape(run.get('description', ''))} | {link} |"
        )

    for run in runs:
        if run.get("tags"):
            lines += ["", f"_{run['name']} tags: {', '.join(run['tags'])}_"]

    path = out_dir / "index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
