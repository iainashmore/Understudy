"""The transcript as a web page.

Same content as the markdown, different reader. Two things need it: the viewer
inside the tool, where the video has to play and the screenshots have to be
visible without leaving the app, and the PDF, which is printed from this page by
a browser.

Written from the results rather than by converting the markdown -- a markdown
renderer is a dependency and a parser is a source of bugs, and neither buys
anything when the data is right there.

`embed=True` inlines the images so the file stands alone. Video is never
inlined: a base64 mp4 doubles a file that is already the largest thing in the
run, and the point of the standalone copy is to be emailable.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from understudy.narrate import load_narration
from understudy.transcript import (
    STATUS_MARK,
    _flow_heading,
    _label_of,
    action_numbers,
    describe_step,
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
footer { margin-top:40px; padding-top:14px; border-top:1px solid var(--line);
         color:var(--dim); font-size:12px; }
a { color:var(--accent); }
@media print {
  body { padding:0; font-size:10.5pt; }
  h2 { padding-top:0; border-top:none; break-after:avoid; }
  /* Only a variant starts a fresh page. Breaking before every heading turns a
     one-variant run into six mostly-empty sheets. */
  h2.variant { break-before:page; }
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


def _img(run_dir: Path, relative: str, embed: bool) -> str:
    path = run_dir / relative
    if not path.exists():
        return f'<p class="note">missing: {_e(relative)}</p>'
    if embed:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        source = f"data:image/png;base64,{data}"
    else:
        source = relative
    return f'<img src="{source}" alt="{_e(relative)}" loading="lazy">'


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
        '<p class="lede">The same path on every variant. Quote these numbers '
        "when referring to a step.</p>"
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
        "<th>variant</th><th>status</th><th>duration</th><th>response</th>"
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
              narration: dict[str, str]) -> str:
    """The run read step by step: what was done, then what it looked like.

    A gallery of screenshots at the bottom and a table of steps below that
    makes the reader do the joining. Every question anyone asks of a transcript
    -- what did step 4 do, what did the screen look like after it, what did the
    assistant actually reply -- is a question about one step.
    """
    entries = timeline(result, narration)
    if not entries:
        return ""

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

        if entry.typed:
            body.append(f'<div class="typed"><span class="tag">typed</span>'
                        f"<pre>{_e(entry.typed)}</pre></div>")
        if entry.keys:
            body.append(f'<div class="typed"><span class="tag">keys</span>'
                        f"<pre>{_e(entry.keys)}</pre></div>")

        for name, text in entry.reads:
            body.append(f'<div class="typed"><span class="tag">{_e(name)}</span>'
                        f"<pre>{_e(text) or '<em>nothing captured</em>'}</pre></div>")
        for name, relative in entry.read_images:
            body.append(f"<figure class=\"region\">{_img(run_dir, relative, embed)}"
                        f"<figcaption>the pixels <code>{_e(name)}</code> was read "
                        f"from</figcaption></figure>")

        for relative in entry.screenshots:
            body.append(f"<figure>{_img(run_dir, relative, embed)}"
                        f"<figcaption>{_e(_label_of(relative))}</figcaption></figure>")

        body.append("</li>")
        out.append("".join(body))
    out.append("</ol>")

    orphans = unclaimed_screenshots(result, entries)
    if orphans:
        out.append("<h3>Other screenshots</h3>")
        for relative in orphans:
            out.append(f"<figure>{_img(run_dir, relative, embed)}<figcaption>"
                       f"{_e(_label_of(relative))}</figcaption></figure>")
    return "".join(out)


def _variant(run_dir: Path, result: dict[str, Any], embed: bool,
             narration: dict[str, str]) -> str:
    title = result["prompt_id"]
    if result.get("repeat_index"):
        title += f" (repeat {result['repeat_index'] + 1})"

    out = [f'<h2 class="variant" id="{_e(_slug(result))}">{_e(title)}</h2>',
           f'<p class="meta">{_status_pill(result.get("status"))}'
           f'<span>{_e(result.get("duration_ms", 0))} ms</span></p>']
    if result.get("error"):
        out.append(f'<p class="note error">{_e(result["error"])}</p>')
    if result.get("pointer_note"):
        out.append(f'<p class="note">Clicks were synthetic: '
                   f'{_e(result["pointer_note"])}. Nothing filming the screen '
                   f'would have seen the cursor move.</p>')

    variables = dict(result.get("variables") or {})
    prompt = (variables.pop("prompt", "") or "").strip()
    out.append(f"<h3>Prompt</h3><pre>{_e(prompt)}</pre>")
    if variables:
        items = "".join(f"<li><code>{_e(k)}</code> = {_e(v)}</li>"
                        for k, v in sorted(variables.items()))
        out.append(f"<h3>Other variables</h3><ul>{items}</ul>")

    response = (result.get("response") or "").strip()
    out.append("<h3>Response</h3>")
    out.append(f"<pre>{_e(response)}</pre>" if response else
               '<p class="note">No text captured. The response was recorded as '
               'pixels; the region image is below.</p>')

    if result.get("recording"):
        source = _e(result["recording"])
        out.append(
            f'<h3>Recording</h3><video controls preload="metadata" src="{source}">'
            f"</video>"
            f'<p class="no-codec note" hidden>This browser cannot decode H.264. '
            f"Chrome, Edge and any desktop player can; some Chromium builds ship "
            f"without it. The link below opens the file.</p>"
            f'<p class="lede"><a href="{source}">{source}</a></p>'
        )
    elif result.get("recording_error"):
        out.append(f'<p class="note">No recording: '
                   f'{_e(result["recording_error"])}</p>')

    entries = timeline(result, narration)
    shown = {name for entry in entries for name, _ in entry.read_images}
    for name, relative in (result.get("read_images") or {}).items():
        if name not in shown:
            out.append(f'<figure class="region">{_img(run_dir, relative, embed)}'
                       f"<figcaption>recorded <code>{_e(name)}</code> region"
                       f"</figcaption></figure>")

    out.append(_timeline(run_dir, result, embed, narration))
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
    narration = load_narration(run_dir)
    title, description = _flow_heading(run_dir)

    files = [name for name in
             ("flow.yaml", "prompts.yaml", "results.jsonl", "results.csv",
              "transcript.md")
             if (run_dir / name).exists()]
    links = " ".join(f'<a href="{_e(name)}">{_e(name)}</a>' for name in files)

    body = [
        f"<h1>{_e(title)}</h1>",
        f'<p class="lede">{_e(description)}</p>' if description else "",
        '<p class="meta">'
        f"<span><b>Run</b> {_e(run_dir.name)}</span>"
        f"<span><b>Backend</b> {_e(summary.backend)}</span>"
        f"<span><b>Variants</b> {summary.variants} — {summary.passed} ok, "
        f"{summary.failed} failed, {summary.timed_out} timed out</span>"
        + (f"<span><b>Started</b> {_e(results[0].get('timestamp', '?'))}</span>"
           if results else "")
        + (f"<span><b>Under test</b> {_e(subject_of(results).summary())}</span>"
           if subject_of(results).recorded else "")
        + "</p>",
        f'<p class="meta screen-only">{links}</p>' if links else "",
        _summary_table(results),
        _steps_list(results, narration),
        "<h2>Prompts and responses</h2>",
        '<div class="wrap"><table><thead><tr><th>variant</th><th>prompt</th>'
        "<th>response</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_e(r['prompt_id'])}</td><td>{_e(r.get('prompt', ''))}</td>"
            f"<td>{_e(r.get('response') or '')}</td></tr>" for r in results
        )
        + "</tbody></table></div>",
    ]
    body += [_variant(run_dir, result, embed, narration) for result in results]
    body.append(
        "<footer>Generated by understudy. Screenshots are the run's own "
        "captures; response text is whatever the flow's <code>read</code> step "
        "extracted.</footer>"
    )

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
    run_dir = Path(run_dir)
    path = run_dir / filename
    path.write_text(render_html(run_dir, embed=embed), encoding="utf-8")
    return path
