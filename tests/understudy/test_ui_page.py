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
    """The Run tab, open. The tag box is in it, and a hidden input cannot be
    typed into."""
    page.click('nav button[data-tab="run"]')
    page.wait_for_timeout(200)
    return page


def test_the_page_runs_without_throwing(page):
    assert page.errors == [], page.errors


def test_the_flow_list_is_populated(page):
    """The symptom of a script that threw: everything is empty and nothing
    says why."""
    assert page.locator(".tree-item").count() == 1


def test_a_flow_lists_its_runs_underneath(page):
    page.locator(".tree-item").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".run-list:not([hidden]) .run").count() == 1


def test_the_tag_box_offers_what_has_been_used_before(page):
    """Typing "R2026x FD03" again to re-run last month's build is the retyping
    that produces "FD3" on the fortieth run."""
    values = [o.get_attribute("value")
              for o in page.locator("#known-tags option").all()]
    assert "FD03" in values
    assert "model-version:FD03" in values, "the prefixed form is offered too"


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
        page.click("#backend")
        assert "FD09" in self.chips(page)

    def test_opening_a_flow_shows_what_it_was_last_run_against(self, run_tab):
        page = run_tab
        page.locator(".tree-item").first.click()
        page.wait_for_timeout(400)
        assert "FD03" in self.chips(page)
        assert "CATIA" in self.chips(page)
