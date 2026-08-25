"""The native driver, against a real window.

Everything else about this driver is tested against synthetic trees. This is
the only test that makes it touch Windows: it drives Notepad end to end --
finds the window, walks the UIA tree, moves the actual desktop cursor, clicks,
types at a human speed, reads the text back, and screenshots it.

It exists because 22 of the driver's 41 methods had never executed a single
line, and the first time they were going to was in front of CATIA with somebody
watching. Notepad is not CATIA, but every pywinauto and Win32 call is the same
one: `Desktop(backend='uia')`, the tree walk, `SetCursorPos`, `SendInput`,
`type_keys`, the UIA value pattern, the clipboard fallback, `capture_as_image`,
and the DPI and monitor calls underneath all of it.

Skipped everywhere but Windows, and run in CI on a `windows-latest` runner.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from understudy.flow import Strategy, Target

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="needs Windows"),
    pytest.mark.native_live,
]

pywinauto = pytest.importorskip("pywinauto", reason="needs pywinauto")

#: Notepad's editing surface. `Document` on the newer builds, `Edit` on the
#: classic one; the driver is asked for both because which one a runner has is
#: not something this test should care about.
EDIT_AREA = Target(
    name="edit_area",
    intent="the text area",
    strategies={"native": (
        Strategy(backend="native", fields={"control_type": "Document"}),
        Strategy(backend="native", fields={"control_type": "Edit"}),
    )},
)

TYPED = "Understudy typed this."


@pytest.fixture(scope="module")
def notepad():
    """One Notepad for the module, closed however the tests end."""
    process = subprocess.Popen(["notepad.exe"])
    time.sleep(2.0)
    yield process
    try:
        subprocess.run(["taskkill", "/pid", str(process.pid), "/f", "/t"],
                       capture_output=True, timeout=20)
    except Exception:
        process.kill()


@pytest.fixture(scope="module")
def driver(notepad):
    from understudy.drivers.native import NativeDriver

    native = NativeDriver()
    native.start({
        "window_title_pattern": "*Notepad*",
        # Instant: a runner has no one watching, and the animation only costs
        # time here. The animated path has its own tests.
        "mouse": {"mode": "instant"},
        "typing": {"mode": "human", "cps": 40},
    })
    yield native
    try:
        native.stop()
    except Exception:
        pass


class TestGettingHold:
    def test_it_finds_the_window(self, driver):
        assert driver.window is not None

    def test_it_reads_the_geometry(self, driver):
        geometry = driver.geometry
        assert geometry is not None
        assert geometry.width > 0 and geometry.height > 0

    def test_it_became_dpi_aware(self, driver):
        """Without this the coordinates on a scaled display are virtualised:
        they look plausible and every click lands short."""
        assert driver.dpi_awareness, "make_dpi_aware() reported nothing"

    def test_it_enumerated_the_monitors(self, driver):
        assert driver.monitors, "no monitors found"
        assert all(m.width > 0 for m in driver.monitors)

    def test_the_window_is_on_a_known_monitor(self, driver):
        assert driver.geometry.monitor, "the window is on no monitor we know of"


class TestTheTreeWalk:
    def test_it_walks_something(self, driver):
        from understudy.drivers.native import walk

        found = list(walk(driver.window))
        assert len(found) > 1, "the UIA tree came back with nothing in it"

    def test_it_resolves_the_edit_area(self, driver):
        handle, resolution = driver.resolve(EDIT_AREA, 5000)
        assert handle is not None
        assert resolution.target == "edit_area"

    def test_a_target_that_is_not_there_fails_as_not_found(self, driver):
        from understudy.drivers.base import TargetNotFound

        missing = Target(name="nope", strategies={"native": (
            Strategy(backend="native",
                     fields={"automation_id": "definitely-not-in-notepad"}),
        )})
        with pytest.raises(TargetNotFound):
            driver.resolve(missing, 800)


class TestInput:
    def cursor(self):
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return (point.x, point.y)

    def test_the_desktop_cursor_ends_up_where_it_was_sent(self, driver):
        """The real one. Synthetic events would leave it where it was, and
        anything filming the screen would show nothing.

        Instant means instant, not absent: this caught the driver returning
        without moving anything whenever the animation was off.
        """
        target = driver.geometry.to_screen(60, 60)
        driver.move_pointer_to(*target)
        time.sleep(0.2)

        landed = self.cursor()
        assert abs(landed[0] - target[0]) <= 2, f"{landed} not near {target}"
        assert abs(landed[1] - target[1]) <= 2, f"{landed} not near {target}"

    def test_it_moves_again_to_somewhere_else(self, driver):
        first = driver.geometry.to_screen(40, 40)
        second = driver.geometry.to_screen(180, 140)
        driver.move_pointer_to(*first)
        time.sleep(0.15)
        driver.move_pointer_to(*second)
        time.sleep(0.15)
        assert self.cursor() != first

    def test_it_clicks_and_types(self, driver):
        driver.type(EDIT_AREA, TYPED, 5000)
        time.sleep(0.4)
        text, _ = driver.read(EDIT_AREA, 5000)
        assert TYPED in text, f"read back {text!r}"

    def test_typing_again_replaces_rather_than_appends(self, driver):
        """The bug that contaminated every variant after the first: select-all
        then typing does not clear the field on every control."""
        driver.type(EDIT_AREA, "second", 5000)
        time.sleep(0.4)
        text, _ = driver.read(EDIT_AREA, 5000)
        assert "second" in text
        assert TYPED not in text, f"the previous text survived: {text!r}"

    def test_a_prompt_containing_sendkeys_syntax_arrives_literally(self, driver):
        """Unescaped, `~` is Enter and `+` is Shift -- a prompt would submit
        itself halfway through being typed."""
        awkward = "C++ at 50% (yes) ~ done"
        driver.type(EDIT_AREA, awkward, 5000)
        time.sleep(0.4)
        text, _ = driver.read(EDIT_AREA, 5000)
        assert awkward in text, f"read back {text!r}"

    def test_a_key_press_reaches_the_window(self, driver):
        driver.type(EDIT_AREA, "line one", 5000)
        driver.key("{ENTER}", EDIT_AREA, 5000)
        driver.type(EDIT_AREA, "line two", 5000, clear=False)
        time.sleep(0.4)
        text, _ = driver.read(EDIT_AREA, 5000)
        assert "line one" in text and "line two" in text


class TestFindingThingsByLookingAtThem:
    """The path CATIA will actually use.

    A CAD package draws its own spec tree, its own toolbars and its own
    viewport, so UIA is likely to show a window with almost nothing useful
    inside it. When that happens the driver falls back to finding a control by
    matching a picture of it against a screenshot, and reading text by OCR.

    That chain has been proven against web pixels. This proves it against a
    native window, which is a different capture path entirely --
    `capture_as_image` on a real HWND rather than a browser screenshot.
    """

    def test_a_control_can_be_found_by_matching_a_picture_of_it(self, driver, tmp_path):
        from understudy.vision import crop, locate_all
        from harness.image import load_rgb, to_png_bytes

        window = load_rgb(driver.screenshot())
        assert window.shape[0] > 40 and window.shape[1] > 120

        # Crop a patch out of the window and ask the matcher to find it again.
        # If the capture path and the matcher disagree about anything -- colour
        # order, scaling, the alpha channel -- this is where it shows.
        patch = crop(window, x=8, y=8, width=90, height=24)
        found = locate_all(window, patch, threshold=0.95)

        assert found, "a patch cut from the window could not be found in it"
        assert found[0].score > 0.99, f"scored only {found[0].score:.3f}"
        assert abs(found[0].x - 8) <= 2 and abs(found[0].y - 8) <= 2, \
            f"found at ({found[0].x}, {found[0].y}), expected about (8, 8)"

    def test_the_driver_resolves_an_image_target_end_to_end(self, driver, tmp_path):
        """The same thing through the driver: an `image:` strategy, resolved
        against a live screenshot, giving a clickable point."""
        from understudy.vision import crop
        from harness.image import load_rgb, to_png_bytes

        window = load_rgb(driver.screenshot())
        anchor = tmp_path / "anchor.png"
        anchor.write_bytes(to_png_bytes(crop(window, x=10, y=10, width=80, height=22)))

        by_picture = Target(name="by_picture", strategies={"native": (
            Strategy(backend="native",
                     fields={"image": str(anchor), "threshold": 0.95}),
        )})
        handle, resolution = driver.resolve(by_picture, 8000)

        assert resolution.via in ("anchor", "learned-anchor"), resolution.via
        x, y = handle.point
        assert 0 <= x < window.shape[1] and 0 <= y < window.shape[0]

    def test_reading_text_off_the_pixels(self, driver):
        """OCR, the last rung. Notepad has real UIA text, so this is checking
        the mechanism rather than the necessity -- but on a custom-drawn panel
        it is the only thing left."""
        from understudy.ocr import read_text

        driver.type(EDIT_AREA, "READBACK", 5000)
        time.sleep(0.5)
        outcome = read_text(driver.screenshot())
        if not outcome.available:
            pytest.skip(f"no OCR engine on this machine: {outcome.error}")
        assert "READBACK" in outcome.text.upper().replace(" ", "")


class TestSeeing:
    def test_it_screenshots_the_window(self, driver):
        from harness.image import load_rgb

        image = driver.screenshot()
        pixels = load_rgb(image)
        assert pixels.shape[0] > 50 and pixels.shape[1] > 50

    def test_it_screenshots_a_region(self, driver):
        from harness.image import load_rgb

        image = driver.screenshot(region={"x": 0, "y": 0, "width": 120, "height": 60})
        assert load_rgb(image).shape[:2] == (60, 120)

    def test_it_can_tell_whether_something_is_visible(self, driver):
        assert driver.is_visible(EDIT_AREA)

    def test_wait_for_element_returns_rather_than_hanging(self, driver):
        resolution = driver.wait_for_element(EDIT_AREA, "visible", 5000)
        assert resolution.target == "edit_area"
