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
import re

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


class ChatApp(FakeApp):
    """An application whose panel grows. Each answer is added to a thread that
    already holds everything said before it -- which is what a fixed rectangle
    over a conversation reads.

    The reply lands *between* two looks at the screen rather than the instant
    Enter is pressed, because that is what a real assistant does: the runner
    takes its baseline the moment the question goes in, and the answer arrives
    afterwards.
    """

    def __init__(self):
        super().__init__()
        self.history = 0
        self.screen = np.zeros((600, 900, 3), dtype=np.uint8)
        self.screen[::4, :] = 90            # furniture: never changes
        self.screen[0:40, :] = 200          # a title bar
        #: What will have happened by the next look at the screen.
        self.later = []

    def key(self, keys, target, timeout_ms):
        self.keys.append(keys)
        self.later.append(self.reply)
        return None

    def reply(self):
        """A block of text below everything already in the thread."""
        block = answer(self.history)
        self.screen[block["y"]:block["y"] + block["height"],
                    block["x"]:block["x"] + block["width"]] = 240
        self.history += 1

    def screenshot(self, target=None, full_page=False, region=None):
        from understudy.vision import crop

        image = crop(self.screen, region) if region else self.screen
        png = to_png_bytes(image)
        while self.later:
            self.later.pop(0)()
        return png


CHANGED_FLOW = {
    **FLOW,
    "steps": [
        {"action": "click", "target": "prompt_box"},
        {"action": "type", "target": "prompt_box", "text": "{{prompt}}"},
        {"action": "key", "keys": "{ENTER}"},
        {"action": "read", "mode": "changed", "store_as": "response"},
    ],
}


def answer(index: int) -> dict[str, int]:
    """Where the index-th reply lands in the thread."""
    return {"x": 100, "y": 100 + index * 60, "width": 400, "height": 50}


def changed_regions(results) -> list[str]:
    """What each prompt run decided the answer occupied."""
    return [
        next(s for s in result.step_statuses if s.action == "read")
        .detail["changed"]
        for result in results
    ]


def rectangle(described: str) -> dict[str, int]:
    numbers = [int(n) for n in re.findall(r"-?\d+", described)]
    keys = ("width", "height", "x", "y")
    return dict(zip(keys, numbers))


#: A read region is grown by a small margin, because text read hard against
#: the edge of a crop reads worse than text with a little room around it.
MARGIN = 16


def snugly_covers(found: dict[str, int], wanted: dict[str, int]) -> bool:
    """The whole of the block, and not much else."""
    return (found["x"] <= wanted["x"]
            and found["y"] <= wanted["y"]
            and found["x"] + found["width"] >= wanted["x"] + wanted["width"]
            and found["y"] + found["height"] >= wanted["y"] + wanted["height"]
            and found["width"] <= wanted["width"] + MARGIN
            and found["height"] <= wanted["height"] + MARGIN)


class TestReadingWhatAppeared:
    """A fixed rectangle over a conversation panel reads the title, the date
    separators, the clock and the input box along with the answer -- and once
    the thread has a history, it reads that too, so prompt five's answer
    arrives with prompts one to four attached."""

    def test_it_reads_only_what_appeared(self, tmp_path):
        results = Runner(parse_flow(CHANGED_FLOW), ChatApp(), tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"}]))

        # The reply block, not the title bar and not the whole panel.
        found = rectangle(changed_regions(results)[0])
        assert snugly_covers(found, answer(0)), found

    def test_the_history_is_not_read_again_for_the_next_prompt(self, tmp_path):
        """The point of it. Two prompts into one thread, and the second answer
        is the second answer -- not both of them."""
        results = Runner(parse_flow(CHANGED_FLOW), ChatApp(), tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"},
                                  {"id": "two", "prompt": "again"}]))

        second = rectangle(changed_regions(results)[1])
        first_reply = answer(0)
        assert second["y"] >= first_reply["y"] + first_reply["height"], second
        assert snugly_covers(second, answer(1)), second

    def test_each_answer_is_measured_on_its_own(self, tmp_path):
        results = Runner(parse_flow(CHANGED_FLOW), ChatApp(), tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"},
                                  {"id": "two", "prompt": "again"}]))

        changed = changed_regions(results)
        for index, described in enumerate(changed):
            assert snugly_covers(rectangle(described), answer(index)), described
        assert changed[0] != changed[1], "and in a different place each time"

    def test_a_bound_keeps_an_unrelated_change_out(self, tmp_path):
        """A clock ticking in a corner would otherwise stretch the answer
        across the whole window."""
        driver = ChatApp()
        document = {**CHANGED_FLOW, "steps": [
            *CHANGED_FLOW["steps"][:-1],
            {"action": "read", "mode": "changed", "store_as": "response",
             "region": {"x": 90, "y": 90, "width": 500, "height": 400}},
        ]}

        def tick():
            driver.screen[580:590, 860:890] = 255      # a clock, far away

        pressed = driver.key

        def key_and_tick(keys, target, timeout_ms):
            outcome = pressed(keys, target, timeout_ms)
            driver.later.append(tick)
            return outcome

        driver.key = key_and_tick
        results = Runner(parse_flow(document), driver, tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"}]))
        found = rectangle(changed_regions(results)[0])
        assert snugly_covers(found, answer(0)), found

    def test_the_answer_is_placed_where_it_was_found(self, tmp_path):
        """Bounded reads report the region in screen coordinates, not in
        coordinates relative to the bound -- otherwise the picture kept beside
        the answer is of somewhere else entirely."""
        document = {**CHANGED_FLOW, "steps": [
            *CHANGED_FLOW["steps"][:-1],
            {"action": "read", "mode": "changed", "store_as": "response",
             "region": {"x": 90, "y": 90, "width": 500, "height": 400}},
        ]}
        results = Runner(parse_flow(document), ChatApp(), tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"}]))

        found = rectangle(changed_regions(results)[0])
        assert snugly_covers(found, answer(0)), found

    def test_nothing_changing_is_said_rather_than_guessed(self, tmp_path):
        """Reading the bound anyway would report the panel's furniture as the
        answer, which is worse than reporting nothing."""
        driver = ChatApp()
        driver.key = lambda keys, target, timeout_ms: None   # no reply ever
        results = Runner(parse_flow(CHANGED_FLOW), driver, tmp_path).run(
            prompts_from_entries([{"id": "one", "prompt": "hello"}]))

        assert changed_regions(results)[0] == "nothing on screen changed"
        step = next(s for s in results[0].step_statuses if s.action == "read")
        assert step.status is Status.ERROR
        assert results[0].error == "read: nothing appeared to read"
        assert not results[0].response, "and no answer is claimed"
