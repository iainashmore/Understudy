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
    blank = np.zeros((600, 900, 3), dtype=np.uint8)
    blank[98:162, 200:360] = (30, 144, 255)
    answered = blank.copy()
    answered[300:500, 500:800] = 200          # a reply arrives here
    made = record_native.Session(Recorder(), lambda: blank, lambda: (0, 0))
    made.blank, made.answered = blank, answered
    return made


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


class TestStoppingAndFindingTheReply:
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

    def test_the_reply_region_is_whatever_changed_after_the_last_step(self, session):
        """Nobody draws a box. Marking it by hand meant a modifier and two
        clicks, and the first real attempt produced a 4x10 region."""
        click(session, 280, 130)
        press(session, "Return")
        session.shot = lambda: session.answered
        session.finish()
        assert session.read_region == {"x": 492, "y": 292,
                                       "width": 316, "height": 216}

    def test_a_screen_that_did_not_change_writes_no_read_step(self, session):
        click(session, 280, 130)
        press(session, "Return")
        session.finish()
        assert session.read_region is None

    def test_a_caret_sized_change_is_not_an_answer(self, session):
        """The failure this replaces: a 4x10 region, OCR returning nothing,
        and no clue in the run about why."""
        click(session, 280, 130)
        press(session, "Return")
        tiny = session.blank.copy()
        tiny[300:308, 400:404] = 255
        session.shot = lambda: tiny
        session.finish()
        assert session.read_region is None


class TestWhatItWrites:
    def test_a_whole_session_produces_a_flow_that_loads(self, session, tmp_path):
        click(session, 280, 130)
        for character in "What is this part for?":
            press(session, character)
            release(session, character)
        press(session, "Return")
        session.shot = lambda: session.answered
        session.finish()

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
