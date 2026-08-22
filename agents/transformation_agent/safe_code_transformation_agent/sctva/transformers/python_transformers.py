"""CST-based Python transformers for safe refactoring actions.

This file uses LibCST, not normal Python AST.

Why CST is used here:
- It preserves comments, formatting, indentation, and source layout.
- It allows safer source-to-source transformation.
- It is better for refactoring tasks like Introduce Constant because the
  transformed code remains developer-readable.

Main fix in this version:
- Introduce Constant no longer uses the same generic EXTRACTED_CONSTANT name
  for every magic number.
- It generates stable constant names such as CONSTANT_NUMBER_6, CONSTANT_NUMBER_50,
    CONSTANT_NUMBER_0_8.
- It inserts the constant assignment into the module before the replaced value
  is used.
- It avoids replacing values inside existing constant assignments.
- It supports optional source_line targeting for JSON plans that contain:
  "source_line": 22
"""

from __future__ import annotations

import ast
import re
from typing import Any, Optional, Sequence, Tuple

import libcst as cst
from libcst.metadata import MetadataWrapper, ParentNodeProvider, PositionProvider

from .python_extract_class import apply_extract_class as _apply_extract_class


def _parse_module(source_code: str) -> cst.Module:
    return cst.parse_module(source_code)


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())

    if not cleaned:
        cleaned = "VALUE"

    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"

    return cleaned.upper()


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
        return f"CONSTANT_NUMBER_{_sanitize_identifier(text)}"

    if isinstance(value, str):
        short = value[:24]
        return f"CONSTANT_STRING_{_sanitize_identifier(short)}"

    return "CONSTANT_VALUE"


def _normalize_legacy_magic_name(cleaned: str, literal_value: Any) -> str:
    if not cleaned.startswith("MAGIC_"):
        return cleaned
    if cleaned.startswith(("MAGIC_NUMBER_", "MAGIC_STRING_", "MAGIC_BOOL_")) or cleaned in {
        "MAGIC_NONE",
        "MAGIC_VALUE",
    }:
        return _constant_name_from_value(literal_value)
    return f"CONSTANT_{cleaned[len('MAGIC_'):]}"


def _normalize_constant_name(
    constant_name: Optional[str],
    literal_value: Any,
) -> str:
    """Create a safe constant name.

    If the caller sends EXTRACTED_CONSTANT for every magic number, this function
    replaces it with a meaningful value-based name. This prevents invalid code
    such as many unrelated values all becoming EXTRACTED_CONSTANT.
    """

    if not constant_name:
        return _constant_name_from_value(literal_value)

    cleaned = _sanitize_identifier(str(constant_name))

    generic_names = {
        "EXTRACTED_CONSTANT",
        "MAGIC_CONSTANT",
        "CONSTANT",
        "VALUE_CONSTANT",
    }

    if cleaned in generic_names:
        return _constant_name_from_value(literal_value)

    cleaned = _normalize_legacy_magic_name(cleaned, literal_value)

    return cleaned


def _has_module_constant(module: cst.Module, constant_name: str) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue

        for element in stmt.body:
            if not isinstance(element, cst.Assign):
                continue

            for target in element.targets:
                if (
                    isinstance(target.target, cst.Name)
                    and target.target.value == constant_name
                ):
                    return True

    return False


def _module_constant_names(module: cst.Module) -> set[str]:
    names: set[str] = set()

    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue

        for element in stmt.body:
            if not isinstance(element, cst.Assign):
                continue

            for target in element.targets:
                if isinstance(target.target, cst.Name):
                    names.add(target.target.value)

    return names


def _unique_constant_name(module: cst.Module, preferred_name: str) -> str:
    existing = _module_constant_names(module)

    if preferred_name not in existing:
        return preferred_name

    index = 2

    while f"{preferred_name}_{index}" in existing:
        index += 1

    return f"{preferred_name}_{index}"


class _NameUsageDetector(cst.CSTVisitor):
    def __init__(self, target_name: str) -> None:
        self.target_name = target_name
        self.found = False

    def visit_Name(self, node: cst.Name) -> Optional[bool]:
        if node.value == self.target_name:
            self.found = True
            return False

        return True


def _module_uses_name(module: cst.Module, constant_name: str) -> bool:
    detector = _NameUsageDetector(constant_name)
    module.visit(detector)
    return detector.found


def _literal_to_node(value: Any) -> cst.BaseExpression:
    if isinstance(value, bool):
        return cst.Name("True" if value else "False")

    if value is None:
        return cst.Name("None")

    if isinstance(value, int):
        if value < 0:
            return cst.UnaryOperation(
                operator=cst.Minus(),
                expression=cst.Integer(str(abs(value))),
            )

        return cst.Integer(str(value))

    if isinstance(value, float):
        if value < 0:
            return cst.UnaryOperation(
                operator=cst.Minus(),
                expression=cst.Float(str(abs(value))),
            )

        return cst.Float(str(value))

    if isinstance(value, str):
        return cst.SimpleString(repr(value))

    return cst.SimpleString(repr(str(value)))


def _node_to_literal(node: cst.CSTNode) -> tuple[bool, Any]:
    if isinstance(node, cst.Integer):
        try:
            return True, int(node.value.replace("_", ""))
        except ValueError:
            return False, None

    if isinstance(node, cst.Float):
        try:
            return True, float(node.value.replace("_", ""))
        except ValueError:
            return False, None

    if isinstance(node, cst.SimpleString):
        try:
            return True, ast.literal_eval(node.value)
        except Exception:
            return False, None

    if isinstance(node, cst.Name):
        if node.value == "True":
            return True, True

        if node.value == "False":
            return True, False

        if node.value == "None":
            return True, None

    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Minus):
        ok, value = _node_to_literal(node.expression)

        if ok and isinstance(value, (int, float)):
            return True, -value

    return False, None


def _insert_module_constant(
    module: cst.Module,
    constant_name: str,
    literal_value: Any,
) -> cst.Module:
    if _has_module_constant(module, constant_name):
        return module

    assignment = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[
                    cst.AssignTarget(
                        target=cst.Name(constant_name),
                    )
                ],
                value=_literal_to_node(literal_value),
            )
        ]
    )

    body = list(module.body)
    insert_at = 0

    # Keep module docstring at the top.
    if body:
        first = body[0]

        if isinstance(first, cst.SimpleStatementLine):
            stmt = first.body[0] if first.body else None

            if isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.SimpleString):
                insert_at = 1

    # Keep __future__ imports immediately after docstring.
    while insert_at < len(body):
        stmt = body[insert_at]

        if not isinstance(stmt, cst.SimpleStatementLine):
            break

        line = stmt.body[0] if stmt.body else None

        if (
            isinstance(line, cst.ImportFrom)
            and isinstance(line.module, cst.Name)
            and line.module.value == "__future__"
        ):
            insert_at += 1
            continue

        break

    # Keep normal imports before constants where possible.
    while insert_at < len(body):
        stmt = body[insert_at]

        if not isinstance(stmt, cst.SimpleStatementLine):
            break

        line = stmt.body[0] if stmt.body else None

        if isinstance(line, (cst.Import, cst.ImportFrom)):
            insert_at += 1
            continue

        break

    body.insert(insert_at, assignment)

    return module.with_changes(body=body)


def _inject_module_constant(
    source_code: str,
    constant_name: str,
    literal_value: Any,
) -> str:
    module = _parse_module(source_code)

    if _has_module_constant(module, constant_name):
        return source_code

    return _insert_module_constant(module, constant_name, literal_value).code


class _RenameSymbolTransformer(cst.CSTTransformer):
    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self.replacements = 0

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.CSTNode:
        if original_node.value == self.old_name:
            self.replacements += 1
            return updated_node.with_changes(value=self.new_name)

        return updated_node

    def leave_Param(
        self,
        original_node: cst.Param,
        updated_node: cst.Param,
    ) -> cst.CSTNode:
        if original_node.name.value == self.old_name:
            self.replacements += 1
            return updated_node.with_changes(name=cst.Name(self.new_name))

        return updated_node

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.CSTNode:
        if original_node.name.value == self.old_name:
            self.replacements += 1
            return updated_node.with_changes(name=cst.Name(self.new_name))

        return updated_node

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.CSTNode:
        if original_node.name.value == self.old_name:
            self.replacements += 1
            return updated_node.with_changes(name=cst.Name(self.new_name))

        return updated_node


class _ReplaceLiteralTransformer(cst.CSTTransformer):
    def __init__(self, old_literal: Any, new_literal: Any) -> None:
        self.old_literal = old_literal
        self.new_literal = new_literal
        self.replacements = 0

    def _maybe_replace(self, node: cst.CSTNode) -> cst.CSTNode:
        ok, value = _node_to_literal(node)

        if ok and value == self.old_literal:
            self.replacements += 1
            return _literal_to_node(self.new_literal)

        return node

    def leave_Integer(
        self,
        original_node: cst.Integer,
        updated_node: cst.Integer,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_Float(
        self,
        original_node: cst.Float,
        updated_node: cst.Float,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_SimpleString(
        self,
        original_node: cst.SimpleString,
        updated_node: cst.SimpleString,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_UnaryOperation(
        self,
        original_node: cst.UnaryOperation,
        updated_node: cst.UnaryOperation,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)


class _ExtractConstantTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (
        PositionProvider,
        ParentNodeProvider,
    )

    def __init__(
        self,
        literal_value: Any,
        constant_name: str,
        source_line: Optional[int] = None,
        docstring_lines: Optional[set[int]] = None,
    ) -> None:
        self.literal_value = literal_value
        self.constant_name = constant_name
        self.source_line = source_line
        self.docstring_lines = docstring_lines or set()
        self.replacements = 0

    def _is_target_line(self, node: cst.CSTNode) -> bool:
        if self.source_line is None:
            return True

        try:
            position = self.get_metadata(PositionProvider, node)
            return position.start.line == self.source_line
        except Exception:
            return True

    def _is_inside_constant_assignment(self, node: cst.CSTNode) -> bool:
        """Avoid replacing values inside existing constant declarations.

        Without this guard, code like:

            CONSTANT_NUMBER_50 = 50

        could be transformed into:

            CONSTANT_NUMBER_50 = CONSTANT_NUMBER_50

        which causes a NameError during module execution.
        """

        current: cst.CSTNode = node

        while True:
            try:
                parent = self.get_metadata(ParentNodeProvider, current)
            except Exception:
                return False

            if isinstance(parent, cst.Module):
                return False

            if isinstance(parent, cst.Assign):
                for target in parent.targets:
                    if isinstance(target.target, cst.Name):
                        target_name = target.target.value

                        if target_name == self.constant_name:
                            return True

                        if target_name.isupper():
                            return True

                return False

            current = parent

    def _maybe_replace(self, node: cst.CSTNode) -> cst.CSTNode:
        # A docstring is executable metadata, not a refactoring literal.  The
        # AST gives us the first statement line for module, class, and function
        # docstrings, while CST keeps the original spelling and formatting.
        try:
            if self.get_metadata(PositionProvider, node).start.line in self.docstring_lines:
                return node
        except Exception:
            pass

        if self._is_inside_constant_assignment(node):
            return node

        if not self._is_target_line(node):
            return node

        ok, value = _node_to_literal(node)

        if ok and value == self.literal_value:
            self.replacements += 1
            return cst.Name(self.constant_name)

        return node

    def leave_Integer(
        self,
        original_node: cst.Integer,
        updated_node: cst.Integer,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_Float(
        self,
        original_node: cst.Float,
        updated_node: cst.Float,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_SimpleString(
        self,
        original_node: cst.SimpleString,
        updated_node: cst.SimpleString,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_UnaryOperation(
        self,
        original_node: cst.UnaryOperation,
        updated_node: cst.UnaryOperation,
    ) -> cst.CSTNode:
        return self._maybe_replace(original_node)

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        if self.replacements == 0:
            return updated_node

        return _insert_module_constant(
            updated_node,
            self.constant_name,
            self.literal_value,
        )


class _RemoveDeadCodeTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(
        self,
        method_name: str,
        class_name: Optional[str],
        target_line: int,
    ) -> None:
        self.method_name = method_name
        self.class_name = class_name
        self.target_line = target_line
        self.replacements = 0
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> Optional[bool]:
        self._class_stack.append(node.name.value)
        return True

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.CSTNode:
        if self._class_stack:
            self._class_stack.pop()

        return updated_node

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.CSTNode:
        current_class = self._class_stack[-1] if self._class_stack else None

        if self.class_name and current_class != self.class_name:
            return updated_node

        position = self.get_metadata(PositionProvider, original_node)
        if (
            original_node.name.value == self.method_name
            and position.start.line == self.target_line
        ):
            self.replacements += 1
            return cst.RemoveFromParent()

        return updated_node


def _apply_transformer(
    source_code: str,
    transformer: cst.CSTTransformer,
) -> Tuple[str, int]:
    module = _parse_module(source_code)

    if getattr(transformer, "METADATA_DEPENDENCIES", None):
        wrapper = MetadataWrapper(module)
        updated = wrapper.visit(transformer)
    else:
        updated = module.visit(transformer)

    return updated.code, getattr(transformer, "replacements", 0)


def apply_rename_symbol(
    source_code: str,
    old_name: str,
    new_name: str,
) -> Tuple[str, int]:
    return _apply_transformer(
        source_code,
        _RenameSymbolTransformer(old_name, new_name),
    )


def apply_replace_literal(
    source_code: str,
    old_literal: Any,
    new_literal: Any,
) -> Tuple[str, int]:
    return _apply_transformer(
        source_code,
        _ReplaceLiteralTransformer(old_literal, new_literal),
    )


def apply_extract_constant(
    source_code: str,
    literal_value: Any,
    constant_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    """Replace a magic literal with a named constant and inject the constant.

    Example:
        Original:
            plt.figure(figsize=(12, 6))

        Transformed:
            CONSTANT_NUMBER_6 = 6
            plt.figure(figsize=(12, CONSTANT_NUMBER_6))

    Parameters:
        source_code:
            Python source code.
        literal_value:
            The actual magic value, for example 6 or 0.8.
        constant_name:
            Optional name. If it is missing or generic like EXTRACTED_CONSTANT,
            this function creates a value-based name.
        source_line:
            Optional exact line from the refactoring plan. If provided, only the
            matching literal on that source line is replaced.
    """

    module = _parse_module(source_code)

    normalized_constant_name = _normalize_constant_name(
        constant_name,
        literal_value,
    )

    # If the constant already exists with this exact name, reuse it.
    # If the caller sent a custom name and it already exists, create a unique one.
    if _has_module_constant(module, normalized_constant_name):
        preferred_name = normalized_constant_name
    else:
        preferred_name = _unique_constant_name(module, normalized_constant_name)

    docstring_lines = _python_docstring_lines(source_code)
    updated_code, replacements = _apply_transformer(
        source_code,
        _ExtractConstantTransformer(
            literal_value=literal_value,
            constant_name=preferred_name,
            source_line=source_line,
            docstring_lines=docstring_lines,
        ),
    )
    if replacements == 0 and source_line is not None:
        updated_code, replacements = _apply_transformer(
            source_code,
            _ExtractConstantTransformer(
                literal_value=literal_value,
                constant_name=preferred_name,
                source_line=None,
                docstring_lines=docstring_lines,
            ),
        )

    # Safety guard:
    # If replacements happened but the constant was somehow not inserted by the
    # CST transformer, inject it here.
    updated_module = _parse_module(updated_code)

    if replacements > 0 and not _has_module_constant(updated_module, preferred_name):
        updated_code = _inject_module_constant(
            updated_code,
            preferred_name,
            literal_value,
        )

    # Do not inject unused constants when no replacement was made.
    return updated_code, replacements


def _python_docstring_lines(source_code: str) -> set[int]:
    """Return lines occupied by module, class, and function docstrings."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()

    lines: set[int] = set()
    scopes: list[ast.AST] = [tree]
    scopes.extend(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for scope in scopes:
        body = getattr(scope, "body", [])
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if not isinstance(first.value.value, str):
            continue
        start = int(getattr(first, "lineno", 0) or 0)
        end = int(getattr(first, "end_lineno", start) or start)
        lines.update(range(start, end + 1))
    return lines


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
    *,
    dead_code_kind: str = "",
    target_statement_fingerprint: str = "",
) -> Tuple[str, int]:
    if not method_name and source_line is None:
        raise ValueError("remove_dead_code requires 'method_name' or 'source_line'.")

    if not method_name and source_line is not None:
        return _remove_proven_dead_python_statement(
            source_code,
            source_line,
            class_name=class_name,
            dead_code_kind=dead_code_kind,
            target_statement_fingerprint=target_statement_fingerprint,
        )

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0

    target = _find_python_dead_callable(
        tree,
        method_name=method_name,
        class_name=class_name,
        source_line=source_line,
    )
    if target is None or not _is_proven_unused_python_callable(
        tree,
        target=target,
        method_name=method_name,
    ):
        return source_code, 0

    return _apply_transformer(
        source_code,
        _RemoveDeadCodeTransformer(
            method_name,
            class_name,
            target_line=target.lineno,
        ),
    )


def apply_narrow_exception_handler(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    original_exception_type: str = "",
    target_exception_type: str = "",
    handler_name: str = "",
) -> Tuple[str, int]:
    """Narrow one Python ``except`` header without touching its body.

    The caller must supply a concrete target type.  This transformer never
    guesses from arbitrary function calls, and it refuses ambiguous handlers.
    """

    if not _valid_python_exception_type(target_exception_type):
        return source_code, 0
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0

    candidates: list[ast.ExceptHandler] = []
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        handler_type = _python_exception_expression_name(handler.type)
        if original_exception_type:
            if handler_type != original_exception_type:
                continue
        elif handler.type is not None:
            continue
        if handler_name and str(handler.name or "") != handler_name:
            continue
        candidates.append(handler)

    if source_line is not None:
        line_matches = [
            handler for handler in candidates
            if int(getattr(handler, "lineno", 0) or 0) == source_line
        ]
        if line_matches:
            candidates = line_matches
    if len(candidates) != 1:
        return source_code, 0

    handler = candidates[0]
    lines = source_code.splitlines(keepends=True)
    header_line = int(getattr(handler, "lineno", 0) or 0)
    if header_line <= 0 or header_line > len(lines):
        return source_code, 0

    original_line = lines[header_line - 1]
    content = original_line.rstrip("\r\n")
    newline = original_line[len(content):]
    match = re.match(
        r"^(?P<indent>[ \t]*)except(?:\s+(?P<type>[^:#]+?))?"
        r"(?P<alias>\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?"
        r"(?P<suffix>\s*:\s*(?:#.*)?)$",
        content,
    )
    if not match:
        return source_code, 0

    alias = match.group("alias") or ""
    replacement = (
        f"{match.group('indent')}except {target_exception_type}{alias}"
        f"{match.group('suffix')}{newline}"
    )
    if replacement == original_line:
        return source_code, 0
    lines[header_line - 1] = replacement
    transformed = "".join(lines)
    try:
        ast.parse(transformed)
    except SyntaxError:
        return source_code, 0
    return transformed, 1


def _python_exception_expression_name(expression: ast.AST | None) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return expression.attr
    return ""


def _valid_python_exception_type(value: str) -> bool:
    names = [part.strip() for part in value.strip().strip("()").split(",") if part.strip()]
    if not names:
        return False
    return all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) for name in names)


def resolve_dead_code_target(
    source_code: str,
    *,
    method_name: str = "",
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> tuple[str, str]:
    """Capture a stable AST anchor for a plan-specified dead-code target.

    RDP plans normally identify a source line. Earlier transformations can add
    lines before the Remove Dead Code action runs, so the engine records this
    semantic anchor from the original code and the remover later relocates it.
    """

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return "", ""

    if method_name:
        target = _find_python_dead_callable(
            tree,
            method_name=method_name,
            class_name=class_name,
            source_line=source_line,
        )
        if target is not None:
            return "unused_callable", ast.dump(target, include_attributes=False)
        return "", ""

    if source_line is None:
        return "", ""

    callable_candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and int(getattr(node, "lineno", 0) or 0) == source_line
    ]
    if callable_candidates:
        target = callable_candidates[0]
        return "unused_callable", ast.dump(target, include_attributes=False)

    false_branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and not node.orelse
        and _static_python_boolean(node.test) is False
        and _python_statement_span(node)[0] <= source_line <= _python_statement_span(node)[1]
    ]
    if false_branches:
        target = min(
            false_branches,
            key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0],
        )
        return "constant_false_branch", ast.dump(target, include_attributes=False)

    for statement in _unreachable_python_statements(tree):
        start_line, end_line = _python_statement_span(statement)
        if start_line <= source_line <= end_line:
            return "unreachable_after_terminator", ast.dump(
                statement,
                include_attributes=False,
            )

    for statement in ast.walk(tree):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        start_line, end_line = _python_statement_span(statement)
        if start_line <= source_line <= end_line:
            return "unused_literal_assignment", ast.dump(
                statement,
                include_attributes=False,
            )
    return "", ""


def _find_python_dead_callable(
    tree: ast.Module,
    *,
    method_name: str,
    class_name: Optional[str],
    source_line: Optional[int] = None,
) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method_name and node.name != method_name:
            continue
        if source_line is not None:
            start, end = _python_statement_span(node)
            if not start <= source_line <= end:
                continue
        if class_name:
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, ast.ClassDef):
                owner = parents.get(owner)
            if not isinstance(owner, ast.ClassDef) or owner.name != class_name:
                continue
        candidates.append(node)

    if source_line is not None:
        candidates.sort(key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0])
        return candidates[0] if candidates else None
    return candidates[0] if len(candidates) == 1 else None


def _is_proven_unused_python_callable(
    tree: ast.Module,
    *,
    target: ast.FunctionDef | ast.AsyncFunctionDef,
    method_name: str,
) -> bool:
    """Reject dynamic/publicly ambiguous callables before CST removal."""

    if target.decorator_list or (
        method_name.startswith("__") and method_name.endswith("__")
    ):
        return False

    dynamic_lookup_names = {
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "eval",
        "exec",
        "__import__",
        "import_module",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in dynamic_lookup_names:
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == method_name:
            return False

    for node in ast.walk(tree):
        if node is target:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == method_name:
            return False
        if isinstance(node, ast.Attribute) and node.attr == method_name:
            return False
    return True


def _iter_python_statement_suites(node: ast.AST):
    for _field_name, value in ast.iter_fields(node):
        if isinstance(value, list):
            statements = [item for item in value if isinstance(item, ast.stmt)]
            if statements and len(statements) == len(value):
                yield value
            for item in value:
                if isinstance(item, ast.AST):
                    yield from _iter_python_statement_suites(item)
        elif isinstance(value, ast.AST):
            yield from _iter_python_statement_suites(value)


def _python_statement_span(statement: ast.stmt) -> tuple[int, int]:
    start = int(getattr(statement, "lineno", 0) or 0)
    end = int(getattr(statement, "end_lineno", start) or start)
    return start, end


def _is_side_effect_free_literal(expression: Optional[ast.AST]) -> bool:
    if expression is None:
        return False
    try:
        ast.literal_eval(expression)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _static_python_boolean(expression: ast.AST) -> Optional[bool]:
    """Evaluate only literal-only conditions; never execute project expressions."""

    if isinstance(expression, ast.Constant):
        return bool(expression.value)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        value = _static_python_boolean(expression.operand)
        return None if value is None else not value
    if isinstance(expression, ast.BoolOp):
        values = [_static_python_boolean(item) for item in expression.values]
        if any(item is None for item in values):
            return None
        return all(values) if isinstance(expression.op, ast.And) else any(values)
    if isinstance(expression, ast.Compare) and len(expression.ops) == 1 and len(expression.comparators) == 1:
        try:
            left = ast.literal_eval(expression.left)
            right = ast.literal_eval(expression.comparators[0])
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return None
        operator = expression.ops[0]
        try:
            if isinstance(operator, ast.Eq):
                return left == right
            if isinstance(operator, ast.NotEq):
                return left != right
            if isinstance(operator, ast.Lt):
                return left < right
            if isinstance(operator, ast.LtE):
                return left <= right
            if isinstance(operator, ast.Gt):
                return left > right
            if isinstance(operator, ast.GtE):
                return left >= right
        except (TypeError, ValueError):
            return None
    return None


def _assigned_local_names(statement: ast.stmt) -> list[str]:
    if isinstance(statement, ast.Assign):
        targets = statement.targets
        value = statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets = [statement.target]
        value = statement.value
    else:
        return []

    if not _is_side_effect_free_literal(value):
        return []

    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        else:
            return []
    return names


def _enclosing_python_function(
    statement: ast.stmt,
    parents: dict[ast.AST, ast.AST],
) -> Optional[ast.AST]:
    current: Optional[ast.AST] = statement
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current
        current = parents.get(current)
    return None


def _remove_python_line_span(source_code: str, start_line: int, end_line: int) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    if start_line <= 0 or end_line < start_line or end_line > len(lines):
        return source_code, 0
    candidate = "".join(lines[: start_line - 1] + lines[end_line:])
    try:
        ast.parse(candidate)
    except SyntaxError:
        return source_code, 0
    return candidate, 1


def _unreachable_python_statements(tree: ast.Module) -> list[ast.stmt]:
    statements: list[ast.stmt] = []
    terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)
    for suite in _iter_python_statement_suites(tree):
        terminated = False
        for statement in suite:
            if terminated:
                statements.append(statement)
            if isinstance(statement, terminators):
                terminated = True
    return statements


def _remove_proven_dead_python_statement(
    source_code: str,
    source_line: int,
    *,
    class_name: Optional[str] = None,
    dead_code_kind: str = "",
    target_statement_fingerprint: str = "",
) -> Tuple[str, int]:
    """Remove only AST-proven unreachable or unused local statements."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    # A stable AST anchor is authoritative. Once earlier actions have shifted
    # lines, falling back to the old line could target different live code.
    if target_statement_fingerprint:
        return _remove_anchored_python_dead_target(
            source_code,
            tree=tree,
            parents=parents,
            dead_code_kind=dead_code_kind,
            target_statement_fingerprint=target_statement_fingerprint,
        )

    # A plan line can point at a function body rather than the ``def`` line.
    # Resolve the smallest enclosing callable first, then apply the same
    # reference/dynamic-usage proof used by method-name targets.
    callable_candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        # A line inside a function is normally a statement target.  Treat a
        # line target as the callable itself only when it identifies the
        # definition header; this prevents an ``if False`` or ``assert`` inside
        # a live function from deleting the enclosing function.
        and int(getattr(node, "lineno", 0) or 0) == source_line
    ]
    if class_name:
        callable_candidates = [
            node
            for node in callable_candidates
            if _python_callable_class_name(node, parents) == class_name
        ]
    callable_candidates.sort(
        key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0]
    )
    if callable_candidates:
        # The smallest enclosing callable is the AST/CST target for a line
        # inside a nested function.  Reference analysis remains the gate that
        # decides whether this candidate may actually be removed.
        target_callable = callable_candidates[0]
        if _is_proven_unused_python_callable(
            tree,
            target=target_callable,
            method_name=target_callable.name,
        ):
            return _apply_transformer(
                source_code,
                _RemoveDeadCodeTransformer(
                    target_callable.name,
                    class_name,
                    target_line=int(getattr(target_callable, "lineno", source_line)),
                ),
            )

    # Remove a complete constant-false branch only when it has no ``else``
    # suite. Replacing an ``if`` with an ``else`` body needs a richer CST move
    # and is intentionally left for review rather than risking layout changes.
    false_branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and not node.orelse
        and _static_python_boolean(node.test) is False
        and _python_statement_span(node)[0] <= source_line <= _python_statement_span(node)[1]
    ]
    false_branches.sort(key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0])
    if false_branches:
        start_line, end_line = _python_statement_span(false_branches[0])
        return _remove_python_line_span(source_code, start_line, end_line)
    # Statements after an unconditional terminator in the same suite are
    # unreachable. Internal actions carry an AST anchor because earlier plan
    # actions may have shifted line numbers before this action is applied.
    unreachable = _unreachable_python_statements(tree)
    for statement in unreachable:
        start_line, end_line = _python_statement_span(statement)
        if start_line <= source_line <= end_line:
            return _remove_python_line_span(source_code, start_line, end_line)
    # A local assignment is removable only when its value is a literal (so
    # evaluating it has no side effects) and the assigned name is never read.
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and _python_statement_span(node)[0] <= source_line <= _python_statement_span(node)[1]
    ]
    candidates.sort(key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0])
    for statement in candidates:
        names = _assigned_local_names(statement)
        function = _enclosing_python_function(statement, parents)
        if not names or function is None:
            continue
        loaded_names = {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        if any(name in loaded_names for name in names):
            continue
        start_line, end_line = _python_statement_span(statement)
        return _remove_python_line_span(source_code, start_line, end_line)

    return source_code, 0


def _remove_anchored_python_dead_target(
    source_code: str,
    *,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    dead_code_kind: str,
    target_statement_fingerprint: str,
) -> Tuple[str, int]:
    """Relocate and remove exactly one previously resolved dead-code target."""

    if dead_code_kind == "unused_callable":
        candidates = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
        if len(candidates) == 1:
            target = candidates[0]
            if _is_proven_unused_python_callable(tree, target=target, method_name=target.name):
                return _apply_transformer(
                    source_code,
                    _RemoveDeadCodeTransformer(
                        target.name,
                        _python_callable_class_name(target, parents),
                        target_line=int(getattr(target, "lineno", 0) or 0),
                    ),
                )
        return source_code, 0

    if dead_code_kind == "constant_false_branch":
        candidates = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and not node.orelse
            and _static_python_boolean(node.test) is False
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    elif dead_code_kind == "unreachable_after_terminator":
        candidates = [
            node
            for node in _unreachable_python_statements(tree)
            if ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    elif dead_code_kind == "unused_literal_assignment":
        candidates = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.stmt)
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    else:
        return source_code, 0

    if len(candidates) != 1:
        return source_code, 0

    target = candidates[0]
    if dead_code_kind == "unused_literal_assignment":
        names = _assigned_local_names(target)
        function = _enclosing_python_function(target, parents)
        if not names or function is None:
            return source_code, 0
        loaded_names = {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        if any(name in loaded_names for name in names):
            return source_code, 0

    start_line, end_line = _python_statement_span(target)
    return _remove_python_line_span(source_code, start_line, end_line)


def _python_callable_class_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
) -> Optional[str]:
    owner = parents.get(node)
    while owner is not None:
        if isinstance(owner, ast.ClassDef):
            return owner.name
        owner = parents.get(owner)
    return None


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def apply_extract_method(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    """Backward-compatible entry point for the semantic sibling extractor."""

    from .python_extract_method import apply_extract_method as apply_semantic_extract_method

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= start_line <= getattr(node, "end_lineno", node.lineno)
    ]
    candidates.sort(key=lambda node: getattr(node, "end_lineno", node.lineno) - node.lineno)
    if not candidates:
        return source_code, 0
    transformed, replacements, _metadata = apply_semantic_extract_method(
        source_code,
        new_method_name=new_method_name,
        method_name=candidates[0].name,
        start_line=start_line,
        end_line=end_line,
    )
    return transformed, replacements


class _ReturnTransformer(ast.NodeTransformer):
    def __init__(self, outputs):
        self.outputs = outputs
    def visit_FunctionDef(self, node):
        return node
    def visit_AsyncFunctionDef(self, node):
        return node
    def visit_Return(self, node):
        elts = []
        for o in self.outputs:
            elts.append(ast.Name(id=o, ctx=ast.Load()))
        elts.append(ast.Constant(value=True))
        elts.append(node.value if node.value else ast.Constant(value=None))
        return ast.Return(value=ast.Tuple(elts=elts, ctx=ast.Load()))


def _extract_full_python_function(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    """Decompose a long Python function into cohesive top-level sub-functions.

    Instead of wrapping the entire function body in a single nested wrapper (which
    doesn't reduce complexity), this splits the function body into logical blocks
    (using blank lines as starting boundaries), calculates their inputs/outputs via
    AST analysis, and replaces each block with a call to a newly extracted helper.
    """
    lines = source_code.splitlines(keepends=True)
    selected = lines[start_line - 1 : end_line]
    if len(selected) < 2:
        return source_code, 0

    header = selected[0]
    body = selected[1:]
    meaningful_body = [line for line in body if line.strip()]
    if not meaningful_body:
        return source_code, 0

    function_indent = _line_indent(header)
    body_indent = min((_line_indent(line) for line in meaningful_body), key=len)
    
    # 1. Group body lines into cohesive syntactically valid blocks
    raw_blocks: List[List[str]] = []
    current_block: List[str] = []
    
    for line in body:
        # We start a new block on empty lines or lines with comments at base body indentation
        stripped = line.strip()
        if not stripped:
            if current_block:
                raw_blocks.append(current_block)
                current_block = []
            continue
        current_block.append(line)
        
    if current_block:
        raw_blocks.append(current_block)

    # Merge blocks that are not syntactically valid on their own (e.g. try without except, if without body)
    valid_blocks: List[str] = []
    temp_lines: List[str] = []
    
    for block in raw_blocks:
        temp_lines.extend(block)
        # Check syntax by dedenting the lines and trying to parse
        block_code = "".join(temp_lines)
        # Dedent helper
        lines_to_dedent = block_code.splitlines()
        min_ind = min((_line_indent(l) for l in lines_to_dedent if l.strip()), default="")
        dedented = "\n".join(l[len(min_ind):] if l.startswith(min_ind) else l.lstrip() for l in lines_to_dedent)
        
        try:
            ast.parse(dedented)
            # Valid block found!
            valid_blocks.append(block_code)
            temp_lines = []
        except SyntaxError:
            # Not valid yet, keep appending next block
            continue
            
    if temp_lines:
        # Append any remaining lines to the last block or as a new block
        block_code = "".join(temp_lines)
        if valid_blocks:
            valid_blocks[-1] += "\n" + block_code
        else:
            valid_blocks.append(block_code)

    # If we only have 1 block, fallback to single extraction or return unchanged
    if len(valid_blocks) <= 1:
        # Fallback: Extract the body as a single nested helper to preserve behavior
        has_return = any(line.strip().startswith("return") for line in meaningful_body)
        helper_header = f"{body_indent}def {new_method_name}():\n"
        helper_body = [
            (f"{body_indent}    {line[len(body_indent):]}" if line.startswith(body_indent) else f"{body_indent}    {line.lstrip()}")
            for line in body
        ]
        call_line = (
            f"{body_indent}return {new_method_name}()\n"
            if has_return
            else f"{body_indent}{new_method_name}()\n"
        )
        replacement = [header, helper_header, *helper_body, call_line]
        return "".join(lines[: start_line - 1] + replacement + lines[end_line:]), 1

    # 2. Extract each block into a helper and build the main function call sequence
    prefix_code = "".join(lines[: start_line - 1])
    suffix_code = "".join(lines[end_line:])
    
    extracted_helpers: List[str] = []
    main_calls: List[str] = []
    
    # Track variables defined before each block starts
    defined_before_set = set()
    # Populate initial parameters from function header
    param_match = re.search(r'def\s+[A-Za-z0-9_]+\s*\(([^)]*)\)', header)
    if param_match:
        for p in param_match.group(1).split(","):
            p_clean = p.split("=")[0].strip()
            if p_clean:
                defined_before_set.add(p_clean)

    for idx, block_code in enumerate(valid_blocks, 1):
        # Determine code before/after this block for scope analysis (local function scope only)
        code_before = header + "".join(valid_blocks[:idx-1])
        code_after = "".join(valid_blocks[idx:])
        
        # Analyze variables (inputs/outputs)
        try:
            block_lines_to_dedent = block_code.splitlines()
            block_min_ind = min((_line_indent(l) for l in block_lines_to_dedent if l.strip()), default="")
            block_dedented = "\n".join(l[len(block_min_ind):] if l.startswith(block_min_ind) else l.lstrip() for l in block_lines_to_dedent)
            block_node = ast.parse(block_dedented)
        except Exception:
            # Fallback for parsing errors
            main_calls.append(block_code)
            continue
            
        reads = set()
        writes = set()
        for node in ast.walk(block_node):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    reads.add(node.id)
                elif isinstance(node.ctx, ast.Store):
                    writes.add(node.id)
            elif isinstance(node, ast.arg):
                writes.add(node.arg)

        # Variables defined before this block
        try:
            before_node = ast.parse(code_before)
            for node in ast.walk(before_node):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    defined_before_set.add(node.id)
                elif isinstance(node, ast.arg):
                    defined_before_set.add(node.arg)
        except Exception:
            pass

        # Variables read after this block
        read_after_set = set()
        try:
            after_dedented = "".join(
                (l[len(body_indent):] if l.startswith(body_indent) else l.lstrip())
                for l in code_after.splitlines(keepends=True)
            )
            after_node = ast.parse(after_dedented)
            for node in ast.walk(after_node):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    read_after_set.add(node.id)
        except Exception:
            pass

        # Filter builtins/globals
        builtins = {"print", "input", "float", "int", "str", "round", "range", "len", "ValueError", "Exception", "True", "False", "None"}
        inputs = sorted([v for v in reads if v in defined_before_set and v not in builtins and not v.isupper()])
        outputs = sorted([v for v in writes if v in read_after_set and v not in builtins and not v.isupper()])

        # Check for early returns in the block (excluding nested functions)
        class ReturnFinder(ast.NodeVisitor):
            def __init__(self):
                self.found = False
            def visit_FunctionDef(self, node):
                pass
            def visit_AsyncFunctionDef(self, node):
                pass
            def visit_Return(self, node):
                self.found = True
        finder = ReturnFinder()
        finder.visit(block_node)
        has_early_return = finder.found

        # Infer a semantic name
        name_choice = "extracted_step"
        # Try finding print header
        header_match = re.search(r'print\(\s*["\']={2,}\s*([^=]+?)\s*={2,}["\']\s*\)', block_code)
        if header_match:
            name_choice = header_match.group(1).lower().strip()
            name_choice = re.sub(r'[^a-z0-9_]+', '_', name_choice)
            if "sheet" in name_choice or "result" in name_choice:
                name_choice = f"display_{name_choice}"
            else:
                name_choice = f"enter_{name_choice}"
        else:
            # Try finding comments
            comment_match = re.search(r'^\s*#\s*([A-Za-z0-9\s_-]+)', block_code, re.MULTILINE)
            if comment_match:
                name_choice = comment_match.group(1).lower().strip()
                name_choice = re.sub(r'[^a-z0-9_]+', '_', name_choice)
            else:
                # Try finding assignments
                assign_match = re.findall(r'^\s*([a-z_][a-z0-9_]*)\s*=', block_code, re.MULTILINE)
                filtered = [v for v in assign_match if v not in ("i", "j", "temp", "val")]
                if filtered:
                    primary = filtered[0]
                    if "grade" in primary:
                        name_choice = "calculate_grades"
                    elif "passed" in primary:
                        name_choice = "count_passed_subjects"
                    elif "overall" in primary:
                        name_choice = "calculate_overall_result"
                    elif "total" in primary:
                        name_choice = "calculate_totals"
                    else:
                        name_choice = f"calculate_{primary}"

        helper_name = f"helper_{name_choice}_{idx}"
        
        # Build helper function signature
        sig_args = ", ".join(inputs)
        helper_sig = f"def {helper_name}({sig_args}):\n"
        
        # Format helper body
        helper_lines = []
        new_locals = sorted([v for v in outputs if v not in inputs])
        if new_locals:
            init_line = "    " + " = ".join(new_locals) + " = None\n"
            helper_lines.append(init_line)

        if has_early_return:
            # Transform returns inside the AST and unparse back
            rt = _ReturnTransformer(outputs)
            transformed_block_node = rt.visit(block_node)
            ast.fix_missing_locations(transformed_block_node)
            block_code_processed = ast.unparse(transformed_block_node)
            helper_lines.extend([f"    {l}\n" for l in block_code_processed.splitlines()])
        else:
            # Deduct indentation and shift by 4 spaces (preserves original formatting and comments)
            lines_to_shift = block_code.splitlines()
            min_ind = min((_line_indent(l) for l in lines_to_shift if l.strip()), default="")
            for l in lines_to_shift:
                l_stripped = l[len(min_ind):] if l.startswith(min_ind) else l.lstrip()
                helper_lines.append(f"    {l_stripped}\n" if l_stripped else "\n")
            
        # Append return statement for success path
        if has_early_return:
            # Success path returns: outputs + (False, None)
            success_returns = outputs + ["False", "None"]
            helper_lines.append(f"    return {', '.join(success_returns)}\n")
        elif outputs:
            helper_lines.append(f"    return {', '.join(outputs)}\n")
            
        extracted_helpers.append(helper_sig + "".join(helper_lines) + "\n\n")
        
        # Build main call line and control flow checks
        call_args = ", ".join(inputs)
        if has_early_return:
            # Helper returns: outputs + (should_return, return_val)
            call_lhs = outputs + ["should_return", "return_val"]
            call_line = f"{body_indent}{', '.join(call_lhs)} = {helper_name}({call_args})\n"
            cf_check = f"{body_indent}if should_return:\n{body_indent}    return return_val\n"
            main_calls.append(call_line + cf_check)
        elif outputs:
            call_line = f"{body_indent}{', '.join(outputs)} = {helper_name}({call_args})\n"
            main_calls.append(call_line)
        else:
            call_line = f"{body_indent}{helper_name}({call_args})\n"
            main_calls.append(call_line)

        # Update defined variables for subsequent blocks
        for o in outputs:
            defined_before_set.add(o)
        for w in writes:
            defined_before_set.add(w)

    # 3. Assemble the final source code
    # Helpers are placed at module scope right before the original function
    assembled_helpers = "".join(extracted_helpers)
    main_func = header + "".join(main_calls)
    
    transformed_code = prefix_code + assembled_helpers + main_func + suffix_code
    return transformed_code, 1


def apply_inject_syntax_error(source_code: str) -> Tuple[str, int]:
    broken = source_code + "\n\ndef __sctva_broken(:\n    pass\n"
    return broken, 1


def apply_fault_injection(
    source_code: str,
    original_logic: str,
    faulty_logic: str,
) -> Tuple[str, int]:
    if not original_logic:
        raise ValueError("fault_injection requires 'original_logic'.")

    if faulty_logic is None:
        raise ValueError("fault_injection requires 'faulty_logic'.")

    if original_logic not in source_code:
        return source_code, 0

    return source_code.replace(original_logic, faulty_logic, 1), 1


def apply_fault_injection_python(
    source_code: str,
    original_logic: str,
    faulty_logic: str,
) -> Tuple[str, int]:
    """Backward-compatible alias for callers that expect a Python-specific name."""
    return apply_fault_injection(source_code, original_logic, faulty_logic)


def apply_extract_class(
    source_code: str,
    *,
    source_file: str = "",
    current_file_name: str = "",
    source_class: str,
    new_class_name: str,
    methods_to_extract: list[str] | None = None,
    fields_to_extract: list[str] | None = None,
    preserve_public_api: bool = True,
    delegation_strategy: str = "wrapper",
    target_file: str = "same_file",
    project_source_files: Sequence[Any] | None = None,
    repository_complete: bool = False,
    behavior_tests: Sequence[dict[str, Any]] | None = None,
    required_public_methods: Sequence[str] | None = None,
    required_public_fields: Sequence[str] | None = None,
    source_resolution_error: str = "",
) -> tuple[str, int, dict[str, Any]]:
    return _apply_extract_class(
        source_code,
        source_file=source_file,
        current_file_name=current_file_name,
        source_class=source_class,
        new_class_name=new_class_name,
        methods_to_extract=methods_to_extract,
        fields_to_extract=fields_to_extract,
        preserve_public_api=preserve_public_api,
        delegation_strategy=delegation_strategy,
        target_file=target_file,
        project_source_files=project_source_files,
        repository_complete=repository_complete,
        behavior_tests=behavior_tests,
        required_public_methods=required_public_methods,
        required_public_fields=required_public_fields,
        source_resolution_error=source_resolution_error,
    )
