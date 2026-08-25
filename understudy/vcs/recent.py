"""Remembering which workspace was open.

A tool that forgets where your flows are every time it starts is a tool you
have to re-configure before you can use it, which is most of the reason people
stop using a thing. Kept next to the credentials, and holding nothing secret --
paths and repository names only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".understudy" / "workspaces.json"
MAX_REMEMBERED = 8


def load(path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = data.get("workspaces") if isinstance(data, dict) else data
    return [e for e in (entries or []) if isinstance(e, dict) and e.get("directory")]


def remember(entry: dict[str, Any], path: Path | str | None = None) -> list[dict[str, Any]]:
    """Move this workspace to the front of the list."""
    path = Path(path) if path else DEFAULT_PATH
    directory = str(entry.get("directory") or "")
    if not directory:
        return load(path)

    kept = [e for e in load(path) if e.get("directory") != directory]
    entries = [dict(entry)] + kept
    entries = entries[:MAX_REMEMBERED]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspaces": entries}, indent=2) + "\n",
                    encoding="utf-8")
    return entries


def most_recent(path: Path | str | None = None) -> dict[str, Any] | None:
    entries = load(path)
    return entries[0] if entries else None


def forget(directory: str, path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(path) if path else DEFAULT_PATH
    entries = [e for e in load(path) if e.get("directory") != directory]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspaces": entries}, indent=2) + "\n",
                    encoding="utf-8")
    return entries
