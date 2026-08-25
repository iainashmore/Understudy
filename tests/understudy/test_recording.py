"""Recording a run to video."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from understudy.recording import (
    FfmpegProcess,
    FfmpegRecorder,
    NullRecorder,
    Recording,
    build_gdigrab_command,
    find_ffmpeg,
)


class TestCommand:
    def test_a_region_is_captured_from_the_desktop(self):
        command = build_gdigrab_command(
            "out.mp4", region={"x": 0, "y": 0, "width": 1920, "height": 1080}
        )
        assert "-f" in command and "gdigrab" in command
        assert command[command.index("-video_size") + 1] == "1920x1080"
        assert command[-1] == "out.mp4"

    def test_odd_dimensions_are_rounded_down(self):
        """H.264 with yuv420p needs even dimensions. An odd one fails at
        startup, and by then the recording is already lost."""
        command = build_gdigrab_command(
            "out.mp4", region={"x": 0, "y": 0, "width": 1921, "height": 1081}
        )
        assert command[command.index("-video_size") + 1] == "1920x1080"

    def test_negative_offsets_survive(self):
        """A monitor left of or above the primary has negative virtual-desktop
        coordinates. That is normal, not an error to sanitise away."""
        command = build_gdigrab_command(
            "out.mp4", region={"x": -2560, "y": -200, "width": 1920, "height": 1080}
        )
        assert command[command.index("-offset_x") + 1] == "-2560"
        assert command[command.index("-offset_y") + 1] == "-200"

    def test_a_window_can_be_captured_by_title(self):
        command = build_gdigrab_command("out.mp4", window_title="CATIA V5")
        assert "title=CATIA V5" in command
        assert "-offset_x" not in command

    def test_with_neither_it_captures_the_whole_desktop(self):
        command = build_gdigrab_command("out.mp4")
        assert command[command.index("-i") + 1] == "desktop"
        assert "-video_size" not in command

    def test_the_pointer_is_drawn_by_default(self):
        """The session is recorded to be watched; a recording with no cursor
        does not show what was clicked."""
        on = build_gdigrab_command("out.mp4")
        off = build_gdigrab_command("out.mp4", draw_mouse=False)
        assert on[on.index("-draw_mouse") + 1] == "1"
        assert off[off.index("-draw_mouse") + 1] == "0"

    def test_a_region_too_small_to_encode_is_refused(self):
        with pytest.raises(ValueError, match="too small"):
            build_gdigrab_command("out.mp4", region={"x": 0, "y": 0, "width": 1, "height": 1})

    def test_ffmpeg_is_found_on_path_or_from_playwright(self):
        found = find_ffmpeg()
        assert found is None or Path(found).exists()


class FakeProcess:
    """Enough of Popen to drive the lifecycle."""

    def __init__(self, exits_on_quit: bool = True) -> None:
        self.exits_on_quit = exits_on_quit
        self.returncode: int | None = None
        self.killed = False
        self.written = b""
        self.stdin = self
        self.stderr = self

    # stdin
    def write(self, data: bytes) -> None:
        self.written += data

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    # stderr
    def read(self) -> bytes:
        return b"frame= 120 fps=12"

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.exits_on_quit and self.written == b"q":
            self.returncode = 0
            return 0
        if self.killed:
            self.returncode = -9
            return -9
        raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)

    def kill(self):
        self.killed = True


class TestProcessLifecycle:
    def test_it_asks_ffmpeg_to_quit_rather_than_killing_it(self):
        """Killing ffmpeg leaves an unfinalised container -- a file that exists
        and will not play."""
        fake = FakeProcess()
        process = FfmpegProcess(["ffmpeg"])
        process.start(spawn=lambda *a, **k: fake)

        assert process.stop() == 0
        assert fake.written == b"q"
        assert fake.killed is False

    def test_it_kills_ffmpeg_that_will_not_go(self):
        fake = FakeProcess(exits_on_quit=False)
        process = FfmpegProcess(["ffmpeg"])
        process.start(spawn=lambda *a, **k: fake)

        process.stop(timeout=0.01)
        assert fake.killed is True

    def test_stderr_is_kept_for_the_error_message(self):
        fake = FakeProcess()
        process = FfmpegProcess(["ffmpeg"])
        process.start(spawn=lambda *a, **k: fake)
        process.stop()
        assert "frame=" in process.stderr_tail

    def test_stopping_something_never_started_is_not_a_crash(self):
        assert FfmpegProcess(["ffmpeg"]).stop() is None


class TestRecorders:
    def test_the_null_recorder_records_nothing_and_says_why(self):
        recorder = NullRecorder("recording is off")
        assert recorder.start(Path("x.mp4")) is False
        assert recorder.stop().error == "recording is off"

    def test_a_missing_ffmpeg_is_reported_not_fatal(self, tmp_path):
        """A missing screen recorder is a note in the results, never a lost
        sweep."""
        recorder = FfmpegRecorder(ffmpeg=None)
        recorder.ffmpeg = None
        assert recorder.available is False
        assert recorder.start(tmp_path / "out.mp4") is False
        assert recorder.stop().ok is False
        # And it can say why, which is what the run reports rather than
        # producing no video and mentioning it nowhere.
        assert "ffmpeg" in recorder.reason
        assert FfmpegRecorder(ffmpeg="ffmpeg").reason is None

    def test_a_region_that_cannot_be_encoded_fails_to_start_cleanly(self, tmp_path):
        recorder = FfmpegRecorder(region={"x": 0, "y": 0, "width": 1, "height": 1},
                                  ffmpeg="ffmpeg")
        assert recorder.start(tmp_path / "out.mp4") is False

    def test_a_header_with_no_frames_in_it_is_not_a_recording(self, tmp_path):
        """A 48-byte mp4 came back from a runner and the transcript embedded it
        as a video. It plays as nothing. A file that exists is worse than no
        file when it looks like a recording and is not one."""
        recorder = FfmpegRecorder(ffmpeg="ffmpeg")
        recorder.current = FfmpegProcess(["ffmpeg"])
        recorder.current.start(spawn=lambda *a, **k: FakeProcess())
        recorder.path = tmp_path / "stub.mp4"
        recorder.path.write_bytes(b"\x00" * 48)

        outcome = recorder.stop()
        assert outcome.ok is False
        assert "48 bytes" in outcome.error

    def test_a_real_sized_file_is_accepted(self, tmp_path):
        recorder = FfmpegRecorder(ffmpeg="ffmpeg")
        recorder.current = FfmpegProcess(["ffmpeg"])
        recorder.current.start(spawn=lambda *a, **k: FakeProcess())
        recorder.path = tmp_path / "real.mp4"
        recorder.path.write_bytes(b"\x00" * 60_000)

        assert recorder.stop().ok is True

    def test_a_recording_that_produced_no_file_reports_that(self, tmp_path):
        recorder = FfmpegRecorder(ffmpeg="ffmpeg")
        recorder.current = FfmpegProcess(["ffmpeg"])
        recorder.current.start(spawn=lambda *a, **k: FakeProcess())
        recorder.path = tmp_path / "never-written.mp4"

        outcome = recorder.stop()
        assert outcome.ok is False
        assert "no file" in outcome.error


def test_a_recording_knows_whether_it_worked(tmp_path):
    written = tmp_path / "v.mp4"
    written.write_bytes(b"x")
    assert Recording(path=written, backend="ffmpeg").ok is True
    assert Recording(backend="ffmpeg", error="nope").ok is False
