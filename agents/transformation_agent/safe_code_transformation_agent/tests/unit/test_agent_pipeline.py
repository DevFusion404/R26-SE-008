from sctva.agent import SafeCodeTransformationValidationAgent


def test_zero_replacement_c_action_does_not_trigger_rollback():
    agent = SafeCodeTransformationValidationAgent()
    result = agent.execute(
        {
            "request_id": "zero_replacement_c",
            "language": "c",
            "source_code": "int value(void) { return 1; }\n",
            "refactoring_plan": {
                "plan_id": "zero_replacement_plan",
                "actions": [
                    {
                        "action_type": "introduce_constant",
                        "parameters": {
                            "literal_value": 999,
                            "constant_name": "MISSING_VALUE",
                            "source_line": 1,
                        },
                    }
                ],
                "behavior_tests": [],
            },
            "execution_options": {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "require_compilation": False,
                "enable_sctva_auto_refactoring": False,
            },
        }
    )

    assert result["transformation_applied"] is False
    assert result["rollback_occurred"] is False
    assert result["validation"]["syntax"]["passed"] is True
    assert result["validation"]["behavioral"]["passed"] is True
    assert result["validation"]["invariant"]["passed"] is True


def test_c_introduce_constant_normalizes_planner_decimal_string():
    agent = SafeCodeTransformationValidationAgent()
    result = agent.execute(
        {
            "request_id": "c_decimal_string_constant",
            "language": "c",
            "source_code": "int allowed(int value) { return value > 10; }\n",
            "refactoring_plan": {
                "plan_id": "c_decimal_string_constant_plan",
                "actions": [
                    {
                        "action_type": "introduce_constant",
                        "parameters": {
                            "literal_value": "10",
                            "constant_name": "MAX_ALLOWED",
                            "source_line": 1,
                        },
                    }
                ],
                "behavior_tests": [],
            },
            "execution_options": {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "require_compilation": False,
                "enable_sctva_auto_refactoring": False,
            },
        }
    )

    action = result["safety_report"]["transformation_log"][0]
    assert result["transformation_applied"] is True
    assert "#define MAX_ALLOWED 10" in result["refactored_code"]
    assert "value > MAX_ALLOWED" in result["refactored_code"]
    assert action["metadata"]["literal_value_normalizations"] == [
        {
            "original": "10",
            "normalized": 10,
            "strategy": "planner_decimal_string",
        }
    ]


def test_c_introduce_parameter_object_executes_successfully():
    agent = SafeCodeTransformationValidationAgent()
    source = "int total(int a, int b, int c) { return a + b + c; }\n"

    result = agent.execute(
        {
            "request_id": "c_parameter_object_supported",
            "language": "c",
            "source_code": source,
            "refactoring_plan": {
                "plan_id": "c_parameter_object_plan",
                "actions": [
                    {
                        "action_type": "introduce_parameter_object",
                        "parameters": {
                            "method": "total",
                            "parameter_object_name": "TotalParams",
                        },
                    }
                ],
                "behavior_tests": [],
            },
            "execution_options": {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "require_compilation": False,
                "enable_sctva_auto_refactoring": False,
            },
        }
    )

    log = result["safety_report"]["transformation_log"][0]
    assert result["transformation_applied"] is True
    assert "typedef struct" in result["refactored_code"]
    assert "TotalParams" in result["refactored_code"]
    assert "int total(TotalParams params)" in result["refactored_code"]
    assert not any("Action failed" in warning for warning in log["warnings"])

