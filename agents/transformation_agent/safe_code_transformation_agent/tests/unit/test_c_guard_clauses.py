from sctva.contracts import RefactoringAction
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers.c_guard_clauses import (
    apply_replace_nested_conditional_with_guard_clauses,
)
from sctva.transformers.engine import TransformationEngine
from sctva.validators.syntax_validator import SyntaxValidator
from sctva.validators.structural_validator import StructuralValidator


def _apply(source: str, method: str = "process"):
    return apply_replace_nested_conditional_with_guard_clauses(
        source,
        method_name=method,
        source_file="Server.c",
    )


def test_c_guard_clauses_flattens_simple_void_function_tail():
    source = '''void process(struct User *user) {
    if (user != NULL) {
        if (user->active) {
            process_user(user);
        }
    }
}
'''

    transformed, replacements, metadata = _apply(source)

    assert replacements == 1
    assert "if (!(user != NULL)) return;" in transformed
    assert "if (!(user->active)) return;" in transformed
    assert "process_user(user);" in transformed
    assert metadata["status"] == "success"
    assert metadata["new_nesting_depth"] < metadata["original_nesting_depth"]


def test_c_guard_clauses_preserves_existing_value_return_failures():
    source = '''int process(struct User *user) {
    if (user != NULL) {
        if (user->active) {
            return 1;
        } else {
            return 0;
        }
    } else {
        return 0;
    }
}
'''

    transformed, replacements, metadata = _apply(source)

    assert replacements == 1
    assert transformed.count("return 0;") == 2
    assert "if (!(user != NULL)) return 0;" in transformed
    assert metadata["exit_strategy"] == "return 0"


def test_c_guard_clauses_uses_continue_at_tail_of_loop():
    source = '''void process(int *items, int count) {
    for (int index = 0; index < count; index++) {
        if (items[index] > 0) {
            if (items[index] < 10) {
                consume(items[index]);
            }
        }
    }
}
'''

    transformed, replacements, metadata = _apply(source)

    assert replacements == 1
    assert transformed.count("continue;") == 2
    assert metadata["exit_strategy"] == "continue"


def test_c_guard_clauses_preserves_existing_break_exit():
    source = '''void process(int *items, int count) {
    for (int index = 0; index < count; index++) {
        if (items[index] > 0) {
            if (items[index] < 10) {
                consume(items[index]);
            } else { break; }
        } else { break; }
    }
}
'''

    transformed, replacements, metadata = _apply(source)

    assert replacements == 1
    assert transformed.count("break;") == 2
    assert metadata["exit_strategy"] == "break"


def test_c_guard_clauses_rejects_scope_sensitive_and_side_effecting_conditions():
    scope_source = '''void process(int enabled, int active) {
    if (enabled) {
        int value = 1;
        if (active) { consume(value); }
    }
}
'''
    side_effect_source = '''void process(struct User *user) {
    if (next_user(user)) {
        if (user->active) { consume(user); }
    }
}
'''

    for source, reason in (
        (scope_source, "GUARD_CLAUSE_SCOPE_CHANGE_UNSAFE"),
        (side_effect_source, "GUARD_CLAUSE_SIDE_EFFECT_ORDER_UNSAFE"),
    ):
        transformed, replacements, metadata = _apply(source)
        assert transformed == source
        assert replacements == 0
        assert metadata["status"] == "review_required"
        assert metadata["reason"] == reason


def test_c_guard_clauses_rejects_goto_switch_and_complex_else_if():
    goto_source = '''void process(int a, int b) {
    if (a) {
        if (b) { goto done; }
    }
done:
    consume(a);
}
'''
    switch_source = '''void process(int code, int a, int b) {
    switch (code) {
    case 1:
        if (a) { if (b) { consume(code); } }
    default:
        consume(code);
    }
}
'''
    else_if_source = '''void process(int a, int b, int c) {
    if (a) {
        if (b) { consume(a); }
        else if (c) { recover(a); }
    }
}
'''

    for source in (goto_source, switch_source, else_if_source):
        transformed, replacements, metadata = _apply(source)
        assert transformed == source
        assert replacements == 0
        assert metadata["status"] == "review_required"


def test_c_guard_clauses_reports_already_simplified():
    source = '''void process(struct User *user) {
    if (user == NULL) return;
    consume(user);
}
'''

    transformed, replacements, metadata = _apply(source)

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "not_applicable"
    assert metadata["reason"] == "GUARD_CLAUSE_ALREADY_SIMPLIFIED"


def test_c_guard_clause_resolves_explicit_method_and_current_line_range():
    source = '''void unrelated(void) { consume(0); }

void ChattingProcess(int enabled, int active) {
    if (enabled) {
        if (active) {
            consume(1);
        }
    }
}
'''

    transformed, replacements, metadata = apply_replace_nested_conditional_with_guard_clauses(
        source,
        method_name="ChattingProcess",
        source_line=4,
        target_lines=[4, 8],
        source_file="Server.c",
    )

    assert replacements == 1
    assert "void unrelated" in transformed
    assert metadata["source_method"] == "ChattingProcess"
    assert metadata["source_method_resolved"] is True
    assert metadata["target_inside_method"] is True
    assert metadata["target_resolution"] == "explicit_source_method"


def test_c_guard_clause_resolves_enclosing_function_from_line_without_method():
    source = '''void first(void) { consume(0); }

void ChattingProcess(int enabled, int active) {
    if (enabled) {
        if (active) {
            consume(1);
        }
    }
}
'''

    transformed, replacements, metadata = apply_replace_nested_conditional_with_guard_clauses(
        source,
        source_line=4,
        source_file="Server.c",
    )

    assert replacements == 1
    assert "if (!(enabled)) return;" in transformed
    assert metadata["source_method"] == "ChattingProcess"
    assert metadata["target_resolution"] == "enclosing_function_from_line"


def test_c_guard_clause_falls_back_only_for_one_safe_candidate():
    source = '''void helper(void) { consume(0); }

void process(int enabled, int active) {
    if (enabled) {
        if (active) {
            consume(1);
        }
    }
}
'''

    transformed, replacements, metadata = apply_replace_nested_conditional_with_guard_clauses(
        source,
        source_file="Server.c",
    )

    assert replacements == 1
    assert metadata["source_method"] == "process"
    assert metadata["target_resolution"] == "unique_safe_nested_conditional_candidate"


def test_c_guard_clause_never_guesses_between_multiple_safe_candidates():
    source = '''void first(int enabled, int active) {
    if (enabled) { if (active) { consume(1); } }
}

void second(int enabled, int active) {
    if (enabled) { if (active) { consume(2); } }
}
'''

    transformed, replacements, metadata = apply_replace_nested_conditional_with_guard_clauses(
        source,
        source_file="Server.c",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "GUARD_CLAUSE_TARGET_AMBIGUOUS"


def test_c_guard_clause_no_nested_conditional_is_not_applicable():
    source = '''void ChattingProcess(int enabled) {
    if (enabled) { consume(1); }
}
'''

    transformed, replacements, metadata = _apply(source, "ChattingProcess")

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "not_applicable"
    assert metadata["reason"] == "GUARD_CLAUSE_NO_NESTED_CONDITIONAL"


def test_c_guard_clause_aliases_map_through_planner_and_engine():
    plan = {
        "plan_id": "c_guard_clause_plan",
        "steps": [{
            "refactoring": "Guard Clauses",
            "target": {"function": "process", "file": "Server.c", "lines": [2, 6]},
        }],
    }
    action = PlannerAdapter().normalize_plan(plan)["actions"][0]
    assert action["action_type"] == "replace_nested_conditional_with_guard_clauses"
    assert RefactoringAction(
        action_type="Replace Nested Conditional with Guard Clause"
    ).action_type == "replace_nested_conditional_with_guard_clauses"
    promoted_legacy = RefactoringAction.from_dict({
        "action_type": "noop",
        "source_refactoring": "Guard Clauses",
        "parameters": {"method": "process", "source_file": "Server.c"},
        "warnings": ["Unsupported refactoring 'Guard Clauses' mapped to noop."],
    })
    assert promoted_legacy.action_type == "replace_nested_conditional_with_guard_clauses"
    assert promoted_legacy.warnings == []

    raw_legacy = RefactoringAction.from_dict({
        "action_type": "noop",
        "source_refactoring": "Guard Clauses",
        "target": {"function": "ChattingProcess", "file": "Server.c", "lines": [90, 130]},
        "parameters": {},
    })
    assert raw_legacy.parameters["source_method"] == "ChattingProcess"
    assert raw_legacy.parameters["source_file"] == "Server.c"
    assert raw_legacy.parameters["source_line"] == 90
    assert raw_legacy.parameters["target_lines"] == [90, 130]

    source = '''void process(struct User *user) {
    if (user != NULL) { if (user->active) { consume(user); } }
}
'''
    transformed, logs, warnings = TransformationEngine().apply_actions(
        source_code=source,
        language="c",
        actions=[RefactoringAction.from_dict(action)],
        strict_mode=True,
        current_file_name="Server.c",
    )
    assert "if (!(user != NULL)) return;" in transformed
    assert logs[0].metadata["status"] == "success"
    assert any("Guard Clauses applied" in warning for warning in warnings)
    structural = StructuralValidator().validate(
        language="c",
        original_code=source,
        transformed_code=transformed,
        actions=[RefactoringAction(
            action_type="replace_nested_conditional_with_guard_clauses",
            parameters={
                "method": "process",
                "applied_transformation_metadata": logs[0].metadata,
            },
        )],
    )
    assert structural.passed is True
    assert structural.details["guard_clause_validation"][0]["checks"]["nesting_reduced"] is True


def test_c_guard_clause_planner_and_legacy_noop_preserve_target_metadata():
    plan = {
        "plan_id": "c_guard_target_plan",
        "steps": [{
            "step_id": "guard-17",
            "refactoring": "Replace Nested Conditional with Guard Clauses",
            "parameters": {"target_function": "ChattingProcess"},
            "target": {"file": "Server.c", "lines": [90, 130]},
        }],
    }
    action = PlannerAdapter().normalize_plan(plan)["actions"][0]
    params = action["parameters"]
    assert action["action_type"] == "replace_nested_conditional_with_guard_clauses"
    assert params["source_file"] == "Server.c"
    assert params["source_method"] == "ChattingProcess"
    assert params["target_function"] == "ChattingProcess"
    assert params["target_lines"] == [90, 130]
    assert params["source_step_id"] == "guard-17"

    legacy = RefactoringAction(
        action_type="noop",
        source_refactoring="Replace Nested Conditional with Guard Clauses",
        warnings=["Unsupported refactoring 'Replace Nested Conditional with Guard Clauses' mapped to noop."],
        parameters={
            "legacy_step": {
                "step_id": "guard-17",
                "parameters": {"target_function": "ChattingProcess"},
                "target": {"file": "Server.c", "lines": [90, 130]},
            },
        },
    )
    SafeCodeTransformationValidationAgent._promote_guard_clause_noops([legacy])
    assert legacy.action_type == "replace_nested_conditional_with_guard_clauses"
    assert legacy.parameters["source_method"] == "ChattingProcess"
    assert legacy.parameters["source_file"] == "Server.c"
    assert legacy.parameters["target_lines"] == [90, 130]
    assert legacy.warnings == []


def test_c_guard_clause_output_passes_c_syntax_validation():
    source = '''static void consume(int value) { (void)value; }

void process(int *value) {
    if (value != 0) {
        if (*value > 0) {
            consume(*value);
        }
    }
}
'''
    transformed, replacements, metadata = _apply(source)

    result = SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    )
    assert replacements == 1
    assert metadata["validation"]["control_flow"] == "PASS"
    assert result.passed is True
