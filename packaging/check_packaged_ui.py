"""Drive the packaged server's own page, in a browser, before shipping it.

The build already checks that the frozen server starts and answers the API.
That is not the same as the page working: a script that throws on load leaves
an empty sidebar, an empty flow list and every API check still green -- which
happened twice in one afternoon during development, both times found by a
screenshot rather than by a test.

It is worth doing against the *packaged* build specifically. The page is a
static file collected by PyInstaller, and "collected" and "served correctly
from where it landed" are different questions, answered on different machines
if nobody asks here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

FLOW = """version: 1
name: packaged-check
title: Packaged check
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


def serving(url: str, attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(2)
    return False


def find_browser(bundle: Path) -> str | None:
    """The Chromium this build ships, if it ships one.

    Looked up here rather than through understudy.drivers, because this script
    checks a packaged build and should not depend on the package being
    importable from wherever it is run.
    """
    roots = [bundle / "ms-playwright"]
    from_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if from_env:
        roots.append(Path(from_env))
    for root in roots:
        if not root.is_dir():
            continue
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root)
        for pattern in ("chromium-*/chrome-win/chrome.exe",
                        "chromium-*/chrome-linux/chrome",
                        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
            found = sorted(root.glob(pattern))
            if found:
                return str(found[-1])
    return None


def main() -> int:
    server_exe = Path(sys.argv[1]).resolve()
    bundle = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else server_exe.parent
    port = 8792
    url = f"http://127.0.0.1:{port}/"

    browser_path = find_browser(bundle)
    if not browser_path:
        # A development build ships no browser. Say so rather than passing
        # quietly, so a check that never runs cannot look like one that does.
        print("no bundled Chromium: skipping the page check", flush=True)
        return 0

    workspace = Path(tempfile.mkdtemp())
    (workspace / "flow.yaml").write_text(FLOW, encoding="utf-8")

    process = subprocess.Popen(
        [str(server_exe), "--workspace", str(workspace), "--port", str(port)]
    )
    try:
        if not serving(url):
            print("the packaged server never came up", file=sys.stderr)
            return 1

        from playwright.sync_api import sync_playwright

        problems: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=browser_path)
            page = browser.new_page()
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1500)

            flows = page.locator(".tree-item").count()
            print(f"page loaded, {flows} flow(s) listed", flush=True)
            if problems:
                print("the page threw: " + "; ".join(problems), file=sys.stderr)
            if not flows:
                print("the flow list is empty, which is what a thrown script "
                      "looks like", file=sys.stderr)
            browser.close()

        return 1 if problems or not flows else 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except Exception:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
