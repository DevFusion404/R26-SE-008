from sctva.agent import SafeCodeTransformationValidationAgent


def test_python_safe_pipeline_passes_without_rollback():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_001",
        "language": "python",
        "source_code": "def f(x):\n    return x * 10\n",
        "refactoring_plan": {
            "plan_id": "plan_int_001",
            "actions": [
                {
                    "action_type": "extract_constant",
                    "parameters": {"literal_value": 10, "constant_name": "FACTOR"},
                }
            ],
            "behavior_tests": [
                {"name": "mul", "call": "f", "args": [2], "expected": 20}
            ],
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
    assert result["rollback_occurred"] is False
    assert result["success"] is True


def test_python_behavior_change_rolls_back():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_002",
        "language": "python",
        "source_code": "def f():\n    return 1\n",
        "refactoring_plan": {
            "plan_id": "plan_int_002",
            "actions": [
                {"action_type": "replace_literal", "parameters": {"old_literal": 1, "new_literal": 2}}
            ],
            "behavior_tests": [{"name": "value", "call": "f", "expected": 1}],
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
    assert result["success"] is False


def test_language_guard_rejects_non_supported():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_003",
        "language": "javascript",
        "source_code": "function x(){return 1;}",
        "refactoring_plan": {"plan_id": "p", "actions": [], "behavior_tests": [], "metadata": {}},
    }

    try:
        agent.execute(payload)
    except Exception as exc:
        assert "Unsupported language" in str(exc)
    else:
        assert False, "Expected unsupported language error"
