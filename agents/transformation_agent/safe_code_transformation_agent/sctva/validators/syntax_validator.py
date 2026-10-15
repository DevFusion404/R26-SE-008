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
import uuid
from pathlib import Path

from .c_support import strip_c_comments
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso


def _make_syntax_temp_dir(prefix: str) -> Path:
    root = Path(tempfile.gettempdir()) / "sctva_syntax"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


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
    _C_COMPILER_DIAGNOSTIC_RE = re.compile(
        r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
        r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.*)$"
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
            if not source_code.strip() and language != "python":
                passed = False
                message = "Syntax validation failed: source code is empty."
                details["diagnostics"].append({"severity": "error", "message": message})
            elif not source_code.strip() and language == "python":
                details["warnings"].append(
                    "Python module is empty after removing proven dead code; ast.parse accepts an empty module."
                )

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
                    details["checks"].append("c_compiler_syntax_only")
                    compiler_result = self._compile_c_sources(
                        [{"file_name": "sctva_temp.c", "source_code": source_code}],
                        timeout_seconds=timeout_seconds,
                    )
                    details["compiler_validation"] = compiler_result["status"]
                    details["compiler"] = compiler_result.get("compiler")
                    details["compiler_details"] = compiler_result
                    if compiler_result["status"] == "FAIL":
                        passed = False
                        message = compiler_result["message"]
                        details["diagnostics"].extend(compiler_result["diagnostics"])
                    elif compiler_result["status"] == "UNAVAILABLE":
                        message = "C syntax validation passed; compiler validation unavailable."
                        details["warnings"].append(compiler_result["message"])

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


    def validate_python_project(
        self,
        sources: list[dict],
        *,
        timeout_seconds: int,
    ) -> ValidationStepResult:
        """Parse and compile every Python module in a candidate repository.

        Python's normal ``compile`` step intentionally does not import peer
        modules, so this method is side-effect free.  Cross-file semantic
        invariants (for example stale class imports after repository Inline
        Class) are checked by the transaction coordinator in addition to this
        syntax pass.
        """
        start_iso = utc_now_iso()
        started = time.perf_counter()
        details = {
            "checks": ["python_repository_parse", "python_repository_compile"],
            "warnings": [],
            "diagnostics": [],
            "validated_files": [],
        }

        for item in sources:
            file_name = str(item.get("file_name") or "")
            source = str(item.get("source_code") or "")
            normalized = file_name.replace("\\", "/").lower()
            language = str(item.get("language") or "").strip().lower()
            if language and language != "python" and not normalized.endswith(".py"):
                continue
            if not language and file_name and not normalized.endswith(".py"):
                continue
            local = self.validate(
                language="python",
                source_code=source,
                require_compilation=False,
                timeout_seconds=timeout_seconds,
            )
            if not local.passed:
                message = (
                    f"Python repository validation failed in {file_name}: "
                    f"{local.message}"
                )
                details["diagnostics"].append({
                    "severity": "error",
                    "file": file_name,
                    "message": local.message,
                    "details": local.details,
                })
                return ValidationStepResult(
                    name="python_repository_syntax",
                    passed=False,
                    score=0.0,
                    message=message,
                    details=details,
                    started_at=start_iso,
                    finished_at=utc_now_iso(),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            details["validated_files"].append(file_name)

        return ValidationStepResult(
            name="python_repository_syntax",
            passed=True,
            score=1.0,
            message="Python repository parse/compile validation passed.",
            details=details,
            started_at=start_iso,
            finished_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def validate_java_project(
        self,
        sources: list[dict],
        *,
        require_compilation: bool,
        timeout_seconds: int,
    ) -> ValidationStepResult:
        """Reparse and, when requested, compile a complete Java source set."""
        start_iso = utc_now_iso()
        started = time.perf_counter()
        details = {"checks": ["java_repository_reparse"], "warnings": [], "diagnostics": []}
        prepared: list[tuple[str, str]] = []
        for item in sources:
            file_name = str(item.get("file_name") or "")
            source = str(item.get("source_code") or "")
            local = self.validate(
                language="java",
                source_code=source,
                require_compilation=False,
                timeout_seconds=timeout_seconds,
            )
            if not local.passed:
                message = f"Java repository static validation failed in {file_name}: {local.message}"
                details["diagnostics"].append({"severity": "error", "file": file_name, "message": local.message})
                return ValidationStepResult(
                    name="java_repository_syntax",
                    passed=False,
                    score=0.0,
                    message=message,
                    details=details,
                    started_at=start_iso,
                    finished_at=utc_now_iso(),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            prepared.append((file_name, source.lstrip("\ufeff")))

        message = "Java repository static validation passed."
        passed = True
        if require_compilation:
            details["checks"].append("javac_project_compile")
            javac = shutil.which("javac")
            if not javac:
                details["warnings"].append("javac not available; repository compile check skipped.")
            else:
                temp_dir = _make_syntax_temp_dir("java_project")
                try:
                    classes_dir = temp_dir / "classes"
                    classes_dir.mkdir(parents=True, exist_ok=True)
                    java_files: list[str] = []
                    written: set[Path] = set()
                    for file_name, source in prepared:
                        clean = self._strip_comments_and_literals(source)
                        public_type = self._JAVA_PUBLIC_TYPE_RE.search(clean)
                        first_type = self._JAVA_TYPE_RE.search(clean)
                        type_name = (public_type or first_type).group(1) if (public_type or first_type) else Path(file_name).stem
                        package_match = re.search(r"(?m)^\s*package\s+([A-Za-z_$][\w.$]*)\s*;", clean)
                        package_dir = Path(*(package_match.group(1).split(".") if package_match else []))
                        destination = temp_dir / package_dir / f"{type_name}.java"
                        if destination in written:
                            passed = False
                            message = f"Java repository compile check failed: duplicate type source {destination.name}."
                            details["diagnostics"].append({"severity": "error", "file": file_name, "message": message})
                            break
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_text(source, encoding="utf-8")
                        written.add(destination)
                        java_files.append(str(destination))
                    if passed and java_files:
                        proc = subprocess.run(
                            [javac, "-proc:none", "-d", str(classes_dir), *java_files],
                            capture_output=True,
                            text=True,
                            timeout=timeout_seconds,
                        )
                        if proc.returncode != 0:
                            passed = False
                            stderr = (proc.stderr or proc.stdout or "").strip()
                            message = f"Java repository compile check failed: {stderr}"
                            details["diagnostics"].append({"severity": "error", "message": message})
                except subprocess.TimeoutExpired:
                    passed = False
                    message = "Java repository compile check timed out."
                    details["diagnostics"].append({"severity": "error", "message": message})
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

        return ValidationStepResult(
            name="java_repository_syntax",
            passed=passed,
            score=1.0 if passed else 0.0,
            message=message,
            details=details,
            started_at=start_iso,
            finished_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def validate_c_project(
        self,
        sources: list[dict],
        *,
        require_compilation: bool,
        timeout_seconds: int,
    ) -> ValidationStepResult:
        """Validate an in-memory C repository, including quoted local headers."""

        start_iso = utc_now_iso()
        started = time.perf_counter()
        details = {
            "checks": ["c_repository_lexical_validation"],
            "warnings": [],
            "diagnostics": [],
            "validated_files": [],
        }
        prepared: list[dict] = []

        for item in sources:
            file_name = str(item.get("file_name") or item.get("file_path") or "")
            source = str(item.get("source_code") or "")
            normalized = file_name.replace("\\", "/").lower()
            language = str(item.get("language") or "").strip().lower()
            if language and language != "c" and not normalized.endswith((".c", ".h")):
                continue
            if not language and file_name and not normalized.endswith((".c", ".h")):
                continue

            local = self.validate(
                language="c",
                source_code=source,
                require_compilation=False,
                timeout_seconds=timeout_seconds,
            )
            if not local.passed:
                message = f"C repository lexical validation failed in {file_name}: {local.message}"
                details["diagnostics"].append({
                    "severity": "error",
                    "file": file_name,
                    "message": local.message,
                    "details": local.details,
                })
                return ValidationStepResult(
                    name="c_repository_syntax",
                    passed=False,
                    score=0.0,
                    message=message,
                    details=details,
                    started_at=start_iso,
                    finished_at=utc_now_iso(),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            prepared.append({"file_name": file_name, "source_code": source})
            details["validated_files"].append(file_name)

        message = "C repository lexical validation passed."
        passed = True
        if require_compilation:
            details["checks"].append("c_repository_compiler_syntax_only")
            compiler_result = self._compile_c_sources(
                prepared,
                timeout_seconds=timeout_seconds,
            )
            details["compiler_validation"] = compiler_result["status"]
            details["compiler"] = compiler_result.get("compiler")
            details["compiler_details"] = compiler_result
            details["diagnostics"].extend(compiler_result["diagnostics"])
            if compiler_result["status"] == "FAIL":
                passed = False
                message = compiler_result["message"]
            elif compiler_result["status"] == "UNAVAILABLE":
                message = "C repository lexical validation passed; compiler validation unavailable."
                details["warnings"].append(compiler_result["message"])

        return ValidationStepResult(
            name="c_repository_syntax",
            passed=passed,
            score=1.0 if passed else 0.0,
            message=message,
            details=details,
            started_at=start_iso,
            finished_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
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

        # A C translation unit contains only one side of a conditional
        # preprocessor group.  Scanning both sides together makes otherwise
        # valid platform entry-points look like they have unmatched braces.
        delimiter_source = self._select_c_preprocessor_branch_for_scan(source)
        scan_ok, scan_msg = self._scan_delimiters(delimiter_source, language="c")
        if not scan_ok:
            return self._syntax_failure(details, "C", scan_msg)

        clean_without_comments = strip_c_comments(source)
        code_without_directives = self._mask_c_preprocessor_directives(clean_without_comments)
        clean = self._strip_comments_and_literals(code_without_directives)
        has_function_definition = bool(self._C_FUNCTION_RE.search(clean_without_comments))
        preprocessor_only = self._is_preprocessor_only_unit(clean_without_comments)
        if not has_function_definition and not (
            preprocessor_only
            or self._looks_like_c_header_or_declaration_unit(code_without_directives)
        ):
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

        if ";" not in code_without_directives and not preprocessor_only:
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
    def _mask_c_preprocessor_directives(source: str) -> str:
        """Replace C preprocessor directives with whitespace, preserving lines.

        Directive bodies are not C statements.  In particular, a continued
        ``#define`` must not be mistaken for an unterminated declaration by
        the lightweight terminator checker.
        """
        masked: list[str] = []
        directive_continues = False
        for raw_line in source.splitlines(keepends=True):
            stripped = raw_line.lstrip()
            is_directive = directive_continues or stripped.startswith("#")
            if is_directive:
                newline = "\n" if raw_line.endswith("\n") else ""
                content = raw_line[:-1] if newline else raw_line
                masked.append(" " * len(content) + newline)
                directive_continues = content.rstrip().endswith("\\")
            else:
                masked.append(raw_line)
                directive_continues = False
        return "".join(masked)

    @staticmethod
    def _select_c_preprocessor_branch_for_scan(source: str) -> str:
        """Mask inactive conditional-preprocessor branches for delimiter scans.

        The lexical validator cannot evaluate build flags.  Selecting the
        first branch in each conditional group still validates a coherent
        translation-unit shape and avoids combining mutually exclusive C
        entry-point declarations.  Newlines are kept so diagnostics retain
        their original line numbers.
        """
        lines = source.splitlines(keepends=True)
        active_stack: list[bool] = []
        selected: list[str] = []

        def mask(raw_line: str) -> str:
            newline = "\n" if raw_line.endswith("\n") else ""
            content = raw_line[:-1] if newline else raw_line
            return " " * len(content) + newline

        for raw_line in lines:
            directive = raw_line.lstrip()
            directive_match = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b", directive)
            currently_active = all(active_stack)
            if not directive_match:
                selected.append(raw_line if currently_active else mask(raw_line))
                continue

            kind = directive_match.group(1)
            # Keep directives blank: their tokens are irrelevant to brace
            # pairing and may contain arbitrary expressions.
            selected.append(mask(raw_line))
            if kind in {"ifdef", "ifndef", "if"}:
                active_stack.append(currently_active)
            elif kind in {"elif", "else"} and active_stack:
                active_stack[-1] = False
            elif kind == "endif" and active_stack:
                active_stack.pop()

        return "".join(selected)

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
                    # Preserve a physical line-continuation marker.  The C
                    # terminator scan needs it to know that an assignment to
                    # a multiline string literal has not ended yet.
                    result.append("\\" if nxt == "\n" else " ")
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
        # Do not let ``\s*`` consume a newline: C and Java both permit a
        # declaration/assignment to continue on the following line.
        match = re.search(r"=[^\S\r\n]*(?:[;,)}}]|$)", clean_source)
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
        """Reject Java methods directly nested in methods, not class bodies.

        A line-oriented method stack cannot distinguish an illegal declaration
        from a perfectly legal method declared by an anonymous/local class.
        This scanner first records method/class/anonymous-class opening braces,
        then evaluates each method against the active lexical scope.
        """
        identifier = r"[A-Za-z_$][A-Za-z0-9_$]*"
        method_start_re = re.compile(
            rf"(?m)^[ \t]*(?:(?:public|private|protected|static|final|abstract|"
            rf"synchronized|native|strictfp|default)\s+)*"
            rf"(?:<[^{{}}\n]+>\s+)?"
            rf"(?P<return_type>{identifier}(?:\s*<[^{{}}\n]+>)?(?:\s*\[\s*\])?)\s+"
            rf"(?P<name>{identifier})\s*\("
        )

        def matching_delimiter(start: int, opening: str, closing: str) -> int | None:
            depth = 0
            for index in range(start, len(clean_source)):
                char = clean_source[index]
                if char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
                    if depth == 0:
                        return index
            return None

        def next_non_space(index: int) -> int:
            while index < len(clean_source) and clean_source[index].isspace():
                index += 1
            return index

        method_braces: dict[int, str] = {}
        method_starts: dict[int, int] = {}
        excluded_return_types = {
            "return", "throw", "new", "if", "for", "while", "switch",
            "catch", "assert", "synchronized", "try",
        }
        for match in method_start_re.finditer(clean_source):
            if match.group("return_type") in excluded_return_types:
                continue
            paren_open = clean_source.find("(", match.start("name"))
            paren_close = matching_delimiter(paren_open, "(", ")")
            if paren_close is None:
                continue
            brace = next_non_space(paren_close + 1)
            if clean_source.startswith("throws", brace):
                throws_end = clean_source.find("{", brace)
                if throws_end < 0:
                    continue
                brace = throws_end
            if brace >= len(clean_source) or clean_source[brace] != "{":
                continue
            # A method call such as ``Type value()`` cannot be a declaration;
            # declarations have a type/name prefix at the start of the line.
            method_braces[brace] = match.group("name")
            method_starts[brace] = match.start()

        class_braces: dict[int, str] = {}
        class_re = re.compile(
            rf"\b(?:class|interface|enum|record|@interface)\s+{identifier}"
            rf"(?:\s+extends\s+[^{{}}]+|\s+implements\s+[^{{}}]+)?\s*\{{"
        )
        for match in class_re.finditer(clean_source):
            brace = clean_source.rfind("{", match.start(), match.end())
            if brace >= 0:
                class_braces[brace] = "CLASS"

        anonymous_braces: set[int] = set()
        new_re = re.compile(rf"\bnew\s+{identifier}(?:\s*\.\s*{identifier})?\s*(?:<[^{{}}]*>)?\s*\(")
        for match in new_re.finditer(clean_source):
            paren_open = clean_source.find("(", match.start())
            paren_close = matching_delimiter(paren_open, "(", ")")
            if paren_close is None:
                continue
            brace = next_non_space(paren_close + 1)
            if brace < len(clean_source) and clean_source[brace] == "{":
                anonymous_braces.add(brace)

        scopes: list[tuple[int, str]] = []
        events = sorted(
            (index, clean_source[index], priority)
            for index in range(len(clean_source))
            if clean_source[index] in "{}"
            for priority in (0,)
        )
        for index, character, _ in events:
            if character == "}":
                if scopes:
                    scopes.pop()
                continue

            if index in method_braces:
                kind = "METHOD"
            elif index in class_braces:
                kind = "LOCAL_CLASS" if any(
                    scope_kind == "METHOD" for _, scope_kind in scopes
                ) else "CLASS"
            elif index in anonymous_braces:
                kind = "ANONYMOUS_CLASS"
            else:
                kind = "BLOCK"

            if kind == "METHOD":
                enclosing = [scope_kind for _, scope_kind in scopes]
                last_class_or_method = next(
                    (
                        scope_kind
                        for scope_kind in reversed(enclosing)
                        if scope_kind in {"METHOD", "CLASS", "LOCAL_CLASS", "ANONYMOUS_CLASS"}
                    ),
                    None,
                )
                if last_class_or_method == "METHOD":
                    line_no = clean_source.count("\n", 0, method_starts[index]) + 1
                    return f"nested method declaration '{method_braces[index]}' near line {line_no}."

            scopes.append((index, kind))
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
        saw_directive = False
        directive_continues = False
        for raw_line in source.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if directive_continues:
                directive_continues = line.endswith("\\")
                continue
            if not line.startswith("#"):
                return False
            saw_directive = True
            directive_continues = line.endswith("\\")
        return saw_directive

    @classmethod
    def _find_c_terminator_issue(cls, clean_source: str) -> str:
        statement_re = re.compile(r"^\s*(?:return|break|continue|goto)\b")
        delimiter_depth = 0
        enum_depth = 0
        pending_line: int | None = None
        pending_kind = ""
        lines = clean_source.splitlines()
        for index, raw_line in enumerate(lines):
            line_no = index + 1
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            opens_enum = bool(
                re.search(r"\b(?:typedef\s+)?enum(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\{", line)
            )
            inside_enum = enum_depth > 0 or opens_enum
            enum_depth += line.count("{") if opens_enum else 0
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

            # Enum members are comma-separated, not semicolon-terminated.
            # The last member is also allowed to omit its trailing comma.
            if inside_enum:
                enum_depth = max(0, enum_depth - line.count("}"))
                continue

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

        temp_dir = _make_syntax_temp_dir("java")
        try:
            classes_dir = temp_dir / "classes"
            classes_dir.mkdir(parents=True, exist_ok=True)
            java_file = temp_dir / f"{class_name}.java"
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
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return True, "javac compile check passed."

    @staticmethod
    def _optional_c_compile_check(source: str, timeout_seconds: int) -> tuple[bool, str]:
        """Compatibility wrapper for callers that only need pass/fail text."""

        result = SyntaxValidator._compile_c_sources(
            [{"file_name": "sctva_temp.c", "source_code": source}],
            timeout_seconds=timeout_seconds,
        )
        return result["status"] != "FAIL", result["message"]

    @staticmethod
    def _find_c_compiler() -> tuple[str | None, str | None]:
        for compiler_name in ("gcc", "clang"):
            executable = shutil.which(compiler_name)
            if executable:
                return compiler_name, executable
        return None, None

    @staticmethod
    def _safe_c_workspace_path(file_name: str, index: int) -> Path:
        raw_parts = str(file_name or "").replace("\\", "/").split("/")
        parts = [
            re.sub(r"[^A-Za-z0-9_.-]", "_", part)
            for part in raw_parts
            if part and part not in {".", ".."}
        ]
        if not parts:
            return Path(f"sctva_source_{index}.c")
        path = Path(*parts)
        if path.suffix.lower() not in {".c", ".h"}:
            path = path.with_suffix(".c")
        return path

    @classmethod
    def _parse_c_compiler_diagnostics(
        cls,
        output: str,
        path_map: dict[str, str],
    ) -> list[dict]:
        diagnostics: list[dict] = []
        for raw_line in str(output or "").splitlines():
            match = cls._C_COMPILER_DIAGNOSTIC_RE.match(raw_line.strip())
            if match is None:
                continue
            compiler_path = str(Path(match.group("file")))
            original_file = path_map.get(compiler_path, match.group("file"))
            diagnostic = {
                "severity": match.group("severity"),
                "file": original_file,
                "line": int(match.group("line")),
                "message": match.group("message").strip(),
            }
            if match.group("column"):
                diagnostic["column"] = int(match.group("column"))
            diagnostics.append(diagnostic)
        if not diagnostics and output.strip():
            diagnostics.append({"severity": "error", "message": output.strip()})
        return diagnostics

    @classmethod
    def _compile_c_sources(
        cls,
        sources: list[dict],
        *,
        timeout_seconds: int,
    ) -> dict:
        """Compile in-memory C translation units with GCC/Clang syntax-only mode."""

        compiler_name, compiler = cls._find_c_compiler()
        if not compiler:
            return {
                "status": "UNAVAILABLE",
                "compiler": None,
                "reason": "C_COMPILER_NOT_AVAILABLE",
                "message": "C compiler validation unavailable: neither gcc nor clang was found.",
                "diagnostics": [],
                "validated_translation_units": [],
            }

        prepared = [
            item
            for item in sources
            if str(item.get("file_name") or item.get("file_path") or "").lower().endswith((".c", ".h"))
        ]
        if not prepared:
            return {
                "status": "UNAVAILABLE",
                "compiler": compiler_name,
                "reason": "NO_C_SOURCE_FILES",
                "message": "C compiler validation unavailable: no C source or header files were provided.",
                "diagnostics": [],
                "validated_translation_units": [],
            }

        temp_dir = _make_syntax_temp_dir("c_project")
        path_map: dict[str, str] = {}
        written: list[tuple[str, Path]] = []
        try:
            used_paths: set[Path] = set()
            for index, item in enumerate(prepared, start=1):
                original_name = str(item.get("file_name") or item.get("file_path") or f"source_{index}.c")
                destination = temp_dir / cls._safe_c_workspace_path(original_name, index)
                while destination in used_paths:
                    destination = destination.with_name(
                        f"{destination.stem}_{index}{destination.suffix}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    str(item.get("source_code") or "").lstrip("\ufeff"),
                    encoding="utf-8",
                )
                used_paths.add(destination)
                path_map[str(destination)] = original_name
                written.append((original_name, destination))

            include_dirs = sorted({temp_dir, *(path.parent for _, path in written)}, key=str)
            include_args = [arg for directory in include_dirs for arg in ("-I", str(directory))]
            translation_units = [
                (name, path) for name, path in written if path.suffix.lower() == ".c"
            ]
            # A header-only payload has no translation unit.  This still gives
            # the compiler a chance to catch declaration syntax without ever
            # linking or executing anything.
            if not translation_units:
                translation_units = [(name, path) for name, path in written if path.suffix.lower() == ".h"]

            validated: list[str] = []
            for original_name, unit in translation_units:
                command = [compiler, "-std=c11", "-fsyntax-only", *include_args]
                if unit.suffix.lower() == ".h":
                    command.extend(["-x", "c-header"])
                command.append(str(unit))
                try:
                    proc = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    return {
                        "status": "FAIL",
                        "compiler": compiler_name,
                        "reason": "C_COMPILER_TIMEOUT",
                        "message": f"{compiler_name} syntax-only validation timed out for {original_name}.",
                        "diagnostics": [{
                            "severity": "error",
                            "file": original_name,
                            "message": "C compiler syntax-only validation timed out.",
                        }],
                        "validated_translation_units": validated,
                    }
                output = (proc.stderr or proc.stdout or "").strip()
                if proc.returncode != 0:
                    return {
                        "status": "FAIL",
                        "compiler": compiler_name,
                        "reason": "C_COMPILER_SYNTAX_ERROR",
                        "message": f"{compiler_name} syntax-only validation failed for {original_name}.",
                        "diagnostics": cls._parse_c_compiler_diagnostics(output, path_map),
                        "validated_translation_units": validated,
                    }
                validated.append(original_name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return {
            "status": "PASS",
            "compiler": compiler_name,
            "reason": "C_COMPILER_SYNTAX_ONLY_PASSED",
            "message": f"C compiler validation passed with {compiler_name}.",
            "diagnostics": [],
            "validated_translation_units": validated,
        }
