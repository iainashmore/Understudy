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


class TestPickingTheWindow:
    """Every row says which process owns the window and how big it is. On a CAD
    workstation several windows answer to the same name -- the client, its
    splash screen, a message-only helper -- and the title cannot separate
    them."""

    def test_the_list_says_it_cannot_look_here(self, page):
        """These tests run on Linux, where there are no windows to enumerate.
        An empty dropdown would read as "no windows are open"."""
        assert page.locator("#pick").is_disabled()
        assert "only on Windows" in page.locator("#pick").inner_text()

    def test_choosing_a_window_fills_in_what_the_flow_matches_on(self, page):
        page.evaluate("""() => {
          openWindows = [{title: "3DEXPERIENCE", process: "3DEXPERIENCE.exe",
                          pid: 4136, width: 1936, height: 1096, visible: true}];
          const pick = document.getElementById("pick");
          pick.disabled = false;
          pick.innerHTML = "";
          pick.append(new Option("choose a window…", ""));
          pick.append(new Option("3DEXPERIENCE — 3DEXPERIENCE.exe  1936x1096", "0"));
        }""")
        page.select_option("#pick", "0")
        assert page.locator("#window").input_value() == "3DEXPERIENCE"
        assert page.locator("#process").input_value() == "3DEXPERIENCE.exe"

    def test_each_row_carries_the_process_and_the_size(self, page):
        page.evaluate("""async () => {
          window.api = async () => ({supported: true, windows: [
            {title: "3DEXPERIENCE", process: "3DEXPERIENCE.exe", pid: 1,
             width: 1936, height: 1096, visible: true},
            {title: "3DEXPERIENCE", process: "CATSplash.exe", pid: 2,
             width: 400, height: 300, visible: false},
          ]});
          await loadWindows();
        }""")
        page.wait_for_timeout(200)
        shown = page.locator("#pick").inner_text()
        assert "3DEXPERIENCE.exe" in shown and "CATSplash.exe" in shown
        assert "1936x1096" in shown
        assert "hidden" in shown, "a window with no pixels says so"


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

    def test_stop_is_not_offered_when_nothing_is_recording(self, page):
        assert page.locator("#stop").is_hidden()
        assert page.locator("#record").is_visible()

    def test_stop_replaces_record_while_it_runs(self, page):
        """Not a second button beside it: there is one thing to do at a time,
        and a live Record button during a recording is an invitation to start
        a second one."""
        page.evaluate("""() => showRecording(
            {available: true, running: true})""")
        assert page.locator("#stop").is_visible()
        assert page.locator("#record").is_hidden()
        assert page.locator("#recording").is_visible()

    def test_replaying_is_not_offered_mid_recording(self, page):
        page.evaluate("""() => showRecording({available: true, running: true})""")
        assert page.locator("#replay").is_disabled()


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


class TestWatchingARecording:
    """Watching a counter move is the difference between "it is working" and
    "it started and did nothing" -- which otherwise look the same until you
    stop and find an empty flow."""

    def test_it_says_when_nothing_has_been_captured_yet(self, page):
        page.evaluate("""() => showRecording({available: true, running: true,
            counts: {events: 0, clicks: 0, keys: 0}})""")
        assert "nothing captured yet" in page.locator("#recordState").inner_text()

    def test_it_counts_what_it_has_caught(self, page):
        page.evaluate("""() => showRecording({available: true, running: true,
            counts: {events: 40, clicks: 2, keys: 17}})""")
        shown = page.locator("#recordState").inner_text()
        assert "2 click(s)" in shown and "17 key(s)" in shown


class TestWhatARecordingIsMissing:
    """The first real recording produced a flow with no read step: it drove
    the application and recorded the answer nowhere. That was printed to a
    console behind the browser."""

    def test_nothing_is_said_when_there_is_nothing_to_say(self, page):
        page.evaluate("""() => showProblemsWith({problems: []})""")
        assert page.locator("#problems").is_hidden()

    def test_the_missing_read_step_is_on_screen(self, page):
        page.evaluate("""() => showProblemsWith({problems: [
            "no reply region was found, so this flow drives the application " +
            "and records nothing."]})""")
        assert page.locator("#problems").is_visible()
        assert "records nothing" in page.locator("#problemList").inner_text()

    def test_every_problem_is_listed(self, page):
        page.evaluate("""() => showProblemsWith({problems: ["one", "two"]})""")
        assert page.locator("#problemList li").count() == 2


class TestSeeingTheAnswerWhenOcrIsMissing:
    """The pixels the answer was read from are kept whether or not OCR ran.
    Without an engine the text is empty and they are the only record of what
    came back -- which is exactly the state a fresh Windows machine is in."""

    def _turn(self, page, result):
        page.evaluate("(r) => showTurn(r, 'runs/x')", result)

    def test_the_read_region_is_shown_when_there_is_no_text(self, page):
        self._turn(page, {"prompt_id": "baseline", "status": "error",
                          "prompt": "hello", "response": "",
                          "read_images": {"response": "baseline/response.png"},
                          "screenshots": []})
        assert page.locator(".shots.answer img").count() == 1
        assert "not read as text" in page.locator(".turn").inner_text()

    def test_it_says_the_pixels_are_there(self, page):
        self._turn(page, {"prompt_id": "baseline", "status": "error",
                          "prompt": "hello", "response": "",
                          "read_images": {"response": "baseline/response.png"},
                          "screenshots": []})
        assert "pixels it was read from" in page.locator(".turn").inner_text()

    def test_text_is_shown_when_there_is_text(self, page):
        self._turn(page, {"prompt_id": "baseline", "status": "ok",
                          "prompt": "hello", "response": "I'm AURA",
                          "read_images": {}, "screenshots": []})
        assert "I'm AURA" in page.locator(".turn").inner_text()
        assert page.locator(".shots.answer").count() == 0
