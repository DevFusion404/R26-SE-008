import json
import re
from pathlib import Path

import sctva.model_assistance.codet5 as codet5_module
from sctva.contracts import ExecutionOptions, RefactoringAction, SCTVARequestContract
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.model_assistance.codet5 import (
    CodeT5CandidateTransformer,
    CodeT5Settings,
    CodeT5TargetContext,
)
from sctva.models import TransformationLogEntry
from sctva.transformers.engine import TransformationEngine, _model_candidate_intent_issue


def test_codet5_prompt_keeps_normalized_rdp_target_metadata():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompt,
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "src/Student.java",
            "source_method": "calculateMarks",
            "source_line": 20,
            "ignored_nested_payload": {"must_not": "leak"},
        },
    )

    prompt = assistant.build_prompt(
        language="java",
        source_code="class Student {}\n",
        action=action,
        current_file_name="src/Student.java",
    )

    assert "refactoring: Extract Method" in prompt
    assert '"source_method":"calculateMarks"' in prompt
    assert '"source_line":20' in prompt
    assert "ignored_nested_payload" not in prompt
    assert "class Student {}" in prompt


def test_model_generation_disables_grouping_even_for_legacy_settings():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        generator=lambda prompt: prompt,
    )

    assert assistant.grouped_generation_enabled is False


def test_target_prompts_include_language_specific_accuracy_contracts():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompt,
    )
    context = CodeT5TargetContext(
        start=0,
        end=12,
        source="target body\n",
        kind="function",
        name="target",
        start_line=1,
        end_line=1,
    )
    action = RefactoringAction(
        action_type="rename_symbol",
        source_refactoring="Rename Variable",
        parameters={"old_name": "value", "new_name": "result"},
    )

    prompts = {
        language: assistant.build_target_prompt(
            language=language,
            action=action,
            current_file_name=f"target.{extension}",
            context=context,
        )
        for language, extension in (("java", "java"), ("python", "py"), ("c", "c"))
    }

    assert "java-contract:" in prompts["java"]
    assert "annotations, modifiers, generic parameters" in prompts["java"]
    assert "python-contract:" in prompts["python"]
    assert "decorators, async state, signature" in prompts["python"]
    assert "c-contract:" in prompts["c"]
    assert "storage qualifiers, pointer and array declarators" in prompts["c"]


def test_java_model_only_refactorings_resolve_targets_and_keep_semantic_parameters():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompt,
    )
    source = (
        "class Destination { int existing() { return 1; } }\n"
        "class Source extends Destination {\n"
        "    int value;\n"
        "    int work(int discount) { int total = value + discount; return total; }\n"
        "}\n"
    )
    cases = [
        ("extract_subclass", {"source_class": "Source", "new_subclass_name": "SourceSubtype"}, "class"),
        ("introduce_facade", {"source_class": "Source", "facade_class_name": "SourceFacade"}, "class"),
        ("replace_temp_with_query", {"source_class": "Source", "method": "work"}, "method"),
        ("move_java_method", {"source_class": "Source", "destination_class": "Destination", "method": "work"}, "class"),
        ("pull_up_method", {"source_class": "Source", "target_class": "Destination", "method": "work"}, "class"),
        ("replace_data_value_with_object", {"source_class": "Source", "method": "work"}, "class"),
        ("inline_java_class", {"class_to_inline": "Source"}, "class"),
        ("collapse_hierarchy", {"source_class": "Source", "parent_class": "Destination"}, "class"),
        ("replace_parameter_with_method_call", {"source_class": "Source", "method": "work"}, "method"),
    ]

    for action_type, parameters, expected_kind in cases:
        action = RefactoringAction(
            action_type=action_type,
            source_refactoring=action_type.replace("_", " ").title(),
            parameters={"source_file": "Source.java", **parameters},
        )
        context = assistant.resolve_target_context(
            language="java",
            source_code=source,
            action=action,
        )
        assert context is not None, action_type
        assert context.kind == expected_kind, action_type
        if action_type in {"move_java_method", "pull_up_method", "collapse_hierarchy"}:
            assert context.name == "Source,Destination", action_type
        prompt = assistant.build_target_prompt(
            language="java",
            action=action,
            current_file_name="Source.java",
            context=context,
        )
        assert "java-action-requirement:" in prompt
        assert "unchanged target does not satisfy the action" in prompt


def test_java_class_target_prompt_requests_complete_class_replacement():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompt,
    )
    action = RefactoringAction(
        action_type="extract_subclass",
        source_refactoring="Extract Subclass",
        parameters={
            "source_class": "Employee",
            "new_subclass_name": "EmployeeSubtype",
        },
    )
    context = assistant.resolve_target_context(
        language="java",
        source_code="class Employee { int id() { return 1; } }\n",
        action=action,
    )

    assert context is not None
    prompt = assistant.build_target_prompt(
        language="java",
        action=action,
        current_file_name="Employee.java",
        context=context,
    )
    assert '"new_subclass_name":"EmployeeSubtype"' in prompt
    assert "return the complete resolved class declaration" in prompt


def test_java_semantic_model_actions_reject_unchanged_and_partial_candidates():
    original = (
        "class Employee {\n"
        "    int id() { return 1; }\n"
        "    int salary() { return 2; }\n"
        "}\n"
    )
    action = RefactoringAction(
        action_type="extract_subclass",
        source_refactoring="Extract Subclass",
        parameters={
            "source_class": "Employee",
            "new_subclass_name": "EmployeeSubtype",
        },
    )

    assert _model_candidate_intent_issue("java", original, original, action) == (
        "JAVA_MODEL_ACTION_DID_NOT_CHANGE_TARGET"
    )
    declaration_only = original + "class EmployeeSubtype extends Employee {}\n"
    assert _model_candidate_intent_issue(
        "java", original, declaration_only, action
    ) == "JAVA_EXTRACT_SUBCLASS_MEMBER_MIGRATION_MISSING"

    migrated = (
        "class Employee { int id() { return 1; } }\n"
        "class EmployeeSubtype extends Employee { int salary() { return 2; } }\n"
    )
    assert _model_candidate_intent_issue("java", original, migrated, action) == ""


def test_model_action_checkpoint_accepts_valid_constants_in_all_languages():
    agent = SafeCodeTransformationValidationAgent()
    cases = (
        (
            "java",
            "public class Value { int value() { return 5; } }\n",
            (
                "public class Value {\n"
                "    private static final int FIVE = 5;\n"
                "    int value() { return FIVE; }\n"
                "}\n"
            ),
            {"source_class": "Value", "literal_value": 5, "constant_name": "FIVE"},
            "Value.java",
        ),
        (
            "python",
            "def value():\n    return 5\n",
            "FIVE = 5\n\ndef value():\n    return FIVE\n",
            {"literal_value": 5, "constant_name": "FIVE"},
            "value.py",
        ),
        (
            "c",
            "int value(void) { return 5; }\n",
            "#define FIVE 5\nint value(void) { return FIVE; }\n",
            {"literal_value": 5, "constant_name": "FIVE"},
            "value.c",
        ),
    )

    for language, original, candidate, parameters, file_name in cases:
        action = RefactoringAction(
            action_type="introduce_constant",
            source_refactoring="Introduce Constant",
            parameters={"source_file": file_name, **parameters},
        )
        checkpoint = agent._validate_model_action_candidate(
            language=language,
            original_code=original,
            candidate_code=candidate,
            action=action,
            timeout_seconds=10,
            behavior_tests=[],
            enable_behavior_tests=True,
            strict_mode=True,
            project_source_files=[],
            current_file_name=file_name,
        )

        assert checkpoint["passed"] is True, (language, checkpoint["issues"])
        assert [item["name"] for item in checkpoint["validation"]] == [
            "syntax",
            "structural",
            "behavioral",
            "invariant",
        ]


def test_codet5_primary_candidate_is_recorded_without_loading_transformers():
    original = "int value(void) { return 1; }\n"
    candidate = "int value(void) { int base = 1; return base; }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, allow_unsupported_actions=True),
        generator=lambda _prompt: candidate,
    )
    action = RefactoringAction(
        action_type="noop",
        source_refactoring="A future RDP refactoring",
        parameters={"source_file": "value.c", "source_line": 1},
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=original,
        actions=[action],
        strict_mode=True,
        current_file_name="value.c",
        model_assistant=assistant,
    )

    assert transformed == candidate
    assert not warnings
    assert logs[0].replacements_count == 1
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["model_provider"] == "qwen_coder"
    assert logs[0].metadata["model_mode"] == "primary"
    assert logs[0].metadata["reclassified_action_type"] == "model_assisted_transformation"


def test_codet5_invalid_primary_candidate_falls_back_to_native_transformer():
    original = "int value(int input) { return input > 10; }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "int value(void) {\n",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "value.c",
            "literal_value": 10,
            "constant_name": "MAX_VALUE",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=original,
        actions=[action],
        strict_mode=True,
        current_file_name="value.c",
        model_assistant=assistant,
    )

    assert transformed != original
    assert "#define MAX_VALUE 10" in transformed
    assert "input > MAX_VALUE" in transformed
    assert logs[0].replacements_count == 1
    assert logs[0].metadata["model_assistance"]["mode"] == "primary"
    assert (
        logs[0].metadata["model_assistance"]["reason"]
        == "CODET5_CANDIDATE_SYNTAX_INVALID"
    )
    assert any("primary candidate rejected" in warning for warning in warnings)


def test_codet5_semantically_incomplete_c_constant_candidate_falls_back():
    original = "int value(int input) { return input > 10; }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "#define MAX_VALUE 10\n\n"
            "int value(int input) { return input > 10; }"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "value.c",
            "source_line": 1,
            "literal_value": 10,
            "constant_name": "MAX_VALUE",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=original,
        actions=[action],
        strict_mode=True,
        current_file_name="value.c",
        model_assistant=assistant,
    )

    assert "input > MAX_VALUE" in transformed
    assert logs[0].metadata.get("model_assisted") is not True
    assert logs[0].metadata["model_assistance"]["reason"] == (
        "CODET5_CANDIDATE_INTENT_NOT_SATISFIED"
    )
    assert logs[0].metadata["model_assistance"]["intent_issue"] == (
        "C_INTRODUCE_CONSTANT_USAGE_NOT_MIGRATED"
    )
    assert any("safety checkpoint" in warning for warning in warnings)


def test_codet5_c_constant_candidate_with_unrelated_operator_change_falls_back():
    original = "int value(int input) { return input > 10; }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "#define MAX_VALUE 10\n\n"
            "int value(int input) { return input >= MAX_VALUE; }\n"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "value.c",
            "source_line": 1,
            "literal_value": 10,
            "constant_name": "MAX_VALUE",
        },
    )

    transformed, logs, _warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=original,
        actions=[action],
        strict_mode=True,
        current_file_name="value.c",
        model_assistant=assistant,
    )

    assert "input > MAX_VALUE" in transformed
    assert "input >= MAX_VALUE" not in transformed
    assert logs[0].metadata.get("model_assisted") is not True
    assert logs[0].metadata["model_assistance"]["intent_issue"] == (
        "C_INTRODUCE_CONSTANT_UNRELATED_SOURCE_CHANGE"
    )


def test_codet5_execution_options_are_disabled_by_default(monkeypatch):
    options = ExecutionOptions.from_dict({})
    assert options.enable_codet5 is False
    assert options.codet5_model_id == "Qwen/Qwen2.5-Coder-0.5B-Instruct"
    assert options.codet5_backend == "auto"
    assert options.codet5_openvino_model_path == ""
    assert options.codet5_max_new_tokens == 256
    assert options.codet5_max_bundle_output_tokens == 192
    assert options.codet5_max_input_tokens == 512
    assert options.codet5_cpu_threads == 6
    assert options.codet5_max_actions_per_request == 100
    assert options.codet5_max_actions_per_file == 100
    assert options.codet5_enable_file_bundling is False
    assert options.codet5_enable_generic_bundling is False
    assert options.codet5_generation_timeout_seconds == 30
    assert options.codet5_max_generic_actions_per_bundle == 2
    assert options.codet5_max_generation_seconds_per_request == 0
    assert options.codet5_runtime_cache is True
    assert CodeT5CandidateTransformer.from_execution_options(options) is None

    enabled = ExecutionOptions.from_dict(
        {
            "enable_codet5": True,
            "codet5_device": "cpu",
            "codet5_max_new_tokens": 128,
            "codet5_max_bundle_output_tokens": 96,
            "codet5_max_input_tokens": 1024,
            "codet5_cpu_threads": 4,
            "codet5_max_actions_per_request": 2,
            "codet5_max_actions_per_file": 2,
            "codet5_generation_timeout_seconds": 15,
            "codet5_backend": "pytorch",
            "codet5_enable_generic_bundling": True,
            "codet5_max_generic_actions_per_bundle": 3,
            "codet5_max_generation_seconds_per_request": 45,
            "codet5_runtime_cache": False,
            "codet5_mode": "primary",
        }
    )
    assert enabled.enable_codet5 is True
    assert enabled.codet5_device == "cpu"
    assert enabled.codet5_max_new_tokens == 128
    assert enabled.codet5_max_bundle_output_tokens == 96
    assert enabled.codet5_cpu_threads == 4
    assert enabled.codet5_max_actions_per_request == 2
    assert enabled.codet5_max_actions_per_file == 2
    assert enabled.codet5_generation_timeout_seconds == 15
    assert enabled.codet5_backend == "pytorch"
    assert enabled.codet5_enable_generic_bundling is True
    assert enabled.codet5_max_generic_actions_per_bundle == 3
    assert enabled.codet5_max_generation_seconds_per_request == 45
    assert enabled.codet5_runtime_cache is False
    assert enabled.codet5_mode == "primary"
    assert enabled.codet5_allow_download is False

    monkeypatch.setenv("SCTVA_ENABLE_CODET5", "true")
    monkeypatch.setenv("SCTVA_CODET5_MODE", "primary")
    monkeypatch.setenv("SCTVA_CODET5_DEVICE", "cpu")
    environment_enabled = ExecutionOptions.from_dict({})
    assert environment_enabled.enable_codet5 is True
    assert environment_enabled.codet5_mode == "primary"
    assert environment_enabled.codet5_device == "cpu"


def test_codet5_action_budget_prevents_unbounded_model_inference():
    calls = []

    def generator(prompt):
        calls.append(prompt)
        return "int value(void) { return 2; }\n"

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=False,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=generator,
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={"source_file": "value.c", "literal_value": 1},
    )
    arguments = {
        "language": "c",
        "source_code": "int value(void) { return 1; }\n",
        "action": action,
        "current_file_name": "value.c",
    }

    first = assistant.propose(**arguments)
    second = assistant.propose(**arguments)
    exhausted = assistant.propose(**arguments)

    assert first.status == "candidate_generated"
    assert second.status == "candidate_generated"
    assert exhausted.reason == "CODET5_ACTION_BUDGET_EXHAUSTED"
    assert exhausted.metadata["attempts_used"] == 2
    assert len(calls) == 2


def test_model_budget_is_distributed_one_attempt_per_file():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=1,
        ),
        generator=lambda _prompt: "int value(void) { return 2; }\n",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"literal_value": 1, "constant_name": "VALUE"},
    )

    assert assistant.can_attempt("first.c") is True
    assistant.propose(
        language="c", source_code="int value(void) { return 1; }\n",
        action=action, current_file_name="first.c",
    )
    assert assistant.can_attempt("first.c") is False
    assert assistant.can_attempt("second.c") is True
    assistant.propose(
        language="c", source_code="int value(void) { return 1; }\n",
        action=action, current_file_name="second.c",
    )
    assert assistant.can_attempt("third.c") is False


def test_exhausted_budget_is_skipped_instead_of_reported_as_rejected_attempt():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=1,
            max_actions_per_file=1,
        ),
        generator=lambda _prompt: "int seed(void) { return 2; }\n",
    )
    seed_action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"literal_value": 1, "constant_name": "VALUE"},
    )
    assistant.propose(
        language="c", source_code="int seed(void) { return 1; }\n",
        action=seed_action, current_file_name="seed.c",
    )

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="c",
        source_code="int value(int input) { return input > 10; }\n",
        actions=[RefactoringAction(
            action_type="introduce_constant",
            parameters={"literal_value": 10, "constant_name": "MAX_VALUE"},
        )],
        strict_mode=True,
        current_file_name="value.c",
        model_assistant=assistant,
    )
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        logs,
        validation_score=1.0,
        rollback_occurred=False,
        transformation_applied=True,
        model_enabled=True,
    )

    assert "#define MAX_VALUE 10" in transformed
    assert "model_assistance" not in logs[0].metadata
    assert summary["model_attempted"] is False
    assert summary["model_status"] == "MODEL_ACTION_BUDGET_EXHAUSTED"


def test_instruction_model_provider_is_reported_separately_from_codet5():
    qwen = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    codet5 = CodeT5CandidateTransformer(CodeT5Settings(
        enabled=True,
        model_id="Salesforce/codet5p-220m",
    ))

    assert qwen.provider == "qwen_coder"
    assert qwen.display_name == "Qwen2.5-Coder"
    assert codet5.provider == "codet5_plus"


def test_codet5_large_python_file_uses_ast_target_span_and_preserves_other_code():
    filler = "\n".join(f"UNRELATED_{index} = {index}" for index in range(180))
    original = (
        "import math\n\n"
        "class Calculator:\n"
        "    def selected(self, value):\n"
        "        return value + 1\n\n"
        "    def untouched(self, value):\n"
        "        return value * 2\n\n"
        + filler
        + "\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=lambda prompt: prompts.append(prompt) or (
            "def selected(self, value):\n    result = value + 1\n    return result"
        ),
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "large.py",
            "source_class": "Calculator",
            "source_method": "selected",
        },
    )

    candidate = assistant.propose(
        language="python",
        source_code=original,
        action=action,
        current_file_name="large.py",
    )

    assert candidate.status == "candidate_generated"
    assert candidate.metadata["context_mode"] == "target_span"
    assert candidate.metadata["target_name"] == "Calculator.selected"
    assert "<sctva-target-task>" in prompts[0]
    assert "    def untouched(self, value):" in candidate.source_code
    assert filler in candidate.source_code
    assert "        result = value + 1" in candidate.source_code


def test_codet5_large_java_extract_method_uses_method_member_span_only():
    filler = "\n".join(f"// repository context line {index}" for index in range(220))
    original = (
        "package sample;\n\n"
        + filler
        + "\npublic class Calculator {\n"
        "    public int selected(int value) {\n"
        "        return value + 1;\n"
        "    }\n\n"
        "    public int untouched(int value) {\n"
        "        return value * 2;\n"
        "    }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=lambda prompt: prompts.append(prompt) or (
            "public int selected(int value) {\n"
            "    return increment(value);\n"
            "}\n\n"
            "private int increment(int value) {\n"
            "    return value + 1;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "Calculator.java",
            "source_class": "Calculator",
            "source_method": "selected",
        },
    )

    candidate = assistant.propose(
        language="java",
        source_code=original,
        action=action,
        current_file_name="Calculator.java",
    )

    assert candidate.status == "candidate_generated"
    assert candidate.metadata["context_mode"] == "target_span"
    assert candidate.metadata["target_kind"] == "method_with_sibling_helper"
    assert candidate.metadata["target_name"] == "Calculator.selected"
    assert "exactly two class members" in prompts[0]
    assert "public int untouched" not in prompts[0]
    assert filler in candidate.source_code
    assert "public int untouched(int value)" in candidate.source_code
    assert "private int increment(int value)" in candidate.source_code


def test_codet5_large_c_file_uses_symbol_model_target_span_only():
    filler = "\n".join(f"/* repository context line {index} */" for index in range(220))
    original = (
        "#include <stdio.h>\n"
        + filler
        + "\nint selected(int value) {\n"
        "    return value + 1;\n"
        "}\n\n"
        "int untouched(int value) {\n"
        "    return value * 2;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=lambda _prompt: (
            "int selected(int value) {\n"
            "    int result = value + 1;\n"
            "    return result;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={"source_file": "large.c", "source_method": "selected"},
    )

    candidate = assistant.propose(
        language="c",
        source_code=original,
        action=action,
        current_file_name="large.c",
    )

    assert candidate.status == "candidate_generated"
    assert candidate.metadata["context_mode"] == "target_span"
    assert candidate.metadata["target_name"] == "selected"
    assert filler in candidate.source_code
    assert "int untouched(int value)" in candidate.source_code
    assert "int result = value + 1;" in candidate.source_code


def test_codet5_java_context_uses_line_when_method_metadata_is_missing():
    source = (
        "public class Order {\n"
        "    int first() {\n"
        "        return 1;\n"
        "    }\n"
        "    int second() {\n"
        "        return 2;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_file": "Order.java", "source_line": 6},
    )

    context = assistant.resolve_target_context(
        language="java", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "method_with_sibling_constant"
    assert context.name == "Order.second"


def test_codet5_java_context_uses_unique_literal_anchor_without_line_or_method():
    source = (
        "public class Order {\n"
        "    int regular() { return 10; }\n"
        "    int discounted() { return 125; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_file": "Order.java", "literal_value": 125},
    )

    context = assistant.resolve_target_context(
        language="java", source_code=source, action=action
    )

    assert context is not None
    assert context.name == "Order.discounted"


def test_java_constant_context_follows_literal_moved_into_extracted_helper():
    source = (
        "public class ContactDb {\n"
        "    void validate() {\n"
        "        extractedValidate();\n"
        "    }\n"
        "\n"
        "    void extractedValidate() {\n"
        "        int mobileColumn = result.getInt(3);\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "ContactDb.java",
            "source_method": "validate",
            "source_line": 3,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    context = assistant.resolve_target_context(
        language="java",
        source_code=source,
        action=action,
    )

    assert context is not None
    assert context.kind == "statement_with_sibling_constant"
    assert context.name == "ContactDb.extractedValidate"
    assert context.source.strip() == "int mobileColumn = result.getInt(3);"


def test_codet5_java_introduce_constant_uses_exact_field_member_span():
    source = (
        "public class Credentials {\n"
        "    private String user = \"root\";\n"
        "    private String password = \"1234\";\n"
        "    int untouched() { return 7; }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompts.append(prompt)
        or "private String password = DEFAULT_PASSWORD;",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "Credentials.java",
            "source_line": 3,
            "literal_value": "1234",
            "constant_name": "DEFAULT_PASSWORD",
        },
    )

    candidate = assistant.propose(
        language="java",
        source_code=source,
        action=action,
        current_file_name="Credentials.java",
    )

    assert candidate.status == "candidate_generated"
    assert candidate.metadata["target_kind"] == "field_with_sibling_constant"
    assert candidate.metadata["target_name"] == "Credentials.password"
    assert "Return only the complete transformed target-source" in prompts[0]
    assert "SCTVA attaches required-declaration" in prompts[0]
    assert (
        'required-declaration: private static final String DEFAULT_PASSWORD = "1234";'
        in prompts[0]
    )
    assert candidate.metadata["adapter_revision"] == (
        "java_constant_moved_target_v22_20261019"
    )
    assert "private String user" not in prompts[0]
    assert "private String user = \"root\";" in candidate.source_code
    assert "int untouched() { return 7; }" in candidate.source_code
    assert "private static final String DEFAULT_PASSWORD" in candidate.source_code


def test_codet5_java_constant_recovers_field_when_prior_insert_shifts_rdp_line():
    source = (
        "public class Credentials {\n"
        "    private static final int PRIOR = 6;\n"
        "    private String user = \"root\";\n"
        "    private String password = \"1234\";\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Credentials.java",
            # This was the password line before PRIOR was inserted.
            "source_line": 3,
            "literal_value": "1234",
            "constant_name": "DEFAULT_PASSWORD",
        },
    )

    context = assistant.resolve_target_context(
        language="java", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "field_with_sibling_constant"
    assert context.name == "Credentials.password"
    assert 'password = "1234"' in context.source
    assert 'user = "root"' not in context.source


def test_java_introduce_constant_model_candidate_preserves_only_planned_change():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "    int untouched() { return 9; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final int LIMIT = 6;\n"
            "int calculate(int value) {\n"
            "    return value + LIMIT;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed
    assert "int untouched() { return 9; }" in transformed


def test_java_introduce_constant_projects_missing_model_declaration():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "    int untouched() { return 9; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "int calculate(int value) {\n"
            "    return value + LIMIT;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed
    projection = logs[0].metadata["model_assistance"]["contract_projection"]
    assert projection["reason"] == "missing_declaration_inserted"


def test_java_introduce_constant_projects_missing_string_field_declaration():
    source = (
        "public class Credentials {\n"
        "    private String user = \"root\";\n"
        "    private String password = \"1234\";\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "private String password = DEFAULT_PASSWORD;",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Credentials.java",
            "source_line": 3,
            "literal_value": "1234",
            "constant_name": "DEFAULT_PASSWORD",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Credentials.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert (
        'private static final String DEFAULT_PASSWORD = "1234";' in transformed
    )
    assert "private String password = DEFAULT_PASSWORD;" in transformed
    assert 'private String user = "root";' in transformed


def test_java_introduce_constant_projects_noncanonical_model_declaration():
    source = (
        "public class Limits {\n"
        "    int value = 6;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "public final int LIMIT = 6;\n"
            "int value = LIMIT;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "public final int LIMIT" not in transformed
    projection = logs[0].metadata["model_assistance"]["contract_projection"]
    assert projection["reason"] == "noncanonical_declaration_replaced"


def test_java_introduce_constant_corrects_model_type_and_value():
    source = (
        "public class Limits {\n"
        "    int value = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final double THRESHOLD_LIMIT_3 = 3.0;\n"
            "int value = THRESHOLD_LIMIT_3;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int THRESHOLD_LIMIT_3 = 3;" in transformed
    assert "double THRESHOLD_LIMIT_3" not in transformed
    projection = logs[0].metadata["model_assistance"]["contract_projection"]
    assert projection["reason"] == "value_or_type_corrected"


def test_java_introduce_constant_does_not_hide_unrelated_edit_when_correcting_type():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 3;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda _prompt: (
            "private static final double THRESHOLD_LIMIT_3 = 3.0;\n"
            "int calculate(int value) {\n"
            "    return value - THRESHOLD_LIMIT_3;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert logs[0].metadata.get("model_assisted") is not True
    assert "return value + THRESHOLD_LIMIT_3;" in transformed
    assert "return value - THRESHOLD_LIMIT_3;" not in transformed
    assert any("JAVA_INTRODUCE_CONSTANT" in warning for warning in warnings)


def test_java_introduce_constant_does_not_project_unrelated_model_edit():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda _prompt: (
            "int calculate(int value) {\n"
            "    return value - LIMIT;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert logs[0].metadata.get("model_assisted") is not True
    assert "return value + LIMIT;" in transformed
    assert "return value - LIMIT;" not in transformed
    assert "contract_projection" not in logs[0].metadata["model_assistance"]
    assert any(
        "JAVA_INTRODUCE_CONSTANT_UNRELATED_SOURCE_CHANGE" in warning
        for warning in warnings
    )


def test_java_introduce_constant_rejects_unrelated_model_edit():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "    int untouched() { return 9; }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int LIMIT = 6;\n"
            "int calculate(int value) {\n"
            "    return value - LIMIT;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert logs[0].metadata.get("model_assisted") is not True
    assert logs[0].metadata["model_assistance"]["reason"] == (
        "CODET5_CANDIDATE_INTENT_NOT_SATISFIED"
    )
    assert len(prompts) == 1
    assert logs[0].metadata["model_assistance"]["repair_skipped"]["reason"] == (
        "JAVA_CONSTANT_INTENT_FAILURE_USES_SAFE_RULE_FALLBACK"
    )
    assert "return value + LIMIT;" in transformed
    assert any("JAVA_INTRODUCE_CONSTANT_UNRELATED_SOURCE_CHANGE" in item for item in warnings)


def test_java_introduce_constant_repairs_missing_declaration_with_feedback():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "}\n"
    )
    prompts = []
    outputs = iter((
        "int calculate(int value) {\n"
        "    return value - OTHER_LIMIT;\n"
        "}",
        "int calculate(int value) {\n"
        "    return value + LIMIT;\n"
        "}",
    ))
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_actions_per_request=2, max_actions_per_file=2),
        generator=lambda prompt: prompts.append(prompt) or next(outputs),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert len(prompts) == 2
    assert "validator-feedback:" in prompts[1]
    assert "<required-repair-target>" in prompts[1]
    assert "private static final int LIMIT = 6;" in prompts[1]
    assert "return value + LIMIT;" in prompts[1]
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["model_assistance"]["generation_phase"] == (
        "validator_guided_repair"
    )
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed


def test_java_introduce_constant_aligns_reversible_model_alias_to_rdp_name():
    source = (
        "public class Limits {\n"
        "    int selected = 6;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final int SIX = 6;\n"
            "int selected = SIX;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    normalization = logs[0].metadata["model_assistance"][
        "target_identifier_normalization"
    ]
    assert normalization["model_identifier"] == "SIX"
    assert normalization["planned_identifier"] == "LIMIT"
    assert "private static final int LIMIT = 6;" in transformed
    assert "int selected = LIMIT;" in transformed
    assert "SIX" not in transformed


def test_java_introduce_constant_trims_extra_model_class_closing_brace():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final int LIMIT = 6;\n"
            "int calculate(int value) {\n"
            "    return value + LIMIT;\n"
            "}\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["model_assistance"]["target_output_repair"] == {
        "status": "repaired",
        "reason": "unmatched_generated_suffix_brace_removed",
        "removed_braces": 1,
    }
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed


def test_java_introduce_constant_collapses_duplicate_generated_declarations():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final int LIMIT = 6;\n"
            "private static final int LIMIT = 6;\n"
            "int calculate(int value) {\n"
            "    return value + LIMIT;\n"
            "}"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count("private static final int LIMIT = 6;") == 1
    assert "return value + LIMIT;" in transformed
    assert logs[0].metadata["model_assistance"]["target_contract_completion"][
        "reason"
    ] == "duplicate_constant_declarations_collapsed"


def legacy_multiple_java_constants_remain_model_eligible_after_line_shifts():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    outputs = iter((
        "private static final int SIX = 6;\nint first = SIX;",
        "private static final int FIVE = 5;\nint second = FIVE;",
    ))
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=False,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda _prompt: next(outputs),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                # Stale after the first constant declaration is inserted.
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert "private static final int SIX = 6;" in transformed
    assert "private static final int FIVE = 5;" in transformed
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert all(
        log.metadata["model_assistance"]["batch_mode"]
        == "independent_target_tensor_batch"
        for log in logs
    )
    assert all(log.metadata["model_assistance"]["batch_size"] == 2 for log in logs)
    assert all(log.metadata["model_assistance"]["batch_rebased"] is True for log in logs)


def legacy_compatible_java_constants_use_one_file_action_bundle_candidate():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_actions_per_bundle=5,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int SIX = 6;\n"
            "int first = SIX;\n"
            "private static final int FIVE = 5;\n"
            "int second = FIVE;"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert len(prompts) == 1
    assert "<sctva-file-action-bundle>" in prompts[0]
    assert "private static final int SIX = 6;" in transformed
    assert "private static final int FIVE = 5;" in transformed
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert logs[0].metadata["model_bundle_primary"] is True
    assert logs[1].metadata["model_bundle_member"] is True
    assert "model_candidate_code" in logs[0].metadata
    assert "model_candidate_code" not in logs[1].metadata
    assert assistant._attempt_count == 1


def legacy_dependency_aware_java_bundle_applies_two_local_actions_in_one_generation():
    source = (
        "public class Values {\n"
        "    int calculate() {\n"
        "        int first = 10;\n"
        "        return first + 20;\n"
        "    }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
            max_actions_per_bundle=5,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "public class Values {\n"
            "    int calculate() {\n"
            "        int first = 11;\n"
            "        return first + 21;\n"
            "    }\n"
            "}\n"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="replace_literal",
            parameters={
                "source_file": "Values.java",
                "source_method": "calculate",
                "old_literal": 10,
                "new_literal": 11,
            },
        ),
        RefactoringAction(
            action_type="replace_literal",
            parameters={
                "source_file": "Values.java",
                "source_method": "calculate",
                "old_literal": 20,
                "new_literal": 21,
            },
        ),
    ]
    checkpoint_calls = []

    def validate_bundle(before, candidate, bundle_actions):
        checkpoint_calls.append((before, candidate, bundle_actions))
        return {
            "passed": True,
            "issues": [],
            "validation": [{"name": "test_bundle_checkpoint", "passed": True}],
        }

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Values.java",
        model_assistant=assistant,
        model_bundle_candidate_validator=validate_bundle,
    )

    assert not warnings
    assert len(prompts) == 1
    assert "bundle-kind: bounded_local_bundle" in prompts[0]
    assert '"old_literal":10' in prompts[0]
    assert '"new_literal":21' in prompts[0]
    assert len(checkpoint_calls) == 1
    assert [action.action_type for action in checkpoint_calls[0][2]] == [
        "replace_literal",
        "replace_literal",
    ]
    assert "int first = 11;" in transformed
    assert "return first + 21;" in transformed
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert logs[0].metadata["model_assistance"]["bundle_mode"] == (
        "bounded_local_bundle"
    )
    assert logs[1].metadata["model_bundle_member"] is True
    assert assistant._attempt_count == 1


def test_generic_file_bundles_are_accuracy_first_opt_in():
    source = (
        "public class Values {\n"
        "    int calculate() { return 10 + 20; }\n"
        "}\n"
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "calculate",
                    "old_literal": 10,
                    "new_literal": 11,
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "calculate",
                    "old_literal": 20,
                    "new_literal": 21,
                },
            ),
        ),
    ]

    default_assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    enabled_assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, enable_generic_bundling=True)
    )

    default_bundles = default_assistant.plan_dependency_aware_bundles(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Values.java",
    )
    enabled_bundles = enabled_assistant.plan_dependency_aware_bundles(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Values.java",
    )

    assert default_bundles == []
    assert [[index for index, _action in bundle] for bundle in enabled_bundles] == [
        [1, 2]
    ]


def legacy_dependency_aware_bundle_rejects_a_missing_action_then_uses_rules():
    source = (
        "public class Values {\n"
        "    int calculate() {\n"
        "        int first = 10;\n"
        "        return first + 20;\n"
        "    }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        # The requested 10 -> 11 action is missing from both attempts.
        generator=lambda prompt: prompts.append(prompt) or (
            "public class Values {\n"
            "    int calculate() {\n"
            "        int first = 12;\n"
            "        return first + 21;\n"
            "    }\n"
            "}\n"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="replace_literal",
            parameters={
                "source_method": "calculate",
                "old_literal": 10,
                "new_literal": 11,
            },
        ),
        RefactoringAction(
            action_type="replace_literal",
            parameters={
                "source_method": "calculate",
                "old_literal": 20,
                "new_literal": 21,
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Values.java",
        model_assistant=assistant,
        model_bundle_candidate_validator=lambda _before, _candidate, _actions: {
            "passed": True,
            "issues": [],
            "validation": [],
        },
    )

    assert len(prompts) == 2
    assert "<validation-feedback>" in prompts[1]
    assert "int first = 11;" in transformed
    assert "return first + 21;" in transformed
    assert "int first = 12;" not in transformed
    assert all(log.metadata.get("model_assisted") is not True for log in logs)
    assert logs[0].metadata["model_assistance"]["reason"] == (
        "CODET5_BUNDLE_REPLACE_LITERAL_INTENT_NOT_SATISFIED"
    )
    assert any("file-action bundle rejected" in warning for warning in warnings)


def test_dependency_aware_planner_keeps_structural_action_as_bundle_barrier():
    source = (
        "public class Values {\n"
        "    int calculate() {\n"
        "        int first = 10;\n"
        "        return first + 20;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        generator=lambda _prompt: "unused",
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "calculate",
                    "old_literal": 10,
                    "new_literal": 11,
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="extract_method",
                parameters={
                    "source_method": "calculate",
                    "new_method_name": "calculatePart",
                },
            ),
        ),
        (
            3,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "calculate",
                    "old_literal": 20,
                    "new_literal": 21,
                },
            ),
        ),
    ]

    bundles = assistant.plan_dependency_aware_bundles(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Values.java",
    )

    assert bundles == []
    assert assistant._attempt_count == 0


def test_dependency_aware_bundle_rejects_edits_outside_resolved_targets():
    source = (
        "public class Values {\n"
        "    int marker = 0;\n"
        "    int first() { return 1; }\n"
        "    int second() { return 2; }\n"
        "}\n"
    )
    candidate = (
        "public class Values {\n"
        "    int marker = 9;\n"
        "    int first() { return 11; }\n"
        "    int second() { return 22; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, enable_generic_bundling=True),
        generator=lambda _prompt: candidate,
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "first",
                    "old_literal": 1,
                    "new_literal": 11,
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="replace_literal",
                parameters={
                    "source_method": "second",
                    "old_literal": 2,
                    "new_literal": 22,
                },
            ),
        ),
    ]

    issue = assistant.bundle_candidate_scope_issue(
        language="java",
        original_source=source,
        candidate_source=candidate,
        actions=actions,
    )
    bundles = assistant.plan_dependency_aware_bundles(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Values.java",
    )

    assert issue == "CODET5_BUNDLE_CHANGE_OUTSIDE_RESOLVED_TARGETS"
    assert bundles == []


def test_bundle_preflight_splits_a_token_oversized_cross_file_span():
    filler = "\n".join(
        f"    // distant source token {index}" for index in range(80)
    )
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        f"{filler}\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_input_tokens=160,
        ),
        generator=lambda _prompt: "unused",
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_file": "Limits.java",
                    "source_line": 2,
                    "literal_value": 6,
                    "constant_name": "SIX",
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_file": "Limits.java",
                    "source_line": 83,
                    "literal_value": 5,
                    "constant_name": "FIVE",
                },
            ),
        ),
    ]

    partitions = assistant.partition_bundle_actions(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Limits.java",
    )

    assert [len(partition) for partition in partitions] == [1, 1]
    assert assistant._attempt_count == 0


def test_bundle_preflight_splits_a_generation_expensive_span():
    filler = "\n".join(
        f"    // compact filler {index}" for index in range(45)
    )
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        f"{filler}\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_input_tokens=1024,
            max_new_tokens=256,
            max_bundle_output_tokens=192,
        ),
        generator=lambda _prompt: "unused",
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 2,
                    "literal_value": 6,
                    "constant_name": "SIX",
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 48,
                    "literal_value": 5,
                    "constant_name": "FIVE",
                },
            ),
        ),
    ]

    partitions = assistant.partition_bundle_actions(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Limits.java",
    )

    assert [len(partition) for partition in partitions] == [1, 1]
    assert assistant._attempt_count == 0


def test_oversized_bundle_preflight_does_not_consume_generation_budget():
    filler = "\n".join(
        f"    // distant source token {index}" for index in range(80)
    )
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        f"{filler}\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_input_tokens=160,
        ),
        generator=lambda _prompt: "unused",
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 2,
                    "literal_value": 6,
                    "constant_name": "SIX",
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 83,
                    "literal_value": 5,
                    "constant_name": "FIVE",
                },
            ),
        ),
    ]

    candidate = assistant.propose_bundle(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Limits.java",
    )

    assert candidate.reason == "CODET5_BUNDLE_CONTEXT_TOO_LARGE"
    assert assistant._attempt_count == 0
    assert assistant._file_attempt_counts == {}


def test_bundle_repair_drops_large_rejected_candidate_before_generation():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_input_tokens=512,
            max_new_tokens=256,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int SIX = 6;\n"
            "int first = SIX;\n"
            "private static final int FIVE = 5;\n"
            "int second = FIVE;"
        ),
    )
    actions = [
        (
            1,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 2,
                    "literal_value": 6,
                    "constant_name": "SIX",
                },
            ),
        ),
        (
            2,
            RefactoringAction(
                action_type="introduce_constant",
                parameters={
                    "source_line": 3,
                    "literal_value": 5,
                    "constant_name": "FIVE",
                },
            ),
        ),
    ]

    candidate = assistant.propose_bundle(
        language="java",
        source_code=source,
        actions=actions,
        current_file_name="Limits.java",
        validation_feedback="JAVA_BUNDLE_CONSTANT_USAGE_NOT_MIGRATED:FIVE",
        previous_candidate="int unrelated = 99;\n" * 1000,
    )

    assert candidate.status == "candidate_generated"
    assert candidate.metadata["repair_prompt_compacted"] is True
    assert len(prompts) == 1
    assert "<rejected-target-replacement>" not in prompts[0]
    assert "<validation-feedback>" in prompts[0]


def legacy_unrelated_bundle_change_decomposes_to_single_model_actions():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if "<sctva-file-action-bundle>" in prompt:
            return (
                "private static final int SIX = 6;\n"
                "int first = SIX + 0;\n"
                "private static final int FIVE = 5;\n"
                "int second = FIVE;"
            )
        if "SIX" in prompt:
            return "private static final int SIX = 6;\nint first = SIX;"
        return "private static final int FIVE = 5;\nint second = FIVE;"

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, enable_file_bundling=True),
        generator=generate,
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert len(prompts) == 3
    assert "int first = SIX + 0;" not in transformed
    assert "private static final int SIX = 6;" in transformed
    assert "private static final int FIVE = 5;" in transformed
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert logs[0].metadata["model_bundle_decomposed_to_single_actions"] is True
    assert any("retrying 2 action(s) individually" in warning for warning in warnings)


def legacy_complete_java_file_bundle_output_is_validated_as_a_full_candidate():
    source = (
        "package demo;\n"
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        generator=lambda _prompt: (
            "package demo;\n"
            "public class Limits {\n"
            "    private static final int SIX = 6;\n"
            "    private static final int FIVE = 5;\n"
            "    int first = SIX;\n"
            "    int second = FIVE;\n"
            "}\n"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 4,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert "package demo;" in transformed
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["model_assistance"]["context_mode"] == (
        "full_file_bundle_output"
    )
    assert assistant._attempt_count == 1


def legacy_rejected_java_bundle_decomposes_before_rule_fallback():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_actions_per_bundle=5,
        ),
        # Both candidates omit FIVE, so collective intent must reject them.
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int SIX = 6;\n"
            "int first = SIX;\n"
            "int second = 5;"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    # The rejected FIVE candidate receives one bounded validator-guided retry
    # before the deterministic fallback applies it.
    assert len(prompts) == 4
    assert assistant._attempt_count == 4
    assert "private static final int SIX = 6;" in transformed
    assert "private static final int FIVE = 5;" in transformed
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert logs[0].metadata.get("model_assisted") is not True
    assert logs[0].metadata["model_bundle_decomposed_to_single_actions"] is True
    assert logs[0].metadata["model_bundle_attempt"].get(
        "bundle_repair_attempted"
    ) is not True
    assert logs[1].metadata.get("model_assisted") is not True
    assert any("retrying 2 action(s) individually" in warning for warning in warnings)


def legacy_invalid_bundle_constant_declaration_retries_single_model_actions():
    source = (
        "public class Limits {\n"
        "    int first = 9;\n"
        "    int second = 5;\n"
        "}\n"
    )
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if "<sctva-file-action-bundle>" in prompt:
            return (
                "private static final double THRESHOLD_LIMIT_9 = 9.0;\n"
                "int first = THRESHOLD_LIMIT_9;\n"
                "private static final int FIVE = 5;\n"
                "int second = FIVE;"
            )
        if "THRESHOLD_LIMIT_9" in prompt:
            return (
                "private static final int THRESHOLD_LIMIT_9 = 9;\n"
                "int first = THRESHOLD_LIMIT_9;"
            )
        return "private static final int FIVE = 5;\nint second = FIVE;"

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, enable_file_bundling=True),
        generator=generate,
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 9,
                "constant_name": "THRESHOLD_LIMIT_9",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert len(prompts) == 3
    assert "private static final int THRESHOLD_LIMIT_9 = 9;" in transformed
    assert "double THRESHOLD_LIMIT_9" not in transformed
    assert "int first = THRESHOLD_LIMIT_9;" in transformed
    assert "private static final int FIVE = 5;" in transformed
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert logs[0].metadata["model_bundle_decomposed_to_single_actions"] is True
    assert any("retrying 2 action(s) individually" in warning for warning in warnings)


def legacy_compatible_c_constants_use_one_file_action_bundle_candidate():
    source = (
        "int values[50];\n"
        "int retries = 3;\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_actions_per_bundle=5,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "#define VALUES_SIZE 50\n"
            "int values[VALUES_SIZE];\n"
            "#define RETRY_LIMIT 3\n"
            "int retries = RETRY_LIMIT;"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "limits.c",
                "source_line": 1,
                "literal_value": 50,
                "constant_name": "VALUES_SIZE",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "limits.c",
                "source_line": 2,
                "literal_value": 3,
                "constant_name": "RETRY_LIMIT",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="limits.c",
        model_assistant=assistant,
    )

    assert not warnings
    assert len(prompts) == 1
    assert "#define VALUES_SIZE 50" in transformed
    assert "#define RETRY_LIMIT 3" in transformed
    assert "int values[VALUES_SIZE];" in transformed
    assert "int retries = RETRY_LIMIT;" in transformed
    assert all(log.metadata["model_assisted"] is True for log in logs)
    assert assistant._attempt_count == 1


def legacy_agent_reports_one_candidate_for_two_accepted_bundle_actions(monkeypatch):
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        generator=lambda _prompt: (
            "private static final int SIX = 6;\n"
            "int first = SIX;\n"
            "private static final int FIVE = 5;\n"
            "int second = FIVE;"
        ),
    )
    monkeypatch.setattr(
        CodeT5CandidateTransformer,
        "from_execution_options",
        classmethod(lambda _cls, _options: assistant),
    )

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "accepted-java-file-action-bundle",
        "language": "java",
        "source_files": [{
            "file_name": "Limits.java",
            "language": "java",
            "source_code": source,
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "accepted-java-file-action-bundle",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "Limits.java",
                        "source_line": 2,
                        "literal_value": 6,
                        "constant_name": "SIX",
                    },
                },
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "Limits.java",
                        "source_line": 3,
                        "literal_value": 5,
                        "constant_name": "FIVE",
                    },
                },
            ],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
            "enable_codet5": True,
        },
    })

    assert result["success"] is True, result
    assert result["accepted_output_engine"] == "QWEN_CODER"
    assert result["model_attempt_count"] == 1
    assert result["model_candidate_count"] == 1
    assert result["model_action_count"] == 2
    assert result["model_backend"] == "injected"
    assert result["model_device"] == "injected_generator"
    assert result["model_runtime_cache_hit"] is False


def legacy_agent_accepts_one_dependency_aware_candidate_for_two_local_actions(
    monkeypatch,
):
    source = (
        "public class Values {\n"
        "    int calculate() {\n"
        "        int first = 10;\n"
        "        return first + 20;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            enable_generic_bundling=True,
        ),
        generator=lambda _prompt: (
            "public class Values {\n"
            "    int calculate() {\n"
            "        int first = 11;\n"
            "        return first + 21;\n"
            "    }\n"
            "}\n"
        ),
    )
    monkeypatch.setattr(
        CodeT5CandidateTransformer,
        "from_execution_options",
        classmethod(lambda _cls, _options: assistant),
    )

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "accepted-dependency-aware-bundle",
        "language": "java",
        "source_files": [{
            "file_name": "Values.java",
            "language": "java",
            "source_code": source,
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "accepted-dependency-aware-bundle",
            "actions": [
                {
                    "action_type": "replace_literal",
                    "parameters": {
                        "source_file": "Values.java",
                        "source_method": "calculate",
                        "old_literal": 10,
                        "new_literal": 11,
                    },
                },
                {
                    "action_type": "replace_literal",
                    "parameters": {
                        "source_file": "Values.java",
                        "source_method": "calculate",
                        "old_literal": 20,
                        "new_literal": 21,
                    },
                },
            ],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
            "enable_codet5": True,
        },
    })

    assert result["success"] is True, result
    assert result["accepted_output_engine"] == "QWEN_CODER"
    assert result["model_attempt_count"] == 1
    assert result["model_candidate_count"] == 1
    assert result["model_action_count"] == 2


def legacy_ten_compatible_actions_are_generated_as_two_bundles():
    source = "public class Limits {\n" + "".join(
        f"    int value{value} = {value};\n" for value in range(1, 11)
    ) + "}\n"
    prompts = []

    def generate_bundle(prompt):
        prompts.append(prompt)
        action_line = next(
            line for line in prompt.splitlines() if line.startswith("actions: ")
        )
        specs = json.loads(action_line.removeprefix("actions: "))
        target = re.search(
            r"<target-source>\n(.*?)\n</target-source>",
            prompt,
            re.DOTALL,
        ).group(1)
        declarations = []
        for spec in specs:
            declarations.append(spec["required_declaration"])
            target = re.sub(
                rf"(?<![A-Za-z0-9_.]){re.escape(spec['literal'])}(?![A-Za-z0-9_.])",
                spec["constant_name"],
                target,
                count=1,
            )
        return "\n".join([*declarations, target])

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            enable_file_bundling=True,
            max_actions_per_bundle=5,
        ),
        generator=generate_bundle,
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": value + 1,
                "literal_value": value,
                "constant_name": f"VALUE_{value}",
            },
        )
        for value in range(1, 11)
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert len(prompts) == 2
    assert assistant._attempt_count == 2
    assert all(log.metadata["model_assisted"] is True for log in logs)
    for value in range(1, 11):
        assert f"private static final int VALUE_{value} = {value};" in transformed
        assert f"int value{value} = VALUE_{value};" in transformed


def test_repeated_java_constant_uses_model_once_then_exact_rule_continuation():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 6;\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=4,
            max_actions_per_file=4,
        ),
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int SIX = 6;\nint first = SIX;"
        ),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert len(prompts) == 1
    assert transformed.count("private static final int SIX = 6;") == 1
    assert "int first = SIX;" in transformed
    assert "int second = SIX;" in transformed
    assert logs[0].metadata["model_assisted"] is True
    assert logs[1].metadata.get("model_assisted") is not True
    assert logs[1].metadata["model_continuation"]["reason"] == (
        "JAVA_CONSTANT_REUSES_ACCEPTED_MODEL_DECLARATION"
    )


def test_repeated_java_constant_can_retry_after_prior_model_rejection():
    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 6;\n"
        "}\n"
    )
    prompts = []
    outputs = iter((
        "int first = 7;",
        "int first = 7;",
        "private static final int SIX = 6;\nint second = SIX;",
    ))
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=4,
            max_actions_per_file=4,
        ),
        generator=lambda prompt: prompts.append(prompt) or next(outputs),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
    ]

    transformed, logs, _warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert len(prompts) == 3
    assert logs[0].metadata.get("model_assisted") is not True
    assert logs[1].metadata["model_assisted"] is True
    assert transformed.count("private static final int SIX = 6;") == 1
    assert "int first = SIX;" in transformed
    assert "int second = SIX;" in transformed


def legacy_java_constant_batch_uses_one_model_generate_call():
    class FakeTensor:
        def __init__(self, batch_size, width, value=0):
            self.shape = (batch_size, width)
            self.value = value

        def to(self, _device):
            return self

        def __getitem__(self, _item):
            return self

    class FakeTokenizer:
        pad_token_id = 0
        eos_token = "<eos>"
        padding_side = "right"

        def __call__(self, prompts, **_kwargs):
            batch_size = len(prompts) if isinstance(prompts, list) else 1
            return {
                "input_ids": FakeTensor(batch_size, 32),
                "attention_mask": FakeTensor(batch_size, 32),
            }

        @staticmethod
        def decode(tokens, skip_special_tokens=True):
            del skip_special_tokens
            return {
                101: "private static final int SIX = 6;\nint first = SIX;",
                102: "private static final int FIVE = 5;\nint second = FIVE;",
            }[tokens.value]

    class FakeModel:
        def __init__(self):
            self.generate_calls = 0

        def generate(self, **_kwargs):
            self.generate_calls += 1
            return [FakeTensor(1, 1, 101), FakeTensor(1, 1, 102)]

    class FakeInferenceMode:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class FakeTorch:
        @staticmethod
        def inference_mode():
            return FakeInferenceMode()

    source = (
        "public class Limits {\n"
        "    int first = 6;\n"
        "    int second = 5;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, enable_file_bundling=False)
    )
    assistant._tokenizer = FakeTokenizer()
    assistant._model = FakeModel()
    assistant._torch = FakeTorch()
    assistant._resolved_device = "cpu"
    assistant._is_encoder_decoder = True
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 2,
                "literal_value": 6,
                "constant_name": "SIX",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "Limits.java",
                "source_line": 3,
                "literal_value": 5,
                "constant_name": "FIVE",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert warnings == []
    assert assistant._model.generate_calls == 1
    assert "int first = SIX;" in transformed
    assert "int second = FIVE;" in transformed
    assert all(log.metadata["model_assistance"]["batch_size"] == 2 for log in logs)

def test_hint_only_java_constants_resolve_stable_methods_after_line_shifts():
    source = (
        "public class StudentDB {\n"
        "    void validate() { read(3); }\n"
        "    void details() { read(3); }\n"
        "}\n"
    )
    outputs = iter((
        "private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    void validate() { read(THRESHOLD_LIMIT_3); }",
        "private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    void details() { read(THRESHOLD_LIMIT_3); }",
    ))
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=4,
            max_actions_per_file=4,
        ),
        generator=lambda _prompt: next(outputs),
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "StudentDB.java",
                "source_line": 2,
                "hint": "Magic number 3 detected",
                "constant_name": "THRESHOLD_LIMIT_3",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "source_file": "StudentDB.java",
                "source_line": 3,
                "hint": "Magic number 3 detected",
                "constant_name": "THRESHOLD_LIMIT_3",
            },
        ),
    ]

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=actions,
        strict_mode=True,
        current_file_name="StudentDB.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert logs[1].metadata.get("model_assisted") is not True
    assert logs[1].metadata["model_continuation"]["reason"] == (
        "JAVA_CONSTANT_REUSES_ACCEPTED_MODEL_DECLARATION"
    )
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    assert "void validate() { read(THRESHOLD_LIMIT_3); }" in transformed
    assert "void details() { read(THRESHOLD_LIMIT_3); }" in transformed
    assert logs[0].metadata["model_action_preparation"]["reference_target"] == (
        "StudentDB.validate"
    )
    assert logs[1].metadata["model_action_preparation"]["reference_target"] == (
        "StudentDB.details"
    )


def test_hint_numeric_java_string_is_prepared_as_typed_string_contract():
    source = (
        "public class DBconnect {\n"
        "    private static String password = \"2210\";\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: (
            prompts.append(prompt)
            or 'private static final String CONSTANT_STRING_N_2210 = "2210";\n'
            'private static String password = CONSTANT_STRING_N_2210;'
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "DBconnect.java",
            "source_line": 2,
            "hint": "Magic number 2210 detected",
            "constant_name": "THRESHOLD_LIMIT_2210",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="DBconnect.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    preparation = logs[0].metadata["model_action_preparation"]
    assert preparation["resolved_literal"] == "2210"
    assert preparation["resolved_constant_name"] == "CONSTANT_STRING_N_2210"
    assert preparation["reference_target"] == "DBconnect.password"
    assert 'required-declaration: private static final String CONSTANT_STRING_N_2210 = "2210";' in prompts[0]
    assert 'private static final String CONSTANT_STRING_N_2210 = "2210";' in transformed
    assert "private static String password = CONSTANT_STRING_N_2210;" in transformed


def test_java_introduce_constant_reuses_existing_model_constant():
    source = (
        "public class Limits {\n"
        "    private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    int first = THRESHOLD_LIMIT_3;\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "int second = THRESHOLD_LIMIT_3;",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 4,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    assert "int first = THRESHOLD_LIMIT_3;" in transformed
    assert "int second = THRESHOLD_LIMIT_3;" in transformed


def test_java_model_preparation_disambiguates_existing_constant_with_other_value():
    source = (
        "public class Limits {\n"
        "    private static final int LIMIT = 5;\n"
        "    int selected = 6;\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: prompts.append(prompt) or (
            "private static final int LIMIT_2 = 6;\n"
            "int selected = LIMIT_2;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    preparation = logs[0].metadata["model_action_preparation"]
    assert preparation["constant_name_collision_resolved"] is True
    assert preparation["conflicting_constant_name"] == "LIMIT"
    assert preparation["resolved_constant_name"] == "LIMIT_2"
    assert "required-declaration: private static final int LIMIT_2 = 6;" in prompts[0]
    assert transformed.count("private static final int LIMIT = 5;") == 1
    assert transformed.count("private static final int LIMIT_2 = 6;") == 1
    assert "int selected = LIMIT_2;" in transformed


def test_java_introduce_constant_removes_duplicate_model_declaration():
    source = (
        "public class Limits {\n"
        "    private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    int first = THRESHOLD_LIMIT_3;\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final int THRESHOLD_LIMIT_3 = 3;\n"
            "int second = THRESHOLD_LIMIT_3;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 4,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    projection = logs[0].metadata["model_assistance"]["contract_projection"]
    assert projection["reason"] == "duplicate_declaration_removed"


def test_java_introduce_constant_collapses_exact_and_wrong_model_duplicate():
    source = (
        "public class Limits {\n"
        "    private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    int first = THRESHOLD_LIMIT_3;\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final double THRESHOLD_LIMIT_3 = 3.0;\n"
            "int second = THRESHOLD_LIMIT_3;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 4,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    assert "double THRESHOLD_LIMIT_3" not in transformed
    assert "int second = THRESHOLD_LIMIT_3;" in transformed
    projection = logs[0].metadata["model_assistance"]["contract_projection"]
    assert projection["reason"] == "value_or_type_corrected"


def test_java_introduce_constant_completes_declaration_only_model_output():
    source = (
        "public class Limits {\n"
        "    private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    int first = THRESHOLD_LIMIT_3;\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda _prompt: (
            "private static final int THRESHOLD_LIMIT_3 = 3;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 4,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "int second = THRESHOLD_LIMIT_3;" in transformed
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "missing_target_member_restored"
    assert completion["replacements"] == 1


def test_java_introduce_constant_completes_wrong_declaration_only_output():
    source = (
        "public class Limits {\n"
        "    private static final int THRESHOLD_LIMIT_3 = 3;\n"
        "    int first = THRESHOLD_LIMIT_3;\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda _prompt: (
            "private static final double THRESHOLD_LIMIT_3 = 3.0;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 4,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count(
        "private static final int THRESHOLD_LIMIT_3 = 3;"
    ) == 1
    assert "double THRESHOLD_LIMIT_3" not in transformed
    assert "int second = THRESHOLD_LIMIT_3;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "missing_target_member_restored"


def test_java_introduce_constant_completes_usage_only_model_output():
    source = (
        "public class Limits {\n"
        "    int second = 3;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "int second = THRESHOLD_LIMIT_3;",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 3,
            "constant_name": "THRESHOLD_LIMIT_3",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int THRESHOLD_LIMIT_3 = 3;" in transformed
    assert "int second = THRESHOLD_LIMIT_3;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "missing_constant_declaration_restored"


def test_java_introduce_constant_accepts_wrapped_commented_declaration():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) {\n"
        "        return value + 6;\n"
        "    }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private\n"
            "static final\n"
            "int LIMIT =\n"
            "6; // extracted from the planned literal\n"
            "return value + LIMIT;"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 3,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert transformed.count("private static final int LIMIT = 6;") == 1
    assert "return value + LIMIT;" in transformed
    assert "extracted from the planned literal" not in transformed


def test_java_introduce_constant_completes_truncated_declaration_only_output():
    source = (
        "public class Limits {\n"
        "    int selected = 6;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "private static final int LIMIT = 6",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "int selected = LIMIT;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "truncated_declaration_and_target_completed"


def test_java_introduce_constant_completes_declaration_prefix_only_output():
    source = (
        "public class Limits {\n"
        "    int selected = 6;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "private static final int LIMIT =",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "int selected = LIMIT;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "truncated_declaration_and_target_completed"


def test_codet5_normalises_copied_java_repair_output_markers():
    output = (
        "<required-repair-output>\n"
        "private static final int LIMIT = 6;\n"
        "int selected = LIMIT;\n"
        "</required-repair-output>"
    )

    assert CodeT5CandidateTransformer._normalise_model_output(output) == (
        "private static final int LIMIT = 6;\nint selected = LIMIT;"
    )


def test_java_introduce_constant_corrects_truncated_placeholder_declaration():
    source = (
        "public class Limits {\n"
        "    int selected = 2210;\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "private static final Object THRESHOLD_LIMIT_2210 = null"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_line": 2,
            "literal_value": 2210,
            "constant_name": "THRESHOLD_LIMIT_2210",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert (
        "private static final int THRESHOLD_LIMIT_2210 = 2210;" in transformed
    )
    assert "int selected = THRESHOLD_LIMIT_2210;" in transformed
    assert "Object THRESHOLD_LIMIT_2210" not in transformed


def test_codet5_normalises_unclosed_markdown_fence_to_empty_output():
    assert CodeT5CandidateTransformer._normalise_model_output("```") == ""
    assert CodeT5CandidateTransformer._normalise_model_output("```java") == ""


def test_elapsed_generation_time_does_not_skip_later_files():
    source = "class Values { int selected() { return 1; } }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=100,
            max_actions_per_file=100,
        ),
        generator=lambda _prompt: "int selected() { return 2; }",
    )
    assistant._generation_seconds_used = 10_000.0
    action = RefactoringAction(
        action_type="rename_symbol",
        parameters={
            "source_file": "Values.java",
            "source_method": "selected",
            "old_name": "selected",
            "new_name": "chosen",
        },
    )

    assert assistant.attempt_budget_status("Values.java")["available"] is True
    candidate = assistant.propose(
        language="java",
        source_code=source,
        action=action,
        current_file_name="Values.java",
    )

    assert candidate.status == "candidate_generated"
    assert candidate.reason == ""


def test_configured_generation_time_budget_uses_rule_fallback_before_inference():
    source = "class Values { int selected() { return 1; } }\n"
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_generation_seconds_per_request=45,
        ),
        generator=lambda _prompt: "must not run",
    )
    assistant._generation_seconds_used = 40.0
    assert assistant._bounded_generation_timeout(15) == 5.0
    assistant._generation_seconds_used = 45.0
    action = RefactoringAction(
        action_type="replace_literal",
        parameters={"old_literal": 1, "new_literal": 2, "source_line": 1},
    )

    status = assistant.attempt_budget_status("Values.java")
    candidate = assistant.propose(
        language="java",
        source_code=source,
        action=action,
        current_file_name="Values.java",
    )

    assert status["available"] is False
    assert status["reason"] == "CODET5_REQUEST_TIME_BUDGET_EXHAUSTED"
    assert candidate.reason == "CODET5_ACTION_BUDGET_EXHAUSTED"
    assert candidate.metadata["budget_reason"] == (
        "CODET5_REQUEST_TIME_BUDGET_EXHAUSTED"
    )


def test_explicit_openvino_backend_requires_an_exported_model_path():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            backend="openvino",
            openvino_model_path="missing-openvino-model-for-test",
        )
    )
    action = RefactoringAction(
        action_type="replace_literal",
        parameters={"old_literal": 1, "new_literal": 2, "source_line": 1},
    )

    candidate = assistant.propose(
        language="java",
        source_code="class Values { int value = 1; }\n",
        action=action,
        current_file_name="Values.java",
    )

    assert candidate.status == "skipped"
    assert "CODET5_OPENVINO_MODEL_PATH_NOT_FOUND" in candidate.reason


def test_openvino_local_windows_path_avoids_broken_hub_cache_precheck(
    monkeypatch,
):
    import optimum.intel
    import transformers

    model_path = Path(__file__).resolve().parent
    calls = {}

    class FakeConfig:
        is_encoder_decoder = False
        use_cache = False

    class FakeTokenizer:
        model_max_length = 512

    class FakeModel:
        config = FakeConfig()

    class FakeOVModelForCausalLM:
        @classmethod
        def from_pretrained(cls, source, **kwargs):
            calls["source"] = source
            calls["options"] = kwargs
            return FakeModel()

    monkeypatch.setattr(
        transformers.AutoConfig,
        "from_pretrained",
        lambda *_args, **_kwargs: FakeConfig(),
    )
    monkeypatch.setattr(
        transformers.AutoTokenizer,
        "from_pretrained",
        lambda *_args, **_kwargs: FakeTokenizer(),
    )
    monkeypatch.setattr(
        optimum.intel,
        "OVModelForCausalLM",
        FakeOVModelForCausalLM,
    )

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            backend="openvino",
            device="cpu",
            openvino_model_path=str(model_path),
            runtime_cache=False,
        )
    )
    assistant._load()

    assert calls["source"] == str(model_path.resolve())
    assert calls["options"]["local_files_only"] is False
    assert calls["options"]["export"] is False
    assert calls["options"]["ov_config"] == {
        "INFERENCE_PRECISION_HINT": "f32",
        "INFERENCE_NUM_THREADS": "6",
        "PERFORMANCE_HINT": "LATENCY",
    }
    assert assistant._resolved_backend == "openvino"


def test_process_runtime_cache_reuses_loaded_model_objects(monkeypatch):
    settings = CodeT5Settings(
        enabled=True,
        backend="pytorch",
        device="cpu",
        runtime_cache=True,
    )
    fake_model = object()
    fake_tokenizer = object()
    fake_torch = object()
    inference_lock = codet5_module.threading.Lock()
    cache_key = (
        "pytorch",
        settings.model_id,
        "cpu",
        False,
        512,
        6,
    )
    monkeypatch.setitem(
        codet5_module._MODEL_RUNTIME_CACHE,
        cache_key,
        {
            "tokenizer": fake_tokenizer,
            "model": fake_model,
            "torch": fake_torch,
            "device": "cpu",
            "backend": "pytorch",
            "is_encoder_decoder": False,
            "max_input_tokens": 512,
            "inference_lock": inference_lock,
        },
    )
    assistant = CodeT5CandidateTransformer(settings)

    assistant._load()

    assert assistant._model is fake_model
    assert assistant._tokenizer is fake_tokenizer
    assert assistant._runtime_inference_lock is inference_lock
    assert assistant._runtime_cache_hit is True


def test_model_run_skips_files_not_referenced_by_rdp_plan(monkeypatch):
    planned_source = (
        "public class Planned {\n"
        "    int value() { return 6; }\n"
        "}\n"
    )
    unplanned_source = (
        "public class Unplanned {\n"
        "    int value() { return 9; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=100,
            max_actions_per_file=100,
        ),
        generator=lambda _prompt: (
            "private static final int LIMIT = 6;\n"
            "int value() { return LIMIT; }"
        ),
    )
    monkeypatch.setattr(
        CodeT5CandidateTransformer,
        "from_execution_options",
        classmethod(lambda _cls, _options: assistant),
    )
    request = SCTVARequestContract.from_dict({
        "request_id": "rdp-plan-only-model-scope",
        "language": "java",
        "source_files": [
            {
                "file_name": "Planned.java",
                "language": "java",
                "source_code": planned_source,
            },
            {
                "file_name": "Unplanned.java",
                "language": "java",
                "source_code": unplanned_source,
            },
        ],
        "refactoring_plan": {
            "plan_id": "rdp-plan-only-model-scope",
            "actions": [{
                "action_type": "introduce_constant",
                "parameters": {
                    "source_file": "Planned.java",
                    "source_line": 2,
                    "literal_value": 6,
                    "constant_name": "LIMIT",
                },
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "enable_codet5": True,
            "enable_sctva_auto_refactoring": True,
            "enable_behavior_tests": False,
            "require_compilation": False,
        },
    })
    agent = SafeCodeTransformationValidationAgent()
    local_detection_files = []
    original_local_detector = agent._local_actions_for_file

    def recording_local_detector(*, request, file_entry, existing_actions):
        local_detection_files.append(file_entry.file_name)
        return original_local_detector(
            request=request,
            file_entry=file_entry,
            existing_actions=existing_actions,
        )

    monkeypatch.setattr(agent, "_local_actions_for_file", recording_local_detector)

    result = agent.execute_request(request)

    assert local_detection_files == ["Planned.java"]
    assert set(assistant._file_attempt_counts) == {"planned.java"}
    unplanned_result = next(
        item for item in result["file_results"]
        if item["file_name"] == "Unplanned.java"
    )
    assert unplanned_result["model_attempted"] is False
    assert unplanned_result["accepted_output_engine"] == "NO_CHANGE"
    assert unplanned_result["refactored_code"] == unplanned_source


def test_java_constant_generation_budget_covers_complete_target():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) { return value + 6; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_new_tokens=256,
            generation_timeout_seconds=90,
        )
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_method": "calculate",
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )
    context = assistant.resolve_target_context(
        language="java",
        source_code=source,
        action=action,
    )

    assert context is not None
    assert context.kind == "statement_with_sibling_constant"
    assert assistant._generation_limits("java", action, context) == (96, 90)


def test_java_constant_recovers_exact_target_from_complete_class_output():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) { return value + 6; }\n"
        "    int untouched() { return 9; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "public class Limits {\n"
            "    private static final int LIMIT = 6;\n"
            "    int calculate(int value) { return value + LIMIT; }\n"
            "    int untouched() { return 99; }\n"
            "}\n"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_method": "calculate",
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed
    assert "int untouched() { return 9; }" in transformed
    assert "return 99;" not in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "complete_source_projected_to_exact_target"


def test_java_constant_does_not_recover_changed_target_from_complete_class_output():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) { return value + 6; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "public class Limits {\n"
            "    private static final int LIMIT = 6;\n"
            "    int calculate(int value) { return value - LIMIT; }\n"
            "}\n"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_method": "calculate",
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert logs[0].metadata.get("model_assisted") is not True
    assert "return value + LIMIT;" in transformed
    assert "return value - LIMIT;" not in transformed
    assert logs[0].metadata["model_assistance"]["reason"] == (
        "CODET5_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
    )


def test_java_constant_recovers_exact_target_from_unified_diff_output():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) { return value + 6; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "--- Limits.java\n"
            "+++ Limits.java\n"
            "@@ -1,3 +1,4 @@\n"
            " public class Limits {\n"
            "+    private static final int LIMIT = 6;\n"
            "-    int calculate(int value) { return value + 6; }\n"
            "+    int calculate(int value) { return value + LIMIT; }\n"
            " }\n"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_method": "calculate",
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "complete_source_projected_to_exact_target"


def test_java_constant_completes_full_class_with_declaration_and_unchanged_target():
    source = (
        "public class Limits {\n"
        "    int calculate(int value) { return value + 6; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "public class Limits {\n"
            "    private static final int LIMIT = 6;\n"
            "    int calculate(int value) { return value + 6; }\n"
            "}\n"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Limits.java",
            "source_method": "calculate",
            "literal_value": 6,
            "constant_name": "LIMIT",
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Limits.java",
        model_assistant=assistant,
    )

    assert not warnings
    assert logs[0].metadata["model_assisted"] is True
    assert "private static final int LIMIT = 6;" in transformed
    assert "return value + LIMIT;" in transformed
    completion = logs[0].metadata["model_assistance"][
        "target_contract_completion"
    ]
    assert completion["reason"] == "complete_source_declaration_intent_completed"


def test_codet5_exact_java_method_wins_over_stale_line_hint():
    source = (
        "public class Order {\n"
        "    int selected() { return 10; }\n"
        "    int other() { return 20; }\n"
        "}\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="extract_method",
        parameters={
            "source_file": "Order.java",
            "source_method": "selected",
            "source_line": 3,
        },
    )

    context = assistant.resolve_target_context(
        language="java", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "method_with_sibling_helper"
    assert context.name == "Order.selected"


def test_codet5_python_context_uses_unique_literal_anchor():
    source = (
        "def ordinary():\n"
        "    return 10\n\n"
        "def selected():\n"
        "    return 125\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_file": "sample.py", "literal_value": 125},
    )

    context = assistant.resolve_target_context(
        language="python", source_code=source, action=action
    )

    assert context is not None
    assert context.name == "selected"


def test_codet5_c_context_uses_only_function_when_metadata_is_missing():
    source = "int selected(void) {\n    return 10;\n}\n"
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="replace_nested_conditional_with_guard_clauses",
        parameters={"source_file": "sample.c"},
    )

    context = assistant.resolve_target_context(
        language="c", source_code=source, action=action
    )

    assert context is not None
    assert context.name == "selected"


def test_codet5_c_header_resolves_line_targeted_preprocessor_directive():
    source = (
        "#ifndef SAMPLE_H\n"
        "#define SAMPLE_H\n"
        "#if defined(__GNUC__) && __GNUC__ >= 4\n"
        "#define SAMPLE_API __attribute__((visibility(\"default\")))\n"
        "#endif\n"
        "#endif\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Sample.h",
            "source_line": 3,
            "constant_name": "MIN_GNUC_VERSION",
            "literal_value": 4,
        },
    )

    context = assistant.resolve_target_context(
        language="c", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "preprocessor"
    assert context.start_line == 3
    assert context.end_line == 3
    assert "__GNUC__ >= 4" in context.source


def test_codet5_c_localized_action_recovers_one_unique_stale_line_anchor():
    source = "int value(void) {\n    int result = 10;\n    return result;\n}\n"
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_line": 1, "literal_value": 10, "constant_name": "LIMIT"},
    )

    context = assistant.resolve_target_context(
        language="c", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "statement"
    assert context.start_line == 2
    assert context.source.strip() == "int result = 10;"


def test_codet5_c_localized_action_does_not_guess_between_stale_anchors():
    source = "int value(int flag) {\n    if (flag) return -2;\n    return -2;\n}\n"
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_line": 1, "literal_value": -2, "constant_name": "ERROR_CODE"},
    )

    context = assistant.resolve_target_context(
        language="c", source_code=source, action=action
    )

    assert context is None


def test_codet5_cxx_header_resolves_exact_method_declaration():
    source = (
        "#include <string>\n"
        "class Reader {\n"
        "public:\n"
        "    explicit Reader(const std::string& filename);\n"
        "    int ParseError() const;\n"
        "};\n"
    )
    assistant = CodeT5CandidateTransformer(CodeT5Settings(enabled=True))
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "source_file": "Reader.h",
            "target_method": "ParseError",
        },
    )

    context = assistant.resolve_target_context(
        language="c", source_code=source, action=action
    )

    assert context is not None
    assert context.kind == "declaration"
    assert context.name == "ParseError"
    assert context.source.strip() == "int ParseError() const;"


def test_codet5_c_uses_target_span_even_when_complete_file_input_fits():
    source = (
        "#include <stdio.h>\n\n"
        "static int selected(void) {\n"
        "    return 50;\n"
        "}\n\n"
        "int untouched(void) { return 1; }\n"
    )
    prompts = []

    def generator(prompt):
        prompts.append(prompt)
        return "static int selected(void) {\n    return 50;\n}"

    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=generator,
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={"source_file": "sample.c", "source_method": "selected"},
    )

    candidate = assistant.propose(
        language="c", source_code=source, action=action, current_file_name="sample.c"
    )

    assert candidate.reason == "CODET5_NO_SOURCE_CHANGE"
    assert candidate.metadata["context_mode"] == "target_span"
    assert "<target-source>" in prompts[0]
    assert "untouched" not in prompts[0]


def test_codet5_java_uses_resolved_target_span_when_file_fits():
    source = (
        "package sample;\n"
        "class Values {\n"
        "    int selected() { return 1; }\n"
        "    int untouched() { return 2; }\n"
        "}\n"
    )
    prompts = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=lambda prompt: prompts.append(prompt) or "int selected() { return 1; }",
    )
    action = RefactoringAction(
        action_type="rename_symbol",
        source_refactoring="Rename Symbol",
        parameters={
            "source_file": "Values.java",
            "source_method": "selected",
            "old_name": "selected",
            "new_name": "chosen",
        },
    )

    candidate = assistant.propose(
        language="java",
        source_code=source,
        action=action,
        current_file_name="Values.java",
    )

    assert candidate.metadata["context_mode"] == "target_span"
    assert "<target-source>" in prompts[0]
    assert "untouched" not in prompts[0]


def test_codet5_retries_once_with_action_validation_feedback():
    source = "class Values {\n    int selected() { return 1; }\n}\n"
    prompts = []
    outputs = iter((
        "int selected() { return 2; }",
        "int selected() { return 3; }",
    ))
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(
            enabled=True,
            max_actions_per_request=2,
            max_actions_per_file=2,
        ),
        generator=lambda prompt: prompts.append(prompt) or next(outputs),
    )
    action = RefactoringAction(
        action_type="rename_symbol",
        source_refactoring="Rename Symbol",
        parameters={
            "source_file": "Values.java",
            "source_method": "selected",
            "old_name": "selected",
            "new_name": "chosen",
        },
    )

    def validator(_original, candidate, _action):
        passed = "return 3" in candidate
        return {
            "passed": passed,
            "issues": [] if passed else ["structural: planned rename was not applied"],
            "validation": [{"name": "structural", "passed": passed}],
        }

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="java",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="Values.java",
        model_assistant=assistant,
        model_candidate_validator=validator,
    )

    assert "return 3" in transformed
    assert len(prompts) == 2
    assert "<validation-feedback>" in prompts[1]
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["model_assistance"]["status"] == "candidate_repaired"
    assert not warnings


def test_java_extract_model_candidate_retains_validated_lineage_through_final_checks():
    original = (
        "public class Calculator {\n"
        "    public int calculate(int value) {\n"
        "        int doubled = value * 2;\n"
        "        int adjusted = doubled + 1;\n"
        "        return adjusted;\n"
        "    }\n\n"
        "    public int untouched(int value) {\n"
        "        return value - 1;\n"
        "    }\n"
        "}\n"
    )
    generated_members = (
        "public int calculate(int value) {\n"
        "    int doubled = calculateDoubled(value);\n"
        "    int adjusted = doubled + 1;\n"
        "    return adjusted;\n"
        "}\n\n"
        "private int calculateDoubled(int value) {\n"
        "    return value * 2;\n"
        "}"
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "Calculator.java",
            "source_class": "Calculator",
            "source_method": "calculate",
        },
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_actions_per_file=2),
        generator=lambda _prompt: generated_members,
    )
    agent = SafeCodeTransformationValidationAgent()

    transformed, logs, warnings = agent.transformer.apply_actions(
        language="java",
        source_code=original,
        actions=[action],
        strict_mode=True,
        current_file_name="Calculator.java",
        project_source_files=[{
            "file_name": "Calculator.java",
            "language": "java",
            "source_code": original,
        }],
        model_assistant=assistant,
        model_candidate_validator=lambda before, candidate, planned: (
            agent._validate_model_action_candidate(
                language="java",
                original_code=before,
                candidate_code=candidate,
                action=planned,
                timeout_seconds=10,
                behavior_tests=[],
                enable_behavior_tests=True,
                strict_mode=True,
                project_source_files=[],
                current_file_name="Calculator.java",
            )
        ),
    )

    assert not warnings
    assert "private int calculateDoubled(int value)" in transformed
    assert "public int untouched(int value)" in transformed
    assert logs[0].metadata["model_assisted"] is True
    assert logs[0].metadata["source_class"] == "Calculator"
    assert logs[0].metadata["source_method"] == "calculate"
    assert logs[0].metadata["extracted_method"] == "calculateDoubled"
    assert logs[0].metadata["plan_compliance"] == "PASS"
    assert logs[0].metadata["validation"]["structural"] == "PASS"
    action_checks = logs[0].metadata["model_assistance"]["action_validation"]
    assert [item["name"] for item in action_checks] == [
        "syntax",
        "structural",
        "behavioral",
        "invariant",
    ]

    effective = agent._actions_with_effective_replacements([action], logs)
    structural = agent.structural_validator.validate(
        language="java",
        original_code=original,
        transformed_code=transformed,
        actions=effective,
    )
    behavioral = agent.behavioral_validator.validate(
        language="java",
        original_code=original,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=effective,
        strict_mode=True,
        project_source_files=[],
        current_file_name="Calculator.java",
        structural_validation_passed=structural.passed,
    )

    assert structural.passed is True
    assert behavioral.passed is True


def test_java_extract_model_candidate_that_changes_behavior_is_rejected():
    original = (
        "public class Calculator {\n"
        "    public int calculate(int value) {\n"
        "        int doubled = value * 2;\n"
        "        int adjusted = doubled + 1;\n"
        "        return adjusted;\n"
        "    }\n"
        "}\n"
    )
    behavior_changing_candidate = (
        "public class Calculator {\n"
        "    public int calculate(int value) {\n"
        "        int doubled = calculateDoubled(value);\n"
        "        int adjusted = doubled + 1;\n"
        "        return adjusted;\n"
        "    }\n\n"
        "    private int calculateDoubled(int value) {\n"
        "        return value * 3;\n"
        "    }\n"
        "}\n"
    )
    action = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "Calculator.java",
            "source_class": "Calculator",
            "source_method": "calculate",
        },
    )

    checkpoint = SafeCodeTransformationValidationAgent()._validate_model_action_candidate(
        language="java",
        original_code=original,
        candidate_code=behavior_changing_candidate,
        action=action,
        timeout_seconds=10,
        behavior_tests=[],
        enable_behavior_tests=True,
        strict_mode=True,
        project_source_files=[],
        current_file_name="Calculator.java",
    )

    assert checkpoint["passed"] is False
    assert checkpoint["accepted_metadata"] == {}
    assert any(issue.startswith("behavioral:") for issue in checkpoint["issues"])
    assert any(
        item["name"] == "behavioral" and item["passed"] is False
        for item in checkpoint["validation"]
    )


def test_codet5_unwraps_single_fenced_block_with_explanation():
    output = "Here is the transformed target:\n```c\nint value(void) { return 2; }\n```\nDone."

    normalized = CodeT5CandidateTransformer._normalise_model_output(output)

    assert normalized == "int value(void) { return 2; }"


def test_c_model_preflight_allows_safe_local_array_extent():
    calls = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: calls.append(prompt) or (
            "#define BUFFER_SIZE 50\n"
            "    char buffer[BUFFER_SIZE];"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "sample.c",
            "source_line": 2,
            "literal_value": 50,
            "constant_name": "BUFFER_SIZE",
        },
    )
    source = "int value(void) {\n    char buffer[50];\n    return buffer[0];\n}\n"

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="c",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="sample.c",
        model_assistant=assistant,
    )
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        logs,
        validation_score=1.0,
        rollback_occurred=False,
        transformation_applied=True,
        model_enabled=True,
    )

    assert "#define BUFFER_SIZE 50" in transformed
    assert "char buffer[BUFFER_SIZE];" in transformed
    assert len(calls) == 1
    assert logs[0].metadata["model_assisted"] is True
    assert summary["model_attempted"] is True
    assert summary["model_action_count"] == 1
    assert summary["model_status"] == "MODEL_OUTPUT_RETAINED"
    assert summary["model_ineligible_reasons"] == []


def test_c_model_preflight_allows_header_prototype_array_extent():
    calls = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: calls.append(prompt) or (
            "#define READER_BUFFER_SIZE 64\n"
            "int read_value(char buffer[READER_BUFFER_SIZE]);"
        ),
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "reader.h",
            "source_line": 4,
            "literal_value": 64,
            "constant_name": "READER_BUFFER_SIZE",
        },
    )
    source = (
        "#ifndef READER_H\n"
        "#define READER_H\n\n"
        "int read_value(char buffer[64]);\n\n"
        "#endif\n"
    )

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="c",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="reader.h",
        model_assistant=assistant,
    )

    assert "#define READER_BUFFER_SIZE 64" in transformed
    assert "int read_value(char buffer[READER_BUFFER_SIZE]);" in transformed
    assert len(calls) == 1
    assert logs[0].metadata["model_assisted"] is True


def test_c_model_preflight_attempts_inihfuzz_global_array_extent():
    calls = []
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda prompt: calls.append(prompt) or "#define PREV_SECTION_SIZE 50",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        source_refactoring="Introduce Constant",
        parameters={
            "source_file": "fuzzing/inihfuzz.c",
            "source_line": 2,
            "literal_value": 50,
            "constant_name": "PREV_SECTION_SIZE",
        },
    )
    source = "int User;\nchar Prev_section[50];\n"

    transformed, logs, _ = TransformationEngine().apply_actions(
        language="c",
        source_code=source,
        actions=[action],
        strict_mode=True,
        current_file_name="fuzzing/inihfuzz.c",
        model_assistant=assistant,
    )

    assert len(calls) == 1
    assert logs[0].metadata["model_assisted"] is True
    assert "#define PREV_SECTION_SIZE 50" in transformed
    assert "char Prev_section[PREV_SECTION_SIZE];" in transformed
    assert "model_eligibility" not in logs[0].metadata
    assert "required-macro: #define PREV_SECTION_SIZE 50" in calls[0]
    assert "Do not add includes, functions, variables" in calls[0]
    completion = logs[0].metadata["model_assistance"]["target_contract_completion"]
    assert completion["reason"] == "missing_target_statement_restored"
    assert completion["contract"] == "c_introduce_constant_target_statement"


def test_codet5_ambiguous_large_file_target_is_not_guessed():
    filler = "\n".join(f"# unrelated context {index}" for index in range(180))
    original = (
        "class First:\n"
        "    def selected(self):\n"
        "        return 1\n\n"
        "class Second:\n"
        "    def selected(self):\n"
        "        return 2\n\n"
        + filler
        + "\n"
    )
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True, max_input_tokens=512),
        generator=lambda _prompt: "must not be called",
    )
    action = RefactoringAction(
        action_type="extract_method",
        parameters={"source_file": "ambiguous.py", "source_method": "selected"},
    )

    candidate = assistant.propose(
        language="python",
        source_code=original,
        action=action,
        current_file_name="ambiguous.py",
    )

    assert not candidate.source_code
    assert candidate.reason == "CODET5_TARGET_CONTEXT_NOT_RESOLVED"
    assert candidate.metadata["context_mode"] == "target_span_unresolved"


def test_model_accuracy_is_zero_when_rule_based_output_is_used():
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        [
            TransformationLogEntry(
                action_index=1,
                action_type="extract_method",
                replacements_count=1,
                metadata={"status": "success"},
            )
        ],
        validation_score=0.96,
        rollback_occurred=False,
        transformation_applied=True,
    )

    assert summary["model_used"] is False
    assert summary["model_validated_accuracy"] == 0
    assert summary["transformation_engine"] == "RULE_BASED"
    assert summary["model_status"] == "MODEL_NOT_USED"


def test_model_accuracy_is_positive_only_for_retained_codet5_output():
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        [
            TransformationLogEntry(
                action_index=1,
                action_type="extract_method",
                replacements_count=1,
                metadata={
                    "status": "success",
                    "model_assisted": True,
                    "model_provider": "codet5_plus",
                    "model_assistance": {"context_mode": "target_span"},
                },
            )
        ],
        validation_score=0.0,
        rollback_occurred=False,
        transformation_applied=True,
    )

    assert summary["model_used"] is True
    assert summary["model_validated_accuracy"] == 1.0
    assert summary["model_context_modes"] == ["target_span"]
    assert summary["transformation_engine"] == "HYBRID"
    assert summary["hybrid_strategy"] == "MODEL_FIRST_RULE_FALLBACK"
    assert summary["accepted_output_engine"] == "CODET5_PLUS"
    assert summary["rule_fallback_used"] is False
    assert summary["model_status"] == "MODEL_OUTPUT_RETAINED"


def test_rejected_model_attempt_reports_zero_and_rule_fallback():
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        [
            TransformationLogEntry(
                action_index=1,
                action_type="extract_method",
                replacements_count=1,
                metadata={
                    "status": "success",
                    "model_assistance": {
                        "status": "candidate_generated",
                        "reason": "CODET5_CANDIDATE_SYNTAX_INVALID",
                    },
                },
            )
        ],
        validation_score=0.99,
        rollback_occurred=False,
        transformation_applied=True,
    )

    assert summary["model_attempted"] is True
    assert summary["model_used"] is False
    assert summary["model_validated_accuracy"] == 0
    assert summary["model_status"] == "MODEL_ATTEMPT_REJECTED_RULE_FALLBACK"
    assert summary["transformation_engine"] == "HYBRID"
    assert summary["hybrid_strategy"] == "MODEL_FIRST_RULE_FALLBACK"
    assert summary["accepted_output_engine"] == "RULE_BASED"
    assert summary["rule_fallback_used"] is True
    assert summary["model_attempt_reasons"] == [
        "CODET5_CANDIDATE_SYNTAX_INVALID"
    ]


def test_rejected_model_attempt_reports_the_final_repair_reason():
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        [
            TransformationLogEntry(
                action_index=1,
                action_type="introduce_constant",
                replacements_count=1,
                metadata={
                    "status": "success",
                    "model_assistance": {
                        "status": "candidate_rejected",
                        "reason": "CODET5_CANDIDATE_INTENT_NOT_SATISFIED",
                        "repair_attempt": {
                            "status": "candidate_rejected",
                            "reason": "CODET5_EMPTY_OUTPUT",
                        },
                    },
                },
            )
        ],
        validation_score=0.99,
        rollback_occurred=False,
        transformation_applied=True,
    )

    assert summary["model_attempt_reasons"] == ["CODET5_EMPTY_OUTPUT"]


def test_enabled_model_without_eligible_action_is_reported_separately():
    summary = SafeCodeTransformationValidationAgent._model_usage_summary(
        [],
        validation_score=1.0,
        rollback_occurred=False,
        transformation_applied=False,
        model_enabled=True,
    )

    assert summary["model_enabled"] is True
    assert summary["model_attempted"] is False
    assert summary["model_used"] is False
    assert summary["model_validated_accuracy"] == 0
    assert summary["model_status"] == "MODEL_ENABLED_NO_ELIGIBLE_ACTION"
    assert summary["transformation_engine"] == "HYBRID"
    assert summary["accepted_output_engine"] == "NO_CHANGE"


def test_final_model_validation_failure_is_reported_as_rule_fallback():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "unused",
    )
    rule_result = {
        "success": True,
        "status": "FULL_SUCCESS",
        "transformation_applied": True,
        "rule_action_count": 1,
        "model_attempt_reasons": [],
        "safety_report": {"human_messages": [], "risk_flags": []},
    }
    failed_log = [
        TransformationLogEntry(
            action_index=1,
            action_type="introduce_constant",
            replacements_count=1,
            metadata={
                "status": "success",
                "model_assisted": True,
                "model_provider": "qwen_coder",
                "model_assistance": {"context_mode": "target_span"},
            },
        ),
        TransformationLogEntry(
            action_index=2,
            action_type="introduce_constant",
            replacements_count=1,
            metadata={
                "status": "success",
                "model_assisted": True,
                "model_provider": "qwen_coder",
                "model_assistance": {
                    "context_mode": "target_span",
                    "shared_candidate": True,
                },
            },
        ),
    ]

    result = SafeCodeTransformationValidationAgent._annotate_model_first_rule_fallback(
        rule_result,
        model_assistant=assistant,
        failed_model_log=failed_log,
        failed_steps=["structural", "behavioral"],
        failed_validation_details=[
            {"name": "structural", "message": "Planned structure was not preserved."}
        ],
        rollback_reason="Structural validation failed.",
    )

    assert result["transformation_engine"] == "HYBRID"
    assert result["hybrid_strategy"] == "MODEL_FIRST_RULE_FALLBACK"
    assert result["accepted_output_engine"] == "RULE_BASED"
    assert result["rule_fallback_used"] is True
    assert result["model_used"] is False
    assert result["model_attempt_count"] == 1
    assert result["model_status"] == "MODEL_FINAL_VALIDATION_FAILED_RULE_FALLBACK"
    assert result["model_failed_validation_steps"] == ["structural", "behavioral"]
    assert result["model_fallback_details"]["failed_validation_details"][0]["name"] == "structural"
    assert result["safety_report"]["model_fallback"]["failed_model_action_count"] == 2


def test_primary_model_final_validation_failure_gets_one_validator_guided_repair(
    monkeypatch,
):
    original = "int value(int input) { return input > 10; }\n"
    request = SCTVARequestContract.from_dict({
        "request_id": "hybrid-final-fallback",
        "language": "c",
        "source_files": [{
            "file_name": "value.c",
            "language": "c",
            "source_code": original,
        }],
        "refactoring_plan": {
            "plan_id": "hybrid-final-fallback-plan",
            "actions": [{
                "action_type": "introduce_constant",
                "parameters": {
                    "source_file": "value.c",
                    "source_line": 1,
                    "literal_value": 10,
                    "constant_name": "MAX_VALUE",
                },
            }],
            "behavior_tests": [],
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: (
            "#define MAX_VALUE 10\n\n"
            "int value(int input) { return input > MAX_VALUE; }\n"
        ),
    )
    agent = SafeCodeTransformationValidationAgent()
    original_evaluate = agent.rollback_manager.evaluate
    evaluate_calls = 0

    def force_first_candidate_rollback(*args, **kwargs):
        nonlocal evaluate_calls
        evaluate_calls += 1
        if evaluate_calls == 1:
            return True, "Forced model final-validation failure."
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        agent.rollback_manager,
        "evaluate",
        force_first_candidate_rollback,
    )

    result = agent._execute_single_file(
        request=request,
        file_entry=request.source_files[0],
        actions=request.refactoring_plan.actions,
        project_files=[request.source_files[0].to_dict()],
        model_assistant=assistant,
    )

    assert evaluate_calls == 2
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["transformation_engine"] == "HYBRID"
    assert result["accepted_output_engine"] == "QWEN_CODER"
    assert result["rule_fallback_used"] is False
    assert result["model_status"] == "MODEL_OUTPUT_RETAINED"
    assert result["model_validation_repair_attempted"] is True
    assert result["model_validation_repair_accepted"] is True
    assert "input > MAX_VALUE" in result["refactored_code"]


def test_usage_snapshot_keeps_generation_accounting_scoped_to_its_file():
    assistant = CodeT5CandidateTransformer(
        CodeT5Settings(enabled=True),
        generator=lambda _prompt: "private static final int LIMIT = 6;\nint value = LIMIT;",
    )
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "constant_name": "LIMIT",
            "literal_value": 6,
            "source_line": 2,
        },
    )

    before = assistant.usage_snapshot("Example.java")
    assistant.propose(
        language="java",
        source_code="class Example {\n    int value = 6;\n}\n",
        action=action,
        current_file_name="Example.java",
    )
    after = assistant.usage_snapshot("Example.java")
    unrelated = assistant.usage_snapshot("Other.java")

    assert after["file_attempt_count"] == before["file_attempt_count"] + 1
    assert after["file_generation_seconds"] >= before["file_generation_seconds"]
    assert unrelated["file_attempt_count"] == 0
    assert unrelated["file_generation_seconds"] == 0.0
