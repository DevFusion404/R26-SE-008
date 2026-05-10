from sctva.contracts import RefactoringAction
from sctva.validators.behavior_fingerprint import compare_fingerprints
from sctva.validators.behavioral_validator import BehavioralValidator


def test_behavior_match_success():
    original = """
def add(a, b):
    return a + b
"""
    transformed = original
    tests = [{"name": "add_simple", "call": "add", "args": [1, 2]}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is True


def test_behavior_return_value_mismatch():
    original = """
def f():
    return 1
"""
    transformed = """
def f():
    return 2
"""
    tests = [{"name": "f_ret", "call": "f"}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is False


def test_behavior_return_type_mismatch():
    original = """
def g():
    return 1
"""
    transformed = """
def g():
    return '1'
"""
    tests = [{"name": "g_type", "call": "g"}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is False


def test_behavior_same_exception():
    original = """
def h():
    raise ValueError('oops')
"""
    transformed = original
    tests = [{"name": "h_exc", "call": "h"}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is True


def test_behavior_different_exception():
    original = """
def i():
    raise ValueError('oops')
"""
    transformed = """
def i():
    raise TypeError('oops')
"""
    tests = [{"name": "i_exc", "call": "i"}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is False


def test_behavior_timeout_handling():
    original = """
import time
def sleepy():
    time.sleep(2)
    return 1
"""
    transformed = original
    tests = [{"name": "sleepy", "call": "sleepy", "timeout_seconds": 0.5}]
    v = BehavioralValidator()
    res = v.validate(
        language="python",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    assert res.passed is False


def test_java_behavior_skipped_when_no_command():
    original = "class A {}"
    transformed = "class A {}"
    tests = [{"name": "java_no_cmd"}]
    v = BehavioralValidator()
    res = v.validate(
        language="java",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=tests,
        enable_behavior_tests=True,
        actions=[],
        strict_mode=False,
    )
    # java_results should indicate skipped
    details = res.details
    assert any(r.get("status") == "skipped" for r in details.get("java_results", []))


def test_java_fault_injection_uses_runtime_probe_when_no_command():
    original = """
public class Demo {
    public double calculateTotal() {
        double total = 2.0;
        return total;
    }
}
"""
    transformed = """
public class Demo {
    public double calculateTotal() {
        double total = 2.0;
        return total + 1;
    }
}
"""
    behavior_tests = []
    actions = [
        RefactoringAction(
            action_type="fault_injection",
            parameters={
                "original_logic": "return total;",
                "faulty_logic": "return total + 1;",
                "target_class": "Demo",
                "target_method": "calculateTotal",
            },
        )
    ]

    validator = BehavioralValidator()
    result = validator.validate(
        language="java",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=behavior_tests,
        enable_behavior_tests=True,
        actions=actions,
        strict_mode=False,
    )

    assert result.details["fingerprint_status"] != "skipped"
    assert result.details["java_results"]
    assert result.details["java_results"][0]["status"] == "ran"


def test_compare_fingerprints_rejects_matching_infrastructure_failures():
    original_fp = {
        "success": False,
        "timeout": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
    }
    transformed_fp = {
        "success": False,
        "timeout": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
    }

    comparison = compare_fingerprints(original_fp, transformed_fp)

    assert comparison["matched"] is False
    assert comparison["reason"] == "fingerprint_execution_failed"


def test_java_fingerprint_compile_failure_fails_behavioral_validation():
    broken_java = """
public class Demo {
    public double calculateTotal() {
        return ;
    }
}
"""
    actions = [
        RefactoringAction(
            action_type="fault_injection",
            parameters={
                "target_class": "Demo",
                "target_method": "calculateTotal",
            },
        )
    ]

    validator = BehavioralValidator()
    result = validator.validate(
        language="java",
        original_code=broken_java,
        transformed_code=broken_java,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=actions,
        strict_mode=False,
    )

    assert result.passed is False
    assert result.details["fingerprint_status"] == "failed"
    assert "fingerprint_execution_failed" in result.details["failures"][0]
