"""Turning a demonstration into a flow.

Everything here is the decision-making half: what a click becomes, what a
sentence becomes, which text turns into the prompt. The Windows hooks that
feed it are a separate, thin thing that cannot run here.
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.image import load_rgb
from understudy.flow import parse_flow
from understudy.recorder import Recorder


@pytest.fixture
def window():
    """A window with distinguishable content, so a crop can be checked."""
    image = np.zeros((600, 900, 3), dtype=np.uint8)
    # Exactly one anchor's worth of blue, centred on (280, 130).
    image[98:162, 200:360] = (30, 144, 255)
    image[398:462, 600:760] = (255, 128, 0)
    # Texture everywhere, because a crop of flat colour is deliberately
    # widened until it has something in it -- which is the subject of its own
    # tests below, and noise in these.
    image[::4, :] = 200
    return image


APP = {"window_title_pattern": "3DEXPERIENCE", "process": "3DEXPERIENCE.exe"}


class TestWhatAClickBecomes:
    def test_a_click_becomes_a_picture_of_where_it_was(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        anchor = recorder.anchors[0]
        assert (anchor.width, anchor.height) == (160, 64)
        assert load_rgb(anchor.png).shape == (64, 160, 3)

    def test_the_picture_is_centred_on_the_click(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        cut = load_rgb(recorder.anchors[0].png)
        assert (cut == window[98:162, 200:360]).all(), \
            "the crop is the click, not near it"
        assert (recorder.anchors[0].dx, recorder.anchors[0].dy) == (0, 0)

    def test_screen_coordinates_are_made_window_relative(self, window):
        """The hooks report where the pointer is on the desktop; anchors and
        offsets are window-relative so the flow survives the window moving."""
        recorder = Recorder()
        recorder.click(1280, 530, window, origin=(1000, 400))
        assert (load_rgb(recorder.anchors[0].png) == window[98:162, 200:360]).all()

    def test_a_click_near_an_edge_still_records_the_right_point(self, window):
        """Clamped rather than refused -- clicking near an edge is ordinary --
        and the offset carries the difference so the driver acts on the point
        that was clicked, not the middle of the crop."""
        recorder = Recorder()
        recorder.click(10, 12, window)
        anchor = recorder.anchors[0]
        assert (anchor.dx, anchor.dy) == (10 - 80, 12 - 32)
        assert load_rgb(anchor.png).shape == (64, 160, 3)


class TestWhatTypingBecomes:
    def test_a_sentence_is_one_step_not_thirty_four(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        for character in "hello there":
            recorder.text(character)
        document = recorder.flow("f", "F", APP)
        typing = [s for s in document["steps"] if s["action"] == "type"]
        assert len(typing) == 1

    def test_typing_goes_to_the_thing_that_was_clicked_last(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.click(680, 430, window)
        recorder.text("hi")
        document = recorder.flow("f", "F", APP)
        typed = next(s for s in document["steps"] if s["action"] == "type")
        assert typed["target"] == "target_2"

    def test_enter_is_an_instruction_not_a_character(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.text("hi")
        recorder.key("Return")
        document = recorder.flow("f", "F", APP)
        assert [s["action"] for s in document["steps"]] == ["click", "type", "key"]
        assert document["steps"][-1]["keys"] == "{ENTER}"

    def test_space_is_a_character_not_an_instruction(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.text("a")
        recorder.key("space")
        recorder.text("b")
        document = recorder.flow("f", "F", APP)
        assert next(s for s in document["steps"]
                    if s["action"] == "type")["text"] == "{{prompt}}"
        assert document["prompts"][0]["prompt"] == "a b"

    def test_a_key_nobody_has_a_spelling_for_is_dropped_not_guessed(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.key("F13")
        assert [s["action"] for s in recorder.steps] == ["click"]

    def test_text_typed_before_any_click_is_still_kept(self, window):
        """The focus may already be where it needs to be."""
        recorder = Recorder()
        recorder.text("straight in")
        document = recorder.flow("f", "F", APP)
        typed = next(s for s in document["steps"] if s["action"] == "type")
        assert "target" not in typed


class TestWhatVaries:
    def test_the_longest_thing_typed_becomes_the_prompt(self, window):
        """The click path is fixed and the question is what changes. That is
        the entire reason for recording one."""
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.text("Part1")
        recorder.key("Return")
        recorder.click(680, 430, window)
        recorder.text("What is this part for?")
        document = recorder.flow("f", "F", APP)

        assert document["prompts"] == [
            {"id": "baseline", "prompt": "What is this part for?"}]
        texts = [s["text"] for s in document["steps"] if s["action"] == "type"]
        assert texts == ["Part1", "{{prompt}}"], "only the prompt is lifted"

    def test_a_recording_with_no_typing_still_produces_a_usable_flow(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        document = recorder.flow("f", "F", APP)
        assert document["prompts"][0]["prompt"]


class TestTheFlowItWrites:
    def _recorded(self, window, **kwargs):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.text("What is this part for?")
        recorder.key("Return")
        return recorder, recorder.flow("leo-recorded", "LEO", APP, **kwargs)

    def test_it_is_a_flow_the_loader_accepts(self, window, tmp_path):
        """The point of failure this catches is a recorder that writes
        something almost right, discovered only when somebody replays it."""
        recorder, document = self._recorded(
            window, read_region={"x": 10, "y": 10, "width": 100, "height": 100})
        anchors = tmp_path / "anchors" / "leo-recorded"
        anchors.mkdir(parents=True)
        for filename, png in recorder.anchor_files().items():
            (anchors / filename).write_bytes(png)

        flow = parse_flow(document, source_path=tmp_path / "flow.yaml")
        flow.validate_for_backend("native")
        assert flow.variables() == {"prompt"}

    def test_it_drives_the_window_it_was_recorded_against(self, window):
        _, document = self._recorded(window)
        assert document["target_app"]["native"]["process"] == "3DEXPERIENCE.exe"
        assert "web" not in document["target_app"], "screen only, by construction"

    def test_every_target_is_a_picture(self, window):
        _, document = self._recorded(window)
        for target in document["targets"].values():
            assert list(target["native"][0]) [0] == "image"

    def test_asked_for_a_read_region_it_waits_before_reading(self, window):
        """An answer streams in, so reading the moment it appears reads half
        of it."""
        _, document = self._recorded(
            window, read_region={"x": 1, "y": 2, "width": 3, "height": 4})
        assert [s["action"] for s in document["steps"][-2:]] == \
            ["wait_for_stable", "read"]
        assert document["steps"][-1]["mode"] == "ocr"
        assert document["steps"][-1]["region"] == {"x": 1, "y": 2,
                                                   "width": 3, "height": 4}

    def test_without_one_it_records_nothing_and_says_so_by_omission(self, window):
        _, document = self._recorded(window)
        assert not [s for s in document["steps"] if s["action"] == "read"]


class TestWhatIsKeptBesidesTheFlow:
    """A recording is worth more than the flow built from it: a screen, the
    point on it that was clicked, and what was there. That is the shape a
    demonstration takes, and a flow is only one thing to build from it."""

    def test_the_whole_window_is_kept_for_every_click(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.click(680, 430, window)
        screens = recorder.screen_files()
        assert sorted(screens) == ["screens/target_1.png", "screens/target_2.png"]
        assert load_rgb(screens["screens/target_1.png"]).shape == window.shape

    def test_the_manifest_says_where_each_click_landed(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        entry = recorder.manifest()[0]
        assert entry["point"] == [280, 130]
        assert entry["screen"] == "screens/target_1.png"
        assert entry["anchor"] == "target_1.png"

    def test_a_described_click_names_the_target_in_the_flow(self, window):
        """target_1 tells a reader nothing at the moment they most want to
        edit the flow."""
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.anchors[0].described = "the send icon"
        document = recorder.flow("f", "F", APP)
        assert document["targets"]["target_1"]["intent"] == "the send icon"

    def test_without_a_description_it_still_says_which_click_it_was(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        document = recorder.flow("f", "F", APP)
        assert document["targets"]["target_1"]["intent"] == "recorded click 1"


class TestClicksThatWereNotInTheWindow:
    """The first click of a recording is often the one that returns to the
    application after pressing Record in the browser -- and on a workstation
    with a monitor to the left of the primary, its screen coordinates are
    negative."""

    def test_a_click_outside_the_window_is_not_recorded(self, window):
        recorder = Recorder()
        assert recorder.click(-50, 920, window) == ""
        assert recorder.anchors == []
        assert recorder.steps == []

    def test_it_says_so_rather_than_dropping_it_silently(self, window):
        recorder = Recorder()
        recorder.click(-50, 920, window)
        assert any("outside the window" in note for note in recorder.notes)

    def test_it_is_a_note_rather_than_a_problem_with_the_flow(self):
        """Reaching for the Stop button is a click outside the window on every
        recording. Reporting that as a problem teaches people to ignore the
        problems."""
        recorder = Recorder()
        recorder.click(-50, 920, np.zeros((600, 900, 3), dtype=np.uint8))
        assert recorder.warnings == []

    def test_a_click_past_the_far_edge_counts_too(self, window):
        recorder = Recorder()
        assert recorder.click(5000, 10, window) == ""
        assert recorder.click(10, 5000, window) == ""
        assert recorder.anchors == []

    def test_the_edges_themselves_are_inside(self, window):
        recorder = Recorder()
        assert recorder.click(0, 0, window) == "target_1"
        assert recorder.click(899, 599, window) == "target_2"

    def test_it_does_not_swallow_what_was_typed_before_it(self, window):
        """The text is still going somewhere: the stray click did not move the
        caret, because it was not in this window."""
        recorder = Recorder()
        recorder.click(280, 130, window)
        recorder.text("hello")
        recorder.click(-50, 920, window)
        document = recorder.flow("f", "F", APP)
        assert document["prompts"][0]["prompt"] == "hello"
        assert [s["action"] for s in document["steps"]] == ["click", "type"]


class TestAnchorsWithNothingInThem:
    """The thing people click is very often featureless. An empty prompt box
    is a flat rounded rectangle, and a picture of flat colour matches
    everywhere in the window or nowhere in it.

    Found by file size: the anchor for a real click on LEO's prompt box came
    out at 343 bytes, which is what a 160x64 PNG of one colour weighs.
    """

    def _flat_window(self):
        image = np.zeros((600, 900, 3), dtype=np.uint8)
        image[:] = (40, 44, 50)                       # a flat panel
        image[300:308, :] = (90, 96, 104)             # one edge, far from the click
        return image

    def test_a_flat_crop_is_widened_until_it_reaches_something(self):
        recorder = Recorder()
        recorder.click(450, 260, self._flat_window())
        anchor = recorder.anchors[0]
        assert (anchor.width, anchor.height) > (160, 64), \
            "a picture of one colour is not worth matching on"

    def test_the_click_stays_where_it_was(self):
        """Widening moves the crop's middle; the offset has to carry the
        difference or every recorded click lands somewhere else."""
        recorder = Recorder()
        recorder.click(450, 260, self._flat_window())
        anchor = recorder.anchors[0]
        centre_x = 450 - anchor.dx
        centre_y = 260 - anchor.dy
        assert abs(centre_x - 450) <= anchor.width // 2
        assert abs(centre_y - 260) <= anchor.height // 2

    def test_a_crop_that_already_has_edges_is_left_alone(self, window):
        recorder = Recorder()
        recorder.click(280, 130, window)
        assert (recorder.anchors[0].width, recorder.anchors[0].height) == (160, 64)

    def test_a_window_of_nothing_at_all_says_so(self):
        blank = np.full((600, 900, 3), 40, dtype=np.uint8)
        recorder = Recorder()
        recorder.click(450, 300, blank)
        assert any("picture of almost nothing" in w for w in recorder.warnings)

    def test_flat_colour_scores_nothing(self):
        from understudy.recorder import features

        assert features(np.full((64, 160, 3), 40, dtype=np.uint8)) == 0.0
        textured = np.zeros((64, 160, 3), dtype=np.uint8)
        textured[::4] = 255
        assert features(textured) > 6
