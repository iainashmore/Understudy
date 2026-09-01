"""The page, in a browser.

Everything here runs the real page against the real server, because the class
of bug this exists to catch is a script that throws on load: the API answers,
the server is fine, and the app is a blank rectangle. That happened twice in
one afternoon and both times a screenshot found it rather than a test.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from understudy.chromium import find_chromium
from understudy.ui.server import Api, Handler, Workspace

from tests.understudy.test_runner_end_to_end import FakeApp

FLOW = """version: 1
name: page-test
title: Page test
target_app:
  native:
    window_title_pattern: "*Fake*"
    process: "fake.exe"
targets:
  box:
    native:
      - control_type: Edit
prompts:
  - id: baseline
    prompt: hello
steps:
  - action: type
    target: box
    text: "{{prompt}}"
  - action: read
    target: box
    store_as: response
"""

# Two problems, so "all of them" is a claim the test can actually check.
BROKEN = "version: 1\nname: broken\nsteps: []\n"


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "flow.yaml").write_text(FLOW)
    return tmp_path


@pytest.fixture
def served(workspace):
    Handler.api = Api(Workspace(workspace),
                      driver_factory=lambda backend, **options: FakeApp())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launched = pw.chromium.launch(executable_path=find_chromium())
        yield launched
        launched.close()


@pytest.fixture
def page(served, browser):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(served, wait_until="networkidle")
    page.wait_for_timeout(300)
    page.errors = errors
    yield page
    page.close()


class TestItLoads:
    def test_the_page_runs_without_throwing(self, page):
        assert page.errors == []
        assert page.locator("h1").inner_text() == "Understudy"

    def test_the_flow_is_open_without_anybody_choosing_it(self, page):
        """One flow, one session. There is no tree to pick from any more."""
        assert page.locator("#flowName").inner_text() == "Page test"
        assert page.locator("#flowCard").is_visible()

    def test_replay_is_offered_for_a_flow_that_loads(self, page):
        assert page.locator("#replay").is_enabled()

    def test_the_steps_are_there_but_folded_away(self, page):
        """The YAML is the detail, not the point. It is one click away for the
        times a recording needs correcting."""
        assert page.locator("#flowText").is_hidden()
        page.click("#flowDetails summary")
        assert page.locator("#flowText").is_visible()
        assert "window_title_pattern" in page.locator("#flowText").input_value()


class TestTheApplicationItDrives:
    def test_the_window_is_filled_in_from_the_flow(self, page):
        """The recorded flow already says which window. Asking again is asking
        a question the file answered."""
        assert page.locator("#window").input_value() == "*Fake*"
        assert page.locator("#process").input_value() == "fake.exe"

    def test_recording_says_why_it_cannot_run_here(self, page):
        """These tests run on Linux. A live-looking button that does nothing is
        worse than a disabled one that says why."""
        assert page.locator("#record").is_disabled()
        assert "Windows" in page.locator("#recordState").inner_text()

    def test_the_recording_instructions_are_not_on_screen_until_recording(self, page):
        assert page.locator("#recording").is_hidden()


class TestReplaying:
    def test_a_replay_shows_what_was_asked_and_what_came_back(self, page):
        page.click("#replay")
        page.wait_for_selector(".turn", timeout=30000)
        page.wait_for_timeout(500)

        turn = page.locator(".turn").first
        assert "hello" in turn.inner_text()
        assert "olleh" in turn.inner_text(), "the fake answers in reverse"
        assert page.errors == []

    def test_the_run_reports_how_many_passed(self, page):
        page.click("#replay")
        page.wait_for_selector("#runState .pill.ok", timeout=30000)
        assert page.locator("#runState").inner_text().strip() == "1/1"

    def test_a_transcript_is_offered_when_it_finishes(self, page):
        page.click("#replay")
        page.wait_for_selector("#runState .pill.ok", timeout=30000)
        page.wait_for_timeout(300)
        assert page.locator("#transcriptLink").is_visible()

    def test_replaying_twice_does_not_stack_the_first_run_under_the_second(self, page):
        for _ in range(2):
            page.click("#replay")
            page.wait_for_selector("#runState .pill.ok", timeout=30000)
            page.wait_for_timeout(300)
        assert page.locator(".turn").count() == 1


class TestAFlowThatWillNotLoad:
    @pytest.fixture
    def broken(self, workspace, served, browser):
        (workspace / "flow.yaml").write_text(BROKEN)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(served, wait_until="networkidle")
        page.wait_for_timeout(300)
        page.errors = errors
        yield page
        page.close()

    def test_the_problems_are_shown_without_pressing_anything(self, broken):
        assert "will not load" in broken.locator("#flowProblems").inner_text()
        assert "'prompts' is a required property" in broken.locator("#turns").inner_text()
        assert broken.errors == []

    def test_every_problem_is_listed_not_only_the_first(self, broken):
        shown = broken.locator("#turns").inner_text()
        assert "steps" in shown, shown
        assert "more problem(s)" not in shown, "counted, rather than named"

    def test_replay_is_refused(self, broken):
        assert broken.locator("#replay").is_disabled()

    def test_fixing_it_and_saving_clears_the_verdict(self, broken):
        # The steps are already unfolded: a flow that will not load is one
        # somebody is about to edit.
        assert broken.locator("#flowText").is_visible()
        broken.fill("#flowText", FLOW)
        broken.click("#save")
        broken.wait_for_timeout(600)
        assert broken.locator("#flowProblems").inner_text().strip() == ""
        assert broken.locator("#replay").is_enabled()


def test_a_workspace_with_no_flow_says_to_record_one(tmp_path, browser):
    Handler.api = Api(Workspace(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
        page.wait_for_timeout(300)
        assert "record one" in page.locator("#flowName").inner_text()
        assert page.locator("#replay").is_disabled()
        assert errors == []
        page.close()
    finally:
        server.shutdown()
