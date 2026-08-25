"""What was under test.

The tool answers "did the behaviour change?", and an answer is only comparable
against another answer if you know what produced each one. A transcript that
records a reply from LEO but not *which* LEO is evidence of nothing.
"""

from __future__ import annotations

import pytest

from understudy.subject import Subject, load_remembered, remember


class TestRecordingIt:
    def test_an_empty_subject_knows_it_is_empty(self):
        assert not Subject().recorded
        assert Subject().summary() == ""

    def test_the_summary_reads_as_a_person_would_say_it(self):
        subject = Subject(app="CATIA V5", app_version="R32 SP4",
                          model="LEO", model_version="2026x FD01")
        assert subject.summary() == "CATIA V5 R32 SP4 · LEO 2026x FD01"

    def test_a_release_number_is_bracketed(self):
        subject = Subject(app="CATIA V5", release="build 4821")
        assert subject.summary() == "CATIA V5 · (build 4821)"

    def test_half_filled_in_is_still_useful(self):
        assert Subject(model="LEO").summary() == "LEO"

    def test_empty_fields_are_not_carried_into_the_results(self):
        assert Subject(app="CATIA V5").as_dict() == {"app": "CATIA V5"}

    def test_an_unknown_field_is_refused_rather_than_swallowed(self):
        """A typo that is silently stored is a field that silently stays
        empty in every transcript."""
        with pytest.raises(ValueError, match="unknown subject field"):
            Subject.from_config({"aplication": "CATIA"})

    def test_whitespace_is_not_a_value(self):
        assert not Subject.from_config({"app": "   "}).recorded


class TestTheRunWinsOverTheFlow:
    """The flow says what it was written against; the run says what was
    actually in front of it."""

    def test_the_run_overrides_where_it_says_anything(self):
        flow = Subject(app="CATIA V5", app_version="R32", model="LEO")
        run = Subject(app_version="R33 SP1")
        assert flow.merged_with(run).summary() == "CATIA V5 R33 SP1 · LEO"

    def test_what_the_run_leaves_blank_stays_as_the_flow_had_it(self):
        flow = Subject(app="CATIA V5", model="LEO")
        assert flow.merged_with(Subject()).summary() == "CATIA V5 · LEO"

    def test_neither_is_mutated(self):
        flow = Subject(app="CATIA V5")
        flow.merged_with(Subject(app="NX"))
        assert flow.app == "CATIA V5"


class TestCarryingItBetweenRuns:
    """You do not retype the service pack every morning."""

    def test_the_last_thing_recorded_comes_back(self, tmp_path):
        store = tmp_path / "subjects.json"
        remember("leo-regression", Subject(app="CATIA V5", app_version="R32 SP4"), store)
        assert load_remembered("leo-regression", store).app_version == "R32 SP4"

    def test_a_flow_never_run_falls_back_to_the_last_one_used(self, tmp_path):
        """Somebody testing a second flow on the same machine is testing the
        same installation, so offering it is right more often than not."""
        store = tmp_path / "subjects.json"
        remember("first-flow", Subject(app="CATIA V5", app_version="R32"), store)
        assert load_remembered("a-flow-never-seen", store).app == "CATIA V5"

    def test_each_flow_keeps_its_own(self, tmp_path):
        store = tmp_path / "subjects.json"
        remember("catia", Subject(app="CATIA V5"), store)
        remember("nx", Subject(app="NX"), store)
        assert load_remembered("catia", store).app == "CATIA V5"
        assert load_remembered("nx", store).app == "NX"

    def test_recording_nothing_does_not_overwrite_what_was_there(self, tmp_path):
        store = tmp_path / "subjects.json"
        remember("catia", Subject(app="CATIA V5"), store)
        remember("catia", Subject(), store)
        assert load_remembered("catia", store).app == "CATIA V5"

    def test_nothing_remembered_yet(self, tmp_path):
        assert not load_remembered("x", tmp_path / "absent.json").recorded

    def test_a_corrupt_store_is_ignored_rather_than_fatal(self, tmp_path):
        store = tmp_path / "subjects.json"
        store.write_text("{not json")
        assert not load_remembered("x", store).recorded

    def test_a_field_from_a_newer_version_does_not_break_the_pre_fill(self, tmp_path):
        store = tmp_path / "subjects.json"
        store.write_text('{"flows": {"x": {"app": "CATIA V5", "future": "?"}}}')
        assert load_remembered("x", store).app == "CATIA V5"
