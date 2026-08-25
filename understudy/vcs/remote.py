"""Reading a git remote well enough to link to it.

Everything else about the backend is provider-agnostic, because it is just git.
This is the part that is not: GitHub and GitLab spell "show me this file on the
web" differently, and a self-hosted GitLab is on a host nobody can guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

#: git@host:owner/repo.git
SSH_FORM = re.compile(r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:/]+):(?P<path>.+?)(?:\.git)?$")
#: scheme://[user[:pass]@]host[:port]/owner/repo.git
URL_FORM = re.compile(
    r"^(?P<scheme>https?|ssh|git)://"
    r"(?:(?P<user>[^@/]+)@)?(?P<host>[^/:]+)(?::(?P<port>\d+))?"
    r"/(?P<path>.+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class Remote:
    """Where a checkout came from, in the terms the UI needs."""

    url: str
    host: str = ""
    #: owner/repo, or group/subgroup/repo on GitLab, which allows nesting.
    path: str = ""
    provider: str = "unknown"

    @property
    def known(self) -> bool:
        return self.provider in ("github", "gitlab")

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.path}" if self.host and self.path else ""

    def browse(self, relative_path: str, branch: str = "main") -> str:
        """A URL that opens this file on the provider's website.

        Empty when we cannot tell -- a dead link is worse than no link.
        """
        if not self.web_url:
            return ""
        # Slashes stay slashes in both. A branch called claude/spec-review is
        # ordinary, and percent-encoding its slash gives a URL that 404s on
        # GitHub -- the provider resolves the ambiguity itself.
        safe = quote(relative_path.strip("/"), safe="/")
        branch = quote(branch, safe="/")
        if self.provider == "github":
            return f"{self.web_url}/blob/{branch}/{safe}"
        if self.provider == "gitlab":
            # GitLab needs the /-/ separator; without it a branch containing a
            # slash is ambiguous with the path.
            return f"{self.web_url}/-/blob/{branch}/{safe}"
        return ""

    def commit_url(self, sha: str) -> str:
        if not self.web_url or not sha:
            return ""
        if self.provider == "github":
            return f"{self.web_url}/commit/{sha}"
        if self.provider == "gitlab":
            return f"{self.web_url}/-/commit/{sha}"
        return ""

    @property
    def token_help(self) -> str:
        """Where the user goes to get a token for this host."""
        if self.provider == "github":
            return f"https://{self.host}/settings/tokens"
        if self.provider == "gitlab":
            return f"https://{self.host}/-/user_settings/personal_access_tokens"
        return ""


def _provider_for(host: str) -> str:
    host = host.lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if host == "gitlab.com" or host.endswith(".gitlab.com"):
        return "gitlab"
    # A self-hosted instance is the common case in a company big enough to run
    # CATIA, and its hostname says nothing. Guess from the name, and be honest
    # that it is a guess by leaving anything else unknown.
    if "gitlab" in host:
        return "gitlab"
    if "github" in host:
        return "github"
    return "unknown"


def parse_remote(url: str) -> Remote:
    """Pull the host and path out of any of git's remote spellings."""
    url = (url or "").strip()
    if not url:
        return Remote(url="")

    match = URL_FORM.match(url) or SSH_FORM.match(url)
    if not match:
        return Remote(url=url)

    host = match.group("host") or ""
    path = (match.group("path") or "").strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return Remote(url=url, host=host, path=path, provider=_provider_for(host))
