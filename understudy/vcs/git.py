"""A small, deliberate slice of git.

Not a git library -- the git binary, driven by subprocess. Two reasons. It is
already on every machine that would be checking out a flow repository, and it
already knows about credential helpers, corporate proxies, SSH keys and
self-hosted certificates. Reimplementing any of that against a REST API would
be work spent recreating something the user has already got working.

What is exposed is only what the UI needs: what changed, commit it, push, pull,
which branch. Anything harder than that is a job for the user's own git.

Two rules run through this file:

  * **Nothing here ever writes a credential to disk.** A token is passed for
    the length of one command through `-c http.extraheader`, never into
    `.git/config` and never into a remote URL, either of which would leave it
    sitting in the checkout for anyone who looks.
  * **Nothing here ever puts a credential in an error message.** Git is happy
    to echo a URL containing a token; the output is scrubbed before it is
    raised, logged or shown.
"""

from __future__ import annotations

import base64
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Long enough that a slow first clone over a corporate proxy still finishes.
DEFAULT_TIMEOUT_S = 120
CLONE_TIMEOUT_S = 600

#: (pattern, replacement). Each keeps only the part that is safe to read: the
#: scheme, or the header name. The secret itself is replaced, never appended to
#: -- getting that backwards leaves the token in the output followed by a mask,
#: which looks redacted and is not.
_SECRET_PATTERNS = (
    # user:token@host in a remote URL
    (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1***@"),
    # GitHub personal access tokens, in all their prefixes
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), "***"),
    # GitLab personal and project access tokens
    (re.compile(r"\bgl(?:pat|soat|ptt)-[A-Za-z0-9_-]{16,}\b"), "***"),
    # anything we sent ourselves
    (re.compile(r"(Authorization: *\w+ *)[A-Za-z0-9+/=._-]+", re.I), r"\1***"),
)


def scrub(text: str) -> str:
    """Remove anything that looks like a credential."""
    out = text or ""
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class GitError(RuntimeError):
    """A git command failed. The message is scrubbed and usually git's own."""


@dataclass(frozen=True)
class Change:
    """One path git considers different from HEAD."""

    path: str
    #: added | modified | deleted | renamed | untracked | conflicted
    state: str
    staged: bool = False


@dataclass(frozen=True)
class GitStatus:
    """Enough to render the repository panel without a second call."""

    is_repo: bool = False
    branch: str = ""
    detached: bool = False
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    changes: tuple[Change, ...] = ()
    remote_url: str = ""
    #: Set when git is not installed, or the folder is not a checkout.
    note: str = ""

    @property
    def dirty(self) -> bool:
        return bool(self.changes)

    @property
    def can_push(self) -> bool:
        return self.is_repo and bool(self.remote_url)


#: git status --porcelain=v2 codes -> what a person would call it.
_XY_STATES = {
    "A": "added", "M": "modified", "D": "deleted", "R": "renamed",
    "C": "copied", "T": "modified", "U": "conflicted",
}


class Git:
    """Git commands run in one directory."""

    def __init__(self, root: Path | str, timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
        self.root = Path(root)
        self.timeout_s = timeout_s

    # -- running --------------------------------------------------------------

    def run(self, *args: str, token: str = "", host: str = "",
            timeout_s: int | None = None, check: bool = True) -> str:
        """One git command. Returns stdout; raises GitError on failure.

        A token, when given, is supplied as a per-invocation header rather than
        written anywhere. `host` scopes it, so a checkout with several remotes
        cannot send one service's token to another.
        """
        command = ["git", "-C", str(self.root)]
        if token:
            header = base64.standard_b64encode(
                f"x-access-token:{token}".encode()
            ).decode("ascii")
            scope = f"http.https://{host}/." if host else "http."
            command += ["-c", f"{scope}extraheader=Authorization: Basic {header}"]
        command += list(args)

        try:
            done = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout_s or self.timeout_s,
            )
        except FileNotFoundError:
            raise GitError(
                "git is not installed, or not on PATH. Understudy uses the git "
                "binary so it inherits your credential helper, proxy and SSH "
                "setup."
            ) from None
        except subprocess.TimeoutExpired:
            raise GitError(
                f"git {args[0] if args else ''} timed out after "
                f"{timeout_s or self.timeout_s}s"
            ) from None

        if check and done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip()
            raise GitError(scrub(detail) or f"git {' '.join(args)} failed")
        return done.stdout

    # -- reading --------------------------------------------------------------

    @property
    def is_repo(self) -> bool:
        try:
            return self.run("rev-parse", "--is-inside-work-tree").strip() == "true"
        except GitError:
            return False

    def remote_url(self, name: str = "origin") -> str:
        try:
            return self.run("remote", "get-url", name).strip()
        except GitError:
            return ""

    def branches(self) -> list[str]:
        try:
            out = self.run("for-each-ref", "--format=%(refname:short)", "refs/heads")
        except GitError:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def status(self) -> GitStatus:
        """Never raises. A panel that cannot render is worse than one that says
        why it is empty."""
        try:
            if not self.is_repo:
                return GitStatus(note=f"{self.root} is not a git checkout")
        except Exception as exc:                        # pragma: no cover
            return GitStatus(note=scrub(str(exc)))

        try:
            raw = self.run("status", "--porcelain=v2", "--branch",
                           "--untracked-files=all")
        except GitError as exc:
            return GitStatus(is_repo=True, note=str(exc))

        branch, upstream, ahead, behind, detached = "", "", 0, 0, False
        changes: list[Change] = []
        for line in raw.splitlines():
            if line.startswith("# branch.head "):
                branch = line.split(" ", 2)[2].strip()
                detached = branch == "(detached)"
            elif line.startswith("# branch.upstream "):
                upstream = line.split(" ", 2)[2].strip()
            elif line.startswith("# branch.ab "):
                parts = line.split()
                ahead = int(parts[2].lstrip("+") or 0)
                behind = int(parts[3].lstrip("-") or 0)
            elif line.startswith("? "):
                changes.append(Change(path=line[2:].strip(), state="untracked"))
            elif line.startswith("u "):
                changes.append(Change(path=line.split(" ", 10)[-1].strip(),
                                      state="conflicted"))
            elif line.startswith(("1 ", "2 ")):
                fields = line.split(" ", 8)
                xy = fields[1]
                path = fields[8]
                if line.startswith("2 "):
                    # A rename records "new\told"; the new name is the one to
                    # show, and the tab is the separator, not a space.
                    path = path.split("\t", 1)[0]
                staged = xy[0] != "."
                code = xy[0] if staged else xy[1]
                changes.append(Change(path=path.strip(),
                                      state=_XY_STATES.get(code, "modified"),
                                      staged=staged))

        return GitStatus(
            is_repo=True, branch=branch, detached=detached, upstream=upstream,
            ahead=ahead, behind=behind, changes=tuple(changes),
            remote_url=self.remote_url(),
        )

    def log(self, limit: int = 10, path: str | None = None) -> list[dict[str, str]]:
        args = ["log", f"-{max(1, limit)}", "--date=iso-strict",
                "--format=%H%x1f%an%x1f%ad%x1f%s"]
        if path:
            args += ["--", path]
        try:
            raw = self.run(*args)
        except GitError:
            return []
        entries = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                entries.append({"sha": parts[0], "author": parts[1],
                                "date": parts[2], "subject": parts[3]})
        return entries

    # -- writing --------------------------------------------------------------

    def commit(self, paths: list[str], message: str,
               author: str = "") -> str | None:
        """Stage exactly these paths and commit them. None if nothing changed.

        Only the named paths are staged. A tool that runs `git add -A` on
        somebody's working repository will one day commit something they were
        halfway through, and they will not forgive it.
        """
        if not message.strip():
            raise GitError("a commit needs a message")
        if not paths:
            return None

        self.run("add", "--", *paths)
        if not self.run("diff", "--cached", "--name-only").strip():
            return None

        args = ["commit", "-m", message]
        if author:
            args += ["--author", author]
        self.run(*args)
        return self.run("rev-parse", "HEAD").strip()

    def push(self, token: str = "", host: str = "", set_upstream: bool = True) -> str:
        status = self.status()
        if not status.branch or status.detached:
            raise GitError("cannot push from a detached HEAD; check out a branch")
        args = ["push"]
        if set_upstream and not status.upstream:
            args += ["--set-upstream", "origin", status.branch]
        return scrub(self.run(*args, token=token, host=host))

    def pull(self, token: str = "", host: str = "") -> str:
        # --ff-only: a merge commit created by a background tool, in a
        # repository somebody else is also using, is a surprise nobody wants.
        # If it will not fast-forward, that is a real decision for a human.
        return scrub(self.run("pull", "--ff-only", token=token, host=host))

    def checkout(self, branch: str, create: bool = False) -> None:
        self.run("checkout", *(["-b"] if create else []), branch)

    @classmethod
    def clone(cls, url: str, destination: Path | str, token: str = "",
              host: str = "", branch: str = "") -> "Git":
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            raise GitError(f"{destination} already exists and is not empty")
        destination.parent.mkdir(parents=True, exist_ok=True)

        git = cls(destination.parent, timeout_s=CLONE_TIMEOUT_S)
        args = ["clone"]
        if branch:
            args += ["--branch", branch]
        args += [url, str(destination)]
        git.run(*args, token=token, host=host, timeout_s=CLONE_TIMEOUT_S)
        return cls(destination)
