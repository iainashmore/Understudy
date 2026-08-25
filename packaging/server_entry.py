"""Entry point for the packaged server.

Deliberately thin. Everything it does is `understudy.cli ui` with arguments,
and the reason it exists at all is that PyInstaller wants a script rather than
a module, and that a frozen build needs to be told where its bundled ffmpeg and
browsers live before anything imports them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundle_root() -> Path:
    """Where the packaged resources landed. The source tree when not frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def point_at_bundled_tools() -> None:
    """Use the binaries we shipped rather than whatever is on PATH.

    A CAD workstation may have neither, and the versions it does have are not
    ones this was tested against. Set before any import that reads them.
    """
    root = bundle_root()

    browsers = root / "ms-playwright"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
        # Nothing should ever try to fetch a browser from a packaged build.
        os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")

    tools = root / "tools"
    if tools.is_dir():
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")

    tessdata = root / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))


def main() -> int:
    point_at_bundled_tools()

    from understudy.cli import main as cli_main

    argv = sys.argv[1:]
    # Launched by the desktop shell with no arguments at all: serve the UI,
    # which is the only thing the shell ever wants.
    if not argv:
        argv = ["ui", "--no-open"]
    elif argv[0].startswith("-"):
        argv = ["ui", "--no-open", *argv]
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
