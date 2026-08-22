"""Structural similarity checks for transformed code."""

from __future__ import annotations

import ast
import re
import time
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

from ..constants import DEFAULT_STRUCTURAL_THRESHOLD_C, DEFAULT_STRUCTURAL_THRESHOLD_JAVA, DEFAULT_STRUCTURAL_THRESHOLD_PYTHON
from .c_support import compare_c_static_summaries, summarize_c_source
from ..models import ValidationStepResult
from ..contracts import RefactoringAction
from ..constants import (
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_REMOVE_DEAD_CODE,
    PARAMETER_OBJECT_ACTIONS,
)
from ..transformers.java_extract_class import _parse_java_class, declared_class_names
from ..utils.io_helpers import utc_now_iso
from ..utils.metrics import (
    cosine_similarity_from_counts,
    count_tokens,
    jaccard_similarity,
    normalized_count_similarity,
)


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
        specific_passed = all(
            item.get("passed")
            for item in [
                *parameter_object_checks,
                *dead_code_checks,
                *exception_handler_checks,
            ]
        )
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
                        else f"Structural validation failed with score {score:.3f} < {threshold:.3f}."
                    )
                )
            )
        )

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
                "exception_handler_validation": exception_handler_checks,
            },
            started_at=start_iso,
            finished_at=end_iso,
            duration_ms=duration_ms,
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
            return candidates[0] if len(candidates) == 1 else None
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
        target_name = method
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
                and self._python_exception_type_name(transformed_handler) == expected_target
            ),
            "handler_binding_preserved": (
                transformed_handler is not None
                and str(transformed_handler.name or "") == str(original_handler.name or "")
            ),
            "handler_body_preserved": bool(transformed_handler and transformed_handler.body),
        }
        return {
            "passed": all(checks.values()),
            "language": "python",
            "target_kind": "except_handler",
            "checks": checks,
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
        checks = {
            "target_existed_before": target_existed,
            "target_removed_after": target_existed and transformed != original,
            "no_required_referenced_code_removed": (
                not method or len(re.findall(rf"\b{re.escape(method)}\b", original)) == 1
            ),
            "unrelated_source_preserved": target_existed and expected == transformed,
        }
        return {
            "passed": all(checks.values()),
            "language": "c",
            "target_kind": "static_function" if method else "line_target",
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
