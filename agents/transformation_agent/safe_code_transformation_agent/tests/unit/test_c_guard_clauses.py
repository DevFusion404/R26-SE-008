"""Unit tests for C Replace Nested Conditional with Guard Clauses in SCTVA."""

from agents.transformation_agent.safe_code_transformation_agent.sctva.agent import (
    SafeCodeTransformationValidationAgent,
)
from agents.transformation_agent.safe_code_transformation_agent.sctva.constants import (
    ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES,
    ACTION_NOOP,
)
from agents.transformation_agent.safe_code_transformation_agent.sctva.integration.planner_adapter import (
    PlannerAdapter,
)
from agents.transformation_agent.safe_code_transformation_agent.sctva.contracts import (
    RefactoringAction,
)
from agents.transformation_agent.safe_code_transformation_agent.sctva.transformers.c_guard_clauses import (
    _invert_c_condition,
    _deduce_return_statement,
    apply_replace_nested_conditional_with_guard_clauses,
    validate_c_guard_clauses,
)
from agents.transformation_agent.safe_code_transformation_agent.sctva.validators.structural_validator import (
    StructuralValidator,
)


DEEP_NESTING_C = """#include <stdio.h>

int check_deep(int a, int b, int c, int d, int e) {
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}
"""

POINTER_NESTED_C = """#include <stdio.h>
#include <stdlib.h>

char* find_data(int a, int b, int c, int d, int e) {
    if (a >= 10) {
        if (b != 0) {
            if (c < 100) {
                if (d <= 50) {
                    if (e == 1) {
                        return "SUCCESS";
                    }
                }
            }
        }
    }
    return NULL;
}
"""

VOID_NESTED_C = """#include <stdio.h>

void perform_action(int a, int b, int c, int d, int e) {
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        printf("All conditions met\\n");
                    }
                }
            }
        }
    }
}
"""

LOOP_NESTED_C = """#include <stdio.h>

void process_items(int n) {
    for (int i = 0; i < n; i++) {
        if (i > 2) {
            if (i % 2 == 0) {
                if (i < 50) {
                    if (i != 10) {
                        if (i != 20) {
                            printf("Item: %d\\n", i);
                        }
                    }
                }
            }
        }
    }
}
"""

NON_TERMINAL_NESTED_C = """#include <stdio.h>

int compute_val(int a, int b, int c, int d, int e) {
    int total = 0;
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        total = a + b + c + d + e;
                    }
                }
            }
        }
    }
    return total;
}
"""


def test_invert_c_condition():
    assert _invert_c_condition("a > 0") == "a <= 0"
    assert _invert_c_condition("a >= 10") == "a < 10"
    assert _invert_c_condition("b < 5") == "b >= 5"
    assert _invert_c_condition("b <= 0") == "b > 0"
    assert _invert_c_condition("ptr == NULL") == "ptr != NULL"
    assert _invert_c_condition("status != 0") == "status == 0"
    assert _invert_c_condition("!is_ready()") == "is_ready()"
    assert _invert_c_condition("(x > 0 && y < 10)") == "!(x > 0 && y < 10)"
    assert _invert_c_condition("!validate_input(a, b)") == "validate_input(a, b)"


def test_apply_replace_nested_conditional_terminal_return():
    transformed, count, meta = apply_replace_nested_conditional_with_guard_clauses(
        DEEP_NESTING_C,
        method_name="check_deep",
    )
    assert count == 1
    assert meta["status"] == "success"
    assert meta["before_nesting_depth"] == 5
    assert meta["after_nesting_depth"] <= 1
    assert meta["nesting_reduced"] is True
    assert meta["smell_reduction"] == "PASS"
    assert "if (a <= 0)" in transformed
    assert "if (b <= 0)" in transformed
    assert "if (c <= 0)" in transformed
    assert "if (d <= 0)" in transformed
    assert "if (e <= 0)" in transformed
    assert "return 0;" in transformed
    assert "return 1;" in transformed


def test_apply_replace_nested_conditional_pointer_return():
    transformed, count, meta = apply_replace_nested_conditional_with_guard_clauses(
        POINTER_NESTED_C,
        method_name="find_data",
    )
    assert count == 1
    assert meta["status"] == "success"
    assert meta["after_nesting_depth"] <= 1
    assert meta["nesting_reduced"] is True
    assert "if (a < 10)" in transformed
    assert "if (b == 0)" in transformed
    assert "if (c >= 100)" in transformed
    assert "if (d > 50)" in transformed
    assert "if (e != 1)" in transformed
    assert "return NULL;" in transformed
    assert 'return "SUCCESS";' in transformed


def test_apply_replace_nested_conditional_void_return():
    transformed, count, meta = apply_replace_nested_conditional_with_guard_clauses(
        VOID_NESTED_C,
        method_name="perform_action",
    )
    assert count == 1
    assert meta["status"] == "success"
    assert meta["after_nesting_depth"] <= 1
    assert meta["nesting_reduced"] is True
    assert "if (a <= 0)" in transformed
    assert "return;" in transformed
    assert 'printf("All conditions met\\n");' in transformed


def test_apply_replace_nested_conditional_inside_loop():
    transformed, count, meta = apply_replace_nested_conditional_with_guard_clauses(
        LOOP_NESTED_C,
        method_name="process_items",
    )
    assert count == 1
    assert meta["status"] == "success"
    assert meta["after_nesting_depth"] <= 2  # for loop (1) + guard ifs (1)
    assert meta["nesting_reduced"] is True
    assert "continue;" in transformed


def test_apply_replace_nested_conditional_non_terminal():
    transformed, count, meta = apply_replace_nested_conditional_with_guard_clauses(
        NON_TERMINAL_NESTED_C,
        method_name="compute_val",
    )
    assert count == 1
    assert meta["status"] == "success"
    assert meta["after_nesting_depth"] <= 1
    assert meta["nesting_reduced"] is True


def test_validate_c_guard_clauses():
    transformed, count, _ = apply_replace_nested_conditional_with_guard_clauses(
        DEEP_NESTING_C,
        method_name="check_deep",
    )
    res = validate_c_guard_clauses(DEEP_NESTING_C, transformed, method="check_deep")
    assert res["passed"] is True
    assert res["checks"]["nesting_depth_reduced"] is True
    assert res["checks"]["below_cuqa_threshold"] is True
    assert res["smell_reduction"] == "PASS"


def test_structural_validator_accepts_guard_clauses():
    transformed, _, _ = apply_replace_nested_conditional_with_guard_clauses(
        DEEP_NESTING_C,
        method_name="check_deep",
    )
    action = RefactoringAction(
        action_type=ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES,
        parameters={"method": "check_deep"},
    )
    res = StructuralValidator().validate(
        language="c",
        original_code=DEEP_NESTING_C,
        transformed_code=transformed,
        actions=[action],
    )
    assert res.passed is True


def test_planner_adapter_normalizes_guard_clauses():
    adapter = PlannerAdapter()
    plan = {
        "plan_id": "test_deep_nesting_plan",
        "steps": [
            {
                "step_id": "step_1",
                "refactoring": "Replace Nested Conditional with Guard Clauses",
                "target": {
                    "file": "deep_nesting.c",
                    "method": "check_deep",
                    "lines": [4, 16],
                    "smell_type": "Deep Nesting",
                },
            }
        ],
    }
    norm = adapter.normalize_plan(plan)
    action = norm["actions"][0]
    assert action["action_type"] == ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES
    assert action["parameters"]["method"] == "check_deep"


def test_agent_end_to_end_c_guard_clauses():
    agent = SafeCodeTransformationValidationAgent()
    adapter = PlannerAdapter()
    plan = {
        "plan_id": "plan_deep_nesting",
        "steps": [
            {
                "step_id": "step_1",
                "refactoring": "Replace Nested Conditional with Guard Clauses",
                "target": {
                    "file": "deep_nesting.c",
                    "method": "check_deep",
                    "lines": [4, 16],
                    "smell_type": "Deep Nesting",
                },
            }
        ],
    }
    normalized_plan = adapter.normalize_plan(plan)
    result = agent.execute(
        {
            "request_id": "req_deep_nesting",
            "language": "c",
            "source_code": DEEP_NESTING_C,
            "source_files": [
                {
                    "file_name": "deep_nesting.c",
                    "source_code": DEEP_NESTING_C,
                    "language": "c",
                }
            ],
            "refactoring_plan": normalized_plan,
        }
    )
    assert result["success"] is True
    assert result["transformation_applied"] is True
    assert result["confidence_score"] >= 0.8
    assert "if (a <= 0)" in result["refactored_code"]
    report = result["safety_report"]
    log_entry = report["transformation_log"][0]
    assert log_entry["metadata"]["smell_reduction"] == "PASS"
    assert log_entry["metadata"]["final_checks"]["nesting_depth_reduction"] == "PASS"


def test_agent_promotes_noop_guard_clauses():
    agent = SafeCodeTransformationValidationAgent()
    # Plan with legacy noop step whose source_refactoring is "Deep Nesting"
    result = agent.execute(
        {
            "request_id": "req_deep_nesting_noop",
            "language": "c",
            "source_code": DEEP_NESTING_C,
            "source_files": [
                {
                    "file_name": "deep_nesting.c",
                    "source_code": DEEP_NESTING_C,
                    "language": "c",
                }
            ],
            "refactoring_plan": {
                "plan_id": "plan_deep_nesting_noop",
                "actions": [
                    {
                        "action_type": ACTION_NOOP,
                        "source_refactoring": "Replace Nested Conditional with Guard Clauses",
                        "parameters": {"method": "check_deep"},
                        "warnings": ["mapped to noop"],
                    }
                ],
            },
        }
    )
    assert result["success"] is True
    assert result["transformation_applied"] is True
    assert "if (a <= 0)" in result["refactored_code"]


def test_agent_review_required_on_no_nested_conditional():
    flat_code = """#include <stdio.h>

int simple_sum(int a, int b) {
    return a + b;
}
"""
    agent = SafeCodeTransformationValidationAgent()
    adapter = PlannerAdapter()
    plan = {
        "plan_id": "plan_no_nesting",
        "steps": [
            {
                "step_id": "step_1",
                "refactoring": "Replace Nested Conditional with Guard Clauses",
                "target": {
                    "file": "simple.c",
                    "method": "simple_sum",
                    "lines": [3, 5],
                    "smell_type": "Deep Nesting",
                },
            }
        ],
    }
    normalized_plan = adapter.normalize_plan(plan)
    result = agent.execute(
        {
            "request_id": "req_no_nesting",
            "language": "c",
            "source_code": flat_code,
            "source_files": [
                {
                    "file_name": "simple.c",
                    "source_code": flat_code,
                    "language": "c",
                }
            ],
            "refactoring_plan": normalized_plan,
        }
    )
    assert result["transformation_applied"] is False
    assert result["refactored_code"] == flat_code
