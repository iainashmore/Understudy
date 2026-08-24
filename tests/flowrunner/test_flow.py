"""Flow file parsing, validation and substitution."""

from __future__ import annotations

import textwrap

import pytest

from flowrunner.flow import Flow, FlowError, load_flow, parse_flow, render_step, substitute

MINIMAL = {
    "version": 1,
    "name": "demo",
    "targets": {"box": {"web": "textarea"}},
    "steps": [{"action": "click", "target": "box"}],
}


def flow(**overrides) -> Flow:
    return parse_flow({**MINIMAL, **overrides})


class TestTargets:
    def test_a_bare_string_is_a_css_selector(self):
        strategies = flow().target_for("box").for_backend("web")
        assert len(strategies) == 1
        assert strategies[0].fields == {"css": "textarea"}

    def test_a_single_mapping_is_one_strategy(self):
        parsed = flow(targets={"box": {"web": {"testid": "prompt"}}})
        assert parsed.target_for("box").for_backend("web")[0].kind == "testid"

    def test_a_list_is_tried_in_order(self):
        """Most stable first: a portalled dialog breaks the CSS path but not the
        role/name lookup."""
        parsed = flow(
            targets={
                "dialog": {
                    "web": [
                        {"testid": "confirm"},
                        {"role": "button", "name": "Confirm send"},
                        {"css": "#inline-slot button"},
                    ]
                }
            },
            steps=[{"action": "click", "target": "dialog"}],
        )
        kinds = [s.kind for s in parsed.target_for("dialog").for_backend("web")]
        assert kinds == ["testid", "role", "css"]

    def test_native_strategies_are_mappings(self):
        parsed = flow(
            targets={"box": {"native": {"control_type": "Edit", "automation_id": "p"}}}
        )
        assert parsed.target_for("box").for_backend("native")[0].fields == {
            "control_type": "Edit",
            "automation_id": "p",
        }

    def test_a_target_can_carry_its_intent(self):
        parsed = flow(targets={"box": {"web": "textarea", "intent": "the message box"}})
        assert parsed.target_for("box").intent == "the message box"

    def test_unknown_strategy_keys_are_rejected(self):
        with pytest.raises(FlowError, match="unknown key 'colour'"):
            flow(targets={"box": {"web": {"colour": "red"}}})

    def test_a_strategy_needs_something_to_match_on(self):
        with pytest.raises(FlowError, match="needs one of"):
            flow(targets={"box": {"web": {"exact": True}}})

    def test_asking_for_an_undefined_backend_is_a_clear_error(self):
        with pytest.raises(FlowError, match="no native strategy"):
            flow().target_for("box").for_backend("native")


class TestValidation:
    def test_a_step_referencing_an_unknown_target_is_rejected(self):
        with pytest.raises(FlowError, match="target 'nope' is not defined"):
            flow(steps=[{"action": "click", "target": "nope"}])

    def test_until_hidden_must_also_be_a_known_target(self):
        with pytest.raises(FlowError, match="until_hidden 'spinner' is not defined"):
            flow(steps=[{"action": "wait_for_stable", "target": "box",
                         "until_hidden": "spinner"}])

    def test_interstitials_must_be_known_targets(self):
        with pytest.raises(FlowError, match="interstitial 'banner'"):
            flow(interstitials=["banner"])

    def test_unknown_actions_are_rejected(self):
        with pytest.raises(FlowError, match="action"):
            flow(steps=[{"action": "hover", "target": "box"}])

    def test_missing_required_parameters_are_rejected(self):
        with pytest.raises(FlowError, match="store_as"):
            flow(steps=[{"action": "read", "target": "box"}])

    def test_typos_in_step_parameters_are_rejected(self):
        # additionalProperties: without this, `stable_ms` silently does nothing
        # and every run waits the default.
        with pytest.raises(FlowError):
            flow(steps=[{"action": "wait_for_stable", "target": "box",
                         "stable_ms": 500}])

    def test_a_backend_check_runs_before_the_run_does(self):
        parsed = flow()
        parsed.validate_for_backend("web")
        with pytest.raises(FlowError, match="no native strategy: box"):
            parsed.validate_for_backend("native")

    def test_unused_targets_do_not_block_a_backend(self):
        """Only what the flow actually touches has to resolve."""
        parsed = flow(
            targets={"box": {"web": "textarea"}, "spare": {"native": {"name": "x"}}}
        )
        parsed.validate_for_backend("web")

    def test_bad_yaml_is_reported_with_the_path(self, tmp_path):
        path = tmp_path / "flow.yaml"
        path.write_text("version: 1\n  bad indent:\n")
        with pytest.raises(FlowError, match="not valid YAML"):
            load_flow(path)

    def test_a_missing_file_is_a_clear_error(self, tmp_path):
        with pytest.raises(FlowError, match="no flow file"):
            load_flow(tmp_path / "absent.yaml")


class TestVariables:
    def test_every_variable_is_discovered(self):
        parsed = flow(
            steps=[
                {"action": "type", "target": "box", "text": "{{prompt}}"},
                {"action": "type", "target": "box", "text": "as {{style}}, {{prompt}}"},
            ]
        )
        assert parsed.variables() == {"prompt", "style"}

    def test_substitution_fills_every_occurrence(self):
        assert substitute("{{a}}-{{b}}-{{a}}", {"a": "1", "b": "2"}) == "1-2-1"

    def test_whitespace_inside_the_braces_is_tolerated(self):
        assert substitute("{{ prompt }}", {"prompt": "hi"}) == "hi"

    def test_an_unknown_variable_is_an_error_not_a_silent_gap(self):
        # A run that goes out with '{{style}}' in the text looks like a real
        # result and is not one.
        with pytest.raises(FlowError, match="no value for"):
            substitute("{{style}}", {"prompt": "hi"})

    def test_render_step_substitutes_only_strings(self):
        parsed = flow(
            steps=[{"action": "type", "target": "box", "text": "{{prompt}}",
                    "delay_ms": 10}]
        )
        rendered = render_step(parsed.steps[0], {"prompt": "hello"})
        assert rendered.params["text"] == "hello"
        assert rendered.params["delay_ms"] == 10
        assert rendered.index == parsed.steps[0].index


class TestDefaultsAndPhases:
    def test_defaults_are_supplied_when_absent(self):
        assert flow().defaults.poll_interval_ms == 250
        assert flow().defaults.stable_for_ms == 1500

    def test_defaults_can_be_overridden(self):
        assert flow(defaults={"poll_interval_ms": 100}).defaults.poll_interval_ms == 100

    def test_reset_steps_are_parsed_and_labelled(self):
        parsed = flow(reset=[{"action": "click", "target": "box"}])
        assert parsed.reset[0].phase == "reset"
        assert parsed.steps[0].phase == "steps"

    def test_steps_are_numbered_from_one(self):
        parsed = flow(steps=[{"action": "click", "target": "box"}] * 3)
        assert [step.index for step in parsed.steps] == [1, 2, 3]


def test_a_realistic_flow_round_trips(tmp_path):
    path = tmp_path / "flow.yaml"
    path.write_text(textwrap.dedent("""
        version: 1
        name: chat-app-basic-flow
        target_app:
          web:
            url: https://example.com/chat
        targets:
          prompt_box:
            intent: the main message input
            web:
              - testid: prompt-input
              - role: textbox
                name: Message
          send_button:
            web:
              - testid: send
              - role: button
                name: Send
          response_area:
            web: "div[data-testid=response]"
        reset:
          - action: click
            target: send_button
        steps:
          - action: type
            target: prompt_box
            text: "{{prompt}}"
          - action: click
            target: send_button
          - action: wait_for_stable
            target: response_area
            stable_for_ms: 800
            timeout_ms: 30000
          - action: capture
            label: after-response
          - action: read
            target: response_area
            store_as: response
    """).strip())

    parsed = load_flow(path)
    assert parsed.name == "chat-app-basic-flow"
    assert parsed.variables() == {"prompt"}
    assert len(parsed.steps) == 5
    assert parsed.source_text
    parsed.validate_for_backend("web")
