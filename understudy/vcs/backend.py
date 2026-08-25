"""The repository as the UI sees it.

One object over the git wrapper, the remote parser and the publish rules, so
neither the HTTP layer nor the CLI has to know how any of them fit together --
and so the awkward parts happen in one place: finding the token for the right
host, keeping run outputs out of the repository until somebody asks, and never
letting a credential out through a return value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from understudy import credentials
from understudy.vcs.git import Git, GitError
from understudy.vcs.publish import (
    Selection, commit_message, select, suggest_message, video_note,
)
from understudy.vcs.remote import parse_remote
from understudy.vcs.workspace_guard import is_source_checkout, refusal


class Repository:
    """Git operations for one workspace."""

    def __init__(self, root: Path | str,
                 credentials_path: Path | str | None = None) -> None:
        self.root = Path(root).resolve()
        self.git = Git(self.root)
        self.credentials_path = credentials_path

    @property
    def read_only(self) -> bool:
        """True when this workspace must not be written to at all."""
        return is_source_checkout(self.root)

    def _refuse_if_read_only(self) -> None:
        if self.read_only:
            raise GitError(refusal(self.root))

    # -- credentials ----------------------------------------------------------

    def _token_for(self, host: str) -> str:
        """The stored token for this host, if there is one.

        No token is not an error. Most checkouts push over SSH or through the
        platform's credential helper, and those work without anything here.
        """
        if not host:
            return ""
        return credentials.load(self.credentials_path).git_token(host)

    # -- reading --------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Everything the repository panel renders. Never raises."""
        status = self.git.status()
        remote = parse_remote(status.remote_url)
        host = remote.host
        return {
            "is_repo": status.is_repo,
            "note": status.note,
            "branch": status.branch,
            "detached": status.detached,
            "branches": self.git.branches(),
            "upstream": status.upstream,
            "ahead": status.ahead,
            "behind": status.behind,
            "dirty": status.dirty,
            "changes": [
                {"path": change.path, "state": change.state, "staged": change.staged}
                for change in status.changes
            ],
            "remote": {
                "url": _safe_url(status.remote_url),
                "host": host,
                "path": remote.path,
                "provider": remote.provider,
                "web_url": remote.web_url,
                "token_help": remote.token_help,
            },
            "has_token": bool(self._token_for(host)),
            "recent": self.git.log(limit=5),
            "read_only": self.read_only,
            "read_only_reason": refusal(self.root) if self.read_only else "",
            "root": str(self.root),
        }

    def browse_url(self, relative_path: str) -> str:
        status = self.git.status()
        remote = parse_remote(status.remote_url)
        return remote.browse(relative_path, status.branch or "main")

    # -- writing --------------------------------------------------------------

    def suggest_message(self, paths: list[str]) -> str:
        return suggest_message(list(paths or []))

    def commit(self, paths: list[str], message: str) -> dict[str, Any]:
        self._refuse_if_read_only()
        sha = self.git.commit(paths, message or suggest_message(list(paths or [])))
        if sha is None:
            return {"committed": False, "reason": "nothing to commit"}
        return {"committed": True, "sha": sha, "url": self._commit_url(sha)}

    def push(self) -> dict[str, Any]:
        self._refuse_if_read_only()
        status = self.git.status()
        remote = parse_remote(status.remote_url)
        output = self.git.push(token=self._token_for(remote.host), host=remote.host)
        return {"pushed": True, "output": output, "state": self.state()}

    def pull(self) -> dict[str, Any]:
        status = self.git.status()
        remote = parse_remote(status.remote_url)
        output = self.git.pull(token=self._token_for(remote.host), host=remote.host)
        return {"pulled": True, "output": output, "state": self.state()}

    def checkout(self, branch: str, create: bool = False) -> dict[str, Any]:
        self.git.checkout(branch, create=create)
        return self.state()

    # -- publishing a run -----------------------------------------------------

    def preview_publish(self, run_dir: str, include_video: bool = False,
                        include_images: bool = True) -> dict[str, Any]:
        """What a publish would do, before it does it.

        Committing is not undoable in any way a person would call undoing, so
        the list is shown first.
        """
        chosen = self._select(run_dir, include_video, include_images)
        return {
            "include": list(chosen.include),
            "excluded": {k: list(v) for k, v in chosen.excluded.items()},
            "total_bytes": chosen.total_bytes,
            "summary": chosen.summary(),
            "message": self._message_for(run_dir),
        }

    def publish(self, run_dir: str, message: str = "",
                include_video: bool = False, include_images: bool = True,
                push: bool = True) -> dict[str, Any]:
        """Commit one run's evidence, and push it unless told not to."""
        self._refuse_if_read_only()
        chosen = self._select(run_dir, include_video, include_images)
        if chosen.empty:
            return {"committed": False, "reason": "nothing in that run to publish"}

        paths = list(chosen.include)
        note = video_note(chosen, Path(run_dir).name)
        if note:
            # A transcript linking a recording that is not in the repository
            # looks like a broken link. This turns it into a decision.
            note_path = self.root / run_dir / "recordings-not-committed.txt"
            note_path.write_text(note, encoding="utf-8")
            paths.append(f"{run_dir.rstrip('/')}/recordings-not-committed.txt")

        outcome = self.commit(paths, message.strip() or self._message_for(run_dir))
        if not outcome.get("committed"):
            return outcome
        outcome["published"] = list(chosen.include)
        outcome["excluded"] = {k: list(v) for k, v in chosen.excluded.items()}
        if push:
            try:
                self.push()
                outcome["pushed"] = True
            except GitError as exc:
                # The commit is real and worth keeping; only the push failed.
                outcome["pushed"] = False
                outcome["push_error"] = str(exc)
        return outcome

    # -- internals ------------------------------------------------------------

    def _select(self, run_dir: str, include_video: bool,
                include_images: bool) -> Selection:
        resolved = (self.root / run_dir).resolve()
        return select(resolved, self.root, include_video=include_video,
                      include_images=include_images)

    def _message_for(self, run_dir: str) -> str:
        results = _read_results(self.root / run_dir / "results.jsonl")
        return commit_message(Path(run_dir), results)

    def _commit_url(self, sha: str) -> str:
        return parse_remote(self.git.remote_url()).commit_url(sha)


def _safe_url(url: str) -> str:
    """A remote URL fit to show. Some people do embed a token in one."""
    from understudy.vcs.git import scrub

    return scrub(url)


def _read_results(path: Path) -> list[dict[str, Any]]:
    import json

    if not path.exists():
        return []
    try:
        return [
            json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except Exception:
        return []
