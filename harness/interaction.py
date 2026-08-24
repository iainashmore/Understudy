"""The vocabulary an agent and an environment exchange.

Deliberately layer-neutral. A UI click, an API call and a kernel pixel write are
all an operation name plus arguments, and every layer answers with the same kind
of observation. If this vocabulary ever grows a field that only one layer can
fill, the comparison between layers has sprung a leak.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

DONE = "done"


class Layer(str, Enum):
    """The three abstraction levels under comparison."""

    UI = "ui"
    API = "api"
    KERNEL = "kernel"


@dataclass(frozen=True)
class Action:
    """One thing the agent wants to do.

    Layers differ in what names they accept, not in the shape of the request.
    `done` is understood everywhere -- the agent needs a way to declare
    completion that does not depend on which interface it was handed.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("action name must be a non-empty string")
        if not isinstance(self.args, dict):
            raise TypeError("action args must be a dict")
        for key in self.args:
            if not isinstance(key, str):
                raise TypeError(f"action argument names must be strings, got {key!r}")

    @classmethod
    def done(cls) -> "Action":
        return cls(DONE)

    @property
    def is_done(self) -> bool:
        return self.name == DONE

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": dict(self.args)}

    def __str__(self) -> str:
        rendered = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        return f"{self.name}({rendered})"


@dataclass(frozen=True)
class Observation:
    """What comes back after an action.

    Every layer returns the rendered canvas, not just the UI one. Showing the
    picture to one layer and not the others would measure sighted-versus-blind
    rather than abstraction level, which is the confound this harness is most
    at risk of.
    """

    text: str = ""
    image: bytes | None = None
    error: str | None = None

    @property
    def has_image(self) -> bool:
        return self.image is not None

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def image_digest(self) -> str | None:
        """Lets a trace record that the canvas changed without storing it
        twice."""
        if self.image is None:
            return None
        return hashlib.sha256(self.image).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        """Trace-safe: the image is summarised, never inlined."""
        return {
            "text": self.text,
            "error": self.error,
            "image_bytes": len(self.image) if self.image else 0,
            "image_digest": self.image_digest,
        }


@dataclass(frozen=True)
class Parameter:
    """One argument of one operation."""

    name: str
    type: str
    description: str
    required: bool = True

    def signature(self) -> str:
        return f"{self.name}: {self.type}" + ("" if self.required else " = null")


@dataclass(frozen=True)
class Operation:
    """One thing a layer lets the agent do, described the way that layer would
    describe it."""

    name: str
    summary: str
    parameters: tuple[Parameter, ...] = ()

    def signature(self) -> str:
        return f"{self.name}({', '.join(p.signature() for p in self.parameters)})"

    def describe(self) -> str:
        lines = [f"{self.signature()}", f"    {self.summary}"]
        lines.extend(
            f"    - {parameter.name}: {parameter.description}"
            for parameter in self.parameters
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class Interface:
    """What a layer offers, handed to the agent once at the start of a run.

    The preamble describes the interface and nothing else. It must never mention
    the task: the moment one layer's preamble contains a hint the others lack,
    the success-rate comparison stops being about abstraction.
    """

    layer: Layer
    preamble: str
    operations: tuple[Operation, ...] = ()

    def __post_init__(self) -> None:
        names = [operation.name for operation in self.operations]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate operation names: {sorted(duplicates)}")

    @property
    def operation_names(self) -> frozenset[str]:
        return frozenset(operation.name for operation in self.operations) | {DONE}

    def accepts(self, action: Action) -> bool:
        return action.name in self.operation_names

    def describe(self) -> str:
        """The text an agent is shown. Built the same way for every layer so the
        framing itself does not become a variable."""
        blocks = [self.preamble.strip(), "Available operations:"]
        blocks.extend(operation.describe() for operation in self.operations)
        blocks.append(
            f"{DONE}()\n    Declare the task finished. Available at every layer."
        )
        return "\n\n".join(blocks)
