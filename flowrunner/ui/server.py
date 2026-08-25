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
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from flowrunner import credentials
from flowrunner.authoring import AuthoringError, duplicate_file, slugify
from flowrunner.drivers import build as build_driver
from flowrunner.flow import FlowError, load_flow
from flowrunner.prompts import PromptsError, prompts_for
from flowrunner.suite import SuiteError, is_suite_file, load_suite
from flowrunner.report import write_report
from flowrunner.resolvers import build as build_resolver
from flowrunner.runner import Runner, Status, run_directory, write_csv

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
    report: str | None = None
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
        return "steps" in keys and bool({"targets", "prompts"} & keys)


class Api:
    """The verbs the UI needs. Kept apart from HTTP so it can be tested
    directly."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.jobs: dict[str, RunJob] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # -- files ----------------------------------------------------------------

    def list_files(self) -> dict[str, Any]:
        return self.workspace.listing()

    def describe_flows(self) -> list[dict[str, Any]]:
        """Title, description and variant count for every flow, so the picker
        shows something a person can choose between."""
        described = []
        for relative in self.workspace.listing()["flows"]:
            entry: dict[str, Any] = {"path": relative, "title": relative,
                                     "description": "", "tags": [],
                                     "variants": 0, "embedded": False, "error": None}
            try:
                flow = load_flow(self.workspace.resolve(relative))
                entry.update(
                    title=flow.title or flow.name, description=flow.description,
                    tags=list(flow.tags), steps=len(flow.steps),
                    variants=len(flow.embedded_prompts),
                    embedded=bool(flow.embedded_prompts),
                )
            except Exception as exc:
                entry["error"] = str(exc)
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

# One entry per variant. Any key besides `id` is a variable the steps can use.
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
            problems.append(f"flow: {exc}")
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

            runner = Runner(flow, driver, job.out_dir,
                            capture_steps=bool(request.get("capture_steps")),
                            record=bool(request.get("record")))
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
            job.report = self.workspace.relative(write_report(job.out_dir))
            job.status = "finished"
            job.emit(
                "finished",
                ok=sum(1 for r in results if r.status is Status.OK),
                total=len(results),
                report=job.report,
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

    def rebuild_report(self, run_dir: str) -> dict[str, Any]:
        path = write_report(self.workspace.resolve(run_dir))
        return {"report": self.workspace.relative(path)}


class Handler(BaseHTTPRequestHandler):
    api: Api = None  # set by serve()
    server_version = "flowrunner"

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
            elif route == "/api/report":
                self._json(self.api.rebuild_report(body["run_dir"]))
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

    def _serve_file(self, relative: str) -> None:
        path = self.api.workspace.resolve(relative)
        if not path.is_file():
            self._json({"error": f"no such file: {relative}"}, 404)
            return
        types = {
            ".png": "image/png", ".md": "text/markdown; charset=utf-8",
            ".json": "application/json", ".jsonl": "application/x-ndjson",
            ".csv": "text/csv", ".yaml": "text/plain; charset=utf-8",
            ".yml": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        }
        self._send(200, path.read_bytes(),
                   types.get(path.suffix.lower(), "application/octet-stream"))


def serve(workspace: Path | str = ".", host: str = "127.0.0.1", port: int = 8765):
    """Start the server. Returns it; call serve_forever() or shutdown()."""
    Handler.api = Api(Workspace(workspace))
    return ThreadingHTTPServer((host, port), Handler)
