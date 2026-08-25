"""The CAD-shaped case: an opaque canvas, unlabelled controls, a viewport that
never stops moving.

These are the conditions that break naive UI replay, reproduced offline against
fixtures/cad_app so the fallbacks can be proven rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowrunner.drivers.base import DriverError, TargetNotFound
from flowrunner.drivers.web import WebDriver
from flowrunner.flow import Target, parse_flow
from flowrunner.vision import crop, locate_all
from flowrunner.waiting import (
    StableOutcome,
    pixels_equivalent,
    text_equivalent,
    wait_until_stable,
)
from harness.image import to_png_bytes

pytest.importorskip("playwright", reason="needs playwright")

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures" / "cad_app" / "index.html")
VIEWPORT = {"width": 1100, "height": 700}


def target(label: str, **fields) -> Target:
    # `label` rather than `name`: `name` is itself a strategy field.
    return parse_flow({
        "version": 1, "name": "t", "prompts": [{"id": "a", "prompt": "x"}],
        "targets": {label: {"web": [fields]}},
        "steps": [{"action": "click", "target": label}],
    }).target_for(label)


@pytest.fixture
def driver():
    web = WebDriver()
    web.start({"url": "about:blank", "viewport": VIEWPORT})
    yield web
    web.stop()


def open_app(driver: WebDriver, query: str) -> None:
    driver.page.set_viewport_size(VIEWPORT)
    driver.page.goto(f"file://{FIXTURE}{query}")
    driver.page.wait_for_timeout(300)


def ask(driver: WebDriver, text: str) -> None:
    driver.page.locator("#ask").fill(text)
    driver.page.locator("#send").click()


def reply_region(driver: WebDriver, selector: str) -> dict[str, int]:
    box = driver.page.locator(selector).bounding_box()
    return {k: int(v) for k, v in
            zip(("x", "y", "width", "height"),
                (box["x"], box["y"], box["width"], box["height"]))}


class TestTheOpaqueViewportProblem:
    def test_whole_window_pixel_stability_never_settles(self, driver):
        """The viewport animates forever. Anyone waiting on the whole window
        waits until the timeout, every single run."""
        open_app(driver, "?viewport=spin&panel=dom&delay=20")
        ask(driver, "hello")

        outcome = wait_until_stable(
            sample=lambda: driver.screenshot(),
            equivalent=pixels_equivalent,
            stable_for_ms=600, timeout_ms=3500, poll_interval_ms=200,
        )
        assert outcome.outcome is StableOutcome.TIMEOUT

    def test_a_region_settles_while_the_viewport_keeps_moving(self, driver):
        """Scoping the check to the part that matters is what makes pixel
        stability usable on a surface like this."""
        open_app(driver, "?viewport=spin&panel=dom&delay=20")
        region = reply_region(driver, "#reply")
        ask(driver, "one two three")

        outcome = wait_until_stable(
            sample=lambda: driver.screenshot(region=region),
            equivalent=pixels_equivalent,
            stable_for_ms=600, timeout_ms=8000, poll_interval_ms=200,
        )
        assert outcome.outcome is StableOutcome.STABLE

    def test_a_static_viewport_is_not_the_interesting_case(self, driver):
        # A reply long enough to still be streaming when the wait begins. With
        # a five-character answer it is already finished by the first poll, and
        # a wait that requires the screen to change first would sit there until
        # the timeout -- correctly, because from its point of view nothing ever
        # arrived.
        open_app(driver, "?viewport=static&panel=dom&delay=60")
        ask(driver, "one two three four five six seven eight")
        outcome = wait_until_stable(
            sample=lambda: driver.screenshot(),
            equivalent=pixels_equivalent,
            stable_for_ms=500, timeout_ms=8000, poll_interval_ms=200,
        )
        assert outcome.outcome is StableOutcome.STABLE


class TestAResponseWithNoText:
    def test_a_painted_response_is_unreadable_through_the_dom(self, driver):
        open_app(driver, "?panel=canvas&viewport=static&delay=20")
        ask(driver, "what is this part")
        driver.page.wait_for_timeout(800)

        # This is why pixel mode exists: the reply is pixels, not text.
        assert driver.page.locator("#reply").inner_text() == ""

    def test_pixel_stability_still_detects_completion(self, driver):
        open_app(driver, "?panel=canvas&viewport=spin&delay=25")
        region = reply_region(driver, "#replyCanvas")
        ask(driver, "one two three four five")

        outcome = wait_until_stable(
            sample=lambda: driver.screenshot(region=region),
            equivalent=pixels_equivalent,
            stable_for_ms=700, timeout_ms=10000, poll_interval_ms=200,
        )
        assert outcome.outcome is StableOutcome.STABLE
        assert outcome.samples > 2, "it should have observed the response arriving"

    def test_text_stability_on_a_painted_response_fails_loudly(self, driver):
        """The response is pixels, so there is no text to watch and the DOM
        answer stays empty forever.

        This used to settle instantly on the empty string and report success
        having captured nothing -- a silent failure, the worst kind. Requiring
        the watched thing to change before it can count as settled turns it
        into a timeout that names itself.
        """
        open_app(driver, "?panel=canvas&viewport=static&delay=25")
        ask(driver, "one two three four five")

        outcome = wait_until_stable(
            sample=lambda: driver.page.locator("#reply").inner_text(),
            equivalent=text_equivalent,
            stable_for_ms=400, timeout_ms=4000, poll_interval_ms=150,
        )
        assert outcome.outcome is StableOutcome.TIMEOUT
        assert outcome.signal == "never-started"
        assert outcome.last_value == ""

    def test_the_old_behaviour_is_still_there_when_asked_for(self, driver):
        """What the silent failure looked like, kept as a warning."""
        open_app(driver, "?panel=canvas&viewport=static&delay=25")
        ask(driver, "one two three four five")

        outcome = wait_until_stable(
            sample=lambda: driver.page.locator("#reply").inner_text(),
            equivalent=text_equivalent, require_change=False,
            stable_for_ms=400, timeout_ms=4000, poll_interval_ms=150,
        )
        assert outcome.outcome is StableOutcome.STABLE
        assert outcome.last_value == "", "settled on nothing at all"


class TestUnlabelledControls:
    def test_accessible_name_strategies_fail(self, driver):
        open_app(driver, "?controls=unlabelled&viewport=static")
        with pytest.raises(TargetNotFound):
            driver.resolve(target("measure", role="button", name="Measure"), 300)

    def test_a_structural_selector_still_resolves_them(self, driver):
        open_app(driver, "?controls=unlabelled&viewport=static")
        handle, resolution = driver.resolve(
            target("measure", css=".tool[data-index='5']"), 2000
        )
        assert resolution.index == 0
        assert handle.is_visible()

    def test_the_ranked_list_falls_through_and_reports_it(self, driver):
        open_app(driver, "?controls=unlabelled&viewport=static")
        ranked = parse_flow({
            "version": 1, "name": "t", "prompts": [{"id": "a", "prompt": "x"}], "prompts": [{"id": "a", "prompt": "x"}],
            "targets": {"measure": {"web": [
                {"role": "button", "name": "Measure"},
                {"css": ".tool[data-index='5']"},
            ]}},
            "steps": [{"action": "click", "target": "measure"}],
        }).target_for("measure")

        _, resolution = driver.resolve(ranked, 2000)
        assert resolution.index == 1
        assert resolution.used_fallback is True

    def test_an_ambiguous_selector_is_refused_not_guessed(self, driver):
        """Six toolbar controls match `.tool`. Clicking the first would be a
        wrong click that looks like a right one."""
        open_app(driver, "?controls=unlabelled&viewport=static")
        with pytest.raises(TargetNotFound, match="ambiguous"):
            driver.resolve(target("any_tool", css=".tool"), 300)


class TestVisualAnchoring:
    """The last resort: no DOM, no names -- find the control by its picture."""

    def anchor_for(self, driver, index: int, tmp_path: Path) -> Path:
        box = driver.page.locator(f".tool[data-index='{index}']").bounding_box()
        region = {"x": int(box["x"]), "y": int(box["y"]),
                  "width": int(box["width"]), "height": int(box["height"])}
        path = tmp_path / f"tool-{index}.png"
        path.write_bytes(to_png_bytes(crop(driver.screenshot(), region)))
        return path

    def test_an_anchor_finds_a_control_with_no_accessible_identity(self, driver, tmp_path):
        open_app(driver, "?controls=unlabelled&viewport=static")
        anchor = self.anchor_for(driver, 4, tmp_path)

        handle, resolution = driver.resolve(
            target("pad_tool", image=str(anchor), threshold=0.95), 2000
        )
        assert "score" in resolution.note
        expected = driver.page.locator(".tool[data-index='4']").bounding_box()
        assert abs(handle.match.centre[0] - (expected["x"] + expected["width"] / 2)) <= 2

    def test_clicking_an_anchor_reaches_the_control(self, driver, tmp_path):
        open_app(driver, "?controls=unlabelled&viewport=static")
        anchor = self.anchor_for(driver, 3, tmp_path)

        driver.click(target("sketch_tool", image=str(anchor), threshold=0.95), 2000)
        clicked = driver.page.locator(".tool[data-index='3']")
        # The fixture repaints a clicked tool #8fa8c8; the browser reports rgb().
        assert "rgb(143, 168, 200)" in (clicked.get_attribute("style") or "")

    def test_an_anchor_is_relocated_after_the_layout_moves(self, driver, tmp_path):
        """The window changed shape; the anchor is found again where it is
        now. A stored coordinate would have been wrong."""
        open_app(driver, "?controls=unlabelled&viewport=static")
        anchor = self.anchor_for(driver, 2, tmp_path)
        before = locate_all(driver.screenshot(), anchor.read_bytes(), 0.95)[0]

        driver.page.evaluate(
            "document.getElementById('toolbar').style.paddingLeft = '120px'"
        )
        driver.page.wait_for_timeout(200)
        after = locate_all(driver.screenshot(), anchor.read_bytes(), 0.95)

        assert len(after) == 1
        assert after[0].x > before.x + 100, "found at its new position"

    def test_reading_text_from_an_anchor_says_why_it_cannot(self, driver, tmp_path):
        open_app(driver, "?controls=unlabelled&viewport=static")
        anchor = self.anchor_for(driver, 1, tmp_path)
        handle, _ = driver.resolve(target("open_tool", image=str(anchor), threshold=0.95), 2000)

        with pytest.raises(DriverError, match="no text to read"):
            handle.inner_text()
