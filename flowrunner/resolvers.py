"""Agent-assisted target resolution.

The last rung of the ladder, below visual anchoring: when no selector matches
and no anchor is found, ask a vision model where the control is, guided by the
target's plain-language `intent`.

Three constraints shape this, and they matter more than the model call itself:

  * **It is off by default.** The tool's value is that only the prompt varies
    between runs. A model choosing where to click introduces variance on top of
    the variance being measured, and a sweep where the agent improvised
    differently for one variant is not a comparison any more.
  * **What it finds is cached as an anchor.** The agent resolves once; the crop
    it found becomes a deterministic anchor for every subsequent run. Resilience
    without the per-run non-determinism.
  * **Every agent resolution is flagged.** A run that needed the model is a
    different kind of result from one that did not, and the results say so.

Screenshots are downscaled before being sent and coordinates scaled back, so a
4K application window returns coordinates in the window's own space rather than
in whatever the API happened to resize it to.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np
from PIL import Image

from harness.image import load_rgb, to_png_bytes

#: Long edge sent to the model. Large enough to keep small UI text legible,
#: small enough to stay cheap.
MAX_EDGE = 1400
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MIN_CONFIDENCE = 0.7

LOCATE_TOOL: dict[str, Any] = {
    "name": "report_control",
    "description": (
        "Report where the requested control is in the screenshot, or that it "
        "is not visible."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["found", "x", "y", "width", "height", "confidence", "reasoning"],
        "properties": {
            "found": {
                "type": "boolean",
                "description": "False if the control is not visible in this screenshot.",
            },
            "x": {"type": "integer", "description": "Left edge in image pixels; 0 if not found."},
            "y": {"type": "integer", "description": "Top edge in image pixels; 0 if not found."},
            "width": {"type": "integer", "description": "Width in pixels; 0 if not found."},
            "height": {"type": "integer", "description": "Height in pixels; 0 if not found."},
            "confidence": {
                "type": "number",
                "description": "0 to 1. Be honest: a wrong click is worse than admitting doubt.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence on how the control was identified.",
            },
        },
    },
}

SYSTEM = (
    "You locate controls in screenshots of desktop and web applications.\n"
    "You are given an image and a description of one control. Report its "
    "bounding box in pixel coordinates of the image exactly as supplied, with "
    "(0, 0) at the top-left.\n"
    "Report the control itself, not its label or its container, and give the "
    "box a human would click.\n"
    "If the control is not visible, set found to false rather than guessing. A "
    "wrong location causes a click on the wrong thing, which is worse than "
    "reporting nothing."
)


@dataclass(frozen=True)
class ResolvedBox:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    reasoning: str = ""

    @property
    def centre(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def as_region(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@runtime_checkable
class Resolver(Protocol):
    name: str
    calls: int

    def locate(
        self, screenshot: bytes, intent: str, hint: str | None = None
    ) -> ResolvedBox | None: ...


class NullResolver:
    """Refuses, with an explanation. The default, so nothing reaches a model by
    accident."""

    name = "none"

    def __init__(self) -> None:
        self.calls = 0

    def locate(self, screenshot: bytes, intent: str, hint: str | None = None) -> None:
        self.calls += 1
        return None


class ScriptedResolver:
    """Test double: returns boxes from a script, keyed by intent.

    The whole agent path can be exercised without spending a call, which is the
    same reason the abstraction harness built a mock agent before a real one.
    """

    name = "scripted"

    def __init__(self, answers: dict[str, ResolvedBox | None]) -> None:
        self.answers = answers
        self.calls = 0
        self.seen: list[tuple[str, str | None]] = []

    def locate(
        self, screenshot: bytes, intent: str, hint: str | None = None
    ) -> ResolvedBox | None:
        self.calls += 1
        self.seen.append((intent, hint))
        return self.answers.get(intent)


def _downscale(screenshot: bytes) -> tuple[bytes, float]:
    """Return the image to send and the factor to scale coordinates back by."""
    pixels = load_rgb(screenshot)
    height, width = pixels.shape[:2]
    longest = max(height, width)
    if longest <= MAX_EDGE:
        return screenshot, 1.0

    scale = MAX_EDGE / longest
    resized = Image.fromarray(pixels, mode="RGB").resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS
    )
    return to_png_bytes(np.asarray(resized, dtype=np.uint8)), scale


class ClaudeResolver:
    """Locates a control with a vision model."""

    name = "claude"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        client: Any = None,
        effort: str | None = None,
    ) -> None:
        self.model = model
        self.min_confidence = min_confidence
        self.effort = effort
        self.calls = 0
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def locate(
        self, screenshot: bytes, intent: str, hint: str | None = None
    ) -> ResolvedBox | None:
        self.calls += 1
        image, scale = _downscale(screenshot)
        with Image.open(io.BytesIO(image)) as picture:
            width, height = picture.size

        description = intent if not hint else f"{intent}\nAdditional detail: {hint}"
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM,
            "thinking": {"type": "adaptive"},
            "tools": [LOCATE_TOOL],
            "tool_choice": {"type": "tool", "name": "report_control"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(image).decode("utf-8"),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"The image is {width}x{height} pixels.\n"
                                f"Find this control:\n{description}"
                            ),
                        },
                    ],
                }
            ],
        }
        if self.effort:
            request["output_config"] = {"effort": self.effort}

        response = self.client.messages.create(**request)
        if getattr(response, "stop_reason", None) == "refusal":
            return None

        report = next(
            (block.input for block in response.content if block.type == "tool_use"),
            None,
        )
        if not report or not report.get("found"):
            return None
        if float(report.get("confidence", 0)) < self.min_confidence:
            return None

        back = 1.0 / scale
        return ResolvedBox(
            x=int(round(report["x"] * back)),
            y=int(round(report["y"] * back)),
            width=max(1, int(round(report["width"] * back))),
            height=max(1, int(round(report["height"] * back))),
            confidence=float(report["confidence"]),
            reasoning=str(report.get("reasoning", "")),
        )


def build(kind: str, **options: Any) -> Resolver:
    if kind in ("off", "none"):
        return NullResolver()
    if kind == "claude":
        return ClaudeResolver(**options)
    raise KeyError(f"unknown resolver {kind!r}; expected 'off' or 'claude'")


def credentials_available() -> bool:
    """Whether a live resolver could run. An unset ANTHROPIC_API_KEY does not
    mean there are no credentials -- the SDK also reads an `ant auth login`
    profile."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    from pathlib import Path

    return (Path.home() / ".config" / "anthropic").exists()
