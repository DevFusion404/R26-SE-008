"""
unit/test_git_push_auth.py
--------------------------
"Push to GitHub" on a hosted backend.

A container has no credential helper, no terminal and no GitHub Desktop, so
git failed with "could not read Username ... No such device or address" and
the response blamed GitHub Desktop. These tests pin the replacement: a token
passed per command (never stored), a fail-fast prompt setting, and GitHub
Desktop skipped where there is no desktop.
"""

import subprocess

import pytest

from services import git_service
from services.git_service import GitAuth, resolve_git_auth


GITHUB_URL = "https://github.com/octo-org/demo.git"


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    monkeypatch.delenv("DIWO_GIT_PUSH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(git_service, "_app_installation_token", lambda owner, repo: "")


# ── Credential resolution ────────────────────────────────────────────────────

class TestResolveGitAuth:
    def test_no_source_means_no_auth(self):
        assert resolve_git_auth(GITHUB_URL) is None

    def test_request_token_wins(self, monkeypatch):
        monkeypatch.setenv("DIWO_GIT_PUSH_TOKEN", "server-token")
        auth = resolve_git_auth(GITHUB_URL, "typed-token")
        assert (auth.token, auth.source) == ("typed-token", "request")

    def test_server_token_before_github_app(self, monkeypatch):
        monkeypatch.setenv("DIWO_GIT_PUSH_TOKEN", "server-token")
        monkeypatch.setattr(git_service, "_app_installation_token", lambda o, r: "app-token")
        assert resolve_git_auth(GITHUB_URL).source == "server"

    def test_github_app_token_for_the_repository(self, monkeypatch):
        seen = {}

        def fake(owner, repo):
            seen["repo"] = (owner, repo)
            return "app-token"

        monkeypatch.setattr(git_service, "_app_installation_token", fake)
        auth = resolve_git_auth(GITHUB_URL)
        assert auth.source == "github_app"
        assert seen["repo"] == ("octo-org", "demo")

    @pytest.mark.parametrize("url", [
        "git@github.com:octo-org/demo.git",     # ssh keeps its own keys
        "C:/repos/demo",
        "file:///tmp/demo.git",
        "",
    ])
    def test_only_https_urls_take_a_token(self, url):
        assert resolve_git_auth(url, "typed-token") is None


# ── The token never leaks ────────────────────────────────────────────────────

class TestGitAuth:
    def test_token_travels_as_a_header_not_in_plain_text(self):
        args = GitAuth("s3cret-token", "request").config_args()
        assert args[0] == "-c"
        assert args[1].startswith("http.https://github.com/.extraheader=AUTHORIZATION: basic ")
        assert "s3cret-token" not in " ".join(args)

    def test_redacts_token_and_header(self):
        auth = GitAuth("s3cret-token", "request")
        header = auth.config_args()[1].rsplit(" ", 1)[-1]
        text = f"fatal: s3cret-token rejected ({header})"
        assert "s3cret-token" not in auth.redact(text)
        assert header not in auth.redact(text)

    def test_repr_hides_token(self):
        assert "s3cret" not in repr(GitAuth("s3cret-token", "server"))


class TestRunGit:
    def test_prompts_disabled_and_auth_passed(self, monkeypatch):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 128, b"", b"bad s3cret-token")

        monkeypatch.setattr(git_service.subprocess, "run", fake_run)
        ok, _, err = git_service.run_git(["push"], auth=GitAuth("s3cret-token", "request"))

        assert not ok
        assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert captured["command"][:2] == ["git", "-c"]
        assert "s3cret-token" not in err


@pytest.mark.parametrize("err, expected", [
    ("fatal: could not read Username for 'https://github.com': No such device or address", True),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", True),
    ("remote: Permission to octo-org/demo.git denied to bot.", True),
    ("fatal: Authentication failed for 'https://github.com/octo-org/demo.git/'", True),
    ("! [rejected] main -> main (non-fast-forward)", False),
])
def test_credential_errors_are_recognised(err, expected):
    assert git_service._is_credential_error(err) is expected


# ── End to end against a local remote ────────────────────────────────────────

class TestApplyAndPush:
    @pytest.fixture
    def repo(self, tmp_path):
        """A working copy whose origin is a local bare repository."""
        def git(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)

        remote = tmp_path / "remote.git"
        remote.mkdir()
        git("init", "-q", "--bare", cwd=remote)

        work = tmp_path / "work"
        work.mkdir()
        git("init", "-q", cwd=work)
        git("config", "user.email", "t@t", cwd=work)
        git("config", "user.name", "T", cwd=work)
        (work / "Order.java").write_text("class Order {}", encoding="utf-8")
        git("add", "-A", cwd=work)
        git("commit", "-qm", "initial", cwd=work)
        git("remote", "add", "origin", remote.as_uri(), cwd=work)
        return work, remote

    def _push(self, work, content, **extra):
        return git_service.apply_and_push({
            "files": [{"path": "Order.java", "after": content}],
            "branch_name": "refactoring/diwo-changes",
            "repository_path": str(work),
            **extra,
        })

    def _remote_branches(self, remote):
        return subprocess.run(["git", "branch", "--list"], cwd=remote,
                              capture_output=True, text=True).stdout

    def test_pushes_and_skips_desktop_on_a_host_without_one(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        launched = []
        monkeypatch.setattr(git_service, "open_github_desktop",
                            lambda path: launched.append(path) or (True, "x"))
        work, remote = repo

        result = self._push(work, "class Order { int id; }")

        assert result["pushed"], result["push_error"]
        assert result["push_error"] == ""
        assert result["github_desktop_detail"] == "skipped"
        assert launched == []
        refs = subprocess.run(["git", "branch", "--list"], cwd=remote,
                              capture_output=True, text=True).stdout
        assert "refactoring/diwo-changes" in refs

    def test_a_typed_name_that_exists_remotely_is_refused(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        work, remote = repo
        assert self._push(work, "class Order { int a; }")["pushed"]
        before = subprocess.run(["git", "rev-parse", "refactoring/diwo-changes"], cwd=remote,
                                capture_output=True, text=True).stdout

        with pytest.raises(git_service.GitOperationError) as caught:
            self._push(work, "class Order { int b; }")

        assert caught.value.status == 409
        assert "already exists" in caught.value.message
        after = subprocess.run(["git", "rev-parse", "refactoring/diwo-changes"], cwd=remote,
                               capture_output=True, text=True).stdout
        assert after == before            # the remote branch was not touched

    def test_generated_name_moves_to_the_next_free_one(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        work, remote = repo

        first = self._push(work, "class Order { int a; }", branch_name="", run_id="wf_ab12")
        assert first["branch"] == "refactoring/diwo-wf-ab12"
        assert first["pushed"], first["push_error"]

        subprocess.run(["git", "checkout", "-q", "master"], cwd=work, capture_output=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=work, capture_output=True)
        second = self._push(work, "class Order { int b; }",
                            branch_name="refactoring/diwo-wf-ab12", branch_auto=True, run_id="wf_ab12")
        assert second["branch"] == "refactoring/diwo-wf-ab12-2"
        assert second["branch_renamed_from"] == "refactoring/diwo-wf-ab12"
        assert second["pushed"], second["push_error"]
        assert "refactoring/diwo-wf-ab12-2" in self._remote_branches(remote)

    def test_identical_project_still_pushes_its_own_branch(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        work, remote = repo

        result = self._push(work, "class Order {}", branch_name="refactoring/same")

        assert result["committed"] and result["pushed"], result["push_error"]
        assert result["staged_files"] == []
        assert "identical" in result["message"]
        assert "refactoring/same" in self._remote_branches(remote)
        # An empty commit: the branch records the run without changing a file.
        changed = subprocess.run(["git", "show", "--name-only", "--format=", "refactoring/same"],
                                 cwd=remote, capture_output=True, text=True).stdout.strip()
        assert changed == ""

    def test_push_lands_on_the_new_branch_and_never_on_main(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        work, remote = repo

        def git(*args, cwd=work):
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()

        # Publish the starting point as the remote's main.
        git("push", "-q", "origin", "HEAD:refs/heads/main")
        main_before = git("rev-parse", "refs/heads/main", cwd=remote)
        # A stale upstream of main, as clones made before the fix carry.
        git("branch", "refactoring/old")
        git("config", "branch.refactoring/old.remote", "origin")
        git("config", "branch.refactoring/old.merge", "refs/heads/main")

        result = self._push(work, "class Order { int id; }", branch_name="refactoring/old")

        assert result["pushed"], result["push_error"]
        assert result["pushed_ref"] == "refs/heads/refactoring/old"
        assert git("rev-parse", "refs/heads/main", cwd=remote) == main_before
        assert git("rev-parse", "refs/heads/refactoring/old", cwd=remote) == git("rev-parse", "HEAD")
        # The branch now follows itself on the remote, so a later push from
        # GitHub Desktop or `git push` cannot land on main either.
        assert git("config", "branch.refactoring/old.merge") == "refs/heads/refactoring/old"

    @pytest.mark.parametrize("name", ["main", "master"])
    def test_base_branch_names_are_refused(self, repo, name):
        work, _ = repo
        with pytest.raises(git_service.GitOperationError) as caught:
            self._push(work, "x", branch_name=name)
        assert caught.value.status == 400
        assert "never to the base branch" in caught.value.message

    def test_invalid_name_is_refused_before_anything_is_written(self, repo):
        work, _ = repo
        with pytest.raises(git_service.GitOperationError) as caught:
            self._push(work, "x", branch_name="bad name//here.")
        assert caught.value.status == 400
        assert (work / "Order.java").read_text(encoding="utf-8") == "class Order {}"

    def test_check_branch_reports_existence_and_a_free_suggestion(self, repo, monkeypatch):
        monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")
        work, _ = repo
        self._push(work, "class Order { int a; }", branch_name="", run_id="wf_ab12")

        taken = git_service.check_branch({"run_id": "wf_ab12", "repository_path": str(work),
                                          "branch_name": "refactoring/diwo-wf-ab12"})
        assert taken["valid"] and taken["exists"] is True
        assert taken["suggestion"] == "refactoring/diwo-wf-ab12-2"

        fresh = git_service.check_branch({"run_id": "wf_new", "repository_path": str(work)})
        assert fresh["branch_name"] == "refactoring/diwo-wf-new"
        assert fresh["valid"] and fresh["exists"] is False
