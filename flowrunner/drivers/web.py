"""Playwright web driver.

Target resolution walks the strategy list in order and stops at the first one
that identifies exactly one element. Two rules matter:

  * Ambiguity is not resolution. A strategy matching several elements is
    skipped rather than silently taking the first -- clicking the wrong thing is
    worse than a clean failure. `nth` makes a deliberate choice explicit.
  * Playwright's role/name matching is already case-insensitive and
    whitespace-normalised, so a button relabelled "Send " or "send" still
    resolves. That is the fuzzy matching, for free, from the accessibility tree
    rather than the DOM path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from flowrunner.drivers.base import DriverError, Resolution, TargetNotFound
from flowrunner.flow import Strategy, Target

FORM_CONTROLS = {"input", "textarea", "select"}
RESOLVE_POLL_S = 0.1


def find_chromium() -> str | None:
    """Locate a browser, preferring whatever Playwright already knows about.

    Falls back to scanning PLAYWRIGHT_BROWSERS_PATH, because a pre-provisioned
    browser build often does not match the pip package's expected revision and
    re-downloading is not always possible.
    """
    override = os.environ.get("FLOWRUNNER_CHROMIUM")
    if override:
        return override

    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if not root.exists():
        return None
    candidates = sorted(root.glob("chromium-*/chrome-linux/chrome"))
    candidates += sorted(root.glob("chromium-*/chrome-win/chrome.exe"))
    candidates += sorted(root.glob("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"))
    return str(candidates[-1]) if candidates else None


class WebDriver:
    backend = "web"

    def __init__(self, headless: bool = True, slow_mo_ms: int = 0) -> None:
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None
        self._app_config: dict[str, Any] = {}

    # -- lifecycle ------------------------------------------------------------

    def start(self, app_config: dict[str, Any]) -> None:
        from playwright.sync_api import sync_playwright

        self._app_config = dict(app_config)
        if not self._app_config.get("url"):
            raise DriverError("flow has no target_app.web.url")

        self._playwright = sync_playwright().start()
        launch: dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
        }
        executable = find_chromium()
        if executable:
            launch["executable_path"] = executable
        self._browser = self._playwright.chromium.launch(**launch)
        self._open_context()

    def _open_context(self) -> None:
        options: dict[str, Any] = {}
        storage_state = self._app_config.get("storage_state")
        if storage_state:
            # Log in once by hand, save the state, reuse it every run. Without
            # this every sweep against an authenticated app stops at a login
            # screen.
            if not Path(storage_state).exists():
                raise DriverError(f"storage_state file not found: {storage_state}")
            options["storage_state"] = storage_state
        viewport = self._app_config.get("viewport")
        if viewport:
            # Fixed by default so screenshots are comparable between runs.
            options["viewport"] = {
                "width": viewport["width"], "height": viewport["height"]
            }
        self._context = self._browser.new_context(**options)
        self.page = self._context.new_page()
        self.page.goto(self._app_config["url"])

    def reset(self) -> None:
        """Level-2 isolation: throw the context away and start again."""
        if self._context:
            self._context.close()
        self._open_context()

    def stop(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        if self._playwright:
            self._playwright.stop()
        self._playwright = self._browser = self._context = self.page = None

    # -- resolution -----------------------------------------------------------

    def _locator(self, strategy: Strategy):
        fields = strategy.fields
        exact = bool(fields.get("exact", False))
        page = self.page

        if "testid" in fields:
            locator = page.get_by_test_id(fields["testid"])
        elif "role" in fields:
            name = fields.get("name")
            locator = (
                page.get_by_role(fields["role"], name=name, exact=exact)
                if name is not None
                else page.get_by_role(fields["role"])
            )
        elif "text" in fields:
            locator = page.get_by_text(fields["text"], exact=exact)
        elif "label" in fields:
            locator = page.get_by_label(fields["label"], exact=exact)
        elif "placeholder" in fields:
            locator = page.get_by_placeholder(fields["placeholder"], exact=exact)
        elif "css" in fields:
            locator = page.locator(fields["css"])
        elif "xpath" in fields:
            locator = page.locator("xpath=" + fields["xpath"])
        else:
            raise DriverError(f"strategy has nothing to match on: {strategy.describe()}")

        if "nth" in fields:
            locator = locator.nth(int(fields["nth"]))
        return locator

    def resolve(self, target: Target, timeout_ms: int):
        """First strategy identifying exactly one element wins."""
        strategies = target.for_backend(self.backend)
        deadline = time.monotonic() + timeout_ms / 1000.0
        attempts: list[str] = []

        while True:
            attempts = []
            for index, strategy in enumerate(strategies):
                try:
                    locator = self._locator(strategy)
                    count = locator.count()
                except Exception as exc:
                    attempts.append(f"{strategy.describe()} -> error: {exc}")
                    continue

                if count == 1 or "nth" in strategy.fields:
                    return locator, Resolution(target.name, index, strategy)
                if count == 0:
                    attempts.append(f"{strategy.describe()} -> no match")
                    continue

                # Several matches: prefer the visible one if that disambiguates,
                # otherwise move on rather than guess.
                try:
                    visible = locator.filter(visible=True)
                    if visible.count() == 1:
                        return visible, Resolution(
                            target.name, index, strategy,
                            note=f"{count} matches, one visible",
                        )
                except Exception:
                    pass
                attempts.append(f"{strategy.describe()} -> {count} matches, ambiguous")

            if time.monotonic() >= deadline:
                raise TargetNotFound(target, self.backend, attempts)
            time.sleep(RESOLVE_POLL_S)

    # -- actions --------------------------------------------------------------

    def click(self, target: Target, timeout_ms: int) -> Resolution:
        locator, resolution = self.resolve(target, timeout_ms)
        locator.click(timeout=timeout_ms)
        return resolution

    def type(
        self, target: Target, text: str, timeout_ms: int,
        mode: str = "type", clear: bool = True, delay_ms: int = 0,
    ) -> Resolution:
        locator, resolution = self.resolve(target, timeout_ms)
        if mode == "fill":
            locator.fill(text, timeout=timeout_ms)
            return resolution
        # Real keystrokes by default: apps that listen for input events (most
        # rich editors) do not react to a value being set.
        if clear:
            locator.fill("", timeout=timeout_ms)
        locator.click(timeout=timeout_ms)
        locator.press_sequentially(text, delay=delay_ms, timeout=timeout_ms)
        return resolution

    def read(self, target: Target, timeout_ms: int) -> tuple[str, Resolution]:
        locator, resolution = self.resolve(target, timeout_ms)
        tag = locator.evaluate("node => node.tagName.toLowerCase()")
        raw = (
            locator.input_value(timeout=timeout_ms)
            if tag in FORM_CONTROLS
            else locator.inner_text(timeout=timeout_ms)
        )
        return raw, resolution

    def key(self, keys: str, target: Target | None, timeout_ms: int) -> Resolution | None:
        if target is None:
            self.page.keyboard.press(keys)
            return None
        locator, resolution = self.resolve(target, timeout_ms)
        locator.press(keys, timeout=timeout_ms)
        return resolution

    def screenshot(self, target: Target | None = None, full_page: bool = False) -> bytes:
        if target is None:
            return self.page.screenshot(full_page=full_page)
        locator, _ = self.resolve(target, 5_000)
        return locator.screenshot()

    def exists(self, target: Target, timeout_ms: int = 0) -> bool:
        try:
            self.resolve(target, timeout_ms)
            return True
        except (TargetNotFound, DriverError):
            return False

    def is_visible(self, target: Target) -> bool:
        try:
            locator, _ = self.resolve(target, 0)
            return locator.is_visible()
        except (TargetNotFound, DriverError):
            return False

    def wait_for_element(self, target: Target, state: str, timeout_ms: int) -> Resolution:
        if state == "enabled":
            locator, resolution = self.resolve(target, timeout_ms)
            deadline = time.monotonic() + timeout_ms / 1000.0
            while time.monotonic() < deadline:
                if locator.is_enabled():
                    return resolution
                time.sleep(RESOLVE_POLL_S)
            raise DriverError(f"target {target.name!r} did not become enabled")

        if state == "hidden":
            # Absent counts as hidden; resolution failure is the expected path.
            deadline = time.monotonic() + timeout_ms / 1000.0
            while time.monotonic() < deadline:
                if not self.is_visible(target):
                    return Resolution(target.name, 0, note="hidden or absent")
                time.sleep(RESOLVE_POLL_S)
            raise DriverError(f"target {target.name!r} did not become hidden")

        locator, resolution = self.resolve(target, timeout_ms)
        locator.wait_for(state=state, timeout=timeout_ms)
        return resolution
