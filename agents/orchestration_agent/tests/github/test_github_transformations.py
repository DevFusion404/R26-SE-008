"""
Features D, E, F — SCTVA on PR code, previews, and final decisions
==================================================================
After a GitHub-approved plan: SCTVA runs on the exact PR commit (source read
from GitHub, never a shared workspace), only approved steps are sent, real
validation decides what may be accepted, and decisions from GitHub and DIWO
land on the same records. SCTVA, RDP, CUQA and GitHub are faked.
"""

import time

import jwt
import pytest

from clients.sctva_client import SCTVAError
from db import github_pr_workflow_repository as prs
from db import github_review_repository as reviews
from services.github_transformation_service import (DecisionError, build_records, decide)
from tests.github.conftest import HEAD_SHA, SNAPSHOT_FILES, make_response

JWT_SECRET = "test-supabase-jwt-secret-with-enough-length"


@pytest.fixture(autouse=True)
def transformation_on(monkeypatch):
    monkeypatch.setenv("GITHUB_PR_TRANSFORMATION_ENABLED", "true")


@pytest.fixture
def no_shared_workspace(monkeypatch):
    from clients import cuqa_client
    from services import source_service, transformation_service
    touched = []

    def forbid(name):
        def _fail(*a, **k):
            touched.append(name)
            raise AssertionError(f"{name} read a shared workspace")
        return _fail
    for module, name in ((cuqa_client, "fetch_source_files"), (cuqa_client, "fetch_quality_report"),
                         (source_service, "fetch_workspace_sources"),
                         (transformation_service, "fetch_workspace_sources")):
        monkeypatch.setattr(module, name, forbid(name))
    return touched


def approve_plan(command, steps=("P001", "P002"), reject=()):
    command("/refactoriq select S001 S003")
    command("/refactoriq generate-plan")
    command(f"/refactoriq approve-plan {' '.join(steps)}")
    if reject:
        command(f"/refactoriq reject-plan {' '.join(reject)}")
    command("/refactoriq submit-plan")
    command("/refactoriq submit-plan confirm")       # queues and runs the SCTVA job


@pytest.fixture
def transformed(app, linked_pr, command, stub_rdp_plan, fake_sctva):
    approve_plan(command)
    return linked_pr["workflow_id"]


def records(app, wf_id):
    with app.app_context():
        return reviews.list_transformations(wf_id)


def bearer():
    now = int(time.time())
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": "u1", "email": "dev@example.org", "iat": now, "exp": now + 600},
        JWT_SECRET, algorithm="HS256")}


# ── Real transformation on the exact commit ─────────────────────────────────

def test_sctva_receives_the_commit_source_and_only_approved_steps(app, linked_pr, command,
                                                                  stub_rdp_plan, fake_sctva,
                                                                  fake_github, no_shared_workspace):
    approve_plan(command, steps=("P001",), reject=("P002",))
    [request] = fake_sctva.calls
    sent = {f["file_name"]: f["source_code"] for f in request["source_files"]}
    assert sent == {"src/app.py": SNAPSHOT_FILES["src/app.py"]}
    assert {f["source_mode"] for f in request["source_files"]} == {"github_commit"}
    actions = request["refactoring_plan"]["actions"]
    assert len(actions) == 1, "the rejected step P002 is never sent to SCTVA"
    reads = [c["path"] for c in fake_github.calls if "/contents/" in c["path"]]
    assert all(path.endswith(f"?ref={HEAD_SHA}") for path in reads)
    assert no_shared_workspace == []


def test_validated_changes_become_reviewable_records(app, transformed, fake_github):
    rows = records(app, transformed)
    assert [(r["display_id"], r["file_path"], r["status"], r["validation_passed"]) for r in rows] == [
        ("T001", "src/app.py", "proposed", True), ("T002", "src/util.py", "proposed", True)]
    first = rows[0]
    assert first["original_content"] == SNAPSHOT_FILES["src/app.py"]
    assert "x0 = ZERO" in first["refactored_content"]
    assert first["diff"].startswith("--- a/src/app.py\n+++ b/src/app.py\n")
    assert "-    x0 = 0" in first["diff"] and "+    x0 = ZERO" in first["diff"]
    assert first["head_sha"] == HEAD_SHA and first["original_blob_sha"]
    with app.app_context():
        from db.workflow_repository import get_workflow
        assert get_workflow(transformed)["status"] == "transformation"


def test_transformation_report_shows_real_validation_and_diffs(app, transformed, fake_github):
    update = fake_github.check_runs[5001][-1]
    output = update["output"]
    assert update["conclusion"] == "action_required"
    assert output["title"] == "2 validated change(s) proposed — developer acceptance required"
    assert "| T001 | src/app.py | Extract Method | passed | passed | passed | not run | 0.91 | proposed |" \
        in output["summary"]
    assert "Repository test suites: not run" in output["summary"]
    assert "```diff\n--- a/src/app.py" in output["text"]
    assert "+    x0 = ZERO" in output["text"]
    assert [a["identifier"] for a in update["actions"]] == ["accept_validated", "reject_all"]
    assert "/refactoriq accept-transformation T001" in fake_github.last_comment()


@pytest.mark.parametrize("outcome,status,reason", [
    ("behavior_fail", "failed", "validation failed: behavioral"),
    ("missing_behavior", "failed", "required validation unavailable: behavioral"),
    ("rollback", "no_change", "SCTVA rolled the change back"),
    ("unchanged", "no_change", "SCTVA produced no change for this file"),
    ("error", "failed", "SCTVA reported the transformation failed"),
])
def test_unvalidated_results_fail_closed(app, linked_pr, command, stub_rdp_plan, fake_sctva,
                                         outcome, status, reason):
    fake_sctva.outcomes["src/app.py"] = outcome
    approve_plan(command)
    t001 = records(app, linked_pr["workflow_id"])[0]
    assert (t001["status"], t001["validation_passed"], t001["status_reason"]) == (status, False, reason)
    with app.app_context(), pytest.raises(DecisionError, match="cannot be accepted"):
        decide(linked_pr["workflow_id"], {"T001": "accept"}, "github:alice")


def test_sctva_outage_is_retried_then_reported(app, linked_pr, command, stub_rdp_plan, fake_sctva,
                                               fake_github, run_jobs, monkeypatch):
    monkeypatch.setenv("GITHUB_JOB_MAX_ATTEMPTS", "1")
    fake_sctva.error = SCTVAError("SCTVA agent is not reachable", status=503)
    approve_plan(command)
    assert records(app, linked_pr["workflow_id"]) == []
    final = fake_github.check_runs[5001][-1]
    assert final["output"]["title"] == "Transformation failed — nothing was changed"
    with app.app_context():
        from db.workflow_repository import get_workflow
        assert get_workflow(linked_pr["workflow_id"])["status"] == "plan_approved"


def test_unsupported_plan_is_reported_not_applied(app, linked_pr, command, stub_rdp_plan,
                                                  fake_sctva, fake_github):
    fake_sctva.error = SCTVAError("Plan contains no executable action", status=422)
    approve_plan(command)
    assert records(app, linked_pr["workflow_id"]) == []
    assert "Transformation failed" in fake_github.check_runs[5001][-1]["output"]["title"]


def test_transformation_is_off_by_default(app, linked_pr, command, stub_rdp_plan, fake_sctva,
                                          fake_github, monkeypatch):
    monkeypatch.setenv("GITHUB_PR_TRANSFORMATION_ENABLED", "false")
    approve_plan(command)
    assert fake_sctva.calls == []
    assert "GITHUB_PR_TRANSFORMATION_ENABLED=false" in fake_github.last_comment()


def test_stale_commit_blocks_the_transformation(app, linked_pr, command, stub_rdp_plan,
                                                fake_sctva, fake_github, post_webhook):
    from tests.github.conftest import pr_payload
    command("/refactoriq select S001 S003")
    command("/refactoriq generate-plan")
    command("/refactoriq approve-plan P001")
    command("/refactoriq submit-plan")
    # confirm queued, then a new push lands before the worker runs it
    command("/refactoriq submit-plan confirm", process=False)
    fake_github.pr_heads[("octo-org/demo", 7)] = "f" * 40
    post_webhook(payload=pr_payload(action="synchronize", sha="f" * 40))
    command("/refactoriq show-selection")
    assert fake_sctva.calls == []


# ── Final decisions ─────────────────────────────────────────────────────────

def test_accept_and_reject_from_github(app, transformed, command, fake_github):
    command("/refactoriq accept-transformation T001")
    command("/refactoriq reject-transformation T002")
    rows = {r["display_id"]: r for r in records(app, transformed)}
    assert (rows["T001"]["status"], rows["T001"]["decided_by"]) == ("accepted", "github:alice")
    assert rows["T002"]["status"] == "rejected"
    assert "Accepted: T001 · Rejected: —" in fake_github.comments[min(fake_github.comments)][-2]
    assert "Accepted: — · Rejected: T002" in fake_github.last_comment()
    update = fake_github.check_runs[5001][-1]
    assert update["output"]["title"] == "1 change(s) accepted — submit to apply"
    with app.app_context():
        from db.database import get_db
        actions = [r[0] for r in get_db().execute(
            "SELECT action FROM feedback_entries WHERE workflow_id=?", (transformed,)).fetchall()]
    assert "github_transformation_accepted" in actions and "github_transformation_rejected" in actions


def test_accept_all_validated_and_cancel(app, transformed, command):
    command("/refactoriq accept-all-validated")
    assert {r["status"] for r in records(app, transformed)} == {"accepted"}
    command("/refactoriq cancel")
    assert {r["status"] for r in records(app, transformed)} == {"rejected"}


def test_unknown_transformation_ids_change_nothing(app, transformed, command, fake_github):
    command("/refactoriq accept-transformation T001 T009")
    assert {r["status"] for r in records(app, transformed)} == {"proposed"}
    assert "T009 is not a transformation" in fake_github.last_comment()


def test_readers_cannot_decide(app, transformed, command, fake_github):
    command("/refactoriq accept-all-validated", login="rita")
    assert {r["status"] for r in records(app, transformed)} == {"proposed"}
    assert "'read' access" in fake_github.last_comment()


def test_check_run_button_accepts_validated_changes(app, transformed, post_webhook, run_jobs):
    payload = {"action": "requested_action", "requested_action": {"identifier": "accept_validated"},
               "check_run": {"id": 5001, "head_sha": HEAD_SHA, "app": {"id": 123456},
                             "pull_requests": [{"number": 7}]},
               "sender": {"login": "alice", "id": 1, "type": "User"},
               "repository": {"name": "demo", "full_name": "octo-org/demo",
                              "owner": {"login": "octo-org"}},
               "installation": {"id": 987654}}
    assert post_webhook(payload=payload, event="check_run").status_code == 202
    run_jobs()
    assert {r["status"] for r in records(app, transformed)} == {"accepted"}


def test_buttons_of_other_apps_are_ignored(app, transformed, post_webhook):
    payload = {"action": "requested_action", "requested_action": {"identifier": "accept_validated"},
               "check_run": {"id": 1, "head_sha": HEAD_SHA, "app": {"id": 999},
                             "pull_requests": [{"number": 7}]},
               "sender": {"login": "alice", "id": 1, "type": "User"},
               "repository": {"name": "demo", "full_name": "octo-org/demo",
                              "owner": {"login": "octo-org"}},
               "installation": {"id": 987654}}
    assert post_webhook(payload=payload, event="check_run").get_json()["ignored"] is True


def test_coupled_cross_file_changes_go_together():
    plan = {"steps": [{"step_id": 1, "refactoring": "Move Method", "target": {"file": "a.py"}},
                      {"step_id": 2, "refactoring": "Move Method", "target": {"file": "b.py"}}]}
    ok = {"syntax": {"passed": True}, "structural": {"passed": True}, "behavioral": {"passed": True}}
    result = {"files": [{"path": p, "after": "new\n", "success": True, "validation": ok}
                        for p in ("a.py", "b.py")]}
    rows = build_records(result, {"a.py": {"source_code": "old\n"}, "b.py": {"source_code": "old\n"}},
                         plan, {"id": "m", "head_sha": HEAD_SHA}, "wf", "run")
    assert all(r["coupled"] for r in rows)


def test_partial_acceptance_of_coupled_changes_is_refused(app, transformed):
    with app.app_context():
        for row in reviews.list_transformations(transformed):
            reviews.update_transformation(row["id"], coupled=1)
        with pytest.raises(DecisionError, match="must be accepted together"):
            decide(transformed, {"T001": "accept"}, "dev")
        assert decide(transformed, {"T001": "accept", "T002": "accept"}, "dev")["accepted"] == [
            "T001", "T002"]


# ── The same records from DIWO ──────────────────────────────────────────────

@pytest.fixture
def diwo_reviewer(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "octo-org/demo=dev@example.org")
    from services import access_service
    access_service.reset_cache()


def test_diwo_shows_original_refactored_and_diff(client, transformed, diwo_reviewer):
    body = client.get(f"/api/github/workflows/{transformed}/transformations",
                      headers=bearer()).get_json()
    t001 = body["transformations"][0]
    assert t001["id"] == "T001" and t001["original"] == SNAPSHOT_FILES["src/app.py"]
    assert "x0 = ZERO" in t001["refactored"] and t001["diff"].startswith("--- a/src/app.py")
    assert t001["validation"]["checks"]["behavioral"]["passed"] is True
    assert body["transformation_enabled"] is True and body["writeback_enabled"] is False


def test_diwo_decisions_update_the_shared_records(app, client, transformed, diwo_reviewer,
                                                  fake_github):
    response = client.post(f"/api/github/workflows/{transformed}/transformations/decision",
                           json={"decisions": {"T001": "accept", "T002": "reject"}},
                           headers=bearer())
    assert response.status_code == 200
    rows = {r["display_id"]: r for r in records(app, transformed)}
    assert rows["T001"]["decided_by"] == "dev@example.org"
    assert fake_github.check_runs[5001][-1]["output"]["title"] == "1 change(s) accepted — submit to apply"


def test_diwo_decisions_need_sign_in_and_a_grant(client, transformed, diwo_reviewer, monkeypatch):
    url = f"/api/github/workflows/{transformed}/transformations/decision"
    assert client.post(url, json={"decisions": {"T001": "accept"}}).status_code == 401
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    assert client.post(url, json={"decisions": {"T001": "accept"}},
                       headers=bearer()).status_code == 403


def test_patch_export_contains_only_accepted_changes(client, transformed, diwo_reviewer):
    client.post(f"/api/github/workflows/{transformed}/transformations/decision",
                json={"decisions": {"T001": "accept", "T002": "reject"}}, headers=bearer())
    patch = client.get(f"/api/github/workflows/{transformed}/patch", headers=bearer())
    assert patch.status_code == 200 and patch.mimetype == "text/x-diff"
    text = patch.get_data(as_text=True)
    assert "a/src/app.py" in text and "src/util.py" not in text
