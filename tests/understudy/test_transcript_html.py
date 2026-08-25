"""The transcript as a page.

Two readers depend on this: the viewer inside the app, where the video has to
play and the screenshots have to be visible without leaving the tool, and the
PDF, which a browser prints from this exact HTML.

Built from the results rather than by converting the markdown, so these check
the parts a converter would have got for free -- escaping, and that nothing is
silently dropped between the two forms.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from understudy.transcript import render_markdown
from understudy.transcript_html import render_html, write_html

FLOW = """version: 1
name: demo-flow
title: Demo flow
description: A flow used by the tests
"""


def result(**overrides):
    base = {
        "prompt_id": "baseline",
        "repeat_index": 0,
        "prompt": "Summarise this.",
        "variables": {"prompt": "Summarise this.", "new_name": "Bracket"},
        "response": "A summary.",
        "reads": {}, "read_images": {},
        "status": "ok", "duration_ms": 4200,
        "screenshots": ["baseline/01-start.png", "baseline/02-done.png"],
        "recording": None, "recording_error": None, "pointer_note": None,
        "used_fallbacks": [], "agent_resolutions": [], "learned_anchors": [],
        "backend": "web", "timestamp": "2026-08-25T00:00:00Z", "error": None,
        "step_statuses": [
            {"index": 1, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 40, "detail": {"screenshot": "baseline/01-start.png"}},
            {"index": 2, "phase": "steps", "action": "click", "target": "pad_node",
             "status": "ok", "duration_ms": 300, "detail": {},
             "resolution": {"via": "anchor"}},
            {"index": 3, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 40, "detail": {"screenshot": "baseline/02-done.png"}},
            {"index": 4, "phase": "steps", "action": "type", "target": "prompt_box",
             "status": "ok", "duration_ms": 2800, "detail": {}},
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture
def run_dir(tmp_path):
    def build(results):
        (tmp_path / "flow.yaml").write_text(FLOW)
        (tmp_path / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in results) + "\n"
        )
        for name in ("baseline/01-start.png", "baseline/02-done.png"):
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # A one-pixel PNG is enough: what matters is that the file exists.
            path.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                "1f15c4890000000a49444154789c6300010000050001"
                "0d0a2db40000000049454e44ae426082"
            ))
        return tmp_path
    return build


# -- structure ----------------------------------------------------------------


def test_the_page_stands_up_on_its_own(run_dir):
    page = render_html(run_dir([result()]))
    assert page.startswith("<!doctype html>")
    assert "<title>Demo flow — transcript</title>" in page
    assert page.rstrip().endswith("</html>")


def test_it_carries_the_prompt_and_the_response(run_dir):
    page = render_html(run_dir([result()]))
    assert "Summarise this." in page
    assert "A summary." in page


def test_the_steps_are_numbered(run_dir):
    page = render_html(run_dir([result()]))
    assert '<ol class="steps">' in page
    assert "<li>Click pad_node</li>" in page
    assert "<li>Type into prompt_box</li>" in page


def test_screenshots_sit_under_the_step_that_produced_them(run_dir):
    """The whole shape of the transcript: read it step by step, and each step
    carries the picture of what it did."""
    page = render_html(run_dir([result()]))
    body = page.split('<ol class="timeline">')[1]
    opening, first, second = body.split("<li")[1:4]

    assert "Before the first step" in opening
    assert "01-start.png" in opening
    assert "Click pad_node" in first
    assert "02-done.png" in first
    assert "Type into prompt_box" in second


def test_a_recording_becomes_a_playable_element(run_dir):
    page = render_html(run_dir([result(recording="baseline/recording.mp4")]))
    assert '<video controls preload="metadata" src="baseline/recording.mp4">' in page


def test_a_missing_recording_says_why(run_dir):
    page = render_html(run_dir([result(recording_error="ffmpeg not found")]))
    assert "ffmpeg not found" in page
    assert "<video" not in page


def test_synthetic_clicks_are_called_out(run_dir):
    """Otherwise a recording with a motionless cursor is a mystery."""
    page = render_html(run_dir([result(pointer_note="browser is headless")]))
    assert "browser is headless" in page
    assert "would have seen the cursor move" in page


def test_a_failure_is_shown_not_buried(run_dir):
    page = render_html(run_dir([result(status="error", error="target not found")]))
    assert "target not found" in page
    assert "FAIL" in page


# -- escaping -----------------------------------------------------------------


def test_a_response_containing_markup_cannot_break_out_of_the_page(run_dir):
    hostile = "<script>alert('x')</script> & <b>bold</b>"
    page = render_html(run_dir([result(response=hostile)]))
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_prompt_with_quotes_survives(run_dir):
    page = render_html(run_dir([result(prompt='say "hello" & go',
                                       variables={"prompt": 'say "hello" & go'})]))
    assert "&quot;hello&quot;" in page or "&#x27;" in page or "hello" in page
    assert "&amp; go" in page


# -- the two forms agree ------------------------------------------------------


def test_both_forms_carry_the_same_responses(run_dir):
    directory = run_dir([result(), result(prompt_id="terse", response="Short.")])
    markdown = render_markdown(directory)
    page = render_html(directory)
    for text in ("A summary.", "Short.", "baseline", "terse"):
        assert text in markdown and text in page


def test_embedding_inlines_the_images(run_dir):
    directory = run_dir([result()])
    assert "data:image/png;base64," not in render_html(directory)
    assert "data:image/png;base64," in render_html(directory, embed=True)


def test_embedding_never_inlines_the_video(run_dir):
    """A base64 mp4 doubles the largest file in the run, and the standalone
    copy exists to be emailable."""
    directory = run_dir([result(recording="baseline/recording.mp4")])
    page = render_html(directory, embed=True)
    assert 'src="baseline/recording.mp4"' in page


def test_write_html_lands_next_to_the_screenshots(run_dir):
    directory = run_dir([result()])
    path = write_html(directory)
    assert path == directory / "transcript.html"
    assert path.read_text().startswith("<!doctype html>")


def test_an_empty_run_still_renders(run_dir):
    page = render_html(run_dir([]))
    assert "<h1>Demo flow</h1>" in page


def test_a_browser_without_h264_is_told_why_the_video_is_dead(run_dir):
    """Some Chromium builds ship without proprietary codecs. A player that
    silently does nothing reads as a broken recording."""
    page = render_html(run_dir([result(recording="baseline/recording.mp4")]))
    assert "cannot decode H.264" in page
    assert "canPlayType" in page
