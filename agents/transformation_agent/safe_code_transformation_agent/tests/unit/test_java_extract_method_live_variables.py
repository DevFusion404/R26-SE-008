from __future__ import annotations

import shutil

import pytest

from sctva.contracts import RefactoringAction
from sctva.transformers.java_extract_method import (
    _validate_java_compilation,
    apply_extract_method,
    validate_java_extract_method_result,
)
from sctva.transformers.engine import TransformationEngine
from sctva.validators.syntax_validator import SyntaxValidator


def _extract(source: str, *, start_line: int | None = None, end_line: int | None = None):
    return apply_extract_method(
        source,
        new_method_name="extractedWork",
        method_name="process",
        source_class="Example",
        start_line=start_line,
        end_line=end_line,
        current_file_name="Example.java",
    )


def _assert_compiles(source: str) -> None:
    result = SyntaxValidator().validate(
        language="java",
        source_code=source,
        require_compilation=True,
        timeout_seconds=10,
    )
    assert result.passed is True, result.to_dict()


def test_declared_inside_candidate_used_after_is_returned_with_caller_declaration():
    source = '''public class Example {
    String process(String request) {
        String forward = "";
        String action = request.trim();
        action = action.toLowerCase();
        forward = action.isEmpty() ? "/empty" : "/view";
        System.out.println(action);
        return forward;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=3, end_line=6)

    assert count == 1, metadata
    assert metadata["scope_validation"] == "PASS"
    assert metadata["post_transform_scope_validation"]["unresolved_variables"] == []
    assert "String forward = \"\";" in transformed
    assert "forward = extractedWork(" in transformed
    _assert_compiles(transformed)


def test_one_live_out_value_is_returned_correctly():
    source = '''public class Example {
    int process(int value) {
        int first = value + 1;
        int second = first * 2;
        int total = second + 3;
        System.out.println(total);
        return total;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=3, end_line=5)

    assert count == 1, metadata
    assert len(metadata["live_out_variables"]) == 1
    output = metadata["live_out_variables"][0]
    assert f"return {output};" in transformed
    _assert_compiles(transformed)


def test_two_live_out_values_are_rejected_without_broken_source():
    source = '''public class Example {
    int process(int value) {
        int first = value + 1;
        int second = value + 2;
        return first + second;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=3, end_line=4)

    assert transformed == source
    assert count == 0
    assert metadata["reason"] == "MULTIPLE_LIVE_OUT_VALUES"
    assert metadata["live_out_variables"] == ["first", "second"]
    assert metadata["final_decision"] == "REVIEW_REQUIRED"


def test_broad_multiple_live_out_hint_falls_back_to_smaller_safe_candidate():
    source = '''public class Example {
    void process() {
        String url = config("url");
        String user = config("user");
        String pass = config("pass");
        executeDatabase(url, user, pass);
        audit(url, user, pass);
    }
    String config(String key) { return key; }
    void executeDatabase(String url, String user, String pass) { }
    void audit(String url, String user, String pass) { }
}
'''
    transformed, count, metadata = _extract(source, start_line=3, end_line=5)

    assert count == 1, metadata
    assert metadata["live_out_variables"] == []
    assert set(metadata["live_in_variables"]) == {"url", "user", "pass"}
    assert any(
        item["rejection_reason"] == "MULTIPLE_LIVE_OUT_VALUES"
        for item in metadata["candidate_rejections"]
    )
    assert metadata["candidate_selected"]["candidate_responsibility"] == (
        "database execution"
    )
    assert "private void extractedWork(String pass, String url, String user)" in transformed
    _assert_compiles(transformed)


def test_caller_declarations_read_inside_candidate_are_live_ins_not_live_outs():
    source = '''public class Example {
    void process() {
        String url = config("url");
        String user = config("user");
        String pass = config("pass");
        executeDatabase(url, user, pass);
        audit(url, user, pass);
    }
    String config(String key) { return key; }
    void executeDatabase(String url, String user, String pass) { }
    void audit(String url, String user, String pass) { }
}
'''
    transformed, count, metadata = _extract(source, start_line=6, end_line=7)

    assert count == 1, metadata
    assert set(metadata["live_in_variables"]) == {"url", "user", "pass"}
    assert metadata["live_out_variables"] == []
    assert "String url = config" in transformed
    assert "String user = config" in transformed
    assert "String pass = config" in transformed
    _assert_compiles(transformed)


def test_assignment_inside_candidate_read_after_is_live_out():
    source = '''public class Example {
    int process(int value) {
        int total = 0;
        total = value + 1;
        total += value;
        total++;
        System.out.println(total);
        return total;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=4, end_line=6)

    assert count == 1, metadata
    assert "total" in metadata["live_out_variables"]
    _assert_compiles(transformed)


def test_external_local_modified_inside_and_read_after_is_input_and_output():
    source = '''public class Example {
    int process(int value) {
        int total = value;
        total += 1;
        total *= 2;
        ++total;
        System.out.println(total);
        return total;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=4, end_line=6)

    assert count == 1, metadata
    assert "total" in metadata["live_in_variables"]
    assert "total" in metadata["live_out_variables"]
    assert "total" in metadata["modified_external_variables"]
    assert "total = extractedWork(total)" in transformed
    _assert_compiles(transformed)


def test_complete_try_catch_finally_candidate_preserves_exception_boundary():
    source = '''public class Example {
    int process(String raw) {
        int value = 0;
        try {
            value = Integer.parseInt(raw);
        } catch (NumberFormatException error) {
            value = -1;
        } finally {
            System.out.println("parsed");
        }
        System.out.println(value);
        return value;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=4, end_line=10)

    assert count == 1, metadata
    assert "try_catch_finally" in metadata["control_flow_dependencies"]
    assert "try {" in transformed and "finally {" in transformed
    _assert_compiles(transformed)


def test_jdbc_style_parameter_binding_and_execution_is_a_meaningful_candidate():
    source = '''public class Example {
    static class Statement {
        void setInt(int index, int value) { }
        void setString(int index, String value) { }
        void executeUpdate() { }
    }
    void process(Statement stmt, int id, String name) {
        validate(name);
        stmt.setInt(1, id);
        stmt.setString(2, name);
        stmt.setString(3, name.trim());
        stmt.executeUpdate();
        audit();
    }
    void validate(String value) { }
    void audit() { }
}
'''
    transformed, count, metadata = _extract(source, start_line=9, end_line=12)

    assert count == 1, metadata
    assert metadata["candidate_selected"]["candidate_responsibility"] == (
        "database parameter binding and execution"
    )
    assert set(metadata["live_in_variables"]) == {"id", "name", "stmt"}
    assert metadata["live_out_variables"] == []
    assert "stmt.executeUpdate();" in transformed
    _assert_compiles(transformed)


def test_safe_statement_sequence_inside_try_is_discovered_and_extracted():
    source = '''public class Example {
    static class DbException extends Exception { }
    static class Statement {
        void setInt(int index, int value) throws DbException { }
        void setString(int index, String value) throws DbException { }
        void executeUpdate() throws DbException { }
    }
    void process(Statement stmt, int id, String name) {
        try {
            stmt.setInt(1, id);
            stmt.setString(2, name);
            stmt.setString(3, name.trim());
            stmt.executeUpdate();
        } catch (DbException failure) {
            log(failure);
        }
        audit();
    }
    void log(DbException failure) { }
    void audit() { }
}
'''
    transformed, count, metadata = _extract(source, start_line=10, end_line=13)

    assert count == 1, metadata
    selected = metadata["candidate_selected"]
    assert selected["scope_depth"] > 0
    assert selected["exception_dependency"] == "enclosing_try"
    assert selected["candidate_responsibility"] == (
        "database parameter binding and execution"
    )
    assert "try {\n            extractedWork(" in transformed
    assert "catch (DbException failure)" in transformed
    assert "throws DbException" in transformed
    _assert_compiles(transformed)


def test_genuinely_tiny_method_remains_review_required():
    source = '''public class Example {
    void process(int value) {
        System.out.println(value);
    }
}
'''
    transformed, count, metadata = _extract(source)

    assert transformed == source
    assert count == 0
    assert metadata["reason"] == "METHOD_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK"
    assert metadata["candidate_rejections"] == []


def test_parameter_object_dependency_is_applied_before_extract_method_retry():
    source = '''public class Example {
    void process(int a, int b, int c, int d, int e, int f, int g) {
        validate(a, b, c, d, e, f, g);
        executeDatabase(a, b, c, d, e, f, g);
        audit(a, b, c, d, e, f, g);
        finish();
    }
    void validate(int a, int b, int c, int d, int e, int f, int g) { }
    void executeDatabase(int a, int b, int c, int d, int e, int f, int g) { }
    void audit(int a, int b, int c, int d, int e, int f, int g) { }
    void finish() { }
}
'''
    actions = [
        RefactoringAction(
            action_type="extract_method",
            parameters={
                "source_file": "Example.java",
                "source_class": "Example",
                "method": "process",
                "new_method_name": "extractedWork",
                "start_line": 3,
                "end_line": 5,
            },
        ),
        RefactoringAction(
            action_type="introduce_java_parameter_object",
            parameters={
                "source_file": "Example.java",
                "source_class": "Example",
                "method": "process",
                "parameter_object_name": "ProcessParams",
            },
        ),
    ]

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Example.java",
    )

    extract_log = next(item for item in logs if item.action_type == "extract_method")
    parameter_log = next(
        item
        for item in logs
        if item.action_type == "introduce_java_parameter_object"
    )
    assert parameter_log.action_index == 2
    assert extract_log.action_index == 1
    assert parameter_log.replacements_count == 1
    assert extract_log.replacements_count == 1
    assert extract_log.metadata["dependency_resolution"]["initial_status"] == (
        "DEFERRED_DEPENDENCY"
    )
    assert extract_log.metadata["dependency_resolution"]["retry_status"] == (
        "PROVEN_SAFE"
    )
    assert extract_log.metadata["candidate_selected"]["parameter_count"] == 1
    assert "static class ProcessParams" in transformed
    assert "private void extractedWork(ProcessParams params)" in transformed
    _assert_compiles(transformed)


@pytest.mark.parametrize(
    "statement",
    [
        "if (value < 0) return 0;",
        "while (value > 0) { value--; break; }",
        "while (value > 0) { value--; continue; }",
    ],
)
def test_cross_boundary_return_break_and_continue_are_not_extracted(statement: str):
    source = f'''public class Example {{
    int process(int value) {{
        int observed = value + 1;
        {statement}
        observed += value;
        return observed;
    }}
}}
'''
    transformed, count, metadata = _extract(source, start_line=3, end_line=5)

    assert transformed == source
    assert count == 0
    assert metadata["final_decision"] == "REVIEW_REQUIRED"


def test_local_shadowing_of_field_and_explicit_this_field_remain_distinct():
    source = '''public class Example {
    int total;
    int process(int value) {
        int total = value;
        total += 1;
        this.total += 2;
        total += 3;
        System.out.println(total);
        return total + this.total;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=5, end_line=7)

    assert count == 1, metadata
    assert metadata["scope_validation"] == "PASS"
    assert "this.total += 2" in transformed
    _assert_compiles(transformed)


def test_object_field_and_array_element_writes_keep_reference_inputs_without_live_outs():
    source = '''public class Example {
    static class Box { int value; }
    void process(Box box, int[] values, int index) {
        box.value = values[index];
        values[index] += 1;
        box.value += values[index];
        System.out.println(box.value);
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=4, end_line=6)

    assert count == 1, metadata
    assert set(metadata["live_in_variables"]) == {"box", "index", "values"}
    assert metadata["live_out_variables"] == []
    _assert_compiles(transformed)


def test_enhanced_for_loop_local_stays_scoped_inside_helper():
    source = '''public class Example {
    int process(int[] values) {
        int total = 0;
        for (int item : values) {
            total += item;
        }
        System.out.println(total);
        return total;
    }
}
'''
    transformed, count, metadata = _extract(source, start_line=4, end_line=6)

    assert count == 1, metadata
    assert "values" in metadata["live_in_variables"]
    assert "total" in metadata["live_in_variables"]
    assert "total" in metadata["live_out_variables"]
    assert "item" not in metadata["live_in_variables"]
    _assert_compiles(transformed)


def test_post_transform_scope_validator_rejects_unresolved_original_local():
    original = '''public class Example {
    int process(int value) {
        int total = value + 1;
        System.out.println(total);
        return total;
    }
}
'''
    broken = '''public class Example {
    int process(int value) {
        extractedWork(value);
        System.out.println(total);
        return total;
    }
    private void extractedWork(int value) { int total = value + 1; }
}
'''
    result = validate_java_extract_method_result(
        original,
        broken,
        source_class="Example",
        source_method="process",
        extracted_method="extractedWork",
    )

    assert result["passed"] is False
    scope = result["post_transform_scope_validation"]
    assert scope["status"] == "FAIL"
    assert scope["unresolved_variables"] == ["total"]


@pytest.mark.skipif(shutil.which("javac") is None, reason="javac unavailable")
def test_repository_compile_distinguishes_local_error_from_missing_dependency():
    original = '''import missing.Dependency;
public class Example {
    int process(int value) {
        int total = value + 1;
        return total;
    }
}
'''
    broken = '''import missing.Dependency;
public class Example {
    int process(int value) {
        return total;
    }
    private int extractedWork(int value) { return value + 1; }
}
'''
    result = _validate_java_compilation(
        original_code=original,
        transformed_code=broken,
        current_file_name="Example.java",
        project_source_files=[
            {"file_name": "Example.java", "source_code": original, "language": "java"},
            {
                "file_name": "Caller.java",
                "source_code": "class Caller { int call(Example e) { return e.process(1); } }",
                "language": "java",
            },
        ],
        original_local_names={"value", "total"},
        extracted_method="extractedWork",
        timeout_seconds=10,
    )

    assert result["status"] == "LOCAL_SOURCE_COMPILATION_ERROR"
    assert result["unresolved_variables"] == ["total"]
