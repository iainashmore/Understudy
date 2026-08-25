"""Typing at a human speed.

Automation fills a field instantly. On a screen recording that reads as a value
appearing from nowhere, and it also skips whatever the application does between
keystrokes -- autocomplete, validation, a send button enabling on the first
character. Typing it out exercises the same code path a person would.

Fast, though: the default is about 12 characters a second, roughly 145 words a
minute. Quick enough not to pad a recording, slow enough to read.

The variation between keystrokes is deterministic, seeded on the text, for the
same reason the pointer's wobble is: a tool whose value is that only the prompt
varies between runs cannot introduce real randomness anywhere, including in
places that only show up on video.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

#: Characters a second. ~145 wpm: a fast typist, not a machine.
DEFAULT_CPS = 12.0
#: How much each keystroke may deviate, as a fraction of the base interval.
DEFAULT_VARIANCE = 0.35
#: Extra beat after a sentence ends, where a person pauses.
SENTENCE_PAUSE_S = 0.22
CLAUSE_PAUSE_S = 0.09
#: However long the text, typing it out should not dominate the recording.
DEFAULT_MAX_TOTAL_S = 20.0

SENTENCE_ENDINGS = ".!?"
CLAUSE_ENDINGS = ",;:"

#: Characters that Windows SendKeys syntax (which pywinauto speaks) reads as
#: instructions rather than as text. Left alone, a prompt mentioning "C++" holds
#: Shift down, "50%" presses Alt, and "~" sends Enter -- submitting the prompt
#: halfway through typing it. Each is escaped by wrapping it in braces.
SEND_KEYS_SPECIAL = set("^%+~(){}[]")


@dataclass(frozen=True)
class TypingStyle:
    mode: str = "human"
    cps: float = DEFAULT_CPS
    variance: float = DEFAULT_VARIANCE
    sentence_pause_s: float = SENTENCE_PAUSE_S
    clause_pause_s: float = CLAUSE_PAUSE_S
    max_total_s: float = DEFAULT_MAX_TOTAL_S

    @property
    def animated(self) -> bool:
        return self.mode == "human" and self.cps > 0

    @classmethod
    def from_config(cls, config: dict | None) -> "TypingStyle":
        config = config or {}
        return cls(
            mode=str(config.get("mode", "human")),
            cps=float(config.get("cps", DEFAULT_CPS)),
            variance=float(config.get("variance", DEFAULT_VARIANCE)),
            sentence_pause_s=float(
                config.get("sentence_pause_ms", SENTENCE_PAUSE_S * 1000)
            ) / 1000.0,
            clause_pause_s=float(
                config.get("clause_pause_ms", CLAUSE_PAUSE_S * 1000)
            ) / 1000.0,
            max_total_s=float(config.get("max_total_s", DEFAULT_MAX_TOTAL_S)),
        )


def delays_for(text: str, style: TypingStyle | None = None) -> list[float]:
    """Seconds to wait after each character. One entry per character."""
    style = style or TypingStyle()
    if not text:
        return []
    if not style.animated:
        return [0.0] * len(text)

    base = 1.0 / style.cps
    rng = random.Random(f"{style.cps}:{text}")
    delays: list[float] = []
    for character in text:
        delay = base * (1.0 + rng.uniform(-style.variance, style.variance))
        if character in SENTENCE_ENDINGS:
            delay += style.sentence_pause_s
        elif character in CLAUSE_ENDINGS:
            delay += style.clause_pause_s
        delays.append(max(0.0, delay))

    total = sum(delays)
    if style.max_total_s and total > style.max_total_s:
        # A very long prompt should not turn into a minute of watching someone
        # type. Speed up proportionally rather than truncating the text.
        scale = style.max_total_s / total
        delays = [delay * scale for delay in delays]
    return delays


def duration_for(text: str, style: TypingStyle | None = None) -> float:
    return sum(delays_for(text, style))


def type_text(text: str, send, sleep, style: TypingStyle | None = None) -> int:
    """Send `text` one character at a time, pausing between them.

    `send` and `sleep` are injected so this can be exercised without a browser
    or a desktop. Returns the number of characters sent.
    """
    style = style or TypingStyle()
    if not text:
        return 0
    if not style.animated:
        send(text)
        return len(text)

    for character, delay in zip(text, delays_for(text, style)):
        send(character)
        if delay:
            sleep(delay)
    return len(text)


def escape_send_keys(text: str) -> str:
    """Quote `text` so Windows SendKeys types it literally.

    Only for the native backend; browsers take the characters as they are.
    """
    return "".join(
        "{" + character + "}" if character in SEND_KEYS_SPECIAL else character
        for character in text
    )
