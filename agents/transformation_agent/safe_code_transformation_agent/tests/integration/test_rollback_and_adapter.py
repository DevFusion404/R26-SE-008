from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter


def test_java_syntax_error_triggers_rollback():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "rb_001",
        "language": "java",
        "source_code": "public class A { public int f(){ return 1; } }",
        "refactoring_plan": {
            "plan_id": "rb_plan_1",
            "actions": [{"action_type": "inject_syntax_error", "parameters": {}}],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is True


def test_adapter_build_request_from_rdp():
    adapter = PlannerAdapter()
    plan = {
        "plan_id": "rdp_1",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Extract Method",
                "target": {"method": "doWork"},
                "parameters": {"new_method_name": "doWorkCore", "start_line": 1, "end_line": 1},
            }
        ],
        "metadata": {"correlation_id": "c1"},
    }
    request = adapter.build_request_from_rdp(
        request_id="req_1",
        language="java",
        source_code="public class A { int doWork(){return 1;} }",
        planner_output=plan,
        correlation_id="c1",
    )

    assert request["refactoring_plan"]["plan_id"] == "rdp_1"
    assert request["refactoring_plan"]["actions"][0]["action_type"] == "extract_method"
