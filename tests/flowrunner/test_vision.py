"""Visual anchoring: finding a control by what it looks like.

The bottom rung of the target ladder, for surfaces with no DOM and no
accessibility tree. Tested on synthetic images so the numbers are exact.
"""

from __future__ import annotations

import numpy as np
import pytest

from flowrunner.vision import DEFAULT_THRESHOLD, crop, locate, locate_all, to_gray


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
