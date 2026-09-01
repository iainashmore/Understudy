"""Plain-language descriptions of what each step does.

"click prompt_box" tells a reader nothing they could not have guessed from the
flow file. "Select PartBody in the specification tree" tells them what the run
actually did, which is what a report is for.

Two things make this affordable and safe:

  * **Narration is a post-processing pass**, run over a finished run directory.
    It never executes during a run, so it cannot affect timing, behaviour or
    the comparison. An old run can be narrated later without re-running it.
  * **It is once per flow, not once per run.** The click path is fixed by
    definition -- only the prompt text varies between variants -- so the
    descriptions are a property of the flow and are cached beside it. A
    ten-step flow costs ten calls, once, and every later run reuses them.

Each step is described with its before and after screenshots plus what the
runner recorded, and with the descriptions of the preceding steps as context,
so a sequence like open-menu then click-item reads as one action.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from PIL import Image

from harness.image import load_rgb, to_png_bytes

MAX_EDGE = 1100
DEFAULT_MODEL = "claude-opus-5"
NARRATION_FILE = "narration.json"

DESCRIBE_TOOL: dict[str, Any] = {
    "name": "describe_step",
    "description": "Describe, in a few words, what this step does to the application.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["description"],
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "An imperative phrase of at most eight words, naming what the "
                    "user sees happen. For example 'select PartBody in the tree', "
                    "'open the Properties dialog', 'type the prompt into the "
                    "assistant'. No trailing full stop."
                ),
            }
        },
    },
}

SYSTEM = (
    "You write one short caption per step of a recorded UI walkthrough.\n"
    "You get the screen before the step, the screen after it, and what the "
    "automation recorded. Say what a person watching would say happened, using "
    "the application's own words for things where the screenshots show them.\n"
    "Be specific and short. 'Select PartBody in the tree' beats 'click an item'. "
    "If the two screens look the same, say what the step attempted rather than "
    "inventing a visible change."
)


@dataclass(frozen=True)
class StepRef:
    """One step, identified the same way in every variant of a flow."""

    phase: str
    index: int
    action: str
    target: str | None
    before: str | None
    after: str | None

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.index}:{self.action}"


@runtime_checkable
class Narrator(Protocol):
    name: str
    calls: int

    def describe(self, step: StepRef, context: list[str], images: dict[str, bytes]) -> str: ...


class ScriptedNarrator:
    """Test double."""

    name = "scripted"

    def __init__(self, answers: dict[str, str] | None = None, default: str = "") -> None:
        self.answers = answers or {}
        self.default = default
        self.calls = 0
        self.seen: list[tuple[str, list[str], list[str]]] = []

    def describe(self, step: StepRef, context: list[str], images: dict[str, bytes]) -> str:
        self.calls += 1
        self.seen.append((step.key, list(context), sorted(images)))
        return self.answers.get(step.key, self.default or f"{step.action} {step.target or ''}".strip())


def _shrink(image: bytes) -> bytes:
    pixels = load_rgb(image)
    height, width = pixels.shape[:2]
    longest = max(height, width)
    if longest <= MAX_EDGE:
        return image
    scale = MAX_EDGE / longest
    resized = Image.fromarray(pixels, mode="RGB").resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS
    )
    return to_png_bytes(np.asarray(resized, dtype=np.uint8))


class ClaudeNarrator:
    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, client: Any = None,
                 effort: str | None = "low") -> None:
        self.model = model
        self.effort = effort
        self.calls = 0
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            from understudy import credentials

            self._client = anthropic.Anthropic(**credentials.client_options())
        return self._client

    def describe(self, step: StepRef, context: list[str], images: dict[str, bytes]) -> str:
        self.calls += 1
        content: list[dict[str, Any]] = []
        for label in ("before", "after"):
            if label in images:
                content += [
                    {"type": "text", "text": f"Screen {label} the step:"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64", "media_type": "image/png",
                            "data": base64.standard_b64encode(
                                _shrink(images[label])
                            ).decode("utf-8"),
                        },
                    },
                ]

        preceding = "\n".join(f"  {n}. {line}" for n, line in enumerate(context, 1))
        content.append({"type": "text", "text": (
            f"The automation recorded: action `{step.action}`"
            + (f" on target `{step.target}`" if step.target else "")
            + ".\n"
            + (f"Steps already described:\n{preceding}\n" if context else "")
            + "Describe this step."
        )})

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM,
            "thinking": {"type": "adaptive"},
            "tools": [DESCRIBE_TOOL],
            "tool_choice": {"type": "tool", "name": "describe_step"},
            "messages": [{"role": "user", "content": content}],
        }
        if self.effort:
            request["output_config"] = {"effort": self.effort}

        response = self.client.messages.create(**request)
        if getattr(response, "stop_reason", None) == "refusal":
            return ""
        report = next(
            (block.input for block in response.content if block.type == "tool_use"), None
        )
        return str(report.get("description", "")).strip() if report else ""


def steps_of(result: dict[str, Any]) -> list[StepRef]:
    """Pair each step with the screen before and after it."""
    images = [
        (status.get("detail") or {}).get("step_image")
        for status in result.get("step_statuses", [])
    ]
    refs = []
    for position, status in enumerate(result.get("step_statuses", [])):
        refs.append(StepRef(
            phase=status.get("phase", "steps"),
            index=status.get("index", position + 1),
            action=status.get("action", "?"),
            target=status.get("target"),
            before=images[position - 1] if position else None,
            after=images[position],
        ))
    return refs


def narrate_run(
    run_dir: Path | str,
    narrator: Narrator,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Describe each step of a run, reusing a cache where one exists."""
    run_dir = Path(run_dir)
    results = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not results:
        raise ValueError(f"no results in {run_dir}")

    cache: dict[str, str] = {}
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists() and not force:
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # Every variant walks the same path, so one variant is enough -- prefer one
    # that finished, since a failed run stops early and describes less.
    sample = next((r for r in results if r.get("status") == "ok"), results[0])
    refs = steps_of(sample)
    if not any(ref.after for ref in refs):
        raise ValueError(
            "this run has no per-step screenshots; re-run with capture_steps "
            "(the --narrate flag turns it on)"
        )

    narration = dict(cache)
    context: list[str] = []
    for ref in refs:
        if ref.key in narration and not force:
            context.append(narration[ref.key])
            continue
        images: dict[str, bytes] = {}
        for label, relative in (("before", ref.before), ("after", ref.after)):
            if relative and (run_dir / relative).exists():
                images[label] = (run_dir / relative).read_bytes()
        description = narrator.describe(ref, context[-6:], images)
        if description:
            narration[ref.key] = description
            context.append(description)

    (run_dir / NARRATION_FILE).write_text(
        json.dumps(narration, indent=2), encoding="utf-8"
    )
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(narration, indent=2), encoding="utf-8")
    return narration


def load_narration(run_dir: Path | str) -> dict[str, str]:
    path = Path(run_dir) / NARRATION_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


CLICK_SYSTEM = (
    "You are looking at a screenshot of a desktop application, with a red "
    "circle drawn around the point somebody clicked. Say what they clicked "
    "on, as a short noun phrase naming the control: 'the send icon', 'the "
    "Filters tab', 'the prompt box'. Name the thing, not the action, and do "
    "not describe the circle. If you cannot tell, answer 'unknown'."
)


def marked(screen: bytes, point: tuple[int, int], radius: int = 26) -> bytes:
    """The screen with a ring around the point that was clicked.

    Drawn rather than described because a coordinate in a prompt is a number
    the model has to trust; a ring is something it can see.
    """
    import io

    from PIL import Image, ImageDraw

    picture = Image.open(io.BytesIO(screen)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    x, y = point
    draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                 outline=(255, 48, 48), width=4)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def describe_click(screen: bytes, point: tuple[int, int], client: Any = None,
                   model: str = DEFAULT_MODEL) -> str:
    """What was clicked on, in words.

    A recorded flow otherwise names its targets target_1, target_2, which
    tells a reader nothing and makes the flow unreadable at exactly the moment
    it wants editing. It is also the beginning of a record of what a person
    did and why -- screen, point, description -- which is the shape a
    demonstration takes.
    """
    if client is None:
        import anthropic

        from understudy import credentials

        client = anthropic.Anthropic(**credentials.client_options())

    response = client.messages.create(
        model=model,
        max_tokens=200,
        system=CLICK_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(
                    _shrink(marked(screen, point))
                ).decode("utf-8"),
            }},
            {"type": "text", "text": "What was clicked?"},
        ]}],
    )
    parts = [block.text for block in response.content
             if getattr(block, "type", "") == "text"]
    described = " ".join(parts).strip().strip(".")
    return "" if described.lower() in ("", "unknown") else described
