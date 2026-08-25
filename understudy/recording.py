"""Recording a run to video.

Two mechanisms, because the two backends offer different things:

  * **native** — ffmpeg, capturing the target monitor region or the window.
    Region capture is the default: dialogs and menus routinely fall outside the
    window rectangle, and a recording that clips them off is a recording of the
    wrong thing. The target application has its own screen here, so the region
    is that screen.
  * **web** — Playwright records the page itself, which needs no external tool
    and captures exactly the viewport.

Both are optional and both degrade to nothing rather than failing a run: a
missing ffmpeg is a note in the results, not a lost sweep.

The ffmpeg invocation is built as data by a pure function and run by a separate
process wrapper, so the command can be checked without Windows and the
lifecycle can be checked without gdigrab.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

#: Frames a second. Enough to follow a pointer; small enough that a fifty-
#: variant sweep does not fill a disk.
DEFAULT_FRAMERATE = 12
#: How long to wait for ffmpeg to finalise the file after being asked to stop.
FINALISE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Recording:
    path: Path | None = None
    backend: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.error is None


def find_ffmpeg() -> str | None:
    """ffmpeg on PATH, or the copy Playwright ships."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import os

    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    for pattern in ("ffmpeg-*/ffmpeg-linux", "ffmpeg-*/ffmpeg-win64.exe",
                    "ffmpeg-*/ffmpeg-mac"):
        candidates = sorted(root.glob(pattern))
        if candidates:
            return str(candidates[-1])
    return None


def _even(value: int) -> int:
    """H.264 with yuv420p needs even dimensions; an odd one fails at startup
    with a message nobody reads until the recording is already lost."""
    return value if value % 2 == 0 else value - 1


def build_gdigrab_command(
    output: Path | str,
    region: dict[str, int] | None = None,
    window_title: str | None = None,
    framerate: int = DEFAULT_FRAMERATE,
    ffmpeg: str = "ffmpeg",
    draw_mouse: bool = True,
) -> list[str]:
    """The Windows screen-capture command.

    Either a region of the desktop -- which is what "CATIA has its own screen"
    means -- or a single window by title. Region wins by default because menus
    and dialogs escape the window rectangle.
    """
    command = [ffmpeg, "-y", "-f", "gdigrab", "-framerate", str(framerate),
               "-draw_mouse", "1" if draw_mouse else "0"]

    if region:
        width, height = _even(int(region["width"])), _even(int(region["height"]))
        if width < 2 or height < 2:
            raise ValueError(f"region is too small to record: {region}")
        # Negative offsets are legitimate: a monitor left of or above the
        # primary has negative virtual-desktop coordinates.
        command += [
            "-offset_x", str(int(region["x"])), "-offset_y", str(int(region["y"])),
            "-video_size", f"{width}x{height}", "-i", "desktop",
        ]
    elif window_title:
        command += ["-i", f"title={window_title}"]
    else:
        command += ["-i", "desktop"]

    return command + [
        # -an: no audio, explicitly. gdigrab captures none, but saying so keeps
        # the output predictable and stops ffmpeg probing for a device.
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", str(output),
    ]


def build_transcode_command(
    source: Path | str, output: Path | str, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """Convert a capture to H.264 MP4, silent.

    Playwright only writes WebM, and MP4 is what plays everywhere without
    argument. yuv420p because some players refuse anything else.
    """
    return [
        ffmpeg, "-y", "-i", str(source), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]


def can_write_mp4(ffmpeg: str | None = None) -> bool:
    """Whether this ffmpeg can produce H.264 MP4.

    Worth asking: Playwright ships a cut-down build with no H.264 encoder and
    no MP4 muxer at all, so finding *an* ffmpeg is not the same as being able
    to write the format that was asked for.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return False
    try:
        encoders = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, timeout=20,
        ).stdout.decode("utf-8", "replace")
        muxers = subprocess.run(
            [ffmpeg, "-hide_banner", "-muxers"],
            capture_output=True, timeout=20,
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return False
    has_encoder = "libx264" in encoders or " h264 " in encoders
    return has_encoder and "mp4" in muxers


def transcode_to_mp4(source: Path, output: Path, ffmpeg: str | None = None) -> Recording:
    """Convert and remove the source, or keep the source and say why not."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return Recording(
            path=source, backend="playwright",
            error="kept as .webm: no ffmpeg found to convert it to mp4",
        )
    if not can_write_mp4(ffmpeg):
        return Recording(
            path=source, backend="playwright",
            error=f"kept as .webm: {ffmpeg} cannot write H.264 mp4 "
                  f"(Playwright's bundled build cannot). Install a full ffmpeg.",
        )
    try:
        result = subprocess.run(
            build_transcode_command(source, output, ffmpeg),
            capture_output=True, timeout=300,
        )
    except Exception as exc:
        return Recording(path=source, backend="playwright",
                         error=f"kept as .webm: conversion failed ({exc})")

    if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
        source.unlink(missing_ok=True)
        return Recording(path=output, backend="playwright")
    tail = result.stderr.decode("utf-8", "replace")[-200:]
    return Recording(path=source, backend="playwright",
                     error=f"kept as .webm: conversion failed -- {tail}")


#: How much of ffmpeg's chatter to keep. Only the end of it is ever useful.
MAX_STDERR = 8192

#: ffmpeg's per-frame progress, which it writes to stderr separated by
#: carriage returns rather than newlines.
PROGRESS = re.compile(r"^(frame|size)=")


def meaningful_stderr(text: str, keep: int = 6) -> str:
    """What ffmpeg said, with the progress counter taken out.

    It writes a progress line per frame, so the last 300 characters of a
    failing capture are "frame= 37 fps=8.0 q=29.0 size= 0KiB" and nothing
    about why it stopped. The error is always further back.
    """
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    real = [line for line in lines if not PROGRESS.match(line)]
    return "\n".join((real or lines)[-keep:])


class FfmpegProcess:
    """Start ffmpeg, then stop it so the file is actually playable.

    Killing ffmpeg leaves an unfinalised container. It is asked to quit by
    writing `q` to its stdin, and only killed if it will not go.
    """

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.process: subprocess.Popen | None = None
        self.stderr_tail: str = ""
        self._pump: threading.Thread | None = None
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def start(self, spawn=subprocess.Popen) -> None:
        self.process = spawn(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._pump = threading.Thread(target=self._drain, daemon=True)
        self._pump.start()

    def _drain(self) -> None:
        """Read stderr while ffmpeg runs, rather than once at the end.

        ffmpeg writes a progress line per frame. Nothing read any of it until
        stop(), so the pipe buffer filled, ffmpeg blocked writing to it, and
        the capture stopped dead -- a 25-second run produced a file holding
        three seconds of nothing and ffmpeg exited 1. Short runs got away with
        it, which is why the first Notepad recordings looked fine.
        """
        stream = getattr(self.process, "stderr", None)
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._buffer.extend(chunk)
                    del self._buffer[:-MAX_STDERR]
        except Exception:
            return

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = FINALISE_TIMEOUT_S) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
                self.process.stdin.close()
            except Exception:
                pass
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        if self._pump is not None:
            self._pump.join(timeout=2)
        with self._lock:
            raw = bytes(self._buffer)
        self.stderr_tail = meaningful_stderr(raw.decode("utf-8", "replace"))
        return self.process.returncode


class NullRecorder:
    """Records nothing, and says why."""

    backend = "none"
    available = False

    def __init__(self, reason: str = "recording is off") -> None:
        self.reason = reason

    def start(self, path: Path) -> bool:
        return False

    def stop(self) -> Recording:
        return Recording(backend=self.backend, error=self.reason)


#: Under this it is not a recording. An mp4 with only its header is around 50
#: bytes; the shortest real capture this produces is tens of kilobytes.
PLAUSIBLE_BYTES = 2048


class FfmpegRecorder:
    """Screen capture for the native backend."""

    backend = "ffmpeg"

    def __init__(self, region: dict[str, int] | None = None,
                 window_title: str | None = None,
                 framerate: int = DEFAULT_FRAMERATE,
                 ffmpeg: str | None = None) -> None:
        self.region = region
        self.window_title = window_title
        self.framerate = framerate
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.current: FfmpegProcess | None = None
        self.path: Path | None = None

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg)

    @property
    def reason(self) -> str | None:
        """Why it cannot record, or None when it can."""
        if self.ffmpeg:
            return None
        return ("ffmpeg was not found: not on PATH, and no copy staged by "
                "packaging/fetch_payload.py")

    def start(self, path: Path) -> bool:
        if not self.ffmpeg:
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            command = build_gdigrab_command(
                path, region=self.region, window_title=self.window_title,
                framerate=self.framerate, ffmpeg=self.ffmpeg,
            )
        except ValueError:
            return False
        self.current = FfmpegProcess(command)
        try:
            self.current.start()
        except Exception:
            self.current = None
            return False
        # Give the capture a moment to attach, so the first step is not missing
        # from the recording.
        time.sleep(0.4)
        self.path = path
        return True

    def stop(self) -> Recording:
        if self.current is None:
            return Recording(backend=self.backend, error="not started")
        code = self.current.stop()
        tail = self.current.stderr_tail
        self.current = None
        size = self.path.stat().st_size if self.path and self.path.exists() else 0
        if size >= PLAUSIBLE_BYTES:
            return Recording(path=self.path, backend=self.backend)
        if size:
            # A container header and nothing in it. The transcript would embed
            # it as a video and it would play as nothing: worse than no file,
            # because it looks like a recording that exists.
            return Recording(
                backend=self.backend,
                error=f"ffmpeg wrote {size} bytes, which is a header and no "
                      f"frames (exit {code}): {tail[-300:]}",
            )
        return Recording(
            backend=self.backend,
            error=f"ffmpeg produced no file (exit {code}): {tail[-300:]}",
        )
