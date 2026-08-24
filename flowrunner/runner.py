"""The runner.

For each prompt variant: reset, execute the steps, capture what happened, write
a row, move on. A variant that fails never stops the sweep -- a broken run in
the middle of fifty is a result, not a reason to lose the other forty-nine.

Results stream to JSONL as they complete, so a sweep interrupted at variant
thirty still leaves thirty readable rows. The flow and prompts as executed are
copied into the run directory, because in a month neither file will still say
what it said today.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from flowrunner.drivers.base import Driver, DriverError, Resolution, TargetNotFound
from flowrunner.flow import Flow, Step, render_step
from flowrunner.ocr import read_text
from flowrunner.prompts import PromptSet, PromptVariant
from flowrunner.waiting import (
    StableOutcome,
    pixels_equivalent,
    text_equivalent,
    wait_until_stable,
)


class Status(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class StepStatus:
    index: int
    phase: str
    action: str
    status: Status
    duration_ms: int
    target: str | None = None
    error: str | None = None
    resolution: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        record = {
            "index": self.index,
            "phase": self.phase,
            "action": self.action,
            "target": self.target,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
        }
        if self.error:
            record["error"] = self.error
        if self.resolution:
            record["resolution"] = self.resolution
        if self.detail:
            record["detail"] = self.detail
        return record


@dataclass
class VariantResult:
    prompt_id: str
    variables: dict[str, str]
    status: Status
    duration_ms: int
    backend: str
    timestamp: str
    repeat_index: int = 0
    reads: dict[str, str] = field(default_factory=dict)
    #: Where a read came from pixels, the pixels are kept: a transcription is a
    #: lossy derivative and the image is the evidence.
    read_images: dict[str, str] = field(default_factory=dict)
    step_statuses: list[StepStatus] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def response(self) -> str:
        return self.reads.get("response", "")

    @property
    def used_fallbacks(self) -> list[str]:
        """Steps that resolved on something other than the preferred strategy.
        The early warning that the UI has moved."""
        return [
            f"{status.target}#{status.resolution['strategy_index']}"
            for status in self.step_statuses
            if status.resolution and status.resolution.get("used_fallback")
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "repeat_index": self.repeat_index,
            "prompt": self.variables.get("prompt", ""),
            "variables": self.variables,
            "response": self.response,
            "reads": self.reads,
            "read_images": self.read_images,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "step_statuses": [status.as_dict() for status in self.step_statuses],
            "screenshots": self.screenshots,
            "used_fallbacks": self.used_fallbacks,
            "backend": self.backend,
            "timestamp": self.timestamp,
            "error": self.error,
        }


CSV_COLUMNS = [
    "prompt_id", "repeat_index", "prompt", "response", "status", "duration_ms",
    "backend", "used_fallbacks", "error",
]


def run_directory(root: Path | str = "runs", now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%S")
    return Path(root) / stamp


class Runner:
    def __init__(
        self,
        flow: Flow,
        driver: Driver,
        out_dir: Path | str,
        reset_level: int = 1,
    ) -> None:
        self.flow = flow
        self.driver = driver
        self.out_dir = Path(out_dir)
        self.reset_level = reset_level
        self.results_path = self.out_dir / "results.jsonl"

    # -- setup ----------------------------------------------------------------

    def prepare(self, prompts: PromptSet) -> None:
        """Copy in what is being executed. Weeks later, the question is always
        'what did this actually run', and by then both files have changed."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.flow.source_text:
            (self.out_dir / "flow.yaml").write_text(self.flow.source_text, encoding="utf-8")
        elif self.flow.source_path:
            shutil.copy2(self.flow.source_path, self.out_dir / "flow.yaml")
        if prompts.source_text:
            name = "prompts.csv" if (
                prompts.source_path and prompts.source_path.suffix.lower() == ".csv"
            ) else "prompts.yaml"
            (self.out_dir / name).write_text(prompts.source_text, encoding="utf-8")

    # -- the loop -------------------------------------------------------------

    def run(self, prompts: PromptSet, repeats: int = 1) -> list[VariantResult]:
        self.prepare(prompts)
        results: list[VariantResult] = []

        for variant in prompts:
            for repeat in range(repeats):
                result = self.run_variant(variant, repeat, repeats)
                results.append(result)
                self._append(result)
        return results

    def variant_directory(self, variant: PromptVariant, repeat: int, repeats: int) -> str:
        # Matches the spec's layout for a single run; repeats would otherwise
        # overwrite each other's screenshots.
        return variant.id if repeats == 1 else f"{variant.id}-{repeat + 1:02d}"

    def run_variant(
        self, variant: PromptVariant, repeat: int = 0, repeats: int = 1
    ) -> VariantResult:
        started = time.monotonic()
        folder = self.variant_directory(variant, repeat, repeats)
        result = VariantResult(
            prompt_id=variant.id,
            repeat_index=repeat,
            variables=dict(variant.variables),
            status=Status.OK,
            duration_ms=0,
            backend=self.driver.backend,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        counter = {"n": 0}

        try:
            if self.reset_level >= 2:
                self.driver.reset()
            for step in self.flow.reset:
                self._run_step(step, variant, result, folder, counter)
            for step in self.flow.steps:
                self._run_step(step, variant, result, folder, counter)
        except Exception as exc:
            # Anything the per-step handling did not already turn into a status.
            result.status = Status.ERROR
            result.error = f"{type(exc).__name__}: {exc}"

        worst = {status.status for status in result.step_statuses}
        if result.status is Status.OK:
            if Status.ERROR in worst:
                result.status = Status.ERROR
            elif Status.TIMEOUT in worst:
                result.status = Status.TIMEOUT

        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # -- steps ----------------------------------------------------------------

    def _run_step(
        self, raw_step: Step, variant: PromptVariant,
        result: VariantResult, folder: str, counter: dict[str, int],
    ) -> None:
        step = render_step(raw_step, variant.variables)
        timeout = int(step.params.get("timeout_ms", self.flow.defaults.timeout_ms))
        started = time.monotonic()
        status = StepStatus(
            index=step.index, phase=step.phase, action=step.action,
            status=Status.OK, duration_ms=0, target=step.target,
        )

        try:
            self._dismiss_interstitials()
            self._dispatch(step, timeout, result, folder, counter, status)
        except TargetNotFound as exc:
            if step.optional:
                # Declared as maybe-absent: a dialog that sometimes appears is
                # not a failure when it does not.
                status.status = Status.SKIPPED
                status.error = "target not found (step marked optional)"
            else:
                status.status = Status.ERROR
                status.error = str(exc)
                self._capture_failure(step, result, folder, counter)
        except DriverError as exc:
            status.status = Status.ERROR
            status.error = str(exc)
            self._capture_failure(step, result, folder, counter)
        except Exception as exc:
            status.status = Status.ERROR
            status.error = f"{type(exc).__name__}: {exc}"
            self._capture_failure(step, result, folder, counter)

        status.duration_ms = int((time.monotonic() - started) * 1000)
        result.step_statuses.append(status)

    def _dispatch(
        self, step: Step, timeout: int, result: VariantResult,
        folder: str, counter: dict[str, int], status: StepStatus,
    ) -> None:
        action = step.action
        target = self.flow.target_for(step.target) if step.target else None

        if action == "click":
            status.resolution = self.driver.click(target, timeout).as_dict()

        elif action == "type":
            resolution = self.driver.type(
                target, step.params["text"], timeout,
                mode=step.params.get("mode", "type"),
                clear=bool(step.params.get("clear", True)),
                delay_ms=int(step.params.get("delay_ms", 0)),
            )
            status.resolution = resolution.as_dict()
            status.detail["chars"] = len(step.params["text"])

        elif action == "read":
            self._read(step, target, timeout, result, folder, counter, status)

        elif action == "key":
            resolution = self.driver.key(step.params["keys"], target, timeout)
            status.resolution = resolution.as_dict() if resolution else None

        elif action == "capture":
            path = self._capture(
                step.params["label"], result, folder, counter,
                target=target, full_page=bool(step.params.get("full_page", False)),
                region=step.params.get("region"),
            )
            status.detail["screenshot"] = path

        elif action == "wait_for_element":
            status.resolution = self.driver.wait_for_element(
                target, step.params.get("state", "visible"), timeout
            ).as_dict()

        elif action == "wait_for_stable":
            self._wait_for_stable(step, target, timeout, status)

        else:
            raise DriverError(f"unsupported action {action!r}")

    def _read(
        self, step: Step, target, timeout: int, result: VariantResult,
        folder: str, counter: dict[str, int], status: StepStatus,
    ) -> None:
        store_as = step.params["store_as"]
        region = step.params.get("region")
        mode = step.params.get("mode", "text")

        if mode == "text" and not region:
            text, resolution = self.driver.read(target, timeout)
            status.resolution = resolution.as_dict()
        else:
            image = self.driver.screenshot(
                target=None if region else target, region=region
            )
            result.read_images[store_as] = self._write_image(
                f"{store_as}-source", result, folder, counter, image
            )
            outcome = read_text(image)
            text = outcome.text
            status.detail["ocr_engine"] = outcome.engine
            if not outcome.available:
                # Never let "could not read" masquerade as "said nothing".
                status.status = Status.ERROR
                status.error = outcome.error

        result.reads[store_as] = text
        status.detail["chars"] = len(text)

    def _wait_for_stable(self, step: Step, target, timeout: int, status: StepStatus) -> None:
        stable_for = int(step.params.get("stable_for_ms", self.flow.defaults.stable_for_ms))
        poll = int(step.params.get("poll_interval_ms", self.flow.defaults.poll_interval_ms))
        timeout_ms = int(step.params.get("timeout_ms", 120_000))

        signal = None
        hidden_target = step.params.get("until_hidden")
        if hidden_target:
            watched = self.flow.target_for(hidden_target)
            signal = lambda: not self.driver.is_visible(watched)  # noqa: E731

        mode = step.params.get("mode", "text")
        region = step.params.get("region")
        if mode == "pixels":
            sample = lambda: self.driver.screenshot(  # noqa: E731
                target=None if region else target, region=region
            )
            equivalent = pixels_equivalent
        else:
            def sample():
                try:
                    return self.driver.read(target, 0)[0]
                except (TargetNotFound, DriverError):
                    # The response area often does not exist until the reply
                    # starts arriving. Absent reads as empty, not as an error.
                    return ""
            equivalent = text_equivalent

        outcome = wait_until_stable(
            sample=sample, equivalent=equivalent, stable_for_ms=stable_for,
            timeout_ms=timeout_ms, poll_interval_ms=poll, done_signal=signal,
        )
        status.detail.update(
            waited_ms=outcome.waited_ms, samples=outcome.samples,
            signal=outcome.signal, mode=mode,
        )
        if outcome.outcome is StableOutcome.TIMEOUT:
            # A timeout is a step status, never a crash: the run still produces
            # a row and its screenshots.
            status.status = Status.TIMEOUT
            status.error = f"still changing after {timeout_ms}ms"

    def _dismiss_interstitials(self) -> None:
        """Cookie banners and 'what's new' modals: not part of the flow, and
        they appear unpredictably."""
        for name in self.flow.interstitials:
            target = self.flow.target_for(name)
            try:
                if self.driver.exists(target, 0):
                    self.driver.click(target, 2_000)
            except (TargetNotFound, DriverError):
                pass

    # -- output ---------------------------------------------------------------

    def _write_image(
        self, label: str, result: VariantResult, folder: str,
        counter: dict[str, int], image: bytes,
    ) -> str:
        counter["n"] += 1
        relative = f"{folder}/{counter['n']:02d}-{label}.png"
        path = self.out_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        result.screenshots.append(relative)
        return relative

    def _capture(
        self, label: str, result: VariantResult, folder: str,
        counter: dict[str, int], target=None, full_page: bool = False,
        region: dict[str, int] | None = None,
    ) -> str:
        return self._write_image(
            label, result, folder, counter,
            self.driver.screenshot(target=target, full_page=full_page, region=region),
        )

    def _capture_failure(
        self, step: Step, result: VariantResult, folder: str, counter: dict[str, int]
    ) -> None:
        """Not in the spec, but the screenshot of the moment it broke is the
        first thing anyone asks for."""
        try:
            self._capture(f"FAILED-{step.action}", result, folder, counter)
        except Exception:
            pass

    def _append(self, result: VariantResult) -> None:
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")


def write_csv(results: list[VariantResult], path: Path | str) -> Path:
    """Comparing response variations happens in a spreadsheet more often than
    not."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for result in results:
            row = result.as_dict()
            writer.writerow({
                column: (
                    ",".join(row["used_fallbacks"])
                    if column == "used_fallbacks"
                    else row.get(column, "")
                )
                for column in CSV_COLUMNS
            })
    return path
