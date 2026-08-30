import ast
import contextlib
import io

from sctva.analysis.local_refactor_detector import LocalRefactorDetector
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction, SCTVARequestContract
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers.engine import TransformationEngine
from sctva.transformers import java_transformers, python_transformers
from sctva.validators.structural_validator import StructuralValidator


def _exception_actions(language: str, source: str):
    return [
        action
        for action in LocalRefactorDetector().detect(
            language=language,
            file_name=f"example.{ 'py' if language == 'python' else 'java' }",
            source_code=source,
            existing_actions=[],
        )
        if action.action_type == "narrow_exception_handler"
    ]


def test_python_bare_except_is_narrowed_without_changing_handler_body():
    source = '''def parse(value):
    try:
        raise ValueError("bad value")
    except:  # legacy catch-all
        return "fallback"
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type=action.parameters["original_exception_type"],
        target_exception_type=action.parameters["target_exception_type"],
        handler_name=action.parameters["handler_name"],
    )

    assert count == 1
    assert "except ValueError:  # legacy catch-all" in transformed
    assert "return \"fallback\"" in transformed
    assert ast.parse(transformed)


def test_python_exception_overreach_is_narrowed_only_for_explicit_type():
    source = '''def parse(value):
    try:
        if not value:
            raise ValueError("value is required")
    except Exception as exc:
        return str(exc)
'''

    actions = _exception_actions("python", source)
    assert len(actions) == 1
    action = actions[0]
    assert action.parameters["target_exception_type"] == "ValueError"

    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type=action.parameters["original_exception_type"],
        target_exception_type=action.parameters["target_exception_type"],
        handler_name=action.parameters["handler_name"],
    )
    assert count == 1
    assert "except ValueError as exc:" in transformed


def test_python_exception_overreach_without_direct_evidence_is_not_auto_refactored():
    source = '''def parse(value):
    try:
        return dependency(value)
    except Exception:
        return None
'''

    assert _exception_actions("python", source) == []


def test_python_exception_overreach_splits_numeric_conversion_near_operation():
    source = '''def checkout(raw_quantity, raw_price):
    label = "order"
    try:
        quantity = int(raw_quantity)
        price = float(raw_price)
        total = quantity * price
    except Exception as error:
        print("Invalid numeric input.")
        return None
    return label, total
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type=action.parameters["original_exception_type"],
        target_exception_type=action.parameters["target_exception_type"],
        handler_name=action.parameters["handler_name"],
    )

    assert count == 2
    assert "except Exception" not in transformed
    assert transformed.count("except ValueError as error:") == 2
    assert "total = quantity * price" in transformed
    assert ast.parse(transformed)


def test_python_exception_overreach_splits_dictionary_lookup_to_key_error():
    source = '''def price_for(product_code):
    product_prices = {"A": 10}
    try:
        catalog_price = product_prices[product_code]
        tax = catalog_price * 0.1
    except Exception:
        return None
    return catalog_price + tax
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
    )

    assert count == 1
    assert "except KeyError:" in transformed
    assert "except Exception" not in transformed
    assert "tax = catalog_price * 0.1" in transformed


def test_python_exception_overreach_splits_list_access_to_index_error():
    source = '''def first_item(index):
    items = ["a", "b"]
    try:
        selected = items[index]
        return selected.upper()
    except Exception:
        return ""
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
    )

    assert count == 1
    assert "except IndexError:" in transformed
    assert "except Exception" not in transformed


def test_python_exception_overreach_splits_zero_division():
    source = '''def ratio(total, count):
    try:
        average = total / count
        return round(average, 2)
    except Exception as error:
        return str(error)
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
        handler_name="error",
    )

    assert count == 1
    assert "except ZeroDivisionError as error:" in transformed
    assert "except Exception" not in transformed


def test_python_exception_overreach_splits_file_access_to_os_error():
    source = '''def write_audit(data):
    try:
        with open("order_audit.txt", "a", encoding="utf-8") as file:
            file.write(data)
        return True
    except Exception as error:
        print("Unable to write audit file.")
        return False
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
        handler_name="error",
    )

    assert count == 1
    assert "except OSError as error:" in transformed
    assert "except Exception" not in transformed


def test_python_exception_overreach_multiple_risky_operations_get_specific_handlers():
    source = '''def checkout(raw_quantity, product_code, divisor):
    product_prices = {"A": 10}
    try:
        quantity = int(raw_quantity)
        catalog_price = product_prices[product_code]
        unit = catalog_price / divisor
        print("done")
    except Exception as error:
        print(error)
        return None
    return quantity * unit
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
        handler_name="error",
    )

    assert count == 3
    assert "except ValueError as error:" in transformed
    assert "except KeyError as error:" in transformed
    assert "except ZeroDivisionError as error:" in transformed
    assert "except Exception" not in transformed
    assert "print(\"done\")" in transformed


def _run_checkout(source: str, raw_quantity: str, product_code: str, divisor: int):
    namespace: dict[str, object] = {}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(source, "<exception-overreach-test>", "exec"), namespace)
        result = namespace["checkout"](raw_quantity, product_code, divisor)
    return result, output.getvalue()


def test_python_exception_overreach_preserves_valid_input_behavior():
    source = '''def checkout(raw_quantity, product_code, divisor):
    product_prices = {"A": 10}
    try:
        quantity = int(raw_quantity)
        catalog_price = product_prices[product_code]
        unit = catalog_price / divisor
        print("done")
    except Exception as error:
        print(f"failed: {error}")
        return None
    return quantity * unit
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
        handler_name="error",
    )

    assert count == 3
    assert _run_checkout(source, "4", "A", 2) == _run_checkout(transformed, "4", "A", 2)


def test_python_exception_overreach_preserves_expected_error_behavior():
    source = '''def checkout(raw_quantity, product_code, divisor):
    product_prices = {"A": 10}
    try:
        quantity = int(raw_quantity)
        catalog_price = product_prices[product_code]
        unit = catalog_price / divisor
        print("done")
    except Exception as error:
        print(f"failed: {error}")
        return None
    return quantity * unit
'''

    action = _exception_actions("python", source)[0]
    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type="Exception",
        target_exception_type=action.parameters["target_exception_type"],
        handler_name="error",
    )

    assert count == 3
    for args in (("invalid", "A", 2), ("4", "missing", 2), ("4", "A", 0)):
        assert _run_checkout(source, *args) == _run_checkout(transformed, *args)


def test_python_exception_overreach_uncertain_call_is_review_required_no_action():
    source = '''def load(value):
    try:
        return dependency(value)
    except Exception as error:
        return str(error)
'''

    transformed, count = python_transformers.apply_narrow_exception_handler(
        source,
        source_line=4,
        original_exception_type="Exception",
        target_exception_type="",
        handler_name="error",
    )

    assert count == 0
    assert transformed == source


def test_exception_overreach_planner_maps_to_dedicated_exception_action():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "exception_overreach",
        "steps": [{
            "step_id": 1,
            "smell": "Exception Overreach",
            "refactoring": "Exception Overreach",
            "target": {"file": "checkout.py", "lines": [5]},
            "parameters": {"source_file": "checkout.py"},
        }],
    })

    action = normalized["actions"][0]
    assert action["action_type"] == "narrow_exception_handler"
    assert action["parameters"]["original_exception_type"] == "Exception"


def test_exception_overreach_structural_validation_fails_when_broad_handler_remains():
    source = '''def checkout(raw_quantity):
    try:
        quantity = int(raw_quantity)
        print("done")
    except Exception as error:
        return None
    return quantity
'''
    transformed = source
    action = RefactoringAction(
        action_type="narrow_exception_handler",
        parameters={
            "source_line": 5,
            "original_exception_type": "Exception",
            "target_exception_type": "ValueError",
            "handler_name": "error",
        },
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=source,
        transformed_code=transformed,
        actions=[action],
    )

    assert result.passed is False
    checks = result.details["exception_handler_validation"][0]["checks"]
    assert checks["broad_exception_removed_or_meaningfully_narrowed"] is False


def test_java_exception_overreach_is_narrowed_for_direct_throw():
    source = '''class Parser {
    String parse(String value) {
        try {
            throw new IllegalArgumentException("bad value");
        } catch (Exception ex) {
            return ex.getMessage();
        }
    }
}
'''

    actions = _exception_actions("java", source)
    assert len(actions) == 1
    action = actions[0]
    assert action.parameters["target_exception_type"] == "IllegalArgumentException"

    transformed, count = java_transformers.apply_narrow_exception_handler(
        source,
        source_line=action.parameters["source_line"],
        original_exception_type=action.parameters["original_exception_type"],
        target_exception_type=action.parameters["target_exception_type"],
        handler_name=action.parameters["handler_name"],
    )
    assert count == 1
    assert "catch (IllegalArgumentException ex)" in transformed


def test_java_handler_with_multiple_explicit_types_is_left_for_review():
    source = '''class Parser {
    String parse(boolean value) {
        try {
            if (value) throw new IllegalArgumentException();
            throw new IllegalStateException();
        } catch (Exception ex) {
            return ex.getMessage();
        }
    }
}
'''

    assert _exception_actions("java", source) == []


def test_python_exception_refactoring_runs_through_the_full_sctva_pipeline():
    source = '''def parse(value):
    try:
        if not value:
            raise ValueError("value is required")
        return value.upper()
    except Exception as exc:
        return str(exc)
'''

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "narrow_python_exception",
        "language": "python",
        "source_files": [{
            "file_name": "example.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "narrow_python_exception",
            "actions": [],
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

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert "except ValueError as exc:" in result["refactored_code"]


def test_rdp_exception_action_without_type_uses_only_proven_local_inference():
    source = '''def parse(value):
    try:
        raise ValueError("value is required")
    except Exception as exc:
        return str(exc)
'''

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=source,
        actions=[RefactoringAction(
            action_type="narrow_exception_handler",
            parameters={
                "source_line": 4,
                "original_exception_type": "Exception",
                "handler_name": "exc",
            },
        )],
        strict_mode=True,
    )

    assert "except ValueError as exc:" in transformed
    assert logs[0].replacements_count == 1
    assert warnings == []


def test_legacy_rdp_dead_code_action_for_bare_except_is_safely_reclassified():
    source = '''def lookup(student_id):
    try:
        raise ValueError("not found")
    except:
        return None
'''

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=source,
        actions=[RefactoringAction(
            action_type="remove_dead_code",
            parameters={"source_line": 4},
            source_refactoring="Remove Dead Code",
        )],
        strict_mode=True,
    )

    assert "except ValueError:" in transformed
    assert logs[0].replacements_count == 1
    assert logs[0].metadata["reclassified_action_type"] == "narrow_exception_handler"
    assert not any("Dead-code removal skipped" in warning for warning in warnings)


def test_planner_routes_bare_except_remove_dead_code_recommendation_to_exception_refactoring():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "bare_except",
        "steps": [{
            "step_id": 1,
            "smell": "Bare Except",
            "refactoring": "Remove Dead Code",
            "target": {"file": "lookup.py", "lines": [4]},
            "parameters": {"source_file": "lookup.py"},
        }],
    })

    action = normalized["actions"][0]
    assert action["action_type"] == "narrow_exception_handler"
    assert action["parameters"]["original_exception_type"] == ""
    assert action["parameters"]["target_exception_type"] == ""


def test_legacy_bare_except_action_passes_structural_validation_without_rollback():
    source = '''def find_student_mark():
    students = {"S001": 78, "S002": 65, "S003": 91}
    student_id = "S001"
    try:
        mark = students[student_id]
        return mark
    except:
        return None
'''

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "legacy_bare_except_structural_validation",
        "language": "python",
        "source_files": [{
            "file_name": "01_bare_except_student_lookup.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "legacy_bare_except_structural_validation",
            "actions": [{
                "action_type": "remove_dead_code",
                "parameters": {
                    "source_file": "01_bare_except_student_lookup.py",
                    "source_line": 7,
                },
                "source_refactoring": "Remove Dead Code",
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

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert "except KeyError:" in result["refactored_code"]
    assert result["validation"]["structural"]["passed"] is True
    assert result["validation"]["structural"]["details"]["dead_code_validation"] == []
    assert result["validation"]["structural"]["details"]["exception_handler_validation"][0]["passed"] is True


def test_replace_bare_except_maps_rdp_display_name_to_dedicated_action():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "replace_bare_except",
        "steps": [{
            "step_id": 1,
            "refactoring": "Replace Bare Except with Specific Exception",
            "target": {"file": "model.py", "class": "Model", "method": "admin_validate", "lines": [8]},
            "parameters": {"source_file": "model.py"},
        }],
    })

    action = normalized["actions"][0]
    assert action["action_type"] == "narrow_exception_handler"
    assert action["parameters"]["original_exception_type"] == ""
    assert action["parameters"]["exception_smell"] == "bare_except"


def test_legacy_noop_with_bare_except_label_is_recovered_at_contract_boundary():
    action = RefactoringAction.from_dict({
        "action_type": "noop",
        "source_refactoring": "Replace Bare Except with Specific Exception",
        "parameters": {"method": "parse"},
    })

    assert action.action_type == "narrow_exception_handler"
    assert action.parameters["original_exception_type"] == ""


def test_mysql_bare_except_uses_import_and_database_context_and_validates():
    source = '''from mysql.connector import Error
class Model:
    def admin_validate(self, connection):
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            return True
        except:
            return False
'''
    action = RefactoringAction(
        action_type="replace_bare_except_with_specific_exception",
        parameters={
            "source_file": "model.py",
            "source_class": "Model",
            "method": "admin_validate",
            "source_line": 8,
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="model.py",
    )

    assert "except Error:" in transformed
    assert "except:" not in transformed
    assert warnings == []
    assert logs[0].metadata["final_decision"] == "PASS"
    assert logs[0].metadata["exception_resolution_strategy"] == "import_and_try_body_context"
    assert logs[0].metadata["source_class"] == "Model"
    assert logs[0].metadata["source_method"] == "admin_validate"
    structural = StructuralValidator().validate(
        language="python", original_code=source, transformed_code=transformed, actions=[action]
    )
    assert structural.passed is True
    assert structural.details["exception_handler_validation"][0]["passed"] is True


def test_bare_except_uses_current_method_target_when_source_line_is_stale():
    source = '''def first(value):
    try:
        return int(value)
    except:
        return -1

def second(value):
    try:
        return float(value)
    except:
        return -2
'''
    action = RefactoringAction(
        action_type="replace_bare_except",
        parameters={"method": "second", "source_line": 999},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=[action], strict_mode=True
    )

    assert "def first(value):\n    try:\n        return int(value)\n    except:" in transformed
    assert "except ValueError:\n        return -2" in transformed
    assert logs[0].metadata["source_method"] == "second"


def test_two_bare_handlers_in_one_class_are_resolved_by_their_methods():
    source = '''from mysql.connector import Error
class Model:
    def admin_validate(self, connection):
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            return "admin"
        except:
            return "fallback"

    def user_validate(self, connection):
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 2")
            return "user"
        except:
            return "fallback"
'''
    actions = [
        RefactoringAction(
            action_type="replace_bare_except",
            parameters={"source_class": "Model", "method": "admin_validate", "source_line": 999},
        ),
        RefactoringAction(
            action_type="replace_bare_except",
            parameters={"source_class": "Model", "method": "user_validate", "source_line": 999},
        ),
    ]
    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=actions, strict_mode=True,
        current_file_name="UMS/Model/model.py",
    )

    assert transformed.count("except Error:") == 2
    assert warnings == []
    assert [entry.metadata["qualified_source_method"] for entry in logs] == [
        "Model.admin_validate", "Model.user_validate"
    ]
    assert [entry.metadata["handler_index"] for entry in logs] == [0, 0]
    structural = StructuralValidator().validate(
        language="python", original_code=source, transformed_code=transformed, actions=actions
    )
    assert structural.passed is True
    assert len(structural.details["exception_handler_validation"]) == 2


def test_bare_except_recovers_enclosing_method_when_class_hint_is_missing():
    source = '''class Model:
    def admin_validate(self, value):
        try:
            return int(value)
        except:
            return -1
'''
    action = RefactoringAction(
        action_type="replace_bare_except",
        parameters={"source_line": 5},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=[action], strict_mode=True
    )

    assert "except ValueError:" in transformed
    assert logs[0].metadata["source_class"] == "Model"
    assert logs[0].metadata["source_method"] == "admin_validate"


def test_local_bare_except_detection_does_not_duplicate_an_equivalent_rdp_action():
    source = '''class Model:
    def admin_validate(self, value):
        try:
            return int(value)
        except:
            return -1
'''
    request = SCTVARequestContract.from_dict({
        "request_id": "bare-except-dedup",
        "language": "python",
        "source_files": [{"file_name": "model.py", "source_code": source, "language": "python"}],
        "refactoring_plan": {"plan_id": "bare-except-dedup", "actions": [{
            "action_type": "replace_bare_except",
            "parameters": {"source_file": "model.py", "source_class": "Model", "method": "admin_validate"},
        }]},
        "execution_options": {"enable_sctva_auto_refactoring": True},
    })
    agent = SafeCodeTransformationValidationAgent()
    local_actions = agent._local_actions_for_file(
        request=request,
        file_entry=request.source_files[0],
        existing_actions=request.refactoring_plan.actions,
    )

    assert not any(
        action.action_type == "narrow_exception_handler"
        and action.source_refactoring == "SCTVA Internal Analysis"
        for action in local_actions
    )


def test_unknown_bare_except_is_review_required_without_source_change():
    source = '''def load(value):
    try:
        return dependency(value)
    except:
        return None
'''
    action = RefactoringAction(action_type="replace_bare_except", parameters={"method": "load"})
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=[action], strict_mode=True
    )

    assert transformed == source
    assert logs[0].replacements_count == 0
    assert logs[0].metadata["status"] == "review_required"
    assert logs[0].metadata["reason"] == "SPECIFIC_EXCEPTION_TYPE_NOT_PROVEN"


def test_existing_specific_handler_is_not_applicable_without_source_change():
    source = '''def parse(value):
    try:
        return int(value)
    except ValueError:
        return -1
'''
    action = RefactoringAction(action_type="replace_bare_except", parameters={"method": "parse"})
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=[action], strict_mode=True
    )

    assert transformed == source
    assert logs[0].metadata["status"] == "not_applicable"
    assert logs[0].metadata["final_decision"] == "NOT_APPLICABLE"


def test_bare_except_preserves_other_handlers_else_finally_and_try_body():
    source = '''from mysql.connector import Error
def parse(connection):
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
    except ValueError:
        return "invalid"
    except:
        return "fallback"
    else:
        return "ok"
    finally:
        audit(connection)
'''
    action = RefactoringAction(
        action_type="replace_bare_except",
        parameters={"method": "parse", "source_line": 7},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python", source_code=source, actions=[action], strict_mode=True
    )

    assert "except ValueError:\n        return \"invalid\"" in transformed
    assert "except Error:\n        return \"fallback\"" in transformed
    assert "else:\n        return \"ok\"" in transformed
    assert "finally:\n        audit(connection)" in transformed
    structural = StructuralValidator().validate(
        language="python", original_code=source, transformed_code=transformed, actions=[action]
    )
    assert logs[0].metadata["final_decision"] == "PASS"
    assert structural.passed is True
