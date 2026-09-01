# Understudy

[![Download the installer](https://img.shields.io/badge/download-Windows%20installer-4f9dd9?style=for-the-badge)](https://github.com/iainashmore/understudy/releases/latest)
[![All releases](https://img.shields.io/badge/all-releases-6b7684?style=for-the-badge)](https://github.com/iainashmore/understudy/releases)

Replays a fixed path through an application's interface, varying only the
prompt, and records what came back.

For checking whether an embedded assistant — LEO in 3DEXPERIENCE, a CAD copilot
— still behaves the way it did last month. The click path is authored once;
after that only the prompt changes, so a difference in the output is a
difference in the assistant.

Each run produces a transcript: the response, a screenshot after every step, a
video, and which application and model version produced it. Compare two runs
and you get a row per prompt, a column per release, and a stepper to walk both
runs side by side.

- Moves the **real mouse pointer** and types **one key at a time**, so the
  application behaves as it does for a person — and a screen recording looks
  like one.
- Finds controls by **matching pictures** of them where there is nothing
  accessible to grab: a CAD viewport, a custom-drawn panel, an embedded web
  view with no debugging port.
- Keeps flows and their evidence in **git**, GitHub or GitLab.

**[Full documentation →](understudy/README.md)**

## Install

[Download the installer](https://github.com/iainashmore/understudy/releases/latest)
and run it. No Python, no pip, no browser download — it carries its own.
Windows only for now.

> Not code-signed yet, so SmartScreen warns on first run: **More info → Run
> anyway**. A managed workstation may block it outright.

If the window does not appear, open <http://127.0.0.1:8765> — it is a local
server.

## Run from source

```bash
git clone https://github.com/iainashmore/understudy && cd understudy
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[native,ocr,agent,pdf]"             # native needs Windows
python3 -m playwright install chromium                # web targets only

python3 -m understudy.cli ui --workspace ~/flows
```

| Command | |
|---|---|
| `understudy ui --workspace ~/flows` | the app, at <http://127.0.0.1:8765> |
| `understudy run flow.yaml --out runs/today --record` | one sweep |
| `understudy compare runs/a runs/b --out cmp` | two runs, side by side |
| `understudy transcript runs/today --pdf` | rebuild the transcript |
| `understudy publish runs/today` | commit it |

ffmpeg is optional, and only for recording. OCR — the fallback read for panels
that expose no accessibility tree — needs the tesseract binary as well as the
`ocr` extra (`choco install tesseract`); the installer carries both. Tests:
`pytest tests/ -q` (990, no network).

## Point a flow at a Windows application

A native flow attaches to a window that is already open. It does not know
where the application is installed and does not launch it; the window title is
the whole handle. That is not enough on its own for 3DEXPERIENCE, which runs
as a crowd of processes -- several own a top-level window and answer to the
same name, so a title glob can match six things.

Ask the machine what is open rather than guessing:

```
python tools/probe_native.py --list
```

Each line is a ready-to-paste `--title`, with the process that owns the window
and how big it is, largest visible first. The client fills a monitor; the
splash screen is 400x300 and the licensing helper has no pixels at all. In the
app, **Pick window…** on an open flow does the same thing and writes the
answer into the YAML.

Where the title alone still cannot separate two windows, name the executable:

```yaml
target_app:
  native:
    window_title_pattern: "*3DEXPERIENCE*"
    process: "CATIA.exe"
```

The driver takes the one visible match and says in the run's warnings what it
chose over. Two windows genuinely up at once is a refusal, not a guess --
replaying into the wrong open document is destructive -- and the error names
them both.

## Build the installer

PyInstaller cannot cross-compile, so Windows builds on Windows —
[`.github/workflows/desktop.yml`](.github/workflows/desktop.yml) does it.

| To get | Do |
|---|---|
| a development build, ~280MB | **Actions → desktop → Run workflow** |
| a release, ~900MB with Chromium | push a `v*` tag |

It does not build on ordinary pushes. See
[desktop/README.md](desktop/README.md) for building by hand.

## Record a flow

Do the thing once; the flow is what comes out.

```
understudy record --title "3DEXPERIENCE" --process 3DEXPERIENCE.exe --name leo-basics
```

or press **Record** in the app. Then, in the application:

Click, type, wait for the reply, then **Stop recording** in the app — or
**ctrl+alt+s**, which works without leaving the application you are recording.

Every click becomes a picture of what was clicked on, matched again at replay
time, and the whole window is kept beside it so what was clicked can be named
rather than guessed at. The longest thing typed becomes the prompt, which is
the point: the click path is fixed and the question is what varies. The area
to read back is not asked for -- it is whatever changed on screen while you
waited for the answer -- and it is read with OCR.

Nothing is read from the accessibility tree or from an embedded web view.
Against the 3DEXPERIENCE client both are dead ends -- 17 UIA nodes for the
whole window, and WebViews reporting an empty document -- which is what the
probe is for finding out.

Then replay it:

```
understudy run examples/leo-basics.yaml --record --capture-steps
```

Anchors are cut on the machine that recorded them and are not committed: they
belong to a window size, a theme and a DPI.

## Also here

`harness/` — a separate, paused project. See [harness/README.md](harness/README.md).
