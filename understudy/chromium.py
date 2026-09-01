"""Finding a browser on this machine.

Not for driving anything: Understudy drives Windows applications. A browser is
needed to turn a transcript into a PDF, and to open Understudy's own page in
the tests that check the app works.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_chromium() -> str | None:
    """Locate a browser, preferring whatever Playwright already knows about.

    Falls back to scanning PLAYWRIGHT_BROWSERS_PATH, because a pre-provisioned
    browser build often does not match the pip package's expected revision and
    re-downloading is not always possible.
    """
    override = os.environ.get("UNDERSTUDY_CHROMIUM")
    if override:
        return override

    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if not root.exists():
        return None
    candidates = sorted(root.glob("chromium-*/chrome-linux/chrome"))
    candidates += sorted(root.glob("chromium-*/chrome-win/chrome.exe"))
    candidates += sorted(
        root.glob("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium")
    )
    return str(candidates[-1]) if candidates else None
