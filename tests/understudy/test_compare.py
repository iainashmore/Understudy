"""Comparing runs of the same flow.

The payoff. Everything else -- holding the click path still, typing at a human
speed, recording which release produced each answer -- exists so that two runs
can be put side by side and the difference between them means something.

The tuning that matters is what counts as "changed". A comparison that cries
wolf over a trailing full stop gets ignored, and an ignored comparison is worse
than none.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from understudy.compare import compare, normalise
from understudy.compare_report import render_html, render_markdown, write_comparison


def make_run(tmp_path, name, subject, answers, statuses=None, prompts=None):
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "flow.yaml").write_text("version: 1\nname: leo-regression\n")
    rows = []
    for index, (prompt_id, response) in enumerate(answers.items()):
        rows.append({
            "prompt_id": prompt_id, "repeat_index": 0,
            "prompt": (prompts or {}).get(prompt_id, f"prompt for {prompt_id}"),
            "variables": {},
            "response": response, "reads": {}, "read_images": {},
            "status": (statuses or {}).get(prompt_id, "ok"),
            "duration_ms": 100, "screenshots": [], "step_statuses": [],
            "backend": "web", "flow": "leo-regression", "subject": subject,
            "timestamp": f"2026-09-0{index + 1}T10:00:00Z",
        })
    (run / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    return run


R32 = {"app": "CATIA V5", "app_version": "R32 SP4", "model": "LEO"}
R33 = {"app": "CATIA V5", "app_version": "R33", "model": "LEO",
       "model_version": "2027x"}


class TestWhatCountsAsChanged:
    def compare_two(self, tmp_path, before, after):
        return compare([
            make_run(tmp_path, "before", R32, {"a": before}),
            make_run(tmp_path, "after", R33, {"a": after}),
        ]).rows[0]

    def test_identical_is_unchanged(self, tmp_path):
        assert self.compare_two(tmp_path, "The pad is 40mm.", "The pad is 40mm.").verdict == "same"

    def test_a_different_answer_is_changed(self, tmp_path):
        row = self.compare_two(tmp_path, "The pad is 40mm.", "The pad measures 55mm.")
        assert row.verdict == "changed" and row.interesting

    def test_trailing_whitespace_is_not_a_behaviour_change(self, tmp_path):
        assert self.compare_two(tmp_path, "The pad is 40mm.", "  The pad is 40mm.  ").verdict == "same"

    def test_a_trailing_full_stop_is_not_a_behaviour_change(self, tmp_path):
        assert self.compare_two(tmp_path, "The pad is 40mm.", "The pad is 40mm").verdict == "same"

    def test_reflowed_whitespace_is_not_a_behaviour_change(self, tmp_path):
        """A streaming renderer wrapping its own output differently."""
        assert self.compare_two(tmp_path, "The pad\nis 40mm", "The pad is  40mm").verdict == "same"

    def test_case_alone_is_not_a_behaviour_change(self, tmp_path):
        assert self.compare_two(tmp_path, "The Pad Is 40mm", "the pad is 40mm").verdict == "same"

    def test_a_prompt_missing_from_one_run_is_flagged(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x", "b": "y"}),
            make_run(tmp_path, "after", R33, {"a": "x"}),
        ])
        verdicts = {row.prompt_id: row.verdict for row in comparison.rows}
        assert verdicts == {"a": "same", "b": "missing"}

    def test_a_failed_variant_is_flagged_rather_than_compared(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x"}),
            make_run(tmp_path, "after", R33, {"a": ""}, statuses={"a": "error"}),
        ])
        assert comparison.rows[0].verdict == "failed"

    def test_normalise_is_what_does_it(self):
        assert normalise("  The Pad\nis 40mm. ") == "the pad is 40mm"


class TestTheComparisonItself:
    def test_columns_are_labelled_by_what_was_under_test(self, tmp_path):
        """Not by a timestamp. "CATIA V5 R33 · LEO 2027x" is what a reader
        needs; "2026-09-01T14-22" is not."""
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x"}),
            make_run(tmp_path, "after", R33, {"a": "x"}),
        ])
        assert comparison.columns[0].heading == "CATIA V5 R32 SP4 · LEO"
        assert comparison.columns[1].heading == "CATIA V5 R33 · LEO 2027x"

    def test_a_run_with_no_subject_falls_back_to_its_folder_name(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "2026-09-01", {}, {"a": "x"}),
            make_run(tmp_path, "2026-09-08", {}, {"a": "x"}),
        ])
        assert comparison.columns[0].heading == "2026-09-01"

    def test_the_order_given_is_the_order_shown(self, tmp_path):
        """Guessing before-and-after from timestamps would be wrong exactly
        when somebody re-runs an old release to check something."""
        after = make_run(tmp_path, "after", R33, {"a": "x"})
        before = make_run(tmp_path, "before", R32, {"a": "x"})
        assert [c.label for c in compare([after, before]).columns] == ["after", "before"]

    def test_the_headline_says_it_in_one_line(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x", "b": "y"}),
            make_run(tmp_path, "after", R33, {"a": "x", "b": "z"}),
        ])
        assert comparison.headline() == "1 same, 1 changed"

    def test_no_change_says_so_plainly(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x"}),
            make_run(tmp_path, "after", R33, {"a": "x"}),
        ])
        assert comparison.headline() == "no change across 1 prompt(s)"

    def test_more_than_two_runs(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "r32", R32, {"a": "x"}),
            make_run(tmp_path, "r33", R33, {"a": "x"}),
            make_run(tmp_path, "r34", {"app_version": "R34"}, {"a": "different"}),
        ])
        assert len(comparison.columns) == 3
        assert comparison.rows[0].verdict == "changed"

    def test_comparing_one_run_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="at least two"):
            compare([make_run(tmp_path, "only", R32, {"a": "x"})])

    def test_comparing_unlike_flows_is_noticed(self, tmp_path):
        before = make_run(tmp_path, "before", R32, {"a": "x"})
        after = make_run(tmp_path, "after", R33, {"a": "x"})
        rows = [json.loads(l) for l in (after / "results.jsonl").read_text().splitlines()]
        rows[0]["flow"] = "something-else"
        (after / "results.jsonl").write_text(json.dumps(rows[0]) + "\n")
        assert compare([before, after]).mixed_flows


class TestTheWrittenComparison:
    @pytest.fixture
    def comparison(self, tmp_path):
        return compare([
            make_run(tmp_path, "before", R32, {"a": "The pad is 40mm.", "b": "Yes."}),
            make_run(tmp_path, "after", R33, {"a": "The pad measures 55mm.", "b": "Yes."}),
        ])

    def test_changed_rows_come_first(self, comparison):
        """Burying the row that moved among ninety that did not is how a
        comparison stops being read."""
        markdown = render_markdown(comparison)
        assert markdown.index("**a**") < markdown.index("**b**")

    def test_both_answers_are_shown_under_their_release(self, comparison):
        markdown = render_markdown(comparison)
        assert "CATIA V5 R32 SP4" in markdown and "CATIA V5 R33" in markdown
        assert "The pad measures 55mm." in markdown

    def test_the_page_escapes_what_a_model_said(self, comparison):
        comparison.rows[0].responses[1] = "<script>alert('x')</script>"
        page = render_html(comparison)
        assert "<script>alert" not in page and "&lt;script&gt;" in page

    def test_both_forms_are_written(self, comparison, tmp_path):
        paths = write_comparison(comparison, tmp_path / "out")
        assert [p.name for p in paths] == ["comparison.md", "comparison.html"]
        assert all(p.exists() for p in paths)

    def test_a_named_file_is_honoured(self, comparison, tmp_path):
        paths = write_comparison(comparison, tmp_path / "r32-vs-r33.md")
        assert paths[0].name == "r32-vs-r33.md"
        assert paths[1].name == "r32-vs-r33.html"


class TestSteppingThroughBothRuns:
    """The divergence worth finding is often visual and several steps before
    the answer -- a dialog that opened somewhere else, a field that did not
    clear. A wall of screenshots does not show that; two pictures of the same
    step, side by side, does.
    """

    def make_stepped_run(self, tmp_path, name, subject, shots):
        run = tmp_path / name
        (run / "baseline").mkdir(parents=True)
        for shot in shots:
            (run / shot).write_bytes(b"\x89PNG\r\n")
        steps = [
            {"index": 1, "phase": "steps", "action": "click", "target": "new_chat",
             "status": "ok", "duration_ms": 40, "detail": {}},
            {"index": 2, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 10, "detail": {"screenshot": shots[0]}},
            {"index": 3, "phase": "steps", "action": "type", "target": "prompt_box",
             "status": "ok", "duration_ms": 2800, "detail": {"text": "hello"}},
            {"index": 4, "phase": "steps", "action": "capture", "status": "ok",
             "duration_ms": 10, "detail": {"screenshot": shots[1]}},
        ]
        (run / "results.jsonl").write_text(json.dumps({
            "prompt_id": "baseline", "repeat_index": 0, "prompt": "hello",
            "variables": {}, "response": "hi", "reads": {}, "read_images": {},
            "status": "ok", "duration_ms": 100, "screenshots": list(shots),
            "step_statuses": steps, "backend": "web", "flow": "demo",
            "subject": subject, "timestamp": "2026-09-01T10:00:00Z",
        }) + "\n")
        return run

    def comparison(self, tmp_path):
        shots = ["baseline/01.png", "baseline/02.png"]
        return compare([
            self.make_stepped_run(tmp_path, "r32", R32, shots),
            self.make_stepped_run(tmp_path, "r33", R33, shots),
        ])

    def test_the_user_actions_are_paired_up_across_runs(self, tmp_path):
        comparison = self.comparison(tmp_path)
        steps = comparison.steps["baseline"]
        assert len(steps) == 2, "two columns"
        assert [v.number for v in steps[0]] == [1, 2]
        assert steps[0][0].description == "Click new_chat"

    def test_a_step_carries_the_screenshot_taken_after_it(self, tmp_path):
        steps = self.comparison(tmp_path).steps["baseline"]
        assert steps[0][0].screenshot == "baseline/01.png"
        assert steps[0][1].screenshot == "baseline/02.png"

    def test_a_step_carries_what_it_typed(self, tmp_path):
        assert self.comparison(tmp_path).steps["baseline"][0][1].typed == "hello"

    def test_image_paths_are_rewritten_to_reach_out_of_the_comparison_folder(
            self, tmp_path):
        """The page is written to comparisons/<name>/, and the screenshots are
        in runs/. A path relative to the run is a broken image on the page."""
        comparison = self.comparison(tmp_path)
        out = tmp_path / "comparisons" / "r32-vs-r33"
        write_comparison(comparison, out)

        page = (out / "comparison.html").read_text()
        data = json.loads(re.search(r"window\.__comparison = (\{.*?\});", page,
                                    re.S).group(1))
        shots = [run["shot"] for step in data["prompts"]["baseline"]
                 for run in step["runs"] if run.get("shot")]
        assert shots, "there should be screenshots to show"
        assert all((out / shot).exists() for shot in shots), shots

    def test_a_run_missing_a_step_is_marked_rather_than_silently_shifted(self, tmp_path):
        """Lining up step 3 of one run against step 4 of another would be
        worse than showing nothing."""
        shots = ["baseline/01.png", "baseline/02.png"]
        long_run = self.make_stepped_run(tmp_path, "r32", R32, shots)
        short = self.make_stepped_run(tmp_path, "r33", R33, shots)
        rows = [json.loads(l) for l in (short / "results.jsonl").read_text().splitlines()]
        rows[0]["step_statuses"] = rows[0]["step_statuses"][:2]
        (short / "results.jsonl").write_text(json.dumps(rows[0]) + "\n")

        comparison = compare([long_run, short])
        page = render_html(comparison)
        data = json.loads(re.search(r"window\.__comparison = (\{.*?\});", page,
                                    re.S).group(1))
        second_step = data["prompts"]["baseline"][1]
        assert second_step["runs"][1]["missing"] is True

    def test_the_stepper_is_left_out_when_there_is_nothing_to_step_through(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "x"}),
            make_run(tmp_path, "after", R33, {"a": "y"}),
        ])
        assert "Step through" not in render_html(comparison)


class TestWhenTheQuestionItselfChanged:
    """A flow is edited between runs -- a prompt reworded by one word -- and
    from the answers alone that is indistinguishable from a release that
    started replying differently. Reporting it as a behaviour change is
    reporting the wrong thing with confidence."""

    def two(self, tmp_path, before_prompt, after_prompt, before="X", after="Y"):
        return compare([
            make_run(tmp_path, "before", R32, {"a": before},
                     prompts={"a": before_prompt}),
            make_run(tmp_path, "after", R33, {"a": after},
                     prompts={"a": after_prompt}),
        ]).rows[0]

    def test_a_changed_prompt_is_called_out_rather_than_the_answer(self, tmp_path):
        row = self.two(tmp_path, "How wide is the pad?", "How wide is the bracket?")
        assert row.verdict == "asked"
        assert row.interesting

    def test_it_outranks_a_failure_because_it_invalidates_everything(self, tmp_path):
        rows = compare([
            make_run(tmp_path, "before", R32, {"a": "X"}, prompts={"a": "one"}),
            make_run(tmp_path, "after", R33, {"a": "Y"},
                     statuses={"a": "error"}, prompts={"a": "two"}),
        ]).rows
        assert rows[0].verdict == "asked"

    def test_the_same_question_worded_with_different_spacing_is_the_same(self, tmp_path):
        row = self.two(tmp_path, "How wide is the pad?", "  How wide is the pad?  ",
                       before="Same", after="Same")
        assert row.verdict == "same"

    def test_the_headline_does_not_say_no_change(self, tmp_path):
        """It did, over a row reporting that the two runs were asked different
        questions -- and the headline is the one line a reader might act on
        without reading further."""
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "X"}, prompts={"a": "one"}),
            make_run(tmp_path, "after", R33, {"a": "X"}, prompts={"a": "two"}),
        ])
        assert "no change" not in comparison.headline()
        assert "asked differently" in comparison.headline()

    def test_the_report_says_which_run_asked_what(self, tmp_path):
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "40mm"},
                     prompts={"a": "How wide is the pad?"}),
            make_run(tmp_path, "after", R33, {"a": "55mm"},
                     prompts={"a": "How wide is the bracket?"}),
        ])
        text = render_markdown(comparison)

        assert "not the same prompt" in text
        assert "How wide is the pad?" in text and "How wide is the bracket?" in text
        page = render_html(comparison)
        assert 'class="asked"' in page

    def test_a_conversation_is_compared_on_what_it_actually_said(self, tmp_path):
        """A conversation flow names its turns opening/followup/closing and has
        no variable called `prompt` at all. Reading one compares two empty
        strings and calls them identical."""
        from understudy.compare import asked_in

        result = {
            "prompt": "", "reads": {"r1": "ok"},
            "step_statuses": [
                {"index": 1, "phase": "steps", "action": "type", "target": "box",
                 "status": "ok", "duration_ms": 1, "detail": {"text": "first turn"}},
                {"index": 2, "phase": "steps", "action": "read", "target": "reply",
                 "status": "ok", "duration_ms": 1, "detail": {"store_as": "r1"}},
            ],
        }
        assert asked_in(result) == "first turn"


def make_conversation(tmp_path, name, subject, turns, prompt_id="session"):
    """A run of one prompt run that is a conversation: ask, read, ask, read."""
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "flow.yaml").write_text("version: 1\nname: leo-regression\n")
    steps, reads, index = [], {}, 1
    for number, (asked, replied) in enumerate(turns, start=1):
        steps.append({"index": index, "phase": "steps", "action": "type",
                      "target": "leo_box", "status": "ok", "duration_ms": 10,
                      "detail": {"text": asked}})
        steps.append({"index": index + 1, "phase": "steps", "action": "read",
                      "target": "leo_reply", "status": "ok", "duration_ms": 5,
                      "detail": {"store_as": f"reply_{number}"}})
        steps.append({"index": index + 2, "phase": "steps", "action": "click",
                      "target": "tree", "status": "ok", "duration_ms": 5})
        reads[f"reply_{number}"] = replied
        index += 3
    (run / "results.jsonl").write_text(json.dumps({
        "prompt_id": prompt_id, "repeat_index": 0, "prompt": "", "variables": {},
        "response": turns[-1][1], "reads": reads, "read_images": {},
        "status": "ok", "duration_ms": 100, "screenshots": [],
        "step_statuses": steps, "backend": "web", "flow": "leo-regression",
        "subject": subject, "timestamp": "2026-09-01T10:00:00Z",
    }) + "\n")
    return run


class TestAConversationIsComparedTurnByTurn:
    """Comparing a three-turn session on its last reply is comparing almost
    nothing: the fillet that stopped working after the hole is turn two, and
    turn three still says the same thing."""

    def test_a_changed_middle_reply_is_found(self, tmp_path):
        comparison = compare([
            make_conversation(tmp_path, "before", R32, [
                ("Add a hole.", "Hole added."),
                ("Now fillet it.", "Fillet added."),
                ("Anything else?", "No."),
            ]),
            make_conversation(tmp_path, "after", R33, [
                ("Add a hole.", "Hole added."),
                ("Now fillet it.", "I cannot fillet that edge."),
                ("Anything else?", "No."),
            ]),
        ])

        verdicts = {row.prompt_id: row.verdict for row in comparison.rows}
        assert verdicts["session · turn 2"] == "changed"
        assert verdicts["session · turn 1"] == "same"
        assert verdicts["session · turn 3"] == "same"

    def test_it_says_which_turn_rather_than_which_prompt_run(self, tmp_path):
        comparison = compare([
            make_conversation(tmp_path, "before", R32,
                              [("a", "one"), ("b", "two")]),
            make_conversation(tmp_path, "after", R33,
                              [("a", "one"), ("b", "TWO DIFFERENT")]),
        ])
        assert [row.prompt_id for row in comparison.rows] == [
            "session · turn 1", "session · turn 2"]

    def test_a_run_with_fewer_turns_shows_the_missing_one(self, tmp_path):
        """A session that stopped early is not a session that answered the
        same way."""
        comparison = compare([
            make_conversation(tmp_path, "before", R32,
                              [("a", "one"), ("b", "two")]),
            make_conversation(tmp_path, "after", R33, [("a", "one")]),
        ])
        verdicts = {row.prompt_id: row.verdict for row in comparison.rows}
        assert verdicts["session · turn 2"] == "missing"

    def test_a_single_question_is_still_one_row(self, tmp_path):
        """The common case is unchanged: no turn numbers where there is one
        turn."""
        comparison = compare([
            make_run(tmp_path, "before", R32, {"a": "X"}),
            make_run(tmp_path, "after", R33, {"a": "X"}),
        ])
        assert [row.prompt_id for row in comparison.rows] == ["a"]
