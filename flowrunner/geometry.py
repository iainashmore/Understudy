"""Window and monitor geometry.

Two coordinate spaces exist and confusing them is silent: a window-relative
space, which is what a screenshot of a window and therefore every anchor match
is in, and the virtual-desktop space, which is what the mouse is driven in.

On a single monitor with the window at the top-left the two coincide, which is
exactly why the mistake survives testing. On a second monitor they differ by the
window origin, and on a monitor placed left of or above the primary that origin
is **negative** -- the virtual desktop puts the primary at (0, 0) and everything
else around it.

Anchors and regions in a flow are window-relative on purpose. A flow should
survive the window being moved, and it should mean the same thing on whichever
monitor the application happens to be on.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowGeometry:
    """Where the target window is, in virtual-desktop coordinates."""

    left: int
    top: int
    right: int
    bottom: int
    #: Device name of the monitor the window is on, when it could be determined.
    monitor: str = ""
    #: That monitor's DPI scale. 1.0 at 96 dpi, 1.5 at 150%.
    scale: float = 1.0

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def origin(self) -> tuple[int, int]:
        return (self.left, self.top)

    def to_screen(self, x: int | float, y: int | float) -> tuple[int, int]:
        """Window-relative to virtual-desktop. What the mouse needs."""
        return (int(round(self.left + x)), int(round(self.top + y)))

    def to_window(self, x: int | float, y: int | float) -> tuple[int, int]:
        """Virtual-desktop to window-relative. What a recorder needs when it
        has a click point from a global hook."""
        return (int(round(x - self.left)), int(round(y - self.top)))

    def contains_screen_point(self, x: int | float, y: int | float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def same_placement_as(self, other: "WindowGeometry") -> bool:
        """Whether anchors taken against `other` are still meaningful.

        Size and scale matter; position does not. An anchor is re-located in
        each run's own screenshot, so a moved window is fine -- a resized or
        rescaled one is not, because the pixels themselves changed.
        """
        return self.size == other.size and self.scale == other.scale

    def describe(self) -> str:
        where = f" on {self.monitor}" if self.monitor else ""
        scale = f" @{self.scale:g}x" if self.scale != 1.0 else ""
        return f"{self.width}x{self.height} at ({self.left}, {self.top}){where}{scale}"


@dataclass(frozen=True)
class Monitor:
    name: str
    left: int
    top: int
    right: int
    bottom: int
    primary: bool = False
    scale: float = 1.0

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int | float, y: int | float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def describe(self) -> str:
        tag = " (primary)" if self.primary else ""
        scale = f" @{self.scale:g}x" if self.scale != 1.0 else ""
        return f"{self.name}: {self.width}x{self.height} at ({self.left}, {self.top}){tag}{scale}"


def monitor_for(monitors: list[Monitor], geometry: WindowGeometry) -> Monitor | None:
    """Which monitor a window is on -- the one containing its centre.

    Centre rather than origin: a window straddling two screens belongs to the
    one showing most of it, and its top-left corner may be on the other.
    """
    centre_x = geometry.left + geometry.width // 2
    centre_y = geometry.top + geometry.height // 2
    for monitor in monitors:
        if monitor.contains(centre_x, centre_y):
            return monitor
    return None


def make_dpi_aware() -> str:
    """Tell Windows this process understands per-monitor DPI.

    Without it, a process running on a 150% monitor is handed virtualised
    coordinates: the numbers look plausible, the clicks land in the wrong place,
    and screenshots come back at a different resolution from the one the anchors
    were captured at. Returns what was achieved, for the record.
    """
    if not sys.platform.startswith("win"):
        return "not windows"
    try:
        import ctypes

        # PER_MONITOR_AWARE_V2. Available from Windows 10 1703.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "unavailable"


def enumerate_monitors() -> list[Monitor]:
    """Every monitor, in virtual-desktop coordinates. Windows only."""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes

    monitors: list[Monitor] = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_double,
    )

    def collect(handle, _dc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return 1
        scale = 1.0
        try:
            dpi_x = ctypes.c_uint()
            dpi_y = ctypes.c_uint()
            # MDT_EFFECTIVE_DPI = 0
            ctypes.windll.shcore.GetDpiForMonitor(
                handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            )
            scale = round(dpi_x.value / 96.0, 4)
        except Exception:
            pass
        monitors.append(Monitor(
            name=info.szDevice, left=info.rcMonitor.left, top=info.rcMonitor.top,
            right=info.rcMonitor.right, bottom=info.rcMonitor.bottom,
            primary=bool(info.dwFlags & 1), scale=scale,
        ))
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, callback_type(collect), 0
        )
    except Exception:
        return []
    return monitors
