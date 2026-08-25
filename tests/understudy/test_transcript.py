"""The markdown report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from understudy.transcript import load_results, render_markdown, summarise, write_transcript
from harness.image import to_png_bytes


def png() -> bytes:
    return to_png_bytes(np.full((40, 60, 3), 200, dtype=np.uint8))


def make_run(tmp_path: Path, results: list[dict]) -> Path:
    run = tmp_path / "2026-08-24T10-00-00"
    run.mkdir(parents=True)
    (run / "flow.yaml").write_text("version: 1\nname: demo-flow\nsteps: []\n")
    (run / "prompts.yaml").write_text("- id: a\n  prompt: hello\n")
    for result in results:
        for relative in result.get("screenshots", []):
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png())
    (run / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results) + "\n"
    )
    return run


def result(**overrides) -> dict:
    base = {
        "prompt_id": "baseline", "repeat_index": 0, "prompt": "Summarise this.",
        "variables": {"prompt": "Summarise this."}, "response": "A summary.",
        "reads": {"response": "A summary."}, "read_images": {},
        "status": "ok", "duration_ms": 1200,
        "screenshots": ["baseline/01-before.png", "baseline/02-after.png"],
        "used_fallbacks": [], "agent_resolutions": [], "learned_anchors": [],
        "backend": "web", "timestamp": "2026-08-24T10:00:07Z", "error": None,
        "step_statuses": [
            {"index": 1, "phase": "reset", "action": "click", "target": "new_chat",
             "status": "ok", "duration_ms": 40, "resolution": {"via": "selector"}},
            {"index": 1, "phase": "steps", "action": "type", "target": "prompt_box",
             "status": "ok", "duration_ms": 90, "resolution": {"via": "anchor"}},
        ],
    }
    base.update(overrides)
    return base


def a_conversation(**overrides) -> dict:
    """Two exchanges in one prompt run: ask, read, click elsewhere, ask again.

    This is what a real session with an embedded assistant looks like -- not
    one question, but several, with clicks between them.
    """
    base = result(
        prompt="Add a 10mm hole.",
        variables={"prompt": "Add a 10mm hole.", "followup": "Now fillet it."},
        reads={"first": "Hole added.", "second": "Fillet added."},
        response="",
        step_statuses=[
            {"index": 1, "phase": "steps", "action": "type", "target": "leo_box",
             "status": "ok", "duration_ms": 90, "detail": {"text": "Add a 10mm hole."},
             "resolution": {"via": "selector"}},
            {"index": 2, "phase": "steps", "action": "read", "target": "leo_reply",
             "status": "ok", "duration_ms": 10,
             "detail": {"store_as": "first", "chars": 11}},
            {"index": 3, "phase": "steps", "action": "click", "target": "tree_node",
             "status": "ok", "duration_ms": 30, "resolution": {"via": "selector"}},
            {"index": 4, "phase": "steps", "action": "type", "target": "leo_box",
             "status": "ok", "duration_ms": 80, "detail": {"text": "Now fillet it."},
             "resolution": {"via": "selector"}},
            {"index": 5, "phase": "steps", "action": "read", "target": "leo_reply",
             "status": "ok", "duration_ms": 10,
             "detail": {"store_as": "second", "chars": 13}},
        ],
    )
    base.update(overrides)
    return base


class TestAConversationRatherThanOneQuestion:
    """A flow is not always one prompt. A session with an embedded assistant is
    several: click into the tree, ask, read the answer, click elsewhere, ask
    again. A transcript showing one prompt and one response is showing a
    quarter of what happened."""

    def test_it_finds_every_exchange_in_order(self):
        from understudy.transcript import exchanges

        turns = exchanges(a_conversation())

        assert [t.prompt for t in turns] == ["Add a 10mm hole.", "Now fillet it."]
        assert [t.response for t in turns] == ["Hole added.", "Fillet added."]

    def test_each_exchange_knows_which_step_said_it(self):
        """So it can be quoted to R&D by number, like every other step.

        1 and 3, not 1 and 4: the numbers count user actions, and the read
        between them is the tool observing rather than something a person did.
        """
        from understudy.transcript import exchanges

        assert [t.step for t in exchanges(a_conversation())] == [1, 3]

    def test_a_reply_read_after_a_later_click_still_belongs_to_its_prompt(self):
        """Reads attach to the last thing typed before them, which is the rule
        a person reading the screen uses."""
        from understudy.transcript import exchanges

        conversation = a_conversation()
        # The reply lands only after the click that follows the prompt.
        conversation["step_statuses"][1], conversation["step_statuses"][2] = (
            conversation["step_statuses"][2], conversation["step_statuses"][1]
        )
        turns = exchanges(conversation)

        assert turns[0].response == "Hole added."

    def test_there_is_no_limit_on_how_many_turns(self):
        """Two is not the shape. A session is as long as it is -- ask, read,
        click, ask, read, click, for as many turns as the work takes."""
        from understudy.transcript import exchanges

        steps, index = [], 1
        for turn in range(1, 7):
            steps.append({"index": index, "phase": "steps", "action": "type",
                          "target": "leo_box", "status": "ok", "duration_ms": 50,
                          "detail": {"text": f"question {turn}"}})
            steps.append({"index": index + 1, "phase": "steps", "action": "read",
                          "target": "leo_reply", "status": "ok", "duration_ms": 5,
                          "detail": {"store_as": f"answer_{turn}"}})
            steps.append({"index": index + 2, "phase": "steps", "action": "click",
                          "target": "tree_node", "status": "ok", "duration_ms": 20})
            index += 3

        turns = exchanges(result(
            response="", step_statuses=steps,
            reads={f"answer_{n}": f"answer {n}" for n in range(1, 7)},
        ))

        assert [t.number for t in turns] == [1, 2, 3, 4, 5, 6]
        assert [t.prompt for t in turns] == [f"question {n}" for n in range(1, 7)]
        assert [t.response for t in turns] == [f"answer {n}" for n in range(1, 7)]

    def test_typing_that_nothing_answered_is_a_step_not_a_turn(self):
        """Flows type into form fields as well as prompt boxes -- renaming a
        part before asking about it is two typed steps and one question. A
        filename listed as an exchange would be wrong in the one place this
        view exists to be right."""
        from understudy.transcript import exchanges

        turns = exchanges(result(
            response="", reads={"answer": "It is 40mm."},
            step_statuses=[
                {"index": 1, "phase": "steps", "action": "type",
                 "target": "dialog_name_field", "status": "ok", "duration_ms": 40,
                 "detail": {"text": "Bracket"}},
                {"index": 2, "phase": "steps", "action": "click",
                 "target": "dialog_done", "status": "ok", "duration_ms": 20},
                {"index": 3, "phase": "steps", "action": "type", "target": "leo_box",
                 "status": "ok", "duration_ms": 60,
                 "detail": {"text": "How wide is Bracket?"}},
                {"index": 4, "phase": "steps", "action": "read", "target": "leo_reply",
                 "status": "ok", "duration_ms": 5, "detail": {"store_as": "answer"}},
            ],
        ))

        assert [t.prompt for t in turns] == ["How wide is Bracket?"]

    def test_the_things_that_were_said_are_not_also_listed_as_variables(self, tmp_path):
        """A conversation flow has one variable per turn. Printing them as a
        list of variables and again as the conversation makes a reader check
        whether the two differ."""
        run = make_run(tmp_path, [a_conversation(
            variables={"opening": "Add a 10mm hole.", "followup": "Now fillet it.",
                       "style": "terse"},
        )])
        text = render_markdown(run)

        assert "`style` = terse" in text
        assert "`opening`" not in text, "said once, listed twice"

    def test_a_flow_with_no_prompt_variable_has_no_empty_prompt_block(self, tmp_path):
        """A conversation names its turns opening/followup/closing. There is
        no variable called `prompt`, and an empty code fence under a Prompt
        heading reads as a run that sent nothing."""
        run = make_run(tmp_path, [a_conversation(
            variables={"opening": "Add a 10mm hole.", "followup": "Now fillet it."},
        )])
        text = render_markdown(run)

        assert "### Prompt\n" not in text

    def test_the_transcript_shows_both_turns(self, tmp_path):
        run = make_run(tmp_path, [a_conversation()])
        text = render_markdown(run)

        assert "2 exchanges" in text
        assert "Add a 10mm hole." in text and "Now fillet it." in text
        assert "Hole added." in text and "Fillet added." in text

    def test_one_prompt_still_reads_as_one_prompt(self, tmp_path):
        """The common case is unchanged: no conversation scaffolding around a
        single question."""
        text = render_markdown(make_run(tmp_path, [result()]))

        assert "exchanges" not in text
        assert "### Response" in text


class TestStructure:
    def test_the_header_says_what_was_run(self, tmp_path):
        run = make_run(tmp_path, [result()])
        text = render_markdown(run)

        assert text.startswith("# demo-flow")
        assert "· web" in text
        assert "1 ok, 0 failed, 0 timed out" in text
        assert "**Input** [`flow.yaml`](flow.yaml)" in text

    def test_the_header_reads_in_the_order_it_is_wanted(self, tmp_path):
        """What was under test, then the raw material either side of the run,
        then the video, then the steps. The video is the fastest way to know
        whether the replay did what it was meant to, so it goes above the
        detail rather than under it."""
        run = make_run(tmp_path, [result(
            recording="baseline/recording.mp4",
            subject={"app": "CATIA V5", "app_version": "R33", "model": "LEO"},
        )])
        text = render_markdown(run)

        order = [text.index(mark) for mark in
                 ("**Under test**", "**Input**", "## Recording", "## Steps")]
        assert order == sorted(order), text[:600]

    def test_the_summary_has_a_row_per_variant(self, tmp_path):
        run = make_run(tmp_path, [result(), result(prompt_id="terse", duration_ms=900)])
        text = render_markdown(run)
        assert "| [baseline](#baseline) | pass | 1200 ms |" in text
        assert "| [terse](#terse) | pass | 900 ms |" in text

    def test_prompts_and_responses_are_together_for_comparison(self, tmp_path):
        """Comparing them is the reason the run happened."""
        run = make_run(tmp_path, [
            result(prompt="one", response="first"),
            result(prompt_id="b", prompt="two", response="second"),
        ])
        section = render_markdown(run).split("## Prompts and responses")[1]
        assert "> first" in section and "> second" in section

    def test_screenshots_are_linked_relatively_and_exist(self, tmp_path):
        run = make_run(tmp_path, [result()])
        text = render_markdown(run)

        assert '<img src="baseline/01-before.png"' in text
        assert (run / "baseline/01-before.png").exists()
        assert "before" in text and "after" in text, "captions come from the filenames"

    def test_reset_steps_are_distinguishable_from_flow_steps(self, tmp_path):
        """A reset click and the first real step are both user actions, so both
        get a number; the transcript has to say which is which."""
        text = render_markdown(make_run(tmp_path, [result()]))
        assert "| 1 (reset) | click |" in text
        assert "| 2 | type |" in text
        assert "_(reset)_" in text

    def test_how_each_target_was_resolved_is_reported(self, tmp_path):
        text = render_markdown(make_run(tmp_path, [result()]))
        assert "| selector |" in text and "| anchor |" in text


class TestAwkwardContent:
    def test_a_response_containing_a_code_fence_cannot_escape_its_block(self, tmp_path):
        run = make_run(tmp_path, [result(response="here is code:\n```py\nx=1\n```\ndone")])
        text = render_markdown(run)
        assert "````text" in text, "the fence must be longer than the content's"

    def test_pipes_in_a_response_do_not_break_the_summary_table(self, tmp_path):
        run = make_run(tmp_path, [result(response="a | b | c")])
        row = [l for l in render_markdown(run).splitlines() if l.startswith("| [baseline]")][0]
        assert row.count("|") == 6, "only the table's own separators"

    def test_a_missing_screenshot_is_noted_rather_than_a_broken_link(self, tmp_path):
        run = make_run(tmp_path, [result()])
        (run / "baseline/01-before.png").unlink()
        assert "(missing: baseline/01-before.png)" in render_markdown(run)

    def test_a_failure_is_surfaced_at_the_top_and_in_the_section(self, tmp_path):
        run = make_run(tmp_path, [result(status="error", error="could not resolve 'send'")])
        text = render_markdown(run)
        assert "FAIL" in text
        assert "0 ok, 1 failed" in text
        assert "**Error** could not resolve 'send'" in text

    def test_a_pixels_only_response_shows_the_image_instead(self, tmp_path):
        run = make_run(tmp_path, [result(
            response="", reads={}, screenshots=["baseline/01-before.png", "baseline/04-src.png"],
            read_images={"response": "baseline/04-src.png"},
        )])
        text = render_markdown(run)

        assert "_No text captured._" in text
        assert "Recorded `response` region:" in text
        assert '<img src="baseline/04-src.png"' in text
        assert text.count('src="baseline/04-src.png"') == 1, "not also as a screenshot"

    def test_repeats_get_distinct_sections(self, tmp_path):
        run = make_run(tmp_path, [result(), result(repeat_index=1)])
        text = render_markdown(run)
        assert "## baseline" in text and "## baseline (repeat 2)" in text


class TestFilesAndModes:
    def test_the_report_is_written_into_the_run_directory(self, tmp_path):
        run = make_run(tmp_path, [result()])
        path = write_transcript(run)
        assert path == run / "transcript.md"
        assert path.read_text(encoding="utf-8").startswith("# demo-flow")

    def test_embedding_makes_the_report_a_single_file(self, tmp_path):
        run = make_run(tmp_path, [result()])
        text = render_markdown(run, embed=True)
        assert "data:image/png;base64," in text
        assert 'src="baseline/' not in text, "nothing left pointing outside the file"

    def test_a_run_without_results_is_a_clear_error(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError, match="no results.jsonl"):
            load_results(tmp_path / "empty")

    def test_the_summary_counts_every_status(self, tmp_path):
        run = make_run(tmp_path, [
            result(), result(prompt_id="b", status="timeout"),
            result(prompt_id="c", status="error"),
        ])
        summary = summarise(run, load_results(run))
        assert (summary.passed, summary.timed_out, summary.failed) == (1, 1, 1)


class TestTheTimeline:
    """The transcript is read step by step, so it is built that way.

    A gallery of screenshots at the bottom and a table of steps below it makes
    the reader do the joining. Every question anyone asks -- what did step 4 do,
    what did the screen look like after it, what did the assistant reply -- is a
    question about one step.
    """

    def steps(self):
        return [
            {"index": 1, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 30, "detail": {"screenshot": "baseline/01-before.png"}},
            {"index": 2, "phase": "steps", "action": "click", "target": "send",
             "status": "ok", "duration_ms": 80, "resolution": {"via": "anchor"},
             "detail": {}},
            {"index": 3, "phase": "steps", "action": "wait_for_stable",
             "target": "reply", "status": "ok", "duration_ms": 1500,
             "detail": {"waited_ms": 1400, "signal": "stable"}},
            {"index": 4, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 30, "detail": {"screenshot": "baseline/02-after.png"}},
            {"index": 5, "phase": "steps", "action": "read", "status": "ok",
             "duration_ms": 60, "detail": {"store_as": "response"}},
        ]

    def test_a_step_carries_the_screenshots_taken_after_it(self, tmp_path):
        from understudy.transcript import timeline

        entries = timeline(result(step_statuses=self.steps()), {})
        assert [e.number for e in entries] == [0, 1]
        assert entries[0].screenshots == ("baseline/01-before.png",)
        assert entries[1].screenshots == ("baseline/02-after.png",)

    def test_the_wait_and_the_read_belong_to_the_click_that_caused_them(self, tmp_path):
        from understudy.transcript import timeline

        entries = timeline(result(step_statuses=self.steps()), {})
        click = entries[1]
        assert click.waited_ms == 1400
        assert click.reads == (("response", "A summary."),)

    def test_typed_text_is_shown_against_the_step_that_typed_it(self, tmp_path):
        from understudy.transcript import timeline

        steps = [{"index": 1, "phase": "steps", "action": "type",
                  "target": "prompt_box", "status": "ok", "duration_ms": 2800,
                  "detail": {"text": "Summarise this."}}]
        entries = timeline(result(step_statuses=steps), {})
        assert entries[0].typed == "Summarise this."

    def test_the_markdown_reads_as_a_sequence(self, tmp_path):
        run = make_run(tmp_path, [result(step_statuses=self.steps())])
        text = render_markdown(run)
        section = text.split("### Step by step")[1]

        assert "**Before the first step**" in section
        assert "**1. Click send**" in section
        assert "waited 1400 ms for the response" in section
        assert "Read as `response`:" in section
        # The picture of the outcome sits under the step, not in a gallery.
        before, after = section.split("**1. Click send**")
        assert "01-before.png" in before and "02-after.png" in after

    def test_a_screenshot_no_step_claims_is_still_shown(self, tmp_path):
        """A run from an older version, or an image written outside a capture
        step, must not vanish."""
        run = make_run(tmp_path, [result()])       # no capture steps at all
        text = render_markdown(run)
        assert "#### Other screenshots" in text
        assert "01-before.png" in text and "02-after.png" in text

    def test_a_failed_step_is_marked_where_it_failed(self, tmp_path):
        steps = self.steps()
        steps[1] = dict(steps[1], status="error", error="target not found")
        run = make_run(tmp_path, [result(step_statuses=steps, status="error")])
        section = render_markdown(run).split("### Step by step")[1]
        assert "**FAIL**" in section
        assert "target not found" in section
