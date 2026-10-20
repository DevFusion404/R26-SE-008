"""
unit/test_git_user_token.py
---------------------------
A developer's own GitHub token on "Push to GitHub" (hosted environment).

The token replaces the server's DIWO_GIT_PUSH_TOKEN for one request, is only
ever sent to an https GitHub URL, and is never persisted — not in the clone's
.git/config, a commit, or the response.
"""

import json
import subprocess

import pytest

from services import git_service
from services.git_service import GitAuth, resolve_git_auth

TOKEN = "github_pat_TESTSECRET123"


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    monkeypatch.delenv("DIWO_GIT_PUSH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(git_service, "_app_installation_token", lambda owner, repo: "")
    monkeypatch.setenv("DIWO_OPEN_GITHUB_DESKTOP", "false")


def test_user_token_replaces_the_server_token(monkeypatch):
    monkeypatch.setenv("DIWO_GIT_PUSH_TOKEN", "server-token")
    auth = resolve_git_auth("https://github.com/octo/demo.git", TOKEN)
    assert auth.token == TOKEN and auth.source == "request"


@pytest.mark.parametrize("url", [
    "http://github.com/octo/demo.git",       # not HTTPS
    "git@github.com:octo/demo.git",
    "ssh://git@github.com/octo/demo.git",
])
def test_token_is_refused_for_non_https_urls(url):
    with pytest.raises(git_service.GitOperationError) as caught:
        git_service.apply_and_push({
            "files": [{"path": "a.txt", "after": "x"}],
            "branch_name": "refactoring/x",
            "repository_path": url,
            "github_token": TOKEN,
        })
    assert caught.value.status == 400
    assert "https://github.com" in caught.value.message
    assert TOKEN not in caught.value.message


def test_token_is_never_written_to_the_clone(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    ok, _, _ = git_service.run_git(["status"], cwd=repo, auth=GitAuth(TOKEN, "request"))

    assert ok
    config = (repo / ".git" / "config").read_text(encoding="utf-8")
    assert TOKEN not in config
    assert "extraheader" not in config


def test_token_is_not_in_the_response_or_commit(tmp_path, monkeypatch):
    """A local push with a token supplied: the token appears nowhere it could leak."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "-q", "--bare"], cwd=remote, check=True)
    work = tmp_path / "work"
    work.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "T")):
        subprocess.run(["git", *args], cwd=work, check=True)
    (work / "A.java").write_text("class A {}", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote.as_uri()], cwd=work, check=True)

    result = git_service.apply_and_push({
        "files": [{"path": "A.java", "after": "class A { int x; }"}],
        "branch_name": "refactoring/token-check",
        "repository_path": str(work),
        "github_token": TOKEN,
    })

    assert result["pushed"], result["push_error"]
    assert TOKEN not in json.dumps(result)
    log = subprocess.run(["git", "log", "-p", "--all"], cwd=work, capture_output=True, text=True).stdout
    assert TOKEN not in log
    assert TOKEN not in (work / ".git" / "config").read_text(encoding="utf-8")
