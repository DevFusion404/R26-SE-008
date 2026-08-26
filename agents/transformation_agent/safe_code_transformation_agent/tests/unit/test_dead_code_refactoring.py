import ast
import shutil

from sctva.analysis.local_refactor_detector import LocalRefactorDetector
from sctva.contracts import RefactoringAction
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.transformers import c_transformers, python_transformers
from sctva.validators.structural_validator import StructuralValidator
from sctva.validators.syntax_validator import SyntaxValidator


def _action(*, method: str = "", source_line: int | None = None) -> RefactoringAction:
    return RefactoringAction(
        action_type="remove_dead_code",
        parameters={"method": method, "source_line": source_line},
    )


def _options() -> dict[str, object]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": 10,
        "require_compilation": False,
        "enable_sctva_auto_refactoring": False,
    }


def test_python_removes_targeted_unreferenced_function_and_preserves_docstring():
    source = '''"""Invoice helpers."""

def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    return "even" if number % 2 == 0 else "odd"
'''
    transformed, count = python_transformers.apply_remove_dead_code(
        source, "old_number_format"
    )

    assert count == 1
    assert "old_number_format" not in transformed
    assert ast.get_docstring(ast.parse(transformed)) == "Invoice helpers."
    assert "def check_number" in transformed


def test_python_line_target_resolves_and_removes_function_definition():
    source = '''"""Invoice helpers."""

def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    return number % 2 == 0
'''
    transformed, count = python_transformers.apply_remove_dead_code(
        source, "", source_line=3
    )

    assert count == 1
    assert "def old_number_format" not in transformed
    assert "def check_number" in transformed
    assert ast.get_docstring(ast.parse(transformed)) == "Invoice helpers."


def test_python_dead_code_recovers_unique_method_from_stale_class_and_line_hints():
    source = '''class LibraryManager:
    def remove_book(self, code):
        self.books.pop(code, None)

    def active_book(self, code):
        return self.books.get(code)
'''

    kind, fingerprint = python_transformers.resolve_dead_code_target(
        source,
        method_name="remove_book",
        class_name="02_large_class_library_system",
        source_line=99,
    )
    transformed, count = python_transformers.apply_remove_dead_code(
        source,
        "remove_book",
        class_name="02_large_class_library_system",
        source_line=99,
    )

    assert kind == "unused_callable"
    assert "remove_book" in fingerprint
    assert count == 1
    assert "def remove_book" not in transformed
    assert "def active_book" in transformed


def test_python_unused_callable_anchor_survives_unrelated_generated_getattr():
    original = '''class LibraryManager:
    def remove_book(self, code):
        self.books.pop(code, None)
'''
    kind, fingerprint = python_transformers.resolve_dead_code_target(
        original,
        method_name="remove_book",
        class_name="LibraryManager",
        source_line=2,
    )
    after_extract_class = '''class ForwardedMember:
    def __get__(self, instance, owner=None):
        return getattr(instance, self.member_name)

class LibraryManager:
    def remove_book(self, code):
        self.books.pop(code, None)
'''

    transformed, count = python_transformers.apply_remove_dead_code(
        after_extract_class,
        "remove_book",
        class_name="LibraryManager",
        source_line=2,
        dead_code_kind=kind,
        target_statement_fingerprint=fingerprint,
    )

    assert kind == "unused_callable"
    assert count == 1
    assert "def remove_book" not in transformed
    assert "def __get__" in transformed


def test_python_inferred_dead_code_owner_is_metadata_not_a_warning():
    source = '''class Catalog:
    def obsolete_lookup(self, code):
        return None

    def active_lookup(self, code):
        return code
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dead_code_owner_inference",
        "language": "python",
        "source_files": [{
            "file_name": "catalog.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dead_code_owner_inference",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {
                    "method": "obsolete_lookup",
                    "source_line": 2,
                    "source_file": "catalog.py",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    log = result["safety_report"]["transformation_log"][0]
    assert log["replacements_count"] == 1
    assert log["metadata"]["dead_code_target_resolution"] == "owner_inferred_from_ast"
    assert not log["warnings"]
    assert "transformation_warning" not in result["safety_report"]["risk_flags"]
    assert "def obsolete_lookup" not in result["refactored_code"]


def test_python_removes_unreachable_return_and_raise_statements():
    after_return = '''def value():
    return 1
    print("dead")
'''
    after_raise = '''def value():
    raise ValueError("bad")
    result = 2
'''

    transformed_return, return_count = python_transformers.apply_remove_dead_code(
        after_return, "", source_line=3
    )
    transformed_raise, raise_count = python_transformers.apply_remove_dead_code(
        after_raise, "", source_line=3
    )

    assert return_count == 1 and "print" not in transformed_return
    assert raise_count == 1 and "result = 2" not in transformed_raise
    ast.parse(transformed_return)
    ast.parse(transformed_raise)


def test_python_removes_targeted_constant_false_block_only():
    source = '''def check_number(number):
    if False:
        print("Legacy diagnostic mode")
    return number % 2 == 0
'''
    transformed, count = python_transformers.apply_remove_dead_code(
        source, "", source_line=2
    )

    assert count == 1
    assert "Legacy diagnostic mode" not in transformed
    assert "return number % 2 == 0" in transformed


def test_python_removes_only_the_selected_dead_candidate():
    source = '''def check_number(number):
    if False:
        print("first dead candidate")
    return number % 2 == 0
    print("second dead candidate")
'''
    transformed, count = python_transformers.apply_remove_dead_code(
        source, "", source_line=2
    )

    assert count == 1
    assert "first dead candidate" not in transformed
    assert "second dead candidate" in transformed


def test_python_keeps_referenced_dynamic_and_nested_functions():
    referenced = '''def old_number_format(number):
    return f"OLD-{number}"

def check_number(number):
    return old_number_format(number)
'''
    dynamic = '''def old_number_format(number):
    return number

value = getattr(__import__(__name__), "old_number_format", None)
'''
    nested = '''def outer():
    def old_number_format(number):
        return number
    return old_number_format(1)
'''

    for source in (referenced, dynamic, nested):
        transformed, count = python_transformers.apply_remove_dead_code(
            source, "old_number_format"
        )
        assert count == 0
        assert transformed == source


def test_python_constant_extraction_does_not_change_docstrings():
    source = '''"""Documentation mentions 14 and 2."""

def check_number(number):
    """The function documentation also mentions 14."""
    return number + 14
'''
    transformed, count = python_transformers.apply_extract_constant(
        source, 14, "CONSTANT_14", source_line=5
    )

    assert count == 1
    assert ast.get_docstring(ast.parse(transformed)) == "Documentation mentions 14 and 2."
    function = next(
        node for node in ast.parse(transformed).body
        if isinstance(node, ast.FunctionDef) and node.name == "check_number"
    )
    assert ast.get_docstring(function) == (
        "The function documentation also mentions 14."
    )
    assert "return number + CONSTANT_14" in transformed


def test_python_plan_does_not_add_unrequested_magic_number_or_string_actions():
    source = '''"""Keep this documentation unchanged."""

def check_number(number):
    if number % 2 == 0:
        return number + 14
    return "odd"
'''
    detector = LocalRefactorDetector()
    plan_action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"literal_value": 14, "constant_name": "CONSTANT_14"},
    )
    detected = detector.detect(
        language="python",
        file_name="example.py",
        source_code=source,
        existing_actions=[plan_action],
    )
    assert {action.action_type for action in detected} == {
        "introduce_constant"
    } or not detected

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dead_code_plan_scope",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dead_code_plan_scope",
            "actions": [{
                "action_type": "introduce_constant",
                "parameters": {
                    "literal_value": 14,
                    "constant_name": "CONSTANT_14",
                    "source_line": 5,
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": True,
        },
    })
    assert "CONSTANT_NUMBER_2" not in result["refactored_code"]
    assert "CONSTANT_STRING_EVEN" not in result["refactored_code"]


def test_python_multiple_unreachable_statements_are_removed_in_order():
    source = '''def value():
    return 1
    print("dead one")
    print("dead two")
'''
    actions = LocalRefactorDetector().detect(
        language="python",
        file_name="example.py",
        source_code=source,
        existing_actions=[],
    )
    dead_actions = [action for action in actions if action.action_type == "remove_dead_code"]
    assert len(dead_actions) >= 2


def test_python_plan_method_can_target_dead_statement_inside_live_method():
    source = '''def process_value(value):
    if False:
        print("legacy branch")
    return value
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dead_statement_inside_live_method",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dead_statement_inside_live_method",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {
                    "method": "process_value",
                    "source_line": 2,
                    "source_file": "example.py",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "legacy branch" not in result["refactored_code"]
    assert "def process_value" in result["refactored_code"]
    log = result["safety_report"]["transformation_log"][0]
    assert log["replacements_count"] == 1
    assert not any("Dead-code removal skipped" in warning for warning in log["warnings"])


def test_python_dead_statement_anchor_survives_literal_replacement():
    source = '''def process_value(value):
    if value > 0:
        return value
        print(14)
    return 0
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dead_statement_anchor_after_constant",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dead_statement_anchor_after_constant",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "literal_value": 14,
                        "constant_name": "CONSTANT_14",
                        "source_line": 4,
                        "source_file": "example.py",
                    },
                },
                {
                    "action_type": "remove_dead_code",
                    "parameters": {
                        "method": "process_value",
                        "source_line": 4,
                        "source_file": "example.py",
                    },
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "print(14)" not in result["refactored_code"]
    assert "print(CONSTANT_14)" not in result["refactored_code"]
    dead_log = [
        entry for entry in result["safety_report"]["transformation_log"]
        if entry["action_type"] == "remove_dead_code"
    ][0]
    assert dead_log["replacements_count"] == 1


def test_python_dynamic_function_reference_is_review_required_not_deleted():
    source = '''def old_number_format(number):
    return f"OLD-{number}"

value = getattr(__import__(__name__), "old_number_format", None)
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "dynamic_dead_code_reference",
        "language": "python",
        "source_files": [{
            "file_name": "example.py", "source_code": source,
            "language": "python", "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "dynamic_dead_code_reference",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {"method": "old_number_format"},
            }],
            "behavior_tests": [], "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })
    assert result["refactored_code"] == source
    log = next(
        entry for entry in result["safety_report"]["transformation_log"]
        if entry["action_type"] == "remove_dead_code"
    )
    assert log["metadata"]["status"] == "review_required"


def test_python_dead_code_removal_preserves_observable_output():
    source = '''def value(number):
    return number * 2
    print("never runs")
'''
    transformed, count = python_transformers.apply_remove_dead_code(
        source, "", source_line=3
    )
    before_namespace: dict[str, object] = {}
    after_namespace: dict[str, object] = {}
    exec(source, before_namespace)
    exec(transformed, after_namespace)
    assert count == 1
    assert [before_namespace["value"](item) for item in range(4)] == [
        after_namespace["value"](item) for item in range(4)
    ]


def test_python_structural_validation_requires_actual_target_removal():
    source = '''def old_number_format(number):
    return number

def check_number(number):
    return number
'''
    action = _action(method="old_number_format")
    transformed, _ = python_transformers.apply_remove_dead_code(source, "old_number_format")

    passed = StructuralValidator().validate(
        language="python", original_code=source, transformed_code=transformed, actions=[action]
    )
    failed = StructuralValidator().validate(
        language="python", original_code=source, transformed_code=source, actions=[action]
    )

    assert passed.passed is True
    assert passed.details["dead_code_validation"][0]["checks"]["target_removed_after"] is True
    assert failed.passed is False


def test_c_removes_unused_static_function_and_preserves_compilation():
    source = '''#include <stdio.h>

static int old_calculation(int value) {
    return value * 100;
}

int check_number(int value) {
    return value % 2 == 0;
}
'''
    transformed, count = c_transformers.apply_remove_dead_code(source, "old_calculation")

    assert count == 1
    assert "old_calculation" not in transformed
    assert "check_number" in transformed
    if shutil.which("gcc") or shutil.which("clang"):
        assert SyntaxValidator().validate(
            language="c", source_code=transformed, require_compilation=True, timeout_seconds=10
        ).passed is True


def test_c_removes_unreachable_and_constant_false_blocks():
    unreachable = '''#include <stdio.h>
int check_number(void) {
    return 1;
    puts("Never runs");
}
'''
    false_block = '''#include <stdio.h>
int check_number(void) {
    if (0) {
        puts("Legacy code");
    }
    return 1;
}
'''
    transformed_unreachable, unreachable_count = c_transformers.apply_remove_dead_code(
        unreachable, "", source_line=4
    )
    transformed_false, false_count = c_transformers.apply_remove_dead_code(
        false_block, "", source_line=3
    )

    assert unreachable_count == 1 and "Never runs" not in transformed_unreachable
    assert false_count == 1 and "Legacy code" not in transformed_false


def test_c_removes_only_the_selected_dead_candidate():
    source = '''#include <stdio.h>
int check_number(void) {
    if (0) {
        puts("first dead candidate");
    }
    return 1;
    puts("second dead candidate");
}
'''
    transformed, count = c_transformers.apply_remove_dead_code(
        source, "", source_line=3
    )

    assert count == 1
    assert "first dead candidate" not in transformed
    assert "second dead candidate" in transformed


def test_c_keeps_referenced_static_global_and_function_pointer_targets():
    referenced = '''static int old_calculation(int value) { return value * 100; }
int check_number(int value) { return old_calculation(value); }
'''
    global_function = '''int old_calculation(int value) { return value * 100; }
int check_number(int value) { return value; }
'''
    pointer = '''static int old_calculation(int value) { return value * 100; }
int (*callback)(int) = old_calculation;
'''

    for source in (referenced, global_function, pointer):
        transformed, count = c_transformers.apply_remove_dead_code(source, "old_calculation")
        assert count == 0
        assert transformed == source


def test_c_structural_validation_requires_exact_safe_removal():
    source = '''static int old_calculation(int value) { return value * 100; }
int check_number(int value) { return value; }
'''
    action = _action(method="old_calculation")
    transformed, _ = c_transformers.apply_remove_dead_code(source, "old_calculation")

    passed = StructuralValidator().validate(
        language="c", original_code=source, transformed_code=transformed, actions=[action]
    )
    failed = StructuralValidator().validate(
        language="c", original_code=source, transformed_code=source, actions=[action]
    )

    assert passed.passed is True
    assert passed.details["dead_code_validation"][0]["checks"]["unrelated_source_preserved"] is True
    assert failed.passed is False


def test_python_dead_code_anchor_marks_referenced_method_as_live_callable():
    source = '''class CustomerContact:
    def formatted_phone(self):
        return "Phone"

class Customer:
    def __init__(self):
        self.contact = CustomerContact()

    def display_details(self):
        return self.contact.formatted_phone()
'''

    kind, fingerprint = python_transformers.resolve_dead_code_target(
        source,
        source_line=2,
    )

    assert kind == "live_callable"
    assert "formatted_phone" in fingerprint


def test_python_false_positive_dead_code_plan_is_not_reported_as_warning():
    source = '''class CustomerContact:
    def __init__(self, phone):
        self.phone = phone

    def formatted_phone(self):
        return f"Phone: {self.phone}"

class Customer:
    def __init__(self, phone):
        self.contact = CustomerContact(phone)

    def display_details(self):
        return self.contact.formatted_phone()
'''

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "live_dead_code_plan_target",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "live_dead_code_plan_target",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {
                    "source_file": "example.py",
                    "source_line": 5,
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    log = result["safety_report"]["transformation_log"][0]
    assert log["action_type"] == "remove_dead_code"
    assert log["metadata"]["status"] == "not_applicable"
    assert log["metadata"]["final_decision"] == "NOT_APPLICABLE"
    assert log["metadata"]["dead_code_target_status"] == "live"
    assert not any(
        "Dead-code removal skipped" in message
        for message in result["safety_report"]["human_messages"]
    )
    assert result["refactored_code"] == source


def test_python_inline_class_plan_live_method_dead_code_steps_do_not_emit_skip_warnings():
    source = '''class CustomerContact:
    def __init__(self, phone):
        self.phone = phone

    def formatted_phone(self):
        return f"Phone: {self.phone}"

class Customer:
    def __init__(self, customer_id, name, phone):
        self.customer_id = customer_id
        self.name = name
        self.contact = CustomerContact(phone)

    def display_details(self):
        print(self.contact.formatted_phone())

    def get_phone(self):
        return self.contact.phone

def main():
    customer = Customer("C001", "Nimal", "077")
    customer.display_details()
    print(customer.get_phone())
'''

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "inline_with_false_dead_code_steps",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "inline_with_false_dead_code_steps",
            "actions": [
                {
                    "action_type": "inline_python_class",
                    "parameters": {
                        "class_to_inline": "CustomerContact",
                        "destination_class": "Customer",
                        "owner_attribute": "contact",
                        "source_file": "example.py",
                    },
                },
                {
                    "action_type": "remove_dead_code",
                    "parameters": {"source_file": "example.py", "source_line": 5},
                },
                {
                    "action_type": "remove_dead_code",
                    "parameters": {"source_file": "example.py", "source_line": 14},
                },
                {
                    "action_type": "remove_dead_code",
                    "parameters": {"source_file": "example.py", "source_line": 17},
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert "class CustomerContact" not in result["refactored_code"]
    assert not any(
        "Dead-code removal skipped" in message
        for message in result["safety_report"]["human_messages"]
    )
    dead_logs = [
        item for item in result["safety_report"]["transformation_log"]
        if item["action_type"] == "remove_dead_code"
    ]
    assert dead_logs
    assert all(item["metadata"]["status"] == "not_applicable" for item in dead_logs)
