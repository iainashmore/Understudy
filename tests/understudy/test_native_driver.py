"""The native driver's testable surface.

The pywinauto contact cannot run here, so what is covered is: the protocol
shape, the tree walk (against fake wrappers, including ones that throw), the
anchor point arithmetic, and that every unavailable path fails with a message
that says what to do. The matching itself lives in `native_match` and has its
own tests.
"""

from __future__ import annotations

import pytest

from understudy.drivers import build
from understudy.drivers.base import Driver, DriverError
from understudy.windows import OpenWindow
from understudy.drivers.native import (NativeDriver, _Point, attach_failure,
                                        walk)
from understudy.vision import Match


class FakeInfo:
    def __init__(self, **fields):
        self.__dict__.update({
            "control_type": "", "automation_id": "", "name": "",
            "class_name": "", **fields,
        })

    @property
    def rectangle(self):
        raise RuntimeError("no rectangle on this control")


class FakeWrapper:
    def __init__(self, info=None, children=(), throws=False):
        self.element_info = info or FakeInfo()
        self._children = list(children)
        self._throws = throws

    def children(self):
        if self._throws:
            raise RuntimeError("COM error 0x80040201")
        return self._children

    def is_enabled(self):
        return True

    def is_visible(self):
        return True


class Exploding:
    """A control whose properties throw. Routine in custom-drawn UIs."""

    @property
    def element_info(self):
        raise RuntimeError("no accessible information")

    def children(self):
        return []


def test_the_driver_satisfies_the_protocol():
    assert isinstance(NativeDriver(), Driver)
    assert NativeDriver().backend == "native"


def test_the_registry_hands_it_out():
    assert isinstance(build("native"), NativeDriver)


class TestTreeWalk:
    def test_a_tree_flattens_with_depths_and_paths(self):
        tree = FakeWrapper(
            FakeInfo(control_type="Window", name="CATIA V5"),
            children=[
                FakeWrapper(FakeInfo(control_type="MenuBar")),
                FakeWrapper(
                    FakeInfo(control_type="Pane", name="Specification Tree"),
                    children=[FakeWrapper(FakeInfo(control_type="TreeItem", name="Pad.1"))],
                ),
            ],
        )
        found = walk(tree)

        assert [e.depth for e in found] == [0, 1, 1, 2]
        pad = found[-1]
        assert pad.name == "Pad.1"
        assert "Specification Tree" in pad.ancestors, (
            "a named pane should be reachable by name in a path"
        )

    def test_an_element_that_throws_does_not_stop_the_walk(self):
        tree = FakeWrapper(
            FakeInfo(control_type="Window"),
            children=[Exploding(), FakeWrapper(FakeInfo(control_type="Button"))],
        )
        found = walk(tree)
        assert len(found) == 3
        assert found[-1].control_type == "Button"

    def test_a_container_that_refuses_to_enumerate_is_not_fatal(self):
        """The signature of an opaque viewport."""
        found = walk(FakeWrapper(FakeInfo(control_type="Pane"), throws=True))
        assert len(found) == 1

    def test_the_walk_is_bounded(self, monkeypatch):
        # CATIA's tree is large; an unbounded walk would hang the run.
        import understudy.drivers.native as native

        monkeypatch.setattr(native, "MAX_ELEMENTS", 5)
        deep = FakeWrapper(FakeInfo(control_type="A"))
        for _ in range(50):
            deep = FakeWrapper(FakeInfo(control_type="A"), children=[deep])
        assert len(walk(deep)) <= 5

    def test_a_missing_rectangle_is_not_an_error(self):
        assert walk(FakeWrapper(FakeInfo(control_type="Pane")))[0].bounds is None


class TestAnchorPoints:
    def test_the_click_point_is_the_centre_of_the_match(self):
        point = _Point(NativeDriver(), Match(100, 200, 40, 20, 0.99))
        assert point.point == (120, 210)

    def test_an_offset_moves_the_click_off_the_anchor(self):
        """Anchor on the static label, click the field below it."""
        point = _Point(NativeDriver(), Match(100, 200, 40, 20, 0.99),
                       offset={"dx": 0, "dy": 26})
        assert point.point == (120, 236)

    def test_an_offset_grows_with_the_interface(self):
        """"26 pixels below the label" was measured in the pixels of the
        machine that recorded it. On an interface drawn half again as large the
        field is 39 below, and the old number lands in the label."""
        point = _Point(NativeDriver(), Match(100, 200, 60, 30, 0.9, scale=1.5),
                       offset={"dx": 0, "dy": 26})
        assert point.point == (130, 254)

    def test_reading_text_from_an_anchor_says_why_it_cannot(self):
        point = _Point(NativeDriver(), Match(0, 0, 10, 10, 0.99))
        with pytest.raises(DriverError, match="no text to read"):
            point.get_value()


class TestWhenTheInterfaceIsNotTheSizeItWasRecordedOn:
    """Anchors are pixels captured at one monitor's DPI. Moved to a screen at
    125%, every one of them misses -- and the flow is not wrong, the picture
    is a different number of pixels across."""

    def _screen(self, at=1.0):
        """A window with one distinctive control in it."""
        import numpy as np
        from harness.image import to_png_bytes
        from understudy.vision import resized

        rng = np.random.default_rng(7)
        image = np.full((300, 500, 3), 40, dtype=np.uint8)
        image[60:100, 200:280] = rng.integers(0, 255, (40, 80, 3), dtype=np.uint8)
        return to_png_bytes(resized(image, at)), image[60:100, 200:280].copy()

    def _driver(self, showing, anchor, tmp_path):
        from harness.image import to_png_bytes
        from understudy.flow import Strategy

        path = tmp_path / "anchor.png"
        path.write_bytes(to_png_bytes(anchor))
        driver = NativeDriver()
        driver.screenshot = lambda *args, **kwargs: showing
        return driver, Strategy("native", {"image": str(path), "threshold": 0.9})

    def test_an_anchor_from_another_dpi_still_resolves(self, tmp_path):
        showing, anchor = self._screen(1.25)
        driver, strategy = self._driver(showing, anchor, tmp_path)

        handle, note = driver._resolve_anchor(strategy)
        assert handle is not None, note
        assert "125%" in note, note
        # The centre of the control as it is drawn now, not as it was captured.
        assert handle.point == pytest.approx((300, 100), abs=3)

    def test_it_says_so_rather_than_resolving_quietly(self, tmp_path):
        """An interface that is not at the scale it was recorded at is a fact
        about the run. Clicking a few pixels approximately and saying nothing
        is how a sweep of eighty prompts goes subtly wrong."""
        showing, anchor = self._screen(1.25)
        driver, strategy = self._driver(showing, anchor, tmp_path)
        driver._resolve_anchor(strategy)

        assert len(driver.warnings) == 1
        assert "125%" in driver.warnings[0]

    def test_the_size_is_learned_once_and_then_simply_used(self, tmp_path):
        """Searching costs a second a size. Every anchor in a rescaled
        interface is rescaled the same way, so the second target asks about one
        size rather than a dozen."""
        showing, anchor = self._screen(1.25)
        driver, strategy = self._driver(showing, anchor, tmp_path)
        asked = []

        import understudy.drivers.native as native
        original = native.locate_scaled

        def watched(shot, image, scales=None, **rest):
            asked.append(scales)
            return original(shot, image, **({} if scales is None else {"scales": scales}), **rest)

        native.locate_scaled = watched
        try:
            driver._resolve_anchor(strategy)
            driver._scale_searched = False       # a new target, a new search
            driver._resolve_anchor(strategy)
        finally:
            native.locate_scaled = original

        assert asked[0] is None, "the first target searches"
        assert asked[1] == (1.25,), "the second is told where to look"
        assert len(driver.warnings) == 1, "and it is not said twice"

    def test_the_search_happens_once_per_target_not_once_per_poll(self, tmp_path):
        """resolve() polls until its timeout. A search on every turn of that
        loop would spend the whole budget resizing pictures."""
        showing, anchor = self._screen(1.25)
        driver, strategy = self._driver(showing, anchor, tmp_path)
        driver._scale_searched = True

        handle, note = driver._resolve_anchor(strategy)
        assert handle is None and note == "0 visual match(es)"

    def test_a_control_that_is_gone_is_not_rescued_by_resizing(self, tmp_path):
        import numpy as np
        from harness.image import to_png_bytes

        _, anchor = self._screen()
        empty = to_png_bytes(np.full((300, 500, 3), 40, dtype=np.uint8))
        driver, strategy = self._driver(empty, anchor, tmp_path)

        handle, note = driver._resolve_anchor(strategy)
        assert handle is None
        assert note == "0 visual match(es)"

    def test_two_of_them_stays_ambiguous(self, tmp_path):
        """Ambiguity is not resolution, and a second candidate size only adds
        candidates."""
        import numpy as np
        from harness.image import to_png_bytes

        _, anchor = self._screen()
        image = np.full((300, 500, 3), 40, dtype=np.uint8)
        image[60:100, 200:280] = anchor
        image[160:200, 100:180] = anchor
        driver, strategy = self._driver(to_png_bytes(image), anchor, tmp_path)

        handle, note = driver._resolve_anchor(strategy)
        assert handle is None
        assert note == "2 visual match(es)"
        assert not driver.warnings, "and nothing is blamed on the DPI"


class TestTypingAtAPixel:
    """An anchor is a location, not a control, so focusing it means clicking
    it. Human-speed typing sends one character at a time -- so a click inside
    typing puts the caret back where the click landed, on every keystroke."""

    def fake_pywinauto(self, monkeypatch):
        import sys, types

        events = []
        mouse = types.ModuleType("pywinauto.mouse")
        mouse.click = lambda **kw: events.append(("click", kw.get("coords")))
        mouse.move = lambda **kw: events.append(("move", kw.get("coords")))
        keyboard = types.ModuleType("pywinauto.keyboard")
        keyboard.send_keys = lambda keys, **kw: events.append(("keys", keys))
        package = types.ModuleType("pywinauto")
        package.mouse, package.keyboard = mouse, keyboard
        for name, module in (("pywinauto", package),
                             ("pywinauto.mouse", mouse),
                             ("pywinauto.keyboard", keyboard)):
            monkeypatch.setitem(sys.modules, name, module)
        return events

    def a_point(self):
        driver = NativeDriver()
        driver.to_screen = lambda x, y: (x, y)
        driver.move_pointer_to = lambda x, y: None
        return _Point(driver, Match(x=10, y=10, width=20, height=10, score=1.0))

    def test_typing_does_not_click(self, monkeypatch):
        """The bug: "Found by picture, typed by keystroke" came back as
        "Found by pic.ekortsyek yb depyt ,erut" -- reversed from the click
        point, one character at a time."""
        events = self.fake_pywinauto(monkeypatch)
        point = self.a_point()

        for character in "abc":
            point.type_keys(character)

        assert [kind for kind, _ in events] == ["keys", "keys", "keys"], \
            f"something moved the mouse mid-typing: {events}"

    def test_focusing_a_pixel_clicks_it(self, monkeypatch):
        """Because that is the only way a location can take focus. It just
        has to happen once, before the typing, not inside it."""
        events = self.fake_pywinauto(monkeypatch)
        point = self.a_point()

        point.set_focus()

        assert ("click", (20, 15)) in events, events


class TestWhenTheWindowIsAmbiguous:
    """Two windows matching one pattern is not an edge case on a CAD
    workstation: CATIA V5 and 3DX side by side, two documents open, a splash
    screen that has not gone away yet."""

    def test_it_names_the_windows_it_could_not_choose_between(self):
        message = attach_failure(
            "*Notepad*", None, RuntimeError("There are 2 elements"),
            ["Untitled - Notepad", "anchors.txt - Notepad"],
        )
        assert "Untitled - Notepad" in message
        assert "anchors.txt - Notepad" in message
        assert "window_title_pattern" in message, "says nothing about the fix"

    def test_one_window_that_simply_would_not_attach_reads_normally(self):
        message = attach_failure("*CATIA*", None, RuntimeError("timed out"), [])
        assert "could not attach to '*CATIA*'" in message
        assert "timed out" in message


class TestUnavailablePaths:
    def test_starting_without_pywinauto_says_what_to_install(self):
        # Only meaningful where pywinauto is genuinely absent. Where it is
        # installed the driver gets past the import and spends a minute
        # waiting for a CATIA window that is not there.
        try:
            import pywinauto  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("pywinauto is installed, so this path cannot be reached")

        with pytest.raises(DriverError, match="pywinauto"):
            NativeDriver().start({"window_title_pattern": "*CATIA*"})

    def test_a_flow_with_no_native_app_config_is_a_clear_error(self, monkeypatch):
        import sys, types

        # Pretend pywinauto is importable so the next check is the one that fires.
        module = types.ModuleType("pywinauto")
        module.Application = object
        module.Desktop = object
        monkeypatch.setitem(sys.modules, "pywinauto", module)

        with pytest.raises(DriverError, match="window_title_pattern"):
            NativeDriver().start({})

    def test_level_two_reset_is_refused_with_a_reason(self):
        with pytest.raises(DriverError, match="level-1 reset steps"):
            NativeDriver().reset()

    def test_using_it_before_start_is_a_clear_error(self):
        with pytest.raises(DriverError, match="before start"):
            NativeDriver().refresh()


class TestPickingTheWindowOutOfACrowd:
    """3DEXPERIENCE runs as several processes, more than one of which owns a
    top-level window of the same name. Attaching by title alone used to fail
    every replay with pywinauto's "there are 6 elements that match"."""

    CLIENT = OpenWindow(title="3DEXPERIENCE R2026x", pid=1, process="CATIA.exe",
                        width=1920, height=1040, visible=True, wrapper="the client")
    SPLASH = OpenWindow(title="3DEXPERIENCE", pid=2, process="CATSplash.exe",
                        width=400, height=300, visible=False, wrapper="the splash")
    OTHER = OpenWindow(title="3DEXPERIENCE R2026x", pid=3, process="CATIA.exe",
                       width=1600, height=900, visible=True, wrapper="another document")

    def _windows(self, monkeypatch, *windows):
        monkeypatch.setattr("understudy.drivers.native.open_windows",
                            lambda pattern: list(windows))

    def test_the_one_live_window_is_taken_over_its_splash_screen(self, monkeypatch):
        self._windows(monkeypatch, self.CLIENT, self.SPLASH)
        driver = NativeDriver()
        assert driver._attach("*3DEXPERIENCE*", None) == "the client"

    def test_choosing_is_recorded_rather_than_done_quietly(self, monkeypatch):
        self._windows(monkeypatch, self.CLIENT, self.SPLASH)
        driver = NativeDriver()
        driver._attach("*3DEXPERIENCE*", None)
        assert any("chosen over 1 other window" in w for w in driver.warnings), \
            driver.warnings

    def test_the_ordinary_single_match_says_nothing(self, monkeypatch):
        self._windows(monkeypatch, self.CLIENT)
        driver = NativeDriver()
        driver._attach("*Notepad*", None)
        assert driver.warnings == [], "one candidate is not a choice worth reporting"

    def test_two_live_windows_are_refused_with_both_named(self, monkeypatch):
        self._windows(monkeypatch, self.CLIENT, self.OTHER)
        with pytest.raises(DriverError) as raised:
            NativeDriver()._attach("*3DEXPERIENCE*", None)
        message = str(raised.value)
        assert "matches 2 windows" in message
        assert "pid 1" in message and "pid 3" in message

    def test_the_process_key_settles_it(self, monkeypatch):
        rival = OpenWindow(title="3DEXPERIENCE R2026x", pid=4,
                           process="CATSplash.exe", width=1900, height=1000,
                           visible=True, wrapper="the impostor")
        self._windows(monkeypatch, self.CLIENT, rival)
        assert NativeDriver()._attach("*3DEXPERIENCE*", "CATIA") == "the client"

    def test_narrowing_by_process_is_offered_when_processes_differ(self, monkeypatch):
        mixed = OpenWindow(title="3DEXPERIENCE R2026x", pid=4, process="CATSplash.exe",
                           width=1900, height=1000, visible=True)
        self._windows(monkeypatch, self.CLIENT, mixed)
        with pytest.raises(DriverError, match="target_app.native.process"):
            NativeDriver()._attach("*3DEXPERIENCE*", None)
