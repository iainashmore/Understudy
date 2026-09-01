#!/usr/bin/env python3
"""Thin wrapper: `understudy record` is the same thing with a nicer name."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from understudy.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["record"] + sys.argv[1:]))
