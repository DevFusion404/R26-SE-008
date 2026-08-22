from sctva.contracts import RefactoringAction
from sctva.validators.behavior_fingerprint import compare_fingerprints
from sctva.validators.behavioral_validator import BehavioralValidator


def test_java_nested_class_uses_binary_runtime_name():
    source = """class Outer {
    static class LibraryManager {
        int count() { return 1; }
    }
}
"""

    assert BehavioralValidator._java_binary_class_name(
        source, "LibraryManager"
    ) == "Outer$LibraryManager"


def test_java_extract_class_probes_target_nested_source_class():
    source = """class Outer {
    static class LibraryManager {
        int count() { return 1; }
    }
    public static void main(String[] args) { }
}
"""
    validator = BehavioralValidator()
    probes = validator._infer_java_runtime_tests_from_source(
        original_code=source,
        actions=[RefactoringAction(
            action_type="extract_java_class",
            parameters={"source_class": "LibraryManager"},
        )],
    )

    assert probes
    assert probes[0]["original_target_class"] == "LibraryManager"
    assert probes[0]["original_target_method"] == "count"


def test_java_source_probe_fallback_uses_direct_nested_method_owner():
    source = """class Outer {
    static class LibraryManager {
        int count() { return 1; }
        boolean available() { return true; }
    }
    public static void main(String[] args) { }
}
"""
    validator = BehavioralValidator()
    probes = validator._infer_java_runtime_tests_from_source(
        original_code=source,
        actions=[RefactoringAction(
            action_type="introduce_constant",
            parameters={"literal_value": 1, "constant_name": "CONSTANT_1"},
        )],
    )

    assert probes
    assert {probe["original_target_class"] for probe in probes} == {"LibraryManager"}
    assert {probe["original_target_method"] for probe in probes} == {
        "count", "available",
    }


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


def test_compare_fingerprints_rejects_matching_missing_java_method():
    fingerprint = {
        "success": False,
        "timeout": False,
        "exception_type": "NoSuchMethodException",
        "exception_message_category": "missing target method or parameter count mismatch",
    }

    comparison = compare_fingerprints(fingerprint, fingerprint)

    assert comparison["matched"] is False
    assert comparison["reason"] == "fingerprint_execution_failed"


def test_java_dependency_compile_failure_uses_static_fallback():
    java_with_missing_dependency = """
import missing.Dependency;

public class Demo {
    public int value() {
        return 1;
    }
}
"""
    transformed = """
import missing.Dependency;

public class Demo {
    public int valueRenamed() {
        return 1;
    }
}
"""
    actions = [
        RefactoringAction(
            action_type="rename_symbol",
            parameters={
                "old_name": "value",
                "new_name": "valueRenamed",
            },
        )
    ]

    validator = BehavioralValidator()
    result = validator.validate(
        language="java",
        original_code=java_with_missing_dependency,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=actions,
        strict_mode=False,
    )

    assert result.passed is True
    assert result.details["fingerprint_status"] == "degraded_static_passed"
    assert result.details["runtime_unavailable_reason"] == "missing_java_dependencies"


def test_java_dependency_classifier_accepts_missing_servlet_classpath():
    fp = {
        "success": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
        "stderr": (
            "SignUpServlet.java:3: error: package javax.servlet.http does not exist\n"
            "public class SignUpServlet extends HttpServlet {\n"
            "                                   ^\n"
            "symbol: class HttpServlet\n"
        ),
    }

    assert BehavioralValidator._fingerprint_dependency_unavailable(fp, language="java") is True


def test_java_dependency_classifier_accepts_declared_magic_in_servlet_dependency_error():
    fp = {
        "success": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
        "stderr": (
            "SignUpServlet.java:53: error: cannot find symbol\n"
            "        request.setAttribute(MAGIC_STRING_ERRORMESSAGE, \"Password Do Not Match!.\");\n"
            "               ^\n"
            "symbol:   method setAttribute(String,String)\n"
            "location: variable request of type HttpServletRequest\n"
        ),
    }

    assert BehavioralValidator._fingerprint_dependency_unavailable(fp, language="java") is True


def test_java_dependency_classifier_rejects_unresolved_sctva_constant():
    fp = {
        "success": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
        "stderr": (
            "OrderServlet.java:21: error: cannot find symbol\n"
            "        String action = MAGIC_STRING_DELETE;\n"
            "                        ^\n"
            "symbol:   variable MAGIC_STRING_DELETE\n"
        ),
    }

    assert BehavioralValidator._fingerprint_dependency_unavailable(fp, language="java") is False


def test_java_real_compile_failure_still_fails_behavioral_validation():
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


def test_c_static_behavioral_fingerprint_passes_without_behavior_tests():
    original = "#include <stdio.h>\ndouble ratio(void) { return 0.12; }\n"
    transformed = "#include <stdio.h>\n#define CONSTANT_NUMBER_0_12 0.12\ndouble ratio(void) { return CONSTANT_NUMBER_0_12; }\n"
    validator = BehavioralValidator()
    result = validator.validate(
        language="c",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=[
            RefactoringAction(
                action_type="introduce_constant",
                parameters={"literal_value": 0.12, "constant_name": "EXTRACTED_CONSTANT"},
            )
        ],
        strict_mode=False,
    )

    assert result.passed is True
    assert result.details["fingerprint_status"] == "passed"
    assert "static_c_fingerprint" in result.details["c_results"][0]["mode"]
