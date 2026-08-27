import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.analysis import LocalRefactorDetector
from sctva.contracts import RefactoringAction
from sctva.constants import ACTION_ENCAPSULATE_VARIABLE
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import c_transformers
from sctva.transformers.engine import TransformationEngine
from sctva.validators.structural_validator import StructuralValidator


MUTABLE_GLOBAL_SOURCE = '''#include <stdio.h>

int stock_count = 10;
int unrelated_total = 7;

void sell_item(void) {
    stock_count--;
}

void receive_items(void) {
    stock_count += 2;
}

int current_stock(void) {
    return stock_count;
}

int main(void) {
    sell_item();
    receive_items();
    printf("%d %d\\n", current_stock(), unrelated_total);
    return 0;
}
'''


def _transform(source=MUTABLE_GLOBAL_SOURCE):
    return c_transformers.apply_encapsulate_c_variable(
        source,
        variable_name="stock_count",
        getter_name="get_stock_count",
        setter_name="set_stock_count",
    )


def _run_c(source: str) -> str:
    gcc = shutil.which("gcc")
    if not gcc:
        pytest.skip("gcc is not available")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source_path = root / "inventory.c"
        executable = root / "inventory.exe"
        source_path.write_text(source, encoding="utf-8")
        subprocess.run(
            [gcc, "-std=c11", "-Wall", "-Werror", str(source_path), "-o", str(executable)],
            check=True,
            capture_output=True,
            text=True,
        )
        return subprocess.run(
            [str(executable)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout


def test_encapsulate_c_global_rewrites_reads_writes_and_updates():
    transformed, replacements, metadata = _transform()

    assert metadata["status"] == "success"
    assert replacements >= 4
    assert "static int stock_count = 10;" in transformed
    assert "int get_stock_count(void)" in transformed
    assert "void set_stock_count(int value)" in transformed
    assert "set_stock_count(get_stock_count() - 1);" in transformed
    assert "set_stock_count(get_stock_count() + (2));" in transformed
    assert "return get_stock_count();" in transformed
    assert "int unrelated_total = 7;" in transformed


def test_encapsulate_c_global_preserves_compilation_and_output():
    transformed, _, metadata = _transform()
    assert metadata["status"] == "success"
    assert _run_c(MUTABLE_GLOBAL_SOURCE) == _run_c(transformed) == "11 7\n"


def test_const_c_global_gets_a_read_only_accessor_without_setter():
    source = '''const int max_items = 100;
int read_max(void) { return max_items; }
int main(void) { return read_max() == 100 ? 0 : 1; }
'''
    transformed, _, metadata = c_transformers.apply_encapsulate_c_variable(
        source,
        variable_name="max_items",
    )

    assert metadata["status"] == "success"
    assert "static const int max_items = 100;" in transformed
    assert "int get_max_items(void)" in transformed
    assert "set_max_items" not in transformed
    assert "return get_max_items();" in transformed


@pytest.mark.parametrize("source, reason", [
    ("int stock_count = 1;\nvoid f(void) { use(&stock_count); }\n", "ADDRESS_OR_ARRAY_ACCESS_UNSUPPORTED"),
    ("extern int stock_count;\nint f(void) { return stock_count; }\n", "EXTERN_GLOBAL_UNSUPPORTED"),
    ("int stock_count[2] = {1, 2};\nint f(void) { return stock_count[0]; }\n", "GLOBAL_ARRAY_UNSUPPORTED"),
])
def test_unsafe_c_global_shapes_require_review(source, reason):
    transformed, replacements, metadata = c_transformers.apply_encapsulate_c_variable(
        source,
        variable_name="stock_count",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == reason


def test_structural_validation_rejects_remaining_direct_global_access():
    transformed, _, metadata = _transform()
    action = RefactoringAction(
        action_type="encapsulate_c_variable",
        parameters=metadata["effective_action_parameters"],
    )
    result = StructuralValidator().validate(
        language="c",
        original_code=MUTABLE_GLOBAL_SOURCE,
        transformed_code=transformed,
        actions=[action],
    )
    assert result.passed is True, result.details
    validation = result.details["c_global_variable_validation"][0]
    assert validation["passed"] is True

    broken = transformed.replace("return get_stock_count();", "return stock_count;", 1)
    invalid = StructuralValidator().validate(
        language="c",
        original_code=MUTABLE_GLOBAL_SOURCE,
        transformed_code=broken,
        actions=[action],
    )
    assert invalid.passed is False
    assert invalid.details["c_global_variable_validation"][0]["checks"]["direct_unsafe_accesses_replaced"] is False


def test_local_detector_finds_clear_mutable_shared_c_global_only():
    actions = LocalRefactorDetector().detect(
        language="c",
        file_name="inventory.c",
        source_code=MUTABLE_GLOBAL_SOURCE,
        existing_actions=[],
    )
    globals_found = [action for action in actions if action.action_type == "encapsulate_c_variable"]
    assert len(globals_found) == 1
    assert globals_found[0].parameters["variable_name"] == "stock_count"

    local_only = '''int main(void) {
    int local_counter = 0;
    local_counter++;
    return local_counter;
}
'''
    assert not [
        action for action in LocalRefactorDetector().detect(
            language="c",
            file_name="local.c",
            source_code=local_only,
            existing_actions=[],
        )
        if action.action_type == "encapsulate_c_variable"
    ]


def test_engine_and_full_sctva_pipeline_apply_global_variable_refactoring():
    action = RefactoringAction(
        action_type="encapsulate_c_variable",
        parameters={"variable_name": "stock_count"},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="c",
        source_code=MUTABLE_GLOBAL_SOURCE,
        actions=[action],
        strict_mode=True,
    )
    assert logs[0].metadata["status"] == "success"
    assert "static int stock_count = 10;" in transformed

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "c_global_variable_pipeline",
        "language": "c",
        "source_code": MUTABLE_GLOBAL_SOURCE,
        "refactoring_plan": {
            "plan_id": "c_global_variable_plan",
            "actions": [{
                "action_type": "encapsulate_c_variable",
                "parameters": {"variable_name": "stock_count"},
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "require_compilation": True,
            "enable_sctva_auto_refactoring": False,
        },
    })
    assert result["success"] is True, result
    assert result["plan_compliance"]["global_variable"] == "PASS"
    assert "static int stock_count = 10;" in result["refactored_code"]


def test_planner_maps_global_variable_and_c_move_method_separately():
    adapter = PlannerAdapter()
    global_plan = adapter.normalize_plan({
        "plan_id": "global_variable",
        "steps": [{
            "step_id": 1,
            "refactoring": "Global Variable",
            "target": {"file": "inventory.c", "variable": "stock_count"},
            "parameters": {"source_file": "inventory.c"},
        }],
    })
    assert global_plan["actions"][0]["action_type"] == "encapsulate_c_variable"
    assert global_plan["actions"][0]["parameters"]["getter_name"] == "get_stock_count"

    move_plan = adapter.normalize_plan({
        "plan_id": "move_function",
        "steps": [{
            "step_id": 1,
            "refactoring": "Move Method",
            "target": {"file": "inventory.c", "function": "calculate_stock_value"},
            "parameters": {
                "source_file": "inventory.c",
                "destination_file": "stock_utils.c",
            },
        }],
    })
    assert move_plan["actions"][0]["action_type"] == "move_c_function"


# ---------------------------------------------------------------------------
# Regression coverage for malformed RDP Global Variable placeholders
# ---------------------------------------------------------------------------

def test_c_encapsulate_variable_does_not_misread_greater_than_as_member_access():
    source = """
int total_stock = 100;

int can_sell(int quantity) {
    if (quantity > total_stock) {
        return 0;
    }
    total_stock -= quantity;
    return 1;
}
"""
    transformed, replacements, metadata = c_transformers.apply_encapsulate_c_variable(
        source,
        variable_name="total_stock",
        getter_name="get_total_stock",
        setter_name="set_total_stock",
    )
    assert metadata["status"] == "success"
    assert replacements > 0
    assert "quantity > get_total_stock()" in transformed
    assert "set_total_stock(get_total_stock() - (quantity))" in transformed or \
        "set_total_stock(get_total_stock() - quantity)" in transformed


def test_c_initializer_validation_accepts_equivalent_introduced_constant_macro():
    original = """
int total_stock = 100;
int read_stock(void) { return total_stock; }
"""
    transformed, replacements, metadata = c_transformers.apply_encapsulate_c_variable(
        original,
        variable_name="total_stock",
        getter_name="get_total_stock",
        setter_name="set_total_stock",
    )
    assert metadata["status"] == "success"
    assert replacements > 0
    transformed = "#define CONSTANT_100 100\n" + transformed.replace(
        "static int total_stock = 100;",
        "static int total_stock = CONSTANT_100;",
    )
    validation = c_transformers.validate_c_encapsulated_variable(
        original,
        transformed,
        variable_name="total_stock",
        getter_name="get_total_stock",
        setter_name="set_total_stock",
    )
    assert validation["checks"]["original_initializer_preserved"] is True


def test_engine_recovers_three_generic_c_global_variable_targets_in_declaration_order():
    source = """
#include <stdio.h>
int total_stock = 100;
int sold_items = 0;
double total_sales = 0.0;
void sell_item(int quantity, double price) {
    if (quantity > total_stock) return;
    total_stock -= quantity;
    sold_items += quantity;
    total_sales += quantity * price;
}
void summary(void) {
    printf("%d %d %.2f\n", total_stock, sold_items, total_sales);
}
"""
    actions = [
        RefactoringAction(
            action_type=ACTION_ENCAPSULATE_VARIABLE,
            source_refactoring="Encapsulate Variable",
            parameters={
                "variable_name": "variable",
                "getter_name": "get_variable",
                "setter_name": "set_variable",
                "source_file": "inventory.c",
                "source_line": line,
            },
        )
        for line in (6, 7, 8)
    ]
    engine = TransformationEngine()
    transformed, logs, _ = engine.apply_actions(
        language="c",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="inventory.c",
    )
    assert [action.parameters["variable_name"] for action in actions] == [
        "total_stock",
        "sold_items",
        "total_sales",
    ]
    assert all(log.replacements_count > 0 for log in logs)
    assert "static int total_stock" in transformed
    assert "static int sold_items" in transformed
    assert "static double total_sales" in transformed
    assert "get_total_stock()" in transformed
    assert "get_sold_items()" in transformed
    assert "get_total_sales()" in transformed
