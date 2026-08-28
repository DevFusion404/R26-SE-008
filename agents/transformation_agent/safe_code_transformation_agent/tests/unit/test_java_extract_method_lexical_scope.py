from __future__ import annotations

import pytest

from sctva.transformers.java_extract_method import (
    apply_extract_method,
    validate_java_extract_method_result,
)


def _scope_result(original_body: str, transformed_body: str) -> dict:
    original = f'''class Example {{
    void process() {{
{original_body}
    }}
}}
'''
    transformed = f'''class Example {{
    void process() {{
{transformed_body}
    }}
    private void extractedWork() {{ }}
}}
'''
    return validate_java_extract_method_result(
        original,
        transformed,
        source_class="Example",
        source_method="process",
        extracted_method="extractedWork",
    )


def _resolved_check(result: dict, variable: str, kind: str) -> dict:
    checks = result["post_transform_scope_validation"]["checked_identifiers"]
    return next(
        item
        for item in checks
        if item["variable"] == variable
        and item["resolved"] is True
        and item["declaration_kind"] == kind
    )


def test_catch_parameter_resolves_inside_its_catch_block():
    body = '''        extractedWork();
        try {
            run();
        } catch (Exception e) {
            e.printStackTrace();
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    check = _resolved_check(result, "e", "catch_parameter")
    assert check["scope"] == "catch_block"


def test_typed_catch_parameter_used_as_argument_resolves():
    body = '''        extractedWork();
        try {
            run();
        } catch (java.sql.SQLException ex) {
            log(ex);
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    _resolved_check(result, "ex", "catch_parameter")


def test_catch_parameter_reference_outside_catch_fails():
    original_body = '''        try {
            run();
        } catch (Exception e) {
            e.printStackTrace();
        }'''
    transformed_body = '''        extractedWork();
        try {
            run();
        } catch (Exception e) {
        }
        e.printStackTrace();'''
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is False
    scope = result["post_transform_scope_validation"]
    assert scope["unresolved_variables"] == ["e"]
    unresolved = next(item for item in scope["checked_identifiers"] if not item["resolved"])
    assert unresolved["reason"] == "OUTSIDE_DECLARATION_SCOPE"


def test_nested_catch_parameters_resolve_only_in_their_own_catches():
    body = '''        extractedWork();
        try {
            run();
        } catch (Exception outer) {
            log(outer);
            try {
                run();
            } catch (RuntimeException inner) {
                log(inner);
                log(outer);
            }
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    _resolved_check(result, "outer", "catch_parameter")
    _resolved_check(result, "inner", "catch_parameter")


@pytest.mark.parametrize(
    ("original_body", "transformed_body", "variable", "kind", "scope"),
    [
        (
            '''        extractedWork();
        for (int i = 0; i < 3; i++) { use(i); }''',
            '''        extractedWork();
        for (int i = 0; i < 3; i++) { use(i); }''',
            "i",
            "for_initializer_variable",
            "for_loop",
        ),
        (
            '''        extractedWork();
        for (String item : items()) { use(item); }''',
            '''        extractedWork();
        for (String item : items()) { use(item); }''',
            "item",
            "enhanced_for_variable",
            "enhanced_for_body",
        ),
        (
            '''        extractedWork();
        try (Resource con = open()) { use(con); }''',
            '''        extractedWork();
        try (Resource con = open()) { use(con); }''',
            "con",
            "try_resource",
            "try_block",
        ),
        (
            '''        extractedWork();
        items().forEach(item -> use(item));''',
            '''        extractedWork();
        items().forEach(item -> use(item));''',
            "item",
            "lambda_parameter",
            "lambda_body",
        ),
    ],
)
def test_scoped_java_declaration_kinds_resolve_inside_lexical_scope(
    original_body: str,
    transformed_body: str,
    variable: str,
    kind: str,
    scope: str,
):
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is True, result
    check = _resolved_check(result, variable, kind)
    assert check["scope"] == scope


@pytest.mark.parametrize(
    ("declaration", "outside_reference", "variable"),
    [
        ("for (int i = 0; i < 1; i++) { use(i); }", "use(i);", "i"),
        ("for (String item : items()) { use(item); }", "use(item);", "item"),
        ("try (Resource con = open()) { use(con); }", "use(con);", "con"),
        ("items().forEach(item -> use(item));", "use(item);", "item"),
    ],
)
def test_scoped_declarations_do_not_escape_their_lexical_scope(
    declaration: str,
    outside_reference: str,
    variable: str,
):
    original_body = f"        {declaration}"
    transformed_body = f"        extractedWork();\n        {declaration}\n        {outside_reference}"
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is False
    assert variable in result["post_transform_scope_validation"]["unresolved_variables"]


def test_real_moved_local_out_of_caller_scope_still_fails():
    original_body = '''        String forward = "/view";
        use(forward);'''
    transformed_body = '''        extractedWork();
        use(forward);'''
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is False
    assert result["post_transform_scope_validation"]["unresolved_variables"] == [
        "forward"
    ]


def test_traditional_switch_cases_share_one_lexical_scope():
    body = '''        extractedWork();
        switch (action()) {
            case 1:
                int orderId = 1;
                use(orderId);
                break;
            case 2:
                orderId = 2;
                use(orderId);
                break;
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    check = _resolved_check(result, "orderId", "local_variable")
    assert check["scope"] == "switch_block"


def test_switch_variable_used_before_declaration_fails():
    original_body = '''        switch (action()) {
            case 1:
                int value = 1;
                use(value);
        }'''
    transformed_body = '''        extractedWork();
        switch (action()) {
            case 1:
                use(value);
            case 2:
                int value = 1;
        }'''
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is False
    unresolved = next(
        item
        for item in result["post_transform_scope_validation"]["checked_identifiers"]
        if item["variable"] == "value" and not item["resolved"]
    )
    assert unresolved["reason"] == "BEFORE_DECLARATION"


def test_explicit_case_block_does_not_leak_into_another_case():
    original_body = '''        switch (action()) {
            case 1: {
                int value = 1;
                use(value);
                break;
            }
        }'''
    transformed_body = '''        extractedWork();
        switch (action()) {
            case 1: {
                int value = 1;
                use(value);
                break;
            }
            case 2:
                use(value);
        }'''
    result = _scope_result(original_body, transformed_body)

    assert result["passed"] is False
    unresolved = next(
        item
        for item in result["post_transform_scope_validation"]["checked_identifiers"]
        if item["variable"] == "value" and not item["resolved"]
    )
    assert unresolved["reason"] == "OUTSIDE_DECLARATION_SCOPE"


def test_default_label_shares_traditional_switch_scope():
    body = '''        extractedWork();
        switch (action()) {
            case 1:
                int value = 1;
                break;
            default:
                value = 2;
                use(value);
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    check = _resolved_check(result, "value", "local_variable")
    assert check["scope"] == "switch_block"


def test_nested_switches_use_the_correct_enclosing_switch_scope():
    body = '''        extractedWork();
        switch (outerAction()) {
            case 1:
                int outerValue = 1;
                switch (innerAction()) {
                    case 1:
                        int innerValue = outerValue;
                        break;
                    default:
                        innerValue = 2;
                        use(innerValue);
                        use(outerValue);
                }
                use(outerValue);
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    outer = _resolved_check(result, "outerValue", "local_variable")
    inner = _resolved_check(result, "innerValue", "local_variable")
    assert outer["scope"] == "switch_block"
    assert inner["scope"] == "switch_block"


def test_catch_parameter_inside_switch_keeps_catch_scope():
    body = '''        extractedWork();
        switch (action()) {
            case 1:
                try {
                    run();
                } catch (Exception failure) {
                    failure.printStackTrace();
                }
                break;
        }'''
    result = _scope_result(body, body)

    assert result["passed"] is True, result
    check = _resolved_check(result, "failure", "catch_parameter")
    assert check["scope"] == "catch_block"


def test_extract_method_continues_after_valid_order_servlet_style_switch_scope():
    source = '''public class Example {
    void process(int action) {
        switch (action) {
            case 1:
                int orderId = 1;
                use(orderId);
                break;
            case 2:
                orderId = 2;
                use(orderId);
                break;
        }
        int total = 1;
        total += 2;
        System.out.println(total);
    }
    static void use(int value) { }
}
'''
    transformed, count, metadata = apply_extract_method(
        source,
        new_method_name="extractedWork",
        method_name="process",
        source_class="Example",
        start_line=13,
        end_line=15,
        current_file_name="Example.java",
    )

    assert count == 1, metadata
    assert metadata["post_transform_scope_validation"]["status"] == "PASS"
    order_id_check = _resolved_check(
        {"post_transform_scope_validation": metadata["post_transform_scope_validation"]},
        "orderId",
        "local_variable",
    )
    assert order_id_check["scope"] == "switch_block"
    assert metadata["validation"]["data_flow"] == "PASS"
    assert metadata["validation"]["structural"] == "PASS"
    assert metadata["plan_compliance"] == "PASS"
    assert "extractedWork();" in transformed


def test_extract_method_candidate_with_catch_parameter_continues_after_scope_check():
    source = '''public class Example {
    int process(String raw) {
        int value = 0;
        try {
            value = Integer.parseInt(raw);
        } catch (Exception e) {
            e.printStackTrace();
            value = -1;
        }
        System.out.println(value);
        return value;
    }
}
'''
    transformed, count, metadata = apply_extract_method(
        source,
        new_method_name="extractedWork",
        method_name="process",
        source_class="Example",
        start_line=4,
        end_line=9,
        current_file_name="Example.java",
    )

    assert count == 1, metadata
    assert metadata["post_transform_scope_validation"]["status"] == "PASS"
    _resolved_check(
        {"post_transform_scope_validation": metadata["post_transform_scope_validation"]},
        "e",
        "catch_parameter",
    )
    assert metadata["validation"]["data_flow"] == "PASS"
    assert metadata["validation"]["structural"] == "PASS"
    assert metadata["plan_compliance"] == "PASS"
    assert "catch (Exception e)" in transformed
