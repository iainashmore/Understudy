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

    def test_the_whole_window_is_kept_beside_each_anchor(self, session, tmp_path):
        """The anchor is what the driver matches on; the screen is what makes
        the recording reviewable afterwards. Only the first was checked, and
        "no screenshots in the folder" was the first thing asked about it."""
        click(session, 280, 130)
        record_native.write(session, "leo-recorded", "LEO", tmp_path,
                            {"window_title_pattern": "x"})
        anchors = tmp_path / "anchors" / "leo-recorded"
        assert (anchors / "screens" / "target_1.png").exists()
        assert (anchors / "recording.json").exists()

        import json

        entry = json.loads((anchors / "recording.json").read_text())[0]
        assert entry["point"] == [280, 130]
        assert (anchors / entry["screen"]).exists(), \
            "the manifest points at a file that is really there"

    def test_the_anchors_land_where_the_flow_looks_for_them(self, session, tmp_path):
        click(session, 280, 130)
        record_native.write(session, "leo-recorded", "LEO", tmp_path,
                          {"window_title_pattern": "x"})
        assert (tmp_path / "anchors" / "leo-recorded" / "target_1.png").exists()


class TestNotLosingARecording:
    """Everything was held in memory until the recording stopped. A session
    that was killed -- or whose stop did not work, which is how this was found
    -- lost every click."""

    def test_each_click_is_written_as_it_happens(self, session, tmp_path):
        session.save, session.save_screen = record_native.saver(tmp_path, "leo")
        click(session, 280, 130)
        anchors = tmp_path / "anchors" / "leo"
        assert (anchors / "target_1.png").exists()
        assert (anchors / "screens" / "target_1.png").exists()

    def test_a_click_outside_the_window_writes_nothing(self, session, tmp_path):
        session.save, session.save_screen = record_native.saver(tmp_path, "leo")
        click(session, -5000, 130)
        assert not (tmp_path / "anchors").exists()

    def test_stopping_unhooks_as_well_as_waking_the_loop(self):
        """Waking the loop is not enough on its own: the loop's condition is
        the hook's own flag, and only the hook can clear it."""
        from understudy.recorder import Recorder

        unhooked = []

        class FakeHook:
            def stop(self):
                unhooked.append(True)

        made = record_native.Session(Recorder(), lambda: None, lambda: (0, 0))
        made.hook = FakeHook()
        record_native.stop(made)
        assert unhooked == [True]
        assert made.stopped.is_set()

    def test_a_hook_that_throws_on_stop_does_not_stop_the_stop(self):
        from understudy.recorder import Recorder

        class AngryHook:
            def stop(self):
                raise RuntimeError("already unhooked")

        made = record_native.Session(Recorder(), lambda: None, lambda: (0, 0))
        made.hook = AngryHook()
        record_native.stop(made)
        assert made.stopped.is_set()


class TestSayingWhatIsMissing:
    """A recording can capture every click and still produce a flow that
    proves nothing. The first real one did exactly that: no reply region, so
    it drove the application and recorded the answer nowhere."""

    def test_no_reply_region_is_the_one_that_matters(self, session):
        click(session, 280, 130)
        problems = record_native.problems_with(session)
        assert any("records nothing" in p for p in problems)
        assert any("wait for the whole answer" in p for p in problems)

    def test_no_clicks_at_all_says_to_press_record_first(self, session):
        problems = record_native.problems_with(session)
        assert any("Press Record first" in p for p in problems)

    def test_typing_before_any_click_is_carried_through(self, session):
        for character in "hello":
            press(session, character)
            release(session, character)
        click(session, 280, 130)
        assert any("outside" in p or "before any click" in p
                   for p in record_native.problems_with(session))

    def test_a_complete_recording_has_nothing_to_say(self, session):
        click(session, 280, 130)
        for character in "hello":
            press(session, character)
            release(session, character)
        press(session, "Return")
        session.shot = lambda: session.answered
        session.finish()
        assert record_native.problems_with(session) == []


class TestWhatTheFlowIsCalled:
    def test_the_title_is_the_name_it_was_given(self, session, tmp_path):
        """It used to be the window's title, which says what was driven rather
        than what the flow does -- every recording against 3DEXPERIENCE came
        out called 3DEXPERIENCE."""
        assert record_native.readable("leo-basics") == "Leo basics"
        assert record_native.readable("tolerance_check") == "Tolerance check"


class TestTheBaselineTheReplyIsMeasuredAgainst:
    """Where the answer appeared is worked out by diffing the screen at the
    last recorded action against the screen at stop. Which screen counts as
    "before" is the whole of it."""

    def test_a_click_outside_the_window_does_not_move_the_baseline(self, session):
        """The click that reaches Stop in the browser is outside the window,
        and by then the answer is already on screen. Taking that as "before"
        left every recording stopped from the app with no reply region."""
        click(session, 280, 130)                 # in the window
        before = session.last_screen
        session.shot = lambda: session.answered  # the answer arrives
        click(session, -5000, 500)               # reaching for Stop
        assert session.last_screen is before

        session.finish()
        assert session.read_region == {"x": 492, "y": 292,
                                       "width": 316, "height": 216}

    def test_a_recorded_click_does_move_it(self, session):
        click(session, 280, 130)
        session.shot = lambda: session.answered
        click(session, 300, 140)
        assert session.last_screen is session.answered

    def test_stopping_from_the_browser_still_finds_the_reply(self, session):
        """The whole sequence, in the order the app makes people do it."""
        click(session, 280, 130)
        for character in "hello":
            press(session, character)
            release(session, character)
        press(session, "Return")
        session.shot = lambda: session.answered   # LEO answers
        click(session, 1472, 503)                 # the browser's Stop button
        session.finish()
        assert session.read_region is not None
        assert record_native.problems_with(session) == []


class TestSeeingBothEnds:
    """Every other screen is one taken *before* a click, so a recording could
    show every click and never the answer -- the one picture somebody wants.
    Start and end are kept whatever happens, including when nothing at all was
    captured, which is exactly when somebody wants to see the screen."""

    def _wired(self, session, tmp_path):
        session.save, session.save_screen = record_native.saver(tmp_path, "leo")
        return tmp_path / "anchors" / "leo" / "screens"

    def test_the_screen_at_the_start_is_kept(self, session, tmp_path):
        screens = self._wired(session, tmp_path)
        session.begin()
        assert (screens / "start.png").exists()

    def test_the_screen_at_the_end_is_kept(self, session, tmp_path):
        screens = self._wired(session, tmp_path)
        click(session, 280, 130)
        session.shot = lambda: session.answered
        session.finish()
        assert (screens / "end.png").exists()

    def test_both_are_kept_even_when_nothing_was_recorded(self, session, tmp_path):
        screens = self._wired(session, tmp_path)
        session.begin()
        session.finish()
        assert (screens / "start.png").exists()
        assert (screens / "end.png").exists()
        assert session.read_region is None, "and it still found no reply"

    def test_the_screen_the_region_was_measured_against_is_kept_too(
            self, session, tmp_path):
        """A region that looks wrong is only checkable against the pair it
        came from."""
        screens = self._wired(session, tmp_path)
        click(session, 280, 130)
        session.shot = lambda: session.answered
        session.finish()
        assert (screens / "before-reply.png").read_bytes() != \
               (screens / "end.png").read_bytes()


def test_the_description_names_the_window_not_the_flow(session, tmp_path):
    """The title is what the flow is about; the description is what it was
    recorded against. Both were the same string, so it read "Leo basics,
    recorded against Leo basics"."""
    import yaml

    click(session, 280, 130)
    path = record_native.write(session, "leo-basics", "Leo basics", tmp_path,
                               {"window_title_pattern": "3DEXPERIENCE"},
                               description="Recorded against 3DEXPERIENCE")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["title"] == "Leo basics"
    assert document["description"] == "Recorded against 3DEXPERIENCE"
