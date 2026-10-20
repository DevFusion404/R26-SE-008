"""
Feature G — safe write-back of accepted, validated changes
==========================================================
Only explicitly accepted, SCTVA-validated changes are committed, only after
an explicit confirmed submit, only as a fast-forward of the analysed commit,
and never twice. The fake GitHub keeps a real branch pointer and refuses
non-fast-forward updates, as GitHub does.
"""

import time

import jwt
import pytest

from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from db import github_review_repository as reviews
from tests.github.conftest import HEAD_SHA, SNAPSHOT_FILES, make_response, pr_payload

JWT_SECRET = "test-supabase-jwt-secret-with-enough-length"


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setenv("GITHUB_PR_TRANSFORMATION_ENABLED", "true")
    monkeypatch.setenv("GITHUB_PR_WRITEBACK_ENABLED", "true")


@pytest.fixture
def accepted(app, linked_pr, command, stub_rdp_plan, fake_sctva):
    """T001 (src/app.py) accepted, T002 (src/util.py) rejected."""
    for line in ("/refactoriq select S001 S003", "/refactoriq generate-plan",
                 "/refactoriq approve-plan P001 P002", "/refactoriq submit-plan",
                 "/refactoriq submit-plan confirm",
                 "/refactoriq accept-transformation T001", "/refactoriq reject-transformation T002"):
        command(line)
    return linked_pr["workflow_id"]


def submit(command):
    command("/refactoriq submit-refactoring")
    command("/refactoriq submit-refactoring confirm")


def rows(app, wf_id):
    with app.app_context():
        return {r["display_id"]: r for r in reviews.list_transformations(wf_id)}


def writeback(app, wf_id):
    with app.app_context():
        return reviews.latest_writeback(wf_id)


def ref_updates(fake_github):
    return [c for c in fake_github.calls if c["method"] == "PATCH" and "/git/refs/" in c["path"]]


# ── The successful path ─────────────────────────────────────────────────────

def test_only_accepted_changes_are_committed_as_a_fast_forward(app, accepted, command, fake_github):
    submit(command)
    [commit_sha] = list(fake_github.commits)
    commit = fake_github.commits[commit_sha]
    assert [p["sha"] for p in commit["parents"]] == [HEAD_SHA]
    [tree] = fake_github.trees.values()
    assert tree["base_tree"] == "basetree"
    assert [e["path"] for e in tree["tree"]] == ["src/app.py"], "rejected T002 is never committed"
    assert fake_github.blobs[tree["tree"][0]["sha"]] == rows(app, accepted)["T001"]["refactored_content"]
    assert [c["json"] for c in ref_updates(fake_github)] == [{"sha": commit_sha, "force": False}]
    assert fake_github.branch["feature"] == commit_sha
    assert "RefactorIQ-Writeback:" in commit["message"] and "Accepted-by: github:alice" in commit["message"]

    t = rows(app, accepted)
    assert (t["T001"]["status"], t["T001"]["applied_commit_sha"]) == ("applied", commit_sha)
    assert t["T002"]["status"] == "rejected" and t["T002"]["applied_commit_sha"] is None
    wb = writeback(app, accepted)
    assert (wb["status"], wb["commit_sha"], wb["branch"]) == ("committed", commit_sha, "feature")
    with app.app_context():
        from db.workflow_repository import get_audit_logs, get_workflow
        assert get_workflow(accepted)["status"] == "completed"
        assert any(e["action"] == "github_writeback_committed" for e in get_audit_logs(accepted))

    final = fake_github.check_runs[5001][-1]
    assert final["output"]["title"] == f"1 accepted change(s) applied in {commit_sha[:7]}"
    assert final["conclusion"] == "neutral" and final["actions"] == []
    assert f"Applied** in commit `{commit_sha}`" in fake_github.last_comment()
    assert "/pull/7/files" in fake_github.last_comment()


def test_submit_needs_an_explicit_confirmation(app, accepted, command, fake_github):
    command("/refactoriq submit-refactoring")
    assert "submit-refactoring confirm" in fake_github.last_comment()
    assert fake_github.commits == {}
    fake_github.permissions["bob"] = "write"
    command("/refactoriq submit-refactoring confirm", login="bob")
    assert fake_github.commits == {}


def test_no_acceptance_means_no_write_back(app, linked_pr, command, stub_rdp_plan, fake_sctva,
                                           fake_github):
    for line in ("/refactoriq select S001", "/refactoriq generate-plan",
                 "/refactoriq approve-plan P001", "/refactoriq submit-plan",
                 "/refactoriq submit-plan confirm"):
        command(line)
    submit(command)
    assert fake_github.commits == {} and ref_updates(fake_github) == []
    assert "No change has been accepted" in fake_github.last_comment()


def test_write_back_is_off_by_default(app, accepted, command, fake_github, monkeypatch):
    monkeypatch.setenv("GITHUB_PR_WRITEBACK_ENABLED", "false")
    submit(command)
    assert fake_github.commits == {}
    assert "GITHUB_PR_WRITEBACK_ENABLED=false" in fake_github.last_comment()


def test_resubmitting_never_creates_a_second_commit(app, accepted, command, fake_github, run_jobs):
    submit(command)
    submit(command)
    with app.app_context():
        from services.github_writeback_service import request_writeback
        mapping = prs.get_mapping_for_workflow(accepted)
        again = request_writeback(mapping, accepted, {"github_login": "alice"})
    assert again["status"] == "committed" and again["duplicate"] is True
    assert len(fake_github.commits) == 1 and len(ref_updates(fake_github)) == 1


# ── Refusals: nothing is written ────────────────────────────────────────────

def _request(app, wf_id, login="alice"):
    with app.app_context():
        from services.github_writeback_service import request_writeback
        return request_writeback(prs.get_mapping_for_workflow(wf_id), wf_id, {"github_login": login})


def test_a_confirm_after_a_new_push_is_refused_at_once(app, accepted, command, fake_github):
    command("/refactoriq submit-refactoring")
    command("/refactoriq submit-refactoring confirm", process=False)
    fake_github.branch["feature"] = "7" * 40            # the developer pushed meanwhile
    command("/refactoriq show-transformations", login="rita")
    assert writeback(app, accepted) is None and fake_github.commits == {}


def test_new_commits_on_the_pr_block_the_write_back(app, accepted, fake_github, run_jobs):
    _request(app, accepted)                             # queued while the PR was current
    fake_github.branch["feature"] = "7" * 40            # ...then the developer pushed
    run_jobs()
    assert fake_github.commits == {} and ref_updates(fake_github) == []
    assert fake_github.branch["feature"] == "7" * 40
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "new commits" in wb["error"]


def test_a_push_racing_the_commit_is_never_overwritten(app, accepted, command, fake_github):
    original = fake_github._update_ref

    def racing(call):
        fake_github.branch["feature"] = "8" * 40       # someone pushes right now
        return original(call)
    fake_github.on("PATCH", r"/repos/([^/]+)/([^/]+)/git/refs/heads/(.+)", racing)
    submit(command)
    assert fake_github.branch["feature"] == "8" * 40
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "nothing was overwritten" in wb["error"]
    assert rows(app, accepted)["T001"]["status"] == "accepted"


def test_protected_branches_are_respected(app, accepted, command, fake_github):
    fake_github.on("PATCH", r"/repos/([^/]+)/([^/]+)/git/refs/heads/(.+)",
                   lambda call: make_response(422, {"message": "Protected branch update failed"}))
    submit(command)
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "protected" in wb["error"]
    assert "Not applied" in fake_github.last_comment()


def test_missing_contents_permission_is_reported(app, accepted, command, fake_github):
    fake_github.on("POST", r"/repos/([^/]+)/([^/]+)/git/blobs",
                   lambda call: make_response(403, {"message": "Resource not accessible by integration"}))
    submit(command)
    assert fake_github.commits == {}
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "Resource not accessible" in wb["error"]
    assert "Not applied" in fake_github.last_comment()


def test_fork_branches_are_not_written(app, accepted, command, fake_github):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/pulls/(\d+)", lambda call: make_response(200, {
        "state": "open", "head": {"sha": HEAD_SHA, "ref": "feature",
                                  "repo": {"full_name": "someone/demo-fork"}}}))
    with app.app_context():
        from services.github_writeback_service import request_writeback
        request_writeback(prs.get_mapping_for_workflow(accepted), accepted, {"github_login": "alice"})
    command("/refactoriq show-transformations", login="rita")
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "fork" in wb["error"]
    assert fake_github.commits == {}


def test_revoked_permission_is_rechecked_before_writing(app, accepted, fake_github, run_jobs):
    from services import github_review_service
    _request(app, accepted)                             # requested while alice could write
    fake_github.permissions["alice"] = "read"           # ...revoked before the worker runs
    github_review_service.reset_permission_cache()
    run_jobs()
    assert fake_github.commits == {}
    wb = writeback(app, accepted)
    assert wb["status"] == "refused" and "'read' access" in wb["error"]


def test_tampered_changes_are_refused(app, accepted, command, fake_github):
    with app.app_context():
        record = reviews.get_transformation(accepted, "T001")
        reviews.update_transformation(record["id"], refactored_content="import os; os.system('x')")
    submit(command)
    assert fake_github.commits == {}
    assert "no longer matches the reviewed change" in fake_github.last_comment()


def test_unvalidated_changes_cannot_reach_the_branch(app, accepted, command, fake_github):
    with app.app_context():
        record = reviews.get_transformation(accepted, "T001")
        reviews.update_transformation(record["id"], validation_passed=0)
    submit(command)
    assert fake_github.commits == {}


# ── Recovery ────────────────────────────────────────────────────────────────

def test_interrupted_update_is_finished_without_a_second_commit(app, accepted, command,
                                                                fake_github, run_jobs):
    original = fake_github._update_ref
    failures = [make_response(502, {"message": "Bad Gateway"})]
    fake_github.on("PATCH", r"/repos/([^/]+)/([^/]+)/git/refs/heads/(.+)",
                   lambda call: failures.pop() if failures else original(call))
    submit(command)
    assert writeback(app, accepted)["status"] == "committing"
    with app.app_context():
        for job in jobs._backend().claimable("9999", "0000"):
            jobs.update_job(job["id"], next_attempt_at=jobs.stamp())
    run_jobs()
    assert len(fake_github.commits) == 1, "the commit made before the failure is reused"
    wb = writeback(app, accepted)
    assert wb["status"] == "committed" and fake_github.branch["feature"] == wb["commit_sha"]


def test_bot_commit_is_analysed_without_starting_another_write_back(app, accepted, command,
                                                                    fake_github, post_webhook,
                                                                    run_jobs):
    submit(command)
    new_sha = writeback(app, accepted)["commit_sha"]
    fake_github.pr_heads[("octo-org/demo", 7)] = new_sha
    assert post_webhook(payload=pr_payload(action="synchronize", sha=new_sha)).status_code == 202
    run_jobs()
    with app.app_context():
        mapping = prs.get_mapping("octo-org/demo", 7, new_sha)
        queued = [j for j in jobs._backend().claimable("9999", "0000")]
    assert mapping["origin_writeback_id"] == writeback(app, accepted)["id"]
    assert queued == [], "nothing is queued automatically after a bot commit"
    assert len(fake_github.commits) == 1
    summary = fake_github.check_runs[max(fake_github.check_runs)][-1]["output"]["summary"]
    assert "created by RefactorIQ write-back" in summary


# ── DIWO side ───────────────────────────────────────────────────────────────

def test_diwo_submit_requires_confirmation_and_a_grant(app, client, accepted, run_jobs,
                                                       fake_github, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "octo-org/demo=dev@example.org")
    from services import access_service
    access_service.reset_cache()
    now = int(time.time())
    headers = {"Authorization": "Bearer " + jwt.encode(
        {"sub": "u", "email": "dev@example.org", "iat": now, "exp": now + 600}, JWT_SECRET,
        algorithm="HS256")}
    url = f"/api/github/workflows/{accepted}/submit-refactoring"
    assert client.post(url, json={}, headers=headers).status_code == 400
    assert client.post(url, json={"confirm": True}).status_code == 401
    response = client.post(url, json={"confirm": True}, headers=headers)
    assert response.status_code == 202
    run_jobs()
    wb = writeback(app, accepted)
    assert wb["status"] == "committed" and wb["requested_by"] == "dev@example.org"


# ── Supabase backend ────────────────────────────────────────────────────────

def test_full_flow_on_supabase(app, post_webhook, app_service, fake_github, fake_cuqa, fake_sctva,
                               stub_rdp_plan, supabase_backend, run_jobs, command, monkeypatch):
    from services import github_job_service, github_review_service
    monkeypatch.setattr(github_job_service, "shared_app_service", lambda: app_service)
    github_review_service.reset_permission_cache()
    fake_github.permissions["alice"] = "write"
    post_webhook(payload=pr_payload())
    run_jobs()
    for line in ("/refactoriq select S001", "/refactoriq generate-plan",
                 "/refactoriq approve-plan P001", "/refactoriq submit-plan",
                 "/refactoriq submit-plan confirm", "/refactoriq accept-transformation T001",
                 "/refactoriq submit-refactoring", "/refactoriq submit-refactoring confirm"):
        command(line)
    [wb] = supabase_backend.tables["github_pr_writebacks"]
    assert wb["status"] == "committed" and len(fake_github.commits) == 1
    [record] = supabase_backend.tables["github_pr_transformations"]
    assert record["status"] == "applied" and record["display_id"] == "T001"
