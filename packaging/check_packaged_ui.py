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


CHROMIUM = (
    "chromium-*/chrome-win64/chrome.exe",
    "chromium-*/chrome-win/chrome.exe",
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
)


def browser_roots(bundle: Path) -> list[Path]:
    """Where a bundled ms-playwright might be.

    Found rather than assumed. PyInstaller 6 puts collected data under
    _internal, and the first version of this looked only beside the executable
    -- so on a build that shipped Chromium it reported "no bundled Chromium"
    and skipped, which is the one thing a check must not do quietly.
    """
    roots = [bundle / "ms-playwright", bundle / "_internal" / "ms-playwright"]
    roots += [path for path in bundle.rglob("ms-playwright") if path.is_dir()]
    from_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if from_env:
        roots.append(Path(from_env))
    return [root for root in roots if root.is_dir()]


def find_browser(bundle: Path) -> tuple[str | None, bool]:
    """(the Chromium this build ships, whether it ships one at all).

    The two are different: no browser directory is a development build and a
    fair skip; a browser directory with nothing runnable in it is a broken
    bundle, and saying "skipping" about that would hide it.

    Looked up here rather than through understudy.drivers, because a check on
    a packaged build should not depend on the package being importable from
    wherever it is run.
    """
    for root in browser_roots(bundle):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root)
        for pattern in CHROMIUM:
            found = sorted(root.glob(pattern))
            if found:
                return str(found[-1]), True
        return None, True
    return None, False


def main() -> int:
    server_exe = Path(sys.argv[1]).resolve()
    bundle = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else server_exe.parent
    port = 8792
    url = f"http://127.0.0.1:{port}/"

    browser_path, ships_browsers = find_browser(bundle)
    if not browser_path and not ships_browsers:
        # A development build ships no browser. Say so rather than passing
        # quietly, so a check that never runs cannot look like one that does.
        print("this build ships no browser: skipping the page check", flush=True)
        return 0
    if not browser_path:
        print("the bundle has an ms-playwright directory with no Chromium in "
              "it", file=sys.stderr)
        return 1
    print(f"driving the page with {browser_path}", flush=True)

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
