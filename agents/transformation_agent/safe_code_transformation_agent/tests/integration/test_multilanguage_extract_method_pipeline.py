from sctva.agent import SafeCodeTransformationValidationAgent


JAVA_SOURCE = '''public class Calculator {
    public static int process(int a, int b) {
        int subtotal = a + b;
        int discount = subtotal / 10;
        int tax = subtotal / 5;
        int total = subtotal - discount + tax;
        System.out.println(total);
        return total;
    }
}
'''


C_SOURCE = '''int process(int a, int b) {
    int subtotal = a + b;
    int discount = subtotal / 10;
    int tax = subtotal / 5;
    int total = subtotal - discount + tax;
    int observed = total;
    return observed;
}
'''


def _options() -> dict[str, object]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": 10,
        "require_compilation": False,
        "enable_sctva_auto_refactoring": False,
    }


def _assert_extract_method_passed(result: dict[str, object], language: str) -> None:
    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    checks = metadata["final_checks"]
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert metadata["language"] == language
    assert metadata["final_status"] == "PASS"
    assert metadata["final_decision"] == "PASS"
    assert checks["plan_compliance"] == "PASS"
    assert checks["extract_method_structural_validation"] == "PASS"
    assert checks["long_method_reduction"] == "PASS"
    assert checks["behavior_preservation"] == "PASS"
    assert checks["compilation_syntax_validation"] == "PASS"
    assert checks["no_severe_new_smell"] == "PASS"
    assert metadata["after_loc"] < metadata["before_loc"]


def test_java_extract_method_runs_through_transactional_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_extract_method_pipeline",
        "language": "java",
        "source_files": [{
            "file_name": "src/Unrelated.java",
            "source_code": "public class Unrelated { public int value() { return 1; } }\n",
            "language": "java",
            "source_mode": "raw",
        }, {
            "file_name": "src/Calculator.java",
            "source_code": JAVA_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_extract_method_plan",
            "actions": [{
                "action_type": "extract_method",
                "parameters": {
                    "source_class": "Calculator",
                    "method": "process",
                    "new_method_name": "calculateTotal",
                    "start_line": 1000,
                    "end_line": 1010,
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["file_name"] == "src/Calculator.java"
    assert "private static int calculateTotal(" in result["refactored_code"]
    _assert_extract_method_passed(result, "java")


def test_c_extract_function_runs_through_transactional_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "c_extract_method_pipeline",
        "language": "c",
        "source_files": [{
            "file_name": "src/unrelated.c",
            "source_code": "int unrelated(void) { return 1; }\n",
            "language": "c",
            "source_mode": "raw",
        }, {
            "file_name": "src/calculator.c",
            "source_code": C_SOURCE,
            "language": "c",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "c_extract_method_plan",
            "actions": [{
                "action_type": "extract_method",
                "parameters": {
                    "function": "process",
                    "new_function_name": "calculate_total",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _options(),
    })

    assert result["file_name"] == "src/calculator.c"
    assert "static void calculate_total(" in result["refactored_code"]
    _assert_extract_method_passed(result, "c")
