"""The agent rung: asking a model where a control is.

The request shape is covered here with a stub client, so the live path is
exercised without spending a call. Whether the driver falls back to it, and
whether the answer is cached, belongs with the driver and is tested there --
the browser this used to drive is gone.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from understudy.flow import Target, parse_flow
from understudy.resolvers import (
    ClaudeResolver,
    NullResolver,
    ResolvedBox,
    Resolver,
    ScriptedResolver,
    build,
)
from harness.image import to_png_bytes

pytest.importorskip("playwright", reason="needs playwright")

VIEWPORT = {"width": 1100, "height": 700}
INTENT = "the Measure tool in the toolbar"


def target(label: str, intent: str | None, *strategies) -> Target:
    spec: dict = {"web": list(strategies)}
    if intent is not None:
        spec["intent"] = intent
    return parse_flow({
        "version": 1, "name": "t", "prompts": [{"id": "a", "prompt": "x"}],
        "targets": {label: spec},
        "steps": [{"action": "click", "target": label}],
    }).target_for(label)


class TestResolverProtocol:
    def test_the_null_resolver_finds_nothing(self):
        resolver = NullResolver()
        assert isinstance(resolver, Resolver)
        assert resolver.locate(b"", "anything") is None

    def test_off_is_the_default_build(self):
        assert isinstance(build("off"), NullResolver)

    def test_an_unknown_resolver_is_a_clear_error(self):
        with pytest.raises(KeyError, match="unknown resolver"):
            build("gpt")



class TestClaudeResolver:
    """Request shape and coordinate handling, with a stub client -- no network."""

    def stub(self, report, stop_reason="end_turn"):
        captured = {}

        class Messages:
            def create(self, **kwargs):
                captured.update(kwargs)
                block = SimpleNamespace(type="tool_use", input=report)
                return SimpleNamespace(content=[block], stop_reason=stop_reason)

        return SimpleNamespace(messages=Messages()), captured

    def png(self, width=800, height=600) -> bytes:
        return to_png_bytes(np.zeros((height, width, 3), dtype=np.uint8))

    def test_a_found_control_comes_back_as_a_box(self):
        client, _ = self.stub(
            {"found": True, "x": 10, "y": 20, "width": 30, "height": 40,
             "confidence": 0.9, "reasoning": "orange icon, sixth from left"}
        )
        found = ClaudeResolver(client=client).locate(self.png(), INTENT)

        assert found == ResolvedBox(10, 20, 30, 40, 0.9, "orange icon, sixth from left")
        assert found.centre == (25, 40)

    def test_the_request_carries_the_image_the_intent_and_the_model(self):
        client, captured = self.stub(
            {"found": True, "x": 1, "y": 1, "width": 1, "height": 1,
             "confidence": 0.9, "reasoning": ""}
        )
        ClaudeResolver(client=client).locate(self.png(), INTENT, hint="orange one")

        assert captured["model"] == "claude-opus-5"
        assert captured["tool_choice"] == {"type": "tool", "name": "report_control"}
        assert captured["tools"][0]["strict"] is True
        assert captured["thinking"] == {"type": "adaptive"}
        content = captured["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert INTENT in content[1]["text"] and "orange one" in content[1]["text"]
        assert "800x600 pixels" in content[1]["text"]

    def test_coordinates_are_scaled_back_from_the_downscaled_image(self):
        """A 4K window is resized before sending; the caller needs coordinates
        in the window's own space, not the resized one."""
        client, captured = self.stub(
            {"found": True, "x": 350, "y": 175, "width": 70, "height": 35,
             "confidence": 0.9, "reasoning": ""}
        )
        found = ClaudeResolver(client=client).locate(self.png(2800, 1400), INTENT)

        assert "1400x700 pixels" in captured["messages"][0]["content"][1]["text"]
        assert (found.x, found.y) == (700, 350)
        assert (found.width, found.height) == (140, 70)

    def test_a_small_image_is_sent_unscaled(self):
        client, captured = self.stub(
            {"found": True, "x": 10, "y": 10, "width": 5, "height": 5,
             "confidence": 0.9, "reasoning": ""}
        )
        found = ClaudeResolver(client=client).locate(self.png(400, 300), INTENT)
        assert "400x300 pixels" in captured["messages"][0]["content"][1]["text"]
        assert (found.x, found.y) == (10, 10)

    def test_not_found_is_respected(self):
        client, _ = self.stub(
            {"found": False, "x": 0, "y": 0, "width": 0, "height": 0,
             "confidence": 0.0, "reasoning": "not visible"}
        )
        assert ClaudeResolver(client=client).locate(self.png(), INTENT) is None

    def test_a_low_confidence_answer_is_rejected(self):
        """A wrong click is worse than a clean failure."""
        client, _ = self.stub(
            {"found": True, "x": 1, "y": 1, "width": 1, "height": 1,
             "confidence": 0.4, "reasoning": "might be this one"}
        )
        assert ClaudeResolver(client=client).locate(self.png(), INTENT) is None
        assert ClaudeResolver(client=client, min_confidence=0.3).locate(
            self.png(), INTENT
        ) is not None

    def test_a_refusal_is_not_a_location(self):
        client, _ = self.stub(
            {"found": True, "x": 1, "y": 1, "width": 1, "height": 1,
             "confidence": 0.9, "reasoning": ""},
            stop_reason="refusal",
        )
        assert ClaudeResolver(client=client).locate(self.png(), INTENT) is None

    def test_effort_is_only_sent_when_asked_for(self):
        report = {"found": True, "x": 1, "y": 1, "width": 1, "height": 1,
                  "confidence": 0.9, "reasoning": ""}
        client, captured = self.stub(report)
        ClaudeResolver(client=client).locate(self.png(), INTENT)
        assert "output_config" not in captured

        client, captured = self.stub(report)
        ClaudeResolver(client=client, effort="low").locate(self.png(), INTENT)
        assert captured["output_config"] == {"effort": "low"}
