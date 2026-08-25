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


@pytest.fixture
def page(served):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from understudy.drivers.web import find_chromium

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=find_chromium())
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(served, wait_until="networkidle")
        page.wait_for_timeout(400)
        page.errors = errors
        yield page
        browser.close()


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


def test_the_subject_fields_offer_what_has_been_used_before(page):
    """Typing "R2026x FD03" again to re-run last month's build is the retyping
    that produces "FD3" on the fortieth run."""
    options = page.locator("#known-model_version option")
    assert options.count() == 1
    assert options.first.get_attribute("value") == "FD03"
