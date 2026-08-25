"""Clicking with the real desktop cursor.

The page is found through CDP and clicked through Windows, which means the two
coordinate systems have to be reconciled: page pixels, which start at the
viewport's top-left corner, and screen pixels, which start at the top-left of
the desktop and are physical rather than CSS.

Getting this wrong is the same class of bug as the multi-monitor one, and it
hides the same way: on an unscaled display with the window at the origin the two
systems are identical.
"""

from __future__ import annotations

import pytest

from understudy.cursor import MouseStyle
from understudy.drivers.base import Resolution
from understudy.drivers.web import WebDriver, _AnchorTarget
from understudy.flow import Target
from understudy.keyboard import TypingStyle
from understudy.os_pointer import ViewportOrigin, origin_from_window
from understudy.vision import Match

TARGET = Target(name="prompt_box", intent="", strategies={})


# -- the arithmetic -----------------------------------------------------------


def test_a_bare_webview_has_no_chrome_to_correct_for():
    """WebView2 inside CATIA: the window *is* the page."""
    origin = origin_from_window(400, 250, 800, 800, 600, 600, 1.0)
    assert origin == ViewportOrigin(x=400, y=250, scale=1.0)
    assert origin.to_screen(0, 0) == (400, 250)
    assert origin.to_screen(120, 40) == (520, 290)


def test_a_browser_windows_chrome_is_subtracted():
    origin = origin_from_window(0, 0, 916, 900, 787, 700, 1.0)
    assert origin.to_screen(0, 0) == (8, 87)


def test_display_scaling_multiplies_both_the_origin_and_the_offset():
    """150% scaling: CSS pixels are 1.5 physical pixels, including the corner."""
    origin = origin_from_window(1000, 200, 800, 800, 600, 600, 1.5)
    assert origin.to_screen(0, 0) == (1500, 300)
    assert origin.to_screen(100, 100) == (1650, 450)


def test_a_second_monitor_left_of_the_primary_has_negative_coordinates():
    origin = origin_from_window(-1920, 0, 800, 800, 600, 600, 1.0)
    assert origin.to_screen(50, 50) == (-1870, 50)


def test_a_missing_device_pixel_ratio_does_not_collapse_the_mapping():
    origin = origin_from_window(100, 100, 800, 800, 600, 600, 0)
    assert origin.scale == 1.0
    assert origin.to_screen(10, 10) == (110, 110)


def test_read_origin_survives_a_page_that_will_not_answer():
    class Mute:
        def evaluate(self, script):
            raise RuntimeError("execution context destroyed")

    class Wrong:
        def evaluate(self, script):
            return None

    from understudy.os_pointer import read_origin

    assert read_origin(Mute()) is None
    assert read_origin(Wrong()) is None


# -- which pointer the driver uses --------------------------------------------


@pytest.fixture
def cursor_exists(monkeypatch):
    monkeypatch.setattr("understudy.os_pointer.available", lambda: True)
    monkeypatch.setattr("understudy.os_pointer.unavailable_reason", lambda: None)


def test_the_desktop_cursor_is_the_default(cursor_exists):
    """Anything watching the screen from outside sees nothing otherwise."""
    driver = WebDriver(headless=False)
    assert driver.pointer_input == "os"
    assert driver.uses_os_pointer
    assert driver.pointer_note is None


def test_it_can_be_turned_off(cursor_exists):
    driver = WebDriver(headless=False)
    driver.pointer_input = "cdp"
    assert not driver.uses_os_pointer
    assert "cdp" in driver.pointer_note


def test_headless_degrades_rather_than_clicking_the_desktop(cursor_exists):
    """There is no window on screen, so an OS click would hit something else."""
    driver = WebDriver(headless=True)
    assert not driver.uses_os_pointer
    assert "headless" in driver.pointer_note


def test_an_attached_headless_host_still_uses_the_desktop(cursor_exists):
    """Attached means somebody else's window, which is on screen."""
    driver = WebDriver(headless=True)
    driver.attached = True
    assert driver.uses_os_pointer


def test_a_machine_without_a_cursor_says_so(monkeypatch):
    monkeypatch.setattr("understudy.os_pointer.available", lambda: False)
    monkeypatch.setattr("understudy.os_pointer.unavailable_reason",
                        lambda: "no X display; is DISPLAY set?")
    driver = WebDriver(headless=False)
    assert not driver.uses_os_pointer
    assert "no X display" in driver.pointer_note


# -- the driver's routing -----------------------------------------------------


class FakeMouse:
    def __init__(self):
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []

    def move(self, x, y):
        self.moves.append((int(x), int(y)))

    def click(self, x, y):
        self.clicks.append((int(x), int(y)))


class FakePage:
    def __init__(self):
        self.mouse = FakeMouse()
        self.viewport_size = {"width": 900, "height": 700}
        self.keyboard = None


class FakeLocator:
    def __init__(self, box):
        self._box = box
        self.clicks = 0

    def bounding_box(self):
        return self._box

    def click(self, timeout=None):
        self.clicks += 1

    def fill(self, text, timeout=None):
        pass


@pytest.fixture
def os_driver(monkeypatch):
    """A driver wired to click through a fake operating system."""
    cursor: dict[str, list] = {"moved": [], "clicked": []}
    monkeypatch.setattr("understudy.drivers.web.set_cursor",
                        lambda x, y: cursor["moved"].append((x, y)))
    monkeypatch.setattr("understudy.drivers.web.click_at",
                        lambda x, y: cursor["clicked"].append((x, y)))
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    monkeypatch.setattr("understudy.os_pointer.available", lambda: True)
    driver = WebDriver(headless=False)
    driver.page = FakePage()
    driver.pointer_input = "os"
    driver.typing_style = TypingStyle(mode="instant")
    # A panel at (2000, 300) on a second screen, unscaled.
    driver._origin = ViewportOrigin(x=2000, y=300, scale=1.0)
    driver._origin_checked = True
    return driver, cursor


def test_the_desktop_cursor_is_clicked_at_the_translated_point(os_driver, monkeypatch):
    driver, cursor = os_driver
    locator = FakeLocator({"x": 100, "y": 50, "width": 40, "height": 20})
    monkeypatch.setattr(driver, "resolve", lambda t, ms: (locator, Resolution("t", 0)))

    driver.click(TARGET, timeout_ms=1000)

    assert cursor["clicked"] == [(2120, 360)]     # page (120, 60) on screen
    assert locator.clicks == 0                    # no synthetic click as well


def test_the_desktop_cursor_travels_rather_than_jumping(os_driver, monkeypatch):
    driver, cursor = os_driver
    locator = FakeLocator({"x": 700, "y": 400, "width": 40, "height": 20})
    monkeypatch.setattr(driver, "resolve", lambda t, ms: (locator, Resolution("t", 0)))

    driver.click(TARGET, timeout_ms=1000)

    assert len(cursor["moved"]) > 10
    assert cursor["moved"][-1] == (2720, 710)
    # every intermediate point is on screen, offset by the panel's corner
    assert all(x >= 2000 and y >= 300 for x, y in cursor["moved"])


def test_the_page_still_sees_the_synthetic_move(os_driver, monkeypatch):
    """Hover states have to follow the travel, or the recording looks dead."""
    driver, _ = os_driver
    locator = FakeLocator({"x": 700, "y": 400, "width": 40, "height": 20})
    monkeypatch.setattr(driver, "resolve", lambda t, ms: (locator, Resolution("t", 0)))

    driver.click(TARGET, timeout_ms=1000)
    assert len(driver.page.mouse.moves) == len(_moved(driver))


def _moved(driver):
    return driver.page.mouse.moves


def test_an_anchor_match_clicks_through_the_desktop_too(os_driver):
    driver, cursor = os_driver
    handle = _AnchorTarget(driver.page, Match(x=10, y=20, width=40, height=10,
                                              score=1.0), driver=driver)
    handle.click()

    assert cursor["clicked"] == [(2030, 325)]     # centre (30, 25) on screen
    assert driver.page.mouse.clicks == []


def test_without_a_usable_origin_it_falls_back_to_a_synthetic_click(monkeypatch):
    monkeypatch.setattr("understudy.os_pointer.available", lambda: True)
    driver = WebDriver(headless=False)
    driver.page = FakePage()
    driver.pointer_input = "os"
    driver.mouse_style = MouseStyle(mode="instant")
    driver._origin = None
    driver._origin_checked = True
    locator = FakeLocator({"x": 100, "y": 50, "width": 40, "height": 20})
    monkeypatch.setattr(driver, "resolve", lambda t, ms: (locator, Resolution("t", 0)))

    driver.click(TARGET, timeout_ms=1000)
    assert locator.clicks == 1
