"""Keeping flows and their evidence in a repository.

Understudy's whole value is that a run is repeatable and comparable, and both
of those want history: which prompt did we send in March, what did LEO answer
then, has the click path drifted since. A folder on one laptop provides none of
that, and a folder on a shared drive provides the worst version of it.

So the workspace can be a git checkout, and the tool learns just enough git to
be useful from the UI: what has changed, commit it, push it, pull. That is
deliberately not an integration with GitHub or GitLab -- it is git, which both
speak, along with self-hosted GitLab and anything else. The provider only comes
into it for the parts that genuinely differ: where a personal access token
comes from, and what URL opens this file in a browser.
"""

from understudy.vcs.git import Git, GitError, GitStatus
from understudy.vcs.remote import Remote, parse_remote

__all__ = ["Git", "GitError", "GitStatus", "Remote", "parse_remote"]
