from agents.transformation_agent.safe_code_transformation_agent.sctva.transformers import python_transformers
from sctva.transformers import java_transformers


def test_python_extract_constant_replaces_literals():
    source = "def value():\n    return 10\n"
    transformed, count = python_transformers.apply_extract_constant(source, 10, "BASE")
    assert count >= 1
    assert "BASE" in transformed


def test_python_rename_symbol_changes_function_name():
    source = "def calc(x):\n    return x + 1\n"
    transformed, count = python_transformers.apply_rename_symbol(source, "calc", "calculate")
    assert count >= 1
    assert "def calculate" in transformed


def test_java_replace_literal_changes_value():
    source = "public class T { int x() { return 5; } }"
    transformed, count = java_transformers.apply_replace_literal(source, 5, 7)
    assert count == 1
    assert "return 7" in transformed


def test_java_rename_symbol_replaces_word_boundary_matches():
    source = "public class T { int processPayment(){ return 1; } }"
    transformed, count = java_transformers.apply_rename_symbol(source, "processPayment", "processPaymentPolymorphic")
    assert count == 1
    assert "processPaymentPolymorphic" in transformed


def test_java_fault_injection_replaces_return_logic():
    source = "public class T { double x() { double total = 1.0; return total; } }"
    transformed, count = java_transformers.apply_fault_injection(source, "return total;", "return total + 1;")
    assert count == 1
    assert "return total + 1;" in transformed


def test_python_fault_injection_replaces_return_logic():
    source = "def x(total):\n    return total\n"
    transformed, count = python_transformers.apply_fault_injection(source, "return total", "return total + 1")
    assert count == 1
    assert "return total + 1" in transformed
