"""Visual anchoring: finding a control by what it looks like.

The bottom rung of the target ladder, for surfaces with no DOM and no
accessibility tree. Tested on synthetic images so the numbers are exact.
"""

from __future__ import annotations

import numpy as np
import pytest

from understudy.vision import (
    DEFAULT_THRESHOLD, SCALES, _shortlist, changed_region, crop, locate,
    locate_all, locate_scaled, resized, to_gray,
)


def canvas(width=200, height=120, colour=(30, 40, 50)) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = colour
    return image


def stamp(image: np.ndarray, x: int, y: int, size: int = 16, seed: int = 0) -> np.ndarray:
    """A distinctive patch -- deterministic, but not flat."""
    rng = np.random.default_rng(seed)
    patch = rng.integers(0, 255, size=(size, size, 3), dtype=np.uint8)
    image[y : y + size, x : x + size] = patch
    return patch


def test_an_anchor_is_found_where_it_was_placed():
    image = canvas()
    patch = stamp(image, x=70, y=40, seed=1)

    match = locate(image, patch)
    assert match is not None
    assert (match.x, match.y) == (70, 40)
    assert match.score == pytest.approx(1.0)
    assert match.centre == (78, 48)


def test_an_absent_anchor_returns_nothing():
    image = canvas()
    stamp(image, x=10, y=10, seed=1)
    unrelated = np.random.default_rng(99).integers(0, 255, (16, 16, 3), dtype=np.uint8)

    assert locate(image, unrelated, threshold=DEFAULT_THRESHOLD) is None


def test_a_match_survives_a_uniform_brightness_shift():
    """Normalised correlation, not exact equality: hover states and sub-pixel
    rendering shift values without changing what the control looks like."""
    image = canvas()
    patch = stamp(image, x=50, y=30, seed=2)
    dimmed = np.clip(image.astype(np.int16) - 25, 0, 255).astype(np.uint8)

    match = locate(dimmed, patch)
    assert match is not None and (match.x, match.y) == (50, 30)


def test_the_anchor_is_relocated_after_the_control_moves():
    """The point of anchoring over stored coordinates: the window moved, and
    the click point is derived again from this run's pixels."""
    patch = stamp(canvas(), x=0, y=0, seed=3)

    moved = canvas()
    moved[75:91, 120:136] = patch
    match = locate(moved, patch)
    assert match is not None and (match.x, match.y) == (120, 75)


def test_a_featureless_anchor_matches_nothing():
    """A flat patch carries no information and would otherwise match
    everywhere, which is worse than failing."""
    image = canvas()
    flat = np.full((12, 12, 3), 30, dtype=np.uint8)
    assert locate(image, flat) is None


def test_an_anchor_larger_than_the_screen_is_not_a_crash():
    assert locate(canvas(40, 40), canvas(80, 80)) is None


class TestAmbiguity:
    def test_repeated_controls_all_report(self):
        """Six identical toolbar buttons: the anchor does not identify one."""
        image = canvas(300, 60)
        patch = stamp(image, x=10, y=20, seed=4)
        for index in range(1, 6):
            image[20:36, 10 + index * 40 : 26 + index * 40] = patch

        matches = locate_all(image, patch)
        assert len(matches) == 6
        assert sorted(m.x for m in matches) == [10, 50, 90, 130, 170, 210]

    def test_one_control_reports_once(self):
        image = canvas()
        patch = stamp(image, x=60, y=60, seed=5)
        assert len(locate_all(image, patch)) == 1


class TestRegions:
    def test_a_region_narrows_the_search(self):
        image = canvas(300, 200)
        patch = stamp(image, x=20, y=20, seed=6)
        image[150:166, 250:266] = patch

        found = locate_all(image, patch, region={"x": 0, "y": 0, "width": 150, "height": 100})
        assert len(found) == 1, "the second copy is outside the region"
        assert (found[0].x, found[0].y) == (20, 20)

    def test_coordinates_come_back_in_window_space(self):
        """A region offset must not leak into the click point."""
        image = canvas(300, 200)
        patch = stamp(image, x=210, y=140, seed=7)

        match = locate(image, patch, region={"x": 200, "y": 130, "width": 100, "height": 70})
        assert match is not None
        assert (match.x, match.y) == (210, 140)

    def test_crop_extracts_the_rectangle(self):
        image = canvas(100, 100)
        image[10:20, 30:40] = (255, 0, 0)
        patch = crop(image, {"x": 30, "y": 10, "width": 10, "height": 10})
        assert patch.shape == (10, 10, 3)
        assert (patch[:, :, 0] == 255).all()


def test_grayscale_is_luma_weighted():
    assert to_gray(np.array([[[255, 0, 0]]], dtype=np.uint8))[0, 0] == pytest.approx(76.245)


class TestFindingWhatChanged:
    """Where a reply appears, found by watching rather than by asking.

    Marking it by hand meant two clicks with a modifier held, and the first
    real attempt produced a 4x10 region -- unreadable, and nothing said why.
    """

    def _screen(self):
        return np.full((600, 900, 3), 40, dtype=np.uint8)

    def test_the_area_that_filled_in_is_the_area_to_read(self):
        before = self._screen()
        after = before.copy()
        after[200:320, 500:800] = 220           # an answer arrives
        region = changed_region(before, after, pad=0)
        assert region == {"x": 500, "y": 200, "width": 300, "height": 120}

    def test_a_little_room_is_left_around_it(self):
        before = self._screen()
        after = before.copy()
        after[200:320, 500:800] = 220
        region = changed_region(before, after, pad=8)
        assert (region["x"], region["y"]) == (492, 192)
        assert (region["width"], region["height"]) == (316, 136)

    def test_a_blinking_caret_does_not_stretch_it_across_the_window(self):
        """A caret two pixels wide changes as much as anything else, and a box
        around every changed pixel would read the whole interface."""
        before = self._screen()
        after = before.copy()
        after[200:320, 500:800] = 220
        after[880:890, 10:12] = 255              # a caret, far away
        region = changed_region(before, after, pad=0)
        assert region["height"] < 200, region

    def test_an_unchanged_screen_is_no_region_rather_than_an_empty_one(self):
        before = self._screen()
        assert changed_region(before, before.copy()) is None

    def test_a_small_change_is_still_reported_rather_than_dropped(self):
        before = self._screen()
        after = before.copy()
        after[300:308, 400:460] = 250
        region = changed_region(before, after, pad=0)
        assert region is not None and region["width"] >= 60

    def test_pictures_of_different_sizes_are_refused(self):
        """The window was resized mid-recording; anything computed from that
        would be measured against the wrong frame."""
        assert changed_region(self._screen(),
                              np.zeros((400, 400, 3), dtype=np.uint8)) is None


class TestFindingTheOcrEngine:
    """"tesseract is not recognized" is the ordinary outcome of installing
    Tesseract on Windows: ticking "add to PATH" is optional and easily missed,
    and even when ticked a new terminal is needed. Looking where it installs
    to is kinder than explaining that."""

    def test_the_override_wins(self, monkeypatch, tmp_path):
        from understudy.ocr import find_tesseract

        monkeypatch.setenv("UNDERSTUDY_TESSERACT", "D:/tools/tesseract.exe")
        assert find_tesseract() == "D:/tools/tesseract.exe"

    def test_the_path_is_used_when_there_is_one(self, monkeypatch):
        from understudy import ocr

        monkeypatch.delenv("UNDERSTUDY_TESSERACT", raising=False)
        monkeypatch.setattr(ocr.shutil if hasattr(ocr, "shutil") else __import__("shutil"),
                            "which", lambda name: "/usr/bin/tesseract")
        assert ocr.find_tesseract() == "/usr/bin/tesseract"

    def test_where_windows_puts_it_is_searched_next(self, monkeypatch, tmp_path):
        from understudy import ocr

        installed = tmp_path / "Tesseract-OCR" / "tesseract.exe"
        installed.parent.mkdir()
        installed.write_text("")
        monkeypatch.delenv("UNDERSTUDY_TESSERACT", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr(ocr, "WINDOWS_PLACES", (str(installed),))
        assert ocr.find_tesseract() == str(installed)

    def test_nothing_anywhere_is_none_rather_than_a_guess(self, monkeypatch):
        from understudy import ocr

        monkeypatch.delenv("UNDERSTUDY_TESSERACT", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr(ocr, "WINDOWS_PLACES", ())
        assert ocr.find_tesseract() is None

    def test_a_missing_engine_says_how_to_get_one(self, monkeypatch):
        from understudy import ocr

        monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
        outcome = ocr.read_text(b"")
        assert outcome.available is False
        assert "winget install" in (outcome.error or "")
        assert "UNDERSTUDY_TESSERACT" in (outcome.error or "")


def toolbar(scale: float, palette=None) -> np.ndarray:
    """A little interface, *drawn* at `scale` rather than resampled to it.

    The distinction is the whole point. A picture scaled up is the same picture
    with more pixels; an interface redrawn at another DPI has its edges on
    different pixel boundaries, its borders a different number of pixels thick
    and its text hinted differently. Anchors survive the first easily and the
    second is what actually happens on a workstation at 125%.
    """
    at = lambda value: int(round(value * scale))
    palette = palette or [(30, 90, 170), (200, 80, 40), (40, 150, 80), (140, 60, 160)]
    image = np.full((at(240), at(400), 3), 235, dtype=np.uint8)
    image[: at(40), :] = 250
    for index, colour in enumerate(palette):
        left, top = at(20 + index * 70), at(8)
        right, bottom = at(20 + index * 70 + 56), at(32)
        image[top:bottom, left:right] = 245
        image[top:bottom, left : left + at(2)] = colour
        image[top : top + at(2), left:right] = colour
        for bar in range(3):
            y = at(13 + bar * 6)
            image[y : y + at(2), left + at(8) : right - at(8)] = colour
    return image


#: The second button, cut from the interface at the size it was recorded at.
SECOND_BUTTON = toolbar(1.0)[6:34, 88:160].copy()
#: Where its centre lands, at 100%.
SECOND_CENTRE = (124, 20)
#: The same interface with that button repainted grey: the control the anchor
#: names is not there any more, and the three others still are.
WITHOUT_IT = [(30, 90, 170), (90, 90, 90), (40, 150, 80), (140, 60, 160)]


class TestFindingItAtAnotherSize:
    """A recording is a photograph: its anchors are pixels captured at one
    monitor's DPI. Replayed at 125% every anchor misses -- not because the
    control moved, which anchors already handle, but because it is a different
    number of pixels across."""

    @pytest.mark.parametrize("drawn_at", [1.25, 1.5, 0.8, 0.75])
    def test_the_control_is_found_and_the_size_reported(self, drawn_at):
        screen = toolbar(drawn_at)
        assert locate(screen, SECOND_BUTTON) is None, "and the strict path cannot"

        found = locate_scaled(screen, SECOND_BUTTON)
        assert found is not None
        wanted = (round(SECOND_CENTRE[0] * drawn_at), round(SECOND_CENTRE[1] * drawn_at))
        assert abs(found.centre[0] - wanted[0]) <= 3
        assert abs(found.centre[1] - wanted[1]) <= 3
        assert found.scale == pytest.approx(drawn_at, abs=0.06)

    @pytest.mark.parametrize("drawn_at", [1.0, 1.25, 1.5, 0.8])
    def test_a_control_that_is_gone_is_not_found_at_some_other_size(self, drawn_at):
        """The failure that would matter. Three buttons remain that look almost
        exactly like the one being searched for, and the best of them scores
        0.81 -- comfortably above any floor worth setting. What separates them
        is that the real control leads the runner-up by three times as much."""
        assert locate_scaled(toolbar(drawn_at, WITHOUT_IT), SECOND_BUTTON) is None

    def test_nothing_is_found_on_an_empty_screen(self):
        blank = np.full((240, 400, 3), 235, dtype=np.uint8)
        assert locate_scaled(blank, SECOND_BUTTON) is None

    def test_the_match_is_the_size_it_was_found_at(self):
        """So the click point is the centre of what is on screen now, and a
        caller can say what it had to do to get there."""
        found = locate_scaled(toolbar(1.5), SECOND_BUTTON)
        assert found.scale == pytest.approx(1.5)
        assert found.width == pytest.approx(SECOND_BUTTON.shape[1] * 1.5, abs=2)
        assert found.height == pytest.approx(SECOND_BUTTON.shape[0] * 1.5, abs=2)

    def test_the_size_it_was_captured_at_is_not_among_those_tried(self):
        """`locate` owns that one, with a strict threshold. Trying it again
        here would quietly overrule the threshold the flow asked for."""
        assert 1.0 not in SCALES
        assert locate_scaled(toolbar(1.0), SECOND_BUTTON) is None

    def test_only_the_named_sizes_are_tried_when_the_caller_names_them(self):
        """The caller that already knows the interface is at 150% -- because
        the last target said so -- pays for one size instead of a dozen."""
        assert locate_scaled(toolbar(1.5), SECOND_BUTTON, scales=(1.5,)) is not None
        assert locate_scaled(toolbar(1.5), SECOND_BUTTON, scales=(0.8,)) is None

    def test_a_region_bounds_the_search_and_the_answer_is_still_on_screen(self):
        screen = toolbar(1.25)
        found = locate_scaled(screen, SECOND_BUTTON,
                              region={"x": 60, "y": 0, "width": 200, "height": 60})
        assert found is not None
        wanted = (round(SECOND_CENTRE[0] * 1.25), round(SECOND_CENTRE[1] * 1.25))
        assert abs(found.centre[0] - wanted[0]) <= 3

    def test_an_anchor_larger_than_the_screen_is_not_an_error(self):
        small = np.full((20, 20, 3), 235, dtype=np.uint8)
        assert locate_scaled(small, SECOND_BUTTON) is None


class TestChoosingWhichSizesToConfirm:
    """The coarse pass runs at half resolution, where 1.2 and 1.25 are nearly
    the same picture. Confirming only its winner would take the wrong one of a
    pair often enough to matter."""

    def test_the_best_few_are_confirmed(self):
        ranked = [(0.9, 1.25), (0.8, 0.8), (0.7, 2.0), (0.6, 0.5)]
        assert _shortlist(ranked, confirm=2)[:2] == [1.25, 0.8]

    def test_and_whatever_sits_either_side_of_the_winner(self):
        ranked = [(0.9, 1.2), (0.8, 0.5), (0.7, 1.25), (0.6, 1.1)]
        chosen = _shortlist(ranked, confirm=2)
        assert 1.1 in chosen and 1.25 in chosen, chosen

    def test_nothing_ranked_means_nothing_to_confirm(self):
        assert _shortlist([], confirm=2) == []


class TestResizing:
    def test_the_size_it_was_asked_for(self):
        assert resized(np.zeros((20, 40, 3), dtype=np.uint8), 1.5).shape[:2] == (30, 60)

    def test_the_same_picture_back_at_its_own_size(self):
        """Not a copy that has been through a resampler and come back subtly
        different -- the strict path's scores have to stay exact."""
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        assert resized(image, 1.0) is image

    def test_it_never_rounds_away_to_nothing(self):
        assert resized(np.zeros((3, 3, 3), dtype=np.uint8), 0.1).shape[:2] == (1, 1)
