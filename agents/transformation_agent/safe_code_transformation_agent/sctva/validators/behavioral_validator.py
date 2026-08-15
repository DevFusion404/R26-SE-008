"""Behavior-preservation checks for Python, Java, and C."""

from __future__ import annotations

import ast
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

from ..constants import KNOWN_UNSAFE_JAVA_ACTIONS
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
        for action in actions:
            if getattr(action, "action_type", "") != "rename_symbol":
                continue
            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()
            if old_name and new_name:
                rename_map[old_name] = new_name

        inferred: List[Dict[str, Any]] = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith("_"):
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
    ) -> Dict[str, Any]:
        if not original_summary.get("parse_success") or not transformed_summary.get("parse_success"):
            return {
                "matched": False,
                "reason": "parse_failed",
                "original": original_summary,
                "transformed": transformed_summary,
            }

        expected_renames: Dict[str, str] = {}

        for action in actions:
            if getattr(action, "action_type", "") != "rename_symbol":
                continue

            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()

            if old_name and new_name:
                expected_renames[old_name] = new_name

        original_functions = dict(original_summary.get("functions", {}))
        transformed_functions = dict(transformed_summary.get("functions", {}))

        missing_functions = []
        changed_signatures = []

        for old_name, old_signature in original_functions.items():
            expected_name = expected_renames.get(old_name, old_name)

            if expected_name not in transformed_functions:
                missing_functions.append(
                    {
                        "original_function": old_name,
                        "expected_transformed_function": expected_name,
                    }
                )
                continue

            new_signature = transformed_functions[expected_name]

            if old_signature != new_signature:
                changed_signatures.append(
                    {
                        "function": old_name,
                        "expected_name": expected_name,
                        "original_signature": old_signature,
                        "transformed_signature": new_signature,
                    }
                )

        matched = not missing_functions and not changed_signatures

        return {
            "matched": matched,
            "reason": (
                "function_signatures_preserved"
                if matched
                else "function_signature_mismatch"
            ),
            "original": {
                "function_count": len(original_functions),
                "functions": original_functions,
                "expected_renames": expected_renames,
            },
            "transformed": {
                "function_count": len(transformed_functions),
                "functions": transformed_functions,
                "missing_functions": missing_functions,
                "changed_signatures": changed_signatures,
            },
        }

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
                "rule": "Every used MAGIC_* or EXTRACTED_CONSTANT name must be declared as a module constant.",
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
            return "MAGIC_VALUE"

        if text[0].isdigit():
            text = f"N_{text}"

        return text.upper()

    def _constant_name_from_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return f"MAGIC_BOOL_{str(value).upper()}"

        if value is None:
            return "MAGIC_NONE"

        if isinstance(value, int):
            if value < 0:
                return f"MAGIC_NUMBER_NEG_{abs(value)}"
            return f"MAGIC_NUMBER_{value}"

        if isinstance(value, float):
            text = str(value).replace("-", "NEG_").replace(".", "_")
            return f"MAGIC_NUMBER_{self._sanitize_constant_name(text)}"

        if isinstance(value, str):
            short = value[:24]
            return f"MAGIC_STRING_{self._sanitize_constant_name(short)}"

        return "MAGIC_VALUE"

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
            )

            comparison = compare_fingerprints(original_fp, transformed_fp)
            dependency_unavailable = self._fingerprints_dependency_unavailable(
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
                dependency_unavailable = False

            if dependency_unavailable:
                comparison = {
                    "matched": False,
                    "reason": "runtime_unavailable_due_to_dependencies",
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
                    "dependency_unavailable": dependency_unavailable,
                }
            )

            if dependency_unavailable:
                dependency_unavailable_count += 1
                warnings.append(
                    f"{name}: Java runtime probe could not execute because project or external dependencies were unavailable."
                )
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
            )
            static_details["checks"] = [
                *checks,
                "java_runtime_dependency_detection",
                *static_details.get("checks", []),
            ]
            static_details["warnings"] = warnings + static_details.get("warnings", [])
            static_details["java_results"] = java_results
            static_details["runtime_unavailable_reason"] = "missing_java_dependencies"
            static_details["fingerprint_status"] = "degraded_static_passed" if static_passed else "failed"
            static_details["fingerprint_summary"] = (
                "Java runtime probes could not execute because dependencies were unavailable; "
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
        return (
            cls._fingerprint_dependency_unavailable(original_fp, language=language)
            and cls._fingerprint_dependency_unavailable(transformed_fp, language=language)
        )

    @staticmethod
    def _fingerprint_dependency_unavailable(fp: Dict[str, Any], *, language: str) -> bool:
        if fp.get("success"):
            return False

        exception_type = str(fp.get("exception_type") or "")
        message = "\n".join(
            str(fp.get(key) or "")
            for key in (
                "exception_message_category",
                "runtime_error_details",
                "stderr",
                "stdout",
            )
        ).lower()

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
            if exception_type != "CompilationError":
                return False
            dependency_patterns = (
                "package ",
                " does not exist",
                "cannot find symbol",
                "symbol:   class ",
                "symbol: class ",
                "class file for ",
                "not found",
                "cannot access ",
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
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        original_summary = self._java_static_summary(original_code)
        transformed_summary = self._java_static_summary(transformed_code)
        comparison = self._compare_java_static_compatibility(
            original_summary,
            transformed_summary,
            actions,
        )

        matched = bool(comparison.get("matched"))
        score = (0.6 * (1.0 if matched else 0.0)) + (0.4 * return_similarity)
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
                    "Used Java static behavioral fallback because runtime probes could not execute with available dependencies."
                ],
                "return_similarity": round(return_similarity, 4),
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
                        "mode": "static_java_fingerprint",
                        "original_fingerprint": original_summary,
                        "transformed_fingerprint": transformed_summary,
                        "comparison": comparison,
                    }
                ],
            },
        )

    def _java_static_summary(self, source_code: str) -> Dict[str, Any]:
        source_code = source_code.lstrip("\ufeff")
        clean = self._strip_java_comments_and_literals(source_code)
        class_names = self._extract_java_class_names(clean)
        methods: Dict[str, Dict[str, Any]] = {}

        for class_name in class_names:
            for candidate in self._extract_java_method_candidates(
                original_code=clean,
                class_name=class_name,
            ):
                method_name = candidate["name"]
                if method_name == class_name:
                    continue
                body = self._extract_java_method_body(clean, class_name, method_name)
                methods[f"{class_name}.{method_name}"] = {
                    "class_name": class_name,
                    "method_name": method_name,
                    "param_types": candidate.get("param_types", []),
                    "return_count": len(re.findall(r"\breturn\b", body)),
                    "throw_count": len(re.findall(r"\bthrow\b", body)),
                    "branch_count": len(re.findall(r"\b(?:if|for|while|switch|case|catch)\b", body)),
                }

        return {
            "class_names": class_names,
            "method_count": len(methods),
            "methods": methods,
            "return_count": len(re.findall(r"\breturn\b", clean)),
            "throw_count": len(re.findall(r"\bthrow\b", clean)),
        }

    def _compare_java_static_compatibility(
        self,
        original_summary: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        actions: Sequence[RefactoringAction],
    ) -> Dict[str, Any]:
        class_renames: Dict[str, str] = {}
        method_renames: Dict[str, str] = {}
        original_classes = set(original_summary.get("class_names", []))

        for action in actions:
            if getattr(action, "action_type", "") != "rename_symbol":
                continue
            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()
            if not old_name or not new_name:
                continue
            if old_name in original_classes:
                class_renames[old_name] = new_name
            else:
                method_renames[old_name] = new_name

        original_methods = dict(original_summary.get("methods", {}))
        transformed_methods = dict(transformed_summary.get("methods", {}))
        transformed_by_pair = {
            (item.get("class_name"), item.get("method_name")): item
            for item in transformed_methods.values()
        }
        missing_methods = []
        changed_methods = []

        for method_key, original_method in original_methods.items():
            original_class = original_method.get("class_name")
            original_method_name = original_method.get("method_name")
            expected_class = class_renames.get(str(original_class), original_class)
            expected_method = method_renames.get(str(original_method_name), original_method_name)
            transformed_method = transformed_by_pair.get((expected_class, expected_method))
            if not transformed_method:
                missing_methods.append(
                    {
                        "original_method": method_key,
                        "expected_class": expected_class,
                        "expected_method": expected_method,
                    }
                )
                continue

            for field in ("param_types", "return_count", "throw_count"):
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

        matched = not missing_methods and not changed_methods
        return {
            "matched": matched,
            "reason": "java_static_summary_preserved" if matched else "java_static_summary_mismatch",
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

            if action.action_type != "rename_symbol":
                continue

            old_name = str(action.parameters.get("old_name") or "").strip()
            new_name = str(action.parameters.get("new_name") or "").strip()

            if old_name in class_names and new_name:
                class_renames[old_name] = new_name

        for action in actions:
            if action.action_type != "rename_symbol":
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

            inferred.append(
                {
                    "name": f"auto_{owner_class}_{old_name}",
                    "original_target_class": owner_class,
                    "original_target_method": old_name,
                    "transformed_target_class": transformed_class,
                    "transformed_target_method": transformed_method,
                    "args": [],
                    "timeout_seconds": self.DEFAULT_JAVA_TIMEOUT_SECONDS,
                    "auto_generated": True,
                    "source_step_id": action.source_step_id,
                    "source_refactoring": action.source_refactoring,
                }
            )

            if len(inferred) >= self.AUTO_PROBE_LIMIT:
                break

        return inferred

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
        original_code = original_code.lstrip("\ufeff")

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

        method_pattern = re.compile(
            r"(?:public|protected|private)?\s*"
            r"(?:static\s+)?"
            r"(?:final\s+)?"
            r"(?:synchronized\s+)?"
            r"(?:native\s+)?"
            r"(?:abstract\s+)?"
            r"[\w<>\[\], ?]+\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
            r"\((?P<params>[^)]*)\)",
            re.MULTILINE,
        )

        candidates: List[Dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        for match in method_pattern.finditer(class_body):
            method_name = match.group("name")
            params_raw = match.group("params") or ""

            param_list = self._split_java_params(params_raw)
            param_types = [self._normalize_java_param_type(p) for p in param_list]
            param_types = [p for p in param_types if p]

            key = (method_name, len(param_types))
            if key in seen:
                continue
            seen.add(key)

            candidates.append(
                {
                    "name": method_name,
                    "param_types": param_types,
                }
            )

        return candidates

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
    ) -> Dict[str, Any]:
        java_exe = shutil.which("java")
        javac_exe = shutil.which("javac")

        if not java_exe or not javac_exe:
            return {
                "success": False,
                "return_value_repr": None,
                "return_type": None,
                "exception_type": "RuntimeUnavailable",
                "exception_message_category": "java_runtime_unavailable",
                "stdout": "",
                "execution_time_ms": 0,
                "timeout": False,
                "runtime_error_details": "java/javac not available",
                "observed_invariants": {
                    **mine_exception_invariants("RuntimeUnavailable", "java_runtime_unavailable"),
                    **stdout_invariants(""),
                },
            }

        source_code = source_code.lstrip("\ufeff")
        class_name = self._extract_java_class_name(source_code)
        package_name = self._extract_java_package(source_code)
        target_class_name = target_class
        if package_name and "." not in target_class_name:
            target_class_name = f"{package_name}.{target_class_name}"
        args = args or []

        temp_path = _make_runtime_temp_dir("java_fp")
        try:

            source_path = temp_path / f"{class_name}.java"
            harness_path = temp_path / "JavaRuntimeProbeHarness.java"

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
                ),
                encoding="utf-8",
            )

            started = time.perf_counter()

            try:
                compile_args = [javac_exe, source_path.name, harness_path.name]
                compile_args.extend(
                    str(path.relative_to(temp_path)).replace("\\", "/")
                    for path in project_java_paths
                    if path.name != source_path.name or path.read_text(encoding="utf-8") != source_code
                )
                compile_proc = subprocess.run(
                    compile_args,
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "TimeoutError",
                    "exception_message_category": "javac_timeout",
                    "stdout": "",
                    "execution_time_ms": int((time.perf_counter() - started) * 1000),
                    "timeout": True,
                    "runtime_error_details": "javac timed out.",
                    "observed_invariants": {
                        **mine_exception_invariants("TimeoutError", "javac_timeout"),
                        **stdout_invariants(""),
                    },
                }

            if compile_proc.returncode != 0:
                missing_symbols = re.findall(
                    r"symbol:\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
                    compile_proc.stderr or "",
                )
                missing_symbols = sorted(set(missing_symbols))

                if missing_symbols:
                    stub_paths: List[Path] = []
                    for symbol in missing_symbols:
                        if symbol == class_name:
                            continue
                        package_dir = temp_path
                        package_decl = ""
                        if package_name:
                            package_dir = temp_path / package_name.replace(".", "/")
                            package_dir.mkdir(parents=True, exist_ok=True)
                            package_decl = f"package {package_name};\n\n"

                        stub_path = package_dir / f"{symbol}.java"
                        if not stub_path.exists():
                            stub_path.write_text(
                                (
                                    f"{package_decl}public class {symbol} {{\n"
                                    f"    public {symbol}() {{}}\n"
                                    f"    public {symbol}(Object... args) {{}}\n"
                                    f"}}\n"
                                ),
                                encoding="utf-8",
                            )
                        stub_paths.append(stub_path)

                    if stub_paths:
                        compile_args = [
                            javac_exe,
                            source_path.name,
                            harness_path.name,
                        ]
                        compile_args.extend(
                            str(path.relative_to(temp_path)).replace("\\", "/")
                            for path in project_java_paths
                            if path.exists()
                        )
                        compile_args.extend(
                            str(path.relative_to(temp_path)).replace("\\", "/")
                            for path in stub_paths
                        )

                        compile_proc = subprocess.run(
                            compile_args,
                            cwd=temp_path,
                            capture_output=True,
                            text=True,
                            timeout=timeout_seconds,
                        )

                        if compile_proc.returncode == 0:
                            pass
                        else:
                            return {
                                "success": False,
                                "return_value_repr": None,
                                "return_type": None,
                                "exception_type": "CompilationError",
                                "exception_message_category": "javac_failed",
                                "stdout": compile_proc.stdout,
                                "stderr": compile_proc.stderr,
                                "execution_time_ms": int((time.perf_counter() - started) * 1000),
                                "timeout": False,
                                "runtime_error_details": compile_proc.stderr,
                                "observed_invariants": {
                                    **mine_exception_invariants("CompilationError", "javac_failed"),
                                    **stdout_invariants(compile_proc.stdout or ""),
                                },
                            }
                    else:
                        return {
                            "success": False,
                            "return_value_repr": None,
                            "return_type": None,
                            "exception_type": "CompilationError",
                            "exception_message_category": "javac_failed",
                            "stdout": compile_proc.stdout,
                            "stderr": compile_proc.stderr,
                            "execution_time_ms": int((time.perf_counter() - started) * 1000),
                            "timeout": False,
                            "runtime_error_details": compile_proc.stderr,
                            "observed_invariants": {
                                **mine_exception_invariants("CompilationError", "javac_failed"),
                                **stdout_invariants(compile_proc.stdout or ""),
                            },
                        }
                else:
                    return {
                        "success": False,
                        "return_value_repr": None,
                        "return_type": None,
                        "exception_type": "CompilationError",
                        "exception_message_category": "javac_failed",
                        "stdout": compile_proc.stdout,
                        "stderr": compile_proc.stderr,
                        "execution_time_ms": int((time.perf_counter() - started) * 1000),
                        "timeout": False,
                        "runtime_error_details": compile_proc.stderr,
                        "observed_invariants": {
                            **mine_exception_invariants("CompilationError", "javac_failed"),
                            **stdout_invariants(compile_proc.stdout or ""),
                        },
                    }

            try:
                run_proc = subprocess.run(
                    [java_exe, "-cp", str(temp_path), "JavaRuntimeProbeHarness"],
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "TimeoutError",
                    "exception_message_category": "java_probe_timeout",
                    "stdout": "",
                        "execution_time_ms": int((time.perf_counter() - started) * 1000),
                        "timeout": True,
                        "runtime_error_details": "Java runtime probe timed out.",
                        "observed_invariants": {
                            **mine_exception_invariants("TimeoutError", "java_probe_timeout"),
                            **stdout_invariants(""),
                        },
                    }

            stdout_raw = (run_proc.stdout or "").strip()

            if run_proc.returncode != 0:
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "RuntimeError",
                    "exception_message_category": "java_probe_failed",
                    "stdout": stdout_raw,
                    "stderr": run_proc.stderr,
                    "execution_time_ms": int((time.perf_counter() - started) * 1000),
                    "timeout": False,
                    "runtime_error_details": run_proc.stderr,
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
                "observed_invariants": {
                    "return": mine_value_invariants(return_value),
                    **stdout_invariants(return_value),
                },
            }
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)

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
    ) -> str:
        raw_args = BehavioralValidator._java_string_array(args or [])

        return f"""
import java.lang.reflect.*;
import java.time.LocalDate;
import java.util.*;

public class JavaRuntimeProbeHarness {{
    public static void main(String[] args) throws Exception {{
        try {{
            Class<?> targetClass = Class.forName("{target_class}");
            Object instance = targetClass.getDeclaredConstructor().newInstance();
            String[] rawArgs = {raw_args};

            Method targetMethod = null;

            for (Method method : targetClass.getDeclaredMethods()) {{
                if (
                    method.getName().equals("{target_method}")
                    && method.getParameterTypes().length == rawArgs.length
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

            Object[] values = buildArgs(targetMethod.getParameterTypes(), rawArgs);
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
                Class<?> customerClass = Class.forName("Customer");

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
                    Class<?> itemClass = Class.forName("OrderItem");

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
}}
"""
