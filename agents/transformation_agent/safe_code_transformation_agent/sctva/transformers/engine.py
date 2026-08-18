"""Transformation engine that dispatches actions per language."""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Tuple

from ..constants import (
    ACTION_EXTRACT_CONSTANT,
    ACTION_INTRODUCE_CONSTANT,
    ACTION_INJECT_SYNTAX_ERROR,
    ACTION_FAULT_INJECTION,
    ACTION_NOOP,
    ACTION_EXTRACT_METHOD,
    ACTION_ENCAPSULATE_VARIABLE,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    ACTION_RENAME_SYMBOL,
    ACTION_REPLACE_LITERAL,
    ACTION_NORMALIZE_MULTILINE_STATEMENT,
)
from ..contracts import RefactoringAction
from ..models import TransformationLogEntry
from . import c_transformers, java_transformers, python_transformers


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
    ) -> Tuple[str, List[TransformationLogEntry], List[str]]:
        current_code = source_code
        logs: List[TransformationLogEntry] = []
        global_warnings: List[str] = []
        syntax_cache: Dict[Tuple[str, str], str] = {}

        for idx, action in enumerate(actions, start=1):
            warnings = list(action.warnings)
            replacements = 0
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
                    method_name = str(action.parameters.get("method") or action.parameters.get("method_name") or "").strip()
                    new_method_name = str(
                        action.parameters.get("new_method_name")
                        or action.parameters.get("extracted_method_name")
                        or ""
                    ).strip()
                    start_line = action.parameters.get("start_line")
                    end_line = action.parameters.get("end_line")
                    if isinstance(start_line, str) and start_line.strip().isdigit():
                        start_line = int(start_line.strip())
                    if isinstance(end_line, str) and end_line.strip().isdigit():
                        end_line = int(end_line.strip())
                    if not new_method_name:
                        raise ValueError("extract_method requires 'new_method_name'.")
                    if not isinstance(start_line, int) or not isinstance(end_line, int):
                        raise ValueError("extract_method requires integer 'start_line' and 'end_line'.")

                    if language == "python":
                        current_code, replacements = python_transformers.apply_extract_method(
                            current_code, new_method_name, start_line, end_line
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_extract_method(
                            current_code, new_method_name, start_line, end_line
                        )
                    else:
                        current_code, replacements = java_transformers.apply_extract_method(
                            current_code,
                            new_method_name,
                            start_line,
                            end_line,
                            method_name=method_name or None,
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

                    if not method_name and source_line is None:
                        raise ValueError("remove_dead_code requires 'method' or 'source_line'.")

                    if class_name is not None:
                        class_name = str(class_name).strip() or None

                    if language == "python":
                        current_code, replacements = python_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name, source_line
                        )
                    elif language == "c":
                        current_code, replacements = c_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name, source_line
                        )
                    else:
                        current_code, replacements = java_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name, source_line
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
                )
            )
            global_warnings.extend(warnings)

        return current_code, logs, global_warnings
