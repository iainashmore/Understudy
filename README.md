# Understudy

An understudy performs the same part, the same way, as many times as it is
asked to. This one replays a fixed path through an application's interface,
varying only the prompt text, and records what came back: the response, a
screenshot of every step, and a video of the whole thing.

It exists to answer a question that is otherwise very tedious to answer — *did
the assistant's behaviour change?* — by holding everything else still. The
click path is authored once. After that only the prompt varies, so a difference
in the output is a difference in the assistant.

It drives the application the way a person does: the real mouse pointer travels
to a control and clicks it, text is typed one keystroke at a time at about 145
words a minute. Not for show — an application that enables its send button on
the first character only behaves the way it does for a person if it sees the
keys arrive separately. It also means a screen recording of a session looks
like somebody using the software, because that is what it is.

**[What it does, in detail →](understudy/README.md)**

---

## Install it

Download the installer from the
[latest release](https://github.com/iainashmore/understudy/releases) and run it.
Nothing else is needed — no Python, no `pip`, no browser download. It carries
its own Python, its own browser, and its own ffmpeg, because the machines this
runs on are often behind a corporate network where fetching anything at first
run is exactly what will not work.

Windows for now. Start it from the Start menu; it opens a window.

> The installer is not code-signed yet, so Windows SmartScreen will warn on
> first run, and a managed workstation may block it outright. Choose **More
> info → Run anyway**, or ask whoever manages the machine to allow it.

If the window does not appear, the application is still a local web server:
open **http://127.0.0.1:8765** in any browser and it will be there.

## Run it from the repository

For development, or on a machine where you would rather not install anything.

```bash
git clone https://github.com/iainashmore/understudy
cd understudy

python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[web,agent,ocr]"                      # add ,native on Windows
python3 -m playwright install chromium                 # only for web targets

python3 -m understudy.cli ui --workspace ~/flows
```

That opens the same interface at http://127.0.0.1:8765. `--workspace` is the
folder holding your flows and their runs; it can be a git checkout, in which
case the Repository tab can commit and push from inside the app.

Without the UI at all:

```bash
python3 -m understudy.cli run    examples/fixture_chat.yaml --out runs/today --record
python3 -m understudy.cli repo   --workspace ~/flows
python3 -m understudy.cli publish runs/today --workspace ~/flows
```

**ffmpeg** is optional and only needed to record; without one, runs still
produce transcripts and screenshots and say why there is no video. `apt install
ffmpeg`, `brew install ffmpeg`, or [a Windows build](https://www.gyan.dev/ffmpeg/builds/).

## Build the installer yourself

PyInstaller cannot cross-compile, so a Windows executable has to be built on
Windows. `.github/workflows/desktop.yml` does that on a runner and uploads the
result; see [desktop/README.md](desktop/README.md) for building it by hand and
for why the shell is Electron.

## Also here

`harness/` is a separate, paused project — a harness for measuring at which
level of abstraction an agent can complete a task. See
[harness/README.md](harness/README.md).
