"""Flow file parsing, validation and substitution."""

from __future__ import annotations

import textwrap

import pytest

from understudy.flow import Flow, FlowError, load_flow, parse_flow, render_step, substitute

MINIMAL = {
    "version": 1,
    "name": "demo",
    "prompts": [{"id": "a", "prompt": "hello"}],
    "targets": {"box": {"native": "Message"}},
    "steps": [{"action": "click", "target": "box"}],
}


def flow(**overrides) -> Flow:
    return parse_flow({**MINIMAL, **overrides})


class TestTargets:
    def test_a_bare_string_is_a_control_name(self):
        strategies = flow().target_for("box").for_backend("native")
        assert len(strategies) == 1
        assert strategies[0].fields == {"name": "Message"}

    def test_a_single_mapping_is_one_strategy(self):
        parsed = flow(targets={"box": {"native": {"automation_id": "prompt"}}})
        assert parsed.target_for("box").for_backend("native")[0].kind == "automation_id"

    def test_a_list_is_tried_in_order(self):
        """Most stable first: an id survives a redesign, a picture of the
        control survives the id being dropped, and nothing survives both."""
        parsed = flow(
            targets={
                "dialog": {
                    "native": [
                        {"automation_id": "confirm"},
                        {"control_type": "Button", "name": "Confirm send"},
                        {"class_name": "Button"},
                    ]
                }
            },
            steps=[{"action": "click", "target": "dialog"}],
        )
        kinds = [s.kind for s in parsed.target_for("dialog").for_backend("native")]
        assert kinds == ["automation_id", "control_type", "class_name"]

    def test_native_strategies_are_mappings(self):
        parsed = flow(
            targets={"box": {"native": {"control_type": "Edit", "automation_id": "p"}}}
        )
        assert parsed.target_for("box").for_backend("native")[0].fields == {
            "control_type": "Edit",
            "automation_id": "p",
        }

    def test_a_target_can_carry_its_intent(self):
        parsed = flow(targets={"box": {"native": "Message", "intent": "the message box"}})
        assert parsed.target_for("box").intent == "the message box"

    def test_unknown_strategy_keys_are_rejected(self):
        with pytest.raises(FlowError, match="unknown key 'colour'"):
            flow(targets={"box": {"native": {"colour": "red"}}})

    def test_a_strategy_needs_something_to_match_on(self):
        with pytest.raises(FlowError, match="needs one of"):
            flow(targets={"box": {"native": {"exact": True}}})

    def test_asking_a_target_for_a_backend_it_does_not_define(self):
        parsed = flow(targets={"box": {"native": "Message"}})
        with pytest.raises(FlowError, match="no web strategy"):
            parsed.target_for("box").for_backend("web")


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

    def test_a_target_with_no_strategy_is_refused_when_the_flow_loads(self):
        """Earlier than it used to be caught: the schema will not accept a
        target that says nothing about how to find it."""
        with pytest.raises(FlowError, match="targets/box"):
            flow(targets={"box": {"intent": "nothing defines me"}})

    def test_unused_targets_do_not_block_a_backend(self):
        """Only what the flow actually touches has to resolve."""
        parsed = flow(
            targets={"box": {"native": "Message"}, "spare": {"native": {"name": "x"}}}
        )
        parsed.validate_for_backend("native")

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
        name: assistant-basic-flow
        title: Assistant basic flow
        description: A worked example
        prompts:
          - id: baseline
            prompt: Summarise this.
        target_app:
          native:
            window_title_pattern: "*Assistant*"
        targets:
          prompt_box:
            intent: the main message input
            native:
              - automation_id: prompt-input
              - control_type: Edit
                name: Message
          send_button:
            native:
              - automation_id: send
              - control_type: Button
                name: Send
          response:
            intent: where the reply appears
            native:
              - control_type: Text
                name: Response
        defaults:
          timeout_ms: 8000
        steps:
          - action: click
            target: prompt_box
          - action: type
            target: prompt_box
            text: "{{prompt}}"
          - action: click
            target: send_button
          - action: read
            target: response
            store_as: response
    """).strip())

    parsed = load_flow(path)
    assert parsed.name == "assistant-basic-flow"
    assert parsed.defaults.timeout_ms == 8000
    assert parsed.variables() == {"prompt"}
    assert [step.action for step in parsed.steps] == [
        "click", "type", "click", "read"]
    assert parsed.target_for("prompt_box").intent == "the main message input"
    parsed.validate_for_backend("native")


class TestExamplesArePortable:
    def test_no_shipped_example_hard_codes_a_machine_path(self):
        """An absolute path baked into an example is an example that only runs
        on the machine it was written on."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        offenders = [
            path.name for path in sorted((repo / "examples").glob("*.yaml"))
            if "C:\\" in path.read_text() or "file:///" in path.read_text()
        ]
        assert offenders == []


class TestTheWindowsPathTrap:
    """`executable: "C:\\Program Files\\app.exe"` is not a broken path, it is
    broken YAML: inside double quotes those backslashes are escape sequences.
    The scanner's own message points at a column and says nothing about why --
    and every flow this tool writes is written on Windows."""

    def write(self, tmp_path, line):
        flow = tmp_path / "flow.yaml"
        flow.write_text(
            "version: 1\nname: x\n"
            "prompts:\n  - id: a\n    prompt: hello\n"
            "steps:\n  - action: capture\n    label: shot\n"
            f"target_app:\n  native:\n    window_title_pattern: \"*x*\"\n"
            f"    {line}\n")
        return flow

    def test_the_error_names_the_actual_problem(self, tmp_path):
        from understudy.flow import FlowError, load_flow

        with pytest.raises(FlowError) as caught:
            load_flow(self.write(tmp_path, r'executable: "C:\Users\me\app.exe"'))
        message = str(caught.value)
        assert "backslashes are read as escape sequences" in message
        assert r"C:\Users\me\app.exe" in message, "it should quote the line"

    def test_single_quotes_are_fine(self, tmp_path):
        from understudy.flow import load_flow

        flow = load_flow(self.write(tmp_path, r"executable: 'C:\Users\me\app.exe'"))
        assert flow.app_config()["executable"].endswith("app.exe")

    def test_an_unrelated_yaml_error_gets_no_spurious_hint(self, tmp_path):
        from understudy.flow import FlowError, load_flow

        flow = tmp_path / "flow.yaml"
        flow.write_text("version: 1\nname: x\n  bad: indentation\n")
        with pytest.raises(FlowError) as caught:
            load_flow(flow)
        assert "backslashes" not in str(caught.value)


def test_a_native_target_can_name_the_process_that_owns_the_window(tmp_path):
    """3DEXPERIENCE runs as several processes owning windows of the same name,
    so the title alone cannot say which one the flow means."""
    from understudy.flow import load_flow

    path = tmp_path / "flow.yaml"
    path.write_text("""version: 1
name: picky
target_app:
  native:
    window_title_pattern: "*3DEXPERIENCE*"
    process: "CATIA.exe"
targets:
  box:
    native:
      - control_type: Edit
prompts:
  - id: one
    prompt: hello
steps:
  - {action: type, target: box, text: "{{prompt}}"}
""")
    flow = load_flow(path)
    assert flow.app_config("native")["process"] == "CATIA.exe"


def test_a_schema_failure_carries_every_problem_not_only_the_first(tmp_path):
    """One missing key usually means the block it belonged to is missing too.
    Being told about them one run at a time is four rounds of edit-and-retry."""
    from understudy.flow import FlowError, load_flow

    path = tmp_path / "flow.yaml"
    path.write_text("version: 1\nname: x\nsteps: []\n")
    with pytest.raises(FlowError) as raised:
        load_flow(path)

    assert len(raised.value.problems) > 1
    assert any("prompts" in problem for problem in raised.value.problems)
    assert any("steps" in problem for problem in raised.value.problems)
    # The message itself stays short; the list is for whoever wants it.
    assert "more problem(s)" in str(raised.value)


def test_an_ordinary_flow_error_still_reads_as_one_problem(tmp_path):
    from understudy.flow import FlowError, load_flow

    with pytest.raises(FlowError) as raised:
        load_flow(tmp_path / "missing.yaml")
    assert raised.value.problems == [str(raised.value)]
