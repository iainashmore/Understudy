"""What was under test: which application, which assistant, which version.

The tool exists to answer "did the behaviour change?", and an answer is only
comparable against another answer if you know what produced each one. A
transcript that records a reply from LEO but not *which* LEO is evidence of
nothing: six months later the model has been swapped twice and the CAD package
has had three service packs, and the difference you are looking at could be any
of them.

Two places, deliberately.

The flow declares what it is *meant* to run against, because that belongs with
the flow and rarely changes. A run records what it *actually* ran against,
because that changes every time somebody installs a patch -- and having to edit
a YAML file to record a service pack is how the field ends up stale and
lying, which is worse than empty.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

#: Free-form on purpose. "R32 SP4", "2026x FD01", "internal build 4821" -- the
#: version strings this has to hold are whatever the vendor prints in the
#: about box, and no scheme survives contact with all of them.
FIELDS = ("app", "app_version", "model", "model_version", "release", "notes")

#: Free-form tags live alongside the fields above rather than among them: they
#: have no place in "CATIA V5 R33 · LEO", and a list is not a string.
LIST_FIELDS = ("tags",)

LABELS = {
    "app": "Application",
    "app_version": "Application version",
    "model": "Assistant or model",
    "model_version": "Assistant version",
    "release": "Release or build",
    "notes": "Notes",
}


@dataclass(frozen=True)
class Subject:
    app: str = ""
    app_version: str = ""
    model: str = ""
    model_version: str = ""
    release: str = ""
    notes: str = ""
    #: Anything else worth recording about this run: the machine it ran on,
    #: the ticket it belongs to, "after the hotfix". No schema survives what
    #: people want to label a run with, so this one does not try.
    tags: tuple[str, ...] = ()

    @property
    def recorded(self) -> bool:
        return any(getattr(self, field) for field in FIELDS) or bool(self.tags)

    def as_dict(self) -> dict[str, Any]:
        recorded: dict[str, Any] = {
            f: getattr(self, f) for f in FIELDS if getattr(self, f)
        }
        if self.tags:
            recorded["tags"] = list(self.tags)
        return recorded

    def summary(self) -> str:
        """One line, for a table cell or a commit subject."""
        left = " ".join(p for p in (self.app, self.app_version) if p)
        right = " ".join(p for p in (self.model, self.model_version) if p)
        parts = [p for p in (left, right) if p]
        if self.release:
            parts.append(f"({self.release})")
        return " · ".join(parts)

    def labels(self) -> tuple[tuple[str, str], ...]:
        """(field, value) for every structured field recorded, in reading order.

        The same facts as `summary()`, kept apart instead of joined into a
        sentence, so a reader can pick "FD03" out of a header at a glance and
        a list of runs can be filtered by it. Comparing FD03 against FD04 is
        the job; finding the two runs should not be the hard part.

        Notes are left out: they are prose, and prose is not a tag.
        """
        return tuple(
            (field, getattr(self, field))
            for field in FIELDS
            if field != "notes" and getattr(self, field)
        )

    def chips(self) -> tuple[str, ...]:
        """Everything this run is labelled with, structured or not, to show."""
        return tuple([value for _, value in self.labels()] + list(self.tags))

    def merged_with(self, other: "Subject") -> "Subject":
        """`other` wins where it says anything.

        A run's own details override the flow's declaration, because the flow
        says what it was written against and the run says what was actually in
        front of it.
        """
        return replace(self, **{
            field: getattr(other, field)
            for field in FIELDS + LIST_FIELDS if getattr(other, field)
        })

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "Subject":
        config = config or {}
        unknown = sorted(set(config) - set(FIELDS) - set(LIST_FIELDS))
        if unknown:
            raise ValueError(
                f"unknown subject field(s): {', '.join(unknown)}. "
                f"Expected any of: {', '.join(FIELDS + LIST_FIELDS)}"
            )
        raw = config.get("tags") or ()
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",")]
        return cls(
            **{f: str(config.get(f, "") or "").strip() for f in FIELDS},
            tags=tuple(str(t).strip() for t in raw if str(t).strip()),
        )


# -- remembering it between runs ----------------------------------------------
#
# You do not retype the service pack every morning. The last thing recorded for
# a flow is offered again next time, because the common case is running the
# same flow against the same installation repeatedly, and the interesting case
# -- a new release -- is the one where somebody will notice and change it.

import json
from pathlib import Path

DEFAULT_STORE = Path.home() / ".understudy" / "subjects.json"


def load_remembered(flow_name: str, path: Path | str | None = None,
                    any_flow: bool = True) -> Subject:
    """What was recorded last time this flow ran. Empty if never.

    `any_flow` falls back to whatever ran last, for a flow that has never run:
    somebody testing three flows against one release should type the release
    once. It is a guess about a different flow, though, so a caller that has a
    better answer -- the flow's own `subject:` block -- turns it off.
    """
    path = Path(path) if path else DEFAULT_STORE
    if not path.exists():
        return Subject()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Subject()
    entry = (data.get("flows") or {}).get(flow_name) or {}
    if not entry and any_flow:
        entry = data.get("last") or {}
    try:
        return Subject.from_config(entry)
    except ValueError:
        # A field this version does not know about: take what fits rather than
        # refusing to pre-fill anything.
        return Subject(**{f: str(entry.get(f, "") or "") for f in FIELDS})


def resolve_subject(flow_name: str, declared: Subject, given: Subject,
                    path: Path | str | None = None) -> Subject:
    """What was under test, in order of authority.

    Flags beat what this flow recorded last time, which beats what the flow
    declares, which beats a value carried over from whatever ran last. That
    last one is a convenience -- test three flows against one release and type
    the release once -- and it is a guess about a different flow, so it sits
    at the bottom. It used to sit at the top, and a flow declaring
    `app: Fixture chat` was reported as CATIA V5 R33 because that had been
    typed for something else an hour earlier.
    """
    # A flow that says what it tests is not filled in from another flow. The
    # carry-over exists so somebody testing three flows against one release
    # types the release once; blending it into a flow that declares its own
    # produced "Fixture chat R33 · fixture 2027x FD02" -- an app from here, a
    # version from something else, and a sentence describing nothing that
    # exists.
    carried = Subject() if declared.recorded else load_remembered(
        flow_name, path, any_flow=True)
    return (
        carried
        .merged_with(declared)
        .merged_with(load_remembered(flow_name, path, any_flow=False))
        .merged_with(given)
    )


def remember(flow_name: str, subject: Subject,
             path: Path | str | None = None) -> None:
    if not subject.recorded:
        return
    path = Path(path) if path else DEFAULT_STORE
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    flows = data.get("flows") or {}
    flows[flow_name] = subject.as_dict()
    payload = {"flows": flows, "last": subject.as_dict()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
