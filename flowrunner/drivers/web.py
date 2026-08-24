"""Playwright web driver, in two modes.

**Launch** -- start a browser and navigate. The ordinary web case.

**Attach** -- connect over the Chrome DevTools Protocol to a Chromium that is
already running inside someone else's process. This is the mode that matters
for an assistant panel embedded in a desktop application: WebView2 and CEF are
Chromium, and when the host exposes a debugging port the panel is drivable as a
normal page, with full DOM access, exact text reads and real selectors. That is
enormously better than clicking at an accessibility tree that may not describe
the content at all.

Enabling the port is a host-side setting:

    WebView2   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
    CEF        --remote-debugging-port=9222 on the host executable

Check with http://127.0.0.1:9222/json -- if it lists pages, attach mode works.

In attach mode the browser belongs to the host application, so this driver
never closes it and cannot offer level-2 reset.

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

import fnmatch
import os
import time
from pathlib import Path
from typing import Any

from flowrunner.drivers.base import DriverError, Resolution, TargetNotFound
from flowrunner.flow import Strategy, Target
from flowrunner.learned import LearnedAnchors
from flowrunner.resolvers import NullResolver, Resolver
from flowrunner.vision import crop, locate_all
from harness.image import to_png_bytes

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


class _AnchorTarget:
    """A point found by locating an anchor image in the current screenshot.

    Exposes the slice of the locator interface the driver uses, so an anchor and
    a real element are interchangeable to everything above. Text cannot be read
    from pixels, and saying so plainly beats returning an empty string that
    looks like an empty response.
    """

    def __init__(self, page, match, offset: dict[str, int] | None = None) -> None:
        self.page = page
        self.match = match
        self.offset = offset or {}

    @property
    def point(self) -> tuple[int, int]:
        x, y = self.match.centre
        return x + int(self.offset.get("dx", 0)), y + int(self.offset.get("dy", 0))

    def click(self, timeout: int | None = None) -> None:
        x, y = self.point
        self.page.mouse.click(x, y)

    def press(self, keys: str, timeout: int | None = None) -> None:
        self.click()
        self.page.keyboard.press(keys)

    def press_sequentially(self, text: str, delay: int = 0, timeout: int | None = None) -> None:
        self.click()
        self.page.keyboard.type(text, delay=delay)

    def fill(self, text: str, timeout: int | None = None) -> None:
        """Select all, delete, then type.

        The delete is not optional. Selecting and typing an empty string leaves
        the selection untouched, so the next keystrokes append instead of
        replacing -- and every prompt variant after the first goes out carrying
        the previous one. That contaminates precisely the comparison this tool
        exists to make, and it does so silently.
        """
        self.click()
        self.page.keyboard.press("Control+a")
        self.page.keyboard.press("Delete")
        if text:
            self.page.keyboard.type(text)

    def screenshot(self) -> bytes:
        return self.page.screenshot(clip={
            "x": self.match.x, "y": self.match.y,
            "width": self.match.width, "height": self.match.height,
        })

    def inner_text(self, timeout: int | None = None) -> str:
        raise DriverError(
            "this target is an image anchor: it has no text to read. Use "
            "wait_for_stable with mode: pixels, or read from a different target."
        )

    input_value = inner_text

    def evaluate(self, _expression: str):
        return "anchor"

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class WebDriver:
    backend = "web"

    #: off      -- agent rungs are skipped entirely (the default)
    #: fallback -- the agent is asked only when every deterministic strategy fails
    #: only     -- deterministic strategies are ignored; the agent resolves everything
    AGENT_MODES = ("off", "fallback", "only")

    def __init__(
        self,
        headless: bool = True,
        slow_mo_ms: int = 0,
        resolver: Resolver | None = None,
        agent_mode: str = "off",
        learned_dir: str | None = None,
    ) -> None:
        if agent_mode not in self.AGENT_MODES:
            raise ValueError(
                f"agent_mode must be one of {self.AGENT_MODES}, got {agent_mode!r}"
            )
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.resolver: Resolver = resolver or NullResolver()
        self.agent_mode = agent_mode
        self.learned = LearnedAnchors(learned_dir)
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None
        self._app_config: dict[str, Any] = {}
        self.attached = False

    # -- lifecycle ------------------------------------------------------------

    def start(self, app_config: dict[str, Any]) -> None:
        from playwright.sync_api import sync_playwright

        self._app_config = dict(app_config)
        self._playwright = sync_playwright().start()

        if self._app_config.get("cdp_url"):
            self._attach()
            return

        if not self._app_config.get("url"):
            raise DriverError("flow has no target_app.web.url")
        launch: dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
        }
        executable = find_chromium()
        if executable:
            launch["executable_path"] = executable
        self._browser = self._playwright.chromium.launch(**launch)
        self._open_context()

    def _attach(self) -> None:
        """Connect to a Chromium already running inside another process."""
        cdp_url = self._app_config["cdp_url"]
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            raise DriverError(
                f"could not attach to {cdp_url}: {exc}. Is the host running with "
                f"remote debugging enabled? Check {cdp_url.rstrip('/')}/json"
            ) from None

        self.attached = True
        contexts = self._browser.contexts
        if not contexts:
            raise DriverError(f"attached to {cdp_url} but it has no browser contexts")
        self._context = contexts[0]

        pages = [page for context in contexts for page in context.pages]
        if not pages:
            raise DriverError(f"attached to {cdp_url} but it has no open pages")
        self.page = self._select_page(pages)

        if self._app_config.get("navigate") and self._app_config.get("url"):
            self.page.goto(self._app_config["url"])

    def _select_page(self, pages: list):
        """A host may run several web views; pick the one the flow names."""
        url_pattern = self._app_config.get("page_url_pattern")
        title_pattern = self._app_config.get("page_title_pattern")

        for page in pages:
            if url_pattern and not fnmatch.fnmatch(page.url, url_pattern):
                continue
            if title_pattern and not fnmatch.fnmatch(page.title(), title_pattern):
                continue
            return page

        if url_pattern or title_pattern:
            listing = "\n".join(f"    {p.url!r} ({p.title()!r})" for p in pages)
            raise DriverError(
                f"no attached page matches "
                f"url={url_pattern!r} title={title_pattern!r}; available:\n{listing}"
            )
        return pages[0]

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
        if self.attached:
            raise DriverError(
                "level-2 reset is not available when attached over CDP: the "
                "browser belongs to the host application. Use level-1 in-app "
                "reset steps instead."
            )
        if self._context:
            self._context.close()
        self._open_context()

    def stop(self) -> None:
        # Never close a browser we did not launch -- that would take the host
        # application's panel down with it.
        closers = [self._browser] if self.attached else [self._context, self._browser]
        for closer in closers:
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

    def _resolve_anchor(self, strategy: Strategy):
        """Locate an anchor image in the current screenshot.

        Coordinates are derived now, from this run's pixels, rather than stored
        at record time -- which is what keeps this within the spirit of the
        never-store-coordinates rule.
        """
        matches = locate_all(
            self.page.screenshot(),
            Path(strategy.fields["image"]).read_bytes(),
            threshold=float(strategy.fields.get("threshold", 0.9)),
            region=strategy.fields.get("region"),
        )
        if len(matches) != 1:
            return None, f"{len(matches)} visual match(es)"
        handle = _AnchorTarget(self.page, matches[0], strategy.fields.get("offset"))
        return handle, f"score {matches[0].score:.3f}"

    def _agent_hint(self, target: Target, strategy: Strategy | None) -> str | None:
        value = strategy.fields.get("agent") if strategy else None
        return value if isinstance(value, str) else None

    def _resolve_by_agent(self, target: Target, strategy: Strategy | None):
        """Learned anchor first, then the model.

        Once the agent has found a control its crop is an ordinary anchor, so
        the second run onwards is deterministic again.
        """
        learned = self.learned.get(target.name)
        if learned is not None:
            matches = locate_all(self.page.screenshot(), learned, threshold=0.93)
            if len(matches) == 1:
                return (
                    _AnchorTarget(self.page, matches[0]),
                    "learned-anchor",
                    f"cached, score {matches[0].score:.3f}",
                )
            # The learned anchor has stopped working: drop it and ask again
            # rather than carrying a stale one forever.
            self.learned.forget(target.name)

        if self.agent_mode == "off":
            return None, "agent", "agent resolution is off"

        intent = target.intent or self._agent_hint(target, strategy) or ""
        if not intent:
            return None, "agent", "no intent to guide the agent"

        screenshot = self.page.screenshot()
        found = self.resolver.locate(
            screenshot, intent, self._agent_hint(target, strategy)
        )
        if found is None:
            return None, "agent", "agent did not find it"

        self.learned.put(
            target.name,
            to_png_bytes(crop(screenshot, found.as_region())),
            note=found.reasoning,
        )
        from flowrunner.vision import Match

        match = Match(found.x, found.y, found.width, found.height, found.confidence)
        return (
            _AnchorTarget(self.page, match),
            "agent",
            f"confidence {found.confidence:.2f}",
        )

    def resolve(self, target: Target, timeout_ms: int):
        """First strategy identifying exactly one element wins."""
        strategies = target.for_backend(self.backend)
        deadline = time.monotonic() + timeout_ms / 1000.0
        attempts: list[str] = []

        if self.agent_mode == "only":
            # Deterministic strategies are ignored on purpose: this mode exists
            # to measure what the agent alone can do.
            agent_strategy = next(
                (s for s in strategies if "agent" in s.fields), None
            )
            handle, via, note = self._resolve_by_agent(target, agent_strategy)
            if handle is not None:
                return handle, Resolution(target.name, 0, agent_strategy, note, via)
            raise TargetNotFound(target, self.backend, [f"agent-only: {note}"])

        while True:
            attempts = []
            for index, strategy in enumerate(strategies):
                if "agent" in strategy.fields:
                    handle, via, note = self._resolve_by_agent(target, strategy)
                    if handle is not None:
                        return handle, Resolution(
                            target.name, index, strategy, note, via
                        )
                    attempts.append(f"{strategy.describe()} -> {note}")
                    continue

                if "image" in strategy.fields:
                    try:
                        handle, note = self._resolve_anchor(strategy)
                    except Exception as exc:
                        attempts.append(f"{strategy.describe()} -> error: {exc}")
                        continue
                    if handle is not None:
                        return handle, Resolution(
                            target.name, index, strategy, note, "anchor"
                        )
                    attempts.append(f"{strategy.describe()} -> {note}")
                    continue

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

    def screenshot(
        self, target: Target | None = None, full_page: bool = False,
        region: dict[str, int] | None = None,
    ) -> bytes:
        if region:
            # A region is what makes pixel stability usable on an opaque
            # surface: the whole window may contain something that never stops
            # moving, so the check has to be scoped to the part that matters.
            return self.page.screenshot(clip={
                "x": region["x"], "y": region["y"],
                "width": region["width"], "height": region["height"],
            })
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
