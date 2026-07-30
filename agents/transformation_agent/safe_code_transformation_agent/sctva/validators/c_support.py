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


_C_COMMENT_RE = re.compile(r"/\*.*?\*/|//.*?$", re.MULTILINE | re.DOTALL)
_C_FUNCTION_RE = re.compile(
    r"(?ms)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^;{}]*)\)\s*\{"
)
_C_MACRO_RE = re.compile(r"(?m)^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b(.*)$")
_C_CONTROL_FLOW_RE = re.compile(r"\b(if|for|while|switch|case|default|goto)\b")


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
            expected_macros[normalized_name] = literal_value

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

    missing_macros: List[Dict[str, Any]] = []
    for name, value in expected_macros.items():
        actual = transformed_summary.get("macros", {}).get(name)
        if actual is None:
            missing_macros.append({"name": name, "expected_value": value, "actual_value": None})
        elif str(actual).strip() != str(value).strip():
            missing_macros.append({"name": name, "expected_value": value, "actual_value": actual})

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
        return "MAGIC_VALUE"
    if text[0].isdigit():
        text = f"N_{text}"
    return text.upper()


def _constant_name_from_value(value: Any) -> str:
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
        text = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
        return f"MAGIC_NUMBER_{text}"
    if isinstance(value, str):
        return f"MAGIC_STRING_{_sanitize_constant_name(value[:24])}"
    return "MAGIC_VALUE"


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
    }


def _c_literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "0"
    return str(value)


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
        }

    args = test.get("args", []) or []
    returns_void = bool(test.get("returns_void") or test.get("return_type") == "void")
    start = time.perf_counter()

    with tempfile.TemporaryDirectory() as temp_dir:
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
        }


def validate_c_behavior(
    *,
    original_code: str,
    transformed_code: str,
    behavior_tests: List[Dict[str, Any]],
    actions: Sequence[RefactoringAction],
    enable_behavior_tests: bool,
    timeout_seconds: int,
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
        )

    return _validate_c_static(
        original_code=original_code,
        transformed_code=transformed_code,
        actions=actions,
    )


def _validate_c_runtime(
    *,
    original_code: str,
    transformed_code: str,
    runtime_tests: List[Dict[str, Any]],
    timeout_seconds: int,
) -> Dict[str, Any]:
    from .behavior_fingerprint import compare_fingerprints

    fingerprints: List[Dict[str, Any]] = []
    failures: List[str] = []
    warnings: List[str] = []
    passed_count = 0

    for idx, test in enumerate(runtime_tests, start=1):
        name = str(test.get("name") or test.get("test_id") or f"c_probe_{idx}")
        try:
            original_fp = run_c_runtime_test(original_code, test, timeout_seconds)
            transformed_fp = run_c_runtime_test(transformed_code, test, timeout_seconds)
            comparison = compare_fingerprints(original_fp, transformed_fp)
            entry = {
                "name": name,
                "call": test.get("call") or test.get("function") or test.get("method") or test.get("target_method"),
                "args": test.get("args", []) or [],
                "original_fingerprint": original_fp,
                "transformed_fingerprint": transformed_fp,
                "comparison": comparison,
            }
            fingerprints.append(entry)
            if comparison.get("matched"):
                passed_count += 1
            else:
                failures.append(f"{name}: {comparison.get('reason', 'fingerprint_mismatch')}")
        except Exception as exc:
            failures.append(f"{name}: runtime error {exc}")

    total = len(runtime_tests)
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


def _validate_c_static(
    *,
    original_code: str,
    transformed_code: str,
    actions: Sequence[RefactoringAction],
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
            ],
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


__all__ = [
    "compare_c_static_summaries",
    "extract_c_function_signatures",
    "extract_c_macros",
    "run_c_runtime_test",
    "summarize_c_source",
    "validate_c_behavior",
]