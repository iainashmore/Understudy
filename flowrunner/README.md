# FlowRunner

Replays a fixed UI click-path many times, varying only the prompt text, and
records the response plus screenshots at every step. No agent decides what to
click; no scoring. The human authors the steps, the tool repeats them reliably.

## Status

| # | Component | State |
|---|-----------|-------|
| 1 | Flow schema + parser | done |
| 2 | Web driver (Playwright) — launch **and CDP attach** | done |
| 3 | Runner + output layout | done |
| 4 | `wait_for_stable` (text and pixel modes, region-scoped) | done |
| — | Visual anchoring + OCR reads, for surfaces with no DOM | done |
| — | Agent fallback rung (vision model, cached) | done |
| 5 | Web recorder (`playwright codegen` wrapper) | not started |
| 6 | Native driver (UIAutomation) | matching done and tested; pywinauto contact unexercised |
| 7 | Native recorder (Win32 hooks + UIA) | blocked on the probe results |

Everything marked done is tested offline against `fixtures/chat_app`, a fake
streaming chat app with switchable behaviour — streaming, instant, stalling,
erroring, a cookie banner, and a confirm dialog rendered either inline or
portalled to `<body>`.

```bash
python3 -m flowrunner.cli ui                     # the authoring and replay UI
python3 -m flowrunner.cli validate examples/fixture_chat.yaml examples/prompts.yaml
python3 -m flowrunner.cli run      examples/fixture_chat.yaml examples/prompts.yaml --csv
python3 -m flowrunner.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
python3 -m flowrunner.cli report   runs/2026-08-24T14-32-00
```

## The UI

`flowrunner ui --workspace <folder>` serves a local page on 127.0.0.1 that
covers the working loop: open a flow and a prompts file, edit either by hand,
save or save-as, validate, replay with live progress, read the output with the
screenshots inline, and view the report.

Built on the standard library — no web framework. This has to run on the machine
that has CATIA on it, which is not necessarily a machine where installing things
is quick or permitted.

The workspace folder is the boundary: the UI will not read or write outside it,
and run output under `runs/` is never offered as a source file. Runs execute on
a worker thread and stream progress as server-sent events, so a fifty-variant
sweep is not a blank screen.

**Recording is the one part not wired up.** Which recorder to build — a picker
injected over CDP, or Win32 hooks plus a UIAutomation lookup, or cropping
anchors from screenshots — is decided by what `tools/probe_native.py` reports
against the real application. The Record tab says so rather than pretending
otherwise.

## Recording a run

```bash
python3 -m flowrunner.cli run flow.yaml --record
```

One video per variant, in the variant's folder, linked from the report.
**H.264 mp4, no audio track**, from both backends — Playwright writes WebM, so
the web driver transcodes and drops the intermediate.

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
only VP8/WebM, so if that is the one found, the web driver keeps the WebM and
says so in the results rather than failing the sweep. `apt install ffmpeg` (or
any real build on PATH) is what gets mp4.

## Typing

Text goes in **one keystroke at a time**, at about 12 characters a second —
roughly 145 wpm, a fast typist rather than a machine. That is not only for the
recording: an application that enables its send button on the first character,
autocompletes, or validates as you go only behaves the way it does for a person
if it sees the keys arrive separately. Filling a field sets a value and skips
all of it.

Pauses are longer after a full stop than after a comma, and each keystroke's
timing is **seeded on the text**, so the same prompt always types at the same
rhythm. A very long prompt is compressed to fit `max_total_s` (20s by default)
rather than truncated.

```yaml
typing: {mode: human, cps: 12, variance: 0.35, max_total_s: 20}
```

`mode: instant` fills the field, for unattended sweeps where nobody is watching
the video. A single step can override the pacing with `delay_ms`.

On Windows the characters are escaped before they reach `send_keys`, which
otherwise reads `^ % + ~ ( ) { } [ ]` as instructions: a prompt containing `~`
would press Enter and submit itself halfway through being typed.

## Pointer movement

The pointer travels to its target rather than teleporting, so a screen recording
shows a hand moving to a control instead of controls being pressed by nothing.
Eased, slightly arced, with a beat on the target before the click, and parked
inside the window at the start of a run.

The wobble is **deterministic** — seeded on the two endpoints, so the same move
always draws the same path. A tool whose value is that only the prompt varies
between runs cannot introduce randomness into the pointer, even the cosmetic
kind. `mouse: {mode: instant}` turns the animation off for unattended sweeps.

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

Every run writes `report.md` beside its screenshots: what was run, a summary
table, prompts against responses for scanning, then a section per variant with
the prompt, the response (or the response *pixels* where there was no text), the
screenshots inline, and a collapsible step table showing how each target
resolved.

Paths are relative, so the run folder can be zipped, committed or attached and
still renders. `--embed-report` inlines the images as data URIs when it has to
travel as a single file. `flowrunner report <run_dir>` rebuilds one for a past
run.

## Driving an embedded web view (WebView2 / CEF)

An assistant panel inside a desktop application is usually Chromium. If the host
exposes a debugging port, the panel is drivable as an ordinary page — **full DOM
access, exact text reads, real selectors, no OCR** — and the native problem
mostly evaporates.

```yaml
target_app:
  web:
    cdp_url: "http://127.0.0.1:9222"
    page_title_pattern: "Assistant*"     # a host may run several web views
```

Turning the port on is a host-side setting:

```
WebView2   set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
CEF        --remote-debugging-port=9222 on the host executable
```

Check `http://127.0.0.1:9222/json` — if it lists pages, this works. When
attached, the driver never closes the browser (it belongs to the host) and
level-2 reset is refused for the same reason.

Run `python3 tools/probe_native.py --title "*CATIA*"` on the target machine to
find out which route is available. It checks for CDP endpoints, dumps the
UIAutomation tree, screenshots the window, and prints a verdict with ready-made
flow config. `--watch 30` reports the focused element once a second while you
click around — the substitute for the right-click inspect an embedded view
doesn't give you.

## Targets drift, so targets are ranked

A target is a list of strategies, tried in order, most stable first. A bare
string is still a CSS selector, as in the original spec.

```yaml
targets:
  send_button:
    intent: submits the message          # guidance for an agent resolver later
    web:
      - testid: send                     # survives everything but deletion
      - role: button
        name: Send                       # survives restyling and re-parenting
      - css: "form > div:nth-child(2) > button"   # brittle, last resort
```

Two rules:

- **Ambiguity is not resolution.** A strategy matching several elements is
  skipped rather than silently taking the first. Use `nth` to choose on purpose.
- **Fallbacks are reported.** Each step records which strategy won, and each
  result carries `used_fallbacks`. A run limping along on strategy 3 tells you
  the UI moved *before* it breaks.

Verified: with `data-testid` stripped from every element, the fixture flow still
completes on role and accessible name and reports all four fallbacks.

Steps take `optional: true` for dialogs that sometimes don't appear, and a flow
can list `interstitials` — cookie banners and similar — dismissed before every
step without being part of the measured path.

## When the surface exposes nothing

The worst case: no DOM, no accessibility tree, no element picking — a
custom-drawn CAD toolbar, a canvas, an embedded view with debugging off. There
is still a path, exercised end to end in `examples/cad_blind.yaml` against
`fixtures/cad_app`.

**Find controls by their picture.** An anchor is a small image of the control,
captured when the flow was authored; at replay it is located in the *current*
screenshot and the click point is derived from where it is now.

```yaml
targets:
  prompt_box:
    web:
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
    web:
      - testid: dlg-done          # tried first
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

Text stability on a painted response is worse than useless: it settles
instantly on the empty string and reports success having captured nothing. That
silent failure is pinned by a test.

A timeout is a step status, never a crash. The run still produces its row, its
screenshots, and whatever text had arrived.

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

`flowrunner/native_match.py` — **tested**, against synthetic trees shaped like a
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

The rules match the web driver's deliberately — ranked strategies, ambiguity is
not resolution, `nth` for a deliberate choice — because a flow that behaves
differently per backend is worse than no flow.

`flowrunner/drivers/native.py` — **unexercised**. The adapter: walk the tree,
click, type, read, screenshot. Reading follows a chain, because a CAD
application answers "what does this say" three different ways: the UIA value
(exact), then the clipboard via select-all-and-copy (exact, and often the only
thing that works on a legacy custom-drawn panel), then OCR (approximate, last
resort). The visual-anchor and agent rungs work exactly as on the web, because
both operate on pixels.

What is testable offline is tested: the tree walk against wrappers that throw
and containers that refuse to enumerate, the walk bounds, the anchor-point
arithmetic, and that every unavailable path fails with a message saying what to
install or do.

Containerising native CAD clients is not viable: Windows containers have no
desktop session and no usable GPU path. Windows VMs with vGPU are the route, and
that is already reset level 3 in the design.
