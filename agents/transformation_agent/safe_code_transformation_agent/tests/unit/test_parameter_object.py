import ast

from sctva.contracts import RefactoringAction
from sctva.transformers.java_parameter_object import apply_introduce_parameter_object as apply_java
from sctva.transformers.python_parameter_object import apply_introduce_parameter_object as apply_python
from sctva.validators.behavioral_validator import BehavioralValidator
from sctva.validators.invariant_miner import InvariantMiner
from sctva.validators.structural_validator import StructuralValidator
from sctva.validators.syntax_validator import SyntaxValidator


PYTHON_FUNCTION = '''def calculate_invoice(
    customer: str,
    item: str,
    quantity: int,
    unit_price: float,
    discount_rate: float = 0.0,
):
    subtotal = quantity * unit_price
    return customer, item, subtotal * (1 - discount_rate)

first = calculate_invoice("A", "Book", 2, 10.0)
second = calculate_invoice("B", "Pen", 3, 2.0, 0.1)
'''


JAVA_STATIC = '''class InvoiceService {
    static double calculateInvoice(
        String customer,
        String item,
        int quantity,
        double unitPrice,
        double discountRate
    ) {
        return quantity * unitPrice * (1 - discountRate);
    }

    static double first() {
        return calculateInvoice("A", "Book", 2, 10.0, 0.0);
    }

    static double second() {
        return calculateInvoice("B", "Pen", 3, 2.0, 0.1);
    }
}
'''


def test_python_parameter_object_preserves_types_defaults_calls_and_behavior():
    transformed, replacements, metadata = apply_python(
        PYTHON_FUNCTION,
        method="calculate_invoice",
        parameter_object_name="CalculateInvoiceParams",
    )
    original_ns: dict[str, object] = {}
    transformed_ns: dict[str, object] = {}
    exec(PYTHON_FUNCTION, original_ns)
    exec(transformed, transformed_ns)

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["call_sites_updated"] == 2
    assert "@dataclass\nclass CalculateInvoiceParams" in transformed
    assert "discount_rate: float = 0.0" in transformed
    target = next(
        node for node in ast.parse(transformed).body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_invoice"
    )
    assert [item.arg for item in target.args.args] == ["params"]
    assert "params.quantity * params.unit_price" in transformed
    assert original_ns["first"] == transformed_ns["first"]
    assert original_ns["second"] == transformed_ns["second"]
    ast.parse(transformed)


def test_python_parameter_object_keeps_self_and_classmethod_cls():
    instance_source = '''class Calculator:
    def total(self, a: int, b: int, c: int):
        return a + b + c
    def sample(self):
        return self.total(1, 2, 3)
'''
    transformed, count, _ = apply_python(
        instance_source,
        method="total",
        source_class="Calculator",
        parameter_object_name="TotalParams",
    )
    namespace: dict[str, object] = {}
    exec(transformed, namespace)
    assert count == 1
    assert "def total(self, params: TotalParams):" in transformed
    assert namespace["Calculator"]().sample() == 6

    class_source = '''class Calculator:
    @classmethod
    def total(cls, a: int, b: int, c: int):
        return a + b + c
    @classmethod
    def sample(cls):
        return cls.total(1, 2, 3)
'''
    transformed, count, _ = apply_python(
        class_source,
        method="total",
        source_class="Calculator",
        parameter_object_name="TotalParams",
    )
    namespace = {}
    exec(transformed, namespace)
    assert count == 1
    assert "def total(cls, params: TotalParams):" in transformed
    assert namespace["Calculator"].sample() == 6

    action = RefactoringAction(
        action_type="introduce_python_parameter_object",
        parameters={
            "method": "total",
            "source_class": "Calculator",
            "parameter_object_name": "TotalParams",
        },
    )
    behavior = BehavioralValidator().validate(
        language="python",
        original_code=instance_source,
        transformed_code=apply_python(
            instance_source,
            method="total",
            source_class="Calculator",
            parameter_object_name="TotalParams",
        )[0],
        behavior_tests=[{
            "name": "instance_total",
            "expression": "Calculator().total(2, 3, 4)",
        }],
        enable_behavior_tests=True,
        actions=[action],
        strict_mode=True,
    )
    assert behavior.passed is True
    assert behavior.details["fingerprints"][0]["transformed_expression"] == (
        "Calculator().total(TotalParams(2, 3, 4))"
    )


def test_python_parameter_object_preserves_async_and_existing_import_order():
    source = '''from decimal import Decimal

async def total(a: int, b: int, c: int):
    return Decimal(a + b + c)
'''
    transformed, count, _ = apply_python(
        source,
        method="total",
        parameter_object_name="TotalParams",
    )
    assert count == 1
    assert transformed.index("from decimal import Decimal") < transformed.index("class TotalParams")
    assert "async def total(params: TotalParams):" in transformed
    ast.parse(transformed)


def test_python_static_fingerprint_proves_parameter_object_migration():
    # ``os`` intentionally forces the no-runtime path. The signature change is
    # acceptable only because the validator can prove the full migration.
    source = '''import os

def create_invoice(customer: str, quantity: int, unit_price: float, discount: float = 0.0) -> float:
    return quantity * unit_price * (1 - discount)
'''
    action = RefactoringAction(
        action_type="introduce_python_parameter_object",
        parameters={
            "method": "create_invoice",
            "parameter_object_name": "CreateInvoiceParams",
        },
    )
    transformed, _, metadata = apply_python(
        source,
        method="create_invoice",
        parameter_object_name="CreateInvoiceParams",
    )
    assert metadata["status"] == "success"

    behavioral = BehavioralValidator().validate(
        language="python",
        original_code=source,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=[action],
        strict_mode=True,
    )
    signature = behavioral.details["fingerprints"][0]["comparison"]
    assert behavioral.passed is True
    assert signature["reason"] == "parameter_object_signature_migration_preserved"
    assert behavioral.details["fingerprints"][0]["transformed_fingerprint"]

    invariant = InvariantMiner().mine(
        language="python",
        behavioral_step=behavioral,
        actions=[action],
        strict_mode=True,
    )
    assert invariant.passed is True

    broken = transformed.replace("    quantity: int\n", "")
    rejected = BehavioralValidator().validate(
        language="python",
        original_code=source,
        transformed_code=broken,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=[action],
        strict_mode=True,
    )
    assert rejected.passed is False
    assert rejected.details["failures"] == [
        "static_function_signature_fingerprint: function_signature_mismatch"
    ]


def test_java_parameter_object_updates_static_calls_compiles_and_preserves_behavior():
    transformed, replacements, metadata = apply_java(
        JAVA_STATIC,
        method="calculateInvoice",
        source_class="InvoiceService",
        parameter_object_name="CalculateInvoiceParams",
    )
    action = RefactoringAction(
        action_type="introduce_java_parameter_object",
        parameters={
            "method": "calculateInvoice",
            "source_class": "InvoiceService",
            "parameter_object_name": "CalculateInvoiceParams",
        },
    )
    behavior = BehavioralValidator().validate(
        language="java",
        original_code=JAVA_STATIC,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=[action],
        strict_mode=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["call_sites_updated"] == 2
    assert "static class CalculateInvoiceParams" in transformed
    assert "static double calculateInvoice(CalculateInvoiceParams params)" in transformed
    assert "params.quantity * params.unitPrice" in transformed
    assert transformed.count("calculateInvoice(new CalculateInvoiceParams(") == 2
    assert SyntaxValidator().validate(
        language="java",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True
    assert behavior.passed is True


def test_java_parameter_object_preserves_instance_method_behavior():
    source = '''class Calculator {
    int total(int a, int b, int c) { return a + b + c; }
    int sample() { return total(1, 2, 3); }
}
'''
    transformed, count, _ = apply_java(
        source,
        method="total",
        source_class="Calculator",
        parameter_object_name="TotalParams",
    )
    assert count == 1
    assert "int total(TotalParams params)" in transformed
    assert "return params.a + params.b + params.c;" in transformed
    assert SyntaxValidator().validate(
        language="java",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_java_parameter_object_structural_validation_counts_helper_body_accesses():
    original = '''class CustomerService {
    public static String update(String name, String address, String email) {
        String combined = name + address;
        return combined + email;
    }
}
'''
    transformed = '''class CustomerService {
    static class UpdateParams {
        String name;
        String address;
        String email;

        UpdateParams(String name, String address, String email) {
            this.name = name;
            this.address = address;
            this.email = email;
        }
    }

    public static String update(UpdateParams params) {
        return buildUpdate(params);
    }

    private static String buildUpdate(UpdateParams params) {
        String combined = params.name + params.address;
        return combined + params.email;
    }
}
'''
    action = RefactoringAction(
        action_type="introduce_java_parameter_object",
        parameters={
            "method": "update",
            "source_class": "CustomerService",
            "parameter_object_name": "UpdateParams",
        },
    )

    result = StructuralValidator().validate(
        language="java",
        original_code=original,
        transformed_code=transformed,
        actions=[action],
    )

    assert result.passed is True
    validation = result.details["parameter_object_validation"][0]
    assert validation["checks"]["body_access_migrated"] is True
    assert validation["migrated_body_accesses"] == ["address", "email", "name"]


def test_parameter_object_rejects_duplicate_class_and_cross_file_callers():
    duplicate = '''class CalculateInvoiceParams:
    pass
def calculate_invoice(a, b, c):
    return a + b + c
'''
    transformed, count, metadata = apply_python(
        duplicate,
        method="calculate_invoice",
        parameter_object_name="CalculateInvoiceParams",
    )
    assert transformed == duplicate
    assert count == 0
    assert metadata["reason"] == "PARAMETER_OBJECT_ALREADY_EXISTS_WITH_LONG_SIGNATURE"

    transformed, count, metadata = apply_java(
        JAVA_STATIC,
        method="calculateInvoice",
        source_class="InvoiceService",
        parameter_object_name="CalculateInvoiceParams",
        current_file_name="InvoiceService.java",
        project_source_files=[
            {"file_name": "InvoiceService.java", "source_code": JAVA_STATIC},
            {"file_name": "Caller.java", "source_code": 'class Caller { double x(InvoiceService s) { return s.calculateInvoice("A", "B", 1, 2, 0); } }'},
        ],
    )
    assert transformed == JAVA_STATIC
    assert count == 0
    assert metadata["reason"] == "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT"


def test_structural_validator_rejects_parameter_class_without_signature_reduction():
    action = RefactoringAction(
        action_type="introduce_python_parameter_object",
        parameters={
            "method": "calculate_invoice",
            "parameter_object_name": "CalculateInvoiceParams",
        },
    )
    fake = '''from dataclasses import dataclass
@dataclass
class CalculateInvoiceParams:
    a: object
    b: object
    c: object
def calculate_invoice(a, b, c):
    return a + b + c
'''
    result = StructuralValidator().validate(
        language="python",
        original_code="def calculate_invoice(a, b, c):\n    return a + b + c\n",
        transformed_code=fake,
        actions=[action],
    )
    assert result.passed is False
    validation = result.details["parameter_object_validation"][0]
    assert validation["checks"]["parameter_count_reduced"] is False
    assert validation["checks"]["single_parameter_object_argument"] is False
