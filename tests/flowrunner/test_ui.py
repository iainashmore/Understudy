"""The local UI: file access, validation, and the run lifecycle.

The API is tested directly rather than over HTTP wherever possible -- it is the
same code the handler calls, and it runs in milliseconds. The HTTP layer gets
its own smaller pass for routing, static serving and the path guard.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from flowrunner.ui.server import Api, Workspace, WorkspaceError, serve

REPO = Path(__file__).resolve().parents[2]
FIXTURE = (REPO / "fixtures" / "chat_app" / "index.html").resolve()

FLOW = f"""version: 1
name: ui-test-flow
target_app:
  web:
    url: "file://{FIXTURE}?mode=instant&dialog=none"
targets:
  prompt_box:
    web: "textarea[data-testid=prompt-input]"
  send_button:
    web: "button[data-testid=send]"
  response_area:
    web: "div[data-testid=response]"
steps:
  - action: type
    target: prompt_box
    text: "{{{{prompt}}}}"
  - action: click
    target: send_button
  - action: capture
    label: after
  - action: read
    target: response_area
    store_as: response
"""
PROMPTS = "- id: alpha\n  prompt: first prompt\n"


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "flow.yaml").write_text(FLOW)
    (tmp_path / "prompts.yaml").write_text(PROMPTS)
    return tmp_path


@pytest.fixture
def api(workspace):
    return Api(Workspace(workspace))


class TestFiles:
    def test_flows_and_prompts_are_told_apart(self, api):
        listing = api.list_files()
        assert listing["flows"] == ["flow.yaml"]
        assert listing["prompts"] == ["prompts.yaml"]

    def test_a_file_can_be_opened(self, api):
        assert "ui-test-flow" in api.read_file("flow.yaml")["text"]

    def test_saving_writes_through(self, api, workspace):
        api.write_file("prompts.yaml", "- id: b\n  prompt: changed\n")
        assert "changed" in (workspace / "prompts.yaml").read_text()

    def test_save_as_creates_a_new_file_and_directories(self, api, workspace):
        api.write_file("variants/short.yaml", "- id: s\n  prompt: short\n")
        assert (workspace / "variants" / "short.yaml").exists()
        assert "variants/short.yaml" in api.list_files()["prompts"]

    def test_run_output_is_not_offered_as_source(self, api, workspace):
        (workspace / "runs" / "old").mkdir(parents=True)
        (workspace / "runs" / "old" / "flow.yaml").write_text(FLOW)
        assert api.list_files()["flows"] == ["flow.yaml"]

    def test_reading_outside_the_workspace_is_refused(self, api):
        """The UI gets pointed at a work machine; it has no business reading
        outside the folder it was given."""
        for attempt in ("../secrets.txt", "../../etc/passwd"):
            with pytest.raises(WorkspaceError, match="outside the workspace"):
                api.read_file(attempt)

    def test_writing_outside_the_workspace_is_refused(self, api):
        with pytest.raises(WorkspaceError, match="outside the workspace"):
            api.write_file("../escaped.yaml", "x")

    def test_a_missing_file_is_a_clear_error(self, api):
        with pytest.raises(WorkspaceError, match="no such file"):
            api.read_file("absent.yaml")


class TestValidation:
    def test_a_good_pair_validates_with_a_summary(self, api):
        result = api.validate("flow.yaml", "prompts.yaml")
        assert result["ok"] and result["problems"] == []
        assert result["summary"]["flow"]["name"] == "ui-test-flow"
        assert result["summary"]["flow"]["variables"] == ["prompt"]
        assert result["summary"]["prompts"]["ids"] == ["alpha"]

    def test_a_broken_flow_is_reported_not_raised(self, api):
        api.write_file("flow.yaml", "version: 1\nname: x\nsteps: []\n")
        result = api.validate("flow.yaml", "prompts.yaml")
        assert not result["ok"]
        assert any("flow:" in problem for problem in result["problems"])

    def test_a_prompts_file_missing_a_variable_is_caught(self, api):
        api.write_file("prompts.yaml", "- id: alpha\n  something_else: x\n")
        result = api.validate("flow.yaml", "prompts.yaml")
        assert not result["ok"]
        assert any("missing prompt" in problem for problem in result["problems"])

    def test_both_files_are_reported_on_together(self, api):
        api.write_file("flow.yaml", "nonsense: true\n")
        api.write_file("prompts.yaml", "not a list\n")
        result = api.validate("flow.yaml", "prompts.yaml")
        assert len(result["problems"]) == 2


class TestRunning:
    def drain(self, job, timeout=180):
        deadline = time.time() + timeout
        events = []
        while time.time() < deadline:
            event = job.events.get(timeout=timeout)
            if event is None:
                return events
            events.append(event)
        raise AssertionError("run did not finish")

    def test_a_run_streams_progress_and_finishes(self, api):
        pytest.importorskip("playwright")
        started = api.start_run({"flow": "flow.yaml", "prompts": "prompts.yaml"})
        job = api.job(started["run_id"])
        kinds = [event["type"] for event in self.drain(job)]

        assert kinds[0] == "started"
        assert "variant_finished" in kinds
        assert kinds[-1] == "finished"
        assert job.status == "finished"

    def test_the_result_carries_the_response_and_a_report(self, api):
        pytest.importorskip("playwright")
        started = api.start_run({"flow": "flow.yaml", "prompts": "prompts.yaml"})
        job = api.job(started["run_id"])
        self.drain(job)

        assert job.results[0]["response"] == "Echo: first prompt"
        assert job.report and job.report.endswith("report.md")
        assert (api.workspace.root / job.report).exists()

    def test_a_broken_flow_fails_the_job_without_taking_the_server_down(self, api):
        api.write_file("flow.yaml", "version: 1\nname: x\nsteps: []\n")
        started = api.start_run({"flow": "flow.yaml", "prompts": "prompts.yaml"})
        job = api.job(started["run_id"])
        events = self.drain(job, timeout=30)

        assert events[-1]["type"] == "failed"
        assert job.status == "failed"
        assert "traceback" in events[-1]

    def test_an_unknown_run_is_a_clear_error(self, api):
        with pytest.raises(WorkspaceError, match="unknown run"):
            api.job("run9999")


class TestHttp:
    @pytest.fixture
    def server(self, workspace):
        httpd = serve(workspace, "127.0.0.1", 8791)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)
        yield "http://127.0.0.1:8791"
        httpd.shutdown()

    def get(self, url):
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()

    def test_the_page_is_served(self, server):
        status, body = self.get(server + "/")
        assert status == 200 and b"FlowRunner" in body

    def test_the_file_api_returns_json(self, server):
        _, body = self.get(server + "/api/file?path=prompts.yaml")
        assert json.loads(body)["text"] == PROMPTS

    def test_workspace_files_are_served_for_the_output_view(self, server):
        status, body = self.get(server + "/files/prompts.yaml")
        assert status == 200 and b"alpha" in body

    def test_path_traversal_over_http_is_refused(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            self.get(server + "/files/../../../etc/passwd")
        assert caught.value.code in (400, 404)

    def test_an_unknown_route_is_a_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as caught:
            self.get(server + "/api/nope")
        assert caught.value.code == 404
