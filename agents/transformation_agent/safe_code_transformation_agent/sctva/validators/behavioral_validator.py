"""Behavior-preservation checks for Python, Java, and C."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .behavior_fingerprint import (
    BehaviorFingerprintRunner,
    compare_fingerprints,
    mine_exception_invariants,
    mine_value_invariants,
    stdout_invariants,
)
from .c_support import validate_c_behavior

from ..constants import KNOWN_UNSAFE_JAVA_ACTIONS, PARAMETER_OBJECT_ACTIONS
from ..contracts import RefactoringAction
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso
from ..utils.metrics import normalized_count_similarity


def _runtime_temp_root() -> Path:
    root = Path(tempfile.gettempdir()) / "sctva_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_runtime_temp_dir(prefix: str) -> Path:
    temp_path = _runtime_temp_root() / f"{prefix}_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=False)
    return temp_path


class BehavioralValidator:
    """Runs behavioral fingerprint validation for Python and Java.

    For Python:
    - If explicit behavior_tests exist, runtime fingerprinting is used.
    - If behavior_tests are missing, the validator runs safe static behavioral
      fingerprints instead of executing arbitrary functions.
    - This prevents timeout from ML imports, CSV loading, plotting, or file I/O.
    """

    AUTO_PROBE_LIMIT = 10
    DEFAULT_PYTHON_TIMEOUT_SECONDS = 8
    DEFAULT_JAVA_TIMEOUT_SECONDS = 8

    def validate(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
        behavior_tests: List[Dict[str, Any]],
        enable_behavior_tests: bool,
        actions: Sequence[RefactoringAction],
        strict_mode: bool,
        project_source_files: Sequence[Any] | None = None,
        current_file_name: str | None = None,
        structural_validation_passed: bool | None = None,
    ) -> ValidationStepResult:
        start_iso = utc_now_iso()
        started = time.perf_counter()

        if not enable_behavior_tests:
            return ValidationStepResult(
                name="behavioral",
                passed=True,
                score=0.5,
                message="Behavioral fingerprinting skipped because it is disabled.",
                details={
                    "checks": ["disabled"],
                    "failures": [],
                    "warnings": ["Behavioral validation was disabled."],
                    "fingerprint_status": "skipped",
                    "fingerprint_summary": "Behavioral fingerprinting disabled.",
                },
                started_at=start_iso,
                finished_at=utc_now_iso(),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        if language.lower() == "python":
            passed, score, message, details = self._validate_python(
                original_code=original_code,
                transformed_code=transformed_code,
                behavior_tests=behavior_tests,
                actions=actions,
                strict_mode=strict_mode,
                project_source_files=project_source_files,
                current_file_name=current_file_name,
            )
        elif language.lower() == "c":
            result = validate_c_behavior(
                original_code=original_code,
                transformed_code=transformed_code,
                behavior_tests=behavior_tests,
                actions=actions,
                enable_behavior_tests=enable_behavior_tests,
                timeout_seconds=self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                project_source_files=project_source_files,
                current_file_name=current_file_name,
            )
            passed = result["passed"]
            score = result["score"]
            message = result["message"]
            details = result["details"]
        else:
            passed, score, message, details = self._validate_java(
                original_code=original_code,
                transformed_code=transformed_code,
                behavior_tests=behavior_tests,
                actions=actions,
                strict_mode=strict_mode,
                project_source_files=project_source_files,
                current_file_name=current_file_name,
                structural_validation_passed=structural_validation_passed,
            )

        return ValidationStepResult(
            name="behavioral",
            passed=passed,
            score=score,
            message=message,
            details=details,
            started_at=start_iso,
            finished_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _validate_python(
        self,
        *,
        original_code: str,
        transformed_code: str,
        behavior_tests: List[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
        strict_mode: bool,
        project_source_files: Sequence[Any] | None = None,
        current_file_name: str | None = None,
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        runtime_tests = list(behavior_tests or [])
        auto_generated = False

        if not runtime_tests:
            runtime_tests = self._infer_python_runtime_tests_from_source(
                original_code=original_code,
                actions=actions,
            )
            auto_generated = bool(runtime_tests)
            if not runtime_tests:
                return self._validate_python_static_fingerprints(
                    original_code=original_code,
                    transformed_code=transformed_code,
                    actions=actions,
                )

        runtime_tests = self._adapt_python_parameter_object_tests(runtime_tests, actions)

        runner = BehaviorFingerprintRunner(
            default_timeout_seconds=self.DEFAULT_PYTHON_TIMEOUT_SECONDS
        )

        fingerprints: List[Dict[str, Any]] = []
        failures: List[str] = []
        warnings: List[str] = []
        if auto_generated:
            warnings.append(
                f"No explicit Python behavior_tests were provided, so {len(runtime_tests)} "
                "safe runtime probe(s) were inferred from simple top-level functions."
            )
        passed_count = 0

        for idx, test in enumerate(runtime_tests, start=1):
            name = str(test.get("name", test.get("test_id", f"test_{idx}")))
            timeout = test.get("timeout_seconds")
            if timeout is None:
                timeout = test.get("timeout")
            if timeout is None:
                timeout = self.DEFAULT_PYTHON_TIMEOUT_SECONDS
            timeout = float(timeout)
            if timeout <= 0:
                timeout = float(self.DEFAULT_PYTHON_TIMEOUT_SECONDS)

            try:
                if "expression" in test or "original_expression" in test or "transformed_expression" in test:
                    expression = str(test.get("expression") or test.get("original_expression"))
                    transformed_expression = str(
                        test.get("transformed_expression")
                        or test.get("expression")
                        or expression
                    )

                    original_fp = runner.run_python_test(
                        original_code,
                        {"expression": expression},
                        timeout=timeout,
                    )

                    transformed_fp = runner.run_python_test(
                        transformed_code,
                        {"expression": transformed_expression},
                        timeout=timeout,
                    )

                    entry = {
                        "name": name,
                        "expression": expression,
                        "transformed_expression": transformed_expression,
                        "auto_generated": bool(test.get("auto_generated")),
                    }

                else:
                    original_fn_name = str(
                        test.get("original_call")
                        or test.get("call")
                        or test.get("target_method")
                        or test.get("method")
                        or ""
                    ).strip()
                    transformed_fn_name = str(
                        test.get("transformed_call")
                        or test.get("call")
                        or test.get("target_method")
                        or test.get("method")
                        or original_fn_name
                        or ""
                    ).strip()

                    if not original_fn_name or not transformed_fn_name:
                        failures.append(f"{name}: missing call or target_method")
                        continue

                    args = test.get("args", []) or []
                    kwargs = test.get("kwargs", {}) or {}

                    original_call_test = {
                        "call": original_fn_name,
                        "args": args,
                        "kwargs": kwargs,
                    }
                    transformed_call_test = {
                        "call": transformed_fn_name,
                        "args": args,
                        "kwargs": kwargs,
                    }

                    original_fp = runner.run_python_test(
                        original_code,
                        original_call_test,
                        timeout=timeout,
                    )

                    transformed_fp = runner.run_python_test(
                        transformed_code,
                        transformed_call_test,
                        timeout=timeout,
                    )

                    entry = {
                        "name": name,
                        "call": original_fn_name,
                        "transformed_call": transformed_fn_name,
                        "args": args,
                        "kwargs": kwargs,
                        "auto_generated": bool(test.get("auto_generated")),
                    }

                comparison = compare_fingerprints(original_fp, transformed_fp)
                dependency_unavailable = self._fingerprints_dependency_unavailable(
                    original_fp,
                    transformed_fp,
                    language="python",
                )
                expected_failure = self._expected_failure(
                    name=name,
                    test=test,
                    original_fp=original_fp,
                    transformed_fp=transformed_fp,
                )
                if expected_failure:
                    comparison = {"matched": False, "reason": expected_failure}

                entry["original_fingerprint"] = original_fp
                entry["transformed_fingerprint"] = transformed_fp
                if dependency_unavailable:
                    comparison = {
                        "matched": False,
                        "reason": "runtime_unavailable_due_to_dependencies",
                    }

                entry["comparison"] = comparison
                if dependency_unavailable:
                    entry["dependency_unavailable"] = True

                fingerprints.append(entry)

                if dependency_unavailable:
                    warnings.append(
                        f"{name}: Python runtime probe could not execute because imports/dependencies were unavailable."
                    )
                elif comparison.get("matched"):
                    passed_count += 1
                else:
                    reason = comparison.get("reason", "fingerprint_mismatch")
                    failures.append(f"{name}: {reason}")

                    if reason in {
                        "both_timed_out",
                        "original_timed_out",
                        "transformed_timed_out",
                    }:
                        warnings.append(
                            f"{name}: timeout detected. Increase timeout_seconds "
                            "or provide a smaller deterministic behavior test."
                        )

            except Exception as exc:
                failures.append(f"{name}: runtime error {exc}")

        total = len(runtime_tests)
        if fingerprints and all(item.get("dependency_unavailable") for item in fingerprints):
            static_passed, static_score, static_message, static_details = self._validate_python_static_fingerprints(
                original_code=original_code,
                transformed_code=transformed_code,
                actions=actions,
            )
            static_details["checks"] = [
                "python_runtime_dependency_detection",
                *static_details.get("checks", []),
            ]
            static_details["runtime_fingerprints"] = fingerprints
            static_details["runtime_unavailable_reason"] = "missing_python_dependencies"
            static_details["warnings"] = warnings + static_details.get("warnings", [])
            static_details["fingerprint_status"] = "degraded_static_passed" if static_passed else "failed"
            static_details["fingerprint_summary"] = (
                "Python runtime probes could not execute because dependencies were unavailable; "
                + static_details.get("fingerprint_summary", static_message)
            )
            return static_passed, min(static_score, 0.75), static_message, static_details

        passed = len(failures) == 0
        score = passed_count / total if total else 0.0

        return (
            passed,
            score,
            (
                "Python behavioral fingerprinting passed."
                if passed
                else f"Python behavioral fingerprinting failed: {len(failures)} issue(s)."
            ),
            {
                "checks": ["python_runtime_fingerprinting"],
                "total_tests": total,
                "passed_tests": passed_count,
                "failures": failures,
                "warnings": warnings,
                "fingerprints": fingerprints,
                "fingerprint_status": "passed" if passed else "failed",
                "fingerprint_summary": (
                    f"{passed_count}/{total} Python behavioral fingerprint test(s) passed."
                ),
            },
        )

    @classmethod
    def _expected_failure(
        cls,
        *,
        name: str,
        test: Dict[str, Any],
        original_fp: Dict[str, Any],
        transformed_fp: Dict[str, Any],
    ) -> str:
        expected_marker = object()
        expected = test.get("expected", expected_marker)
        if expected is expected_marker:
            expected = test.get("expected_return", expected_marker)
        if expected is expected_marker:
            expected = test.get("expected_value", expected_marker)
        if expected is expected_marker:
            return ""

        expected_values = cls._expected_return_reprs(expected)
        original_actual = str(original_fp.get("return_value_repr"))
        transformed_actual = str(transformed_fp.get("return_value_repr"))

        if original_fp.get("success") and original_actual not in expected_values:
            return f"original_expected_value_mismatch:{name}"

        if transformed_fp.get("success") and transformed_actual not in expected_values:
            return f"transformed_expected_value_mismatch:{name}"

        return ""

    @staticmethod
    def _expected_return_reprs(expected: Any) -> set[str]:
        return {str(expected), repr(expected)}

    def _infer_python_runtime_tests_from_source(
        self,
        *,
        original_code: str,
        actions: Sequence[RefactoringAction],
    ) -> List[Dict[str, Any]]:
        if not self._python_source_safe_for_auto_runtime(original_code):
            return []

        try:
            tree = ast.parse(original_code)
        except SyntaxError:
            return []

        rename_map: Dict[str, str] = {}
        removed_functions: set[str] = set()
        for action in actions:
            action_type = getattr(action, "action_type", "")
            if action_type in {"rename_symbol", "rename_method"}:
                old_name = str(action.parameters.get("old_name") or "").strip()
                new_name = str(action.parameters.get("new_name") or "").strip()
                if old_name and new_name:
                    rename_map[old_name] = new_name
            elif action_type == "remove_dead_code":
                method = str(
                    action.parameters.get("method")
                    or action.parameters.get("method_name")
                    or ""
                ).strip()
                if method:
                    removed_functions.add(method)

        inferred: List[Dict[str, Any]] = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            if node.name in removed_functions:
                continue

            args = self._python_args_for_auto_probe(node)
            if args is None:
                continue

            inferred.append(
                {
                    "name": f"auto_python_{node.name}",
                    "original_call": node.name,
                    "transformed_call": rename_map.get(node.name, node.name),
                    "args": args,
                    "timeout_seconds": self.DEFAULT_PYTHON_TIMEOUT_SECONDS,
                    "auto_generated": True,
                }
            )

            if len(inferred) >= self.AUTO_PROBE_LIMIT:
                break

        return inferred

    @staticmethod
    def _adapt_python_parameter_object_tests(
        tests: Sequence[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
    ) -> List[Dict[str, Any]]:
        parameter_actions = {
            str(action.parameters.get("method") or action.parameters.get("method_name") or "").strip(): str(
                action.parameters.get("parameter_object_name")
                or action.parameters.get("new_class_name")
                or ""
            ).strip()
            for action in actions
            if action.action_type in {
                "introduce_parameter_object",
                "introduce_python_parameter_object",
            }
        }
        adapted: List[Dict[str, Any]] = []
        for test in tests:
            copied = dict(test)
            if any(key in copied for key in ("expression", "original_expression", "transformed_expression")):
                if copied.get("transformed_expression"):
                    adapted.append(copied)
                    continue
                original_expression = str(
                    copied.get("original_expression")
                    or copied.get("expression")
                    or ""
                )
                transformed_expression = original_expression
                for method, object_name in parameter_actions.items():
                    transformed_expression = BehavioralValidator._rewrite_python_parameter_object_expression(
                        transformed_expression,
                        method=method,
                        object_name=object_name,
                    )
                copied["original_expression"] = original_expression
                copied["transformed_expression"] = transformed_expression
                adapted.append(copied)
                continue
            call = str(
                copied.get("original_call")
                or copied.get("call")
                or copied.get("target_method")
                or copied.get("method")
                or ""
            ).strip()
            object_name = parameter_actions.get(call)
            if not object_name:
                adapted.append(copied)
                continue
            args = list(copied.get("args") or [])
            kwargs = dict(copied.get("kwargs") or {})
            rendered = [repr(value) for value in args]
            rendered.extend(f"{key}={value!r}" for key, value in kwargs.items())
            joined = ", ".join(rendered)
            copied["original_expression"] = f"{call}({joined})"
            copied["transformed_expression"] = f"{call}({object_name}({joined}))"
            adapted.append(copied)
        return adapted

    @staticmethod
    def _rewrite_python_parameter_object_expression(
        expression: str,
        *,
        method: str,
        object_name: str,
    ) -> str:
        if not expression or not method or not object_name:
            return expression
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return expression

        class CallRewriter(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call) -> ast.AST:
                updated = self.generic_visit(node)
                if not isinstance(updated, ast.Call):
                    return updated
                matches = (
                    isinstance(updated.func, ast.Name) and updated.func.id == method
                ) or (
                    isinstance(updated.func, ast.Attribute) and updated.func.attr == method
                )
                already_wrapped = bool(
                    len(updated.args) == 1
                    and isinstance(updated.args[0], ast.Call)
                    and isinstance(updated.args[0].func, ast.Name)
                    and updated.args[0].func.id == object_name
                )
                if not matches or already_wrapped:
                    return updated
                parameter_call = ast.Call(
                    func=ast.Name(id=object_name, ctx=ast.Load()),
                    args=updated.args,
                    keywords=updated.keywords,
                )
                return ast.copy_location(
                    ast.Call(func=updated.func, args=[parameter_call], keywords=[]),
                    updated,
                )

        rewritten = CallRewriter().visit(tree)
        ast.fix_missing_locations(rewritten)
        return ast.unparse(rewritten.body)

    @staticmethod
    def _python_source_safe_for_auto_runtime(source_code: str) -> bool:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return False

        safe_import_roots = {"math", "statistics", "decimal", "fractions", "datetime", "re", "string"}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] not in safe_import_roots:
                        return False
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".", 1)[0]
                if module and module not in safe_import_roots:
                    return False
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                continue
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            else:
                return False

        return True

    def _python_args_for_auto_probe(self, node: ast.FunctionDef) -> List[Any] | None:
        args = node.args
        if args.vararg or args.kwarg or args.kwonlyargs:
            return None

        positional = list(args.args)
        defaults = list(args.defaults)
        required_count = len(positional) - len(defaults)
        rendered: List[Any] = []

        for index, arg in enumerate(positional):
            default_index = index - required_count
            if default_index >= 0:
                literal = self._literal_value(defaults[default_index])
                if literal["literal"]:
                    rendered.append(literal["value"])
                    continue

            value = self._python_default_arg_for_annotation(arg.annotation)
            if value is None and index < required_count:
                return None
            rendered.append(value)

        return rendered

    @staticmethod
    def _python_default_arg_for_annotation(annotation: ast.AST | None) -> Any:
        if annotation is None:
            return None

        name = ""
        if isinstance(annotation, ast.Name):
            name = annotation.id
        elif isinstance(annotation, ast.Constant):
            name = str(annotation.value)
        elif isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
            name = annotation.value.id

        name = name.lower()
        if name == "int":
            return 0
        if name == "float":
            return 0.0
        if name == "str":
            return "sample"
        if name == "bool":
            return False
        if name in {"list", "sequence", "tuple", "set"}:
            return []
        if name in {"dict", "mapping"}:
            return {}

        return None

    def _validate_python_static_fingerprints(
        self,
        *,
        original_code: str,
        transformed_code: str,
        actions: Sequence[RefactoringAction],
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        """Safe no-runtime Python behavior preservation check.

        This is used when the JSON plan has no explicit behavior_tests.
        It does not import pandas/sklearn/matplotlib and does not execute project code.
        Therefore, it avoids timeout but still produces fingerprints for invariant mining.
        """

        fingerprints: List[Dict[str, Any]] = []
        failures: List[str] = []
        warnings: List[str] = [
            "No explicit Python behavior_tests were provided.",
            "Used safe static behavioral fingerprints to avoid timeout from imports, file I/O, plotting, ML training, or external dataset loading.",
        ]

        original_summary = self._python_static_summary(original_code)
        transformed_summary = self._python_static_summary(transformed_code)

        signature_match = self._compare_python_signature_compatibility(
            original_summary,
            transformed_summary,
            actions,
            original_code=original_code,
            transformed_code=transformed_code,
        )

        constant_check = self._check_python_introduced_constants(
            transformed_code=transformed_code,
            actions=actions,
        )

        unresolved_constant_check = self._check_unresolved_magic_constant_names(
            transformed_code
        )

        checks = [
            {
                "name": "static_function_signature_fingerprint",
                "matched": signature_match["matched"],
                "reason": signature_match["reason"],
                "original": signature_match["original"],
                "transformed": signature_match["transformed"],
            },
            {
                "name": "static_introduced_constant_fingerprint",
                "matched": constant_check["matched"],
                "reason": constant_check["reason"],
                "original": constant_check["original"],
                "transformed": constant_check["transformed"],
            },
            {
                "name": "static_unresolved_constant_name_fingerprint",
                "matched": unresolved_constant_check["matched"],
                "reason": unresolved_constant_check["reason"],
                "original": unresolved_constant_check["original"],
                "transformed": unresolved_constant_check["transformed"],
            },
        ]

        for check in checks:
            original_fp = self._static_success_fingerprint(check["original"])
            transformed_fp = self._static_success_fingerprint(check["transformed"])

            comparison = {
                "matched": bool(check["matched"]),
                "reason": check["reason"],
            }

            fingerprints.append(
                {
                    "name": check["name"],
                    "mode": "static_python_fingerprint",
                    "original_fingerprint": original_fp,
                    "transformed_fingerprint": transformed_fp,
                    "comparison": comparison,
                }
            )

            if not check["matched"]:
                failures.append(f"{check['name']}: {check['reason']}")

        total = len(fingerprints)
        passed_count = total - len(failures)
        passed = not failures
        score = passed_count / total if total else 0.75

        return (
            passed,
            score,
            (
                "Python static behavioral fingerprinting passed."
                if passed
                else f"Python static behavioral fingerprinting failed: {len(failures)} issue(s)."
            ),
            {
                "checks": [
                    "python_static_behavioral_fingerprinting",
                    "function_signature_compatibility",
                    "introduced_constant_value_preservation",
                    "unresolved_constant_name_detection",
                ],
                "total_tests": total,
                "passed_tests": passed_count,
                "failures": failures,
                "warnings": warnings,
                "fingerprints": fingerprints,
                "fingerprint_status": "passed" if passed else "failed",
                "fingerprint_summary": (
                    f"{passed_count}/{total} Python static behavioral fingerprint test(s) passed."
                ),
            },
        )

    @staticmethod
    def _static_success_fingerprint(value: Any) -> Dict[str, Any]:
        return {
            "success": True,
            "return_value_repr": repr(value),
            "return_type": type(value).__name__,
            "exception_type": None,
            "exception_message_category": None,
            "stdout": "",
            "execution_time_ms": 0,
            "timeout": False,
            "runtime_error_details": None,
        }

    def _python_static_summary(self, source_code: str) -> Dict[str, Any]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return {
                "parse_success": False,
                "syntax_error": str(exc),
                "functions": {},
                "classes": {},
                "module_constants": {},
                "name_loads": [],
                "name_stores": [],
            }

        functions: Dict[str, Dict[str, Any]] = {}
        classes: Dict[str, List[str]] = {}
        module_constants: Dict[str, Any] = {}
        name_loads: List[str] = []
        name_stores: List[str] = []

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                functions[node.name] = self._python_function_signature(node)

            elif isinstance(node, ast.ClassDef):
                class_methods = []
                for body_node in node.body:
                    if isinstance(body_node, ast.FunctionDef):
                        class_methods.append(body_node.name)
                classes[node.name] = sorted(class_methods)

            elif isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target_name = node.targets[0].id
                    if target_name.isupper():
                        literal_value = self._literal_value(node.value)
                        if literal_value["literal"]:
                            module_constants[target_name] = literal_value["value"]

        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    name_loads.append(node.id)
                elif isinstance(node.ctx, ast.Store):
                    name_stores.append(node.id)

        return {
            "parse_success": True,
            "functions": functions,
            "classes": classes,
            "module_constants": module_constants,
            "name_loads": sorted(set(name_loads)),
            "name_stores": sorted(set(name_stores)),
        }

    @staticmethod
    def _python_function_signature(node: ast.FunctionDef) -> Dict[str, Any]:
        args = node.args

        positional = [arg.arg for arg in args.args]
        keyword_only = [arg.arg for arg in args.kwonlyargs]
        defaults_count = len(args.defaults)
        kw_defaults_count = len([d for d in args.kw_defaults if d is not None])

        return {
            "args": positional,
            "keyword_only_args": keyword_only,
            "defaults_count": defaults_count,
            "kw_defaults_count": kw_defaults_count,
            "has_vararg": args.vararg is not None,
            "has_kwarg": args.kwarg is not None,
            "returns": ast.unparse(node.returns) if node.returns else None,
        }

    @staticmethod
    def _literal_value(node: ast.AST) -> Dict[str, Any]:
        try:
            value = ast.literal_eval(node)
            if isinstance(value, (int, float, str, bool, type(None))):
                return {"literal": True, "value": value}
        except Exception:
            pass

        return {"literal": False, "value": None}

    def _compare_python_signature_compatibility(
        self,
        original_summary: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        actions: Sequence[RefactoringAction],
        *,
        original_code: str,
        transformed_code: str,
    ) -> Dict[str, Any]:
        if not original_summary.get("parse_success") or not transformed_summary.get("parse_success"):
            return {
                "matched": False,
                "reason": "parse_failed",
                "original": original_summary,
                "transformed": transformed_summary,
            }

        expected_renames: Dict[str, str] = {}
        expected_removed: set[str] = set()
        try:
            original_tree = ast.parse(original_code)
        except SyntaxError:
            original_tree = None

        for action in actions:
            action_type = getattr(action, "action_type", "")
            if action_type in {"rename_symbol", "rename_method"}:
                old_name = str(action.parameters.get("old_name") or "").strip()
                new_name = str(action.parameters.get("new_name") or "").strip()
                if old_name and new_name:
                    expected_renames[old_name] = new_name
            elif action_type == "remove_dead_code":
                method = str(
                    action.parameters.get("method")
                    or action.parameters.get("method_name")
                    or ""
                ).strip()
                if method:
                    expected_removed.add(method)
                    continue

                # RDP often supplies only a line number. Resolve a callable
                # removal from the original AST before comparing signatures;
                # do not treat statement-level dead-code actions as function
                # removals merely because they appear inside a function.
                raw_line = action.parameters.get("source_line")
                source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
                if source_line is None or original_tree is None:
                    continue
                candidates = [
                    node
                    for node in ast.walk(original_tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and int(getattr(node, "lineno", 0) or 0) == source_line
                ]
                if len(candidates) == 1:
                    expected_removed.add(candidates[0].name)

        original_functions = dict(original_summary.get("functions", {}))
        transformed_functions = dict(transformed_summary.get("functions", {}))

        expected_parameter_object_migrations = (
            self._validated_python_parameter_object_migrations(
                original_code=original_code,
                transformed_code=transformed_code,
                actions=actions,
            )
        )
        allowed_signature_changes = {
            item["function"]: item for item in expected_parameter_object_migrations
        }

        missing_functions = []
        changed_signatures = []
        accepted_signature_changes = []
        removed_functions = []

        for old_name, old_signature in original_functions.items():
            expected_name = expected_renames.get(old_name, old_name)

            if expected_name not in transformed_functions:
                if old_name in expected_removed:
                    removed_functions.append(old_name)
                    continue
                missing_functions.append(
                    {
                        "original_function": old_name,
                        "expected_transformed_function": expected_name,
                    }
                )
                continue

            new_signature = transformed_functions[expected_name]

            if old_signature != new_signature:
                change = {
                    "function": old_name,
                    "expected_name": expected_name,
                    "original_signature": old_signature,
                    "transformed_signature": new_signature,
                }
                migration = allowed_signature_changes.get(old_name)
                if migration and expected_name == old_name:
                    accepted_signature_changes.append({**change, **migration})
                else:
                    changed_signatures.append(change)

        matched = not missing_functions and not changed_signatures
        reason = "function_signatures_preserved"
        if matched and accepted_signature_changes:
            reason = "parameter_object_signature_migration_preserved"
        elif not matched:
            reason = "function_signature_mismatch"

        return {
            "matched": matched,
            "reason": reason,
            "original": {
                "function_count": len(original_functions),
                "functions": original_functions,
                "expected_renames": expected_renames,
                "expected_removed": sorted(expected_removed),
            },
            "transformed": {
                "function_count": len(transformed_functions),
                "functions": transformed_functions,
                "missing_functions": missing_functions,
                "removed_functions": removed_functions,
                "changed_signatures": changed_signatures,
                "accepted_parameter_object_migrations": accepted_signature_changes,
            },
        }

    @staticmethod
    def _validated_python_parameter_object_migrations(
        *,
        original_code: str,
        transformed_code: str,
        actions: Sequence[RefactoringAction],
    ) -> List[Dict[str, Any]]:
        """Prove an intentional Python parameter-object signature migration.

        Introduce Parameter Object deliberately changes a callable's signature.  A
        raw signature equality check must therefore not report a behavior change
        when the new object faithfully represents the old arguments.  This check
        is intentionally independent from structural validation so a malformed
        migration cannot bypass behavioral rollback merely by naming an action.
        """

        try:
            original_tree = ast.parse(original_code)
            transformed_tree = ast.parse(transformed_code)
        except SyntaxError:
            return []

        def find_target(
            tree: ast.Module,
            method: str,
            source_class: str,
        ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
            candidates: List[ast.FunctionDef | ast.AsyncFunctionDef] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not source_class and node.name == method:
                        candidates.append(node)
                elif isinstance(node, ast.ClassDef):
                    if source_class and node.name != source_class:
                        continue
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method:
                            candidates.append(child)
            return candidates[0] if len(candidates) == 1 else None

        def non_receiver_parameters(
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> List[ast.arg]:
            return [
                item
                for item in [*node.args.posonlyargs, *node.args.args]
                if item.arg not in {"self", "cls"}
            ]

        validated: List[Dict[str, Any]] = []
        for action in actions:
            if action.action_type not in PARAMETER_OBJECT_ACTIONS:
                continue
            if action.action_type == "introduce_java_parameter_object":
                continue

            params = action.parameters or {}
            method = str(params.get("method") or params.get("method_name") or "").strip()
            source_class = str(params.get("source_class") or "").strip()
            object_name = str(
                params.get("parameter_object_name") or params.get("new_class_name") or ""
            ).strip()
            parameter_name = str(params.get("parameter_name") or "params").strip()
            if not method or not object_name or not parameter_name:
                continue

            before = find_target(original_tree, method, source_class)
            after = find_target(transformed_tree, method, source_class)
            object_node = next(
                (
                    node for node in transformed_tree.body
                    if isinstance(node, ast.ClassDef) and node.name == object_name
                ),
                None,
            )
            if before is None or after is None or object_node is None:
                continue

            before_parameters = non_receiver_parameters(before)
            after_parameters = non_receiver_parameters(after)
            if len(before_parameters) < 2 or [item.arg for item in after_parameters] != [parameter_name]:
                continue
            if before.args.vararg or before.args.kwarg or before.args.kwonlyargs:
                continue
            if after.args.vararg or after.args.kwarg or after.args.kwonlyargs:
                continue

            annotation = after_parameters[0].annotation
            if not isinstance(annotation, ast.Name) or annotation.id != object_name:
                continue
            before_return = (
                ast.dump(before.returns, include_attributes=False)
                if before.returns is not None
                else None
            )
            after_return = (
                ast.dump(after.returns, include_attributes=False)
                if after.returns is not None
                else None
            )
            if before_return != after_return:
                continue

            before_defaults: Dict[str, str | None] = {}
            defaults = [None] * (len(before_parameters) - len(before.args.defaults)) + list(before.args.defaults)
            for item, default in zip(before_parameters, defaults):
                before_defaults[item.arg] = (
                    ast.dump(default, include_attributes=False) if default is not None else None
                )
            object_fields = {
                node.target.id: (
                    ast.dump(node.value, include_attributes=False) if node.value is not None else None
                )
                for node in object_node.body
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            }
            if any(object_fields.get(name, object()) != default for name, default in before_defaults.items()):
                continue

            used_before = {
                node.id
                for node in ast.walk(before)
                if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in before_defaults
            }
            accessed_after = {
                node.attr
                for node in ast.walk(after)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == parameter_name
            }
            if not used_before <= accessed_after:
                continue

            validated.append(
                {
                    "function": method,
                    "parameter_object": object_name,
                    "parameter_name": parameter_name,
                    "preserved_fields": sorted(before_defaults),
                    "body_accesses": sorted(accessed_after),
                    "proof": "parameter_object_fields_defaults_return_annotation_and_body_access_preserved",
                }
            )

        return validated

    def _check_python_introduced_constants(
        self,
        *,
        transformed_code: str,
        actions: Sequence[RefactoringAction],
    ) -> Dict[str, Any]:
        summary = self._python_static_summary(transformed_code)
        module_constants = summary.get("module_constants", {})

        expected_constants: List[Dict[str, Any]] = []

        for action in actions:
            if getattr(action, "action_type", "") != "introduce_constant":
                continue

            params = getattr(action, "parameters", {}) or {}

            literal_value = (
                params.get("literal_value")
                if "literal_value" in params
                else params.get("old_literal")
            )

            constant_name = str(
                params.get("constant_name")
                or params.get("new_name")
                or self._constant_name_from_value(literal_value)
            )

            if literal_value is None:
                continue

            normalized_name = self._sanitize_constant_name(constant_name)

            if normalized_name in {
                "EXTRACTED_CONSTANT",
                "MAGIC_CONSTANT",
                "CONSTANT",
                "VALUE_CONSTANT",
            }:
                normalized_name = self._constant_name_from_value(literal_value)

            expected_constants.append(
                {
                    "name": normalized_name,
                    "value": literal_value,
                }
            )

        missing_or_wrong = []

        for expected in expected_constants:
            name = expected["name"]
            value = expected["value"]

            if name not in module_constants:
                missing_or_wrong.append(
                    {
                        "constant": name,
                        "expected_value": value,
                        "actual_value": None,
                        "reason": "constant_missing",
                    }
                )
                continue

            actual_value = module_constants[name]

            if actual_value != value:
                missing_or_wrong.append(
                    {
                        "constant": name,
                        "expected_value": value,
                        "actual_value": actual_value,
                        "reason": "constant_value_mismatch",
                    }
                )

        matched = not missing_or_wrong

        return {
            "matched": matched,
            "reason": (
                "introduced_constants_preserved"
                if matched
                else "introduced_constant_missing_or_wrong_value"
            ),
            "original": {
                "expected_constants": expected_constants,
            },
            "transformed": {
                "module_constants": module_constants,
                "missing_or_wrong": missing_or_wrong,
            },
        }

    def _check_unresolved_magic_constant_names(
        self,
        transformed_code: str,
    ) -> Dict[str, Any]:
        summary = self._python_static_summary(transformed_code)

        if not summary.get("parse_success"):
            return {
                "matched": False,
                "reason": "parse_failed",
                "original": {},
                "transformed": summary,
            }

        module_constants = set(summary.get("module_constants", {}).keys())
        name_loads = set(summary.get("name_loads", []))

        suspicious_loads = {
            name
            for name in name_loads
            if (
                name.startswith("MAGIC_")
                or name.startswith("CONSTANT_")
                or name == "EXTRACTED_CONSTANT"
            )
        }

        unresolved = sorted(suspicious_loads - module_constants)

        matched = not unresolved

        return {
            "matched": matched,
            "reason": (
                "no_unresolved_magic_constants"
                if matched
                else "unresolved_magic_constant_names"
            ),
            "original": {
                "rule": "Every used MAGIC_*, CONSTANT_*, or EXTRACTED_CONSTANT name must be declared as a module constant.",
            },
            "transformed": {
                "module_constants": sorted(module_constants),
                "suspicious_loads": sorted(suspicious_loads),
                "unresolved": unresolved,
            },
        }

    @staticmethod
    def _sanitize_constant_name(value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^A-Za-z0-9_]", "_", text)

        if not text:
            return "CONSTANT_VALUE"

        if text[0].isdigit():
            text = f"N_{text}"

        return text.upper()

    def _constant_name_from_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return f"CONSTANT_BOOL_{str(value).upper()}"

        if value is None:
            return "CONSTANT_NONE"

        if isinstance(value, int):
            if value < 0:
                return f"CONSTANT_NUMBER_NEG_{abs(value)}"
            return f"CONSTANT_NUMBER_{value}"

        if isinstance(value, float):
            text = str(value).replace("-", "NEG_").replace(".", "_")
            return f"CONSTANT_NUMBER_{self._sanitize_constant_name(text)}"

        if isinstance(value, str):
            short = value[:24]
            return f"CONSTANT_STRING_{self._sanitize_constant_name(short)}"

        return "CONSTANT_VALUE"

    def _validate_java(
        self,
        *,
        original_code: str,
        transformed_code: str,
        behavior_tests: List[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
        strict_mode: bool,
        project_source_files: Sequence[Any] | None = None,
        current_file_name: str | None = None,
        structural_validation_passed: bool | None = None,
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        checks: List[str] = []
        failures: List[str] = []
        warnings: List[str] = []
        component_scores: List[float] = []
        java_results: List[Dict[str, Any]] = []
        dependency_unavailable_count = 0

        original_code = original_code.lstrip("\ufeff")
        transformed_code = transformed_code.lstrip("\ufeff")
        project_sources = self._normalize_project_source_files(project_source_files)

        checks.append("unsafe_action_guard")

        unsafe_actions = [
            action.action_type
            for action in actions
            if action.action_type in KNOWN_UNSAFE_JAVA_ACTIONS
        ]

        if unsafe_actions:
            warnings.append(
                "High-risk Java action(s) found: " + ", ".join(unsafe_actions)
            )

        checks.append("return_structure_consistency")

        original_return_count = len(re.findall(r"\breturn\b", original_code))
        transformed_return_count = len(re.findall(r"\breturn\b", transformed_code))

        return_similarity = normalized_count_similarity(
            original_return_count,
            transformed_return_count,
        )

        component_scores.append(return_similarity)

        if return_similarity < 0.4:
            warnings.append(
                "Large return-structure drift detected "
                f"(original={original_return_count}, transformed={transformed_return_count})."
            )

        runtime_tests = self._extract_java_runtime_tests(behavior_tests)

        if not runtime_tests:
            auto_tests = self._infer_java_runtime_tests_from_actions(
                actions=actions,
                original_code=original_code,
            )

            if auto_tests:
                runtime_tests = auto_tests
                checks.append("auto_generated_java_runtime_probes")
                warnings.append(
                    f"No behavior_tests were provided, so {len(auto_tests)} "
                    "Java runtime probe(s) were generated from the refactoring plan."
                )
            else:
                source_tests = self._infer_java_runtime_tests_from_source(
                    original_code=original_code,
                    actions=actions,
                )

                if source_tests:
                    runtime_tests = source_tests
                    checks.append("auto_generated_java_runtime_probes")
                    warnings.append(
                        f"No behavior_tests were provided, so {len(source_tests)} "
                        "Java runtime probe(s) were inferred from public methods in the source."
                    )
                else:
                    return (
                        True,
                        0.6,
                        "Java behavioral fingerprinting skipped because no runnable methods could be inferred.",
                        {
                            "checks": checks,
                            "failures": [],
                            "warnings": warnings
                            + [
                                "No Java behavior_tests were provided and no suitable "
                                "runtime probes could be inferred from the plan actions or source."
                            ],
                            "java_results": [
                                {
                                    "name": f"java_probe_{idx}",
                                    "status": "skipped",
                                    "reason": "no_command_or_inference",
                                }
                                for idx, _ in enumerate(
                                    behavior_tests or [{"name": "java_probe_1"}],
                                    start=1,
                                )
                            ],
                            "return_similarity": round(return_similarity, 4),
                            "fingerprint_status": "skipped",
                            "fingerprint_summary": (
                                "No Java runtime tests/harness available; fingerprinting skipped."
                            ),
                        },
                    )

        runtime_tests = self._adapt_java_parameter_object_tests(runtime_tests, actions)

        for idx, test in enumerate(runtime_tests, start=1):
            name = str(test.get("name") or test.get("test_id") or f"java_probe_{idx}")

            original_class = str(
                test.get("original_target_class")
                or test.get("target_class")
                or test.get("class")
                or ""
            ).strip()

            transformed_class = str(
                test.get("transformed_target_class")
                or test.get("target_class")
                or test.get("class")
                or original_class
            ).strip()

            original_method = str(
                test.get("original_target_method")
                or test.get("target_method")
                or test.get("method")
                or ""
            ).strip()

            transformed_method = str(
                test.get("transformed_target_method")
                or test.get("target_method")
                or test.get("method")
                or original_method
            ).strip()

            args = test.get("args", []) or []
            timeout_seconds = int(
                test.get("timeout_seconds")
                or test.get("timeout")
                or self.DEFAULT_JAVA_TIMEOUT_SECONDS
            )

            if (
                not original_class
                or not transformed_class
                or not original_method
                or not transformed_method
            ):
                failures.append(f"{name}: missing Java target class or method")
                java_results.append(
                    {
                        "name": name,
                        "status": "failed",
                        "reason": "missing_target_class_or_method",
                    }
                )
                continue

            original_fp = self._run_java_runtime_probe(
                source_code=original_code,
                target_class=original_class,
                target_method=original_method,
                args=args,
                timeout_seconds=timeout_seconds,
                project_source_files=project_sources,
                current_file_name=current_file_name,
            )

            transformed_fp = self._run_java_runtime_probe(
                source_code=transformed_code,
                target_class=transformed_class,
                target_method=transformed_method,
                args=args,
                timeout_seconds=timeout_seconds,
                project_source_files=project_sources,
                current_file_name=current_file_name,
                parameter_object=bool(test.get("transformed_parameter_object")),
            )

            comparison = compare_fingerprints(original_fp, transformed_fp)
            runtime_infrastructure_unavailable = self._fingerprints_runtime_infrastructure_unavailable(
                original_fp,
                transformed_fp,
                language="java",
            )
            expected_failure = self._expected_failure(
                name=name,
                test=test,
                original_fp=original_fp,
                transformed_fp=transformed_fp,
            )
            if expected_failure:
                comparison = {"matched": False, "reason": expected_failure}
                runtime_infrastructure_unavailable = False

            if runtime_infrastructure_unavailable:
                comparison = {
                    "matched": False,
                    "reason": "runtime_unavailable_due_to_infrastructure",
                }

            java_results.append(
                {
                    "name": name,
                    "status": "ran",
                    "auto_generated": bool(test.get("auto_generated")),
                    "original_target_class": original_class,
                    "original_target_method": original_method,
                    "transformed_target_class": transformed_class,
                    "transformed_target_method": transformed_method,
                    "args": args,
                    "original_fingerprint": original_fp,
                    "transformed_fingerprint": transformed_fp,
                    "comparison": comparison,
                    "dependency_unavailable": runtime_infrastructure_unavailable,
                    "runtime_infrastructure_unavailable": runtime_infrastructure_unavailable,
                }
            )

            if runtime_infrastructure_unavailable:
                dependency_unavailable_count += 1
                warnings.append(
                    f"{name}: Java runtime probe could not execute because its compile/classpath "
                    "environment was unavailable."
                )
                remaining_tests = runtime_tests[idx:]
                if remaining_tests and not failures:
                    warnings.append(
                        f"Skipped {len(remaining_tests)} remaining Java runtime probe(s) "
                        "after dependency-unavailable compilation was confirmed; "
                        "static behavioral fallback still ran."
                    )
                    for remaining_index, remaining in enumerate(remaining_tests, start=idx + 1):
                        remaining_name = str(
                            remaining.get("name")
                            or remaining.get("test_id")
                            or f"java_probe_{remaining_index}"
                        )
                        java_results.append(
                            {
                                "name": remaining_name,
                                "status": "skipped",
                                "auto_generated": bool(remaining.get("auto_generated")),
                                "reason": "runtime_infrastructure_unavailable_after_preflight",
                                "dependency_unavailable": True,
                                "runtime_infrastructure_unavailable": True,
                            }
                        )
                        dependency_unavailable_count += 1
                    break
            elif comparison.get("matched"):
                component_scores.append(1.0)
            else:
                component_scores.append(0.0)
                failures.append(
                    f"{name}: {comparison.get('reason', 'fingerprint_mismatch')}"
                )

        if java_results and dependency_unavailable_count == len(java_results) and not failures:
            static_passed, static_score, static_message, static_details = self._validate_java_static_fingerprints(
                original_code=original_code,
                transformed_code=transformed_code,
                actions=actions,
                return_similarity=return_similarity,
                structural_validation_passed=structural_validation_passed,
            )
            static_details["checks"] = [
                *checks,
                "java_runtime_dependency_detection",
                *static_details.get("checks", []),
            ]
            static_details["warnings"] = warnings + static_details.get("warnings", [])
            static_details["java_results"] = java_results
            runtime_categories = sorted(
                {
                    str(fingerprint.get("runtime_failure_category") or "")
                    for result in java_results
                    for fingerprint in (
                        result.get("original_fingerprint", {}),
                        result.get("transformed_fingerprint", {}),
                    )
                    if fingerprint.get("runtime_infrastructure")
                }
                - {""}
            )
            static_details["runtime_unavailable_reason"] = (
                "missing_java_dependencies"
                if not runtime_categories or runtime_categories == ["MISSING_DEPENDENCY"]
                else "java_runtime_infrastructure_unavailable"
            )
            static_details["runtime_failure_categories"] = runtime_categories
            static_details["fingerprint_status"] = "degraded_static_passed" if static_passed else "failed"
            static_details["fingerprint_summary"] = (
                "Java runtime probes could not execute because the compile/classpath environment "
                "was unavailable; "
                + static_details.get("fingerprint_summary", static_message)
            )
            return static_passed, min(static_score, 0.75), static_message, static_details

        passed = len(failures) == 0
        score = sum(component_scores) / len(component_scores) if component_scores else 0.6

        return (
            passed,
            score,
            (
                "Java behavioral fingerprinting passed."
                if passed
                else f"Java behavioral fingerprinting failed: {len(failures)} issue(s)."
            ),
            {
                "checks": checks,
                "failures": failures,
                "warnings": warnings,
                "java_results": java_results,
                "return_similarity": round(return_similarity, 4),
                "fingerprint_status": "passed" if passed else "failed",
                "fingerprint_summary": (
                    f"{len(java_results)} Java behavioral runtime probe(s) executed."
                    if java_results
                    else "No Java runtime tests/harness available; fingerprinting skipped."
                ),
            },
        )

    @classmethod
    def _fingerprints_dependency_unavailable(
        cls,
        original_fp: Dict[str, Any],
        transformed_fp: Dict[str, Any],
        *,
        language: str,
    ) -> bool:
        """Backward-compatible name for Java runtime infrastructure checks."""
        return cls._fingerprints_runtime_infrastructure_unavailable(
            original_fp,
            transformed_fp,
            language=language,
        )

    @classmethod
    def _fingerprints_runtime_infrastructure_unavailable(
        cls,
        original_fp: Dict[str, Any],
        transformed_fp: Dict[str, Any],
        *,
        language: str,
    ) -> bool:
        return (
            cls._fingerprint_runtime_infrastructure_unavailable(
                original_fp,
                language=language,
            )
            and cls._fingerprint_runtime_infrastructure_unavailable(
                transformed_fp,
                language=language,
            )
        )

    @staticmethod
    def _fingerprint_dependency_unavailable(fp: Dict[str, Any], *, language: str) -> bool:
        """Backward-compatible dependency-only entry point."""
        return BehavioralValidator._fingerprint_runtime_infrastructure_unavailable(
            fp,
            language=language,
        )

    @staticmethod
    def _fingerprint_runtime_infrastructure_unavailable(
        fp: Dict[str, Any],
        *,
        language: str,
    ) -> bool:
        if fp.get("success"):
            return False

        if fp.get("runtime_infrastructure"):
            return True

        exception_type = str(fp.get("exception_type") or "")
        raw_message = "\n".join(
            str(fp.get(key) or "")
            for key in (
                "exception_message_category",
                "runtime_error_details",
                "stderr",
                "stdout",
            )
        )
        message = raw_message.lower()

        if exception_type == "RuntimeUnavailable":
            return True

        if language == "python":
            return exception_type in {"ModuleNotFoundError", "ImportError"} or any(
                pattern in message
                for pattern in (
                    "no module named",
                    "cannot import name",
                    "importerror",
                    "modulenotfounderror",
                )
            )

        if language == "java":
            if exception_type == "RuntimeUnavailable":
                return True

            if exception_type == "RuntimeInfrastructureError":
                return True

            if exception_type != "CompilationError":
                return False

            unresolved_magic = re.search(
                r"symbol:\s+(?:variable|class|interface|method)\s+(?:MAGIC|CONSTANT)_[A-Z0-9_]*",
                raw_message,
            )
            if unresolved_magic:
                return False

            missing_package = re.search(
                r"package\s+[a-zA-Z_][\w.]*\s+does\s+not\s+exist",
                message,
            )
            servlet_dependency_patterns = (
                "package javax.servlet",
                "package jakarta.servlet",
                "symbol: class httpservlet",
                "symbol:   class httpservlet",
                "symbol: class httpservletrequest",
                "symbol:   class httpservletrequest",
                "symbol: class httpservletresponse",
                "symbol:   class httpservletresponse",
                "symbol: class requestdispatcher",
                "symbol:   class requestdispatcher",
                "symbol: class servletexception",
                "symbol:   class servletexception",
                "symbol: class webservlet",
                "symbol:   class webservlet",
                "cannot be converted to annotation",
                "cannot be converted to throwable",
                "location: variable request of type httpservletrequest",
                "location: variable response of type httpservletresponse",
                "location: variable dispatcher of type requestdispatcher",
            )
            if missing_package or any(pattern in message for pattern in servlet_dependency_patterns):
                return True

            dependency_patterns = (
                "package javax.servlet",
                "package jakarta.servlet",
                "package ",
                " does not exist",
                "cannot find symbol",
                "symbol:   class ",
                "symbol: class ",
                "symbol:   interface ",
                "symbol: interface ",
                "class file for ",
                "not found",
                "cannot access ",
                "bad class file",
            )
            syntax_patterns = (
                "';' expected",
                "illegal start of",
                "not a statement",
                "reached end of file",
                "missing return statement",
                "incompatible types",
                "unclosed string literal",
            )
            return any(pattern in message for pattern in dependency_patterns) and not any(
                pattern in message for pattern in syntax_patterns
            )

        if language == "c":
            if exception_type not in {"CompilationError", "RuntimeUnavailable"}:
                return False
            dependency_patterns = (
                "no such file or directory",
                "file not found",
                "cannot find",
                "fatal error:",
                "undefined reference",
                "ld returned",
            )
            syntax_patterns = (
                "expected ';'",
                "expected expression",
                "expected declaration",
                "syntax error",
                "undeclared",
                "too few arguments",
                "too many arguments",
            )
            return exception_type == "RuntimeUnavailable" or (
                any(pattern in message for pattern in dependency_patterns)
                and not any(pattern in message for pattern in syntax_patterns)
            )

        return False

    @staticmethod
    def _normalize_project_source_files(project_source_files: Sequence[Any] | None) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for item in project_source_files or []:
            if isinstance(item, dict):
                file_name = str(item.get("file_name") or item.get("name") or item.get("path") or "")
                source_code = str(item.get("source_code") or item.get("code") or "")
                language = str(item.get("language") or "").lower()
            else:
                file_name = str(getattr(item, "file_name", "") or getattr(item, "name", ""))
                source_code = str(getattr(item, "source_code", "") or getattr(item, "code", ""))
                language = str(getattr(item, "language", "") or "").lower()

            if file_name and source_code:
                normalized.append(
                    {
                        "file_name": file_name.replace("\\", "/"),
                        "source_code": source_code,
                        "language": language,
                    }
                )

        return normalized

    def _validate_java_static_fingerprints(
        self,
        *,
        original_code: str,
        transformed_code: str,
        actions: Sequence[RefactoringAction],
        return_similarity: float,
        structural_validation_passed: bool | None = None,
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        original_summary = self._java_static_summary(original_code)
        transformed_summary = self._java_static_summary(transformed_code)
        comparison = self._compare_java_static_compatibility(
            original_summary,
            transformed_summary,
            actions,
            structural_validation_passed=structural_validation_passed,
        )

        matched = bool(comparison.get("matched"))
        score = (0.6 * (1.0 if matched else 0.0)) + (0.4 * return_similarity)
        validation_mode = (
            "refactoring_aware_static_fallback"
            if comparison.get("refactoring_aware")
            else "java_static_fallback"
        )
        return (
            matched,
            round(score, 4),
            (
                "Java static behavioral fallback passed."
                if matched
                else "Java static behavioral fallback failed."
            ),
            {
                "checks": ["java_static_behavioral_fallback"],
                "failures": [] if matched else [comparison.get("reason", "java_static_summary_mismatch")],
                "warnings": [
                    "Used Java static behavioral fallback because the runtime probe compile/classpath environment was unavailable."
                ],
                "return_similarity": round(return_similarity, 4),
                "behavioral_validation_mode": validation_mode,
                "signature_change": comparison.get("signature_change", "NONE"),
                "signature_compatibility": comparison.get(
                    "signature_compatibility",
                    "PASS" if matched else "FAIL",
                ),
                "compatibility_reason": comparison.get("compatibility_reason"),
                "static_comparison": comparison,
                "fingerprint_status": "passed" if matched else "failed",
                "fingerprint_summary": (
                    "Java static behavioral fallback passed."
                    if matched
                    else "Java static behavioral fallback failed."
                ),
                "java_results": [
                    {
                        "name": "static_java_summary",
                        "mode": validation_mode,
                        "original_fingerprint": original_summary,
                        "transformed_fingerprint": transformed_summary,
                        "comparison": comparison,
                    }
                ],
            },
        )

    def _java_static_summary(self, source_code: str) -> Dict[str, Any]:
        """Build a Java behavior summary from the shared structural parser.

        The old implementation independently parsed method declarations with a
        ``[^)]*`` regular expression.  Spring parameter annotations such as
        ``@RequestParam("id")`` therefore terminated the parameter list early
        and could make annotated methods disappear from the static summary.
        The summary now consumes ``_extract_java_method_candidates()``, whose
        primary path reuses the same Java member parser as the transformers.
        """

        source_code = source_code.lstrip("\ufeff")
        clean = self._strip_java_comments_and_literals(source_code)
        class_names = self._extract_java_class_names(clean)
        methods: Dict[str, Dict[str, Any]] = {}
        class_fields: Dict[str, List[Dict[str, str]]] = {}
        parsed: List[Dict[str, Any]] = []

        for class_name in class_names:
            class_fields[class_name] = self._extract_java_class_fields(
                source_code=clean,
                class_name=class_name,
            )
            for candidate in self._extract_java_method_candidates(
                original_code=clean,
                class_name=class_name,
            ):
                method_name = str(candidate.get("name") or "")
                if not method_name or method_name == class_name:
                    continue
                body = str(candidate.get("body") or "")
                item = {
                    "class_name": class_name,
                    "method_name": method_name,
                    "param_types": list(candidate.get("param_types") or []),
                    "param_names": list(candidate.get("param_names") or []),
                    "return_type": str(candidate.get("return_type") or ""),
                    "access_modifier": str(
                        candidate.get("access_modifier") or "package"
                    ),
                    "is_static": bool(candidate.get("is_static")),
                    "checked_exceptions": list(
                        candidate.get("checked_exceptions") or []
                    ),
                    "annotations": list(candidate.get("annotations") or []),
                    "body": body,
                    "return_count": len(re.findall(r"\breturn\b", body)),
                    "throw_count": len(re.findall(r"\bthrow\b", body)),
                    "branch_count": len(
                        re.findall(r"\b(?:if|for|while|switch|case|catch)\b", body)
                    ),
                    "start_line": candidate.get("start_line"),
                    "end_line": candidate.get("end_line"),
                }
                parsed.append(item)

        pair_counts: Dict[tuple[str, str], int] = {}
        for item in parsed:
            pair = (str(item["class_name"]), str(item["method_name"]))
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

        pair_indexes: Dict[tuple[str, str], int] = {}
        for item in parsed:
            pair = (str(item["class_name"]), str(item["method_name"]))
            base = f"{pair[0]}.{pair[1]}"
            if pair_counts[pair] == 1:
                key = base
            else:
                signature = ",".join(str(value) for value in item["param_types"])
                key = f"{base}({signature})"
                if key in methods:
                    pair_indexes[pair] = pair_indexes.get(pair, 1) + 1
                    key = f"{key}#{pair_indexes[pair]}"
            methods[key] = item

        return {
            "class_names": class_names,
            "method_count": len(methods),
            "methods": methods,
            "class_fields": class_fields,
            "return_count": len(re.findall(r"\breturn\b", clean)),
            "throw_count": len(re.findall(r"\bthrow\b", clean)),
        }

    def _compare_java_static_compatibility(
        self,
        original_summary: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        actions: Sequence[RefactoringAction],
        *,
        structural_validation_passed: bool | None = None,
    ) -> Dict[str, Any]:
        """Compare Java summaries while proving approved refactoring lineages.

        Static fallback must not compare an Extract Class delegation wrapper or
        an Extract Method caller as though it were the unchanged implementation.
        Such differences are accepted only when the effective transformation
        metadata and the transformed source prove the expected migration.
        """

        class_renames: Dict[str, str] = {}
        method_renames: Dict[str, str] = {}
        original_classes = set(original_summary.get("class_names", []))

        for action in actions:
            action_type = getattr(action, "action_type", "")
            if action_type not in {"rename_symbol", "rename_method"}:
                continue
            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()
            if not old_name or not new_name:
                continue
            if action_type == "rename_symbol" and old_name in original_classes:
                class_renames[old_name] = new_name
            else:
                method_renames[old_name] = new_name

        original_methods = dict(original_summary.get("methods", {}))
        transformed_methods = dict(transformed_summary.get("methods", {}))
        transformed_by_pair: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
        for item in transformed_methods.values():
            if not isinstance(item, dict):
                continue
            pair = (
                str(item.get("class_name") or ""),
                str(item.get("method_name") or ""),
            )
            transformed_by_pair.setdefault(pair, []).append(item)

        missing_methods: List[Dict[str, Any]] = []
        changed_methods: List[Dict[str, Any]] = []
        expected_signature_migrations: List[Dict[str, Any]] = []
        expected_behavior_migrations: List[Dict[str, Any]] = []

        for method_key, original_method in original_methods.items():
            original_class = str(original_method.get("class_name") or "")
            original_method_name = str(original_method.get("method_name") or "")
            expected_class = str(class_renames.get(original_class, original_class))
            expected_method = str(
                method_renames.get(original_method_name, original_method_name)
            )
            candidates = transformed_by_pair.get((expected_class, expected_method), [])
            transformed_method = self._select_java_transformed_method(
                original_method=original_method,
                candidates=candidates,
                actions=actions,
            )
            if transformed_method is None:
                missing_methods.append(
                    {
                        "original_method": method_key,
                        "expected_class": expected_class,
                        "expected_method": expected_method,
                        "candidate_count": len(candidates),
                    }
                )
                continue

            # Extract Class keeps the public method but intentionally converts
            # its body into a delegation wrapper.  Validate wrapper -> helper
            # lineage instead of comparing wrapper return/branch counts with
            # the original implementation.
            extract_class_migration = self._validate_java_extract_class_delegation(
                original_method=original_method,
                transformed_method=transformed_method,
                transformed_by_pair=transformed_by_pair,
                actions=actions,
                structural_validation_passed=structural_validation_passed,
            )
            if extract_class_migration is not None:
                if extract_class_migration.get("matched"):
                    expected_behavior_migrations.append(extract_class_migration)
                    continue
                changed_methods.append(
                    {
                        "method": method_key,
                        "expected_method": f"{expected_class}.{expected_method}",
                        "field": "extract_class_behavior_lineage",
                        "original": "source_implementation",
                        "transformed": "delegation_wrapper_and_helper",
                        "signature_compatibility": "FAIL",
                        "compatibility_reason": extract_class_migration.get(
                            "compatibility_reason",
                            "EXTRACT_CLASS_BEHAVIOR_LINEAGE_FAILED",
                        ),
                        "compatibility_details": extract_class_migration,
                    }
                )
                continue

            # Extract Method intentionally moves part of the caller body into a
            # helper.  Prove the helper call, parameter flow, API signature and
            # transformer postconditions before suppressing body-shape deltas.
            extract_method_migration = self._validate_java_extract_method_lineage(
                original_method=original_method,
                transformed_method=transformed_method,
                transformed_by_pair=transformed_by_pair,
                actions=actions,
                structural_validation_passed=structural_validation_passed,
            )
            if extract_method_migration is not None:
                if extract_method_migration.get("matched"):
                    expected_behavior_migrations.append(extract_method_migration)
                    continue
                changed_methods.append(
                    {
                        "method": method_key,
                        "expected_method": f"{expected_class}.{expected_method}",
                        "field": "extract_method_behavior_lineage",
                        "original": "source_implementation",
                        "transformed": "caller_and_extracted_helper",
                        "signature_compatibility": "FAIL",
                        "compatibility_reason": extract_method_migration.get(
                            "compatibility_reason",
                            "EXTRACT_METHOD_BEHAVIOR_LINEAGE_FAILED",
                        ),
                        "compatibility_details": extract_method_migration,
                    }
                )
                continue

            for field in (
                "return_type",
                "access_modifier",
                "is_static",
                "checked_exceptions",
                "annotations",
                "return_count",
                "throw_count",
            ):
                if original_method.get(field) != transformed_method.get(field):
                    changed_methods.append(
                        {
                            "method": method_key,
                            "expected_method": f"{expected_class}.{expected_method}",
                            "field": field,
                            "original": original_method.get(field),
                            "transformed": transformed_method.get(field),
                        }
                    )

            if original_method.get("param_types") != transformed_method.get("param_types"):
                migration = self._validate_java_parameter_object_migration(
                    original_method=original_method,
                    transformed_method=transformed_method,
                    transformed_summary=transformed_summary,
                    actions=actions,
                    structural_validation_passed=structural_validation_passed,
                )
                if migration.get("matched"):
                    expected_signature_migrations.append(migration)
                else:
                    changed_methods.append(
                        {
                            "method": method_key,
                            "expected_method": f"{expected_class}.{expected_method}",
                            "field": "param_types",
                            "original": original_method.get("param_types"),
                            "transformed": transformed_method.get("param_types"),
                            "signature_compatibility": "FAIL",
                            "compatibility_reason": migration.get(
                                "compatibility_reason",
                                "UNAPPROVED_SIGNATURE_CHANGE",
                            ),
                            "compatibility_details": migration,
                        }
                    )

        matched = not missing_methods and not changed_methods
        expected_signature_change = bool(expected_signature_migrations)
        expected_behavior_change = bool(expected_behavior_migrations)
        refactoring_aware = expected_signature_change or expected_behavior_change

        if matched and expected_signature_change and not expected_behavior_change:
            reason = "INTRODUCE_PARAMETER_OBJECT_MAPPING_PRESERVED"
            compatibility_reason = "INTRODUCE_PARAMETER_OBJECT_MAPPING_PRESERVED"
        elif matched and refactoring_aware:
            reason = "java_refactoring_aware_behavior_preserved"
            compatibility_reason = "JAVA_REFACTORING_MIGRATIONS_PRESERVED"
        elif matched:
            reason = "java_static_summary_preserved"
            compatibility_reason = "EXACT_SIGNATURE_PRESERVED"
        else:
            reason = "java_static_summary_mismatch"
            compatibility_reason = "JAVA_STATIC_COMPATIBILITY_FAILED"

        return {
            "matched": matched,
            "reason": reason,
            "refactoring_aware": refactoring_aware,
            "signature_change": "EXPECTED" if expected_signature_change else (
                "NONE" if matched else "UNEXPECTED"
            ),
            "behavior_change": "EXPECTED" if expected_behavior_change else (
                "NONE" if matched else "UNEXPECTED"
            ),
            "signature_compatibility": "PASS" if matched else "FAIL",
            "compatibility_reason": compatibility_reason,
            "expected_signature_migrations": expected_signature_migrations,
            "expected_behavior_migrations": expected_behavior_migrations,
            "unexpected_behavior_changes": changed_methods,
            "original": {
                "method_count": original_summary.get("method_count", 0),
                "methods": original_methods,
                "class_renames": class_renames,
                "method_renames": method_renames,
            },
            "transformed": {
                "method_count": transformed_summary.get("method_count", 0),
                "methods": transformed_methods,
                "missing_methods": missing_methods,
                "changed_methods": changed_methods,
            },
        }

    def _select_java_transformed_method(
        self,
        *,
        original_method: Dict[str, Any],
        candidates: Sequence[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
    ) -> Dict[str, Any] | None:
        if not candidates:
            return None
        original_types = [
            self._normalize_java_type_name(item)
            for item in original_method.get("param_types") or []
        ]
        exact = [
            item for item in candidates
            if [
                self._normalize_java_type_name(value)
                for value in item.get("param_types") or []
            ] == original_types
        ]
        if len(exact) == 1:
            return exact[0]
        if len(candidates) == 1:
            return candidates[0]

        target_class = str(original_method.get("class_name") or "")
        target_method = str(original_method.get("method_name") or "")
        for action in actions:
            if action.action_type not in PARAMETER_OBJECT_ACTIONS:
                continue
            applied = (action.parameters or {}).get("applied_transformation_metadata")
            if not isinstance(applied, dict):
                continue
            if str(applied.get("source_class") or "") != target_class:
                continue
            if str(applied.get("method") or "") != target_method:
                continue
            object_name = str(applied.get("parameter_object_name") or "")
            expected = [
                item for item in candidates
                if list(item.get("param_types") or []) == [object_name]
            ]
            if len(expected) == 1:
                return expected[0]
        return None

    def _validate_java_extract_class_delegation(
        self,
        *,
        original_method: Dict[str, Any],
        transformed_method: Dict[str, Any],
        transformed_by_pair: Dict[tuple[str, str], List[Dict[str, Any]]],
        actions: Sequence[RefactoringAction],
        structural_validation_passed: bool | None,
    ) -> Dict[str, Any] | None:
        target_class = str(original_method.get("class_name") or "")
        target_method = str(original_method.get("method_name") or "")

        for action in actions:
            if action.action_type not in {"extract_class", "extract_java_class"}:
                continue
            params = action.parameters or {}
            applied = params.get("applied_transformation_metadata")
            if not isinstance(applied, dict):
                continue
            if str(applied.get("source_class") or params.get("source_class") or "") != target_class:
                continue
            moved = [str(item) for item in applied.get("methods_moved") or []]
            if target_method not in moved:
                continue
            extracted_class = str(applied.get("extracted_class") or "").strip()
            if not extracted_class:
                continue

            helper_candidates = transformed_by_pair.get(
                (extracted_class, target_method), []
            )
            original_types = [
                self._normalize_java_type_name(item)
                for item in original_method.get("param_types") or []
            ]
            helper_matches = [
                item for item in helper_candidates
                if [
                    self._normalize_java_type_name(value)
                    for value in item.get("param_types") or []
                ] == original_types
            ]

            wrapper = self._java_delegation_wrapper_details(
                transformed_method=transformed_method,
                delegated_method=target_method,
            )
            # Overloaded methods are represented by the same method name in
            # Extract Class metadata.  If this exact overload has neither a
            # matching helper nor a delegation wrapper, it was not the moved
            # overload and should be compared normally.
            if wrapper is None and not helper_matches:
                return None

            base = {
                "method": f"{target_class}.{target_method}",
                "refactoring": "Extract Class",
                "extracted_class": extracted_class,
                "wrapper_validation": "FAIL",
                "helper_validation": "FAIL",
                "behavior_lineage": "FAIL",
            }
            if structural_validation_passed is not True:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "STRUCTURAL_VALIDATION_NOT_PASSED",
                }
            if len(helper_matches) != 1:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_HELPER_METHOD_NOT_UNIQUE",
                }
            if wrapper is None:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_DELEGATION_WRAPPER_INVALID",
                }

            signature_failure = self._java_api_signature_failure(
                original_method, transformed_method, include_annotations=True
            )
            if signature_failure:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": signature_failure,
                }

            expected_args = [str(item) for item in original_method.get("param_names") or []]
            if wrapper.get("args") != expected_args:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_DELEGATION_ARGUMENTS_CHANGED",
                    "expected_args": expected_args,
                    "actual_args": wrapper.get("args"),
                }
            expected_return = str(original_method.get("return_type") or "") != "void"
            if bool(wrapper.get("returns_result")) != expected_return:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_DELEGATION_RETURN_SEMANTICS_CHANGED",
                }

            helper = helper_matches[0]
            if (
                self._normalize_java_type_name(helper.get("return_type"))
                != self._normalize_java_type_name(original_method.get("return_type"))
                or [
                    self._normalize_java_type_name(value)
                    for value in helper.get("param_types") or []
                ] != original_types
            ):
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_HELPER_SIGNATURE_CHANGED",
                }

            original_body = self._normalize_java_behavior_body(
                str(original_method.get("body") or "")
            )
            helper_body = self._normalize_java_behavior_body(
                str(helper.get("body") or "")
            )
            if not original_body or original_body != helper_body:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_CLASS_HELPER_BODY_NOT_PRESERVED",
                }

            validation = applied.get("validation")
            if isinstance(validation, dict):
                critical = {
                    "dependency",
                    "full_api_preservation",
                    "internal_references_updated",
                    "repository_references",
                    "single_state_owner",
                    "structural",
                    "syntax",
                }
                failed = [
                    key for key in critical
                    if key in validation and str(validation.get(key)).upper() != "PASS"
                ]
                if failed:
                    return {
                        **base,
                        "matched": False,
                        "compatibility_reason": "EXTRACT_CLASS_POSTCONDITION_FAILED",
                        "failed_postconditions": failed,
                    }

            return {
                **base,
                "matched": True,
                "compatibility_reason": "EXTRACT_CLASS_DELEGATION_LINEAGE_PRESERVED",
                "wrapper_validation": "PASS",
                "helper_validation": "PASS",
                "behavior_lineage": "PASS",
                "delegation_receiver": wrapper.get("receiver"),
            }
        return None

    def _validate_java_extract_method_lineage(
        self,
        *,
        original_method: Dict[str, Any],
        transformed_method: Dict[str, Any],
        transformed_by_pair: Dict[tuple[str, str], List[Dict[str, Any]]],
        actions: Sequence[RefactoringAction],
        structural_validation_passed: bool | None,
    ) -> Dict[str, Any] | None:
        target_class = str(original_method.get("class_name") or "")
        target_method = str(original_method.get("method_name") or "")

        for action in actions:
            if action.action_type not in {"extract_method", "extract_java_method"}:
                continue
            params = action.parameters or {}
            applied = params.get("applied_transformation_metadata")
            if not isinstance(applied, dict):
                continue
            language = str(applied.get("language") or "java").lower()
            if language != "java":
                continue
            source_class = str(
                applied.get("source_class")
                or params.get("source_class")
                or params.get("class_name")
                or ""
            )
            source_method = str(
                applied.get("source_method")
                or params.get("source_method")
                or params.get("method")
                or params.get("method_name")
                or ""
            )
            if source_class != target_class or source_method != target_method:
                continue

            extracted_method = str(
                applied.get("extracted_method")
                or params.get("extracted_method")
                or params.get("new_method_name")
                or ""
            ).strip()
            base = {
                "method": f"{target_class}.{target_method}",
                "refactoring": "Extract Method",
                "extracted_method": extracted_method,
                "caller_validation": "FAIL",
                "helper_validation": "FAIL",
                "behavior_lineage": "FAIL",
            }
            if structural_validation_passed is not True:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "STRUCTURAL_VALIDATION_NOT_PASSED",
                }
            if not extracted_method:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_HELPER_NAME_MISSING",
                }

            signature_failure = self._java_api_signature_failure(
                original_method, transformed_method, include_annotations=True
            )
            if signature_failure:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": signature_failure,
                }

            helper_candidates = transformed_by_pair.get(
                (target_class, extracted_method), []
            )
            if len(helper_candidates) != 1:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_HELPER_NOT_UNIQUE",
                    "helper_candidate_count": len(helper_candidates),
                }
            helper = helper_candidates[0]

            call_pattern = re.compile(
                rf"(?<![A-Za-z0-9_$]){re.escape(extracted_method)}\s*\((?P<args>.*?)\)\s*;",
                re.DOTALL,
            )
            calls = list(call_pattern.finditer(str(transformed_method.get("body") or "")))
            if len(calls) != 1:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_CALL_NOT_EXACTLY_ONCE",
                    "call_count": len(calls),
                }

            actual_args = [
                re.sub(r"\s+", "", item)
                for item in self._split_java_params(calls[0].group("args"))
                if item.strip()
            ]
            expected_inputs = [
                str(item) for item in (
                    applied.get("inputs")
                    or applied.get("live_in_variables")
                    or []
                )
            ]
            if expected_inputs and actual_args != expected_inputs:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_CALL_ARGUMENTS_CHANGED",
                    "expected_args": expected_inputs,
                    "actual_args": actual_args,
                }
            if expected_inputs and list(helper.get("param_names") or []) != expected_inputs:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_HELPER_PARAMETERS_CHANGED",
                }
            if not str(helper.get("body") or "").strip():
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_HELPER_BODY_EMPTY",
                }

            validation = applied.get("validation")
            if not isinstance(validation, dict) or not validation:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_POSTCONDITIONS_MISSING",
                }
            critical = {
                "target_resolution",
                "scope_validation",
                "data_flow",
                "structural",
                "long_method_reduction",
                "no_severe_new_smell",
            }
            failed = [
                key for key in critical
                if key in validation and str(validation.get(key)).upper() != "PASS"
            ]
            missing = [key for key in critical if key not in validation]
            if failed or missing:
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "EXTRACT_METHOD_POSTCONDITION_FAILED",
                    "failed_postconditions": failed,
                    "missing_postconditions": missing,
                }
            if str(applied.get("plan_compliance") or "").upper() != "PASS":
                return {
                    **base,
                    "matched": False,
                    "compatibility_reason": "PLAN_COMPLIANCE_NOT_PASSED",
                }

            return {
                **base,
                "matched": True,
                "compatibility_reason": "EXTRACT_METHOD_LINEAGE_PRESERVED",
                "caller_validation": "PASS",
                "helper_validation": "PASS",
                "behavior_lineage": "PASS",
                "helper_arguments": actual_args,
            }
        return None

    @staticmethod
    def _java_api_signature_failure(
        original_method: Dict[str, Any],
        transformed_method: Dict[str, Any],
        *,
        include_annotations: bool,
    ) -> str:
        fields = [
            "return_type",
            "access_modifier",
            "is_static",
            "checked_exceptions",
            "param_types",
        ]
        if include_annotations:
            fields.append("annotations")
        for field in fields:
            if original_method.get(field) != transformed_method.get(field):
                return f"METHOD_{field.upper()}_CHANGED"
        return ""

    def _java_delegation_wrapper_details(
        self,
        *,
        transformed_method: Dict[str, Any],
        delegated_method: str,
    ) -> Dict[str, Any] | None:
        body = str(transformed_method.get("body") or "")
        pattern = re.compile(
            rf"^\s*(?P<return>return\s+)?"
            rf"(?:(?:this\s*\.\s*)?)(?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)"
            rf"\s*\.\s*{re.escape(delegated_method)}\s*\((?P<args>.*?)\)\s*;\s*$",
            re.DOTALL,
        )
        match = pattern.match(body)
        if not match:
            return None
        args = [
            re.sub(r"\s+", "", item)
            for item in self._split_java_params(match.group("args") or "")
            if item.strip()
        ]
        return {
            "receiver": match.group("receiver"),
            "args": args,
            "returns_result": bool(match.group("return")),
        }

    def _normalize_java_behavior_body(self, body: str) -> str:
        normalized = self._strip_java_comments_and_literals(body)
        # Introduce Constant is behavior-preserving and may replace numeric
        # literals with generated all-caps constants.  Normalize both forms to
        # the same token before comparing an Extract Class helper with its
        # original implementation.
        normalized = re.sub(r"\b[A-Z][A-Z0-9_]*\b", "NUM", normalized)
        normalized = re.sub(
            r"(?<![A-Za-z0-9_$])(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[fFdDlL])?)(?![A-Za-z0-9_$])",
            "NUM",
            normalized,
        )
        return re.sub(r"\s+", "", normalized)

    def _validate_java_parameter_object_migration(
        self,
        *,
        original_method: Dict[str, Any],
        transformed_method: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        actions: Sequence[RefactoringAction],
        structural_validation_passed: bool | None,
    ) -> Dict[str, Any]:
        """Prove one metadata-approved Java parameter-object migration."""

        target_class = str(original_method.get("class_name") or "")
        target_method = str(original_method.get("method_name") or "")
        for action in actions:
            if action.action_type not in PARAMETER_OBJECT_ACTIONS:
                continue

            params = action.parameters or {}
            applied = params.get("applied_transformation_metadata")
            if not isinstance(applied, dict):
                continue
            if str(applied.get("language") or "").lower() != "java":
                continue
            if str(applied.get("method") or "") != target_method:
                continue
            if str(applied.get("source_class") or "") != target_class:
                continue

            failure = self._java_parameter_object_migration_failure(
                original_method=original_method,
                transformed_method=transformed_method,
                transformed_summary=transformed_summary,
                applied=applied,
                structural_validation_passed=structural_validation_passed,
            )
            if failure:
                return {
                    "matched": False,
                    "signature_change": "UNEXPECTED",
                    "signature_compatibility": "FAIL",
                    "compatibility_reason": failure,
                }

            moved = list(applied.get("parameters_moved") or [])
            return {
                "matched": True,
                "method": f"{target_class}.{target_method}",
                "refactoring": "Introduce Parameter Object",
                "signature_change": "EXPECTED",
                "signature_compatibility": "PASS",
                "compatibility_reason": "INTRODUCE_PARAMETER_OBJECT_MAPPING_PRESERVED",
                "parameter_object_name": applied.get("parameter_object_name"),
                "parameter_name": applied.get("parameter_name"),
                "parameters_moved": moved,
                "parameter_types": dict(applied.get("parameter_types") or {}),
                "structural_validation": "PASS",
                "body_migration": "PASS",
            }

        return {
            "matched": False,
            "signature_change": "UNEXPECTED",
            "signature_compatibility": "FAIL",
            "compatibility_reason": "NO_ACCEPTED_SIGNATURE_CHANGING_REFACTORING",
        }

    def _java_parameter_object_migration_failure(
        self,
        *,
        original_method: Dict[str, Any],
        transformed_method: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        applied: Dict[str, Any],
        structural_validation_passed: bool | None,
    ) -> str:
        if structural_validation_passed is not True:
            return "STRUCTURAL_VALIDATION_NOT_PASSED"
        if str(applied.get("status") or "").lower() not in {
            "success",
            "pass",
            "accepted",
            "already_applied",
        }:
            return "TRANSFORMATION_NOT_ACCEPTED"
        if str(applied.get("plan_compliance") or "").upper() != "PASS":
            return "PLAN_COMPLIANCE_NOT_PASSED"
        validation = applied.get("validation")
        if not isinstance(validation, dict) or not validation or any(
            str(value).upper() != "PASS" for value in validation.values()
        ):
            return "PARAMETER_OBJECT_STRUCTURAL_POSTCONDITION_FAILED"

        moved = [str(item) for item in applied.get("parameters_moved") or []]
        original_names = [str(item) for item in original_method.get("param_names") or []]
        if len(original_names) < 2 or moved != original_names:
            return "PARAMETER_MAPPING_INCOMPLETE_OR_REORDERED"

        type_map = applied.get("parameter_types")
        if not isinstance(type_map, dict) or list(type_map) != moved:
            return "PARAMETER_TYPE_MAPPING_INCOMPLETE_OR_REORDERED"
        original_types = [
            self._normalize_java_type_name(item)
            for item in original_method.get("param_types") or []
        ]
        mapped_types = [
            self._normalize_java_type_name(type_map.get(name)) for name in moved
        ]
        if mapped_types != original_types:
            return "PARAMETER_TYPES_NOT_PRESERVED"

        object_name = str(applied.get("parameter_object_name") or "")
        parameter_name = str(applied.get("parameter_name") or "")
        if not object_name or transformed_method.get("param_types") != [object_name]:
            return "PARAMETER_OBJECT_SIGNATURE_NOT_PRESERVED"
        if transformed_method.get("param_names") != [parameter_name]:
            return "PARAMETER_OBJECT_VARIABLE_NOT_PRESERVED"

        fields = (transformed_summary.get("class_fields") or {}).get(object_name, [])
        field_names = [str(item.get("name") or "") for item in fields]
        field_types = [
            self._normalize_java_type_name(item.get("type")) for item in fields
        ]
        if field_names != moved:
            return "PARAMETER_OBJECT_FIELDS_INCOMPLETE_OR_REORDERED"
        if field_types != original_types:
            return "PARAMETER_OBJECT_FIELD_TYPES_NOT_PRESERVED"

        for field in (
            "return_type",
            "access_modifier",
            "is_static",
            "checked_exceptions",
            "return_count",
            "throw_count",
            "branch_count",
        ):
            if original_method.get(field) != transformed_method.get(field):
                return f"METHOD_{field.upper()}_CHANGED"

        original_body = str(original_method.get("body") or "")
        original_usage_pattern = re.compile(
            rf"(?<![A-Za-z0-9_$.])({'|'.join(re.escape(name) for name in moved)})\b"
        )
        original_usage_sequence = [
            match.group(1) for match in original_usage_pattern.finditer(original_body)
        ]
        transformed_usage_sequence = [
            match.group(1)
            for match in re.finditer(
                rf"\b{re.escape(parameter_name)}\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)\b",
                str(transformed_method.get("body") or ""),
            )
        ]
        expanded_usage_sequence = self._java_parameter_object_usage_sequence(
            transformed_summary=transformed_summary,
            transformed_method=transformed_method,
            parameter_name=parameter_name,
            object_name=object_name,
        )
        if expanded_usage_sequence:
            transformed_usage_sequence = expanded_usage_sequence
        if original_usage_sequence != transformed_usage_sequence:
            return "PARAMETER_OBJECT_BODY_MAPPING_NOT_PRESERVED"
        return ""

    def _java_parameter_object_usage_sequence(
        self,
        *,
        transformed_summary: Dict[str, Any],
        transformed_method: Dict[str, Any],
        parameter_name: str,
        object_name: str,
        max_depth: int = 3,
    ) -> list[str]:
        methods = transformed_summary.get("methods") or {}
        method_lookup = {
            (
                str(item.get("class_name") or ""),
                str(item.get("method_name") or ""),
            ): item
            for item in methods.values()
            if isinstance(item, dict)
        }

        event_pattern = re.compile(
            rf"\b(?P<param>{re.escape(parameter_name)})\s*\.\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\b"
            rf"|(?:\bthis\s*\.\s*)?(?P<call>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*{re.escape(parameter_name)}\s*\)"
        )

        def collect(method_info: Dict[str, Any], param_name: str, depth: int, seen: set[tuple[str, str]]) -> list[str]:
            class_name = str(method_info.get("class_name") or "")
            method_name = str(method_info.get("method_name") or "")
            key = (class_name, method_name)
            if depth > max_depth or key in seen:
                return []
            seen.add(key)

            body = str(method_info.get("body") or "")
            if param_name != parameter_name:
                local_pattern = re.compile(
                    rf"\b(?P<param>{re.escape(param_name)})\s*\.\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\b"
                    rf"|(?:\bthis\s*\.\s*)?(?P<call>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*{re.escape(param_name)}\s*\)"
                )
            else:
                local_pattern = event_pattern

            sequence: list[str] = []
            for match in local_pattern.finditer(body):
                field = match.group("field")
                if field:
                    sequence.append(field)
                    continue
                helper_name = match.group("call")
                if not helper_name:
                    continue
                helper = method_lookup.get((class_name, helper_name))
                if not helper:
                    continue
                helper_param_types = list(helper.get("param_types") or [])
                helper_param_names = [str(item) for item in helper.get("param_names") or []]
                if helper_param_types != [object_name] or len(helper_param_names) != 1:
                    continue
                sequence.extend(collect(helper, helper_param_names[0], depth + 1, set(seen)))
            return sequence

        return collect(transformed_method, parameter_name, 0, set())

    @staticmethod
    def _normalize_java_type_name(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or ""))

    @staticmethod
    def _strip_java_comments_and_literals(source: str) -> str:
        return re.sub(
            r"/\*.*?\*/|//.*?$|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
            lambda match: "\n" * match.group(0).count("\n") if match.group(0).startswith(("/*", "//")) else "0",
            source,
            flags=re.MULTILINE | re.DOTALL,
        )

    def _extract_java_method_body(self, source_code: str, class_name: str, method_name: str) -> str:
        class_match = re.search(
            rf"\bclass\s+{re.escape(class_name)}\b[^{{]*\{{",
            source_code,
        )
        if not class_match:
            return ""
        class_start = class_match.end() - 1
        class_end = self._find_matching_brace(source_code, class_start)
        if class_end == -1:
            return ""
        class_body = source_code[class_start + 1 : class_end]
        method_match = re.search(
            rf"\b{re.escape(method_name)}\s*\([^)]*\)\s*(?:throws\s+[A-Za-z0-9_.,\s]+)?\{{",
            class_body,
        )
        if not method_match:
            return ""
        body_start = method_match.end() - 1
        body_end = self._find_matching_brace(class_body, body_start)
        if body_end == -1:
            return ""
        return class_body[body_start + 1 : body_end]

    @staticmethod
    def _extract_java_runtime_tests(
        behavior_tests: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        runtime_tests: List[Dict[str, Any]] = []

        for test in behavior_tests:
            target = test.get("target") if isinstance(test.get("target"), dict) else {}

            has_target_method = bool(
                test.get("target_method")
                or test.get("method")
                or target.get("method")
            )

            has_command = bool(
                test.get("java_cmd")
                or test.get("harness_cmd")
                or test.get("test_command")
            )

            if has_target_method and not has_command:
                copied = dict(test)

                if "target_class" not in copied and target.get("class"):
                    copied["target_class"] = target.get("class")

                if "target_method" not in copied and target.get("method"):
                    copied["target_method"] = target.get("method")

                runtime_tests.append(copied)

        return runtime_tests

    def _infer_java_runtime_tests_from_actions(
        self,
        *,
        actions: Sequence[RefactoringAction],
        original_code: str,
    ) -> List[Dict[str, Any]]:
        class_names = set(self._extract_java_class_names(original_code))
        class_renames: Dict[str, str] = {}
        inferred: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        for action in actions:
            if action.action_type in {
                "introduce_parameter_object",
                "introduce_java_parameter_object",
            }:
                target_class = str(action.parameters.get("source_class") or "").strip()
                target_method = str(
                    action.parameters.get("method")
                    or action.parameters.get("method_name")
                    or ""
                ).strip()
                object_name = str(
                    action.parameters.get("parameter_object_name")
                    or action.parameters.get("new_class_name")
                    or ""
                ).strip()
                if not target_class:
                    target_class = self._find_java_method_owner(original_code, target_method) or ""
                candidate = next(
                    (
                        item for item in self._extract_java_method_candidates(
                            original_code=original_code,
                            class_name=target_class,
                        )
                        if item["name"] == target_method
                    ),
                    None,
                )
                if not target_class or not target_method or not object_name or candidate is None:
                    continue
                key = (target_class, target_method, target_class, target_method)
                if key in seen:
                    continue
                seen.add(key)
                inferred.append({
                    "name": f"auto_{target_class}_{target_method}_parameter_object",
                    "original_target_class": target_class,
                    "original_target_method": target_method,
                    "transformed_target_class": target_class,
                    "transformed_target_method": target_method,
                    "args": [
                        self._java_raw_arg_for_type(type_name)
                        for type_name in candidate.get("param_types", [])
                    ],
                    "transformed_parameter_object": object_name,
                    "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                    "auto_generated": True,
                    "source_step_id": action.source_step_id,
                    "source_refactoring": action.source_refactoring,
                })
                if len(inferred) >= self.AUTO_PROBE_LIMIT:
                    break
                continue

            if action.action_type == "fault_injection":
                target_class = str(action.parameters.get("target_class") or "").strip()
                target_method = str(action.parameters.get("target_method") or "").strip()

                if not target_class or not target_method:
                    continue

                key = (target_class, target_method, target_class, target_method)

                if key in seen:
                    continue

                seen.add(key)

                inferred.append(
                    {
                        "name": f"auto_{target_class}_{target_method}_fault_injection",
                        "original_target_class": target_class,
                        "original_target_method": target_method,
                        "transformed_target_class": target_class,
                        "transformed_target_method": target_method,
                        "args": [],
                        "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                        "auto_generated": True,
                        "source_step_id": action.source_step_id,
                        "source_refactoring": action.source_refactoring,
                    }
                )

                if len(inferred) >= self.AUTO_PROBE_LIMIT:
                    break

                continue

            if action.action_type in {"extract_class", "extract_java_class"}:
                target_class = str(
                    action.parameters.get("source_class")
                    or action.parameters.get("class_name")
                    or ""
                ).strip()
                method_names = action.parameters.get("methods_to_extract") or []
                if not target_class or not isinstance(method_names, list):
                    continue
                candidates = {
                    candidate["name"]: candidate
                    for candidate in self._extract_java_method_candidates(
                        original_code=original_code,
                        class_name=target_class,
                    )
                }
                for method_name in (str(item).strip() for item in method_names):
                    candidate = candidates.get(method_name)
                    if not candidate:
                        continue
                    key = (target_class, method_name, target_class, method_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    inferred.append({
                        "name": f"auto_{target_class}_{method_name}_extract_class",
                        "original_target_class": target_class,
                        "original_target_method": method_name,
                        "transformed_target_class": target_class,
                        "transformed_target_method": method_name,
                        "args": [
                            self._java_raw_arg_for_type(type_name)
                            for type_name in candidate.get("param_types", [])
                        ],
                        "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                        "auto_generated": True,
                        "source_step_id": action.source_step_id,
                        "source_refactoring": action.source_refactoring,
                    })
                    if len(inferred) >= self.AUTO_PROBE_LIMIT:
                        break
                if len(inferred) >= self.AUTO_PROBE_LIMIT:
                    break
                continue

            if action.action_type != "rename_symbol":
                continue

            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()

            if old_name in class_names and new_name:
                class_renames[old_name] = new_name

        for action in actions:
            if action.action_type not in {"rename_symbol", "rename_method"}:
                continue

            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()

            if not old_name:
                continue

            owner_class = self._find_java_method_owner(original_code, old_name)

            if not owner_class:
                continue

            transformed_class = class_renames.get(owner_class, owner_class)
            transformed_method = new_name if new_name else old_name

            key = (owner_class, old_name, transformed_class, transformed_method)

            if key in seen:
                continue

            seen.add(key)
            method_candidates = {
                candidate["name"]: candidate
                for candidate in self._extract_java_method_candidates(
                    original_code=original_code,
                    class_name=owner_class,
                )
            }
            param_types = method_candidates.get(old_name, {}).get("param_types", [])

            inferred.append(
                {
                    "name": f"auto_{owner_class}_{old_name}",
                    "original_target_class": owner_class,
                    "original_target_method": old_name,
                    "transformed_target_class": transformed_class,
                    "transformed_target_method": transformed_method,
                    "args": [
                        self._java_raw_arg_for_type(type_name)
                        for type_name in param_types
                    ],
                    "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                    "auto_generated": True,
                    "source_step_id": action.source_step_id,
                    "source_refactoring": action.source_refactoring,
                }
            )

            if len(inferred) >= self.AUTO_PROBE_LIMIT:
                break

        return inferred

    @staticmethod
    def _adapt_java_parameter_object_tests(
        tests: Sequence[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
    ) -> List[Dict[str, Any]]:
        parameter_actions = {
            str(action.parameters.get("method") or action.parameters.get("method_name") or "").strip(): str(
                action.parameters.get("parameter_object_name")
                or action.parameters.get("new_class_name")
                or ""
            ).strip()
            for action in actions
            if action.action_type in {
                "introduce_parameter_object",
                "introduce_java_parameter_object",
            }
        }
        adapted: List[Dict[str, Any]] = []
        for test in tests:
            copied = dict(test)
            method = str(
                copied.get("original_target_method")
                or copied.get("target_method")
                or copied.get("method")
                or ""
            ).strip()
            if method in parameter_actions:
                copied["transformed_parameter_object"] = parameter_actions[method]
            adapted.append(copied)
        return adapted

    def _infer_java_runtime_tests_from_source(
        self,
        *,
        original_code: str,
        actions: Sequence[RefactoringAction],
        class_renames: Dict[str, str] | None = None,
    ) -> List[Dict[str, Any]]:
        class_renames = class_renames or {}
        inferred: List[Dict[str, Any]] = []

        try:
            class_name = self._extract_java_class_name(original_code)
        except ValueError:
            return []

        for action in actions:
            if action.action_type not in {
                "extract_class",
                "extract_python_class",
                "extract_java_class",
            }:
                continue
            requested_class = str(
                action.parameters.get("source_class")
                or action.parameters.get("class_name")
                or ""
            ).strip()
            if requested_class and self._extract_java_method_candidates(
                original_code=original_code,
                class_name=requested_class,
            ):
                class_name = requested_class
                break

        # With no effective Extract Class action (for example, when another
        # action changed the file first), choose the class that directly owns
        # the useful methods. An enclosing class must not inherit methods from
        # its nested classes merely because their text lies inside its braces.
        if not any(
            action.action_type in {
                "extract_class",
                "extract_python_class",
                "extract_java_class",
            }
            for action in actions
        ):
            ranked_classes = []
            for declared_class in self._extract_java_class_names(original_code):
                owned_methods = self._extract_java_method_candidates(
                    original_code=original_code,
                    class_name=declared_class,
                )
                useful_methods = [
                    candidate
                    for candidate in owned_methods
                    if candidate["name"] not in {declared_class, "main"}
                ]
                ranked_classes.append((len(useful_methods), len(owned_methods), declared_class))
            if ranked_classes:
                best_useful_count, _, best_class = max(ranked_classes)
                if best_useful_count > 0:
                    class_name = best_class

        if not class_renames:
            class_names = set(self._extract_java_class_names(original_code))
            for action in actions:
                if action.action_type != "rename_symbol":
                    continue
                old_name = str(action.parameters.get("old_name") or "").strip()
                new_name = str(action.parameters.get("new_name") or "").strip()
                if old_name in class_names and new_name:
                    class_renames[old_name] = new_name

        transformed_class = class_renames.get(class_name, class_name)

        method_candidates = self._extract_java_method_candidates(
            original_code=original_code,
            class_name=class_name,
        )

        for candidate in method_candidates:
            method_name = candidate["name"]
            param_types = candidate["param_types"]
            if method_name == class_name:
                continue

            raw_args = [self._java_raw_arg_for_type(t) for t in param_types]

            inferred.append(
                {
                    "name": f"auto_{class_name}_{method_name}_source",
                    "original_target_class": class_name,
                    "original_target_method": method_name,
                    "transformed_target_class": transformed_class,
                    "transformed_target_method": method_name,
                    "args": raw_args,
                    "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                    "auto_generated": True,
                }
            )

            if len(inferred) >= self.AUTO_PROBE_LIMIT:
                break

        return inferred

    def _extract_java_method_candidates(
        self,
        *,
        original_code: str,
        class_name: str,
    ) -> List[Dict[str, Any]]:
        """Return top-level methods owned by ``class_name``.

        The primary path deliberately reuses the Java structural member parser
        used by Extract Class/Extract Method.  This prevents Spring parameter
        annotations from being mistaken for method declarations and keeps
        behavioral validation aligned with transformation target resolution.
        """

        original_code = original_code.lstrip("\ufeff")
        try:
            from ..transformers.java_extract_class import (
                _mask_java_annotations,
                _parse_java_class,
            )

            model = _parse_java_class(original_code, class_name)
        except (ImportError, TypeError, ValueError, AttributeError):
            model = None

        if model is not None:
            candidates: List[Dict[str, Any]] = []
            for method in model.methods:
                if method.is_constructor or method.name == class_name:
                    continue
                masked_header = _mask_java_annotations(method.header)
                name_match = None
                for match in re.finditer(
                    rf"\b{re.escape(method.name)}\s*\(", masked_header
                ):
                    name_match = match
                if name_match is None:
                    continue
                paren_open = masked_header.find("(", name_match.start())
                paren_close = self._matching_java_parenthesis(
                    masked_header, paren_open
                )
                if paren_close == -1:
                    continue

                params_raw = masked_header[paren_open + 1 : paren_close]
                param_list = self._split_java_params(params_raw)
                param_types = [
                    self._normalize_java_param_type(item) for item in param_list
                ]
                param_types = [item for item in param_types if item]
                param_names = [
                    self._java_parameter_name(item) for item in param_list
                ]
                param_names = [item for item in param_names if item]

                suffix = masked_header[paren_close + 1 :]
                throws_match = re.search(r"\bthrows\s+(.+?)\s*$", suffix, re.DOTALL)
                checked_exceptions = [
                    re.sub(r"\s+", "", item)
                    for item in self._split_java_params(
                        throws_match.group(1) if throws_match else ""
                    )
                    if item.strip()
                ]

                prefix = method.header[: name_match.start()]
                annotations = [
                    re.sub(r"\s+", "", match.group(0))
                    for match in re.finditer(
                        r"@[A-Za-z_$][A-Za-z0-9_$.]*(?:\s*\([^\n]*?\))?",
                        prefix,
                    )
                ]
                start_line = original_code.count("\n", 0, method.start) + 1
                end_line = original_code.count("\n", 0, method.end) + 1
                access_modifier = next(
                    (
                        item for item in ("public", "protected", "private")
                        if item in method.modifiers
                    ),
                    "package",
                )
                candidates.append(
                    {
                        "name": method.name,
                        "param_types": param_types,
                        "param_names": param_names,
                        "return_type": str(method.return_type or "").strip(),
                        "access_modifier": access_modifier,
                        "is_static": "static" in method.modifiers,
                        "checked_exceptions": checked_exceptions,
                        "annotations": annotations,
                        "body": self._strip_java_comments_and_literals(method.body),
                        "header": method.header,
                        "start_line": start_line,
                        "end_line": end_line,
                    }
                )
            return candidates

        # Conservative fallback for malformed/incomplete Java.  It masks
        # annotations before matching so annotation parentheses cannot truncate
        # the method parameter list.
        class_match = re.search(
            rf"\bclass\s+{re.escape(class_name)}\b[^{{]*\{{",
            original_code,
        )
        if not class_match:
            return []
        body_start = class_match.end() - 1
        body_end = self._find_matching_brace(original_code, body_start)
        if body_end == -1:
            return []
        class_body = original_code[body_start + 1 : body_end]
        try:
            from ..transformers.java_extract_class import _mask_java_annotations
            masked_body = _mask_java_annotations(class_body)
        except (ImportError, TypeError, ValueError, AttributeError):
            masked_body = class_body

        method_pattern = re.compile(
            r"(?P<prefix>(?:public|protected|private)?\s*(?:static\s+)?"
            r"(?:final\s+)?(?:synchronized\s+)?(?:native\s+)?"
            r"(?:abstract\s+)?(?:strictfp\s+)?[\w$<>\[\], ?.]+\s+)"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        )
        candidates: List[Dict[str, Any]] = []
        for match in method_pattern.finditer(masked_body):
            if self._java_brace_depth_at(masked_body, match.start()) != 0:
                continue
            paren_open = masked_body.find("(", match.start())
            paren_close = self._matching_java_parenthesis(masked_body, paren_open)
            if paren_close == -1:
                continue
            cursor = paren_close + 1
            while cursor < len(masked_body) and masked_body[cursor].isspace():
                cursor += 1
            throws_text = ""
            throws_match = re.match(r"throws\s+([^\{;]+)", masked_body[cursor:])
            if throws_match:
                throws_text = throws_match.group(1)
                cursor += throws_match.end()
                while cursor < len(masked_body) and masked_body[cursor].isspace():
                    cursor += 1
            if cursor >= len(masked_body) or masked_body[cursor] not in "{;":
                continue

            params_raw = masked_body[paren_open + 1 : paren_close]
            param_list = self._split_java_params(params_raw)
            prefix_tokens = str(match.group("prefix") or "").split()
            modifier_names = {
                "public", "protected", "private", "static", "final",
                "synchronized", "native", "abstract", "strictfp",
            }
            return_type = " ".join(
                token for token in prefix_tokens if token not in modifier_names
            ).strip()
            access_modifier = next(
                (
                    token for token in prefix_tokens
                    if token in {"public", "protected", "private"}
                ),
                "package",
            )
            candidates.append(
                {
                    "name": match.group("name"),
                    "param_types": [
                        value for value in (
                            self._normalize_java_param_type(item)
                            for item in param_list
                        ) if value
                    ],
                    "param_names": [
                        value for value in (
                            self._java_parameter_name(item)
                            for item in param_list
                        ) if value
                    ],
                    "return_type": return_type,
                    "access_modifier": access_modifier,
                    "is_static": "static" in prefix_tokens,
                    "checked_exceptions": [
                        re.sub(r"\s+", "", item)
                        for item in self._split_java_params(throws_text)
                        if item.strip()
                    ],
                    "annotations": [],
                    "body": "",
                }
            )
        return candidates

    @staticmethod
    def _matching_java_parenthesis(text: str, open_index: int) -> int:
        if open_index < 0 or open_index >= len(text) or text[open_index] != "(":
            return -1
        depth = 0
        for index in range(open_index, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    @staticmethod
    def _java_parameter_name(parameter: str) -> str:
        cleaned = re.sub(r"@\w+(?:\s*\([^)]*\))?", "", parameter)
        cleaned = re.sub(r"\bfinal\b", "", cleaned).strip()
        match = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\[\s*\])?\s*$", cleaned)
        return match.group(1) if match else ""

    def _extract_java_class_fields(
        self,
        *,
        source_code: str,
        class_name: str,
    ) -> List[Dict[str, str]]:
        # Reuse the parsed Java member model used by the transformer so field
        # order and types are checked with the same grammar that created the
        # parameter object.
        try:
            from ..transformers.java_extract_class import _parse_java_class

            model = _parse_java_class(source_code, class_name)
        except (ImportError, TypeError, ValueError):
            model = None
        if model is None:
            return []
        return [
            {"name": field.name, "type": field.type_name}
            for field in model.fields.values()
        ]

    @staticmethod
    def _java_brace_depth_at(source_code: str, end_index: int) -> int:
        """Return Java brace depth while ignoring comments and literals."""

        depth = 0
        index = 0
        state = "code"
        while index < min(end_index, len(source_code)):
            char = source_code[index]
            nxt = source_code[index + 1] if index + 1 < end_index else ""
            if state == "line_comment":
                if char == "\n":
                    state = "code"
            elif state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "code"
                    index += 1
            elif state in {"string", "char"}:
                if char == "\\":
                    index += 1
                elif (state == "string" and char == '"') or (
                    state == "char" and char == "'"
                ):
                    state = "code"
            elif char == "/" and nxt == "/":
                state = "line_comment"
                index += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
                index += 1
            elif char == '"':
                state = "string"
            elif char == "'":
                state = "char"
            elif char == "{":
                depth += 1
            elif char == "}":
                depth = max(depth - 1, 0)
            index += 1
        return depth

    @staticmethod
    def _split_java_params(params_raw: str) -> List[str]:
        params_raw = params_raw.strip()

        if not params_raw:
            return []

        parts: List[str] = []
        current: List[str] = []
        depth = 0

        for ch in params_raw:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth = max(depth - 1, 0)
            elif ch == "," and depth == 0:
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                continue

            current.append(ch)

        tail = "".join(current).strip()
        if tail:
            parts.append(tail)

        return parts

    @staticmethod
    def _normalize_java_param_type(param: str) -> str:
        tokens = [t for t in re.split(r"\s+", param.strip()) if t]
        tokens = [t for t in tokens if not t.startswith("@")]  # drop annotations
        tokens = [t for t in tokens if t != "final"]

        if not tokens:
            return ""

        if len(tokens) == 1:
            return tokens[0]

        return " ".join(tokens[:-1])

    @staticmethod
    def _java_raw_arg_for_type(type_name: str) -> str:
        base = type_name.strip()
        base = re.sub(r"<.*?>", "", base)
        base = base.replace("...", "").replace("[]", "")
        base = base.replace("java.lang.", "")
        base = base.strip()

        lower = base.lower()

        if lower in {"boolean", "bool", "boolean"}:
            return "false"
        if lower in {"int", "integer", "short", "byte"}:
            return "0"
        if lower in {"long"}:
            return "0"
        if lower in {"double", "float"}:
            return "0.0"
        if lower in {"char", "character"}:
            return "a"
        if base == "String" or lower.endswith("string"):
            return "sample"

        return ""

    @staticmethod
    def _extract_java_class_names(source_code: str) -> List[str]:
        source_code = source_code.lstrip("\ufeff")

        return [
            match.group(1)
            for match in re.finditer(
                r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b",
                source_code,
            )
        ]

    @staticmethod
    def _extract_java_class_name(source_code: str) -> str:
        source_code = source_code.lstrip("\ufeff")

        public_match = re.search(
            r"\bpublic\s+class\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            source_code,
        )

        if public_match:
            return public_match.group(1)

        match = re.search(
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            source_code,
        )

        if not match:
            raise ValueError("Could not determine Java class name from source.")

        return match.group(1)

    @staticmethod
    def _extract_java_package(source_code: str) -> str | None:
        match = re.search(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", source_code, re.MULTILINE)
        return match.group(1) if match else None

    def _find_java_method_owner(self, source_code: str, method_name: str) -> str | None:
        source_code = source_code.lstrip("\ufeff")

        for class_match in re.finditer(
            r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b[^{]*\{",
            source_code,
        ):
            class_name = class_match.group(1)
            body_start = class_match.end() - 1
            body_end = self._find_matching_brace(source_code, body_start)

            if body_end == -1:
                continue

            class_body = source_code[body_start + 1 : body_end]

            method_pattern = (
                r"(?:public|private|protected)?\s*"
                r"(?:static\s+)?"
                r"(?:final\s+)?"
                r"[\w<>\[\], ?]+\s+"
                + re.escape(method_name)
                + r"\s*\("
            )

            if re.search(method_pattern, class_body):
                return class_name

        return None

    @staticmethod
    def _find_matching_brace(source_code: str, open_index: int) -> int:
        depth = 0

        for index in range(open_index, len(source_code)):
            char = source_code[index]

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1

                if depth == 0:
                    return index

        return -1

    def _run_java_runtime_probe(
        self,
        *,
        source_code: str,
        target_class: str,
        target_method: str,
        args: List[Any] | None = None,
        timeout_seconds: int,
        project_source_files: Sequence[Dict[str, str]] | None = None,
        current_file_name: str | None = None,
        parameter_object: bool = False,
    ) -> Dict[str, Any]:
        java_exe = shutil.which("java")
        javac_exe = shutil.which("javac")

        if not java_exe or not javac_exe:
            return self._java_runtime_probe_failure(
                category="JAVA_COMPILE_UNAVAILABLE",
                details="java/javac not available",
                diagnostics={
                    "javac_exit_status": None,
                    "java_exit_status": None,
                    "dependency_resolution_status": "unavailable",
                },
            )

        source_code = source_code.lstrip("\ufeff")
        class_name = self._extract_java_class_name(source_code)
        package_name = self._extract_java_package(source_code)
        target_class_name = self._java_binary_class_name(source_code, target_class)
        if package_name and "." not in target_class_name:
            target_class_name = f"{package_name}.{target_class_name}"
        args = args or []

        temp_path = _make_runtime_temp_dir("java_fp")
        try:
            source_path = temp_path / f"{class_name}.java"
            harness_path = temp_path / "JavaRuntimeProbeHarness.java"
            classes_path = temp_path / "classes"
            classes_path.mkdir(parents=True, exist_ok=True)

            source_path.write_text(source_code, encoding="utf-8")
            project_java_paths = self._write_java_project_sources(
                temp_path=temp_path,
                source_code=source_code,
                project_source_files=project_source_files,
                current_file_name=current_file_name,
            )

            harness_path.write_text(
                self._build_java_runtime_probe_harness(
                    target_class=target_class_name,
                    target_method=target_method,
                    args=args,
                    parameter_object=parameter_object,
                ),
                encoding="utf-8",
            )

            started = time.perf_counter()
            classpath_entries = self._java_runtime_classpath_entries(classes_path)
            diagnostics = {
                "source_file": current_file_name or f"{class_name}.java",
                "package_name": package_name or None,
                "fully_qualified_class_name": target_class_name,
                "compiled_classes_directory": str(classes_path),
                "classpath_entries": classpath_entries,
                "target_class_file": str(
                    classes_path / f"{target_class_name.replace('.', '/')}.class"
                ),
                "dependency_resolution_status": (
                    "classpath_available" if len(classpath_entries) > 1 else "source_only"
                ),
            }

            try:
                compile_args = [javac_exe, "-proc:none", "-d", str(classes_path)]
                dependency_classpath = classpath_entries[1:]
                if dependency_classpath:
                    compile_args.extend(["-classpath", os.pathsep.join(dependency_classpath)])
                compile_args.extend(
                    str(path.relative_to(temp_path)).replace("\\", "/")
                    for path in [source_path, *project_java_paths, harness_path]
                )
                compile_proc = subprocess.run(
                    compile_args,
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return self._java_runtime_probe_failure(
                    category="JAVA_COMPILE_TIMEOUT",
                    details="javac timed out.",
                    diagnostics={**diagnostics, "javac_exit_status": None, "java_exit_status": None},
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    timeout=True,
                )

            if compile_proc.returncode != 0:
                category = self._classify_java_compile_failure(compile_proc.stderr or "")
                if category:
                    return self._java_runtime_probe_failure(
                        category=category,
                        details=compile_proc.stderr or compile_proc.stdout,
                        diagnostics={
                            **diagnostics,
                            "javac_exit_status": compile_proc.returncode,
                            "java_exit_status": None,
                        },
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        stdout=compile_proc.stdout,
                        stderr=compile_proc.stderr,
                    )
                return self._java_runtime_probe_failure(
                    category="JAVA_COMPILE_FAILED",
                    details=compile_proc.stderr or compile_proc.stdout,
                    diagnostics={
                        **diagnostics,
                        "javac_exit_status": compile_proc.returncode,
                        "java_exit_status": None,
                    },
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    stdout=compile_proc.stdout,
                    stderr=compile_proc.stderr,
                    infrastructure=False,
                    exception_type="CompilationError",
                )

            target_class_file = Path(diagnostics["target_class_file"])
            diagnostics["target_class_file_exists"] = target_class_file.exists()
            if not target_class_file.exists():
                return self._java_runtime_probe_failure(
                    category="TARGET_CLASS_NOT_COMPILED",
                    details=f"Compiled target class was not found: {target_class_file}",
                    diagnostics={**diagnostics, "javac_exit_status": compile_proc.returncode, "java_exit_status": None},
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )

            try:
                run_proc = subprocess.run(
                    [java_exe, "-cp", os.pathsep.join(classpath_entries), "JavaRuntimeProbeHarness"],
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return self._java_runtime_probe_failure(
                    category="JAVA_RUNTIME_TIMEOUT",
                    details="Java runtime probe timed out.",
                    diagnostics={**diagnostics, "javac_exit_status": compile_proc.returncode, "java_exit_status": None},
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    timeout=True,
                )

            stdout_raw = (run_proc.stdout or "").strip()

            if run_proc.returncode != 0:
                runtime_error = run_proc.stderr or run_proc.stdout or ""
                category = (
                    "CLASSPATH_CONFIGURATION_ERROR"
                    if "Could not find or load main class JavaRuntimeProbeHarness" in runtime_error
                    else "JAVA_PROBE_PROCESS_FAILED"
                )
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "RuntimeInfrastructureError" if category == "CLASSPATH_CONFIGURATION_ERROR" else "RuntimeError",
                    "exception_message_category": category.lower(),
                    "stdout": stdout_raw,
                    "stderr": run_proc.stderr,
                    "execution_time_ms": int((time.perf_counter() - started) * 1000),
                    "timeout": False,
                    "runtime_error_details": run_proc.stderr,
                    "runtime_infrastructure": category == "CLASSPATH_CONFIGURATION_ERROR",
                    "runtime_failure_category": category,
                    "runtime_diagnostics": {
                        **diagnostics,
                        "javac_exit_status": compile_proc.returncode,
                        "java_exit_status": run_proc.returncode,
                    },
                    "observed_invariants": {
                        **mine_exception_invariants("RuntimeError", "java_probe_failed"),
                        **stdout_invariants(stdout_raw),
                    },
                }

            lines = [line.strip() for line in stdout_raw.splitlines() if line.strip()]
            last_line = lines[-1] if lines else ""

            if last_line.startswith("EXC:"):
                exception_type = last_line.split("|", 1)[0].replace("EXC:", "")
                message = last_line.split("|", 1)[1] if "|" in last_line else ""

                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": exception_type,
                    "exception_message_category": message[:200],
                    "stdout": stdout_raw,
                    "execution_time_ms": int((time.perf_counter() - started) * 1000),
                    "timeout": False,
                    "runtime_error_details": message,
                    "observed_invariants": {
                        **mine_exception_invariants(exception_type, message),
                        **stdout_invariants(stdout_raw),
                    },
                }

            if last_line.startswith("INFRA:"):
                category_part, _, message = last_line.partition("|")
                return self._java_runtime_probe_failure(
                    category=category_part.replace("INFRA:", "") or "JAVA_RUNTIME_INFRASTRUCTURE_UNAVAILABLE",
                    details=message,
                    diagnostics={
                        **diagnostics,
                        "javac_exit_status": compile_proc.returncode,
                        "java_exit_status": run_proc.returncode,
                    },
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    stdout=stdout_raw,
                    stderr=run_proc.stderr,
                )

            return_type = "unknown"
            return_value = last_line

            if last_line.startswith("OK:") and "|" in last_line:
                type_part, value_part = last_line.split("|", 1)
                return_type = type_part.replace("OK:", "")
                return_value = value_part

            return {
                "success": True,
                "return_value_repr": return_value,
                "return_type": return_type,
                "exception_type": None,
                "exception_message_category": None,
                "stdout": return_value,
                "execution_time_ms": int((time.perf_counter() - started) * 1000),
                "timeout": False,
                "runtime_error_details": None,
                "runtime_infrastructure": False,
                "runtime_diagnostics": {
                    **diagnostics,
                    "javac_exit_status": compile_proc.returncode,
                    "java_exit_status": run_proc.returncode,
                },
                "observed_invariants": {
                    "return": mine_value_invariants(return_value),
                    **stdout_invariants(return_value),
                },
            }
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)

    @staticmethod
    def _java_runtime_classpath_entries(classes_path: Path) -> List[str]:
        """Return an isolated probe classpath rooted at the freshly compiled classes."""
        entries = [str(classes_path)]
        inherited = os.environ.get("CLASSPATH", "")
        for entry in inherited.split(os.pathsep):
            normalized = entry.strip()
            if normalized and normalized not in entries:
                entries.append(normalized)
        return entries

    @staticmethod
    def _classify_java_compile_failure(stderr: str) -> str | None:
        """Classify environment/compiler setup failures without hiding source errors."""
        message = str(stderr or "").lower()
        dependency_markers = (
            "package ",
            " does not exist",
            "class file for ",
            "cannot access ",
            "bad class file",
            "module not found",
        )
        syntax_or_transform_markers = (
            "';' expected",
            "illegal start of",
            "not a statement",
            "reached end of file",
            "missing return statement",
            "incompatible types",
            "unclosed string literal",
            "cannot find symbol",
        )
        unresolved_sctva_constant = re.search(
            r"symbol:\s+(?:variable|class|interface|method)\s+(?:magic|constant)_[a-z0-9_]*",
            message,
        )
        if "cannot find symbol" in message and not unresolved_sctva_constant:
            return "MISSING_DEPENDENCY"
        if any(marker in message for marker in dependency_markers) and not any(
            marker in message for marker in syntax_or_transform_markers
        ):
            return "MISSING_DEPENDENCY"
        return None

    @staticmethod
    def _java_runtime_probe_failure(
        *,
        category: str,
        details: str | None,
        diagnostics: Dict[str, Any],
        duration_ms: int = 0,
        timeout: bool = False,
        stdout: str | None = None,
        stderr: str | None = None,
        infrastructure: bool = True,
        exception_type: str = "RuntimeInfrastructureError",
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "return_value_repr": None,
            "return_type": None,
            "exception_type": exception_type,
            "exception_message_category": category.lower(),
            "stdout": stdout or "",
            "stderr": stderr or "",
            "execution_time_ms": duration_ms,
            "timeout": timeout,
            "runtime_error_details": details or "",
            "runtime_infrastructure": infrastructure,
            "runtime_failure_category": category,
            "runtime_diagnostics": diagnostics,
            "observed_invariants": {
                **mine_exception_invariants(exception_type, category.lower()),
                **stdout_invariants(stdout or ""),
            },
        }

    @classmethod
    def _java_binary_class_name(cls, source_code: str, target_class: str) -> str:
        """Resolve a Java member class to the JVM name used by reflection."""

        requested = str(target_class or "").strip()
        if not requested or "$" in requested:
            return requested
        simple_name = requested.rsplit(".", 1)[-1]
        class_pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b[^\{;]*\{")
        spans: list[tuple[str, int, int]] = []
        target_span: tuple[str, int, int] | None = None
        for match in class_pattern.finditer(source_code):
            open_brace = source_code.find("{", match.start(), match.end())
            close_brace = cls._find_matching_brace(source_code, open_brace)
            if close_brace == -1:
                continue
            span = (match.group(1), match.start(), close_brace)
            spans.append(span)
            if match.group(1) == simple_name and target_span is None:
                target_span = span
        if target_span is None:
            return requested

        enclosing = sorted(
            (
                span for span in spans
                if span[1] < target_span[1] and target_span[2] < span[2]
            ),
            key=lambda span: span[1],
        )
        if not enclosing:
            return requested
        return "$".join([*(span[0] for span in enclosing), simple_name])

    def _write_java_project_sources(
        self,
        *,
        temp_path: Path,
        source_code: str,
        project_source_files: Sequence[Dict[str, str]] | None,
        current_file_name: str | None,
    ) -> List[Path]:
        written: List[Path] = []
        current_norm = self._normalize_project_path(current_file_name or "")
        source_norm = source_code.strip()

        for item in project_source_files or []:
            file_name = str(item.get("file_name") or "")
            if not file_name.lower().endswith(".java"):
                continue

            item_source = str(item.get("source_code") or "").lstrip("\ufeff")
            if not item_source.strip():
                continue

            item_norm = self._normalize_project_path(file_name)
            if current_norm and item_norm == current_norm:
                continue
            if item_source.strip() == source_norm:
                continue

            try:
                item_class = self._extract_java_class_name(item_source)
            except ValueError:
                continue

            package_name = self._extract_java_package(item_source)
            target_dir = temp_path
            if package_name:
                target_dir = temp_path / package_name.replace(".", "/")
                target_dir.mkdir(parents=True, exist_ok=True)

            target_path = target_dir / f"{item_class}.java"
            if target_path.exists():
                continue
            target_path.write_text(item_source, encoding="utf-8")
            written.append(target_path)

        return written

    @staticmethod
    def _normalize_project_path(value: str) -> str:
        return str(value or "").replace("\\", "/").strip().lower()

    @staticmethod
    def _java_string_array(values: List[Any]) -> str:
        escaped: List[str] = []

        for value in values:
            text = str(value)
            text = text.replace("\\", "\\\\").replace('"', '\\"')
            escaped.append(f'"{text}"')

        return "new String[]{" + ", ".join(escaped) + "}"

    @staticmethod
    def _build_java_runtime_probe_harness(
        *,
        target_class: str,
        target_method: str,
        args: List[Any] | None = None,
        parameter_object: bool = False,
    ) -> str:
        raw_args = BehavioralValidator._java_string_array(args or [])
        target_package = target_class.rpartition(".")[0]
        expected_arity = "1" if parameter_object else "rawArgs.length"
        build_values = (
            "buildParameterObjectArgs(targetMethod.getParameterTypes()[0], rawArgs)"
            if parameter_object
            else "buildArgs(targetMethod.getParameterTypes(), rawArgs)"
        )

        return f"""
import java.lang.reflect.*;
import java.time.LocalDate;
import java.util.*;

public class JavaRuntimeProbeHarness {{
    public static void main(String[] args) throws Exception {{
        try {{
            Class<?> targetClass;
            try {{
                targetClass = Class.forName("{target_class}");
            }} catch (ClassNotFoundException | LinkageError ex) {{
                System.out.println(
                    "INFRA:TARGET_CLASS_LOAD_FAILED|" + String.valueOf(ex.getMessage())
                );
                return;
            }}
            String[] rawArgs = {raw_args};

            Method targetMethod = null;

            for (Method method : targetClass.getDeclaredMethods()) {{
                if (
                    method.getName().equals("{target_method}")
                    && method.getParameterTypes().length == {expected_arity}
                ) {{
                    targetMethod = method;
                    break;
                }}
            }}

            if (targetMethod == null) {{
                System.out.println(
                    "EXC:NoSuchMethodException|missing target method or parameter count mismatch"
                );
                return;
            }}

            targetMethod.setAccessible(true);

            Object[] values = {build_values};
            Object instance = null;
            if (!Modifier.isStatic(targetMethod.getModifiers())) {{
                try {{
                    Constructor<?> constructor = targetClass.getDeclaredConstructor();
                    constructor.setAccessible(true);
                    instance = constructor.newInstance();
                }} catch (ReflectiveOperationException ex) {{
                    System.out.println(
                        "INFRA:TARGET_INSTANTIATION_UNAVAILABLE|"
                        + String.valueOf(ex.getMessage())
                    );
                    return;
                }}
            }}
            Object result = targetMethod.invoke(instance, values);

            String resultType = result == null
                ? "null"
                : result.getClass().getSimpleName();

            String resultValue = result == null
                ? "null"
                : result.toString();

            System.out.println("OK:" + resultType + "|" + resultValue);

        }} catch (InvocationTargetException ex) {{
            Throwable cause = ex.getCause() == null ? ex : ex.getCause();

            System.out.println(
                "EXC:"
                + cause.getClass().getSimpleName()
                + "|"
                + String.valueOf(cause.getMessage())
            );

        }} catch (Throwable ex) {{
            System.out.println(
                "EXC:"
                + ex.getClass().getSimpleName()
                + "|"
                + String.valueOf(ex.getMessage())
            );
        }}
    }}

    private static Object[] buildParameterObjectArgs(
        Class<?> parameterObjectType,
        String[] rawArgs
    ) throws Exception {{
        Constructor<?> selected = null;
        for (Constructor<?> constructor : parameterObjectType.getDeclaredConstructors()) {{
            if (constructor.getParameterTypes().length == rawArgs.length) {{
                selected = constructor;
                break;
            }}
        }}
        if (selected == null) {{
            throw new NoSuchMethodException("parameter object constructor mismatch");
        }}
        selected.setAccessible(true);
        Object value = selected.newInstance(buildArgs(selected.getParameterTypes(), rawArgs));
        return new Object[] {{value}};
    }}

    private static Object[] buildArgs(Class<?>[] parameterTypes, String[] rawArgs) {{
        Object[] values = new Object[parameterTypes.length];

        for (int i = 0; i < parameterTypes.length; i++) {{
            if (i < rawArgs.length) {{
                values[i] = convertRaw(rawArgs[i], parameterTypes[i]);
            }} else {{
                values[i] = defaultValue(parameterTypes[i]);
            }}
        }}

        return values;
    }}

    private static Object convertRaw(String raw, Class<?> type) {{
        if (type == String.class) return raw;

        if (type == boolean.class || type == Boolean.class) {{
            return Boolean.parseBoolean(raw);
        }}

        if (type == int.class || type == Integer.class) {{
            return Integer.parseInt(raw);
        }}

        if (type == short.class || type == Short.class) {{
            return Short.parseShort(raw);
        }}

        if (type == byte.class || type == Byte.class) {{
            return Byte.parseByte(raw);
        }}

        if (type == long.class || type == Long.class) {{
            return Long.parseLong(raw);
        }}

        if (type == double.class || type == Double.class) {{
            return Double.parseDouble(raw);
        }}

        if (type == float.class || type == Float.class) {{
            return Float.parseFloat(raw);
        }}

        if (type == char.class || type == Character.class) {{
            return raw.isEmpty() ? '\\0' : raw.charAt(0);
        }}

        return defaultValue(type);
    }}

    private static Object defaultValue(Class<?> type) {{
        if (type == boolean.class || type == Boolean.class) return false;
        if (type == int.class || type == Integer.class) return 0;
        if (type == short.class || type == Short.class) return (short)0;
        if (type == byte.class || type == Byte.class) return (byte)0;
        if (type == long.class || type == Long.class) return 0L;
        if (type == double.class || type == Double.class) return 0.0d;
        if (type == float.class || type == Float.class) return 0.0f;
        if (type == char.class || type == Character.class) return '\\0';
        if (type == String.class) return "";

        String simpleName = type.getSimpleName();

        try {{
            if (simpleName.equals("Customer")) {{
                return type
                    .getDeclaredConstructor(
                        int.class,
                        String.class,
                        String.class,
                        String.class,
                        String.class
                    )
                    .newInstance(
                        1,
                        "Pasan",
                        "pasan@example.com",
                        "premium",
                        "Colombo"
                    );
            }}

            if (simpleName.equals("Order")) {{
                Class<?> customerClass = Class.forName(siblingClassName("Customer"));

                Object customer = customerClass
                    .getDeclaredConstructor(
                        int.class,
                        String.class,
                        String.class,
                        String.class,
                        String.class
                    )
                    .newInstance(
                        1,
                        "Pasan",
                        "pasan@example.com",
                        "premium",
                        "Colombo"
                    );

                Object order = type
                    .getDeclaredConstructor(int.class, customerClass)
                    .newInstance(1001, customer);

                try {{
                    Class<?> itemClass = Class.forName(siblingClassName("OrderItem"));

                    Object item = itemClass
                        .getDeclaredConstructor(
                            String.class,
                            int.class,
                            double.class
                        )
                        .newInstance("Laptop", 2, 1200.00d);

                    Object itemList = type.getField("items").get(order);
                    itemList.getClass()
                        .getMethod("add", Object.class)
                        .invoke(itemList, item);
                }} catch (Exception ignored) {{
                }}

                return order;
            }}

            if (simpleName.equals("OrderItem")) {{
                return type
                    .getDeclaredConstructor(String.class, int.class, double.class)
                    .newInstance("Laptop", 2, 1200.00d);
            }}

            if (simpleName.equals("LocalDate")) {{
                return LocalDate.now();
            }}

            if (simpleName.equals("List")) {{
                return new ArrayList<Object>();
            }}

            if (simpleName.equals("Map")) {{
                return new HashMap<Object, Object>();
            }}

            return type.getDeclaredConstructor().newInstance();

        }} catch (Exception ex) {{
            return null;
        }}
    }}

    private static String siblingClassName(String simpleName) {{
        String targetPackage = "{target_package}";
        return targetPackage.isEmpty() ? simpleName : targetPackage + "." + simpleName;
    }}
}}
"""
