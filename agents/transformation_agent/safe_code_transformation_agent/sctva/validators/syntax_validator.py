"""Syntax validation for Python, Java, and C outputs."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .c_support import strip_c_comments
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso


class SyntaxValidator:
    """Performs language-specific syntax checks."""

    def validate(
        self,
        *,
        language: str,
        source_code: str,
        require_compilation: bool,
        timeout_seconds: int,
    ) -> ValidationStepResult:
        start_iso = utc_now_iso()
        started = time.perf_counter()

        passed = True
        message = "Syntax validation passed."
        details = {"checks": []}

        try:
            if language == "python":
                ast.parse(source_code)
                details["checks"].append("ast.parse")
                if require_compilation:
                    compile(source_code, "<sctva_python>", "exec")
                    details["checks"].append("python_compile")

            elif language == "java":
                bracket_ok, bracket_msg = self._check_brackets(source_code)
                class_ok = bool(re.search(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*", source_code))
                details["checks"].append("bracket_heuristic")
                details["checks"].append("class_heuristic")

                if not bracket_ok:
                    passed = False
                    message = f"Java syntax heuristic failed: {bracket_msg}"
                elif not class_ok:
                    passed = False
                    message = "Java syntax heuristic failed: no class declaration found."

                if passed and require_compilation:
                    details["checks"].append("javac_compile")
                    compile_passed, compile_msg = self._optional_javac_check(source_code, timeout_seconds)
                    if not compile_passed:
                        passed = False
                        message = compile_msg
                    elif "skipped" in compile_msg.lower():
                        details["warning"] = compile_msg

            elif language == "c":
                bracket_ok, bracket_msg = self._check_brackets(source_code)
                function_ok = bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_\s\*]*\b[A-Za-z_][A-Za-z0-9_]*\s*\([^;{}]*\)\s*\{", strip_c_comments(source_code)))
                semicolon_ok = ";" in strip_c_comments(source_code)
                details["checks"].extend(["bracket_heuristic", "function_heuristic", "semicolon_heuristic"])

                if not bracket_ok:
                    passed = False
                    message = f"C syntax heuristic failed: {bracket_msg}"
                elif not function_ok:
                    passed = False
                    message = "C syntax heuristic failed: no function definition found."
                elif not semicolon_ok:
                    passed = False
                    message = "C syntax heuristic failed: no semicolon found."
                elif re.search(r"=\s*;", strip_c_comments(source_code)):
                    passed = False
                    message = "C syntax heuristic failed: malformed assignment or declaration."

                if passed and require_compilation:
                    details["checks"].append("c_compile_only")
                    compile_passed, compile_msg = self._optional_c_compile_check(source_code, timeout_seconds)
                    if not compile_passed:
                        passed = False
                        message = compile_msg
                    elif "skipped" in compile_msg.lower():
                        details["warning"] = compile_msg

            else:
                passed = False
                message = f"Unsupported language for syntax validation: {language}"

        except SyntaxError as exc:
            passed = False
            message = f"Python syntax error: {exc}"
        except Exception as exc:
            passed = False
            message = f"Syntax validation exception: {exc}"

        duration_ms = int((time.perf_counter() - started) * 1000)
        end_iso = utc_now_iso()
        score = 1.0 if passed else 0.0

        return ValidationStepResult(
            name="syntax",
            passed=passed,
            score=score,
            message=message,
            details=details,
            started_at=start_iso,
            finished_at=end_iso,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _check_brackets(source: str) -> tuple[bool, str]:
        stack = []
        pairs = {")": "(", "]": "[", "}": "{"}
        open_set = set(pairs.values())

        for idx, char in enumerate(source):
            if char in open_set:
                stack.append((char, idx))
            elif char in pairs:
                if not stack or stack[-1][0] != pairs[char]:
                    return False, f"unmatched '{char}' at index {idx}"
                stack.pop()

        if stack:
            return False, f"unclosed bracket '{stack[-1][0]}'"
        return True, "balanced"

    @staticmethod
    def _optional_javac_check(source: str, timeout_seconds: int) -> tuple[bool, str]:
        javac = shutil.which("javac")
        if not javac:
            return True, "javac not available; compile check skipped."

        source = source.lstrip("\ufeff")

        class_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", source)
        class_name = class_match.group(1) if class_match else "SctvaTemp"

        with tempfile.TemporaryDirectory() as temp_dir:
            java_file = Path(temp_dir) / f"{class_name}.java"
            java_file.write_text(source, encoding="utf-8")

            proc = subprocess.run(
                [javac, str(java_file)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or proc.stdout or "").strip()
                return False, f"javac compile check failed: {stderr}"

        return True, "javac compile check passed."

    @staticmethod
    def _optional_c_compile_check(source: str, timeout_seconds: int) -> tuple[bool, str]:
        compiler = shutil.which("gcc") or shutil.which("clang")
        if not compiler:
            return True, "C compiler not available; compile check skipped."

        source = source.lstrip("\ufeff")

        with tempfile.TemporaryDirectory() as temp_dir:
            c_file = Path(temp_dir) / "sctva_temp.c"
            c_file.write_text(source, encoding="utf-8")

            compile_args = [compiler, "-std=c11", "-fsyntax-only", str(c_file)]
            proc = subprocess.run(
                compile_args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or proc.stdout or "").strip()
                return False, f"C compile check failed: {stderr}"

        return True, f"{compiler} compile check passed."
