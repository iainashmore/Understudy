# Understudy

[![Download the installer](https://img.shields.io/badge/download-Windows%20installer-4f9dd9?style=for-the-badge)](https://github.com/iainashmore/understudy/releases/latest/download/Understudy-0.1.0-win64-setup.exe)
[![All releases](https://img.shields.io/badge/all-releases-6b7684?style=for-the-badge)](https://github.com/iainashmore/understudy/releases)

An understudy performs the same part, the same way, as many times as it is
asked to. This one replays a fixed path through an application's interface,
varying only the prompt text, and records what came back.

## What it is for

Assistants embedded in desktop software — LEO in 3DEXPERIENCE, a copilot in a
CAD package — change. Prompts get tuned, models get swapped, an update lands.
The question *did the behaviour change?* is easy to ask and miserable to answer
by hand: it means driving the same twelve clicks again, typing a slightly
different prompt, and remembering what the answer looked like last month.

Understudy holds everything else still. The click path is authored once. After
that only the prompt varies, so a difference in the output is a difference in
the assistant, not in how somebody happened to click that day.

What comes out of a run is evidence: the response, a screenshot after every
step, a video of the whole thing, and a transcript that pairs them up. All of
it committable, so last month's answer is still there to compare against.

It drives the application the way a person does. The **real mouse pointer**
travels to a control and clicks it; text is typed **one keystroke at a time**
at about 145 words a minute. That is not for show — an application that enables
its send button on the first character only behaves the way it does for a
person if it sees the keys arrive separately. It also means a screen recording
of a session looks like somebody using the software, because that is what it is.

Where the interface has no accessible names to grab — a CAD viewport, a
custom-drawn panel, an embedded web view with no debugging port — it finds
controls by **matching pictures of them**, and reads answers back off the
pixels. That path is proven end to end against a fixture that is deliberately
as hostile as the real thing.

**[The detail: targets, waiting, anchors, the agent fallback →](understudy/README.md)**

## Install it

**[Download the Windows installer](https://github.com/iainashmore/understudy/releases/latest)** and run it.

Nothing else is needed — no Python, no `pip`, no browser download. It carries
its own Python, its own Chromium and its own ffmpeg, because the machines this
runs on are usually behind a corporate network where fetching anything at first
run is exactly what will not work.

Start it from the Start menu; it opens a window.

> **SmartScreen will warn on first run.** The installer is not code-signed yet:
> choose **More info → Run anyway**. A managed workstation may block it
> outright, in which case whoever administers the machine has to allow it.
> Worth sorting out before the day you need it.

If the window does not appear, the application is still a local web server:
open **http://127.0.0.1:8765** in any browser and it will be there.

## Run it from the repository

For development, or on a machine where you would rather not install anything.

```bash
git clone https://github.com/iainashmore/understudy
cd understudy

python3 -m venv .venv
source .venv/bin/activate                    # Windows: .venv\Scripts\activate

pip install -e ".[web,agent,ocr]"            # add ,native on Windows
python3 -m playwright install chromium       # only for web targets

python3 -m understudy.cli ui --workspace ~/flows
```

That serves the same interface at <http://127.0.0.1:8765>. `--workspace` is the
folder holding your flows and their runs; make it a git checkout and the
Repository tab can commit and push from inside the app.

Without the UI at all:

```bash
python3 -m understudy.cli run     examples/fixture_chat.yaml --out runs/today --record
python3 -m understudy.cli transcript runs/today --pdf
python3 -m understudy.cli repo    --workspace ~/flows
python3 -m understudy.cli publish runs/today --workspace ~/flows
```

**ffmpeg** is optional and only needed to record. Without one, runs still
produce transcripts and screenshots and say why there is no video.
`apt install ffmpeg`, `brew install ffmpeg`, or
[a Windows build](https://www.gyan.dev/ffmpeg/builds/).

**Tests:** `python3 -m pytest tests/ -q` — 888 of them, no network required.

## Build the installer

PyInstaller cannot cross-compile, so a Windows executable has to be built on
Windows. [`.github/workflows/desktop.yml`](.github/workflows/desktop.yml) does
that on a runner.

| To get | Do this | You get |
|---|---|---|
| a development build | **Actions → desktop → Run workflow** | installer as an artifact, ~280MB, no Chromium |
| a release | push a `v*` tag | the release, with the full ~900MB installer attached |

It deliberately does not build on an ordinary push: a Windows runner bills at
double rate and emails on every failure.

By hand, on the platform you are targeting:

```bash
pip install ".[web,agent,ocr,native]" pyinstaller
python packaging/fetch_payload.py            # --skip-browsers for a small build
pyinstaller --noconfirm --clean --distpath dist packaging/understudy-server.spec
cd desktop && npm install && npm run dist
```

See [desktop/README.md](desktop/README.md) for what is in the bundle and why
the shell is Electron.

## What is not built yet

**The recorder.** Flows are written by hand today; there is a working example
to start from and the UI validates as you type. Recording a flow by clicking
through the application is the next feature, and which of two mechanisms it
needs depends on what `tools/probe_native.py` finds against the target
application.

## Also here

`harness/` is a separate, paused project — a harness for measuring at which
level of abstraction an agent can complete a task. See
[harness/README.md](harness/README.md).
