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
    assert rdp_log["metadata"]["dead_code_target_resolution"] == (
        "stale_target_recovered_from_ast"
    )
    assert not rdp_log["warnings"]
    assert not any(
        "Recovered stale RDP Remove Dead Code target" in message
        for message in result["safety_report"]["human_messages"]
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


def test_python_sequential_dead_code_removals_use_action_snapshots():
    source = '''def old_alpha():
    return "alpha"

def old_beta():
    return "beta"

def live_value():
    return "live"
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "sequential_dead_removals",
        "language": "python",
        "source_files": [{
            "file_name": "helpers.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "sequential_dead_removals",
            "actions": [
                {"action_type": "remove_dead_code", "parameters": {"method": "old_alpha"}},
                {"action_type": "remove_dead_code", "parameters": {"method": "old_beta"}},
            ],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "old_alpha" not in result["refactored_code"]
    assert "old_beta" not in result["refactored_code"]
    validations = result["validation"]["structural"]["details"]["dead_code_validation"]
    assert len(validations) == 2
    assert all(item["checks"]["unrelated_source_preserved"] for item in validations)
    logs = result["safety_report"]["transformation_log"]
    assert all(
        entry["metadata"]["dead_code_removal_ledger_entry"]["validation_result"] == "APPLIED"
        for entry in logs
        if entry["action_type"] == "remove_dead_code"
    )


def test_python_cross_file_reference_is_not_deleted():
    helpers = '''def shared_format(value):
    return f"value={value}"

def obsolete_format(value):
    return f"old={value}"
'''
    caller = '''from helpers import shared_format

def render(value):
    return shared_format(value)
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "cross_file_deadness",
        "language": "python",
        "source_files": [
            {"file_name": "helpers.py", "source_code": helpers, "language": "python", "source_mode": "raw"},
            {"file_name": "caller.py", "source_code": caller, "language": "python", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "cross_file_deadness",
            "actions": [
                {"action_type": "remove_dead_code", "parameters": {"method": "shared_format", "source_file": "helpers.py"}},
                {"action_type": "remove_dead_code", "parameters": {"method": "obsolete_format", "source_file": "helpers.py"}},
            ],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    helper_result = next(
        item for item in result.get("file_results", [result])
        if item["file_name"] == "helpers.py"
    )
    assert helper_result["rollback_occurred"] is False
    assert "def shared_format" in helper_result["refactored_code"]
    assert "def obsolete_format" not in helper_result["refactored_code"]
    first_log = helper_result["safety_report"]["transformation_log"][0]
    assert first_log["metadata"]["dead_code_status"] == "NOT_DEAD"


def test_python_duplicate_dead_code_request_is_already_handled():
    source = '''def obsolete_helper():
    return 1
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "duplicate_dead_removal",
        "language": "python",
        "source_files": [{
            "file_name": "helpers.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "duplicate_dead_removal",
            "actions": [
                {"action_type": "remove_dead_code", "parameters": {"method": "obsolete_helper"}},
                {"action_type": "remove_dead_code", "parameters": {"method": "obsolete_helper"}},
            ],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    logs = result["safety_report"]["transformation_log"]
    assert logs[0]["metadata"]["final_decision"] == "PASS"
    assert logs[1]["metadata"]["final_decision"] == "ALREADY_HANDLED"


def test_python_later_live_removal_does_not_rollback_prior_dead_removal():
    source = '''def obsolete_helper():
    return "obsolete"

def active_helper(value):
    return value

def run(value):
    return active_helper(value)
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "selective_dead_code_action_outcome",
        "language": "python",
        "source_files": [{
            "file_name": "helpers.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "selective_dead_code_action_outcome",
            "actions": [
                {"action_type": "remove_dead_code", "parameters": {"method": "obsolete_helper"}},
                {"action_type": "remove_dead_code", "parameters": {"method": "active_helper"}},
            ],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "def obsolete_helper" not in result["refactored_code"]
    assert "def active_helper" in result["refactored_code"]
    logs = result["safety_report"]["transformation_log"]
    assert logs[0]["metadata"]["final_decision"] == "PASS"
    assert logs[1]["metadata"]["dead_code_status"] == "NOT_DEAD"


def test_python_unreferenced_class_is_removed_only_after_repository_proof():
    source = '''class LegacyFormatter:
    def format(self, value):
        return str(value)

def live_value():
    return "live"
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dead_class_removal",
        "language": "python",
        "source_files": [{
            "file_name": "formatters.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dead_class_removal",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {"class_name": "LegacyFormatter", "source_file": "formatters.py"},
            }],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert "class LegacyFormatter" not in result["refactored_code"]
    assert "def live_value" in result["refactored_code"]
