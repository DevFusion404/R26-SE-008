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
    ACTION_ENCAPSULATE_C_VARIABLE,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    ACTION_RENAME_SYMBOL,
    ACTION_RENAME_METHOD,
    ACTION_REPLACE_LITERAL,
    ACTION_NORMALIZE_MULTILINE_STATEMENT,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_INTRODUCE_PARAMETER_OBJECT,
    ACTION_INTRODUCE_C_PARAMETER_OBJECT,
    ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT,
    ACTION_INTRODUCE_PYTHON_PARAMETER_OBJECT,
    ACTION_INLINE_PYTHON_CLASS,
    ACTION_HIDE_DELEGATE,
    ACTION_MOVE_PYTHON_METHOD,
    ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
    ACTION_MOVE_C_FUNCTION,
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
    java_hide_delegate,
    java_parameter_object,
    java_transformers,
    python_extract_method,
    python_hide_delegate,
    python_inline_class,
    python_parameter_object,
    python_replace_conditional,
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


def _action_method_name(action: RefactoringAction) -> str:
    return str(
        action.parameters.get("method")
        or action.parameters.get("method_name")
        or action.parameters.get("function")
        or action.parameters.get("function_name")
        or action.parameters.get("source_method")
        or ""
    ).strip()


def _action_source_class(action: RefactoringAction) -> str:
    return str(
        action.parameters.get("source_class")
        or action.parameters.get("target_class")
        or action.parameters.get("class_name")
        or action.parameters.get("class")
        or ""
    ).strip()


def _same_java_method_target(
    extract_action: RefactoringAction,
    parameter_action: RefactoringAction,
) -> bool:
    extract_method = _action_method_name(extract_action)
    parameter_method = _action_method_name(parameter_action)
    if not extract_method or extract_method != parameter_method:
        return False
    extract_class = _action_source_class(extract_action)
    parameter_class = _action_source_class(parameter_action)
    if extract_class and parameter_class and extract_class != parameter_class:
        return False
    extract_file = str(extract_action.parameters.get("source_file") or "").replace("\\", "/").lower()
    parameter_file = str(parameter_action.parameters.get("source_file") or "").replace("\\", "/").lower()
    if extract_file and parameter_file and extract_file != parameter_file:
        return False
    return True


def _java_extract_action_kwargs(
    action: RefactoringAction,
    *,
    current_file_name: str,
    project_source_files: Sequence[Any] | None,
) -> dict[str, Any]:
    start_line = action.parameters.get("start_line")
    end_line = action.parameters.get("end_line")
    if isinstance(start_line, str) and start_line.strip().isdigit():
        start_line = int(start_line.strip())
    if isinstance(end_line, str) and end_line.strip().isdigit():
        end_line = int(end_line.strip())
    return {
        "new_method_name": str(
            action.parameters.get("new_method_name")
            or action.parameters.get("extracted_method_name")
            or action.parameters.get("new_function_name")
            or action.parameters.get("extracted_function_name")
            or ""
        ).strip(),
        "method_name": _action_method_name(action),
        "source_class": _action_source_class(action),
        "method_signature": str(
            action.parameters.get("method_signature")
            or action.parameters.get("function_signature")
            or action.parameters.get("signature")
            or ""
        ).strip(),
        "start_line": start_line if isinstance(start_line, int) else None,
        "end_line": end_line if isinstance(end_line, int) else None,
        "source_file": str(action.parameters.get("source_file") or ""),
        "current_file_name": current_file_name,
        "source_resolution_error": str(
            action.parameters.get("source_resolution_error") or ""
        ),
        "project_source_files": project_source_files,
    }


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
    def _resolve_c_encapsulate_actions_from_source(
        source_code: str,
        actions: List[RefactoringAction],
        *,
        file_name: str = "",
    ) -> None:
        """Last-chance resolution for generic C Global Variable plan targets.

        This intentionally lives in the transformation engine as well as the
        agent layer.  Direct engine callers and older integration paths can
        otherwise bypass agent-side target recovery and still pass the literal
        planner placeholder ``variable`` to the C transformer.
        """

        encapsulate_actions = [
            action
            for action in actions
            if action.action_type in {
                ACTION_ENCAPSULATE_VARIABLE,
                ACTION_ENCAPSULATE_C_VARIABLE,
            }
            or str(action.source_refactoring or "").strip().lower()
            in {"encapsulate variable", "global variable"}
        ]
        if not encapsulate_actions:
            return

        detected = [
            action
            for action in LocalRefactorDetector().detect(
                language="c",
                file_name=file_name,
                source_code=source_code,
                existing_actions=[],
            )
            if action.action_type == ACTION_ENCAPSULATE_C_VARIABLE
        ]
        if not detected:
            return

        detected.sort(
            key=lambda action: int(action.parameters.get("source_line") or 10**9)
        )
        by_name = {
            str(action.parameters.get("variable_name") or "").strip(): action
            for action in detected
            if str(action.parameters.get("variable_name") or "").strip()
        }
        generic_names = {
            "variable", "global", "global_variable", "globalvariable", "var", "value"
        }
        resolved_names: set[str] = set()
        unresolved: list[tuple[int, RefactoringAction, str]] = []

        for order, action in enumerate(encapsulate_actions):
            params = action.parameters
            requested = str(params.get("variable_name") or params.get("variable") or "").strip()
            candidate = by_name.get(requested)
            if candidate is None:
                unresolved.append((order, action, requested))
                continue
            cparams = candidate.parameters
            action.action_type = ACTION_ENCAPSULATE_C_VARIABLE
            params["source_file"] = str(params.get("source_file") or file_name)
            params["source_line"] = cparams.get("source_line")
            if str(params.get("getter_name") or "").strip().lower() in {"", "get_variable", "get_global", "get_var"}:
                params["getter_name"] = cparams.get("getter_name")
            if str(params.get("setter_name") or "").strip().lower() in {"", "set_variable", "set_global", "set_var"}:
                params["setter_name"] = cparams.get("setter_name")
            params["_c_global_target_resolution"] = {
                "status": "success",
                "strategy": "engine_exact_source_global",
                "requested_variable_name": requested,
                "variable_name": requested,
                "source_line": cparams.get("source_line"),
            }
            resolved_names.add(requested)

        available = [
            candidate for candidate in detected
            if str(candidate.parameters.get("variable_name") or "").strip() not in resolved_names
        ]
        if not unresolved or len(unresolved) != len(available):
            return

        unresolved.sort(
            key=lambda item: (
                int(item[1].parameters.get("source_line") or 10**9),
                item[0],
            )
        )
        available.sort(
            key=lambda action: int(action.parameters.get("source_line") or 10**9)
        )
        for (_, action, requested), candidate in zip(unresolved, available):
            if requested and requested.lower() not in generic_names:
                continue
            params = action.parameters
            cparams = candidate.parameters
            resolved = str(cparams.get("variable_name") or "").strip()
            if not resolved:
                continue
            requested_getter = str(params.get("getter_name") or "").strip()
            requested_setter = str(params.get("setter_name") or "").strip()
            action.action_type = ACTION_ENCAPSULATE_C_VARIABLE
            params["requested_variable_name"] = requested
            params["variable_name"] = resolved
            params["source_file"] = str(params.get("source_file") or file_name)
            params["source_line"] = cparams.get("source_line")
            params["getter_name"] = (
                str(cparams.get("getter_name") or f"get_{resolved}")
                if not requested_getter or requested_getter.lower() in {"get_variable", "get_global", "get_var"}
                else requested_getter
            )
            params["setter_name"] = (
                str(cparams.get("setter_name") or f"set_{resolved}")
                if not requested_setter or requested_setter.lower() in {"set_variable", "set_global", "set_var"}
                else requested_setter
            )
            params["_c_global_target_resolution"] = {
                "status": "success",
                "strategy": "engine_declaration_order_placeholder_recovery",
                "requested_variable_name": requested,
                "variable_name": resolved,
                "source_line": cparams.get("source_line"),
            }

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
        if language == "c":
            # Resolve generic RDP Global Variable placeholders before the first
            # action executes.  This is deliberately an engine-level fallback
            # so direct engine callers cannot bypass the recovery performed by
            # SafeCodeTransformationValidationAgent.
            self._resolve_c_encapsulate_actions_from_source(
                source_code,
                actions,
                file_name=current_file_name,
            )
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
                anchor = python_transformers.resolve_dead_code_target(
                    source_code,
                    method_name=method_name,
                    class_name=class_name,
                    source_line=source_line,
                )
                dead_code_anchors[action_index] = anchor
                anchor_kind, anchor_fingerprint = anchor
                if anchor_kind == "unused_callable" and anchor_fingerprint:
                    resolved_method, resolved_class = (
                        python_transformers.resolve_dead_code_callable_target(
                        source_code,
                        anchor_fingerprint,
                        )
                    )
                    if resolved_method and (
                        resolved_method != method_name
                        or resolved_class != (class_name or "")
                    ):
                        action.parameters["requested_dead_code_method"] = method_name
                        action.parameters["requested_dead_code_class"] = class_name or ""
                        action.parameters["method"] = resolved_method
                        action.parameters["class_name"] = resolved_class
                        action.parameters["dead_code_target_resolution"] = (
                            "owner_inferred_from_ast"
                            if resolved_method == method_name and not class_name
                            else "stale_target_recovered_from_ast"
                        )
                exception_action = self._exception_action_at_source_line(
                    language=language,
                    source_code=source_code,
                    source_line=source_line,
                )
                if exception_action is not None:
                    legacy_exception_anchors[action_index] = exception_action

        execution_actions = list(enumerate(actions, start=1))
        dependency_resolutions: Dict[int, Dict[str, Any]] = {}
        if language == "java":
            reordered: list[tuple[int, RefactoringAction]] = []
            cursor = 0
            while cursor < len(execution_actions):
                extract_index, extract_action = execution_actions[cursor]
                if (
                    extract_action.action_type == ACTION_EXTRACT_METHOD
                    and cursor + 1 < len(execution_actions)
                ):
                    parameter_index, parameter_action = execution_actions[cursor + 1]
                    if (
                        parameter_action.action_type in {
                            ACTION_INTRODUCE_PARAMETER_OBJECT,
                            ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT,
                        }
                        and _same_java_method_target(extract_action, parameter_action)
                    ):
                        try:
                            _, _, blocked_metadata = java_extract_method.apply_extract_method(
                                source_code,
                                **_java_extract_action_kwargs(
                                    extract_action,
                                    current_file_name=current_file_name,
                                    project_source_files=project_source_files,
                                ),
                            )
                            parameterized_code, parameter_count, parameter_metadata = (
                                java_parameter_object.apply_introduce_parameter_object(
                                    source_code,
                                    method=_action_method_name(parameter_action),
                                    parameter_object_name=str(
                                        parameter_action.parameters.get("parameter_object_name")
                                        or parameter_action.parameters.get("new_class_name")
                                        or parameter_action.parameters.get("parameter_class_name")
                                        or ""
                                    ).strip(),
                                    source_class=_action_source_class(parameter_action),
                                    source_file=str(
                                        parameter_action.parameters.get("source_file") or ""
                                    ),
                                    current_file_name=current_file_name,
                                    parameter_name=str(
                                        parameter_action.parameters.get("parameter_name")
                                        or "params"
                                    ).strip(),
                                    project_source_files=project_source_files,
                                    source_resolution_error=str(
                                        parameter_action.parameters.get("source_resolution_error")
                                        or ""
                                    ),
                                )
                            )
                            _, retry_count, retry_metadata = (
                                java_extract_method.apply_extract_method(
                                    parameterized_code,
                                    **_java_extract_action_kwargs(
                                        extract_action,
                                        current_file_name=current_file_name,
                                        project_source_files=project_source_files,
                                    ),
                                )
                            )
                        except (TypeError, ValueError):
                            blocked_metadata = {}
                            parameter_count = 0
                            parameter_metadata = {}
                            retry_count = 0
                            retry_metadata = {}
                        if (
                            blocked_metadata.get("reason") == "TOO_MANY_PARAMETERS"
                            and parameter_count == 1
                            and parameter_metadata.get("status") == "success"
                            and retry_count == 1
                            and retry_metadata.get("status") == "success"
                        ):
                            resolution = {
                                "initial_status": "DEFERRED_DEPENDENCY",
                                "blocking_reason": "TOO_MANY_PARAMETERS",
                                "dependency_action_index": parameter_index,
                                "dependency_action_type": parameter_action.action_type,
                                "retry_status": "PROVEN_SAFE",
                                "target_method": _action_method_name(extract_action),
                                "target_class": _action_source_class(extract_action),
                            }
                            dependency_resolutions[extract_index] = resolution
                            dependency_resolutions[parameter_index] = {
                                "unblocks_action_index": extract_index,
                                "unblocks_action_type": ACTION_EXTRACT_METHOD,
                                "target_method": _action_method_name(extract_action),
                            }
                            reordered.extend([
                                (parameter_index, parameter_action),
                                (extract_index, extract_action),
                            ])
                            cursor += 2
                            continue
                reordered.append((extract_index, extract_action))
                cursor += 1
            execution_actions = reordered

        for idx, action in execution_actions:
            warnings = list(action.warnings)
            replacements = 0
            action_metadata: Dict[str, Any] = {}
            inline_action_trace: Dict[str, Any] = {}
            before_action_code = current_code

            try:
                # Inline Class is order-sensitive.  An earlier Move Method can
                # change the target's responsibility without changing its
                # class symbol.  Never let a preflight stale-file diagnostic
                # suppress an explicit class that still exists in the current
                # transformed AST.
                if language == "python" and action.action_type == ACTION_INLINE_PYTHON_CLASS:
                    requested_target = action.parameters.get("requested_target")
                    requested_target = (
                        requested_target if isinstance(requested_target, dict) else {}
                    )
                    requested_inline_class = str(
                        action.parameters.get("class_to_inline")
                        or action.parameters.get("target_class")
                        or action.parameters.get("source_class")
                        or requested_target.get("class_to_inline")
                        or ""
                    ).strip()
                    try:
                        current_classes = sorted(
                            (
                                python_transformers.build_python_symbol_table(current_code)
                                .get("classes", {})
                                .keys()
                            )
                        )
                    except (SyntaxError, TypeError, ValueError):
                        current_classes = []
                    inline_action_trace = {
                        "action_type": ACTION_INLINE_PYTHON_CLASS,
                        "raw_parameters": dict(action.parameters),
                        "raw_target": dict(requested_target),
                        "requested_target": {
                            "class_to_inline": requested_inline_class,
                            "source_file": str(
                                action.parameters.get("source_file")
                                or requested_target.get("source_file")
                                or current_file_name
                            ),
                        },
                        "normalized_target_class": requested_inline_class,
                        "source_file": str(
                            action.parameters.get("source_file") or current_file_name
                        ),
                        "current_classes_before": current_classes,
                        "current_ast_version": idx,
                    }
                    if requested_inline_class:
                        recovered_stale_inline_target = any(
                            action.parameters.get(key) is True
                            for key in (
                                "not_applicable_to_source",
                                "unresolved_legacy_target",
                            )
                        ) or bool(
                            action.parameters.get("source_resolution_error")
                            or action.parameters.get("source_file_resolution_error")
                        )
                        current_resolution = python_transformers.resolve_inline_class_target(
                            current_code,
                            class_to_inline=requested_inline_class,
                        )
                        if (
                            current_resolution.get("status") == "success"
                            and str(current_resolution.get("class_to_inline") or "")
                            == requested_inline_class
                        ):
                            resolved_inline_class = str(
                                current_resolution["class_to_inline"]
                            )
                            action.parameters.pop("not_applicable_to_source", None)
                            action.parameters.pop("not_applicable_reason", None)
                            action.parameters.pop("not_applicable_action_type", None)
                            action.parameters.pop("unresolved_legacy_target", None)
                            action.parameters.pop("unresolved_reason", None)
                            action.parameters.pop("source_resolution_error", None)
                            action.parameters.pop("source_file_resolution_error", None)
                            action.parameters.pop("target_resolution_error", None)
                            action.parameters["class_to_inline"] = resolved_inline_class
                            action.parameters["target_class"] = resolved_inline_class
                            if recovered_stale_inline_target:
                                action.parameters["target_resolution"] = (
                                    "current_transformed_ast_explicit_target"
                                )
                                action.parameters["inline_target_resolution"] = (
                                    "current_transformed_ast_explicit_target"
                                )
                                inline_action_trace["preflight_target_recovered"] = True
                            inline_action_trace["normalized_target_class"] = (
                                resolved_inline_class
                            )
                            inline_action_trace["target_resolution_strategy"] = (
                                "current_transformed_ast_explicit_target"
                            )
                        else:
                            inline_action_trace["target_resolution_strategy"] = str(
                                current_resolution.get("target_resolution")
                                or "current_transformed_ast_not_resolved"
                            )
                    else:
                        inline_action_trace["target_resolution_strategy"] = (
                            "missing_canonical_target"
                        )
                # Stale/filename-derived RDP targets that do not exist in this
                # source are plan diagnostics, not transformation failures.
                # This guard runs before language-specific dispatch so a
                # not-applicable Extract/Move/Inline action cannot accidentally
                # execute with invalid target metadata.
                if action.parameters.get("not_applicable_to_source") is True:
                    action_metadata = {
                        "status": "not_applicable",
                        "reason": str(
                            action.parameters.get("not_applicable_reason")
                            or action.parameters.get("source_resolution_error")
                            or "TARGET_NOT_FOUND_IN_SOURCE"
                        ),
                        "requested_action_type": str(
                            action.parameters.get("not_applicable_action_type")
                            or action.action_type
                        ),
                        "source_file": str(action.parameters.get("source_file") or ""),
                        "target_resolution": "stale_rdp_target_guard",
                    }
                    # Do not add a human-facing warning here.  The detailed
                    # transformation log retains the reason and status.
                    warnings = []
                elif action.parameters.get("unresolved_legacy_target") is True:
                    unresolved_status = str(
                        action.parameters.get("unresolved_status") or "not_applicable"
                    ).strip().lower()
                    if unresolved_status not in {"not_applicable", "review_required"}:
                        unresolved_status = "review_required"
                    action_metadata = {
                        "status": unresolved_status,
                        "reason": str(
                            action.parameters.get("unresolved_reason")
                            or action.parameters.get("source_resolution_error")
                            or "TARGET_NOT_FOUND_IN_SOURCE"
                        ),
                        "requested_action_type": action.action_type,
                        "source_file": str(action.parameters.get("source_file") or ""),
                        "target_resolution": "failed_after_plan_and_source_analysis",
                    }
                    warnings.append(
                        f"RDP {action.action_type} target resolution requires review: "
                        f"{action_metadata['reason']}."
                    )
                elif action.action_type == ACTION_NOOP:
                    # Older/upstream PlannerAdapter versions may already have
                    # converted a malformed Feature-Envy Move Method step to
                    # noop before the request reaches this engine.  Recover it
                    # only when the original refactoring label proves that this
                    # noop came from Move Method and the Python AST identifies
                    # one unambiguous safe target.
                    source_refactoring = str(action.source_refactoring or "").strip().lower()
                    warning_text = " ".join(str(item) for item in action.warnings).lower()
                    is_move_method_noop = (
                        source_refactoring in {"move method", "feature envy"}
                        or "move method" in warning_text
                    )

                    if action.parameters.get("not_applicable_to_source") is True:
                        action_metadata = {
                            "status": "not_applicable",
                            "reason": str(
                                action.parameters.get("not_applicable_reason")
                                or "TARGET_NOT_FOUND_IN_SOURCE"
                            ),
                            "original_action_type": str(
                                action.parameters.get("not_applicable_action_type")
                                or action.source_refactoring
                                or "unknown"
                            ),
                        }
                    elif language == "python" and is_move_method_noop:
                        source_line = action.parameters.get("source_line")
                        source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                        resolution = python_transformers.resolve_move_method_target(
                            current_code,
                            method_name=str(action.parameters.get("method") or ""),
                            source_class=str(action.parameters.get("source_class") or ""),
                            destination_class=str(action.parameters.get("destination_class") or ""),
                            destination_parameter=str(action.parameters.get("destination_parameter") or ""),
                            source_line=source_line,
                        )
                        if resolution.get("status") == "success":
                            effective_params = {
                                "method": str(resolution["method"]),
                                "source_class": str(resolution["source_class"]),
                                "destination_class": str(resolution["destination_class"]),
                                "destination_parameter": str(resolution["destination_parameter"]),
                                "source_file": str(action.parameters.get("source_file") or ""),
                            }
                            current_code, replacements, move_metadata = python_transformers.apply_move_method(
                                current_code,
                                method_name=effective_params["method"],
                                source_class=effective_params["source_class"],
                                destination_class=effective_params["destination_class"],
                                destination_parameter=effective_params["destination_parameter"],
                                source_line=source_line,
                            )
                            action_metadata = dict(move_metadata)
                            validation_evidence = action_metadata.get("move_method_validation_evidence")
                            if isinstance(validation_evidence, dict):
                                effective_params["move_method_validation_evidence"] = dict(
                                    validation_evidence
                                )
                            action_metadata["reclassified_action_type"] = ACTION_MOVE_PYTHON_METHOD
                            action_metadata["effective_action_parameters"] = effective_params
                            action_metadata["recovered_from_noop"] = True
                            action_metadata["noop_target_resolution"] = resolution
                            if action_metadata.get("status") == "success":
                                warnings = [
                                    warning
                                    for warning in warnings
                                    if not (
                                        "move method" in str(warning).lower()
                                        and (
                                            "richer semantic edits" in str(warning).lower()
                                            or "not simulated with a rename" in str(warning).lower()
                                            or "mapped to noop" in str(warning).lower()
                                        )
                                    )
                                ]
                                warnings.append(
                                    "Recovered malformed Move Method noop from AST evidence: "
                                    f"{effective_params['source_class']}.{effective_params['method']} -> "
                                    f"{effective_params['destination_class']}."
                                )
                            else:
                                warnings.append(
                                    "Recovered Move Method target but transformation still requires review: "
                                    f"{action_metadata.get('reason', 'unsafe move candidate')}."
                                )
                        else:
                            warnings.append(
                                "Move Method noop could not be safely recovered from source AST: "
                                f"{resolution.get('reason', 'target not found')}."
                            )
                    else:
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

                elif action.action_type == ACTION_RENAME_METHOD:
                    old_name = str(
                        action.parameters.get("old_name")
                        or action.parameters.get("method")
                        or action.parameters.get("method_name")
                        or ""
                    ).strip()
                    new_name = str(
                        action.parameters.get("new_name")
                        or action.parameters.get("new_method_name")
                        or action.parameters.get("renamed_to")
                        or ""
                    ).strip()
                    source_class = str(action.parameters.get("source_class") or "").strip()
                    raw_parameter_types = action.parameters.get("parameter_types") or action.parameters.get("param_types")
                    parameter_types = [
                        str(item).strip()
                        for item in raw_parameter_types
                        if str(item).strip()
                    ] if isinstance(raw_parameter_types, list) else None
                    if not old_name or not new_name:
                        raise ValueError("rename_method requires 'old_name' and 'new_name'.")
                    if language == "java":
                        current_code, replacements, rename_metadata = java_transformers.apply_rename_method(
                            current_code,
                            old_name,
                            new_name,
                            source_class=source_class,
                            parameter_types=parameter_types,
                        )
                    elif language == "python":
                        current_code, replacements, rename_metadata = python_transformers.apply_rename_method(
                            current_code,
                            old_name,
                            new_name,
                            source_class=source_class,
                        )
                    else:
                        raise ValueError("rename_method is currently implemented for Python and Java source files.")
                    action_metadata.update(rename_metadata)
                    if str(rename_metadata.get("status") or "").lower() == "review_required":
                        warnings.append(
                            f"Rename Method requires review: {rename_metadata.get('reason', 'UNKNOWN')}."
                        )
                    elif str(rename_metadata.get("status") or "").lower() == "not_applicable":
                        warnings.append(
                            f"Rename Method is not applicable to this source: {rename_metadata.get('reason', 'UNKNOWN')}."
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
                            project_source_files=project_source_files,
                        )
                    else:
                        raise ValueError(
                            f"extract_method is not supported for language '{language}'."
                        )

                    target_resolution = str(
                        action.parameters.get("method_target_resolution") or ""
                    ).strip()
                    if target_resolution:
                        action_metadata["method_target_resolution"] = target_resolution

                    if action_metadata.get("status") == "review_required":
                        warnings.append(
                            "Extract Method requires review: "
                            f"{action_metadata.get('reason', 'unsafe extraction candidate')}."
                        )

                elif action.action_type == ACTION_MOVE_PYTHON_METHOD:
                    if language != "python":
                        raise ValueError("move_python_method requires a Python source file.")
                    method_name = str(action.parameters.get("method") or "").strip()
                    source_class = str(action.parameters.get("source_class") or "").strip()
                    destination_class = str(action.parameters.get("destination_class") or "").strip()
                    destination_parameter = str(
                        action.parameters.get("destination_parameter") or ""
                    ).strip()
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    current_code, replacements, action_metadata = python_transformers.apply_move_method(
                        current_code,
                        method_name=method_name,
                        source_class=source_class,
                        destination_class=destination_class,
                        destination_parameter=destination_parameter,
                        source_line=source_line,
                    )
                    # Keep the semantically resolved target in the action so
                    # downstream structural validation sees the operation that
                    # was actually applied rather than stale planner hints.
                    move_status = str(action_metadata.get("status") or "").lower()
                    if move_status in {"success", "already_applied"}:
                        effective_params = {
                            "method": str(action_metadata.get("method") or method_name),
                            "source_class": str(action_metadata.get("source_class") or source_class),
                            "destination_class": str(action_metadata.get("destination_class") or destination_class),
                            "destination_parameter": str(
                                action_metadata.get("destination_parameter") or destination_parameter
                            ),
                            "source_file": str(action.parameters.get("source_file") or ""),
                        }
                        validation_evidence = action_metadata.get("move_method_validation_evidence")
                        if isinstance(validation_evidence, dict):
                            effective_params["move_method_validation_evidence"] = dict(
                                validation_evidence
                            )
                        action_metadata["reclassified_action_type"] = ACTION_MOVE_PYTHON_METHOD
                        action_metadata["effective_action_parameters"] = effective_params
                    if move_status not in {"success", "already_applied"}:
                        warnings.append(
                            "Feature Envy Move Method requires review: "
                            f"{action_metadata.get('reason', 'unsafe move candidate')}."
                        )
                    elif move_status == "already_applied":
                        warnings = [
                            warning
                            for warning in warnings
                            if not (
                                "move method" in str(warning).lower()
                                and (
                                    "richer semantic edits" in str(warning).lower()
                                    or "not simulated with a rename" in str(warning).lower()
                                    or "mapped to noop" in str(warning).lower()
                                )
                            )
                        ]
                        warnings.append(
                            f"Feature Envy Move Method already applied: {method_name} is already on "
                            f"{destination_class}."
                        )
                    else:
                        # Older planner versions attached a warning saying Move
                        # Method was only simulated/no-op.  Once the real
                        # semantic transformation succeeds, keeping that stale
                        # warning makes the safety report contradict itself.
                        warnings = [
                            warning
                            for warning in warnings
                            if not (
                                "move method" in str(warning).lower()
                                and (
                                    "richer semantic edits" in str(warning).lower()
                                    or "not simulated with a rename" in str(warning).lower()
                                    or "mapped to noop" in str(warning).lower()
                                )
                            )
                        ]
                        warnings.append(
                            f"Feature Envy Move Method applied: {method_name} moved from "
                            f"{source_class} to {destination_class}."
                        )

                elif action.action_type == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM:
                    if language != "python":
                        action_metadata = {
                            "status": "review_required",
                            "reason": "POLYMORPHISM_REFACTORING_CURRENTLY_REQUIRES_PYTHON",
                        }
                    else:
                        method_name = str(
                            action.parameters.get("method")
                            or action.parameters.get("method_name")
                            or action.parameters.get("target_method")
                            or ""
                        ).strip()
                        source_class = str(
                            action.parameters.get("source_class")
                            or action.parameters.get("target_class")
                            or ""
                        ).strip()

                        def optional_line(name: str) -> int | None:
                            value = action.parameters.get(name)
                            if isinstance(value, str) and value.strip().isdigit():
                                value = int(value.strip())
                            return int(value) if isinstance(value, (int, float)) else None

                        current_code, replacements, action_metadata = (
                            python_replace_conditional.apply_replace_conditional_with_polymorphism(
                                current_code,
                                method_name=method_name,
                                source_class=source_class,
                                source_line=optional_line("source_line"),
                                start_line=optional_line("start_line"),
                                end_line=optional_line("end_line"),
                                base_class_name=str(
                                    action.parameters.get("base_class_name") or ""
                                ).strip(),
                            )
                        )
                    if action_metadata.get("status") == "success":
                        action_metadata["reclassified_action_type"] = (
                            ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
                        )
                        effective_parameters = action_metadata.get("effective_action_parameters")
                        if isinstance(effective_parameters, dict):
                            effective_parameters["source_file"] = str(
                                action.parameters.get("source_file") or current_file_name
                            )
                        warnings.append(
                            "Replace Conditional with Polymorphism applied: "
                            f"{action_metadata.get('method')} now dispatches through "
                            f"{action_metadata.get('branch_count')} strategy subclasses."
                        )
                    else:
                        warnings.append(
                            "Replace Conditional with Polymorphism requires review: "
                            f"{action_metadata.get('reason') or 'unsafe conditional hierarchy'}."
                        )

                elif action.action_type == ACTION_INLINE_PYTHON_CLASS:
                    if language != "python":
                        raise ValueError("inline_python_class requires a Python source file.")
                    class_to_inline = str(
                        action.parameters.get("class_to_inline")
                        or action.parameters.get("source_class")
                        or ""
                    ).strip()

                    # Prefer the true Fowler-style owned-composition Inline
                    # Class strategy when a tiny helper is uniquely owned by
                    # another class (for example Customer -> CustomerContact).
                    # If that pattern is not present, fall back to the existing
                    # module-function strategy so previous supported fixtures
                    # continue to work.
                    owned_code, owned_replacements, owned_metadata = (
                        python_inline_class.apply_owned_inline_class(
                            current_code,
                            class_to_inline=class_to_inline,
                            preferred_destination_class=str(
                                action.parameters.get("destination_class") or ""
                            ).strip(),
                            preferred_owner_attribute=str(
                                action.parameters.get("owner_attribute") or ""
                            ).strip(),
                        )
                    )
                    if owned_metadata.get("status") == "not_applicable":
                        prior_transformations = [
                            {
                                "action_type": str(
                                    entry.metadata.get("reclassified_action_type")
                                    or entry.action_type
                                ),
                                "status": str(entry.metadata.get("status") or "success"),
                                "source_class": str(entry.metadata.get("source_class") or ""),
                                "destination_class": str(entry.metadata.get("destination_class") or ""),
                                "method": str(entry.metadata.get("method") or ""),
                                "class_removed": bool(entry.metadata.get("class_removed") is True),
                            }
                            for entry in logs
                            if str(entry.metadata.get("status") or "success").lower()
                            in {"success", "already_applied"}
                        ]
                        current_code, replacements, action_metadata = python_transformers.apply_inline_class(
                            current_code,
                            class_to_inline=class_to_inline,
                            prior_transformations=prior_transformations,
                            project_source_files=project_source_files or [],
                            current_file_name=current_file_name,
                        )
                    else:
                        current_code = owned_code
                        replacements = owned_replacements
                        action_metadata = owned_metadata

                    if action_metadata.get("status") == "success":
                        action_metadata["reclassified_action_type"] = ACTION_INLINE_PYTHON_CLASS
                        action_metadata["plan_compliance"] = "PASS"
                        effective_parameters = {
                            "class_to_inline": str(
                                action_metadata.get("class_to_inline") or class_to_inline
                            ),
                            "source_file": str(action.parameters.get("source_file") or ""),
                        }
                        destination_class = str(
                            action_metadata.get("destination_class")
                            or action.parameters.get("destination_class")
                            or ""
                        ).strip()
                        owner_attribute = str(
                            action_metadata.get("owner_attribute")
                            or action.parameters.get("owner_attribute")
                            or ""
                        ).strip()
                        if destination_class:
                            effective_parameters["destination_class"] = destination_class
                        if owner_attribute:
                            effective_parameters["owner_attribute"] = owner_attribute
                        if action_metadata.get("inline_mode"):
                            effective_parameters["inline_mode"] = str(
                                action_metadata.get("inline_mode")
                            )
                        if action_metadata.get("class_was_empty") is True:
                            effective_parameters["class_was_empty"] = True
                        if action_metadata.get("strategy"):
                            effective_parameters["strategy"] = str(
                                action_metadata.get("strategy")
                            )
                        action_metadata["effective_action_parameters"] = effective_parameters

                        if action_metadata.get("inline_mode") == "empty_class_cleanup":
                            warnings.append(
                                "Inline Class cleanup applied: "
                                f"removed proven-unused empty class {class_to_inline}."
                            )
                        elif action_metadata.get("inline_mode") == "owner_class":
                            warnings.append(
                                "Inline Class applied: "
                                f"{class_to_inline} was inlined into "
                                f"{destination_class or 'its owning class'}."
                            )
                        else:
                            warnings.append(
                                f"Inline Class applied: {class_to_inline} was safely inlined."
                            )
                    elif action_metadata.get("status") in {"not_applicable", "satisfied"}:
                        # A previous safe transformation can make a planned
                        # Lazy Class target meaningful.  Keep this outcome in
                        # the log without incorrectly presenting it as an
                        # unsafe Inline Class failure.
                        if action_metadata.get("reason") == "SMELL_RESOLVED_BY_PRIOR_REFACTORING":
                            action_metadata["reclassified_action_type"] = ACTION_INLINE_PYTHON_CLASS
                            action_metadata["class_exists"] = True
                            action_metadata["class_removed"] = False
                            action_metadata["outcome_status"] = "satisfied"
                            action_metadata["plan_compliance"] = "PASS"
                            action_metadata["effective_action_parameters"] = {
                                "class_to_inline": class_to_inline,
                                "source_file": str(action.parameters.get("source_file") or ""),
                                "inline_mode": "satisfied_by_prior_refactoring",
                                "reason": "SMELL_RESOLVED_BY_PRIOR_REFACTORING",
                                "class_exists": True,
                                "class_removed": False,
                                "prior_transformations": list(
                                    action_metadata.get("prior_transformations") or []
                                ),
                            }
                            warnings.append(
                                "Inline Class satisfied by prior refactoring: "
                                f"{class_to_inline} now has meaningful responsibility."
                            )
                    else:
                        warnings.append(
                            "Inline Class requires review: "
                            f"{action_metadata.get('reason', 'unsafe inline candidate')}."
                        )
                    action_metadata.setdefault(
                        "target_resolution",
                        str(
                            action.parameters.get("target_resolution")
                            or action.parameters.get("inline_target_resolution")
                            or "transformer_input"
                        ),
                    )

                elif action.action_type == ACTION_HIDE_DELEGATE:
                    source_class = str(action.parameters.get("source_class") or "").strip()
                    delegate_member = str(action.parameters.get("delegate_member") or "").strip()
                    delegated_member = str(action.parameters.get("delegated_member") or "").strip()
                    new_method_name = str(action.parameters.get("new_method_name") or "").strip()
                    if not all((source_class, delegate_member, delegated_member)):
                        action_metadata = {
                            "status": "review_required",
                            "reason": str(
                                action.parameters.get("source_resolution_error")
                                or "MISSING_HIDE_DELEGATE_TARGET"
                            ),
                            "missing": [
                                name
                                for name, value in (
                                    ("source_class", source_class),
                                    ("delegate_member", delegate_member),
                                    ("delegated_member", delegated_member),
                                )
                                if not value
                            ],
                        }
                    elif language == "python":
                        current_code, replacements, action_metadata = python_hide_delegate.apply_hide_delegate(
                            current_code,
                            source_class=source_class,
                            delegate_member=delegate_member,
                            delegated_member=delegated_member,
                            new_method_name=new_method_name,
                        )
                    elif language == "java":
                        current_code, replacements, action_metadata = java_hide_delegate.apply_hide_delegate(
                            current_code,
                            source_class=source_class,
                            delegate_member=delegate_member,
                            delegated_member=delegated_member,
                            new_method_name=new_method_name,
                        )
                    else:
                        action_metadata = {
                            "status": "review_required",
                            "reason": "HIDE_DELEGATE_UNSUPPORTED_FOR_C",
                        }
                    if action_metadata.get("status") == "success":
                        action_metadata["reclassified_action_type"] = ACTION_HIDE_DELEGATE
                        warnings.append(
                            "Hide Delegate applied: "
                            f"{source_class}.{new_method_name or action_metadata.get('new_method_name')} now hides {delegate_member}."
                        )
                    else:
                        warnings.append(
                            "Hide Delegate requires review: "
                            f"{action_metadata.get('reason') or 'unsafe message chain'}."
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
                        java_state = {
                            "repository_original_code": source_code,
                            "prior_transformations": self._accepted_action_history(logs),
                        }
                        current_code, replacements, action_metadata = java_extract_class.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                            **java_state,
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
                        java_state = {
                            "repository_original_code": source_code,
                            "prior_transformations": self._accepted_action_history(logs),
                        }
                        current_code, replacements, action_metadata = java_extract_class.apply_extract_class(
                            current_code,
                            **extract_kwargs,
                            **java_state,
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
                            action_metadata["message"] = (
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
                    if action.action_type == ACTION_INTRODUCE_C_PARAMETER_OBJECT and language != "c":
                        raise ValueError("introduce_c_parameter_object requires a C source file.")
                    if language == "java":
                        current_code, replacements, action_metadata = (
                            java_parameter_object.apply_introduce_parameter_object(current_code, **kwargs)
                        )
                    elif language == "python":
                        current_code, replacements, action_metadata = (
                            python_parameter_object.apply_introduce_parameter_object(current_code, **kwargs)
                        )
                    elif language == "c":
                        current_code, replacements, action_metadata = (
                            c_transformers.apply_introduce_parameter_object(current_code, **kwargs)
                        )
                    else:
                        action_metadata = {
                            "status": "not_applicable",
                            "reason": "UNSUPPORTED_LANGUAGE",
                            "language": language,
                            "refactoring": "Introduce Parameter Object",
                            "supported_languages": ["java", "python", "c"],
                            "plan_compliance": "FAIL",
                            "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
                        }
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

                elif action.action_type in {
                    "replace_nested_conditional_with_guard_clauses",
                    "replace_conditional_with_guard_clauses",
                    "simplify_conditional_loop",
                    "guard_clauses",
                }:
                    method_name = str(action.parameters.get("method") or action.parameters.get("target_method") or "").strip()
                    source_line = action.parameters.get("source_line")
                    source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                    if language == "c":
                        current_code, replacements, action_metadata = c_transformers.apply_replace_nested_conditional_with_guard_clauses(
                            current_code,
                            method_name,
                            source_line,
                        )
                        if replacements == 0:
                            warnings.append(
                                "Replace Nested Conditional with Guard Clauses: Review Required (cannot be safely transformed automatically)."
                            )
                        else:
                            warnings.append(
                                f"Replace Nested Conditional with Guard Clauses applied to function {method_name or 'target'}."
                            )

                elif action.action_type in {ACTION_ENCAPSULATE_VARIABLE, ACTION_ENCAPSULATE_C_VARIABLE}:
                    variable_name = str(action.parameters.get("variable_name") or "").strip()
                    getter_name = str(action.parameters.get("getter_name") or f"get_{variable_name}").strip()
                    setter_name = str(action.parameters.get("setter_name") or f"set_{variable_name}").strip()
                    if not variable_name:
                        raise ValueError("encapsulate_c_variable requires 'variable_name'.")
                    if language != "c":
                        warnings.append("encapsulate_c_variable is currently supported for C source only.")
                    else:
                        current_code, replacements, action_metadata = c_transformers.apply_encapsulate_c_variable(
                            current_code,
                            variable_name=variable_name,
                            getter_name=getter_name,
                            setter_name=setter_name,
                        )
                        target_resolution = action.parameters.get(
                            "_c_global_target_resolution"
                        ) or action.parameters.get("target_resolution")
                        if isinstance(target_resolution, dict):
                            action_metadata["target_resolution"] = dict(target_resolution)
                            action_metadata["requested_variable_name"] = str(
                                action.parameters.get("requested_variable_name")
                                or target_resolution.get("requested_variable_name")
                                or variable_name
                            )
                        if isinstance(action_metadata.get("effective_action_parameters"), dict):
                            action_metadata["effective_action_parameters"].update({
                                "source_file": str(action.parameters.get("source_file") or current_file_name),
                                "source_line": action.parameters.get("source_line"),
                            })
                        if action_metadata.get("status") == "success":
                            action_metadata["reclassified_action_type"] = ACTION_ENCAPSULATE_C_VARIABLE
                            warnings.append(
                                f"C Global Variable encapsulated: {variable_name} now uses {getter_name}"
                                + (f"/{setter_name}." if action_metadata.get("writable") else ".")
                            )
                        else:
                            warnings.append(
                                "C Encapsulate Variable requires review: "
                                f"{action_metadata.get('reason') or 'unsafe global access pattern'}."
                            )

                elif action.action_type == ACTION_MOVE_C_FUNCTION:
                    # Moving C functions changes at least two translation units
                    # and usually a header. This single-file engine must not
                    # fabricate partial cross-file edits; a project transaction
                    # is required before it can be safely applied.
                    action_metadata = {
                        "status": "review_required",
                        "reason": "PROJECT_MULTI_FILE_MOVE_FUNCTION_REQUIRED",
                        "function_name": str(action.parameters.get("function_name") or ""),
                        "source_file": str(action.parameters.get("source_file") or ""),
                        "destination_file": str(action.parameters.get("destination_file") or ""),
                    }
                    warnings.append(
                        "C Move Method was normalized to Move Function and requires an explicit project-level multi-file transaction."
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
                    dead_code_target_resolution = str(
                        action.parameters.get("dead_code_target_resolution") or ""
                    ).strip()
                    if dead_code_target_resolution:
                        action_metadata["dead_code_target_resolution"] = (
                            dead_code_target_resolution
                        )
                        action_metadata["requested_dead_code_method"] = str(
                            action.parameters.get("requested_dead_code_method") or ""
                        )
                        action_metadata["requested_dead_code_class"] = str(
                            action.parameters.get("requested_dead_code_class") or ""
                        )
                        action_metadata["resolved_dead_code_method"] = method_name
                        action_metadata["resolved_dead_code_class"] = str(class_name or "")
                    target_statement_fingerprint = str(
                        action.parameters.get("target_statement_fingerprint") or ""
                    ).strip()
                    if language == "python" and not target_statement_fingerprint:
                        anchor_kind, anchor_fingerprint = dead_code_anchors.get(idx - 1, ("", ""))
                        dead_code_kind = dead_code_kind or anchor_kind
                        target_statement_fingerprint = anchor_fingerprint

                    if language != "c" and not method_name and source_line is None:
                        raise ValueError("remove_dead_code requires 'method' or 'source_line'.")

                    if class_name is not None:
                        class_name = str(class_name).strip() or None

                    legacy_exception_action = legacy_exception_anchors.get(idx - 1)
                    if language == "python" and dead_code_kind == "dynamic_callable":
                        action_metadata.update({
                            "dead_code_target_status": "dynamic_reference",
                            "dead_code_target_kind": "dynamic_callable",
                            "status": "review_required",
                            "final_decision": "REVIEW_REQUIRED",
                            "reason": (
                                "Remove Dead Code target may be resolved dynamically; "
                                "SCTVA preserved it because reachability cannot be proven safely."
                            ),
                        })
                    elif language == "python" and dead_code_kind == "live_callable":
                        # The plan targeted a real, referenced method/function.
                        # Removing it would be behavior-changing, so treat this
                        # planner step as safely NOT_APPLICABLE rather than as a
                        # failed dead-code transformation.
                        action_metadata.update({
                            "dead_code_target_status": "live",
                            "dead_code_target_kind": "live_callable",
                            "status": "not_applicable",
                            "final_decision": "NOT_APPLICABLE",
                            "reason": (
                                "Planner Remove Dead Code target resolves to a live/referenced "
                                "callable; SCTVA intentionally preserved it."
                            ),
                        })
                    elif legacy_exception_action is not None:
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
                        named_c_target = bool(method_name)
                        c_analysis = c_transformers.analyze_c_dead_code_target(
                            current_code,
                            method_name,
                            source_line=source_line,
                            project_source_files=project_source_files,
                            current_file_name=current_file_name,
                        )
                        resolved_target = str(c_analysis.get("target") or method_name).strip()
                        if resolved_target and (
                            named_c_target or c_analysis.get("removable") is True
                        ):
                            method_name = resolved_target
                            action.parameters["method"] = resolved_target
                            action_metadata["target"] = resolved_target
                        action_metadata["c_dead_code_analysis"] = dict(c_analysis)
                        blocked_statuses = {
                            "live_reference",
                            "protected_entry_point",
                            "external_linkage",
                        }
                        if (
                            named_c_target
                            and str(c_analysis.get("status") or "") in blocked_statuses
                        ):
                            action_metadata.update({
                                "dead_code_target_status": "live",
                                "status": "not_applicable",
                                "final_decision": "NOT_APPLICABLE",
                                "reason": str(
                                    c_analysis.get("reason")
                                    or "C function is not proven dead and was preserved."
                                ),
                            })
                        else:
                            current_code, replacements = c_transformers.apply_remove_dead_code(
                                current_code,
                                method_name,
                                class_name,
                                source_line,
                                project_source_files=project_source_files,
                                current_file_name=current_file_name,
                                repository_complete=repository_complete,
                            )
                            if replacements:
                                action_metadata["target_removed"] = True
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

            dead_code_not_applicable = (
                action.action_type == ACTION_REMOVE_DEAD_CODE
                and str(action_metadata.get("status") or "").lower() == "not_applicable"
            )

            if (
                replacements == 0
                and action.action_type == ACTION_REMOVE_DEAD_CODE
                and not dead_code_not_applicable
            ):
                warnings.append(
                    "Dead-code removal skipped: SCTVA could not prove the target was unreachable "
                    "or an unused side-effect-free declaration."
                )

            if replacements == 0 and action.action_type == ACTION_NARROW_EXCEPTION_HANDLER:
                warnings.append(
                    "Exception handler narrowing skipped: SCTVA could not prove a unique safe target exception type."
                )

            source_file_resolution = action.parameters.get("source_file_resolution")
            if inline_action_trace:
                for key, value in inline_action_trace.items():
                    action_metadata.setdefault(key, value)
                action_metadata.setdefault(
                    "target_resolution_strategy",
                    str(
                        action_metadata.get("target_resolution")
                        or action.parameters.get("target_resolution")
                        or inline_action_trace.get("target_resolution_strategy")
                        or "transformer_input"
                    ),
                )
            if isinstance(source_file_resolution, dict):
                action_metadata.setdefault(
                    "source_file_resolution",
                    dict(source_file_resolution),
                )
                if source_file_resolution.get("status") == "success":
                    action_metadata.setdefault(
                        "source_file",
                        str(source_file_resolution.get("resolved") or ""),
                    )

            dependency_resolution = dependency_resolutions.get(idx)
            if dependency_resolution:
                action_metadata["dependency_resolution"] = dict(
                    dependency_resolution
                )
                action_metadata["execution_order_resolved"] = True

            if (
                language == "python"
                and action.action_type in {
                    ACTION_MOVE_PYTHON_METHOD,
                    ACTION_INLINE_PYTHON_CLASS,
                    ACTION_EXTRACT_CLASS,
                    ACTION_EXTRACT_PYTHON_CLASS,
                    ACTION_REMOVE_DEAD_CODE,
                }
                and str(action_metadata.get("status") or "").lower()
                in {"success", "already_applied", "not_applicable", "satisfied"}
            ):
                symbol_table = python_transformers.build_python_symbol_table(current_code)
                action_metadata["current_symbol_table"] = symbol_table
                action_metadata["current_ast_version"] = idx
                action_metadata.setdefault("source_file", current_file_name)
                if action.action_type == ACTION_MOVE_PYTHON_METHOD:
                    source_class_name = str(action_metadata.get("source_class") or "")
                    destination_class_name = str(action_metadata.get("destination_class") or "")
                    method_name = str(action_metadata.get("method") or "")
                    classes = symbol_table.get("classes") or {}
                    source_state = classes.get(source_class_name) or {}
                    action_metadata["affected_class"] = source_class_name
                    action_metadata["methods_removed"] = [method_name] if method_name else []
                    action_metadata["methods_added"] = [method_name] if method_name else []
                    action_metadata["source_class_became_empty"] = bool(
                        source_state.get("empty") is True
                    )
                    action_metadata["current_symbol_state"] = {
                        "source_class": source_state,
                        "destination_class": classes.get(destination_class_name) or {},
                    }

            if (
                replacements == 0
                and action.action_type != ACTION_NOOP
                and not dead_code_not_applicable
                and str(action_metadata.get("status") or "").lower() != "not_applicable"
                and str(action_metadata.get("status") or "").lower() != "already_applied"
                and str(action_metadata.get("status") or "").lower() != "satisfied"
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
            # ``not_applicable`` means the action was proven irrelevant to
            # this source (typically a stale/false-positive RDP target).  Keep
            # diagnostics in the per-action log but do not elevate them to the
            # Safety Report's transformation_warning risk.
            if str(action_metadata.get("status") or "").lower() not in {
                "not_applicable",
                "satisfied",
            }:
                global_warnings.extend(warnings)

        return current_code, logs, global_warnings

    @staticmethod
    def _accepted_action_history(
        logs: Sequence[TransformationLogEntry],
    ) -> list[Dict[str, Any]]:
        return [
            {
                "action_index": entry.action_index,
                "action_type": entry.action_type,
                "replacements_count": entry.replacements_count,
                "status": str(entry.metadata.get("status") or "success"),
                "reason": str(entry.metadata.get("reason") or ""),
            }
            for entry in logs
            if entry.replacements_count > 0
            and str(entry.metadata.get("status") or "success").lower()
            in {"success", "pass", "accepted", "already_applied"}
        ]
