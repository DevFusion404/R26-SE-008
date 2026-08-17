"""Transformation engine that dispatches actions per language."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

from ..constants import (
    ACTION_EXTRACT_CONSTANT,
    ACTION_INTRODUCE_CONSTANT,
    ACTION_INJECT_SYNTAX_ERROR,
    ACTION_FAULT_INJECTION,
    ACTION_NOOP,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_RENAME_SYMBOL,
    ACTION_REPLACE_LITERAL,
)
from ..contracts import RefactoringAction
from ..models import TransformationLogEntry
from . import java_transformers, python_transformers


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

        for idx, action in enumerate(actions, start=1):
            warnings = list(action.warnings)
            replacements = 0

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
                    else:
                        current_code, replacements = java_transformers.apply_rename_symbol(
                            current_code, old_name, new_name
                        )

                elif action.action_type == ACTION_EXTRACT_CONSTANT:
                    constant_name = str(action.parameters.get("constant_name", "EXTRACTED_CONSTANT")).strip() or "EXTRACTED_CONSTANT"
                    literal_value = action.parameters.get("literal_value")
                    if literal_value is None:
                        raise ValueError("extract_constant requires 'literal_value'.")
                    if language == "python":
                        current_code, replacements = python_transformers.apply_extract_constant(
                            current_code, literal_value, constant_name
                        )
                    else:
                        current_code, replacements = java_transformers.apply_extract_constant(
                            current_code, literal_value, constant_name
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
                        if language == "python":
                            current_code, step_replacements = python_transformers.apply_extract_constant(
                                current_code, literal_value, name
                            )
                        else:
                            current_code, step_replacements = java_transformers.apply_extract_constant(
                                current_code, literal_value, name
                            )
                        replacements += step_replacements

                elif action.action_type == ACTION_REPLACE_LITERAL:
                    if "old_literal" not in action.parameters or "new_literal" not in action.parameters:
                        raise ValueError("replace_literal requires 'old_literal' and 'new_literal'.")
                    old_literal = action.parameters["old_literal"]
                    new_literal = action.parameters["new_literal"]
                    if language == "python":
                        current_code, replacements = python_transformers.apply_replace_literal(
                            current_code, old_literal, new_literal
                        )
                    else:
                        current_code, replacements = java_transformers.apply_replace_literal(
                            current_code, old_literal, new_literal
                        )

                elif action.action_type == ACTION_INJECT_SYNTAX_ERROR:
                    if language == "python":
                        current_code, replacements = python_transformers.apply_inject_syntax_error(current_code)
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

                    if not method_name:
                        raise ValueError("remove_dead_code requires 'method'.")

                    if class_name is not None:
                        class_name = str(class_name).strip() or None

                    if language == "python":
                        current_code, replacements = python_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name
                        )
                    else:
                        current_code, replacements = java_transformers.apply_remove_dead_code(
                            current_code, method_name, class_name
                        )

            except Exception as exc:
                warnings.append(f"Action failed: {exc}")
                if strict_mode:
                    raise

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
