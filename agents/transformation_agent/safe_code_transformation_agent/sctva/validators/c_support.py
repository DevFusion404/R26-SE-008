"""Shared helpers for conservative C validation and fingerprinting."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from ..contracts import RefactoringAction
from .behavior_fingerprint import (
    compare_fingerprints,
    mine_exception_invariants,
    mine_value_invariants,
    stdout_invariants,
)


_C_COMMENT_RE = re.compile(r"/\*.*?\*/|//.*?$", re.MULTILINE | re.DOTALL)
_C_FUNCTION_RE = re.compile(
    r"(?ms)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^;{}]*)\)\s*\{"
)
_C_MACRO_RE = re.compile(r"(?m)^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b(.*)$")
_C_CONTROL_FLOW_RE = re.compile(r"\b(if|for|while|switch|case|default|goto)\b")


def _runtime_temp_root() -> Path:
    root = Path(tempfile.gettempdir()) / "sctva_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def strip_c_comments(source_code: str) -> str:
    return _C_COMMENT_RE.sub("", source_code)


def _normalize_params(params_raw: str) -> str:
    params_raw = params_raw.strip()
    if not params_raw:
        return "void"
    params = re.sub(r"\s+", " ", params_raw)
    params = params.replace("\n", " ").strip()
    return params


def extract_c_function_signatures(source_code: str) -> List[str]:
    source_code = strip_c_comments(source_code)
    signatures: List[str] = []

    for match in _C_FUNCTION_RE.finditer(source_code):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "case", "default"}:
            continue
        params = _normalize_params(match.group("params") or "")
        signatures.append(f"{name}:{params}")

    return signatures


def extract_c_macros(source_code: str) -> Dict[str, str]:
    source_code = strip_c_comments(source_code)
    macros: Dict[str, str] = {}

    for match in _C_MACRO_RE.finditer(source_code):
        name = match.group(1)
        value = match.group(2).strip()
        macros[name] = value

    return macros


def summarize_c_source(source_code: str) -> Dict[str, Any]:
    source_code = source_code.lstrip("\ufeff")
    function_signatures = extract_c_function_signatures(source_code)
    macros = extract_c_macros(source_code)
    control_flow_count = len(_C_CONTROL_FLOW_RE.findall(strip_c_comments(source_code)))
    return_count = len(re.findall(r"\breturn\b", strip_c_comments(source_code)))

    return {
        "parse_success": True,
        "functions": {signature.split(":", 1)[0]: signature for signature in function_signatures},
        "function_signatures": function_signatures,
        "function_count": len(function_signatures),
        "macros": macros,
        "macro_count": len(macros),
        "return_count": return_count,
        "control_flow_count": control_flow_count,
    }


def expected_c_transform_summary(actions: Sequence[RefactoringAction]) -> Dict[str, Any]:
    expected_renames: Dict[str, str] = {}
    expected_removed: set[str] = set()
    expected_macros: Dict[str, Any] = {}

    for action in actions:
        action_type = getattr(action, "action_type", "")
        params = getattr(action, "parameters", {}) or {}

        if action_type == "rename_symbol":
            old_name = str(params.get("old_name") or "").strip()
            new_name = str(params.get("new_name") or "").strip()
            if old_name and new_name:
                expected_renames[old_name] = new_name

        elif action_type == "remove_dead_code":
            method_name = str(
                params.get("method")
                or params.get("method_name")
                or ""
            ).strip()
            if method_name:
                expected_removed.add(method_name)

        elif action_type in {"extract_constant", "introduce_constant"}:
            literal_value = (
                params.get("literal_value")
                if "literal_value" in params
                else params.get("old_literal")
            )
            if literal_value is None:
                continue

            constant_name = str(
                params.get("constant_name")
                or params.get("new_name")
                or _constant_name_from_value(literal_value)
            )
            normalized_name = _sanitize_constant_name(constant_name)
            if normalized_name in {"EXTRACTED_CONSTANT", "MAGIC_CONSTANT", "CONSTANT", "VALUE_CONSTANT"}:
                normalized_name = _constant_name_from_value(literal_value)
            normalized_name = _normalize_legacy_magic_name(normalized_name, literal_value)
            unique_name = normalized_name
            suffix = 2
            while unique_name in expected_macros and expected_macros[unique_name] != literal_value:
                unique_name = f"{normalized_name}_{suffix}"
                suffix += 1
            expected_macros[unique_name] = literal_value

    return {
        "expected_renames": expected_renames,
        "expected_removed": sorted(expected_removed),
        "expected_macros": expected_macros,
    }


def compare_c_static_summaries(
    original_code: str,
    transformed_code: str,
    actions: Sequence[RefactoringAction],
) -> Dict[str, Any]:
    original_summary = summarize_c_source(original_code)
    transformed_summary = summarize_c_source(transformed_code)
    expected = expected_c_transform_summary(actions)

    if not original_summary.get("parse_success") or not transformed_summary.get("parse_success"):
        return {
            "matched": False,
            "reason": "parse_failed",
            "original": original_summary,
            "transformed": transformed_summary,
            "expected": expected,
        }

    expected_renames = expected["expected_renames"]
    expected_removed = set(expected["expected_removed"])
    expected_macros = expected["expected_macros"]

    original_signatures = dict(original_summary.get("functions", {}))
    transformed_signatures = dict(transformed_summary.get("functions", {}))

    normalized_expected_original = set()
    for name, signature in original_signatures.items():
        expected_name = expected_renames.get(name, name)
        if name in expected_removed:
            continue
        normalized_expected_original.add(signature.replace(name, expected_name, 1))

    transformed_signature_set = set(transformed_summary.get("function_signatures", []))

    missing_functions = sorted(normalized_expected_original - transformed_signature_set)
    unexpected_functions = sorted(transformed_signature_set - normalized_expected_original)

    transformed_macros = transformed_summary.get("macros", {})
    missing_macros: List[Dict[str, Any]] = []
    for name, value in expected_macros.items():
        expected_value = _c_literal(value)
        actual = transformed_macros.get(name)
        if actual is None and _has_equivalent_introduced_macro(
            transformed_macros,
            expected_value,
        ):
            continue
        if actual is None:
            missing_macros.append({"name": name, "expected_value": expected_value, "actual_value": None})
        elif str(actual).strip() != str(expected_value).strip():
            missing_macros.append({"name": name, "expected_value": expected_value, "actual_value": actual})

    matched = not missing_functions and not unexpected_functions and not missing_macros

    return {
        "matched": matched,
        "reason": "static_summary_match" if matched else "static_summary_mismatch",
        "original": original_summary,
        "transformed": transformed_summary,
        "expected": expected,
        "missing_functions": missing_functions,
        "unexpected_functions": unexpected_functions,
        "missing_macros": missing_macros,
    }


def _sanitize_constant_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_]", "_", text)
    if not text:
        return "CONSTANT_VALUE"
    if text[0].isdigit():
        text = f"N_{text}"
    return text.upper()


def _has_equivalent_introduced_macro(
    macros: Dict[str, str],
    expected_value: str,
) -> bool:
    expected = str(expected_value).strip()
    for name, actual_value in macros.items():
        normalized_name = str(name or "").upper()
        if not (
            normalized_name.startswith("MAGIC_")
            or normalized_name.startswith("CONSTANT_")
            or normalized_name.startswith("SCTVA_")
        ):
            continue
        if str(actual_value).strip() == expected:
            return True
    return False


def _normalize_legacy_magic_name(cleaned: str, literal_value: Any) -> str:
    if not cleaned.startswith("MAGIC_"):
        return cleaned
    if cleaned.startswith(("MAGIC_NUMBER_", "MAGIC_STRING_", "MAGIC_BOOL_")) or cleaned in {
        "MAGIC_NONE",
        "MAGIC_VALUE",
    }:
        return _constant_name_from_value(literal_value)
    return f"CONSTANT_{cleaned[len('MAGIC_'):]}"


def _constant_name_from_value(value: Any) -> str:
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
        text = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
        return f"CONSTANT_NUMBER_{text}"
    if isinstance(value, str):
        return f"CONSTANT_STRING_{_sanitize_constant_name(value[:24])}"
    return "CONSTANT_VALUE"


def _java_style_result(success: bool, return_value: str, return_type: str, stdout: str = "") -> Dict[str, Any]:
    return {
        "success": success,
        "return_value_repr": return_value,
        "return_type": return_type,
        "exception_type": None if success else "RuntimeError",
        "exception_message_category": None if success else "c_runtime_failed",
        "stdout": stdout,
        "execution_time_ms": 0,
        "timeout": False,
        "runtime_error_details": None,
        "observed_invariants": (
            {"return": mine_value_invariants(return_value), **stdout_invariants(stdout)}
            if success
            else {**mine_exception_invariants("RuntimeError", "c_runtime_failed"), **stdout_invariants(stdout)}
        ),
    }


def _c_literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + _escape_c_string(value) + '"'
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "0"
    return str(value)


def _escape_c_string(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\0":
            escaped.append("\\0")
        else:
            escaped.append(char)
    return "".join(escaped)


def build_c_runtime_harness(*, source_filename: str, target_function: str, args: Sequence[Any], returns_void: bool) -> str:
    rendered_args = ", ".join(_c_literal(arg) for arg in args)
    if rendered_args:
        call_expr = f"{target_function}({rendered_args})"
    else:
        call_expr = f"{target_function}()"

    observe_decl = """
typedef struct {
    const char *type;
    char value[256];
} SctvaObservation;

static SctvaObservation observe_int(long long value) {
    SctvaObservation result = {"int", {0}};
    snprintf(result.value, sizeof(result.value), "%lld", value);
    return result;
}

static SctvaObservation observe_double(double value) {
    SctvaObservation result = {"double", {0}};
    snprintf(result.value, sizeof(result.value), "%.17g", value);
    return result;
}

static SctvaObservation observe_float(float value) {
    SctvaObservation result = {"float", {0}};
    snprintf(result.value, sizeof(result.value), "%.9g", value);
    return result;
}

static SctvaObservation observe_char(int value) {
    SctvaObservation result = {"char", {0}};
    snprintf(result.value, sizeof(result.value), "%d", value);
    return result;
}

static SctvaObservation observe_string(const char *value) {
    SctvaObservation result = {"string", {0}};
    snprintf(result.value, sizeof(result.value), "%s", value ? value : "<null>");
    return result;
}

static SctvaObservation observe_pointer(const void *value) {
    SctvaObservation result = {"pointer", {0}};
    snprintf(result.value, sizeof(result.value), "%p", value);
    return result;
}

#define SCTVA_OBSERVE(expr) _Generic((expr), \
    char: observe_char, \
    signed char: observe_char, \
    unsigned char: observe_char, \
    short: observe_int, \
    unsigned short: observe_int, \
    int: observe_int, \
    unsigned int: observe_int, \
    long: observe_int, \
    unsigned long: observe_int, \
    long long: observe_int, \
    unsigned long long: observe_int, \
    float: observe_float, \
    double: observe_double, \
    long double: observe_double, \
    char *: observe_string, \
    const char *: observe_string, \
    void *: observe_pointer, \
    const void *: observe_pointer, \
    default: observe_pointer \
)(expr)
"""

    if returns_void:
        call_line = f"    {call_expr};\n    puts(\"OK:void|void\");"
    else:
        call_line = f"    SctvaObservation result = SCTVA_OBSERVE({call_expr});\n    printf(\"OK:%s|%s\\n\", result.type, result.value);"

    return f"""
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define main sctva_original_main
#include "{source_filename}"
#undef main

{observe_decl}

int main(void) {{
{call_line}
    return 0;
}}
"""


def run_c_runtime_test(
    source_code: str,
    test: Dict[str, Any],
    timeout_seconds: int,
) -> Dict[str, Any]:
    compiler = shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        return {
            "success": False,
            "return_value_repr": None,
            "return_type": None,
            "exception_type": "RuntimeUnavailable",
            "exception_message_category": "c_runtime_unavailable",
            "stdout": "",
            "execution_time_ms": 0,
            "timeout": False,
            "runtime_error_details": "gcc/clang not available",
            "observed_invariants": {
                **mine_exception_invariants("RuntimeUnavailable", "c_runtime_unavailable"),
                **stdout_invariants(""),
            },
        }

    source_code = source_code.lstrip("\ufeff")
    target_function = str(
        test.get("call")
        or test.get("function")
        or test.get("method")
        or test.get("target_method")
        or ""
    ).strip()
    if not target_function:
        return {
            "success": False,
            "return_value_repr": None,
            "return_type": None,
            "exception_type": "HarnessError",
            "exception_message_category": "missing_target_function",
            "stdout": "",
            "execution_time_ms": 0,
            "timeout": False,
            "runtime_error_details": "behavior test missing target function",
            "observed_invariants": {
                **mine_exception_invariants("HarnessError", "missing_target_function"),
                **stdout_invariants(""),
            },
        }

    args = test.get("args", []) or []
    returns_void = bool(test.get("returns_void") or test.get("return_type") == "void")
    start = time.perf_counter()

    with tempfile.TemporaryDirectory(
        dir=_runtime_temp_root(),
        ignore_cleanup_errors=True,
    ) as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "source.c"
        harness_path = temp_path / "probe.c"
        executable_path = temp_path / ("probe.exe" if shutil.which("cl") is None else "probe")

        source_path.write_text(source_code, encoding="utf-8")
        harness_path.write_text(
            build_c_runtime_harness(
                source_filename=source_path.name,
                target_function=target_function,
                args=args,
                returns_void=returns_void,
            ),
            encoding="utf-8",
        )

        compile_cmd = [compiler, "-std=c11", str(harness_path.name), "-o", str(executable_path.name)]

        try:
            compile_proc = subprocess.run(
                compile_cmd,
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
                "exception_message_category": "c_compile_timeout",
                "stdout": "",
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": True,
                "runtime_error_details": "C harness compilation timed out.",
                "observed_invariants": {
                    **mine_exception_invariants("TimeoutError", "c_compile_timeout"),
                    **stdout_invariants(""),
                },
            }

        if compile_proc.returncode != 0:
            return {
                "success": False,
                "return_value_repr": None,
                "return_type": None,
                "exception_type": "CompilationError",
                "exception_message_category": "c_compile_failed",
                "stdout": compile_proc.stdout,
                "stderr": compile_proc.stderr,
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": False,
                "runtime_error_details": compile_proc.stderr or compile_proc.stdout,
                "observed_invariants": {
                    **mine_exception_invariants("CompilationError", "c_compile_failed"),
                    **stdout_invariants(compile_proc.stdout or ""),
                },
            }

        try:
            run_proc = subprocess.run(
                [str(executable_path.name)],
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
                "exception_message_category": "c_runtime_timeout",
                "stdout": "",
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": True,
                "runtime_error_details": "C runtime probe timed out.",
                "observed_invariants": {
                    **mine_exception_invariants("TimeoutError", "c_runtime_timeout"),
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
                "exception_message_category": "c_runtime_failed",
                "stdout": stdout_raw,
                "stderr": run_proc.stderr,
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": False,
                "runtime_error_details": run_proc.stderr or stdout_raw,
                "observed_invariants": {
                    **mine_exception_invariants("RuntimeError", "c_runtime_failed"),
                    **stdout_invariants(stdout_raw),
                },
            }

        lines = [line.strip() for line in stdout_raw.splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""
        if last_line.startswith("OK:") and "|" in last_line:
            type_part, value_part = last_line.split("|", 1)
            return {
                "success": True,
                "return_value_repr": value_part,
                "return_type": type_part.replace("OK:", ""),
                "exception_type": None,
                "exception_message_category": None,
                "stdout": stdout_raw,
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": False,
                "runtime_error_details": None,
                "observed_invariants": {
                    "return": mine_value_invariants(_parse_c_observed_value(value_part)),
                    **stdout_invariants(stdout_raw),
                },
            }

        return {
            "success": True,
            "return_value_repr": last_line,
            "return_type": "unknown",
            "exception_type": None,
            "exception_message_category": None,
            "stdout": stdout_raw,
            "execution_time_ms": int((time.perf_counter() - start) * 1000),
            "timeout": False,
            "runtime_error_details": None,
            "observed_invariants": {
                "return": mine_value_invariants(_parse_c_observed_value(last_line)),
                **stdout_invariants(stdout_raw),
            },
        }


def _parse_c_observed_value(value: str) -> Any:
    text = str(value).strip()
    if text in {"void", "<null>"}:
        return None
    try:
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
            return float(text)
    except Exception:
        pass
    return text


def infer_c_runtime_tests_from_source(source_code: str, limit: int = 10) -> List[Dict[str, Any]]:
    stripped = strip_c_comments(source_code.lstrip("\ufeff"))
    inferred: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for match in _C_FUNCTION_RE.finditer(stripped):
        name = match.group("name")
        if name in seen or name in {"main", "if", "for", "while", "switch"}:
            continue

        params = _normalize_params(match.group("params") or "")
        if params not in {"", "void"}:
            continue

        prefix = stripped[match.start() : match.start("name")].strip()
        returns_void = bool(re.search(r"\bvoid\s*$", prefix))
        seen.add(name)
        inferred.append(
            {
                "name": f"auto_c_{name}",
                "call": name,
                "args": [],
                "returns_void": returns_void,
                "timeout_seconds": 8,
                "auto_generated": True,
            }
        )

        if len(inferred) >= limit:
            break

    return inferred


def validate_c_behavior(
    *,
    original_code: str,
    transformed_code: str,
    behavior_tests: List[Dict[str, Any]],
    actions: Sequence[RefactoringAction],
    enable_behavior_tests: bool,
    timeout_seconds: int,
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str | None = None,
) -> Dict[str, Any]:
    if not enable_behavior_tests:
        return {
            "passed": True,
            "score": 0.5,
            "message": "Behavioral fingerprinting skipped because it is disabled.",
            "details": {
                "checks": ["disabled"],
                "failures": [],
                "warnings": ["Behavioral validation was disabled."],
                "fingerprint_status": "skipped",
                "fingerprint_summary": "Behavioral fingerprinting disabled.",
            },
        }

    runtime_tests = list(behavior_tests or [])
    if runtime_tests:
        return _validate_c_runtime(
            original_code=original_code,
            transformed_code=transformed_code,
            runtime_tests=runtime_tests,
            timeout_seconds=timeout_seconds,
            actions=actions,
        )

    inferred_tests = infer_c_runtime_tests_from_source(original_code)
    if inferred_tests and (shutil.which("gcc") or shutil.which("clang")):
        result = _validate_c_runtime(
            original_code=original_code,
            transformed_code=transformed_code,
            runtime_tests=inferred_tests,
            timeout_seconds=timeout_seconds,
            actions=actions,
        )
        result["details"]["checks"].append("auto_generated_c_runtime_probes")
        result["details"]["warnings"].append(
            f"No explicit C behavior_tests were provided, so {len(inferred_tests)} "
            "no-argument runtime probe(s) were inferred from the source."
        )
        return result

    fallback_warnings = []
    if inferred_tests:
        fallback_warnings.append(
            "C runtime probes were inferred, but gcc/clang was not available; used static behavioral fingerprints instead."
        )

    return _validate_c_static(
        original_code=original_code,
        transformed_code=transformed_code,
        actions=actions,
        extra_warnings=fallback_warnings,
    )


def _validate_c_runtime(
    *,
    original_code: str,
    transformed_code: str,
    runtime_tests: List[Dict[str, Any]],
    timeout_seconds: int,
    actions: Sequence[RefactoringAction],
) -> Dict[str, Any]:
    fingerprints: List[Dict[str, Any]] = []
    failures: List[str] = []
    warnings: List[str] = []
    passed_count = 0
    dependency_unavailable_count = 0

    for idx, test in enumerate(runtime_tests, start=1):
        name = str(test.get("name") or test.get("test_id") or f"c_probe_{idx}")
        try:
            original_call = test.get("original_call") or test.get("call") or test.get("function") or test.get("method") or test.get("target_method")
            transformed_call = test.get("transformed_call") or test.get("call") or test.get("function") or test.get("method") or test.get("target_method")
            original_test = {**test, "call": original_call}
            transformed_test = {**test, "call": transformed_call}

            original_fp = run_c_runtime_test(original_code, original_test, timeout_seconds)
            transformed_fp = run_c_runtime_test(transformed_code, transformed_test, timeout_seconds)
            comparison = compare_fingerprints(original_fp, transformed_fp)
            dependency_unavailable = _c_fingerprints_dependency_unavailable(original_fp, transformed_fp)
            expected_failure = _expected_failure_c(
                name=name,
                test=test,
                original_fp=original_fp,
                transformed_fp=transformed_fp,
            )
            if expected_failure:
                comparison = {"matched": False, "reason": expected_failure}
                dependency_unavailable = False
            if dependency_unavailable:
                comparison = {"matched": False, "reason": "runtime_unavailable_due_to_dependencies"}
            entry = {
                "name": name,
                "call": original_call,
                "transformed_call": transformed_call,
                "args": test.get("args", []) or [],
                "auto_generated": bool(test.get("auto_generated")),
                "original_fingerprint": original_fp,
                "transformed_fingerprint": transformed_fp,
                "comparison": comparison,
                "dependency_unavailable": dependency_unavailable,
            }
            fingerprints.append(entry)
            if dependency_unavailable:
                dependency_unavailable_count += 1
                warnings.append(
                    f"{name}: C runtime probe could not execute because compiler, headers, or linked dependencies were unavailable."
                )
                remaining_tests = runtime_tests[idx:]
                if remaining_tests and not failures:
                    warnings.append(
                        f"Skipped {len(remaining_tests)} remaining C runtime probe(s) "
                        "after dependency-unavailable compilation was confirmed; "
                        "static behavioral fallback still ran."
                    )
                    for remaining_index, remaining in enumerate(remaining_tests, start=idx + 1):
                        remaining_name = str(
                            remaining.get("name")
                            or remaining.get("test_id")
                            or f"c_probe_{remaining_index}"
                        )
                        fingerprints.append(
                            {
                                "name": remaining_name,
                                "call": (
                                    remaining.get("original_call")
                                    or remaining.get("call")
                                    or remaining.get("function")
                                    or remaining.get("method")
                                    or remaining.get("target_method")
                                ),
                                "transformed_call": (
                                    remaining.get("transformed_call")
                                    or remaining.get("call")
                                    or remaining.get("function")
                                    or remaining.get("method")
                                    or remaining.get("target_method")
                                ),
                                "args": remaining.get("args", []) or [],
                                "auto_generated": bool(remaining.get("auto_generated")),
                                "status": "skipped",
                                "reason": "dependency_unavailable_after_preflight",
                                "dependency_unavailable": True,
                            }
                        )
                        dependency_unavailable_count += 1
                    break
            elif comparison.get("matched"):
                passed_count += 1
            else:
                failures.append(f"{name}: {comparison.get('reason', 'fingerprint_mismatch')}")
        except Exception as exc:
            failures.append(f"{name}: runtime error {exc}")

    total = len(runtime_tests)
    if fingerprints and dependency_unavailable_count == len(fingerprints) and not failures:
        result = _validate_c_static(
            original_code=original_code,
            transformed_code=transformed_code,
            actions=actions,
            extra_warnings=warnings
            + [
                "Used C static behavioral fallback because runtime probes could not execute with available dependencies."
            ],
        )
        result["score"] = min(result["score"], 0.75)
        result["details"]["checks"] = [
            "c_runtime_dependency_detection",
            *result["details"].get("checks", []),
        ]
        result["details"]["runtime_c_results"] = fingerprints
        result["details"]["runtime_unavailable_reason"] = "missing_c_dependencies"
        result["details"]["fingerprint_status"] = (
            "degraded_static_passed" if result["passed"] else "failed"
        )
        result["details"]["fingerprint_summary"] = (
            "C runtime probes could not execute because dependencies were unavailable; "
            + result["details"].get("fingerprint_summary", result["message"])
        )
        return result

    passed = len(failures) == 0
    score = passed_count / total if total else 0.0

    return {
        "passed": passed,
        "score": score,
        "message": (
            "C behavioral fingerprinting passed."
            if passed
            else f"C behavioral fingerprinting failed: {len(failures)} issue(s)."
        ),
        "details": {
            "checks": ["c_runtime_fingerprinting"],
            "total_tests": total,
            "passed_tests": passed_count,
            "failures": failures,
            "warnings": warnings,
            "c_results": fingerprints,
            "fingerprint_status": "passed" if passed else "failed",
            "fingerprint_summary": (
                f"{len(fingerprints)} C behavioral runtime probe(s) executed."
                if fingerprints
                else "No C runtime tests available; fingerprinting skipped."
            ),
        },
    }


def _c_fingerprints_dependency_unavailable(
    original_fp: Dict[str, Any],
    transformed_fp: Dict[str, Any],
) -> bool:
    return _c_fingerprint_dependency_unavailable(original_fp) and _c_fingerprint_dependency_unavailable(transformed_fp)


def _c_fingerprint_dependency_unavailable(fp: Dict[str, Any]) -> bool:
    if fp.get("success"):
        return False
    exception_type = str(fp.get("exception_type") or "")
    message = "\n".join(
        str(fp.get(key) or "")
        for key in ("exception_message_category", "runtime_error_details", "stderr", "stdout")
    ).lower()
    if exception_type == "RuntimeUnavailable":
        return True
    if exception_type != "CompilationError":
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
    return any(pattern in message for pattern in dependency_patterns) and not any(
        pattern in message for pattern in syntax_patterns
    )


def _validate_c_static(
    *,
    original_code: str,
    transformed_code: str,
    actions: Sequence[RefactoringAction],
    extra_warnings: List[str] | None = None,
) -> Dict[str, Any]:
    original_summary = summarize_c_source(original_code)
    transformed_summary = summarize_c_source(transformed_code)
    comparison = compare_c_static_summaries(original_code, transformed_code, actions)

    matched = bool(comparison.get("matched"))
    return {
        "passed": matched,
        "score": 1.0 if matched else 0.0,
        "message": (
            "C static behavioral fingerprinting passed."
            if matched
            else "C static behavioral fingerprinting failed."
        ),
        "details": {
            "checks": ["c_static_fingerprinting"],
            "failures": [] if matched else [comparison.get("reason", "static_summary_mismatch")],
            "warnings": [
                "No explicit C behavior_tests were provided.",
                "Used safe static behavioral fingerprints instead of executing arbitrary C functions.",
            ] + list(extra_warnings or []),
            "c_results": [
                {
                    "name": "static_c_summary",
                    "mode": "static_c_fingerprint",
                    "original_fingerprint": original_summary,
                    "transformed_fingerprint": transformed_summary,
                    "comparison": {
                        "matched": matched,
                        "reason": comparison.get("reason", "static_summary_mismatch"),
                    },
                }
            ],
            "fingerprint_status": "passed" if matched else "failed",
            "fingerprint_summary": (
                "C static behavioral fingerprinting passed."
                if matched
                else "C static behavioral fingerprinting failed."
            ),
            "static_comparison": comparison,
        },
    }


def _expected_failure_c(
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

    expected_values = {str(expected), repr(expected)}
    original_actual = str(original_fp.get("return_value_repr"))
    transformed_actual = str(transformed_fp.get("return_value_repr"))

    if original_fp.get("success") and original_actual not in expected_values:
        return f"original_expected_value_mismatch:{name}"

    if transformed_fp.get("success") and transformed_actual not in expected_values:
        return f"transformed_expected_value_mismatch:{name}"

    return ""


__all__ = [
    "compare_c_static_summaries",
    "extract_c_function_signatures",
    "extract_c_macros",
    "infer_c_runtime_tests_from_source",
    "run_c_runtime_test",
    "summarize_c_source",
    "validate_c_behavior",
]
