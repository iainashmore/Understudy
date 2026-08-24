"""Task definitions.

A task is data, not code. The same `Task` object is handed to any of the three
environments (UI / API / Kernel) without modification, and nothing on it may
describe how the drawing is to be performed -- only what the result must look
like.

Task files live in `tasks/*.json`. Each file also carries a `golden` recipe used
to render the reference image. That recipe is *authoring input*, not part of the
task: `load_task` drops it, and only `harness.reference` (via
`tools/generate_references.py`) ever reads it. An environment that could see the
recipe would be handed the answer as a shape list, which is roughly the API
layer's action space -- the comparison would be meaningless.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
REFERENCES_DIR = REPO_ROOT / "references"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Scoring defaults. Individual tasks may override any of these under "scoring".
#
# Calibrated, not guessed. tests/test_task_validity.py brackets them from both
# sides against an independent rasteriser: with these values every correct
# drawing scores exactly 1.0 and the best *wrong* drawing in the perturbation
# set (the whole figure shifted one pixel) scores 0.981, so 0.99 sits in the
# middle of the gap rather than up against either edge.
#
# Re-run that calibration when the UI environment lands. A browser canvas is a
# fourth rasteriser and has not been measured yet; if correct UI drawings come
# back clustered just under the threshold, the threshold is wrong, not the
# layer, and reading it as a UI capability gap would be the exact mistake this
# harness exists to avoid.
DEFAULT_PASS_THRESHOLD = 0.99
DEFAULT_CHANNEL_TOLERANCE = 24
DEFAULT_BLUR_SIGMA = 1.5


class Difficulty(IntEnum):
    """The spec's difficulty gradient: simple shapes, compositions, then cases
    where ordering matters."""

    SIMPLE = 1
    COMPOSITE = 2
    OCCLUSION = 3

    @classmethod
    def parse(cls, value: str) -> "Difficulty":
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(
                f"unknown difficulty {value!r}; expected one of "
                f"{[d.name.lower() for d in cls]}"
            ) from None


@dataclass(frozen=True)
class Canvas:
    """The drawing surface. Every layer must produce an artifact of this size."""

    width: int
    height: int
    background: str

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


@dataclass(frozen=True)
class ScoringConfig:
    """How the scorer decides pass/fail for this task."""

    pass_threshold: float = DEFAULT_PASS_THRESHOLD
    channel_tolerance: int = DEFAULT_CHANNEL_TOLERANCE
    blur_sigma: float = DEFAULT_BLUR_SIGMA


@dataclass(frozen=True)
class Task:
    """Layer-agnostic task definition."""

    task_id: str
    description: str
    prompt: str
    canvas: Canvas
    difficulty: Difficulty
    reference_path: Path
    scoring: ScoringConfig

    def reference_bytes(self) -> bytes:
        if not self.reference_path.exists():
            raise FileNotFoundError(
                f"no reference image for task {self.task_id!r} at "
                f"{self.reference_path}; run tools/generate_references.py"
            )
        return self.reference_path.read_bytes()


def _require(raw: dict[str, Any], key: str, source: Path) -> Any:
    if key not in raw:
        raise ValueError(f"{source}: missing required key {key!r}")
    return raw[key]


def _parse_canvas(raw: dict[str, Any], source: Path) -> Canvas:
    width = int(_require(raw, "width", source))
    height = int(_require(raw, "height", source))
    background = str(_require(raw, "background", source))
    if width <= 0 or height <= 0:
        raise ValueError(f"{source}: canvas dimensions must be positive")
    if not _HEX_RE.match(background):
        raise ValueError(
            f"{source}: canvas background {background!r} must be #rrggbb"
        )
    return Canvas(width=width, height=height, background=background.lower())


def _parse_scoring(raw: dict[str, Any], source: Path) -> ScoringConfig:
    cfg = ScoringConfig(
        pass_threshold=float(raw.get("pass_threshold", DEFAULT_PASS_THRESHOLD)),
        channel_tolerance=int(
            raw.get("channel_tolerance", DEFAULT_CHANNEL_TOLERANCE)
        ),
        blur_sigma=float(raw.get("blur_sigma", DEFAULT_BLUR_SIGMA)),
    )
    if not 0.0 < cfg.pass_threshold <= 1.0:
        raise ValueError(f"{source}: pass_threshold must be in (0, 1]")
    if not 0 <= cfg.channel_tolerance <= 255:
        raise ValueError(f"{source}: channel_tolerance must be in [0, 255]")
    if cfg.blur_sigma < 0:
        raise ValueError(f"{source}: blur_sigma must be >= 0")
    return cfg


def task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def reference_path(task_id: str) -> Path:
    return REFERENCES_DIR / f"{task_id}.png"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"no task file at {path}")
    with path.open() as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: task file must contain a JSON object")
    return raw


def load_task(task_id: str) -> Task:
    """Load a task. The golden recipe is deliberately not included."""
    path = task_path(task_id)
    raw = _read_json(path)

    declared_id = str(_require(raw, "id", path))
    if declared_id != task_id:
        raise ValueError(
            f"{path}: declared id {declared_id!r} does not match filename"
        )

    return Task(
        task_id=declared_id,
        description=str(_require(raw, "description", path)),
        prompt=str(_require(raw, "prompt", path)),
        canvas=_parse_canvas(_require(raw, "canvas", path), path),
        difficulty=Difficulty.parse(str(_require(raw, "difficulty", path))),
        reference_path=reference_path(declared_id),
        scoring=_parse_scoring(raw.get("scoring", {}), path),
    )


def list_task_ids() -> list[str]:
    return sorted(p.stem for p in TASKS_DIR.glob("*.json"))


def load_all_tasks() -> list[Task]:
    """Every task, ordered by difficulty tier then id."""
    tasks = [load_task(task_id) for task_id in list_task_ids()]
    return sorted(tasks, key=lambda t: (t.difficulty, t.task_id))


def load_golden_recipe(task_id: str) -> dict[str, Any]:
    """Authoring-only. Do not call this from an environment or an agent."""
    path = task_path(task_id)
    raw = _read_json(path)
    return dict(_require(raw, "golden", path))
