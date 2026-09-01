"""The recording session's sequencing.

The Windows hooks cannot run here, but what they drive can: a click while a
region is being marked means something different from a click that is being
recorded, a shortcut is not text, and a modifier is not a keystroke. Those are
the parts that go wrong, and none of them need a desktop.
"""

from __future__ import annotations

import numpy as np
import pytest

from understudy import record_native
from understudy.flow import parse_flow
from understudy.recorder import Recorder


class Event:
    """What pywinauto hands a hook handler."""

    def __init__(self, event_type, current_key, mouse_x=0, mouse_y=0):
        self.event_type = event_type
        self.current_key = current_key
        self.mouse_x = mouse_x
        self.mouse_y = mouse_y


@pytest.fixture
def session():
    window = np.zeros((600, 900, 3), dtype=np.uint8)
    window[98:162, 200:360] = (30, 144, 255)
    return record_native.Session(Recorder(), lambda: window, lambda: (0, 0))


def press(session, key, x=0, y=0):
    record_native.dispatch(session, Event("key down", key, x, y))


def release(session, key):
    record_native.dispatch(session, Event("key up", key))


def click(session, x, y):
    press(session, "LButton", x, y)
    record_native.dispatch(session, Event("key up", "LButton", x, y))


class TestWhatGetsRecorded:
    def test_a_left_click_is_recorded_once_not_twice(self, session):
        click(session, 280, 130)
        assert len([s for s in session.recorder.steps if s["action"] == "click"]) == 1

    def test_a_right_click_is_left_alone(self, session):
        """Only because handling it properly means context menus, which the
        MVP does not do. Silently recording it as a left click would be worse
        than not recording it."""
        press(session, "RButton", 280, 130)
        assert session.recorder.steps == []

    def test_typing_is_recorded_as_text(self, session):
        click(session, 280, 130)
        for character in "hi":
            press(session, character)
            release(session, character)
        document = session.recorder.flow("f", "F", {"window_title_pattern": "x"})
        assert document["prompts"][0]["prompt"] == "hi"

    def test_a_modifier_is_not_a_keystroke(self, session):
        press(session, "Lcontrol")
        release(session, "Lcontrol")
        assert session.recorder.steps == []
        assert session.recorder._typed == []

    def test_a_shortcut_is_not_something_typed(self, session):
        """Ctrl+C during a recording is the person copying something, not the
        letter c."""
        press(session, "Lcontrol")
        press(session, "c")
        release(session, "c")
        release(session, "Lcontrol")
        assert session.recorder._typed == []

    def test_enter_survives_as_an_instruction(self, session):
        click(session, 280, 130)
        press(session, "Return")
        assert session.recorder.steps[-1] == {"action": "key", "keys": "{ENTER}"}


class TestStoppingAndMarking:
    def test_the_stop_hotkey_stops(self, session):
        press(session, "Lcontrol")
        press(session, "Lmenu")
        press(session, "s")
        assert session.stopped.is_set()

    def test_the_stop_hotkey_is_not_also_typed(self, session):
        press(session, "Lcontrol")
        press(session, "Lmenu")
        press(session, "s")
        assert session.recorder._typed == []

    def test_marking_takes_two_clicks_and_records_no_steps(self, session):
        press(session, "Lcontrol")
        press(session, "Lmenu")
        press(session, "r")
        release(session, "r")
        click(session, 1502, 215)
        click(session, 1926, 883)
        assert session.read_region == {"x": 1502, "y": 215,
                                       "width": 424, "height": 668}
        assert session.recorder.steps == [], "marking a region is not a click"

    def test_corners_in_either_order(self, session):
        session.hotkey(record_native.MARK)
        click(session, 1926, 883)
        click(session, 1502, 215)
        assert session.read_region["x"] == 1502 and session.read_region["y"] == 215

    def test_the_region_is_window_relative(self, session):
        session.origin = lambda: (100, 50)
        session.hotkey(record_native.MARK)
        click(session, 200, 100)
        click(session, 300, 200)
        assert session.read_region == {"x": 100, "y": 50,
                                       "width": 100, "height": 100}

    def test_clicking_after_marking_is_recorded_again(self, session):
        session.hotkey(record_native.MARK)
        click(session, 10, 10)
        click(session, 110, 110)
        click(session, 280, 130)
        assert len(session.recorder.steps) == 1


class TestWhatItWrites:
    def test_a_whole_session_produces_a_flow_that_loads(self, session, tmp_path):
        click(session, 280, 130)
        for character in "What is this part for?":
            press(session, character)
            release(session, character)
        press(session, "Return")
        session.hotkey(record_native.MARK)
        click(session, 1502, 215)
        click(session, 1926, 883)

        path = record_native.write(
            session, "leo-recorded", "LEO", tmp_path,
            {"window_title_pattern": "3DEXPERIENCE", "process": "3DEXPERIENCE.exe"},
        )
        assert path.exists()

        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        flow = parse_flow(document, source_path=path)
        flow.validate_for_backend("native")
        assert flow.variables() == {"prompt"}
        assert [s.action for s in flow.steps][-1] == "read"

    def test_the_anchors_land_where_the_flow_looks_for_them(self, session, tmp_path):
        click(session, 280, 130)
        record_native.write(session, "leo-recorded", "LEO", tmp_path,
                          {"window_title_pattern": "x"})
        assert (tmp_path / "anchors" / "leo-recorded" / "target_1.png").exists()
