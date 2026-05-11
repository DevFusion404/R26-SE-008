"""Behavior-preservation checks for Python and Java."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .behavior_fingerprint import BehaviorFingerprintRunner, compare_fingerprints

from ..constants import KNOWN_UNSAFE_JAVA_ACTIONS
from ..contracts import RefactoringAction
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso
from ..utils.metrics import normalized_count_similarity


class BehavioralValidator:
    """Runs behavioral fingerprint validation for Python and Java.

    Main fix:
    If Java behavior_tests are missing, this validator now auto-generates
    Java runtime probes from the refactoring plan actions. Therefore, the
    original RDP refactoring-plan JSON structure does not need to change.
    """

    AUTO_PROBE_LIMIT = 10

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
                strict_mode=strict_mode,
            )
        else:
            passed, score, message, details = self._validate_java(
                original_code=original_code,
                transformed_code=transformed_code,
                behavior_tests=behavior_tests,
                actions=actions,
                strict_mode=strict_mode,
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
        strict_mode: bool,
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        if not behavior_tests:
            return (
                True,
                0.6,
                "Python behavioral fingerprinting skipped because no behavior tests were provided.",
                {
                    "checks": ["missing_tests"],
                    "failures": [],
                    "warnings": ["No Python behavior_tests were provided."],
                    "fingerprint_status": "skipped",
                    "fingerprint_summary": "No Python behavior tests provided.",
                },
            )

        runner = BehaviorFingerprintRunner()
        fingerprints: List[Dict[str, Any]] = []
        failures: List[str] = []
        passed_count = 0

        for idx, test in enumerate(behavior_tests, start=1):
            name = str(test.get("name", test.get("test_id", f"test_{idx}")))
            timeout = test.get("timeout_seconds") or test.get("timeout") or 2

            try:
                if "expression" in test:
                    original_fp = runner.run_python_test(
                        original_code,
                        {"expression": str(test["expression"])},
                        timeout=timeout,
                    )

                    transformed_fp = runner.run_python_test(
                        transformed_code,
                        {"expression": str(test["expression"])},
                        timeout=timeout,
                    )

                    entry = {
                        "name": name,
                        "expression": str(test["expression"]),
                    }

                else:
                    fn_name = str(test.get("call") or test.get("target_method") or "")

                    if not fn_name:
                        failures.append(f"{name}: missing call or target_method")
                        continue

                    args = test.get("args", []) or []
                    kwargs = test.get("kwargs", {}) or {}

                    call_test = {
                        "call": fn_name,
                        "args": args,
                        "kwargs": kwargs,
                    }

                    original_fp = runner.run_python_test(
                        original_code,
                        call_test,
                        timeout=timeout,
                    )

                    transformed_fp = runner.run_python_test(
                        transformed_code,
                        call_test,
                        timeout=timeout,
                    )

                    entry = {
                        "name": name,
                        "call": fn_name,
                        "args": args,
                        "kwargs": kwargs,
                    }

                comparison = compare_fingerprints(original_fp, transformed_fp)

                entry["original_fingerprint"] = original_fp
                entry["transformed_fingerprint"] = transformed_fp
                entry["comparison"] = comparison

                fingerprints.append(entry)

                if comparison.get("matched"):
                    passed_count += 1
                else:
                    failures.append(f"{name}: {comparison.get('reason')}")

            except Exception as exc:
                failures.append(f"{name}: runtime error {exc}")

        total = len(behavior_tests)
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
                "warnings": [],
                "fingerprints": fingerprints,
                "fingerprint_status": "passed" if passed else "failed",
                "fingerprint_summary": (
                    f"{passed_count}/{total} Python behavioral fingerprint test(s) passed."
                ),
            },
        )

    def _validate_java(
        self,
        *,
        original_code: str,
        transformed_code: str,
        behavior_tests: List[Dict[str, Any]],
        actions: Sequence[RefactoringAction],
        strict_mode: bool,
    ) -> tuple[bool, float, str, Dict[str, Any]]:
        checks: List[str] = []
        failures: List[str] = []
        warnings: List[str] = []
        component_scores: List[float] = []
        java_results: List[Dict[str, Any]] = []

        original_code = original_code.lstrip("\ufeff")
        transformed_code = transformed_code.lstrip("\ufeff")

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
                            "runtime probes could be inferred from the plan actions."
                        ],
                        "java_results": [
                            {
                                "name": f"java_probe_{idx}",
                                "status": "skipped",
                                "reason": "no_command_or_inference",
                            }
                            for idx, _ in enumerate(behavior_tests or [{"name": "java_probe_1"}], start=1)
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
            timeout_seconds = int(test.get("timeout_seconds", test.get("timeout", 8)) or 8)

            if not original_class or not transformed_class or not original_method or not transformed_method:
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
            )

            transformed_fp = self._run_java_runtime_probe(
                source_code=transformed_code,
                target_class=transformed_class,
                target_method=transformed_method,
                args=args,
                timeout_seconds=timeout_seconds,
            )

            comparison = compare_fingerprints(original_fp, transformed_fp)

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
                }
            )

            if comparison.get("matched"):
                component_scores.append(1.0)
            else:
                component_scores.append(0.0)
                failures.append(
                    f"{name}: {comparison.get('reason', 'fingerprint_mismatch')}"
                )

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
                        "timeout_seconds": 8,
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
                    "timeout_seconds": 8,
                    "auto_generated": True,
                    "source_step_id": action.source_step_id,
                    "source_refactoring": action.source_refactoring,
                }
            )

            if len(inferred) >= self.AUTO_PROBE_LIMIT:
                break

        return inferred

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
            }

        source_code = source_code.lstrip("\ufeff")
        class_name = self._extract_java_class_name(source_code)
        args = args or []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            source_path = temp_path / f"{class_name}.java"
            harness_path = temp_path / "JavaRuntimeProbeHarness.java"

            source_path.write_text(source_code, encoding="utf-8")

            harness_path.write_text(
                self._build_java_runtime_probe_harness(
                    target_class=target_class,
                    target_method=target_method,
                    args=args,
                ),
                encoding="utf-8",
            )

            started = time.perf_counter()

            try:
                compile_proc = subprocess.run(
                    [javac_exe, source_path.name, harness_path.name],
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
                }

            if compile_proc.returncode != 0:
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
            }

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

        if (type == long.class || type == Long.class) {{
            return Long.parseLong(raw);
        }}

        if (type == double.class || type == Double.class) {{
            return Double.parseDouble(raw);
        }}

        if (type == float.class || type == Float.class) {{
            return Float.parseFloat(raw);
        }}

        return defaultValue(type);
    }}

    private static Object defaultValue(Class<?> type) {{
        if (type == boolean.class || type == Boolean.class) return false;
        if (type == int.class || type == Integer.class) return 0;
        if (type == long.class || type == Long.class) return 0L;
        if (type == double.class || type == Double.class) return 0.0d;
        if (type == float.class || type == Float.class) return 0.0f;
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