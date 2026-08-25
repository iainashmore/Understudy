"""Typing speed.

Two things matter and both are easy to lose silently. The prompt has to arrive
one keystroke at a time -- an application that enables its send button on the
first character, or autocompletes, only behaves like it does for a person if it
sees the keys spread out. And the pacing has to be reproducible, because a run
that varies in anything but the prompt is not the comparison this tool exists
to make.

The failure mode these guard against is a real one: the driver imported the
typing helper and then never called it, and the only symptom was a step that
took 200ms instead of three seconds.
"""

from __future__ import annotations

import pytest

from flowrunner.keyboard import (
    DEFAULT_CPS,
    DEFAULT_MAX_TOTAL_S,
    TypingStyle,
    delays_for,
    duration_for,
    type_text,
)

SENTENCE = "Summarise this in one paragraph."


def collect(text: str, style: TypingStyle | None = None):
    """Type `text`, recording what was sent and how long we slept."""
    sent: list[str] = []
    slept: list[float] = []
    count = type_text(text, sent.append, slept.append, style)
    return count, sent, slept


# -- pacing -------------------------------------------------------------------


def test_types_one_character_at_a_time():
    count, sent, _ = collect(SENTENCE)
    assert count == len(SENTENCE)
    assert sent == list(SENTENCE)
    assert "".join(sent) == SENTENCE


def test_default_speed_is_fast_human_not_instant():
    seconds = duration_for(SENTENCE)
    # ~145wpm over 32 characters, plus a beat at the full stop.
    assert 2.0 < seconds < 4.5
    per_character = seconds / len(SENTENCE)
    assert 0.5 / DEFAULT_CPS < per_character < 2.5 / DEFAULT_CPS


def test_every_keystroke_is_followed_by_a_pause():
    _, sent, slept = collect(SENTENCE)
    assert len(slept) == len(sent)
    assert all(delay > 0 for delay in slept)


def test_sentence_end_pauses_longer_than_a_plain_character():
    delays = delays_for("ab. cd, ef")
    plain = delays[0]
    assert delays[2] > plain * 2      # the full stop
    assert delays[6] > plain          # the comma
    assert delays[6] < delays[2]      # but less than the full stop


def test_long_text_is_capped_rather_than_truncated():
    text = "word " * 400
    style = TypingStyle(max_total_s=20.0)
    count, sent, slept = collect(text, style)
    assert count == len(text)
    assert "".join(sent) == text      # nothing dropped
    assert sum(slept) == pytest.approx(20.0, abs=0.01)


def test_cap_does_not_stretch_short_text():
    assert duration_for(SENTENCE) < DEFAULT_MAX_TOTAL_S


# -- determinism --------------------------------------------------------------


def test_same_text_gives_the_same_pacing_every_time():
    assert delays_for(SENTENCE) == delays_for(SENTENCE)


def test_different_text_gives_different_pacing():
    # Otherwise the "variance" is a constant offset and not variance at all.
    assert delays_for(SENTENCE) != delays_for("Summarise this in one sentence.")


def test_pacing_varies_between_keystrokes():
    delays = delays_for("aaaaaaaaaaaaaaaaaaaa")
    assert len(set(delays)) > 10


def test_variance_stays_within_its_bounds():
    style = TypingStyle(variance=0.25, sentence_pause_s=0, clause_pause_s=0)
    base = 1.0 / style.cps
    for delay in delays_for("abcdefghijklmnop", style):
        assert base * 0.75 <= delay <= base * 1.25


# -- modes and configuration --------------------------------------------------


def test_instant_mode_sends_the_text_in_one_go():
    count, sent, slept = collect(SENTENCE, TypingStyle(mode="instant"))
    assert count == len(SENTENCE)
    assert sent == [SENTENCE]         # one call, not 32
    assert slept == []


def test_empty_text_does_nothing():
    count, sent, slept = collect("")
    assert (count, sent, slept) == (0, [], [])


def test_from_config_reads_milliseconds_and_defaults():
    style = TypingStyle.from_config(
        {"cps": 20, "variance": 0.1, "sentence_pause_ms": 500}
    )
    assert style.cps == 20
    assert style.variance == 0.1
    assert style.sentence_pause_s == 0.5
    assert style.clause_pause_s == TypingStyle().clause_pause_s


def test_from_config_of_nothing_is_the_default():
    assert TypingStyle.from_config(None) == TypingStyle()
    assert TypingStyle().animated


def test_faster_cps_takes_less_time():
    quick = duration_for(SENTENCE, TypingStyle(cps=30))
    slow = duration_for(SENTENCE, TypingStyle(cps=6))
    assert quick < slow / 3
