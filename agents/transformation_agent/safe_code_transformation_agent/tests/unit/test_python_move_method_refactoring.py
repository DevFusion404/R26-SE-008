import ast
import contextlib
import io

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers.engine import TransformationEngine
from sctva.transformers import python_transformers
from sctva.validators.structural_validator import StructuralValidator


SOURCE = '''class Student:
    def __init__(self, name, maths, science, english):
        self.name = name
        self.maths = maths
        self.science = science
        self.english = english


class ReportPrinter:
    def print_student_report(self, student):
        total = student.maths + student.science + student.english
        average = total / 3
        highest = max(student.maths, student.science, student.english)
        lowest = min(student.maths, student.science, student.english)
        passed = lowest >= 35
        print(f"Student: {student.name}")
        print(f"Marks: {student.maths} {student.science} {student.english}")
        print(f"Total: {total} Average: {average:.2f}")
        print(f"Highest: {highest} Lowest: {lowest} Passed: {passed}")
        return total, average, highest, lowest, passed


def run_report():
    student = Student("Maya", 78, 69, 88)
    printer = ReportPrinter()
    return printer.print_student_report(student)
'''


def _move(source=SOURCE, **overrides):
    params = {
        "method_name": "print_student_report",
        "source_class": "ReportPrinter",
        "destination_class": "Student",
    }
    params.update(overrides)
    return python_transformers.apply_move_method(source, **params)


def _run_report(source):
    namespace = {}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(source, "<move-method-test>", "exec"), namespace)
        result = namespace["run_report"]()
    return result, output.getvalue()


def _move_action():
    return RefactoringAction(
        action_type="move_python_method",
        parameters={
            "method": "print_student_report",
            "source_class": "ReportPrinter",
            "destination_class": "Student",
        },
    )


def test_feature_envy_method_is_moved_to_destination_class():
    transformed, replacements, metadata = _move()

    assert metadata["status"] == "success"
    assert replacements == 2
    tree = ast.parse(transformed)
    student = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Student")
    printer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ReportPrinter")
    assert any(method.name == "print_student_report" for method in student.body if isinstance(method, ast.FunctionDef))
    assert not any(method.name == "print_student_report" for method in printer.body if isinstance(method, ast.FunctionDef))


def test_feature_envy_destination_fields_are_rewritten_to_self():
    transformed, _, metadata = _move()

    assert metadata["status"] == "success"
    assert "self.maths + self.science + self.english" in transformed
    assert "self.name" in transformed
    assert "student.maths" not in transformed


def test_feature_envy_return_value_and_direct_call_site_are_preserved():
    transformed, _, metadata = _move()

    assert metadata["status"] == "success"
    assert "return total, average, highest, lowest, passed" in transformed
    assert "return student.print_student_report()" in transformed
    assert "printer.print_student_report(student)" not in transformed


def test_feature_envy_output_and_return_value_are_equivalent_after_move():
    transformed, _, metadata = _move()

    assert metadata["status"] == "success"
    assert _run_report(SOURCE) == _run_report(transformed)


def test_feature_envy_move_method_structural_validation_proves_real_move():
    transformed, _, metadata = _move()

    assert metadata["status"] == "success"
    result = StructuralValidator().validate(
        language="python",
        original_code=SOURCE,
        transformed_code=transformed,
        actions=[_move_action()],
    )
    assert result.passed is True
    checks = result.details["move_method_validation"][0]["checks"]
    assert all(checks.values())




def test_move_method_structural_validation_accepts_later_introduced_constants():
    """Regression: Move Method must not fail after later constant extraction.

    The original method contains literal ``3`` and ``35``.  A valid Move Method
    first changes ``student.*`` to ``self.*``.  Later actions in the same plan
    replace those literals with ``CONSTANT_3`` and ``CONSTANT_35``.  Structural
    validation must compare their semantic literal values, not raw AST names.
    """

    transformed, _, metadata = _move()
    assert metadata["status"] == "success"

    transformed = (
        "CONSTANT_3 = 3\n"
        "CONSTANT_35 = 35\n\n"
        + transformed.replace("average = total / 3", "average = total / CONSTANT_3")
        .replace("passed = lowest >= 35", "passed = lowest >= CONSTANT_35")
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=SOURCE,
        transformed_code=transformed,
        actions=[_move_action()],
    )

    assert result.passed is True
    validation = result.details["move_method_validation"][0]
    assert validation["passed"] is True
    assert validation["checks"]["actual_method_logic_moved"] is True


def test_move_method_structural_validation_rejects_wrong_constant_value():
    """The normalizer must not hide a behavior-changing constant value."""

    transformed, _, metadata = _move()
    assert metadata["status"] == "success"

    transformed = (
        "CONSTANT_3 = 999\n\n"
        + transformed.replace("average = total / 3", "average = total / CONSTANT_3")
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=SOURCE,
        transformed_code=transformed,
        actions=[_move_action()],
    )

    assert result.passed is False
    validation = result.details["move_method_validation"][0]
    assert validation["checks"]["actual_method_logic_moved"] is False


def test_feature_envy_move_method_runs_through_transformation_engine():
    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=SOURCE,
        actions=[_move_action()],
        strict_mode=True,
    )

    assert "def print_student_report(self):" in transformed
    assert "return student.print_student_report()" in transformed
    assert logs[0].replacements_count == 2
    assert any("Feature Envy Move Method applied" in warning for warning in warnings)




def test_successful_move_method_removes_stale_noop_warning():
    action = _move_action()
    action.warnings = [
        "Move Method needs richer semantic edits and was not simulated with a rename."
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=SOURCE,
        actions=[action],
        strict_mode=True,
    )

    assert "def print_student_report(self):" in transformed
    assert logs[0].replacements_count == 2
    assert not any("not simulated with a rename" in warning for warning in warnings)
    assert any("Feature Envy Move Method applied" in warning for warning in warnings)


def test_feature_envy_duplicate_source_method_fails_structural_validation():
    transformed, _, metadata = _move()
    assert metadata["status"] == "success"
    duplicated = transformed.replace(
        "class ReportPrinter:\n",
        '''class ReportPrinter:
    def print_student_report(self, student):
        return student.maths

''',
        1,
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=SOURCE,
        transformed_code=duplicated,
        actions=[_move_action()],
    )
    assert result.passed is False
    checks = result.details["move_method_validation"][0]["checks"]
    assert checks["method_removed_from_source_class"] is False


def test_feature_envy_stale_destination_is_recovered_from_ast_evidence():
    transformed, replacements, metadata = _move(destination_class="MissingStudent")

    assert transformed != SOURCE
    assert replacements > 0
    assert metadata["status"] == "success"
    assert metadata["destination_class"] == "Student"


def test_feature_envy_ambiguous_destination_is_review_required():
    source = '''class Student:
    pass


class ReportPrinter:
    def describe(self, student, teacher):
        return student.name + teacher.name
'''

    transformed, replacements, metadata = python_transformers.apply_move_method(
        source,
        method_name="describe",
        source_class="ReportPrinter",
        destination_class="Student",
    )
    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] in {
        "DESTINATION_OBJECT_AMBIGUOUS",
        "MOVE_METHOD_TARGET_NOT_FOUND",
    }


def test_feature_envy_unresolved_direct_call_site_is_review_required():
    source = SOURCE.replace(
        "    printer = ReportPrinter()\n",
        "    printer = make_printer()\n",
    )

    transformed, replacements, metadata = _move(source)
    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "UNRESOLVED_DIRECT_CALL_SITE"


def test_feature_envy_planner_mapping_uses_method_and_classes_not_file_stem():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "feature_envy_plan",
        "steps": [{
            "step_id": 1,
            "smell": "Feature Envy",
            "refactoring": "Move Method",
            "target": {"method": "print_student_report"},
            "parameters": {
                "source_class": "ReportPrinter",
                "destination_class": "Student",
                "source_file": "07_feature_envy_student_report.py",
            },
        }],
    })

    action = normalized["actions"][0]
    assert action["action_type"] == "move_python_method"
    assert action["parameters"]["method"] == "print_student_report"
    assert action["parameters"]["source_class"] == "ReportPrinter"
    assert action["parameters"]["destination_class"] == "Student"
    assert action["parameters"]["destination_parameter"] == ""
    assert action["parameters"]["source_file"] == "07_feature_envy_student_report.py"
    assert action["parameters"]["smell"] == "Feature Envy"
    assert action["parameters"]["semantic_recovery_required"] is False


MALFORMED_RDP_PLAN = {
    "plan_id": "plan_feature_envy_malformed",
    "target": "07_feature_envy_student_report.py",
    "steps": [
        {
            "step_id": 1,
            "refactoring": "Move Method",
            "target": {
                "class": "07_feature_envy_student_report",
                "file": "07_feature_envy_student_report.py",
                "lines": [11],
                "method": "07_feature_envy_student_report",
            },
            "parameters": {
                "destination_class": "print_student_report",
                "method": "07_feature_envy_student_report",
                "source_class": "07_feature_envy_student_report",
                "source_file": "07_feature_envy_student_report.py",
            },
        }
    ],
}


def test_move_method_resolver_recovers_the_real_feature_envy_target_from_bad_rdp_hints():
    resolved = python_transformers.resolve_move_method_target(
        SOURCE,
        method_name="07_feature_envy_student_report",
        source_class="07_feature_envy_student_report",
        destination_class="print_student_report",
        source_line=11,
    )

    assert resolved["status"] == "success"
    assert resolved["method"] == "print_student_report"
    assert resolved["source_class"] == "ReportPrinter"
    assert resolved["destination_class"] == "Student"
    assert resolved["destination_parameter"] == "student"


def test_apply_move_method_recovers_malformed_rdp_target_without_agent_help():
    transformed, replacements, metadata = python_transformers.apply_move_method(
        SOURCE,
        method_name="07_feature_envy_student_report",
        source_class="07_feature_envy_student_report",
        destination_class="print_student_report",
        source_line=11,
    )

    assert metadata["status"] == "success"
    assert replacements == 2
    assert "def print_student_report(self):" in transformed
    assert "return student.print_student_report()" in transformed
    assert "class ReportPrinter:\n    pass" in transformed


def test_engine_recovers_legacy_noop_that_originated_from_move_method():
    action = RefactoringAction(
        action_type="noop",
        parameters={"source_file": "07_feature_envy_student_report.py"},
        source_refactoring="Move Method",
        warnings=[
            "Move Method needs richer semantic edits and was not simulated with a rename.",
            "Action mapped to noop; no code change applied.",
        ],
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=SOURCE,
        actions=[action],
        strict_mode=True,
        current_file_name="07_feature_envy_student_report.py",
    )

    assert logs[0].replacements_count == 2
    assert logs[0].metadata["reclassified_action_type"] == "move_python_method"
    assert logs[0].metadata["status"] == "success"
    assert "def print_student_report(self):" in transformed
    assert "return student.print_student_report()" in transformed
    assert any("Recovered malformed Move Method noop" in warning for warning in warnings)


def test_planner_adapter_does_not_turn_filename_derived_move_method_into_noop():
    normalized = PlannerAdapter().normalize_plan(MALFORMED_RDP_PLAN)
    action = normalized["actions"][0]

    assert action["action_type"] == "move_python_method"
    assert action["parameters"]["semantic_recovery_required"] is True
    assert action["parameters"]["source_file"] == "07_feature_envy_student_report.py"


REAL_FEATURE_ENVY_SOURCE = r'''"""Intentionally contains Feature Envy. Not refactored."""

class Student:
    def __init__(self, name, maths, science, english):
        self.name = name
        self.maths = maths
        self.science = science
        self.english = english

class ReportPrinter:
    def print_student_report(self, student):
        total = student.maths + student.science + student.english
        average = (student.maths + student.science + student.english) / 3
        highest = max(student.maths, student.science, student.english)
        lowest = min(student.maths, student.science, student.english)
        passed = student.maths >= 35 and student.science >= 35 and student.english >= 35
        print("Student:", student.name)
        print("Marks:", student.maths, student.science, student.english)
        print("Total:", total, "Average:", round(average, 2))
        print("Highest:", highest, "Lowest:", lowest, "Passed:", passed)

if __name__ == "__main__":
    ReportPrinter().print_student_report(Student("Maya", 78, 69, 88))
'''


def test_real_uploaded_feature_envy_shape_is_moved_from_report_printer_to_student():
    transformed, replacements, metadata = python_transformers.apply_move_method(
        REAL_FEATURE_ENVY_SOURCE,
        method_name="07_feature_envy_student_report",
        source_class="07_feature_envy_student_report",
        destination_class="print_student_report",
        source_line=11,
    )

    assert metadata["status"] == "success"
    assert replacements == 2
    assert "class ReportPrinter:\n    pass" in transformed
    assert "def print_student_report(self):" in transformed
    assert "Student('Maya', 78, 69, 88).print_student_report()" in transformed or \
        'Student("Maya", 78, 69, 88).print_student_report()' in transformed
    assert "ReportPrinter().print_student_report" not in transformed
    assert "student.maths" not in transformed
    assert "self.maths" in transformed



def test_agent_pipeline_recovers_legacy_move_method_noop_and_validates_real_move():
    agent = SafeCodeTransformationValidationAgent()
    result = agent.execute({
        "request_id": "feature_envy_legacy_noop",
        "language": "python",
        "source_code": REAL_FEATURE_ENVY_SOURCE,
        "source_files": [
            {
                "file_name": "07_feature_envy_student_report.py",
                "source_code": REAL_FEATURE_ENVY_SOURCE,
                "language": "python",
                "source_mode": "raw",
            }
        ],
        "refactoring_plan": {
            "plan_id": "feature_envy_legacy_noop_plan",
            "actions": [
                {
                    "action_type": "noop",
                    "parameters": {
                        "reason": "malformed_step",
                        "source_file": "07_feature_envy_student_report.py",
                    },
                    "source_step_id": 1,
                    "source_refactoring": "Move Method",
                    "warnings": [
                        "Move Method needs richer semantic edits and was not simulated with a rename.",
                        "Action mapped to noop; no code change applied.",
                    ],
                }
            ],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True
    assert result["plan_compliance"]["move_method"] == "PASS"
    assert "def print_student_report(self):" in result["refactored_code"]
    assert "ReportPrinter().print_student_report" not in result["refactored_code"]
    assert result["validation"]["structural"]["details"]["move_method_validation"]
    assert result["validation"]["structural"]["details"]["move_method_validation"][0]["passed"] is True
