"""Pointer movement.

The session is being screen-recorded, so the pointer has to travel rather than
teleport. It also has to stay deterministic: a tool whose whole value is that
only the prompt varies between runs cannot introduce real randomness, even
cosmetic randomness.
"""

from __future__ import annotations

import math

import pytest

from flowrunner.cursor import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    MouseStyle,
    bounding_box,
    duration_for,
    move,
    path,
    step_delay,
)

FAR = ((100, 100), (1500, 800))


class TestPath:
    def test_it_starts_and_ends_exactly_where_asked(self):
        """The bow must not displace the endpoints, or the click misses."""
        points = path(*FAR)
        assert points[0] == FAR[0]
        assert points[-1] == FAR[1]

    def test_it_travels_rather_than_teleporting(self):
        assert len(path(*FAR)) > 30

    def test_it_is_not_a_dead_straight_line(self):
        """A hand arcs. A perfectly straight path reads as automation."""
        start, end = FAR
        points = path(start, end)
        midpoint = points[len(points) // 2]
        straight = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        assert math.hypot(midpoint[0] - straight[0], midpoint[1] - straight[1]) > 5

    def test_it_stays_near_the_line_it_is_travelling(self):
        """Bounded, so a move inside the target window cannot wander onto the
        other monitor mid-flight."""
        start, end = FAR
        left, top, right, bottom = bounding_box(path(start, end))
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        margin = distance * 0.12
        assert left >= min(start[0], end[0]) - margin
        assert right <= max(start[0], end[0]) + margin
        assert top >= min(start[1], end[1]) - margin
        assert bottom <= max(start[1], end[1]) + margin

    def test_progress_along_the_line_never_goes_backwards(self):
        start, end = FAR
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        projections = [
            ((x - start[0]) * dx + (y - start[1]) * dy) / length
            for x, y in path(start, end)
        ]
        assert projections == sorted(projections)

    def test_it_eases_in_and_out(self):
        """Slow at both ends, quick through the middle -- the shape of a real
        movement, and what makes a recording readable."""
        points = path(*FAR)
        gap = lambda a, b: math.hypot(b[0] - a[0], b[1] - a[1])  # noqa: E731
        first = gap(points[0], points[1])
        middle = gap(points[len(points) // 2], points[len(points) // 2 + 1])
        last = gap(points[-2], points[-1])
        assert middle > first * 1.5
        assert middle > last * 1.5


class TestDeterminism:
    def test_the_same_move_always_draws_the_same_path(self):
        assert path(*FAR) == path(*FAR)

    def test_different_moves_draw_different_paths(self):
        assert path((100, 100), (1500, 800)) != path((101, 100), (1500, 800))

    def test_a_repeated_sweep_moves_identically(self):
        """Variant two must not differ from variant one in any way the results
        could pick up."""
        first = [path((10, 10), (500, 400)), path((500, 400), (900, 200))]
        second = [path((10, 10), (500, 400)), path((500, 400), (900, 200))]
        assert first == second


class TestTiming:
    def test_further_takes_longer(self):
        assert duration_for(50) < duration_for(500) < duration_for(3000)

    def test_but_never_instant_and_never_a_crawl(self):
        assert duration_for(1) >= MIN_DURATION_S
        assert duration_for(100_000) <= MAX_DURATION_S

    def test_the_step_delay_spreads_the_points_over_the_duration(self):
        points = path(*FAR)
        distance = math.hypot(1400, 700)
        total = step_delay(len(points), distance) * (len(points) - 1)
        assert total == pytest.approx(duration_for(distance), rel=0.01)

    def test_a_single_point_needs_no_delay(self):
        assert step_delay(1, 0) == 0.0


class TestModes:
    def test_instant_mode_teleports(self):
        """Unattended sweeps do not need the animation, and it is not free."""
        assert path(*FAR, MouseStyle(mode="instant")) == [FAR[1]]

    def test_a_move_of_almost_nothing_does_not_animate(self):
        assert path((100, 100), (100, 100)) == [(100, 100)]

    def test_style_reads_from_flow_config(self):
        style = MouseStyle.from_config(
            {"mode": "instant", "speed": 900, "settle_ms": 250}
        )
        assert style.mode == "instant"
        assert style.speed == 900
        assert style.settle_s == 0.25

    def test_the_default_is_to_travel(self):
        assert MouseStyle.from_config(None).animated is True


class TestDriving:
    def test_every_point_is_visited_in_order(self):
        visited: list[tuple[int, int]] = []
        slept: list[float] = []
        returned = move((0, 0), (400, 300),
                        set_position=lambda x, y: visited.append((x, y)),
                        sleep=slept.append)

        assert visited == returned
        assert visited[0] == (0, 0) and visited[-1] == (400, 300)
        assert len(slept) == len(visited) + 1, "one per step, plus the settle"

    def test_it_settles_on_the_target_before_the_click(self):
        """A beat on the button, so a viewer registers what is being pressed."""
        slept: list[float] = []
        move((0, 0), (400, 300), MouseStyle(settle_s=0.3),
             set_position=lambda x, y: None, sleep=slept.append)
        assert slept[-1] == 0.3

    def test_instant_mode_visits_only_the_destination(self):
        visited: list[tuple[int, int]] = []
        move((0, 0), (400, 300), MouseStyle(mode="instant"),
             set_position=lambda x, y: visited.append((x, y)), sleep=lambda _: None)
        assert visited == [(400, 300)]


def test_a_move_across_negative_coordinates_works():
    """The target monitor may sit left of the primary."""
    points = path((-2000, 300), (-1200, 700))
    assert points[0] == (-2000, 300) and points[-1] == (-1200, 700)
    assert all(x < 0 for x, _ in points)
