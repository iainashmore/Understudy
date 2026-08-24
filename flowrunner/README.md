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
| 4 | `wait_for_stable` (text and pixel modes) | done |
| 5 | Web recorder (`playwright codegen` wrapper) | not started |
| 6 | Native driver (UIAutomation) | blocked on a Windows machine |
| 7 | Native recorder (Win32 hooks + UIA) | blocked on a Windows machine |

Everything marked done is tested offline against `fixtures/chat_app`, a fake
streaming chat app with switchable behaviour — streaming, instant, stalling,
erroring, a cookie banner, and a confirm dialog rendered either inline or
portalled to `<body>`.

```bash
python3 -m flowrunner.cli validate examples/fixture_chat.yaml examples/prompts.yaml
python3 -m flowrunner.cli run      examples/fixture_chat.yaml examples/prompts.yaml --csv
python3 -m flowrunner.cli run flow.yaml prompts.csv --only baseline,terse --repeat 3
```

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
text at all: a CAD viewport, a custom-drawn panel. Comparison is blurred and
tolerance-based, not exact, because a caret blink would otherwise mean "still
changing" forever.

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

## Not built yet

The native (UIAutomation) driver. Writing an untested Win32 hook layer would be
worse than not shipping one, so it waits on a machine to exercise it against.
The pure part — matching strategies against element descriptors — can be written
and unit-tested ahead of that; only the thin pywinauto adapter needs hardware.

Containerising native CAD clients is not viable: Windows containers have no
desktop session and no usable GPU path. Windows VMs with vGPU are the route, and
that is already reset level 3 in the design.
