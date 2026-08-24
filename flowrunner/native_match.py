"""Matching UIAutomation elements against a target's strategies.

This is the half of the native driver that can be written and tested without
Windows. The driver proper walks the UIA tree and turns each element into an
`ElementDescriptor`; everything after that -- deciding which element a strategy
means, and what to do when several match -- happens here, against plain data.

Splitting it this way is deliberate. The pywinauto layer cannot be exercised
until there is a machine with CATIA on it, and debugging matching logic and COM
plumbing at the same time, on a deadline, is how you lose a day. Only the thin
adapter is left unexercised.

The rules match the web driver's on purpose, because a flow that behaves
differently per backend is worse than no flow at all:

  * strategies are tried in order, most stable first;
  * ambiguity is not resolution -- a strategy matching several elements is
    skipped rather than silently taking the first;
  * `nth` makes a deliberate choice explicit;
  * name matching is case-insensitive and whitespace-normalised by default,
    exact only when asked. Playwright gives the web layer that for free; here
    it has to be written down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from flowrunner.flow import Strategy, Target

#: Win32 menu and button text carries baggage that has nothing to do with
#: identity: "&File" is File with an Alt-F mnemonic, "Save\tCtrl+S" shows an
#: accelerator, "Properties..." opens a dialog. A flow should say `name:
#: Properties` and still match all three.
_MNEMONIC = re.compile(r"&(.)")
_TRAILING_ELLIPSIS = re.compile(r"\s*(\.{3}|…)$")


def normalise_name(value: Any) -> str:
    """Fold a UIA Name down to what a human would call the control."""
    text = "" if value is None else str(value)
    text = text.split("\t")[0]                       # drop the accelerator
    text = _MNEMONIC.sub(lambda m: m.group(1), text)  # &File -> File, && -> &
    text = _TRAILING_ELLIPSIS.sub("", text)
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class ElementDescriptor:
    """One element, as the driver saw it. Plain data, so it can be built in a
    test as easily as from a UIA tree."""

    control_type: str = ""
    automation_id: str = ""
    name: str = ""
    class_name: str = ""
    depth: int = 0
    #: Control types from the root down to (not including) this element. Used
    #: by `path`, which is how you disambiguate the same-looking control in two
    #: different panes.
    ancestors: tuple[str, ...] = ()
    enabled: bool = True
    visible: bool = True
    bounds: tuple[int, int, int, int] | None = None
    #: The live wrapper the driver will act on. Never compared -- two elements
    #: are the same because their identity matches, not their COM pointer.
    handle: Any = field(default=None, compare=False, repr=False)

    def describe(self) -> str:
        parts = [self.control_type or "?"]
        if self.automation_id:
            parts.append(f"id={self.automation_id!r}")
        if self.name:
            parts.append(f"name={self.name!r}")
        if self.class_name:
            parts.append(f"class={self.class_name!r}")
        return " ".join(parts)


@dataclass(frozen=True)
class NativeResolution:
    index: int
    element: ElementDescriptor
    note: str | None = None


class NoMatch(LookupError):
    """Nothing resolved. Carries what was tried, because the next thing a human
    does is fix the flow."""

    def __init__(self, target_name: str, attempts: list[str]) -> None:
        detail = "\n".join(f"    {line}" for line in attempts)
        super().__init__(
            f"could not resolve target {target_name!r} on native; tried:\n{detail}"
        )
        self.attempts = attempts


def _path_matches(wanted: list[str], ancestors: tuple[str, ...]) -> bool:
    """`path` is a subsequence, not a full chain.

    A real tree is full of anonymous wrappers, and a flow that had to name every
    one of them would break the first time a layout gained a container. Saying
    `path: [Window, Pane]` means "inside a Pane, inside a Window", not "exactly
    two levels".
    """
    remaining = list(ancestors)
    for step in wanted:
        target = normalise_name(step)
        while remaining:
            candidate = remaining.pop(0)
            if normalise_name(candidate) == target:
                break
        else:
            return False
    return True


def matches(element: ElementDescriptor, strategy: Strategy) -> bool:
    """Does this element satisfy every field of this strategy?"""
    fields = strategy.fields
    exact = bool(fields.get("exact", False))

    if "automation_id" in fields:
        # An AutomationId is a programmer-chosen identifier: compared exactly,
        # case and all. Folding it would merge controls the developer
        # deliberately kept apart.
        if element.automation_id != str(fields["automation_id"]):
            return False

    if "control_type" in fields:
        if normalise_name(element.control_type) != normalise_name(fields["control_type"]):
            return False

    if "class_name" in fields:
        if normalise_name(element.class_name) != normalise_name(fields["class_name"]):
            return False

    if "name" in fields:
        wanted = normalise_name(fields["name"])
        actual = normalise_name(element.name)
        if not (actual == wanted if exact else wanted in actual):
            return False

    if "path" in fields:
        wanted_path = fields["path"]
        if not isinstance(wanted_path, (list, tuple)):
            return False
        if not _path_matches(list(wanted_path), element.ancestors):
            return False

    return True


def candidates_for(
    elements: list[ElementDescriptor], strategy: Strategy
) -> list[ElementDescriptor]:
    return [element for element in elements if matches(element, strategy)]


def resolve(
    elements: list[ElementDescriptor], target: Target, backend: str = "native"
) -> NativeResolution:
    """First strategy that identifies exactly one element wins."""
    strategies = target.for_backend(backend)
    attempts: list[str] = []

    for index, strategy in enumerate(strategies):
        if "image" in strategy.fields or "agent" in strategy.fields:
            # Handled by the driver: one needs a screenshot, the other a model.
            attempts.append(f"{strategy.describe()} -> not a tree strategy")
            continue

        found = candidates_for(elements, strategy)
        if not found:
            attempts.append(f"{strategy.describe()} -> no match")
            continue

        if "nth" in strategy.fields:
            position = int(strategy.fields["nth"])
            if not -len(found) <= position < len(found):
                attempts.append(
                    f"{strategy.describe()} -> nth={position} but only "
                    f"{len(found)} match(es)"
                )
                continue
            return NativeResolution(
                index, found[position], f"nth={position} of {len(found)}"
            )

        if len(found) == 1:
            return NativeResolution(index, found[0])

        # Several matches. Prefer the one a user could actually interact with,
        # if that settles it; otherwise move on rather than guess.
        usable = [e for e in found if e.visible and e.enabled]
        if len(usable) == 1:
            return NativeResolution(
                index, usable[0], f"{len(found)} matches, one usable"
            )
        attempts.append(f"{strategy.describe()} -> {len(found)} matches, ambiguous")

    raise NoMatch(target.name, attempts)


def rank_by_stability(elements: list[ElementDescriptor]) -> list[dict[str, Any]]:
    """Suggest strategies for an element, most stable first.

    This is what a recorder emits: rather than one selector, several, so the
    flow has somewhere to fall back to when the UI drifts. Kept here because it
    is the inverse of the matching rules and has to stay consistent with them.
    """
    suggestions: list[dict[str, Any]] = []
    for element in elements:
        ranked: list[dict[str, Any]] = []
        if element.automation_id:
            ranked.append({"automation_id": element.automation_id})
        if element.name and element.control_type:
            ranked.append({
                "control_type": element.control_type,
                "name": element.name,
            })
        if element.name:
            ranked.append({"name": element.name})
        if element.control_type and element.ancestors:
            ranked.append({
                "control_type": element.control_type,
                "path": list(element.ancestors[-2:]),
            })
        if element.class_name and element.control_type:
            ranked.append({
                "control_type": element.control_type,
                "class_name": element.class_name,
            })
        suggestions.append({"element": element.describe(), "strategies": ranked})
    return suggestions


def unique_strategies(
    elements: list[ElementDescriptor], element: ElementDescriptor
) -> list[dict[str, Any]]:
    """Of the suggestions for one element, only those that actually identify it
    uniquely in this tree.

    A recorder that emits a strategy matching six controls has recorded a bug.
    """
    from flowrunner.flow import Strategy as _Strategy

    proposed = rank_by_stability([element])[0]["strategies"]
    keep = []
    for fields in proposed:
        found = candidates_for(elements, _Strategy(backend="native", fields=dict(fields)))
        if len(found) == 1 and found[0] == element:
            keep.append(fields)
    return keep
