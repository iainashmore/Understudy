"""Reading an embedded panel over CDP, to find out what a flow should name.

Run against a real Chromium with a real debugging port, because everything
interesting here -- iframes, aria-live regions, shadowless streaming panels --
is browser behaviour, and a mocked DOM would only confirm the mock.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import inspect_cdp  # noqa: E402

from understudy.drivers.web import find_chromium  # noqa: E402

pytest.importorskip("playwright", reason="needs playwright")

PORT = 9413
REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "fixtures" / "chat_app" / "index.html"


@pytest.fixture(scope="module")
def endpoint(tmp_path_factory):
    executable = find_chromium() or shutil.which("chromium")
    if not executable:
        pytest.skip("no chromium available")
    profile = tmp_path_factory.mktemp("inspect-profile")
    process = subprocess.Popen(
        [executable, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={profile}", "--no-first-run", "--no-sandbox",
         "--disable-dev-shm-usage", f"file://{FIXTURE}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{PORT}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if any(t.get("title") for t in inspect_cdp.targets(url)):
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        process.kill()
        pytest.skip("debugging endpoint never came up")
    yield url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


class TestListingTargets:
    def test_every_target_is_listed_whatever_its_type(self, endpoint):
        listed = inspect_cdp.targets(endpoint)
        assert listed, "an open page should be listed"
        assert all(isinstance(target, dict) for target in listed)

    def test_a_dead_endpoint_is_an_error_not_a_traceback(self):
        with pytest.raises(Exception):
            inspect_cdp.targets("http://127.0.0.1:59997")


class TestFindingWhatAFlowNames:
    @pytest.fixture(scope="class")
    def report(self, endpoint, tmp_path_factory):
        out = tmp_path_factory.mktemp("inspect-out")
        return inspect_cdp.inspect(endpoint, out, shots=False)

    def _frame(self, report):
        return next(f for f in report["frames"] if f["entry"])

    def test_the_box_that_takes_typing_is_found(self, report):
        entries = self._frame(report)["entry"]
        assert any(e["tag"] == "textarea" for e in entries)

    def test_the_button_that_submits_is_found(self, report):
        labels = [e["text"] for e in self._frame(report)["submit"]]
        assert "Send" in labels

    def test_the_place_the_answer_appears_is_found_while_still_empty(self, report):
        """The response element holds nothing until a reply streams in, so it
        cannot be found by its text. It is found by its role."""
        outputs = self._frame(report)["output"]
        selectors = [inspect_cdp.selector_for(e) for e in outputs]
        assert any("response" in selector for selector in selectors), selectors

    def test_hidden_elements_are_left_out(self, report):
        """The fixture keeps a Stop button hidden until a reply is streaming.
        Offering it as a target would produce a flow that waits forever."""
        assert "Stop" not in [e["text"] for e in self._frame(report)["submit"]]


class TestChoosingASelector:
    def test_a_test_id_wins_over_everything_else(self):
        assert inspect_cdp.selector_for({
            "tag": "textarea",
            "attributes": {"data-testid": "prompt-input", "id": "x",
                           "class": "css-1x2y3z", "aria-label": "Message"},
        }) == "[data-testid='prompt-input']"

    def test_an_id_is_next(self):
        assert inspect_cdp.selector_for(
            {"tag": "div", "attributes": {"id": "response", "class": "a b"}}
        ) == "#response"

    def test_a_label_beats_a_generated_class_name(self):
        assert inspect_cdp.selector_for({
            "tag": "button",
            "attributes": {"aria-label": "Send", "class": "css-1x2y3z"},
        }) == "[aria-label='Send']"

    def test_a_class_is_the_last_resort_and_says_so_by_being_ugly(self):
        assert inspect_cdp.selector_for(
            {"tag": "div", "attributes": {"class": "css-1x2y3z other"}}
        ) == "div.css-1x2y3z"

    def test_an_element_with_nothing_to_go_on_falls_back_to_its_tag(self):
        assert inspect_cdp.selector_for({"tag": "textarea", "attributes": {}}) == "textarea"


class TestTheSuggestedFlow:
    def test_it_hands_back_a_targets_block_to_paste(self, endpoint, tmp_path):
        report = inspect_cdp.inspect(endpoint, tmp_path, shots=False)
        block = "\n".join(inspect_cdp.suggest(report))
        assert block.startswith("targets:")
        assert "prompt_box:" in block and "[data-testid='prompt-input']" in block
        assert "send:" in block and "answer:" in block

    def test_finding_nothing_says_the_likely_reason(self):
        lines = inspect_cdp.suggest({"frames": []})
        assert any("panel was not open" in line for line in lines)
