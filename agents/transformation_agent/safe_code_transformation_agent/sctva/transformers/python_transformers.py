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
- It generates stable constant names such as MAGIC_NUMBER_6, MAGIC_NUMBER_50,
  MAGIC_NUMBER_0_8.
- It inserts the constant assignment into the module before the replaced value
  is used.
- It avoids replacing values inside existing constant assignments.
- It supports optional source_line targeting for JSON plans that contain:
  "source_line": 22
"""

from __future__ import annotations

import ast
import re
from typing import Any, Optional, Tuple

import libcst as cst
from libcst.metadata import MetadataWrapper, ParentNodeProvider, PositionProvider


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
        return f"MAGIC_BOOL_{str(value).upper()}"

    if value is None:
        return "MAGIC_NONE"

    if isinstance(value, int):
        if value < 0:
            return f"MAGIC_NUMBER_NEG_{abs(value)}"
        return f"MAGIC_NUMBER_{value}"

    if isinstance(value, float):
        text = str(value).replace("-", "NEG_").replace(".", "_")
        return f"MAGIC_NUMBER_{_sanitize_identifier(text)}"

    if isinstance(value, str):
        short = value[:24]
        return f"MAGIC_STRING_{_sanitize_identifier(short)}"

    return "MAGIC_VALUE"


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
    ) -> None:
        self.literal_value = literal_value
        self.constant_name = constant_name
        self.source_line = source_line
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

            MAGIC_NUMBER_50 = 50

        could be transformed into:

            MAGIC_NUMBER_50 = MAGIC_NUMBER_50

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
    def __init__(self, method_name: str, class_name: Optional[str]) -> None:
        self.method_name = method_name
        self.class_name = class_name
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

        if original_node.name.value == self.method_name:
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
            MAGIC_NUMBER_6 = 6
            plt.figure(figsize=(12, MAGIC_NUMBER_6))

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

    updated_code, replacements = _apply_transformer(
        source_code,
        _ExtractConstantTransformer(
            literal_value=literal_value,
            constant_name=preferred_name,
            source_line=source_line,
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


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
) -> Tuple[str, int]:
    if not method_name:
        raise ValueError("remove_dead_code requires 'method_name'.")

    return _apply_transformer(
        source_code,
        _RemoveDeadCodeTransformer(method_name, class_name),
    )


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