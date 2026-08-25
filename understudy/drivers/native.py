"""Windows UIAutomation driver.

**Not yet exercised.** No part of this has run against a real application: there
is no Windows machine in the environment it was written in. It is structured so
that the parts which *can* be tested already are, and the parts which cannot are
as small and as obvious as possible.

  * Every decision -- which strategy means which element, what to do when
    several match, how to fold a Win32 name -- lives in `understudy.native_match`
    and is covered by tests against synthetic trees.
  * This file is the adapter: walk the tree, click things, read text, take
    screenshots. Three functions of pywinauto contact, marked below.

Reading text follows a chain, because a CAD application answers "what does this
say" in three different ways depending on the control:

  1. the UIA value or name -- exact, when the control exposes one;
  2. the clipboard, via select-all and copy -- frequently the only reliable way
     to get text out of a legacy custom-drawn panel, and still exact;
  3. OCR on a screenshot -- approximate, and the last resort.

The visual-anchor and agent rungs work here exactly as they do on the web,
because both operate on pixels and every backend has those.
"""

from __future__ import annotations

import time
from typing import Any

from understudy.drivers.base import DriverError, Resolution, TargetNotFound
from understudy.cursor import MouseStyle, move as move_pointer
from understudy.flow import Strategy, Target
from understudy.keyboard import TypingStyle, escape_send_keys, type_text
from understudy.geometry import (
    WindowGeometry,
    enumerate_monitors,
    make_dpi_aware,
    monitor_for,
)
from understudy.learned import LearnedAnchors
from understudy.native_match import (
    ElementDescriptor,
    NoMatch,
    normalise_name,
    resolve as resolve_in_tree,
)
from understudy.ocr import read_text
from understudy.recording import FfmpegRecorder, NullRecorder, Recording
from understudy.resolvers import NullResolver, Resolver
from understudy.vision import Match, crop, locate_all
from harness.image import to_png_bytes

#: How long to let a freshly-opened window settle before walking it.
SETTLE_S = 0.2
#: Guard against walking an enormous tree forever. CATIA's is large.
MAX_ELEMENTS = 8000
MAX_DEPTH = 14


def _describe(wrapper, ancestors: tuple[str, ...], depth: int) -> ElementDescriptor:
    """One pywinauto wrapper as plain data.

    Every property access is guarded: on a custom-drawn control any of them can
    throw, and a walk that dies on the first odd element is useless.
    """
    def safe(getter, default=""):
        try:
            value = getter()
            return default if value is None else value
        except Exception:
            return default

    info = safe(lambda: wrapper.element_info, None)
    return ElementDescriptor(
        control_type=str(safe(lambda: info.control_type)),
        automation_id=str(safe(lambda: info.automation_id)),
        name=str(safe(lambda: info.name)),
        class_name=str(safe(lambda: info.class_name)),
        depth=depth,
        ancestors=ancestors,
        enabled=bool(safe(lambda: wrapper.is_enabled(), True)),
        visible=bool(safe(lambda: wrapper.is_visible(), True)),
        bounds=_rectangle(info),
        handle=wrapper,
    )


def _rectangle(info) -> tuple[int, int, int, int] | None:
    try:
        rect = info.rectangle
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None


def walk(wrapper, ancestors: tuple[str, ...] = (), depth: int = 0,
         budget: dict[str, int] | None = None) -> list[ElementDescriptor]:
    """Flatten a UIA subtree into descriptors. **Unexercised.**"""
    budget = budget if budget is not None else {"n": 0}
    if depth > MAX_DEPTH or budget["n"] >= MAX_ELEMENTS:
        return []
    budget["n"] += 1

    described = _describe(wrapper, ancestors, depth)
    found = [described]
    # The name is carried into the path as well as the type, so `path:
    # [Window, Filters]` can name a pane rather than only its kind.
    step = described.control_type or "?"
    child_path = ancestors + ((described.name or step),) if described.name else ancestors + (step,)
    try:
        children = wrapper.children()
    except Exception:
        children = []
    for child in children:
        found.extend(walk(child, child_path, depth + 1, budget))
    return found


def matching_titles(pattern: str | None) -> list[str]:
    """Every top-level window the pattern matches, or [] if we cannot look."""
    if not pattern:
        return []
    try:
        from pywinauto import Desktop

        return [
            window.window_text()
            for window in Desktop(backend="uia").windows(
                title_re=_glob_to_regex(pattern), top_level_only=True
            )
        ]
    except Exception:
        return []


def attach_failure(pattern: str | None, executable: str | None,
                   exc: Exception, titles: list[str]) -> str:
    """Why the attach failed, in terms the person who wrote the flow can act on.

    Two windows matching one pattern is not an edge case on a CAD workstation:
    CATIA V5 and 3DX side by side, two documents open, a splash screen that has
    not gone away. pywinauto reports it as "There are 2 elements that match the
    criteria {'title_re': ...}", which says nothing about which two.
    """
    if len(titles) > 1:
        listed = "\n".join(f"  - {title!r}" for title in titles)
        return (
            f"{pattern!r} matches {len(titles)} windows, so there is no way to "
            f"tell which one the flow means:\n{listed}\n"
            f"Tighten target_app.native.window_title_pattern until it matches "
            f"one of them, or close the others."
        )
    return f"could not attach to {pattern or executable!r}: {exc}"


class NativeDriver:
    """UIAutomation, with the visual and agent rungs from the web driver."""

    backend = "native"

    def __init__(self, resolver: Resolver | None = None, agent_mode: str = "off",
                 learned_dir: str | None = None, **_ignored: Any) -> None:
        self.resolver: Resolver = resolver or NullResolver()
        self.agent_mode = agent_mode
        self.learned = LearnedAnchors(learned_dir)
        self.window = None
        self.app = None
        self._elements: list[ElementDescriptor] = []
        self.monitors: list = []
        self.geometry: WindowGeometry | None = None
        #: Placement at start. Anchors were captured against this; a resize or
        #: a DPI change invalidates them, a move does not.
        self.baseline: WindowGeometry | None = None
        self.dpi_awareness = "not set"
        self.warnings: list[str] = []
        self.mouse_style = MouseStyle()
        self.typing_style = TypingStyle()
        self.recorder = NullRecorder("start() has not run yet")

    # -- lifecycle ------------------------------------------------------------

    def start(self, app_config: dict[str, Any]) -> None:
        try:
            from pywinauto import Application, Desktop
        except ImportError:
            raise DriverError(
                "the native backend needs pywinauto (pip install pywinauto), "
                "and Windows to run it on"
            ) from None

        pattern = app_config.get("window_title_pattern")
        executable = app_config.get("executable")
        if not pattern and not executable:
            raise DriverError(
                "flow has no target_app.native.window_title_pattern or .executable"
            )
        # Before anything reads a coordinate. On a 150% monitor an unaware
        # process is handed virtualised numbers: they look plausible, the
        # clicks land wrong, and screenshots come back at a different
        # resolution from the one the anchors were captured at.
        self.dpi_awareness = make_dpi_aware()
        self.monitors = enumerate_monitors()
        self.mouse_style = MouseStyle.from_config(app_config.get("mouse"))
        self.typing_style = TypingStyle.from_config(app_config.get("typing"))

        try:
            if executable and app_config.get("launch", False):
                self.app = Application(backend="uia").start(executable)
            desktop = Desktop(backend="uia")
            self.window = desktop.window(title_re=_glob_to_regex(pattern or "*"))
            self.window.wait("exists ready", timeout=60)
        except Exception as exc:
            raise DriverError(
                attach_failure(pattern, executable, exc, matching_titles(pattern))
            ) from None

        self.refresh()
        self.baseline = self.geometry
        # After refresh(), because the recorder frames itself on the monitor
        # the window turned out to be on. Never reached until now: the whole
        # native recording path was dead code, and a run asked to record said
        # nothing at all about producing no video.
        self._prepare_recorder(app_config)
        self.park_pointer()
        expected = app_config.get("monitor")
        if expected and self.geometry and self.geometry.monitor != expected:
            raise DriverError(
                f"the window is on {self.geometry.monitor or 'an unknown monitor'}, "
                f"but the flow expects {expected}. Move it, or change "
                f"target_app.native.monitor. Anchors and regions are captured "
                f"per monitor and do not carry across a DPI change."
            )

    def refresh(self) -> None:
        """Re-walk the tree and re-read the window's placement."""
        if self.window is None:
            raise DriverError("driver used before start")
        time.sleep(SETTLE_S)
        self.geometry = self._read_geometry()
        self._check_placement()
        self._elements = walk(self.window)

    def _read_geometry(self) -> WindowGeometry | None:
        try:
            rect = self.window.rectangle()
        except Exception:
            return None
        found = WindowGeometry(
            left=int(rect.left), top=int(rect.top),
            right=int(rect.right), bottom=int(rect.bottom),
        )
        monitor = monitor_for(self.monitors, found)
        if monitor is None:
            return found
        return WindowGeometry(
            left=found.left, top=found.top, right=found.right, bottom=found.bottom,
            monitor=monitor.name, scale=monitor.scale,
        )

    def _check_placement(self) -> None:
        """Warn once if the window has been resized or moved to a monitor at a
        different scale. Anchors are pixels; both invalidate them."""
        if not (self.baseline and self.geometry):
            return
        if self.geometry.same_placement_as(self.baseline):
            return
        message = (
            f"the window changed from {self.baseline.describe()} to "
            f"{self.geometry.describe()}; anchors captured before this may no "
            f"longer match"
        )
        if message not in self.warnings:
            self.warnings.append(message)

    # -- recording ------------------------------------------------------------

    def _prepare_recorder(self, app_config: dict[str, Any]) -> None:
        """Capture the monitor the window is on, by default.

        A window rectangle is the wrong boundary: menus, tooltips and modal
        dialogs routinely fall outside it, and a recording that clips those off
        is a recording of the wrong thing. Since the application has a screen to
        itself, that screen is the right frame.
        """
        settings = app_config.get("record") or {}
        if settings.get("mode") == "window":
            self.recorder = FfmpegRecorder(
                window_title=app_config.get("window_title_pattern"),
                framerate=int(settings.get("framerate", 12)),
            )
            return

        region = None
        monitor = monitor_for(self.monitors, self.geometry) if self.geometry else None
        if monitor is not None:
            region = {"x": monitor.left, "y": monitor.top,
                      "width": monitor.width, "height": monitor.height}
        elif self.geometry is not None:
            region = {"x": self.geometry.left, "y": self.geometry.top,
                      "width": self.geometry.width, "height": self.geometry.height}
        self.recorder = FfmpegRecorder(
            region=region, framerate=int(settings.get("framerate", 12))
        )

    def start_recording(self, path) -> bool:
        return self.recorder.start(path)

    def stop_recording(self) -> Recording:
        return self.recorder.stop()

    def recording_unavailable(self) -> str | None:
        if getattr(self.recorder, "available", False):
            return None
        return getattr(self.recorder, "reason", None) or "the recorder is not ready"

    # -- pointer --------------------------------------------------------------

    def pointer_position(self) -> tuple[int, int]:
        try:
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            point = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return (int(point.x), int(point.y))
        except Exception:
            # Unknown start point: begin the move from the destination, which
            # degrades to a teleport rather than flying in from (0, 0).
            return (0, 0)

    def move_pointer_to(self, x: int, y: int) -> None:
        """Put the pointer on a screen point, travelling there when animated.

        Instant means instant, not absent. This used to return without moving
        anything when the animation was off, which left the cursor wherever it
        happened to be -- fine for a click, which places it itself, and wrong
        for everything else that assumes the pointer is where it was put. The
        web driver always moved; the two backends disagreed, and only one of
        them was right.
        """
        target = (int(x), int(y))
        if not self.mouse_style.animated:
            _set_cursor(*target)
            return

        start = self.pointer_position()
        if start == (0, 0):
            # Nowhere to travel from. Landing on the target beats not moving.
            _set_cursor(*target)
            return
        move_pointer(start, target, self.mouse_style)

    def park_pointer(self) -> None:
        """Put the pointer inside the target window before the first step, so a
        recording does not open with a jump in from wherever it was left."""
        if self.geometry is None:
            return
        centre = self.geometry.to_screen(
            self.geometry.width // 2, self.geometry.height // 2
        )
        self.move_pointer_to(*centre)

    def _centre_of(self, handle) -> tuple[int, int] | None:
        try:
            rect = handle.rectangle()
            return (
                int((rect.left + rect.right) / 2),
                int((rect.top + rect.bottom) / 2),
            )
        except Exception:
            return None

    def to_screen(self, x: int | float, y: int | float) -> tuple[int, int]:
        """Window-relative to virtual-desktop, which is what the mouse takes.

        Everything a flow declares -- anchors, regions -- is window-relative, so
        it survives the window moving and means the same thing on whichever
        monitor the application is on.
        """
        if self.geometry is None:
            raise DriverError("window geometry is unknown; cannot place a click")
        return self.geometry.to_screen(x, y)

    def stop(self) -> None:
        self.window = None
        self._elements = []

    def reset(self) -> None:
        raise DriverError(
            "level-2 reset is not implemented for the native backend; use "
            "level-1 reset steps in the flow"
        )

    def interface(self) -> Any:  # pragma: no cover - parity with the protocol
        raise DriverError("native flows are authored, not introspected")

    # -- resolution -----------------------------------------------------------

    def resolve(self, target: Target, timeout_ms: int):
        """Tree strategies first, then anchors, then the agent.

        The ordering, the ambiguity rule and the name folding all come from
        `native_match`, which is tested; this only supplies the elements and
        handles the two rungs that need pixels.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        attempts: list[str] = []

        while True:
            try:
                found = resolve_in_tree(self._elements, target, self.backend)
                return found.element.handle, Resolution(
                    target.name, found.index, None, found.note, "selector"
                )
            except NoMatch as exc:
                attempts = list(exc.attempts)

            for index, strategy in enumerate(target.for_backend(self.backend)):
                if "image" in strategy.fields:
                    handle, note = self._resolve_anchor(strategy)
                    if handle is not None:
                        return handle, Resolution(target.name, index, strategy, note, "anchor")
                    attempts.append(f"{strategy.describe()} -> {note}")
                elif "agent" in strategy.fields:
                    handle, via, note = self._resolve_by_agent(target, strategy)
                    if handle is not None:
                        return handle, Resolution(target.name, index, strategy, note, via)
                    attempts.append(f"{strategy.describe()} -> {note}")

            if time.monotonic() >= deadline:
                raise TargetNotFound(target, self.backend, attempts)
            self.refresh()

    def _resolve_anchor(self, strategy: Strategy):
        from pathlib import Path

        matches = locate_all(
            self.screenshot(), Path(strategy.fields["image"]).read_bytes(),
            threshold=float(strategy.fields.get("threshold", 0.9)),
            region=strategy.fields.get("region"),
        )
        if len(matches) != 1:
            return None, f"{len(matches)} visual match(es)"
        return _Point(self, matches[0], strategy.fields.get("offset")), \
            f"score {matches[0].score:.3f}"

    def _resolve_by_agent(self, target: Target, strategy: Strategy):
        learned = self.learned.get(target.name)
        if learned is not None:
            matches = locate_all(self.screenshot(), learned, threshold=0.93)
            if len(matches) == 1:
                return _Point(self, matches[0]), "learned-anchor", \
                    f"cached, score {matches[0].score:.3f}"
            self.learned.forget(target.name)

        if self.agent_mode == "off":
            return None, "agent", "agent resolution is off"
        intent = target.intent or (
            strategy.fields.get("agent") if isinstance(strategy.fields.get("agent"), str) else ""
        )
        if not intent:
            return None, "agent", "no intent to guide the agent"

        shot = self.screenshot()
        found = self.resolver.locate(shot, intent)
        if found is None:
            return None, "agent", "agent did not find it"
        self.learned.put(target.name, to_png_bytes(crop(shot, found.as_region())),
                         note=found.reasoning)
        return (
            _Point(self, Match(found.x, found.y, found.width, found.height, found.confidence)),
            "agent",
            f"confidence {found.confidence:.2f}",
        )

    # -- actions --------------------------------------------------------------

    def click(self, target: Target, timeout_ms: int) -> Resolution:
        handle, resolution = self.resolve(target, timeout_ms)
        self._approach(handle)
        _act(lambda: handle.click_input())
        self.refresh()
        return resolution

    def _approach(self, handle) -> None:
        """Travel to the control before pressing it.

        pywinauto's own click_input teleports; arriving first means the click
        itself moves the pointer nowhere, so the recording shows a hand moving
        to a button rather than a button being pressed by nothing.
        """
        if isinstance(handle, _Point):
            self.move_pointer_to(*handle.screen_point)
            return
        centre = self._centre_of(handle)
        if centre:
            self.move_pointer_to(*centre)

    def type(self, target: Target, text: str, timeout_ms: int, mode: str = "type",
             clear: bool = True, delay_ms: int = 0) -> Resolution:
        handle, resolution = self.resolve(target, timeout_ms)
        self._approach(handle)
        _act(lambda: handle.click_input())
        if clear:
            # Select-all then delete: typing over a selection without deleting
            # first appends on some controls, which silently carries the
            # previous variant's text into this one.
            _act(lambda: handle.type_keys("^a{DELETE}"))
        _act(lambda: type_text(
            text,
            send=lambda chunk: handle.type_keys(
                escape_send_keys(chunk), with_spaces=True, with_newlines=False
            ),
            sleep=time.sleep,
            style=self.typing_style,
        ))
        self.refresh()
        return resolution

    def read(self, target: Target, timeout_ms: int) -> tuple[str, Resolution]:
        handle, resolution = self.resolve(target, timeout_ms)

        for attempt in (self._read_uia, self._read_clipboard):
            text = attempt(handle)
            if text:
                return text, resolution
        return self._read_ocr(handle), resolution

    def _read_uia(self, handle) -> str:
        for getter in ("get_value", "window_text", "texts"):
            try:
                value = getattr(handle, getter)()
            except Exception:
                continue
            if isinstance(value, (list, tuple)):
                value = " ".join(str(part) for part in value)
            if value and str(value).strip():
                return " ".join(str(value).split())
        return ""

    def _read_clipboard(self, handle) -> str:
        """Select-all, copy, read the clipboard. Exact, where UIA gives nothing."""
        try:
            import pyperclip
        except ImportError:
            return ""
        try:
            handle.click_input()
            handle.type_keys("^a^c")
            time.sleep(0.15)
            return " ".join(str(pyperclip.paste()).split())
        except Exception:
            return ""

    def _read_ocr(self, handle) -> str:
        try:
            image = handle.capture_as_image()
        except Exception:
            return ""
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return read_text(buffer.getvalue()).text

    def key(self, keys: str, target: Target | None, timeout_ms: int) -> Resolution | None:
        if target is None:
            _act(lambda: self.window.type_keys(keys))
            return None
        handle, resolution = self.resolve(target, timeout_ms)
        # Focus first, explicitly. A real wrapper's type_keys does it itself;
        # an anchor's cannot, because for a pixel focusing means clicking, and
        # a click inside typing moves the caret.
        _focus(handle)
        _act(lambda: handle.type_keys(keys))
        return resolution

    def screenshot(self, target: Target | None = None, full_page: bool = False,
                   region: dict[str, int] | None = None) -> bytes:
        import io

        source = self.window
        if target is not None and region is None:
            source, _ = self.resolve(target, 5_000)
        try:
            image = source.capture_as_image()
        except Exception as exc:
            raise DriverError(f"could not capture the window: {exc}") from None

        if region:
            image = image.crop((
                region["x"], region["y"],
                region["x"] + region["width"], region["y"] + region["height"],
            ))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def exists(self, target: Target, timeout_ms: int = 0) -> bool:
        try:
            self.resolve(target, timeout_ms)
            return True
        except (TargetNotFound, DriverError):
            return False

    def is_visible(self, target: Target) -> bool:
        try:
            handle, _ = self.resolve(target, 0)
            return bool(handle.is_visible())
        except Exception:
            return False

    def wait_for_element(self, target: Target, state: str, timeout_ms: int) -> Resolution:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if state == "hidden":
                if not self.is_visible(target):
                    return Resolution(target.name, 0, note="hidden or absent")
            else:
                try:
                    handle, resolution = self.resolve(target, 0)
                    if state == "enabled" and not handle.is_enabled():
                        raise DriverError("not enabled yet")
                    return resolution
                except (TargetNotFound, DriverError):
                    pass
            time.sleep(0.2)
            self.refresh()
        raise DriverError(f"target {target.name!r} did not become {state}")


class _Point:
    """A location found in pixels, exposing the slice of the wrapper interface
    the driver uses -- so an anchor and a real control are interchangeable."""

    def __init__(self, driver: NativeDriver, match: Match,
                 offset: dict[str, int] | None = None) -> None:
        self.driver = driver
        self.match = match
        self.offset = offset or {}

    @property
    def point(self) -> tuple[int, int]:
        x, y = self.match.centre
        return x + int(self.offset.get("dx", 0)), y + int(self.offset.get("dy", 0))

    @property
    def screen_point(self) -> tuple[int, int]:
        """The click point in virtual-desktop coordinates.

        `point` is window-relative, because that is the space the screenshot --
        and therefore the anchor match -- is in. The mouse is driven in screen
        coordinates. On a single monitor with the window at the origin the two
        are the same, which is exactly why getting this wrong survives testing;
        on a second monitor they differ by the window origin, and left of or
        above the primary that origin is negative.
        """
        return self.driver.to_screen(*self.point)

    def click_input(self) -> None:
        from pywinauto import mouse

        self.driver.move_pointer_to(*self.screen_point)
        mouse.click(coords=self.screen_point)

    #: A pixel has no focus of its own, so focusing one means clicking it.
    set_focus = click_input

    def type_keys(self, keys: str, **kwargs) -> None:
        """Keys only. Emphatically not a click.

        This used to click first, mirroring pywinauto's own type_keys, which
        calls set_focus. On a real control that is free. On a pixel it is a
        mouse click, and human-speed typing sends one character at a time --
        so every keystroke put the caret back where the click landed, and
        "Found by picture, typed by keystroke" arrived as
        "Found by pic.ekortsyek yb depyt ,erut". Reversed, one character at a
        time, from the click point onwards.

        Focus is the caller's business: type() and key() click once, first.
        """
        from pywinauto import keyboard

        keyboard.send_keys(keys, **kwargs)

    def capture_as_image(self):
        return self.driver.window.capture_as_image().crop((
            self.match.x, self.match.y,
            self.match.x + self.match.width, self.match.y + self.match.height,
        ))

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def get_value(self):
        raise DriverError(
            "this target is an image anchor: it has no text to read. Use "
            "wait_for_stable with mode: pixels, or read from a different target."
        )


def _focus(handle) -> None:
    """Best effort. A real wrapper's type_keys focuses itself, so failing here
    costs nothing; an anchor has no other way to get focus, so it is worth
    trying. Either way, not a reason to fail the step."""
    focus = getattr(handle, "set_focus", None)
    if focus is None:
        return
    try:
        focus()
    except Exception:
        pass


def _act(operation) -> None:
    try:
        operation()
    except Exception as exc:
        raise DriverError(f"{type(exc).__name__}: {exc}") from None


def _set_cursor(x: int, y: int) -> None:  # pragma: no cover - needs Windows
    """One call, no animation. Separate so the animated path and the instant
    one cannot drift apart again."""
    from pywinauto import mouse

    mouse.move(coords=(int(x), int(y)))


def _glob_to_regex(pattern: str) -> str:
    import re

    return "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"
