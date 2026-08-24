"""The Windows probe.

The CDP half is stdlib and runs anywhere, so it is tested against a real
debugging endpoint. The UIA half cannot run here, so its pure logic -- tree
walking, formatting, summarising, and the verdict -- is tested against
synthetic trees, leaving only the thin pywinauto adapter unexercised.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import probe_native  # noqa: E402

from flowrunner.drivers.web import find_chromium  # noqa: E402

PORT = 9412
FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures" / "chat_app" / "index.html")


class FakeInfo:
    def __init__(self, **fields):
        self.__dict__.update(
            {"control_type": "", "automation_id": "", "name": "",
             "class_name": "", "rectangle": "", "visible": True, **fields}
        )


class FakeElement:
    def __init__(self, info=None, children=(), children_raise=False):
        self.element_info = info or FakeInfo()
        self._children = list(children)
        self._children_raise = children_raise

    def children(self):
        if self._children_raise:
            raise RuntimeError("COM error 0x80040201")
        return self._children


class Exploding:
    """A control whose properties throw -- routine in custom-drawn UIs."""

    @property
    def element_info(self):
        raise RuntimeError("no accessible information")


class TestCdpDetection:
    @pytest.fixture(scope="class")
    @classmethod
    def endpoint(cls, tmp_path_factory):
        executable = find_chromium() or shutil.which("chromium")
        if not executable:
            pytest.skip("no chromium available")
        profile = tmp_path_factory.mktemp("probe-profile")
        process = subprocess.Popen(
            [executable, "--headless=new", f"--remote-debugging-port={PORT}",
             f"--user-data-dir={profile}", "--no-first-run", "--no-sandbox",
             "--disable-dev-shm-usage", f"file://{FIXTURE}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for a *page* target, not just the endpoint: /json/version
        # answers before the page has loaded and taken its title.
        deadline = time.time() + 30
        while time.time() < deadline:
            found = probe_native.probe_cdp([PORT])
            if found and any(page["title"] for page in found[0]["pages"]):
                break
            time.sleep(0.2)
        else:
            process.kill()
            pytest.skip("debugging endpoint never came up")
        yield PORT
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    def test_a_live_endpoint_is_found_with_its_pages(self, endpoint):
        found = probe_native.probe_cdp([endpoint])
        assert len(found) == 1
        assert found[0]["cdp_url"] == f"http://127.0.0.1:{endpoint}"
        assert any(page["title"] == "Fixture Chat" for page in found[0]["pages"])

    def test_dead_ports_are_silent_not_slow_failures(self, endpoint):
        assert probe_native.probe_cdp([59998, 59999]) == []

    def test_the_verdict_hands_back_ready_made_flow_config(self, endpoint):
        found = probe_native.probe_cdp([endpoint])
        lines = "\n".join(probe_native.verdict(found, {}))
        assert "attach over CDP" in lines
        assert f'cdp_url: "http://127.0.0.1:{endpoint}"' in lines


class TestTreeWalking:
    def test_a_tree_is_flattened_with_depths(self):
        tree = FakeElement(
            FakeInfo(control_type="Window", name="CATIA V5"),
            children=[
                FakeElement(FakeInfo(control_type="MenuBar", name="Menu")),
                FakeElement(
                    FakeInfo(control_type="Pane", automation_id="viewport"),
                    children=[FakeElement(FakeInfo(control_type="Custom"))],
                ),
            ],
        )
        nodes = probe_native.walk(tree)
        assert [n["depth"] for n in nodes] == [0, 1, 1, 2]
        assert nodes[0]["name"] == "CATIA V5"

    def test_an_element_that_throws_does_not_stop_the_walk(self):
        tree = FakeElement(
            FakeInfo(control_type="Window"),
            children=[Exploding(), FakeElement(FakeInfo(control_type="Button"))],
        )
        nodes = probe_native.walk(tree)
        assert len(nodes) == 3
        assert nodes[2]["control_type"] == "Button"

    def test_a_container_that_refuses_to_enumerate_is_recorded(self):
        """The signature of an opaque canvas: a control with no reachable
        children."""
        tree = FakeElement(FakeInfo(control_type="Pane"), children_raise=True)
        nodes = probe_native.walk(tree)
        assert "COM error" in nodes[0]["children_error"]

    def test_the_walk_is_bounded(self, monkeypatch):
        # CATIA's tree can be enormous; an unbounded probe would hang.
        monkeypatch.setattr(probe_native, "MAX_TREE_NODES", 5)
        deep = FakeElement(FakeInfo(control_type="A"))
        for _ in range(50):
            deep = FakeElement(FakeInfo(control_type="A"), children=[deep])
        assert len(probe_native.walk(deep)) <= 5

    def test_depth_is_bounded_too(self, monkeypatch):
        monkeypatch.setattr(probe_native, "MAX_TREE_DEPTH", 3)
        deep = FakeElement(FakeInfo(control_type="A"))
        for _ in range(20):
            deep = FakeElement(FakeInfo(control_type="A"), children=[deep])
        assert max(n["depth"] for n in probe_native.walk(deep)) <= 3


class TestReporting:
    NODES = [
        {"depth": 0, "control_type": "Window", "automation_id": "", "name": "CATIA V5", "class_name": "X"},
        {"depth": 1, "control_type": "MenuBar", "automation_id": "menu", "name": "Menu", "class_name": ""},
        {"depth": 1, "control_type": "Pane", "automation_id": "", "name": "", "class_name": ""},
    ]

    def test_the_tree_renders_readably(self):
        rendered = probe_native.format_tree(self.NODES)
        assert rendered.splitlines()[0].startswith("Window")
        assert "  MenuBar  id='menu'  name='Menu'" in rendered

    def test_the_summary_counts_what_decides_the_approach(self):
        summary = probe_native.summarise_tree(self.NODES)
        assert summary["nodes"] == 3
        assert summary["with_automation_id"] == 1
        assert summary["with_name"] == 2
        assert summary["control_types"]["Window"] == 1

    def test_no_cdp_gives_the_instructions_for_turning_it_on(self):
        lines = "\n".join(probe_native.verdict([], {"available": True, **probe_native.summarise_tree(self.NODES)}))
        assert "remote-debugging-port" in lines
        assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" in lines

    def test_a_sparse_uia_tree_is_called_out(self):
        lines = "\n".join(
            probe_native.verdict([], {"available": True, **probe_native.summarise_tree(self.NODES)})
        )
        assert "Very few AutomationIds" in lines
        assert "OCR" in lines

    def test_a_missing_dependency_is_reported_not_hidden(self):
        lines = "\n".join(probe_native.verdict([], {"available": False, "error": "pywinauto not installed"}))
        assert "pywinauto not installed" in lines


def test_title_globs_become_anchored_regexes():
    assert probe_native._glob_to_regex("*CATIA*") == "^.*CATIA.*$"
    assert probe_native._glob_to_regex("Example App") == "^Example\\ App$"
