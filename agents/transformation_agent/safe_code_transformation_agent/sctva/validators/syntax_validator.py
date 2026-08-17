"""Syntax validation for Python, Java, and C outputs."""

from __future__ import annotations

import ast
import io
import re
import shutil
import subprocess
import tempfile
import time
import tokenize
from pathlib import Path

from .c_support import strip_c_comments
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso


class SyntaxValidator:
    """Performs language-specific syntax checks."""

    _JAVA_TYPE_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
    _JAVA_PUBLIC_TYPE_RE = re.compile(
        r"\bpublic\s+(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    )
    _C_FUNCTION_RE = re.compile(
        r"""
        (?m)^[ \t]*
        (?:[A-Za-z_][A-Za-z0-9_]*\s+|[*\s])*
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        \s*\([^;{}]*\)\s*\{
        """,
        re.VERBOSE,
    )

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
        details = {"checks": [], "warnings": [], "diagnostics": []}

        try:
            language = str(language or "").strip().lower()
            source_code = source_code or ""
            if not source_code.strip():
                passed = False
                message = "Syntax validation failed: source code is empty."
                details["diagnostics"].append({"severity": "error", "message": message})

            if language == "python":
                if passed:
                    passed, message = self._validate_python(source_code, details)

            elif language == "java":
                if passed:
                    passed, message = self._validate_java(source_code, details)

                if passed and require_compilation:
                    details["checks"].append("javac_compile")
                    compile_passed, compile_msg = self._optional_javac_check(source_code, timeout_seconds)
                    if not compile_passed:
                        passed = False
                        message = compile_msg
                        details["diagnostics"].append({"severity": "error", "message": compile_msg})
                    elif "skipped" in compile_msg.lower():
                        details["warnings"].append(compile_msg)

            elif language == "c":
                if passed:
                    passed, message = self._validate_c(source_code, details)

                if passed and require_compilation:
                    details["checks"].append("c_compile_only")
                    compile_passed, compile_msg = self._optional_c_compile_check(source_code, timeout_seconds)
                    if not compile_passed:
                        passed = False
                        message = compile_msg
                        details["diagnostics"].append({"severity": "error", "message": compile_msg})
                    elif "skipped" in compile_msg.lower():
                        details["warnings"].append(compile_msg)

            else:
                passed = False
                message = f"Unsupported language for syntax validation: {language}"
                details["diagnostics"].append({"severity": "error", "message": message})

        except SyntaxError as exc:
            passed = False
            message = f"Python syntax error: {exc}"
            details["diagnostics"].append(self._python_syntax_diagnostic(exc))
        except tokenize.TokenError as exc:
            passed = False
            message = f"Python tokenization error: {exc}"
            details["diagnostics"].append({"severity": "error", "message": message})
        except Exception as exc:
            passed = False
            message = f"Syntax validation exception: {exc}"
            details["diagnostics"].append({"severity": "error", "message": message})

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

    def _validate_python(self, source: str, details: dict) -> tuple[bool, str]:
        details["checks"].append("python_tokenize")
        list(tokenize.generate_tokens(io.StringIO(source).readline))

        details["checks"].append("ast.parse")
        ast.parse(source)

        details["checks"].append("python_compile")
        compile(source, "<sctva_python>", "exec")

        return True, "Python syntax validation passed."

    def _validate_java(self, source: str, details: dict) -> tuple[bool, str]:
        details["checks"].extend(
            [
                "java_advanced_lexical_scan",
                "java_comment_string_aware_delimiters",
                "java_type_declaration",
                "java_declaration_order",
                "java_multiline_statement_terminators",
            ]
        )

        scan_ok, scan_msg = self._scan_delimiters(source, language="java")
        if not scan_ok:
            return self._syntax_failure(details, "Java", scan_msg)

        clean = self._strip_comments_and_literals(source)
        if not self._JAVA_TYPE_RE.search(clean):
            return self._syntax_failure(details, "Java", "no class/interface/enum/record declaration found.")

        order_ok, order_msg = self._check_java_declaration_order(clean)
        if not order_ok:
            return self._syntax_failure(details, "Java", order_msg)

        nested_method = self._find_java_nested_method_declaration(clean)
        if nested_method:
            return self._syntax_failure(details, "Java", nested_method)

        malformed = self._find_malformed_assignment(clean)
        if malformed:
            return self._syntax_failure(details, "Java", malformed)

        terminator_issue = self._find_java_terminator_issue(clean)
        if terminator_issue:
            return self._syntax_failure(details, "Java", terminator_issue)

        return True, "Java syntax validation passed."

    def _validate_c(self, source: str, details: dict) -> tuple[bool, str]:
        details["checks"].extend(
            [
                "c_advanced_lexical_scan",
                "c_comment_string_aware_delimiters",
                "c_translation_unit_shape",
                "c_preprocessor_directives",
                "c_multiline_statement_terminators",
            ]
        )

        scan_ok, scan_msg = self._scan_delimiters(source, language="c")
        if not scan_ok:
            return self._syntax_failure(details, "C", scan_msg)

        clean = self._strip_comments_and_literals(source)
        clean_without_comments = strip_c_comments(source)
        has_function_definition = bool(self._C_FUNCTION_RE.search(clean_without_comments))
        if not has_function_definition and not self._looks_like_c_header_or_declaration_unit(clean_without_comments):
            return self._syntax_failure(
                details,
                "C",
                "no function definition or header/declaration constructs found.",
            )
        if not has_function_definition:
            details["warnings"].append(
                "No C function body found; validated as a header/declaration unit."
            )

        include_issue = self._find_c_preprocessor_issue(clean_without_comments)
        if include_issue:
            return self._syntax_failure(details, "C", include_issue)

        malformed = self._find_malformed_assignment(clean)
        if malformed:
            return self._syntax_failure(details, "C", malformed)

        terminator_issue = self._find_c_terminator_issue(clean)
        if terminator_issue:
            return self._syntax_failure(details, "C", terminator_issue)

        if ";" not in clean_without_comments and not self._is_preprocessor_only_unit(clean_without_comments):
            return self._syntax_failure(details, "C", "no semicolon found.")

        return True, "C syntax validation passed."

    @staticmethod
    def _syntax_failure(details: dict, language: str, reason: str) -> tuple[bool, str]:
        message = f"{language} syntax validation failed: {reason}"
        details["diagnostics"].append({"severity": "error", "message": message})
        return False, message

    @classmethod
    def _scan_delimiters(cls, source: str, *, language: str) -> tuple[bool, str]:
        stack: list[tuple[str, int, int]] = []
        pairs = {")": "(", "]": "[", "}": "{"}
        open_set = set(pairs.values())
        state = "normal"
        line = 1
        col = 0
        i = 0

        while i < len(source):
            char = source[i]
            nxt = source[i + 1] if i + 1 < len(source) else ""

            if char == "\n":
                line += 1
                col = 0
                if state == "line_comment":
                    state = "normal"
                i += 1
                continue

            col += 1

            if state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "normal"
                    i += 2
                    col += 1
                    continue
                i += 1
                continue

            if state == "line_comment":
                i += 1
                continue

            if state in {"string", "char"}:
                quote = '"' if state == "string" else "'"
                if char == "\\":
                    i += 2
                    col += 1
                    continue
                if char == quote:
                    state = "normal"
                i += 1
                continue

            if char == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                col += 1
                continue

            if char == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                col += 1
                continue

            if char == '"':
                state = "string"
                i += 1
                continue

            if char == "'":
                state = "char"
                i += 1
                continue

            if char in open_set:
                stack.append((char, line, col))
            elif char in pairs:
                if not stack or stack[-1][0] != pairs[char]:
                    return False, f"unmatched '{char}' at line {line}, column {col}"
                stack.pop()

            i += 1

        if state == "block_comment":
            return False, "unclosed block comment"
        if state == "string":
            return False, "unclosed string literal"
        if state == "char":
            return False, "unclosed character literal"
        if stack:
            bracket, bracket_line, bracket_col = stack[-1]
            return False, f"unclosed bracket '{bracket}' opened at line {bracket_line}, column {bracket_col}"

        return True, "balanced"

    @staticmethod
    def _strip_comments_and_literals(source: str) -> str:
        result: list[str] = []
        state = "normal"
        i = 0

        while i < len(source):
            char = source[i]
            nxt = source[i + 1] if i + 1 < len(source) else ""

            if state == "line_comment":
                if char == "\n":
                    state = "normal"
                    result.append("\n")
                else:
                    result.append(" ")
                i += 1
                continue

            if state == "block_comment":
                if char == "*" and nxt == "/":
                    result.extend("  ")
                    state = "normal"
                    i += 2
                else:
                    result.append("\n" if char == "\n" else " ")
                    i += 1
                continue

            if state in {"string", "char"}:
                if char == "\\":
                    result.append(" ")
                    if nxt:
                        result.append("\n" if nxt == "\n" else " ")
                    i += 2
                    continue
                if (state == "string" and char == '"') or (state == "char" and char == "'"):
                    state = "normal"
                result.append("\n" if char == "\n" else " ")
                i += 1
                continue

            if char == "/" and nxt == "/":
                state = "line_comment"
                result.extend("  ")
                i += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                result.extend("  ")
                i += 2
                continue
            if char == '"':
                state = "string"
                result.append("0")
                i += 1
                continue
            if char == "'":
                state = "char"
                result.append("0")
                i += 1
                continue

            result.append(char)
            i += 1

        return "".join(result)

    @staticmethod
    def _check_java_declaration_order(clean_source: str) -> tuple[bool, str]:
        package_matches = list(re.finditer(r"(?m)^\s*package\s+[A-Za-z_][A-Za-z0-9_.]*\s*;", clean_source))
        if len(package_matches) > 1:
            return False, "multiple package declarations found."

        first_type = re.search(r"\b(?:class|interface|enum|record)\s+[A-Za-z_][A-Za-z0-9_]*\b", clean_source)
        first_type_idx = first_type.start() if first_type else len(clean_source)

        if package_matches:
            non_blank_prefix = clean_source[: package_matches[0].start()].strip()
            if non_blank_prefix:
                return False, "package declaration must appear before imports and type declarations."

        for match in re.finditer(r"(?m)^\s*import\s+(?:static\s+)?[A-Za-z_][A-Za-z0-9_.*]*\s*;", clean_source):
            if match.start() > first_type_idx:
                return False, "import declaration appears after a type declaration."

        return True, "declaration order ok"

    @staticmethod
    def _find_malformed_assignment(clean_source: str) -> str:
        match = re.search(r"=\s*(?:[;,)}}]|$)", clean_source, re.MULTILINE)
        if not match:
            return ""
        line = clean_source[: match.start()].count("\n") + 1
        return f"malformed assignment or declaration near line {line}."

    @classmethod
    def _find_java_terminator_issue(cls, clean_source: str) -> str:
        statement_re = re.compile(r"^\s*(?:return|throw|break|continue)\b")
        delimiter_depth = 0
        pending_line: int | None = None
        pending_kind = ""
        lines = clean_source.splitlines()
        for index, raw_line in enumerate(lines):
            line_no = index + 1
            line = raw_line.strip()
            if not line or line.startswith("@") or line.startswith("*"):
                continue
            previous_depth = delimiter_depth
            delimiter_depth = max(0, delimiter_depth + cls._paren_bracket_delta(line))
            is_complete = line.endswith((";", "{", "}", ":", ","))
            is_continued = (
                previous_depth > 0
                or delimiter_depth > 0
                or cls._continues_expression(line)
                or cls._next_line_starts_expression_continuation(lines, index)
            )
            is_statement = bool(statement_re.match(line))
            is_assignment = cls._has_assignment_operator(line) and not re.search(r"\b(?:if|while|for|switch|catch)\s*\(", line)

            if pending_line is not None and is_complete and (is_statement or is_assignment):
                if pending_kind == "statement":
                    return f"statement missing semicolon near line {pending_line}."
                return f"assignment or declaration missing semicolon near line {pending_line}."
            if pending_line is not None and is_complete:
                pending_line = None
                pending_kind = ""
                continue
            if pending_line is not None and is_continued:
                continue
            if pending_line is not None:
                if pending_kind == "statement":
                    return f"statement missing semicolon near line {pending_line}."
                return f"assignment or declaration missing semicolon near line {pending_line}."

            if line.endswith((";", "{", "}", ":", ",")):
                continue
            if is_continued and (is_statement or is_assignment):
                pending_line = line_no
                pending_kind = "statement" if is_statement else "assignment"
                continue
            if is_continued:
                continue
            if is_statement:
                return f"statement missing semicolon near line {line_no}."
            if is_assignment:
                return f"assignment or declaration missing semicolon near line {line_no}."
        if pending_line is not None:
            if pending_kind == "statement":
                return f"statement missing semicolon near line {pending_line}."
            return f"assignment or declaration missing semicolon near line {pending_line}."
        return ""

    @staticmethod
    def _find_java_nested_method_declaration(clean_source: str) -> str:
        method_re = re.compile(
            r"^[ \t]*(?:(?:public|private|protected|static|final|synchronized|native)\s+)*"
            r"[A-Za-z_][A-Za-z0-9_<>\[\].?]*\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
        )
        method_stack: list[int] = []
        brace_depth = 0
        for line_no, raw_line in enumerate(clean_source.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("@"):
                continue
            previous_depth = brace_depth
            method_match = method_re.match(raw_line)
            if method_match and method_stack:
                return f"nested method declaration '{method_match.group('name')}' near line {line_no}."

            brace_depth += raw_line.count("{") - raw_line.count("}")
            if method_match:
                method_stack.append(previous_depth + 1)
            while method_stack and brace_depth < method_stack[-1]:
                method_stack.pop()
        return ""

    @staticmethod
    def _find_c_preprocessor_issue(clean_source: str) -> str:
        for line_no, raw_line in enumerate(clean_source.splitlines(), start=1):
            line = raw_line.strip()
            if not line.startswith("#"):
                continue
            if line.endswith("\\"):
                continue
            if re.match(r"#\s*include\s*(?:<[^>]+>|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_/.]*)\s*$", line):
                continue
            if re.match(r"#\s*(?:define|ifdef|ifndef|if|elif|else|endif|pragma|undef|error|warning|line)\b(?:\s+.*)?$", line):
                continue
            return f"invalid preprocessor directive syntax near line {line_no}."
        return ""

    @staticmethod
    def _looks_like_c_header_or_declaration_unit(source: str) -> bool:
        stripped = source.strip()
        if not stripped:
            return False

        patterns = (
            r"(?m)^\s*#\s*(?:ifndef|ifdef|if|define|include|pragma)\b",
            r"(?m)^\s*typedef\b[\s\S]*?;",
            r"(?m)^\s*(?:extern\s+)?(?:struct|enum|union)\b[\s\S]*?;",
            r"(?m)^\s*(?:extern\s+)?[A-Za-z_][A-Za-z0-9_\s\*]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^;{}]*\)\s*;",
            r"(?m)^\s*(?:extern\s+)?[A-Za-z_][A-Za-z0-9_\s\*]*\s+[A-Za-z_][A-Za-z0-9_]*\s*(?:\[[^\]]*\])?\s*;",
            r"(?m)^\s*extern\s+\"C\"\s*\{",
            r"(?m)^\s*(?:template\s*<[^>]+>\s*)?class\s+[A-Za-z_][A-Za-z0-9_]*\b",
            r"(?m)^\s*namespace\s+[A-Za-z_][A-Za-z0-9_]*\b",
        )
        return any(re.search(pattern, stripped) for pattern in patterns)

    @staticmethod
    def _is_preprocessor_only_unit(source: str) -> bool:
        meaningful = [
            line.strip()
            for line in source.splitlines()
            if line.strip()
        ]
        return bool(meaningful) and all(line.startswith("#") for line in meaningful)

    @classmethod
    def _find_c_terminator_issue(cls, clean_source: str) -> str:
        statement_re = re.compile(r"^\s*(?:return|break|continue|goto)\b")
        delimiter_depth = 0
        pending_line: int | None = None
        pending_kind = ""
        lines = clean_source.splitlines()
        for index, raw_line in enumerate(lines):
            line_no = index + 1
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            previous_depth = delimiter_depth
            delimiter_depth = max(0, delimiter_depth + cls._paren_bracket_delta(line))
            is_complete = line.endswith((";", "{", "}", ":", ","))
            is_continued = (
                previous_depth > 0
                or delimiter_depth > 0
                or cls._continues_expression(line)
                or cls._next_line_starts_expression_continuation(lines, index)
            )
            is_statement = bool(statement_re.match(line))
            is_assignment = cls._has_assignment_operator(line) and not re.search(r"\b(?:if|while|for|switch)\s*\(", line)

            if pending_line is not None and is_complete and (is_statement or is_assignment):
                if pending_kind == "statement":
                    return f"statement missing semicolon near line {pending_line}."
                return f"assignment or declaration missing semicolon near line {pending_line}."
            if pending_line is not None and is_complete:
                pending_line = None
                pending_kind = ""
                continue
            if pending_line is not None and is_continued:
                continue
            if pending_line is not None:
                if pending_kind == "statement":
                    return f"statement missing semicolon near line {pending_line}."
                return f"assignment or declaration missing semicolon near line {pending_line}."

            if line.endswith((";", "{", "}", ":", ",")):
                continue
            if is_continued and (is_statement or is_assignment):
                pending_line = line_no
                pending_kind = "statement" if is_statement else "assignment"
                continue
            if is_continued:
                continue
            if is_statement:
                return f"statement missing semicolon near line {line_no}."
            if is_assignment:
                return f"assignment or declaration missing semicolon near line {line_no}."
        if pending_line is not None:
            if pending_kind == "statement":
                return f"statement missing semicolon near line {pending_line}."
            return f"assignment or declaration missing semicolon near line {pending_line}."
        return ""

    @staticmethod
    def _paren_bracket_delta(line: str) -> int:
        return line.count("(") + line.count("[") - line.count(")") - line.count("]")

    @staticmethod
    def _has_assignment_operator(line: str) -> bool:
        return bool(re.search(r"(?<![=!<>+\-*/%&|^])=(?![=>])", line))

    @staticmethod
    def _continues_expression(line: str) -> bool:
        stripped = line.rstrip()
        return stripped.endswith(
            (
                ".",
                "+",
                "-",
                "*",
                "/",
                "%",
                "&&",
                "||",
                "?",
                "=",
                "==",
                "!=",
                "<",
                ">",
                "<=",
                ">=",
                "&",
                "|",
                "^",
                "\\",
            )
        )

    @staticmethod
    def _next_line_starts_expression_continuation(lines: list[str], current_index: int) -> bool:
        for next_line in lines[current_index + 1 :]:
            stripped = next_line.strip()
            if not stripped or stripped.startswith(("*", "//")):
                continue
            return stripped.startswith(
                (
                    ".",
                    "+",
                    "-",
                    "*",
                    "/",
                    "%",
                    "&&",
                    "||",
                    "?",
                    ":",
                    "&",
                    "|",
                    "^",
                )
            )
        return False

    @staticmethod
    def _python_syntax_diagnostic(exc: SyntaxError) -> dict:
        return {
            "severity": "error",
            "message": str(exc),
            "line": exc.lineno,
            "column": exc.offset,
            "text": exc.text.strip() if exc.text else "",
        }

    @staticmethod
    def _optional_javac_check(source: str, timeout_seconds: int) -> tuple[bool, str]:
        javac = shutil.which("javac")
        if not javac:
            return True, "javac not available; compile check skipped."

        source = source.lstrip("\ufeff")

        public_type = SyntaxValidator._JAVA_PUBLIC_TYPE_RE.search(source)
        first_type = SyntaxValidator._JAVA_TYPE_RE.search(source)
        class_name = (public_type or first_type).group(1) if (public_type or first_type) else "SctvaTemp"

        with tempfile.TemporaryDirectory() as temp_dir:
            classes_dir = Path(temp_dir) / "classes"
            classes_dir.mkdir(parents=True, exist_ok=True)
            java_file = Path(temp_dir) / f"{class_name}.java"
            java_file.write_text(source, encoding="utf-8")

            proc = subprocess.run(
                [javac, "-proc:none", "-d", str(classes_dir), str(java_file)],
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

            compile_args = [compiler, "-std=c11", "-fsyntax-only", "-I", temp_dir, str(c_file)]
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
