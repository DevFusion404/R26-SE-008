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
