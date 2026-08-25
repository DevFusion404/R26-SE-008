from sctva.agent import SafeCodeTransformationValidationAgent


def _options(require_compilation: bool = False) -> dict[str, object]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": 10,
        "require_compilation": require_compilation,
        "enable_sctva_auto_refactoring": False,
    }


def test_python_dead_code_pipeline_removes_only_target_and_passes_validation():
    source = '''def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    return "even" if number % 2 == 0 else "odd"
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_dead_code_pipeline",
        "language": "python",
        "source_files": [{
            "file_name": "example.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_dead_code_plan",
            "actions": [{"action_type": "remove_dead_code", "parameters": {"method": "old_number_format"}}],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "old_number_format" not in result["refactored_code"]
    assert result["validation"]["structural"]["details"]["dead_code_validation"][0]["passed"] is True


def test_python_internal_dead_code_action_survives_prior_line_shifting_action():
    source = '''def check_number(number):
    if number % 2 == 0:
        return "even"
        print("This line can never run")
    return "odd"

value = 14
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_dead_code_shifted_line",
        "language": "python",
        "source_files": [{
            "file_name": "05_dead_code_number_checker.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_dead_code_shifted_line_plan",
            # This stale RDP line deliberately does not identify dead code. SCTVA
            # must still relocate its separately proven internal action.
            "actions": [
                {"action_type": "introduce_constant", "parameters": {
                    "literal_value": 14, "constant_name": "CONSTANT_14", "source_line": 7,
                }},
                {"action_type": "remove_dead_code", "parameters": {"source_line": 3}},
            ],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": {
            **_options(),
            "enable_sctva_auto_refactoring": True,
        },
    })

    internal_dead_code_logs = [
        entry for entry in result["safety_report"]["transformation_log"]
        if entry["action_type"] == "remove_dead_code"
        and entry["warnings"]
        and "unreachable Python statement" in entry["warnings"][0]
    ]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "This line can never run" not in result["refactored_code"]
    assert len(internal_dead_code_logs) == 1
    assert internal_dead_code_logs[0]["replacements_count"] == 1


def test_python_dead_code_anchors_survive_constant_insertion_without_skip_warnings():
    source = '''def check_number(number):
    if False:
        print("Legacy diagnostic mode")
    if number > 0:
        return number + 14
        print("This line can never run")
    return 0
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_dead_code_all_anchors",
        "language": "python",
        "source_files": [{
            "file_name": "05_dead_code_number_checker.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_dead_code_all_anchors",
            "actions": [
                {"action_type": "introduce_constant", "parameters": {
                    "literal_value": 14,
                    "constant_name": "CONSTANT_14",
                    "source_line": 5,
                }},
                {"action_type": "remove_dead_code", "parameters": {"source_line": 2}},
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            **_options(),
            "enable_sctva_auto_refactoring": True,
        },
    })

    warnings = [
        warning
        for entry in result["safety_report"]["transformation_log"]
        for warning in entry["warnings"]
    ]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "Legacy diagnostic mode" not in result["refactored_code"]
    assert "This line can never run" not in result["refactored_code"]
    assert not any("Dead-code removal skipped" in warning for warning in warnings)


def test_python_line_targeted_function_removal_preserves_behavioral_signatures():
    source = '''"""Intentionally contains Dead Code. Not refactored."""

def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    if number % 2 == 0:
        return "even"
        print("This line can never run")
    return "odd"

def main():
    value = 14
    if False:
        print("Legacy diagnostic mode")
    result = check_number(value)
    print(value, "is", result)

if __name__ == "__main__":
    main()
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_line_targeted_function_removal",
        "language": "python",
        "source_files": [{
            "file_name": "05_dead_code_number_checker.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_line_targeted_function_removal",
            "actions": [
                {"action_type": "introduce_constant", "parameters": {
                    "literal_value": 14,
                    "constant_name": "CONSTANT_14",
                    "source_line": 13,
                }},
                {"action_type": "remove_dead_code", "parameters": {"source_line": 3}},
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            **_options(),
            "enable_sctva_auto_refactoring": True,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "def old_number_format" not in result["refactored_code"]
    assert "This line can never run" not in result["refactored_code"]
    assert "Legacy diagnostic mode" not in result["refactored_code"]
    assert result["validation"]["behavioral"]["passed"] is True
    assert result["validation"]["invariant"]["passed"] is True


def test_python_recovers_filename_derived_rdp_target_and_removes_legacy_helper():
    source = '''"""Intentionally contains Dead Code. Not refactored."""

def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    if number % 2 == 0:
        return "even"
        print("This line can never run")
    return "odd"

def main():
    value = 14
    if False:
        print("Legacy diagnostic mode")
    result = check_number(value)
    print(value, "is", result)

if __name__ == "__main__":
    main()
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_recovered_dead_callable",
        "language": "python",
        "source_files": [{
            "file_name": "05_dead_code_number_checker.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_recovered_dead_callable",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "literal_value": 14,
                        "constant_name": "MAIN_14",
                        "source_line": 13,
                        "source_file": "05_dead_code_number_checker.py",
                    },
                },
                {
                    "action_type": "remove_dead_code",
                    "parameters": {
                        "method": "05_dead_code_number_checker",
                        "class_name": "05_dead_code_number_checker",
                        "source_line": 1,
                        "source_file": "05_dead_code_number_checker.py",
                    },
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            **_options(),
            "enable_sctva_auto_refactoring": True,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "def old_number_format" not in result["refactored_code"]
    assert "This line can never run" not in result["refactored_code"]
    assert "Legacy diagnostic mode" not in result["refactored_code"]
    rdp_log = result["safety_report"]["transformation_log"][1]
    assert rdp_log["action_type"] == "remove_dead_code"
    assert rdp_log["replacements_count"] == 1
    assert rdp_log["metadata"]["final_decision"] == "PASS"
    assert any(
        "Recovered stale RDP Remove Dead Code target" in warning
        for warning in rdp_log["warnings"]
    )
    assert not any(
        "Dead-code removal skipped" in warning
        for entry in result["safety_report"]["transformation_log"]
        for warning in entry["warnings"]
    )


def test_c_dead_code_pipeline_removes_only_static_target_and_passes_validation():
    source = '''static int old_calculation(int value) {
    return value * 100;
}

int check_number(int value) {
    return value % 2 == 0;
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "c_dead_code_pipeline",
        "language": "c",
        "source_files": [{
            "file_name": "example.c", "source_code": source,
            "language": "c", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "c_dead_code_plan",
            "actions": [{"action_type": "remove_dead_code", "parameters": {"method": "old_calculation"}}],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(require_compilation=True),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "old_calculation" not in result["refactored_code"]
    assert result["validation"]["structural"]["details"]["dead_code_validation"][0]["passed"] is True
