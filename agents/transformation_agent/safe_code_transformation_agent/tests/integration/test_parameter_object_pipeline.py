from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter


def _options(require_compilation: bool = False) -> dict[str, object]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": 10,
        "require_compilation": require_compilation,
        "enable_sctva_auto_refactoring": False,
    }


def test_python_parameter_object_full_pipeline_passes_all_checks():
    source = '''def calculate_invoice(
    customer: str,
    item: str,
    quantity: int,
    unit_price: float,
    discount_rate: float,
):
    return customer + item, quantity * unit_price * (1 - discount_rate)
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_parameter_object",
        "language": "python",
        "source_files": [{
            "file_name": "invoice.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_parameter_object_plan",
            "actions": [{
                "action_type": "introduce_python_parameter_object",
                "parameters": {
                    "source_file": "invoice.py",
                    "method": "calculate_invoice",
                    "parameter_object_name": "CalculateInvoiceParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })
    log = result["safety_report"]["transformation_log"][0]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert log["metadata"]["final_decision"] == "PASS"
    assert all(value == "PASS" for value in log["metadata"]["final_checks"].values())


def test_python_parameter_object_static_fallback_does_not_rollback_valid_migration():
    # The unsupported import intentionally makes SCTVA use static fingerprints,
    # matching projects where importing source could start external work.
    source = '''import os

def create_invoice(
    customer_name: str,
    customer_email: str,
    item_name: str,
    quantity: int,
    unit_price: float,
    discount_rate: float = 0.0,
) -> float:
    return quantity * unit_price * (1 - discount_rate)
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "python_parameter_object_static_fallback",
        "language": "python",
        "source_files": [{
            "file_name": "03_too_many_parameters_invoice.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "python_parameter_object_static_fallback_plan",
            "actions": [{
                "action_type": "introduce_python_parameter_object",
                "parameters": {
                    "source_file": "03_too_many_parameters_invoice.py",
                    "method": "create_invoice",
                    "parameter_object_name": "create_invoiceParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    behavioral = result["validation"]["behavioral"]
    invariant = result["validation"]["invariant"]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert behavioral["passed"] is True
    assert behavioral["details"]["fingerprints"][0]["comparison"]["reason"] == (
        "parameter_object_signature_migration_preserved"
    )
    assert invariant["passed"] is True


def test_java_parameter_object_full_pipeline_passes_all_checks():
    source = '''class InvoiceService {
    double calculateInvoice(String customer, String item, int quantity, double price, double discount) {
        return quantity * price * (1 - discount);
    }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object",
        "language": "java",
        "source_files": [{
            "file_name": "InvoiceService.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "InvoiceService.java",
                    "source_class": "InvoiceService",
                    "method": "calculateInvoice",
                    "parameter_object_name": "CalculateInvoiceParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(require_compilation=True),
    })
    log = result["safety_report"]["transformation_log"][0]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert log["metadata"]["final_decision"] == "PASS"
    assert all(value == "PASS" for value in log["metadata"]["final_checks"].values())


def test_java_parameter_object_resolves_numbered_filename_to_unique_method_owner():
    file_name = "03_too_many_parameters_invoice.java"
    source = '''class TooManyParametersInvoice {
    static double calculateInvoice(String customer, String item, int quantity,
            double unitPrice, double discountRate, double taxRate,
            double shippingFee, String currency) {
        double subtotal = quantity * unitPrice;
        double discounted = subtotal - subtotal * discountRate;
        return discounted + discounted * taxRate + shippingFee;
    }
    public static void main(String[] args) {
        System.out.println(calculateInvoice("Nimal", "Keyboard", 2, 4500,
                0.05, 0.18, 500, "LKR"));
    }
}
'''
    plan = PlannerAdapter().normalize_plan({
        "plan_id": "numbered_parameter_object",
        "steps": [{
            "step_id": 1,
            "refactoring": "Introduce Parameter Object",
            "smell": "Long Parameter List",
            "target": {
                "class": "03_too_many_parameters_invoice",
                "file": file_name,
                "method": "calculateInvoice",
            },
            "parameters": {
                "method": "calculateInvoice",
                "parameter_object_name": "calculateInvoiceParams",
                "source_file": file_name,
            },
        }],
    })
    assert plan["actions"][0]["parameters"]["source_class_origin"] == "file_stem_fallback"

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "numbered_parameter_object",
        "language": "java",
        "source_files": [{
            "file_name": file_name,
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": plan,
        "execution_options": _options(require_compilation=True),
    })

    log = result["safety_report"]["transformation_log"][0]
    metadata = log["metadata"]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert metadata["source_class"] == "TooManyParametersInvoice"
    assert metadata["requested_source_class"] == "03_too_many_parameters_invoice"
    assert metadata["source_class_resolution"] == "parsed_unique_method_owner"
    assert metadata["final_decision"] == "PASS"
    assert "static class calculateInvoiceParams" in result["refactored_code"]
    assert "calculateInvoice(calculateInvoiceParams params)" in result["refactored_code"]
    assert "calculateInvoice(new calculateInvoiceParams(" in result["refactored_code"]
