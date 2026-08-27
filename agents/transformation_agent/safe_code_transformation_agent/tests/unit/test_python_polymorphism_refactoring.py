import ast

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.analysis.local_refactor_detector import LocalRefactorDetector
from sctva.contracts import RefactoringAction
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import python_replace_conditional
from sctva.validators.structural_validator import StructuralValidator


SOURCE = '''def shipping_cost(kind, weight):
    surcharge = 2
    if kind == "standard":
        return weight + surcharge
    elif kind == "express":
        return weight * 2 + surcharge
    else:
        raise ValueError(f"Unknown shipping kind: {kind}")
'''


def _apply(source=SOURCE, **overrides):
    params = {"method_name": "shipping_cost"}
    params.update(overrides)
    return python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        **params,
    )


def _call(source, function, *args):
    namespace = {}
    exec(compile(source, "<polymorphism-test>", "exec"), namespace)
    try:
        return ("return", namespace[function](*args))
    except Exception as exc:  # noqa: BLE001 - exception behavior is the fingerprint.
        return ("raise", type(exc).__name__, str(exc))


def _effective_action(metadata):
    return RefactoringAction(
        action_type="replace_conditional_with_polymorphism",
        parameters=metadata["effective_action_parameters"],
    )


def test_real_strategy_classes_replace_terminal_conditional_chain():
    transformed, replacements, metadata = _apply()

    assert metadata["status"] == "success"
    assert replacements == 2
    assert "class _SctvaShippingCostPolymorphicCase:" in transformed
    assert "class _SctvaShippingCostPolymorphicCaseCase1(" in transformed
    assert "class _SctvaShippingCostPolymorphicCaseDefaultCase(" in transformed
    assert "_sctva_strategy.matches(kind, weight, surcharge)" in transformed
    assert "_sctva_strategy.execute(kind, weight, surcharge)" in transformed

    tree = ast.parse(transformed)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    assert sum(isinstance(node, ast.If) for node in ast.walk(function)) == 1


def test_normal_returns_and_exception_behavior_are_preserved():
    transformed, _, metadata = _apply()
    assert metadata["status"] == "success"

    for arguments in [("standard", 5), ("express", 5), ("unknown", 5)]:
        assert _call(SOURCE, "shipping_cost", *arguments) == _call(
            transformed,
            "shipping_cost",
            *arguments,
        )


def test_filename_derived_rdp_method_is_recovered_from_source_range():
    transformed, replacements, metadata = _apply(
        method_name="01_long_method_student_marks",
        source_class="01_long_method_student_marks",
        start_line=1,
        end_line=8,
    )

    assert replacements == 2
    assert metadata["status"] == "success"
    assert metadata["method"] == "shipping_cost"
    assert metadata["source_class"] == ""
    assert metadata["target_resolution"] == "python_ast_semantic_recovery"
    assert transformed != SOURCE


def test_class_method_keeps_self_and_public_signature():
    source = '''class PriceCalculator:
    def calculate(self, kind, amount):
        if kind == "retail":
            return self.tax + amount
        elif kind == "wholesale":
            return amount
        else:
            raise ValueError(kind)

    tax = 4
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        method_name="calculate",
        source_class="PriceCalculator",
    )

    assert replacements == 2
    assert metadata["status"] == "success"
    assert metadata["source_class"] == "PriceCalculator"
    before = {}
    after = {}
    exec(compile(source, "<before>", "exec"), before)
    exec(compile(transformed, "<after>", "exec"), after)
    assert before["PriceCalculator"]().calculate("retail", 10) == 14
    assert after["PriceCalculator"]().calculate("retail", 10) == 14
    assert after["PriceCalculator"]().calculate("wholesale", 10) == 10


def test_structural_validation_proves_logic_moved_and_complexity_reduced():
    transformed, _, metadata = _apply()
    result = StructuralValidator().validate(
        language="python",
        original_code=SOURCE,
        transformed_code=transformed,
        actions=[_effective_action(metadata)],
    )

    assert result.passed is True, result.to_dict()
    validation = result.details["polymorphism_validation"][0]
    assert validation["passed"] is True
    assert all(validation["checks"].values())
    assert validation["before_if_count"] == 2
    assert validation["after_if_count"] == 1


def test_assignment_producing_branches_are_moved_and_behavior_is_preserved():
    source = '''def classify(kind):
    if kind == "a":
        result = 1
    elif kind == "b":
        result = 2
    else:
        result = 3
    return result
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        method_name="classify",
    )

    assert replacements == 2
    assert metadata["status"] == "success"
    assert metadata["mode"] == "assignment_outputs"
    assert metadata["outputs"] == ["result"]
    for kind in ("a", "b", "other"):
        assert _call(source, "classify", kind) == _call(transformed, "classify", kind)


def test_multiple_assignment_outputs_are_returned_to_the_original_function():
    source = '''def classify(kind):
    if kind == "a":
        label = "A"
        points = 3
    elif kind == "b":
        label = "B"
        points = 2
    else:
        label = "F"
        points = 0
    return label, points
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        method_name="classify",
    )

    assert replacements == 2
    assert metadata["outputs"] == ["label", "points"]
    for kind in ("a", "b", "other"):
        assert _call(source, "classify", kind) == _call(transformed, "classify", kind)

    result = StructuralValidator().validate(
        language="python",
        original_code=source,
        transformed_code=transformed,
        actions=[_effective_action(metadata)],
    )
    assert result.passed is True, result.to_dict()
    assert result.details["polymorphism_validation"][0]["passed"] is True


def test_inconsistent_assignment_outputs_require_review_without_partial_classes():
    source = '''def classify(kind):
    if kind == "a":
        result = 1
    elif kind == "b":
        other = 2
    else:
        result = 3
    return result
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        method_name="classify",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "NON_TERMINAL_BRANCH_BEHAVIOR"


def test_conditional_comments_require_review_instead_of_being_lost():
    source = '''def classify(kind):
    if kind == "a":
        # This business rule must stay attached to branch A.
        return 1
    elif kind == "b":
        return 2
    else:
        return 3
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(
        source,
        method_name="classify",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "CONDITIONAL_COMMENTS_REQUIRE_CST_REVIEW"


def test_multiple_unhinted_targets_are_review_required():
    source = SOURCE + '''

def payment(kind):
    if kind == "cash":
        return 1
    elif kind == "card":
        return 2
    else:
        return 3
'''
    transformed, replacements, metadata = python_replace_conditional.apply_replace_conditional_with_polymorphism(source)

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "AMBIGUOUS_POLYMORPHIC_CONDITIONAL_TARGET"


def test_planner_adapter_emits_dedicated_action_for_screenshot_plan_shape():
    plan = PlannerAdapter().normalize_plan({
        "plan_id": "switch_plan",
        "steps": [{
            "step_id": 2,
            "refactoring": "Replace Conditional with Polymorphism",
            "smell": "Switch Statements",
            "target": {
                "file": "01_long_method_student_marks.py",
                "method": "01_long_method_student_marks",
                "class": "01_long_method_student_marks",
                "lines": [4, 89],
            },
            "parameters": {
                "method": "01_long_method_student_marks",
                "source_class": "01_long_method_student_marks",
                "source_file": "01_long_method_student_marks.py",
            },
        }],
    })

    action = plan["actions"][0]
    assert action["action_type"] == "replace_conditional_with_polymorphism"
    assert action["parameters"]["source_file"] == "01_long_method_student_marks.py"
    assert action["parameters"]["start_line"] == 4
    assert action["parameters"]["end_line"] == 89
    assert action["warnings"] == []


def test_legacy_noop_is_promoted_without_losing_rdp_target_hints():
    action = RefactoringAction(
        action_type="noop",
        source_refactoring="Replace Conditional with Polymorphism",
        parameters={
            "legacy_step": {
                "target": {
                    "file": "01_long_method_student_marks.py",
                    "method": "01_long_method_student_marks",
                    "lines": [4, 89],
                },
                "parameters": {},
            },
        },
        warnings=[
            "Replace Conditional with Polymorphism needs richer semantic edits and was mapped to noop."
        ],
    )

    SafeCodeTransformationValidationAgent._promote_polymorphism_noops([action])

    assert action.action_type == "replace_conditional_with_polymorphism"
    assert action.parameters["source_file"] == "01_long_method_student_marks.py"
    assert action.parameters["method"] == "01_long_method_student_marks"
    assert action.parameters["start_line"] == 4
    assert action.parameters["end_line"] == 89
    assert action.warnings == []


def test_agent_pipeline_recovers_target_validates_and_reports_pass():
    plan = PlannerAdapter().normalize_plan({
        "plan_id": "switch_pipeline",
        "steps": [{
            "step_id": 1,
            "refactoring": "Replace Conditional with Polymorphism",
            "smell": "Switch Statements",
            "target": {
                "file": "shipping.py",
                "method": "shipping",
                "lines": [1, 8],
            },
            "parameters": {
                "method": "shipping",
                "source_file": "shipping.py",
            },
        }],
    })
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "switch_pipeline",
        "language": "python",
        "source_files": [{
            "file_name": "project/shipping.py",
            "source_code": SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": plan,
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "enable_sctva_auto_refactoring": True,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["replace_conditional_with_polymorphism"] == "PASS"
    assert result["validation"]["structural"]["details"]["polymorphism_validation"][0]["passed"] is True
    assert len([
        entry
        for entry in result["safety_report"]["transformation_log"]
        if entry["action_type"] == "replace_conditional_with_polymorphism"
    ]) == 1


def test_local_detector_emits_dedicated_action_when_rdp_misses_switch_smell():
    actions = LocalRefactorDetector().detect(
        language="python",
        file_name="shipping.py",
        source_code=SOURCE,
        existing_actions=[],
    )

    polymorphism = [
        action
        for action in actions
        if action.action_type == "replace_conditional_with_polymorphism"
    ]
    assert len(polymorphism) == 1
    assert polymorphism[0].parameters["method"] == "shipping_cost"
    assert polymorphism[0].parameters["source_line"] == 3


def test_local_detector_accepts_consistent_assignment_output_chains():
    source = '''def grade(mark):
    if mark >= 75:
        result = "A"
    elif mark >= 65:
        result = "B"
    else:
        result = "F"
    return result
'''

    actions = LocalRefactorDetector().detect(
        language="python",
        file_name="grades.py",
        source_code=source,
        existing_actions=[],
    )

    polymorphism = [
        action
        for action in actions
        if action.action_type == "replace_conditional_with_polymorphism"
    ]
    assert len(polymorphism) == 1
    assert polymorphism[0].parameters["method"] == "grade"
