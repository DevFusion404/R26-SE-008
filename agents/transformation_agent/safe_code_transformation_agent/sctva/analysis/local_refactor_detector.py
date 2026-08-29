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
    ACTION_ENCAPSULATE_C_VARIABLE,
    ACTION_INTRODUCE_CONSTANT,
    ACTION_INLINE_PYTHON_CLASS,
    ACTION_NORMALIZE_MULTILINE_STATEMENT,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
)
from ..contracts import RefactoringAction


class _PythonRiskyExceptionVisitor(ast.NodeVisitor):
    def __init__(self, known_containers: dict[str, str]) -> None:
        self.known_containers = known_containers
        self.exception_types: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            expression = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = self._python_exception_name(expression)
            if name and name not in {"Exception", "BaseException"}:
                self.exception_types.add(name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self._python_call_name(node.func)
        if call_name in {"int", "float", "complex"}:
            self.exception_types.add("ValueError")
        elif call_name == "open" or call_name.endswith(".open"):
            self.exception_types.add("OSError")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        container_name = node.value.id if isinstance(node.value, ast.Name) else ""
        container_type = self.known_containers.get(container_name)
        if container_type == "dict":
            self.exception_types.add("KeyError")
        elif container_type == "sequence":
            self.exception_types.add("IndexError")
        elif re.search(r"(dict|map|catalog|price|prices|student|students|lookup|table)$", container_name):
            self.exception_types.add("KeyError")
        elif re.search(r"(list|items|records|rows|values|array|sequence)$", container_name):
            self.exception_types.add("IndexError")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            self.exception_types.add("ZeroDivisionError")
        self.generic_visit(node)

    @classmethod
    def _python_exception_name(cls, expression: ast.AST | None) -> str:
        if isinstance(expression, ast.Name):
            return expression.id
        if isinstance(expression, ast.Attribute):
            return expression.attr
        return ""

    @classmethod
    def _python_call_name(cls, function: ast.AST) -> str:
        if isinstance(function, ast.Name):
            return function.id
        if isinstance(function, ast.Attribute):
            base = cls._python_call_name(function.value)
            return f"{base}.{function.attr}" if base else function.attr
        return ""


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
            for action in self._detect_c_global_variables(file_name, source_code):
                add(action)

        # Broad exception handlers are an exception-handling smell, not dead
        # code.  Bare ``except:`` handlers are sent through the same
        # evidence-based resolver as broad handlers.  It may leave an action
        # review-required, but it must never silently turn a bare handler into
        # a generic ``except Exception`` and call that a refactoring.
        for action in self._detect_exception_handler_smells(language, file_name, source_code):
            add(action)

        for action in self._detect_dead_code(language, file_name, source_code):
            add(action)

        for action in self._detect_long_methods(language, file_name, source_code):
            add(action)

        if language == "python":
            for action in self._detect_python_polymorphic_conditionals(file_name, source_code):
                add(action)

        for action in self._detect_string_constants(language, file_name, source_code, skip_lines=skip_string_lines):
            add(action)

        for action in self._detect_magic_numbers(language, file_name, source_code):
            add(action)

        # Lazy Class detection is Python-only and intentionally conservative.
        # Run it after line-targeted literal refactorings so those actions keep
        # the original source positions they were detected from.
        if language == "python":
            for action in self._detect_python_lazy_classes(file_name, source_code):
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

        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }

        def handler_context(handler: ast.ExceptHandler) -> tuple[str, str]:
            class_name = ""
            method_name = ""
            current: ast.AST | None = handler
            while current is not None:
                current = parents.get(current)
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method_name:
                    method_name = current.name
                if isinstance(current, ast.ClassDef) and not class_name:
                    class_name = current.name
            return class_name, method_name

        for try_node in (node for node in ast.walk(tree) if isinstance(node, ast.Try)):
            raised_types = self._python_risky_exception_types(tree, try_node)
            for handler in try_node.handlers:
                original_type = self._python_exception_name(handler.type)
                if handler.type is None:
                    source_class, source_method = handler_context(handler)
                    yield self._internal_action(
                        ACTION_NARROW_EXCEPTION_HANDLER,
                        file_name=file_name,
                        reason="bare Python except handler",
                        parameters={
                            "source_line": int(getattr(handler, "lineno", 0) or 0),
                            "source_class": source_class,
                            "class_name": source_class,
                            "source_method": source_method,
                            "method": source_method,
                            "original_exception_type": "",
                            "target_exception_type": "",
                            "handler_name": str(handler.name or ""),
                            "exception_smell": "bare_except",
                        },
                    )
                    continue

                # ``BaseException`` deliberately remains review-only: changing
                # it may alter KeyboardInterrupt/SystemExit handling.  A plain
                # Exception handler is narrowed only when the guarded body has
                # locally-provable exception types from syntax such as numeric
                # conversion, indexing, file I/O, division, or explicit raise.
                if original_type != "Exception" or not raised_types:
                    continue
                if raised_types == {"Exception"}:
                    continue
                yield self._internal_action(
                    ACTION_NARROW_EXCEPTION_HANDLER,
                    file_name=file_name,
                    reason="overly broad Python Exception handler with provable risky operations",
                    parameters={
                        "source_line": int(getattr(handler, "lineno", 0) or 0),
                        "original_exception_type": original_type,
                        "target_exception_type": ", ".join(sorted(raised_types)),
                        "handler_name": str(handler.name or ""),
                        "exception_smell": "exception_overreach",
                        "requires_try_split": len(try_node.body) > 1,
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
    def _python_risky_exception_types(cls, tree: ast.Module, try_node: ast.Try) -> set[str]:
        known_containers = cls._python_known_container_types_before(tree, try_node)
        exception_types: set[str] = set()
        for statement in try_node.body:
            exception_types.update(
                cls._python_statement_exception_types(statement, known_containers)
            )
            cls._update_python_known_container_types(statement, known_containers)
        return exception_types

    @classmethod
    def _python_known_container_types_before(
        cls,
        tree: ast.Module,
        target: ast.Try,
    ) -> dict[str, str]:
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        owner = parents.get(target)
        while owner is not None and not hasattr(owner, "body"):
            owner = parents.get(owner)
        known: dict[str, str] = {}
        for statement in getattr(owner, "body", []):
            if statement is target:
                break
            cls._update_python_known_container_types(statement, known)
        return known

    @staticmethod
    def _update_python_known_container_types(
        statement: ast.AST,
        known: dict[str, str],
    ) -> None:
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        if value is None:
            return
        container_type = ""
        if isinstance(value, ast.Dict):
            container_type = "dict"
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            container_type = "sequence"
        if not container_type:
            return
        for target in targets:
            if isinstance(target, ast.Name):
                known[target.id] = container_type

    @classmethod
    def _python_statement_exception_types(
        cls,
        statement: ast.AST,
        known_containers: dict[str, str],
    ) -> set[str]:
        visitor = _PythonRiskyExceptionVisitor(known_containers)
        visitor.visit(statement)
        return visitor.exception_types

    @classmethod
    def _explicit_python_raised_types(cls, try_node: ast.Try) -> set[str]:
        return cls._python_statement_exception_types(try_node, {})

    @classmethod
    def _python_call_name(cls, function: ast.AST) -> str:
        if isinstance(function, ast.Name):
            return function.id
        if isinstance(function, ast.Attribute):
            base = cls._python_call_name(function.value)
            return f"{base}.{function.attr}" if base else function.attr
        return ""

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
        if action.action_type == ACTION_NARROW_EXCEPTION_HANDLER:
            source_method = str(
                params.get("source_method")
                or params.get("method")
                or params.get("method_name")
                or ""
            ).strip()
            source_class = str(
                params.get("source_class")
                or params.get("class_name")
                or ""
            ).strip()
            if source_method:
                return (
                    action.action_type,
                    source_file,
                    source_class,
                    source_method,
                    str(params.get("handler_name") or ""),
                )
            return (
                action.action_type,
                source_file,
                source_line,
                str(params.get("handler_name") or ""),
            )
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
        if action.action_type == ACTION_INLINE_PYTHON_CLASS:
            return (
                action.action_type,
                source_file,
                params.get("class_to_inline") or params.get("source_class"),
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

    def _detect_c_global_variables(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        """Detect only clear mutable scalar C globals with local usage."""

        masked = self._mask_c_family_comments_and_strings(source_code)
        brace_depth = [0] * (len(masked) + 1)
        depth = 0
        for index, character in enumerate(masked):
            brace_depth[index] = depth
            if character == "{":
                depth += 1
            elif character == "}" and depth:
                depth -= 1
        brace_depth[len(masked)] = depth
        declaration_re = re.compile(
            r"(?m)^(?P<indent>[ \t]*)(?P<storage>static\s+)?"
            r"(?P<type>(?:(?:unsigned|signed|short|long)\s+)*(?:char|int|float|double|_Bool|size_t)"
            r"(?:\s*\*+\s*)?)\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<array>\[[^\]\n]*\])?"
            r"\s*(?:=\s*[^;\n{}]+)?\s*;"
        )
        for match in declaration_re.finditer(masked):
            if brace_depth[match.start()] != 0:
                continue
            variable_name = match.group("name")
            if match.group("array"):
                continue
            declaration_text = source_code[match.start():match.end()]
            if "const" in declaration_text.split() or "volatile" in declaration_text.split():
                continue
            if re.search(rf"\b{re.escape(variable_name)}\b", masked[:match.start()]):
                # The identifier already appeared in a preprocessor directive,
                # typedef, or earlier declaration; the file scope is unclear.
                continue
            references = list(re.finditer(rf"\b{re.escape(variable_name)}\b", masked))
            if len(references) < 3:  # declaration plus mutable shared usage
                continue
            yield self._internal_action(
                ACTION_ENCAPSULATE_C_VARIABLE,
                file_name=file_name,
                reason=f"mutable C global variable '{variable_name}' shared across functions",
                parameters={
                    "variable_name": variable_name,
                    "getter_name": f"get_{variable_name}",
                    "setter_name": f"set_{variable_name}",
                    "source_line": self._line_of(source_code, match.start()),
                    "smell": "Global Variable",
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

    def _detect_python_polymorphic_conditionals(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        """Detect safe terminal or assignment-producing dispatch chains."""

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                routines = [(node, "")]
            elif isinstance(node, ast.ClassDef):
                routines = [
                    (item, node.name)
                    for item in node.body
                    if isinstance(item, ast.FunctionDef)
                ]
            else:
                continue
            for routine, owner in routines:
                for statement in routine.body:
                    if not isinstance(statement, ast.If):
                        continue
                    branch_count = 1
                    current = statement
                    while len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                        branch_count += 1
                        current = current.orelse[0]
                    if branch_count < 2 or not current.orelse:
                        continue
                    bodies = []
                    current = statement
                    while True:
                        bodies.append(current.body)
                        if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                            current = current.orelse[0]
                            continue
                        bodies.append(current.orelse)
                        break
                    terminal = all(
                        body and isinstance(body[-1], (ast.Return, ast.Raise))
                        for body in bodies
                    )

                    def assigned_names(body: list[ast.stmt]) -> tuple[str, ...] | None:
                        names: list[str] = []
                        for item in body:
                            if not isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                                return None
                            targets = (
                                item.targets
                                if isinstance(item, ast.Assign)
                                else [item.target]
                            )
                            for target in targets:
                                if not isinstance(target, ast.Name):
                                    return None
                                if target.id not in names:
                                    names.append(target.id)
                        return tuple(names) if names else None

                    branch_outputs = [assigned_names(body) for body in bodies]
                    assignment_outputs = (
                        all(output is not None for output in branch_outputs)
                        and len(set(branch_outputs)) == 1
                    )
                    if not terminal and not assignment_outputs:
                        continue
                    yield self._internal_action(
                        ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
                        file_name=file_name,
                        reason=f"polymorphic Python conditional dispatch in '{routine.name}'",
                        parameters={
                            "method": routine.name,
                            "source_class": owner,
                            "source_line": int(statement.lineno),
                            "start_line": int(routine.lineno),
                            "end_line": int(routine.end_lineno),
                            "smell": "Switch Statements",
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
        # Module/class/function docstrings are metadata, not ordinary string
        # literals.  Turning them into constants changes ``__doc__`` and is not
        # a safe automatic refactoring.
        docstring_nodes: set[ast.Constant] = set()
        for owner in ast.walk(tree):
            if not isinstance(
                owner,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            body = getattr(owner, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_nodes.add(first.value)

        string_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node not in docstring_nodes
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

    def _detect_python_lazy_classes(
        self,
        file_name: str,
        source_code: str,
    ) -> Iterable[RefactoringAction]:
        """Detect only high-confidence owned Lazy Class candidates.

        SCTVA does not classify every small class as lazy.  The detector emits
        Inline Class only when a tiny helper is constructed exactly once as a
        ``self.<attribute>`` owned by another class and all visible use stays
        behind that owner attribute.  This is the pattern used by the Inline
        Class regression fixture (``Customer -> CustomerContact``).
        """

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return

        top_level_classes = [
            node for node in tree.body if isinstance(node, ast.ClassDef)
        ]
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }

        def class_method(owner: ast.ClassDef, name: str) -> ast.FunctionDef | None:
            matches = [
                node
                for node in owner.body
                if isinstance(node, ast.FunctionDef) and node.name == name
            ]
            return matches[0] if len(matches) == 1 else None

        def constructor_fields(
            constructor: ast.FunctionDef | None,
        ) -> tuple[set[str], bool]:
            if constructor is None:
                return set(), False
            args = constructor.args
            if (
                constructor.decorator_list
                or args.posonlyargs
                or args.vararg
                or args.kwarg
                or args.kwonlyargs
                or not args.args
                or args.args[0].arg != "self"
            ):
                return set(), False
            parameter_names = {argument.arg for argument in args.args[1:]}
            body = list(constructor.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            fields: set[str] = set()
            for statement in body:
                if not (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Attribute)
                    and isinstance(statement.targets[0].value, ast.Name)
                    and statement.targets[0].value.id == "self"
                ):
                    return set(), False
                # High-confidence detector: constructor state may come only
                # from its own arguments or literals/simple expressions.
                for node in ast.walk(statement.value):
                    if isinstance(
                        node,
                        (
                            ast.Call,
                            ast.Attribute,
                            ast.Subscript,
                            ast.Lambda,
                            ast.ListComp,
                            ast.SetComp,
                            ast.DictComp,
                            ast.GeneratorExp,
                            ast.Await,
                            ast.Yield,
                            ast.YieldFrom,
                        ),
                    ):
                        return set(), False
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                        if node.id not in parameter_names:
                            return set(), False
                fields.add(statement.targets[0].attr)
            return fields, bool(fields)

        def is_descendant(node: ast.AST, ancestor: ast.AST) -> bool:
            current: ast.AST | None = node
            while current is not None:
                if current is ancestor:
                    return True
                current = parents.get(current)
            return False

        for candidate in top_level_classes:
            # A Lazy Class is not inferred merely from low LOC.  Require the
            # complete strong pattern: tiny responsibility + unique owner.
            if candidate.bases or candidate.keywords or candidate.decorator_list:
                continue
            class_lines = int(candidate.end_lineno or candidate.lineno) - int(candidate.lineno) + 1
            if class_lines > 30:
                continue

            methods = [
                node for node in candidate.body if isinstance(node, ast.FunctionDef)
            ]
            constructor = next((node for node in methods if node.name == "__init__"), None)
            business_methods = [node for node in methods if node.name != "__init__"]
            if not (1 <= len(business_methods) <= 3):
                continue

            allowed_members = set(methods)
            member_shape_safe = True
            for member in candidate.body:
                if member in allowed_members:
                    continue
                if (
                    isinstance(member, ast.Expr)
                    and isinstance(member.value, ast.Constant)
                    and isinstance(member.value.value, str)
                ):
                    continue
                member_shape_safe = False
                break
            if not member_shape_safe:
                continue

            fields, constructor_safe = constructor_fields(constructor)
            if not constructor_safe or len(fields) > 3:
                continue

            method_names = {method.name for method in business_methods}
            unsafe_method = False
            for method in business_methods:
                args = method.args
                if (
                    method.decorator_list
                    or args.posonlyargs
                    or args.vararg
                    or args.kwarg
                    or args.kwonlyargs
                    or not args.args
                    or args.args[0].arg != "self"
                ):
                    unsafe_method = True
                    break
                for node in ast.walk(method):
                    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        if node.value.id == "self" and node.attr not in fields | method_names:
                            unsafe_method = True
                            break
                if unsafe_method:
                    break
            if unsafe_method:
                continue

            ownerships: list[tuple[ast.ClassDef, str, ast.AST, ast.Call]] = []
            for owner in top_level_classes:
                if owner is candidate:
                    continue
                owner_constructor = class_method(owner, "__init__")
                if owner_constructor is None:
                    continue
                for statement in ast.walk(owner_constructor):
                    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = getattr(statement, "value", None)
                    if not (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == candidate.name
                    ):
                        continue
                    target: ast.AST | None = None
                    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                        target = statement.targets[0]
                    elif isinstance(statement, ast.AnnAssign):
                        target = statement.target
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        ownerships.append((owner, target.attr, statement, value))

            if len(ownerships) != 1:
                continue
            owner, owner_attribute, construction_statement, construction_call = ownerships[0]

            # Reject a helper whose class name appears anywhere other than its
            # own definition and the single owned construction.
            external_class_reference = False
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Name)
                    and node.id == candidate.name
                    and isinstance(node.ctx, ast.Load)
                ):
                    continue
                if is_descendant(node, candidate):
                    continue
                parent = parents.get(node)
                if parent is construction_call and construction_call.func is node:
                    continue
                external_class_reference = True
                break
            if external_class_reference:
                continue

            # The owned helper may only be used as
            # ``<owner>.contact.<known field/method>``.  Passing or returning
            # ``contact`` itself would make Inline Class unsafe.
            allowed_members = fields | method_names
            owner_usage_safe = True
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr != owner_attribute:
                    continue
                if is_descendant(node, candidate) or is_descendant(node, construction_statement):
                    continue
                outer = parents.get(node)
                if not (
                    isinstance(outer, ast.Attribute)
                    and outer.value is node
                    and outer.attr in allowed_members
                ):
                    owner_usage_safe = False
                    break
            if not owner_usage_safe:
                continue

            yield self._internal_action(
                ACTION_INLINE_PYTHON_CLASS,
                file_name=file_name,
                reason=(
                    f"Lazy Class {candidate.name} has {len(fields)} small field(s), "
                    f"{len(business_methods)} small method(s), and is uniquely owned by "
                    f"{owner.name}.{owner_attribute}"
                ),
                parameters={
                    "class_to_inline": candidate.name,
                    "destination_class": owner.name,
                    "owner_attribute": owner_attribute,
                    "source_line": int(candidate.lineno),
                    "smell": "Lazy Class",
                    "detection_confidence": "high",
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
