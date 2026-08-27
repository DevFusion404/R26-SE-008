from __future__ import annotations

import shutil

try:
    from sctva.agent import SafeCodeTransformationValidationAgent
    from sctva.integration.planner_adapter import PlannerAdapter
    from sctva.transformers import c_transformers
    from sctva.validators.syntax_validator import SyntaxValidator
except ModuleNotFoundError:  # Repository-root pytest invocation.
    from agents.transformation_agent.safe_code_transformation_agent.sctva.agent import (
        SafeCodeTransformationValidationAgent,
    )
    from agents.transformation_agent.safe_code_transformation_agent.sctva.integration.planner_adapter import (
        PlannerAdapter,
    )
    from agents.transformation_agent.safe_code_transformation_agent.sctva.transformers import (
        c_transformers,
    )
    from agents.transformation_agent.safe_code_transformation_agent.sctva.validators.syntax_validator import (
        SyntaxValidator,
    )


def _project(source: str, other_source: str = "") -> list[dict[str, str]]:
    files = [{
        "file_name": "invoice.c",
        "source_code": source,
        "language": "c",
        "source_mode": "raw",
    }]
    if other_source:
        files.append({
            "file_name": "consumer.c",
            "source_code": other_source,
            "language": "c",
            "source_mode": "raw",
        })
    return files


def test_rdp_remove_dead_code_survives_file_scoping_and_removes_static_function():
    source = '''#include <stdio.h>

static double legacy_discount_formula(double subtotal) {
    double obsolete_rate = 0.35;
    return subtotal * obsolete_rate;
}

double calculate_total(double subtotal) {
    return subtotal + 1.0;
}

int main(void) {
    printf("%.1f\\n", calculate_total(2.0));
    return 0;
}
'''
    planner_plan = PlannerAdapter().normalize_plan({
        "plan_id": "rdp_c_dead_code",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Introduce Constant",
                "parameters": {
                    "literal_value": 0.35,
                    "constant_name": "OBSOLETE_RATE",
                    "source_file": "invoice.c",
                },
            },
            {
                "step_id": 2,
                "refactoring": "Introduce Constant",
                "parameters": {
                    "literal_value": 1.0,
                    "constant_name": "TOTAL_INCREMENT",
                    "source_file": "invoice.c",
                },
            },
            {
                "step_id": 3,
                "refactoring": "Introduce Constant",
                "parameters": {
                    "literal_value": 2.0,
                    "constant_name": "SAMPLE_SUBTOTAL",
                    "source_file": "invoice.c",
                },
            },
            {
                "step_id": 4,
                "action_type": "Remove Dead Code",
                "target": "legacy_discount_formula",
            },
        ],
    })
    assert planner_plan["actions"][3]["action_type"] == "remove_dead_code"

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "c_dead_code_scope_regression",
        "language": "c",
        "source_files": [
            *_project(source),
            {
                "file_name": "invoice.h",
                "source_code": "double calculate_total(double subtotal);\n",
                "language": "c",
                "source_mode": "raw",
            },
        ],
        "refactoring_plan": planner_plan,
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": bool(shutil.which("gcc") or shutil.which("clang")),
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert "legacy_discount_formula" not in result["refactored_code"]
    log = next(
        item for item in result["safety_report"]["transformation_log"]
        if item["action_type"] == "remove_dead_code"
    )
    assert log["replacements_count"] == 1
    assert log["metadata"]["status"] == "pass"
    assert log["metadata"]["target_removed"] is True
    assert log["metadata"]["target"] == "legacy_discount_formula"
    assert log["metadata"]["dead_code_validation"] == "PASS"
    assert log["metadata"]["original_function_count"] == 3
    assert log["metadata"]["transformed_function_count"] == 2
    assert log["metadata"]["expected_removed"] == ["legacy_discount_formula"]
    structural = result["validation"]["structural"]["details"]
    assert structural["dead_code_validation_status"] == "PASS"
    assert result["validation"]["behavioral"]["passed"] is True


def test_used_static_main_cross_file_and_function_pointer_targets_are_preserved():
    used = '''static int live_helper(int value) { return value + 1; }
int run(void) { return live_helper(2); }
'''
    main_source = "int main(void) { return 0; }\n"
    cross_file = "static int shared_helper(int value) { return value + 1; }\n"
    consumer = "int consume(void) { return shared_helper(4); }\n"
    pointer = '''static int callback_target(int value) { return value + 1; }
int (*callback)(int) = callback_target;
'''
    macro = '''static int macro_target(int value) { return value + 1; }
#define RUN_TARGET(value) macro_target(value)
'''
    extern_use = '''static int declared_target(int value) { return value + 1; }
extern int declared_target(int value);
'''

    for source, target in (
        (used, "live_helper"),
        (main_source, "main"),
        (pointer, "callback_target"),
        (macro, "macro_target"),
        (extern_use, "declared_target"),
    ):
        transformed, count = c_transformers.apply_remove_dead_code(source, target)
        assert count == 0
        assert transformed == source

    transformed, count = c_transformers.apply_remove_dead_code(
        cross_file,
        "shared_helper",
        project_source_files=_project(cross_file, consumer),
        current_file_name="invoice.c",
        repository_complete=True,
    )
    assert count == 0
    assert transformed == cross_file


def test_removed_static_function_still_parses_or_compiles():
    source = '''static int obsolete(void) { return 41; }
int main(void) { return 0; }
'''
    transformed, count = c_transformers.apply_remove_dead_code(source, "obsolete")

    assert count == 1
    assert "obsolete" not in transformed
    validation = SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=bool(shutil.which("gcc") or shutil.which("clang")),
        timeout_seconds=10,
    )
    assert validation.passed is True, validation


def test_python_remove_dead_code_also_survives_mixed_file_scoping():
    source = '''def old_helper(value):
    return value * 100

def calculate(value):
    return value + 14
'''
    plan = PlannerAdapter().normalize_plan({
        "plan_id": "python_dead_code_scope",
        "steps": [
            {
                "refactoring": "Introduce Constant",
                "parameters": {
                    "literal_value": 14,
                    "constant_name": "INCREMENT",
                    "source_file": "calculator.py",
                },
            },
            {
                "refactoring": "Remove Dead Code",
                "target": {"function": "old_helper"},
            },
        ],
    })
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_dead_code_scope",
        "language": "python",
        "source_files": [
            {
                "file_name": "calculator.py",
                "source_code": source,
                "language": "python",
                "source_mode": "raw",
            },
            {
                "file_name": "settings.py",
                "source_code": "DEFAULT_VALUE = 2\n",
                "language": "python",
                "source_mode": "raw",
            },
        ],
        "refactoring_plan": plan,
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert "old_helper" not in result["refactored_code"]
    dead_log = next(
        item for item in result["safety_report"]["transformation_log"]
        if item["action_type"] == "remove_dead_code"
    )
    assert dead_log["replacements_count"] == 1
