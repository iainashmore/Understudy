"""Window and monitor geometry.

The bug this exists to prevent: an anchor match is in window coordinates
(a screenshot of the window is what it was found in), the mouse is driven in
virtual-desktop coordinates, and on a single monitor with the window at the
origin the two are identical. Every test that matters here puts the window
somewhere else.
"""

from __future__ import annotations

import pytest

from understudy.drivers.native import NativeDriver, _Point
from understudy.geometry import Monitor, WindowGeometry, monitor_for
from understudy.vision import Match

PRIMARY = Monitor("\\\\.\\DISPLAY1", 0, 0, 1920, 1080, primary=True, scale=1.0)
#: To the right, and taller, so its origin is positive but its rows are not.
RIGHT = Monitor("\\\\.\\DISPLAY2", 1920, -200, 4480, 1240, scale=1.0)
#: To the left of the primary: the virtual desktop puts the primary at (0, 0),
#: so everything left of it is negative. This is the one that catches bugs.
LEFT = Monitor("\\\\.\\DISPLAY3", -2560, 0, 0, 1440, scale=1.5)


class TestCoordinateTranslation:
    def test_the_origin_case_hides_the_bug(self):
        """Window at (0, 0): window and screen coordinates coincide. This is
        why the mistake survives a single-monitor test."""
        geometry = WindowGeometry(0, 0, 1200, 800)
        assert geometry.to_screen(300, 400) == (300, 400)

    def test_a_window_on_the_second_monitor_needs_the_offset(self):
        geometry = WindowGeometry(2100, 100, 3300, 900, monitor=RIGHT.name)
        assert geometry.to_screen(300, 400) == (2400, 500)

    def test_a_monitor_left_of_the_primary_has_negative_coordinates(self):
        """The case that goes badly wrong: a click at window (300, 400) belongs
        at screen (-2260, 500), not (300, 400)."""
        geometry = WindowGeometry(-2560, 100, -1360, 900, monitor=LEFT.name)
        assert geometry.to_screen(300, 400) == (-2260, 500)

    def test_translation_round_trips(self):
        geometry = WindowGeometry(-2560, -300, -1360, 500)
        assert geometry.to_window(*geometry.to_screen(120, 240)) == (120, 240)

    def test_a_screen_point_can_be_tested_for_membership(self):
        geometry = WindowGeometry(1920, 0, 3200, 800)
        assert geometry.contains_screen_point(2000, 100)
        assert not geometry.contains_screen_point(100, 100)


class TestPlacementChanges:
    def test_moving_the_window_does_not_invalidate_anchors(self):
        """An anchor is re-located in each run's own screenshot, so a move is
        fine. That is the whole point of anchoring over stored coordinates."""
        before = WindowGeometry(0, 0, 1200, 800, scale=1.0)
        after = WindowGeometry(1920, 300, 3120, 1100, scale=1.0)
        assert after.same_placement_as(before)

    def test_resizing_does_invalidate_them(self):
        before = WindowGeometry(0, 0, 1200, 800)
        after = WindowGeometry(0, 0, 1000, 700)
        assert not after.same_placement_as(before)

    def test_a_different_dpi_scale_invalidates_them(self):
        """Same size in logical pixels, different physical pixels. The anchor
        image itself no longer matches."""
        before = WindowGeometry(0, 0, 1200, 800, scale=1.0)
        after = WindowGeometry(-2560, 0, -1360, 800, scale=1.5)
        assert not after.same_placement_as(before)


class TestMonitorSelection:
    MONITORS = [PRIMARY, RIGHT, LEFT]

    def test_a_window_is_placed_by_its_centre(self):
        found = monitor_for(self.MONITORS, WindowGeometry(2100, 100, 3300, 900))
        assert found is RIGHT

    def test_a_window_straddling_two_screens_belongs_to_the_one_showing_most(self):
        """Its top-left corner is on the primary; most of it is not."""
        straddling = WindowGeometry(1800, 100, 3000, 900)
        assert monitor_for(self.MONITORS, straddling) is RIGHT

    def test_a_window_on_the_negative_monitor_is_found(self):
        assert monitor_for(self.MONITORS, WindowGeometry(-2000, 200, -800, 900)) is LEFT

    def test_a_window_off_every_screen_returns_nothing(self):
        assert monitor_for(self.MONITORS, WindowGeometry(9000, 9000, 9200, 9200)) is None

    def test_no_monitors_is_not_a_crash(self):
        assert monitor_for([], WindowGeometry(0, 0, 100, 100)) is None


class TestAnchorClicksLandOnTheRightMonitor:
    """The end-to-end version of the bug, through the object that clicks."""

    def driver_at(self, geometry: WindowGeometry) -> NativeDriver:
        driver = NativeDriver()
        driver.geometry = geometry
        return driver

    def test_an_anchor_click_is_translated_to_screen_space(self):
        driver = self.driver_at(WindowGeometry(1920, 300, 3120, 1100, monitor=RIGHT.name))
        point = _Point(driver, Match(100, 200, 40, 20, 0.99))

        assert point.point == (120, 210), "window-relative, as the match was"
        assert point.screen_point == (2040, 510), "screen-relative, as the mouse needs"

    def test_the_offset_is_applied_before_the_translation(self):
        driver = self.driver_at(WindowGeometry(1920, 300, 3120, 1100))
        point = _Point(driver, Match(100, 200, 40, 20, 0.99), offset={"dx": 0, "dy": 26})
        assert point.screen_point == (2040, 536)

    def test_a_negative_origin_monitor_gives_negative_screen_coordinates(self):
        driver = self.driver_at(WindowGeometry(-2560, 0, -1360, 800, monitor=LEFT.name))
        point = _Point(driver, Match(100, 200, 40, 20, 0.99))
        assert point.screen_point == (-2440, 210)

    def test_unknown_geometry_refuses_to_guess(self):
        """Clicking at an unknown offset would be a click somewhere arbitrary."""
        from understudy.drivers.base import DriverError

        point = _Point(NativeDriver(), Match(100, 200, 40, 20, 0.99))
        with pytest.raises(DriverError, match="geometry is unknown"):
            point.screen_point


def test_geometry_describes_itself_for_the_log():
    described = WindowGeometry(-2560, 0, -1360, 800, monitor="\\\\.\\DISPLAY3", scale=1.5)
    assert described.describe() == "1200x800 at (-2560, 0) on \\\\.\\DISPLAY3 @1.5x"


def test_dpi_awareness_is_reported_not_assumed():
    from understudy.geometry import make_dpi_aware

    # Off Windows this is a no-op, and says so rather than pretending.
    assert make_dpi_aware() == "not windows"


def test_enumerating_monitors_off_windows_is_empty_not_an_error():
    from understudy.geometry import enumerate_monitors

    assert enumerate_monitors() == []
