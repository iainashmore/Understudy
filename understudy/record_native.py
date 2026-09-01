"""Record a click path against a Windows application, and write it as a flow.

Do the thing once -- click into the panel, type a question, press Enter -- and
this writes the flow that does it again with a different question.

    understudy record --title "3DEXPERIENCE" --process 3DEXPERIENCE.exe \
        --name leo-basics --out examples

Nothing is read from the accessibility tree and nothing from a DOM. A click is
recorded as a crop of the window around the point that was clicked, and the
reply is read back with OCR, because that is all a CAD application offers.

While recording:

    click and type          recorded
    ctrl+alt+s              stop, and write the flow (or press Stop in the app)

Every interaction saves the whole window as well as the crop around the
pointer, so what was clicked can be worked out afterwards without going back
to the machine. The area a reply appears in is not asked for: it is whatever
changed on screen between the last thing done and stopping.

The mouse is not intercepted -- every click goes through to the application as
usual, and what is captured is a copy.

This module is the half that needs a real desktop. Everything it decides is
decided in understudy/recorder.py, which does not.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from understudy.recorder import Recorder
from understudy.vision import changed_region

STOP = "ctrl+alt+s"

#: Below this, what changed is a caret or a hover highlight rather than an
#: answer. OCR over a 4x10 sliver returns an empty string and the run says
#: nothing about why -- which is exactly what the first real recording did.
MIN_REGION = 40

#: How many raw events to keep for diagnosis. Enough to see the shape of one
#: click and a few keystrokes.
SEEN_LIMIT = 40

MODIFIERS = {"lcontrol", "rcontrol", "lmenu", "rmenu", "lshift", "rshift",
             "lwin", "rwin"}


class Session:
    """The recording, and the state a hook needs to keep.

    Kept apart from the hook itself so the sequencing -- what a click means
    while a region is being marked, what a modifier does -- is ordinary code
    that can be tested without a desktop.
    """

    def __init__(self, recorder: Recorder, shot, origin) -> None:
        self.recorder = recorder
        self.shot = shot          # () -> the window as an array, right now
        self.origin = origin      # () -> the window's top-left on the desktop
        self.read_region: dict[str, int] | None = None
        self.stopped = threading.Event()
        self.held: set[str] = set()
        #: The Win32 thread the hook is installed on. A message loop only
        #: accepts a quit message posted to its own thread, so stopping from
        #: anywhere else -- a button in the app, on another thread -- needs
        #: this rather than the hook object.
        self.thread_id = 0
        #: Counted so a recording can say what it is capturing while it runs.
        #: "Nothing was recorded" and "nothing reached the hook" are different
        #: failures with different fixes, and they look identical afterwards.
        self.counts = {"events": 0, "clicks": 0, "keys": 0}
        #: The first few raw events, as whatever attributes they turned out to
        #: have. The one part of this that cannot be tested off a desktop is
        #: the shape pywinauto hands over, so a recording that captures
        #: nothing keeps the evidence rather than needing to be done again.
        self.seen: list[dict[str, Any]] = []
        #: The hook, once installed. Stopping needs both halves: unhooking,
        #: which any thread may do, and waking the message loop, which only a
        #: message posted to its own thread will.
        self.hook = None
        #: Called with each anchor as it is captured. A recording used to hold
        #: everything in memory until it stopped, so a session that was killed
        #: -- or whose stop did not work -- lost the lot.
        self.save = None
        #: The window as it was at the last thing the person did. Diffed
        #: against the window at stop, which is where the reply landed.
        self.last_screen = None

    # -- what the hooks call --------------------------------------------------

    def note(self, event) -> None:
        """Every event the hook produced, counted, and the first few kept."""
        self.counts["events"] += 1
        if len(self.seen) < SEEN_LIMIT:
            self.seen.append({
                name: str(getattr(event, name))[:80]
                for name in dir(event)
                if not name.startswith("_") and not callable(getattr(event, name, None))
            })

    def click(self, x: int, y: int) -> None:
        self.counts["clicks"] += 1
        image = self.shot()
        left, top = self.origin()
        name = self.recorder.click(x, y, image, (left, top))
        if name:
            # Only a click that was part of the recording moves the baseline
            # the reply is measured against. The click that reaches the Stop
            # button is in the browser, by which time the answer is already on
            # screen -- and taking that as "before" made the reply region come
            # out empty for everybody who stopped from the app.
            self.last_screen = image
            if self.save:
                self.save(self.recorder.anchors[-1])
        # Both coordinates, because the difference between them is the whole
        # of what can go wrong here. On a monitor placed left of the primary
        # the screen coordinate is negative and the window one is not, and a
        # window origin that failed to read looks exactly like a click that
        # missed.
        where = f"screen ({x}, {y}) -> window ({x - left}, {y - top})"
        print(f"  click {where} -> {name}" if name
              else f"  click {where}: outside the window, ignored")

    def text(self, characters: str) -> None:
        self.counts["keys"] += 1
        self.recorder.text(characters)

    def key(self, name: str) -> None:
        before = len(self.recorder.steps)
        self.recorder.key(name)
        if len(self.recorder.steps) > before:
            # A key that meant something -- usually the Enter that sends the
            # question. The window as it is now is the "before" the reply is
            # measured against.
            self.last_screen = self.shot()

    # -- hotkeys --------------------------------------------------------------

    def hotkey(self, combination: str) -> bool:
        """True if this was a hotkey, and so not part of the recording."""
        if combination == STOP:
            self.stopped.set()
            return True
        return False

    def finish(self) -> None:
        """Work out where the reply appeared, from what changed.

        Nobody is asked to draw a box. Between the last thing the person did
        -- almost always pressing Enter -- and stopping, the only part of the
        window that changes much is the part the answer arrived in.
        """
        if self.last_screen is None:
            return
        region = changed_region(self.last_screen, self.shot())
        if region is None:
            print("nothing on screen changed after the last step, so there is "
                  "no reply region to read. Wait for the answer before "
                  "stopping.")
            return
        if region["width"] < MIN_REGION or region["height"] < MIN_REGION:
            print(f"only a {region['width']}x{region['height']} patch changed, "
                  f"which is a caret rather than an answer. No read step "
                  f"written.")
            return
        self.read_region = region
        print(f"reply region: {region['width']}x{region['height']} at "
              f"({region['x']}, {region['y']})")


def stop(session: Session) -> bool:
    """Stop a recording from outside its own thread.

    The hotkey works because it arrives on the hook's thread, where calling
    the hook's own stop is enough. A button in the app is on a different
    thread entirely, and PostQuitMessage posts to whichever thread calls it --
    so it would quit the web server's loop and leave the hook running.

    Returns whether the message went anywhere. The event is set either way, so
    the recording still stops at the next click or keystroke if this fails.
    """
    session.stopped.set()
    # Unhook first, from whichever thread: the hook handle belongs to the
    # process, and this is what makes the loop's own condition false.
    if session.hook is not None:
        try:
            session.hook.stop()
        except Exception:
            pass
    if not session.thread_id or sys.platform != "win32":
        return False
    # Then wake it. A message loop blocked in GetMessage does not notice that
    # a flag changed; it notices a message, and only one posted to its own
    # thread.
    import ctypes

    WM_QUIT = 0x0012
    return bool(ctypes.windll.user32.PostThreadMessageW(
        session.thread_id, WM_QUIT, 0, 0))


def problems_with(session: Session) -> list[str]:
    """What is wrong with what was just recorded, in the order it matters.

    A recording can succeed at capturing and still produce a flow that proves
    nothing, and the difference is not visible in a folder full of files. It
    is visible here.
    """
    found = []
    if not session.recorder.anchors:
        found.append(
            "no clicks inside the window were recorded, so the flow has "
            "nothing to aim at. Press Record first, then click into the "
            "application."
        )
    if not session.read_region:
        found.append(
            "no reply region was found, so this flow drives the application "
            "and records nothing. Ask the question, wait for the whole answer "
            "to arrive, and only then stop."
        )
    found.extend(session.recorder.warnings)
    return found


def dispatch(session: Session, event) -> None:
    """One pywinauto hook event, normalised.

    Defensive about attribute names on purpose: this is the one part that
    cannot be exercised off a real desktop, so it reads what it can find
    rather than insisting on a shape.
    """
    session.note(event)
    kind = getattr(event, "event_type", "")
    key = str(getattr(event, "current_key", "") or "")

    if key in ("LButton", "RButton", "MButton"):
        if kind == "key down" and key == "LButton":
            session.click(int(getattr(event, "mouse_x", 0)),
                          int(getattr(event, "mouse_y", 0)))
        return

    if kind == "key down":
        session.held.add(key.lower())
        combination = _combination(session.held, key)
        if combination and session.hotkey(combination):
            return
        if key.lower() in MODIFIERS:
            return
        if session.held & {"lcontrol", "rcontrol", "lmenu", "rmenu"}:
            return                      # a shortcut, not something typed
        if len(key) == 1:
            session.text(key)
        else:
            session.key(key)
    elif kind == "key up":
        session.held.discard(key.lower())


def _combination(held: set[str], key: str) -> str:
    control = bool(held & {"lcontrol", "rcontrol"})
    alt = bool(held & {"lmenu", "rmenu"})
    if control and alt and len(key) == 1:
        return f"ctrl+alt+{key.lower()}"
    return ""


def name_clicks(session: Session) -> None:
    """Ask what each click landed on, so the flow reads like a description.

    Skipped without credentials rather than failing: a recording that names
    its targets target_1 is worse to read but works exactly as well, and
    losing the recording because a key was missing would be absurd.
    """
    from understudy.narrate import describe_click
    from understudy.resolvers import credentials_available

    if not credentials_available():
        print("no Anthropic credentials, so the targets keep their numbers")
        return
    for anchor in session.recorder.anchors:
        try:
            anchor.described = describe_click(anchor.screen, anchor.point)
        except Exception as exc:
            print(f"  ({anchor.name}: {type(exc).__name__}: {exc})")
            continue
        if anchor.described:
            print(f"  {anchor.name}: {anchor.described}")


def saver(out_dir: Path, name: str):
    """Write each anchor and its screen the moment it is captured.

    Everything used to be held until the recording stopped, so a session that
    was killed -- or whose stop did not work -- lost every click. The flow
    still needs the end, but the evidence does not.
    """
    anchors = out_dir / "anchors" / name
    screens = anchors / "screens"

    def save(anchor) -> None:
        screens.mkdir(parents=True, exist_ok=True)
        (anchors / f"{anchor.name}.png").write_bytes(anchor.png)
        if anchor.screen:
            (screens / f"{anchor.name}.png").write_bytes(anchor.screen)

    return save


def write(session: Session, name: str, title: str, out_dir: Path,
          app_config: dict[str, Any]) -> Path:
    """The flow, and its anchors beside it where the flow looks for them."""
    import json

    import yaml

    document = session.recorder.flow(name, title, app_config,
                                     read_region=session.read_region)
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = out_dir / "anchors" / name
    anchors.mkdir(parents=True, exist_ok=True)
    for filename, png in session.recorder.anchor_files().items():
        (anchors / filename).write_bytes(png)
    for filename, png in session.recorder.screen_files().items():
        destination = anchors / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(png)

    (anchors / "recording.json").write_text(
        json.dumps(session.recorder.manifest(), indent=2), encoding="utf-8")

    path = out_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def readable(name: str) -> str:
    """`leo-basics` -> `Leo basics`. The window's own title was being used,
    which says what was driven rather than what the flow does."""
    return name.replace("-", " ").replace("_", " ").strip().capitalize() or name


def app_config_for(title: str, process: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"window_title_pattern": title}
    if process:
        config["process"] = process
    # Visible travel and human typing, so a recording of the replay is
    # followable by somebody who was not watching it happen.
    config["mouse"] = {"mode": "human", "speed": 2200}
    config["typing"] = {"mode": "human", "cps": 22}
    return config


def finish_and_write(session: Session, name: str, title: str,
                     process: str | None, out_dir: Path) -> Path:
    """Work out the reply region, name the clicks, and write the flow.

    Called from the recording's own thread when the hook loop ends, and from
    whoever pressed Stop when it does not. Either way somebody gets their
    recording.
    """
    session.finish()
    name_clicks(session)
    return write(session, name, readable(name), out_dir,
                 app_config_for(title, process))


def record(title: str, process: str | None, name: str, out_dir: Path,
           session_holder: dict[str, Any] | None = None) -> Path:
    """Attach, hook, and block until stopped. Windows only.

    `session_holder` is handed the session as soon as there is one, so that
    whatever started this on another thread has something to stop.
    """
    from harness.image import load_rgb
    from understudy.drivers.native import NativeDriver

    driver = NativeDriver()
    driver.start({"window_title_pattern": title, "process": process,
                  "mouse": {"mode": "instant"}})
    for note in driver.warnings:
        print(f"note: {note}")

    def shot():
        return load_rgb(driver.screenshot())

    def origin():
        # Re-read every click: the window can be dragged mid-recording, and a
        # stale origin puts every anchor after it in the wrong place. Reading
        # the rectangle is cheap; refresh() walks the whole tree and is not.
        geometry = driver.where()
        return (geometry.left, geometry.top) if geometry else (0, 0)

    session = Session(Recorder(), shot, origin)
    session.save = saver(out_dir, name)
    if session_holder is not None:
        session_holder["session"] = session
    placement = driver.geometry
    if placement:
        # Said once, up front. A window on a monitor left of the primary has
        # negative screen coordinates, which is expected and is also what a
        # broken origin looks like.
        print(f"  window at ({placement.left}, {placement.top}), "
              f"{placement.width}x{placement.height}"
              + (f" on {placement.monitor}" if placement.monitor else ""))
    print(f"recording against {title!r}. Do the thing once, wait for the "
          f"reply, then {STOP} to stop.")

    from pywinauto.win32_hooks import Hook

    import ctypes

    session.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    hook = Hook()
    session.hook = hook

    def handle(event) -> None:
        try:
            dispatch(session, event)
        except Exception as exc:          # a hook that throws stops recording
            print(f"  (ignored: {type(exc).__name__}: {exc})")
        if session.stopped.is_set():
            hook.stop()

    hook.handler = handle
    hook.hook(keyboard=True, mouse=True)

    path = finish_and_write(session, name, title, process, out_dir)
    anchors = out_dir / "anchors" / name
    print(f"\n{len(session.recorder.anchors)} anchor(s), "
          f"{len(session.recorder.steps)} step(s), "
          f"{session.counts['keys']} keystroke(s)")
    print(f"  flow      {path}")
    print(f"  anchors   {anchors}")
    print(f"  screens   {anchors / 'screens'}   (the whole window, per click)")
    for problem in problems_with(session):
        print(f"\n! {problem}")
    if not session.read_region:
        print("no reply region was found, so the flow drives the application "
              "and records nothing. Re-record, and wait for the answer to "
              "finish arriving before stopping.")
    driver.stop()
    return path


def available() -> str:
    """Why recording cannot run here, or an empty string if it can."""
    if sys.platform != "win32":
        return "recording drives Windows hooks, so it only runs on Windows"
    try:
        import pywinauto.win32_hooks  # noqa: F401
    except Exception as exc:
        return f"recording needs pywinauto: {exc}"
    return ""
