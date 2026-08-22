"""
api/test_workflow_api.py
------------------------
Every workflow endpoint, through the Flask test client.

These assert the HTTP contract the React frontend depends on: URLs, methods,
status codes and the shape of the JSON. A change here breaks the DIWO UI even
if the workflow logic underneath is perfect.
"""

import io
import json
import zipfile

import pytest


@pytest.mark.api
class TestWorkflowCreation:
    def test_post_workflows_returns_201_with_an_id(self, client, smells):
        response = client.post("/api/workflows", json={
            "target": "OrderService", "language": "java", "smells": smells,
        })
        assert response.status_code == 201
        body = response.get_json()
        assert body["workflow_id"].startswith("wf_")
        assert body["status"] == "smell_review"
        assert "metrics_before" in body

    def test_workflow_ids_are_unique(self, make_workflow):
        assert make_workflow() != make_workflow()

    @pytest.mark.parametrize("body,fragment", [
        ({}, "valid JSON"),
        ({"target": "T", "language": "java"}, "non-empty list"),
        ({"target": "T", "language": "java", "smells": []}, "non-empty list"),
        ({"target": "T", "language": "java", "smells": "nope"}, "non-empty list"),
        ({"target": "T", "language": "java", "smells": ["not-an-object"]}, "must be an object"),
        ({"target": "T", "language": "java", "smells": [{"severity": "high"}]}, "'type'"),
    ])
    def test_invalid_bodies_are_refused_with_400(self, client, body, fragment):
        response = client.post("/api/workflows", json=body)
        assert response.status_code == 400
        assert fragment in response.get_json()["error"]

    def test_target_and_language_have_defaults(self, client, smells):
        response = client.post("/api/workflows", json={"smells": smells})
        assert response.status_code == 201


@pytest.mark.api
class TestWorkflowRetrieval:
    def test_get_returns_every_field_the_ui_reads(self, client, workflow_id):
        body = client.get(f"/api/workflows/{workflow_id}").get_json()
        for field in ("id", "target", "language", "status", "created_at",
                      "updated_at", "smells", "selected_smells", "plan",
                      "transformation_result", "metrics_before", "metrics_after"):
            assert field in body, f"{field} missing from the workflow payload"

    def test_an_unknown_workflow_is_404(self, client):
        response = client.get("/api/workflows/wf_does_not_exist")
        assert response.status_code == 404
        assert response.get_json()["error"] == "Workflow not found."

    def test_list_returns_a_summary_row_per_workflow(self, client, make_workflow):
        make_workflow()
        make_workflow()
        rows = client.get("/api/workflows").get_json()
        assert len(rows) == 2
        assert set(rows[0]) == {"id", "target", "language", "status",
                                "created_at", "updated_at"}

    def test_list_is_empty_when_nothing_exists(self, client):
        assert client.get("/api/workflows").get_json() == []


@pytest.mark.api
class TestSmellSelectionPass:
    """The preview: report only, no planning, no state change."""

    def test_returns_the_filtered_report_without_advancing(self, client, workflow_id, smells):
        response = client.post(f"/api/workflows/{workflow_id}/smell-selection-pass",
                               json={"selected_ids": [smells[0]["id"]]})
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "smell_review"
        assert client.get(f"/api/workflows/{workflow_id}").get_json()["status"] == "smell_review"

    def test_the_preview_contains_what_rdp_would_receive(self, client, workflow_id, smells):
        body = client.post(f"/api/workflows/{workflow_id}/smell-selection-pass",
                           json={"selected_ids": [smells[0]["id"]]}).get_json()
        assert "rdp_plan_input" in body
        assert "updated_report" in body

    def test_but_no_plan_is_generated(self, client, workflow_id, smells):
        # Planning here would put two POSTs to /generate in every flow.
        body = client.post(f"/api/workflows/{workflow_id}/smell-selection-pass",
                           json={"selected_ids": [smells[0]["id"]]}).get_json()
        assert "plan" not in body

    def test_an_unresolvable_selection_is_400(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/smell-selection-pass",
                               json={"selected_ids": ["ghost"]})
        assert response.status_code == 400

    @pytest.mark.parametrize("field", ["selected_ids", "selected_files", "selected_smells"])
    def test_a_non_list_selection_field_is_400(self, client, workflow_id, field):
        response = client.post(f"/api/workflows/{workflow_id}/smell-selection-pass",
                               json={field: "not-a-list"})
        assert response.status_code == 400


@pytest.mark.api
class TestSelectSmells:
    def test_advances_to_plan_approval(self, client, workflow_id, smells):
        response = client.post(f"/api/workflows/{workflow_id}/select-smells",
                               json={"selected_ids": [smells[0]["id"]]})
        assert response.status_code == 200
        assert response.get_json()["status"] == "plan_approval"

    def test_reports_which_planner_produced_the_plan(self, client, workflow_id, smells):
        # RDP is unreachable in the suite, so this must say so rather than
        # passing a fallback off as agent output.
        body = client.post(f"/api/workflows/{workflow_id}/select-smells",
                           json={"selected_ids": [smells[0]["id"]]}).get_json()
        assert body["plan_source"] == "diwo_local_fallback"
        assert body["plan_warning"]

    def test_selection_by_file_expands_to_that_file_s_smells(self, client, workflow_id):
        body = client.post(f"/api/workflows/{workflow_id}/select-smells",
                           json={"selected_files": ["src/util/Helper.java"]}).get_json()
        assert body["selected_count"] == 1

    def test_counts_add_up_to_the_whole_report(self, client, workflow_id, smells):
        body = client.post(f"/api/workflows/{workflow_id}/select-smells",
                           json={"selected_ids": [smells[0]["id"]]}).get_json()
        assert body["selected_count"] + body["excluded_count"] == len(smells)

    def test_an_empty_selection_is_400(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/select-smells", json={})
        assert response.status_code == 400

    def test_non_string_ids_are_refused(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/select-smells",
                               json={"selected_ids": [1, 2, 3]})
        assert response.status_code == 400
        assert "string" in response.get_json()["error"]


@pytest.mark.api
class TestPlanDecision:
    @pytest.mark.parametrize("decision", ["approve", "reject", "modify"])
    def test_every_valid_decision_is_accepted(self, client, at_plan_approval, decision):
        response = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                               json={"decision": decision})
        assert response.status_code == 200

    @pytest.mark.parametrize("decision", ["approved", "yes", "", None, 1])
    def test_an_invalid_decision_is_400(self, client, at_plan_approval, decision):
        response = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                               json={"decision": decision})
        assert response.status_code == 400
        assert "must be one of" in response.get_json()["error"]

    def test_reject_terminates_the_workflow(self, client, at_plan_approval):
        body = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                           json={"decision": "reject"}).get_json()
        assert body["status"] == "rolled_back"

    def test_approve_returns_the_approved_plan_and_advances(self, client, at_plan_approval):
        plan = client.get(f"/api/workflows/{at_plan_approval}").get_json()["plan"]
        decisions = {str(plan["steps"][0]["step_id"]): "approve"}
        body = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                           json={"decision": "approve", "decisions": decisions}).get_json()
        assert body["status"] == "transformation"
        assert len(body["approved_plan"]["steps"]) == 1

    def test_modify_stays_at_plan_approval(self, client, at_plan_approval):
        plan = client.get(f"/api/workflows/{at_plan_approval}").get_json()["plan"]
        decisions = {str(s["step_id"]): "reject" for s in plan["steps"][1:]}
        decisions[str(plan["steps"][0]["step_id"])] = "approve"
        body = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                           json={"decision": "modify", "decisions": decisions}).get_json()
        assert body["status"] == "plan_approval"
        assert len(body["plan"]["steps"]) == 1

    def test_modified_steps_must_be_a_list(self, client, at_plan_approval):
        response = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                               json={"decision": "modify", "modified_steps": "nope"})
        assert response.status_code == 400


@pytest.mark.api
class TestPlanPreferenceUpdate:
    def test_returns_a_reranked_plan(self, client, at_plan_approval):
        response = client.post(f"/api/workflows/{at_plan_approval}/plan-preference-update",
                               json={"decisions": {}, "preferences": {"risk_tolerance": "conservative"}})
        assert response.status_code == 200
        assert "updated_planning_report" in response.get_json()

    @pytest.mark.parametrize("body", [
        {"decisions": "nope"},
        {"preferences": "nope"},
    ])
    def test_malformed_bodies_are_400(self, client, at_plan_approval, body):
        assert client.post(f"/api/workflows/{at_plan_approval}/plan-preference-update",
                           json=body).status_code == 400


@pytest.mark.api
class TestTransformationDecision:
    @pytest.mark.parametrize("decision", ["accepted", "cancel", "", None])
    def test_an_invalid_decision_is_400(self, client, at_transformation, decision):
        response = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": decision})
        assert response.status_code == 400

    def test_rollback_returns_to_rolled_back(self, client, at_transformation):
        body = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": "rollback"}).get_json()
        assert body["status"] == "rolled_back"

    def test_accept_advances_to_comparison(self, client, at_transformation):
        body = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": "accept", "accepted_files": ["src/Order.java"]}).get_json()
        assert body["status"] == "comparison"

    def test_accept_with_file_contents_builds_an_archive(self, client, at_transformation):
        body = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": "accept",
                  "accepted_files": ["src/Order.java"],
                  "files": [{"path": "src/Order.java", "content": "class Order {}"}]}).get_json()
        assert body["archive"]["file_count"] == 1
        assert body["archive"]["url"].endswith("/refactored-archive")

    def test_download_zip_returns_the_archive_bytes(self, client, at_transformation):
        response = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": "accept",
                  "accepted_files": ["src/Order.java"],
                  "files": [{"path": "src/Order.java", "content": "class Order {}"}],
                  "download": "zip"})
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            assert "src/Order.java" in archive.namelist()

    def test_download_zip_without_contents_is_400(self, client, at_transformation):
        response = client.post(
            f"/api/workflows/{at_transformation}/transformation-decision",
            json={"decision": "accept", "download": "zip"})
        assert response.status_code == 400


@pytest.mark.api
class TestArchiveDownload:
    def test_returns_the_zip_after_acceptance(self, client, at_comparison):
        response = client.get(f"/api/workflows/{at_comparison}/refactored-archive")
        assert response.status_code == 200
        assert response.mimetype == "application/zip"

    def test_a_workflow_with_no_archive_is_404(self, client, workflow_id):
        response = client.get(f"/api/workflows/{workflow_id}/refactored-archive")
        assert response.status_code == 404
        assert "No archive" in response.get_json()["error"]

    def test_the_archive_preserves_folder_structure(self, client, at_comparison):
        response = client.get(f"/api/workflows/{at_comparison}/refactored-archive")
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            assert "src/Order.java" in archive.namelist()
            assert "src/util/Helper.java" in archive.namelist()

    def test_and_carries_a_manifest(self, client, at_comparison):
        response = client.get(f"/api/workflows/{at_comparison}/refactored-archive")
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            manifest = json.loads(archive.read("REFACTORING_MANIFEST.json"))
            assert manifest["file_count"] == 2
            assert {e["path"] for e in manifest["files"]} == {
                "src/Order.java", "src/util/Helper.java"}


@pytest.mark.api
class TestCompletionAndAudit:
    def test_complete_finishes_the_workflow(self, client, at_comparison):
        body = client.post(f"/api/workflows/{at_comparison}/complete",
                           json={"notes": "done"}).get_json()
        assert body["status"] == "completed"

    def test_an_empty_body_is_accepted(self, client, at_comparison):
        assert client.post(f"/api/workflows/{at_comparison}/complete").status_code == 200

    def test_audit_logs_have_a_stable_row_shape(self, client, workflow_id):
        logs = client.get(f"/api/workflows/{workflow_id}/audit-logs").get_json()
        assert logs
        assert set(logs[0]) == {"id", "stage", "action", "actor", "details", "timestamp"}

    def test_audit_logs_for_an_unknown_workflow_are_empty_not_an_error(self, client):
        response = client.get("/api/workflows/wf_missing/audit-logs")
        assert response.status_code == 200
        assert response.get_json() == []

    def test_feedback_export_reports_a_count_and_rows(self, client, at_plan_approval):
        body = client.get("/api/feedback/export").get_json()
        assert "count" in body and "data" in body
        assert body["count"] == len(body["data"])


@pytest.mark.api
class TestSaveUpdatedReport:
    def test_writes_the_report_and_returns_its_path(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/save-updated-report",
                               json={"updated_report": {"files": [], "summary": {}}})
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "success"
        assert body["file_name"].startswith(f"updated_report_{workflow_id}")

    @pytest.mark.parametrize("body", [{}, {"updated_report": None},
                                      {"updated_report": "not-an-object"}])
    def test_a_missing_or_malformed_report_is_400(self, client, workflow_id, body):
        assert client.post(f"/api/workflows/{workflow_id}/save-updated-report",
                           json=body).status_code == 400


@pytest.mark.api
class TestResetRoutes:
    def test_reset_to_smell_review_clears_the_plan(self, client, at_plan_approval):
        body = client.post(f"/api/workflows/{at_plan_approval}/reset-to-smell-review",
                           json={}).get_json()
        assert body["status"] == "smell_review"
        assert body["changed"] is True
        assert client.get(f"/api/workflows/{at_plan_approval}").get_json()["plan"] is None

    def test_resetting_an_already_reset_workflow_is_a_no_op(self, client, workflow_id):
        body = client.post(f"/api/workflows/{workflow_id}/reset-to-smell-review",
                           json={}).get_json()
        assert body["changed"] is False

    def test_reset_to_plan_approval_restores_the_full_plan(self, client, at_transformation):
        # Approval trimmed the plan to one step; the rollback must offer them
        # all again or the developer cannot reinstate what they rejected.
        body = client.post(f"/api/workflows/{at_transformation}/reset-to-plan-approval",
                           json={}).get_json()
        assert body["status"] == "plan_approval"
        assert body["restored_full_plan"] is True
        assert len(body["plan"]["steps"]) == 3

    def test_a_workflow_with_no_plan_cannot_go_back_to_one(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/reset-to-plan-approval",
                               json={})
        assert response.status_code == 400


@pytest.mark.api
class TestHealthAndIntegrationRoutes:
    def test_root_reports_the_service(self, client):
        body = client.get("/").get_json()
        assert body["status"] == "DIWO Agent Backend Running"

    def test_api_health_reports_the_agent(self, client):
        body = client.get("/api/health").get_json()
        assert body["status"] == "ok"
        assert body["agent"] == "DIWO"

    @pytest.mark.parametrize("path", ["/api/cuqa/status", "/api/rdp/status",
                                      "/api/sctva/status"])
    def test_agent_status_answers_even_when_the_agent_is_down(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.get_json()["reachable"] is False

    def test_a_down_cuqa_returns_503_not_500(self, client):
        response = client.post("/api/cuqa/quality-report", json={})
        assert response.status_code == 503
        assert "cuqa_url" in response.get_json()

    def test_a_down_sctva_workspace_read_returns_503(self, client):
        response = client.post("/api/workspace/sources", json={"file_paths": ["a.java"]})
        assert response.status_code == 503
        assert "sctva_url" in response.get_json()

    def test_an_empty_source_request_short_circuits_without_calling_the_agent(self, client):
        response = client.post("/api/workspace/sources", json={"file_paths": []})
        assert response.status_code == 200
        assert response.get_json() == {"files": [], "missing": [], "imported": 0, "total": 0}
