"""Where a workspace comes from: a folder, GitHub, or GitLab.

Cloning is the same git command whichever it is, so the distinction is not
technical -- it is that the three ask for different things and go wrong in
different ways. Somebody connecting to GitHub knows `owner/repo`, not a clone
URL. Somebody connecting to a company GitLab knows the project path and the
hostname of their instance, and will otherwise be quietly sent to gitlab.com.
And somebody who just wants a folder should not be asked about either.

So the choice is explicit, each kind asks only for what it needs, and the URL
is assembled here rather than typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KINDS = ("local", "github", "gitlab")
DEFAULT_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}

#: owner/repo, or GitLab's group/subgroup/project. Deliberately strict: a typo
#: here becomes a confusing 404 from git several seconds later.
PROJECT_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+$")


class SourceError(ValueError):
    """The connection details do not make sense yet."""


@dataclass(frozen=True)
class WorkspaceSource:
    kind: str = "local"
    #: Absolute path of the folder to use, or to clone into.
    directory: str = ""
    #: owner/repo on GitHub; group/project (possibly nested) on GitLab.
    project: str = ""
    #: Left empty for the public instance; set for a self-hosted GitLab.
    host: str = ""
    branch: str = ""

    @property
    def resolved_host(self) -> str:
        return (self.host or DEFAULT_HOSTS.get(self.kind, "")).strip().lower()

    @property
    def clone_url(self) -> str:
        if self.kind == "local":
            return ""
        return f"https://{self.resolved_host}/{self.project.strip('/')}.git"

    @property
    def suggested_directory(self) -> str:
        """A sensible folder name, so nobody has to invent one."""
        if self.kind == "local" or not self.project:
            return ""
        return self.project.rstrip("/").split("/")[-1]


def parse(payload: dict) -> WorkspaceSource:
    """Validate what the UI sent, and say what is missing rather than failing
    later inside git."""
    kind = str(payload.get("kind") or "local").strip().lower()
    if kind not in KINDS:
        raise SourceError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")

    directory = str(payload.get("directory") or "").strip()
    source = WorkspaceSource(
        kind=kind,
        directory=directory,
        project=str(payload.get("project") or "").strip().strip("/"),
        host=str(payload.get("host") or "").strip(),
        branch=str(payload.get("branch") or "").strip(),
    )

    if not directory:
        raise SourceError(
            "give the folder to use" if kind == "local"
            else "give the folder to clone into"
        )
    if not Path(directory).expanduser().is_absolute():
        raise SourceError(f"{directory} is not an absolute path")

    if kind == "local":
        return source

    if not source.project:
        label = "owner/repo" if kind == "github" else "group/project"
        raise SourceError(f"give the repository as {label}")
    if not PROJECT_PATH.match(source.project):
        label = "owner/repo" if kind == "github" else "group/project"
        raise SourceError(
            f"{source.project!r} does not look like {label}. Paste the path "
            f"from the repository's page, not the whole URL."
        )
    if kind == "github" and source.project.count("/") > 1:
        raise SourceError(
            "GitHub repositories are owner/repo; only GitLab nests groups"
        )
    return source


def from_url(url: str) -> WorkspaceSource:
    """Best effort at filling the form from a pasted URL, because somebody
    will paste one into every field on this page."""
    from understudy.vcs.remote import parse_remote

    remote = parse_remote(url.strip())
    if not remote.path:
        return WorkspaceSource()
    kind = remote.provider if remote.provider in ("github", "gitlab") else "local"
    host = "" if remote.host in DEFAULT_HOSTS.values() else remote.host
    return WorkspaceSource(kind=kind, project=remote.path, host=host)
