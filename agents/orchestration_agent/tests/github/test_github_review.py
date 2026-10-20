"""
Milestone 4 — developer review and RDP planning of GitHub-linked workflows
==========================================================================
A workflow created by the PR pipeline is reviewed through the ordinary DIWO
endpoints, with four extra guarantees:

  * only an authorized reviewer of THAT repository can decide;
  * only while the analysed commit is still the PR head;
  * RDP receives only the selected smells, plus source read from GitHub at the
    analysed commit — nothing ever comes from CUQA's shared workspace;
  * approval records the decision and stops: no transformation, simulated or
    real, and the Check Run says what happened without claiming any change.

GitHub, CUQA and RDP are faked; nothing touches the network.
"""

import time

import jwt
import pytest

from db import github_pr_workflow_repository as prs
from services import access_service, github_review_service
from services.github_job_service import run_pending_jobs
from tests.github.conftest import HEAD_SHA, SNAPSHOT_FILES, make_response, pr_payload

JWT_SECRET = "test-supabase-jwt-secret-with-enough-length"
REVIEWER = "dev@example.org"
SMELL_IDS = ["src/app.py:1:0", "src/app.py:0:1", "src/util.py:1:0"]


def bearer(email=REVIEWER):
    now = int(time.time())
    token = jwt.encode({"sub": f"user-{email}", "email": email, "iat": now, "exp": now + 3600},
                       JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def review_env(monkeypatch, github_env):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", f"octo-org/demo={REVIEWER}")
    monkeypatch.setenv("DIWO_FRONTEND_URL", "https://refactoriq.example.org")
    access_service.reset_cache()
    github_review_service.reset_permission_cache()


@pytest.fixture
def no_shared_workspace(monkeypatch):
    """Fail the test if anything reads CUQA's (or SCTVA's) shared workspace."""
    from clients import cuqa_client
    from services import source_service, transformation_service
    touched = []

    def forbidden(name):
        def _fail(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"{name} read the shared workspace")
        return _fail

    for module, name in ((cuqa_client, "fetch_quality_report"), (cuqa_client, "fetch_source_files"),
                         (cuqa_client, "fetch_project_structure"),
                         (source_service, "fetch_workspace_sources"),
                         (transformation_service, "fetch_workspace_sources")):
        monkeypatch.setattr(module, name, forbidden(name))
    return touched


@pytest.fixture
def linked(app, post_webhook, app_service, fake_github, fake_cuqa, monkeypatch):
    """A workflow created by the real PR pipeline, at smell_review."""
    from services import github_job_service
    monkeypatch.setattr(github_job_service, "shared_app_service", lambda: app_service)
    assert post_webhook(payload=pr_payload()).status_code == 202
    with app.app_context():
        assert run_pending_jobs(app=app_service, worker_id="t")[0][1] == "completed"
        mapping = prs.get_mapping("octo-org/demo", 7, HEAD_SHA)
    return mapping["workflow_id"]


def check_updates(fake_github, check_id=5001):
    return fake_github.check_runs[check_id]


def select(client, wf_id, ids=SMELL_IDS[:2], headers=None):
    return client.post(f"/api/workflows/{wf_id}/select-smells", json={"selected_ids": ids},
                       headers=bearer() if headers is None else headers)


def approve_all(client, wf_id, plan):
    decisions = {str(s["step_id"]): "approve" for s in plan["steps"]}
    return client.post(f"/api/workflows/{wf_id}/plan-decision",
                       json={"decision": "approve", "decisions": decisions,
                             "feedback": {"reason": "Looks right"}},
                       headers=bearer())


def workflow_row(app, wf_id):
    with app.app_context():
        from db.workflow_repository import get_audit_logs, get_workflow, parse_json_field
        wf = get_workflow(wf_id)
        return wf, parse_json_field, get_audit_logs(wf_id)


# ── Smell selection → RDP ───────────────────────────────────────────────────

def test_only_approved_smells_and_commit_source_reach_rdp(app, client, linked, stub_rdp_plan,
                                                          fake_github, no_shared_workspace):
    response = select(client, linked, ids=["src/app.py:1:0"])
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["status"] == "plan_approval"
    assert body["plan_source"] == "rdp_agent"

    [sent] = stub_rdp_plan
    smells_sent = [s for f in sent["files"] for s in f["code_smells"]]
    assert [(s["type"], s["line"]) for s in smells_sent] == [("LongMethod", 1)]
    assert [f["relative_path"] for f in sent["files"]] == ["src/app.py"]

    # Source attached for RDP comes from GitHub at the analysed commit.
    assert [s["file_path"] for s in sent["source_files"]] == ["src/app.py"]
    assert sent["source_files"][0]["source_code"] == SNAPSHOT_FILES["src/app.py"]
    assert sent["source_files"][0]["source_mode"] == "github_commit"
    reads = [c["path"] for c in fake_github.calls if "/contents/" in c["path"]]
    assert reads == [f"/repos/octo-org/demo/contents/src/app.py?ref={HEAD_SHA}"]
    assert no_shared_workspace == []


def test_plan_ready_is_published_on_the_check_run(app, client, linked, stub_rdp_plan, fake_github):
    plan = select(client, linked).get_json()["plan"]
    update = check_updates(fake_github)[-1]
    assert update["conclusion"] == "action_required"
    assert update["details_url"] == f"https://refactoriq.example.org/?diwo_workflow={linked}"
    output = update["output"]
    assert output["title"] == f"Refactoring plan ready — awaiting developer approval " \
                              f"({len(plan['steps'])} steps)"
    assert "| Plan source | RDP agent |" in output["summary"]
    assert "| Smells selected by developer | 2 (excluded 1) |" in output["summary"]
    assert REVIEWER in output["summary"]
    assert "Extract Method" in output["text"] and "pending" in output["text"]
    assert "No code has been changed" in output["summary"]

    with app.app_context():
        mapping = prs.get_mapping_for_workflow(linked)
    assert (mapping["review_status"], mapping["reviewed_by"]) == ("plan_ready", REVIEWER)


def test_rdp_outage_is_reported_as_the_fallback_planner(client, linked, fake_github):
    # No stub_rdp_plan: the suite's default RDP is unreachable.
    body = select(client, linked).get_json()
    assert body["plan_source"] == "diwo_local_fallback"
    summary = check_updates(fake_github)[-1]["output"]["summary"]
    assert "DIWO offline fallback planner — the RDP agent was unavailable" in summary


def test_source_read_failure_never_falls_back_to_the_workspace(client, linked, stub_rdp_plan,
                                                                fake_github, no_shared_workspace):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/contents/(.+)\?ref=([0-9a-f]+)",
                   lambda call: make_response(502, {"message": "Bad Gateway"}))
    body = select(client, linked).get_json()
    assert body["status"] == "plan_approval"
    assert "source_files" not in stub_rdp_plan[0]
    assert "could not be read from GitHub" in body["plan_warning"]
    assert no_shared_workspace == []


def test_source_attachment_can_be_disabled(client, linked, stub_rdp_plan, monkeypatch):
    monkeypatch.setenv("GITHUB_RDP_ATTACH_SOURCES", "false")
    select(client, linked)
    assert "source_files" not in stub_rdp_plan[0]


# ── Plan decisions ──────────────────────────────────────────────────────────

def test_approval_is_recorded_without_any_transformation(app, client, linked, stub_rdp_plan,
                                                         fake_github, no_shared_workspace):
    plan = select(client, linked).get_json()["plan"]
    response = approve_all(client, linked, plan)
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "plan_approved"
    assert body["transformation_result"] is None
    assert "no code has been changed" in body["message"].lower()

    wf, parse, logs = workflow_row(app, linked)
    assert wf["status"] == "plan_approved"
    assert wf["transformation_result_json"] is None and wf["metrics_after_json"] is None
    actions = {(entry["action"], entry["actor"]) for entry in logs}
    assert ("plan_approved", f"developer:{REVIEWER}") in actions
    assert ("smells_selected", f"developer:{REVIEWER}") in actions
    assert ("plan_awaiting_transformation", "system") in actions
    assert not any(a == "transformation_completed" for a, _ in actions)

    final = check_updates(fake_github)[-1]
    assert final["conclusion"] == "neutral"
    assert final["output"]["title"] == "Refactoring plan approved by developer — not yet applied"
    assert "transformation has not been run" in final["output"]["summary"]
    assert "approved" in final["output"]["text"]
    assert no_shared_workspace == []


def test_feedback_rows_are_persisted(app, client, linked, stub_rdp_plan):
    plan = select(client, linked).get_json()["plan"]
    approve_all(client, linked, plan)
    with app.app_context():
        from db.database import get_db
        rows = get_db().execute(
            "SELECT action FROM feedback_entries WHERE workflow_id=?", (linked,)).fetchall()
    actions = [r[0] for r in rows]
    assert "smell_excluded" in actions and "plan_approved" in actions
    assert "plan_step_accepted" in actions


def test_rejection_is_recorded_and_published(app, client, linked, stub_rdp_plan, fake_github):
    select(client, linked)
    response = client.post(f"/api/workflows/{linked}/plan-decision",
                           json={"decision": "reject", "feedback": {"reason": "Too risky now"}},
                           headers=bearer())
    assert response.get_json()["status"] == "rolled_back"
    output = check_updates(fake_github)[-1]["output"]
    assert output["title"] == "Refactoring plan rejected by developer"
    assert "Too risky now" in output["summary"]


def test_modify_keeps_the_review_open(client, linked, stub_rdp_plan, fake_github):
    plan = select(client, linked).get_json()["plan"]
    decisions = {str(s["step_id"]): "reject" for s in plan["steps"]}
    decisions[str(plan["steps"][0]["step_id"])] = "approve"
    response = client.post(f"/api/workflows/{linked}/plan-decision",
                           json={"decision": "modify", "decisions": decisions}, headers=bearer())
    assert response.get_json()["status"] == "plan_approval"
    update = check_updates(fake_github)[-1]
    assert update["conclusion"] == "action_required"
    assert "| Approved / rejected steps | 1 /" in update["output"]["summary"]


def test_an_approved_plan_can_be_reopened_for_review(client, linked, stub_rdp_plan, fake_github):
    plan = select(client, linked).get_json()["plan"]
    approve_all(client, linked, plan)
    response = client.post(f"/api/workflows/{linked}/reset-to-plan-approval", json={},
                           headers=bearer())
    assert response.get_json()["status"] == "plan_approval"
    assert check_updates(fake_github)[-1]["output"]["title"].startswith("Refactoring plan ready")


@pytest.mark.parametrize("method,path", [
    ("post", "/api/workflows/{id}/transform"),
    ("post", "/api/workflows/{id}/transformation-decision"),
    ("get", "/api/workflows/{id}/refactored-archive"),
])
def test_workspace_reading_stages_are_refused(client, linked, stub_rdp_plan, method, path,
                                              no_shared_workspace):
    response = getattr(client, method)(path.format(id=linked), json={}, headers=bearer())
    assert response.status_code == 409
    assert "shared workspace" in response.get_json()["error"]
    assert no_shared_workspace == []


def test_check_run_failure_does_not_fail_the_decision(client, linked, stub_rdp_plan, fake_github):
    fake_github.on("PATCH", r"/repos/[^/]+/[^/]+/check-runs/\d+",
                   lambda call: make_response(502, {"message": "Bad Gateway"}))
    body = select(client, linked).get_json()
    assert body["status"] == "plan_approval"
    assert body["github_check_updated"] is False


# ── Repository authorization ────────────────────────────────────────────────

def test_viewing_needs_only_sign_in(client, linked, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    assert client.get(f"/api/workflows/{linked}", headers=bearer("viewer@example.org")).status_code == 200


def test_approvals_need_a_repository_grant(client, linked, stub_rdp_plan, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    response = select(client, linked)
    assert response.status_code == 403
    assert "GITHUB_REPO_REVIEWERS" in response.get_json()["error"]
    assert stub_rdp_plan == [], "RDP is not called for an unauthorized decision"


def test_reviewer_list_is_per_repository(client, linked, stub_rdp_plan):
    assert select(client, linked, headers=bearer("intruder@example.org")).status_code == 403


def test_organisation_wildcard_grants(client, linked, stub_rdp_plan, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "octo-org/*=lead@example.org")
    assert select(client, linked, headers=bearer("lead@example.org")).status_code == 200


@pytest.mark.parametrize("role,status", [("write", 200), ("maintain", 200), ("admin", 200),
                                         ("triage", 403), ("read", 403)])
def test_github_collaborator_permission_is_checked_live(client, linked, stub_rdp_plan,
                                                        fake_github, monkeypatch, role, status):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    monkeypatch.setenv("GITHUB_USER_LOGINS", f"{REVIEWER}=octocat")
    fake_github.on("GET", r"/repos/octo-org/demo/collaborators/octocat/permission",
                   lambda call: make_response(200, {"permission": role, "role_name": role}))
    assert select(client, linked).status_code == status


def test_non_collaborator_is_refused(client, linked, stub_rdp_plan, fake_github, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    monkeypatch.setenv("GITHUB_USER_LOGINS", f"{REVIEWER}=stranger")
    # The fake answers 404 for unknown routes, which GitHub uses for "not a collaborator".
    response = select(client, linked)
    assert response.status_code == 403 and "'none'" in response.get_json()["error"]


def test_permission_check_outage_fails_closed(client, linked, stub_rdp_plan, fake_github,
                                              monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    monkeypatch.setenv("GITHUB_USER_LOGINS", f"{REVIEWER}=octocat")
    fake_github.on("GET", r"/repos/octo-org/demo/collaborators/octocat/permission",
                   lambda call: make_response(500, {"message": "boom"}))
    assert select(client, linked).status_code == 503


def test_authorization_can_be_relaxed_for_local_demos(client, linked, stub_rdp_plan, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO_REVIEWERS", "")
    monkeypatch.setenv("GITHUB_REQUIRE_REPO_AUTHORIZATION", "false")
    assert select(client, linked, headers=bearer("anyone@example.org")).status_code == 200


def test_decisions_still_require_sign_in(client, linked, stub_rdp_plan):
    assert select(client, linked, headers={}).status_code == 401


# ── Stale PR commits ────────────────────────────────────────────────────────

def test_decisions_on_an_outdated_commit_are_refused_after_a_new_push(
        app, client, linked, stub_rdp_plan, post_webhook):
    # The new commit has been delivered but not yet analysed.
    post_webhook(payload=pr_payload(action="synchronize", sha="b" * 40))
    response = select(client, linked)
    assert response.status_code == 409
    assert "newer commit" in response.get_json()["error"]
    assert stub_rdp_plan == []


def test_decisions_refused_when_a_newer_commit_was_analysed(app, client, linked, stub_rdp_plan):
    with app.app_context():
        prs.get_or_create_mapping({
            "id": "ghjob_x", "repo_owner": "octo-org", "repo_name": "demo",
            "repo_full_name": "octo-org/demo", "pr_number": 7, "head_sha": "c" * 40,
            "installation_id": 987654})
    assert select(client, linked).status_code == 409


def test_live_pr_head_is_checked(client, linked, stub_rdp_plan, fake_github):
    fake_github.pr_heads[("octo-org/demo", 7)] = "d" * 40       # webhook missed
    assert select(client, linked).status_code == 409


def test_closed_pr_is_refused(client, linked, stub_rdp_plan, fake_github):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/pulls/(\d+)",
                   lambda call: make_response(200, {"state": "closed", "head": {"sha": HEAD_SHA}}))
    assert select(client, linked).status_code == 409


def test_pr_lookup_outage_fails_closed(client, linked, stub_rdp_plan, fake_github):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/pulls/(\d+)",
                   lambda call: make_response(502, {"message": "Bad Gateway"}))
    assert select(client, linked).status_code == 503


def test_read_only_previews_stay_available_on_an_outdated_commit(client, linked, post_webhook):
    post_webhook(payload=pr_payload(action="synchronize", sha="b" * 40))
    response = client.post(f"/api/workflows/{linked}/smell-selection-pass",
                           json={"selected_ids": SMELL_IDS[:1]}, headers=bearer())
    assert response.status_code == 200


# ── What the frontend reads ─────────────────────────────────────────────────

def test_context_reports_review_state_and_capability(client, linked, stub_rdp_plan):
    select(client, linked)
    body = client.get(f"/api/github/workflows/{linked}", headers=bearer()).get_json()
    github = body["github"]
    assert github["review_status"] == "plan_ready"
    assert github["can_review"] is True and github["authorization"] == "reviewer_list"
    assert body["status"] == "plan_approval" and body["plan"]["steps"]

    viewer = client.get(f"/api/github/workflows/{linked}",
                        headers=bearer("viewer@example.org")).get_json()["github"]
    assert viewer["can_review"] is False and "not a configured reviewer" in viewer["reason"]


# ── Ordinary workflows are unchanged ────────────────────────────────────────

def test_ordinary_workflow_approval_still_runs_its_transformation(client, at_plan_approval):
    plan = client.get(f"/api/workflows/{at_plan_approval}").get_json()["plan"]
    decisions = {str(s["step_id"]): "approve" for s in plan["steps"]}
    body = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                       json={"decision": "approve", "decisions": decisions}).get_json()
    assert body["status"] == "transformation"
