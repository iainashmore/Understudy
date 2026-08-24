"""Prompt variants.

Kept in their own file because this is the one the user edits constantly; it
must never require touching the flow. YAML and CSV are both first-class -- CSV
because editing variants in a spreadsheet is the common case, BOM and all.

Any column or key besides `id` is a variable, so a flow can reference
{{prompt}}, {{style}}, {{document}} or anything else without a format change.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class PromptsError(ValueError):
    """The prompts file is wrong. Human-fixable."""


@dataclass(frozen=True)
class PromptVariant:
    id: str
    variables: dict[str, str] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        """Convenience for the common single-variable case."""
        return self.variables.get("prompt", "")


@dataclass(frozen=True)
class PromptSet:
    variants: tuple[PromptVariant, ...]
    source_path: Path | None = None
    source_text: str = ""

    def __iter__(self):
        return iter(self.variants)

    def __len__(self) -> int:
        return len(self.variants)

    def select(self, only: list[str] | None) -> "PromptSet":
        if not only:
            return self
        wanted = list(dict.fromkeys(only))
        known = {variant.id: variant for variant in self.variants}
        missing = [name for name in wanted if name not in known]
        if missing:
            raise PromptsError(
                f"unknown prompt id(s): {', '.join(missing)}. "
                f"Available: {', '.join(known)}"
            )
        return PromptSet(
            variants=tuple(known[name] for name in wanted),
            source_path=self.source_path,
            source_text=self.source_text,
        )

    def check_provides(self, required: set[str]) -> None:
        """Every variable the flow uses must be present in every row.

        Checked before the run starts. Discovering on row 40 of 50 that one
        column was misspelled wastes the whole sweep, and a row that ran with
        '{{style}}' left in the text looks like a real result.
        """
        problems = []
        for variant in self.variants:
            missing = sorted(required - set(variant.variables))
            if missing:
                problems.append(f"  {variant.id}: missing {', '.join(missing)}")
        if problems:
            raise PromptsError(
                "the flow uses variables the prompts file does not provide:\n"
                + "\n".join(problems)
            )


def _variant(row: dict[str, Any], position: int, source: str) -> PromptVariant:
    cleaned = {
        str(key).strip(): ("" if value is None else str(value))
        for key, value in row.items()
        if key is not None and str(key).strip()
    }
    identifier = cleaned.pop("id", "").strip()
    if not identifier:
        raise PromptsError(f"{source}: entry {position} has no 'id'")
    return PromptVariant(id=identifier, variables=cleaned)


def parse_prompts(text: str, fmt: str, source: str = "prompts") -> PromptSet:
    if fmt == "yaml":
        data = yaml.safe_load(text)
        if not isinstance(data, list):
            raise PromptsError(f"{source}: expected a list of prompt entries")
        rows = []
        for position, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                raise PromptsError(
                    f"{source}: entry {position} must be a mapping, "
                    f"got {type(entry).__name__}"
                )
            rows.append(entry)
    elif fmt == "csv":
        # utf-8-sig: Excel writes a BOM, and a BOM turns the first column name
        # into '﻿id', which then looks like a missing id.
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not reader.fieldnames:
            raise PromptsError(f"{source}: CSV has no header row")
    else:
        raise PromptsError(f"unsupported prompts format {fmt!r}")

    if not rows:
        raise PromptsError(f"{source}: no prompt variants")

    variants = tuple(
        _variant(row, position, source) for position, row in enumerate(rows, start=1)
    )
    duplicates = sorted(
        {v.id for v in variants if [x.id for x in variants].count(v.id) > 1}
    )
    if duplicates:
        raise PromptsError(
            f"{source}: duplicate prompt id(s): {', '.join(duplicates)}"
        )
    return PromptSet(variants=variants, source_text=text)


def load_prompts(path: Path | str) -> PromptSet:
    path = Path(path)
    if not path.exists():
        raise PromptsError(f"no prompts file at {path}")
    fmt = "csv" if path.suffix.lower() in (".csv", ".tsv") else "yaml"
    text = path.read_text(encoding="utf-8-sig")
    parsed = parse_prompts(text, fmt, source=str(path))
    return PromptSet(
        variants=parsed.variants, source_path=path, source_text=text
    )
