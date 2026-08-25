"""Keeping flows and their evidence in a repository.

The git wrapper is exercised against real repositories in tmp directories --
git is the thing being relied on, so mocking it would test the mock. What is
covered here is the parsing (which is where the bugs are), the safety rules
(which is where the damage is), and the publish selection (which is where the
judgement is).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from understudy.vcs import Git, GitError, parse_remote
from understudy.vcs.git import scrub
from understudy.vcs.publish import commit_message, select, video_note


def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


needs_git = pytest.mark.skipif(not git_available(), reason="needs the git binary")


@pytest.fixture
def repo(tmp_path):
    """A real repository, with one commit, and a bare remote to push to."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   capture_output=True, check=True)

    work = tmp_path / "work"
    work.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["remote", "add", "origin", str(remote)],
    ):
        subprocess.run(["git", "-C", str(work), *args], capture_output=True, check=True)
    (work / "flows").mkdir()
    (work / "flows" / "one.yaml").write_text("version: 1\n")
    subprocess.run(["git", "-C", str(work), "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "first"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "main"],
                   capture_output=True, check=True)
    return work


# -- reading a remote ---------------------------------------------------------


class TestRemotes:
    def test_the_ssh_form(self):
        remote = parse_remote("git@github.com:hoppalabs/understudy.git")
        assert (remote.host, remote.path, remote.provider) == (
            "github.com", "hoppalabs/understudy", "github")

    def test_the_https_form(self):
        remote = parse_remote("https://github.com/hoppalabs/understudy.git")
        assert remote.path == "hoppalabs/understudy"

    def test_a_gitlab_group_can_be_nested(self):
        """GitLab allows group/subgroup/project; GitHub does not."""
        remote = parse_remote("https://gitlab.com/cad/tools/understudy.git")
        assert remote.path == "cad/tools/understudy"
        assert remote.provider == "gitlab"

    def test_a_self_hosted_gitlab_is_recognised_by_name(self):
        remote = parse_remote("https://gitlab.bigco.example/cad/understudy.git")
        assert remote.provider == "gitlab"
        assert remote.web_url == "https://gitlab.bigco.example/cad/understudy"

    def test_an_unrecognisable_host_says_so_rather_than_guessing(self):
        """A wrong link is worse than none."""
        remote = parse_remote("ssh://git@git.internal:2222/cad/understudy.git")
        assert remote.provider == "unknown"
        assert remote.browse("a.yaml") == ""
        assert remote.commit_url("abc123") == ""

    def test_the_two_providers_spell_a_file_link_differently(self):
        github = parse_remote("https://github.com/a/b.git")
        gitlab = parse_remote("https://gitlab.com/a/b.git")
        assert github.browse("flows/x.yaml", "main") == \
            "https://github.com/a/b/blob/main/flows/x.yaml"
        assert gitlab.browse("flows/x.yaml", "main") == \
            "https://gitlab.com/a/b/-/blob/main/flows/x.yaml"

    def test_a_branch_with_a_slash_keeps_its_slash(self):
        """Percent-encoding it gives a URL that 404s; the provider resolves the
        ambiguity itself."""
        remote = parse_remote("https://github.com/a/b.git")
        assert remote.browse("x.yaml", "feat/thing").endswith("/blob/feat/thing/x.yaml")

    def test_a_space_in_a_path_is_encoded(self):
        remote = parse_remote("https://github.com/a/b.git")
        assert "%20" in remote.browse("my flows/x.yaml", "main")

    def test_no_remote_at_all(self):
        remote = parse_remote("")
        assert remote.provider == "unknown" and remote.browse("x") == ""


# -- not leaking credentials --------------------------------------------------


class TestSecrets:
    def test_a_token_in_a_url_is_scrubbed(self):
        text = "fatal: could not read https://user:glpat-abc123@gitlab.com/a/b.git"
        assert "glpat-abc123" not in scrub(text)
        assert "***@" in scrub(text)

    def test_a_bare_github_token_is_scrubbed(self):
        assert "ghp_" not in scrub("remote: bad credentials ghp_" + "a" * 36)

    def test_a_bare_gitlab_token_is_scrubbed(self):
        assert "glpat-" not in scrub("token glpat-" + "b" * 20 + " rejected")

    def test_an_authorization_header_is_scrubbed(self):
        assert "c2VjcmV0" not in scrub("Authorization: Basic c2VjcmV0dmFsdWVoZXJl")

    @needs_git
    def test_a_token_is_never_written_into_the_checkout(self, repo):
        """It goes in as a per-command header, not into .git/config and not
        into the remote URL, either of which would leave it on disk."""
        git = Git(repo)
        git.run("status", token="ghp_" + "s" * 36, host="github.com")

        config = (repo / ".git" / "config").read_text()
        assert "ghp_" not in config
        assert "extraheader" not in config

    @needs_git
    def test_a_failure_against_a_tokened_remote_does_not_echo_the_token(self, tmp_path):
        work = tmp_path / "w"
        work.mkdir()
        subprocess.run(["git", "-C", str(work), "init", "-b", "main"],
                       capture_output=True, check=True)
        git = Git(work)
        token = "ghp_" + "z" * 36
        with pytest.raises(GitError) as caught:
            git.run("push", f"https://user:{token}@github.invalid/a/b.git",
                    token=token, host="github.invalid", timeout_s=30)
        assert token not in str(caught.value)


# -- status -------------------------------------------------------------------


class TestStatus:
    def test_a_folder_that_is_not_a_checkout_says_so(self, tmp_path):
        status = Git(tmp_path).status()
        assert not status.is_repo
        assert "not a git checkout" in status.note

    @needs_git
    def test_a_clean_checkout(self, repo):
        status = Git(repo).status()
        assert status.is_repo and status.branch == "main"
        assert not status.dirty and status.can_push
        assert status.ahead == 0 and status.behind == 0

    @needs_git
    def test_changes_are_classified(self, repo):
        (repo / "flows" / "one.yaml").write_text("version: 2\n")
        (repo / "flows" / "two.yaml").write_text("version: 1\n")
        states = {c.path: c.state for c in Git(repo).status().changes}
        assert states["flows/one.yaml"] == "modified"
        assert states["flows/two.yaml"] == "untracked"

    @needs_git
    def test_a_deletion_is_a_change_too(self, repo):
        (repo / "flows" / "one.yaml").unlink()
        states = {c.path: c.state for c in Git(repo).status().changes}
        assert states["flows/one.yaml"] == "deleted"

    @needs_git
    def test_being_ahead_of_the_remote_is_counted(self, repo):
        (repo / "flows" / "two.yaml").write_text("version: 1\n")
        git = Git(repo)
        git.commit(["flows/two.yaml"], "add two")
        assert git.status().ahead == 1


# -- committing ---------------------------------------------------------------


class TestCommitting:
    @needs_git
    def test_only_the_named_paths_are_staged(self, repo):
        """A tool that runs `git add -A` in somebody's working repository will
        one day commit something they were halfway through."""
        (repo / "flows" / "wanted.yaml").write_text("a\n")
        (repo / "flows" / "half-finished.yaml").write_text("b\n")

        git = Git(repo)
        git.commit(["flows/wanted.yaml"], "add the wanted one")

        committed = git.run("show", "--name-only", "--format=", "HEAD").split()
        assert committed == ["flows/wanted.yaml"]
        assert any(c.path == "flows/half-finished.yaml"
                   for c in git.status().changes)

    @needs_git
    def test_committing_nothing_is_not_an_error(self, repo):
        assert Git(repo).commit([], "nothing") is None

    @needs_git
    def test_committing_an_unchanged_file_is_not_an_error(self, repo):
        assert Git(repo).commit(["flows/one.yaml"], "no change") is None

    @needs_git
    def test_a_commit_needs_a_message(self, repo):
        (repo / "flows" / "x.yaml").write_text("a\n")
        with pytest.raises(GitError, match="needs a message"):
            Git(repo).commit(["flows/x.yaml"], "   ")

    @needs_git
    def test_a_commit_returns_its_sha(self, repo):
        (repo / "flows" / "x.yaml").write_text("a\n")
        sha = Git(repo).commit(["flows/x.yaml"], "add x")
        assert sha and len(sha) == 40

    @needs_git
    def test_push_and_pull_round_trip(self, repo, tmp_path):
        (repo / "flows" / "x.yaml").write_text("a\n")
        git = Git(repo)
        git.commit(["flows/x.yaml"], "add x")
        git.push()

        other = Git.clone(str(tmp_path / "remote.git"), tmp_path / "second")
        assert (other.root / "flows" / "x.yaml").exists()

    @needs_git
    def test_pull_refuses_to_invent_a_merge_commit(self, repo, tmp_path):
        """Diverged histories are a decision for a person, not for a tool
        running in the background of somebody's afternoon."""
        other = Git.clone(str(tmp_path / "remote.git"), tmp_path / "second")
        for args in (["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
            other.run(*args)
        (other.root / "theirs.yaml").write_text("theirs\n")
        other.commit(["theirs.yaml"], "theirs")
        other.push()

        (repo / "mine.yaml").write_text("mine\n")
        git = Git(repo)
        git.commit(["mine.yaml"], "mine")
        git.run("fetch")
        with pytest.raises(GitError):
            git.pull()

    @needs_git
    def test_cloning_into_a_non_empty_directory_is_refused(self, repo, tmp_path):
        busy = tmp_path / "busy"
        busy.mkdir()
        (busy / "something.txt").write_text("here already\n")
        with pytest.raises(GitError, match="not empty"):
            Git.clone(str(tmp_path / "remote.git"), busy)


# -- what gets published ------------------------------------------------------


class TestPublishSelection:
    @pytest.fixture
    def run(self, tmp_path):
        run = tmp_path / "runs" / "2026-09-01"
        (run / "baseline").mkdir(parents=True)
        (run / "transcript.md").write_text("# transcript\n")
        (run / "transcript.html").write_text("<p>x</p>")
        (run / "transcript.pdf").write_bytes(b"%PDF-1.4\n")
        (run / "results.jsonl").write_text("{}\n")
        (run / "baseline" / "01-start.png").write_bytes(b"\x89PNG\r\n")
        (run / "baseline" / "recording.mp4").write_bytes(b"\x00" * 2048)
        return run, tmp_path

    def test_the_transcript_and_screenshots_are_included(self, run):
        chosen = select(*run)
        assert "runs/2026-09-01/transcript.md" in chosen.include
        assert "runs/2026-09-01/transcript.html" in chosen.include
        assert "runs/2026-09-01/transcript.pdf" in chosen.include
        assert "runs/2026-09-01/results.jsonl" in chosen.include
        assert "runs/2026-09-01/baseline/01-start.png" in chosen.include

    def test_video_is_left_out_by_default(self, run):
        """A year of daily runs would put gigabytes of mp4 into a history that
        cannot be trimmed without rewriting it."""
        chosen = select(*run)
        assert not any(p.endswith(".mp4") for p in chosen.include)
        assert chosen.excluded["video"] == ("runs/2026-09-01/baseline/recording.mp4",)

    def test_video_can_be_asked_for(self, run):
        chosen = select(*run, include_video=True)
        assert "runs/2026-09-01/baseline/recording.mp4" in chosen.include

    def test_what_was_left_out_is_reported_not_dropped_quietly(self, run):
        chosen = select(*run)
        assert "video" in chosen.excluded
        assert "1 skipped (video)" in chosen.summary()

    def test_an_enormous_file_is_skipped_whatever_its_type(self, run):
        run_dir, root = run
        (run_dir / "huge.png").write_bytes(b"\x00" * 4096)
        chosen = select(run_dir, root, max_file_mb=0.001)
        assert "runs/2026-09-01/huge.png" in chosen.excluded["larger than 0.001MB"]

    def test_a_credentials_file_is_never_published(self, run):
        run_dir, root = run
        (run_dir / "credentials.json").write_text('{"api_key": "sk-ant-secret"}')
        chosen = select(run_dir, root)
        assert not any("credentials" in p for p in chosen.include)
        assert chosen.excluded["never committed"]

    def test_a_run_outside_the_workspace_is_refused(self, run, tmp_path):
        with pytest.raises(ValueError, match="not inside"):
            select(run[0], tmp_path / "somewhere-else")

    def test_the_note_explains_where_the_video_went(self, run):
        note = video_note(select(*run), "2026-09-01")
        assert "recording.mp4" in note and "Git LFS" in note

    def test_there_is_no_note_when_nothing_was_left_out(self, run):
        assert video_note(select(*run, include_video=True), "x") == ""

    def test_the_commit_subject_says_what_happened(self, run):
        message = commit_message(run[0], [
            {"flow": "leo-regression", "status": "ok"},
            {"flow": "leo-regression", "status": "error"},
        ])
        assert message == "Run leo-regression: 2 variant(s), 1/2 ok"


class TestPublishingARun:
    """End to end, against a real repository and a real bare remote."""

    @pytest.fixture
    def workspace(self, repo):
        run = repo / "runs" / "2026-09-01T14-22"
        (run / "baseline").mkdir(parents=True)
        (run / "transcript.md").write_text("# transcript\n")
        (run / "results.jsonl").write_text(
            '{"flow": "leo", "prompt_id": "baseline", "status": "ok"}\n'
        )
        (run / "baseline" / "01-start.png").write_bytes(b"\x89PNG\r\n")
        (run / "baseline" / "recording.mp4").write_bytes(b"\x00" * 4096)
        return repo

    def backend(self, workspace, tmp_path):
        from understudy.vcs.backend import Repository

        # Point the credential store at a tmp file: a test must never read or
        # write the developer's real one.
        return Repository(workspace, credentials_path=tmp_path / "creds.json")

    @needs_git
    def test_the_state_describes_the_checkout(self, workspace, tmp_path):
        state = self.backend(workspace, tmp_path).state()
        assert state["is_repo"] and state["branch"] == "main"
        assert state["dirty"] and not state["has_token"]

    @needs_git
    def test_a_preview_lists_what_would_be_committed(self, workspace, tmp_path):
        preview = self.backend(workspace, tmp_path).preview_publish(
            "runs/2026-09-01T14-22")
        assert "runs/2026-09-01T14-22/transcript.md" in preview["include"]
        assert preview["excluded"]["video"]
        assert preview["message"] == "Run leo: 1 variant(s), all ok"

    @needs_git
    def test_a_preview_changes_nothing(self, workspace, tmp_path):
        backend = self.backend(workspace, tmp_path)
        before = backend.git.log(limit=1)
        backend.preview_publish("runs/2026-09-01T14-22")
        assert backend.git.log(limit=1) == before

    @needs_git
    def test_publishing_commits_the_evidence_and_not_the_video(self, workspace, tmp_path):
        backend = self.backend(workspace, tmp_path)
        outcome = backend.publish("runs/2026-09-01T14-22")

        assert outcome["committed"] and outcome["pushed"]
        committed = backend.git.run("show", "--name-only", "--format=", "HEAD").split()
        assert "runs/2026-09-01T14-22/transcript.md" in committed
        assert "runs/2026-09-01T14-22/baseline/01-start.png" in committed
        assert not any(p.endswith(".mp4") for p in committed)

    @needs_git
    def test_the_missing_video_is_explained_in_the_commit(self, workspace, tmp_path):
        """A transcript linking a recording that is not there looks like a
        broken link, rather than a decision somebody made."""
        backend = self.backend(workspace, tmp_path)
        backend.publish("runs/2026-09-01T14-22")

        note = workspace / "runs/2026-09-01T14-22/recordings-not-committed.txt"
        assert note.exists() and "recording.mp4" in note.read_text()
        committed = backend.git.run("show", "--name-only", "--format=", "HEAD")
        assert "recordings-not-committed.txt" in committed

    @needs_git
    def test_publishing_with_video_says_nothing_about_missing_video(self, workspace, tmp_path):
        backend = self.backend(workspace, tmp_path)
        backend.publish("runs/2026-09-01T14-22", include_video=True)
        committed = backend.git.run("show", "--name-only", "--format=", "HEAD")
        assert "recording.mp4" in committed
        assert "recordings-not-committed" not in committed

    @needs_git
    def test_a_failed_push_keeps_the_commit(self, workspace, tmp_path):
        """The work is done and worth keeping; only the network failed."""
        backend = self.backend(workspace, tmp_path)
        backend.git.run("remote", "set-url", "origin",
                        "https://understudy.invalid/nope.git")
        outcome = backend.publish("runs/2026-09-01T14-22")

        assert outcome["committed"] is True
        assert outcome["pushed"] is False and outcome["push_error"]
        assert backend.git.log(limit=1)[0]["subject"].startswith("Run leo")

    @needs_git
    def test_publishing_an_empty_run_is_refused_clearly(self, workspace, tmp_path):
        (workspace / "runs" / "empty").mkdir(parents=True)
        outcome = self.backend(workspace, tmp_path).publish("runs/empty")
        assert outcome == {"committed": False,
                           "reason": "nothing in that run to publish"}

    @needs_git
    def test_the_commit_carries_a_link_when_the_provider_is_known(self, workspace, tmp_path):
        backend = self.backend(workspace, tmp_path)
        backend.git.run("remote", "set-url", "origin",
                        "https://github.com/hoppalabs/flows.git")
        outcome = backend.publish("runs/2026-09-01T14-22", push=False)
        assert outcome["url"].startswith("https://github.com/hoppalabs/flows/commit/")

    @needs_git
    def test_a_token_is_looked_up_by_host_and_never_returned(self, workspace, tmp_path):
        from understudy import credentials

        creds = tmp_path / "creds.json"
        credentials.save_git_token("github.com", "ghp_" + "t" * 36, creds)
        backend = self.backend(workspace, tmp_path)
        backend.git.run("remote", "set-url", "origin",
                        "https://github.com/hoppalabs/flows.git")

        state = backend.state()
        assert state["has_token"] is True
        assert "ghp_" not in str(state)

    @needs_git
    def test_a_token_for_another_host_is_not_used(self, workspace, tmp_path):
        from understudy import credentials

        creds = tmp_path / "creds.json"
        credentials.save_git_token("github.com", "ghp_" + "t" * 36, creds)
        backend = self.backend(workspace, tmp_path)
        backend.git.run("remote", "set-url", "origin",
                        "https://gitlab.bigco.example/cad/flows.git")
        assert backend.state()["has_token"] is False


class TestNotTheToolsOwnRepository:
    """The most likely `.` on the day somebody first runs this is the checkout
    they cloned to get the tool. Publishing there commits their CAD screenshots
    into a source repository and pushes them to whoever owns it.
    """

    def test_the_understudy_checkout_is_recognised(self):
        from understudy.vcs.workspace_guard import is_source_checkout

        repo_root = Path(__file__).resolve().parents[2]
        assert is_source_checkout(repo_root)

    def test_an_ordinary_folder_is_not(self, tmp_path):
        from understudy.vcs.workspace_guard import is_source_checkout

        (tmp_path / "flows").mkdir()
        assert not is_source_checkout(tmp_path)

    def test_a_folder_that_merely_has_a_pyproject_is_not(self, tmp_path):
        from understudy.vcs.workspace_guard import is_source_checkout

        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert not is_source_checkout(tmp_path)

    def test_writes_are_refused_there(self, tmp_path):
        from understudy.vcs.backend import Repository
        from understudy.vcs.workspace_guard import MARKER

        (tmp_path / "understudy").mkdir()
        (tmp_path / "understudy" / "cli.py").write_text("")
        (tmp_path / "understudy" / "runner.py").write_text("")
        (tmp_path / "pyproject.toml").write_text("")

        repository = Repository(tmp_path)
        assert repository.read_only
        with pytest.raises(GitError, match="checkout of Understudy itself"):
            repository.commit(["pyproject.toml"], "no")
        with pytest.raises(GitError, match="checkout of Understudy itself"):
            repository.publish("runs/x")
        with pytest.raises(GitError, match="checkout of Understudy itself"):
            repository.push()

        # and the refusal says how to get past it deliberately
        (tmp_path / MARKER).write_text("flows really do live here\n")
        assert not Repository(tmp_path).read_only

    def test_the_state_says_so_rather_than_looking_broken(self, tmp_path):
        from understudy.vcs.backend import Repository

        (tmp_path / "understudy").mkdir()
        (tmp_path / "understudy" / "cli.py").write_text("")
        (tmp_path / "understudy" / "runner.py").write_text("")
        (tmp_path / "pyproject.toml").write_text("")

        state = Repository(tmp_path).state()
        assert state["read_only"]
        assert "flow workspace" in state["read_only_reason"]


class TestAutomaticCommitMessages:
    """Leaving the box alone should still produce something worth reading in a
    log six months from now."""

    def message(self, paths):
        from understudy.vcs.publish import suggest_message

        return suggest_message(paths)

    def test_one_flow_is_named(self):
        assert self.message(["flows/catia/rename-and-ask.yaml"]) == \
            "Update rename-and-ask"

    def test_several_flows_are_counted(self):
        assert self.message(["flows/a.yaml", "flows/b.yaml"]) == "Update 2 flows"

    def test_a_run_is_a_publish(self):
        assert self.message([
            "runs/2026-09-01T14-22/transcript.md",
            "runs/2026-09-01T14-22/baseline/01.png",
        ]) == "Publish run 2026-09-01T14-22"

    def test_several_runs_are_counted(self):
        assert self.message(["runs/a/t.md", "runs/b/t.md"]) == "Publish 2 runs"

    def test_a_mixture_mentions_both(self):
        message = self.message(["flows/a.yaml", "runs/r1/t.md"])
        assert "Update a" in message and "publish run r1" in message

    def test_nothing_selected_is_empty_not_a_placeholder(self):
        assert self.message([]) == ""

    @needs_git
    def test_committing_without_a_message_writes_one(self, repo, tmp_path):
        from understudy.vcs.backend import Repository

        (repo / "flows" / "two.yaml").write_text("version: 1\n")
        repository = Repository(repo, credentials_path=tmp_path / "creds.json")
        repository.commit(["flows/two.yaml"], "")
        assert repository.git.log(limit=1)[0]["subject"] == "Update two"


#: An absolute path on whichever platform the tests are running. "/home/me"
#: is not absolute on Windows -- there is no drive -- so hard-coding a Unix
#: path makes every one of these fail there for a reason that has nothing to
#: do with what they are testing.
def absolute(*parts: str) -> str:
    from pathlib import Path as _Path

    root = _Path("C:/") if sys.platform == "win32" else _Path("/")
    return str(root.joinpath(*parts))


class TestChoosingTheSource:
    """A folder, a GitHub repository, or a GitLab one.

    Cloning is the same git command either way; the distinction exists because
    the three ask for different things. Somebody connecting to a company GitLab
    knows their project path and their instance's hostname, and will otherwise
    be quietly sent to gitlab.com.
    """

    def parse(self, **payload):
        from understudy.vcs.source import parse

        return parse(payload)

    def test_a_local_folder_needs_nothing_else(self):
        source = self.parse(kind="local", directory=absolute("home", "me", "flows"))
        assert source.clone_url == ""

    def test_github_builds_its_own_url(self):
        source = self.parse(kind="github", project="hoppalabs/flows",
                            directory=absolute("home", "me", "flows"))
        assert source.clone_url == "https://github.com/hoppalabs/flows.git"

    def test_gitlab_defaults_to_the_public_instance(self):
        source = self.parse(kind="gitlab", project="cad/flows",
                            directory=absolute("home", "me", "flows"))
        assert source.clone_url == "https://gitlab.com/cad/flows.git"

    def test_a_self_hosted_gitlab_is_honoured(self):
        source = self.parse(kind="gitlab", project="cad/tools/flows",
                            host="gitlab.bigco.example", directory=absolute("home", "me", "f"))
        assert source.clone_url == "https://gitlab.bigco.example/cad/tools/flows.git"

    def test_gitlab_groups_may_nest_and_github_repos_may_not(self):
        from understudy.vcs.source import SourceError

        assert self.parse(kind="gitlab", project="a/b/c", directory=absolute("x")).project
        with pytest.raises(SourceError, match="only GitLab nests"):
            self.parse(kind="github", project="a/b/c", directory=absolute("x"))

    def test_a_missing_repository_says_which_shape_it_wants(self):
        from understudy.vcs.source import SourceError

        with pytest.raises(SourceError, match="owner/repo"):
            self.parse(kind="github", directory=absolute("x"))
        with pytest.raises(SourceError, match="group/project"):
            self.parse(kind="gitlab", directory=absolute("x"))

    def test_a_pasted_url_is_refused_with_an_explanation(self):
        """It is the commonest thing to type into that box."""
        from understudy.vcs.source import SourceError

        with pytest.raises(SourceError, match="not the whole URL"):
            self.parse(kind="github", project="https://github.com/a/b",
                       directory=absolute("x"))

    def test_a_relative_folder_is_refused(self):
        from understudy.vcs.source import SourceError

        with pytest.raises(SourceError, match="absolute"):
            self.parse(kind="local", directory="flows")

    def test_a_url_can_be_taken_apart_to_fill_the_form(self):
        from understudy.vcs.source import from_url

        source = from_url("https://gitlab.bigco.example/cad/tools/flows.git")
        assert (source.kind, source.project, source.host) == (
            "gitlab", "cad/tools/flows", "gitlab.bigco.example")
        assert source.suggested_directory == "flows"

    def test_a_public_host_is_not_repeated_as_a_custom_one(self):
        from understudy.vcs.source import from_url

        assert from_url("https://github.com/a/b.git").host == ""


class TestRememberingWorkspaces:
    """A tool that forgets where your flows are every time it starts is one you
    have to configure before you can use it."""

    def test_the_most_recent_comes_back_first(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        recent.remember({"kind": "local", "directory": "/a"}, store)
        recent.remember({"kind": "github", "directory": "/b",
                         "project": "org/flows"}, store)
        assert recent.most_recent(store)["directory"] == "/b"
        assert [e["directory"] for e in recent.load(store)] == ["/b", "/a"]

    def test_reopening_one_moves_it_to_the_front_rather_than_duplicating(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        recent.remember({"kind": "local", "directory": "/a"}, store)
        recent.remember({"kind": "local", "directory": "/b"}, store)
        recent.remember({"kind": "local", "directory": "/a"}, store)
        assert [e["directory"] for e in recent.load(store)] == ["/a", "/b"]

    def test_the_list_does_not_grow_without_limit(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        for n in range(20):
            recent.remember({"kind": "local", "directory": f"/w{n}"}, store)
        assert len(recent.load(store)) == recent.MAX_REMEMBERED

    def test_one_can_be_forgotten(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        recent.remember({"kind": "local", "directory": "/a"}, store)
        recent.remember({"kind": "local", "directory": "/b"}, store)
        assert [e["directory"] for e in recent.forget("/a", store)] == ["/b"]

    def test_a_corrupt_file_is_ignored_rather_than_fatal(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        store.write_text("{not json")
        assert recent.load(store) == []

    def test_nothing_secret_is_stored(self, tmp_path):
        from understudy.vcs import recent

        store = tmp_path / "workspaces.json"
        recent.remember({"kind": "github", "directory": "/a",
                         "project": "org/flows", "host": ""}, store)
        text = store.read_text()
        assert "token" not in text and "ghp_" not in text
