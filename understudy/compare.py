"""Comparing runs of the same flow.

This is the payoff. Everything else — holding the click path still, typing at a
human speed, recording the version that produced each answer — exists so that
two runs can be put side by side and the difference between them means
something.

A comparison is per prompt, because that is the unit somebody reasons about:
*we asked this, and in R32 it said one thing and in R33 it says another*. The
columns are the runs, labelled with what was under test rather than with a
timestamp, since "CATIA V5 R32 SP4 · LEO 2026x" is what a reader needs and
"2026-09-01T14-22" is not.

What counts as "changed" is deliberately not exact string equality. A reply
whose only difference is whitespace, or a trailing full stop, has not changed
in any sense worth a person's attention, and a comparison that cries wolf on
those gets ignored — which makes it worse than no comparison.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from understudy.subject import Subject
from understudy.transcript import load_results

#: Below this, two replies are "the same answer, worded slightly differently".
#: Above it, something worth looking at. Tuned to be quiet rather than clever:
#: the tool's job here is to point at the interesting rows, not to grade them.
SIMILAR_ENOUGH = 0.995

VERDICTS = ("same", "reworded", "changed", "missing", "failed")


def normalise(text: str) -> str:
    """Whitespace and trailing punctuation are not behaviour changes."""
    return re.sub(r"\s+", " ", (text or "").strip()).rstrip(".").lower()


@dataclass(frozen=True)
class Column:
    """One run in the comparison."""

    run_dir: str
    label: str
    subject: Subject
    timestamp: str = ""
    flow: str = ""

    @property
    def heading(self) -> str:
        return self.subject.summary() or self.label


@dataclass
class Row:
    """One prompt, across every run."""

    prompt_id: str
    prompt: str
    responses: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(s not in ("ok", "") for s in self.statuses):
            return "failed"
        present = [r for r in self.responses if r is not None]
        if len(present) < len(self.responses):
            return "missing"
        if len({normalise(r) for r in present}) == 1:
            return "same"
        # Every pair close enough to be the same answer said differently.
        pairs = [
            difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()
            for a, b in zip(present, present[1:])
        ]
        return "reworded" if pairs and min(pairs) >= SIMILAR_ENOUGH else "changed"

    @property
    def interesting(self) -> bool:
        return self.verdict in ("changed", "missing", "failed")

    def diff(self, left: int = 0, right: int = -1) -> list[str]:
        """A word-level diff between two of the columns."""
        a = (self.responses[left] or "").split()
        b = (self.responses[right] or "").split()
        return list(difflib.unified_diff(a, b, lineterm="", n=3))


@dataclass(frozen=True)
class StepView:
    """One step of one run, as the stepper shows it."""

    number: int
    description: str
    status: str = "ok"
    duration_ms: int = 0
    #: Relative to the run directory; made relative to the page when written.
    screenshot: str = ""
    typed: str = ""
    response: str = ""


@dataclass
class Comparison:
    columns: list[Column]
    rows: list[Row]
    #: Flow names seen. More than one means somebody compared unlike things.
    flows: tuple[str, ...] = ()
    #: prompt id -> per-column list of steps, for stepping through both runs
    #: together. The interesting divergence is often visual and several steps
    #: before the answer -- a dialog that opened somewhere else, a field that
    #: did not clear -- and a wall of screenshots does not show that. Two
    #: pictures of the same step, side by side, does.
    steps: dict[str, list[list[StepView]]] = field(default_factory=dict)

    @property
    def changed(self) -> list[Row]:
        return [row for row in self.rows if row.interesting]

    @property
    def mixed_flows(self) -> bool:
        return len(self.flows) > 1

    def counts(self) -> dict[str, int]:
        counts = {verdict: 0 for verdict in VERDICTS}
        for row in self.rows:
            counts[row.verdict] += 1
        return counts

    def headline(self) -> str:
        counts = self.counts()
        if counts["changed"] or counts["failed"] or counts["missing"]:
            parts = [f"{counts[v]} {v}" for v in VERDICTS if counts[v]]
            return ", ".join(parts)
        return f"no change across {len(self.rows)} prompt(s)"


def _column_for(run_dir: Path, results: list[dict[str, Any]]) -> Column:
    subject = Subject.from_config((results[0].get("subject") if results else None) or {})
    return Column(
        run_dir=str(run_dir),
        label=run_dir.name,
        subject=subject,
        timestamp=results[0].get("timestamp", "") if results else "",
        flow=results[0].get("flow", "") if results else "",
    )


def compare(run_dirs: list[Path | str]) -> Comparison:
    """Line up the same prompts across several runs.

    Ordered by the runs as given, so "before, after" is whatever order you
    typed -- guessing from timestamps would be wrong exactly when somebody
    re-runs an old release to check something.
    """
    if len(run_dirs) < 2:
        raise ValueError("comparing needs at least two runs")

    columns: list[Column] = []
    per_run: list[dict[str, dict[str, Any]]] = []
    order: list[str] = []

    for run_dir in run_dirs:
        run_dir = Path(run_dir)
        results = load_results(run_dir)
        columns.append(_column_for(run_dir, results))
        by_prompt = {}
        for result in results:
            key = result["prompt_id"]
            if result.get("repeat_index"):
                key = f"{key} #{result['repeat_index'] + 1}"
            by_prompt[key] = result
            if key not in order:
                order.append(key)
        per_run.append(by_prompt)

    rows = []
    for key in order:
        first = next((run[key] for run in per_run if key in run), {})
        row = Row(prompt_id=key, prompt=first.get("prompt", ""))
        for run in per_run:
            result = run.get(key)
            row.responses.append(None if result is None else (result.get("response") or ""))
            row.statuses.append("" if result is None else result.get("status", ""))
        rows.append(row)

    steps = {}
    for key in order:
        per_column = [_steps_of(run.get(key)) for run in per_run]
        if any(per_column):
            steps[key] = per_column

    return Comparison(
        columns=columns, rows=rows,
        flows=tuple(dict.fromkeys(c.flow for c in columns if c.flow)),
        steps=steps,
    )


def _steps_of(result: dict[str, Any] | None) -> list[StepView]:
    """The user actions of one variant, each with the screenshot taken after
    it -- the same grouping the transcript reads by."""
    if not result:
        return []
    from understudy.transcript import timeline

    views = []
    for entry in timeline(result, {}):
        if not entry.number:
            continue
        views.append(StepView(
            number=entry.number,
            description=entry.description,
            status=entry.status,
            duration_ms=entry.duration_ms,
            screenshot=entry.screenshots[-1] if entry.screenshots else "",
            typed=entry.typed or "",
            response="\n".join(text for _, text in entry.reads),
        ))
    return views
