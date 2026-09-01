"""Capturing a window and cutting anchors out of the capture.

The capture itself is pywinauto and cannot run here. Everything around it --
the grid that makes a region readable, the box parsing, the cropping and its
refusals -- is arithmetic on an image, and is what these cover.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import capture_window  # noqa: E402

from harness.image import load_rgb, to_png_bytes  # noqa: E402


@pytest.fixture
def window(tmp_path):
    """A window-sized image with something identifiable at a known place."""
    image = np.zeros((600, 900, 3), dtype=np.uint8)
    image[400:460, 200:500] = (30, 144, 255)   # a "prompt box" at a known box
    path = tmp_path / "window.png"
    path.write_bytes(to_png_bytes(image))
    return path


class TestReadingRegionsOffTheCapture:
    def test_the_grid_does_not_change_the_size(self, window):
        """The numbers read off the grid are used against the plain capture,
        so the two have to be the same picture."""
        image = load_rgb(window.read_bytes())
        gridded = capture_window.with_grid(image)
        assert gridded.size == (image.shape[1], image.shape[0])

    def test_the_grid_is_actually_drawn(self, window):
        image = load_rgb(window.read_bytes())
        gridded = np.asarray(capture_window.with_grid(image))
        assert (gridded != image).any(), "a grid nobody can see is not a grid"
        assert gridded[0, 100].tolist() == list(capture_window.GRID_COLOUR)


class TestBoxes:
    def test_a_box_reads_as_a_flow_region(self):
        assert capture_window.parse_box("1240,880,300,60") == {
            "x": 1240, "y": 880, "width": 300, "height": 60}

    def test_nonsense_is_refused_with_the_shape_it_wanted(self):
        with pytest.raises(SystemExit, match="x,y,width,height"):
            capture_window.parse_box("1240 880 300 60")

    def test_a_box_with_no_area_is_refused(self):
        with pytest.raises(SystemExit, match="no area"):
            capture_window.parse_box("10,10,0,50")


class TestCuttingAnchors:
    def test_the_anchor_is_the_pixels_that_were_asked_for(self, window, tmp_path):
        out = tmp_path / "anchors" / "prompt.png"
        size = capture_window.cut(window, {"x": 200, "y": 400,
                                           "width": 300, "height": 60}, out)
        assert size == (300, 60)
        cut = load_rgb(out.read_bytes())
        assert cut.shape == (60, 300, 3)
        assert (cut == (30, 144, 255)).all(), "the region, not somewhere near it"

    def test_directories_are_made_rather_than_demanded(self, window, tmp_path):
        out = tmp_path / "deep" / "nested" / "prompt.png"
        capture_window.cut(window, {"x": 0, "y": 0, "width": 10, "height": 10}, out)
        assert out.exists()

    def test_a_box_off_the_edge_says_which_coordinates_it_wanted(self, window, tmp_path):
        """Screen coordinates instead of window coordinates is the mistake
        this catches, and silently clamping would hide it until replay."""
        with pytest.raises(SystemExit, match="window coordinates"):
            capture_window.cut(window, {"x": 3000, "y": 100,
                                        "width": 300, "height": 60},
                               tmp_path / "x.png")
