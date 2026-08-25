# Understudy

An understudy performs the same part, the same way, as many times as it is
asked to. This one replays a fixed UI click-path, varying only the prompt text,
and records the response plus screenshots and video at every step. No agent
decides what to click; no scoring. A person authors the steps once, and the tool
repeats them exactly -- moving the real cursor and typing at a human speed, so
what comes out is a recording of the application being used rather than of it
being driven.

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
python3 -m understudy.cli ui                     # the authoring and replay UI
python3 -m understudy.cli validate examples/fixture_chat.yaml examples/prompts.yaml
python3 -m understudy.cli run      examples/fixture_chat.yaml examples/prompts.yaml --csv
python3 -m understudy.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
python3 -m understudy.cli transcript runs/2026-08-24T14-32-00
```

## The UI

`understudy ui --workspace <folder>` serves a local page on 127.0.0.1 that
covers the working loop: open a flow and a prompts file, edit either by hand,
save or save-as, validate, replay with live progress, read the output with the
screenshots inline, and view the transcript.

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
python3 -m understudy.cli run flow.yaml --record
```

One video per variant, in the variant's folder, linked from the transcript.
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

### It is the real cursor

By default the **operating system's cursor** is what moves and clicks, on both
backends. This matters more than it sounds. Playwright's own mouse API sends CDP
input events: the page reacts to them exactly as it reacts to a person — hover
states, focus, clicks — but they are delivered inside the renderer, so the arrow
drawn on the desktop never moves. Anything filming the screen from outside,
Camtasia included, records a panel operating itself while the cursor sits where
you left it. For an embedded web view inside a native application that is the
only kind of recording there is.

So `click` goes through `SetCursorPos` and `SendInput` on Windows, and XTest on
X11. The element is still found through CDP; only the click is delivered by the
desktop. The page cannot tell the difference, which is the point.

It degrades rather than failing. A headless browser has no window for a cursor
to be over, so an OS click would land on whatever else is at those coordinates —
headless falls back to synthetic clicks. So does a machine with no reachable
cursor. Either way the reason is recorded as `pointer_note` on every variant and
printed in the transcript, because a run whose pointer never moved looks broken
and should not be a mystery. `mouse: {input: cdp}` asks for synthetic clicks
deliberately.

The translation from page coordinates to screen coordinates is the part that
goes wrong: the viewport's corner on the desktop, plus the browser chrome above
it, times the display scaling. On an unscaled single monitor with the window at
the origin every term is zero or one, which is exactly why getting it wrong
survives testing. `tools/probe_native.py` prints the numbers it computes so the
mapping can be checked in a few seconds on a real machine.

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

**The wait has two conditions, and the first is the one that is easy to
forget.** An assistant that thinks for eight seconds before printing anything
leaves its panel perfectly unchanged for those eight seconds, so "unchanged for
a second" is satisfied immediately -- and the run captures an empty answer and
calls it a pass. So the watched area must first *change*, and only then start
settling. A response that never arrives is a timeout that says
`never-started`, which is a different thing to look into than one that never
stopped. `require_change: false` restores the naive behaviour for a step that is
waiting for something to stop moving rather than start arriving.

That also turns the nastiest case loud: text stability on a painted response
used to settle instantly on the empty string and report success having captured
nothing. Both behaviours are pinned by tests.

**A completion signal beats both.** `until_hidden: stop_button` waits for the
control that only exists while a reply is generating to disappear -- then still
serves out the settle window, because the text lags the spinner by a frame or
two and stopping on the signal truncates the last token. Where the application
offers such a control, name it: it is the only thing that reliably distinguishes
a finished answer from a long pause mid-stream.

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
where a personal access token comes from, and what URL opens a file on the web.

```bash
python3 -m understudy.cli repo    --workspace ~/flows
python3 -m understudy.cli publish runs/2026-09-01T14-22 --workspace ~/flows
```

### Which repository

Yours, and you pick how. The Repository tab offers three sources:

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

**Not this one.** Understudy refuses to write to a checkout of its own source,
because the most likely `.` on the day somebody first runs the tool is the
folder they cloned to get it, and publishing there would commit their CAD
screenshots into a source repository and push them to whoever owns it. The
refusal explains itself and says how to point somewhere sensible. If flows
genuinely do belong in that checkout, a `.understudy-workspace` file says so.

### Commit messages

Written for you from what is actually being committed — `Update rename-and-ask`,
`Publish run 2026-09-01T14-22`, `Update 3 flows` — and editable before you
commit. Not a placeholder like "Update files": somebody reads this in a log six
months from now, trying to find the day the click path changed.

**Only the files you tick are staged.** Never `git add -A`: this is a
repository somebody is also working in, and a tool that stages everything will
one day commit something they were halfway through, and they will not forgive
it. `pull` is `--ff-only` for the same reason — a merge commit invented by a
background tool is a surprise nobody wants.

### What gets published

A run is mostly evidence: a transcript, the screenshots it links, the results a
machine reads — and one video per variant, which is by far the largest thing in
it and the one thing git handles worst. Committing everything is the obvious
choice and the wrong one: a year of daily regression runs puts gigabytes of mp4
into a history that cannot be trimmed without rewriting it, and every clone pays
for it forever.

So **video is left out by default and linked instead**, and a
`recordings-not-committed.txt` goes in beside the transcript saying where it
went — a transcript linking a recording that is not there looks like a broken
link rather than a decision somebody made. A full CAD run costs about 400KB
that way. `--include-video` overrides it for the run worth keeping whole;
consider Git LFS first.

Anything unusually large is skipped whatever its type and reported rather than
dropped quietly, and `credentials.json` is never committed by any path through
this code.

### Tokens

Nothing is needed for an SSH remote or a working credential helper, which is
most setups. Where a token is needed it is stored **per host** in the same
owner-only file as the API key, and:

- it is never returned to the browser, only a masked form;
- a token saved for one host is never sent to another — a GitHub token must not
  reach a self-hosted GitLab;
- when git needs it, it is passed as a header for the length of one command. It
  is never written into `.git/config` or into a remote URL, either of which
  would leave it sitting in the checkout;
- anything that looks like a token is scrubbed out of git's own output before
  it is shown, logged or raised.

## Paths in a flow

Anchor images and `target_app.web.url` both resolve **relative to the flow
file**. A flow that hard-codes an absolute path only runs on the machine it was
written on: a checkout in a different directory, a colleague's laptop, or a
repository that has been renamed breaks every one of them. Anything with a
scheme — `https://`, or a `file:///` you wrote deliberately — is left alone.

```yaml
target_app:
  web:
    url: "../fixtures/cad_app/index.html?viewport=spin"
```

## What was under test

The tool answers *did the behaviour change?*, and an answer is only comparable
against another answer if you know what produced each one. A transcript that
records a reply from LEO but not **which** LEO is evidence of nothing: six
months later the model has been swapped twice and the CAD package has had three
service packs, and the difference could be any of them.

So a run records it, in two places. The flow declares what it is *meant* to run
against, because that belongs with the flow and rarely changes:

```yaml
subject:
  app: CATIA V5
  model: LEO
```

and the run records what it *actually* ran against, because that changes every
time somebody installs a patch:

```bash
understudy run leo.yaml --app-version "R33 SP1" --model-version "2027x FD02"
```

The run wins where it says anything. **It is remembered between runs**, per
flow, so a service pack is typed once and every later run of that flow carries
it — having to edit a YAML file to record a patch level is how the field ends
up stale and lying, which is worse than empty.

## Comparing releases

The payoff, and the reason for all of the above:

```bash
understudy compare runs/r32 runs/r33 --out comparisons/r32-vs-r33
```

One row per prompt, one column per run, and the columns are labelled with what
was under test rather than with a timestamp — `CATIA V5 R33 · LEO 2027x` is what
a reader needs; `2026-09-01T14-22` is not.

```
  runs/r32                   CATIA V5 R32 SP4 · LEO 2026x FD01
  runs/r33                   CATIA V5 R33 · LEO 2027x FD02

2 same, 1 changed

! baseline               changed
      CATIA V5 R32 SP4 · LEO 2026x FD01  Echo: Summarise this in one paragraph.
      CATIA V5 R33 · LEO 2027x FD02      Echo: I can summarise that for you …
```

**What counts as changed is deliberately quiet.** Whitespace, reflowed lines, a
trailing full stop and letter case are not behaviour changes, and a comparison
that cries wolf over them gets ignored — which makes it worse than no
comparison at all. A reply that is close but not identical is marked `~`
(reworded) rather than `!` (moved). Changed rows sort to the top, because
burying the two that moved among ninety that did not is the other way a
comparison stops being read.

`--changed-only` prints just those rows and exits non-zero when there are any,
which is what you want from a scheduled job.

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

**It reads step by step.** Each user action gets a number, the text it typed if
it typed one, the reply it produced if it produced one, and the screenshot of
what the screen looked like afterwards. A gallery of images at the bottom and a
table of steps below that makes the reader do the joining, and every question
anyone asks of a transcript — what did step 4 do, what did it look like after,
what did the assistant actually say — is a question about one step.

The numbers cover **user actions only**: clicks, typing, keys. Capturing,
waiting and reading are things the tool does around them, and numbering those
alongside would make "step 4" mean different things to the person reading and
the person who ran it. They are all still there, in a collapsed table at the end
of each variant. The numbering is stable across variants, because every variant
walks the same path — which is the premise of the tool and the reason a number
is worth quoting.

Anything the timeline cannot place against a step is still shown, under *Other
screenshots*. A picture that exists on disk but appears nowhere is the kind of
gap nobody notices until they are looking for the one image that would have
explained something.

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

The rules match the web driver's deliberately — ranked strategies, ambiguity is
not resolution, `nth` for a deliberate choice — because a flow that behaves
differently per backend is worse than no flow.

`understudy/drivers/native.py` — **unexercised**. The adapter: walk the tree,
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
