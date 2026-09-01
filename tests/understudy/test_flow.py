"""Flow file parsing, validation and substitution."""

from __future__ import annotations

import textwrap

import pytest

from understudy.flow import Flow, FlowError, load_flow, parse_flow, render_step, substitute

MINIMAL = {
    "version": 1,
    "name": "demo",
    "prompts": [{"id": "a", "prompt": "hello"}],
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
        title: Chat app basic flow
        description: A worked example
        prompts:
          - id: baseline
            prompt: Summarise this.
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
    assert parsed.title == "Chat app basic flow"
    assert [dict(entry)["id"] for entry in parsed.embedded_prompts] == ["baseline"]
    assert parsed.variables() == {"prompt"}
    assert len(parsed.steps) == 5
    assert parsed.source_text
    parsed.validate_for_backend("web")


class TestPortablePaths:
    """A flow that hard-codes an absolute path only runs on the machine it was
    written on. A checkout in a different directory -- or a repository that has
    been renamed -- breaks every example that does it.
    """

    def write(self, tmp_path, url):
        flow = tmp_path / "flows" / "demo.yaml"
        flow.parent.mkdir(parents=True, exist_ok=True)
        flow.write_text(
            "version: 1\nname: demo\n"
            "prompts:\n  - id: a\n    prompt: hello\n"
            f'target_app:\n  web:\n    url: "{url}"\n'
            "targets:\n  box:\n    web: \"#box\"\n"
            "steps:\n  - action: click\n    target: box\n"
        )
        return flow

    def test_a_relative_path_resolves_against_the_flow_file(self, tmp_path):
        from understudy.flow import load_flow

        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "app.html").write_text("<p>hi</p>")
        flow = load_flow(self.write(tmp_path, "../fixtures/app.html"))

        url = flow.app_config("web")["url"]
        assert url.startswith("file:///")
        assert url.endswith("/fixtures/app.html")

    def test_the_query_string_survives_the_resolution(self, tmp_path):
        from understudy.flow import load_flow

        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "app.html").write_text("<p>hi</p>")
        flow = load_flow(self.write(tmp_path, "../fixtures/app.html?mode=stream&delay=25"))
        assert flow.app_config("web")["url"].endswith("app.html?mode=stream&delay=25")

    def test_a_real_url_is_left_exactly_as_written(self, tmp_path):
        from understudy.flow import load_flow

        for url in ("https://example.com/app?a=1", "file:///opt/app/index.html"):
            flow = load_flow(self.write(tmp_path, url))
            assert flow.app_config("web")["url"] == url

    def test_the_same_flow_works_from_two_different_directories(self, tmp_path):
        """The point of the exercise: rename the checkout, nothing changes."""
        import shutil

        from understudy.flow import load_flow

        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "app.html").write_text("<p>hi</p>")
        self.write(tmp_path, "../fixtures/app.html")

        renamed = tmp_path.parent / (tmp_path.name + "-renamed")
        shutil.copytree(tmp_path, renamed)

        first = load_flow(tmp_path / "flows" / "demo.yaml").app_config("web")["url"]
        second = load_flow(renamed / "flows" / "demo.yaml").app_config("web")["url"]
        assert first != second, "each resolves to its own checkout"
        assert first.endswith("/fixtures/app.html")
        assert second.endswith("/fixtures/app.html")


class TestExamplesArePortable:
    def test_no_shipped_example_hard_codes_an_absolute_path(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        offenders = [
            path.name for path in sorted((repo / "examples").glob("*.yaml"))
            if "file:///" in path.read_text()
        ]
        assert offenders == []


class TestTheWindowsPathTrap:
    """`url: "C:\\Users\\me\\app.html"` is not a broken URL, it is broken YAML:
    inside double quotes those backslashes are escape sequences. The scanner's
    own message points at a column and says nothing about why."""

    def write(self, tmp_path, url_line):
        flow = tmp_path / "flow.yaml"
        flow.write_text(
            "version: 1\nname: x\n"
            "prompts:\n  - id: a\n    prompt: hello\n"
            "steps:\n  - action: capture\n    label: shot\n"
            f"target_app:\n  web:\n    {url_line}\n")
        return flow

    def test_the_error_names_the_actual_problem(self, tmp_path):
        from understudy.flow import FlowError, load_flow

        with pytest.raises(FlowError) as caught:
            load_flow(self.write(tmp_path, r'url: "file://C:\Users\me\app.html"'))
        message = str(caught.value)
        assert "backslashes are read as escape sequences" in message
        assert r"C:\Users\me\app.html" in message, "it should quote the line"

    def test_single_quotes_are_fine(self, tmp_path):
        from understudy.flow import load_flow

        flow = load_flow(self.write(tmp_path, r"url: 'file:///C:/Users/me/app.html'"))
        assert flow.app_config("web")["url"].endswith("app.html")

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


class TestWhichBackendAFlowDrives:
    """The flow file declares what it drives, so asking again on the command
    line is a question with one right answer -- and defaulting to web turned a
    native-only flow into "these targets have no web strategy"."""

    def _flow(self, tmp_path, target_app):
        from understudy.flow import load_flow

        path = tmp_path / "flow.yaml"
        path.write_text(f"""version: 1
name: picky
{target_app}
targets:
  box:
    native:
      - control_type: Edit
    web:
      - testid: box
prompts:
  - id: one
    prompt: hello
steps:
  - {{action: type, target: box, text: "{{{{prompt}}}}"}}
""")
        return load_flow(path)

    def test_a_flow_with_one_target_needs_no_backend_flag(self, tmp_path):
        from understudy.cli import backend_of

        flow = self._flow(tmp_path, 'target_app:\n  native:\n'
                                    '    window_title_pattern: "*App*"\n')
        assert backend_of(flow) == "native"

    def test_a_flow_that_drives_both_asks(self, tmp_path):
        from understudy.cli import backend_of
        from understudy.flow import FlowError

        flow = self._flow(tmp_path, 'target_app:\n  native:\n'
                                    '    window_title_pattern: "*App*"\n'
                                    '  web:\n    url: "https://example.com/"\n')
        with pytest.raises(FlowError, match="--backend"):
            backend_of(flow)
        with pytest.raises(FlowError, match="native or web"):
            backend_of(flow)
