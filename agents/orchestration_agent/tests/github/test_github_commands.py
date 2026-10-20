"""
Features B and C — /refactoriq PR commands: selection and RDP planning
======================================================================
The GitHub-native side of the review, end to end through the real webhook,
job queue and worker. 'alice' has write access, 'rita' read access,
'mallory' none. GitHub, CUQA and RDP are faked.
"""

import json
import time

import jwt
import pytest

from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from services.github_command_parser import command_for_action, parse_command
from tests.github.conftest import HEAD_SHA, comment_payload, make_response, pr_payload

S = ["src/app.py:1:0", "src/app.py:0:1", "src/util.py:1:0"]     # S001, S002, S003


def mapping(app):
    with app.app_context():
        return prs.get_mapping("octo-org/demo", 7, HEAD_SHA)


def draft(app):
    return prs.get_draft(mapping(app), "draft_selection_json", None)


def workflow(app, wf_id):
    with app.app_context():
        from db.workflow_repository import get_workflow
        return get_workflow(wf_id)


# ── Parsing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,name,args", [
    ("/refactoriq select S001 S002", "select", ["S001", "S002"]),
    ("  /RefactorIQ  Select S001,S002", "select", ["S001", "S002"]),
    ("Looks good!\n/refactoriq generate-plan\n/refactoriq submit-plan confirm", "generate-plan", []),
    ("/refactoriq", "help", []),
])
def test_commands_are_parsed(body, name, args):
    parsed = parse_command(body)
    assert (parsed["name"], parsed["args"]) == (name, args)


@pytest.mark.parametrize("body", ["please /refactoriq select S001", "> /refactoriq select S001",
                                  "`/refactoriq select S001`", "", None, "/refactoriqx list"])
def test_text_that_merely_mentions_a_command_is_ignored(body):
    assert parse_command(body) is None


def test_unknown_commands_are_flagged():
    assert parse_command("/refactoriq approve-everything")["known"] is False


def test_buttons_map_to_commands():
    assert command_for_action("apply_accepted")["name"] == "submit-refactoring"
    assert command_for_action("not_ours") is None


# ── Webhook filtering ───────────────────────────────────────────────────────

def test_command_comment_is_queued(post_webhook, linked_pr):
    response = post_webhook(payload=comment_payload("/refactoriq list"), event="issue_comment")
    assert response.status_code == 202 and response.get_json()["command"] == "list"


@pytest.mark.parametrize("payload", [
    comment_payload("thanks, merging"),
    comment_payload("/refactoriq list", action="edited"),
    comment_payload("/refactoriq list", action="deleted"),
    comment_payload("/refactoriq list", sender_type="Bot"),
    comment_payload("/refactoriq list", login="refactoriq[bot]"),
    comment_payload("/refactoriq list", on_pr=False),
])
def test_irrelevant_comments_are_ignored(post_webhook, linked_pr, payload):
    response = post_webhook(payload=payload, event="issue_comment")
    assert response.status_code == 200 and response.get_json()["ignored"] is True


def test_comments_posted_by_an_app_are_ignored(post_webhook, linked_pr):
    payload = comment_payload("/refactoriq select S001")
    payload["comment"]["performed_via_github_app"] = {"id": 1}
    assert post_webhook(payload=payload, event="issue_comment").get_json()["ignored"] is True


def test_commands_can_be_switched_off(post_webhook, linked_pr, monkeypatch):
    monkeypatch.setenv("GITHUB_COMMANDS_ENABLED", "false")
    assert post_webhook(payload=comment_payload("/refactoriq list"),
                        event="issue_comment").get_json()["ignored"] is True


def test_duplicate_deliveries_queue_one_command(post_webhook, linked_pr):
    first = post_webhook(payload=comment_payload("/refactoriq select S001"), event="issue_comment",
                         delivery="dup-cmd-1")
    second = post_webhook(payload=comment_payload("/refactoriq select S001"), event="issue_comment",
                          delivery="dup-cmd-1")
    assert (first.status_code, second.status_code) == (202, 200)


def test_unsigned_commands_are_rejected(post_webhook, linked_pr):
    response = post_webhook(payload=comment_payload("/refactoriq select S001"),
                            event="issue_comment", sign_body=False)
    assert response.status_code == 401


# ── Selection ───────────────────────────────────────────────────────────────

def test_writer_selects_individual_findings(app, linked_pr, command, fake_github):
    command("/refactoriq select S001 S003")
    assert draft(app) == [S[0], S[2]]
    reply = fake_github.last_comment()
    assert "RefactorIQ — Developer Selection" in reply
    assert "Selected findings: **2**" in reply and "Excluded findings: **1**" in reply
    assert "S001 — LongMethod — src/app.py" in reply
    assert "/refactoriq generate-plan" in reply
    assert "@alice" in reply


def test_one_status_comment_is_edited_not_spammed(app, linked_pr, command, fake_github):
    command("/refactoriq select S001")
    command("/refactoriq select S002")
    command("/refactoriq show-selection")
    assert len(fake_github.comments) == 1
    assert len(fake_github.comments[min(fake_github.comments)]) == 3


def test_select_deselect_and_clear(app, linked_pr, command):
    command("/refactoriq select S001 S002 S003")
    command("/refactoriq deselect S002")
    assert draft(app) == [S[0], S[2]]
    command("/refactoriq clear-selection")
    assert draft(app) == []


def test_repeating_a_command_is_harmless(app, linked_pr, command):
    command("/refactoriq select S001")
    command("/refactoriq select S001")
    assert draft(app) == [S[0]]


def test_group_selection_needs_both_file_and_type(app, linked_pr, command, fake_github):
    command("/refactoriq select-group src/app.py")
    assert draft(app) is None and "both are required" in fake_github.last_comment()
    command("/refactoriq select-group src/app.py LongMethod")
    assert draft(app) == [S[0]]


def test_invalid_ids_change_nothing(app, linked_pr, command, fake_github):
    command("/refactoriq select S001")
    command("/refactoriq select S002 S999 LongMethod")
    assert draft(app) == [S[0]]
    reply = fake_github.last_comment()
    assert "Not findings of this pull request" in reply and "S999" in reply


@pytest.mark.parametrize("login,fragment", [("rita", "'read' access"), ("mallory", "'none' access")])
def test_users_without_write_access_cannot_select(app, linked_pr, command, fake_github, login,
                                                  fragment):
    command("/refactoriq select S001", login=login)
    assert draft(app) is None
    assert fragment in fake_github.last_comment()


def test_read_only_commands_work_for_readers(app, linked_pr, command, fake_github):
    command("/refactoriq list", login="rita")
    reply = fake_github.last_comment()
    assert "src/app.py" in reply and "LongMethod (1, high) — S001" in reply


def test_permission_check_outage_retries_instead_of_guessing(app, linked_pr, command, fake_github):
    fake_github.on("GET", r"/repos/([^/]+)/([^/]+)/collaborators/([^/]+)/permission",
                   lambda call: make_response(502, {"message": "Bad Gateway"}))
    response = command("/refactoriq select S001")
    with app.app_context():
        job = jobs.get_job(response.get_json()["job_id"])
    assert job["status"] == "queued", "a transient GitHub failure is retried"
    assert "verify your repository permission" in job["last_error"]
    assert draft(app) is None, "nothing changes while authorization is unknown"
    assert fake_github.comments == {}


def test_commands_on_an_outdated_commit_are_refused(app, linked_pr, command, post_webhook,
                                                    fake_github, run_jobs):
    # The command is queued, then a new push is delivered before the worker
    # gets to it: the command must not act on the old findings.
    command("/refactoriq select S001", process=False)
    post_webhook(payload=pr_payload(action="synchronize", sha="b" * 40))
    fake_github.pr_heads[("octo-org/demo", 7)] = "b" * 40
    run_jobs()
    assert draft(app) is None
    assert "newer commit" in fake_github.comments[min(fake_github.comments)][0]


def test_commands_follow_the_newest_analysed_commit(app, linked_pr, command, post_webhook,
                                                    fake_github, run_jobs):
    fake_github.pr_heads[("octo-org/demo", 7)] = "b" * 40
    post_webhook(payload=pr_payload(action="synchronize", sha="b" * 40))
    run_jobs()
    command("/refactoriq select S001")
    with app.app_context():
        newest = prs.get_mapping("octo-org/demo", 7, "b" * 40)
    assert prs.get_draft(newest, "draft_selection_json", None) == [S[0]]
    assert draft(app) is None, "the old commit's workflow is left untouched"


def test_commands_on_a_closed_pr_are_refused(app, linked_pr, command, fake_github):
    fake_github.pr_state[("octo-org/demo", 7)] = "closed"
    command("/refactoriq select S001")
    assert "no longer open" in fake_github.last_comment()
    assert draft(app) is None


def test_unknown_command_replies_with_help(app, linked_pr, command, fake_github):
    command("/refactoriq approve-everything")
    assert "is not a RefactorIQ command" in fake_github.last_comment()


def test_selection_is_shared_with_diwo(app, client, linked_pr, command, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-supabase-jwt-secret-with-enough-length")
    command("/refactoriq select S002")
    now = int(time.time())
    token = jwt.encode({"sub": "u", "email": "dev@example.org", "iat": now, "exp": now + 600},
                       "test-supabase-jwt-secret-with-enough-length", algorithm="HS256")
    body = client.get(f"/api/github/workflows/{linked_pr['workflow_id']}",
                      headers={"Authorization": f"Bearer {token}"}).get_json()
    assert body["draft_selection"] == [S[1]]
    assert body["smell_ids"] == {S[0]: "S001", S[1]: "S002", S[2]: "S003"}


# ── Planning ────────────────────────────────────────────────────────────────

def test_generate_plan_sends_only_the_selection_to_rdp(app, linked_pr, command, stub_rdp_plan,
                                                       fake_github):
    command("/refactoriq generate-plan")
    assert "Nothing is selected" in fake_github.last_comment()
    assert stub_rdp_plan == [], "no plan before a selection"

    command("/refactoriq select S001")
    command("/refactoriq generate-plan")
    [sent] = stub_rdp_plan
    assert [(s["type"]) for f in sent["files"] for s in f["code_smells"]] == ["LongMethod"]
    assert sent["source_files"][0]["source_mode"] == "github_commit"

    wf = workflow(app, linked_pr["workflow_id"])
    assert wf["status"] == "plan_approval"
    reply = fake_github.last_comment()
    assert "RefactorIQ — RDP Planning" in reply and "Planning source: **RDP agent**" in reply
    assert "### File: src/app.py" in reply and "| P001 | Extract Method |" in reply
    check = fake_github.check_runs[5001][-1]["output"]
    assert check["title"].startswith("Refactoring plan ready")
    assert "### File: src/app.py" in check["text"]


def test_fallback_plan_is_labelled_as_such(app, linked_pr, command, fake_github):
    command("/refactoriq select S001")
    command("/refactoriq generate-plan")                  # RDP unreachable by default
    assert "DIWO offline fallback planner" in fake_github.last_comment()


def planned(command, stub_rdp_plan, ids="S001 S003"):
    command(f"/refactoriq select {ids}")
    command("/refactoriq generate-plan")


def test_plan_step_decisions_are_drafted_and_shown(app, linked_pr, command, stub_rdp_plan,
                                                   fake_github):
    planned(command, stub_rdp_plan)
    command("/refactoriq approve-plan P001")
    command("/refactoriq reject-plan P002")
    assert prs.get_draft(mapping(app), "draft_plan_decisions_json", {}) == {"1": "approve",
                                                                           "2": "reject"}
    text = fake_github.check_runs[5001][-1]["output"]["text"]
    assert "approved (draft)" in text and "rejected (draft)" in text


def test_unknown_plan_steps_change_nothing(app, linked_pr, command, stub_rdp_plan, fake_github):
    planned(command, stub_rdp_plan)
    command("/refactoriq approve-plan P001 P042")
    assert prs.get_draft(mapping(app), "draft_plan_decisions_json", {}) == {}
    assert "P042" in fake_github.last_comment()


def test_manual_only_steps_cannot_be_approved_from_github(app, linked_pr, command, stub_rdp_plan,
                                                          fake_github):
    planned(command, stub_rdp_plan)
    with app.app_context():
        from db.workflow_repository import parse_json_field, update_workflow
        wf = workflow(app, linked_pr["workflow_id"])
        plan = parse_json_field(wf, "plan_json")
        plan["steps"][0].setdefault("decision_support", {})["category"] = "manual_only"
        update_workflow(wf["id"], plan_json=json.dumps(plan))
    command("/refactoriq approve-plan P001")
    assert "manual-only" in fake_github.last_comment()
    assert prs.get_draft(mapping(app), "draft_plan_decisions_json", {}) == {}


def test_submit_plan_needs_confirmation_from_the_same_user(app, linked_pr, command, stub_rdp_plan,
                                                           fake_github):
    planned(command, stub_rdp_plan)
    command("/refactoriq approve-plan P001")
    command("/refactoriq reject-plan P002")
    command("/refactoriq submit-plan")
    assert "submit-plan confirm" in fake_github.last_comment()
    assert workflow(app, linked_pr["workflow_id"])["status"] == "plan_approval"

    fake_github.permissions["bob"] = "write"
    command("/refactoriq submit-plan confirm", login="bob")
    assert "no matching request to confirm" in fake_github.last_comment()

    command("/refactoriq submit-plan confirm")
    wf = workflow(app, linked_pr["workflow_id"])
    assert wf["status"] == "plan_approved"
    from db.workflow_repository import parse_json_field
    assert [s["step_id"] for s in parse_json_field(wf, "plan_json")["steps"]] == [1]
    reply = fake_github.last_comment()
    assert "Plan approved: 1 step(s) forwarded" in reply
    assert "GITHUB_PR_TRANSFORMATION_ENABLED=false" in reply


def test_changing_a_decision_invalidates_the_confirmation(app, linked_pr, command, stub_rdp_plan,
                                                          fake_github):
    planned(command, stub_rdp_plan)
    command("/refactoriq approve-plan P001")
    command("/refactoriq submit-plan")
    command("/refactoriq approve-plan P002")
    command("/refactoriq submit-plan confirm")
    assert "no matching request to confirm" in fake_github.last_comment()


def test_expired_confirmation_is_refused(app, linked_pr, command, stub_rdp_plan, fake_github,
                                         monkeypatch):
    planned(command, stub_rdp_plan)
    command("/refactoriq approve-plan P001")
    command("/refactoriq submit-plan")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)
    command("/refactoriq submit-plan confirm")
    assert "no matching request" in fake_github.last_comment()


def test_github_actor_is_recorded_in_the_audit_trail(app, linked_pr, command, stub_rdp_plan):
    planned(command, stub_rdp_plan)
    with app.app_context():
        from db.workflow_repository import get_audit_logs
        actors = {(e["action"], e["actor"]) for e in get_audit_logs(linked_pr["workflow_id"])}
    assert ("smells_selected", "github:alice") in actors


def test_an_analysis_job_after_a_command_is_not_attributed_to_the_commenter(
        app, linked_pr, command, post_webhook, run_jobs, fake_github):
    command("/refactoriq select S001")
    fake_github.pr_heads[("octo-org/demo", 7)] = "e" * 40
    post_webhook(payload=pr_payload(action="synchronize", sha="e" * 40))
    run_jobs()
    with app.app_context():
        from db.workflow_repository import get_audit_logs
        new = prs.get_mapping("octo-org/demo", 7, "e" * 40)
        actors = {e["actor"] for e in get_audit_logs(new["workflow_id"])}
    assert "github:alice" not in actors
