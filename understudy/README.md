# Understudy

Replays a fixed UI click-path, varying only the prompt, and records the
response plus screenshots and video at every step. No agent decides what to
click. Moves the real cursor and types at a human speed, so the output is a
recording of the application being used rather than driven.

## Status

| Component | State |
|---|---|
| Flow schema, parser, embedded prompts | done |
| Native driver — attach by title and process | done, against 3DEXPERIENCE |
| **Recorder** — clicks and typing to a flow | done, hooks unexercised off Windows |
| Runner, transcripts (md, html, pdf), video | done |
| `wait_for_stable`, text and pixel, region-scoped | done |
| Visual anchoring + OCR, for surfaces that expose nothing | done |
| Agent fallback rung (vision model, cached) | done |
| Real cursor and human typing | done |
| Git backend, publish, GitHub/GitLab | done |
| Subject fields and release comparison | done |
| Desktop installer (Electron + PyInstaller) | done |

There used to be a web driver, and a CAD fixture and a chat fixture to exercise
it. Both are gone: this drives Windows applications, and a second backend that
was never going to be used was a second backend to keep working. What that cost
is offline coverage of the runner, which is now kept by a fake application
implementing the driver protocol — no browser, no desktop, and the suite runs in
twenty seconds rather than seven minutes.

```bash
understudy record --title "*3DEXPERIENCE*" --name leo-basics
understudy ui --workspace ~/flows          # the authoring and replay app
understudy validate examples/leo_3dx.yaml
understudy run      examples/leo_3dx.yaml --record --capture-steps
understudy run      flow.yaml --only baseline,terse --repeat 3
understudy compare  runs/a runs/b --out comparisons/a-vs-b
understudy transcript runs/today --pdf
```

## The UI

`understudy ui --workspace <folder>` serves a local page on 127.0.0.1. Tabs:
**Flow** (edit, validate, duplicate, delete), **Run** (backend, repeats, agent
mode, what was under test), **Output**, **Transcript** (rendered, with the video
playing, exports to md/html/pdf), **Compare**, **Record**, **Repository**,
**Credentials**.

The workspace is a local folder, a GitHub repository or a GitLab one; the last
few are remembered.

## Recording a run

```bash
python3 -m understudy.cli run flow.yaml --record
```

One video per variant, in the variant's folder, linked from the transcript.
**H.264 mp4, no audio track**, from both backends — Playwright writes WebM, so
the recorder transcodes and drops the intermediate.

**Native** uses ffmpeg. It captures the *monitor region* by default, not the
window rectangle: menus, tooltips and modal dialogs routinely fall outside the
window, and a recording that clips those off is a recording of the wrong thing.
Since the application under test has a screen to itself, that screen is the
right frame. `record: {mode: window}` captures the window instead, and
`framerate` is configurable. ffmpeg is found on PATH or from Playwright's
bundled copy; a missing one is a note in the results, never a lost sweep.

**Web** uses Playwright, which records the page and needs no external tool. A
video belongs to a browser context, so recording implies a **fresh context per
variant** — level-2 isolation whether or not it was asked for. That is a real
behaviour change, which is why it only happens when recording is on. Recording
is unavailable when attached over CDP: the context belongs to the host
application, and closing it would take their panel down.

Camtasia remains the better tool for a polished capture. This exists so a run's
video is filed with its own results, rather than being something to line up
afterwards.

Writing mp4 needs an ffmpeg with an H.264 encoder. Playwright's bundled copy has
only VP8/WebM, so if that is the one found, the recorder keeps the WebM and
says so in the results rather than failing the sweep. `apt install ffmpeg` (or
any real build on PATH) is what gets mp4.

## Typing

**One keystroke at a time**, about 12 characters a second (~145 wpm). Not
only for the recording: an application that enables its send button on the
first character, autocompletes, or validates as you go only behaves the way it
does for a person if the keys arrive separately.

Pauses are longer after a full stop than a comma, and timing is **seeded on the
text**, so the same prompt always types at the same rhythm. A long prompt is
compressed to fit `max_total_s` (20s), not truncated.

```yaml
typing: {mode: human, cps: 12, variance: 0.35, max_total_s: 20}
```

`mode: instant` fills the field, for unattended sweeps where nobody is watching
the video. A single step can override the pacing with `delay_ms`.

On Windows the characters are escaped before they reach `send_keys`, which
otherwise reads `^ % + ~ ( ) { } [ ]` as instructions: a prompt containing `~`
would press Enter and submit itself halfway through being typed.

## Pointer movement

The pointer travels rather than teleporting: eased, slightly arced, with a
beat on the target before the click, and parked inside the window at the start.

The wobble is **deterministic**, seeded on the two endpoints — a tool whose
value is that only the prompt varies cannot introduce randomness anywhere,
including cosmetically. `mouse: {mode: instant}` turns it off.

### It is the real cursor

By default the **operating system's cursor** moves and clicks, on both
backends. Playwright's mouse API sends CDP input events: the page reacts as it
would to a person, but they are delivered inside the renderer, so the desktop
arrow never moves. Anything filming the screen — Camtasia included — records a
panel operating itself. For an assistant panel inside a desktop application
that is the only kind of recording there is.

So clicks go through `SetCursorPos`/`SendInput` on Windows, XTest on X11. The
element is still found through CDP; only the click comes from the desktop.

It degrades rather than failing. Headless has no window for a cursor to be over,
so an OS click would land on whatever else is at those coordinates; that and a
machine with no reachable cursor fall back to synthetic clicks, and the reason
is recorded as `pointer_note` on every variant. `mouse: {input: cdp}` asks for
synthetic clicks deliberately.

Page-to-screen translation is the part that goes wrong: viewport corner, plus
browser chrome, times display scaling. On an unscaled single monitor at the
origin every term is zero or one, which is why getting it wrong survives
testing. `tools/probe_native.py` prints the numbers so the mapping can be
checked on a real machine.

## Multiple monitors

Everything a flow declares — anchors, regions — is **window-relative**, so it
means the same thing wherever the window is. Screen coordinates are derived at
click time by translating through the window rectangle.

That translation is the bug worth knowing about: an anchor match is in window
coordinates, the mouse is driven in virtual-desktop coordinates, and on a single
monitor with the window at the origin the two are identical — which is exactly
why getting it wrong survives testing. On a second monitor they differ by the
window origin, and on a monitor placed left of or above the primary that origin
is negative.

The process declares per-monitor DPI awareness before reading any coordinate. A
flow can name the monitor it expects (`target_app.native.monitor`) and the run
refuses to start on another, because anchors do not survive a scale change. A
resize or rescale mid-run is warned about; a move is not, since anchors are
re-located in each run's own screenshot.

## Reports

Every run writes `transcript.md` beside its screenshots: what was run, a summary
table, prompts against responses for scanning, then a section per variant with
the prompt, the response (or the response *pixels* where there was no text), the
screenshots inline, and a collapsible step table showing how each target
resolved.

Paths are relative, so the run folder can be zipped, committed or attached and
still renders. `--embed-transcript` inlines the images as data URIs when it has to
travel as a single file. `understudy transcript <run_dir>` rebuilds one for a past
run.

## What the panel is made of does not change the approach

An assistant panel inside a desktop application is usually Chromium, and for a
while this drove one over a debugging port -- full DOM access, exact reads, no
OCR. That route is gone, because against the application this exists for it was
not there.

3DEXPERIENCE gives 17 UIAutomation nodes for a 1936x1096 window: the frame, and
nothing inside it. Its WebViews report `about:blank` over an empty document, so
there was nothing to attach to either. Both rungs were absent exactly where they
were needed, and a rung that is missing when it matters is not a rung.

So there is one route: pictures for the controls, OCR for the text. Run
`python3 tools/probe_native.py --title "*CATIA*"` against a new application to
see what its accessibility tree offers -- usually little -- and
`--watch 30` reports the focused element once a second while you click around,
which is the substitute for the right-click inspect an embedded view does not
give you.

## Targets drift, so targets are ranked

A target is a list of strategies, tried in order, most stable first. A bare
string is still a CSS selector, as in the original spec.

```yaml
targets:
  send_button:
    intent: submits the message          # guidance for an agent resolver later
    native:
      - automation_id: send              # survives everything but deletion
      - control_type: Button
        name: Send                       # survives re-layout and re-parenting
      - image: anchors/send.png          # survives having neither, and little else
```

Two rules:

- **Ambiguity is not resolution.** A strategy matching several elements is
  skipped rather than silently taking the first. Use `nth` to choose on purpose.
- **Fallbacks are reported.** Each step records which strategy won, and each
  result carries `used_fallbacks`. A run limping along on strategy 3 tells you
  the UI moved *before* it breaks.

Verified against Notepad with UIAutomation switched off entirely: the flow
completes on pictures alone, and the OCR read matches what was typed.

Steps take `optional: true` for dialogs that sometimes don't appear, and a flow
can list `interstitials` — cookie banners and similar — dismissed before every
step without being part of the measured path.

## When the surface exposes nothing

The worst case: no DOM, no accessibility tree, no element picking — a
custom-drawn CAD toolbar, a canvas, an embedded view with debugging off. There
is still a path, and the one this tool takes: `examples/leo_3dx.yaml` drives
3DEXPERIENCE with nothing else available.

**Find controls by their picture.** An anchor is a small image of the control,
captured when the flow was authored; at replay it is located in the *current*
screenshot and the click point is derived from where it is now.

```yaml
targets:
  prompt_box:
    native:
      - image: anchors/assistant_label.png
        threshold: 0.95
        region: {x: 800, y: 40, width: 300, height: 200}
        offset: {dx: 0, dy: 49}
```

That is not the same as storing coordinates, which is what the core rule
forbids: a stored coordinate is wrong the moment the window moves, an anchor is
re-located every run. It still fails on a theme change or a DPI change, so it
sits below anything semantic.

Matching is normalised cross-correlation **across colour channels**, not on
luma. Toolbar icons are routinely distinguished only by hue, and a red glyph and
a blue one on the same grey chrome have nearly identical luminance — a
grayscale match cannot tell them apart and will click the wrong tool. Several
matches means the anchor does not identify one control, and the same rule
applies as everywhere else: ambiguity is not resolution.

**Anchor on something that does not change.** This one cost a real bug. An
anchor taken from an empty textbox stops matching the moment there is text in
it, so every prompt variant after the first fails to find the box. Anchor on the
static label beside it and use `offset` to reach the control.

**Record the pixels, transcribe them if you can.**

```yaml
- action: read
  mode: ocr
  region: {x: 811, y: 176, width: 279, height: 241}
  store_as: response
```

The region image is always saved and referenced in the result row; OCR text is
added when `pytesseract` and a tesseract binary are present. A missing OCR
engine is reported as a step error, never as an empty response — an empty string
meaning "could not read" is indistinguishable from one meaning "the assistant
said nothing", and the two demand opposite reactions.

## The agent rung

The bottom of the ladder, below visual anchoring: when no selector matches and
no anchor is found, ask a vision model where the control is, guided by the
target's `intent`.

```yaml
targets:
  dialog_done:
    intent: the button that commits the rename and closes the dialog
    native:
      - automation_id: dlg-done   # tried first
      - image: anchors/done.png   # then this
      - agent: true               # then, and only then, the model
```

```bash
--agent off        # default: agent rungs are skipped entirely
--agent fallback   # ask the model only when everything deterministic has failed
--agent only       # ignore deterministic strategies; measure the agent alone
--learned-dir DIR  # where found anchors are cached (default <flow dir>/learned)
```

**It is off by default, and that is the point.** This tool's value is that only
the prompt varies between runs. A model choosing where to click adds variance on
top of the variance you are trying to measure, and a sweep where the agent
improvised differently on one variant is not a comparison any more.

**What the agent finds is cached as an anchor.** The crop it pointed at is
written to `learned/<target>.png` and every later run matches it
deterministically — including runs with the agent switched back off:

```
flow has drifted: the CSS selector no longer matches anything

  run 1 (--agent fallback)     via=agent           model calls=1  confidence 0.93
  run 2 (--agent fallback)     via=learned-anchor  model calls=0  cached, score 1.000
  run 3 (--agent off)          via=learned-anchor  model calls=0  cached, score 1.000
```

Resilience when the UI moves, without the per-run non-determinism. A cached
anchor that stops matching is discarded and the model asked again, so a stale
one is never carried forever.

**Every resolution records how it was reached.** Steps carry
`via: selector | anchor | learned-anchor | agent`, and each result row carries
`agent_resolutions` and `learned_anchors`. A run that needed the model is a
different kind of result from one that did not, and the results say so rather
than leaving you to infer it.

Other details: the model is `claude-opus-5` with adaptive thinking and a strict
tool for the bounding box; a `found: false` or low-confidence answer resolves to
nothing rather than a guess, because a wrong click is worse than a clean
failure; screenshots are downscaled before sending and coordinates scaled back,
so a 4K window returns coordinates in its own space. `learned/` is a directory
of PNGs plus an index — inspect it, delete from it, or promote an entry into the
flow by hand.

## Knowing when a response is complete

No fixed sleeps, ever. `wait_for_stable` polls until the content stops changing:

```yaml
- action: wait_for_stable
  target: response_area
  until_hidden: stop_button      # a completion signal beats text stability
  stable_for_ms: 1500
  timeout_ms: 120000
  mode: text                     # or: pixels
```

`until_hidden` takes precedence but still requires the settle window afterwards
— text routinely lags the spinner by a frame, and stopping the instant the
spinner clears truncates the last token.

`mode: pixels` polls a screenshot instead of text, for surfaces that expose no
text at all: a CAD viewport, a custom-drawn panel. Comparison is blurred,
downscaled and tolerance-based, not exact, because a caret blink would otherwise
mean "still changing" forever.

**Scope it with `region`.** A CAD viewport that animates continuously means
whole-window stability *never* settles — measured, not assumed: the fixture's
spinning viewport makes a full-window wait hit its timeout every time, while the
same wait scoped to the reply rectangle settles in about 1.5s.

**The wait has two conditions.** An assistant that thinks for eight seconds
before printing leaves its panel unchanged for those eight seconds, so
"unchanged for a second" is satisfied immediately and the run captures an empty
answer as a pass. The watched area must first *change*, then settle. A response
that never arrives times out as `never-started`, which sends you somewhere
different from one that never stopped. `require_change: false` restores the
naive behaviour.

**A completion signal beats both.** `until_hidden: stop_button` waits for the
control that exists only while a reply generates, then still serves out the
settle window — text lags the spinner and stopping on the signal truncates the
last token. Where the application offers one, name it: it is the only reliable
way to tell a finished answer from a long pause mid-stream.

A timeout is a step status, never a crash. The run still produces its row, its
screenshots, and whatever text had arrived.

## Keeping it in a repository

The workspace can be a git checkout, and the UI grows a Repository tab: branch,
what has changed, commit, push, pull, and a Publish button for a run.

This is **git, not a GitHub integration**. The tool drives the `git` binary you
already have, so it behaves identically against GitHub, GitLab, a self-hosted
GitLab or anything else that speaks git — and inherits your credential helper,
your proxy and your SSH keys, none of which would work if this were a REST
client. The provider only matters for the two things that genuinely differ:
where a personal access token comes from, and what URL opens a file in a browser.

```bash
python3 -m understudy.cli repo    --workspace ~/flows
python3 -m understudy.cli publish runs/2026-09-01T14-22 --workspace ~/flows
```

### Which repository

Yours. The Repository tab offers three sources:

- **Local folder** — the default, and it needs nothing else. Flows and runs
  work exactly the same; only committing and publishing want a repository.
- **GitHub** — `owner/repo`, cloned into a folder you name.
- **GitLab** — `group/project`, which may nest, plus the hostname of your
  instance. That field is the reason the two are asked separately: a company
  GitLab is not gitlab.com, and a single "clone URL" box quietly sends people
  to the wrong one.

The fields the other two would ask for stay visible but dimmed and disabled,
so the choice is legible and the panel does not jump about as you click between
them. Whichever you pick, the whole UI follows, and the last few workspaces are
remembered so restarting does not mean setting it up again.

**Not this one.** Understudy refuses to write to a checkout of its own
source: the most likely `.` on the day somebody first runs it is the folder they
cloned to get it, and publishing there would push their CAD screenshots to
whoever owns that repository. A `.understudy-workspace` file overrides it.

### Commit messages

Written from what is being committed — `Update rename-and-ask`, `Publish run
2026-09-01T14-22` — and editable. Not a placeholder: somebody reads this in a
log six months from now looking for the day the click path changed.

**Only the files you tick are staged** — never `git add -A` in a repository
somebody is also working in. `pull` is `--ff-only` for the same reason.

### What gets published

A run is mostly evidence — transcript, screenshots, results — plus one video
per variant, the largest thing in it and the one git handles worst. A year of
daily runs would put gigabytes of mp4 into a history that cannot be trimmed
without rewriting it.

So **video is left out by default and linked instead**, with a
`recordings-not-committed.txt` beside the transcript saying where it went. A
full CAD run costs about 400KB. `--include-video` overrides it; consider Git
LFS first.

Anything unusually large is skipped whatever its type and reported rather than
dropped quietly, and `credentials.json` is never committed by any path through
this code.

### Tokens

Nothing is needed for an SSH remote or a working credential helper. Where a
token is needed it is stored **per host** in the same owner-only file as the API
key:

- it is never returned to the browser, only a masked form;
- a token saved for one host is never sent to another — a GitHub token must not
  reach a self-hosted GitLab;
- when git needs it, it is passed as a header for the length of one command. It
  is never written into `.git/config` or into a remote URL, either of which
  would leave it sitting in the checkout;
- anything that looks like a token is scrubbed out of git's own output before
  it is shown, logged or raised.

## Paths in a flow

Anchor images resolve **relative to the flow file**, so a flow directory can be
moved, copied into a run folder, or checked out somewhere else intact. A flow
that hard-codes an absolute path only runs on the machine it was written on.

```yaml
targets:
  prompt_box:
    native:
      - image: anchors/leo/prompt_box.png
```

Anchors themselves are not committed: they belong to a window size, a theme and
a DPI, and one cut on your monitor is a near-miss on anyone else's.

## What was under test

A reply is only comparable against another reply if you know what produced
each one. A transcript recording an answer from LEO but not **which** LEO is
evidence of nothing.

Two places. The flow declares what it is *meant* to run against:

```yaml
subject:
  app: CATIA V5
  model: LEO
```

The run records what it *actually* ran against:

```bash
understudy run leo.yaml --app-version "R33 SP1" --model-version "2027x FD02"
```

The run wins. **Remembered per flow**, so a service pack is typed once and
later runs carry it — needing to edit YAML to record a patch level is how that
field ends up stale, which is worse than empty.

## Comparing releases

The payoff, and the reason for all of the above:

```bash
understudy compare runs/r32 runs/r33 --out comparisons/r32-vs-r33
```

One row per prompt, one column per run, labelled with what was under test
rather than a timestamp.

```
  runs/r32                   CATIA V5 R32 SP4 · LEO 2026x FD01
  runs/r33                   CATIA V5 R33 · LEO 2027x FD02

2 same, 1 changed

! baseline               changed
      CATIA V5 R32 SP4 · LEO 2026x FD01  Echo: Summarise this in one paragraph.
      CATIA V5 R33 · LEO 2027x FD02      Echo: I can summarise that for you …
```

**What counts as changed is deliberately quiet.** Whitespace, reflowed lines,
a trailing full stop and case are not behaviour changes; a comparison that cries
wolf gets ignored. Close-but-not-identical is `~` (reworded) rather than `!`
(moved). Changed rows sort to the top.

`--changed-only` prints just those rows and exits non-zero when there are any,
which is what you want from a scheduled job.

### Stepping through both runs

A **stepper** under the table: the same step of each run side by side, with
prev/next, a slider and arrow keys.

Divergence is often visual and several steps before the answer — a dialog that
opened elsewhere, a field that did not clear. Differing answers tell you *that*
something changed; two pictures of step 4 tell you *where*. A run missing that
step says so rather than lining step 3 up against step 4.

In the app it is the **Compare** tab: pick a *before* and an *after* from two
lists — labelled by what each was run against, not by timestamp — and press
Compare. The result renders in place, links to each run's full transcript, and
exports as markdown or a standalone page. It is written into `comparisons/` in
the workspace, so it can be committed next to the runs it is about.

## The transcript

`transcript.md` and `transcript.html`, written into the run folder beside the
screenshots they link. The page is what the tool's Transcript tab shows, with
the video playing in place; `--pdf` (or the Export button) prints it through
Chromium.

**It reads step by step.** Each user action gets a number, the text it typed,
the reply it produced, and the screenshot of what the screen looked like after.
Every question anyone asks of a transcript is a question about one step.

Numbers cover **user actions only** — clicks, typing, keys. Capturing, waiting
and reading are the tool's own housekeeping, and numbering those alongside would
make "step 4" mean two things. They are all still there in a collapsed table.
The numbering is stable across variants, which is why a number is worth quoting.

Anything the timeline cannot place is still shown under *Other screenshots*.

## Output

```
runs/2026-08-24T14-32-00/
  flow.yaml          # as executed -- both files will have changed by next month
  prompts.yaml
  results.jsonl      # streamed, so an interrupted run is still readable
  results.csv        # with --csv
  baseline/01-before-prompt.png  02-after-typing.png  03-after-response.png
```

A failing variant never stops the sweep, and the moment it broke is captured as
`NN-FAILED-<action>.png`. Repeats get `baseline-01/`, `baseline-02/` — the
spec's layout collides otherwise.

## The native backend

Split so that everything decidable is decided in tested code, and only the
pywinauto contact waits for hardware.

`understudy/native_match.py` — **tested**, against synthetic trees shaped like a
CAD window. It owns which strategy means which element, what happens when
several match, and how a Win32 name folds:

```
&File            -> file          (mnemonic stripped)
Save\tCtrl+S     -> save          (accelerator dropped)
Properties...    -> properties    (trailing ellipsis dropped)
Search && Replace-> search & replace
```

So a flow says `name: Properties` and matches `Properties...`, rather than
making the author copy the punctuation. AutomationIds are compared exactly —
they are developer-chosen identifiers and folding them would merge controls
someone deliberately kept apart.

`path` is a **subsequence** of the ancestor chain, not the full thing: a real
tree is full of anonymous wrappers, and a flow naming every level breaks the
first time a layout gains a container. `path: [Window, Filters]` means "inside
something called Filters, inside a Window".

The rules are the same throughout — ranked strategies, ambiguity is
not resolution, `nth` for a deliberate choice — because a flow that behaves
differently per backend is worse than no flow.

`understudy/drivers/native.py` — **unexercised**. The adapter: walk the tree,
click, type, read, screenshot. Reading follows a chain, because a CAD
application answers "what does this say" three different ways: the UIA value
(exact), then the clipboard via select-all-and-copy (exact, and often the only
thing that works on a legacy custom-drawn panel), then OCR (approximate, last
resort). The visual-anchor and agent rungs need no accessibility at all, because
both operate on pixels.

What is testable offline is tested: the tree walk against wrappers that throw
and containers that refuse to enumerate, the walk bounds, the anchor-point
arithmetic, and that every unavailable path fails with a message saying what to
install or do.

Containerising native CAD clients is not viable: Windows containers have no
desktop session and no usable GPU path. Windows VMs with vGPU are the route, and
that is already reset level 3 in the design.
