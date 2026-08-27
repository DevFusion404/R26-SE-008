import contextlib
import io

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import java_hide_delegate, python_hide_delegate
from sctva.transformers.engine import TransformationEngine
from sctva.validators.structural_validator import StructuralValidator


PYTHON_SOURCE = '''class Address:
    def __init__(self, city, postcode):
        self.city = city
        self.postcode = postcode

    def get_postcode(self):
        return self.postcode


class Customer:
    def __init__(self, address):
        self.address = address


def city_label(customer: Customer):
    return customer.address.city


def postcode_label(customer: Customer):
    return customer.address.get_postcode()


def unrelated(address: Address):
    return address.city


def main():
    customer = Customer(Address("Colombo", "10100"))
    print(city_label(customer))
    print(postcode_label(customer))
    print(unrelated(customer.address))


if __name__ == "__main__":
    main()
'''


JAVA_SOURCE = '''class Address {
    private final String city;

    Address(String city) {
        this.city = city;
    }

    public String getCity() {
        return city;
    }
}

class Customer {
    private final Address address;

    Customer(Address address) {
        this.address = address;
    }

    public Address getAddress() {
        return address;
    }
}

class App {
    static String city(Customer customer) {
        return customer.getAddress().getCity();
    }
}
'''


MESSAGE_CHAIN_SOURCE = '''class Manager:
    def __init__(self, name, email):
        self.name = name
        self.email = email

class Department:
    def __init__(self, name, manager):
        self.name = name
        self.manager = manager

class Employee:
    def __init__(self, employee_id, name, department):
        self.employee_id = employee_id
        self.name = name
        self.department = department

def print_employee_report(employee):
    print(employee.department.name)
    print(employee.department.manager.name)
    print(employee.department.manager.email)

def main():
    manager = Manager("Nadeesha", "n@example.com")
    department = Department("Software", manager)
    employee = Employee("E001", "Kamal", department)
    print_employee_report(employee)
'''


def _run_python_main(source: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(source, "<hide-delegate>", "exec"), {"__name__": "__main__"})
    return output.getvalue()


def test_python_hide_delegate_moves_field_navigation_to_owner():
    transformed, replacements, metadata = python_hide_delegate.apply_hide_delegate(
        PYTHON_SOURCE,
        source_class="Customer",
        delegate_member="address",
        delegated_member="city",
        new_method_name="get_city",
    )

    assert metadata["status"] == "success"
    assert replacements == 2
    assert "def get_city(self):" in transformed
    assert "return self.address.city" in transformed
    assert "return customer.get_city()" in transformed
    assert "return address.city" in transformed
    assert _run_python_main(transformed) == _run_python_main(PYTHON_SOURCE)


def test_python_hide_delegate_supports_a_no_argument_delegated_method():
    transformed, _, metadata = python_hide_delegate.apply_hide_delegate(
        PYTHON_SOURCE,
        source_class="Customer",
        delegate_member="address",
        delegated_member="get_postcode",
        new_method_name="get_postcode",
    )

    assert metadata["status"] == "success"
    assert "return self.address.get_postcode()" in transformed
    assert "return customer.get_postcode()" in transformed


def test_python_hide_delegate_dynamic_access_requires_review():
    source = PYTHON_SOURCE + '\nvalue = getattr(Customer(Address("x", "y")), "address")\n'
    transformed, replacements, metadata = python_hide_delegate.apply_hide_delegate(
        source,
        source_class="Customer",
        delegate_member="address",
        delegated_member="city",
        new_method_name="get_city",
    )
    assert transformed == source
    assert replacements == 0
    assert metadata["reason"] == "DYNAMIC_ATTRIBUTE_ACCESS_UNSUPPORTED"


def test_java_hide_delegate_rewrites_getter_chain_and_creates_forwarder():
    transformed, replacements, metadata = java_hide_delegate.apply_hide_delegate(
        JAVA_SOURCE,
        source_class="Customer",
        delegate_member="address",
        delegated_member="city",
        new_method_name="getCity",
    )

    assert metadata["status"] == "success", metadata
    assert replacements == 2
    assert "public String getCity()" in transformed
    assert "return address.getCity();" in transformed
    assert "return customer.getCity();" in transformed


def test_hide_delegate_structural_validation_rejects_partial_transformation():
    transformed, _, metadata = python_hide_delegate.apply_hide_delegate(
        PYTHON_SOURCE,
        source_class="Customer",
        delegate_member="address",
        delegated_member="city",
        new_method_name="get_city",
    )
    action = RefactoringAction(
        action_type="hide_delegate",
        parameters=metadata["effective_action_parameters"],
    )
    passed = StructuralValidator().validate(
        language="python",
        original_code=PYTHON_SOURCE,
        transformed_code=transformed,
        actions=[action],
    )
    assert passed.passed is True, passed.details

    partial = transformed.replace("return customer.get_city()", "return customer.address.city", 1)
    failed = StructuralValidator().validate(
        language="python",
        original_code=PYTHON_SOURCE,
        transformed_code=partial,
        actions=[action],
    )
    assert failed.passed is False
    assert failed.details["hide_delegate_validation"][0]["checks"]["client_message_chain_shortened"] is False


def test_engine_pipeline_and_planner_use_dedicated_hide_delegate_action():
    action = RefactoringAction(
        action_type="hide_delegate",
        parameters={
            "source_class": "Customer",
            "delegate_member": "address",
            "delegated_member": "city",
            "new_method_name": "get_city",
        },
    )
    transformed, logs, _ = TransformationEngine().apply_actions(
        language="python",
        source_code=PYTHON_SOURCE,
        actions=[action],
        strict_mode=True,
    )
    assert logs[0].metadata["status"] == "success"
    assert "customer.get_city()" in transformed

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "hide_delegate_pipeline",
        "language": "python",
        "source_code": PYTHON_SOURCE,
        "refactoring_plan": {
            "plan_id": "hide_delegate_pipeline",
            "actions": [{
                "action_type": "hide_delegate",
                "parameters": action.parameters,
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "enable_sctva_auto_refactoring": False,
        },
    })
    assert result["success"] is True, result
    assert result["plan_compliance"]["hide_delegate"] == "PASS"

    plan = PlannerAdapter().normalize_plan({
        "plan_id": "hide_delegate_plan",
        "steps": [{
            "step_id": 1,
            "refactoring": "Hide Delegate",
            "target": {"file": "customer.py", "class": "Customer"},
            "parameters": {
                "source_class": "Customer",
                "delegate_member": "address",
                "delegated_member": "city",
                "new_method_name": "get_city",
                "source_file": "customer.py",
            },
        }],
    })
    assert plan["actions"][0]["action_type"] == "hide_delegate"


def test_legacy_hide_delegate_noop_is_promoted_to_the_real_transformer():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "legacy_hide_delegate_pipeline",
        "language": "python",
        "source_code": PYTHON_SOURCE,
        "refactoring_plan": {
            "plan_id": "legacy_hide_delegate_pipeline",
            "actions": [{
                "action_type": "noop",
                "source_refactoring": "Hide Delegate",
                "parameters": {
                    "source_class": "Customer",
                    "delegate_member": "address",
                    "delegated_member": "city",
                    "new_method_name": "get_city",
                },
                "warnings": [
                    "Hide Delegate needs richer semantic edits and was not simulated with a rename.",
                    "Action mapped to noop; no code change applied.",
                ],
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert result["plan_compliance"]["hide_delegate"] == "PASS"
    assert "customer.get_city()" in result["refactored_code"]
    assert not any(
        "Hide Delegate needs richer semantic edits" in message
        for message in result["safety_report"]["human_messages"]
    )


def test_adapter_preserves_malformed_hide_delegate_target_for_recovery():
    plan = PlannerAdapter().normalize_plan({
        "plan_id": "legacy_hide_delegate_target",
        "steps": [{
            "step_id": 1,
            "refactoring": "Hide Delegate",
            "target": {"file": "customer.py", "class": "Customer"},
            "parameters": {"delegate_member": "address"},
        }],
    })

    action = plan["actions"][0]
    assert action["action_type"] == "noop"
    assert action["parameters"]["legacy_step"]["target"]["class"] == "Customer"


def test_incomplete_legacy_hide_delegate_recovers_one_typed_message_chain():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "incomplete_legacy_hide_delegate",
        "language": "python",
        "source_code": PYTHON_SOURCE,
        "refactoring_plan": {
            "plan_id": "incomplete_legacy_hide_delegate",
            "actions": [{
                "action_type": "noop",
                "source_refactoring": "Hide Delegate",
                "parameters": {
                    "delegate_member": "address",
                    "delegated_member": "city",
                },
                "warnings": [
                    "Hide Delegate needs richer semantic edits and was not simulated with a rename.",
                ],
            }],
            "behavior_tests": [],
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "customer.get_city()" in result["refactored_code"]


def test_legacy_hide_delegate_splits_safe_members_of_the_same_delegate():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "ambiguous_legacy_hide_delegate",
        "language": "python",
        "source_code": PYTHON_SOURCE,
        "refactoring_plan": {
            "plan_id": "ambiguous_legacy_hide_delegate",
            "actions": [{
                "action_type": "noop",
                "source_refactoring": "Hide Delegate",
                "parameters": {},
                "warnings": [
                    "Hide Delegate needs richer semantic edits and was not simulated with a rename.",
                ],
            }],
            "behavior_tests": [],
        },
    })

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "customer.get_city()" in result["refactored_code"]
    assert "customer.get_postcode()" in result["refactored_code"]


def test_stale_unrelated_legacy_actions_do_not_fail_message_chain_plan_compliance():
    file_name = "01_message_chain_hide_delegate_employee.py"
    legacy_warning = "needs richer semantic edits and was not simulated with a rename."
    actions = [{
        "action_type": "extract_method",
        "parameters": {
            "source_file": file_name,
            "source_class": "01_message_chain_hide_delegate_employee",
            "method": "01_message_chain_hide_delegate_employee",
            "new_method_name": "extracted_message_chain",
            "start_line": 2,
            "end_line": 4,
        },
    }, {
        "action_type": "noop",
        "source_refactoring": "Move Method",
        "parameters": {"source_file": file_name},
        "warnings": [f"Move Method {legacy_warning}"],
    }]
    actions.extend({
        "action_type": "noop",
        "source_refactoring": "Inline Class",
        "parameters": {"source_file": file_name},
        "warnings": [f"Inline Class {legacy_warning}"],
    } for _ in range(3))
    actions.append({
        "action_type": "noop",
        "source_refactoring": "Hide Delegate",
        "parameters": {"source_file": file_name},
        "warnings": [f"Hide Delegate {legacy_warning}"],
    })

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "message_chain_with_stale_actions",
        "language": "python",
        "source_files": [{
            "file_name": file_name,
            "source_code": MESSAGE_CHAIN_SOURCE,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "message_chain_with_stale_actions",
            "actions": actions,
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["success"] is True, result
    assert result["plan_compliance"]["move_method"] == "NOT_APPLICABLE"
    assert result["plan_compliance"]["inline_class"] == "NOT_APPLICABLE"
    assert result["plan_compliance"]["hide_delegate"] == "PASS"
    assert "employee.get_name()" in result["refactored_code"]
    assert "employee.get_manager()" in result["refactored_code"]
    assert not any(
        flag.startswith("plan_compliance_failed")
        for flag in result["safety_report"]["risk_flags"]
    )
    statuses = {
        entry["action_type"]: entry["metadata"].get("status")
        for entry in result["safety_report"]["transformation_log"]
    }
    assert statuses["move_python_method"] == "not_applicable"
    assert statuses["inline_python_class"] == "not_applicable"
    # Stale RDP compatibility actions are retained in the transformation log,
    # but they must not be presented as failures/warnings for this source.
    assert not any(
        "could not be resolved for this source" in message
        for message in result["safety_report"]["human_messages"]
    )
