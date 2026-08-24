"""Prompt variants.

Every flow carries its own variants, in the flow file. One file is one test:
the steps, the targets and the prompts travel together, so a flow can be
copied, mailed or committed without leaving half of itself behind.

Any key besides `id` is a variable, so a flow can reference {{prompt}},
{{style}}, {{document}} or anything else without a format change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


def prompts_from_entries(
    entries: list[dict[str, Any]], source: str = "flow"
) -> PromptSet:
    """Build a PromptSet from variants written inside a flow file."""
    if not entries:
        raise PromptsError(f"{source}: no prompt variants")
    variants = tuple(
        _variant(entry, position, source)
        for position, entry in enumerate(entries, start=1)
    )
    duplicates = sorted(
        {v.id for v in variants if [x.id for x in variants].count(v.id) > 1}
    )
    if duplicates:
        raise PromptsError(f"{source}: duplicate prompt id(s): {', '.join(duplicates)}")
    return PromptSet(variants=variants)


def prompts_for(flow) -> PromptSet:
    """This flow's variants, from its own `prompts:` block."""
    if not flow.embedded_prompts:
        raise PromptsError(
            f"flow {flow.name!r} has no `prompts:` block. Every flow carries "
            f"its own variants."
        )
    parsed = prompts_from_entries(
        [dict(entry) for entry in flow.embedded_prompts],
        source=str(flow.source_path or flow.name),
    )
    return PromptSet(
        variants=parsed.variants, source_path=flow.source_path, source_text=""
    )
