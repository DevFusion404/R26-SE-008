"""
Git repository operations
=========================
R26-SE-008 | Bandara S M Y M | IT22277886

Clone / fetch / checkout / branch / stage / commit / push, plus writing the
refactored project into the working copy and opening GitHub Desktop on it.

Every git call goes through run_git(), which never raises — the caller reads
(ok, stdout, stderr) and decides what to report. Behaviour is exactly what it
was inside diwo/routes.apply_and_push; only the clone root and the timeout now
come from config.
"""

import os
import shutil
import subprocess
from pathlib import Path

from config import CLONE_ROOT, GIT_TIMEOUT
from services.archive_service import safe_archive_path


def run_git(args, cwd=None, timeout=GIT_TIMEOUT):
    """Run one git command. Returns (ok, stdout, stderr) and never raises."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            timeout=timeout,
        )
        return (
            proc.returncode == 0,
            proc.stdout.decode("utf-8", errors="replace").strip(),
            proc.stderr.decode("utf-8", errors="replace").strip(),
        )
    except FileNotFoundError:
        return False, "", "git is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return False, "", f"git {' '.join(args)} timed out after {timeout}s."


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


def prepare_repository(repository: str):
    """Resolve the request's repository to a local working copy.

    A URL is cloned (once) into CLONE_ROOT and re-fetched on later runs; a
    filesystem path is used where it is. Returns (repo_path, info) or (None, error).
    """
    if is_remote_repo(repository):
        CLONE_ROOT.mkdir(parents=True, exist_ok=True)
        repo_path = CLONE_ROOT / repo_name_from_url(repository)

        if (repo_path / ".git").exists():
            ok, _, err = run_git(["fetch", "origin", "--prune"], cwd=repo_path)
            if not ok:
                return None, f"Could not fetch the existing clone at {repo_path}: {err}"
        else:
            if repo_path.exists():
                shutil.rmtree(repo_path, ignore_errors=True)
            ok, _, err = run_git(["clone", repository, str(repo_path)])
            if not ok:
                return None, (
                    f"Could not clone {repository}: {err} — check the URL, and that you have "
                    "access to the repository (a private repo needs credentials configured for git)."
                )

        return repo_path, {"remote": True, "clone_url": repository, "base_branch": default_remote_branch(repo_path)}

    repo_path = Path(repository).expanduser().resolve()
    if not (repo_path / ".git").exists():
        return None, f"Not a git repository: {repo_path}"

    ok, out, _ = run_git(["remote", "get-url", "origin"], cwd=repo_path)
    return repo_path, {"remote": False, "clone_url": out if ok else "", "base_branch": None}


class GitOperationError(RuntimeError):
    """A git step failed in a way the caller should report verbatim.

    Carries the HTTP status the route used to return directly, so splitting
    the handler into a service did not change a single response code.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


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
    repository = str(data.get("repository_path") or "").strip()
    commit_message = str(data.get("commit_message") or "").strip() or (
        f"refactor: DIWO agent changes on {branch_name or 'diwo branch'}"
    )
    want_commit = data.get("commit", True) is not False
    want_push = data.get("push", True) is not False

    if not isinstance(files, list) or not files:
        raise GitOperationError("No files provided.", 400)
    if not branch_name:
        raise GitOperationError("Branch name required.", 400)
    if not repository:
        raise GitOperationError("Repository path or GitHub URL required.", 400)

    repo_path, info = prepare_repository(repository)
    if repo_path is None:
        raise GitOperationError(info, 400)

    # ── Branch ───────────────────────────────────────────────────────────────
    if info["remote"]:
        # Cut the work branch from the remote's current head, so a second run
        # does not stack on top of the previous run's commit.
        base = info["base_branch"]
        ok, _, err = run_git(["checkout", "-B", branch_name, f"origin/{base}"], cwd=repo_path)
        if not ok:
            ok, _, err = run_git(["checkout", "-B", branch_name], cwd=repo_path)
        if not ok:
            raise GitOperationError(f"Could not create branch '{branch_name}': {err}", 500)
    else:
        exists, _, _ = run_git(["rev-parse", "--verify", branch_name], cwd=repo_path)
        ok, _, err = run_git(["checkout", branch_name] if exists else ["checkout", "-b", branch_name], cwd=repo_path)
        if not ok:
            raise GitOperationError(f"Could not check out branch '{branch_name}': {err} — commit or stash the "
                "repository's current changes first.", 500)

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
    if want_commit and staged_files:
        ok, _, err = run_git(["commit", "-m", commit_message], cwd=repo_path)

        # A machine with no git identity cannot commit at all. For a clone DIWO
        # created it can set one itself — scoped to that clone, never --global,
        # and never touching a repository the developer already had.
        if not ok and info["remote"] and "user.email" in (err or "").lower():
            run_git(["config", "user.email", "diwo-agent@localhost"], cwd=repo_path)
            run_git(["config", "user.name", "DIWO Agent"], cwd=repo_path)
            ok, _, err = run_git(["commit", "-m", commit_message], cwd=repo_path)

        if ok:
            committed = True
            _, commit_sha, _ = run_git(["rev-parse", "--short", "HEAD"], cwd=repo_path)
        else:
            commit_error = err or "git commit failed."

    # ── Push ─────────────────────────────────────────────────────────────────
    has_origin, _, _ = run_git(["remote", "get-url", "origin"], cwd=repo_path)
    pushed = False
    push_error = ""
    if want_push and committed and has_origin:
        ok, _, err = run_git(["push", "-u", "origin", branch_name], cwd=repo_path)
        if ok:
            pushed = True
        else:
            push_error = (
                f"{err} — the branch is committed locally, so you can push it from GitHub Desktop "
                "once credentials are available."
            )
    elif want_push and not has_origin:
        push_error = "This repository has no 'origin' remote, so the branch stays local."
    elif want_push and not committed:
        push_error = commit_error or "Nothing was committed, so there was nothing to push."

    opened, launch_detail = open_github_desktop(repo_path)

    web_url = web_url_for_remote(info.get("clone_url") or "")
    branch_url = f"{web_url}/tree/{branch_name}" if (web_url and pushed) else ""

    if committed and pushed:
        message = f"Branch '{branch_name}' committed and pushed to origin."
    elif committed:
        message = f"Branch '{branch_name}' committed locally."
    elif staged_files:
        message = f"Changes staged on '{branch_name}' — review and commit in GitHub Desktop."
    else:
        message = f"Branch '{branch_name}' is up to date; the project already matches these files."

    return {
        "status": "success",
        "message": message,
        "branch": branch_name,
        "base_branch": info.get("base_branch"),
        "repository": str(repo_path),
        "cloned": bool(info["remote"]),
        "clone_url": info.get("clone_url") or "",
        "branch_url": branch_url,
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
