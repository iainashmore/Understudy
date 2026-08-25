"""Both drivers must actually type, not fill.

This exists because the wiring broke once and nothing caught it: the web driver
imported the typing helper, never called it, and every test still passed. The
only visible symptom was a step that took 200ms instead of three seconds, which
no assertion was looking at. These tests look at the keystrokes.
"""

from __future__ import annotations

import pytest

from flowrunner.drivers.base import Resolution
from flowrunner.drivers.native import NativeDriver
from flowrunner.drivers.web import WebDriver
from flowrunner.flow import Target
from flowrunner.keyboard import TypingStyle

PROMPT = "Rename to Housing, then explain why."
TARGET = Target(name="prompt_box", intent="", strategies={})


def resolution() -> Resolution:
    return Resolution(target="prompt_box", index=0)


@pytest.fixture
def no_sleep(monkeypatch):
    """Run the pacing without paying for it."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


# -- web ----------------------------------------------------------------------


class FakeKeyboard:
    def __init__(self):
        self.chunks: list[str] = []

    def type(self, text, delay=None):
        self.chunks.append(text)

    def press(self, keys):
        self.chunks.append(f"<{keys}>")


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()


class FakeLocator:
    def __init__(self):
        self.filled: list[str] = []
        self.sequential: list[str] = []
        self.clicks = 0

    def fill(self, text, timeout=None):
        self.filled.append(text)

    def click(self, timeout=None):
        self.clicks += 1

    def press_sequentially(self, text, delay=0, timeout=None):
        self.sequential.append(text)


def web_driver(monkeypatch, style: TypingStyle):
    driver = WebDriver()
    driver.page = FakePage()
    driver.typing_style = style
    locator = FakeLocator()
    monkeypatch.setattr(driver, "resolve", lambda target, timeout: (locator, resolution()))
    return driver, locator


def test_web_sends_one_keystroke_per_character(monkeypatch, no_sleep):
    driver, locator = web_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000)

    assert driver.page.keyboard.chunks == list(PROMPT)
    assert locator.sequential == []          # not one indivisible burst
    assert len(no_sleep) == len(PROMPT)      # paced, not dumped


def test_web_typing_takes_human_time(monkeypatch, no_sleep):
    driver, _ = web_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000)
    assert 2.0 < sum(no_sleep) < 6.0


def test_web_clears_before_typing(monkeypatch, no_sleep):
    driver, locator = web_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000, clear=True)
    assert locator.filled == [""]


def test_web_fill_mode_stays_instant(monkeypatch, no_sleep):
    """`mode: fill` is the deliberate opt-out; it should not be paced."""
    driver, locator = web_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000, mode="fill")
    assert locator.filled == [PROMPT]
    assert driver.page.keyboard.chunks == []
    assert no_sleep == []


def test_web_explicit_step_delay_overrides_the_style(monkeypatch, no_sleep):
    driver, locator = web_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000, delay_ms=30)
    assert locator.sequential == [PROMPT]
    assert driver.page.keyboard.chunks == []


def test_web_instant_style_sends_the_text_in_one_go(monkeypatch, no_sleep):
    driver, _ = web_driver(monkeypatch, TypingStyle(mode="instant"))
    driver.type(TARGET, PROMPT, timeout_ms=5000)
    assert driver.page.keyboard.chunks == [PROMPT]
    assert no_sleep == []


# -- native -------------------------------------------------------------------


class FakeHandle:
    def __init__(self):
        self.keys: list[str] = []
        self.clicks = 0

    def click_input(self):
        self.clicks += 1

    def type_keys(self, keys, **kwargs):
        self.keys.append(keys)


def native_driver(monkeypatch, style: TypingStyle):
    driver = NativeDriver()
    driver.typing_style = style
    handle = FakeHandle()
    monkeypatch.setattr(driver, "resolve", lambda target, timeout: (handle, resolution()))
    monkeypatch.setattr(driver, "_approach", lambda handle: None)
    monkeypatch.setattr(driver, "refresh", lambda: None)
    return driver, handle


def test_native_sends_one_keystroke_per_character(monkeypatch, no_sleep):
    driver, handle = native_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, PROMPT, timeout_ms=5000, clear=False)

    assert handle.keys == list(PROMPT)
    assert len(no_sleep) == len(PROMPT)


def test_native_clear_selects_all_and_deletes(monkeypatch, no_sleep):
    driver, handle = native_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, "ab", timeout_ms=5000, clear=True)
    assert handle.keys[0] == "^a{DELETE}"
    assert handle.keys[1:] == ["a", "b"]


def test_native_escapes_sendkeys_syntax_in_the_prompt(monkeypatch, no_sleep):
    """Unescaped, "~" is Enter -- the prompt submits itself mid-sentence."""
    driver, handle = native_driver(monkeypatch, TypingStyle())
    driver.type(TARGET, "C++ ~ 50%", timeout_ms=5000, clear=False)

    assert handle.keys == ["C", "{+}", "{+}", " ", "{~}", " ", "5", "0", "{%}"]


# -- pointer ------------------------------------------------------------------


class FakeMouse:
    def __init__(self):
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []

    def move(self, x, y):
        self.moves.append((int(x), int(y)))

    def click(self, x, y):
        self.clicks.append((int(x), int(y)))


class FakeBoxLocator(FakeLocator):
    """A locator that knows where it is, like a real one."""

    def __init__(self, box):
        super().__init__()
        self._box = box

    def bounding_box(self):
        return self._box


def pointer_driver(monkeypatch, locator, style=None):
    driver = WebDriver()
    driver.page = FakePage()
    driver.page.mouse = FakeMouse()
    driver.page.viewport_size = {"width": 900, "height": 700}
    driver.typing_style = TypingStyle(mode="instant")
    if style is not None:
        driver.mouse_style = style
    monkeypatch.setattr(driver, "resolve", lambda target, timeout: (locator, resolution()))
    return driver


def test_pointer_travels_to_a_locator_rather_than_jumping(monkeypatch, no_sleep):
    locator = FakeBoxLocator({"x": 700, "y": 100, "width": 80, "height": 30})
    driver = pointer_driver(monkeypatch, locator)
    driver.click(TARGET, timeout_ms=5000)

    moves = driver.page.mouse.moves
    assert len(moves) > 10                    # a path, not a teleport
    assert moves[0] == (450, 690)             # parked at the foot of the page
    assert moves[-1] == (740, 115)            # the locator's centre
    assert locator.clicks == 1


def test_pointer_path_is_continuous(monkeypatch, no_sleep):
    locator = FakeBoxLocator({"x": 700, "y": 100, "width": 80, "height": 30})
    driver = pointer_driver(monkeypatch, locator)
    driver.click(TARGET, timeout_ms=5000)

    moves = driver.page.mouse.moves
    hops = [
        max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        for a, b in zip(moves, moves[1:])
    ]
    assert max(hops) < 60                     # no jump big enough to look like one


def test_pointer_resumes_from_where_it_was_left(monkeypatch, no_sleep):
    locator = FakeBoxLocator({"x": 700, "y": 100, "width": 80, "height": 30})
    driver = pointer_driver(monkeypatch, locator)
    driver.click(TARGET, timeout_ms=5000)
    first_end = driver.page.mouse.moves[-1]

    driver.page.mouse.moves.clear()
    locator._box = {"x": 100, "y": 500, "width": 40, "height": 20}
    driver.click(TARGET, timeout_ms=5000)

    assert driver.page.mouse.moves[0] == first_end
    assert driver.page.mouse.moves[-1] == (120, 510)


def test_pointer_travels_before_typing_too(monkeypatch, no_sleep):
    locator = FakeBoxLocator({"x": 40, "y": 300, "width": 500, "height": 60})
    driver = pointer_driver(monkeypatch, locator)
    driver.type(TARGET, "hello", timeout_ms=5000)
    assert len(driver.page.mouse.moves) > 10


def test_instant_mouse_style_teleports(monkeypatch, no_sleep):
    from flowrunner.cursor import MouseStyle

    locator = FakeBoxLocator({"x": 700, "y": 100, "width": 80, "height": 30})
    driver = pointer_driver(monkeypatch, locator, MouseStyle(mode="instant"))
    driver.click(TARGET, timeout_ms=5000)
    assert driver.page.mouse.moves == []      # Playwright's own click still lands


def test_a_locator_without_a_box_is_clicked_anyway(monkeypatch, no_sleep):
    """A detached or zero-size element should not take the run down."""
    locator = FakeBoxLocator(None)
    driver = pointer_driver(monkeypatch, locator)
    driver.click(TARGET, timeout_ms=5000)
    assert locator.clicks == 1
