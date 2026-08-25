"""Viewing a transcript inside the tool.

The rendered view is served to an iframe, and the video inside it has to be
scrubbable -- which is what the range requests are for. A browser served the
whole file on every seek will play from the start and refuse to move, and the
recording is then useless for the one thing it is for: jumping to the step
somebody is asking about.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

from understudy.ui.server import serve

PORT = 8794


@pytest.fixture
def server(tmp_path):
    run = tmp_path / "runs" / "one"
    run.mkdir(parents=True)
    (run / "transcript.html").write_text("<!doctype html><p>hello</p>")
    (run / "recording.mp4").write_bytes(bytes(range(256)) * 40)   # 10240 bytes

    httpd = serve(tmp_path, "127.0.0.1", PORT)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{PORT}"
    httpd.shutdown()
    thread.join(timeout=5)


def fetch(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def test_the_rendered_transcript_is_served_as_html(server):
    status, headers, body = fetch(f"{server}/files/runs/one/transcript.html")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"hello" in body


def test_a_video_is_served_as_video(server):
    status, headers, _ = fetch(f"{server}/files/runs/one/recording.mp4")
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"


def test_a_range_request_gets_exactly_that_range(server):
    status, headers, body = fetch(f"{server}/files/runs/one/recording.mp4",
                                  {"Range": "bytes=100-199"})
    assert status == 206
    assert headers["Content-Range"] == "bytes 100-199/10240"
    assert len(body) == 100
    assert body[0] == 100


def test_an_open_ended_range_runs_to_the_end(server):
    status, headers, body = fetch(f"{server}/files/runs/one/recording.mp4",
                                  {"Range": "bytes=10000-"})
    assert status == 206
    assert headers["Content-Range"] == "bytes 10000-10239/10240"
    assert len(body) == 240


def test_a_suffix_range_gets_the_tail(server):
    """How a player finds an mp4's index when it is written at the end."""
    status, headers, body = fetch(f"{server}/files/runs/one/recording.mp4",
                                  {"Range": "bytes=-64"})
    assert status == 206
    assert headers["Content-Range"] == "bytes 10176-10239/10240"
    assert len(body) == 64


def test_a_range_past_the_end_falls_back_to_the_whole_file(server):
    status, _, body = fetch(f"{server}/files/runs/one/recording.mp4",
                            {"Range": "bytes=99999-"})
    assert status == 200
    assert len(body) == 10240


def test_a_nonsense_range_header_is_ignored_rather_than_fatal(server):
    status, _, body = fetch(f"{server}/files/runs/one/recording.mp4",
                            {"Range": "kittens=1-2"})
    assert status == 200
    assert len(body) == 10240
