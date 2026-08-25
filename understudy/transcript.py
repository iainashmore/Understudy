"""Markdown transcript of a run.

results.jsonl is what a machine reads. This is what a person reads: prompts and
responses side by side, the screenshots that prove the flow did what it was
meant to, and enough diagnostics to work out why a prompt run failed.

Written into the run directory alongside the screenshots it links, with
relative paths, so the whole folder can be zipped, committed or attached and
still renders. `embed=True` inlines the images as data URIs instead, for when
the transcript has to travel as a single file.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from understudy.narrate import load_narration, steps_of
from understudy.subject import LABELS as SUBJECT_LABELS, Subject

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


def subject_of(results: list[dict[str, Any]]) -> Subject:
    """What was under test, from the first result. Every variant in a run saw
    the same installation, so the first one speaks for all of them."""
    return Subject.from_config((results[0].get("subject") if results else None) or {})


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


#: Steps a person would perform. The rest -- capturing, waiting, reading -- are
#: things the tool does around them, and numbering those alongside makes "step
#: 4" mean two different things depending on who is counting.
USER_ACTIONS = frozenset({
    "click", "double_click", "right_click", "type", "key", "hover", "scroll", "drag",
})


def step_key(step: dict[str, Any]) -> str:
    return f"{step.get('phase', 'steps')}:{step.get('index')}:{step.get('action')}"


def action_numbers(result: dict[str, Any]) -> dict[str, int]:
    """Number the user actions in the order they happen: step key -> number.

    Stable across variants, because every variant walks the same path -- which
    is the premise of the tool and the reason a number is worth quoting. "Step 4
    failed on the second prompt" has to mean the same step to everyone reading.
    """
    numbers: dict[str, int] = {}
    for step in result.get("step_statuses", []):
        if step.get("action") in USER_ACTIONS:
            numbers[step_key(step)] = len(numbers) + 1
    return numbers


def describe_step(step: dict[str, Any], narration: dict[str, str]) -> str:
    """What this step does, in words. The agent's description when there is one,
    and a plain reading of the step when there is not."""
    described = narration.get(step_key(step))
    if described:
        return described
    action = step.get("action", "")
    target = step.get("target")
    if action == "click":
        return f"Click {target}" if target else "Click"
    if action == "type":
        return f"Type into {target}" if target else "Type"
    if action == "key":
        return f"Press a key in {target}" if target else "Press a key"
    return f"{action.replace('_', ' ')} {target or ''}".strip()


def intent_descriptions(run_dir: Path, results: list[dict[str, Any]]) -> dict[str, str]:
    """Step descriptions built from the flow's own `intent:` lines.

    Every target carries one -- "the properties tool in the ribbon", "where the
    reply appears" -- written when the flow was authored, by the person who
    knew what the control was. "Click properties_tool" throws that away and
    hands the reader an identifier; "Click the properties tool in the ribbon"
    is the same sentence a person would say.

    Shaped like a narration file so it can be laid under one: the model's
    descriptions win where they exist, and this covers the rest for free,
    without an API key.
    """
    try:
        from understudy.flow import load_flow

        flow = load_flow(run_dir / "flow.yaml")
    except Exception:
        return {}

    intents = {name: (target.intent or "").strip()
               for name, target in flow.targets.items()}
    verbs = {"click": "Click", "type": "Type into", "key": "Press a key in"}

    described: dict[str, str] = {}
    for result in results:
        for step in result.get("step_statuses", []):
            intent = intents.get(step.get("target") or "")
            verb = verbs.get(step.get("action", ""))
            if intent and verb:
                # The name as well as the intent. Intents are written both
                # ways -- "the main message input" but also "submits the
                # message" -- so "Click submits the message" is what you get
                # from gluing a verb to whichever one it happens to be. The
                # name also keeps the description tied to the flow file.
                name = (step.get("target") or "").replace("_", " ")
                described[step_key(step)] = f"{verb} {name} — {intent}"
    return described


def numbered_steps(result: dict[str, Any], narration: dict[str, str]) -> list[tuple[int, str, bool]]:
    """(number, description, is_reset) for each user action, in order."""
    numbers = action_numbers(result)
    out = []
    for step in result.get("step_statuses", []):
        number = numbers.get(step_key(step))
        if number is None:
            continue
        out.append((number, describe_step(step, narration), step.get("phase") == "reset"))
    return out


@dataclass(frozen=True)
class TimelineEntry:
    """One user action and everything the run recorded around it.

    The transcript is read step by step -- "what did it do, and what did the
    screen look like afterwards" -- so that is the shape it is built in. The
    captures, waits and reads that follow an action belong to it: they are the
    tool observing the consequences of that action, not steps of their own.
    """

    number: int
    description: str
    action: str
    target: str | None
    status: str
    duration_ms: int
    via: str | None = None
    note: str | None = None
    is_reset: bool = False
    #: The text this step typed, when it typed one.
    typed: str | None = None
    #: The keys this step pressed, when it pressed some.
    keys: str | None = None
    screenshots: tuple[str, ...] = ()
    #: name -> text, for reads that completed after this action.
    reads: tuple[tuple[str, str], ...] = ()
    #: name -> image, where the read came from pixels.
    read_images: tuple[tuple[str, str], ...] = ()
    #: How long the tool waited for the response after this action.
    waited_ms: int | None = None
    wait_note: str | None = None

    @property
    def failed(self) -> bool:
        return self.status not in ("ok", None)


def _note_of(step: dict[str, Any]) -> str | None:
    resolution = step.get("resolution") or {}
    detail = step.get("detail") or {}
    return step.get("error") or resolution.get("note") or detail.get("signal")


@dataclass(frozen=True)
class Exchange:
    """One thing said to the assistant, and what came back.

    A flow is not always one prompt. A real session is several: click into the
    model tree, ask for a hole, read the answer, click somewhere else, ask for
    a fillet, read that. Each of those is an exchange, and a prompt run is the
    whole conversation -- so a transcript that shows a single prompt and a
    single response is showing one turn of a session that had four.
    """

    #: 1-based, in the order they happened.
    number: int
    #: The numbered user action that typed it, so it can be quoted with R&D.
    step: int
    prompt: str
    #: name -> text, for everything read before the next thing was typed.
    reads: tuple[tuple[str, str], ...] = ()

    @property
    def response(self) -> str:
        return "\n".join(text for _, text in self.reads if text)


def exchanges(result: dict[str, Any]) -> list[Exchange]:
    """The conversation, in order, derived from what the steps actually did.

    Reads belong to the last thing typed before them, which is the same rule a
    person reading the screen uses. Anything read before the first prompt --
    the state the panel started in -- belongs to no exchange and is left to the
    timeline.
    """
    typed: list[Exchange] = []
    for entry in timeline(result, {}):
        if entry.typed is not None:
            typed.append(Exchange(
                number=0, step=entry.number,
                prompt=entry.typed, reads=tuple(entry.reads),
            ))
        elif typed and entry.reads:
            last = typed[-1]
            typed[-1] = replace(last, reads=last.reads + tuple(entry.reads))

    # Typing that was answered is a turn; typing that was not is a step. A
    # flow types into form fields as well as prompt boxes -- renaming a part
    # before asking about it is two typed steps and one question -- and
    # calling a filename an exchange would be wrong in the one place this
    # view exists to be right. Every typed step is still in the timeline.
    answered = [turn for turn in typed if turn.reads]
    return [replace(turn, number=index)
            for index, turn in enumerate(answered, start=1)]


def timeline(result: dict[str, Any], narration: dict[str, str]) -> list[TimelineEntry]:
    """Group the run's steps into the user actions a person performed.

    Anything before the first action -- the opening screenshot, usually -- comes
    back as entry number 0, because "what it looked like to start with" is worth
    showing and does not belong to any step.
    """
    numbers = action_numbers(result)
    reads = result.get("reads") or {}
    read_images = result.get("read_images") or {}

    entries: list[TimelineEntry] = []
    pending: dict[str, Any] = {
        "screenshots": [], "reads": [], "read_images": [],
        "waited_ms": None, "wait_note": None,
    }

    def flush_into(entry: TimelineEntry) -> TimelineEntry:
        merged = replace(
            entry,
            screenshots=tuple(pending["screenshots"]),
            reads=tuple(pending["reads"]),
            read_images=tuple(pending["read_images"]),
            waited_ms=pending["waited_ms"],
            wait_note=pending["wait_note"],
        )
        pending.update({"screenshots": [], "reads": [], "read_images": [],
                        "waited_ms": None, "wait_note": None})
        return merged

    open_entry: TimelineEntry | None = None
    for step in result.get("step_statuses", []):
        detail = step.get("detail") or {}
        number = numbers.get(step_key(step))

        if number is not None:
            if open_entry is not None:
                entries.append(flush_into(open_entry))
            elif any(pending[key] for key in ("screenshots", "reads", "read_images")):
                # Whatever happened before the first action: the opening shot.
                entries.append(flush_into(TimelineEntry(
                    number=0, description="Before the first step", action="start",
                    target=None, status="ok", duration_ms=0,
                )))
            open_entry = TimelineEntry(
                number=number,
                description=describe_step(step, narration),
                action=step.get("action", ""),
                target=step.get("target"),
                status=step.get("status", "ok"),
                duration_ms=int(step.get("duration_ms") or 0),
                via=(step.get("resolution") or {}).get("via"),
                note=_note_of(step),
                is_reset=step.get("phase") == "reset",
                typed=detail.get("text"),
                keys=detail.get("keys"),
            )
            continue

        if detail.get("screenshot"):
            pending["screenshots"].append(detail["screenshot"])
        if detail.get("store_as"):
            name = detail["store_as"]
            pending["reads"].append((name, reads.get(name, "")))
            if name in read_images:
                pending["read_images"].append((name, read_images[name]))
        if "waited_ms" in detail:
            pending["waited_ms"] = detail["waited_ms"]
            pending["wait_note"] = step.get("error") or detail.get("signal")

    if open_entry is not None:
        entries.append(flush_into(open_entry))
    elif any(pending[key] for key in ("screenshots", "reads", "read_images")):
        entries.append(flush_into(TimelineEntry(
            number=0, description="Before the first step", action="start",
            target=None, status="ok", duration_ms=0,
        )))
    return entries


def unclaimed_screenshots(result: dict[str, Any],
                          entries: list[TimelineEntry]) -> list[str]:
    """Screenshots the timeline did not place against a step.

    A capture always records the file it wrote, so normally there are none. A
    run from an older version, or an image written outside a capture step, would
    otherwise disappear from the transcript entirely -- and a screenshot that
    exists on disk but appears nowhere is the kind of gap nobody notices until
    they are looking for the one picture that would have explained something.
    """
    placed = {shot for entry in entries for shot in entry.screenshots}
    placed |= {image for entry in entries for _, image in entry.read_images}
    return [
        shot for shot in result.get("screenshots", [])
        if shot not in placed
        and shot not in (result.get("read_images") or {}).values()
    ]


def screenshot_captions(result: dict[str, Any], narration: dict[str, str]) -> dict[str, str]:
    """Tie each screenshot to the step it followed.

    "3-dialog-open" says what is in the picture; "after step 4 -- Open the
    properties dialog" says which action produced it, which is the question
    somebody asks when a screenshot looks wrong.
    """
    numbers = action_numbers(result)
    captions: dict[str, str] = {}
    last: tuple[int, str] | None = None
    for step in result.get("step_statuses", []):
        number = numbers.get(step_key(step))
        if number is not None:
            last = (number, describe_step(step, narration))
            continue
        shot = (step.get("detail") or {}).get("screenshot")
        if shot and last is not None:
            captions[shot] = f"after step {last[0]} — {last[1]}"
        elif shot:
            captions[shot] = "before the first step"
    return captions


def _sample_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (r for r in results if r.get("status") == "ok"),
        results[0] if results else None,
    )


def _walkthrough(results: list[dict[str, Any]], narration: dict[str, str]) -> list[str]:
    """The click path in plain language, once, numbered.

    It belongs at the top and only once because every variant walks the same
    path -- that is the premise of the whole tool. The numbers are the point of
    the section: they are what somebody quotes back at you.
    """
    sample = _sample_result(results)
    if sample is None:
        return []
    steps = numbered_steps(sample, narration)
    if not steps:
        return []

    lines = ["", "## Steps", ""]
    for number, description, is_reset in steps:
        prefix = "_(reset)_ " if is_reset else ""
        lines.append(f"{number}. {prefix}{description}")
    if narration:
        lines += ["", "_Descriptions written from the run's own screenshots._"]
    return lines


def _steps_table(result: dict[str, Any], narration: dict[str, str]) -> list[str]:
    lines = [
        "| step | action | target | what it does | status | ms | via | note |",
        "|------|--------|--------|--------------|--------|----|-----|------|",
    ]
    described = {ref.key: narration.get(ref.key, "") for ref in steps_of(result)}
    numbers = action_numbers(result)
    for step in result.get("step_statuses", []):
        resolution = step.get("resolution") or {}
        detail = step.get("detail") or {}
        note = step.get("error") or detail.get("signal") or resolution.get("note") or ""
        if "waited_ms" in detail:
            note = f"waited {detail['waited_ms']}ms, {detail.get('samples', '?')} samples"
        # Reset and main steps both number from 1; without the phase the table
        # looks like it repeats itself.
        phase = step.get("phase", "steps")
        key = step_key(step)
        # The user-action number, blank for the tool's own housekeeping. A
        # transcript that numbers "capture" alongside "click" makes "step 4"
        # ambiguous between the person reading it and the person who ran it.
        action_number = numbers.get(key)
        number = "" if action_number is None else str(action_number)
        if number and phase == "reset":
            number = f"{number} (reset)"
        # A user action always says what it does, from the agent's narration
        # when there is one and from the step itself when there is not. The
        # tool's own housekeeping only speaks when narrated.
        what = (describe_step(step, narration) if action_number is not None
                else described.get(key, ""))
        lines.append(
            f"| {number} | {step.get('action', '')} "
            f"| {step.get('target') or ''} | {_escape(what)} "
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
    # The flow's own intents underneath, the model's descriptions on top.
    narration = {**intent_descriptions(run_dir, results),
                 **load_narration(run_dir)}

    title, description = _flow_heading(run_dir)
    out: list[str] = [f"# {title}", ""]
    if description:
        out += [description, ""]

    # What produced these answers, first: a transcript records a reply, and
    # without the release that said it, comparing two of them means nothing.
    subject = subject_of(results)
    if subject.recorded:
        out.append("- **Under test** " + subject.summary())
        for field, label in SUBJECT_LABELS.items():
            value = getattr(subject, field)
            if value and field not in ("app", "app_version", "model",
                                       "model_version", "release"):
                out.append(f"- **{label}** {value}")
    out += [
        f"- **Run** `{run_dir.name}` · {summary.backend}",
        f"- **Prompt runs** {summary.variants} — "
        f"{summary.passed} ok, {summary.failed} failed, {summary.timed_out} timed out",
    ]
    if results:
        out.append(f"- **Started** {results[0].get('timestamp', '?')}")

    # The raw material either side of the run, named as what they are rather
    # than left as a row of filenames.
    inputs = [name for name in ("flow.yaml", "prompts.yaml", "prompts.csv")
              if (run_dir / name).exists()]
    outputs = [name for name in ("results.jsonl", "results.csv")
               if (run_dir / name).exists()]
    if inputs:
        out.append("- **Input** " + ", ".join(f"[`{n}`]({n})" for n in inputs))
    if outputs:
        out.append("- **Output** " + ", ".join(f"[`{n}`]({n})" for n in outputs))

    out += _recordings_markdown(results)

    # And then the run, step by step. There was a summary table, a list of the
    # steps, a table of prompts against responses, and then the steps again
    # with everything attached -- four views of one run, and a reader assembling
    # them. One view: what happened, in order, with what was said and what came
    # back at the step where it happened.
    for result in results:
        out += _variant_section(run_dir, result, embed, narration)

    out += ["", "---", "",
            "_Generated by understudy. Screenshots are the run's own captures; "
            "response text is whatever the flow's `read` step extracted._"]
    return "\n".join(out) + "\n"


def _slug(result: dict[str, Any]) -> str:
    suffix = f"-{result['repeat_index'] + 1}" if result.get("repeat_index") else ""
    return f"{result['prompt_id']}{suffix}".lower().replace(" ", "-").replace(".", "")


def _recordings_markdown(results: list[dict[str, Any]]) -> list[str]:
    """Every run's video, together and early.

    Watching it is the fastest way to know whether the replay did what it was
    meant to -- faster than any table -- so it belongs above the detail rather
    than inside each prompt run's own section, which is where a reader finds it
    only after scrolling past everything it would have explained.
    """
    videos = [r for r in results if r.get("recording")]
    failures = [r for r in results if not r.get("recording") and r.get("recording_error")]
    if not videos and not failures:
        return []

    named = len(results) > 1
    out = ["", "## Recording", ""]
    for result in videos:
        if named:
            out += [f"**{result['prompt_id']}**", ""]
        out += [
            f'<video controls width="460" src="{result["recording"]}"></video>', "",
            f"[{result['recording']}]({result['recording']})", "",
        ]
    for result in failures:
        label = f"{result['prompt_id']}: " if named else ""
        out += [f"_No recording — {label}{_escape(result['recording_error'])}_", ""]
    return out


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

    # Everything a prompt run said and heard is in the steps, at the step it
    # happened. Variables the steps did not say -- a style, a document name --
    # are the only thing left worth stating up front.
    said = {turn.prompt.strip() for turn in exchanges(result)}
    other = {k: v for k, v in (result.get("variables") or {}).items()
             if str(v).strip() and str(v).strip() not in said}
    if other:
        out += ["", ", ".join(f"`{k}` = {v}" for k, v in sorted(other.items()))]

    entries = timeline(result, narration or {})
    shown_reads = {name for entry in entries for name, _ in entry.read_images}
    for name, relative in (result.get("read_images") or {}).items():
        if name not in shown_reads:
            out += ["", f"Recorded `{name}` region:", "",
                    _image(run_dir, relative, RESPONSE_IMAGE_WIDTH, embed)]

    out += _timeline_markdown(run_dir, result, embed, narration or {})

    out += ["", "<details><summary>Every step, including the tool's own</summary>", ""]
    out += _steps_table(result, narration or {})
    out += ["", "</details>"]
    return out


def _timeline_markdown(run_dir: Path, result: dict[str, Any], embed: bool,
                       narration: dict[str, str]) -> list[str]:
    """The run read step by step. Same shape as the page, in markdown.

    A gallery at the bottom and a table below it makes the reader do the
    joining. Every question anyone asks of a transcript -- what did step 4 do,
    what did the screen look like afterwards, what did the assistant reply --
    is a question about one step.
    """
    entries = timeline(result, narration)
    if not entries:
        return []

    # Which steps are turns in a conversation. Typing a prompt and typing a
    # filename are both "type", and only one of them has a reply.
    turns = {turn.step: turn.number for turn in exchanges(result)}

    out = ["", "### Step by step", ""]
    for entry in entries:
        if entry.number == 0:
            out += ["**Before the first step**", ""]
        else:
            reset = "_(reset)_ " if entry.is_reset else ""
            out += [f"**{entry.number}. {reset}{entry.description}**", ""]

        meta = [f"{entry.duration_ms} ms"] if entry.number else []
        if entry.via:
            meta.append(entry.via)
        if entry.failed:
            meta.append(f"**{STATUS_MARK.get(entry.status, entry.status)}**")
        if entry.waited_ms is not None:
            meta.append(f"waited {entry.waited_ms} ms for the response")
        if entry.note:
            meta.append(_escape(str(entry.note)))
        if meta:
            out += [f"_{' · '.join(meta)}_", ""]

        # Numbered only when there is more than one, because "Prompt 1" on a
        # flow that asks one question is a number that answers no question.
        turn = turns.get(entry.number)
        count = "" if len(turns) < 2 else f" {turn}"
        if entry.typed:
            label = f"Prompt{count}:" if turn else "Typed:"
            out += [label, ""] + _fence(entry.typed) + [""]
        if entry.keys:
            out += [f"Pressed `{entry.keys}`", ""]
        for name, text in entry.reads:
            label = f"Reply{count}:" if turn else f"Read as `{name}`:"
            out += [label, ""] + _fence(text or "(nothing captured)") + [""]
        for name, relative in entry.read_images:
            out += [f"The pixels `{name}` was read from:", "",
                    _image(run_dir, relative, RESPONSE_IMAGE_WIDTH, embed), ""]
        for relative in entry.screenshots:
            out += [_image(run_dir, relative, THUMBNAIL_WIDTH, embed), "",
                    f"_{_label_of(relative)}_", ""]

    orphans = unclaimed_screenshots(result, entries)
    if orphans:
        out += ["", "#### Other screenshots", ""]
        for relative in orphans:
            out += [f"**{_label_of(relative)}**", "",
                    _image(run_dir, relative, THUMBNAIL_WIDTH, embed), ""]
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
