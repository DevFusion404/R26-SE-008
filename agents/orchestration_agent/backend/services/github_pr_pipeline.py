"""
GitHub PR analysis pipeline
===========================
R26-SE-008 | Bandara S M Y M | IT22277886

Milestones 2-3: one pull-request commit → CUQA findings → a DIWO workflow at
the Code Smell Approval stage.

    PR head SHA ──GET /repos/{o}/{r}/zipball/{sha}──> snapshot.zip  (own temp dir)
         │                                                │
         │                      verify: one top-level dir named
         │                      {owner}-{repo}-{sha prefix}, no unsafe paths,
         │                      bounded size and entry count
         v                                                v
    CUQA POST /api/analyze-snapshot  (CUQA's own temp dir; its shared
         │                            workspace is never touched)
         v
    normalize_cuqa_report → cuqa_report_to_smells   (existing DIWO helpers)
         v
    persist_new_workflow(..., wf_id)                 (existing DIWO helper)
         v
    github_pr_workflows row for (repo, PR, SHA)

ISOLATION. Each analysis downloads into its own temporary directory, removed
afterwards, and CUQA analyses it in its own temporary directory. Nothing is
shared between two PRs, or between a PR and what a developer loaded in CUQA.

UNTRUSTED CODE. The snapshot is never executed: DIWO only reads the archive's
file list, and CUQA parses source statically. The installation token is used
by DIWO for the download and never reaches CUQA.

EXACT COMMIT. The archive is requested by full SHA, and GitHub names its single
top-level directory after that commit; a mismatch aborts the analysis.

IDEMPOTENT. The workflow id is written to the commit's mapping row BEFORE the
workflow is created, so a retry after a crash finds the id and reuses it — at
most one workflow per analysed commit.
"""

import logging
import shutil
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import config
from clients.cuqa_client import (CUQAError, analyze_snapshot, cuqa_base_url,
                                 fetch_quality_report, upload_workspace_zip)
from db import github_pr_workflow_repository as prs
from db.workflow_repository import get_workflow, log_event, parse_json_field
from domain.audit_detail import severity_totals, smell_type_totals, smells_by_file
from domain.cuqa_normalizer import (cuqa_report_to_smells, detect_primary_language,
                                    normalize_cuqa_report)
from services.github_app_service import GitHubAPIError, GitHubAppService
from services.github_report_service import verify_locations_in_snapshot
from services.workflow_service import new_workflow_id, persist_new_workflow

logger = logging.getLogger("diwo.github.pipeline")

#: Upper bounds on what DIWO accepts as a snapshot, checked before CUQA sees it.
MAX_ARCHIVE_ENTRIES = 50_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024

#: The CUQA quality report analyses at most this many files per repository.
CUQA_FILE_CAP = 50


class PipelineError(RuntimeError):
    """The analysis could not be completed.

    `retryable` marks failures that may clear on their own (CUQA restarting, a
    network blip); the job worker retries those within its attempt budget.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


# ─── GitHub: PR state and snapshot ──────────────────────────────────────────

def fetch_pr_head(app: GitHubAppService, job: dict) -> dict:
    """The PR as GitHub sees it now: {head_sha, state, head_repo_full_name}."""
    pr = app.request("GET", f"repos/{job['repo_owner']}/{job['repo_name']}/pulls/{job['pr_number']}",
                     job["installation_id"], expected=(200,), what="Pull request lookup") or {}
    head = pr.get("head") or {}
    return {
        "head_sha": head.get("sha"),
        "state": pr.get("state"),
        "head_repo_full_name": (head.get("repo") or {}).get("full_name"),
    }


@contextmanager
def isolated_workspace(job_id: str):
    """A private temporary directory for one analysis, always removed."""
    safe = "".join(c for c in job_id if c.isalnum() or c in "_-")[:40]
    path = Path(tempfile.mkdtemp(prefix=f"diwo_pr_{safe}_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _unsafe_member(name: str) -> bool:
    if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
        return True
    return ".." in PurePosixPath(name.replace("\\", "/")).parts


def verify_snapshot(zip_path, owner: str, repo: str, head_sha: str) -> str:
    """Check the archive is the requested commit and safe to hand to CUQA.

    Returns the archive's top-level directory name.
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
    except zipfile.BadZipFile:
        raise PipelineError("GitHub returned an invalid snapshot archive.", retryable=True) from None

    if not members:
        raise PipelineError("The snapshot archive is empty.")
    if len(members) > MAX_ARCHIVE_ENTRIES:
        raise PipelineError(f"The snapshot has more than {MAX_ARCHIVE_ENTRIES} entries.")
    if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
        raise PipelineError("The snapshot is larger than 1 GB uncompressed.")
    if any(_unsafe_member(m.filename) for m in members):
        raise PipelineError("The snapshot archive contains unsafe paths.")

    tops = {m.filename.replace("\\", "/").split("/", 1)[0] for m in members}
    if len(tops) != 1:
        raise PipelineError("The snapshot archive does not have a single root directory.")
    top = tops.pop()

    # GitHub names the root "{owner}-{repo}-{sha prefix}".
    suffix = top.rsplit("-", 1)[-1].lower()
    if len(suffix) < 7 or not head_sha.lower().startswith(suffix):
        raise PipelineError(
            f"The downloaded snapshot does not match commit {head_sha[:7]}; analysis aborted.")
    return top


def download_snapshot(app: GitHubAppService, job: dict, workdir: Path) -> tuple[Path, str]:
    """Download and verify the exact head commit. Returns (zip_path, top_dir)."""
    zip_path = workdir / "snapshot.zip"
    app.download_to_file(
        f"repos/{job['repo_owner']}/{job['repo_name']}/zipball/{job['head_sha']}",
        job["installation_id"], zip_path, config.github_snapshot_max_bytes(),
        what="PR snapshot download")
    top = verify_snapshot(zip_path, job["repo_owner"], job["repo_name"], job["head_sha"])
    return zip_path, top


# ─── CUQA ───────────────────────────────────────────────────────────────────

_shared_workspace_lock = threading.Lock()


def _cuqa_failure(exc: CUQAError) -> PipelineError:
    retryable = exc.status in (408, 429) or exc.status >= 500
    return PipelineError(f"CUQA analysis failed: {exc.message[:500]}", retryable=retryable)


def _analyse_in_shared_workspace(zip_path, job: dict) -> dict:
    """Opt-in fallback for a CUQA without /api/analyze-snapshot.

    Serialized within this process, and the report is accepted only if CUQA
    says it describes the archive just uploaded — if anything replaced the
    workspace in between, the run is retried rather than publishing a report
    for some other repository.
    """
    marker = f"diwo-pr-{job['id']}"
    timeout = config.github_cuqa_timeout_seconds()
    with _shared_workspace_lock:
        upload_workspace_zip(zip_path, f"{marker}.zip", timeout=timeout)
        payload = fetch_quality_report(timeout=timeout)

    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(report, dict) or report.get("repo_name") != marker:
        raise PipelineError(
            "CUQA's shared workspace changed during the analysis; the report was discarded.",
            retryable=True)
    report["repo_name"] = job["repo_full_name"]
    payload.setdefault("snapshot", {"top_level_dir": None, "files_found": None,
                                    "files_analyzed": None, "mode": "shared_workspace"})
    return payload


def run_cuqa(zip_path, top_dir: str, job: dict) -> dict:
    """The raw CUQA payload for this snapshot, verified to be for this snapshot."""
    timeout = config.github_cuqa_timeout_seconds()
    filename = f"{top_dir}.zip"
    try:
        payload = analyze_snapshot(zip_path, filename, job["repo_full_name"], timeout=timeout)
    except CUQAError as exc:
        if exc.status in (404, 405):
            if config.github_cuqa_allow_shared_workspace():
                try:
                    return _analyse_in_shared_workspace(zip_path, job)
                except CUQAError as inner:
                    raise _cuqa_failure(inner) from None
            raise PipelineError(
                f"The CUQA agent at {cuqa_base_url()} does not provide POST /api/analyze-snapshot, "
                "so this commit cannot be analysed in isolation. Deploy the updated CUQA agent "
                "(or, temporarily, set GITHUB_CUQA_ALLOW_SHARED_WORKSPACE=true).") from None
        raise _cuqa_failure(exc) from None

    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if isinstance(snapshot, dict) and snapshot.get("top_level_dir") not in (None, top_dir):
        raise PipelineError("CUQA reported on a different snapshot than the one sent.")
    return payload


# ─── The analysis ───────────────────────────────────────────────────────────

def _result(status, report=None, smells=None, workflow_id=None):
    report = report or {}
    snapshot = report.get("snapshot") or {}
    summary = report.get("summary") or {}
    return {
        "status": status,
        "workflow_id": workflow_id,
        "report": report,
        "smells": smells or [],
        "files_found": snapshot.get("files_found"),
        "files_analyzed": snapshot.get("files_analyzed", summary.get("files_analyzed")),
    }


def workflow_target(job: dict) -> str:
    return f"{job['repo_full_name']}#{job['pr_number']}@{job['head_sha'][:7]}"


def run_pr_analysis(app: GitHubAppService, job: dict, mapping: dict) -> dict:
    """Analyse the job's commit and create (or reuse) its DIWO workflow.

    Returns {status, workflow_id, report, smells, files_found, files_analyzed}
    where status is prs.COMPLETED or prs.NO_SMELLS. Raises PipelineError or
    GitHubAPIError; nothing is published to GitHub here.
    """
    with isolated_workspace(job["id"]) as workdir:
        zip_path, top_dir = download_snapshot(app, job, workdir)
        payload = run_cuqa(zip_path, top_dir, job)

        try:
            report = normalize_cuqa_report(payload)
        except ValueError as exc:
            raise PipelineError(f"Unexpected CUQA response shape: {exc}") from None
        report["repo_name"] = job["repo_full_name"]
        report["snapshot"] = {**(payload.get("snapshot") or {}), "head_sha": job["head_sha"],
                              "top_level_dir": top_dir}
        report["github"] = {"repository": job["repo_full_name"], "pr_number": job["pr_number"],
                            "head_sha": job["head_sha"]}
        smells = cuqa_report_to_smells(report)
        # Check every finding's line against the file AT THIS COMMIT while the
        # archive is still here, so annotations land on the right lines.
        report["snapshot"].update(verify_locations_in_snapshot(zip_path, top_dir, smells))
    files_analyzed = (report.get("summary") or {}).get("files_analyzed", 0)

    if not smells:
        prs.update_mapping(mapping["id"], analysis_status=prs.NO_SMELLS, smell_count=0,
                           files_analyzed=files_analyzed, error=None, job_id=job["id"])
        return _result(prs.NO_SMELLS, report)

    # Record the id first: a crash after this line still leads a retry back
    # to the same workflow.
    wf_id = mapping.get("workflow_id") or new_workflow_id()
    if not mapping.get("workflow_id"):
        prs.update_mapping(mapping["id"], workflow_id=wf_id)

    if get_workflow(wf_id) is None:
        persist_new_workflow(workflow_target(job), detect_primary_language(report), smells,
                             source="github_pr", cuqa_report=report, wf_id=wf_id)
        log_event(wf_id, "smell_review", "github_pr_linked", {
            "repository": job["repo_full_name"],
            "pr_number": job["pr_number"],
            "head_sha": job["head_sha"],
            "installation_id": job["installation_id"],
            "delivery_id": job["delivery_id"],
            "job_id": job["id"],
        }, actor="system")
        log_event(wf_id, "smell_review", "cuqa_report_ingested", {
            "cuqa_url": cuqa_base_url(),
            "report_type": report.get("report_type"),
            "repo_name": report.get("repo_name"),
            "head_sha": job["head_sha"],
            "files_analyzed": files_analyzed,
            "smell_count": len(smells),
            "files_affected": len(smells_by_file(smells)),
            "by_file": smells_by_file(smells),
            "smell_types": smell_type_totals(smells),
            "severities": severity_totals(smells),
        }, actor="system")
        logger.info("Workflow %s created for %s", wf_id, workflow_target(job))

    prs.update_mapping(mapping["id"], analysis_status=prs.COMPLETED, smell_count=len(smells),
                       files_analyzed=files_analyzed, error=None, job_id=job["id"])
    return _result(prs.COMPLETED, report, smells, wf_id)


def load_existing_result(mapping: dict) -> dict | None:
    """The stored result for a commit that was already analysed, or None.

    A redelivery or a reopened PR at the same commit republishes this instead
    of analysing — and creating a workflow — a second time.
    """
    if mapping["analysis_status"] == prs.NO_SMELLS:
        return _result(prs.NO_SMELLS, {"summary": {"files_analyzed": mapping.get("files_analyzed")}})
    if mapping["analysis_status"] != prs.COMPLETED or not mapping.get("workflow_id"):
        return None
    wf = get_workflow(mapping["workflow_id"])
    if wf is None:
        return None
    report = parse_json_field(wf, "cuqa_report_json") or {}
    smells = parse_json_field(wf, "smells_json") or []
    return _result(prs.COMPLETED, report, smells, mapping["workflow_id"])


__all__ = [
    "PipelineError", "GitHubAPIError", "fetch_pr_head", "isolated_workspace",
    "verify_snapshot", "download_snapshot", "run_cuqa", "run_pr_analysis",
    "load_existing_result", "workflow_target", "CUQA_FILE_CAP",
]
