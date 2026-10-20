"""
Access to GitHub-linked workflows
=================================
A workflow created from a PR is reachable only by a signed-in DIWO user —
its id, which appears in a public GitHub link, is not a capability. Ordinary
DIWO workflows must behave exactly as before.

Tokens are Supabase-style HS256 JWTs signed with SUPABASE_JWT_SECRET, the
same check user_management performs.
"""

import time

import jwt
import pytest

from db import github_pr_workflow_repository as prs
from services import access_service
from tests.github.conftest import HEAD_SHA, INSTALLATION_ID, SNAPSHOT_FILES

JWT_SECRET = "test-supabase-jwt-secret-with-enough-length"


def token(sub="user-1", email="dev@example.org", secret=JWT_SECRET, expires_in=3600):
    now = int(time.time())
    return jwt.encode({"sub": sub, "email": email, "aud": "authenticated",
                       "iat": now, "exp": now + expires_in}, secret, algorithm="HS256")


def bearer(value=None):
    return {"Authorization": f"Bearer {value or token()}"}


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    access_service.reset_cache()


@pytest.fixture
def linked_workflow(app, client, make_workflow):
    """A workflow linked to a PR commit, as the pipeline leaves it."""
    from db import github_job_repository as jobs
    from services.workflow_service import persist_new_workflow
    from tests.github.conftest import cuqa_payload
    from domain.cuqa_normalizer import cuqa_report_to_smells, normalize_cuqa_report

    report = normalize_cuqa_report(cuqa_payload())
    smells = cuqa_report_to_smells(report)
    with app.app_context():
        job, _ = jobs.enqueue_pr_job({
            "delivery_id": "access-1", "event": "pull_request", "action": "opened",
            "installation_id": INSTALLATION_ID, "repo_owner": "octo-org", "repo_name": "demo",
            "repo_full_name": "octo-org/demo", "pr_number": 7, "head_sha": HEAD_SHA})
        mapping = prs.get_or_create_mapping(job)
        wf_id, _ = persist_new_workflow("octo-org/demo#7", "python", smells,
                                        source="github_pr", cuqa_report=report)
        prs.update_mapping(mapping["id"], workflow_id=wf_id, analysis_status="completed",
                           smell_count=len(smells), files_analyzed=2)
    return wf_id


# ── The guard ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/workflows/{id}", "/api/workflows/{id}/smell-categories",
    "/api/workflows/{id}/smell-impacts", "/api/workflows/{id}/audit-logs",
    "/api/github/workflows/{id}",
])
def test_linked_workflow_routes_require_sign_in(client, linked_workflow, path):
    response = client.get(path.format(id=linked_workflow))
    assert response.status_code == 401
    assert "Sign in" in response.get_json()["error"]


@pytest.mark.parametrize("path,body", [
    ("/api/workflows/{id}/select-smells", {"selected_ids": ["src/app.py:1:0"]}),
    ("/api/workflows/{id}/smell-selection-pass", {"selected_ids": ["src/app.py:1:0"]}),
    ("/api/workflows/{id}/reset-to-smell-review", {}),
])
def test_linked_workflow_actions_require_sign_in(client, linked_workflow, path, body):
    assert client.post(path.format(id=linked_workflow), json=body).status_code == 401


@pytest.mark.parametrize("bad", [
    lambda: token(secret="another-secret-of-sufficient-length!"),
    lambda: token(expires_in=-10),
    lambda: token(sub=""),
    lambda: "not.a.jwt",
])
def test_invalid_tokens_are_rejected(client, linked_workflow, bad):
    response = client.get(f"/api/workflows/{linked_workflow}", headers=bearer(bad()))
    assert response.status_code == 401


def test_signed_in_user_can_open_and_review(client, linked_workflow):
    response = client.get(f"/api/workflows/{linked_workflow}", headers=bearer())
    assert response.status_code == 200
    assert response.get_json()["status"] == "smell_review"
    assert client.get(f"/api/workflows/{linked_workflow}/smell-categories",
                      headers=bearer()).status_code == 200


def test_allow_list_restricts_access(client, linked_workflow, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKFLOW_ALLOWED_EMAILS", "lead@example.org, other@example.org")
    assert client.get(f"/api/workflows/{linked_workflow}", headers=bearer()).status_code == 403
    allowed = bearer(token(email="Lead@Example.org"))
    assert client.get(f"/api/workflows/{linked_workflow}", headers=allowed).status_code == 200


def test_no_verification_method_fails_closed(client, linked_workflow, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")      # and SQLite: no Supabase Auth
    response = client.get(f"/api/workflows/{linked_workflow}", headers=bearer())
    assert response.status_code == 503


def test_supabase_auth_is_used_without_a_jwt_secret(client, linked_workflow, monkeypatch):
    """Tokens are checked with Supabase Auth (cached) when no secret is set."""
    import config
    from db import supabase_client

    calls = []

    class Auth:
        def get_user(self, jwt_value):
            calls.append(jwt_value)
            if jwt_value != "good-token":
                raise ValueError("invalid JWT")
            return type("R", (), {"user": type("U", (), {"id": "u1", "email": "a@b.c"})()})()

    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(config, "uses_supabase", lambda: True)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: type("S", (), {"auth": Auth()})())
    # The mapping lookup itself stays on SQLite for this test.
    monkeypatch.setattr(prs, "_backend", lambda: prs._Sqlite())

    assert access_service._verify_with_supabase("good-token")["user_id"] == "u1"
    assert access_service._verify_with_supabase("good-token")["email"] == "a@b.c"
    assert calls == ["good-token"], "the answer is cached"
    with pytest.raises(access_service.AccessError):
        access_service._verify_with_supabase("bad-token")


def test_database_error_while_checking_fails_closed(client, linked_workflow, monkeypatch):
    def broken(workflow_id):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(prs, "get_mapping_for_workflow", broken)
    assert client.get(f"/api/workflows/{linked_workflow}").status_code == 503


def test_missing_mapping_table_does_not_break_ordinary_workflows(client, workflow_id, monkeypatch):
    class Missing(Exception):
        code = "42P01"
    def missing(*args, **kwargs):
        raise Missing('relation "github_pr_workflows" does not exist')
    monkeypatch.setattr(prs, "get_mapping_for_workflow", missing)
    assert client.get(f"/api/workflows/{workflow_id}").status_code == 200


# ── Ordinary workflows are unaffected ───────────────────────────────────────

def test_ordinary_workflows_need_no_token(client, workflow_id, smells):
    assert client.get(f"/api/workflows/{workflow_id}").status_code == 200
    response = client.post(f"/api/workflows/{workflow_id}/select-smells",
                           json={"selected_ids": [s["id"] for s in smells]})
    assert response.status_code == 200


def test_listing_hides_linked_workflows_from_anonymous_callers(client, linked_workflow,
                                                                workflow_id):
    anonymous = {w["id"] for w in client.get("/api/workflows").get_json()}
    assert anonymous == {workflow_id}
    signed_in = {w["id"] for w in client.get("/api/workflows", headers=bearer()).get_json()}
    assert signed_in == {workflow_id, linked_workflow}


# ── GitHub workflow endpoints ───────────────────────────────────────────────

def test_github_context_endpoint(client, linked_workflow):
    body = client.get(f"/api/github/workflows/{linked_workflow}", headers=bearer()).get_json()
    assert body["github"]["repository"] == "octo-org/demo"
    assert body["github"]["pr_number"] == 7
    assert body["github"]["head_sha"] == HEAD_SHA
    assert body["github"]["is_latest_commit"] is True
    assert body["github"]["pull_request_url"] == "https://github.com/octo-org/demo/pull/7"
    assert len(body["smells"]) == 3
    assert body["report"]["files"][0]["relative_path"] == "src/app.py"


def test_github_context_for_an_ordinary_workflow_is_404(client, workflow_id):
    assert client.get(f"/api/github/workflows/{workflow_id}").status_code == 404


def test_newer_commit_marks_the_workflow_outdated(app, client, linked_workflow):
    with app.app_context():
        prs.get_or_create_mapping({
            "id": "ghjob_new", "repo_owner": "octo-org", "repo_name": "demo",
            "repo_full_name": "octo-org/demo", "pr_number": 7, "head_sha": "b" * 40,
            "installation_id": INSTALLATION_ID})
    body = client.get(f"/api/github/workflows/{linked_workflow}", headers=bearer()).get_json()
    assert body["github"]["is_latest_commit"] is False


def test_sources_are_read_from_the_analysed_commit(client, linked_workflow, app_service,
                                                   fake_github, monkeypatch):
    from services import github_job_service
    monkeypatch.setattr(github_job_service, "shared_app_service", lambda: app_service)

    response = client.post(f"/api/github/workflows/{linked_workflow}/sources",
                           json={"file_paths": ["src/app.py", "README.md"]}, headers=bearer())
    assert response.status_code == 200
    body = response.get_json()
    assert [f["file_name"] for f in body["files"]] == ["src/app.py"]
    assert body["files"][0]["source_code"] == SNAPSHOT_FILES["src/app.py"]
    # README.md exists in the repository but was not analysed: not served.
    assert body["missing"] == ["README.md"]
    read = [c["path"] for c in fake_github.calls if "/contents/" in c["path"]]
    assert read == [f"/repos/octo-org/demo/contents/src/app.py?ref={HEAD_SHA}"]


def test_sources_require_sign_in(client, linked_workflow, github_env):
    response = client.post(f"/api/github/workflows/{linked_workflow}/sources",
                           json={"file_paths": ["src/app.py"]})
    assert response.status_code == 401


@pytest.mark.parametrize("body", [{}, {"file_paths": []}, {"file_paths": "src/app.py"},
                                  {"file_paths": ["x"] * 21}])
def test_sources_validate_the_request(client, linked_workflow, github_env, body):
    response = client.post(f"/api/github/workflows/{linked_workflow}/sources", json=body,
                           headers=bearer())
    assert response.status_code == 400
