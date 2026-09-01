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
    ctrl+alt+r              mark the region a reply appears in: press it, then
                            click the top-left and bottom-right of the region
    ctrl+alt+s              stop, and write the flow

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

STOP = "ctrl+alt+s"
MARK = "ctrl+alt+r"

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
        self.marking: list[tuple[int, int]] = []
        self.read_region: dict[str, int] | None = None
        self.stopped = threading.Event()
        self.held: set[str] = set()
        self._marking_active = False

    # -- what the hooks call --------------------------------------------------

    def click(self, x: int, y: int) -> None:
        if self._marking_active:
            self.marking.append((x, y))
            print(f"  corner {len(self.marking)}: ({x}, {y})")
            if len(self.marking) == 2:
                self._finish_marking()
            return
        name = self.recorder.click(x, y, self.shot(), self.origin())
        print(f"  click ({x}, {y}) -> {name}")

    def text(self, characters: str) -> None:
        self.recorder.text(characters)

    def key(self, name: str) -> None:
        self.recorder.key(name)

    # -- hotkeys --------------------------------------------------------------

    def hotkey(self, combination: str) -> bool:
        """True if this was a hotkey, and so not part of the recording."""
        if combination == STOP:
            self.stopped.set()
            return True
        if combination == MARK:
            self._marking_active = True
            self.marking = []
            print("  marking a read region: click its top-left, then its "
                  "bottom-right")
            return True
        return False

    def _finish_marking(self) -> None:
        (x1, y1), (x2, y2) = self.marking
        left, top = self.origin()
        self.read_region = {
            "x": min(x1, x2) - left, "y": min(y1, y2) - top,
            "width": abs(x2 - x1), "height": abs(y2 - y1),
        }
        self._marking_active = False
        print(f"  read region: {self.read_region}")


def dispatch(session: Session, event) -> None:
    """One pywinauto hook event, normalised.

    Defensive about attribute names on purpose: this is the one part that
    cannot be exercised off a real desktop, so it reads what it can find
    rather than insisting on a shape.
    """
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


def write(session: Session, name: str, title: str, out_dir: Path,
          app_config: dict[str, Any]) -> Path:
    """The flow, and its anchors beside it where the flow looks for them."""
    import yaml

    document = session.recorder.flow(name, title, app_config,
                                     read_region=session.read_region)
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = out_dir / "anchors" / name
    anchors.mkdir(parents=True, exist_ok=True)
    for filename, png in session.recorder.anchor_files().items():
        (anchors / filename).write_bytes(png)

    path = out_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def app_config_for(title: str, process: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"window_title_pattern": title}
    if process:
        config["process"] = process
    # Visible travel and human typing, so a recording of the replay is
    # followable by somebody who was not watching it happen.
    config["mouse"] = {"mode": "human", "speed": 2200}
    config["typing"] = {"mode": "human", "cps": 22}
    return config


def record(title: str, process: str | None, name: str, out_dir: Path) -> Path:
    """Attach, hook, and block until the stop hotkey. Windows only."""
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
        driver.refresh()
        geometry = driver.geometry
        return (geometry.left, geometry.top) if geometry else (0, 0)

    session = Session(Recorder(), shot, origin)
    print(f"recording against {title!r}. {MARK} to mark the reply region, "
          f"{STOP} to stop.")

    from pywinauto.win32_hooks import Hook

    hook = Hook()

    def handle(event) -> None:
        try:
            dispatch(session, event)
        except Exception as exc:          # a hook that throws stops recording
            print(f"  (ignored: {type(exc).__name__}: {exc})")
        if session.stopped.is_set():
            hook.stop()

    hook.handler = handle
    hook.hook(keyboard=True, mouse=True)

    path = write(session, name, title, out_dir, app_config_for(title, process))
    print(f"\n{len(session.recorder.anchors)} anchor(s), "
          f"{len(session.recorder.steps)} step(s) -> {path}")
    if not session.read_region:
        print(f"no read region was marked, so the flow drives the application "
              f"and records nothing. Re-record and press {MARK}, or add a read "
              f"step by hand.")
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
