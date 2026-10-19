"""
Background processing end to end
================================
services/github_job_service.py over the pipeline, the job queue and the PR
mapping: signed webhook → durable job → exact PR snapshot → CUQA → DIWO
workflow → GitHub Check Run with the real findings.

GitHub and CUQA are faked (tests/github/conftest.py); the database is the
suite's throwaway SQLite file, and the Supabase class at the bottom repeats the
key paths against the fake PostgREST client.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from clients.cuqa_client import CUQAError
from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from services.github_job_service import process_job, run_pending_jobs
from tests.github.conftest import (HEAD_SHA, INSTALLATION_ID, INSTALLATION_TOKEN,
                                   WEBHOOK_SECRET, make_response, pr_payload)

NEW_SHA = "b" * 40


def run_worker(app, app_service):
    with app.app_context():
        return run_pending_jobs(app=app_service, worker_id="test-worker")


def get_job(app, job_id):
    with app.app_context():
        return jobs.get_job(job_id)


def mapping_for(app, sha=HEAD_SHA, number=7):
    with app.app_context():
        return prs.get_mapping("octo-org/demo", number, sha)


def workflow(app, wf_id):
    with app.app_context():
        from db.workflow_repository import get_audit_logs, get_workflow, parse_json_field
        wf = get_workflow(wf_id)
        return wf, parse_json_field(wf, "smells_json"), parse_json_field(wf, "cuqa_report_json"), \
            get_audit_logs(wf_id)


def accept(post_webhook, **payload_kwargs):
    response = post_webhook(payload=pr_payload(**payload_kwargs))
    assert response.status_code == 202, response.get_data(as_text=True)
    return response.get_json()["job_id"]


def final_check(fake_github, check_id=5001):
    """The PATCH that completed the Check Run."""
    return next(u for u in fake_github.check_runs[check_id] if u.get("status") == "completed")


def make_due(app, job_id):
    with app.app_context():
        jobs.update_job(job_id, next_attempt_at=jobs.stamp())


# ── Acceptance path ─────────────────────────────────────────────────────────

def test_pr_opened_publishes_real_cuqa_findings(app, post_webhook, app_service, fake_github,
                                                fake_cuqa):
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "completed")]

    # Exactly the PR head commit was downloaded and handed to CUQA.
    assert fake_github.downloads == [HEAD_SHA]
    [cuqa_call] = fake_cuqa.calls
    assert cuqa_call["filename"] == f"octo-org-demo-{HEAD_SHA[:7]}.zip"
    assert all(m.startswith(f"octo-org-demo-{HEAD_SHA[:7]}/") for m in cuqa_call["members"])

    created = fake_github.check_runs[5001][0]
    assert created["head_sha"] == HEAD_SHA and created["status"] == "queued"
    statuses = [u.get("status") for u in fake_github.check_runs[5001]]
    assert statuses[:3] == ["queued", "in_progress", "completed"]

    final = final_check(fake_github)
    # Review is possible from the PR itself (commands), so action is required;
    # with no DIWO_FRONTEND_URL the details link is the PR conversation.
    assert final["conclusion"] == "action_required"
    assert final["details_url"] == "https://github.com/octo-org/demo/pull/7"
    output = final["output"]
    assert output["title"] == "3 code smells in 2 files — developer review required"
    summary = output["summary"]
    assert "octo-org/demo" in summary and "#7" in summary and HEAD_SHA in summary
    assert "| Detected code smells | 3 |" in summary
    assert "| Affected files | 2 |" in summary
    assert "high 1 · medium 1 · low 1" in summary
    assert "Analysis completed — developer review required" in summary
    assert "No code has been changed" in summary
    assert "LongMethod" in output["text"] and "src/util.py" in output["text"]

    # Two smells have usable lines; the third (line 0) stays in the text only.
    annotations = output["annotations"]
    assert [(a["path"], a["start_line"], a["end_line"], a["annotation_level"]) for a in annotations] \
        == [("src/app.py", 1, 61, "warning"), ("src/util.py", 1, 2, "warning")]

    mapping = mapping_for(app)
    assert mapping["analysis_status"] == "completed"
    assert mapping["smell_count"] == 3
    assert mapping["check_run_id"] == 5001
    assert mapping["installation_id"] == INSTALLATION_ID

    wf, smells, report, logs = workflow(app, mapping["workflow_id"])
    assert wf["status"] == "smell_review"
    assert wf["selected_smells_json"] is None, "no smell may be auto-selected"
    assert wf["plan_json"] is None, "no planning may start"
    assert wf["target"] == f"octo-org/demo#7@{HEAD_SHA[:7]}"
    assert wf["language"] == "python"
    assert [s["type"] for s in smells] == ["LongMethod", "MagicNumber", "DeadCode"]
    assert smells[0]["location"] == {"file": "src/app.py", "class": "app",
                                     "method": "long_function", "lines": [1, 61]}
    assert report["snapshot"]["head_sha"] == HEAD_SHA
    assert report["github"] == {"repository": "octo-org/demo", "pr_number": 7, "head_sha": HEAD_SHA}
    actions = [entry["action"] for entry in logs]
    assert {"workflow_started", "github_pr_linked", "cuqa_report_ingested"} <= set(actions)

    assert get_job(app, job_id)["status"] == "completed"


def test_configured_frontend_url_links_the_review(app, post_webhook, app_service, fake_github,
                                                  fake_cuqa, monkeypatch):
    monkeypatch.setenv("DIWO_FRONTEND_URL", "https://refactoriq.example.org/")
    accept(post_webhook)
    run_worker(app, app_service)

    wf_id = mapping_for(app)["workflow_id"]
    final = final_check(fake_github)
    assert final["conclusion"] == "action_required"
    assert final["details_url"] == f"https://refactoriq.example.org/?diwo_workflow={wf_id}"
    assert f"(https://refactoriq.example.org/?diwo_workflow={wf_id})" in final["output"]["summary"]


def test_frontend_url_is_never_guessed(app, post_webhook, app_service, fake_github, fake_cuqa,
                                       monkeypatch):
    monkeypatch.setenv("DIWO_FRONTEND_URL", "not-a-url")
    accept(post_webhook)
    run_worker(app, app_service)
    assert final_check(fake_github)["details_url"] == "https://github.com/octo-org/demo/pull/7"


def test_synchronize_creates_a_separate_commit_specific_workflow(app, post_webhook, app_service,
                                                                 fake_github, fake_cuqa):
    accept(post_webhook)
    run_worker(app, app_service)
    fake_github.pr_heads[("octo-org/demo", 7)] = NEW_SHA
    accept(post_webhook, action="synchronize", sha=NEW_SHA)
    run_worker(app, app_service)

    first, second = mapping_for(app), mapping_for(app, NEW_SHA)
    assert first["workflow_id"] != second["workflow_id"]
    assert (first["head_sha"], second["head_sha"]) == (HEAD_SHA, NEW_SHA)
    assert fake_github.downloads == [HEAD_SHA, NEW_SHA]
    # The second Check Run is attached to the new head.
    assert fake_github.check_runs[5002][0]["head_sha"] == NEW_SHA


def test_same_commit_redelivered_reuses_its_workflow(app, post_webhook, app_service, fake_github,
                                                     fake_cuqa):
    accept(post_webhook, action="opened")
    run_worker(app, app_service)
    accept(post_webhook, action="reopened")              # new delivery, same commit
    run_worker(app, app_service)

    assert len(fake_cuqa.calls) == 1, "an analysed commit is not analysed again"
    with app.app_context():
        from db.workflow_repository import list_workflows
        assert len(list_workflows()) == 1
    assert final_check(fake_github, 5002)["output"]["title"].startswith("3 code smells")


def test_each_analysis_uses_its_own_temporary_workspace(app, post_webhook, app_service,
                                                        fake_github, fake_cuqa):
    import os
    accept(post_webhook, number=1, sha="1" * 40)
    accept(post_webhook, number=2, sha="2" * 40)
    fake_github.pr_heads.update({("octo-org/demo", 1): "1" * 40, ("octo-org/demo", 2): "2" * 40})
    run_worker(app, app_service)

    paths = [c["zip_path"] for c in fake_cuqa.calls]
    assert len(paths) == 2 and paths[0] != paths[1]
    assert os.path.dirname(paths[0]) != os.path.dirname(paths[1])
    assert not any(os.path.exists(os.path.dirname(p)) for p in paths), "workspaces are removed"


# ── CUQA results and failures ───────────────────────────────────────────────

def test_empty_cuqa_results_create_no_workflow(app, post_webhook, app_service, fake_github,
                                               fake_cuqa):
    fake_cuqa.files = []
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "completed")]

    mapping = mapping_for(app)
    assert (mapping["analysis_status"], mapping["workflow_id"]) == ("no_smells", None)
    final = final_check(fake_github)
    assert final["conclusion"] == "neutral"
    assert final["output"]["title"] == "No code smells detected"
    assert "annotations" not in final["output"]
    with app.app_context():
        from db.workflow_repository import list_workflows
        assert list_workflows() == []


def test_cuqa_outage_is_retried_then_reported_as_failed(app, post_webhook, app_service,
                                                         fake_github, fake_cuqa, monkeypatch):
    monkeypatch.setenv("GITHUB_JOB_MAX_ATTEMPTS", "2")
    fake_cuqa.error = CUQAError("CUQA agent is not reachable", status=503)
    job_id = accept(post_webhook)

    assert run_worker(app, app_service) == [(job_id, "queued")]
    retrying = fake_github.check_runs[5001][-1]
    assert retrying["status"] == "in_progress"
    assert retrying["output"]["title"] == "Analysis delayed — retrying"
    assert mapping_for(app)["analysis_status"] == "pending"

    make_due(app, job_id)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    final = final_check(fake_github)
    assert final["conclusion"] == "neutral"
    assert final["output"]["title"] == "Analysis failed"
    assert "not reachable" in final["output"]["summary"]
    mapping = mapping_for(app)
    assert mapping["analysis_status"] == "failed"
    assert "CUQA analysis failed" in mapping["error"]
    assert mapping["workflow_id"] is None, "no findings are invented on failure"


def test_cuqa_without_isolated_endpoint_fails_clearly(app, post_webhook, app_service,
                                                      fake_github, fake_cuqa):
    fake_cuqa.error = CUQAError("Not Found", status=404)
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    assert "/api/analyze-snapshot" in get_job(app, job_id)["last_error"]
    assert final_check(fake_github)["output"]["title"] == "Analysis failed"


def test_shared_workspace_fallback_is_opt_in_and_verified(app, post_webhook, app_service,
                                                          fake_github, fake_cuqa, monkeypatch):
    from services import github_pr_pipeline
    from tests.github.conftest import cuqa_payload

    monkeypatch.setenv("GITHUB_CUQA_ALLOW_SHARED_WORKSPACE", "true")
    fake_cuqa.error = CUQAError("Not Found", status=404)
    uploaded = []
    monkeypatch.setattr(github_pr_pipeline, "upload_workspace_zip",
                        lambda path, name, timeout=300: uploaded.append(name) or {})

    def quality_report(timeout=120):
        payload = cuqa_payload()
        payload["report"]["repo_name"] = uploaded[-1][:-4]      # what CUQA names it
        return payload
    monkeypatch.setattr(github_pr_pipeline, "fetch_quality_report", quality_report)

    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "completed")]
    assert uploaded == [f"diwo-pr-{job_id}.zip"]


def test_shared_workspace_report_for_another_repo_is_discarded(app, post_webhook, app_service,
                                                               fake_github, fake_cuqa, monkeypatch):
    from services import github_pr_pipeline
    from tests.github.conftest import cuqa_payload

    monkeypatch.setenv("GITHUB_CUQA_ALLOW_SHARED_WORKSPACE", "true")
    fake_cuqa.error = CUQAError("Not Found", status=404)
    monkeypatch.setattr(github_pr_pipeline, "upload_workspace_zip", lambda *a, **k: {})
    monkeypatch.setattr(github_pr_pipeline, "fetch_quality_report",
                        lambda timeout=120: cuqa_payload())    # repo_name: someone else's

    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "queued")]
    assert "shared workspace changed" in get_job(app, job_id)["last_error"]
    assert mapping_for(app)["workflow_id"] is None


def test_snapshot_for_a_different_commit_is_rejected(app, post_webhook, app_service, fake_github,
                                                     fake_cuqa):
    from tests.github.conftest import make_snapshot_zip
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/zipball/([0-9a-f]+)",
                   lambda call: make_response(200, raw=make_snapshot_zip(sha="c" * 40)))
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    assert "does not match commit" in get_job(app, job_id)["last_error"]
    assert fake_cuqa.calls == [], "CUQA never sees an unverified snapshot"


# ── GitHub failures ─────────────────────────────────────────────────────────

def test_authentication_failure_fails_the_job(app, post_webhook, app_service, fake_github,
                                              fake_cuqa):
    fake_github.on("POST", r"/app/installations/\d+/access_tokens",
                   lambda call: make_response(401, {"message": "Bad credentials"}))
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    assert "HTTP 401" in get_job(app, job_id)["last_error"]
    assert fake_github.check_runs == {} and fake_cuqa.calls == []


def test_failed_check_run_creation_is_recorded(app, post_webhook, app_service, fake_github,
                                               fake_cuqa):
    fake_github.on("POST", r"/repos/[^/]+/[^/]+/check-runs",
                   lambda call: make_response(422, {"message": "No commit found for SHA"}))
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    job = get_job(app, job_id)
    assert "Check Run creation failed: HTTP 422" in job["last_error"]
    assert job["check_run_id"] is None


def test_snapshot_download_failure_is_retried(app, post_webhook, app_service, fake_github,
                                              fake_cuqa):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/zipball/([0-9a-f]+)",
                   lambda call: make_response(502, {"message": "Bad Gateway"}))
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "queued")]
    assert "PR snapshot download failed: HTTP 502" in get_job(app, job_id)["last_error"]


def test_oversized_snapshot_is_refused(app, post_webhook, app_service, fake_github, fake_cuqa,
                                       monkeypatch):
    from config import github_snapshot_max_bytes  # noqa: F401 - documents the knob
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/zipball/([0-9a-f]+)",
                   lambda call: make_response(200, raw=b"x" * 2048,
                                              headers={"Content-Length": str(200 * 1024 * 1024)}))
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "failed")]
    assert "exceeds" in get_job(app, job_id)["last_error"]


def test_publish_failure_does_not_rerun_cuqa(app, post_webhook, app_service, fake_github,
                                             fake_cuqa):
    failures = [make_response(503, {"message": "Unavailable"})]
    original = fake_github._update_check

    def patch(call):
        if call["json"].get("status") == "completed" and failures:
            return failures.pop()
        return original(call)
    fake_github.on("PATCH", r"/repos/[^/]+/[^/]+/check-runs/\d+", patch)

    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "queued")]
    assert mapping_for(app)["analysis_status"] == "completed"

    make_due(app, job_id)
    assert run_worker(app, app_service) == [(job_id, "completed")]
    assert len(fake_cuqa.calls) == 1
    assert list(fake_github.check_runs) == [5001], "a retry must not create a second Check Run"


def test_database_error_in_the_worker_is_retried(app, post_webhook, app_service, fake_github,
                                                 fake_cuqa, monkeypatch):
    def broken(job):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(prs, "get_or_create_mapping", broken)
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "queued")]
    assert "RuntimeError" in get_job(app, job_id)["last_error"]


# ── Superseded commits and forks ────────────────────────────────────────────

def test_older_commit_in_progress_is_superseded_by_a_queued_newer_one(app, post_webhook,
                                                                      app_service, fake_github,
                                                                      fake_cuqa):
    older = accept(post_webhook, sha="1" * 40)
    with app.app_context():
        claimed = jobs.claim_next_job("w1")
    newer = accept(post_webhook, action="synchronize", sha="2" * 40)
    fake_github.pr_heads[("octo-org/demo", 7)] = "2" * 40

    with app.app_context():
        assert process_job(claimed, app_service) == "superseded"
    assert get_job(app, older)["superseded_by"] == newer
    assert fake_cuqa.calls == []

    assert run_worker(app, app_service) == [(newer, "completed")]
    assert fake_github.downloads == ["2" * 40]


def test_pr_head_moving_during_analysis_never_publishes_stale_results(app, post_webhook,
                                                                      app_service, fake_github,
                                                                      fake_cuqa, monkeypatch):
    from services import github_pr_pipeline

    def moves_head(*args, **kwargs):
        fake_github.pr_heads[("octo-org/demo", 7)] = NEW_SHA     # pushed mid-analysis
        return fake_cuqa(*args, **kwargs)
    monkeypatch.setattr(github_pr_pipeline, "analyze_snapshot", moves_head)

    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "superseded")]
    final = final_check(fake_github)
    assert final["conclusion"] == "cancelled"
    assert final["output"]["title"] == "Superseded by a newer commit"
    assert NEW_SHA[:7] in final["output"]["summary"]
    assert "annotations" not in final["output"]
    assert mapping_for(app)["analysis_status"] == "superseded"


def test_pr_already_moved_on_is_not_analysed(app, post_webhook, app_service, fake_github,
                                             fake_cuqa):
    fake_github.pr_heads[("octo-org/demo", 7)] = NEW_SHA
    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "superseded")]
    assert fake_github.downloads == [] and fake_cuqa.calls == []
    assert get_job(app, job_id)["superseded_by"] == f"head:{NEW_SHA}"


def test_fork_prs_are_skipped_explicitly(app, post_webhook, app_service, fake_github, fake_cuqa):
    job_id = accept(post_webhook, head_repo="someone/demo-fork")
    assert run_worker(app, app_service) == [(job_id, "completed")]
    final = final_check(fake_github)
    assert final["conclusion"] == "skipped"
    assert final["output"]["title"] == "Fork pull requests are not analysed yet"
    assert fake_github.downloads == [] and fake_cuqa.calls == []
    assert mapping_for(app)["analysis_status"] == "skipped_fork"


# ── Queue mechanics ─────────────────────────────────────────────────────────

def test_worker_with_nothing_queued_does_nothing(app, app_service, fake_github):
    assert run_worker(app, app_service) == []
    assert fake_github.calls == []


def test_disabled_integration_leaves_jobs_queued(app, post_webhook, app_service, fake_github,
                                                 monkeypatch):
    job_id = accept(post_webhook)
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "false")
    assert run_worker(app, app_service) == []
    assert get_job(app, job_id)["status"] == "queued"
    assert fake_github.calls == []


def test_missing_private_key_leaves_jobs_queued(app, post_webhook, github_env, monkeypatch):
    job_id = accept(post_webhook)
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PEM", "")
    with app.app_context():
        assert run_pending_jobs(worker_id="test-worker") == []
    assert get_job(app, job_id)["status"] == "queued"


def test_a_job_abandoned_by_a_crashed_worker_is_reclaimed(app, post_webhook, app_service,
                                                          fake_github, fake_cuqa):
    job_id = accept(post_webhook)
    with app.app_context():
        assert jobs.claim_next_job("crashed-worker")["id"] == job_id
        assert jobs.claim_next_job("other-worker") is None
        jobs.update_job(job_id, locked_at=jobs.stamp(
            datetime.now(timezone.utc) - timedelta(hours=2)))

    assert run_worker(app, app_service) == [(job_id, "completed")]
    assert get_job(app, job_id)["attempts"] == 2


def test_an_abandoned_job_with_no_attempts_left_is_failed(app, post_webhook, app_service,
                                                          monkeypatch):
    monkeypatch.setenv("GITHUB_JOB_MAX_ATTEMPTS", "1")
    job_id = accept(post_webhook)
    with app.app_context():
        jobs.claim_next_job("crashed-worker")
        jobs.update_job(job_id, locked_at=jobs.stamp(
            datetime.now(timezone.utc) - timedelta(hours=2)))

    assert run_worker(app, app_service) == []
    job = get_job(app, job_id)
    assert job["status"] == "failed" and "lock timeout" in job["last_error"]


def test_crash_after_workflow_creation_does_not_duplicate_it(app, post_webhook, app_service,
                                                             fake_github, fake_cuqa, monkeypatch):
    """The workflow id is recorded before the workflow is created."""
    from services import github_pr_pipeline
    real_update = prs.update_mapping
    crashed = []

    def crash_once(row_id, **fields):
        if fields.get("analysis_status") == "completed" and not crashed:
            crashed.append(True)
            raise RuntimeError("worker died")
        return real_update(row_id, **fields)
    monkeypatch.setattr(github_pr_pipeline.prs, "update_mapping", crash_once)

    job_id = accept(post_webhook)
    assert run_worker(app, app_service) == [(job_id, "queued")]
    make_due(app, job_id)
    assert run_worker(app, app_service) == [(job_id, "completed")]
    with app.app_context():
        from db.workflow_repository import list_workflows
        assert len(list_workflows()) == 1


# ── Log hygiene ─────────────────────────────────────────────────────────────

def test_no_secrets_appear_in_logs(app, post_webhook, app_service, fake_github, fake_cuqa, caplog,
                                   capsys, private_key_pem):
    caplog.set_level(logging.DEBUG)
    accept(post_webhook)
    run_worker(app, app_service)
    fake_cuqa.error = CUQAError("CUQA agent is not reachable", status=503)
    accept(post_webhook, number=8, sha="9" * 40)
    fake_github.pr_heads[("octo-org/demo", 8)] = "9" * 40
    run_worker(app, app_service)

    app_jwt = next(c["headers"]["Authorization"] for c in fake_github.calls
                   if c["path"].endswith("/access_tokens")).split(" ", 1)[1]
    captured = capsys.readouterr()
    output = caplog.text + captured.out + captured.err
    assert "Check Run 5001 published (completed)" in output
    for secret in (INSTALLATION_TOKEN, WEBHOOK_SECRET, app_jwt, private_key_pem.splitlines()[1]):
        assert secret not in output


# ── The same paths on the Supabase backend ──────────────────────────────────

class TestOnSupabase:

    def test_end_to_end(self, app, post_webhook, app_service, fake_github, fake_cuqa,
                        supabase_backend):
        job_id = accept(post_webhook)
        assert run_worker(app, app_service) == [(job_id, "completed")]

        [job] = supabase_backend.tables["github_analysis_jobs"]
        assert job["status"] == "completed" and job["check_run_id"] == 5001
        [mapping] = supabase_backend.tables["github_pr_workflows"]
        assert mapping["analysis_status"] == "completed"
        [wf] = supabase_backend.tables["workflows"]
        assert wf["id"] == mapping["workflow_id"] and wf["status"] == "smell_review"
        assert len(wf["smells_json"]) == 3

    def test_duplicate_delivery_and_reuse(self, app, post_webhook, app_service, fake_github,
                                          fake_cuqa, supabase_backend):
        first = post_webhook(delivery="supa-dup")
        second = post_webhook(delivery="supa-dup")
        assert (first.status_code, second.status_code) == (202, 200)
        assert len(supabase_backend.tables["github_analysis_jobs"]) == 1

        run_worker(app, app_service)
        accept(post_webhook, action="reopened")
        run_worker(app, app_service)
        assert len(fake_cuqa.calls) == 1
        assert len(supabase_backend.tables["workflows"]) == 1

    def test_superseded_and_failure_states(self, app, post_webhook, app_service, fake_github,
                                           fake_cuqa, supabase_backend, monkeypatch):
        monkeypatch.setenv("GITHUB_JOB_MAX_ATTEMPTS", "1")
        accept(post_webhook, sha="1" * 40)
        accept(post_webhook, action="synchronize", sha="2" * 40)
        fake_github.pr_heads[("octo-org/demo", 7)] = "2" * 40
        fake_cuqa.error = CUQAError("down", status=503)
        run_worker(app, app_service)

        statuses = {j["head_sha"][:1]: j["status"]
                    for j in supabase_backend.tables["github_analysis_jobs"]}
        assert statuses == {"1": "superseded", "2": "failed"}
        [mapping] = supabase_backend.tables["github_pr_workflows"]
        assert (mapping["head_sha"], mapping["analysis_status"]) == ("2" * 40, "failed")
