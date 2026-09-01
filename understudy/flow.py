"""Flow file loading, validation and variable substitution.

A flow is a declaration of a fixed click-path. It is authored by a human (or
drafted by the recorder and then cleaned up), so the parser's job is as much
about producing legible complaints as it is about building objects.

Targets are *lists* of strategies rather than single selectors. The UI under
test drifts -- a dialog gets portalled to <body> instead of rendering inline, a
class name changes -- and a single path-anchored selector is the thing that
breaks first. Strategies are tried in order, most stable first, and the runner
records which one actually resolved so drift is visible in the results before it
becomes a failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from understudy.subject import Subject

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "flow.schema.json"

#: {{name}} -- the same syntax for every variable, not just {{prompt}}.
VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

#: `image` is the one that carries a CAD application: it works on pixels,
#: which is all such a surface exposes.
NATIVE_STRATEGY_KEYS = {
    "automation_id", "name", "control_type", "class_name", "image", "agent",
    "path",
}
NATIVE_MODIFIER_KEYS = {"threshold", "region", "offset", "exact", "nth"}

DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_POLL_INTERVAL_MS = 250
DEFAULT_STABLE_FOR_MS = 1_500


class FlowError(ValueError):
    """The flow file is wrong. Always a human-fixable authoring problem.

    Carries every problem it found, not just the first. A schema failure
    usually comes in a batch -- one missing key often means the block it
    belonged to is missing too -- and being told about them one run at a time
    is four rounds of edit-and-retry instead of one.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = list(problems) if problems else [message]


@dataclass(frozen=True)
class Strategy:
    """One way of finding an element. Backend-specific fields, kept raw so the
    driver owns the translation and the core stays backend-agnostic."""

    backend: str
    fields: dict[str, Any]

    @property
    def kind(self) -> str:
        for key in ("testid", "role", "text", "label", "placeholder", "css", "xpath",
                    "image", "agent", "automation_id", "control_type", "class_name",
                    "name"):
            if key in self.fields:
                return key
        return "unknown"

    def describe(self) -> str:
        rendered = " ".join(f"{k}={v!r}" for k, v in sorted(self.fields.items()))
        return f"{self.kind}({rendered})"


@dataclass(frozen=True)
class Target:
    name: str
    strategies: dict[str, tuple[Strategy, ...]]
    intent: str | None = None

    def for_backend(self, backend: str) -> tuple[Strategy, ...]:
        found = self.strategies.get(backend)
        if not found:
            raise FlowError(
                f"target {self.name!r} has no {backend} strategy; "
                f"it defines {sorted(self.strategies) or 'nothing'}"
            )
        return found


@dataclass(frozen=True)
class Step:
    index: int
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    phase: str = "steps"

    @property
    def target(self) -> str | None:
        return self.params.get("target")

    @property
    def optional(self) -> bool:
        return bool(self.params.get("optional", False))

    @property
    def label(self) -> str:
        return str(self.params.get("label", self.action))

    def describe(self) -> str:
        target = f" {self.target}" if self.target else ""
        return f"{self.phase}[{self.index}] {self.action}{target}"


@dataclass(frozen=True)
class Defaults:
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    stable_for_ms: int = DEFAULT_STABLE_FOR_MS


@dataclass(frozen=True)
class Flow:
    version: int
    #: Identifier: appears in run ids, paths and results.
    name: str
    #: What a person reads. Falls back to the name when not given.
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    subject: Subject = field(default_factory=Subject)
    #: Prompt variants written into the flow itself. The original spec kept
    #: prompts in their own file, for the good reason that they change far more
    #: often than the steps do -- so both work, and an external file wins.
    embedded_prompts: tuple[dict[str, Any], ...] = ()
    targets: dict[str, Target] = field(default_factory=dict)
    steps: tuple[Step, ...] = ()
    reset: tuple[Step, ...] = ()
    interstitials: tuple[str, ...] = ()
    target_app: dict[str, Any] = field(default_factory=dict)
    defaults: Defaults = field(default_factory=Defaults)
    source_path: Path | None = None
    source_text: str = ""

    def target_for(self, name: str) -> Target:
        try:
            return self.targets[name]
        except KeyError:
            raise FlowError(f"unknown target {name!r}") from None

    def variables(self) -> set[str]:
        """Every {{name}} the flow refers to."""
        found: set[str] = set()
        for step in self.reset + self.steps:
            for value in step.params.values():
                if isinstance(value, str):
                    found.update(VARIABLE_RE.findall(value))
        return found

    def app_config(self, backend: str = "native") -> dict[str, Any]:
        return dict(self.target_app.get(backend) or {})

    def validate_for_backend(self, backend: str) -> None:
        """Check every target the flow actually uses can be found on this
        backend, before a run starts rather than half way through one."""
        used = {step.target for step in self.reset + self.steps if step.target}
        used |= {
            step.params["until_hidden"]
            for step in self.reset + self.steps
            if step.params.get("until_hidden")
        }
        used |= set(self.interstitials)

        missing = sorted(
            name for name in used if backend not in self.targets[name].strategies
        )
        if missing:
            raise FlowError(
                f"these targets have no {backend} strategy: {', '.join(missing)}"
            )
        if backend not in self.target_app and self.target_app:
            raise FlowError(
                f"flow has no target_app.{backend} section; it defines "
                f"{sorted(self.target_app)}"
            )


def _normalise_strategies(
    raw: Any, backend: str, target_name: str, base_dir: Path | None = None
) -> tuple[Strategy, ...]:
    """Accept the brief form and the ranked form.

        native: "Send"                        one control, by name
        native: {control_type: Button}        one strategy
        native: [{image: a.png}, {name: OK}]  ranked, most stable first
    """
    entries = raw if isinstance(raw, list) else [raw]
    strategies = []
    for position, entry in enumerate(entries):
        if isinstance(entry, str):
            fields: dict[str, Any] = {"name": entry}
        elif isinstance(entry, dict):
            fields = dict(entry)
        else:
            raise FlowError(
                f"target {target_name!r}: {backend} strategy {position} must be "
                f"a string or a mapping, got {type(entry).__name__}"
            )

        allowed = NATIVE_STRATEGY_KEYS | NATIVE_MODIFIER_KEYS
        unknown = sorted(set(fields) - allowed)
        if unknown:
            raise FlowError(
                f"target {target_name!r}: {backend} strategy {position} has "
                f"unknown key {unknown[0]!r}; allowed: {', '.join(sorted(allowed))}"
            )

        primary = NATIVE_STRATEGY_KEYS
        if not set(fields) & primary:
            raise FlowError(
                f"target {target_name!r}: {backend} strategy {position} needs one "
                f"of {', '.join(sorted(primary))}"
            )
        if "image" in fields:
            # Anchor paths are written relative to the flow file, so a flow
            # directory can be moved or copied into a run folder intact.
            anchor = Path(str(fields["image"]))
            if not anchor.is_absolute() and base_dir is not None:
                anchor = (base_dir / anchor).resolve()
            if not anchor.exists():
                raise FlowError(
                    f"target {target_name!r}: anchor image not found: {anchor}"
                )
            fields["image"] = str(anchor)

        strategies.append(Strategy(backend=backend, fields=fields))
    return tuple(strategies)


def _steps(raw: list[dict[str, Any]], phase: str) -> tuple[Step, ...]:
    return tuple(
        Step(
            index=position,
            action=entry["action"],
            params={k: v for k, v in entry.items() if k != "action"},
            phase=phase,
        )
        for position, entry in enumerate(raw, start=1)
    )


def _schema() -> dict[str, Any]:
    with SCHEMA_PATH.open() as handle:
        return json.load(handle)


def parse_flow(data: dict[str, Any], source_path: Path | None = None,
               source_text: str = "") -> Flow:
    """Validate and build. Schema first for shape, then the cross-references it
    cannot express."""
    if not isinstance(data, dict):
        raise FlowError("flow must be a mapping at the top level")

    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(data),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        problems = [
            f"{'/'.join(str(part) for part in error.absolute_path) or '(root)'}: "
            f"{error.message}"
            for error in errors
        ]
        raise FlowError(
            problems[0]
            + (f" (and {len(problems) - 1} more problem(s))" if len(problems) > 1 else ""),
            problems,
        )

    base_dir = source_path.parent if source_path else None
    targets = {}
    for name, spec in (data.get("targets") or {}).items():
        targets[name] = Target(
            name=name,
            intent=spec.get("intent"),
            strategies={
                backend: _normalise_strategies(spec[backend], backend, name, base_dir)
                for backend in ("native",)
                if backend in spec
            },
        )

    flow = Flow(
        version=data["version"],
        name=data["name"],
        title=str(data.get("title", "") or data["name"]),
        description=str(data.get("description", "")),
        tags=tuple(data.get("tags") or ()),
        subject=Subject.from_config(data.get("subject")),
        embedded_prompts=tuple(data.get("prompts") or ()),
        targets=targets,
        steps=_steps(data["steps"], "steps"),
        reset=_steps(data.get("reset") or [], "reset"),
        interstitials=tuple(data.get("interstitials") or ()),
        target_app=data.get("target_app") or {},
        defaults=Defaults(**(data.get("defaults") or {})),
        source_path=source_path,
        source_text=source_text,
    )

    for name, target in flow.targets.items():
        for strategies in target.strategies.values():
            for strategy in strategies:
                if "agent" in strategy.fields and not (
                    target.intent or isinstance(strategy.fields["agent"], str)
                ):
                    raise FlowError(
                        f"target {name!r}: an agent strategy needs guidance -- give "
                        f"the target an `intent`, or put a description in the "
                        f"`agent` value"
                    )

    known = set(flow.targets)
    for step in flow.reset + flow.steps:
        for key in ("target", "until_hidden"):
            referenced = step.params.get(key)
            if referenced and referenced not in known:
                raise FlowError(
                    f"{step.describe()}: {key} {referenced!r} is not defined in "
                    f"targets ({', '.join(sorted(known)) or 'none'})"
                )
    for step in flow.reset + flow.steps:
        if step.action == "read" and not (step.target or step.params.get("region")):
            raise FlowError(
                f"{step.describe()}: read needs either a target or a region"
            )
        if step.params.get("mode") == "pixels" and not (
            step.target or step.params.get("region")
        ):
            raise FlowError(f"{step.describe()}: pixel mode needs a target or a region")

    for name in flow.interstitials:
        if name not in known:
            raise FlowError(f"interstitial {name!r} is not defined in targets")

    return flow


def load_flow(path: Path | str) -> Flow:
    path = Path(path)
    if not path.exists():
        raise FlowError(f"no flow file at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FlowError(f"{path}: not valid YAML -- {exc}{_yaml_hint(text, exc)}") from None
    return parse_flow(data, source_path=path, source_text=text)


def _yaml_hint(text: str, exc: Exception) -> str:
    """A nudge for the mistake a Windows user makes on their first flow.

    `executable: "C:\\Program Files\\app.exe"` is not a broken path, it is
    broken YAML: inside double quotes those backslashes are escape sequences,
    and the parser fails somewhere that looks unrelated. Single quotes fix it,
    and that is not guessable from the scanner's own message.
    """
    if "double-quoted scalar" not in str(exc):
        return ""
    culprits = [
        line.strip() for line in text.splitlines()
        if '\\' in line and '"' in line
    ]
    if not culprits:
        return ""
    return (
        "\n\nA Windows path inside double quotes is the usual cause: the "
        "backslashes are read as escape sequences. Use single quotes, forward "
        "slashes, or -- better -- a path relative to this file.\n"
        f"  {culprits[0]}"
    )


def substitute(text: str, variables: dict[str, str]) -> str:
    """Replace every {{name}}. Unknown names are an error, not a silent gap --
    a prompt that runs with '{{style}}' left in it is a wasted run that looks
    like a real one."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise FlowError(
                f"no value for {{{{{name}}}}}; the prompts file provides "
                f"{', '.join(sorted(variables)) or 'nothing'}"
            )
        return str(variables[name])

    return VARIABLE_RE.sub(replace, text)


def render_step(step: Step, variables: dict[str, str]) -> Step:
    """A copy with every string parameter substituted."""
    return Step(
        index=step.index,
        action=step.action,
        phase=step.phase,
        params={
            key: substitute(value, variables) if isinstance(value, str) else value
            for key, value in step.params.items()
        },
    )
