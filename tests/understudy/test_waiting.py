"""Knowing when a response has arrived.

The wait is the load-bearing piece of the whole tool, and it can fail in a way
that looks like success: an assistant that spends eight seconds thinking before
it prints anything leaves its panel perfectly unchanged for those eight seconds.
A naive "unchanged for a second" test passes immediately, captures nothing, and
reports a green run with an empty answer.

So the wait has two conditions, and these tests are mostly about the first one.
"""

from __future__ import annotations

import pytest

from understudy.waiting import StableOutcome, wait_until_stable


class Clock:
    """A clock the test moves by hand, so the timings are exact and instant."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run(samples: list[str], **kwargs):
    """Replay a scripted sequence of observations, one per poll."""
    clock = Clock()
    remaining = list(samples)
    last = [samples[0]]

    def sample():
        if remaining:
            last[0] = remaining.pop(0)
        return last[0]

    options = dict(
        sample=sample, equivalent=lambda a, b: a == b,
        stable_for_ms=500, timeout_ms=10_000, poll_interval_ms=250,
        sleep=clock.sleep, clock=clock,
    )
    options.update(kwargs)
    return wait_until_stable(**options)


def test_a_slow_start_is_not_mistaken_for_a_finished_answer():
    """Eight polls of nothing, then the answer. The wait must survive the pause."""
    silence = [""] * 8
    result = run(silence + ["Ans", "Answer", "Answer.", "Answer.", "Answer."])

    assert result.outcome is StableOutcome.STABLE
    assert result.last_value == "Answer."
    assert result.waited_ms >= 2000            # it did not return during the silence


def test_the_naive_behaviour_is_still_available_and_returns_at_once():
    silence = [""] * 8
    result = run(silence, require_change=False)

    assert result.outcome is StableOutcome.STABLE
    assert result.last_value == ""
    assert result.waited_ms < 1000


def test_a_response_that_never_arrives_times_out_saying_so():
    result = run([""] * 100, timeout_ms=2000)

    assert result.outcome is StableOutcome.TIMEOUT
    assert result.signal == "never-started"


def test_a_response_that_never_settles_times_out_differently():
    """Distinguishable, because the two send you to look at different things."""
    result = run([f"token {n}" for n in range(200)], timeout_ms=2000)

    assert result.outcome is StableOutcome.TIMEOUT
    assert result.signal == "timeout"


def test_streaming_pauses_shorter_than_the_settle_window_do_not_end_it():
    stream = ["", "a", "a", "ab", "ab", "abc", "abc", "abc", "abc"]
    result = run(stream, stable_for_ms=750)

    assert result.outcome is StableOutcome.STABLE
    assert result.last_value == "abc"


def test_a_long_pause_mid_stream_does_end_it():
    """Honest about the limit: the settle window cannot tell a finished answer
    from a stalled one. That is what a completion signal is for."""
    stream = ["", "a"] + ["a"] * 10 + ["a longer answer"]
    result = run(stream, stable_for_ms=500)

    assert result.outcome is StableOutcome.STABLE
    assert result.last_value == "a"


# -- completion signals -------------------------------------------------------


def test_a_completion_signal_counts_as_the_response_having_started():
    """A stop button that appeared and vanished is proof, even if the sampled
    region never looked different -- an answer rendered outside it, say."""
    fired = {"n": 0}

    def stopped():
        fired["n"] += 1
        return fired["n"] > 4

    result = run([""] * 20, done_signal=stopped)

    assert result.outcome is StableOutcome.STABLE
    assert result.signal == "signal+stable"


def test_the_settle_window_still_applies_after_the_signal():
    """The text lags the spinner; stopping on the signal truncates the tail."""
    stopped = iter([False, False, True] + [True] * 20)
    result = run(["", "a", "ab", "abc", "abc", "abc", "abc"],
                 done_signal=lambda: next(stopped), stable_for_ms=500)

    assert result.last_value == "abc"


def test_without_a_signal_the_result_says_it_settled_on_its_own():
    result = run(["", "a", "a", "a", "a"])
    assert result.signal == "stable"


def test_samples_are_counted_for_the_transcript():
    result = run(["", "a", "a", "a", "a"])
    assert result.samples >= 4
