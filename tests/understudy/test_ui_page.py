"""The page itself loads and runs.

Every other UI test drives the API directly, which is the right way to test
what the endpoints do and no way at all to notice that the page they serve
does not execute. A duplicate `const` at the top level throws before the first
line of setup runs: no flow list, no runs, no buttons -- an entirely blank
sidebar, and every API test still green.

That happened twice in one afternoon. This is the check that would have caught
it in a second rather than in a screenshot.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from understudy.ui.server import Api, Handler, Workspace

FLOW = """version: 1
name: page-test
title: Page test
target_app:
  web:
    url: "about:blank"
targets:
  box:
    web:
      - testid: prompt-input
prompts:
  - id: baseline
    prompt: hello
steps:
  - action: type
    target: box
    text: "{{prompt}}"
"""


@pytest.fixture
def served(tmp_path):
    (tmp_path / "flow.yaml").write_text(FLOW)
    run = tmp_path / "runs" / "2026-09-01"
    run.mkdir(parents=True)
    (run / "flow.yaml").write_text(FLOW)
    (run / "results.jsonl").write_text(json.dumps({
        "prompt_id": "baseline", "repeat_index": 0, "prompt": "hello",
        "variables": {}, "response": "hi", "reads": {"response": "hi"},
        "read_images": {}, "status": "ok", "duration_ms": 10, "screenshots": [],
        "step_statuses": [], "backend": "web", "flow": "page-test",
        "subject": {"app": "CATIA", "app_version": "R2026x",
                    "model": "LEO", "model_version": "FD03"},
        "timestamp": "2026-09-01T10:00:00Z",
    }) + "\n")

    Handler.api = Api(Workspace(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from understudy.drivers.web import find_chromium

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


@pytest.fixture
def run_tab(page):
    """A flow open, which is where the replay controls live now. There is no
    Run tab: the pane is whatever is selected on the left."""
    page.locator(".tree-item").first.click()
    page.wait_for_timeout(300)
    return page


def test_the_page_runs_without_throwing(page):
    assert page.errors == [], page.errors


def test_the_flow_list_is_populated(page):
    """The symptom of a script that threw: everything is empty and nothing
    says why."""
    assert page.locator(".tree-item").count() == 1


def test_a_flow_lists_its_runs_underneath(page):
    """And on the first draw, not after something else happens to redraw it.
    The tree draws each flow's runs under it; drawn before the runs arrive it
    said "no runs yet" over a workspace full of them."""
    page.locator(".tree-item").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".run-list:not([hidden]) .run").count() == 1


def test_showing_the_browser_is_not_offered_for_a_windows_flow(run_tab):
    """There is no browser to show. An option that cannot mean anything is a
    question the reader has to answer before they can ignore it."""
    page = run_tab
    assert page.locator("#headedLabel").is_visible(), "this flow drives a page"

    page.evaluate("() => { document.getElementById('backend').value = 'native'; "
                  "backendChanged(); }")
    assert page.locator("#headedLabel").is_hidden()


def test_the_form_says_what_the_flow_drives_rather_than_asking(run_tab):
    """The flow file already says. Asking again is a question with one right
    answer, and the wrong one fails at the first step with "flow has no
    target_app.web section"."""
    page = run_tab
    assert page.locator("#backend").is_hidden(), "a choice with one option"
    assert "web page" in page.locator("#drivesLabel").inner_text()


def test_the_tag_box_offers_what_has_been_used_before(page):
    """Typing "R2026x FD03" again to re-run last month's build is the retyping
    that produces "FD3" on the fortieth run."""
    values = [o.get_attribute("value")
              for o in page.locator("#known-tags option").all()]
    assert "FD03" in values
    assert "model-version:FD03" in values, "the prefixed form is offered too"


class TestSelectionDrivesThePane:
    """No tabs. Pressing one was always a second way of saying what the
    selection already said, and the two could disagree."""

    def test_a_flow_shows_the_flow(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        assert page.locator("section#tab-flow").is_visible()
        assert page.locator("section#tab-transcript").is_hidden()

    def test_a_run_shows_its_transcript(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        page.locator(".run-list:not([hidden]) .run").first.click()
        page.wait_for_timeout(600)

        assert page.locator("section#tab-transcript").is_visible()
        assert page.locator("section#tab-flow").is_hidden()
        # And the header says which run, since nothing else does now.
        assert "FD03" in page.locator("#currentDesc").inner_text()

    def test_going_back_to_the_flow_lets_go_of_the_run(self, page):
        """Leaving a reader looking at a transcript belonging to a flow they
        have navigated away from is worse than showing them the flow."""
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        page.locator(".run-list:not([hidden]) .run").first.click()
        page.wait_for_timeout(600)
        assert page.locator("section#tab-transcript").is_visible()

        page.locator(".tree-item").first.click()
        page.wait_for_timeout(400)
        assert page.locator("section#tab-flow").is_visible()

    def test_settings_are_behind_the_gear(self, page):
        """Repository and credentials are configuration, not content, and they
        were sitting in the bar next to the transcript."""
        assert page.locator("#settingsMenu").is_hidden()
        page.click("#settings")
        page.click('#settingsMenu button[data-view="repo"]')
        page.wait_for_timeout(200)

        assert page.locator("section#tab-repo").is_visible()
        page.locator("section#tab-repo button.back").click()
        assert page.locator("section#tab-flow").is_visible(), "and a way out"

    def test_replaying_is_offered_for_a_flow_and_not_for_a_run(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        assert page.locator("#run").is_visible()


class TestTheCountsInTheTree:
    """The same-looking number in the same column meant two things: how many
    prompts a flow has, and how many of a run's prompt runs passed."""

    def test_a_flow_says_what_its_number_counts(self, page):
        count = page.locator(".tree-item .count").first
        assert "prompt" in count.inner_text()

    def test_a_run_is_a_ratio_and_says_so(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        count = page.locator(".run-list:not([hidden]) .run .count").first

        assert count.inner_text().strip() == "1/1"
        assert "passed" in count.get_attribute("title")


class TestTheContextMenu:
    """Right-click, not link buttons that appear on hover. A narrow column of
    names is a bad place to put "del" one pixel from "copy"."""

    def entries(self, page):
        return [b.inner_text() for b in page.locator("#menu button").all()]

    def test_a_flow_offers_what_can_be_done_to_a_flow(self, page):
        page.locator(".tree-item").first.click(button="right")
        page.wait_for_timeout(200)

        entries = self.entries(page)
        # Everything a flow can be told to do, in one place.
        for expected in ("Open", "Replay", "Validate", "Record steps",
                         "Duplicate…", "Save as…"):
            assert expected in entries, f"{expected} missing from {entries}"
        assert any(e.startswith("Delete") for e in entries)

    def test_a_run_offers_what_can_be_done_to_a_run(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        page.locator(".run-list:not([hidden]) .run").first.click(button="right")
        page.wait_for_timeout(200)

        entries = self.entries(page)
        for expected in ("Open transcript", "Rebuild transcript", "Export PDF",
                         "Export a standalone page", "Download the markdown"):
            assert expected in entries, f"{expected} missing from {entries}"
        assert any(e.startswith("Publish") for e in entries)
        # A run cannot be duplicated or edited; offering it would be a lie.
        assert "Duplicate…" not in entries

    def test_comparing_asks_which_run_rather_than_guessing_one(self, page):
        """A workspace of one run has nothing to compare against, so the item
        is not offered at all -- a submenu that opens onto nothing is worse
        than a missing one."""
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        page.locator(".run-list:not([hidden]) .run").first.click(button="right")
        page.wait_for_timeout(200)

        assert not any(e.startswith("Compare with") for e in self.entries(page))

    def test_it_names_what_it_is_acting_on(self, page):
        """Menus opened by right-clicking a list are opened next to the row
        above as often as the right one."""
        page.locator(".tree-item").first.click(button="right")
        page.wait_for_timeout(200)
        assert "Chat assistant" in page.locator("#menu .heading").inner_text() \
            or page.locator("#menu .heading").inner_text().strip()

    def test_escape_closes_it(self, page):
        page.locator(".tree-item").first.click(button="right")
        page.wait_for_timeout(200)
        assert page.locator("#menu").is_visible()

        page.keyboard.press("Escape")
        assert page.locator("#menu").is_hidden()

    def test_it_stays_on_screen_near_the_bottom(self, page):
        """A menu opened low in a long list otherwise puts Delete off the
        edge, which is the item you least want to have to guess at."""
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        rows = page.locator(".run-list:not([hidden]) .run")
        rows.last.click(button="right", position={"x": 5, "y": 5})
        page.wait_for_timeout(200)

        box = page.locator("#menu").bounding_box()
        height = page.evaluate("() => window.innerHeight")
        assert box["y"] + box["height"] <= height, box

    def test_nothing_is_only_reachable_from_it(self, page):
        """A context menu is a shortcut, not the only door: every action in it
        exists somewhere a keyboard can reach."""
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        for selector in ("#duplicate", "#deleteFlow", "[data-saveas]"):
            assert page.locator(selector).first.is_visible(), selector


class TestTheTagBox:
    """One control, not five boxes. A tag is a word; a tag with a known prefix
    fills a field, which is the only reason the fields still exist -- a
    comparison labels its columns with them."""

    def chips(self, page):
        return [c.inner_text().strip().rstrip("×").strip()
                for c in page.locator("#subjectTags .chip").all()]

    def test_a_bare_word_becomes_a_tag(self, run_tab):
        page = run_tab
        page.click("#tagInput")
        page.type("#tagInput", "qa-laptop")
        page.keyboard.press("Enter")
        assert "qa-laptop" in self.chips(page)

    def test_a_prefix_fills_the_field_the_comparison_labels_with(self, run_tab):
        page = run_tab
        page.click("#tagInput")
        page.type("#tagInput", "app:CATIA V5")
        page.keyboard.press("Enter")
        assert "CATIA V5" in self.chips(page)
        # A colon inside the value survives: "note:see ticket: 44".
        page.type("#tagInput", "note:see ticket: 44")
        page.keyboard.press("Enter")
        assert "see ticket: 44" in self.chips(page)

    def test_a_chip_can_be_taken_off_again(self, run_tab):
        page = run_tab
        page.click("#tagInput")
        page.type("#tagInput", "wrong-one")
        page.keyboard.press("Enter")
        assert "wrong-one" in self.chips(page)
        page.locator("#subjectTags .chip", has_text="wrong-one").locator("button").click()
        assert "wrong-one" not in self.chips(page)

    def test_leaving_the_box_commits_what_was_typed(self, run_tab):
        """A run started with a half-typed build still in the box, and nothing
        recorded against it, is a run that has to be done again."""
        page = run_tab
        page.click("#tagInput")
        page.type("#tagInput", "FD09")
        page.click("#drivesLabel")
        assert "FD09" in self.chips(page)

    def test_opening_a_flow_shows_what_it_was_last_run_against(self, run_tab):
        page = run_tab
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(400)
        assert "FD03" in self.chips(page)
        assert "CATIA" in self.chips(page)


class TestComparingFromTheMenu:
    """"Compare with…" is a question with as many answers as there are other
    runs of that flow, so it asks instead of choosing one."""

    @pytest.fixture
    def two_runs(self, tmp_path):
        """A second run of the same flow, so there is something to compare."""
        second = tmp_path / "runs" / "2026-09-02"
        second.mkdir(parents=True)
        (second / "flow.yaml").write_text(FLOW)
        (second / "results.jsonl").write_text(json.dumps({
            "prompt_id": "baseline", "repeat_index": 0, "prompt": "hello",
            "variables": {}, "response": "hi", "reads": {"response": "hi"},
            "read_images": {}, "status": "ok", "duration_ms": 10,
            "screenshots": [], "step_statuses": [], "backend": "web",
            "flow": "page-test",
            "subject": {"app": "CATIA", "app_version": "R2026x",
                        "model": "LEO", "model_version": "FD04"},
            "timestamp": "2026-09-02T10:00:00Z",
        }) + "\n")
        return tmp_path

    def open_run_menu(self, page):
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(300)
        page.locator(".run-list:not([hidden]) .run").first.click(button="right")
        page.wait_for_timeout(200)

    def test_it_lists_the_other_runs_of_that_flow(self, two_runs, page):
        self.open_run_menu(page)
        page.locator("#menu .has-submenu button").hover()
        page.wait_for_timeout(300)

        offered = [b.inner_text() for b in page.locator(".flyout button").all()]
        assert len(offered) == 1, offered
        assert "FD03" in offered[0], "the other run, not this one"

    def test_the_run_itself_is_not_in_its_own_list(self, two_runs, page):
        self.open_run_menu(page)
        page.locator("#menu .has-submenu button").hover()
        page.wait_for_timeout(300)

        offered = " ".join(b.inner_text() for b in page.locator(".flyout button").all())
        assert "FD04" not in offered, "comparing a run with itself compares nothing"

    def test_the_list_stays_on_screen(self, two_runs, page):
        self.open_run_menu(page)
        page.locator("#menu .has-submenu button").hover()
        page.wait_for_timeout(300)

        box = page.locator(".flyout").bounding_box()
        assert box["x"] + box["width"] <= page.evaluate("() => window.innerWidth")
        assert box["y"] + box["height"] <= page.evaluate("() => window.innerHeight")


class TestThePickWindowButton:
    """Naming the window a native flow drives, by picking it rather than
    guessing a glob. 3DEXPERIENCE offers several windows of the same name, so
    the process goes into the flow too."""

    def test_it_says_when_the_machine_cannot_look(self, run_tab):
        # These tests run on Linux, where there are no windows to enumerate --
        # which is itself a case the menu has to handle without throwing.
        run_tab.click("#pickWindow")
        run_tab.wait_for_timeout(200)
        assert run_tab.locator("#menu").is_visible()
        assert "Only on Windows" in run_tab.locator("#menu").inner_text()
        assert run_tab.errors == []

    def test_a_flow_with_no_target_gets_a_whole_native_block(self, page):
        written = page.evaluate(
            """() => withNativeTarget("title: A flow\\n", "3DX R2026x", "CATIA.exe")"""
        )
        assert written.endswith(
            'target_app:\n  native:\n'
            '    window_title_pattern: "3DX R2026x"\n'
            '    process: "CATIA.exe"\n'
        )

    def test_an_existing_native_block_is_filled_in_not_duplicated(self, page):
        written = page.evaluate(
            """() => withNativeTarget(
                 "target_app:\\n  native:\\n    monitor: primary\\n",
                 "3DX R2026x", "CATIA.exe")"""
        )
        assert written.count("native:") == 1
        assert '    window_title_pattern: "3DX R2026x"' in written
        assert "    monitor: primary" in written, "the rest of the block survives"

    def test_picking_again_replaces_rather_than_appends(self, page):
        once = page.evaluate(
            """() => withNativeTarget(
                 'target_app:\\n  native:\\n    window_title_pattern: "*Old*"\\n'
                 + '    process: "old.exe"\\n', "3DX R2026x", "CATIA.exe")"""
        )
        assert once.count("window_title_pattern") == 1
        assert once.count("process:") == 1
        assert "*Old*" not in once and "old.exe" not in once

    def test_a_web_flow_keeps_its_own_target(self, page):
        written = page.evaluate(
            """() => withNativeTarget(
                 'target_app:\\n  web:\\n    url: "https://example.com/"\\n',
                 "3DX", "CATIA.exe")"""
        )
        assert 'url: "https://example.com/"' in written
        assert "native:" in written

    def test_a_window_is_labelled_by_more_than_its_title(self, page):
        label = page.evaluate(
            """() => windowLabel({title: "3DEXPERIENCE", process: "CATIA.exe",
                                  pid: 7788, width: 1920, height: 1040,
                                  visible: true})"""
        )
        assert "CATIA.exe" in label and "1920" in label


class TestAFlowThatWillNotLoad:
    """A typo used to be silent: the tree greyed the title, the header claimed
    the flow drove a web page, and the first real sign was a replay dying at
    the first step."""

    @pytest.fixture
    def broken(self, served, browser, tmp_path):
        (tmp_path / "broken.yaml").write_text("version: 1\nname: x\nsteps: []\n")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(served, wait_until="networkidle")
        page.wait_for_timeout(300)
        page.errors = errors
        page.locator(".tree-item", has_text="broken.yaml").click()
        page.wait_for_timeout(300)
        yield page
        page.close()

    def test_the_problems_are_shown_without_pressing_validate(self, broken):
        shown = broken.locator("#validation").inner_text()
        assert "will not load" in shown
        assert "'prompts' is a required property" in shown
        assert broken.errors == []

    def test_every_problem_is_listed_not_only_the_first(self, broken):
        shown = broken.locator("#validation").inner_text()
        assert "steps" in shown, "the second schema failure is listed too"
        assert "more problem(s)" not in shown, "counted, rather than named"

    def test_it_does_not_claim_to_drive_anything(self, broken):
        assert "Will not load" in broken.locator("#drivesLabel").inner_text()
        assert broken.locator("#backend").is_hidden()

    def test_replay_is_refused_with_a_reason(self, broken):
        assert broken.locator("#run").is_disabled()
        assert "does not load" in broken.locator("#run").get_attribute("title")

    def test_the_tree_says_which_flow_it_is(self, broken):
        row = broken.locator(".tree-item", has_text="broken.yaml")
        # The label is kept on one line with non-breaking spaces.
        assert "will not load" in row.inner_text().replace("\u00a0", " ")

    def test_a_flow_that_loads_says_none_of_this(self, broken):
        broken.locator(".tree-item", has_text="Page test").click()
        broken.wait_for_timeout(300)
        assert broken.locator("#validation").inner_text().strip() == ""
        assert broken.locator("#run").is_enabled()

    def test_fixing_it_and_saving_clears_the_verdict(self, broken):
        broken.fill("#flowText", FLOW)
        broken.click("[data-save]")
        broken.wait_for_timeout(500)
        assert broken.locator("#validation").inner_text().strip() == ""
        assert broken.locator("#run").is_enabled()
        assert "Drives" in broken.locator("#drivesLabel").inner_text()
