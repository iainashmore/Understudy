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


def one(run, index: int = 0, **kwargs) -> str:
    """One prompt run's own page -- where the steps, the prompts, the replies
    and the screenshots live. render_html is an index of them."""
    from understudy.transcript import load_results
    from understudy.transcript_html import render_one_html

    return render_one_html(run, load_results(run)[index], **kwargs)


def result(**overrides):
    base = {
        "prompt_id": "baseline",
        "repeat_index": 0,
        "prompt": "Summarise this.",
        "variables": {"prompt": "Summarise this.", "new_name": "Bracket"},
        "response": "A summary.",
        "reads": {"response": "A summary."}, "read_images": {},
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
             "status": "ok", "duration_ms": 2800,
             "detail": {"text": "Summarise this."}},
            # A read step, because that is the only way `reads` is ever filled.
            {"index": 5, "phase": "steps", "action": "read", "target": "response",
             "status": "ok", "duration_ms": 5, "detail": {"store_as": "response"}},
        ],
    }
    base.update(overrides)
    # `response` is derived from the reads in a real result, so a test setting
    # one and not the other describes a run that cannot happen.
    if "response" in overrides and "reads" not in overrides:
        base["reads"] = {"response": overrides["response"]}
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
    page = one(run_dir([result()]))
    assert "Summarise this." in page
    assert "A summary." in page


def test_the_steps_are_numbered(run_dir):
    """There is one list of steps now, and it is the one with the screenshots
    and the replies attached -- not a bare list above it saying the same thing
    without any of them."""
    page = one(run_dir([result()]))
    assert '<ol class="steps">' not in page
    assert '<span class="step-no">1</span>' in page
    assert "Click pad_node" in page and "Type into prompt_box" in page


def test_screenshots_sit_under_the_step_that_produced_them(run_dir):
    """The whole shape of the transcript: read it step by step, and each step
    carries the picture of what it did."""
    page = one(run_dir([result()]))
    body = page.split('<ol class="timeline">')[1]
    opening, first, second = body.split("<li")[1:4]

    assert "Before the first step" in opening
    assert "01-start.png" in opening
    assert "Click pad_node" in first
    assert "02-done.png" in first
    assert "Type into prompt_box" in second


def test_a_recording_becomes_a_playable_element(run_dir):
    page = one(run_dir([result(recording="baseline/recording.mp4")]))
    assert '<video controls preload="metadata" src="recording.mp4">' in page


def test_a_missing_recording_says_why(run_dir):
    page = one(run_dir([result(recording_error="ffmpeg not found")]))
    assert "ffmpeg not found" in page
    assert "<video" not in page


def test_synthetic_clicks_are_called_out(run_dir):
    """Otherwise a recording with a motionless cursor is a mystery."""
    page = one(run_dir([result(pointer_note="browser is headless")]))
    assert "browser is headless" in page
    assert "would have seen the cursor move" in page


def test_a_failure_is_shown_not_buried(run_dir):
    page = one(run_dir([result(status="error", error="target not found")]))
    assert "target not found" in page
    assert "FAIL" in page


# -- escaping -----------------------------------------------------------------


def test_a_response_containing_markup_cannot_break_out_of_the_page(run_dir):
    hostile = "<script>alert('x')</script> & <b>bold</b>"
    page = one(run_dir([result(response=hostile)]))
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_prompt_with_quotes_survives(run_dir):
    page = one(run_dir([result(prompt='say "hello" & go',
                                       variables={"prompt": 'say "hello" & go'})]))
    assert "&quot;hello&quot;" in page or "&#x27;" in page or "hello" in page
    assert "&amp; go" in page


# -- the two forms agree ------------------------------------------------------


def test_both_forms_carry_the_same_responses(run_dir):
    from understudy.transcript import render_one

    from understudy.transcript import load_results

    directory = run_dir([result(), result(prompt_id="terse", response="Short.")])
    results = load_results(directory)
    for index, response in enumerate(("A summary.", "Short.")):
        assert response in render_one(directory, results[index])
        assert response in one(directory, index)
    # And the index names both, whichever form you open.
    for name in ("baseline", "terse"):
        assert name in render_markdown(directory) and name in render_html(directory)


def test_what_was_under_test_reads_as_separate_tags(run_dir):
    """Comparing FD03 against FD04 starts with finding the two runs, and
    "CATIA V5 R2026x · LEO FD03" as one sentence buries the part that
    distinguishes them."""
    page = one(run_dir([result(subject={
        "app": "CATIA V5", "app_version": "R2026x",
        "model": "LEO", "model_version": "FD03",
    })]))

    assert page.count('class="tagchip"') == 4
    assert ">FD03</span>" in page
    assert 'title="Assistant version"' in page


class TestReadingOnlyTheConversation:
    """Reading what the assistant said across forty prompts should not mean
    scrolling past forty screenshots of a spec tree to do it."""

    def test_the_turns_are_marked_and_the_other_steps_are_not(self, run_dir):
        page = one(run_dir([a_conversation()]))

        assert page.count('data-turn="1"') == 1
        assert page.count('data-turn="2"') == 1
        # The click between the two turns is a step, and is not marked.
        assert '<li class="entry"><div class="head">' in page

    def test_there_is_a_control_to_switch_between_them(self, run_dir):
        page = one(run_dir([a_conversation()]))
        assert 'data-view="turns"' in page and 'data-view="all"' in page

    def test_a_prompt_run_with_no_conversation_offers_no_such_view(self, run_dir):
        """A flow that only clicks has nothing to filter down to, and a button
        that empties the page is worse than no button."""
        page = one(run_dir([result(reads={}, response="", step_statuses=[
            {"index": 1, "phase": "steps", "action": "click", "target": "pad",
             "status": "ok", "duration_ms": 10},
        ])]))
        assert '<div class="viewbar">' not in page

    def test_it_hides_the_steps_rather_than_writing_a_second_document(self, run_dir):
        """The steps are the transcript. A separate conversation-only render
        is a second document that can disagree with the first."""
        page = one(run_dir([a_conversation()]))

        assert "only-turns li.entry:not([data-turn])" in page
        assert "Now fillet it." in page, "still in the page, just hidden"


class TestTheRawFiles:
    """What went in and what came out, linked from the transcript. It is what
    somebody checks when they doubt what they are reading."""

    def test_they_are_linked(self, run_dir):
        page = render_html(run_dir([result()]))
        assert '<a href="flow.yaml">flow.yaml</a>' in page
        assert '<a href="results.jsonl">results.jsonl</a>' in page

    def test_a_standalone_copy_carries_them(self, run_dir):
        """The exported transcript is one file, sent to somebody who has no
        run directory. A relative link to flow.yaml is dead the moment it
        leaves -- which is exactly when a reader wants to see what was run."""
        page = render_html(run_dir([result()]), embed=True)

        assert 'download="flow.yaml"' in page
        assert 'download="results.jsonl"' in page
        assert '<a href="flow.yaml">' not in page, "still a dead relative link"

    def test_a_file_too_large_to_inline_stays_a_link(self, run_dir):
        from understudy.transcript_html import MAX_INLINE_FILE_BYTES

        directory = run_dir([result()])
        # A long sweep's CSV is not nothing, and a transcript nobody can open
        # is worse than a link.
        (directory / "results.csv").write_bytes(b"x" * (MAX_INLINE_FILE_BYTES + 1))

        page = render_html(directory, embed=True)
        assert '<a href="results.csv">' in page
        assert 'download="flow.yaml"' in page, "the small ones still travel"


def test_embedding_inlines_the_images(run_dir):
    directory = run_dir([result()])
    assert "data:image/png;base64," not in one(directory)
    assert "data:image/png;base64," in one(directory, embed=True)


def a_video(directory, size):
    path = directory / "baseline" / "recording.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


def test_embedding_inlines_a_video_small_enough_to_email(run_dir):
    """An exported transcript that leaves its video behind shows a player that
    cannot play, which reads as a broken recording rather than a moved file."""
    directory = run_dir([result(recording="baseline/recording.mp4")])
    a_video(directory, 60_000)

    page = one(directory, embed=True)
    assert "data:video/mp4;base64," in page


def test_a_large_video_stays_a_link_and_says_so(run_dir):
    """Base64 costs a third on top of the largest file in the run, and a sweep
    of forty variants carries forty of them."""
    from understudy.transcript_html import MAX_INLINE_VIDEO_BYTES

    directory = run_dir([result(recording="baseline/recording.mp4")])
    a_video(directory, MAX_INLINE_VIDEO_BYTES + 1)

    page = one(directory, embed=True)
    assert "data:video/mp4" not in page
    assert 'src="recording.mp4"' in page
    assert "not inside this file" in page, \
        "the reader is left to work out why the player is empty"


def test_the_viewer_in_the_app_still_points_at_the_file(run_dir):
    """Inlining is for the exported copy. The viewer serves the run directory,
    so there is nothing to inline and megabytes of base64 to avoid."""
    directory = run_dir([result(recording="baseline/recording.mp4")])
    a_video(directory, 60_000)

    page = one(directory)
    assert 'src="recording.mp4"' in page
    assert "data:video/mp4" not in page


def a_conversation():
    """Two exchanges in one prompt run, with a click between them."""
    return result(
        prompt="Add a 10mm hole.", response="",
        reads={"first": "Hole added.", "second": "Fillet added."},
        step_statuses=[
            {"index": 1, "phase": "steps", "action": "type", "target": "leo_box",
             "status": "ok", "duration_ms": 90,
             "detail": {"text": "Add a 10mm hole."}},
            {"index": 2, "phase": "steps", "action": "read", "target": "leo_reply",
             "status": "ok", "duration_ms": 10, "detail": {"store_as": "first"}},
            {"index": 3, "phase": "steps", "action": "click", "target": "tree_node",
             "status": "ok", "duration_ms": 30},
            {"index": 4, "phase": "steps", "action": "type", "target": "leo_box",
             "status": "ok", "duration_ms": 80,
             "detail": {"text": "Now fillet it."}},
            {"index": 5, "phase": "steps", "action": "read", "target": "leo_reply",
             "status": "ok", "duration_ms": 10, "detail": {"store_as": "second"}},
        ],
    )


def test_a_conversation_renders_as_one_block_per_turn(run_dir):
    """Not one prompt and whichever read happened to be stored as `response`."""
    page = one(run_dir([a_conversation()]))

    # At the steps that said them, not gathered into a block of their own.
    assert page.count('class="tag">prompt') == 2
    assert page.count('class="tag">reply') == 2
    assert "Now fillet it." in page and "Fillet added." in page


def test_the_exchange_rule_is_not_print_only(run_dir):
    """It was, first time: the CSS landed inside the @media print block, so on
    screen four turns ran together as one wall of text."""
    from understudy.transcript_html import STYLE

    on_screen, _, printed = STYLE.partition("@media print")
    assert ".exchange {" in on_screen
    assert ".exchange" in printed, "and it still avoids breaking across pages"


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
    page = one(run_dir([result(recording="baseline/recording.mp4")]))
    assert "cannot decode H.264" in page
    assert "canPlayType" in page
