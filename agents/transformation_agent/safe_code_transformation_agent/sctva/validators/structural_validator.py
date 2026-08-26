"""Structural similarity checks for transformed code."""

from __future__ import annotations

import ast
import copy
import re
import time
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

from ..constants import DEFAULT_STRUCTURAL_THRESHOLD_C, DEFAULT_STRUCTURAL_THRESHOLD_JAVA, DEFAULT_STRUCTURAL_THRESHOLD_PYTHON
from .c_support import compare_c_static_summaries, summarize_c_source
from ..models import ValidationStepResult
from ..contracts import RefactoringAction
from ..constants import (
    ACTION_ENCAPSULATE_C_VARIABLE,
    ACTION_ENCAPSULATE_VARIABLE,
    ACTION_HIDE_DELEGATE,
    ACTION_EXTRACT_METHOD,
    ACTION_INLINE_PYTHON_CLASS,
    ACTION_MOVE_PYTHON_METHOD,
    ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_RENAME_METHOD,
    ACTION_REMOVE_DEAD_CODE,
    PARAMETER_OBJECT_ACTIONS,
)
from ..transformers import c_transformers, java_transformers, python_transformers
from ..transformers import java_hide_delegate, python_hide_delegate, python_replace_conditional
from ..transformers.java_extract_class import _parse_java_class, declared_class_names
from ..transformers.python_move_method_validation import validate_python_move_method
from ..utils.io_helpers import utc_now_iso
from ..utils.metrics import (
    cosine_similarity_from_counts,
    count_tokens,
    jaccard_similarity,
    normalized_count_similarity,
)


class _PythonExtractMethodStructuralNormalizer(ast.NodeTransformer):
    """Treat SCTVA-introduced constants as their original literal values.

    Extract Method is checked after the whole RDP plan has run. A later
    Introduce Constant action may replace ``75`` inside the extracted helper
    with ``CONSTANT_75``. That is an intentional follow-up refactoring, not a
    loss of the extracted grading logic.
    """

    _CONSTANT_NAME = re.compile(r"^CONSTANT_[A-Za-z0-9_]+$")

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, (int, float, complex)) and not isinstance(node.value, bool):
            return ast.copy_location(
                ast.Name(id="__sctva_literal__", ctx=ast.Load()),
                node,
            )
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if self._CONSTANT_NAME.match(node.id):
            return ast.copy_location(
                ast.Name(id="__sctva_literal__", ctx=node.ctx),
                node,
            )
        return node


class _PythonMoveMethodStructuralNormalizer(ast.NodeTransformer):
    """Canonicalize a moved Python method for semantic structural comparison.

    Move Method intentionally changes references such as ``student.maths`` to
    ``self.maths``.  Later plan steps may also replace numeric literals inside
    the moved method with SCTVA module constants (for example ``35`` becomes
    ``CONSTANT_35``).  A raw ``ast.dump`` comparison therefore produces a
    false negative even when the method was moved correctly.

    This normalizer performs only the equivalence rewrites that SCTVA itself
    expects:

    * the destination parameter name becomes ``self``;
    * SCTVA-style module constant names are resolved back to their literal
      values before the methods are compared.

    Literal values are preserved exactly.  Therefore a wrong constant value
    still fails validation instead of being hidden by normalization.
    """

    _SCTVA_CONSTANT_NAME = re.compile(
        r"^(?:CONSTANT_|MAGIC_|EXTRACTED_CONSTANT)[A-Za-z0-9_]*$"
    )

    def __init__(
        self,
        destination_parameter: str = "",
        constant_values: Dict[str, Any] | None = None,
    ) -> None:
        self.destination_parameter = destination_parameter
        self.constant_values = dict(constant_values or {})

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if self.destination_parameter and node.id == self.destination_parameter:
            return ast.copy_location(
                ast.Name(id="self", ctx=node.ctx),
                node,
            )

        if (
            self._SCTVA_CONSTANT_NAME.match(node.id)
            and node.id in self.constant_values
        ):
            return ast.copy_location(
                ast.Constant(value=self.constant_values[node.id]),
                node,
            )

        return node


class StructuralValidator:
    """Checks structure-preserving similarity between original and transformed code."""

    def validate(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        actions: Sequence[RefactoringAction] | None = None,
    ) -> ValidationStepResult:
        start_iso = utc_now_iso()
        started = time.perf_counter()

        if language == "python":
            score, details = self._validate_python(original_code, transformed_code)
            threshold = DEFAULT_STRUCTURAL_THRESHOLD_PYTHON
        elif language == "c":
            score, details = self._validate_c(original_code, transformed_code)
            threshold = DEFAULT_STRUCTURAL_THRESHOLD_C
        else:
            score, details = self._validate_java(original_code, transformed_code)
            threshold = DEFAULT_STRUCTURAL_THRESHOLD_JAVA

        parameter_object_checks = [
            self._validate_parameter_object_action(
                language=language,
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if action.action_type in PARAMETER_OBJECT_ACTIONS
        ]
        dead_code_checks = [
            self._validate_remove_dead_code_action(
                language=language,
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if action.action_type == ACTION_REMOVE_DEAD_CODE
        ]
        exception_handler_checks = [
            self._validate_narrow_exception_handler_action(
                language=language,
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if action.action_type == ACTION_NARROW_EXCEPTION_HANDLER
        ]
        extract_method_checks = [
            self._validate_python_extract_method_action(
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language == "python" and action.action_type == ACTION_EXTRACT_METHOD
        ]
        move_method_checks = [
            self._validate_python_move_method_action(
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language == "python" and action.action_type == ACTION_MOVE_PYTHON_METHOD
        ]
        inline_class_checks = [
            self._validate_python_inline_class_action(
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language == "python" and action.action_type == ACTION_INLINE_PYTHON_CLASS
        ]
        c_global_variable_checks = [
            self._validate_c_encapsulate_variable_action(
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language == "c" and action.action_type in {
                ACTION_ENCAPSULATE_VARIABLE,
                ACTION_ENCAPSULATE_C_VARIABLE,
            }
        ]
        hide_delegate_checks = [
            self._validate_hide_delegate_action(
                language=language,
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if action.action_type == ACTION_HIDE_DELEGATE and language in {"python", "java"}
        ]
        rename_method_checks = [
            self._validate_rename_method_action(
                language=language,
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language in {"python", "java"} and action.action_type == ACTION_RENAME_METHOD
        ]
        polymorphism_checks = [
            self._validate_python_polymorphism_action(
                original_code=original_code,
                transformed_code=transformed_code,
                action=action,
            )
            for action in actions or []
            if language == "python"
            and action.action_type == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
        ]
        specific_passed = all(
            item.get("passed")
            for item in [
                *parameter_object_checks,
                *dead_code_checks,
                *exception_handler_checks,
                *extract_method_checks,
                *move_method_checks,
                *inline_class_checks,
                *c_global_variable_checks,
                *hide_delegate_checks,
                *rename_method_checks,
                *polymorphism_checks,
            ]
        )
        if polymorphism_checks and all(item.get("passed") for item in polymorphism_checks):
            score = max(score, threshold)
        passed = score >= threshold and specific_passed
        message = (
            f"Structural validation passed with score {score:.3f} >= {threshold:.3f}."
            if passed
            else (
                "Introduce Parameter Object structural validation failed."
                if parameter_object_checks and not all(item.get("passed") for item in parameter_object_checks)
                else (
                    "Remove Dead Code structural validation failed."
                    if dead_code_checks and not all(item.get("passed") for item in dead_code_checks)
                    else (
                        "Exception handler structural validation failed."
                        if exception_handler_checks and not all(item.get("passed") for item in exception_handler_checks)
                        else (
                            "Extract Method structural validation failed."
                            if extract_method_checks and not all(item.get("passed") for item in extract_method_checks)
                            else (
                                "Move Method structural validation failed."
                                if move_method_checks and not all(item.get("passed") for item in move_method_checks)
                                else (
                                    "Inline Class structural validation failed."
                                    if inline_class_checks and not all(item.get("passed") for item in inline_class_checks)
                                    else (
                                        "C Encapsulate Variable structural validation failed."
                                        if c_global_variable_checks and not all(item.get("passed") for item in c_global_variable_checks)
                                        else (
                                            "Hide Delegate structural validation failed."
                                            if hide_delegate_checks and not all(item.get("passed") for item in hide_delegate_checks)
                                            else (
                                                "Rename Method structural validation failed."
                                                if rename_method_checks and not all(item.get("passed") for item in rename_method_checks)
                                                else f"Structural validation failed with score {score:.3f} < {threshold:.3f}."
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
        if polymorphism_checks and not all(item.get("passed") for item in polymorphism_checks):
            message = "Replace Conditional with Polymorphism structural validation failed."

        duration_ms = int((time.perf_counter() - started) * 1000)
        end_iso = utc_now_iso()

        return ValidationStepResult(
            name="structural",
            passed=passed,
            score=score,
            message=message,
            details={
                "threshold": threshold,
                **details,
                "parameter_object_validation": parameter_object_checks,
                "dead_code_validation": dead_code_checks,
                "dead_code_validation_status": (
                    "PASS"
                    if dead_code_checks and all(item.get("passed") for item in dead_code_checks)
                    else ("FAIL" if dead_code_checks else "NOT_APPLICABLE")
                ),
                "exception_handler_validation": exception_handler_checks,
                "extract_method_validation": extract_method_checks,
                "move_method_validation": move_method_checks,
                "inline_class_validation": inline_class_checks,
                "c_global_variable_validation": c_global_variable_checks,
                "hide_delegate_validation": hide_delegate_checks,
                "rename_method_validation": rename_method_checks,
                "polymorphism_validation": polymorphism_checks,
            },
            started_at=start_iso,
            finished_at=end_iso,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _validate_rename_method_action(
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters or {}
        old_name = str(
            params.get("old_name")
            or params.get("method")
            or params.get("method_name")
            or ""
        ).strip()
        new_name = str(
            params.get("new_name")
            or params.get("new_method_name")
            or params.get("renamed_to")
            or ""
        ).strip()
        source_class = str(params.get("source_class") or "").strip()
        raw_parameter_types = params.get("parameter_types") or params.get("param_types")
        parameter_types = [
            str(item).strip()
            for item in raw_parameter_types
            if str(item).strip()
        ] if isinstance(raw_parameter_types, list) else None
        if not old_name or not new_name:
            return {"passed": False, "reason": "missing_method_names"}
        if language == "python":
            return python_transformers.validate_python_rename_method(
                original_code,
                transformed_code,
                old_name=old_name,
                new_name=new_name,
                source_class=source_class,
            )
        return java_transformers.validate_java_rename_method(
            original_code,
            transformed_code,
            old_name=old_name,
            new_name=new_name,
            source_class=source_class,
            parameter_types=parameter_types,
        )

    @staticmethod
    def _validate_c_encapsulate_variable_action(
        *,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters or {}
        variable_name = str(params.get("variable_name") or params.get("variable") or "").strip()
        getter_name = str(params.get("getter_name") or f"get_{variable_name}").strip()
        setter_name = str(params.get("setter_name") or f"set_{variable_name}").strip()
        if not variable_name:
            return {"passed": False, "reason": "missing_variable_name"}
        return c_transformers.validate_c_encapsulated_variable(
            original_code,
            transformed_code,
            variable_name=variable_name,
            getter_name=getter_name,
            setter_name=setter_name,
        )

    @staticmethod
    def _validate_hide_delegate_action(
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters or {}
        source_class = str(params.get("source_class") or "").strip()
        delegate_member = str(params.get("delegate_member") or "").strip()
        delegated_member = str(params.get("delegated_member") or "").strip()
        new_method_name = str(params.get("new_method_name") or "").strip()
        if not all((source_class, delegate_member, delegated_member, new_method_name)):
            return {"passed": False, "reason": "missing_hide_delegate_parameters"}
        if language == "python":
            return python_hide_delegate.validate_hide_delegate(
                original_code,
                transformed_code,
                source_class=source_class,
                delegate_member=delegate_member,
                delegated_member=delegated_member,
                new_method_name=new_method_name,
                delegated_member_is_call=bool(params.get("delegated_member_is_call")),
            )
        return java_hide_delegate.validate_hide_delegate(
            original_code,
            transformed_code,
            source_class=source_class,
            delegate_member=delegate_member,
            delegated_member=delegated_member,
            new_method_name=new_method_name,
            delegate_getter=str(params.get("delegate_getter") or ""),
        )

    @staticmethod
    def _validate_python_polymorphism_action(
        *,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters or {}
        strategy_names = params.get("strategy_class_names")
        if not isinstance(strategy_names, list):
            strategy_names = []
        method_name = str(params.get("method") or params.get("method_name") or "").strip()
        base_class_name = str(params.get("base_class_name") or "").strip()
        if not method_name or not base_class_name or not strategy_names:
            return {"passed": False, "reason": "missing_polymorphism_parameters"}
        raw_line = params.get("source_line")
        source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
        return python_replace_conditional.validate_transformation(
            original_code,
            transformed_code,
            method_name=method_name,
            source_class=str(params.get("source_class") or "").strip(),
            base_class_name=base_class_name,
            strategy_class_names=[str(name) for name in strategy_names],
            source_line=source_line,
            mode=str(params.get("mode") or "terminal"),
            outputs=(
                [str(name) for name in params.get("outputs", [])]
                if isinstance(params.get("outputs"), list)
                else []
            ),
        )

    def _validate_python(self, original: str, transformed: str) -> Tuple[float, Dict[str, float]]:
        try:
            orig_tree = ast.parse(original)
            trans_tree = ast.parse(transformed)
        except SyntaxError:
            return 0.0, {"node_similarity": 0.0, "signature_similarity": 0.0}

        orig_counts = Counter(type(node).__name__ for node in ast.walk(orig_tree))
        trans_counts = Counter(type(node).__name__ for node in ast.walk(trans_tree))
        node_similarity = cosine_similarity_from_counts(dict(orig_counts), dict(trans_counts))

        orig_signatures = self._python_signatures(orig_tree)
        trans_signatures = self._python_signatures(trans_tree)
        signature_similarity = jaccard_similarity(orig_signatures, trans_signatures)

        score = (0.7 * node_similarity) + (0.3 * signature_similarity)
        return score, {
            "node_similarity": round(node_similarity, 4),
            "signature_similarity": round(signature_similarity, 4),
            "normalized_similarity": round(score, 4),
        }

    @staticmethod
    def _python_signatures(tree: ast.Module) -> List[str]:
        signatures: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                signatures.append(f"func:{node.name}:{len(node.args.args)}")
            elif isinstance(node, ast.AsyncFunctionDef):
                signatures.append(f"async_func:{node.name}:{len(node.args.args)}")
            elif isinstance(node, ast.ClassDef):
                signatures.append(f"class:{node.name}")
        return signatures

    def _validate_java(self, original: str, transformed: str) -> Tuple[float, Dict[str, float]]:
        orig_tokens = self._java_tokens(original)
        trans_tokens = self._java_tokens(transformed)

        token_similarity = cosine_similarity_from_counts(
            count_tokens(orig_tokens),
            count_tokens(trans_tokens),
        )

        orig_class_count = len(re.findall(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*", original))
        trans_class_count = len(re.findall(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*", transformed))
        class_similarity = normalized_count_similarity(orig_class_count, trans_class_count)

        orig_method_count = len(re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z_][A-Za-z0-9_<>,\[\]]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", original))
        trans_method_count = len(re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z_][A-Za-z0-9_<>,\[\]]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", transformed))
        method_similarity = normalized_count_similarity(orig_method_count, trans_method_count)

        score = (0.6 * token_similarity) + (0.2 * class_similarity) + (0.2 * method_similarity)

        return score, {
            "token_similarity": round(token_similarity, 4),
            "class_count_similarity": round(class_similarity, 4),
            "method_count_similarity": round(method_similarity, 4),
            "normalized_similarity": round(score, 4),
        }

    def _validate_c(self, original: str, transformed: str) -> Tuple[float, Dict[str, float]]:
        original_summary = summarize_c_source(original)
        transformed_summary = summarize_c_source(transformed)
        comparison = compare_c_static_summaries(original, transformed, [])

        orig_signatures = set(original_summary.get("function_signatures", []))
        trans_signatures = set(transformed_summary.get("function_signatures", []))
        signature_similarity = jaccard_similarity(orig_signatures, trans_signatures)

        function_count_similarity = normalized_count_similarity(
            int(original_summary.get("function_count", 0)),
            int(transformed_summary.get("function_count", 0)),
        )
        return_count_similarity = normalized_count_similarity(
            int(original_summary.get("return_count", 0)),
            int(transformed_summary.get("return_count", 0)),
        )
        control_flow_similarity = normalized_count_similarity(
            int(original_summary.get("control_flow_count", 0)),
            int(transformed_summary.get("control_flow_count", 0)),
        )

        orig_macros = original_summary.get("macros", {})
        trans_macros = transformed_summary.get("macros", {})
        macro_similarity = jaccard_similarity(orig_macros.keys(), trans_macros.keys())

        score = (
            0.35 * signature_similarity
            + 0.20 * function_count_similarity
            + 0.20 * return_count_similarity
            + 0.15 * control_flow_similarity
            + 0.10 * macro_similarity
        )

        if comparison.get("matched"):
            score = max(score, 0.6)

        return score, {
            "function_signature_similarity": round(signature_similarity, 4),
            "function_count_similarity": round(function_count_similarity, 4),
            "return_count_similarity": round(return_count_similarity, 4),
            "control_flow_similarity": round(control_flow_similarity, 4),
            "macro_name_similarity": round(macro_similarity, 4),
            "normalized_similarity": round(score, 4),
            "original_summary": original_summary,
            "transformed_summary": transformed_summary,
        }

    def _validate_parameter_object_action(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters
        method = str(params.get("method") or params.get("method_name") or "").strip()
        source_class = str(params.get("source_class") or "").strip()
        object_name = str(
            params.get("parameter_object_name")
            or params.get("new_class_name")
            or ""
        ).strip()
        parameter_name = str(params.get("parameter_name") or "params").strip()
        if language == "python":
            return self._validate_python_parameter_object(
                original_code,
                transformed_code,
                method=method,
                source_class=source_class,
                object_name=object_name,
                parameter_name=parameter_name,
            )
        if language == "java":
            return self._validate_java_parameter_object(
                original_code,
                transformed_code,
                method=method,
                source_class=source_class,
                object_name=object_name,
                parameter_name=parameter_name,
            )
        return {"passed": False, "reason": "unsupported_language"}

    @staticmethod
    def _validate_python_parameter_object(
        original: str,
        transformed: str,
        *,
        method: str,
        source_class: str,
        object_name: str,
        parameter_name: str,
    ) -> Dict[str, Any]:
        try:
            original_tree = ast.parse(original)
            transformed_tree = ast.parse(transformed)
        except SyntaxError:
            return {"passed": False, "reason": "parse_failed"}

        def target(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
            if source_class:
                owner = next(
                    (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == source_class),
                    None,
                )
                candidates = [] if owner is None else [
                    node for node in owner.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
                ]
            else:
                candidates = [
                    node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
                ]
            return candidates[0] if len(candidates) == 1 else None

        before = target(original_tree)
        after = target(transformed_tree)
        object_node = next(
            (node for node in transformed_tree.body if isinstance(node, ast.ClassDef) and node.name == object_name),
            None,
        )
        if before is None or after is None or object_node is None:
            return {"passed": False, "reason": "target_or_parameter_object_missing"}
        before_names = [
            item.arg for item in [*before.args.posonlyargs, *before.args.args]
            if item.arg not in {"self", "cls"}
        ]
        after_names = [
            item.arg for item in [*after.args.posonlyargs, *after.args.args]
            if item.arg not in {"self", "cls"}
        ]
        fields = {
            node.target.id
            for node in object_node.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        used_before = {
            node.id for node in ast.walk(before)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in before_names
        }
        accessed_after = {
            node.attr for node in ast.walk(after)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == parameter_name
        }
        checks = {
            "parameter_object_created": bool(object_node),
            "fields_preserved": set(before_names) <= fields,
            "parameter_count_reduced": len(before_names) > len(after_names),
            "single_parameter_object_argument": after_names == [parameter_name],
            "body_access_migrated": used_before <= accessed_after,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "method": method,
            "before_parameter_count": len(before_names),
            "after_parameter_count": len(after_names),
            "fields": sorted(fields),
            "checks": checks,
        }

    def _validate_remove_dead_code_action(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        params = action.parameters or {}
        method = str(params.get("method") or params.get("method_name") or "").strip()
        class_name = str(
            params.get("class_name")
            or params.get("source_class")
            or params.get("target_class")
            or ""
        ).strip()
        raw_line = params.get("source_line")
        source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
        if language == "python":
            return self._validate_python_dead_code(
                original_code,
                transformed_code,
                method=method,
                class_name=class_name,
                source_line=source_line,
            )
        if language == "c":
            return self._validate_c_dead_code(
                original_code,
                transformed_code,
                method=method,
                source_line=source_line,
            )
        return {"passed": False, "reason": "unsupported_language"}

    @staticmethod
    def _python_statement_span(node: ast.stmt) -> tuple[int, int]:
        start = int(getattr(node, "lineno", 0) or 0)
        return start, int(getattr(node, "end_lineno", start) or start)

    @staticmethod
    def _python_constant_false(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return not bool(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            if isinstance(node.operand, ast.Constant):
                return bool(node.operand.value)
            return False
        if isinstance(node, ast.BoolOp):
            values = [StructuralValidator._python_static_boolean(item) for item in node.values]
            if any(value is None for value in values):
                return False
            return (all(values) if isinstance(node.op, ast.And) else any(values)) is False
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            try:
                left = ast.literal_eval(node.left)
                right = ast.literal_eval(node.comparators[0])
            except (TypeError, ValueError, SyntaxError, MemoryError, RecursionError):
                return False
            try:
                if isinstance(node.ops[0], ast.Eq):
                    return (left == right) is False
                if isinstance(node.ops[0], ast.NotEq):
                    return (left != right) is False
                if isinstance(node.ops[0], ast.Lt):
                    return (left < right) is False
                if isinstance(node.ops[0], ast.LtE):
                    return (left <= right) is False
                if isinstance(node.ops[0], ast.Gt):
                    return (left > right) is False
                if isinstance(node.ops[0], ast.GtE):
                    return (left >= right) is False
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _python_static_boolean(node: ast.AST) -> bool | None:
        if isinstance(node, ast.Constant):
            return bool(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            value = StructuralValidator._python_static_boolean(node.operand)
            return None if value is None else not value
        if isinstance(node, ast.BoolOp):
            values = [StructuralValidator._python_static_boolean(item) for item in node.values]
            if any(value is None for value in values):
                return None
            return all(values) if isinstance(node.op, ast.And) else any(values)
        return None

    def _python_dead_target(
        self,
        tree: ast.Module,
        *,
        method: str,
        class_name: str,
        source_line: int | None,
    ) -> ast.stmt | None:
        if method:
            candidates: List[ast.stmt] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not class_name and node.name == method:
                    candidates.append(node)
                elif isinstance(node, ast.ClassDef) and (not class_name or node.name == class_name):
                    candidates.extend(
                        child for child in node.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method
                    )
            if len(candidates) != 1:
                return None
            callable_target = candidates[0]
            if source_line is None or int(getattr(callable_target, "lineno", 0) or 0) == source_line:
                return callable_target

            # ``method`` can describe the owning routine while ``source_line``
            # identifies dead code inside it. Structural validation must use
            # the same target interpretation as the transformer; otherwise a
            # correctly removed unreachable statement is incorrectly checked
            # as if the entire live method should have disappeared.
            scope = callable_target
            false_branches = [
                node for node in ast.walk(scope)
                if isinstance(node, ast.If)
                and not node.orelse
                and self._python_constant_false(node.test)
                and self._python_statement_span(node)[0] <= source_line <= self._python_statement_span(node)[1]
            ]
            if false_branches:
                return min(
                    false_branches,
                    key=lambda item: self._python_statement_span(item)[1]
                    - self._python_statement_span(item)[0],
                )

            suites: list[list[ast.stmt]] = []
            for node in ast.walk(scope):
                for field in ("body", "orelse", "finalbody"):
                    value = getattr(node, field, None)
                    if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                        suites.append(value)
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        if handler.body:
                            suites.append(handler.body)
            for suite in suites:
                terminated = False
                for statement in suite:
                    start, end = self._python_statement_span(statement)
                    if terminated and start <= source_line <= end:
                        return statement
                    if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                        terminated = True

            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            assignments = [
                node for node in ast.walk(scope)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and self._python_statement_span(node)[0] <= source_line <= self._python_statement_span(node)[1]
            ]
            for statement in sorted(
                assignments,
                key=lambda item: self._python_statement_span(item)[1]
                - self._python_statement_span(item)[0],
            ):
                names = self._python_literal_assignment_names(statement)
                if not names:
                    continue
                loaded_names = {
                    node.id for node in ast.walk(callable_target)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                if not any(name in loaded_names for name in names):
                    return statement
            return callable_target
        if source_line is None:
            return None
        callable_candidates = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and int(getattr(node, "lineno", 0) or 0) == source_line
        ]
        if class_name:
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            callable_candidates = [
                node
                for node in callable_candidates
                if self._python_callable_class_name(node, parents) == class_name
            ]
        if callable_candidates:
            return min(
                callable_candidates,
                key=lambda item: self._python_statement_span(item)[1]
                - self._python_statement_span(item)[0],
            )
        false_branches = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and not node.orelse
            and self._python_constant_false(node.test)
            and self._python_statement_span(node)[0] <= source_line <= self._python_statement_span(node)[1]
        ]
        if false_branches:
            return min(false_branches, key=lambda item: self._python_statement_span(item)[1] - self._python_statement_span(item)[0])
        terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)
        for parent in ast.walk(tree):
            for _name, value in ast.iter_fields(parent):
                if not isinstance(value, list) or not value or not all(isinstance(item, ast.stmt) for item in value):
                    continue
                terminated = False
                for statement in value:
                    start, end = self._python_statement_span(statement)
                    if terminated and start <= source_line <= end:
                        return statement
                    if isinstance(statement, terminators):
                        terminated = True
        candidates = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and self._python_statement_span(node)[0] <= source_line <= self._python_statement_span(node)[1]
        ]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _python_callable_class_name(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parents: Dict[ast.AST, ast.AST],
    ) -> str | None:
        owner = parents.get(node)
        while owner is not None:
            if isinstance(owner, ast.ClassDef):
                return owner.name
            owner = parents.get(owner)
        return None

    def _validate_python_dead_code(
        self,
        original: str,
        transformed: str,
        *,
        method: str,
        class_name: str,
        source_line: int | None,
    ) -> Dict[str, Any]:
        try:
            original_tree = ast.parse(original)
            transformed_tree = ast.parse(transformed)
        except SyntaxError:
            return {"passed": False, "reason": "parse_failed"}
        legacy_exception_check = self._validate_python_legacy_dead_code_exception_target(
            original_tree,
            transformed_tree,
            source_line=source_line,
        )
        if legacy_exception_check is not None:
            return legacy_exception_check
        target = self._python_dead_target(
            original_tree,
            method=method,
            class_name=class_name,
            source_line=source_line,
        )
        if target is None:
            return {"passed": False, "reason": "target_not_found"}
        target_dump = ast.dump(target, include_attributes=False)
        target_removed = target_dump not in {
            ast.dump(node, include_attributes=False)
            for node in ast.walk(transformed_tree)
            if isinstance(node, ast.stmt)
        }
        parents = {
            child: parent
            for parent in ast.walk(original_tree)
            for child in ast.iter_child_nodes(parent)
        }
        owner = parents.get(target)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = parents.get(owner)
        after_named_scopes = {
            (type(node).__name__, node.name)
            for node in ast.walk(transformed_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
            expected_scopes = {
                (type(node).__name__, node.name)
                for node in original_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node is not target
            }
        elif isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            expected_scopes = {(type(owner).__name__, owner.name)}
        else:
            expected_scopes = {
                (type(node).__name__, node.name)
                for node in original_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
        no_required_reference_removed = True
        target_name = method if isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)) else ""
        if not target_name and isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
            target_name = target.name
        if target_name:
            no_required_reference_removed = not any(
                (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == target_name)
                or (isinstance(node, ast.Attribute) and node.attr == target_name)
                for node in ast.walk(original_tree)
                if node is not target
            )
        original_docstring = ast.get_docstring(original_tree, clean=False)
        transformed_docstring = ast.get_docstring(transformed_tree, clean=False)
        checks = {
            "target_existed_before": True,
            "target_removed_after": target_removed,
            "no_required_referenced_code_removed": no_required_reference_removed,
            # Other planned refactorings may legitimately change literals or
            # statements elsewhere in this file. Preserve surrounding callable
            # identity here; full-file structural similarity is checked above.
            "unrelated_source_preserved": expected_scopes <= after_named_scopes,
            "module_docstring_preserved": original_docstring == transformed_docstring,
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "target_kind": type(target).__name__,
            "checks": checks,
        }

    def _validate_narrow_exception_handler_action(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        if language == "python":
            return self._validate_python_narrow_exception_handler(
                original_code,
                transformed_code,
                action.parameters or {},
            )
        if language == "java":
            return self._validate_java_narrow_exception_handler(
                original_code,
                transformed_code,
                action.parameters or {},
            )
        return {"passed": False, "reason": "unsupported_language"}

    def _validate_python_extract_method_action(
        self,
        *,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        """Prove that Python Extract Method moved real logic, not a wrapper."""

        params = action.parameters or {}
        method_name = str(
            params.get("method")
            or params.get("method_name")
            or params.get("function")
            or params.get("function_name")
            or ""
        ).strip()
        helper_name = str(
            params.get("new_method_name")
            or params.get("new_function_name")
            or params.get("extracted_method_name")
            or ""
        ).strip()
        source_class = str(
            params.get("source_class")
            or params.get("target_class")
            or params.get("class_name")
            or ""
        ).strip()
        if not method_name or not helper_name:
            return {"passed": False, "reason": "missing_method_or_helper_name"}

        try:
            before_tree = ast.parse(original_code)
            after_tree = ast.parse(transformed_code)
        except SyntaxError:
            return {"passed": False, "reason": "parse_failed"}

        before_target = self._find_python_extract_method_target(
            before_tree, method_name, source_class
        )
        after_target = self._find_python_extract_method_target(
            after_tree, method_name, source_class
        )
        helper = self._find_python_extract_method_target(
            after_tree, helper_name, source_class
        )
        if before_target is None or after_target is None or helper is None:
            return {"passed": False, "reason": "target_or_requested_helper_not_found"}

        before_body = self._meaningful_python_body_dumps(before_target)
        after_body = self._meaningful_python_body_dumps(after_target)
        helper_body = self._meaningful_python_body_dumps(helper)
        moved_dumps = (before_body & helper_body) - after_body
        # A direct ``return value`` in both routines is output plumbing for a
        # successful extraction, not duplicated business logic. Compare the
        # executable body without direct returns for the duplication check.
        duplicated_dumps = (
            self._meaningful_python_body_dumps(helper, exclude_direct_returns=True)
            & self._meaningful_python_body_dumps(
                after_target,
                exclude_direct_returns=True,
            )
        )
        helper_calls = [
            node
            for node in ast.walk(after_target)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == helper_name)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == helper_name)
            )
        ]
        helper_parameters = [
            argument.arg
            for argument in [
                *helper.args.posonlyargs,
                *helper.args.args,
                *helper.args.kwonlyargs,
            ]
            if argument.arg not in {"self", "cls"}
        ]
        call_matches_signature = any(
            len(call.args) + len(call.keywords) >= len(helper_parameters)
            for call in helper_calls
        )
        helper_returns_value = any(
            isinstance(node, ast.Return) and node.value is not None
            for node in ast.walk(helper)
        )
        returned_value_is_used = not helper_returns_value or any(
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and any(call is child for child in ast.walk(node))
            for call in helper_calls
            for node in ast.walk(after_target)
        )
        before_metrics = self._python_extract_method_metrics(before_target)
        after_metrics = self._python_extract_method_metrics(after_target)
        checks = {
            "requested_new_method_exists": helper.name == helper_name,
            "helper_contains_real_moved_logic": bool(moved_dumps),
            "original_calls_new_method": bool(helper_calls),
            "extracted_statements_removed_from_original": bool(moved_dumps),
            "no_logic_duplicated": not bool(duplicated_dumps),
            "original_method_loc_reduced": after_metrics["loc"] < before_metrics["loc"],
            "original_method_complexity_not_increased": (
                after_metrics["complexity"] <= before_metrics["complexity"]
            ),
            "helper_inputs_passed": call_matches_signature,
            "helper_outputs_used": returned_value_is_used,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "method": method_name,
            "helper": helper_name,
            "before_metrics": before_metrics,
            "after_metrics": after_metrics,
            "moved_top_level_statement_count": len(moved_dumps),
            "duplicated_top_level_statement_count": len(duplicated_dumps),
            "checks": checks,
        }

    @staticmethod
    def _find_python_extract_method_target(
        tree: ast.Module,
        method_name: str,
        source_class: str,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        if source_class:
            owner = next(
                (
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == source_class
                ),
                None,
            )
            candidates = [] if owner is None else [
                node for node in owner.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ]
            if len(candidates) == 1:
                return candidates[0]

            # RDP occasionally places the Python module filename in
            # ``source_class``. Recover only a unique routine across the
            # parsed module; do not guess when two real methods share a name.
            candidates = [
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ]
            candidates.extend(
                member
                for owner in tree.body
                if isinstance(owner, ast.ClassDef)
                for member in owner.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name == method_name
            )
        else:
            candidates = [
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _meaningful_python_body_dumps(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        exclude_direct_returns: bool = False,
    ) -> set[str]:
        body = list(function.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        if exclude_direct_returns:
            body = [statement for statement in body if not isinstance(statement, ast.Return)]
        return {
            ast.dump(
                _PythonExtractMethodStructuralNormalizer().visit(
                    copy.deepcopy(statement)
                ),
                include_attributes=False,
            )
            for statement in body
        }

    @staticmethod
    def _python_extract_method_metrics(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> Dict[str, int]:
        complexity_types = (
            ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match,
            ast.BoolOp, ast.IfExp,
        )
        return {
            "loc": int(getattr(function, "end_lineno", function.lineno) or function.lineno) - function.lineno + 1,
            "complexity": 1 + sum(
                isinstance(node, complexity_types) for node in ast.walk(function)
            ),
        }

    def _validate_python_move_method_action(
        self,
        *,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        """Use the same semantic proof as the Python Move Method transformer."""

        params = action.parameters or {}
        return validate_python_move_method(
            original_code=original_code,
            transformed_code=transformed_code,
            method_name=str(params.get("method") or "").strip(),
            source_class=str(params.get("source_class") or "").strip(),
            destination_class=str(params.get("destination_class") or "").strip(),
            destination_parameter=str(params.get("destination_parameter") or "").strip(),
            action_evidence=(
                dict(params.get("move_method_validation_evidence"))
                if isinstance(params.get("move_method_validation_evidence"), dict)
                else None
            ),
        )

    @staticmethod
    def _python_move_method_normalized_body_dump(
        method: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        destination_parameter: str,
        constant_values: Dict[str, Any],
    ) -> str:
        normalized = _PythonMoveMethodStructuralNormalizer(
            destination_parameter=destination_parameter,
            constant_values=constant_values,
        ).visit(copy.deepcopy(method))
        if not isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ""
        module = ast.Module(body=list(normalized.body), type_ignores=[])
        return ast.dump(ast.fix_missing_locations(module), include_attributes=False)

    @staticmethod
    def _python_move_method_signature_migrated(
        original_method: ast.FunctionDef | ast.AsyncFunctionDef,
        moved_method: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        destination_parameter: str,
    ) -> bool:
        def arg_names(args: ast.arguments) -> list[str]:
            return [arg.arg for arg in [*args.posonlyargs, *args.args]]

        expected_args = [
            name for name in arg_names(original_method.args)
            if name != destination_parameter
        ]
        actual_args = arg_names(moved_method.args)
        if expected_args != actual_args:
            return False
        if bool(original_method.args.vararg) != bool(moved_method.args.vararg):
            return False
        if bool(original_method.args.kwarg) != bool(moved_method.args.kwarg):
            return False
        if [arg.arg for arg in original_method.args.kwonlyargs] != [
            arg.arg for arg in moved_method.args.kwonlyargs
        ]:
            return False
        if len(original_method.args.defaults) != len(moved_method.args.defaults):
            return False
        if len([item for item in original_method.args.kw_defaults if item is not None]) != len([
            item for item in moved_method.args.kw_defaults if item is not None
        ]):
            return False
        if isinstance(original_method, ast.AsyncFunctionDef) != isinstance(moved_method, ast.AsyncFunctionDef):
            return False
        if original_method.returns is None or moved_method.returns is None:
            return original_method.returns is None and moved_method.returns is None
        return ast.dump(original_method.returns, include_attributes=False) == ast.dump(
            moved_method.returns,
            include_attributes=False,
        )

    @staticmethod
    def _python_move_method_has_real_logic(
        method: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> bool:
        body = list(method.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        if not body:
            return False
        return not all(
            isinstance(statement, ast.Pass)
            or (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is Ellipsis
            )
            for statement in body
        )

    @staticmethod
    def _python_sctva_module_constant_values(tree: ast.Module) -> Dict[str, Any]:
        """Return safe literal values for SCTVA-style module constants.

        The structural validator runs after the complete plan.  Therefore a
        method that was correctly moved may already contain names introduced
        by a later ``Introduce Constant`` action.  Only simple top-level
        assignments whose names clearly follow SCTVA's generated constant
        naming convention are considered.  ``ast.literal_eval`` is used so
        no source expression is executed.
        """

        constant_name = re.compile(
            r"^(?:CONSTANT_|MAGIC_|EXTRACTED_CONSTANT)[A-Za-z0-9_]*$"
        )
        values: Dict[str, Any] = {}

        for statement in tree.body:
            target_name = ""
            value_node: ast.AST | None = None

            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                if isinstance(target, ast.Name):
                    target_name = target.id
                    value_node = statement.value

            elif isinstance(statement, ast.AnnAssign):
                if isinstance(statement.target, ast.Name):
                    target_name = statement.target.id
                    value_node = statement.value

            if (
                not target_name
                or value_node is None
                or not constant_name.match(target_name)
            ):
                continue

            try:
                value = ast.literal_eval(value_node)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue

            # Keep comparison deterministic and conservative.  These are the
            # literal types used by SCTVA's Introduce Constant transformations.
            if isinstance(value, (str, bytes, int, float, complex, bool, type(None))):
                values[target_name] = value

        return values

    @staticmethod
    def _python_top_level_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
        matches = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _python_class_method(owner: ast.ClassDef, name: str) -> ast.FunctionDef | None:
        matches = [
            node for node in owner.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _python_external_method_call_count(
        tree: ast.Module,
        method: ast.FunctionDef,
        method_name: str,
    ) -> int:
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != method_name:
                continue
            current: ast.AST | None = node
            within_method = False
            while current is not None:
                if current is method:
                    within_method = True
                    break
                current = parents.get(current)
            if not within_method:
                count += 1
        return count

    @staticmethod
    def _python_known_class_instances(tree: ast.Module, class_name: str) -> set[str]:
        return {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == class_name
            for target in node.targets
            if isinstance(target, ast.Name)
        }

    def _validate_python_inline_class_action(
        self,
        *,
        original_code: str,
        transformed_code: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        """Require a complete Inline Class transformation, not just parsing.

        Two safe Python shapes are supported:

        1. legacy/module-function inline: the tiny class becomes one or more
           module functions and local state variables;
        2. owned-composition inline: a helper such as ``CustomerContact`` is
           uniquely owned by ``Customer`` and its fields/methods are moved
           directly into the owner class.
        """

        params = action.parameters or {}
        class_name = str(
            params.get("class_to_inline")
            or params.get("source_class")
            or ""
        ).strip()
        if not class_name:
            return {"passed": False, "reason": "missing_class_to_inline"}
        try:
            before_tree = ast.parse(original_code)
            after_tree = ast.parse(transformed_code)
        except SyntaxError:
            return {"passed": False, "reason": "parse_failed"}

        inline_mode = str(params.get("inline_mode") or "").strip()
        if inline_mode == "satisfied_by_prior_refactoring":
            return self._validate_python_inline_class_satisfied_by_prior_refactoring(
                after_tree=after_tree,
                class_name=class_name,
                action=action,
            )
        if inline_mode == "empty_class_cleanup":
            return self._validate_python_empty_class_cleanup(
                before_tree=before_tree,
                after_tree=after_tree,
                class_name=class_name,
                action=action,
            )

        original_class = self._python_top_level_class(before_tree, class_name)
        if original_class is None:
            return {"passed": False, "reason": "target_class_not_found_before"}

        class_removed = self._python_top_level_class(after_tree, class_name) is None
        original_methods = [
            node
            for node in original_class.body
            if isinstance(node, ast.FunctionDef) and node.name != "__init__"
        ]
        method_names = {node.name for node in original_methods}

        original_constructor = self._python_class_method(original_class, "__init__")
        original_fields: set[str] = set()
        if original_constructor is not None:
            for node in ast.walk(original_constructor):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and isinstance(node.ctx, ast.Store)
                ):
                    original_fields.add(node.attr)
        # A helper without constructor fields may still expose self fields only
        # through a method. Keep those in the state-preservation set too.
        for method in original_methods:
            for node in ast.walk(method):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    original_fields.add(node.attr)

        destination_class_name = str(params.get("destination_class") or "").strip()
        owner_attribute = str(params.get("owner_attribute") or "").strip()

        # Engine metadata normally provides destination_class for owned Inline
        # Class.  For direct validator use, infer it only when exactly one
        # surviving class contains every original business method.
        if not destination_class_name and method_names:
            destination_candidates = []
            for node in after_tree.body:
                if not isinstance(node, ast.ClassDef) or node.name == class_name:
                    continue
                node_methods = {
                    child.name
                    for child in node.body
                    if isinstance(child, ast.FunctionDef)
                }
                if method_names <= node_methods:
                    destination_candidates.append(node.name)
            if len(destination_candidates) == 1:
                destination_class_name = destination_candidates[0]

        destination_class = (
            self._python_top_level_class(after_tree, destination_class_name)
            if destination_class_name
            else None
        )

        if destination_class is not None:
            # ------------------------------
            # Owned-composition Inline Class
            # ------------------------------
            after_destination_methods = {
                node.name: node
                for node in destination_class.body
                if isinstance(node, ast.FunctionDef) and node.name in method_names
            }
            method_counts_in_destination = {
                name: sum(
                    isinstance(node, ast.FunctionDef) and node.name == name
                    for node in destination_class.body
                )
                for name in method_names
            }
            methods_preserved = (
                set(after_destination_methods) == method_names
                and all(count == 1 for count in method_counts_in_destination.values())
            )

            destination_fields = {
                node.attr
                for node in ast.walk(destination_class)
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and isinstance(node.ctx, ast.Store)
                )
            }
            state_preserved = original_fields <= destination_fields

            before_constants = self._python_sctva_module_constant_values(before_tree)
            after_constants = self._python_sctva_module_constant_values(after_tree)
            logic_preserved = methods_preserved
            for original_method in original_methods:
                moved_method = after_destination_methods.get(original_method.name)
                if moved_method is None:
                    logic_preserved = False
                    break
                expected = _PythonMoveMethodStructuralNormalizer(
                    constant_values=before_constants,
                ).visit(copy.deepcopy(original_method))
                actual = _PythonMoveMethodStructuralNormalizer(
                    constant_values=after_constants,
                ).visit(copy.deepcopy(moved_method))
                ast.fix_missing_locations(expected)
                ast.fix_missing_locations(actual)
                if ast.dump(expected, include_attributes=False) != ast.dump(
                    actual,
                    include_attributes=False,
                ):
                    logic_preserved = False
                    break

            remaining_instantiations = sum(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == class_name
                for node in ast.walk(after_tree)
            )
            unresolved_class_references = any(
                isinstance(node, ast.Name) and node.id == class_name
                for node in ast.walk(after_tree)
            )

            before_chained_calls = 0
            if owner_attribute:
                before_chained_calls = sum(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in method_names
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == owner_attribute
                    for node in ast.walk(before_tree)
                )
            else:
                before_chained_calls = sum(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in method_names
                    for node in ast.walk(before_tree)
                )

            after_direct_calls = sum(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in method_names
                for node in ast.walk(after_tree)
            )

            remaining_owner_member_references = 0
            if owner_attribute:
                remaining_owner_member_references = sum(
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == owner_attribute
                    and node.attr in (method_names | original_fields)
                    for node in ast.walk(after_tree)
                )

            checks = {
                "target_class_existed_before": True,
                "target_class_removed_after": class_removed,
                "destination_class_exists_after": destination_class is not None,
                "required_methods_and_state_preserved_elsewhere": (
                    methods_preserved and state_preserved and logic_preserved
                ),
                "actual_method_logic_preserved": logic_preserved,
                "original_class_instantiations_removed_or_updated": remaining_instantiations == 0,
                "affected_call_sites_updated": after_direct_calls >= before_chained_calls,
                "owner_wrapper_references_removed": remaining_owner_member_references == 0,
                "no_duplicated_logic": class_removed and methods_preserved,
                "no_unresolved_references_to_removed_class": not unresolved_class_references,
                "python_syntax_valid": True,
            }
            return {
                "passed": all(checks.values()),
                "language": "python",
                "inline_mode": "owner_class",
                "class_to_inline": class_name,
                "destination_class": destination_class_name,
                "owner_attribute": owner_attribute,
                "before_direct_call_sites": before_chained_calls,
                "after_direct_call_sites": after_direct_calls,
                "checks": checks,
            }

        # --------------------------
        # Legacy module-function mode
        # --------------------------
        after_functions = {
            node.name: node
            for node in after_tree.body
            if isinstance(node, ast.FunctionDef) and node.name in method_names
        }
        method_counts = {
            name: sum(
                isinstance(node, ast.FunctionDef) and node.name == name
                for node in after_tree.body
            )
            for name in method_names
        }
        before_instance_names = self._python_known_class_instances(before_tree, class_name)
        before_method_calls = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in method_names
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in before_instance_names
            for node in ast.walk(before_tree)
        )
        after_function_calls = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in method_names
            for node in ast.walk(after_tree)
        )
        remaining_instantiations = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == class_name
            for node in ast.walk(after_tree)
        )
        unresolved_class_references = any(
            isinstance(node, ast.Name) and node.id == class_name
            for node in ast.walk(after_tree)
        )
        methods_preserved = (
            set(after_functions) == method_names
            and all(count == 1 for count in method_counts.values())
        )
        state_preserved = all(
            field in {
                argument.arg
                for function in after_functions.values()
                for argument in function.args.args
            }
            or any(
                isinstance(node, ast.Name) and node.id.endswith(f"_{field}")
                for node in ast.walk(after_tree)
            )
            for field in original_fields
        )
        checks = {
            "target_class_existed_before": True,
            "target_class_removed_after": class_removed,
            "required_methods_and_state_preserved_elsewhere": methods_preserved and state_preserved,
            "original_class_instantiations_removed_or_updated": remaining_instantiations == 0,
            "affected_call_sites_updated": after_function_calls >= before_method_calls,
            "no_duplicated_logic": methods_preserved,
            "no_unresolved_references_to_removed_class": not unresolved_class_references,
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "inline_mode": "module_function",
            "class_to_inline": class_name,
            "before_direct_call_sites": before_method_calls,
            "after_function_call_sites": after_function_calls,
            "checks": checks,
        }

    def _validate_python_inline_class_satisfied_by_prior_refactoring(
        self,
        *,
        after_tree: ast.Module,
        class_name: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        """Prove that a prior action made the requested class meaningful."""

        current_class = self._python_top_level_class(after_tree, class_name)
        methods = (
            [
                node
                for node in current_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name != "__init__"
            ]
            if current_class is not None
            else []
        )
        fields = (
            {
                node.attr
                for node in ast.walk(current_class)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
                and isinstance(node.ctx, ast.Store)
            }
            if current_class is not None
            else set()
        )
        history = (action.parameters or {}).get("prior_transformations") or []
        prior_move_added_responsibility = any(
            isinstance(item, dict)
            and str(item.get("action_type") or "") == ACTION_MOVE_PYTHON_METHOD
            and str(item.get("destination_class") or "") == class_name
            and str(item.get("status") or "").lower() in {"success", "already_applied"}
            for item in history
        )
        meaningful_methods_exist = any(
            self._python_move_method_has_real_logic(method)
            for method in methods
        )
        checks = {
            "class_found_in_current_ast": current_class is not None,
            "prior_refactoring_changed_responsibility": prior_move_added_responsibility,
            "meaningful_methods_exist": meaningful_methods_exist,
            "meaningful_state_exists": bool(fields),
            "class_preserved": current_class is not None,
            "smell_resolved": (
                prior_move_added_responsibility
                and meaningful_methods_exist
                and bool(fields)
            ),
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "class_to_inline": class_name,
            "inline_mode": "satisfied_by_prior_refactoring",
            "result": "SATISFIED_BY_PRIOR_REFACTORING",
            "reason": "SMELL_RESOLVED_BY_PRIOR_REFACTORING",
            "checks": checks,
        }

    def _validate_python_empty_class_cleanup(
        self,
        *,
        before_tree: ast.Module,
        after_tree: ast.Module,
        class_name: str,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        """Validate cleanup after a prior operation made a class empty.

        The original source can still contain the method that a preceding Move
        Method removed.  This validator therefore checks the cleanup's own
        safety contract rather than incorrectly requiring that original method
        to be inlined a second time.
        """

        original_class = self._python_top_level_class(before_tree, class_name)
        class_removed = self._python_top_level_class(after_tree, class_name) is None
        unresolved_references = any(
            isinstance(node, ast.Name) and node.id == class_name
            for node in ast.walk(after_tree)
        )
        checks = {
            "target_class_existed_before": original_class is not None,
            "class_was_empty_when_removed": bool(
                (action.parameters or {}).get("class_was_empty") is True
            ),
            "target_class_removed_after": class_removed,
            "no_unresolved_references": not unresolved_references,
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "class_to_inline": class_name,
            "inline_mode": "empty_class_cleanup",
            "strategy": str((action.parameters or {}).get("strategy") or ""),
            "checks": checks,
        }

    @staticmethod
    def _python_exception_type_name(handler: ast.ExceptHandler) -> str:
        if isinstance(handler.type, ast.Name):
            return handler.type.id
        if isinstance(handler.type, ast.Attribute):
            return handler.type.attr
        return ""

    def _validate_python_narrow_exception_handler(
        self,
        original: str,
        transformed: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            before_tree = ast.parse(original)
            after_tree = ast.parse(transformed)
        except SyntaxError:
            return {"passed": False, "reason": "parse_failed"}

        original_handlers = sorted(
            (node for node in ast.walk(before_tree) if isinstance(node, ast.ExceptHandler)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        transformed_handlers = sorted(
            (node for node in ast.walk(after_tree) if isinstance(node, ast.ExceptHandler)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        raw_line = params.get("source_line")
        source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
        expected_original = str(params.get("original_exception_type") or "")
        expected_target = str(params.get("target_exception_type") or "").strip()
        expected_name = str(params.get("handler_name") or "")

        before_broad_count = sum(
            1
            for handler in original_handlers
            if self._python_handler_is_broad(handler)
        )
        after_broad_count = sum(
            1
            for handler in transformed_handlers
            if self._python_handler_is_broad(handler)
        )
        candidates = [
            handler for handler in original_handlers
            if (not expected_original and handler.type is None)
            or (expected_original and self._python_exception_type_name(handler) == expected_original)
        ]
        if expected_name:
            candidates = [handler for handler in candidates if str(handler.name or "") == expected_name]
        if source_line is not None:
            line_matches = [handler for handler in candidates if handler.lineno == source_line]
            if line_matches:
                candidates = line_matches
        if len(candidates) != 1:
            return {"passed": False, "reason": "original_handler_not_found"}

        original_handler = candidates[0]
        original_try = self._python_parent_try(before_tree, original_handler)
        original_body_count = len(original_try.body) if original_try else 0
        expected_targets = self._python_expected_exception_types(expected_target)
        transformed_types = {
            exception_type
            for handler in transformed_handlers
            for exception_type in self._python_exception_type_names(handler)
        }
        if not expected_targets:
            expected_targets = {
                exception_type
                for exception_type in transformed_types
                if exception_type not in {"Exception", "BaseException"}
            }
        target_handlers = [
            handler for handler in transformed_handlers
            if self._python_exception_type_names(handler) & expected_targets
        ]
        if not target_handlers and expected_targets == {"Exception"}:
            target_handlers = [
                handler for handler in transformed_handlers
                if self._python_exception_type_names(handler) == {"Exception"}
            ]
        transformed_try_nodes = [
            node
            for node in ast.walk(after_tree)
            if isinstance(node, ast.Try)
            and any(handler in target_handlers for handler in node.handlers)
        ]
        overreaching_try_reduced = True
        if original_body_count > 1 and expected_targets != {"Exception"}:
            overreaching_try_reduced = any(
                len(node.body) < original_body_count
                for node in transformed_try_nodes
            )
        original_body_dump = {
            ast.dump(statement, include_attributes=False)
            for statement in original_handler.body
        }
        handler_body_preserved = any(
            original_body_dump
            <= {
                ast.dump(statement, include_attributes=False)
                for statement in handler.body
            }
            for handler in target_handlers
        )
        broad_removed_or_narrowed = (
            after_broad_count < before_broad_count
            or expected_targets == {"Exception"}
        )
        checks = {
            "handler_existed_before": True,
            "broad_exception_removed_or_meaningfully_narrowed": broad_removed_or_narrowed,
            "specific_exception_handlers_introduced": bool(target_handlers),
            "target_exception_type_applied": expected_targets <= transformed_types,
            "handler_binding_preserved": (
                not str(original_handler.name or "")
                or all(str(handler.name or "") == str(original_handler.name or "") for handler in target_handlers)
            ),
            "handler_body_preserved": handler_body_preserved,
            "overreaching_try_reduced_when_required": overreaching_try_reduced,
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "target_kind": "except_handler",
            "expected_exception_types": sorted(expected_targets),
            "transformed_exception_types": sorted(transformed_types),
            "checks": checks,
        }

    @classmethod
    def _validate_python_legacy_dead_code_exception_target(
        cls,
        original_tree: ast.Module,
        transformed_tree: ast.Module,
        *,
        source_line: int | None,
    ) -> Dict[str, Any] | None:
        if source_line is None:
            return None
        original_handlers = [
            node for node in ast.walk(original_tree)
            if isinstance(node, ast.ExceptHandler)
            and int(getattr(node, "lineno", 0) or 0) == source_line
        ]
        if len(original_handlers) != 1:
            return None
        original_handler = original_handlers[0]
        if not cls._python_handler_is_broad(original_handler):
            return None
        before_broad_count = sum(
            1 for node in ast.walk(original_tree)
            if isinstance(node, ast.ExceptHandler) and cls._python_handler_is_broad(node)
        )
        transformed_handlers = [
            node for node in ast.walk(transformed_tree)
            if isinstance(node, ast.ExceptHandler)
        ]
        after_broad_count = sum(
            1 for node in transformed_handlers
            if cls._python_handler_is_broad(node)
        )
        specific_handlers = [
            node for node in transformed_handlers
            if cls._python_exception_type_names(node)
            and not cls._python_handler_is_broad(node)
        ]
        bare_except_narrowed = (
            original_handler.type is None
            and any(cls._python_exception_type_names(node) == {"Exception"} for node in transformed_handlers)
        )
        checks = {
            "legacy_remove_dead_code_target_was_exception_handler": True,
            "broad_exception_removed_or_narrowed": (
                after_broad_count < before_broad_count or bare_except_narrowed
            ),
            "specific_exception_handler_present": bool(specific_handlers) or bare_except_narrowed,
            "python_syntax_valid": True,
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "target_kind": "legacy_exception_handler",
            "checks": checks,
        }

    @staticmethod
    def _python_exception_type_names(handler: ast.ExceptHandler) -> set[str]:
        if isinstance(handler.type, ast.Name):
            return {handler.type.id}
        if isinstance(handler.type, ast.Attribute):
            return {handler.type.attr}
        if isinstance(handler.type, ast.Tuple):
            names = set()
            for item in handler.type.elts:
                if isinstance(item, ast.Name):
                    names.add(item.id)
                elif isinstance(item, ast.Attribute):
                    names.add(item.attr)
            return names
        return set()

    @classmethod
    def _python_handler_is_broad(cls, handler: ast.ExceptHandler) -> bool:
        names = cls._python_exception_type_names(handler)
        return handler.type is None or bool(names & {"Exception", "BaseException"})

    @staticmethod
    def _python_parent_try(
        tree: ast.Module,
        handler: ast.ExceptHandler,
    ) -> ast.Try | None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and handler in node.handlers:
                return node
        return None

    @staticmethod
    def _python_expected_exception_types(value: str) -> set[str]:
        text = str(value or "").strip().strip("()")
        if not text:
            return set()
        return {
            part.strip().rsplit(".", 1)[-1]
            for part in text.split(",")
            if part.strip()
        }

    @staticmethod
    def _java_catch_handlers(source: str) -> list[re.Match[str]]:
        return list(re.finditer(
            r"\bcatch\s*\(\s*(?:final\s+)?(?P<type>[A-Za-z_][A-Za-z0-9_.]*)\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)",
            source,
        ))

    def _validate_java_narrow_exception_handler(
        self,
        original: str,
        transformed: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        original_handlers = self._java_catch_handlers(original)
        transformed_handlers = self._java_catch_handlers(transformed)
        raw_line = params.get("source_line")
        source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
        expected_original = str(params.get("original_exception_type") or "")
        expected_target = str(params.get("target_exception_type") or "").strip()
        expected_name = str(params.get("handler_name") or "")
        candidates = [
            handler for handler in original_handlers
            if not expected_original or handler.group("type") == expected_original
        ]
        if expected_name:
            candidates = [handler for handler in candidates if handler.group("name") == expected_name]
        if source_line is not None:
            line_matches = [
                handler for handler in candidates
                if original.count("\n", 0, handler.start()) + 1 == source_line
            ]
            if line_matches:
                candidates = line_matches
        if len(candidates) != 1:
            return {"passed": False, "reason": "original_handler_not_found"}
        original_handler = candidates[0]
        original_index = original_handlers.index(original_handler)
        transformed_handler = (
            transformed_handlers[original_index]
            if len(transformed_handlers) > original_index
            else None
        )
        checks = {
            "handler_existed_before": True,
            "handler_count_preserved": len(original_handlers) == len(transformed_handlers),
            "handler_preserved_after": transformed_handler is not None,
            "target_exception_type_applied": (
                transformed_handler is not None
                and transformed_handler.group("type") == expected_target
            ),
            "handler_binding_preserved": (
                transformed_handler is not None
                and transformed_handler.group("name") == original_handler.group("name")
            ),
        }
        return {
            "passed": all(checks.values()),
            "language": "java",
            "target_kind": "catch_handler",
            "checks": checks,
        }

    @staticmethod
    def _validate_c_dead_code(
        original: str,
        transformed: str,
        *,
        method: str,
        source_line: int | None,
    ) -> Dict[str, Any]:
        from ..transformers.c_transformers import apply_remove_dead_code

        expected, replacements = apply_remove_dead_code(
            original,
            method,
            source_line=source_line,
        )
        target_existed = replacements == 1
        original_summary = summarize_c_source(original)
        transformed_summary = summarize_c_source(transformed)
        original_count = int(original_summary.get("function_count", 0))
        transformed_count = int(transformed_summary.get("function_count", 0))
        expected_removed = [method] if method else []
        original_functions = set((original_summary.get("functions") or {}).keys())
        transformed_functions = set((transformed_summary.get("functions") or {}).keys())
        function_count_changed_as_expected = (
            transformed_count >= original_count - 1
            if method
            else transformed_count == original_count
        )
        checks = {
            "target_existed_before": target_existed,
            "target_removed_after": (
                target_existed
                and transformed != original
                and (
                    not method
                    or not re.search(rf"\b{re.escape(method)}\b", transformed)
                )
            ),
            "no_required_referenced_code_removed": (
                not method or len(re.findall(rf"\b{re.escape(method)}\b", original)) == 1
            ),
            "unrelated_source_preserved": (
                target_existed
                and (
                    (original_functions - {method}) <= transformed_functions
                    if method
                    else expected == transformed
                )
            ),
            "function_count_changed_as_expected": function_count_changed_as_expected,
        }
        return {
            "passed": all(checks.values()),
            "status": "PASS" if all(checks.values()) else "FAIL",
            "language": "c",
            "target_kind": "static_function" if method else "line_target",
            "target": method,
            "original_function_count": original_count,
            "transformed_function_count": transformed_count,
            "expected_removed": expected_removed,
            "checks": checks,
        }

    @staticmethod
    def _validate_java_parameter_object(
        original: str,
        transformed: str,
        *,
        method: str,
        source_class: str,
        object_name: str,
        parameter_name: str,
    ) -> Dict[str, Any]:
        if not source_class:
            owners = [
                name for name in declared_class_names(original)
                if (model := _parse_java_class(original, name)) is not None
                and any(item.name == method for item in model.methods)
            ]
            source_class = owners[0] if len(owners) == 1 else ""
        before_model = _parse_java_class(original, source_class) if source_class else None
        after_model = _parse_java_class(transformed, source_class) if source_class else None
        object_model = _parse_java_class(transformed, object_name) if object_name else None
        before = None if before_model is None else next(
            (item for item in before_model.methods if item.name == method), None
        )
        after = None if after_model is None else next(
            (item for item in after_model.methods if item.name == method), None
        )
        if before is None or after is None or object_model is None:
            return {"passed": False, "reason": "target_or_parameter_object_missing"}
        used_before = {
            name for name in before.parameters
            if re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(name)}\b", before.body)
        }
        accessed_after = {
            match.group(1)
            for match in re.finditer(
                rf"\b{re.escape(parameter_name)}\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)\b",
                after.body,
            )
        }
        checks = {
            "parameter_object_created": bool(object_model),
            "fields_preserved": set(before.parameters) <= set(object_model.fields),
            "parameter_count_reduced": len(before.parameters) > len(after.parameters),
            "single_parameter_object_argument": after.parameters == [parameter_name],
            "body_access_migrated": used_before <= accessed_after,
        }
        return {
            "passed": all(checks.values()),
            "language": "java",
            "method": method,
            "before_parameter_count": len(before.parameters),
            "after_parameter_count": len(after.parameters),
            "fields": sorted(object_model.fields),
            "checks": checks,
        }

    @staticmethod
    def _java_tokens(code: str) -> List[str]:
        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[{}();.,=+-/*]", code)
