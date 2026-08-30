from __future__ import annotations

from flask import Flask

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.api import create_sctva_blueprint
from sctva.integration.planner_adapter import normalize_sctva_request_payload


def _raw_move_request() -> dict:
    return {
        "request_id": "move-entrypoint-test",
        "language": "python",
        "source_code": "def time():\n    return 1\n",
        "plan_id": "rdp-move-entrypoint-test",
        "steps": [
            {
                "step_id": "move-1",
                "refactoring": "Move Method",
                "target": {
                    "class": "SourceClass",
                    "method": "time",
                    "lines": [4, 8],
                },
                "parameters": {
                    "source_file": "jarvis.py",
                    "source_class": "SourceClass",
                    "source_method": "time",
                    "destination_class": "TimeHelper",
                    "destination_parameter": "helper",
                },
            }
        ],
    }


def test_both_execute_endpoints_normalize_raw_rdp_move_method_identically(monkeypatch):
    captured: list[dict] = []

    def fake_execute(self, payload):
        captured.append(payload)
        return {"success": True, "status": "ok"}

    monkeypatch.setattr(SafeCodeTransformationValidationAgent, "execute", fake_execute)
    app = Flask(__name__)
    app.register_blueprint(create_sctva_blueprint())
    client = app.test_client()

    first = client.post("/sctva/execute", json=_raw_move_request())
    second = client.post("/sctva/execute_from_rdp", json=_raw_move_request())

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(captured) == 2
    first_action = captured[0]["refactoring_plan"]["actions"][0]
    second_action = captured[1]["refactoring_plan"]["actions"][0]
    assert first_action == second_action
    assert first_action["source_step_id"] == "move-1"
    assert first_action["parameters"]["source_file"] == "jarvis.py"
    assert first_action["parameters"]["source_class"] == "SourceClass"
    assert first_action["parameters"]["source_method"] == "time"
    assert first_action["parameters"]["method"] == "time"
    assert first_action["parameters"]["destination_class"] == "TimeHelper"
    assert first_action["parameters"]["destination_parameter"] == "helper"
    assert first_action["parameters"]["source_line"] == 4
    assert first_action["parameters"]["target_lines"] == [4, 8]


def test_missing_rdp_move_method_data_is_reported_before_execution():
    payload = _raw_move_request()
    payload["steps"][0]["parameters"] = {"source_file": "jarvis.py"}
    normalized, issues = normalize_sctva_request_payload(payload)

    assert normalized["refactoring_plan"]["actions"][0]["action_type"] == "move_python_method"
    assert issues[0]["reason"] == "RDP_MOVE_METHOD_PARAMETERS_LOST"
    assert "destination_class" in issues[0]["missing_planner_fields"]


def test_direct_agent_reports_lost_move_method_parameters_without_changing_source():
    payload = _raw_move_request()
    payload["steps"][0]["parameters"] = {"source_file": "jarvis.py"}

    result = SafeCodeTransformationValidationAgent().execute(payload)

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["reason"] == "RDP_MOVE_METHOD_PARAMETERS_LOST"
    assert result["normalization_diagnostics"][0]["missing_planner_fields"]
