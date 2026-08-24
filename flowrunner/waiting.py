"""Knowing when a response is complete.

The load-bearing piece, and the reason fixed sleeps are banned: a recorded
"human paused 4.2 seconds" either truncates the response or wastes minutes on
every run.

The polling loop lives here rather than in the drivers, so every backend gets
the same semantics and the same two sampling modes:

  text    -- poll the target's text until it stops changing. Works wherever the
             accessibility layer exposes text.
  pixels  -- poll a screenshot of the target until it stops changing. The
             fallback for surfaces that expose no text at all: a CAD viewport, a
             custom-drawn panel. Comparison is blurred and tolerance-based
             rather than exact, because a caret blink or a re-antialiased glyph
             would otherwise mean "still changing" forever.

A completion *signal* -- a stop button vanishing, a send button re-enabling --
beats both, and is used when the flow names one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from harness.image import gaussian_blur, load_rgb, resize_to

#: Matches the harness scorer's calibration: enough blur to absorb rendering
#: jitter, tight enough tolerance that real change still registers.
PIXEL_BLUR_SIGMA = 1.0
PIXEL_TOLERANCE = 24
#: Fraction of pixels allowed to differ while still counting as unchanged.
PIXEL_CHANGE_FLOOR = 0.002
#: Longest edge compared. A stability check asks "did this change", not "how
#: exactly"; comparing a full window every 250ms costs more than the polling
#: interval and would make the wait itself the bottleneck.
PIXEL_MAX_EDGE = 320


class StableOutcome(str, Enum):
    STABLE = "stable"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class StableResult:
    outcome: StableOutcome
    waited_ms: int
    samples: int
    signal: str
    last_value: str = ""

    @property
    def timed_out(self) -> bool:
        return self.outcome is StableOutcome.TIMEOUT


def _normalise(text: str) -> str:
    """Whitespace-insensitive. A streaming renderer reflowing its own output is
    not new content."""
    return " ".join(text.split())


def _prepare(image: bytes) -> np.ndarray:
    pixels = load_rgb(image)
    height, width = pixels.shape[:2]
    longest = max(height, width)
    if longest > PIXEL_MAX_EDGE:
        scale = PIXEL_MAX_EDGE / longest
        pixels = resize_to(pixels, max(1, int(height * scale)), max(1, int(width * scale)))
    return gaussian_blur(pixels, PIXEL_BLUR_SIGMA)


def pixels_equivalent(first: bytes, second: bytes) -> bool:
    """Blurred, tolerance-based image comparison, on a downscaled copy."""
    if first == second:
        return True
    try:
        left, right = _prepare(first), _prepare(second)
    except Exception:
        return False
    if left.shape != right.shape:
        return False
    deviation = np.abs(left - right).max(axis=2)
    changed = float((deviation > PIXEL_TOLERANCE).mean())
    return changed <= PIXEL_CHANGE_FLOOR


def wait_until_stable(
    sample: Callable[[], object],
    equivalent: Callable[[object, object], bool],
    stable_for_ms: int,
    timeout_ms: int,
    poll_interval_ms: int = 250,
    done_signal: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> StableResult:
    """Poll until the sample stops changing, or the completion signal fires.

    When a `done_signal` is given it takes precedence, but stability is still
    required afterwards: the text routinely lags the spinner by a frame or two,
    and stopping the instant the spinner clears truncates the last token.
    """
    started = clock()
    deadline = started + timeout_ms / 1000.0
    settle = stable_for_ms / 1000.0
    interval = poll_interval_ms / 1000.0

    previous = sample()
    samples = 1
    unchanged_since = clock()
    signalled = done_signal is None

    while True:
        now = clock()
        if now >= deadline:
            return StableResult(
                outcome=StableOutcome.TIMEOUT,
                waited_ms=int((now - started) * 1000),
                samples=samples,
                signal="timeout",
                last_value=previous if isinstance(previous, str) else "",
            )

        if not signalled and done_signal is not None and done_signal():
            signalled = True
            # Restart the settle window: the signal says generation stopped, the
            # settle window confirms the content has caught up.
            unchanged_since = now

        sleep(min(interval, max(0.0, deadline - now)))
        current = sample()
        samples += 1
        now = clock()

        if not equivalent(previous, current):
            unchanged_since = now
        previous = current

        if signalled and (now - unchanged_since) >= settle:
            return StableResult(
                outcome=StableOutcome.STABLE,
                waited_ms=int((now - started) * 1000),
                samples=samples,
                signal="signal+stable" if done_signal is not None else "stable",
                last_value=current if isinstance(current, str) else "",
            )


def text_equivalent(first: object, second: object) -> bool:
    return _normalise(str(first)) == _normalise(str(second))
