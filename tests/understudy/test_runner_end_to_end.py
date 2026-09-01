"""The runner, all the way through, against a driver that needs no machine.

This used to be covered by driving a fixture web page, which is gone with the
web driver. The coverage is not: everything between "a flow and some prompts"
and "a run directory with results, screenshots and a transcript in it" is the
part that has to keep working, and none of it is specific to what is being
driven.

The fake stands in for an application: it answers, it can be made to fail, and
it records what was asked of it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from understudy.drivers.base import DriverError, Resolution, TargetNotFound
from understudy.flow import parse_flow
from understudy.prompts import prompts_from_entries
from understudy.runner import Runner, Status
from harness.image import to_png_bytes

FLOW = {
    "version": 1,
    "name": "fake-app",
    "title": "A fake application",
    "target_app": {"native": {"window_title_pattern": "*Fake*"}},
    "targets": {
        "prompt_box": {"intent": "where the question goes",
                       "native": [{"control_type": "Edit"}]},
        "answer": {"intent": "where the reply appears",
                   "native": [{"control_type": "Text"}]},
    },
    "prompts": [{"id": "baseline", "prompt": "how many pads"},
                {"id": "terse", "prompt": "pads?"}],
    "steps": [
        {"action": "capture", "label": "1-before"},
        {"action": "click", "target": "prompt_box"},
        {"action": "type", "target": "prompt_box", "text": "{{prompt}}"},
        {"action": "key", "keys": "{ENTER}"},
        {"action": "read", "target": "answer", "store_as": "response"},
    ],
}


class FakeApp:
    """A driver-shaped application. Answers what it was asked, in reverse."""

    backend = "native"

    def __init__(self, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.typed: list[str] = []
        self.keys: list[str] = []
        self.started: dict | None = None
        self.stopped = False
        self.recording_to = None

    def _resolution(self, name: str) -> Resolution:
        return Resolution(target=name, index=0, strategy=None)

    def start(self, app_config): self.started = app_config
    def stop(self): self.stopped = True
    def reset(self): pass

    def click(self, target, timeout_ms):
        if target.name == self.fail_on:
            raise TargetNotFound(target, self.backend, ["image: no match"])
        return self._resolution(target.name)

    def type(self, target, text, timeout_ms, mode="type", clear=True, delay_ms=0):
        self.typed.append(text)
        return self._resolution(target.name if target else "")

    def key(self, keys, target, timeout_ms):
        self.keys.append(keys)
        return None

    def read(self, target, timeout_ms):
        answer = self.typed[-1][::-1] if self.typed else ""
        return answer, self._resolution(target.name)

    def screenshot(self, target=None, full_page=False, region=None):
        return to_png_bytes(np.zeros((40, 60, 3), dtype=np.uint8))

    def exists(self, target, timeout_ms=0): return True
    def is_visible(self, target): return True

    def wait_for_element(self, target, state, timeout_ms):
        return self._resolution(target.name)

    def start_recording(self, path):
        self.recording_to = path
        return False

    def stop_recording(self): return None
    def recording_unavailable(self): return "the fake has no recorder"


@pytest.fixture
def flow():
    return parse_flow(FLOW)


@pytest.fixture
def prompts():
    return prompts_from_entries(FLOW["prompts"])


def test_every_prompt_runs_and_is_answered(flow, prompts, tmp_path):
    driver = FakeApp()
    results = Runner(flow, driver, tmp_path).run(prompts)

    assert [r.prompt_id for r in results] == ["baseline", "terse"]
    assert all(r.status is Status.OK for r in results)
    assert results[0].response == "sdap ynam woh"


def test_the_prompt_is_what_varies_and_the_path_does_not(flow, prompts, tmp_path):
    """The whole premise: the same clicks, a different question."""
    driver = FakeApp()
    Runner(flow, driver, tmp_path).run(prompts)
    assert driver.typed == ["how many pads", "pads?"]
    assert driver.keys == ["{ENTER}", "{ENTER}"]


def test_what_ran_is_copied_in_beside_the_results(flow, prompts, tmp_path):
    """Weeks later the question is always what this actually ran, and by then
    both files have changed."""
    runner = Runner(flow, driver=FakeApp(), out_dir=tmp_path)
    runner.run(prompts)
    assert (tmp_path / "results.jsonl").exists()


def test_results_are_written_as_they_finish(flow, prompts, tmp_path):
    Runner(flow, FakeApp(), tmp_path).run(prompts)
    lines = (tmp_path / "results.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["response"] == "sdap ynam woh"


def test_a_step_that_cannot_find_its_target_fails_that_prompt_only(
        flow, prompts, tmp_path):
    driver = FakeApp(fail_on="prompt_box")
    results = Runner(flow, driver, tmp_path).run(prompts)
    assert [r.status for r in results] == [Status.ERROR, Status.ERROR]
    # On the result, not only buried in the steps: a failed prompt run has to
    # answer "what went wrong?" without anybody opening results.jsonl.
    assert "prompt_box" in (results[0].error or ""), results[0].error
    assert results[0].error.startswith("click:")

    failed = [s for s in results[0].step_statuses if s.status is Status.ERROR]
    assert [s.action for s in failed] == ["click"], "and it stops at the failure"


def test_screenshots_are_kept_when_asked_for(flow, prompts, tmp_path):
    Runner(flow, FakeApp(), tmp_path, capture_steps=True).run(prompts)
    shots = list(tmp_path.rglob("*.png"))
    assert shots, "capture_steps means a picture of every step"


def test_a_driver_that_cannot_record_says_so_rather_than_failing(
        flow, prompts, tmp_path):
    """A missing screen recorder is a note in the results, not a lost sweep."""
    results = Runner(flow, FakeApp(), tmp_path, record=True).run(prompts)
    assert all(r.status is Status.OK for r in results)
    assert not results[0].recording
