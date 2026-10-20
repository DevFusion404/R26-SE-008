"""
GitHub job processing
=====================
R26-SE-008 | Bandara S M Y M | IT22277886

What the worker does with one claimed job (Milestones 1-3):

    claim job ──> superseded by a newer commit? ──yes──> mark superseded
                         │ no
                         v
    Check Run on the PR head SHA (queued) ── fork PR? ──> completed: skipped
                         │
                         v  in_progress
    PR head still this commit?  (else completed: cancelled / superseded)
                         │
                         v
    commit already analysed? ── yes ──> reuse its workflow
                         │ no
                         v
    github_pr_pipeline: exact snapshot → CUQA → DIWO workflow → mapping
                         │
                         v
    PR head still this commit?  (a slow analysis never overwrites a newer one)
                         │
                         v
    completed: action_required (smells, review in DIWO) | neutral (none / failed)

Conclusions never claim more than happened: nothing is refactored, applied or
validated here, so "success" is never used. A CUQA outage is retried within
the attempt budget and then published as "Analysis failed" — the GitHub
connection keeps working even when CUQA does not.

The Check Run id is stored on the job the moment GitHub returns it, so a retry
after a later failure updates that same Check Run instead of creating a second.

Each job runs inside a Flask app context supplied by the caller, because the
SQLite backend binds its connection to Flask's `g`.
"""

import logging
import os
import socket
import threading

import config
from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from services import github_pr_pipeline as pipeline
from services.github_app_service import GitHubAPIError, GitHubAppService, GitHubConfigError
from services.github_check_service import (build_annotations, build_findings_output,
                                           create_diwo_check, failure_output, fork_output,
                                           publish_result, superseded_output,
                                           update_diwo_check, workflow_details_url)

logger = logging.getLogger("diwo.github.worker")

#: Upper bound on jobs handled per run_pending_jobs() call, so one busy poll
#: cannot starve the stop signal.
BATCH_LIMIT = 25


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class _Superseded(Exception):
    def __init__(self, newer_sha: str | None = None, newer_job: str | None = None):
        super().__init__(newer_sha or newer_job or "superseded")
        self.newer_sha = newer_sha
        self.newer_job = newer_job


def _ensure_current(job: dict, app: GitHubAppService) -> None:
    """Raise _Superseded when the PR has moved past this job's commit.

    Two independent signals: a later webhook already queued for the PR, and
    the PR head GitHub reports right now (covers a missed or delayed delivery).
    """
    newer_job = jobs.newer_job_for_pr(job)
    if newer_job:
        raise _Superseded(newer_job=newer_job)
    head_sha = pipeline.fetch_pr_head(app, job).get("head_sha")
    if head_sha and head_sha != job["head_sha"]:
        raise _Superseded(newer_sha=head_sha)


def _best_effort(action, label: str) -> None:
    """Publish something to GitHub without letting a failure mask the real error."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not update the Check Run (%s): %s", label,
                       getattr(exc, "message", type(exc).__name__))


TASK_EVENTS = ("issue_comment", "check_run", "transformation", "writeback")


def process_task(job: dict, app: GitHubAppService) -> str:
    """Run a non-analysis job: a PR command, an SCTVA run or a write-back."""
    import json
    from flask import g
    from services.github_command_service import RetryableCommandError, process_command_job

    label = f"job={job['id']} event={job['event']} repo={job['repo_full_name']} pr=#{job['pr_number']}"
    command = json.loads(job.get("command_json") or "{}")
    if job["event"] in ("transformation", "writeback"):
        g.diwo_user = command.get("identity") or None
    try:
        if job["event"] in ("issue_comment", "check_run"):
            outcome = process_command_job(job, app)
        elif job["event"] == "transformation":
            outcome = _transformation_task(job, app, command)
        else:
            outcome = _writeback_task(job, app, command)
    except (RetryableCommandError, _Retry) as exc:
        status = jobs.retry_or_fail_job(job, str(exc))
        logger.warning("Task %s (%s): %s", status, label, exc)
        return status
    except GitHubAPIError as exc:
        status = jobs.retry_or_fail_job(job, exc.message) if exc.retryable else None
        if status is None:
            jobs.fail_job(job["id"], exc.message)
            status = jobs.FAILED
        logger.warning("Task %s (%s): %s", status, label, exc.message)
        return status
    except _Fail:
        raise                      # definitive failure: recorded by process_job, not retried
    except Exception as exc:  # noqa: BLE001 - a job must never kill the worker
        logger.exception("Unexpected error processing %s", label)
        return jobs.retry_or_fail_job(job, f"Unexpected error: {type(exc).__name__}")
    jobs.complete_job(job["id"])
    logger.info("Task completed (%s): %s", outcome, label)
    return jobs.COMPLETED


class _Retry(Exception):
    pass


def _transformation_task(job, app, command) -> str:
    from services.github_transformation_service import (TransformationJobError,
                                                        publish_transformations,
                                                        run_transformation_job)
    try:
        run_transformation_job(job, app)
    except TransformationJobError as exc:
        if exc.retryable and job["attempts"] < job["max_attempts"]:
            raise _Retry(exc.message) from None
        mapping = prs.get_mapping_for_workflow(command.get("workflow_id"))
        if mapping:
            prs.update_mapping(mapping["id"], review_status="transformation_failed")
            if mapping.get("check_run_id"):
                _best_effort(lambda: publish_result(
                    app, pipeline_job(mapping), mapping["check_run_id"], conclusion="neutral",
                    output={"title": "Transformation failed — nothing was changed",
                            "summary": f"{exc.message}\n\nThe approved plan is kept; the pull "
                                       "request is unchanged."}), job["id"])
        raise _Fail(exc.message) from None
    mapping = prs.get_mapping_for_workflow(command.get("workflow_id"))
    prs.update_mapping(mapping["id"], review_status="transformation_ready")
    publish_transformations(app, mapping, command["workflow_id"])
    return "transformed"


def _writeback_task(job, app, command) -> str:
    from db import github_review_repository as reviews
    from services.github_transformation_service import publish_transformations
    from services.github_writeback_service import run_writeback_job

    writeback = run_writeback_job(job, app)
    mapping = prs.get_mapping_by_id(writeback["mapping_id"])
    if writeback["status"] == reviews.WB_COMMITTED:
        pr_url = (f"https://github.com/{mapping['repository_full_name']}/pull/"
                  f"{mapping['pr_number']}/files")
        note = (f"**Applied** in commit `{writeback['commit_sha']}` on `{writeback.get('branch')}` "
                f"by RefactorIQ on behalf of {writeback.get('requested_by')}. "
                f"[View the updated diff]({pr_url}). The new commit is analysed as a new snapshot.")
    else:
        note = f"**Not applied:** {writeback.get('error')}. Nothing was committed."
    publish_transformations(app, mapping, writeback["workflow_id"], writeback=writeback, note=note)
    return writeback["status"]


class _Fail(Exception):
    pass


def pipeline_job(mapping: dict) -> dict:
    return {"repo_owner": mapping["repository_owner"], "repo_name": mapping["repository_name"],
            "repo_full_name": mapping["repository_full_name"], "pr_number": mapping["pr_number"],
            "head_sha": mapping["head_sha"], "installation_id": mapping["installation_id"]}


def process_job(job: dict, app: GitHubAppService) -> str:
    """Run one claimed job to a terminal or retry state. Returns that status.

        Check Run (queued) → fork? → current? → analyse or reuse → current?
        → publish findings → job completed
    """
    if job.get("event") in TASK_EVENTS:
        try:
            return process_task(job, app)
        except _Fail as exc:
            jobs.fail_job(job["id"], str(exc))
            return jobs.FAILED

    label = f"job={job['id']} delivery={job['delivery_id']} " \
            f"repo={job['repo_full_name']} pr=#{job['pr_number']} sha={job['head_sha'][:7]}"

    newer = jobs.newer_job_for_pr(job)
    if newer:
        if job.get("check_run_id"):
            _best_effort(lambda: publish_result(app, job, job["check_run_id"], conclusion="cancelled",
                                                output=superseded_output(job, None)), label)
        jobs.supersede_job(job["id"], newer)
        logger.info("Superseded by %s: %s", newer, label)
        return jobs.SUPERSEDED

    owner, repo, installation = job["repo_owner"], job["repo_name"], job["installation_id"]
    check_run_id = job.get("check_run_id")
    mapping = None
    try:
        if not check_run_id:
            run = create_diwo_check(app, installation, owner, repo, job["head_sha"],
                                    status="queued", external_id=job["id"])
            check_run_id = run["id"]
            jobs.update_job(job["id"], check_run_id=check_run_id)

        mapping = prs.get_or_create_mapping(job)
        prs.update_mapping(mapping["id"], job_id=job["id"], check_run_id=check_run_id)

        if job.get("is_fork"):
            publish_result(app, job, check_run_id, conclusion="skipped", output=fork_output(job))
            prs.update_mapping(mapping["id"], analysis_status=prs.SKIPPED_FORK)
            jobs.complete_job(job["id"])
            logger.info("Fork PR skipped: %s", label)
            return jobs.COMPLETED

        update_diwo_check(app, installation, owner, repo, check_run_id, status="in_progress",
                          title=f"Analysing commit {job['head_sha'][:7]}",
                          summary=f"RefactorIQ is running CUQA on commit `{job['head_sha']}`.")
        _ensure_current(job, app)

        result = pipeline.load_existing_result(mapping)
        if result is None:
            result = pipeline.run_pr_analysis(app, job, mapping)
        else:
            logger.info("Commit already analysed; republishing: %s", label)

        # The analysis may have taken minutes. Never let it publish over a
        # newer commit's result.
        _ensure_current(job, app)

        details_url = workflow_details_url(result["workflow_id"])
        annotations, _ = build_annotations(result["smells"], result.get("report"))
        # Review is possible from DIWO (a link) or from the PR (commands).
        reviewable = bool(details_url) or config.github_commands_enabled()
        conclusion = "action_required" if result["smells"] and reviewable else "neutral"
        output = build_findings_output(job, result, details_url)

        # A commit RefactorIQ itself pushed is a new snapshot like any other:
        # analysed and shown, never acted on automatically — so it cannot
        # start another bot commit. It is linked to the write-back it came from.
        from db.github_review_repository import writeback_for_commit
        origin = writeback_for_commit(job["head_sha"])
        if origin:
            prs.update_mapping(mapping["id"], origin_writeback_id=origin["id"])
            output["summary"] = (f"_This commit was created by RefactorIQ write-back "
                                 f"`{origin['id']}` (workflow `{origin['workflow_id']}`). It is "
                                 "analysed as a new snapshot; nothing runs on it automatically._"
                                 "\n\n" + output["summary"])
        publish_result(app, job, check_run_id, conclusion=conclusion, output=output,
                       annotations=annotations, details_url=details_url)

    except _Superseded as exc:
        _best_effort(lambda: publish_result(app, job, check_run_id, conclusion="cancelled",
                                            output=superseded_output(job, exc.newer_sha)), label)
        if mapping:
            _best_effort(lambda: prs.update_mapping(mapping["id"],
                                                    analysis_status=prs.SUPERSEDED), label)
        jobs.supersede_job(job["id"], exc.newer_job or f"head:{exc.newer_sha}")
        logger.info("Superseded during processing: %s", label)
        return jobs.SUPERSEDED

    except (pipeline.PipelineError, GitHubAPIError) as exc:
        return _handle_failure(job, app, check_run_id, mapping, exc.message, exc.retryable, label)

    except GitHubConfigError as exc:
        jobs.fail_job(job["id"], str(exc))
        logger.error("GitHub configuration error: %s - %s", label, exc)
        return jobs.FAILED

    except Exception as exc:  # noqa: BLE001 - a job must never kill the worker
        logger.exception("Unexpected error processing %s", label)
        return _handle_failure(job, app, check_run_id, mapping,
                               f"Unexpected error: {type(exc).__name__}", True, label)

    jobs.complete_job(job["id"])
    logger.info("Check Run %s published (%s): %s", check_run_id, result["status"], label)
    return jobs.COMPLETED


def _handle_failure(job, app, check_run_id, mapping, message, retryable, label) -> str:
    """Retry within the attempt budget; otherwise record and publish the failure."""
    if retryable:
        status = jobs.retry_or_fail_job(job, message)
    else:
        jobs.fail_job(job["id"], message)
        status = jobs.FAILED
    logger.warning("Job %s (attempt %s/%s): %s - %s", status, job["attempts"],
                   job["max_attempts"], label, message)

    if mapping:
        def record():
            # A failure while PUBLISHING leaves a finished analysis intact, so
            # the retry republishes it instead of running CUQA again.
            current = prs.get_mapping(job["repo_full_name"], job["pr_number"], job["head_sha"])
            if current and current["analysis_status"] in (prs.COMPLETED, prs.NO_SMELLS):
                return
            prs.update_mapping(mapping["id"], error=message[:2000],
                               analysis_status=prs.FAILED if status == jobs.FAILED else prs.PENDING)
        _best_effort(record, label)
    if not check_run_id:
        return status
    if status == jobs.FAILED:
        _best_effort(lambda: publish_result(app, job, check_run_id, conclusion="neutral",
                                            output=failure_output(job, message)), label)
    else:
        _best_effort(lambda: update_diwo_check(
            app, job["installation_id"], job["repo_owner"], job["repo_name"], check_run_id,
            status="in_progress", title="Analysis delayed — retrying",
            summary=f"Attempt {job['attempts']} of {job['max_attempts']} did not complete: "
                    f"{message[:500]}\n\nRefactorIQ will retry automatically."), label)
    return status


def run_pending_jobs(app: GitHubAppService | None = None, worker_id: str | None = None,
                     limit: int = BATCH_LIMIT) -> list:
    """Claim and process jobs until none is due. Returns [(job_id, status), ...].

    Does nothing while the integration is disabled or misconfigured, and in
    that case leaves every job queued: they are durable, and become processable
    as soon as the configuration is fixed.
    """
    if not config.github_integration_enabled():
        return []
    if app is None:
        try:
            app = shared_app_service()
        except GitHubConfigError as exc:
            logger.error("GitHub worker idle - %s", exc)
            return []

    worker_id = worker_id or default_worker_id()
    results = []
    for _ in range(limit):
        job = jobs.claim_next_job(worker_id)
        if job is None:
            break
        _clear_actor()        # one job's identity must never attribute the next job's actions
        try:
            results.append((job["id"], process_job(job, app)))
        finally:
            _clear_actor()
    return results


def _clear_actor() -> None:
    from flask import g, has_app_context
    if has_app_context():
        g.pop("diwo_user", None)


_app_service = None
_app_service_lock = threading.Lock()


def shared_app_service() -> GitHubAppService:
    """One service per process, so installation tokens are reused across jobs."""
    global _app_service
    with _app_service_lock:
        if _app_service is None:
            _app_service = GitHubAppService.from_config()
        return _app_service


def reset_shared_app_service() -> None:
    global _app_service
    with _app_service_lock:
        _app_service = None


def run_worker(flask_app, stop_event: threading.Event | None = None,
               once: bool = False, worker_id: str | None = None) -> None:
    """Poll the job table until `stop_event` is set (or one pass when `once`)."""
    stop_event = stop_event or threading.Event()
    worker_id = worker_id or default_worker_id()
    poll = config.github_worker_poll_seconds()
    logger.info("GitHub worker %s started (poll every %ss)", worker_id, poll)

    while not stop_event.is_set():
        try:
            with flask_app.app_context():
                run_pending_jobs(worker_id=worker_id)
        except Exception:  # noqa: BLE001 - e.g. database briefly unreachable
            logger.exception("GitHub worker pass failed; will retry")
        if once:
            break
        stop_event.wait(poll)


def start_embedded_worker(flask_app) -> threading.Thread:
    """Run the worker as a daemon thread in this process (development only)."""
    thread = threading.Thread(target=run_worker, args=(flask_app,),
                              name="diwo-github-worker", daemon=True)
    thread.start()
    return thread
