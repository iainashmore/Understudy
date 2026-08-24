"""The agent fallback rung.

Exercised end to end with a scripted resolver, so the whole path runs without
spending an API call -- the same reason the abstraction harness built a mock
agent before a real one. The live model path is covered separately by request
shape, with a stub client.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from flowrunner.drivers.base import TargetNotFound
from flowrunner.drivers.web import WebDriver
from flowrunner.flow import Target, parse_flow
from flowrunner.resolvers import (
    ClaudeResolver,
    NullResolver,
    ResolvedBox,
    Resolver,
    ScriptedResolver,
    build,
)
from harness.image import to_png_bytes

pytest.importorskip("playwright", reason="needs playwright")

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures" / "cad_app" / "index.html")
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


def make_driver(tmp_path, resolver, mode):
    driver = WebDriver(
        resolver=resolver, agent_mode=mode, learned_dir=str(tmp_path / "learned")
    )
    driver.start({"url": f"file://{FIXTURE}?controls=unlabelled&viewport=static",
                  "viewport": VIEWPORT})
    return driver


def box_of(driver, selector) -> ResolvedBox:
    box = driver.page.locator(selector).bounding_box()
    return ResolvedBox(
        x=int(box["x"]), y=int(box["y"]),
        width=int(box["width"]), height=int(box["height"]),
        confidence=0.95, reasoning="the sixth toolbar control",
    )


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


class TestAgentModes:
    def test_off_never_asks_the_model(self, tmp_path):
        """The default. Nothing reaches a model by accident."""
        resolver = ScriptedResolver({})
        driver = make_driver(tmp_path, resolver, "off")
        try:
            with pytest.raises(TargetNotFound, match="agent resolution is off"):
                driver.resolve(target("measure", INTENT, {"agent": True}), 300)
        finally:
            driver.stop()
        assert resolver.calls == 0

    def test_fallback_does_not_ask_when_a_selector_works(self, tmp_path):
        """The agent costs money and introduces variance. It is a last resort,
        not a first one."""
        resolver = ScriptedResolver({})
        driver = make_driver(tmp_path, resolver, "fallback")
        try:
            _, resolution = driver.resolve(
                target("measure", INTENT,
                       {"css": ".tool[data-index='5']"}, {"agent": True}),
                2000,
            )
        finally:
            driver.stop()

        assert resolution.via == "selector"
        assert resolver.calls == 0

    def test_fallback_asks_when_everything_else_fails(self, tmp_path):
        driver = make_driver(tmp_path, ScriptedResolver({}), "fallback")
        try:
            resolver = ScriptedResolver({INTENT: box_of(driver, ".tool[data-index='5']")})
            driver.resolver = resolver
            handle, resolution = driver.resolve(
                target("measure", INTENT,
                       {"css": ".does-not-exist"}, {"agent": True}),
                1000,
            )
            expected = driver.page.locator(".tool[data-index='5']").bounding_box()
            assert abs(handle.point[0] - (expected["x"] + expected["width"] / 2)) <= 2
        finally:
            driver.stop()

        assert resolver.calls == 1
        assert resolution.via == "agent"
        assert "confidence 0.95" in resolution.note

    def test_only_ignores_deterministic_strategies_entirely(self, tmp_path):
        """This mode measures what the agent alone can do, so a working
        selector must not quietly rescue it."""
        driver = make_driver(tmp_path, ScriptedResolver({}), "only")
        try:
            resolver = ScriptedResolver({INTENT: box_of(driver, ".tool[data-index='5']")})
            driver.resolver = resolver
            _, resolution = driver.resolve(
                target("measure", INTENT,
                       {"css": ".tool[data-index='5']"}, {"agent": True}),
                2000,
            )
        finally:
            driver.stop()

        assert resolution.via == "agent"
        assert resolver.calls == 1, "the working selector should have been ignored"

    def test_only_fails_clearly_with_nothing_to_guide_it(self, tmp_path):
        driver = make_driver(tmp_path, ScriptedResolver({}), "only")
        try:
            with pytest.raises(TargetNotFound, match="no intent to guide"):
                driver.resolve(target("mystery", None, {"css": ".tool"}), 300)
        finally:
            driver.stop()

    def test_an_invalid_mode_is_rejected(self):
        with pytest.raises(ValueError, match="agent_mode must be one of"):
            WebDriver(agent_mode="sometimes")


class TestCaching:
    """The property that keeps agent assistance from destroying comparability:
    the model is asked once, and every run after that is deterministic."""

    def test_the_agent_is_asked_once_and_cached_thereafter(self, tmp_path):
        driver = make_driver(tmp_path, ScriptedResolver({}), "fallback")
        try:
            resolver = ScriptedResolver({INTENT: box_of(driver, ".tool[data-index='5']")})
            driver.resolver = resolver
            spec = target("measure", INTENT, {"css": ".missing"}, {"agent": True})

            _, first = driver.resolve(spec, 500)
            _, second = driver.resolve(spec, 500)
            _, third = driver.resolve(spec, 500)
        finally:
            driver.stop()

        assert resolver.calls == 1, "the model should have been consulted once"
        assert first.via == "agent"
        assert second.via == "learned-anchor"
        assert third.via == "learned-anchor"

    def test_the_learned_anchor_is_written_where_it_can_be_inspected(self, tmp_path):
        driver = make_driver(tmp_path, ScriptedResolver({}), "fallback")
        try:
            driver.resolver = ScriptedResolver(
                {INTENT: box_of(driver, ".tool[data-index='5']")}
            )
            driver.resolve(target("measure", INTENT, {"agent": True}), 500)
        finally:
            driver.stop()

        anchor = tmp_path / "learned" / "measure.png"
        assert anchor.stat().st_size > 0
        index = (tmp_path / "learned" / "index.json").read_text()
        assert "measure" in index and "sixth toolbar control" in index

    def test_a_cached_run_works_with_the_agent_switched_back_off(self, tmp_path):
        """Learn once with the agent on, then run deterministically forever."""
        learning = make_driver(tmp_path, ScriptedResolver({}), "fallback")
        try:
            learning.resolver = ScriptedResolver(
                {INTENT: box_of(learning, ".tool[data-index='5']")}
            )
            learning.resolve(target("measure", INTENT, {"agent": True}), 500)
        finally:
            learning.stop()

        offline = make_driver(tmp_path, NullResolver(), "off")
        try:
            _, resolution = offline.resolve(target("measure", INTENT, {"agent": True}), 500)
        finally:
            offline.stop()

        assert resolution.via == "learned-anchor"

    def test_a_stale_anchor_is_discarded_and_the_agent_asked_again(self, tmp_path):
        """A cached anchor that stops matching must not be carried forever."""
        (tmp_path / "learned").mkdir(parents=True)
        junk = np.random.default_rng(0).integers(0, 255, (20, 20, 3), dtype=np.uint8)
        (tmp_path / "learned" / "measure.png").write_bytes(to_png_bytes(junk))

        driver = make_driver(tmp_path, ScriptedResolver({}), "fallback")
        try:
            resolver = ScriptedResolver({INTENT: box_of(driver, ".tool[data-index='5']")})
            driver.resolver = resolver
            _, resolution = driver.resolve(target("measure", INTENT, {"agent": True}), 500)
        finally:
            driver.stop()

        assert resolver.calls == 1
        assert resolution.via == "agent"


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
