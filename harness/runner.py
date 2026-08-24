"""The runner loop.

Feed the task in, ask the agent for an action, execute it in the environment,
feed the result back, repeat until the agent declares completion or the turn
budget runs out. Score whatever came out. Write a trace of the whole thing.

The runner is the only component that sees both sides: the agent and the
environment get a `TaskBrief`, the scorer gets the reference, and the runner
holds the `Task` that connects them.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from harness.agents.base import Agent, Usage
from harness.environment import Environment
from harness.interaction import Action, Layer, Observation
from harness.scorer import ScoreResult, Scorer
from harness.task import Task

#: Provisional, and per-layer on purpose: one UI click accomplishes far less
#: than one API call, so a single shared budget would hand the lower-level
#: layers a handicap that looks like incapability. These numbers are guesses
#: until there is data -- revisit once each environment has run.
DEFAULT_TURN_LIMITS: dict[Layer, int] = {
    Layer.API: 20,
    Layer.KERNEL: 40,
    Layer.UI: 60,
}


class Outcome(str, Enum):
    """Why the loop stopped. Orthogonal to whether the drawing was right."""

    COMPLETED = "completed"
    TURN_LIMIT = "turn_limit"
    AGENT_ERROR = "agent_error"
    ENVIRONMENT_ERROR = "environment_error"


@dataclass(frozen=True)
class RunnerConfig:
    turn_limits: Mapping[Layer, int] = field(
        default_factory=lambda: dict(DEFAULT_TURN_LIMITS)
    )
    fallback_turn_limit: int = 20
    #: Save the canvas after every turn, not just at the end. Off by default --
    #: it is a lot of PNGs, and worth turning on when reading a specific
    #: failure.
    capture_turn_images: bool = False

    def turn_limit_for(self, layer: Layer) -> int:
        limit = self.turn_limits.get(layer, self.fallback_turn_limit)
        if limit < 1:
            raise ValueError(f"turn limit for {layer.value} must be at least 1")
        return limit


@dataclass(frozen=True)
class RunResult:
    """One run of one agent, on one task, at one layer."""

    run_id: str
    task_id: str
    layer: Layer
    agent_name: str
    is_oracle: bool
    outcome: Outcome
    passed: bool
    score: float
    turns_used: int
    turn_limit: int
    duration_s: float
    agent_seconds: float
    environment_seconds: float
    usage: Usage
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    trace_path: Path | None = None
    artifact_path: Path | None = None

    @property
    def hit_turn_limit(self) -> bool:
        return self.outcome is Outcome.TURN_LIMIT

    def as_row(self) -> dict[str, Any]:
        """One row of the results table."""
        return {
            "run_id": self.run_id,
            "agent": self.agent_name,
            "layer": self.layer.value,
            "task_id": self.task_id,
            "passed": self.passed,
            "score": round(self.score, 6),
            "outcome": self.outcome.value,
            "turns_used": self.turns_used,
            "turn_limit": self.turn_limit,
            "duration_s": round(self.duration_s, 4),
            "agent_seconds": round(self.agent_seconds, 4),
            "environment_seconds": round(self.environment_seconds, 4),
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "is_oracle": self.is_oracle,
            "error": self.error or "",
        }


class TraceWriter:
    """Appends JSONL as the run happens.

    Written incrementally rather than assembled and dumped at the end: a run
    that dies half way through is exactly the one worth reading, and it should
    not take its own transcript down with it.
    """

    def __init__(self, path: Path | None, image_dir: Path | None = None) -> None:
        self.path = path
        self.image_dir = image_dir
        self._handle = None
        self.records: list[dict[str, Any]] = []

    def __enter__(self) -> "TraceWriter":
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self._handle:
            self._handle.write(json.dumps(record, default=str) + "\n")
            self._handle.flush()

    def save_image(self, name: str, data: bytes | None) -> Path | None:
        if data is None or self.image_dir is None:
            return None
        self.image_dir.mkdir(parents=True, exist_ok=True)
        path = self.image_dir / name
        path.write_bytes(data)
        return path


class Runner:
    """Drives one agent through one task in one environment."""

    def __init__(
        self,
        scorer: Scorer,
        config: RunnerConfig | None = None,
        trace_dir: Path | str | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.scorer = scorer
        self.config = config or RunnerConfig()
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.clock = clock

    def run_id_for(
        self, task: Task, environment: Environment, agent: Agent, repeat: int
    ) -> str:
        return f"{agent.name}.{environment.layer.value}.{task.task_id}.{repeat:02d}"

    def run(
        self,
        task: Task,
        environment: Environment,
        agent: Agent,
        repeat: int = 0,
    ) -> RunResult:
        run_id = self.run_id_for(task, environment, agent, repeat)
        layer = environment.layer
        turn_limit = self.config.turn_limit_for(layer)
        brief = task.brief()

        trace_path = self.trace_dir / f"{run_id}.jsonl" if self.trace_dir else None
        image_dir = self.trace_dir / run_id if self.trace_dir else None

        started = self.clock()
        agent_seconds = 0.0
        environment_seconds = 0.0
        usage = Usage()
        turns_used = 0
        outcome = Outcome.COMPLETED
        error: str | None = None

        with TraceWriter(trace_path, image_dir) as trace:
            trace.write(
                {
                    "type": "run_start",
                    "run_id": run_id,
                    "task_id": task.task_id,
                    "layer": layer.value,
                    "agent": agent.name,
                    "is_oracle": getattr(agent, "is_oracle", False),
                    "turn_limit": turn_limit,
                    "prompt": brief.prompt,
                    "canvas": {
                        "width": brief.canvas.width,
                        "height": brief.canvas.height,
                        "background": brief.canvas.background,
                    },
                }
            )

            try:
                mark = self.clock()
                observation = environment.reset(brief)
                interface = environment.interface()
                environment_seconds += self.clock() - mark
                agent.reset(brief, interface)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                trace.write(
                    {
                        "type": "run_end",
                        "outcome": Outcome.ENVIRONMENT_ERROR.value,
                        "error": error,
                        "traceback": traceback.format_exc(),
                    }
                )
                return self._failed_before_start(
                    run_id, task, layer, agent, turn_limit, error,
                    self.clock() - started, trace_path,
                )

            trace.write(
                {
                    "type": "interface",
                    "layer": layer.value,
                    "operations": [op.signature() for op in interface.operations],
                }
            )

            for turn in range(1, turn_limit + 1):
                try:
                    mark = self.clock()
                    action = agent.act(observation)
                    agent_seconds += self.clock() - mark
                except Exception as exc:
                    # A model client can time out or return nonsense. That fails
                    # this run, not the sweep.
                    outcome = Outcome.AGENT_ERROR
                    error = f"{type(exc).__name__}: {exc}"
                    trace.write(
                        {
                            "type": "agent_error",
                            "turn": turn,
                            "error": error,
                            "traceback": traceback.format_exc(),
                        }
                    )
                    break

                turn_usage = getattr(agent, "last_usage", None)
                if turn_usage:
                    usage = usage + turn_usage

                if action.is_done:
                    trace.write({"type": "done", "turn": turn})
                    outcome = Outcome.COMPLETED
                    break

                try:
                    mark = self.clock()
                    observation = environment.step(action)
                    environment_seconds += self.clock() - mark
                except Exception as exc:
                    outcome = Outcome.ENVIRONMENT_ERROR
                    error = f"{type(exc).__name__}: {exc}"
                    trace.write(
                        {
                            "type": "environment_error",
                            "turn": turn,
                            "action": action.as_dict(),
                            "error": error,
                            "traceback": traceback.format_exc(),
                        }
                    )
                    break

                turns_used = turn
                if self.config.capture_turn_images:
                    trace.save_image(f"turn_{turn:03d}.png", observation.image)

                trace.write(
                    {
                        "type": "turn",
                        "turn": turn,
                        "action": action.as_dict(),
                        "observation": observation.as_dict(),
                        "agent_seconds": round(agent_seconds, 6),
                        "environment_seconds": round(environment_seconds, 6),
                        "usage": turn_usage.as_dict() if turn_usage else None,
                    }
                )
            else:
                outcome = Outcome.TURN_LIMIT

            artifact: bytes | None = None
            try:
                artifact = environment.artifact()
            except Exception as exc:
                # Losing the artifact is not the same as never having drawn
                # anything, so it is recorded separately from an earlier error.
                artifact_error = f"could not retrieve artifact: {exc}"
                error = f"{error}; {artifact_error}" if error else artifact_error

            artifact_path = trace.save_image("final.png", artifact)

            # Scored even on a turn-limit or error run: an agent that drew the
            # right thing and never said so is a different failure from one that
            # drew the wrong thing, and the difference is worth keeping.
            if artifact is None:
                score_result = ScoreResult(
                    task_id=task.task_id,
                    passed=False,
                    score=0.0,
                    error=error or "environment produced no artifact",
                )
            else:
                score_result = self.scorer.score(task, artifact)

            duration = self.clock() - started
            trace.write(
                {
                    "type": "run_end",
                    "outcome": outcome.value,
                    "turns_used": turns_used,
                    "passed": score_result.passed,
                    "score": score_result.score,
                    "metrics": score_result.metrics,
                    "scoring_details": score_result.details,
                    "error": error or score_result.error,
                    "duration_s": round(duration, 6),
                }
            )

        return RunResult(
            run_id=run_id,
            task_id=task.task_id,
            layer=layer,
            agent_name=agent.name,
            is_oracle=getattr(agent, "is_oracle", False),
            outcome=outcome,
            passed=score_result.passed,
            score=score_result.score,
            turns_used=turns_used,
            turn_limit=turn_limit,
            duration_s=duration,
            agent_seconds=agent_seconds,
            environment_seconds=environment_seconds,
            usage=usage,
            metrics=dict(score_result.metrics),
            error=error or score_result.error,
            trace_path=trace_path,
            artifact_path=artifact_path,
        )

    def _failed_before_start(
        self,
        run_id: str,
        task: Task,
        layer: Layer,
        agent: Agent,
        turn_limit: int,
        error: str,
        duration: float,
        trace_path: Path | None,
    ) -> RunResult:
        return RunResult(
            run_id=run_id,
            task_id=task.task_id,
            layer=layer,
            agent_name=agent.name,
            is_oracle=getattr(agent, "is_oracle", False),
            outcome=Outcome.ENVIRONMENT_ERROR,
            passed=False,
            score=0.0,
            turns_used=0,
            turn_limit=turn_limit,
            duration_s=duration,
            agent_seconds=0.0,
            environment_seconds=0.0,
            usage=Usage(),
            error=error,
            trace_path=trace_path,
        )
