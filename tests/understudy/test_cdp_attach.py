"""Attaching to a Chromium that is already running in another process.

This is the rehearsal for an assistant panel embedded in a desktop application.
A separate Chromium launched with --remote-debugging-port stands in for the
host's WebView2/CEF instance: the driver's code path is identical, so what
passes here is what would run against the real embedded panel.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from understudy.drivers.base import DriverError
from understudy.drivers.web import WebDriver, find_chromium
from understudy.flow import load_flow
from understudy.prompts import prompts_from_entries
from understudy.runner import Runner, Status

pytest.importorskip("playwright", reason="needs playwright")

REPO = Path(__file__).resolve().parents[2]
FIXTURE = (REPO / "fixtures" / "chat_app" / "index.html").resolve()
FLOW_TEMPLATE = (REPO / "examples" / "fixture_chat.yaml").read_text()
PROMPTS = prompts_from_entries([{'id': 'baseline', 'prompt': 'hello there'}])
PORT = 9411


def cdp_get(path: str, timeout: float = 2.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def open_tab(url: str) -> bool:
    """Chrome 111+ requires PUT on /json/new; older builds accept GET."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    try:
        urllib.request.urlopen(request, timeout=3).read()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    """A separate browser process, standing in for the desktop application
    that hosts the embedded web view. Deliberately a subprocess: the real host
    is another process, and it keeps this test out of the driver's own
    Playwright session."""
    executable = find_chromium() or shutil.which("chromium") or shutil.which("google-chrome")
    if not executable:
        pytest.skip("no chromium binary to host the embedded view")

    profile = tmp_path_factory.mktemp("host-profile")
    process = subprocess.Popen(
        [
            executable, "--headless=new", f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            # Container-only: Chromium refuses to start as root otherwise. A
            # real WebView2/CEF host needs none of this.
            "--no-sandbox", "--disable-dev-shm-usage",
            f"file://{FIXTURE}?mode=stream&delay=15&dialog=portal",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if any(t.get("type") == "page" for t in cdp_get("/json/list")):
                break
        except Exception:
            time.sleep(0.2)
    else:
        process.kill()
        pytest.skip("host browser did not expose a debugging port")

    yield {"process": process, "cdp": f"http://127.0.0.1:{PORT}"}
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def attach_flow(tmp_path: Path, host, **extra: str):
    text = re.sub(
        r'url: "file://[^"]*"',
        f'cdp_url: "{host["cdp"]}"' + "".join(f"\n    {line}" for line in extra.values()),
        FLOW_TEMPLATE,
    )
    path = tmp_path / "flow.yaml"
    path.write_text(text)
    return load_flow(path)


def test_a_flow_runs_against_an_already_running_browser(tmp_path, host):
    flow = attach_flow(tmp_path, host)
    driver = WebDriver()
    driver.start(flow.app_config("web"))
    try:
        assert driver.attached is True
        runner = Runner(flow, driver, tmp_path / "out")
        runner.prepare(PROMPTS)
        result = runner.run_variant(PROMPTS.variants[0])
    finally:
        driver.stop()

    assert result.status is Status.OK
    assert result.response == "Echo: hello there"
    assert len(result.screenshots) == 3


def test_the_hosts_browser_survives_the_run(tmp_path, host):
    """Closing a browser we did not launch would take the host application's
    panel down with it."""
    flow = attach_flow(tmp_path, host)
    driver = WebDriver()
    driver.start(flow.app_config("web"))
    driver.stop()

    assert host["process"].poll() is None, "the host process was killed"
    assert any(t.get("type") == "page" for t in cdp_get("/json/list"))


def test_level_two_reset_is_refused_when_attached(tmp_path, host):
    flow = attach_flow(tmp_path, host)
    driver = WebDriver()
    driver.start(flow.app_config("web"))
    try:
        with pytest.raises(DriverError, match="belongs to the host application"):
            driver.reset()
    finally:
        driver.stop()


def test_the_right_page_is_chosen_when_the_host_has_several(tmp_path, host):
    """A desktop app commonly runs more than one web view."""
    if not open_tab("data:text/html,<title>Not the panel</title><h1>other</h1>"):
        pytest.skip("could not open a second tab in the host")

    flow = attach_flow(tmp_path, host, pattern='page_title_pattern: "Fixture*"')
    driver = WebDriver()
    driver.start(flow.app_config("web"))
    try:
        assert driver.page.title() == "Fixture Chat"
    finally:
        driver.stop()


def test_no_matching_page_lists_what_was_there(tmp_path, host):
    flow = attach_flow(tmp_path, host, pattern='page_title_pattern: "Nothing*"')
    driver = WebDriver()
    with pytest.raises(DriverError, match="no attached page matches"):
        driver.start(flow.app_config("web"))
    driver.stop()


def test_a_dead_endpoint_says_how_to_check(tmp_path, host):
    flow = attach_flow(tmp_path, host)
    config = flow.app_config("web")
    config["cdp_url"] = "http://127.0.0.1:9999"
    driver = WebDriver()
    with pytest.raises(DriverError, match="remote debugging enabled"):
        driver.start(config)
    driver.stop()
