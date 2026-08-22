"""SCTVA-owned safe smell detection that complements the RDP plan.

The detector intentionally emits normal RefactoringAction objects. That keeps
SCTVA's local findings inside the same transform, validation, scoring, and
rollback pipeline used for RDP-supplied actions.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from typing import Any, Iterable, Sequence

from ..constants import (
    ACTION_EXTRACT_CONSTANT,
    ACTION_EXTRACT_METHOD,
    ACTION_INTRODUCE_CONSTANT,
    ACTION_NORMALIZE_MULTILINE_STATEMENT,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
)
from ..contracts import RefactoringAction


class LocalRefactorDetector:
    """Find small, validation-friendly refactorings that RDP may miss."""

    MAX_ACTIONS_PER_FILE = 40
    LONG_METHOD_MIN_LINES = 35
    LONG_STRING_MIN_LENGTH = 32
    REPEATED_STRING_MIN_LENGTH = 12

    _UNSAFE_C_FUNCTIONS = {
        "gets": "fgets",
        "strcpy": "strncpy",
        "strcat": "strncat",
        "sprintf": "snprintf",
    }

    _SQL_HINT_RE = re.compile(
        r"\b(select|insert|update|delete|where|join|values|from|order\s+by|group\s+by)\b",
        re.IGNORECASE,
    )
    _NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])")

    def detect(
        self,
        *,
        language: str,
        file_name: str,
        source_code: str,
        existing_actions: Sequence[RefactoringAction],
    ) -> list[RefactoringAction]:
        language = str(language or "").strip().lower()
        if language not in {"python", "java", "c"} or not source_code.strip():
            return []

        actions: list[RefactoringAction] = []
        seen = self._existing_action_keys(existing_actions)
        unscoped_plan_lines = {
            (action.action_type, action.parameters.get("source_line"))
            for action in existing_actions
            if action.parameters.get("source_line")
            and not str(
                action.parameters.get("source_file")
                or action.parameters.get("file")
                or ""
            ).strip()
        }

        def add(action: RefactoringAction) -> None:
            if len(actions) >= self.MAX_ACTIONS_PER_FILE:
                return
            key = self._action_key(action)
            if key in seen or (
                action.action_type,
                action.parameters.get("source_line"),
            ) in unscoped_plan_lines:
                return
            seen.add(key)
            actions.append(action)

        skip_string_lines: set[int] = set()
        if language == "java":
            for action in self._detect_java_multiline_sql(file_name, source_code):
                add(action)
                skip_string_lines.update(
                    int(line_no)
                    for line_no in action.parameters.get("covered_lines", [])
                    if isinstance(line_no, int)
                )

        if language == "c":
            for action in self._detect_c_unsafe_function_calls(file_name, source_code):
                add(action)

        # Broad exception handlers are an exception-handling smell, not dead
        # code.  Only emit an action when SCTVA can select a concrete narrow
        # type from an explicit throw/raise in the guarded block.  A bare
        # Python ``except:`` is the one safe, well-defined special case: it is
        # narrowed to ``Exception`` so control-flow exceptions are not hidden.
        for action in self._detect_exception_handler_smells(language, file_name, source_code):
            add(action)

        for action in self._detect_dead_code(language, file_name, source_code):
            add(action)

        for action in self._detect_long_methods(language, file_name, source_code):
            add(action)

        for action in self._detect_string_constants(language, file_name, source_code, skip_lines=skip_string_lines):
            add(action)

        for action in self._detect_magic_numbers(language, file_name, source_code):
            add(action)

        return actions

    def _detect_exception_handler_smells(
        self,
        language: str,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        if language == "python":
            yield from self._detect_python_broad_exception_handlers(file_name, source_code)
        elif language == "java":
            yield from self._detect_java_broad_exception_handlers(file_name, source_code)

    def _detect_python_broad_exception_handlers(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return

        for try_node in (node for node in ast.walk(tree) if isinstance(node, ast.Try)):
            raised_types = self._explicit_python_raised_types(try_node)
            for handler in try_node.handlers:
                original_type = self._python_exception_name(handler.type)
                if handler.type is None:
                    yield self._internal_action(
                        ACTION_NARROW_EXCEPTION_HANDLER,
                        file_name=file_name,
                        reason="bare Python except handler",
                        parameters={
                            "source_line": int(getattr(handler, "lineno", 0) or 0),
                            "original_exception_type": "",
                            "target_exception_type": "Exception",
                            "handler_name": str(handler.name or ""),
                            "exception_smell": "bare_except",
                        },
                    )
                    continue

                # ``BaseException`` deliberately remains review-only: changing
                # it may alter KeyboardInterrupt/SystemExit handling.  A plain
                # Exception handler is narrowed only when the guarded body has
                # one explicit exception type, which makes the edit auditable.
                if original_type != "Exception" or len(raised_types) != 1:
                    continue
                target_type = next(iter(raised_types))
                if target_type == "Exception":
                    continue
                yield self._internal_action(
                    ACTION_NARROW_EXCEPTION_HANDLER,
                    file_name=file_name,
                    reason="overly broad Python Exception handler with explicit raised type",
                    parameters={
                        "source_line": int(getattr(handler, "lineno", 0) or 0),
                        "original_exception_type": original_type,
                        "target_exception_type": target_type,
                        "handler_name": str(handler.name or ""),
                        "exception_smell": "exception_overreach",
                    },
                )

    @staticmethod
    def _python_exception_name(expression: ast.AST | None) -> str:
        if isinstance(expression, ast.Name):
            return expression.id
        if isinstance(expression, ast.Attribute):
            return expression.attr
        return ""

    @classmethod
    def _explicit_python_raised_types(cls, try_node: ast.Try) -> set[str]:
        raised_types: set[str] = set()

        def visit(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                # A nested definition is not executed by entering the outer
                # try block, so its raises must not influence this handler.
                return
            if isinstance(node, ast.Raise) and node.exc is not None:
                expression = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                name = cls._python_exception_name(expression)
                if name and name not in {"Exception", "BaseException"}:
                    raised_types.add(name)
            for child in ast.iter_child_nodes(node):
                visit(child)

        for statement in try_node.body:
            visit(statement)
        return raised_types

    def _detect_java_broad_exception_handlers(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        masked = self._mask_c_family_comments_and_strings(source_code)
        catch_re = re.compile(
            r"\bcatch\s*\(\s*(?:final\s+)?(?P<type>Exception|Throwable)\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
        )
        for match in catch_re.finditer(masked):
            raised_types = self._explicit_java_raised_types(masked, match.start())
            if len(raised_types) != 1:
                continue
            target_type = next(iter(raised_types))
            if target_type in {"Exception", "Throwable"}:
                continue
            yield self._internal_action(
                ACTION_NARROW_EXCEPTION_HANDLER,
                file_name=file_name,
                reason="overly broad Java catch handler with explicit thrown type",
                parameters={
                    "source_line": self._line_of(masked, match.start()),
                    "original_exception_type": match.group("type"),
                    "target_exception_type": target_type,
                    "handler_name": match.group("name"),
                    "exception_smell": "exception_overreach",
                },
            )

    @staticmethod
    def _explicit_java_raised_types(masked_source: str, catch_index: int) -> set[str]:
        """Return direct ``throw new X`` types from the matching try body.

        This intentionally avoids guessing exceptions from arbitrary method
        calls, which would make a behavior-preserving transformation unsafe.
        """

        candidate_bodies: list[str] = []
        for match in re.finditer(r"\btry\s*(?:\([^{}]*\)\s*)?\{", masked_source[:catch_index]):
            body_start = match.end() - 1
            body_end = LocalRefactorDetector._find_matching_brace(masked_source, body_start)
            if body_end is not None and body_end < catch_index:
                candidate_bodies.append(masked_source[body_start + 1:body_end])
        if not candidate_bodies:
            return set()
        body = candidate_bodies[-1]
        return {
            thrown.group(1)
            for thrown in re.finditer(
                r"\bthrow\s+new\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\(", body
            )
        }

    @staticmethod
    def _find_matching_brace(source_code: str, opening_index: int) -> int | None:
        depth = 0
        for index in range(opening_index, len(source_code)):
            character = source_code[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return index
        return None

    @classmethod
    def _existing_action_keys(
        cls,
        actions: Sequence[RefactoringAction],
    ) -> set[tuple[Any, ...]]:
        return {cls._action_key(action) for action in actions}

    @staticmethod
    def _action_key(action: RefactoringAction) -> tuple[Any, ...]:
        params = action.parameters or {}
        source_path = str(params.get("source_file") or params.get("file") or "").replace("\\", "/").lower()
        source_file = source_path.rsplit("/", 1)[-1]
        source_line = params.get("source_line")
        if action.action_type in {ACTION_EXTRACT_CONSTANT, ACTION_INTRODUCE_CONSTANT} and source_line:
            return (action.action_type, source_file, source_line)
        if action.action_type == ACTION_REMOVE_DEAD_CODE and source_line:
            return (action.action_type, source_file, source_line)
        if action.action_type == ACTION_EXTRACT_METHOD:
            return (
                action.action_type,
                source_file,
                params.get("method") or params.get("method_name"),
                params.get("start_line"),
                params.get("end_line"),
            )
        if action.action_type == ACTION_NORMALIZE_MULTILINE_STATEMENT and source_line:
            return (action.action_type, source_file, source_line, params.get("normalization"))
        return (
            action.action_type,
            source_file,
            source_line,
            params.get("start_line"),
            params.get("end_line"),
            params.get("literal_value"),
            tuple(params.get("literal_values") or []),
            params.get("unsafe_function"),
            params.get("method") or params.get("method_name"),
            params.get("normalization"),
        )

    @staticmethod
    def _internal_action(
        action_type: str,
        *,
        file_name: str,
        reason: str,
        parameters: dict[str, Any],
    ) -> RefactoringAction:
        final_parameters = {"source_file": file_name, **parameters}
        return RefactoringAction(
            action_type=action_type,
            parameters=final_parameters,
            source_step_id=None,
            source_refactoring="SCTVA Internal Analysis",
            warnings=[f"SCTVA internal detector added this action: {reason}"],
        )

    @staticmethod
    def _line_of(source_code: str, index: int) -> int:
        return source_code.count("\n", 0, max(0, index)) + 1

    @staticmethod
    def _safe_name(*parts: Any) -> str:
        raw = "_".join(str(part or "") for part in parts)
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_").upper()
        if not cleaned:
            cleaned = "VALUE"
        if cleaned[0].isdigit():
            cleaned = f"N_{cleaned}"
        return cleaned[:72]

    def _detect_java_multiline_sql(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        string = r'"(?:\\.|[^"\\])*"'
        pattern = re.compile(
            rf"""
            \bPreparedStatement\s+[A-Za-z_][A-Za-z0-9_]*\s*=
            \s*[A-Za-z_][A-Za-z0-9_.]*\s*\.prepareStatement\s*\(
            (?P<expr>\s*{string}(?:\s*\+\s*{string})+\s*)
            \)\s*;
            """,
            re.VERBOSE | re.DOTALL,
        )

        for match in pattern.finditer(source_code):
            expr = match.group("expr")
            if "\n" not in expr or not self._SQL_HINT_RE.search(expr):
                continue
            line_no = self._line_of(source_code, match.start())
            end_line = self._line_of(source_code, match.end())
            yield self._internal_action(
                ACTION_NORMALIZE_MULTILINE_STATEMENT,
                file_name=file_name,
                reason="multiline SQL string passed directly to prepareStatement",
                parameters={
                    "source_line": line_no,
                    "constant_name": self._safe_name("sctva_sql", file_name.rsplit("/", 1)[-1], line_no),
                    "normalization": "java_prepare_statement_sql",
                    "covered_lines": list(range(line_no, end_line + 1)),
                },
            )

    def _detect_c_unsafe_function_calls(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        masked = self._mask_c_family_comments_and_strings(source_code)
        for unsafe_function, safe_alternative in self._UNSAFE_C_FUNCTIONS.items():
            pattern = re.compile(rf"\b{re.escape(unsafe_function)}\s*\(")
            for match in pattern.finditer(masked):
                yield self._internal_action(
                    ACTION_REPLACE_UNSAFE_FUNCTION,
                    file_name=file_name,
                    reason=f"unsafe C function call '{unsafe_function}'",
                    parameters={
                        "unsafe_function": unsafe_function,
                        "safe_alternative": safe_alternative,
                        "source_line": self._line_of(masked, match.start()),
                    },
                )

    def _detect_dead_code(
        self,
        language: str,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        if language == "python":
            yield from self._detect_python_dead_code(file_name, source_code)
        elif language == "java":
            yield from self._detect_c_family_unused_declarations(
                file_name,
                source_code,
                declaration_re=re.compile(
                    r"^\s*(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>,.?\[\]]*\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*([^;]+))?;\s*(?://.*)?$"
                ),
            )
        elif language == "c":
            yield from self._detect_c_family_unused_declarations(
                file_name,
                source_code,
                declaration_re=re.compile(
                    r"^\s*(?:(?:const|volatile|unsigned|signed|short|long|static|register)\s+)*"
                    r"(?:struct\s+\w+|enum\s+\w+|union\s+\w+|[A-Za-z_]\w*)"
                    r"(?:\s*\*+|\s+)([A-Za-z_]\w*)\s*(?:=\s*([^;]+))?;\s*(?://.*)?$"
                ),
            )

    def _detect_python_dead_code(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return

        emitted_spans: set[tuple[int, int]] = set()
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        false_branch_nodes: set[ast.AST] = set()

        # Constant-false branches are safe only when the condition is made
        # entirely from literals.  This deliberately does not evaluate names,
        # calls, attributes, or arbitrary Python expressions.
        for node in ast.walk(tree):
            if not isinstance(node, ast.If) or node.orelse:
                continue
            if not self._python_constant_false(node.test):
                continue
            span = self._python_statement_span(node)
            emitted_spans.add(span)
            false_branch_nodes.add(node)
            yield self._internal_action(
                ACTION_REMOVE_DEAD_CODE,
                file_name=file_name,
                reason="statically unreachable Python if False branch",
                parameters={
                    "source_line": getattr(node, "lineno", None),
                    "dead_code_kind": "constant_false_branch",
                    "target_statement_fingerprint": ast.dump(
                        node,
                        include_attributes=False,
                    ),
                },
            )

        for suite in self._python_statement_suites(tree):
            terminated = False
            for statement in suite:
                if self._has_false_branch_ancestor(statement, parents, false_branch_nodes):
                    continue
                if terminated:
                    span = self._python_statement_span(statement)
                    if span in emitted_spans:
                        continue
                    emitted_spans.add(span)
                    yield self._internal_action(
                        ACTION_REMOVE_DEAD_CODE,
                        file_name=file_name,
                        reason="unreachable Python statement after a terminator",
                        parameters={
                            "source_line": getattr(statement, "lineno", None),
                            # Earlier plan actions can insert lines before this
                            # local action runs. Preserve an AST anchor so SCTVA
                            # can relocate this exact proven-dead statement.
                            "dead_code_kind": "unreachable_after_terminator",
                            "target_statement_fingerprint": ast.dump(
                                statement,
                                include_attributes=False,
                            ),
                        },
                    )
                if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                    terminated = True

        for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            loaded = {
                node.id
                for node in ast.walk(function)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            for statement in ast.walk(function):
                if self._has_false_branch_ancestor(statement, parents, false_branch_nodes):
                    continue
                names = self._python_literal_assignment_names(statement)
                if names and not any(name in loaded for name in names):
                    yield self._internal_action(
                        ACTION_REMOVE_DEAD_CODE,
                        file_name=file_name,
                        reason="unused side-effect-free Python local assignment",
                        parameters={
                            "source_line": getattr(statement, "lineno", None),
                            "dead_code_kind": "unused_literal_assignment",
                            "target_statement_fingerprint": ast.dump(
                                statement,
                                include_attributes=False,
                            ),
                        },
                    )

    @staticmethod
    def _python_statement_span(node: ast.stmt) -> tuple[int, int]:
        start = int(getattr(node, "lineno", 0) or 0)
        return start, int(getattr(node, "end_lineno", start) or start)

    @staticmethod
    def _has_false_branch_ancestor(
        node: ast.AST,
        parents: dict[ast.AST, ast.AST],
        false_branch_nodes: set[ast.AST],
    ) -> bool:
        current = parents.get(node)
        while current is not None:
            if current in false_branch_nodes:
                return True
            current = parents.get(current)
        return False

    @classmethod
    def _python_constant_false(cls, expression: ast.AST) -> bool:
        """Evaluate only literal-only conditions without executing code."""

        if isinstance(expression, ast.Constant):
            return not bool(expression.value)
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            value = cls._python_static_boolean(expression.operand)
            return value is False
        if isinstance(expression, ast.BoolOp):
            values = [cls._python_static_boolean(value) for value in expression.values]
            if any(value is None for value in values):
                return False
            return (all(values) if isinstance(expression.op, ast.And) else any(values)) is False
        if isinstance(expression, ast.Compare) and len(expression.ops) == 1 and len(expression.comparators) == 1:
            try:
                left = ast.literal_eval(expression.left)
                right = ast.literal_eval(expression.comparators[0])
                operator = expression.ops[0]
                if isinstance(operator, ast.Eq):
                    return (left == right) is False
                if isinstance(operator, ast.NotEq):
                    return (left != right) is False
                if isinstance(operator, ast.Lt):
                    return (left < right) is False
                if isinstance(operator, ast.LtE):
                    return (left <= right) is False
                if isinstance(operator, ast.Gt):
                    return (left > right) is False
                if isinstance(operator, ast.GtE):
                    return (left >= right) is False
            except (TypeError, ValueError, SyntaxError, MemoryError, RecursionError):
                return False
        return False

    @classmethod
    def _python_static_boolean(cls, expression: ast.AST) -> bool | None:
        if isinstance(expression, ast.Constant):
            return bool(expression.value)
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            value = cls._python_static_boolean(expression.operand)
            return None if value is None else not value
        if isinstance(expression, ast.BoolOp):
            values = [cls._python_static_boolean(value) for value in expression.values]
            if any(value is None for value in values):
                return None
            return all(values) if isinstance(expression.op, ast.And) else any(values)
        return None

    @staticmethod
    def _python_statement_suites(tree: ast.AST) -> Iterable[list[ast.stmt]]:
        for node in ast.walk(tree):
            for field in (
                "body",
                "orelse",
                "finalbody",
            ):
                value = getattr(node, field, None)
                if isinstance(value, list) and all(isinstance(item, ast.stmt) for item in value):
                    yield value
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    yield handler.body

    @staticmethod
    def _python_literal_assignment_names(statement: ast.AST) -> list[str]:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            return []

        try:
            ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return []

        names: list[str] = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            else:
                return []
        return names

    def _detect_c_family_unused_declarations(
        self,
        file_name: str,
        source_code: str,
        *,
        declaration_re: re.Pattern[str],
    ) -> Iterable[RefactoringAction]:
        masked = self._mask_c_family_comments_and_strings(source_code)
        for line_no, line in enumerate(source_code.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "import ", "package ")):
                continue
            match = declaration_re.match(stripped)
            if not match:
                continue
            name = match.group(1)
            initializer = match.group(2) or ""
            if initializer and re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", initializer):
                continue
            if len(re.findall(rf"\b{re.escape(name)}\b", masked)) != 1:
                continue
            yield self._internal_action(
                ACTION_REMOVE_DEAD_CODE,
                file_name=file_name,
                reason="unused side-effect-free local declaration",
                parameters={"source_line": line_no},
            )

    def _detect_long_methods(
        self,
        language: str,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        if language == "python":
            yield from self._detect_python_long_methods(file_name, source_code)
        elif language == "java":
            yield from self._detect_java_long_methods(file_name, source_code)
        elif language == "c":
            yield from self._detect_c_long_functions(file_name, source_code)

    def _detect_python_long_methods(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end_line = getattr(node, "end_lineno", None)
            start_line = getattr(node, "lineno", None)
            if not start_line or not end_line or end_line - start_line + 1 < self.LONG_METHOD_MIN_LINES:
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            if len(body) < 3:
                continue
            yield self._internal_action(
                ACTION_EXTRACT_METHOD,
                file_name=file_name,
                reason=f"long Python function '{node.name}'",
                parameters={
                    "method": node.name,
                    "new_method_name": f"extracted_{node.name}_responsibility",
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )

    def _detect_java_long_methods(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        method_re = re.compile(
            r"(?m)^[ \t]*(?:(?:public|private|protected|static|final|synchronized|native)\s+)*"
            r"[A-Za-z_][A-Za-z0-9_<>\[\].?]*\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
        )
        for match in method_re.finditer(source_code):
            brace_idx = source_code.find("{", match.end() - 1)
            end_idx = self._find_matching_brace(source_code, brace_idx)
            if end_idx is None:
                continue
            start_line = self._line_of(source_code, match.start())
            end_line = self._line_of(source_code, end_idx)
            if end_line - start_line + 1 < self.LONG_METHOD_MIN_LINES:
                continue
            method_name = match.group("name")
            yield self._internal_action(
                ACTION_EXTRACT_METHOD,
                file_name=file_name,
                reason=f"long Java method '{method_name}'",
                parameters={
                    "method": method_name,
                    "new_method_name": f"extracted_{method_name}_responsibility",
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )

    def _detect_c_long_functions(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        function_re = re.compile(
            r"(?m)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
        )
        for match in function_re.finditer(source_code):
            brace_idx = source_code.find("{", match.end() - 1)
            end_idx = self._find_matching_brace(source_code, brace_idx)
            if end_idx is None:
                continue
            start_line = self._line_of(source_code, match.start())
            end_line = self._line_of(source_code, end_idx)
            if end_line - start_line + 1 < self.LONG_METHOD_MIN_LINES:
                continue
            function_name = match.group("name")
            yield self._internal_action(
                ACTION_EXTRACT_METHOD,
                file_name=file_name,
                reason=f"long C function '{function_name}'",
                parameters={
                    "method": function_name,
                    "new_method_name": f"extracted_{function_name}_responsibility",
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )

    def _detect_string_constants(
        self,
        language: str,
        file_name: str,
        source_code: str,
        *,
        skip_lines: set[int] | None = None,
    ) -> Iterable[RefactoringAction]:
        skip_lines = skip_lines or set()
        if language == "python":
            yield from self._detect_python_string_constants(file_name, source_code)
            return

        literals = list(self._iter_c_family_string_literals(source_code))
        counts = Counter(value for value, _line in literals if value)
        for value, line_no in literals:
            if line_no in skip_lines:
                continue
            if not value:
                continue
            is_long = len(value) >= self.LONG_STRING_MIN_LENGTH
            is_repeated = counts[value] > 1 and len(value) >= self.REPEATED_STRING_MIN_LENGTH
            is_sql = bool(self._SQL_HINT_RE.search(value))
            if not (is_long or is_repeated or is_sql):
                continue
            yield self._internal_action(
                ACTION_INTRODUCE_CONSTANT,
                file_name=file_name,
                reason="long or repeated string literal",
                parameters={
                    "literal_value": value,
                    "constant_name": "EXTRACTED_CONSTANT",
                    "source_line": line_no,
                },
            )

    def _detect_python_string_constants(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return
        string_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        counts = Counter(node.value for node in string_nodes if node.value)
        for node in string_nodes:
            value = node.value
            if not value:
                continue
            is_long = len(value) >= self.LONG_STRING_MIN_LENGTH
            is_repeated = counts[value] > 1 and len(value) >= self.REPEATED_STRING_MIN_LENGTH
            is_sql = bool(self._SQL_HINT_RE.search(value))
            if not (is_long or is_repeated or is_sql):
                continue
            yield self._internal_action(
                ACTION_INTRODUCE_CONSTANT,
                file_name=file_name,
                reason="long or repeated Python string literal",
                parameters={
                    "literal_value": value,
                    "constant_name": "EXTRACTED_CONSTANT",
                    "source_line": getattr(node, "lineno", None),
                },
            )

    def _detect_magic_numbers(
        self,
        language: str,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        if language == "python":
            yield from self._detect_python_magic_numbers(file_name, source_code)
            return

        masked = self._mask_c_family_comments_and_strings(source_code)
        for match in self._NUMBER_RE.finditer(masked):
            literal_text = match.group(0)
            value = self._number_value(literal_text)
            if value is None or value in {-1, 0, 1}:
                continue
            line_no = self._line_of(masked, match.start())
            line = source_code.splitlines()[line_no - 1] if line_no <= len(source_code.splitlines()) else ""
            stripped = line.strip()
            if stripped.startswith(("#", "import ", "package ")) or "static final" in stripped:
                continue
            yield self._internal_action(
                ACTION_INTRODUCE_CONSTANT,
                file_name=file_name,
                reason=f"magic number literal {literal_text}",
                parameters={
                    "literal_value": value,
                    "constant_name": "EXTRACTED_CONSTANT",
                    "source_line": line_no,
                },
            )

    def _detect_python_magic_numbers(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, (int, float)):
                continue
            value = node.value
            if value in {-1, 0, 1}:
                continue
            yield self._internal_action(
                ACTION_INTRODUCE_CONSTANT,
                file_name=file_name,
                reason=f"magic number literal {value}",
                parameters={
                    "literal_value": value,
                    "constant_name": "EXTRACTED_CONSTANT",
                    "source_line": getattr(node, "lineno", None),
                },
            )

    @staticmethod
    def _number_value(text: str) -> int | float | None:
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            return None

    @staticmethod
    def _find_matching_brace(source: str, start_idx: int) -> int | None:
        if start_idx < 0:
            return None
        depth = 0
        state = "code"
        index = start_idx
        while index < len(source):
            char = source[index]
            nxt = source[index + 1] if index + 1 < len(source) else ""
            if state == "code":
                if char == "/" and nxt == "/":
                    state = "line_comment"
                    index += 2
                    continue
                if char == "/" and nxt == "*":
                    state = "block_comment"
                    index += 2
                    continue
                if char == '"':
                    state = "string"
                elif char == "'":
                    state = "char"
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return index
            elif state == "line_comment":
                if char == "\n":
                    state = "code"
            elif state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "code"
                    index += 2
                    continue
            elif state in {"string", "char"}:
                quote = '"' if state == "string" else "'"
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    state = "code"
            index += 1
        return None

    @staticmethod
    def _mask_c_family_comments_and_strings(source_code: str) -> str:
        result: list[str] = []
        state = "code"
        index = 0
        while index < len(source_code):
            char = source_code[index]
            nxt = source_code[index + 1] if index + 1 < len(source_code) else ""
            if state == "code":
                if char == "/" and nxt == "/":
                    result.extend("  ")
                    state = "line_comment"
                    index += 2
                    continue
                if char == "/" and nxt == "*":
                    result.extend("  ")
                    state = "block_comment"
                    index += 2
                    continue
                if char == '"':
                    result.append(" ")
                    state = "string"
                    index += 1
                    continue
                if char == "'":
                    result.append(" ")
                    state = "char"
                    index += 1
                    continue
                result.append(char)
                index += 1
                continue
            if state == "line_comment":
                result.append("\n" if char == "\n" else " ")
                if char == "\n":
                    state = "code"
                index += 1
                continue
            if state == "block_comment":
                if char == "*" and nxt == "/":
                    result.extend("  ")
                    state = "code"
                    index += 2
                    continue
                result.append("\n" if char == "\n" else " ")
                index += 1
                continue
            quote = '"' if state == "string" else "'"
            if char == "\\":
                result.append(" ")
                if nxt:
                    result.append("\n" if nxt == "\n" else " ")
                index += 2
                continue
            result.append("\n" if char == "\n" else " ")
            if char == quote:
                state = "code"
            index += 1
        return "".join(result)

    def _iter_c_family_string_literals(
        self,
        source_code: str,
    ) -> Iterable[tuple[str, int]]:
        state = "code"
        index = 0
        start = 0
        value: list[str] = []
        escape = False
        while index < len(source_code):
            char = source_code[index]
            nxt = source_code[index + 1] if index + 1 < len(source_code) else ""
            if state == "code":
                if char == "/" and nxt == "/":
                    state = "line_comment"
                    index += 2
                    continue
                if char == "/" and nxt == "*":
                    state = "block_comment"
                    index += 2
                    continue
                if char == '"':
                    state = "string"
                    start = index
                    value = []
                    escape = False
                index += 1
                continue
            if state == "line_comment":
                if char == "\n":
                    state = "code"
                index += 1
                continue
            if state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "code"
                    index += 2
                    continue
                index += 1
                continue
            if escape:
                value.append(self._decode_c_string_escape(char))
                escape = False
                index += 1
                continue
            if char == "\\":
                escape = True
                index += 1
                continue
            if char == '"':
                yield "".join(value), self._line_of(source_code, start)
                state = "code"
                index += 1
                continue
            value.append(char)
            index += 1

    @staticmethod
    def _decode_c_string_escape(char: str) -> str:
        escapes = {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "0": "\0",
            "\\": "\\",
            '"': '"',
            "'": "'",
            "a": "\a",
            "b": "\b",
            "f": "\f",
            "v": "\v",
        }
        return escapes.get(char, f"\\{char}")
