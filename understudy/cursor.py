"""Moving the pointer the way a person does.

Automation normally teleports the cursor: one frame it is here, the next it is
on the button. That is fine for a machine and wrong for a recording, where a
viewer needs to follow what is being demonstrated.

So the pointer travels — eased, slightly arced, with a small settle before the
click. Three properties matter:

  * **Deterministic.** The wobble comes from a generator seeded on the two
    endpoints, so the same move always draws the same path. A tool whose value
    is that only the prompt varies between runs cannot introduce real
    randomness into the pointer, even cosmetic randomness.
  * **Bounded.** The path stays inside the rectangle spanned by its endpoints
    plus a small margin, so a move within the target window cannot stray onto
    the other monitor mid-flight.
  * **Skippable.** Unattended sweeps do not need it; `instant` moves cost
    nothing and are the default off-camera.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

Point = tuple[int, int]

#: Pointer speed in pixels per second at the fastest part of the movement.
#: A quick but followable hand.
DEFAULT_SPEED = 1400.0
#: Nobody moves a pointer in under this, however short the distance.
MIN_DURATION_S = 0.08
#: Nor takes longer than this, however far.
MAX_DURATION_S = 1.10
#: Frames per second of the movement.
STEP_RATE = 90.0
#: How far the path bows out, as a fraction of the distance travelled.
ARC = 0.06
#: Pause after arriving, before the click, so a viewer sees the target.
SETTLE_S = 0.12


@dataclass(frozen=True)
class MouseStyle:
    """How the pointer should behave. `instant` is the old teleport."""

    mode: str = "human"
    speed: float = DEFAULT_SPEED
    arc: float = ARC
    settle_s: float = SETTLE_S
    step_rate: float = STEP_RATE

    @property
    def animated(self) -> bool:
        return self.mode == "human"

    @classmethod
    def from_config(cls, config: dict | None) -> "MouseStyle":
        config = config or {}
        return cls(
            mode=str(config.get("mode", "human")),
            speed=float(config.get("speed", DEFAULT_SPEED)),
            arc=float(config.get("arc", ARC)),
            settle_s=float(config.get("settle_ms", SETTLE_S * 1000)) / 1000.0,
            step_rate=float(config.get("step_rate", STEP_RATE)),
        )


def duration_for(distance: float, speed: float = DEFAULT_SPEED) -> float:
    """Longer for further, but never instant and never a crawl."""
    if distance <= 0:
        return MIN_DURATION_S
    # Square root rather than linear: a hand does not take ten times as long to
    # cross ten times the distance.
    seconds = math.sqrt(distance) / math.sqrt(speed) * 1.6
    return max(MIN_DURATION_S, min(MAX_DURATION_S, seconds))


def _ease(t: float) -> float:
    """Ease in and out. Slow at both ends, quick through the middle."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def path(start: Point, end: Point, style: MouseStyle | None = None) -> list[Point]:
    """The points the pointer passes through, endpoints included.

    Seeded on the endpoints, so a given move always draws the same path.
    """
    style = style or MouseStyle()
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)

    if distance < 1 or not style.animated:
        return [(int(x1), int(y1))]

    seconds = duration_for(distance, style.speed)
    steps = max(2, int(seconds * style.step_rate))

    # Perpendicular unit vector, for the bow in the path.
    perp_x, perp_y = -dy / distance, dx / distance
    # A string seed: tuples are not accepted, and this keeps the mapping from
    # endpoints to path stable across Python versions.
    rng = random.Random(f"{int(x0)},{int(y0)}->{int(x1)},{int(y1)}")
    bow = distance * style.arc * rng.choice((-1.0, 1.0))
    wobble = distance * 0.004

    points: list[Point] = []
    for index in range(steps + 1):
        t = index / steps
        eased = _ease(t)
        # sin(pi t) is zero at both ends, so the bow never displaces the
        # endpoints -- the click still lands exactly where it was asked to.
        across = math.sin(math.pi * t) * bow
        jitter = math.sin(math.pi * t) * rng.uniform(-wobble, wobble)
        points.append((
            int(round(x0 + dx * eased + perp_x * (across + jitter))),
            int(round(y0 + dy * eased + perp_y * (across + jitter))),
        ))

    points[0] = (int(x0), int(y0))
    points[-1] = (int(x1), int(y1))
    return points


def step_delay(point_count: int, distance: float, style: MouseStyle | None = None) -> float:
    """Seconds to sleep between successive points."""
    style = style or MouseStyle()
    if point_count <= 1:
        return 0.0
    return duration_for(distance, style.speed) / (point_count - 1)


def bounding_box(points: list[Point]) -> tuple[int, int, int, int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def move(start: Point, end: Point, style: MouseStyle | None = None,
         set_position=None, sleep=None) -> list[Point]:
    """Drive the pointer along the path. Returns the points visited.

    `set_position` and `sleep` are injectable so the movement can be tested
    without a mouse.
    """
    style = style or MouseStyle()
    if set_position is None:  # pragma: no cover - needs Windows
        from pywinauto import mouse

        def set_position(x: int, y: int) -> None:
            mouse.move(coords=(x, y))
    if sleep is None:  # pragma: no cover
        import time

        sleep = time.sleep

    points = path(start, end, style)
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    delay = step_delay(len(points), distance, style)

    for point in points:
        set_position(*point)
        if delay:
            sleep(delay)
    if style.animated and style.settle_s:
        # A beat on the target before the click, so a viewer registers what is
        # about to be pressed.
        sleep(style.settle_s)
    return points
