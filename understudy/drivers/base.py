"""The driver protocol.

Web (Playwright), native Windows (UIAutomation) and -- for CATIA V5 -- COM
automation all implement this. The runner talks to nothing else, which is what
lets a flow move between backends without being rewritten.

Resolution is reported, not just performed. Every call that finds an element
returns which strategy found it, so a run that limped along on a fallback shows
that in its results before the UI drifts far enough to break outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from understudy.flow import Strategy, Target


class DriverError(RuntimeError):
    """The driver could not do what was asked. Recorded as a step status."""


class TargetNotFound(DriverError):
    """No strategy resolved. Carries what was tried, because the next thing the
    human does is fix the flow."""

    def __init__(self, target: Target, backend: str, attempts: list[str]) -> None:
        detail = "\n".join(f"    {line}" for line in attempts)
        super().__init__(
            f"could not resolve target {target.name!r} on {backend}; tried:\n{detail}"
        )
        self.target = target
        self.attempts = attempts


@dataclass(frozen=True)
class Resolution:
    """Which strategy actually found the element, and by what means."""

    target: str
    index: int
    strategy: Strategy | None = None
    note: str | None = None
    #: selector | anchor | learned-anchor | agent. A run that needed the model
    #: is a different kind of result from one that did not.
    via: str = "selector"

    @property
    def used_fallback(self) -> bool:
        """True when the preferred strategy did not work. The early warning
        that the UI has moved."""
        return self.index > 0

    @property
    def used_agent(self) -> bool:
        return self.via == "agent"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "strategy_index": self.index,
            "strategy": self.strategy.describe() if self.strategy else None,
            "used_fallback": self.used_fallback,
            "via": self.via,
            "note": self.note,
        }


@runtime_checkable
class Driver(Protocol):
    backend: str

    def start(self, app_config: dict[str, Any]) -> None: ...
    def stop(self) -> None: ...
    def reset(self) -> None:
        """Level-2 isolation: a fresh context or process. Level-1 in-app reset
        is expressed as flow steps instead, because it is app-specific and
        belongs where a human can edit it."""
        ...

    def click(self, target: Target, timeout_ms: int) -> Resolution: ...
    def type(
        self, target: Target | None, text: str, timeout_ms: int,
        mode: str = "type", clear: bool = True, delay_ms: int = 0,
    ) -> Resolution | None: ...
    def read(self, target: Target, timeout_ms: int) -> tuple[str, Resolution]: ...
    def key(self, keys: str, target: Target | None, timeout_ms: int) -> Resolution | None: ...
    def screenshot(
        self, target: Target | None = None, full_page: bool = False,
        region: dict[str, int] | None = None,
    ) -> bytes: ...
    def exists(self, target: Target, timeout_ms: int = 0) -> bool: ...
    def is_visible(self, target: Target) -> bool: ...
    def wait_for_element(
        self, target: Target, state: str, timeout_ms: int
    ) -> Resolution: ...

    # -- optional -------------------------------------------------------------

    def start_recording(self, path: Any) -> bool:
        """Begin recording to `path`. False when this backend cannot.

        Optional: the runner checks the return value and carries on either way.
        A missing screen recorder is a note in the results, not a lost sweep.
        """
        ...

    def stop_recording(self) -> Any:
        """Finish and return a `Recording`."""
        ...

    def recording_unavailable(self) -> str | None:
        """Why `start_recording` would refuse, or None if it would work.

        Optional, and only consulted after a refusal. A run asked to record
        and silently producing no video is the worst of the three outcomes:
        the reason belongs in the results next to everything else that did
        not go to plan.
        """
        return None
