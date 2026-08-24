"""Collections of flows.

A suite is a list of flows to run together, with enough description to navigate
them. It exists because one flow per file stops scaling the moment there are
twenty of them and nobody remembers which is which.

Paths are relative to the suite file, so a folder of flows moves as a unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from flowrunner.flow import Flow, FlowError, load_flow


class SuiteError(ValueError):
    """The suite file is wrong. Human-fixable."""


@dataclass(frozen=True)
class SuiteEntry:
    flow_path: Path
    description: str = ""
    tags: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    skip: bool = False
    #: Populated on load so a listing can be shown without re-reading the files.
    flow: Flow | None = None
    error: str | None = None

    @property
    def name(self) -> str:
        return self.flow.name if self.flow else self.flow_path.stem

    @property
    def title(self) -> str:
        return (self.flow.title if self.flow else "") or self.name

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "-").replace("/", "-")

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "flow": str(self.flow_path),
            "description": self.description,
            "tags": list(self.tags),
            "steps": len(self.flow.steps) if self.flow else 0,
            "variants": len(self.flow.embedded_prompts) if self.flow else 0,
            "skip": self.skip,
            "error": self.error,
        }


@dataclass(frozen=True)
class Suite:
    name: str
    entries: tuple[SuiteEntry, ...]
    description: str = ""
    source_path: Path | None = None
    source_text: str = ""

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def select(self, names: list[str] | None = None, tags: list[str] | None = None) -> "Suite":
        """Narrow by flow name or tag. Skipped entries stay skipped."""
        chosen = list(self.entries)
        if names:
            wanted = {name.lower() for name in names}
            known = {entry.name.lower() for entry in chosen} | {
                entry.flow_path.stem.lower() for entry in chosen
            }
            missing = sorted(wanted - known)
            if missing:
                raise SuiteError(
                    f"unknown flow(s): {', '.join(missing)}. "
                    f"Available: {', '.join(entry.name for entry in self.entries)}"
                )
            chosen = [
                entry for entry in chosen
                if entry.name.lower() in wanted or entry.flow_path.stem.lower() in wanted
            ]
        if tags:
            wanted_tags = {tag.lower() for tag in tags}
            chosen = [
                entry for entry in chosen
                if wanted_tags & {tag.lower() for tag in entry.tags}
            ]
        return Suite(self.name, tuple(chosen), self.description,
                     self.source_path, self.source_text)

    @property
    def runnable(self) -> list[SuiteEntry]:
        return [entry for entry in self.entries if not entry.skip and entry.error is None]

    def problems(self) -> list[str]:
        return [f"{entry.flow_path}: {entry.error}" for entry in self.entries if entry.error]


def parse_suite(data: dict[str, Any], base_dir: Path, source_text: str = "",
                source_path: Path | None = None) -> Suite:
    if not isinstance(data, dict):
        raise SuiteError("suite must be a mapping at the top level")
    if data.get("version") != 1:
        raise SuiteError("suite needs `version: 1`")
    if "flows" not in data or not isinstance(data["flows"], list) or not data["flows"]:
        raise SuiteError("suite needs a non-empty `flows` list")

    entries = []
    seen: set[Path] = set()
    for position, raw in enumerate(data["flows"], start=1):
        if isinstance(raw, str):
            raw = {"path": raw}
        if not isinstance(raw, dict) or "path" not in raw:
            raise SuiteError(f"flows[{position}] needs a `path`")

        flow_path = (base_dir / raw["path"]).resolve()
        if flow_path in seen:
            raise SuiteError(f"{raw['path']} is listed twice")
        seen.add(flow_path)

        flow: Flow | None = None
        error: str | None = None
        try:
            flow = load_flow(flow_path)
        except (FlowError, OSError) as exc:
            # A broken flow does not stop the suite from listing: the point of
            # a collection is to see everything, including what is broken.
            error = str(exc)

        entries.append(SuiteEntry(
            flow_path=flow_path,
            description=raw.get("description") or (flow.description if flow else ""),
            tags=tuple(raw.get("tags") or (flow.tags if flow else ())),
            only=tuple(raw.get("only") or ()),
            skip=bool(raw.get("skip", False)),
            flow=flow,
            error=error,
        ))

    return Suite(
        name=str(data.get("name") or (source_path.stem if source_path else "suite")),
        description=str(data.get("description", "")),
        entries=tuple(entries),
        source_path=source_path,
        source_text=source_text,
    )


def load_suite(path: Path | str) -> Suite:
    path = Path(path)
    if not path.exists():
        raise SuiteError(f"no suite file at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SuiteError(f"{path}: not valid YAML -- {exc}") from None
    return parse_suite(data, path.parent, text, path)


def is_suite_file(path: Path | str) -> bool:
    """Tell a suite from a flow when listing a folder.

    Top-level keys anywhere in the file, not in a fixed-size window -- comments
    at the top of a real file push the distinguishing keys well past any head
    you might read.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    keys = {
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line[:1].isalpha() and ":" in line
    }
    return "flows" in keys and "steps" not in keys
