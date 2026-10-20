"""
POST /api/github/webhook
========================
Signature verification, event filtering, payload validation, durable job
creation and duplicate-delivery handling. No GitHub call is made inside the
request, so none of these tests needs a fake GitHub.
"""

import json
import logging

import pytest

from tests.github.conftest import HEAD_SHA, INSTALLATION_ID, WEBHOOK_SECRET, pr_payload, sign


def job_count(app):
    with app.app_context():
        from db.database import get_db
        return get_db().execute("SELECT COUNT(*) FROM github_analysis_jobs").fetchone()[0]


def jobs_for(app, **where):
    with app.app_context():
        from db.database import get_db
        clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
        rows = get_db().execute(
            f"SELECT * FROM github_analysis_jobs WHERE {clause} ORDER BY created_at",
            list(where.values())).fetchall()
        return [dict(r) for r in rows]


# ── Configuration gates ─────────────────────────────────────────────────────

def test_disabled_integration_is_refused_and_stores_nothing(app, post_webhook):
    response = post_webhook()
    assert response.status_code == 503
    assert "disabled" in response.get_json()["error"]
    assert job_count(app) == 0


def test_enabled_without_a_secret_is_refused(app, post_webhook, github_env, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    response = post_webhook()
    assert response.status_code == 503
    assert job_count(app) == 0


def test_status_endpoint_reports_names_not_values(client, github_env):
    body = client.get("/api/github/status").get_json()
    assert body == {**body, "enabled": True, "configured": True, "problems": [],
                    "autonomy_level": "L0", "check_name": "RefactorIQ / DIWO",
                    "webhook_path": "/api/github/webhook"}
    assert WEBHOOK_SECRET not in json.dumps(body)


def test_status_endpoint_names_missing_settings(client, github_env, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("GITHUB_AUTONOMY_LEVEL", "L3")
    body = client.get("/api/github/status").get_json()
    assert body["configured"] is False
    assert any("GITHUB_APP_ID" in p for p in body["problems"])
    assert any("GITHUB_AUTONOMY_LEVEL" in p for p in body["problems"])


# ── Signature ───────────────────────────────────────────────────────────────

def test_valid_signature_is_accepted_and_persisted(app, post_webhook, github_env):
    response = post_webhook(delivery="delivery-valid-1")
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] is True

    [job] = jobs_for(app, delivery_id="delivery-valid-1")
    assert job["id"] == body["job_id"]
    assert job["status"] == "queued"
    assert (job["repo_owner"], job["repo_name"], job["repo_full_name"]) == \
        ("octo-org", "demo", "octo-org/demo")
    assert job["pr_number"] == 7
    assert job["head_sha"] == HEAD_SHA
    assert job["head_repo_full_name"] == "octo-org/demo"
    assert job["is_fork"] == 0
    assert job["installation_id"] == INSTALLATION_ID
    assert (job["event"], job["action"]) == ("pull_request", "opened")


def test_missing_signature_is_rejected(app, post_webhook, github_env):
    response = post_webhook(sign_body=False)
    assert response.status_code == 401
    assert job_count(app) == 0


@pytest.mark.parametrize("signature", [
    sign(b"something else"),
    "sha256=" + "0" * 64,
    "sha1=" + "0" * 40,
    "not-a-signature",
])
def test_invalid_signature_is_rejected(app, post_webhook, github_env, signature):
    assert post_webhook(signature=signature).status_code == 401
    assert job_count(app) == 0


def test_signature_made_with_another_secret_is_rejected(app, post_webhook, github_env):
    raw = json.dumps(pr_payload()).encode()
    response = post_webhook(body=raw, signature=sign(raw, secret="wrong-secret"))
    assert response.status_code == 401


def test_modified_body_is_rejected(app, post_webhook, github_env):
    original = json.dumps(pr_payload(number=7)).encode()
    tampered = json.dumps(pr_payload(number=8)).encode()
    response = post_webhook(body=tampered, signature=sign(original))
    assert response.status_code == 401
    assert job_count(app) == 0


# ── Body and headers ────────────────────────────────────────────────────────

def test_invalid_json_is_rejected(app, post_webhook, github_env):
    response = post_webhook(body=b"{not json")
    assert response.status_code == 400
    assert "Malformed JSON" in response.get_json()["error"]


def test_json_that_is_not_an_object_is_rejected(app, post_webhook, github_env):
    assert post_webhook(body=b"[1, 2, 3]").status_code == 400


def test_missing_delivery_header_is_rejected(client, github_env):
    raw = json.dumps(pr_payload()).encode()
    response = client.post("/api/github/webhook", data=raw, headers={
        "X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(raw)})
    assert response.status_code == 400


def test_ping_is_answered(post_webhook, github_env):
    response = post_webhook(payload={"zen": "Keep it logically awesome."}, event="ping")
    assert response.status_code == 200
    assert response.get_json()["pong"] is True


# ── Event filtering ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
def test_supported_pr_actions_queue_a_job(app, post_webhook, github_env, action):
    response = post_webhook(payload=pr_payload(action=action))
    assert response.status_code == 202
    assert jobs_for(app)[0]["action"] == action


@pytest.mark.parametrize("action", ["closed", "labeled", "edited", "review_requested", None])
def test_unsupported_pr_actions_are_ignored(app, post_webhook, github_env, action):
    response = post_webhook(payload=pr_payload(action=action))
    assert response.status_code == 200
    assert response.get_json()["ignored"] is True
    assert job_count(app) == 0


@pytest.mark.parametrize("event", ["push", "issues", "check_suite", "installation"])
def test_unrelated_events_are_ignored(app, post_webhook, github_env, event):
    response = post_webhook(payload={"action": "opened"}, event=event)
    assert response.status_code == 200
    assert response.get_json()["ignored"] is True
    assert job_count(app) == 0


# ── Payload validation ──────────────────────────────────────────────────────

def test_missing_installation_id_is_rejected(app, post_webhook, github_env):
    response = post_webhook(payload=pr_payload(installation_id=None))
    assert response.status_code == 422
    assert "installation" in response.get_json()["error"]
    assert job_count(app) == 0


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("repository"),
    lambda p: p.pop("pull_request"),
    lambda p: p["pull_request"]["head"].update(sha="not-a-sha"),
    lambda p: p["pull_request"].update(number="7"),
    lambda p: p["repository"]["owner"].update(login="../etc"),
    lambda p: p["repository"].update(full_name="someone/else"),
    lambda p: p["installation"].update(id="987"),
])
def test_malformed_pr_payloads_are_rejected(app, post_webhook, github_env, mutate):
    payload = pr_payload()
    mutate(payload)
    assert post_webhook(payload=payload).status_code == 422
    assert job_count(app) == 0


def test_fork_pr_is_recorded_as_a_fork(app, post_webhook, github_env):
    assert post_webhook(payload=pr_payload(head_repo="someone/demo-fork")).status_code == 202
    [job] = jobs_for(app)
    assert job["is_fork"] == 1
    assert job["head_repo_full_name"] == "someone/demo-fork"


# ── Idempotency and durability ──────────────────────────────────────────────

def test_duplicate_delivery_creates_one_job(app, post_webhook, github_env):
    first = post_webhook(delivery="dup-delivery-1")
    second = post_webhook(delivery="dup-delivery-1")
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["job_id"] == first.get_json()["job_id"]
    assert job_count(app) == 1


def test_new_commit_supersedes_a_queued_older_commit(app, post_webhook, github_env):
    post_webhook(payload=pr_payload(action="opened", sha="1" * 40))
    post_webhook(payload=pr_payload(action="synchronize", sha="2" * 40))
    older, newer = jobs_for(app)
    assert older["status"] == "superseded"
    assert older["superseded_by"] == newer["id"]
    assert newer["status"] == "queued"


def test_other_prs_are_not_superseded(app, post_webhook, github_env):
    post_webhook(payload=pr_payload(number=1, sha="1" * 40))
    post_webhook(payload=pr_payload(number=2, sha="2" * 40))
    assert [j["status"] for j in jobs_for(app)] == ["queued", "queued"]


def test_storage_failure_is_not_reported_as_accepted(app, post_webhook, github_env, monkeypatch):
    from db import github_job_repository

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(github_job_repository, "enqueue_pr_job", broken)
    response = post_webhook()
    assert response.status_code == 503


# ── Logging ─────────────────────────────────────────────────────────────────

def test_webhook_logs_contain_no_secrets(app, post_webhook, github_env, caplog, capsys,
                                         private_key_pem):
    caplog.set_level(logging.DEBUG)
    raw = json.dumps(pr_payload()).encode()
    signature = sign(raw)
    post_webhook(body=raw, signature=signature, delivery="log-check-1")
    post_webhook(body=raw, signature=signature, delivery="log-check-1")     # duplicate
    post_webhook(body=raw, signature=sign(b"x"))                           # rejected

    captured = capsys.readouterr()
    output = caplog.text + captured.out + captured.err
    assert "log-check-1" in output, "the delivery id is what makes a log line traceable"
    for secret in (WEBHOOK_SECRET, signature, private_key_pem.splitlines()[1]):
        assert secret not in output
