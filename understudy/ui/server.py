"""A local web UI for authoring, running and reviewing flows.

Deliberately built on the standard library. This has to run on the machine that
has CATIA on it, which is not a machine where installing a web framework is
necessarily quick or permitted, and the UI is a handful of endpoints.

Everything it does is something the CLI already does -- it opens, edits, saves,
validates, replays and reports. The UI exists because a YAML file with pixel
regions and anchor paths is not something anyone wants to edit blind, and
because the person changing the prompts is not necessarily the person who wrote
the flow.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from understudy import credentials
from understudy.authoring import AuthoringError, duplicate_file, slugify
from understudy.drivers import build as build_driver
from understudy.flow import FlowError, load_flow
from understudy.prompts import PromptsError, prompts_for
from understudy.suite import SuiteError, is_suite_file, load_suite
from understudy.pdf import write_pdf
from understudy.transcript import load_results, write_transcript
from understudy.transcript_html import write_html
from understudy.compare import compare as compare_runs
from understudy.compare_report import write_comparison
from understudy.subject import FIELDS as SUBJECT_FIELDS
from understudy.subject import Subject, remember, resolve_subject
from understudy.vcs.backend import Repository
from understudy.vcs.git import Git, GitError
from understudy import record_native
from understudy.windows import open_windows as list_windows
from understudy.vcs.remote import parse_remote
from understudy.vcs import recent as recent_workspaces
from understudy.vcs.source import SourceError, WorkspaceSource
from understudy.vcs.source import parse as parse_source
from understudy.resolvers import build as build_resolver
from understudy.runner import Runner, Status, run_directory, write_csv

STATIC = Path(__file__).resolve().parent / "static"
FLOW_SUFFIXES = (".yaml", ".yml")



class WorkspaceError(ValueError):
    """A path outside the workspace, or one that does not exist."""


@dataclass
class RunJob:
    """One replay, executing on a worker thread."""

    id: str
    flow_path: str
    out_dir: Path
    events: queue.Queue = field(default_factory=queue.Queue)
    status: str = "starting"
    results: list[dict[str, Any]] = field(default_factory=list)
    transcript: str | None = None
    transcript_html: str | None = None
    error: str | None = None

    def emit(self, kind: str, **payload: Any) -> None:
        self.events.put({"type": kind, **payload})


class Workspace:
    """Everything the UI may read or write, and nothing else."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.runs_root = self.root / "runs"

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        # A UI that will be pointed at a work machine has no business reading
        # outside the folder it was given.
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(f"path outside the workspace: {relative}")
        return candidate

    def relative(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root)).replace("\\", "/")

    def listing(self) -> dict[str, list[str]]:
        def find(suffixes: tuple[str, ...]) -> list[str]:
            found = [
                self.relative(path)
                for path in sorted(self.root.rglob("*"))
                if path.is_file()
                and path.suffix.lower() in suffixes
                and "runs" not in path.relative_to(self.root).parts
            ]
            return found

        candidates = find(FLOW_SUFFIXES)
        suites = [p for p in candidates if is_suite_file(self.root / p)]
        return {
            "flows": [p for p in candidates if self._looks_like_flow(p)],
            "suites": suites,

            "runs": sorted(
                (self.relative(d) for d in self.runs_root.glob("*") if d.is_dir()),
                reverse=True,
            ) if self.runs_root.exists() else [],
        }

    def _looks_like_flow(self, relative: str) -> bool:
        """Whether this YAML file is a flow.

        Matched on top-level keys anywhere in the file, not in a fixed-size
        window: a well-commented flow easily pushes `steps:` past the first few
        thousand characters, and a sniff that reads only the head silently drops
        the file from every listing.
        """
        path = self.root / relative
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        keys = {
            line.split(":", 1)[0]
            for line in text.splitlines()
            if line[:1].isalpha() and ":" in line
        }
        # `steps` alone, because a flow being edited is a flow: deleting the
        # prompts block for a moment used to drop the file out of the sidebar
        # entirely, which reads as "my flow is gone" rather than "this does not
        # load yet". Suites are told apart before this is asked.
        return "steps" in keys


class Api:
    """The verbs the UI needs. Kept apart from HTTP so it can be tested
    directly."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.jobs: dict[str, RunJob] = {}
        self._counter = 0
        self._lock = threading.Lock()
        #: The recording in progress, if any. One at a time: a global keyboard
        #: hook is a machine-wide thing, not a per-tab one.
        self.recording: dict[str, Any] | None = None

    # -- files ----------------------------------------------------------------

    def list_files(self) -> dict[str, Any]:
        return self.workspace.listing()

    def describe_flows(self) -> list[dict[str, Any]]:
        """Title, description and variant count for every flow, so the picker
        shows something a person can choose between."""
        described = []
        for relative in self.workspace.listing()["flows"]:
            entry: dict[str, Any] = {"path": relative, "name": "",
                                     "title": relative,
                                     "description": "", "tags": [],
                                     "variants": 0, "backends": [],
                                     "embedded": False, "error": None}
            try:
                flow = load_flow(self.workspace.resolve(relative))
                entry.update(
                    name=flow.name,   # what a run records, so runs can be
                                      # listed under the flow that produced them
                    title=flow.title or flow.name, description=flow.description,
                    tags=list(flow.tags), steps=len(flow.steps),
                    variants=len(flow.embedded_prompts),
                    # What this flow can be driven as. The flow says so; the
                    # form has no business asking.
                    backends=sorted(flow.target_app),
                    embedded=bool(flow.embedded_prompts),
                )
            except Exception as exc:
                entry["error"] = str(exc)
                entry["problems"] = list(getattr(exc, "problems", [str(exc)]))
            described.append(entry)
        return described

    def describe_suites(self) -> list[dict[str, Any]]:
        described = []
        for relative in self.workspace.listing()["suites"]:
            try:
                suite = load_suite(self.workspace.resolve(relative))
                described.append({
                    "path": relative, "name": suite.name,
                    "description": suite.description,
                    "flows": [
                        {**entry.summary(),
                         "path": self.workspace.relative(entry.flow_path)}
                        for entry in suite
                    ],
                })
            except (SuiteError, OSError) as exc:
                described.append({"path": relative, "name": relative,
                                  "description": "", "flows": [], "error": str(exc)})
        return described

    def duplicate(self, source: str, destination: str, name: str | None = None,
                  title: str | None = None, description: str | None = None) -> dict[str, Any]:
        path = duplicate_file(
            self.workspace.resolve(source), self.workspace.resolve(destination),
            name=name or slugify(Path(destination).stem),
            title=title, description=description,
        )
        return {"path": self.workspace.relative(path)}

    NEW_FLOW = """version: 1
name: {name}
title: {title}
description: {description}
tags: []

target_app:
  web:
    url: "https://example.com/"

defaults: {{timeout_ms: 10000, stable_for_ms: 1500}}

# Named elements. Strategies are tried in order, most stable first.
targets:
  prompt_box:
    intent: the main text input
    web:
      - testid: prompt-input
      - role: textbox

  send_button:
    intent: submits the prompt
    web:
      - testid: send
      - role: button
        name: Send

  response_area:
    intent: where the reply appears
    web:
      - testid: response

# One entry per prompt run. Any key besides `id` is a variable the steps use.
prompts:
  - id: baseline
    prompt: Replace this with the prompt to test.

steps:
  - action: capture
    label: before-prompt
  - action: type
    target: prompt_box
    text: "{{{{prompt}}}}"
  - action: click
    target: send_button
  - action: wait_for_stable
    target: response_area
    stable_for_ms: 1500
    timeout_ms: 120000
  - action: capture
    label: after-response
  - action: read
    target: response_area
    store_as: response
"""

    NEW_SUITE = """version: 1
name: {name}
description: {description}

flows: []
"""

    def create(self, kind: str, relative: str, title: str = "",
               description: str = "") -> dict[str, Any]:
        path = self.workspace.resolve(relative)
        if path.exists():
            raise WorkspaceError(f"{relative} already exists")
        template = self.NEW_FLOW if kind == "flow" else self.NEW_SUITE
        name = slugify(title or Path(relative).stem)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            template.format(
                name=name,
                title=title or Path(relative).stem,
                description=description or "TODO: what this checks",
            ),
            encoding="utf-8",
        )
        return {"path": self.workspace.relative(path)}

    def delete(self, relative: str, with_contents: bool = False) -> dict[str, Any]:
        """Remove a flow, or a collection and optionally the flows it lists.

        Deleting a collection alone leaves its flows on disk: a collection is a
        list, and throwing away the list should not throw away the work unless
        that is what was asked for.
        """
        path = self.workspace.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"no such file: {relative}")

        removed = []
        if with_contents and is_suite_file(path):
            try:
                for entry in load_suite(path):
                    if entry.flow_path.is_file():
                        entry.flow_path.unlink()
                        removed.append(self.workspace.relative(entry.flow_path))
            except SuiteError:
                pass
        path.unlink()
        removed.append(relative)
        return {"deleted": removed}

    def read_file(self, relative: str) -> dict[str, Any]:
        path = self.workspace.resolve(relative)
        if not path.exists():
            raise WorkspaceError(f"no such file: {relative}")
        return {"path": relative, "text": path.read_text(encoding="utf-8")}

    def write_file(self, relative: str, text: str) -> dict[str, Any]:
        """Also covers save-as: the UI just sends a different path."""
        path = self.workspace.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return {"path": relative, "bytes": len(text.encode("utf-8"))}

    # -- validation -----------------------------------------------------------

    def validate(self, flow_path: str, backend: str = "web") -> dict[str, Any]:
        problems: list[str] = []
        summary: dict[str, Any] = {}
        try:
            flow = load_flow(self.workspace.resolve(flow_path))
            flow.validate_for_backend(backend)
            summary["flow"] = {
                "name": flow.name,
                "steps": len(flow.steps),
                "reset_steps": len(flow.reset),
                "targets": sorted(flow.targets),
                "variables": sorted(flow.variables()),
            }
        except FlowError as exc:
            # Every problem, not the first with a count of the rest: fixing
            # them one per run is four rounds of edit-and-retry.
            problems.extend(f"flow: {problem}" for problem in exc.problems)
            flow = None
        except Exception as exc:
            problems.append(f"flow: {type(exc).__name__}: {exc}")
            flow = None

        try:
            if flow is None:
                raise PromptsError("the flow did not load")
            prompts = prompts_for(flow)
            summary["prompts"] = {
                "count": len(prompts),
                "ids": [variant.id for variant in prompts],
                "variables": sorted(
                    {key for variant in prompts for key in variant.variables}
                ),
            }
            if flow is not None:
                prompts.check_provides(flow.variables())
        except PromptsError as exc:
            problems.append(f"prompts: {exc}")
        except Exception as exc:
            problems.append(f"prompts: {type(exc).__name__}: {exc}")

        return {"ok": not problems, "problems": problems, "summary": summary}

    # -- running --------------------------------------------------------------

    def start_run(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            job_id = f"run{self._counter:04d}"

        out_dir = run_directory(self.workspace.runs_root)
        job = RunJob(
            id=job_id,
            flow_path=request["flow"],
            out_dir=out_dir,
        )
        self.jobs[job_id] = job
        thread = threading.Thread(
            target=self._execute, args=(job, request), daemon=True
        )
        thread.start()
        return {"run_id": job_id, "out_dir": self.workspace.relative(out_dir)}

    def _execute(self, job: RunJob, request: dict[str, Any]) -> None:
        backend = request.get("backend", "web")
        only = [name for name in (request.get("only") or []) if name]
        repeats = int(request.get("repeats", 1))
        agent_mode = request.get("agent", "off")

        driver = None
        try:
            flow = load_flow(self.workspace.resolve(job.flow_path))
            prompts = prompts_for(flow).select(only or None)
            flow.validate_for_backend(backend)
            prompts.check_provides(flow.variables())

            job.status = "running"
            job.emit("started", variants=len(prompts) * repeats,
                     out_dir=self.workspace.relative(job.out_dir))

            driver = build_driver(
                backend,
                headless=not request.get("headed", False),
                resolver=build_resolver("claude" if agent_mode != "off" else "off"),
                agent_mode=agent_mode,
                learned_dir=str(
                    self.workspace.resolve(job.flow_path).parent / "learned"
                ),
            )
            driver.start(flow.app_config(backend))
            # Which window it attached to, when there was a choice. Otherwise
            # this is a decision made silently, and "it typed into the wrong
            # 3DEXPERIENCE window" is not a thing to discover from a video.
            for note in getattr(driver, "warnings", []):
                job.emit("note", text=note)

            # What was under test: whatever the form said, falling back to
            # what this flow was last run against, so it is typed once.
            given = Subject.from_config(request.get("subject") or {})
            subject = resolve_subject(flow.name, flow.subject, given)
            if given.recorded:
                remember(flow.name, flow.subject.merged_with(subject))

            runner = Runner(flow, driver, job.out_dir,
                            capture_steps=bool(request.get("capture_steps")),
                            record=bool(request.get("record")),
                            subject=subject)
            runner.prepare(prompts)
            results = []
            for variant in prompts:
                for repeat in range(repeats):
                    job.emit("variant_started", prompt_id=variant.id, repeat=repeat)
                    result = runner.run_variant(variant, repeat, repeats)
                    runner._append(result)
                    results.append(result)
                    job.emit("variant_finished", result=result.as_dict())

            job.results = [result.as_dict() for result in results]
            write_csv(results, job.out_dir / "results.csv")
            job.transcript = self.workspace.relative(write_transcript(job.out_dir))
            job.transcript_html = self.workspace.relative(write_html(job.out_dir))
            job.status = "finished"
            job.emit(
                "finished",
                ok=sum(1 for r in results if r.status is Status.OK),
                total=len(results),
                transcript=job.transcript,
                transcript_html=job.transcript_html,
            )
        except Exception as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit("failed", error=job.error, traceback=traceback.format_exc())
        finally:
            if driver is not None:
                try:
                    driver.stop()
                except Exception:
                    pass
            job.events.put(None)

    def job(self, run_id: str) -> RunJob:
        if run_id not in self.jobs:
            raise WorkspaceError(f"unknown run {run_id}")
        return self.jobs[run_id]

    # -- credentials ----------------------------------------------------------
    #
    # Only ever the masked shape leaves this process. The raw key is not sent
    # to the browser, so it cannot be read off a screenshot of the UI or a
    # browser cache.

    def read_credentials(self) -> dict[str, Any]:
        return credentials.load().public()

    def save_credentials(self, values: dict[str, Any]) -> dict[str, Any]:
        # A blank api_key means "clear it", not "leave what was there".
        return credentials.save(values).public()

    def clear_credentials(self) -> dict[str, Any]:
        return credentials.clear().public()

    def test_credentials(self) -> dict[str, Any]:
        return credentials.check()

    # -- runs and comparison --------------------------------------------------

    def describe_runs(self) -> dict[str, Any]:
        """Every run, labelled by what was under test rather than by a
        timestamp -- which is what somebody picking two to compare needs."""
        runs = []
        for directory in sorted(self.workspace.runs_root.glob("*"), reverse=True):
            if not directory.is_dir():
                continue
            entry: dict[str, Any] = {
                "dir": self.workspace.relative(directory),
                "name": directory.name,
                "flow": "", "subject": "", "tags": [], "when": "",
                "ok": 0, "total": 0,
            }
            try:
                results = load_results(directory)
            except Exception:
                results = []
            if results:
                entry["flow"] = results[0].get("flow", "")
                entry["when"] = results[0].get("timestamp", "")
                subject = Subject.from_config(results[0].get("subject") or {})
                entry["subject"] = subject.summary()
                # Separately as well, so the list can be filtered by a version
                # rather than by a substring of a sentence.
                entry["tags"] = list(subject.chips())
                entry["by_field"] = dict(subject.labels())
                entry["total"] = len(results)
                entry["ok"] = sum(1 for r in results if r.get("status") == "ok")
            entry["label"] = " · ".join(
                p for p in (entry["subject"] or entry["flow"], entry["name"]) if p)
            runs.append(entry)
        _label_by_difference(runs)
        return {"runs": runs}

    def open_windows(self, pattern: str = "*") -> dict[str, Any]:
        """What is open on this machine, for the window picker.

        A native flow attaches by window title, and on a CAD workstation the
        title is not enough on its own: 3DEXPERIENCE runs as several processes
        and more than one of them owns a window of the same name. Picking from
        a list of what is actually running beats guessing a glob.
        """
        found = list_windows(pattern)
        return {
            "windows": [window.as_dict() for window in found],
            # An empty list means two different things, and the page should be
            # able to say which: nothing matched, or this is not Windows.
            "supported": sys.platform == "win32",
        }

    # -- recording ------------------------------------------------------------

    def recording_state(self) -> dict[str, Any]:
        job = self.recording
        return {
            "available": not record_native.available(),
            "reason": record_native.available(),
            "running": bool(job and job.get("running")),
            "flow": (job or {}).get("flow"),
            "error": (job or {}).get("error"),
            "clicks": (job or {}).get("clicks", 0),
        }

    def start_recording(self, request: dict[str, Any]) -> dict[str, Any]:
        """Hook the desktop and block until the stop hotkey, on a worker.

        A separate thread rather than a subprocess because the hook has to
        live somewhere with a message loop for its whole life, and a thread
        that owns one is simpler to stop than a process to signal.
        """
        unavailable = record_native.available()
        if unavailable:
            raise WorkspaceError(unavailable)
        if self.recording and self.recording.get("running"):
            raise WorkspaceError("a recording is already running")

        name = slugify(request.get("name") or "recorded")
        title = request.get("title") or "*"
        process = request.get("process") or None
        job: dict[str, Any] = {"running": True, "flow": None, "error": None,
                               "clicks": 0}
        self.recording = job

        def work() -> None:
            try:
                path = record_native.record(title, process, name,
                                            self.workspace.root)
                job["flow"] = self.workspace.relative(path)
            except Exception as exc:
                job["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                job["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return self.recording_state()

    def known_subject_values(self) -> dict[str, Any]:
        """Every value ever recorded for each field, so they can be reused.

        Typing "R2026x FD03" again to re-run last month's build is the kind of
        retyping that produces "FD3" on the fortieth run -- and a tag with a
        typo in it is a tag that does not group.
        """
        seen: dict[str, list[str]] = {field: [] for field in SUBJECT_FIELDS}
        for directory in sorted(self.workspace.runs_root.glob("*"), reverse=True):
            if not directory.is_dir():
                continue
            try:
                results = load_results(directory)
            except Exception:
                continue
            if not results:
                continue
            subject = Subject.from_config(results[0].get("subject") or {})
            for field, value in subject.as_dict().items():
                if field in seen and value not in seen[field]:
                    seen[field].append(value)
        return {"values": seen}

    def compare(self, run_dirs: list[str]) -> dict[str, Any]:
        """Line up two or more runs. Written into the workspace so the result
        can be committed alongside the runs it is about."""
        resolved = [self.workspace.resolve(d) for d in run_dirs]
        try:
            comparison = compare_runs(resolved)
        except (ValueError, FileNotFoundError) as exc:
            return {"error": str(exc)}

        stem = "-vs-".join(Path(d).name for d in run_dirs)[:120]
        paths = write_comparison(comparison, self.workspace.root / "comparisons" / stem)
        return {
            "markdown": self.workspace.relative(paths[0]),
            "html": self.workspace.relative(paths[1]),
            "headline": comparison.headline(),
            "counts": comparison.counts(),
            "mixed_flows": comparison.mixed_flows,
            "columns": [
                {"label": c.label, "heading": c.heading,
                 "transcript": f"{self.workspace.relative(Path(c.run_dir))}/transcript.html"}
                for c in comparison.columns
            ],
        }

    def remembered_subject(self, flow_path: str) -> dict[str, Any]:
        """Pre-fill the run form with what this flow was last run against."""
        try:
            flow = load_flow(self.workspace.resolve(flow_path))
        except Exception:
            return {"subject": {}}
        # The same order the run itself uses, so the form shows what would
        # actually be recorded rather than something close to it.
        subject = resolve_subject(flow.name, flow.subject, Subject())
        if not subject.recorded:
            # Nothing remembered on this machine. The runs on disk are the
            # better record anyway: a workspace cloned from the repository
            # arrives full of runs and an empty local store, and the last
            # thing this flow was run against is sitting right there.
            subject = self._subject_of_last_run(flow.name)
        return {"subject": subject.as_dict(), "summary": subject.summary()}

    def _subject_of_last_run(self, flow_name: str) -> Subject:
        for directory in sorted(self.workspace.runs_root.glob("*"), reverse=True):
            if not directory.is_dir():
                continue
            try:
                results = load_results(directory)
            except Exception:
                continue
            if results and results[0].get("flow") == flow_name:
                return Subject.from_config(results[0].get("subject") or {})
        return Subject()

    # -- repository -----------------------------------------------------------

    @property
    def repository(self) -> Repository:
        return Repository(self.workspace.root)

    def open_workspace(self, path: str) -> dict[str, Any]:
        """Point the whole UI at a different folder.

        An absolute path, deliberately: this is the one place the workspace's
        own path guard cannot apply, because changing the workspace is exactly
        what is being asked for. It is a local tool bound to the loopback
        interface, and the person driving it already has a shell.
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            return {"error": "give an absolute path to the folder"}
        if not target.exists():
            return {"error": f"{target} does not exist"}
        if not target.is_dir():
            return {"error": f"{target} is not a folder"}

        self.workspace = Workspace(target)
        return {"workspace": str(self.workspace.root), "repo": self.repo_state()}

    def connect_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Point the UI at a local folder, a GitHub repo, or a GitLab repo.

        Cloning is the same git command for both providers; asking separately
        is so each can ask for what it actually needs -- owner/repo for GitHub,
        a project path and possibly a hostname for a company GitLab, and
        neither for a folder.
        """
        try:
            source = parse_source(payload)
        except SourceError as exc:
            return {"error": str(exc)}

        target = Path(source.directory).expanduser()
        if source.kind == "local":
            outcome = self.open_workspace(str(target))
        else:
            if target.exists() and any(target.iterdir()):
                # Already cloned: opening it is almost certainly what was
                # meant, and refusing would send them to a shell to find out.
                outcome = self.open_workspace(str(target))
            else:
                host = source.resolved_host
                token = credentials.load().git_token(host)
                try:
                    Git.clone(source.clone_url, target, token=token, host=host,
                              branch=source.branch)
                except GitError as exc:
                    return {"error": str(exc)}
                outcome = self.open_workspace(str(target))

        if "error" in outcome:
            return outcome
        outcome["recent"] = recent_workspaces.remember({
            "kind": source.kind,
            "directory": str(target),
            # A folder is a folder. Carrying a repository name that was typed
            # into the form and then not used makes the recent list lie.
            "project": source.project if source.kind != "local" else "",
            "host": source.host if source.kind != "local" else "",
        })
        return outcome

    def recent_workspaces(self) -> dict[str, Any]:
        return {"recent": recent_workspaces.load(),
                "workspace": str(self.workspace.root)}

    def forget_workspace(self, directory: str) -> dict[str, Any]:
        return {"recent": recent_workspaces.forget(directory)}

    def repo_state(self) -> dict[str, Any]:
        return self.repository.state()

    def repo_commit(self, paths: list[str], message: str) -> dict[str, Any]:
        """Commit exactly the paths the user ticked.

        Never everything that happens to be dirty: this is somebody's working
        repository, and a tool that stages the lot will one day commit
        something they were halfway through.
        """
        try:
            outcome = self.repository.commit(list(paths or []), message)
        except GitError as exc:
            return {"error": str(exc)}
        outcome["state"] = self.repo_state()
        return outcome

    def repo_push(self) -> dict[str, Any]:
        try:
            return self.repository.push()
        except GitError as exc:
            return {"error": str(exc), "state": self.repo_state()}

    def repo_pull(self) -> dict[str, Any]:
        try:
            return self.repository.pull()
        except GitError as exc:
            return {"error": str(exc), "state": self.repo_state()}

    def repo_checkout(self, branch: str, create: bool = False) -> dict[str, Any]:
        try:
            return {"state": self.repository.checkout(branch, create=create)}
        except GitError as exc:
            return {"error": str(exc), "state": self.repo_state()}

    def repo_preview_publish(self, run_dir: str,
                             include_video: bool = False) -> dict[str, Any]:
        self.workspace.resolve(run_dir)
        try:
            return self.repository.preview_publish(
                run_dir, include_video=include_video)
        except (GitError, ValueError) as exc:
            return {"error": str(exc)}

    def repo_publish(self, run_dir: str, message: str = "",
                     include_video: bool = False,
                     push: bool = True) -> dict[str, Any]:
        self.workspace.resolve(run_dir)
        try:
            return self.repository.publish(
                run_dir, message=message, include_video=include_video, push=push)
        except (GitError, ValueError) as exc:
            return {"error": str(exc)}

    def save_git_token(self, host: str, token: str) -> dict[str, Any]:
        try:
            return credentials.save_git_token(host, token).public()
        except ValueError as exc:
            return {"error": str(exc)}

    def clear_git_token(self, host: str) -> dict[str, Any]:
        return credentials.clear_git_token(host).public()

    def rebuild_transcript(self, run_dir: str) -> dict[str, Any]:
        resolved = self.workspace.resolve(run_dir)
        return {
            "transcript": self.workspace.relative(write_transcript(resolved)),
            "transcript_html": self.workspace.relative(write_html(resolved)),
        }

    def export_pdf(self, run_dir: str) -> dict[str, Any]:
        """Print the transcript. A failure here is a message, not a 500: the
        run and its transcript are fine, only the export is missing."""
        resolved = self.workspace.resolve(run_dir)
        write_html(resolved)
        outcome = write_pdf(resolved)
        if not outcome.ok:
            return {"error": outcome.error}
        return {"pdf": self.workspace.relative(outcome.path)}

    def export_standalone(self, run_dir: str) -> dict[str, Any]:
        """One HTML file with everything inlined, for sending to someone who
        does not have the run folder.

        Every prompt run, not the index: the point of the export is that it
        arrives complete, and an index linking eleven files that did not travel
        with it is the opposite.
        """
        from understudy.transcript_html import render_full_html

        resolved = self.workspace.resolve(run_dir)
        path = resolved / "transcript-standalone.html"
        path.write_text(render_full_html(resolved, embed=True), encoding="utf-8")
        return {"html": self.workspace.relative(path)}


#: Most specific first. A list of runs is read left to right and truncated on
#: the right, so the build has to be the part that survives: "FD04 R2026x…"
#: identifies a run and "3DEXPERIENCE R2026x · LE…" does not.
SPECIFIC_FIRST = ("model_version", "app_version", "release", "model", "app")


def _label_by_difference(runs: list[dict[str, Any]]) -> None:
    """Give each run a short label: what its siblings do not have.

    Runs of one flow differ in one tag or two -- the same application, the same
    assistant, a different build -- and the whole subject in a narrow column
    truncates at every character except the one saying which run this is.
    """
    by_flow: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_flow.setdefault(run.get("flow", ""), []).append(run)

    for siblings in by_flow.values():
        fields = [run.get("by_field") or {} for run in siblings]
        shared = {
            field for field in SPECIFIC_FIRST
            if len({f.get(field, "") for f in fields}) == 1
        } if len(siblings) > 1 else set()
        for run in siblings:
            by_field = run.get("by_field") or {}
            ordered = [by_field[field] for field in SPECIFIC_FIRST
                       if by_field.get(field) and field not in shared]
            if not ordered:
                # Every tag identical: the timestamp is all that is left, and
                # two runs of one build against one release do differ by when.
                ordered = [run.get("when", "")[:16] or run["name"]]
            run["short"] = " ".join(ordered)


class Handler(BaseHTTPRequestHandler):
    api: Api = None  # set by serve()
    server_version = "understudy"

    def log_message(self, *_args) -> None:  # quiet by default
        pass

    # -- plumbing -------------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes ---------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        route = url.path
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if route in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif route == "/api/files":
                self._json(self.api.list_files())
            elif route == "/api/credentials":
                self._json(self.api.read_credentials())
            elif route == "/api/runs":
                self._json(self.api.describe_runs())
            elif route == "/api/subjects":
                self._json(self.api.known_subject_values())
            elif route == "/api/record":
                self._json(self.api.recording_state())
            elif route == "/api/windows":
                self._json(self.api.open_windows(params.get("pattern", "*")))
            elif route == "/api/repo":
                self._json(self.api.repo_state())
            elif route == "/api/workspace":
                self._json(self.api.recent_workspaces())
            elif route == "/api/flows":
                self._json({"flows": self.api.describe_flows()})
            elif route == "/api/suites":
                self._json({"suites": self.api.describe_suites()})
            elif route == "/api/file":
                self._json(self.api.read_file(params["path"]))
            elif route.startswith("/api/run/") and route.endswith("/events"):
                self._stream(route.split("/")[3])
            elif route.startswith("/files/"):
                self._serve_file(unquote(route[len("/files/"):]))
            else:
                self._json({"error": f"no route {route}"}, 404)
        except WorkspaceError as exc:
            self._json({"error": str(exc)}, 400)
        except KeyError as exc:
            self._json({"error": f"missing parameter {exc}"}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            body = self._body()
            if route == "/api/file":
                self._json(self.api.write_file(body["path"], body.get("text", "")))
            elif route == "/api/validate":
                self._json(self.api.validate(body["flow"], body.get("backend", "web")))
            elif route == "/api/new":
                self._json(self.api.create(
                    body.get("kind", "flow"), body["path"],
                    body.get("title", ""), body.get("description", ""),
                ))
            elif route == "/api/delete":
                self._json(self.api.delete(
                    body["path"], bool(body.get("with_contents", False))
                ))
            elif route == "/api/duplicate":
                self._json(self.api.duplicate(
                    body["source"], body["destination"], body.get("name"),
                    body.get("title"), body.get("description"),
                ))
            elif route == "/api/run":
                self._json(self.api.start_run(body))
            elif route == "/api/transcript":
                self._json(self.api.rebuild_transcript(body["run_dir"]))
            elif route == "/api/transcript/pdf":
                self._json(self.api.export_pdf(body["run_dir"]))
            elif route == "/api/workspace/open":
                self._json(self.api.open_workspace(body["path"]))
            elif route == "/api/workspace/connect":
                self._json(self.api.connect_workspace(body))
            elif route == "/api/workspace/forget":
                self._json(self.api.forget_workspace(body["directory"]))
            elif route == "/api/repo/suggest-message":
                self._json({"message": self.api.repository.suggest_message(
                    body.get("paths") or [])})
            elif route == "/api/compare":
                self._json(self.api.compare(body["run_dirs"]))
            elif route == "/api/record/start":
                self._json(self.api.start_recording(body))
            elif route == "/api/subject":
                self._json(self.api.remembered_subject(body["flow"]))
            elif route == "/api/repo/commit":
                self._json(self.api.repo_commit(
                    body.get("paths") or [], body.get("message", "")))
            elif route == "/api/repo/push":
                self._json(self.api.repo_push())
            elif route == "/api/repo/pull":
                self._json(self.api.repo_pull())
            elif route == "/api/repo/checkout":
                self._json(self.api.repo_checkout(
                    body["branch"], bool(body.get("create"))))
            elif route == "/api/repo/publish/preview":
                self._json(self.api.repo_preview_publish(
                    body["run_dir"], bool(body.get("include_video"))))
            elif route == "/api/repo/publish":
                self._json(self.api.repo_publish(
                    body["run_dir"], body.get("message", ""),
                    bool(body.get("include_video")),
                    bool(body.get("push", True))))
            elif route == "/api/credentials/git":
                self._json(self.api.save_git_token(body["host"], body["token"]))
            elif route == "/api/credentials/git/delete":
                self._json(self.api.clear_git_token(body["host"]))
            elif route == "/api/transcript/standalone":
                self._json(self.api.export_standalone(body["run_dir"]))
            elif route == "/api/credentials":
                self._json(self.api.save_credentials(body))
            elif route == "/api/credentials/clear":
                self._json(self.api.clear_credentials())
            elif route == "/api/credentials/test":
                self._json(self.api.test_credentials())
            else:
                self._json({"error": f"no route {route}"}, 404)
        except (WorkspaceError, AuthoringError, FlowError, PromptsError) as exc:
            self._json({"error": str(exc)}, 400)
        except KeyError as exc:
            self._json({"error": f"missing field {exc}"}, 400)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _stream(self, run_id: str) -> None:
        """Server-sent events: progress as each variant finishes, so a long
        sweep is not a blank screen."""
        job = self.api.job(run_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        while True:
            event = job.events.get()
            if event is None:
                self.wfile.write(b"data: {\"type\": \"end\"}\n\n")
                self.wfile.flush()
                return
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
            self.wfile.flush()

    CONTENT_TYPES = {
        ".png": "image/png", ".md": "text/markdown; charset=utf-8",
        ".json": "application/json", ".jsonl": "application/x-ndjson",
        ".csv": "text/csv", ".yaml": "text/plain; charset=utf-8",
        ".yml": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".mp4": "video/mp4",
        ".webm": "video/webm", ".pdf": "application/pdf",
    }

    def _serve_file(self, relative: str) -> None:
        path = self.api.workspace.resolve(relative)
        if not path.is_file():
            self._json({"error": f"no such file: {relative}"}, 404)
            return
        content_type = self.CONTENT_TYPES.get(
            path.suffix.lower(), "application/octet-stream"
        )
        data = path.read_bytes()

        # Range requests, so the transcript's video can be scrubbed. Without
        # them a browser will play a video from the start and refuse to seek,
        # which makes the recording useless for the one thing it is for:
        # jumping to the step somebody is asking about.
        span = self._requested_range(len(data))
        if span is None:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        start, end = span
        chunk = data[start:end + 1]
        self.send_response(206)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def _requested_range(self, size: int) -> tuple[int, int] | None:
        """(start, end) inclusive, or None for the whole file."""
        header = self.headers.get("Range", "")
        if not header.startswith("bytes=") or "," in header:
            return None
        first, _, last = header[len("bytes="):].partition("-")
        try:
            if not first:                      # bytes=-500: the final 500 bytes
                length = int(last)
                if length <= 0:
                    return None
                return max(0, size - length), size - 1
            start = int(first)
            end = int(last) if last else size - 1
        except ValueError:
            return None
        if start >= size or start > end:
            return None
        return start, min(end, size - 1)


def serve(workspace: Path | str = ".", host: str = "127.0.0.1", port: int = 8765):
    """Start the server. Returns it; call serve_forever() or shutdown()."""
    Handler.api = Api(Workspace(workspace))
    return ThreadingHTTPServer((host, port), Handler)
