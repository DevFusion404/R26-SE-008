"""Transformation engine that dispatches actions per language."""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Sequence, Tuple

from ..analysis import LocalRefactorDetector
from ..constants import (
    ACTION_EXTRACT_CONSTANT,
    ACTION_INTRODUCE_CONSTANT,
    ACTION_INJECT_SYNTAX_ERROR,
    ACTION_FAULT_INJECTION,
    ACTION_NOOP,
    ACTION_EXTRACT_METHOD,
    ACTION_EXTRACT_CLASS,
    ACTION_EXTRACT_PYTHON_CLASS,
    ACTION_EXTRACT_JAVA_CLASS,
    ACTION_EXTRACT_C_COMPONENT,
    ACTION_ENCAPSULATE_VARIABLE,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    ACTION_RENAME_SYMBOL,
    ACTION_REPLACE_LITERAL,
    ACTION_NORMALIZE_MULTILINE_STATEMENT,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_INTRODUCE_PARAMETER_OBJECT,
    ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT,
    ACTION_INTRODUCE_PYTHON_PARAMETER_OBJECT,
    PARAMETER_OBJECT_ACTIONS,
    EXTRACT_CLASS_ACTIONS,
)
from ..contracts import RefactoringAction
from ..models import TransformationLogEntry
from . import (
    c_extract_class,
    c_extract_method,
    c_transformers,
    java_extract_class,
    java_extract_method,
    java_parameter_object,
    java_transformers,
    python_extract_method,
    python_parameter_object,
    python_transformers,
)


def _parse_literal_values_from_hint(hint: str) -> List[Any]:
    values: List[Any] = []
    seen = set()
    for match in re.findall(r"-?\d+(?:\.\d+)?", hint):
        value: Any
        if "." in match:
            value = float(match)
        else:
            value = int(match)
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


class TransformationEngine:
    """Applies refactoring actions and records detailed audit logs."""

    @staticmethod
    def _suppress_no_replacements_warning(
        action: RefactoringAction,
        current_code: str,
    ) -> bool:
        if action.action_type not in {ACTION_EXTRACT_CONSTANT, ACTION_INTRODUCE_CONSTANT}:
            return False

        constant_name = str(action.parameters.get("constant_name") or "").strip()
        if not constant_name:
            return False
        return constant_name in current_code

    @staticmethod
    def _infer_exception_target_from_source(
        *,
        language: str,
        source_code: str,
        source_line: int | None,
        original_exception_type: str,
        handler_name: str,
    ) -> str:
        """Supply a missing RDP exception type only when local analysis proves it.

        Planner output often labels the refactoring but omits the concrete
        exception type. Reuse SCTVA's conservative detector rather than making
        an unsupported best-effort replacement in a language transformer.
        """

        candidates = [
            action
            for action in LocalRefactorDetector().detect(
                language=language,
                file_name="",
                source_code=source_code,
                existing_actions=[],
            )
            if action.action_type == ACTION_NARROW_EXCEPTION_HANDLER
        ]
        if source_line is not None:
            line_matches = [
                action for action in candidates
                if action.parameters.get("source_line") == source_line
            ]
            if line_matches:
                candidates = line_matches
        if original_exception_type:
            type_matches = [
                action for action in candidates
                if action.parameters.get("original_exception_type") == original_exception_type
            ]
            if type_matches:
                candidates = type_matches
        if handler_name:
            name_matches = [
                action for action in candidates
                if action.parameters.get("handler_name") == handler_name
            ]
            if name_matches:
                candidates = name_matches
        if len(candidates) != 1:
            return ""
        return str(candidates[0].parameters.get("target_exception_type") or "").strip()

    @staticmethod
    def _exception_action_at_source_line(
        *,
        language: str,
        source_code: str,
        source_line: int | None,
    ) -> Dict[str, Any] | None:
        """Identify a legacy dead-code action that actually targets a handler."""

        if language not in {"python", "java"} or source_line is None:
            return None
        matches = [
            action
            for action in LocalRefactorDetector().detect(
                language=language,
                file_name="",
                source_code=source_code,
                existing_actions=[],
            )
            if action.action_type == ACTION_NARROW_EXCEPTION_HANDLER
            and action.parameters.get("source_line") == source_line
        ]
        if len(matches) != 1:
            return None
        return dict(matches[0].parameters)

    @staticmethod
    def _candidate_syntax_issue(language: str, source_code: str) -> str:
        if language == "python":
            try:
                ast.parse(source_code)
            except SyntaxError as exc:
                return f"Python syntax error: {exc.msg} at line {exc.lineno}."
            return ""

        pairs = {")": "(", "]": "[", "}": "{"}
        stack: list[str] = []
        state = "code"
        index = 0
        while index < len(source_code):
            char = source_code[index]
            nxt = source_code[index + 1] if index + 1 < len(source_code) else ""
            if state == "code":
                if char == "/" and nxt == "/":
                    state = "line_comment"
                    index += 2
                    continue
                if char == "/" and nxt == "*":
                    state = "block_comment"
                    index += 2
                    continue
                if char == '"':
                    state = "string"
                elif char == "'":
                    state = "char"
                elif char in "([{":
                    stack.append(char)
                elif char in pairs:
                    if not stack or stack.pop() != pairs[char]:
                        return f"Unmatched delimiter '{char}'."
            elif state == "line_comment":
                if char == "\n":
                    state = "code"
            elif state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "code"
                    index += 2
                    continue
            elif state in {"string", "char"}:
                quote = '"' if state == "string" else "'"
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    state = "code"
            index += 1

        if stack:
            return f"Unclosed delimiter '{stack[-1]}'."
        if state in {"block_comment", "string", "char"}:
            return f"Unclosed {state.replace('_', ' ')}."
        return ""

    def apply_actions(
        self,
        *,
        language: str,
        source_code: str,
        actions: List[RefactoringAction],
        strict_mode: bool,
        project_source_files: Sequence[Any] | None = None,
        current_file_name: str = "",
        repository_complete: bool = False,
        behavior_tests: Sequence[Dict[str, Any]] | None = None,
    ) -> Tuple[str, List[TransformationLogEntry], List[str]]:
        current_code = source_code
        logs: List[TransformationLogEntry] = []
        global_warnings: List[str] = []
        syntax_cache: Dict[Tuple[str, str], str] = {}
        dead_code_anchors: Dict[int, Tuple[str, str]] = {}
        legacy_exception_anchors: Dict[int, Dict[str, Any]] = {}
        if language == "python":
            for action_index, action in enumerate(actions):
                if action.action_type != ACTION_REMOVE_DEAD_CODE:
                    continue
                if str(action.parameters.get("target_statement_fingerprint") or "").strip():
                    continue
                method_name = str(
                    action.parameters.get("method")
                    or action.parameters.get("method_name")
                    or ""
                ).strip()
                class_name = str(
                    action.parameters.get("class_name")
                    or action.parameters.get("target_class")
                    or action.parameters.get("source_class")
                    or action.parameters.get("class")
                    or ""
                ).strip() or None
                raw_line = action.parameters.get("source_line")
                source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
                dead_code_anchors[action_index] = python_transformers.resolve_dead_code_target(
                    source_code,
                    method_name=method_name,
                    class_name=class_name,
                    source_line=source_line,
                )
                exception_action = self._exception_action_at_source_line(
                    language=language,
                    source_code=source_code,
                    source_line=source_line,
                )
                if exception_action is not None:
                    legacy_exception_anchors[action_index] = exception_action

        for idx, action in enumerate(actions, start=1):
            warnings = list(action.warnings)
            replacements = 0
            action_metadata: Dict[str, Any] = {}
            before_action_code = current_code

            try:
                if action.action_type == ACTION_NOOP:
                    warnings.append("Action mapped to noop; no code change applied.")

                elif action.action_type == ACTION_RENAME_SYMBOL:
                    old_name = str(action.parameters.get("old_name", "")).strip()
                    new_name = str(action.parameters.get("new_name", "")).strip()
                    if not old_name or not new_name:
                        raise ValueError("rename_symbol requires 'old_name' and 'new_name'.")
                    if language == "python":
                        current_code, replacements = python_transformers.apply_rename_symbol(
                            current_code, old_name, new_name
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_rename_symbol(
                            current_code, old_name, new_name
                        )
                    else:
                        current_code, replacements = java_transformers.apply_rename_symbol(
                            current_code, old_name, new_name
                        )

                elif action.action_type == ACTION_EXTRACT_CONSTANT:
                    constant_name = str(action.parameters.get("constant_name", "EXTRACTED_CONSTANT")).strip() or "EXTRACTED_CONSTANT"
                    literal_value = action.parameters.get("literal_value")
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    if literal_value is None:
                        raise ValueError("extract_constant requires 'literal_value'.")
                    if language == "python":
                        current_code, replacements = python_transformers.apply_extract_constant(
                            current_code, literal_value, constant_name, source_line
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_extract_constant(
                            current_code, literal_value, constant_name, source_line
                        )
                    else:
                        current_code, replacements = java_transformers.apply_extract_constant(
                            current_code, literal_value, constant_name, source_line
                        )

                elif action.action_type == ACTION_INTRODUCE_CONSTANT:
                    constant_name = str(action.parameters.get("constant_name", "EXTRACTED_CONSTANT")).strip() or "EXTRACTED_CONSTANT"
                    literal_values: List[Any] = []

                    literal_value = action.parameters.get("literal_value") if "literal_value" in action.parameters else None
                    if literal_value is not None:
                        literal_values = [literal_value]
                    elif isinstance(action.parameters.get("literal_values"), list):
                        literal_values = list(action.parameters.get("literal_values") or [])
                    elif action.parameters.get("hint"):
                        literal_values = _parse_literal_values_from_hint(str(action.parameters.get("hint")))

                    if not literal_values:
                        raise ValueError("introduce_constant requires literal_value, literal_values, or hint.")

                    for index, literal_value in enumerate(literal_values, start=1):
                        if literal_value is None:
                            continue
                        name = constant_name if len(literal_values) == 1 else f"{constant_name}_{index}"
                        source_line = action.parameters.get("source_line")
                        source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                        if language == "python":
                            current_code, step_replacements = python_transformers.apply_extract_constant(
                                current_code, literal_value, name, source_line
                            )
                        elif language == "c":
                            current_code, step_replacements = c_transformers.apply_extract_constant(
                                current_code, literal_value, name, source_line
                            )
                        else:
                            current_code, step_replacements = java_transformers.apply_extract_constant(
                                current_code, literal_value, name, source_line
                            )
                        replacements += step_replacements

                elif action.action_type == ACTION_REPLACE_LITERAL:
                    if "old_literal" not in action.parameters or "new_literal" not in action.parameters:
                        raise ValueError("replace_literal requires 'old_literal' and 'new_literal'.")
                    old_literal = action.parameters["old_literal"]
                    new_literal = action.parameters["new_literal"]
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    if language == "python":
                        current_code, replacements = python_transformers.apply_replace_literal(
                            current_code, old_literal, new_literal
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_replace_literal(
                            current_code, old_literal, new_literal, source_line
                        )
                    else:
                        current_code, replacements = java_transformers.apply_replace_literal(
                            current_code, old_literal, new_literal, source_line
                        )

                elif action.action_type == ACTION_NORMALIZE_MULTILINE_STATEMENT:
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    constant_name = str(action.parameters.get("constant_name") or "SCTVA_EXTRACTED_VALUE").strip()
                    normalization = str(action.parameters.get("normalization") or "").strip()
                    if language == "java":
                        current_code, replacements = java_transformers.apply_normalize_multiline_statement(
                            current_code,
                            source_line=source_line,
                            constant_name=constant_name,
                            normalization=normalization,
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_normalize_multiline_statement(
                            current_code,
                            source_line=source_line,
                            constant_name=constant_name,
                            normalization=normalization,
                        )
                    else:
                        warnings.append("normalize_multiline_statement is not required for Python source.")

                elif action.action_type == ACTION_EXTRACT_METHOD:
                    method_name = str(
                        action.parameters.get("method")
                        or action.parameters.get("method_name")
                        or action.parameters.get("function")
                        or action.parameters.get("function_name")
                        or action.parameters.get("source_method")
                        or ""
                    ).strip()
                    new_method_name = str(
                        action.parameters.get("new_method_name")
                        or action.parameters.get("extracted_method_name")
                        or action.parameters.get("new_function_name")
                        or action.parameters.get("extracted_function_name")
                        or ""
                    ).strip()
                    source_class = str(
                        action.parameters.get("source_class")
                        or action.parameters.get("target_class")
                        or action.parameters.get("class_name")
                        or action.parameters.get("class")
                        or action.parameters.get("module_name")
                        or ""
                    ).strip()
                    method_signature = str(
                        action.parameters.get("method_signature")
                        or action.parameters.get("function_signature")
                        or action.parameters.get("signature")
                        or ""
                    ).strip()
                    start_line = action.parameters.get("start_line")
                    end_line = action.parameters.get("end_line")
                    if isinstance(start_line, str) and start_line.strip().isdigit():
                        start_line = int(start_line.strip())
                    if isinstance(end_line, str) and end_line.strip().isdigit():
                        end_line = int(end_line.strip())
                    if not method_name:
                        raise ValueError("extract_method requires a semantic method/function target.")
                    if not new_method_name:
                        raise ValueError("extract_method requires 'new_method_name'.")
                    if not isinstance(start_line, int):
                        start_line = None
                    if not isinstance(end_line, int):
                        end_line = None

                    if language == "python":
                        current_code, replacements, action_metadata = python_extract_method.apply_extract_method(
                            current_code,
                            new_method_name=new_method_name,
                            method_name=method_name,
                            source_class=source_class,
                            method_signature=method_signature,
                            start_line=start_line,
                            end_line=end_line,
                            source_file=str(action.parameters.get("source_file") or ""),
                            current_file_name=current_file_name,
                            source_resolution_error=str(action.parameters.get("source_resolution_error") or ""),
                        )
                    elif language == "c":
                        current_code, replacements, action_metadata = c_extract_method.apply_extract_method(
                            current_code,
                            new_method_name=new_method_name,
                            method_name=method_name,
                            source_class=source_class,
                            method_signature=method_signature,
                            start_line=start_line,
                            end_line=end_line,
                            source_file=str(action.parameters.get("source_file") or ""),
                            current_file_name=current_file_name,
                            source_resolution_error=str(action.parameters.get("source_resolution_error") or ""),
                        )
                    elif language == "java":
                        current_code, replacements, action_metadata = java_extract_method.apply_extract_method(
                            current_code,
                            new_method_name=new_method_name,
                            method_name=method_name,
                            source_class=source_class,
                            method_signature=method_signature,
                            start_line=start_line,
                            end_line=end_line,
                            source_file=str(action.parameters.get("source_file") or ""),
                            current_file_name=current_file_name,
                            source_resolution_error=str(action.parameters.get("source_resolution_error") or ""),
                        )
                    else:
                        raise ValueError(
                            f"extract_method is not supported for language '{language}'."
                        )

                    if action_metadata.get("status") == "review_required":
                        warnings.append(
                            "Extract Method requires review: "
                            f"{action_metadata.get('reason', 'unsafe extraction candidate')}."
                        )

                elif action.action_type in EXTRACT_CLASS_ACTIONS:
                    source_class = str(
                        action.parameters.get("source_class")
                        or action.parameters.get("class_name")
                        or action.parameters.get("target_class")
                        or action.parameters.get("class")
                        or ""
                    ).strip()
                    new_class_name = str(
                        action.parameters.get("new_class_name")
                        or action.parameters.get("extracted_class_name")
                        or action.parameters.get("destination_class")
                        or action.parameters.get("new_component_name")
                        or ""
                    ).strip()
                    methods_to_extract = (
                        action.parameters.get("methods_to_extract")
                        or action.parameters.get("functions_to_extract")
                    )
                    fields_to_extract = (
                        action.parameters.get("fields_to_extract")
                        or action.parameters.get("globals_to_extract")
                    )
                    if not isinstance(methods_to_extract, list):
                        methods_to_extract = []
                    if not isinstance(fields_to_extract, list):
                        fields_to_extract = []
                    preserve_public_api = bool(action.parameters.get("preserve_public_api", True))
                    delegation_strategy = str(action.parameters.get("delegation_strategy") or "wrapper")
                    target_file = str(action.parameters.get("target_file") or "same_file")

                    extract_kwargs = {
                        "source_file": str(action.parameters.get("source_file") or ""),
                        "current_file_name": current_file_name,
                        "source_class": source_class,
                        "new_class_name": new_class_name,
                        "methods_to_extract": [str(item) for item in methods_to_extract],
                        "fields_to_extract": [str(item) for item in fields_to_extract],
                        "preserve_public_api": preserve_public_api,
                        "delegation_strategy": delegation_strategy,
                        "target_file": target_file,
                        "project_source_files": project_source_files,
                        "repository_complete": repository_complete,
                        "behavior_tests": behavior_tests,
                        "required_public_methods": action.parameters.get("required_public_methods"),
                        "required_public_fields": action.parameters.get("required_public_fields"),
                        "source_resolution_error": str(
                            action.parameters.get("source_resolution_error") or ""
                        ),
                    }
                    if action.action_type == ACTION_EXTRACT_PYTHON_CLASS:
                        if language != "python":
                            raise ValueError("extract_python_class requires a Python source file.")
                        current_code, replacements, action_metadata = python_transformers.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                        )
                    elif action.action_type == ACTION_EXTRACT_JAVA_CLASS:
                        if language != "java":
                            raise ValueError("extract_java_class requires a Java source file.")
                        current_code, replacements, action_metadata = java_extract_class.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                        )
                    elif action.action_type == ACTION_EXTRACT_C_COMPONENT:
                        if language != "c":
                            raise ValueError("extract_c_component requires a C source file.")
                        current_code, replacements, action_metadata = c_extract_class.apply_extract_component(
                            current_code,
                            **extract_kwargs,
                        )
                    elif action.action_type == ACTION_EXTRACT_CLASS and language == "python":
                        current_code, replacements, action_metadata = python_transformers.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                        )
                    elif action.action_type == ACTION_EXTRACT_CLASS and language == "java":
                        current_code, replacements, action_metadata = java_extract_class.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                        )
                    elif action.action_type == ACTION_EXTRACT_CLASS and language == "c":
                        current_code, replacements, action_metadata = c_extract_class.apply_extract_component(
                            current_code,
                            **extract_kwargs,
                        )
                    else:
                        raise ValueError(
                            f"{action.action_type} is not supported for language '{language}'."
                        )

                    if action_metadata:
                        for resolution_key in (
                            "requested_source_class",
                            "source_class_resolution",
                            "source_class_origin",
                        ):
                            resolution_value = action.parameters.get(resolution_key)
                            if resolution_value:
                                action_metadata[resolution_key] = resolution_value
                        status = str(action_metadata.get("status") or "")
                        reason = str(action_metadata.get("reason") or "")
                        if status and status != "success":
                            warnings.append(
                                f"Extract Class {status}: {reason or 'review_required'}."
                            )
                        elif status == "success":
                            moved = ", ".join(action_metadata.get("methods_moved") or [])
                            if not action.parameters.get("methods_to_extract"):
                                action.parameters["methods_to_extract"] = list(
                                    action_metadata.get("methods_moved") or []
                                )
                            if not action.parameters.get("fields_to_extract"):
                                action.parameters["fields_to_extract"] = list(
                                    action_metadata.get("fields_moved") or []
                                )
                            target = action_metadata.get("extracted_class") or new_class_name
                            refactoring_name = action_metadata.get("refactoring") or "Extract Class"
                            warnings.append(
                                f"{refactoring_name} applied: moved methods to {target}"
                                + (f" ({moved})." if moved else ".")
                            )

                elif action.action_type in PARAMETER_OBJECT_ACTIONS:
                    method_name = str(
                        action.parameters.get("method")
                        or action.parameters.get("method_name")
                        or action.parameters.get("function")
                        or action.parameters.get("function_name")
                        or ""
                    ).strip()
                    object_name = str(
                        action.parameters.get("parameter_object_name")
                        or action.parameters.get("new_class_name")
                        or action.parameters.get("parameter_class_name")
                        or ""
                    ).strip()
                    kwargs = {
                        "method": method_name,
                        "parameter_object_name": object_name,
                        "source_class": str(action.parameters.get("source_class") or "").strip(),
                        "source_file": str(action.parameters.get("source_file") or ""),
                        "current_file_name": current_file_name,
                        "parameter_name": str(action.parameters.get("parameter_name") or "params").strip(),
                        "project_source_files": project_source_files,
                        "source_resolution_error": str(
                            action.parameters.get("source_resolution_error") or ""
                        ),
                    }
                    if action.action_type == ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT and language != "java":
                        raise ValueError("introduce_java_parameter_object requires a Java source file.")
                    if action.action_type == ACTION_INTRODUCE_PYTHON_PARAMETER_OBJECT and language != "python":
                        raise ValueError("introduce_python_parameter_object requires a Python source file.")
                    if language == "java":
                        current_code, replacements, action_metadata = (
                            java_parameter_object.apply_introduce_parameter_object(current_code, **kwargs)
                        )
                    elif language == "python":
                        current_code, replacements, action_metadata = (
                            python_parameter_object.apply_introduce_parameter_object(current_code, **kwargs)
                        )
                    else:
                        raise ValueError("Introduce Parameter Object is supported for Java and Python only.")
                    status = str(action_metadata.get("status") or "")
                    for resolution_key in (
                        "requested_source_class",
                        "source_class_resolution",
                        "source_class_origin",
                    ):
                        resolution_value = action.parameters.get(resolution_key)
                        if resolution_value:
                            action_metadata[resolution_key] = resolution_value
                    reason = str(action_metadata.get("reason") or "")
                    if status != "success":
                        warnings.append(
                            f"Introduce Parameter Object {status or 'review_required'}: "
                            f"{reason or 'unsafe transformation candidate'}."
                        )
                    else:
                        warnings.append(
                            "Introduce Parameter Object applied: "
                            f"{method_name} now accepts {object_name}."
                        )

                elif action.action_type == ACTION_REPLACE_UNSAFE_FUNCTION:
                    unsafe_function = str(action.parameters.get("unsafe_function") or "").strip()
                    safe_alternative = str(action.parameters.get("safe_alternative") or "").strip()
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    if not unsafe_function or not safe_alternative:
                        raise ValueError("replace_unsafe_function requires 'unsafe_function' and 'safe_alternative'.")
                    if language != "c":
                        warnings.append("replace_unsafe_function is currently supported for C source only.")
                    else:
                        current_code, replacements = c_transformers.apply_replace_unsafe_function(
                            current_code,
                            unsafe_function,
                            safe_alternative,
                            source_line,
                        )

                elif action.action_type == ACTION_ENCAPSULATE_VARIABLE:
                    variable_name = str(action.parameters.get("variable_name") or "").strip()
                    getter_name = str(action.parameters.get("getter_name") or f"get_{variable_name}").strip()
                    setter_name = str(action.parameters.get("setter_name") or f"set_{variable_name}").strip()
                    if not variable_name:
                        raise ValueError("encapsulate_variable requires 'variable_name'.")
                    if language != "c":
                        warnings.append("encapsulate_variable is currently supported for C source only.")
                    else:
                        current_code, replacements = c_transformers.apply_encapsulate_variable(
                            current_code,
                            variable_name,
                            getter_name,
                            setter_name,
                        )

                elif action.action_type == ACTION_INJECT_SYNTAX_ERROR:
                    if language == "python":
                        current_code, replacements = python_transformers.apply_inject_syntax_error(current_code)
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_inject_syntax_error(current_code)
                    else:
                        current_code, replacements = java_transformers.apply_inject_syntax_error(current_code)

                elif action.action_type == ACTION_FAULT_INJECTION:
                    original_logic = str(action.parameters.get("original_logic", "")).strip()
                    faulty_logic = action.parameters.get("faulty_logic")
                    if not original_logic:
                        raise ValueError("fault_injection requires 'original_logic'.")
                    if faulty_logic is None:
                        raise ValueError("fault_injection requires 'faulty_logic'.")
                    if language == "python":
                        current_code, replacements = python_transformers.apply_fault_injection(
                            current_code, original_logic, str(faulty_logic)
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_fault_injection(
                            current_code, original_logic, str(faulty_logic)
                        )
                    else:
                        current_code, replacements = java_transformers.apply_fault_injection(
                            current_code, original_logic, str(faulty_logic)
                        )

                elif action.action_type == ACTION_REMOVE_DEAD_CODE:
                    method_name = str(
                        action.parameters.get("method")
                        or action.parameters.get("method_name")
                        or ""
                    ).strip()
                    class_name = action.parameters.get("class_name")
                    if not class_name:
                        class_name = action.parameters.get("target_class") or action.parameters.get("source_class")
                    if not class_name:
                        class_name = action.parameters.get("class")
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    dead_code_kind = str(action.parameters.get("dead_code_kind") or "").strip()
                    target_statement_fingerprint = str(
                        action.parameters.get("target_statement_fingerprint") or ""
                    ).strip()
                    if language == "python" and not target_statement_fingerprint:
                        anchor_kind, anchor_fingerprint = dead_code_anchors.get(idx - 1, ("", ""))
                        dead_code_kind = dead_code_kind or anchor_kind
                        target_statement_fingerprint = anchor_fingerprint

                    if not method_name and source_line is None:
                        raise ValueError("remove_dead_code requires 'method' or 'source_line'.")

                    if class_name is not None:
                        class_name = str(class_name).strip() or None

                    legacy_exception_action = legacy_exception_anchors.get(idx - 1)
                    if legacy_exception_action is not None:
                        current_code, replacements = python_transformers.apply_narrow_exception_handler(
                            current_code,
                            source_line=source_line,
                            original_exception_type=str(
                                legacy_exception_action.get("original_exception_type") or ""
                            ),
                            target_exception_type=str(
                                legacy_exception_action.get("target_exception_type") or ""
                            ),
                            handler_name=str(legacy_exception_action.get("handler_name") or ""),
                        )
                        if replacements:
                            action_metadata["reclassified_action_type"] = ACTION_NARROW_EXCEPTION_HANDLER
                            action_metadata["effective_action_parameters"] = dict(
                                legacy_exception_action
                            )
                            action_metadata["reclassification_reason"] = (
                                "RDP Remove Dead Code targeted a live broad exception handler."
                            )
                            warnings.append(
                                "RDP Remove Dead Code action was safely reclassified to narrow_exception_handler."
                            )
                    elif language == "python":
                        current_code, replacements = python_transformers.apply_remove_dead_code(
                            current_code,
                            method_name,
                            class_name,
                            source_line,
                            dead_code_kind=dead_code_kind,
                            target_statement_fingerprint=target_statement_fingerprint,
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name, source_line
                        )
                    else:
                        current_code, replacements = java_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name, source_line
                        )

                elif action.action_type == ACTION_NARROW_EXCEPTION_HANDLER:
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    original_exception_type = str(
                        action.parameters.get("original_exception_type") or ""
                    ).strip()
                    target_exception_type = str(
                        action.parameters.get("target_exception_type") or ""
                    ).strip()
                    handler_name = str(action.parameters.get("handler_name") or "").strip()
                    if not target_exception_type and language in {"python", "java"}:
                        target_exception_type = self._infer_exception_target_from_source(
                            language=language,
                            source_code=current_code,
                            source_line=source_line,
                            original_exception_type=original_exception_type,
                            handler_name=handler_name,
                        )
                    if language == "python":
                        current_code, replacements = python_transformers.apply_narrow_exception_handler(
                            current_code,
                            source_line=source_line,
                            original_exception_type=original_exception_type,
                            target_exception_type=target_exception_type,
                            handler_name=handler_name,
                        )
                    elif language == "java":
                        current_code, replacements = java_transformers.apply_narrow_exception_handler(
                            current_code,
                            source_line=source_line,
                            original_exception_type=original_exception_type,
                            target_exception_type=target_exception_type,
                            handler_name=handler_name,
                        )
                    else:
                        warnings.append(
                            "Exception handler narrowing is not applicable to C because C has no try/catch handlers."
                        )

            except Exception as exc:
                warnings.append(f"Action failed: {exc}")
                if strict_mode:
                    raise

            if replacements > 0 and action.action_type != ACTION_INJECT_SYNTAX_ERROR:
                syntax_key = (language, current_code)
                syntax_issue = syntax_cache.get(syntax_key)
                if syntax_issue is None:
                    syntax_issue = self._candidate_syntax_issue(language, current_code)
                    syntax_cache[syntax_key] = syntax_issue
                if syntax_issue:
                    current_code = before_action_code
                    replacements = 0
                    warnings.append(
                        "Action reverted by the per-action syntax checkpoint: "
                        f"{syntax_issue}"
                    )

            if replacements == 0 and action.action_type == ACTION_REMOVE_DEAD_CODE:
                warnings.append(
                    "Dead-code removal skipped: SCTVA could not prove the target was unreachable "
                    "or an unused side-effect-free declaration."
                )

            if replacements == 0 and action.action_type == ACTION_NARROW_EXCEPTION_HANDLER:
                warnings.append(
                    "Exception handler narrowing skipped: SCTVA could not prove a unique safe target exception type."
                )

            if (
                replacements == 0
                and action.action_type != ACTION_NOOP
                and not self._suppress_no_replacements_warning(action, current_code)
            ):
                warnings.append("No replacements were applied.")

            logs.append(
                TransformationLogEntry(
                    action_index=idx,
                    action_type=action.action_type,
                    replacements_count=replacements,
                    warnings=warnings,
                    metadata=action_metadata,
                )
            )
            global_warnings.extend(warnings)

        return current_code, logs, global_warnings
