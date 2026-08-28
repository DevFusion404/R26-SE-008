import ast
import contextlib
import io

from sctva.contracts import RefactoringAction, SourceFileContract
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.analysis import LocalRefactorDetector
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import python_inline_class, python_transformers
from sctva.transformers.engine import TransformationEngine
from sctva.validators.structural_validator import StructuralValidator


SIMPLE_SOURCE = '''class Student:
    def __init__(self, name):
        self.name = name


class ReportPrinter:
    def print_report(self, student):
        print(f"Student: {student.name}")
        return student.name.upper()


def run_report():
    student = Student("Maya")
    printer = ReportPrinter()
    return printer.print_report(student)
'''


FIELD_SOURCE = '''class Helper:
    def __init__(self):
        self.status = "ACTIVE"

    def describe(self, value):
        return f"{self.status}:{value}"


def run_helper():
    helper = Helper()
    return helper.describe("ready"), helper.status
'''


OWNED_LAZY_CLASS_SOURCE = '''"""Intentionally contains the Lazy Class code smell.
Target refactoring: Inline Class.
This source is intentionally NOT refactored.
"""


class CustomerContact:
    """A tiny class with too little responsibility."""

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
        print(f"Customer ID: {self.customer_id}")
        print(f"Customer Name: {self.name}")
        print(self.contact.formatted_phone())

    def get_phone(self):
        return self.contact.phone


def main():
    print("=== Customer Details ===")
    customer = Customer(
        customer_id="C001",
        name="Nimal Perera",
        phone="0771234567",
    )
    customer.display_details()
    print(f"Raw phone: {customer.get_phone()}")


if __name__ == "__main__":
    main()
'''


ONLINE_ORDER_SOURCE = '''class CourierCompany:
    def __init__(self, name):
        self.name = name

    def delivery_label(self):
        return f"Courier: {self.name}"


class Shipment:
    def __init__(self, courier):
        self.courier = courier


class OnlineOrder:
    def __init__(self, shipment):
        self.shipment = shipment
'''


FIELD_ONLY_WRAPPER_SOURCE = '''class DeliveryDetails:
    def __init__(self, reference):
        self.reference = reference


class Order:
    def __init__(self, reference):
        self.delivery = DeliveryDetails(reference)

    def delivery_reference(self):
        return self.delivery.reference


def run_order():
    return Order("REF-42").delivery_reference()
'''


ACCESSOR_WRAPPER_SOURCE = '''class Preferences:
    def __init__(self, theme):
        self.theme = theme

    def get_theme(self):
        return self.theme

    def set_theme(self, theme):
        self.theme = theme

    def theme_label(self):
        return f"Theme: {self.theme}"


class Profile:
    def __init__(self, theme):
        self.preferences = Preferences(theme)

    def update(self, theme):
        self.preferences.set_theme(theme)
        return self.preferences.theme_label()


def run_profile():
    return Profile("light").update("dark")
'''


MEANINGFUL_WRAPPER_SOURCE = '''class DiscountPolicy:
    def __init__(self, rate):
        self.rate = rate

    def calculate(self, total):
        if total > 100:
            return total * self.rate
        return 0


class Cart:
    def __init__(self, rate):
        self.policy = DiscountPolicy(rate)
'''


INLINE_CHAIN_SOURCE = '''class Country:
    def __init__(self, code):
        self.code = code


class Address:
    def __init__(self, code):
        self.country = Country(code)


class Profile:
    def __init__(self, code):
        self.address = Address(code)


class User:
    def __init__(self, code):
        self.profile = Profile(code)

    def country_code(self):
        return self.profile.address.country.code


def run_user():
    return User("LK").country_code()
'''


def _run(source, function_name):
    namespace = {}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(source, "<inline-class-test>", "exec"), namespace)
        value = namespace[function_name]()
    return value, output.getvalue()


def _inline_action(class_name):
    return RefactoringAction(
        action_type="inline_python_class",
        parameters={"class_to_inline": class_name},
    )


def test_inline_class_converts_simple_helper_to_module_function():
    transformed, replacements, metadata = python_transformers.apply_inline_class(
        SIMPLE_SOURCE,
        class_to_inline="ReportPrinter",
    )

    assert metadata["status"] == "success"
    assert replacements == 3
    tree = ast.parse(transformed)
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ReportPrinter"
        for node in tree.body
    )
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "print_report"
        for node in tree.body
    )


def test_inline_class_removes_creation_and_rewrites_method_call_site():
    transformed, _, metadata = python_transformers.apply_inline_class(
        SIMPLE_SOURCE,
        class_to_inline="ReportPrinter",
    )

    assert metadata["status"] == "success"
    assert "printer = ReportPrinter()" not in transformed
    assert "return print_report(student)" in transformed
    assert "printer.print_report(student)" not in transformed


def test_inline_class_preserves_output_and_return_value():
    transformed, _, metadata = python_transformers.apply_inline_class(
        SIMPLE_SOURCE,
        class_to_inline="ReportPrinter",
    )

    assert metadata["status"] == "success"
    assert _run(SIMPLE_SOURCE, "run_report") == _run(transformed, "run_report")


def test_inline_class_preserves_one_small_literal_field():
    transformed, _, metadata = python_transformers.apply_inline_class(
        FIELD_SOURCE,
        class_to_inline="Helper",
    )

    assert metadata["status"] == "success"
    assert "helper_status = 'ACTIVE'" in transformed
    assert "def describe(status, value):" in transformed
    assert "return describe(helper_status, 'ready'), helper_status" in transformed
    assert _run(FIELD_SOURCE, "run_helper") == _run(transformed, "run_helper")


def test_inline_class_multiple_direct_usages_are_updated():
    source = SIMPLE_SOURCE.replace(
        "    printer = ReportPrinter()\n    return printer.print_report(student)",
        "    first = ReportPrinter()\n    second = ReportPrinter()\n    return first.print_report(student), second.print_report(student)",
    )

    transformed, _, metadata = python_transformers.apply_inline_class(
        source,
        class_to_inline="ReportPrinter",
    )
    assert metadata["status"] == "success"
    assert "ReportPrinter()" not in transformed
    assert "return print_report(student), print_report(student)" in transformed


def test_inline_class_structural_validation_rejects_class_that_remains():
    action = _inline_action("ReportPrinter")
    result = StructuralValidator().validate(
        language="python",
        original_code=SIMPLE_SOURCE,
        transformed_code=SIMPLE_SOURCE,
        actions=[action],
    )

    assert result.passed is False
    checks = result.details["inline_class_validation"][0]["checks"]
    assert checks["target_class_removed_after"] is False


def test_inline_class_structural_validation_rejects_duplicated_logic():
    transformed, _, metadata = python_transformers.apply_inline_class(
        SIMPLE_SOURCE,
        class_to_inline="ReportPrinter",
    )
    assert metadata["status"] == "success"
    duplicated = transformed.replace(
        "def print_report(student):",
        '''class ReportPrinter:
    def print_report(self, student):
        print(f"Student: {student.name}")
        return student.name.upper()


def print_report(student):''',
        1,
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=SIMPLE_SOURCE,
        transformed_code=duplicated,
        actions=[_inline_action("ReportPrinter")],
    )
    assert result.passed is False
    assert result.details["inline_class_validation"][0]["checks"]["target_class_removed_after"] is False


def test_inline_class_inheritance_requires_review():
    source = SIMPLE_SOURCE.replace("class ReportPrinter:", "class ReportPrinter(Student):")
    transformed, replacements, metadata = python_transformers.apply_inline_class(
        source,
        class_to_inline="ReportPrinter",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "INHERITANCE_OR_METACLASS_UNSUPPORTED"


def test_inline_class_dynamic_getattr_requires_review():
    source = SIMPLE_SOURCE.replace(
        "    return printer.print_report(student)",
        "    return getattr(printer, 'print_report')(student)",
    )
    transformed, replacements, metadata = python_transformers.apply_inline_class(
        source,
        class_to_inline="ReportPrinter",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"


def test_explicit_inline_class_targets_are_resolved_directly_from_ast():
    for class_name in ("CourierCompany", "Shipment", "OnlineOrder"):
        resolution = python_transformers.resolve_inline_class_target(
            ONLINE_ORDER_SOURCE,
            class_to_inline=class_name,
        )
        assert resolution == {
            "status": "success",
            "class_to_inline": class_name,
            "strategy": "explicit_plan_target",
            "target_resolution": "explicit_plan_target",
        }


def test_explicit_courier_target_does_not_depend_on_lazy_class_detector():
    detected = LocalRefactorDetector().detect(
        language="python",
        file_name="01_message_chain_hide_delegate_online_order.py",
        source_code=ONLINE_ORDER_SOURCE,
        existing_actions=[],
    )
    assert not any(
        action.action_type == "inline_python_class"
        and action.parameters.get("class_to_inline") == "CourierCompany"
        for action in detected
    )
    resolution = python_transformers.resolve_inline_class_target(
        ONLINE_ORDER_SOURCE,
        class_to_inline="CourierCompany",
    )
    assert resolution["status"] == "success"
    assert resolution["target_resolution"] == "explicit_plan_target"


def test_agent_resolves_explicit_courier_target_before_local_fallback():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "CourierCompany",
            "source_file": "01_message_chain_hide_delegate_online_order.py",
        },
    )
    source = SourceFileContract(
        file_name="01_message_chain_hide_delegate_online_order.py",
        source_code=ONLINE_ORDER_SOURCE,
        language="python",
        source_mode="raw",
    )

    SafeCodeTransformationValidationAgent._resolve_inline_class_source_files(
        [action],
        [source],
    )

    assert action.parameters["class_to_inline"] == "CourierCompany"
    assert action.parameters["target_resolution"] == "explicit_plan_target"
    assert action.parameters["source_resolution_status"] == "success"
    assert "source_resolution_error" not in action.parameters


def test_missing_explicit_inline_class_is_not_applicable():
    resolution = python_transformers.resolve_inline_class_target(
        ONLINE_ORDER_SOURCE,
        class_to_inline="MissingCourier",
    )
    assert resolution["status"] == "not_applicable"
    assert resolution["reason"] == "TARGET_CLASS_NOT_FOUND"


def test_missing_explicit_inline_class_reports_not_applicable_in_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "missing_explicit_inline_target",
        "language": "python",
        "source_files": [{
            "file_name": "01_message_chain_hide_delegate_online_order.py",
            "source_code": ONLINE_ORDER_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "missing_explicit_inline_target",
            "actions": [{
                "action_type": "inline_python_class",
                "parameters": {
                    "class_to_inline": "MissingCourier",
                    "source_file": "01_message_chain_hide_delegate_online_order.py",
                },
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is False
    assert result["plan_compliance"]["inline_class"] == "NOT_APPLICABLE"
    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert metadata["status"] == "not_applicable"
    assert metadata["reason"] == "TARGET_CLASS_NOT_FOUND"


def test_malformed_explicit_inline_target_uses_semantic_fallback():
    resolution = python_transformers.resolve_inline_class_target(
        OWNED_LAZY_CLASS_SOURCE,
        class_to_inline="01-lazy-class.py",
    )
    assert resolution["status"] == "success"
    assert resolution["class_to_inline"] == "CustomerContact"
    assert resolution["target_resolution"] == "owner_usage_semantic_recovery"


def test_multiple_inline_recovery_candidates_require_review():
    source = '''class FirstHelper:
    def run(self):
        return 1

class FirstOwner:
    def __init__(self):
        self.helper = FirstHelper()

class SecondHelper:
    def run(self):
        return 2

class SecondOwner:
    def __init__(self):
        self.helper = SecondHelper()
'''
    resolution = python_transformers.resolve_inline_class_target(source)
    assert resolution["status"] == "review_required"
    assert resolution["reason"] == "AMBIGUOUS_INLINE_CLASS_TARGET"


def test_inline_class_planner_maps_to_dedicated_action():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "inline_class_plan",
        "steps": [{
            "step_id": 1,
            "smell": "Lazy Class",
            "refactoring": "Inline Class",
            "target": {"class": "ReportPrinter"},
            "parameters": {"source_file": "07_feature_envy_student_report.py"},
        }],
    })

    action = normalized["actions"][0]
    assert action["action_type"] == "inline_python_class"
    assert action["parameters"]["class_to_inline"] == "ReportPrinter"


def test_inline_class_runs_through_transformation_engine():
    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=SIMPLE_SOURCE,
        actions=[_inline_action("ReportPrinter")],
        strict_mode=True,
    )

    assert "class ReportPrinter" not in transformed
    assert logs[0].replacements_count == 3
    assert logs[0].metadata["status"] == "success"
    assert any("Inline Class applied" in warning for warning in warnings)


def test_inline_class_runs_through_full_sctva_validation_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "inline_python_class",
        "language": "python",
        "source_files": [{
            "file_name": "report_printer.py",
            "source_code": SIMPLE_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "inline_python_class_plan",
            "actions": [{
                "action_type": "inline_python_class",
                "parameters": {
                    "class_to_inline": "ReportPrinter",
                    "source_file": "report_printer.py",
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

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["inline_class"] == "PASS"
    assert result["validation"]["structural"]["details"]["inline_class_validation"][0]["passed"] is True


def test_existing_but_unsafe_inline_class_requires_review_without_target_not_found():
    unsafe_source = SIMPLE_SOURCE.replace("class ReportPrinter:", "class ReportPrinter(Student):")
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "unsafe_inline_python_class",
        "language": "python",
        "source_files": [{
            "file_name": "report_printer.py",
            "source_code": unsafe_source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "unsafe_inline_python_class_plan",
            "actions": [{
                "action_type": "inline_python_class",
                "parameters": {
                    "class_to_inline": "ReportPrinter",
                    "source_file": "report_printer.py",
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

    assert result["success"] is False
    assert result["plan_compliance"]["inline_class"] == "REVIEW_REQUIRED"
    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert metadata["reason"] == "INHERITANCE_OR_METACLASS_UNSUPPORTED"
    assert metadata["target_resolution"] == "explicit_plan_target"
    assert any(
        "Inline Class requires review" in message
        for message in result["safety_report"]["human_messages"]
    )


def test_local_detector_finds_owned_lazy_class_and_does_not_extract_docstrings():
    actions = LocalRefactorDetector().detect(
        language="python",
        file_name="01_lazy_class_inline_customer_contact.py",
        source_code=OWNED_LAZY_CLASS_SOURCE,
        existing_actions=[],
    )

    inline_actions = [
        action
        for action in actions
        if action.action_type == "inline_python_class"
    ]
    assert len(inline_actions) == 1
    assert inline_actions[0].parameters["class_to_inline"] == "CustomerContact"
    assert inline_actions[0].parameters["destination_class"] == "Customer"
    assert inline_actions[0].parameters["owner_attribute"] == "contact"

    # Docstrings must remain metadata, not auto-generated constants.
    docstring_constant_actions = [
        action
        for action in actions
        if action.action_type in {"introduce_constant", "extract_constant"}
        and "Lazy Class code smell" in str(action.parameters.get("literal_value") or "")
    ]
    assert docstring_constant_actions == []


def test_owned_lazy_class_runs_through_engine_and_moves_state_and_method_into_owner():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "CustomerContact",
            "destination_class": "Customer",
            "owner_attribute": "contact",
        },
    )
    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=OWNED_LAZY_CLASS_SOURCE,
        actions=[action],
        strict_mode=True,
    )

    assert logs[0].metadata["status"] == "success"
    assert logs[0].metadata["inline_mode"] == "owner_class"
    assert logs[0].replacements_count > 0
    assert "class CustomerContact" not in transformed
    assert "self.contact = CustomerContact(phone)" not in transformed
    assert "self.phone = phone" in transformed
    assert "def formatted_phone(self):" in transformed
    assert "self.contact.formatted_phone()" not in transformed
    assert "self.formatted_phone()" in transformed
    assert "return self.contact.phone" not in transformed
    assert "return self.phone" in transformed
    assert any("inlined into Customer" in warning for warning in warnings)


def test_owned_lazy_class_preserves_console_output():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "CustomerContact",
            "destination_class": "Customer",
            "owner_attribute": "contact",
        },
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=OWNED_LAZY_CLASS_SOURCE,
        actions=[action],
        strict_mode=True,
    )
    assert logs[0].metadata["status"] == "success"

    def run_main(source):
        namespace = {"__name__": "__main__"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(source, "<owned-inline>", "exec"), namespace)
        return output.getvalue()

    assert run_main(OWNED_LAZY_CLASS_SOURCE) == run_main(transformed)


def test_owned_inline_class_accepts_field_only_wrapper_without_fake_responsibility():
    transformed, replacements, metadata = python_inline_class.apply_owned_inline_class(
        FIELD_ONLY_WRAPPER_SOURCE,
        class_to_inline="DeliveryDetails",
    )

    assert metadata["status"] == "success", metadata
    assert replacements > 0
    assert metadata["responsibility_profile"]["effective_responsibility_count"] == 0
    assert "class DeliveryDetails" not in transformed
    assert "self.reference = reference" in transformed
    assert "return self.reference" in transformed
    assert _run(FIELD_ONLY_WRAPPER_SOURCE, "run_order") == _run(
        transformed, "run_order"
    )


def test_owned_inline_class_treats_getters_setters_and_field_formatting_as_wrapper_api():
    transformed, replacements, metadata = python_inline_class.apply_owned_inline_class(
        ACCESSOR_WRAPPER_SOURCE,
        class_to_inline="Preferences",
    )

    assert metadata["status"] == "success", metadata
    assert replacements > 0
    assert metadata["responsibility_profile"]["meaningful_methods"] == []
    assert "class Preferences" not in transformed
    assert "self.preferences.set_theme" not in transformed
    assert "self.set_theme(theme)" in transformed
    assert _run(ACCESSOR_WRAPPER_SOURCE, "run_profile") == _run(
        transformed, "run_profile"
    )


def test_owned_inline_class_keeps_cross_file_wrapper_for_atomic_repository_edit():
    transformed, replacements, metadata = python_inline_class.apply_owned_inline_class(
        FIELD_ONLY_WRAPPER_SOURCE,
        class_to_inline="DeliveryDetails",
        current_file_name="order.py",
        project_source_files=[
            {
                "file_name": "consumer.py",
                "language": "python",
                "source_code": "from order import DeliveryDetails\n",
            }
        ],
    )

    assert transformed == FIELD_ONLY_WRAPPER_SOURCE
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "EXTERNAL_REFERENCES"
    assert metadata["reference_files"] == ["consumer.py"]


def test_owned_inline_class_keeps_meaningful_business_class_for_review():
    transformed, replacements, metadata = python_inline_class.apply_owned_inline_class(
        MEANINGFUL_WRAPPER_SOURCE,
        class_to_inline="DiscountPolicy",
    )

    assert transformed == MEANINGFUL_WRAPPER_SOURCE
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "CLASS_RESPONSIBILITY_NOT_SMALL"
    assert metadata["responsibility_profile"]["meaningful_methods"] == ["calculate"]


def test_sequential_duplicate_inline_class_action_is_already_handled_via_lineage():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={"class_to_inline": "DeliveryDetails"},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=FIELD_ONLY_WRAPPER_SOURCE,
        actions=[action, action],
        strict_mode=True,
        current_file_name="order.py",
    )

    assert "class DeliveryDetails" not in transformed
    assert logs[0].metadata["status"] == "success"
    assert logs[1].metadata["status"] == "already_handled"
    assert logs[1].metadata["reason"] == "ALREADY_HANDLED_BY_PRIOR_INLINE_CLASS"


def test_structural_validation_follows_three_step_inline_class_lineage_to_terminal_owner():
    actions = [
        RefactoringAction(
            action_type="inline_python_class",
            parameters={"class_to_inline": class_name},
        )
        for class_name in ("Country", "Address", "Profile")
    ]
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=INLINE_CHAIN_SOURCE,
        actions=actions,
        strict_mode=True,
        current_file_name="user.py",
    )

    assert [log.metadata["status"] for log in logs] == ["success"] * 3
    assert [
        log.metadata["inline_class_lineage"]["terminal_destination_class"]
        for log in logs
    ] == ["User", "User", "User"]
    assert all(f"class {class_name}" not in transformed for class_name in (
        "Country", "Address", "Profile",
    ))
    assert "class User" in transformed
    assert "return self.code" in transformed
    assert _run(INLINE_CHAIN_SOURCE, "run_user") == _run(transformed, "run_user")

    effective_actions = []
    for index, log in enumerate(logs, start=1):
        parameters = dict(log.metadata["effective_action_parameters"])
        parameters["_sctva_action_index"] = index
        parameters["applied_transformation_metadata"] = dict(log.metadata)
        effective_actions.append(RefactoringAction(
            action_type="inline_python_class",
            parameters=parameters,
        ))

    result = StructuralValidator().validate(
        language="python",
        original_code=INLINE_CHAIN_SOURCE,
        transformed_code=transformed,
        actions=effective_actions,
    )

    assert result.passed is True, result.details
    validations = result.details["inline_class_validation"]
    assert all(item["passed"] is True for item in validations)
    assert validations[0]["terminal_destination_class"] == "User"
    assert validations[0]["superseded_by_inline_class"] is True
    assert validations[0]["checks"]["required_methods_and_state_preserved_elsewhere"] is True


def test_structural_validation_reports_incorrect_inline_class_terminal_destination():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={"class_to_inline": "DeliveryDetails"},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=FIELD_ONLY_WRAPPER_SOURCE,
        actions=[action],
        strict_mode=True,
        current_file_name="order.py",
    )
    applied = dict(logs[0].metadata)
    lineage = dict(applied["inline_class_lineage"])
    lineage["destination_class"] = "MissingTerminalOwner"
    applied["inline_class_lineage"] = lineage
    parameters = dict(logs[0].metadata["effective_action_parameters"])
    parameters["destination_class"] = "MissingTerminalOwner"
    parameters["applied_transformation_metadata"] = applied
    result = StructuralValidator().validate(
        language="python",
        original_code=FIELD_ONLY_WRAPPER_SOURCE,
        transformed_code=transformed,
        actions=[RefactoringAction(
            action_type="inline_python_class",
            parameters=parameters,
        )],
    )

    validation = result.details["inline_class_validation"][0]
    assert validation["passed"] is False
    assert validation["reason"] == "TERMINAL_DESTINATION_NOT_FOUND"


def test_owned_lazy_class_structural_validation_passes():
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "CustomerContact",
            "destination_class": "Customer",
            "owner_attribute": "contact",
        },
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=OWNED_LAZY_CLASS_SOURCE,
        actions=[action],
        strict_mode=True,
    )
    effective = RefactoringAction(
        action_type="inline_python_class",
        parameters=logs[0].metadata["effective_action_parameters"],
    )
    result = StructuralValidator().validate(
        language="python",
        original_code=OWNED_LAZY_CLASS_SOURCE,
        transformed_code=transformed,
        actions=[effective],
    )

    assert result.passed is True, result.details
    validation = result.details["inline_class_validation"][0]
    assert validation["passed"] is True
    assert validation["inline_mode"] == "owner_class"
    assert all(validation["checks"].values())


def test_full_sctva_auto_refactoring_detects_and_applies_lazy_class_with_empty_plan():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "auto_inline_owned_lazy_class",
        "language": "python",
        "source_files": [{
            "file_name": "01_lazy_class_inline_customer_contact.py",
            "source_code": OWNED_LAZY_CLASS_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "empty_plan_auto_inline",
            "actions": [],
            "behavior_tests": [],
            "metadata": {"enable_sctva_auto_refactoring": True},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": True,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["inline_class"] == "PASS"
    assert "class CustomerContact" not in result["refactored_code"]
    inline_validation = result["validation"]["structural"]["details"]["inline_class_validation"]
    assert inline_validation
    assert inline_validation[0]["passed"] is True


def test_legacy_inline_class_noop_is_promoted_resolved_and_applied():
    """Regression for legacy RDP payloads like sctva_result (22).json."""

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "legacy_inline_class_noop",
        "language": "python",
        "source_files": [{
            "file_name": "01_lazy_class_inline_customer_contact.py",
            "source_code": OWNED_LAZY_CLASS_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "legacy_inline_class_noop_plan",
            "actions": [{
                "action_type": "noop",
                "parameters": {},
                "source_refactoring": "Inline Class",
                "warnings": [
                    "Inline Class needs richer semantic edits and was not simulated with a rename.",
                    "Action mapped to noop; no code change applied.",
                ],
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["inline_class"] == "PASS"
    assert "class CustomerContact" not in result["refactored_code"]
    assert "self.phone = phone" in result["refactored_code"]
    assert "self.formatted_phone()" in result["refactored_code"]
    assert not any(
        "not simulated with a rename" in message.lower()
        for message in result["safety_report"]["human_messages"]
    )


def test_inline_class_rechecks_current_symbols_after_move_method_and_cleans_empty_source_class():
    """Regression for a plan containing Move Method, Inline Student, Inline ReportPrinter."""

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "move_then_inline_current_symbol_state",
        "language": "python",
        "source_files": [{
            "file_name": "student_report.py",
            "source_code": SIMPLE_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "move_then_inline_current_symbol_state_plan",
            "actions": [
                {
                    "action_type": "move_python_method",
                    "parameters": {
                        "method": "print_report",
                        "source_class": "ReportPrinter",
                        "destination_class": "Student",
                        "destination_parameter": "student",
                        "source_file": "student_report.py",
                    },
                },
                {
                    "action_type": "inline_python_class",
                    "parameters": {
                        "class_to_inline": "Student",
                        "source_file": "student_report.py",
                    },
                },
                {
                    "action_type": "inline_python_class",
                    "parameters": {
                        "class_to_inline": "ReportPrinter",
                        "source_file": "student_report.py",
                    },
                },
            ],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["inline_class"] == "PASS"
    assert "class ReportPrinter" not in result["refactored_code"]
    assert "printer = ReportPrinter()" not in result["refactored_code"]
    assert "return student.print_report()" in result["refactored_code"]
    assert _run(SIMPLE_SOURCE, "run_report") == _run(
        result["refactored_code"], "run_report"
    )

    logs = result["safety_report"]["transformation_log"]
    assert logs[0]["metadata"]["status"] == "success"
    assert logs[1]["metadata"]["status"] == "satisfied"
    assert logs[1]["metadata"]["reason"] == "SMELL_RESOLVED_BY_PRIOR_REFACTORING"
    assert logs[2]["metadata"]["status"] == "success"
    assert logs[2]["metadata"]["inline_mode"] == "empty_class_cleanup"
    assert result["validation"]["structural"]["passed"] is True
    inline_validation = result["validation"]["structural"]["details"][
        "inline_class_validation"
    ]
    assert len(inline_validation) == 2
    assert inline_validation[0]["result"] == "SATISFIED_BY_PRIOR_REFACTORING"
    assert inline_validation[0]["passed"] is True
    assert inline_validation[1]["inline_mode"] == "empty_class_cleanup"
    assert inline_validation[1]["passed"] is True
    move_history = logs[0]["metadata"]
    assert move_history["source_class_became_empty"] is True
    assert move_history["current_symbol_table"]["classes"]["Student"]["methods"] == [
        "__init__",
        "print_report",
    ]


def test_inline_class_preserves_mixed_rdp_target_shapes_through_full_pipeline():
    """RDP target aliases must survive contract parsing and stale-target guards."""

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "inline_class_mixed_rdp_targets",
        "language": "python",
        "source_files": [{
            "file_name": "07_feature_envy_student_report.py",
            "source_code": SIMPLE_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "inline_class_mixed_rdp_targets_plan",
            "actions": [
                {
                    "action_type": "move_python_method",
                    "parameters": {
                        "method": "print_report",
                        "source_class": "ReportPrinter",
                        "destination_class": "Student",
                        "destination_parameter": "student",
                        "source_file": "07_feature_envy_student_report.py",
                    },
                },
                {
                    "action_type": "inline_python_class",
                    "parameters": {
                        "source_file": "07_feature_envy_student_report.py",
                    },
                    "target": {"class": "Student"},
                },
                {
                    "action_type": "inline_python_class",
                    "parameters": {
                        "target_class": "ReportPrinter",
                        "source_file": "07_feature_envy_student_report.py",
                    },
                },
            ],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["plan_compliance"]["inline_class"] == "PASS"
    assert "class ReportPrinter" not in result["refactored_code"]
    logs = result["safety_report"]["transformation_log"]
    assert logs[1]["metadata"]["normalized_target_class"] == "Student"
    assert logs[1]["metadata"]["requested_target"]["class_to_inline"] == "Student"
    assert logs[1]["metadata"]["status"] == "satisfied"
    assert logs[1]["metadata"]["reason"] == "SMELL_RESOLVED_BY_PRIOR_REFACTORING"
    assert logs[2]["metadata"]["normalized_target_class"] == "ReportPrinter"
    assert logs[2]["metadata"]["requested_target"]["class_to_inline"] == "ReportPrinter"
    assert logs[2]["metadata"]["status"] == "success"
    assert logs[2]["metadata"]["reason"] == "SAFE_EMPTY_CLASS_REMOVAL"
    inline_validation = result["validation"]["structural"]["details"][
        "inline_class_validation"
    ]
    assert len(inline_validation) == 2
    assert all(item["passed"] is True for item in inline_validation)


def test_empty_inline_class_cleanup_requires_review_when_instance_is_still_live():
    source = '''class ReportPrinter:
    pass


def run_report():
    printer = ReportPrinter()
    return printer
'''

    transformed, replacements, metadata = python_transformers.apply_inline_class(
        source,
        class_to_inline="ReportPrinter",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "EMPTY_CLASS_INSTANCE_STILL_LIVE"


def test_explicit_inline_target_recovers_from_stale_rdp_file_before_engine_guard():
    """A stale RDP path must not block a class found in the imported AST."""

    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "ReportPrinter",
            "source_file": "obsolete_rdp_path.py",
        },
    )
    source = SourceFileContract(
        file_name="current_report.py",
        source_code=SIMPLE_SOURCE,
        language="python",
        source_mode="raw",
    )

    SafeCodeTransformationValidationAgent._resolve_action_source_files(
        [action], [source]
    )
    SafeCodeTransformationValidationAgent._resolve_inline_class_source_files(
        [action], [source]
    )

    assert action.parameters["source_file"] == "current_report.py"
    assert action.parameters["source_resolution_status"] == "success"
    assert action.parameters["target_resolution"] == "explicit_plan_target"
    assert "source_resolution_error" not in action.parameters
    assert "source_file_resolution_error" not in action.parameters


def test_engine_rechecks_current_ast_before_honoring_stale_inline_guard():
    source = '''class ReportPrinter:
    pass


def run_report():
    printer = ReportPrinter()
    return "done"
'''
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={
            "class_to_inline": "ReportPrinter",
            "not_applicable_to_source": True,
            "not_applicable_reason": "INLINE_CLASS_TARGET_NOT_FOUND",
        },
    )

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=source,
        actions=[action],
        strict_mode=True,
    )

    assert logs[0].metadata["status"] == "success"
    assert logs[0].metadata["inline_mode"] == "empty_class_cleanup"
    assert "class ReportPrinter" not in transformed
    assert "stale_rdp_target_guard" not in str(logs[0].metadata)


def test_empty_class_cleanup_is_blocked_by_repository_reference():
    source = '''class ReportPrinter:
    pass
'''
    action = RefactoringAction(
        action_type="inline_python_class",
        parameters={"class_to_inline": "ReportPrinter"},
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="report_printer.py",
        project_source_files=[
            {
                "file_name": "report_printer.py",
                "language": "python",
                "source_code": source,
            },
            {
                "file_name": "consumer.py",
                "language": "python",
                "source_code": "from report_printer import ReportPrinter\n",
            },
        ],
        repository_complete=True,
    )

    assert transformed == source
    assert logs[0].metadata["status"] == "review_required"
    assert logs[0].metadata["reason"] == "EXTERNAL_REPOSITORY_REFERENCE"
    assert logs[0].metadata["reference_file"] == "consumer.py"
