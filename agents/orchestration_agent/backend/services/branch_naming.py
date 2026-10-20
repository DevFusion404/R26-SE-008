"""
Branch naming for "Push to GitHub"
==================================
R26-SE-008 | Bandara S M Y M | IT22277886

Every refactoring execution gets its own branch, named from the workflow / run
id, so two runs can never land on the same branch:

    refactoring/diwo-wf-3f9a1c        first run of workflow wf_3f9a1c
    refactoring/diwo-wf-3f9a1c-2      the same workflow pushed again

Names are checked twice: here, by rules that explain *why* a name is wrong in
words a developer can act on, and then by `git check-ref-format --branch`,
which is the authority — a rule missing from the list below cannot let a name
through that git itself would refuse.

The other half is never overwriting a branch that already exists on the
remote. A generated name that is taken moves on to the next free suffix; a
name the developer typed is refused with a clear message instead.
"""

import re

#: Prefix of every generated branch.
BRANCH_PREFIX = "refactoring/diwo-"

#: Upper bound on generated-name retries before giving up.
MAX_SUFFIX_ATTEMPTS = 50

#: A sane length cap. Git has no hard limit, but GitHub's UI and most file
#: systems do, and a 300-character branch name is always a mistake.
MAX_BRANCH_LENGTH = 200

# Characters git refuses anywhere in a ref name (see git-check-ref-format(1)).
_INVALID_CHARS = set("~^:?*[\\")


def slugify(value: str) -> str:
    """Lowercase letters, digits and single hyphens only."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower())
    return slug.strip("-")


def generated_branch_name(run_id: str, attempt: int = 1) -> str:
    """`refactoring/diwo-<run id>`, with `-<n>` from the second attempt on.

    Uses only lowercase letters, digits, hyphens and forward slashes, so a
    generated name is valid by construction.
    """
    slug = slugify(run_id)[:60] or "run"
    name = f"{BRANCH_PREFIX}{slug}"
    return name if attempt <= 1 else f"{name}-{attempt}"


def branch_name_errors(name: str) -> list:
    """Every rule `name` breaks, as messages for the developer. [] when valid.

    The rule checks only; check_ref_format() is run on top of these by
    validate_branch_name().
    """
    errors = []
    if not name:
        return ["Branch name is required."]

    if len(name) > MAX_BRANCH_LENGTH:
        errors.append(f"Branch name is longer than {MAX_BRANCH_LENGTH} characters.")
    if re.search(r"\s", name):
        errors.append("Branch name must not contain spaces or other whitespace.")
    if "//" in name:
        errors.append("Branch name must not contain consecutive slashes (//).")
    if ".." in name:
        errors.append("Branch name must not contain two consecutive dots (..).")
    if name.endswith("/"):
        errors.append("Branch name must not end with a slash.")
    if name.endswith("."):
        errors.append("Branch name must not end with a dot.")
    if name.startswith("/"):
        errors.append("Branch name must not start with a slash.")
    if name.startswith("-"):
        errors.append("Branch name must not start with a hyphen.")
    if name == "@":
        errors.append("'@' on its own is not a valid branch name.")
    if "@{" in name:
        errors.append("Branch name must not contain '@{'.")
    if name.endswith(".lock") or "/." in name or name.startswith(".") or ".lock/" in name:
        errors.append("No part of a branch name may start with a dot or end with '.lock'.")

    bad = sorted({ch for ch in name if ch in _INVALID_CHARS})
    if bad:
        errors.append("Branch name contains characters git does not allow: "
                      + " ".join(repr(ch) for ch in bad) + ".")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        errors.append("Branch name must not contain control characters.")
    return errors


def check_ref_format(name: str, run_git) -> tuple:
    """(ok, message) from `git check-ref-format --branch <name>`.

    `--` is not used: check-ref-format takes exactly one argument, and the
    rule checks above already refuse a leading hyphen, so the name cannot be
    read as an option.
    """
    ok, _, err = run_git(["check-ref-format", "--branch", name])
    if ok:
        return True, ""
    if "not installed" in (err or ""):
        # No git binary: the rule checks stand on their own.
        return True, ""
    return False, f"'{name}' is not a valid git branch name."


def validate_branch_name(name: str, run_git) -> list:
    """Rule errors, then git's own verdict. [] when the name is usable."""
    errors = branch_name_errors(name)
    if errors:
        return errors
    ok, message = check_ref_format(name, run_git)
    return [] if ok else [message]


def remote_branch_exists(remote: str, name: str, run_git, cwd=None, auth=None):
    """True / False when the remote answered, None when it could not be asked.

    `remote` is a URL or a remote name ("origin", with `cwd` set to the clone).
    `git ls-remote --heads` asks the remote itself, so the answer does not
    depend on how recently the local clone was fetched.
    """
    ok, out, err = run_git(["ls-remote", "--heads", remote, f"refs/heads/{name}"],
                           cwd=cwd, auth=auth)
    if not ok:
        return None, err
    return bool(out.strip()), ""


def next_free_branch_name(run_id: str, exists) -> str:
    """The first generated name for `run_id` that `exists(name)` says is free.

    `exists` returns True / False / None (unknown). An unknown answer is
    treated as free — the push itself still refuses to overwrite, so the worst
    case is a clear error, never a lost branch.
    """
    for attempt in range(1, MAX_SUFFIX_ATTEMPTS + 1):
        candidate = generated_branch_name(run_id, attempt)
        if exists(candidate) is not True:
            return candidate
    raise RuntimeError(
        f"Could not find a free branch name for run {run_id} after "
        f"{MAX_SUFFIX_ATTEMPTS} attempts.")
