"""Driving the real Windows cursor at a page rendered inside another process.

Playwright moves the pointer with CDP input events. The page reacts to them
exactly as it reacts to a person -- hover states, focus, clicks all behave --
but they are synthetic events delivered inside the renderer, so the arrow drawn
on the desktop never moves. A screen capture of a CATIA window whose embedded
LEO panel is being driven that way shows the panel operating itself while the
cursor sits wherever it was left.

For an unattended sweep that does not matter. For a session being recorded by
Camtasia, it is the difference between a demonstration and a haunting.

So when the flow asks for it, clicks are delivered by the operating system
instead: find the element through CDP as usual, work out where it is on the
desktop, then move and click the actual cursor. The page cannot tell the
difference -- which is the point, since it is a more faithful reproduction of a
person than the synthetic path was.

The arithmetic is here, separate from the Win32 calls, because the arithmetic is
what goes wrong and it is the part that can be tested away from Windows.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

#: What the browser is asked for. `screenX`/`screenY` are the *window's* corner,
#: so the chrome above the page has to be subtracted off; an embedded WebView2
#: has no chrome and the correction comes out as zero.
VIEWPORT_ORIGIN_JS = """() => [
  window.screenX, window.screenY,
  window.outerWidth, window.innerWidth,
  window.outerHeight, window.innerHeight,
  window.devicePixelRatio
]"""


@dataclass(frozen=True)
class ViewportOrigin:
    """Where the page's top-left corner sits on the desktop, and at what scale.

    `x` and `y` are physical screen pixels. `scale` converts CSS pixels to
    physical ones: 1.0 at 100% display scaling, 1.5 at 150%, and so on.
    """

    x: float
    y: float
    scale: float = 1.0

    def to_screen(self, client_x: float, client_y: float) -> tuple[int, int]:
        """A point in page coordinates -> a point on the desktop."""
        return (
            int(round(self.x + client_x * self.scale)),
            int(round(self.y + client_y * self.scale)),
        )


def origin_from_window(
    screen_x: float, screen_y: float,
    outer_width: float, inner_width: float,
    outer_height: float, inner_height: float,
    device_pixel_ratio: float = 1.0,
) -> ViewportOrigin:
    """Work the viewport's desktop position out of what the page can see.

    The vertical correction is the browser's chrome -- tab strip, address bar --
    which sits between the window's top edge and the page. The horizontal one is
    the window border, split between the two sides. Both are zero for a bare
    WebView, which is the case that matters here.
    """
    scale = device_pixel_ratio or 1.0
    chrome_height = max(0.0, outer_height - inner_height)
    border = max(0.0, (outer_width - inner_width) / 2.0)
    return ViewportOrigin(
        x=(screen_x + border) * scale,
        y=(screen_y + chrome_height) * scale,
        scale=scale,
    )


def read_origin(page) -> ViewportOrigin | None:
    """Ask a live page where it is. None when it will not say."""
    try:
        values = page.evaluate(VIEWPORT_ORIGIN_JS)
    except Exception:
        return None
    try:
        screen_x, screen_y, outer_w, inner_w, outer_h, inner_h, dpr = values
    except (TypeError, ValueError):
        return None
    return origin_from_window(
        screen_x, screen_y, outer_w, inner_w, outer_h, inner_h, dpr
    )


# -- moving the actual cursor -------------------------------------------------
#
# Two small backends rather than a dependency. Windows is the target; X11 exists
# because it can be exercised here, and a mechanism that has been run at least
# once somewhere is worth more than one that has only been written.


class WindowsPointer:
    """SetCursorPos and SendInput, straight through ctypes."""

    name = "windows"

    MOUSEEVENTF = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }

    def __init__(self) -> None:  # pragma: no cover - needs Windows
        import ctypes

        self._user32 = ctypes.windll.user32

    def move(self, x: int, y: int) -> None:  # pragma: no cover
        self._user32.SetCursorPos(int(x), int(y))

    def click(self, x: int, y: int, button: str = "left") -> None:  # pragma: no cover
        down, up = self.MOUSEEVENTF.get(button, self.MOUSEEVENTF["left"])
        self.move(x, y)
        self._user32.mouse_event(down, 0, 0, 0, 0)
        self._user32.mouse_event(up, 0, 0, 0, 0)


class X11Pointer:
    """XTest, which is how xdotool does it."""

    name = "x11"

    BUTTONS = {"left": 1, "middle": 2, "right": 3}

    def __init__(self) -> None:
        import ctypes
        import ctypes.util

        x11_path = ctypes.util.find_library("X11")
        xtst_path = ctypes.util.find_library("Xtst")
        if not x11_path or not xtst_path:
            raise OSError("libX11 and libXtst are needed to move the cursor")
        self._x11 = ctypes.cdll.LoadLibrary(x11_path)
        self._xtst = ctypes.cdll.LoadLibrary(xtst_path)
        self._x11.XOpenDisplay.restype = ctypes.c_void_p
        self._display = self._x11.XOpenDisplay(None)
        if not self._display:
            raise OSError("no X display; is DISPLAY set?")
        self._display = ctypes.c_void_p(self._display)

    def move(self, x: int, y: int) -> None:
        self._xtst.XTestFakeMotionEvent(self._display, -1, int(x), int(y), 0)
        self._x11.XFlush(self._display)

    def click(self, x: int, y: int, button: str = "left") -> None:
        code = self.BUTTONS.get(button, 1)
        self.move(x, y)
        self._xtst.XTestFakeButtonEvent(self._display, code, True, 0)
        self._xtst.XTestFakeButtonEvent(self._display, code, False, 0)
        self._x11.XFlush(self._display)


_backend: object | None = None
_reason: str | None = None
_probed = False


def _probe() -> None:
    global _backend, _reason, _probed
    _probed = True
    if sys.platform == "win32":  # pragma: no cover - needs Windows
        from flowrunner.geometry import make_dpi_aware

        try:
            # Without this, screen coordinates are reported in the scaled space
            # and every click on a high-DPI display lands short of its target.
            make_dpi_aware()
            _backend = WindowsPointer()
            return
        except Exception as exc:
            _reason = f"could not reach the Windows cursor: {exc}"
            return
    try:
        _backend = X11Pointer()
    except Exception as exc:
        _reason = f"no desktop cursor available on {sys.platform}: {exc}"


def backend():
    """The thing that moves the cursor, or None with a reason for why not."""
    if not _probed:
        _probe()
    return _backend


def unavailable_reason() -> str | None:
    if not _probed:
        _probe()
    return _reason


def available() -> bool:
    return backend() is not None


def reset_backend() -> None:
    """Forget the probe. For tests, and for a display that appears late."""
    global _backend, _reason, _probed
    _backend, _reason, _probed = None, None, False


def set_cursor(x: int, y: int) -> None:
    pointer = backend()
    if pointer is None:
        raise OSError(unavailable_reason() or "no desktop cursor")
    pointer.move(int(x), int(y))


def click_at(x: int, y: int, button: str = "left") -> None:
    pointer = backend()
    if pointer is None:
        raise OSError(unavailable_reason() or "no desktop cursor")
    pointer.click(int(x), int(y), button)
