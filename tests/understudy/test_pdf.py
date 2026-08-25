"""Printing the transcript.

Chromium does the layout, so there is little logic here to test. What is worth
pinning is that a failure loses the export rather than the run, and that the
printed page really contains the transcript rather than an empty sheet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from understudy.pdf import write_pdf

FLOW = "version: 1\nname: pdf-flow\ntitle: PDF flow\n"


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "flow.yaml").write_text(FLOW)
    (tmp_path / "results.jsonl").write_text(json.dumps({
        "prompt_id": "baseline", "repeat_index": 0,
        "prompt": "A distinctive prompt string", "variables": {},
        "response": "A distinctive response string", "reads": {}, "read_images": {},
        "status": "ok", "duration_ms": 100, "screenshots": [],
        "backend": "web", "timestamp": "2026-08-25T00:00:00Z",
        "step_statuses": [{"index": 1, "phase": "steps", "action": "click",
                           "target": "send", "status": "ok", "duration_ms": 10}],
    }) + "\n")
    return tmp_path


def test_a_pdf_is_written(run_dir):
    pytest.importorskip("playwright")
    outcome = write_pdf(run_dir)
    assert outcome.ok, outcome.error
    assert outcome.path == run_dir / "transcript.pdf"
    assert outcome.path.read_bytes().startswith(b"%PDF-")


def test_the_pdf_contains_the_transcript(run_dir):
    pytest.importorskip("playwright")
    poppler = __import__("shutil").which("pdftotext")
    if not poppler:
        pytest.skip("needs poppler-utils to read the pdf back")
    outcome = write_pdf(run_dir)
    text = __import__("subprocess").run(
        [poppler, str(outcome.path), "-"], capture_output=True, text=True
    ).stdout
    assert "A distinctive prompt string" in text
    assert "A distinctive response string" in text
    assert "PDF flow" in text


def test_a_run_with_no_results_fails_with_a_message_not_a_traceback(tmp_path):
    outcome = write_pdf(tmp_path)
    assert not outcome.ok
    assert "could not build the transcript" in outcome.error


def test_a_broken_browser_is_reported_not_raised(run_dir, monkeypatch):
    pytest.importorskip("playwright")
    import playwright.sync_api

    def explode(*args, **kwargs):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", explode)
    outcome = write_pdf(run_dir)
    assert not outcome.ok
    assert "no browser here" in outcome.error
