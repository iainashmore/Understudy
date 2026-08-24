"""End-to-end: flow -> web driver -> runner -> results, against the fixture app.

Everything here runs offline against fixtures/chat_app, so the streaming,
stalling and drift cases are reproducible rather than dependent on a live
service.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from flowrunner.drivers import build
from flowrunner.drivers.base import TargetNotFound
from flowrunner.flow import load_flow
from flowrunner.prompts import prompts_from_entries
from flowrunner.runner import Runner, Status, write_csv

REPO = Path(__file__).resolve().parents[2]
FIXTURE = (REPO / "fixtures" / "chat_app" / "index.html").resolve()
FLOW_TEMPLATE = (REPO / "examples" / "fixture_chat.yaml").read_text()
PROMPTS = prompts_from_entries([{'id': 'baseline', 'prompt': 'hello there'}])

pytest.importorskip("playwright", reason="web driver needs playwright")


def make_flow(tmp_path: Path, query: str, **replacements: str):
    text = re.sub(
        r'url: "file://[^"]*"', f'url: "file://{FIXTURE}{query}"', FLOW_TEMPLATE
    )
    for old, new in replacements.items():
        text = text.replace(old, new)
    path = tmp_path / "flow.yaml"
    path.write_text(text)
    return load_flow(path)


def execute(tmp_path: Path, query: str, prompts=PROMPTS, repeats: int = 1, **replacements):
    flow = make_flow(tmp_path, query, **replacements)
    driver = build("web")
    driver.start(flow.app_config("web"))
    try:
        runner = Runner(flow, driver, tmp_path / "out")
        return runner.run(prompts, repeats=repeats), runner
    finally:
        driver.stop()


class TestHappyPath:
    @pytest.fixture(scope="class")
    @classmethod
    def outcome(cls, tmp_path_factory):
        tmp_path = tmp_path_factory.mktemp("happy")
        results, runner = execute(tmp_path, "?mode=stream&delay=15&dialog=portal&banner=1")
        return results[0], runner, tmp_path

    def test_the_variant_succeeds(self, outcome):
        result, _, _ = outcome
        assert result.status is Status.OK
        assert result.error is None

    def test_the_response_reflects_the_prompt(self, outcome):
        result, _, _ = outcome
        assert result.response == "Echo: hello there"

    def test_every_step_ran(self, outcome):
        result, _, _ = outcome
        assert [s.status for s in result.step_statuses] == [Status.OK] * 9

    def test_a_portalled_dialog_is_still_found(self, outcome):
        """The dialog is appended to <body>, not to the container it is
        declared next to. Anything path-anchored would miss it."""
        result, _, _ = outcome
        confirm = [s for s in result.step_statuses if s.target == "confirm_dialog"]
        assert confirm and confirm[0].status is Status.OK

    def test_the_cookie_banner_is_dismissed_without_being_a_step(self, outcome):
        result, _, _ = outcome
        assert all(s.target != "cookie_banner" for s in result.step_statuses)

    def test_screenshots_land_where_the_spec_says(self, outcome):
        result, _, tmp_path = outcome
        assert result.screenshots == [
            "baseline/01-before-prompt.png",
            "baseline/02-after-typing.png",
            "baseline/03-after-response.png",
        ]
        for relative in result.screenshots:
            assert (tmp_path / "out" / relative).stat().st_size > 0

    def test_the_completion_signal_was_used(self, outcome):
        result, _, _ = outcome
        wait = next(s for s in result.step_statuses if s.action == "wait_for_stable")
        assert wait.detail["signal"] == "signal+stable"

    def test_the_flow_as_executed_is_copied_in(self, outcome):
        """One file holds the steps and the variants, so copying it in captures
        everything that ran."""
        _, _, tmp_path = outcome
        copied = (tmp_path / "out" / "flow.yaml").read_text()
        assert "prompts:" in copied and "steps:" in copied

    def test_results_stream_to_jsonl(self, outcome):
        _, runner, _ = outcome
        rows = [json.loads(line) for line in runner.results_path.read_text().splitlines()]
        assert rows[0]["prompt_id"] == "baseline"
        assert rows[0]["response"] == "Echo: hello there"
        assert rows[0]["backend"] == "web"


def test_a_stalled_response_times_out_without_crashing(tmp_path):
    """A timeout is a step status. The run still produces a row and its
    screenshots."""
    results, _ = execute(
        tmp_path, "?mode=stall&delay=80&dialog=none", **{"timeout_ms: 30000": "timeout_ms: 2500"}
    )
    result = results[0]

    assert result.status is Status.TIMEOUT
    wait = next(s for s in result.step_statuses if s.action == "wait_for_stable")
    assert wait.status is Status.TIMEOUT
    assert "still changing" in wait.error
    assert len(result.screenshots) == 3, "screenshots survive a timeout"
    assert result.reads["response"], "whatever had arrived is still recorded"


def test_resolution_falls_back_when_the_preferred_selector_disappears(tmp_path):
    """data-testid stripped: every first-choice strategy is gone. The run should
    still work, on role and accessible name, and say that it did."""
    results, _ = execute(tmp_path, "?mode=instant&dialog=none&testids=0")
    result = results[0]

    assert result.status is Status.OK
    assert result.response == "Echo: hello there"
    assert "prompt_box#1" in result.used_fallbacks
    assert "response_area#1" in result.used_fallbacks


def test_an_absent_optional_step_is_skipped_not_failed(tmp_path):
    """dialog=none: the confirm dialog never appears."""
    results, _ = execute(tmp_path, "?mode=instant&dialog=none")
    confirm = next(
        s for s in results[0].step_statuses if s.target == "confirm_dialog"
    )
    assert confirm.status is Status.SKIPPED
    assert results[0].status is Status.OK


def test_a_missing_required_target_fails_the_variant_and_captures_the_moment(tmp_path):
    # Break every strategy for the send button.
    flow = make_flow(
        tmp_path, "?mode=instant&dialog=none",
        **{"- testid: send\n      - role: button\n        name: Send":
           "- testid: definitely-not-here"},
    )
    driver = build("web")
    driver.start(flow.app_config("web"))
    try:
        runner = Runner(flow, driver, tmp_path / "broken")
        runner.prepare(PROMPTS)
        result = runner.run_variant(PROMPTS.variants[0])
    finally:
        driver.stop()

    assert result.status is Status.ERROR
    failed = next(s for s in result.step_statuses if s.status is Status.ERROR)
    assert "could not resolve target" in failed.error
    assert any("FAILED" in shot for shot in result.screenshots)


def test_repeats_get_their_own_directories(tmp_path):
    results, _ = execute(tmp_path, "?mode=instant&dialog=none", repeats=2)
    assert [r.repeat_index for r in results] == [0, 1]
    assert results[0].screenshots[0].startswith("baseline-01/")
    assert results[1].screenshots[0].startswith("baseline-02/")


def test_multiple_variants_all_run_and_export(tmp_path):
    prompts = prompts_from_entries([{'id': 'a', 'prompt': 'first'}, {'id': 'b', 'prompt': 'second'}])
    results, _ = execute(tmp_path, "?mode=instant&dialog=none", prompts=prompts)

    assert [r.response for r in results] == ["Echo: first", "Echo: second"]
    csv_path = write_csv(results, tmp_path / "results.csv")
    body = csv_path.read_text()
    assert "Echo: first" in body and "Echo: second" in body
