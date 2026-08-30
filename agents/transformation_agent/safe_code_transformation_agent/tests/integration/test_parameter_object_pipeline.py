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
    ledger = log["metadata"]["parameter_object_ledger_entry"]
    assert ledger["old_parameters"] == ["customer", "item", "quantity", "price", "discount"]
    assert ledger["new_signature"] == ["CalculateInvoiceParams"]
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


def test_java_parameter_object_static_fallback_preserves_prior_extract_method():
    source = '''import missing.Dependency;

public class Calculator {
    public static int process(int a, int b, int c, int d, int e, int f, int g) {
        int subtotal = a + b;
        int discount = subtotal / c;
        int tax = subtotal / d;
        int total = subtotal - discount + tax + e + f + g;
        System.out.println(total);
        return total;
    }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_after_extract_method",
        "language": "java",
        "source_files": [{
            "file_name": "Calculator.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_after_extract_method_plan",
            "actions": [{
                "action_type": "extract_method",
                "parameters": {
                    "source_file": "Calculator.java",
                    "source_class": "Calculator",
                    "method": "process",
                    "new_method_name": "calculateTotal",
                    "start_line": 1000,
                    "end_line": 1010,
                },
            }, {
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "Calculator.java",
                    "source_class": "Calculator",
                    "method": "process",
                    "parameter_object_name": "ProcessParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    logs = result["safety_report"]["transformation_log"]
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert result["validation"]["behavioral"]["details"][
        "behavioral_validation_mode"
    ] == "refactoring_aware_static_fallback"
    assert logs[0]["metadata"]["final_decision"] == "PASS"
    assert logs[1]["metadata"]["final_decision"] == "PASS"
    assert "calculateTotal(" in result["refactored_code"]
    assert "process(ProcessParams params)" in result["refactored_code"]


def test_java_parameter_object_updates_instance_cross_file_caller():
    source = '''class Service {
    int combine(int a, int b, int c) { return a + b + c; }
}
'''
    caller = '''class Caller {
    int call(Service service) { return service.combine(1, 2, 3); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_cross_file_instance",
        "language": "java",
        "source_files": [{
            "file_name": "Service.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }, {
            "file_name": "Caller.java",
            "source_code": caller,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_cross_file_instance_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "Service.java",
                    "source_class": "Service",
                    "method": "combine",
                    "parameter_object_name": "CombineParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["success"] is True, result
    by_file = {item["file_name"]: item for item in result["file_results"]}
    assert "combine(CombineParams params)" in by_file["Service.java"]["refactored_code"]
    assert "service.combine(new Service.CombineParams(1, 2, 3))" in by_file["Caller.java"]["refactored_code"]


def test_java_parameter_object_ignores_same_name_call_in_unrelated_class():
    service = '''class CustomerDb {
    static int insert(String customer, int amount) { return amount; }
}
'''
    unrelated = '''class AuditStore {
    int insert(String value, int amount) { return amount; }
    int audit() { return insert("audit", 1); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_unrelated_same_name",
        "language": "java",
        "source_files": [
            {"file_name": "CustomerDb.java", "source_code": service, "language": "java", "source_mode": "raw"},
            {"file_name": "AuditStore.java", "source_code": unrelated, "language": "java", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_unrelated_same_name_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "CustomerDb.java",
                    "source_class": "CustomerDb",
                    "method": "insert",
                    "parameter_object_name": "InsertParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })
    assert result["success"] is True, result
    assert "insert(InsertParams params)" in result["refactored_code"]


def test_java_parameter_object_updates_real_cross_file_callers_as_one_transaction():
    service = '''class CustomerDb {
    static int insert(String customer, int amount) { return amount; }
}
'''
    caller = '''class CustomerController {
    int create() { return CustomerDb.insert("Nimal", 7); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_coordinated_callers",
        "language": "java",
        "source_files": [
            {"file_name": "CustomerDb.java", "source_code": service, "language": "java", "source_mode": "raw"},
            {"file_name": "CustomerController.java", "source_code": caller, "language": "java", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_coordinated_callers_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "CustomerDb.java",
                    "source_class": "CustomerDb",
                    "method": "insert",
                    "parameter_object_name": "InsertParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(require_compilation=True),
    })
    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    by_file = {item["file_name"]: item for item in result["file_results"]}
    assert "insert(InsertParams params)" in by_file["CustomerDb.java"]["refactored_code"]
    assert "CustomerDb.insert(new CustomerDb.InsertParams(\"Nimal\", 7))" in by_file["CustomerController.java"]["refactored_code"]


def test_java_parameter_object_resolves_lowercase_target_class_receivers():
    service = '''class customer_DBUtil {
    static int insert(String customer, String address) { return customer.length(); }
}
'''
    caller = '''class CustomerController {
    int create() { return customer_DBUtil.insert("Nimal", "Colombo"); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_lowercase_class",
        "language": "java",
        "source_files": [
            {"file_name": "customer_DBUtil.java", "source_code": service, "language": "java", "source_mode": "raw"},
            {"file_name": "CustomerController.java", "source_code": caller, "language": "java", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_lowercase_class_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "customer_DBUtil.java",
                    "source_class": "customer_DBUtil",
                    "method": "insert",
                    "parameter_object_name": "InsertParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(require_compilation=True),
    })
    assert result["success"] is True, result
    by_file = {item["file_name"]: item for item in result["file_results"]}
    assert "customer_DBUtil.insert(new customer_DBUtil.InsertParams" in by_file["CustomerController.java"]["refactored_code"]


def test_java_parameter_object_updates_static_import_and_multiple_real_callers():
    service = '''package shop;
public class CustomerDb {
    public static int insert(String customer, int amount) { return amount; }
}
'''
    first_caller = '''package shop;
import static shop.CustomerDb.insert;
class FirstController {
    int create() { return insert("Nimal", 7); }
}
'''
    second_caller = '''package shop;
class SecondController {
    int create() { return CustomerDb.insert("Kamal", 9); }
}
'''
    unrelated = '''package shop;
class AuditStore {
    static int insert(String label, int code) { return code; }
    int audit() { return insert("audit", 1); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_multiple_callers",
        "language": "java",
        "source_files": [
            {"file_name": "shop/CustomerDb.java", "source_code": service, "language": "java", "source_mode": "raw"},
            {"file_name": "shop/FirstController.java", "source_code": first_caller, "language": "java", "source_mode": "raw"},
            {"file_name": "shop/SecondController.java", "source_code": second_caller, "language": "java", "source_mode": "raw"},
            {"file_name": "shop/AuditStore.java", "source_code": unrelated, "language": "java", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_multiple_callers_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "shop/CustomerDb.java",
                    "source_class": "CustomerDb",
                    "method": "insert",
                    "parameter_object_name": "InsertParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(require_compilation=True),
    })
    assert result["success"] is True, result
    by_file = {item["file_name"]: item for item in result["file_results"]}
    assert "insert(new CustomerDb.InsertParams(\"Nimal\", 7))" in by_file["shop/FirstController.java"]["refactored_code"]
    assert "CustomerDb.insert(new CustomerDb.InsertParams(\"Kamal\", 9))" in by_file["shop/SecondController.java"]["refactored_code"]
    assert "insert(\"audit\", 1)" in unrelated


def test_java_parameter_object_unresolved_cross_file_receiver_remains_review_required():
    service = '''class CustomerDb {
    static int insert(String customer, int amount) { return amount; }
}
'''
    caller = '''class CustomerController {
    int create() { return insert("Nimal", 7); }
}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_parameter_object_unresolved_caller",
        "language": "java",
        "source_files": [
            {"file_name": "CustomerDb.java", "source_code": service, "language": "java", "source_mode": "raw"},
            {"file_name": "CustomerController.java", "source_code": caller, "language": "java", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_unresolved_caller_plan",
            "actions": [{
                "action_type": "introduce_java_parameter_object",
                "parameters": {
                    "source_file": "CustomerDb.java",
                    "source_class": "CustomerDb",
                    "method": "insert",
                    "parameter_object_name": "InsertParams",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })
    log = result["safety_report"]["transformation_log"][0]
    assert result["transformation_applied"] is False
    assert log["metadata"]["reason"] == "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT"


def test_java_parameter_object_selective_replay_preserves_independent_action():
    source = '''class Service {
    static int combine(String left, String right) { return left.length() + right.length(); }
    static int calculate(int quantity, int price) { return quantity * price; }
    static int sample() { return combine("A", "B") + calculate(2, 3); }
}
'''
    agent = SafeCodeTransformationValidationAgent()
    original_validate = agent.structural_validator.validate
    calls = {"count": 0}

    def fail_second_parameter_object_once(**kwargs):
        result = original_validate(**kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            checks = result.details["parameter_object_validation"]
            checks[1]["passed"] = False
            checks[1]["checks"]["body_access_migrated"] = False
            result.passed = False
        return result

    agent.structural_validator.validate = fail_second_parameter_object_once
    result = agent.execute({
        "request_id": "java_parameter_object_selective_replay",
        "language": "java",
        "source_files": [{
            "file_name": "Service.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_parameter_object_selective_replay_plan",
            "actions": [
                {
                    "action_type": "introduce_java_parameter_object",
                    "parameters": {
                        "source_file": "Service.java",
                        "source_class": "Service",
                        "method": "combine",
                        "parameter_object_name": "CombineParams",
                    },
                },
                {
                    "action_type": "introduce_java_parameter_object",
                    "parameters": {
                        "source_file": "Service.java",
                        "source_class": "Service",
                        "method": "calculate",
                        "parameter_object_name": "CalculateParams",
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
    assert "combine(CombineParams params)" in result["refactored_code"]
    assert "calculate(int quantity, int price)" in result["refactored_code"]
    assert any(
        "Selective rollback preserved independent accepted actions" in message
        for message in result["safety_report"]["human_messages"]
    )
