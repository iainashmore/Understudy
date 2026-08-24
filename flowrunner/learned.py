"""Anchors the agent found, kept so it never has to be asked twice.

The agent resolves a control once; the crop it pointed at becomes an ordinary
anchor, and every later run matches it deterministically. That is what keeps
agent assistance from turning a repeatable sweep into a variable one -- the
model is consulted when the flow breaks, not on every variant.

Stored beside the flow rather than written back into it: rewriting a human's
flow file mid-run is a surprise nobody wants, and a directory of PNGs is
trivially inspectable and deletable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class LearnedAnchors:
    def __init__(self, directory: Path | str | None) -> None:
        self.directory = Path(directory) if directory else None
        self.index_path = self.directory / "index.json" if self.directory else None
        self.index: dict[str, dict] = {}
        if self.index_path and self.index_path.exists():
            try:
                self.index = json.loads(self.index_path.read_text())
            except Exception:
                self.index = {}

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def path_for(self, target: str) -> Path | None:
        if not self.directory:
            return None
        return self.directory / f"{target}.png"

    def get(self, target: str) -> bytes | None:
        path = self.path_for(target)
        if path and path.exists():
            return path.read_bytes()
        return None

    def put(self, target: str, image: bytes, note: str = "") -> Path | None:
        path = self.path_for(target)
        if not path:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        self.index[target] = {
            "anchor": path.name,
            "learned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": note,
        }
        if self.index_path:
            self.index_path.write_text(json.dumps(self.index, indent=2))
        return path

    def forget(self, target: str) -> None:
        path = self.path_for(target)
        if path and path.exists():
            path.unlink()
        self.index.pop(target, None)
        if self.index_path:
            self.index_path.write_text(json.dumps(self.index, indent=2))
