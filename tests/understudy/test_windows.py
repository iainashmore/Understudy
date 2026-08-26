"""Choosing which window a flow means.

The reason this module exists: 3DEXPERIENCE runs as a crowd of processes,
several of which own a top-level window and answer to the same title. Title
alone cannot pick the client out of that, and picking wrong means replaying
into someone's open document.

Enumeration itself is pywinauto, so it cannot run here; the choosing is pure
and is what these cover.
"""

from __future__ import annotations

from understudy.windows import (
    OpenWindow, best_first, choose, glob_to_regex, owned_by, process_names,
)

CLIENT = OpenWindow(title="3DEXPERIENCE R2026x", pid=7788, process="CATIA.exe",
                    width=1920, height=1040, visible=True)
SPLASH = OpenWindow(title="3DEXPERIENCE", pid=7790, process="CATSplash.exe",
                    width=400, height=300, visible=False)
HELPER = OpenWindow(title="3DEXPERIENCE", pid=7791, process="DSLicHelper.exe",
                    width=0, height=0, visible=False)
SECOND = OpenWindow(title="3DEXPERIENCE R2026x", pid=8000, process="CATIA.exe",
                    width=1600, height=900, visible=True)


class TestRanking:
    def test_the_client_outranks_its_splash_screen(self):
        assert best_first([HELPER, SPLASH, CLIENT]) == [CLIENT, SPLASH, HELPER]

    def test_a_big_hidden_window_still_loses_to_a_small_visible_one(self):
        huge = OpenWindow(title="x", width=4000, height=2000, visible=False)
        small = OpenWindow(title="y", width=100, height=100, visible=True)
        assert best_first([huge, small]) == [small, huge]


class TestChoosing:
    def test_the_only_visible_window_is_the_answer(self):
        chosen, over = choose([CLIENT, SPLASH, HELPER])
        assert chosen == CLIENT
        assert len(over) == 2, "and it should say what it was chosen over"

    def test_two_live_windows_are_a_refusal_not_a_guess(self):
        chosen, candidates = choose([CLIENT, SECOND])
        assert chosen is None, "replaying into the wrong open document is destructive"
        assert candidates == [CLIENT, SECOND]

    def test_nothing_visible_at_all_is_also_a_refusal(self):
        chosen, candidates = choose([SPLASH, HELPER])
        assert chosen is None
        assert len(candidates) == 2

    def test_nothing_matched_is_empty_rather_than_an_error(self):
        assert choose([]) == (None, [])


class TestNarrowingByProcess:
    def test_the_executable_separates_windows_of_the_same_name(self):
        assert owned_by([CLIENT, SPLASH, HELPER], "CATIA.exe") == [CLIENT]

    def test_the_extension_is_optional_and_case_does_not_matter(self):
        assert owned_by([CLIENT, SPLASH], "catia") == [CLIENT]

    def test_no_process_given_leaves_everything(self):
        assert owned_by([CLIENT, SPLASH], None) == [CLIENT, SPLASH]

    def test_narrowing_turns_an_ambiguity_into_an_answer(self):
        chosen, _ = choose(owned_by([CLIENT, SPLASH, HELPER], "CATSplash.exe"))
        assert chosen is None, "the splash screen is not visible, so still no"
        chosen, _ = choose(owned_by([CLIENT, SPLASH, HELPER], "CATIA"))
        assert chosen == CLIENT


class TestDescribing:
    def test_a_window_is_described_by_owner_and_size(self):
        assert CLIENT.described() == (
            "'3DEXPERIENCE R2026x'  CATIA.exe pid 7788  1920x1040"
        )

    def test_an_invisible_window_says_so(self):
        assert "(not visible)" in HELPER.described()

    def test_an_unnamed_process_falls_back_to_the_pid(self):
        assert OpenWindow(title="x", pid=42).owner == "pid 42"

    def test_the_live_wrapper_does_not_leak_into_the_description(self):
        with_wrapper = OpenWindow(title="x", pid=1, wrapper=object())
        assert "wrapper" not in with_wrapper.as_dict()
        assert with_wrapper == OpenWindow(title="x", pid=1), \
            "two views of the same window are the same window"


class TestProcessNames:
    def test_tasklist_output_becomes_a_pid_map(self, monkeypatch):
        class Completed:
            stdout = ('"System Idle Process","0","Services","0","8 K"\n'
                      '"CATIA.exe","7788","Console","1","1,204,880 K"\n')

        monkeypatch.setattr("understudy.windows.subprocess.run",
                            lambda *a, **k: Completed())
        assert process_names()[7788] == "CATIA.exe"

    def test_a_machine_without_tasklist_is_not_fatal(self, monkeypatch):
        def explode(*args, **kwargs):
            raise FileNotFoundError("tasklist")

        monkeypatch.setattr("understudy.windows.subprocess.run", explode)
        assert process_names() == {}


def test_title_globs_become_anchored_regexes():
    assert glob_to_regex("*3DEXPERIENCE*") == "^.*3DEXPERIENCE.*$"
    assert glob_to_regex("Example App") == "^Example\\ App$"
