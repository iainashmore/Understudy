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
from pathlib import Path

import pytest

from understudy.compare import compare, normalise
from understudy.compare_report import render_html, render_markdown, write_comparison


def make_run(tmp_path, name, subject, answers, statuses=None):
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "flow.yaml").write_text("version: 1\nname: leo-regression\n")
    rows = []
    for index, (prompt_id, response) in enumerate(answers.items()):
        rows.append({
            "prompt_id": prompt_id, "repeat_index": 0,
            "prompt": f"prompt for {prompt_id}", "variables": {},
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
