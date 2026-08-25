"""Printing a transcript to PDF.

Chromium is already here for the web backend, and it is a better print engine
than anything that could be added as a dependency: it lays out the same HTML the
viewer shows, so the PDF and the screen agree.

The page hides its video element when printing -- a PDF cannot play one -- and
the recording is linked instead. Images are inlined so the PDF does not depend
on the run folder still existing next to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from understudy.transcript_html import render_full_html

#: Roomy enough for a 460px screenshot at full width without shrinking it.
PAGE_FORMAT = "A4"
MARGIN = {"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"}


@dataclass(frozen=True)
class PdfResult:
    path: Path | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


def write_pdf(run_dir: Path | str, filename: str = "transcript.pdf") -> PdfResult:
    """Render the run's transcript and print it. Never raises."""
    run_dir = Path(run_dir)
    target = run_dir / filename
    try:
        # Every prompt run, not the index: a filed copy that says "see
        # the other eleven files" is not a record of anything.
        html = render_full_html(run_dir, embed=True)
    except Exception as exc:
        return PdfResult(None, f"could not build the transcript: {exc}")

    try:
        from playwright.sync_api import sync_playwright

        from understudy.drivers.web import find_chromium
    except ImportError:
        return PdfResult(None, "PDF export needs playwright (pip install "
                               "understudy[web])")

    launch = {"headless": True}
    executable = find_chromium()
    if executable:
        launch["executable_path"] = executable

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch)
            try:
                page = browser.new_page()
                # A data URL would hit Chromium's URL length limit on a
                # transcript with inlined screenshots, and a file:// page would
                # need the images left on disk. set_content sidesteps both.
                page.set_content(html, wait_until="load")
                page.emulate_media(media="print")
                page.pdf(path=str(target), format=PAGE_FORMAT,
                         margin=MARGIN, print_background=True,
                         display_header_footer=False)
            finally:
                browser.close()
    except Exception as exc:
        return PdfResult(None, f"could not print the transcript: {exc}")

    if not target.exists():
        return PdfResult(None, "chromium produced no file")
    return PdfResult(target)
