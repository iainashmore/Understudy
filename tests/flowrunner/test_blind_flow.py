"""The fully blind path, end to end.

No DOM knowledge, no accessibility tree, no element picking: click the prompt
box by locating a picture of it, type, click send the same way, wait on a
rectangle of pixels, and record both the prompt and the response.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowrunner.drivers import build
from flowrunner.flow import load_flow
from flowrunner.ocr import available as ocr_available
from flowrunner.prompts import parse_prompts
from flowrunner.runner import Runner, Status

pytest.importorskip("playwright", reason="needs playwright")

REPO = Path(__file__).resolve().parents[2]
FLOW = REPO / "examples" / "cad_blind.yaml"
PROMPTS = parse_prompts(
    "- id: baseline\n  prompt: how many pads are in this part\n", "yaml"
)


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    out = tmp_path_factory.mktemp("blind")
    flow = load_flow(FLOW)
    driver = build("web")
    driver.start(flow.app_config("web"))
    try:
        runner = Runner(flow, driver, out)
        runner.prepare(PROMPTS)
        result = runner.run_variant(PROMPTS.variants[0])
    finally:
        driver.stop()
    return result, out


def step(result, action):
    return next(s for s in result.step_statuses if s.action == action)


def test_the_prompt_box_is_found_and_typed_into_without_any_dom_knowledge(outcome):
    result, _ = outcome
    typed = step(result, "type")
    assert typed.status is Status.OK
    assert typed.resolution["strategy"].startswith("image")
    assert "score" in typed.resolution["note"]


def test_the_send_button_is_clicked_by_its_picture(outcome):
    result, _ = outcome
    clicked = step(result, "click")
    assert clicked.status is Status.OK
    assert clicked.resolution["strategy"].startswith("image")


def test_the_response_is_waited_for_on_pixels_not_text(outcome):
    """The viewport animates forever; only the scoped region can settle."""
    result, _ = outcome
    waited = step(result, "wait_for_stable")
    assert waited.status is Status.OK, waited.error
    assert waited.detail["mode"] == "pixels"
    assert waited.detail["samples"] > 2


def test_the_prompt_is_recorded(outcome):
    result, _ = outcome
    assert result.variables["prompt"] == "how many pads are in this part"


def test_the_response_pixels_are_always_kept(outcome):
    """A transcription is a lossy derivative; the image is the evidence."""
    result, out = outcome
    assert "response" in result.read_images
    assert (out / result.read_images["response"]).stat().st_size > 0


@pytest.mark.skipif(not ocr_available(), reason="no OCR engine installed")
def test_the_response_is_transcribed_when_ocr_is_available(outcome):
    result, _ = outcome
    assert "Echo" in result.reads["response"]


@pytest.mark.skipif(ocr_available(), reason="OCR is installed here")
def test_a_missing_ocr_engine_is_loud_not_silent(outcome):
    """An empty string meaning 'could not read' is indistinguishable from one
    meaning 'the assistant said nothing', and the two need opposite reactions."""
    result, _ = outcome
    reading = step(result, "read")
    assert reading.status is Status.ERROR
    assert "pytesseract" in reading.error
    assert result.status is Status.ERROR, "the run must not look successful"


def test_the_run_is_still_fully_recorded(outcome):
    result, out = outcome
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()] \
        if (out / "results.jsonl").exists() else []
    assert len(result.screenshots) >= 4, "three captures plus the response region"
    for relative in result.screenshots:
        assert (out / relative).stat().st_size > 0
    assert (out / "flow.yaml").exists()
    assert rows == [] or rows[0]["variables"]["prompt"]


def test_a_second_variant_is_not_contaminated_by_the_first(tmp_path):
    """Cross-variant contamination is the worst failure this tool can have: the
    responses still look plausible, so a corrupted sweep reads as a real one.

    Two things caused it, and both are pinned here. An anchor taken from an
    empty textbox stops matching once there is text in it, so the flow anchors
    on the static label and clicks at an offset. And clearing a field by
    selecting all and typing an empty string does not clear it.
    """
    flow = load_flow(FLOW)
    prompts = parse_prompts(
        "- id: first\n  prompt: alpha alpha\n- id: second\n  prompt: bravo bravo\n",
        "yaml",
    )
    driver = build("web")
    driver.start(flow.app_config("web"))
    try:
        runner = Runner(flow, driver, tmp_path)
        runner.prepare(prompts)
        results = [runner.run_variant(v) for v in prompts]
        typed = [
            driver.page.locator("#ask").input_value()
            if index == len(results) - 1 else None
            for index in range(len(results))
        ]
    finally:
        driver.stop()

    for result in results:
        typing = next(s for s in result.step_statuses if s.action == "type")
        assert typing.status is Status.OK, f"{result.prompt_id}: {typing.error}"

    assert typed[-1] == "bravo bravo", "the box still held the previous prompt"
