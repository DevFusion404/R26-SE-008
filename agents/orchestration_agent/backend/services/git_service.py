"""
Git repository operations
=========================
R26-SE-008 | Bandara S M Y M | IT22277886

Clone / fetch / checkout / branch / stage / commit / push, plus writing the
refactored project into the working copy and, on a local backend, opening
GitHub Desktop on it.

Remote operations authenticate with a token (see resolve_git_auth), so a
hosted backend can clone private repositories and push without a credential
helper or GitHub Desktop.

Every git call goes through run_git(), which never raises — the caller reads
(ok, stdout, stderr) and decides what to report. Behaviour is exactly what it
was inside diwo/routes.apply_and_push; only the clone root and the timeout now
come from config.
"""

import base64
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import config
from config import CLONE_ROOT, GIT_TIMEOUT
from services.archive_service import safe_archive_path
from services.branch_naming import (
    generated_branch_name, next_free_branch_name, remote_branch_exists, validate_branch_name,
)

logger = logging.getLogger(__name__)


class GitOperationError(RuntimeError):
    """A git step failed in a way the caller should report verbatim.

    Carries the HTTP status the route used to return directly, so splitting
    the handler into a service did not change a single response code.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class GitAuth:
    """A token git sends for ONE command, as an HTTP Authorization header.

    Passed with `-c http.<origin>/.extraheader=...`, so it is never written to
    .git/config or embedded in the remote URL — a later `git remote -v` or a
    copied clone does not carry it. `source` says where it came from, for the
    response; the token itself is never returned or logged.
    """

    def __init__(self, token: str, source: str, host: str = "github.com"):
        self.token = token
        self.source = source
        self.host = host

    def config_args(self) -> list:
        basic = base64.b64encode(f"x-access-token:{self.token}".encode("utf-8")).decode("ascii")
        return ["-c", f"http.https://{self.host}/.extraheader=AUTHORIZATION: basic {basic}"]

    def redact(self, text: str) -> str:
        if not text:
            return text
        basic = base64.b64encode(f"x-access-token:{self.token}".encode("utf-8")).decode("ascii")
        return text.replace(self.token, "***").replace(basic, "***")

    def __repr__(self):
        return f"<GitAuth source={self.source} host={self.host}>"


def run_git(args, cwd=None, timeout=GIT_TIMEOUT, auth=None):
    """Run one git command. Returns (ok, stdout, stderr) and never raises.

    GIT_TERMINAL_PROMPT=0 makes a missing credential fail at once with a clear
    error. Without it a hosted backend has no terminal to prompt on, and git
    answers "could not read Username ... No such device or address".
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    command = ["git", *(auth.config_args() if auth else []), *args]
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        if auth:
            out, err = auth.redact(out), auth.redact(err)
        return proc.returncode == 0, out, err
    except FileNotFoundError:
        return False, "", "git is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return False, "", f"git {' '.join(args)} timed out after {timeout}s."


# ── Credentials ──────────────────────────────────────────────────────────────

_GITHUB_PATH_RE = re.compile(r"^/?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def github_repo_from_url(url: str):
    """(host, owner, repo) for an https GitHub URL, else None."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    match = _GITHUB_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    return parsed.hostname, match.group(1), match.group(2)


def _app_installation_token(owner: str, repo: str) -> str:
    """An installation token from the RefactorIQ GitHub App, when it is
    configured and installed on owner/repo. "" on any failure — this is the
    second of three credential sources, not a requirement."""
    if not config.github_integration_enabled() or config.github_config_problems():
        return ""
    try:
        from services.github_app_service import GitHubAppService
        service = GitHubAppService.from_config()
        installation_id = service.find_repo_installation(owner, repo)
        return service.get_installation_token(installation_id) if installation_id else ""
    except Exception as exc:  # noqa: BLE001 - fall through to the next source
        logger.info("GitHub App token unavailable for %s/%s: %s", owner, repo, type(exc).__name__)
        return ""


def require_https_for_token(repository: str, request_token: str) -> None:
    """A developer's token is only ever sent to an https:// GitHub URL.

    Refused outright rather than silently ignored: a token typed for an
    http://, ssh or file URL would otherwise be dropped and the push would fail
    later with a misleading "no credentials" message.
    """
    if request_token and is_remote_repo(repository) and not github_repo_from_url(repository):
        raise GitOperationError(
            "A GitHub token can only be used with an https://github.com/<owner>/<repo> URL. "
            "Use the repository's HTTPS URL, or leave the token empty.", 400)


def resolve_git_auth(clone_url: str, request_token: str = ""):
    """The credential git should use for `clone_url`, or None.

    In order: the developer's own token from the Results page (their
    fine-grained PAT; it replaces the server's token for this request, lives
    only in memory for it, and is never written to the database, the audit
    trail, the clone's .git/config, a commit or a response), the server's
    DIWO_GIT_PUSH_TOKEN, then the
    GitHub App's installation token for the repository. Only https GitHub URLs
    take a token; an ssh or local remote keeps whatever git already has.
    """
    target = github_repo_from_url(clone_url)
    if not target:
        return None
    host, owner, repo = target

    if request_token:
        return GitAuth(request_token, "request", host)
    if config.git_push_token():
        return GitAuth(config.git_push_token(), "server", host)
    token = _app_installation_token(owner, repo)
    if token:
        return GitAuth(token, "github_app", host)
    return None


def is_remote_repo(value: str) -> bool:
    """A clone URL rather than a filesystem path.

    `file://` counts: git clones it happily, but Path() would treat the URL text
    as a directory name and the repository would never be found.
    """
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "git@", "ssh://", "file://"))


def repo_name_from_url(url: str) -> str:
    """`https://github.com/user/my-repo.git` -> `my-repo`."""
    cleaned = url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    tail = cleaned.replace(":", "/").split("/")[-1]
    return safe_archive_path(tail, "repository").replace("/", "_") or "repository"


def web_url_for_remote(url: str) -> str:
    """Browser URL for a clone URL, so the response can link to the branch."""
    cleaned = url.strip()
    if cleaned.startswith("git@"):                       # git@github.com:user/repo.git
        host, _, path = cleaned[4:].partition(":")
        cleaned = f"https://{host}/{path}"
    if cleaned.startswith("ssh://git@"):
        cleaned = "https://" + cleaned[len("ssh://git@"):]
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned if cleaned.startswith("http") else ""


def _is_credential_error(err: str) -> bool:
    lowered = (err or "").lower()
    return any(marker in lowered for marker in (
        "could not read username", "terminal prompts disabled", "authentication failed",
        "invalid username or password", "permission to", "403",
    ))


def default_remote_branch(repo_path: Path) -> str:
    """The remote's own HEAD, so a work branch is cut from the right place."""
    ok, out, _ = run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo_path)
    if ok and out:
        return out.split("/", 1)[-1]
    for candidate in ("main", "master"):
        ok, _, _ = run_git(["rev-parse", "--verify", f"refs/remotes/origin/{candidate}"], cwd=repo_path)
        if ok:
            return candidate
    return "main"


def open_github_desktop(repo_path: Path):
    """Open GitHub Desktop on `repo_path`. Returns (opened, detail).

    The old command was `start github -- -r "<path>"`, which cmd reads as
    title="github", command="--" — the path never reached GitHub Desktop, so it
    opened on whatever repository was last selected and the refactored changes
    appeared to be missing. `start "" github "<path>"` passes the repository as
    the argument it actually is, and the direct executable is the fallback for
    when the `github` shim is not on PATH.
    """
    target = str(repo_path)

    if os.name == "nt":
        attempts = [
            ["cmd", "/c", "start", "", "github", target],
            [str(Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubDesktop" / "GitHubDesktop.exe"), target],
        ]
    else:
        attempts = [["github", target], ["open", "-a", "GitHub Desktop", target]]

    errors = []
    for cmd in attempts:
        if not cmd[0]:
            continue
        try:
            subprocess.Popen(cmd, cwd=target)
            return True, f"Launched: {' '.join(cmd[:2])}"
        except Exception as exc:                      # not installed / not on PATH
            errors.append(f"{cmd[0]}: {exc}")

    return False, "; ".join(errors) or "GitHub Desktop could not be launched."


def prepare_repository(repository: str, auth=None):
    """Resolve the request's repository to a local working copy.

    A URL is cloned (once) into CLONE_ROOT and re-fetched on later runs; a
    filesystem path is used where it is.

    Returns (repo_path, info). Every failure raises GitOperationError with the
    message the route reports — it used to return (None, error_message)
    instead, which made the result a union the caller had to unpack by hand
    and left `info` typed as "dict or the error string".
    """
    if is_remote_repo(repository):
        CLONE_ROOT.mkdir(parents=True, exist_ok=True)
        repo_path = CLONE_ROOT / repo_name_from_url(repository)

        if (repo_path / ".git").exists():
            ok, _, err = run_git(["fetch", "origin", "--prune"], cwd=repo_path, auth=auth)
            if not ok:
                raise GitOperationError(
                    f"Could not fetch the existing clone at {repo_path}: {err}", 400)
        else:
            if repo_path.exists():
                shutil.rmtree(repo_path, ignore_errors=True)
            ok, _, err = run_git(["clone", repository, str(repo_path)], auth=auth)
            if not ok:
                raise GitOperationError(
                    f"Could not clone {repository}: {err} — check the URL, and that you have "
                    "access to the repository (a private repo needs a GitHub token: enter your "
                    "fine-grained token on the Results page).",
                    400)

        return repo_path, {"remote": True, "clone_url": repository, "base_branch": default_remote_branch(repo_path)}

    repo_path = Path(repository).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise GitOperationError(f"Not a git repository: {repo_path}", 400)

    ok, out, _ = run_git(["remote", "get-url", "origin"], cwd=repo_path)
    return repo_path, {"remote": False, "clone_url": out if ok else "", "base_branch": None}


def apply_and_push(data: dict) -> dict:
    """Write the refactored project into a git repository, on its own branch.

    `data` is the request body of POST /api/diwo/apply-and-push:

      {
        "files":           [{ path, after|content }, ...],   # the WHOLE project
        "branch_name":     "refactoring/diwo-changes",
        "repository_path": "https://github.com/user/repo"  |  "C:/path/to/repo",
        "commit_message":  "...",       optional
        "commit":          true,        optional (default true)
        "push":            true         optional (default true when a remote exists)
      }

    `files` is the same entry list the "Download Project (.zip)" action packs:
    every project file, with the accepted refactorings replacing the originals
    in place. Sending only the refactored files would leave a freshly cloned
    repository holding nothing else.

    Returns the response body; raises GitOperationError with the status the
    route should answer with.
    """
    files = data.get("files") or []
    branch_name = str(data.get("branch_name") or "").strip()
    run_id = str(data.get("run_id") or data.get("workflow_id") or "").strip()
    # True when the name is DIWO's own suggestion: a taken suggestion is moved
    # to the next free name. A name the developer typed is never changed.
    branch_auto = bool(data.get("branch_auto")) or not branch_name
    repository = str(data.get("repository_path") or "").strip()
    want_commit = data.get("commit", True) is not False
    want_push = data.get("push", True) is not False
    # Optional, for this request only: never stored, logged or returned.
    request_token = str(data.get("github_token") or "").strip()

    if not isinstance(files, list) or not files:
        raise GitOperationError("No files provided.", 400)
    if not branch_name:
        if not run_id:
            raise GitOperationError("Branch name required (or a run_id to generate one).", 400)
        branch_name = generated_branch_name(run_id)
    if not repository:
        raise GitOperationError("Repository path or GitHub URL required.", 400)

    errors = validate_branch_name(branch_name, run_git)
    if errors:
        raise GitOperationError(" ".join(errors), 400)
    require_https_for_token(repository, request_token)

    auth = resolve_git_auth(repository, request_token) if is_remote_repo(repository) else None
    repo_path, info = prepare_repository(repository, auth=auth)
    if auth is None and not info["remote"]:
        # A local clone whose origin is a GitHub URL can be pushed with a token too.
        auth = resolve_git_auth(info.get("clone_url") or "", request_token)

    # ── Never overwrite an existing remote branch ────────────────────────────
    has_origin, _, _ = run_git(["remote", "get-url", "origin"], cwd=repo_path)
    renamed_from = ""
    if has_origin:
        def exists(name):
            return remote_branch_exists("origin", name, run_git, cwd=repo_path, auth=auth)[0]

        if exists(branch_name):
            if branch_auto and run_id:
                renamed_from = branch_name
                branch_name = next_free_branch_name(run_id, exists)
            else:
                raise GitOperationError(
                    f"Branch '{branch_name}' already exists on the remote repository. "
                    "Choose another name — DIWO does not overwrite existing branches.", 409)

    commit_message = str(data.get("commit_message") or "").strip() or (
        f"refactor: DIWO agent changes on {branch_name}"
    )

    # ── Never write to the base branch ───────────────────────────────────────
    # The refactoring always goes onto its own branch; main / master and the
    # remote's default branch are only ever the starting point.
    protected = {"main", "master"} | ({info["base_branch"]} if info.get("base_branch") else set())
    if branch_name in protected:
        raise GitOperationError(
            f"'{branch_name}' is the repository's base branch. DIWO pushes the refactoring to a "
            "new branch and never to the base branch — choose a different branch name.", 400)

    # ── Branch ───────────────────────────────────────────────────────────────
    if info["remote"]:
        # Cut the work branch from the remote's current head, so a second run
        # does not stack on top of the previous run's commit.
        #
        # --no-track: `checkout -B <branch> origin/main` otherwise records
        # origin/main as the branch's upstream, and any push that follows the
        # upstream — GitHub Desktop's Push button, a plain `git push` —
        # would put the refactoring on main instead of on this branch.
        base = info["base_branch"]
        ok, _, err = run_git(["checkout", "--no-track", "-B", branch_name, f"origin/{base}"], cwd=repo_path)
        if not ok:
            ok, _, err = run_git(["checkout", "-B", branch_name], cwd=repo_path)
        if not ok:
            raise GitOperationError(f"Could not create branch '{branch_name}': {err}", 500)
    else:
        exists, _, _ = run_git(["rev-parse", "--verify", branch_name], cwd=repo_path)
        ok, _, err = run_git(["checkout", branch_name] if exists
                             else ["checkout", "--no-track", "-b", branch_name], cwd=repo_path)
        if not ok:
            raise GitOperationError(f"Could not check out branch '{branch_name}': {err} — commit or stash the "
                "repository's current changes first.", 500)

    # A branch reused from an earlier run may still carry an upstream of main
    # (clones made before --no-track). Drop it; the push below sets the right one.
    run_git(["branch", "--unset-upstream", branch_name], cwd=repo_path)

    # ── Write the project ────────────────────────────────────────────────────
    written_files = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            continue
        content = entry.get("after")
        if content is None:
            content = entry.get("content") or ""

        target = (repo_path / rel).resolve()
        # Compare resolved parents so "src/../../etc/passwd" cannot escape, and
        # a path that merely shares a prefix ("/repo-backup") is not accepted.
        if repo_path != target and repo_path not in target.parents:
            raise GitOperationError(f"Path traversal detected: {rel}", 400)

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        written_files.append(rel)

    if not written_files:
        raise GitOperationError("None of the provided files had a usable path.", 400)

    # ── Stage ────────────────────────────────────────────────────────────────
    ok, _, err = run_git(["add", "-A"], cwd=repo_path)
    if not ok:
        raise GitOperationError(f"Failed to stage changes: {err}", 500)

    ok, staged_out, _ = run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    staged_files = [line.strip() for line in staged_out.splitlines() if line.strip()] if ok else []

    # ── Commit ───────────────────────────────────────────────────────────────
    committed = False
    commit_sha = ""
    commit_error = ""
    # Every run gets its own branch on the remote, even when the written files
    # are identical to the base branch (the refactorings are already there).
    # git cannot commit "no change", so that run is recorded as an EMPTY
    # commit, which still gives the branch something to push.
    no_changes = not staged_files
    if want_commit:
        commit_args = ["commit", "-m", commit_message]
        if no_changes:
            commit_args = ["commit", "--allow-empty", "-m", commit_message, "-m",
                           "DIWO run with no file differences: every written file already "
                           "matches the base branch."]
        ok, _, err = run_git(commit_args, cwd=repo_path)

        # A machine with no git identity cannot commit at all. For a clone DIWO
        # created it can set one itself — scoped to that clone, never --global,
        # and never touching a repository the developer already had.
        if not ok and info["remote"] and "user.email" in (err or "").lower():
            run_git(["config", "user.email", "diwo-agent@localhost"], cwd=repo_path)
            run_git(["config", "user.name", "DIWO Agent"], cwd=repo_path)
            ok, _, err = run_git(commit_args, cwd=repo_path)

        if ok:
            committed = True
            _, commit_sha, _ = run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
        else:
            commit_error = err or "git commit failed."

    # ── Push ─────────────────────────────────────────────────────────────────
    pushed = False
    push_error = ""
    if want_push and committed and has_origin:
        # A plain push, never --force: the branch was checked to be new above,
        # and if someone created it in the meantime git refuses the push rather
        # than replacing their commits.
        #
        # The refspec names the destination in full — refs/heads/<branch> on
        # the remote — so nothing (an upstream, push.default) can redirect the
        # commit to main. -u then makes that same remote branch the upstream,
        # so a later push from GitHub Desktop goes there too.
        refspec = f"refs/heads/{branch_name}:refs/heads/{branch_name}"
        ok, _, err = run_git(["push", "-u", "origin", refspec], cwd=repo_path, auth=auth)
        if ok:
            # Confirm the remote branch now holds exactly this commit.
            _, local_sha, _ = run_git(["rev-parse", "HEAD"], cwd=repo_path)
            _, remote_line, _ = run_git(["ls-remote", "origin", f"refs/heads/{branch_name}"],
                                        cwd=repo_path, auth=auth)
            pushed = bool(local_sha) and remote_line.split("\t", 1)[0] == local_sha
            if not pushed:
                push_error = (f"git reported success, but origin/{branch_name} does not hold "
                              f"commit {local_sha[:7]} — check the repository on GitHub.")
        elif "rejected" in (err or "").lower() or "fetch first" in (err or "").lower():
            push_error = (
                f"Branch '{branch_name}' was created on the remote by someone else while DIWO "
                "was writing it, so the push was refused rather than overwrite it. Push again "
                "with a different branch name."
            )
        elif auth is None and _is_credential_error(err):
            push_error = (
                "GitHub needs credentials to push. Enter your GitHub fine-grained token "
                "(Contents: Read and write on this repository) on the Results page and push "
                "again. The branch is committed on the server."
            )
        elif _is_credential_error(err) or "403" in (err or ""):
            push_error = (
                f"GitHub rejected the push"
                f"{' with your token' if auth and auth.source == 'request' else ''}: {err} — "
                "check that the token has Contents: Read and write access to this repository "
                "and has not expired."
            )
        else:
            push_error = f"{err} — the branch is committed on the server but was not pushed."
    elif want_push and not has_origin:
        push_error = "This repository has no 'origin' remote, so the branch stays local."
    elif want_push and not committed and (commit_error or staged_files):
        push_error = commit_error or "Nothing was committed, so there was nothing to push."
    # No staged files is not a failure: the branch already holds this exact
    # project, so there is nothing to push and nothing went wrong.

    # GitHub Desktop only makes sense when the backend runs on the developer's
    # own machine. On a hosted backend the working copy is on the server, so
    # launching it is skipped rather than reported as a failure.
    if config.open_github_desktop_enabled() and data.get("open_github_desktop", True) is not False:
        opened, launch_detail = open_github_desktop(repo_path)
    else:
        opened, launch_detail = False, "skipped"

    web_url = web_url_for_remote(info.get("clone_url") or "")
    branch_url = f"{web_url}/tree/{branch_name}" if (web_url and pushed) else ""
    base = info.get("base_branch")
    compare_url = (f"{web_url}/compare/{base}...{branch_name}?expand=1"
                   if (web_url and pushed and base) else "")

    base_label = f"origin/{info['base_branch']}" if info.get("base_branch") else "the base branch"
    if committed and pushed and no_changes:
        message = (
            f"Branch '{branch_name}' pushed to origin. Its files are identical to {base_label} "
            "— these refactorings are already there — so the commit records the run with no "
            "file changes."
        )
    elif committed and pushed:
        message = f"Branch '{branch_name}' committed and pushed to origin."
    elif committed:
        message = f"Branch '{branch_name}' committed, but not pushed to GitHub."
    elif staged_files:
        message = f"Changes staged on '{branch_name}' but not committed."
    else:
        # Name the commit the files already match, so "nothing changed" points
        # at where the changes are instead of reading like a failed push.
        _, last = run_git(["log", "-1", "--format=%h %s"], cwd=repo_path)[:2]
        message = (
            f"Nothing committed: every written file is identical to {base_label}"
            f"{f' (latest commit {last})' if last else ''}."
        )

    return {
        "status": "success",
        "message": message,
        "branch": branch_name,
        # Set when the suggested name was taken and DIWO picked the next one.
        "branch_renamed_from": renamed_from,
        "base_branch": info.get("base_branch"),
        "repository": str(repo_path),
        "cloned": bool(info["remote"]),
        "clone_url": info.get("clone_url") or "",
        "branch_url": branch_url,
        "compare_url": compare_url,
        # The exact remote ref written — always the new branch, never the base.
        "pushed_ref": f"refs/heads/{branch_name}" if pushed else "",
        "auth_source": auth.source if auth else None,
        "written_files": written_files,
        "written_count": len(written_files),
        "staged_files": staged_files,
        "committed": committed,
        "commit_sha": commit_sha,
        "commit_message": commit_message if committed else "",
        "commit_error": commit_error,
        "pushed": pushed,
        "push_error": push_error,
        "github_desktop_opened": opened,
        "github_desktop_detail": launch_detail,
    }


def check_branch(data: dict) -> dict:
    """Suggest and validate a branch name before anything is written.

    `data` is the body of POST /api/diwo/branch-name/check:

      {
        "run_id":          "wf_3f9a1c",          workflow / run id to name from
        "branch_name":     "...",                optional; omitted -> suggest one
        "repository_path": "https://github.com/user/repo" | "C:/path/to/repo",
        "github_token":    "..."                 optional, this request only
      }

    The remote is asked directly (`git ls-remote`), so a URL is checked
    without cloning it. Returns:

      {
        "branch_name": str,        the name checked (the suggestion if none given)
        "valid":       bool,       passes the naming rules and git check-ref-format
        "errors":      [str],
        "exists":      bool|None,  on the remote; None when it could not be asked
        "exists_error": str,
        "suggestion":  str,        a free generated name for this run
      }
    """
    run_id = str(data.get("run_id") or data.get("workflow_id") or "").strip()
    name = str(data.get("branch_name") or "").strip()
    repository = str(data.get("repository_path") or "").strip()
    request_token = str(data.get("github_token") or "").strip()

    if not name:
        name = generated_branch_name(run_id or "run")

    errors = validate_branch_name(name, run_git)

    # Where to ask: the URL itself, or the local clone's origin.
    remote, cwd, auth = None, None, None
    if repository and is_remote_repo(repository):
        remote, auth = repository, resolve_git_auth(repository, request_token)
    elif repository:
        path = Path(repository).expanduser()
        if (path / ".git").exists():
            ok, url, _ = run_git(["remote", "get-url", "origin"], cwd=path)
            if ok:
                remote, cwd = "origin", path
                auth = resolve_git_auth(url, request_token)

    exists, exists_error = None, ""
    if remote and not errors:
        exists, exists_error = remote_branch_exists(remote, name, run_git, cwd=cwd, auth=auth)

    def probe(candidate):
        if not remote:
            return False
        return remote_branch_exists(remote, candidate, run_git, cwd=cwd, auth=auth)[0]

    suggestion = next_free_branch_name(run_id or "run", probe)

    return {
        "branch_name": name,
        "valid": not errors,
        "errors": errors,
        "exists": exists,
        "exists_error": exists_error,
        "suggestion": suggestion,
    }
