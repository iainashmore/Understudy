"""The transcript as a web page.

Same content as the markdown, different reader. Two things need it: the viewer
inside the tool, where the video has to play and the screenshots have to be
visible without leaving the app, and the PDF, which is printed from this page by
a browser.

Written from the results rather than by converting the markdown -- a markdown
renderer is a dependency and a parser is a source of bugs, and neither buys
anything when the data is right there.

`embed=True` inlines the images so the file stands alone, and the video too
when it is small enough to stay emailable. Base64 costs a third on top of a
file that is already the largest thing in the run, so past a cap the link
stays a link -- and says the file sits beside the transcript, rather than
showing a player that cannot play because the reader moved one file and not
the other.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from understudy.narrate import load_narration
from understudy.subject import LABELS
from understudy.transcript import (
    STATUS_MARK,
    _flow_heading,
    _label_of,
    action_numbers,
    describe_step,
    exchanges,
    folder_of,
    intent_descriptions,
    load_results,
    numbered_steps,
    screenshot_captions,
    step_key,
    subject_of,
    summarise,
    timeline,
    unclaimed_screenshots,
    _sample_result,
)

STYLE = """
:root { color-scheme: light dark;
  --bg:#ffffff; --fg:#1b1f24; --dim:#5c6773; --line:#d8dee6; --panel:#f6f8fa;
  --ok:#1a7f45; --bad:#c0392b; --warn:#9a6b00; --accent:#1f6feb; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg:#1c2025; --fg:#e6eaef; --dim:#9aa5b1;
          --line:#39414b; --panel:#252a31; --ok:#4fd98a; --bad:#e05c5c;
          --warn:#e0a94f; --accent:#4f9dd9; } }
/* The viewer inside the tool asks for a theme explicitly, so the page does not
   sit as a white sheet inside a dark application. */
:root[data-theme="dark"] { color-scheme: dark;
  --bg:#1c2025; --fg:#e6eaef; --dim:#9aa5b1; --line:#39414b; --panel:#252a31;
  --ok:#4fd98a; --bad:#e05c5c; --warn:#e0a94f; --accent:#4f9dd9; }
:root[data-theme="light"] { color-scheme: light; }
* { box-sizing:border-box; }
body { margin:0; padding:28px 32px 60px; background:var(--bg); color:var(--fg);
  font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:900px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:18px; margin:32px 0 10px; padding-top:14px;
     border-top:1px solid var(--line); }
h3 { font-size:14px; margin:20px 0 8px; text-transform:uppercase;
     letter-spacing:.06em; color:var(--dim); }
.lede { color:var(--dim); margin:0 0 18px; }
.meta { display:flex; flex-wrap:wrap; gap:6px 18px; color:var(--dim);
        font-size:13px; margin-bottom:18px; }
.meta b { color:var(--fg); font-weight:600; }
.tagchip { display:inline-block; margin-left:6px; padding:1px 8px; font-size:12px;
           border:1px solid var(--line); border-radius:10px; color:var(--fg);
           background:var(--panel); }
/* The raw material either side of the run. Worth finding: it is what somebody
   checks when they doubt the transcript. */
.meta.files { margin-top:2px; }
.meta.files a { border-bottom:1px solid var(--line); }
table { border-collapse:collapse; width:100%; font-size:13px; margin:8px 0; }
th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--dim); font-weight:600; }
td.num { text-align:right; white-space:nowrap; color:var(--dim); }
.wrap { overflow-x:auto; }
.pill { display:inline-block; padding:1px 8px; border-radius:9px;
        font-size:12px; font-weight:600; }
.pill.pass { background:rgba(79,217,138,.18); color:var(--ok); }
.pill.FAIL, .pill.TIMEOUT { background:rgba(224,92,92,.18); color:var(--bad); }
pre { background:var(--panel); border:1px solid var(--line); border-radius:6px;
      padding:10px 12px; overflow:auto; white-space:pre-wrap;
      font:12.5px/1.55 ui-monospace,"DejaVu Sans Mono",monospace; }
ol.steps { padding-left:22px; }
ol.timeline { list-style:none; margin:0; padding:0; }
ol.timeline > li { border-left:2px solid var(--line); padding:2px 0 18px 18px;
  margin-left:11px; position:relative; }
ol.timeline > li:last-child { border-left-color:transparent; }
ol.timeline .head { display:flex; align-items:baseline; gap:8px; }
ol.timeline .step-no { position:absolute; left:-12px; top:0; width:22px;
  height:22px; border-radius:50%; background:var(--panel);
  border:1px solid var(--line); color:var(--dim); font-size:11px;
  font-weight:700; display:flex; align-items:center; justify-content:center; }
ol.timeline li.failed .step-no { border-color:var(--bad); color:var(--bad); }
ol.timeline .reset { color:var(--dim); font-style:italic; font-size:12px; }
.entry-meta { color:var(--dim); font-size:12px; margin:2px 0 8px; }
.typed { margin:6px 0; }
.typed .tag { display:inline-block; font-size:11px; letter-spacing:.05em;
  text-transform:uppercase; color:var(--dim); margin-bottom:2px; }
.typed pre { margin:2px 0 0; }
figure.region img { max-width:340px; }
ol.steps li { margin:3px 0; }
ol.steps .reset { color:var(--dim); font-style:italic; }
figure { margin:0 0 16px; }
figure img { max-width:100%; border:1px solid var(--line); border-radius:6px;
             display:block; }
figcaption { color:var(--dim); font-size:12px; padding-top:4px; }
video { max-width:100%; border:1px solid var(--line); border-radius:6px; }
.note { border-left:3px solid var(--warn); background:rgba(224,169,79,.08);
        padding:8px 12px; border-radius:0 4px 4px 0; margin:10px 0;
        color:var(--dim); }
.error { border-left-color:var(--bad); background:rgba(224,92,92,.08); }
/* One turn of a conversation. The rule down the side is what makes four
   exchanges read as four rather than one wall of text. */
.exchange { border-left:3px solid var(--line); padding-left:14px; margin:14px 0; }
.exchange .lede { margin:6px 0 4px; }
.step-ref { opacity:.7; }
footer { margin-top:40px; padding-top:14px; border-top:1px solid var(--line);
         color:var(--dim); font-size:12px; }
a { color:var(--accent); }
@media print {
  body { padding:0; font-size:10.5pt; }
  h2 { padding-top:0; border-top:none; break-after:avoid; }
  /* Only a variant starts a fresh page. Breaking before every heading turns a
     one-variant run into six mostly-empty sheets. */
  h2.variant { break-before:page; }
  .exchange { break-inside:avoid; }
  h3 { break-after:avoid; }
  figure, tr, pre { break-inside:avoid; }
  figure img { max-height:150mm; width:auto; }
  video { display:none; }
  .screen-only { display:none; }
}
"""


#: Some Chromium builds ship without proprietary codecs, so an mp4 recording
#: shows as a dead player with no explanation. Say so rather than leaving the
#: reader to conclude the recording is broken.
CODEC_SCRIPT = (
    "(()=>{const v=document.createElement('video');"
    "if(v.canPlayType('video/mp4; codecs=\"avc1.42E01E\"'))return;"
    "for(const n of document.querySelectorAll('.no-codec'))n.hidden=false;"
    "for(const n of document.querySelectorAll('video'))n.hidden=true;})();"
)

#: `?theme=dark` on the URL. Printing and opening the file directly ignore it
#: and fall back to the reader's own preference, which is what they should do.
THEME_SCRIPT = (
    "(()=>{const t=new URLSearchParams(location.search).get('theme');"
    "if(t==='dark'||t==='light')document.documentElement"
    ".setAttribute('data-theme',t);})();"
)


def _e(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _subject_tags(subject) -> str:
    """What was under test, as separate chips rather than one sentence.

    "R2026x FD03" is the thing a reader looks for, and comparing FD03 against
    FD04 starts with finding them.
    """
    if not subject.recorded:
        return ""
    chips = "".join(f'<span class="tagchip" title="{_e(LABELS[field])}">'
                    f"{_e(value)}</span>"
                    for field, value in subject.tags())
    return f"<span><b>Under test</b> {chips}</span>"


def _beside(relative: str, base: str) -> str:
    """A run-relative path, rewritten for a page that lives inside `base`."""
    if base and relative.startswith(base + "/"):
        return relative[len(base) + 1:]
    return relative


def _img(run_dir: Path, relative: str, embed: bool, base: str = "") -> str:
    path = run_dir / relative
    if not path.exists():
        return f'<p class="note">missing: {_e(relative)}</p>'
    if embed:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        source = f"data:image/png;base64,{data}"
    else:
        source = _beside(relative, base)
    return f'<img src="{source}" alt="{_e(relative)}" loading="lazy">'


#: Past this a video stays a link. A transcript people email should not need
#: a broadband connection to open, and a sweep of forty variants would carry
#: forty of these.
MAX_INLINE_VIDEO_BYTES = 12 * 1024 * 1024


def _video_source(run_dir: Path, relative: str, embed: bool,
                  base: str = "") -> tuple[str, bool]:
    """Where the player should point, and whether the file is inside this one."""
    path = run_dir / relative
    if not embed or not path.exists():
        return _e(_beside(relative, base)), False
    if path.stat().st_size > MAX_INLINE_VIDEO_BYTES:
        return _e(_beside(relative, base)), False
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return f"data:video/mp4;base64,{data}", True


def _status_pill(status: str) -> str:
    mark = STATUS_MARK.get(status, status or "?")
    return f'<span class="pill {_e(mark)}">{_e(mark)}</span>'


def _steps_list(results: list[dict[str, Any]], narration: dict[str, str]) -> str:
    sample = _sample_result(results)
    if sample is None:
        return ""
    steps = numbered_steps(sample, narration)
    if not steps:
        return ""
    items = []
    for _, description, is_reset in steps:
        prefix = '<span class="reset">(reset)</span> ' if is_reset else ""
        items.append(f"<li>{prefix}{_e(description)}</li>")
    trailer = ('<p class="lede">Descriptions written from the run\'s own '
               'screenshots.</p>') if narration else ""
    return (
        "<h2>Steps</h2>"
        f'<ol class="steps">{"".join(items)}</ol>{trailer}'
    )


def _summary_table(results: list[dict[str, Any]]) -> str:
    rows = []
    for result in results:
        notes = []
        for key, label in (("used_fallbacks", "fallbacks"),
                           ("agent_resolutions", "agent"),
                           ("learned_anchors", "learned")):
            if result.get(key):
                notes.append(f"{label}: {', '.join(result[key])}")
        if result.get("pointer_note"):
            notes.append(f"pointer: {result['pointer_note']}")
        if result.get("error"):
            notes.append(result["error"])
        rows.append(
            f'<tr><td><a href="#{_e(_slug(result))}">{_e(result["prompt_id"])}</a></td>'
            f"<td>{_status_pill(result.get('status'))}</td>"
            f'<td class="num">{_e(result.get("duration_ms", 0))} ms</td>'
            f'<td class="num">{len(result.get("response") or "")}</td>'
            f"<td>{_e('; '.join(notes))}</td></tr>"
        )
    return (
        '<h2>Summary</h2><div class="wrap"><table><thead><tr>'
        "<th>prompt run</th><th>status</th><th>duration</th><th>response</th>"
        "<th>notes</th></tr></thead><tbody>"
        f"{''.join(rows)}</tbody></table></div>"
    )


def _slug(result: dict[str, Any]) -> str:
    suffix = f"-{result['repeat_index'] + 1}" if result.get("repeat_index") else ""
    return f"{result['prompt_id']}{suffix}".lower().replace(" ", "-").replace(".", "")


def _steps_table(result: dict[str, Any], narration: dict[str, str]) -> str:
    numbers = action_numbers(result)
    rows = []
    for step in result.get("step_statuses", []):
        resolution = step.get("resolution") or {}
        detail = step.get("detail") or {}
        note = step.get("error") or detail.get("signal") or resolution.get("note") or ""
        if "waited_ms" in detail:
            note = f"waited {detail['waited_ms']}ms, {detail.get('samples', '?')} samples"
        number = numbers.get(step_key(step))
        label = "" if number is None else str(number)
        if label and step.get("phase") == "reset":
            label += " (reset)"
        what = (describe_step(step, narration) if number is not None
                else narration.get(step_key(step), ""))
        rows.append(
            f'<tr><td class="num">{_e(label)}</td><td>{_e(step.get("action"))}</td>'
            f'<td>{_e(step.get("target") or "")}</td><td>{_e(what)}</td>'
            f"<td>{_status_pill(step.get('status'))}</td>"
            f'<td class="num">{_e(step.get("duration_ms", ""))}</td>'
            f'<td>{_e(resolution.get("via") or "")}</td><td>{_e(note)}</td></tr>'
        )
    return (
        '<h3>Every step</h3><div class="wrap"><table><thead><tr><th>step</th>'
        "<th>action</th><th>target</th><th>what it does</th><th>status</th>"
        "<th>ms</th><th>via</th><th>note</th></tr></thead><tbody>"
        f"{''.join(rows)}</tbody></table></div>"
    )


def _timeline(run_dir: Path, result: dict[str, Any], embed: bool,
              narration: dict[str, str], base: str = "") -> str:
    """The run read step by step: what was done, then what it looked like.

    A gallery of screenshots at the bottom and a table of steps below that
    makes the reader do the joining. Every question anyone asks of a transcript
    -- what did step 4 do, what did the screen look like after it, what did the
    assistant actually reply -- is a question about one step.
    """
    entries = timeline(result, narration)
    if not entries:
        return ""

    # Which steps are turns in a conversation. Typing a prompt and typing a
    # filename are both "type", and only one of them has a reply.
    turns = {turn.step: turn.number for turn in exchanges(result)}

    out = ["<h3>Step by step</h3>", '<ol class="timeline">']
    for entry in entries:
        classes = "entry" + (" failed" if entry.failed else "")
        if entry.number == 0:
            head = '<span class="step-no">—</span><b>Before the first step</b>'
        else:
            reset = '<span class="reset">(reset)</span> ' if entry.is_reset else ""
            head = (f'<span class="step-no">{entry.number}</span>'
                    f"{reset}<b>{_e(entry.description)}</b>")

        meta = [f"{entry.duration_ms} ms"] if entry.number else []
        if entry.via:
            meta.append(_e(entry.via))
        if entry.failed:
            meta.append(f'<span class="pill FAIL">{_e(entry.status)}</span>')
        if entry.waited_ms is not None:
            meta.append(f"waited {entry.waited_ms} ms for the response")
        if entry.note:
            meta.append(_e(entry.note))

        body = [f'<li class="{classes}"><div class="head">{head}</div>',
                f'<div class="entry-meta">{" · ".join(meta)}</div>' if meta else ""]

        turn = turns.get(entry.number)
        count = "" if len(turns) < 2 else f" {turn}"
        if entry.typed:
            tag = f"prompt{count}" if turn else "typed"
            body.append(f'<div class="typed"><span class="tag">{_e(tag)}</span>'
                        f"<pre>{_e(entry.typed)}</pre></div>")
        if entry.keys:
            body.append(f'<div class="typed"><span class="tag">keys</span>'
                        f"<pre>{_e(entry.keys)}</pre></div>")

        for name, text in entry.reads:
            tag = f"reply{count}" if turn else name
            body.append(f'<div class="typed"><span class="tag">{_e(tag)}</span>'
                        f"<pre>{_e(text) or '<em>nothing captured</em>'}</pre></div>")
        for name, relative in entry.read_images:
            body.append(f"<figure class=\"region\">{_img(run_dir, relative, embed, base)}"
                        f"<figcaption>the pixels <code>{_e(name)}</code> was read "
                        f"from</figcaption></figure>")

        for relative in entry.screenshots:
            body.append(f"<figure>{_img(run_dir, relative, embed, base)}"
                        f"<figcaption>{_e(_label_of(relative))}</figcaption></figure>")

        body.append("</li>")
        out.append("".join(body))
    out.append("</ol>")

    orphans = unclaimed_screenshots(result, entries)
    if orphans:
        out.append("<h3>Other screenshots</h3>")
        for relative in orphans:
            out.append(f"<figure>{_img(run_dir, relative, embed, base)}<figcaption>"
                       f"{_e(_label_of(relative))}</figcaption></figure>")
    return "".join(out)


#: Raw files are small next to a video, but a results file from a long sweep
#: is not nothing, and a transcript nobody can open is worse than a link.
MAX_INLINE_FILE_BYTES = 4 * 1024 * 1024


def _file_link(run_dir: Path, name: str, embed: bool, prefix: str = "") -> str:
    """A link to a raw file that still works from a copy of this page alone.

    The exported transcript is one file, sent to somebody who does not have the
    run directory. A relative link to flow.yaml is dead the moment it leaves,
    which is exactly when a reader most wants to see what was actually run --
    so when the page is standalone, the file travels inside it.
    """
    path = run_dir / name
    if not embed or not path.exists() or path.stat().st_size > MAX_INLINE_FILE_BYTES:
        return f'<a href="{_e(prefix + name)}">{_e(name)}</a>'
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return (f'<a href="data:text/plain;base64,{data}" download="{_e(name)}">'
            f"{_e(name)}</a>")


def _recordings(run_dir: Path, results: list[dict[str, Any]], embed: bool,
                base: str = "") -> str:
    """Every prompt run's video, together and early.

    Watching it is the fastest way to know whether the replay did what it was
    meant to -- faster than any table -- so it belongs above the detail rather
    than inside each prompt run's section, which is where a reader finds it
    only after scrolling past everything it would have explained.
    """
    videos = [r for r in results if r.get("recording")]
    failures = [r for r in results
                if not r.get("recording") and r.get("recording_error")]
    if not videos and not failures:
        return ""

    named = len(results) > 1
    out = ["<h2>Recording</h2>"]
    for result in videos:
        source, inlined = _video_source(run_dir, result["recording"], embed, base)
        beside = ("" if inlined or not embed else
                  '<p class="note">The video is not inside this file: it is too '
                  'large to inline. Keep it beside the transcript.</p>')
        out.append(
            (f'<p class="lede">{_e(result["prompt_id"])}</p>' if named else "")
            + f'<video controls preload="metadata" src="{source}"></video>'
            f'<p class="no-codec note" hidden>This browser cannot decode H.264. '
            f"Chrome, Edge and any desktop player can; some Chromium builds "
            f"ship without it.</p>{beside}"
        )
    for result in failures:
        label = f"{_e(result['prompt_id'])}: " if named else ""
        out.append(f'<p class="note">No recording — {label}'
                   f'{_e(result["recording_error"])}</p>')
    return "".join(out)


def _exchange_rows(result: dict[str, Any]) -> str:
    """One row per turn, so a session of four questions reads as four.

    A flow is not always one prompt: click into the tree, ask, read, click
    elsewhere, ask again. Showing only the read stored as `response` shows one
    turn of a conversation that had four.
    """
    name = _e(result["prompt_id"])
    turns = exchanges(result)
    if len(turns) <= 1:
        return (f"<tr><td>{name}</td><td>{_e(result.get('prompt', ''))}</td>"
                f"<td>{_e(result.get('response') or '')}</td></tr>")
    rows = []
    for turn in turns:
        label = f"{name} <span class=\"lede\">{turn.number}/{len(turns)}</span>"
        rows.append(f"<tr><td>{label}</td><td>{_e(turn.prompt)}</td>"
                    f"<td>{_e(turn.response)}</td></tr>")
    return "".join(rows)


def _variant(run_dir: Path, result: dict[str, Any], embed: bool,
             narration: dict[str, str], heading: bool = True,
             base: str = "") -> str:
    out = []
    if heading:
        title = result["prompt_id"]
        if result.get("repeat_index"):
            title += f" (repeat {result['repeat_index'] + 1})"
        out += [f'<h2 class="variant" id="{_e(_slug(result))}">{_e(title)}</h2>',
                f'<p class="meta">{_status_pill(result.get("status"))}'
                f'<span>{_e(result.get("duration_ms", 0))} ms</span></p>']
    if result.get("error"):
        out.append(f'<p class="note error">{_e(result["error"])}</p>')
    if result.get("pointer_note"):
        out.append(f'<p class="note">Clicks were synthetic: '
                   f'{_e(result["pointer_note"])}. Nothing filming the screen '
                   f'would have seen the cursor move.</p>')

    # Everything this prompt run said and heard is in the steps, at the step
    # it happened. Variables the steps did not say -- a style, a document name
    # -- are the only thing left worth stating up front.
    said = {turn.prompt.strip() for turn in exchanges(result)}
    other = {k: v for k, v in (result.get("variables") or {}).items()
             if str(v).strip() and str(v).strip() not in said}
    if other:
        items = " · ".join(f"<code>{_e(k)}</code> = {_e(v)}"
                           for k, v in sorted(other.items()))
        out.append(f'<p class="meta"><span>{items}</span></p>')

    entries = timeline(result, narration)
    shown = {name for entry in entries for name, _ in entry.read_images}
    for name, relative in (result.get("read_images") or {}).items():
        if name not in shown:
            out.append(f'<figure class="region">{_img(run_dir, relative, embed, base)}'
                       f"<figcaption>recorded <code>{_e(name)}</code> region"
                       f"</figcaption></figure>")

    out.append(_timeline(run_dir, result, embed, narration, base))
    out.append(
        "<details><summary>Every step, including the tool's own</summary>"
        f"{_steps_table(result, narration)}</details>"
    )
    return "".join(out)


def render_html(
    run_dir: Path | str, results: list[dict[str, Any]] | None = None,
    embed: bool = False,
) -> str:
    run_dir = Path(run_dir)
    results = load_results(run_dir) if results is None else results
    summary = summarise(run_dir, results)
    # The flow's own intents underneath, the model's descriptions on top.
    narration = {**intent_descriptions(run_dir, results),
                 **load_narration(run_dir)}
    title, description = _flow_heading(run_dir)

    body = [
        f"<h1>{_e(title)}</h1>",
        f'<p class="lede">{_e(description)}</p>' if description else "",
        '<p class="meta">'
        # What was under test first: a transcript records a reply, and without
        # the release that said it, comparing two of them means nothing.
        + _subject_tags(subject_of(results))
        + f"<span><b>Run</b> {_e(run_dir.name)} · {_e(summary.backend)}</span>"
        f"<span><b>Prompt runs</b> {summary.variants} — {summary.passed} ok, "
        f"{summary.failed} failed, {summary.timed_out} timed out</span>"
        + (f"<span><b>Started</b> {_e(results[0].get('timestamp', '?'))}</span>"
           if results else "")
        + "</p>",
        # Not in the PDF: a printed page cannot follow a link, and in a
        # standalone copy they are data: URIs that print as nothing useful.
        # They matter on screen and in a published repository, where the files
        # sit next to the transcript and the relative links resolve.
        '<p class="meta files screen-only">'
        + _group_links(run_dir, "Input",
                       ("flow.yaml", "prompts.yaml", "prompts.csv"), embed)
        + _group_links(run_dir, "Output",
                       ("results.jsonl", "results.csv"), embed)
        + "</p>",
    ]
    # A front door, not a compendium: each prompt run has its own page in its
    # own folder. Twelve prompts is twelve files, not one page a reader
    # scrolls through hunting for the third.
    body.append("<h2>Prompt runs</h2><ol class=\"steps\">")
    for result in results:
        label = _e(result["prompt_id"])
        if result.get("repeat_index"):
            label += f" (repeat {result['repeat_index'] + 1})"
        body.append(
            f'<li><a href="{_e(folder_of(result))}/transcript.html">{label}</a> '
            f'{_status_pill(result.get("status"))}'
            f'<span class="lede">{_e(result.get("duration_ms", 0))} ms</span></li>'
        )
    body.append("</ol>")
    return _page(title, body)


def render_full_html(run_dir: Path | str, embed: bool = False) -> str:
    """Every prompt run on one page, for printing.

    On screen the split is the point: twelve prompt runs are twelve pages, and
    a reader opens the one they want. A PDF is the opposite -- it is the copy
    that gets filed and mailed, and one that says "see the other eleven files"
    is not a record of anything.
    """
    run_dir = Path(run_dir)
    results = load_results(run_dir)
    narration = {**intent_descriptions(run_dir, results),
                 **load_narration(run_dir)}
    title, _ = _flow_heading(run_dir)

    body = [render_html(run_dir, results, embed=embed)
            .split("<main>", 1)[1].split("</main>", 1)[0]]
    for result in results:
        body.append(_recordings(run_dir, [result], embed))
        body.append(_variant(run_dir, result, embed, narration))
    return _page(title, body)


def render_one_html(run_dir: Path | str, result: dict[str, Any],
                    embed: bool = False) -> str:
    """One prompt run's page, written to stand on its own inside its folder."""
    run_dir = Path(run_dir)
    folder = folder_of(result)
    narration = {**intent_descriptions(run_dir, [result]),
                 **load_narration(run_dir)}
    title, description = _flow_heading(run_dir)
    subject = subject_of([result])

    body = [
        f'<h1>{_e(title)} — {_e(result["prompt_id"])}</h1>',
        f'<p class="lede">{_e(description)}</p>' if description else "",
        '<p class="meta">'
        + _subject_tags(subject)
        + f"<span><b>Run</b> {_e(run_dir.name)} · "
        f'{_e(result.get("backend", ""))}</span>'
        f"<span>{_status_pill(result.get('status'))}"
        f'{_e(result.get("duration_ms", 0))} ms</span>'
        f'<span><b>Started</b> {_e(result.get("timestamp", "?"))}</span></p>',
        '<p class="meta files screen-only">'
        + _group_links(run_dir, "Input",
                       ("flow.yaml", "prompts.yaml", "prompts.csv"), embed, "../")
        + _group_links(run_dir, "Output",
                       ("results.jsonl", "results.csv"), embed, "../")
        + "</p>",
        _recordings(run_dir, [result], embed, folder),
        _variant(run_dir, result, embed, narration, heading=False, base=folder),
    ]
    return _page(f"{title} — {result['prompt_id']}", body)


def _group_links(run_dir: Path, label: str, names: tuple[str, ...],
                 embed: bool, prefix: str = "") -> str:
    found = [n for n in names if (run_dir / n).exists()]
    if not found:
        return ""
    return (f"<span><b>{label}</b> "
            + " ".join(_file_link(run_dir, n, embed, prefix) for n in found)
            + "</span>")


def _page(title: str, body: list[str]) -> str:
    body = list(body) + [
        "<footer>Generated by understudy. Screenshots are the run's own "
        "captures; response text is whatever the flow's <code>read</code> step "
        "extracted.</footer>"
    ]
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)} — transcript</title><style>{STYLE}</style>"
        f"<script>{THEME_SCRIPT}</script></head>"
        f"<body><main>{''.join(body)}</main>"
        f"<script>{CODEC_SCRIPT}</script></body></html>\n"
    )


def write_html(
    run_dir: Path | str, embed: bool = False, filename: str = "transcript.html"
) -> Path:
    """The run's index page, and one page per prompt run beside it."""
    run_dir = Path(run_dir)
    results = load_results(run_dir)
    for result in results:
        folder = run_dir / folder_of(result)
        if not folder.is_dir():
            continue
        (folder / filename).write_text(
            render_one_html(run_dir, result, embed=embed), encoding="utf-8")
    path = run_dir / filename
    path.write_text(render_html(run_dir, results, embed=embed), encoding="utf-8")
    return path
