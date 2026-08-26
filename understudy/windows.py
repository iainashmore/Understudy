"""What is open, and which of it a flow means.

Attaching to a Windows application means naming a window, and on a CAD
workstation the name is not enough. 3DEXPERIENCE runs as a crowd of processes,
several of which own a top-level window and answer to the same title: the
client, a splash screen that has not gone away, a licensing helper, a
message-only window with no pixels at all. Asking for "*3DEXPERIENCE*" and
getting six answers is the normal case, not the edge case.

So a window is identified here by more than its title -- the process that owns
it, and how big it is -- and the same description serves the probe, the driver
and the window picker in the UI.

Windows-only, and deliberately soft about it: everything returns empty rather
than raising where pywinauto is not installed, so a Linux checkout can import
this and the tests can exercise the choosing.
"""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OpenWindow:
    """One top-level window, described well enough to tell it from its siblings."""

    title: str
    pid: int = 0
    process: str = ""
    class_name: str = ""
    width: int = 0
    height: int = 0
    visible: bool = True
    #: The live pywinauto wrapper, when there is one. Excluded from equality
    #: and from as_dict(), so a window can be compared and serialised.
    wrapper: Any = field(default=None, compare=False, repr=False)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def owner(self) -> str:
        return f"{self.process} pid {self.pid}" if self.process else f"pid {self.pid}"

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "pid": self.pid, "process": self.process,
                "class_name": self.class_name, "width": self.width,
                "height": self.height, "visible": self.visible}

    def described(self) -> str:
        hidden = "" if self.visible else "  (not visible)"
        return f"{self.title!r}  {self.owner}  {self.width}x{self.height}{hidden}"


def process_names() -> dict[int, str]:
    """pid -> image name, from tasklist. Built into Windows, no dependency."""
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return {}
    names: dict[int, str] = {}
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 2:
            try:
                names[int(row[1])] = row[0]
            except ValueError:
                continue
    return names


def glob_to_regex(pattern: str) -> str:
    import re

    return "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"


def open_windows(pattern: str = "*") -> list[OpenWindow]:
    """Every top-level window whose title matches, best candidate first."""
    try:
        from pywinauto import Desktop

        found = Desktop(backend="uia").windows(
            title_re=glob_to_regex(pattern), top_level_only=True
        )
    except Exception:
        return []

    names = process_names()
    windows = []
    for wrapper in found:
        info = getattr(wrapper, "element_info", None)
        rectangle = getattr(info, "rectangle", None)
        pid = getattr(info, "process_id", 0) or 0
        try:
            visible = bool(info.visible)
        except Exception:
            visible = False
        try:
            title = wrapper.window_text()
        except Exception:
            title = ""
        windows.append(OpenWindow(
            title=title,
            pid=pid,
            process=names.get(pid, ""),
            class_name=getattr(info, "class_name", "") or "",
            width=rectangle.width() if rectangle else 0,
            height=rectangle.height() if rectangle else 0,
            visible=visible,
            wrapper=wrapper,
        ))
    return best_first(windows)


def best_first(windows: list[OpenWindow]) -> list[OpenWindow]:
    """Visible before hidden, then biggest first.

    Among a dozen windows called 3DEXPERIENCE, the one filling a monitor is
    the client and the 400x300 one is the splash screen.
    """
    return sorted(windows, key=lambda w: (w.visible, w.area), reverse=True)


def owned_by(windows: list[OpenWindow], process: str | None) -> list[OpenWindow]:
    """Narrow to one executable. Case-insensitive, and '.exe' is optional."""
    if not process:
        return windows
    wanted = process.lower().removesuffix(".exe")
    return [w for w in windows
            if w.process.lower().removesuffix(".exe") == wanted]


def choose(windows: list[OpenWindow]) -> tuple[OpenWindow | None, list[OpenWindow]]:
    """The window a flow means, and the ones it is being chosen over.

    One visible candidate is the answer even when hidden windows share its
    title -- a splash screen with no pixels was never a real alternative.
    Two visible candidates is a genuine ambiguity: replaying into the wrong
    open document is destructive, so the caller is expected to refuse rather
    than guess.
    """
    ranked = best_first(windows)
    if not ranked:
        return None, []
    visible = [w for w in ranked if w.visible]
    if len(visible) == 1:
        return visible[0], [w for w in ranked if w is not visible[0]]
    if not visible:
        return None, ranked
    return None, ranked
