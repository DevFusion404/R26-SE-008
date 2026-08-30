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
import copy
import re
import textwrap
from collections import Counter
from typing import Any, Optional, Sequence, Tuple

import libcst as cst
from libcst.metadata import MetadataWrapper, ParentNodeProvider, PositionProvider

from .python_extract_class import apply_extract_class as _apply_extract_class
from .python_move_method_validation import create_python_move_method_evidence


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


class _RenamePythonMethodTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(
        self,
        old_name: str,
        new_name: str,
        *,
        target_kind: str,
        source_class: str = "",
    ) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self.target_kind = target_kind
        self.source_class = source_class
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
        current_class = self._class_stack[-1] if self._class_stack else ""
        if self.target_kind == "module_function":
            should_rename = not current_class and original_node.name.value == self.old_name
        else:
            should_rename = (
                current_class == self.source_class
                and original_node.name.value == self.old_name
            )
        if not should_rename:
            return updated_node
        self.replacements += 1
        return updated_node.with_changes(name=cst.Name(self.new_name))

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.CSTNode:
        if self.target_kind != "module_function" or original_node.value != self.old_name:
            return updated_node
        if self._is_definition_name(original_node):
            return updated_node
        self.replacements += 1
        return updated_node.with_changes(value=self.new_name)

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.CSTNode:
        if self.target_kind != "class_method":
            return updated_node
        if not isinstance(original_node.attr, cst.Name) or original_node.attr.value != self.old_name:
            return updated_node
        self.replacements += 1
        return updated_node.with_changes(attr=cst.Name(self.new_name))

    def _is_definition_name(self, node: cst.Name) -> bool:
        try:
            parent = self.get_metadata(ParentNodeProvider, node)
        except Exception:
            return False
        return isinstance(
            parent,
            (
                cst.FunctionDef,
                cst.ClassDef,
                cst.Param,
                cst.AssignTarget,
                cst.AnnAssign,
                cst.ImportAlias,
            ),
        )


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


class _RemoveDeadClassTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, class_name: str, target_line: int) -> None:
        self.class_name = class_name
        self.target_line = target_line
        self.replacements = 0

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.CSTNode:
        position = self.get_metadata(PositionProvider, original_node)
        if (
            original_node.name.value == self.class_name
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


def apply_rename_method(
    source_code: str,
    old_name: str,
    new_name: str,
    *,
    source_class: str = "",
) -> Tuple[str, int, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "refactoring": "Rename Method",
        "language": "python",
        "old_name": old_name,
        "new_name": new_name,
        "source_class": source_class,
        "plan_compliance": "UNKNOWN",
    }
    if not _is_python_identifier(old_name) or not _is_python_identifier(new_name):
        return source_code, 0, {**metadata, "status": "review_required", "reason": "INVALID_METHOD_NAME"}
    if old_name == new_name:
        return source_code, 0, {**metadata, "status": "already_applied", "reason": "METHOD_NAME_UNCHANGED"}

    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        return source_code, 0, {
            **metadata,
            "status": "review_required",
            "reason": "PYTHON_PARSE_FAILED",
            "error": str(exc),
        }

    resolution = _resolve_python_rename_method_target(
        tree,
        old_name=old_name,
        new_name=new_name,
        source_class=source_class,
    )
    if resolution["status"] != "success":
        return source_code, 0, {**metadata, **resolution}

    transformed, replacements = _apply_transformer(
        source_code,
        _RenamePythonMethodTransformer(
            old_name,
            new_name,
            target_kind=resolution["target_kind"],
            source_class=resolution.get("source_class", ""),
        ),
    )
    if replacements <= 0:
        return source_code, 0, {**metadata, "status": "review_required", "reason": "NO_METHOD_REFERENCES_RENAMED"}

    verification = validate_python_rename_method(
        source_code,
        transformed,
        old_name=old_name,
        new_name=new_name,
        source_class=resolution.get("source_class", ""),
    )
    status = "success" if verification.get("passed") else "review_required"
    reason = "RENAMED_METHOD_AND_CALL_SITES" if verification.get("passed") else verification.get("reason", "VALIDATION_FAILED")
    return transformed, replacements, {
        **metadata,
        **resolution,
        "status": status,
        "reason": reason,
        "declaration_renamed": verification.get("declaration_renamed", False),
        "old_declaration_removed": verification.get("old_declaration_removed", False),
        "new_declaration_present": verification.get("new_declaration_present", False),
        "call_sites_updated": verification.get("call_sites_updated", False),
        "signature_preserved": verification.get("signature_preserved", False),
        "replacements": replacements,
        "plan_compliance": "PASS" if verification.get("passed") else "REVIEW_REQUIRED",
    }


def validate_python_rename_method(
    original_code: str,
    transformed_code: str,
    *,
    old_name: str,
    new_name: str,
    source_class: str = "",
) -> dict[str, Any]:
    try:
        original_tree = ast.parse(original_code)
        transformed_tree = ast.parse(transformed_code)
    except SyntaxError as exc:
        return {"passed": False, "reason": "parse_failed", "error": str(exc)}

    original_resolution = _resolve_python_rename_method_target(
        original_tree,
        old_name=old_name,
        new_name=new_name,
        source_class=source_class,
        validate_new_collision=False,
    )
    target_kind = str(original_resolution.get("target_kind") or "")
    resolved_class = str(original_resolution.get("source_class") or "")
    if original_resolution.get("status") != "success":
        return {"passed": False, "reason": original_resolution.get("reason", "target_not_found")}

    before_node = _find_python_callable(
        original_tree,
        old_name,
        source_class=resolved_class if target_kind == "class_method" else "",
    )
    after_new = _find_python_callable(
        transformed_tree,
        new_name,
        source_class=resolved_class if target_kind == "class_method" else "",
    )
    after_old = _find_python_callable(
        transformed_tree,
        old_name,
        source_class=resolved_class if target_kind == "class_method" else "",
    )
    old_refs = _count_python_rename_references(
        transformed_tree,
        old_name,
        target_kind=target_kind,
    )
    new_refs = _count_python_rename_references(
        transformed_tree,
        new_name,
        target_kind=target_kind,
    )
    signature_preserved = (
        before_node is not None
        and after_new is not None
        and _python_callable_signature(before_node) == _python_callable_signature(after_new)
    )
    declaration_renamed = before_node is not None and after_new is not None
    old_declaration_removed = after_old is None
    new_declaration_present = after_new is not None
    call_sites_updated = old_refs == 0 and new_refs > 0
    passed = (
        declaration_renamed
        and old_declaration_removed
        and new_declaration_present
        and signature_preserved
        and call_sites_updated
    )
    return {
        "passed": passed,
        "reason": "python_rename_method_passed" if passed else "python_rename_method_failed",
        "target_kind": target_kind,
        "source_class": resolved_class,
        "old_name": old_name,
        "new_name": new_name,
        "declaration_renamed": declaration_renamed,
        "old_declaration_removed": old_declaration_removed,
        "new_declaration_present": new_declaration_present,
        "signature_preserved": signature_preserved,
        "call_sites_updated": call_sites_updated,
        "remaining_old_references": old_refs,
        "new_references": new_refs,
    }


def _resolve_python_rename_method_target(
    tree: ast.Module,
    *,
    old_name: str,
    new_name: str,
    source_class: str = "",
    validate_new_collision: bool = True,
) -> dict[str, Any]:
    if source_class:
        owner = next(
            (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == source_class),
            None,
        )
        if owner is None:
            return {"status": "not_applicable", "reason": "SOURCE_CLASS_NOT_FOUND"}
        old_methods = [
            node for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old_name
        ]
        if not old_methods:
            return {"status": "not_applicable", "reason": "METHOD_TARGET_NOT_FOUND"}
        if len(old_methods) > 1:
            return {"status": "review_required", "reason": "AMBIGUOUS_METHOD_TARGET"}
        if validate_new_collision and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == new_name
            for node in owner.body
        ):
            return {"status": "review_required", "reason": "METHOD_NAME_COLLISION"}
        duplicate_owners = [
            node.name for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name != source_class
            and any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == old_name
                for child in node.body
            )
        ]
        if duplicate_owners:
            return {
                "status": "review_required",
                "reason": "AMBIGUOUS_CLASS_METHOD_CALL_TARGET",
                "other_owner_classes": sorted(duplicate_owners),
            }
        return {
            "status": "success",
            "target_kind": "class_method",
            "source_class": source_class,
            "target_resolution": "explicit_class_method",
        }

    top_level_matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old_name
    ]
    class_method_matches = [
        (owner.name, child)
        for owner in tree.body
        if isinstance(owner, ast.ClassDef)
        for child in owner.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == old_name
    ]
    if len(top_level_matches) == 1 and not class_method_matches:
        if validate_new_collision and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == new_name
            for node in tree.body
        ):
            return {"status": "review_required", "reason": "METHOD_NAME_COLLISION"}
        binding_reason = _unsafe_python_module_rename_binding(tree, old_name, target=top_level_matches[0])
        if binding_reason:
            return {"status": "review_required", "reason": binding_reason}
        return {
            "status": "success",
            "target_kind": "module_function",
            "source_class": "",
            "target_resolution": "module_function",
        }
    if len(top_level_matches) == 0 and len(class_method_matches) == 1:
        owner_name, _method = class_method_matches[0]
        if validate_new_collision:
            owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner_name)
            if any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == new_name
                for node in owner.body
            ):
                return {"status": "review_required", "reason": "METHOD_NAME_COLLISION"}
        return {
            "status": "success",
            "target_kind": "class_method",
            "source_class": owner_name,
            "target_resolution": "unique_class_method",
        }
    if not top_level_matches and not class_method_matches:
        return {"status": "not_applicable", "reason": "METHOD_TARGET_NOT_FOUND"}
    return {
        "status": "review_required",
        "reason": "AMBIGUOUS_METHOD_TARGET",
        "top_level_matches": len(top_level_matches),
        "class_method_matches": [owner for owner, _method in class_method_matches],
    }


def _is_python_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")))


def _find_python_callable(
    tree: ast.Module,
    name: str,
    *,
    source_class: str = "",
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    if source_class:
        owner = next(
            (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == source_class),
            None,
        )
        if owner is None:
            return None
        candidates = [
            node for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
    else:
        candidates = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
    return candidates[0] if len(candidates) == 1 else None


def _python_callable_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    args = node.args
    return {
        "posonly": [arg.arg for arg in args.posonlyargs],
        "args": [arg.arg for arg in args.args],
        "kwonly": [arg.arg for arg in args.kwonlyargs],
        "defaults": len(args.defaults),
        "kw_defaults": len([item for item in args.kw_defaults if item is not None]),
        "vararg": args.vararg.arg if args.vararg else "",
        "kwarg": args.kwarg.arg if args.kwarg else "",
        "returns": ast.unparse(node.returns) if node.returns else "",
        "async": isinstance(node, ast.AsyncFunctionDef),
    }


def _count_python_rename_references(
    tree: ast.Module,
    method_name: str,
    *,
    target_kind: str,
) -> int:
    count = 0
    for node in ast.walk(tree):
        if target_kind == "module_function":
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == method_name:
                count += 1
        elif isinstance(node, ast.Attribute) and node.attr == method_name:
            count += 1
    return count


def _unsafe_python_module_rename_binding(
    tree: ast.Module,
    old_name: str,
    *,
    target: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    for node in ast.walk(tree):
        if node is target:
            continue
        if isinstance(node, ast.arg) and node.arg == old_name:
            return "NAME_SHADOWED_BY_PARAMETER"
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == old_name:
            return "NAME_SHADOWED_BY_ASSIGNMENT"
        if isinstance(node, ast.alias) and (node.asname or node.name.split(".")[-1]) == old_name:
            return "NAME_SHADOWED_BY_IMPORT"
        if isinstance(node, ast.ClassDef) and node.name == old_name:
            return "NAME_SHADOWED_BY_CLASS"
    return ""


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
    if not method_name and source_line is None and not class_name:
        raise ValueError("remove_dead_code requires 'method_name', 'class_name', or 'source_line'.")

    # RDP can provide the owning method together with the line of a dead
    # statement inside it. Resolve that statement before considering removal
    # of the callable itself; a live method must never block safe statement
    # removal.
    if method_name and source_line is not None:
        if dead_code_kind in {
            "constant_false_branch",
            "unreachable_after_terminator",
            "unused_literal_assignment",
        } or target_statement_fingerprint:
            return _remove_proven_dead_python_statement(
                source_code,
                source_line,
                class_name=class_name,
                method_name=method_name,
                dead_code_kind=dead_code_kind,
                target_statement_fingerprint=target_statement_fingerprint,
            )
        resolved_kind, resolved_fingerprint = resolve_dead_code_target(
            source_code,
            method_name=method_name,
            class_name=class_name,
            source_line=source_line,
        )
        if resolved_kind in {
            "constant_false_branch",
            "unreachable_after_terminator",
            "unused_literal_assignment",
        }:
            return _remove_proven_dead_python_statement(
                source_code,
                source_line,
                class_name=class_name,
                method_name=method_name,
                dead_code_kind=resolved_kind,
                target_statement_fingerprint=resolved_fingerprint,
            )

    if not method_name and source_line is not None:
        return _remove_proven_dead_python_statement(
            source_code,
            source_line,
            class_name=class_name,
            method_name=method_name,
            dead_code_kind=dead_code_kind,
            target_statement_fingerprint=target_statement_fingerprint,
        )

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0

    if not method_name and class_name and dead_code_kind == "unused_class":
        classes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        if len(classes) != 1:
            return source_code, 0
        target = classes[0]
        return _apply_transformer(
            source_code,
            _RemoveDeadClassTransformer(
                class_name,
                target_line=int(getattr(target, "lineno", 0) or 0),
            ),
        )

    target = _resolve_python_dead_callable_identity(
        tree,
        method_name=method_name,
        class_name=class_name,
        source_line=source_line,
    )
    if target is None or not _is_proven_unused_python_callable(
        tree,
        target=target,
        method_name=target.name,
    ):
        return source_code, 0

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    resolved_class = _python_callable_class_name(target, parents)

    return _apply_transformer(
        source_code,
        _RemoveDeadCodeTransformer(
            target.name,
            resolved_class,
            target_line=target.lineno,
        ),
    )


def resolve_bare_exception_handler(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    source_class: str = "",
    source_method: str = "",
    handler_name: str = "",
    target_exception_type: str = "",
    require_specific_exception: bool = True,
) -> dict[str, Any]:
    """Resolve one current-AST bare handler and a *proven* replacement type.

    Line numbers and class/method names from RDP are hints: preceding SCTVA
    actions can legitimately move a handler.  A target is accepted only when
    exactly one bare handler remains after applying every usable hint.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "PYTHON_PARSE_FAILED"}

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def context(handler: ast.ExceptHandler) -> tuple[str, str, ast.Try | None]:
        class_name = ""
        method_name = ""
        current: ast.AST | None = handler
        parent_try: ast.Try | None = None
        while current is not None:
            current = parents.get(current)
            if isinstance(current, ast.Try) and parent_try is None:
                parent_try = current
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method_name:
                method_name = current.name
            if isinstance(current, ast.ClassDef) and not class_name:
                class_name = current.name
        return class_name, method_name, parent_try

    def method_context(method: ast.AST) -> tuple[str, str]:
        class_name = ""
        current: ast.AST | None = method
        while current is not None:
            current = parents.get(current)
            if isinstance(current, ast.ClassDef):
                class_name = current.name
                break
        return class_name, str(getattr(method, "name", "") or "")

    def handler_belongs_to(handler: ast.ExceptHandler, method: ast.AST) -> bool:
        current: ast.AST | None = handler
        while current is not None:
            if current is method:
                return True
            current = parents.get(current)
        return False

    bare_handlers = [
        handler
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler) and handler.type is None
    ]
    if not bare_handlers:
        return {"status": "not_applicable", "reason": "BARE_EXCEPT_TARGET_NOT_FOUND"}

    method_nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    selected_method: ast.AST | None = None
    target_resolution = "file_bare_handler_fallback"
    explicit_matches = [
        method for method in method_nodes
        if method_context(method) == (source_class, source_method)
    ] if source_class and source_method else []
    method_matches = [
        method for method in method_nodes
        if str(getattr(method, "name", "") or "") == source_method
    ] if source_method else []
    line_matches = [
        method for method in method_nodes
        if source_line is not None
        and int(getattr(method, "lineno", 0) or 0) <= source_line <= int(
            getattr(method, "end_lineno", 0) or 0
        )
    ]

    if len(explicit_matches) == 1:
        selected_method = explicit_matches[0]
        target_resolution = "explicit_class_and_method"
    elif len(method_matches) == 1:
        selected_method = method_matches[0]
        target_resolution = "explicit_method_current_ast"
    elif line_matches:
        # Nested callables can share a line range. The innermost enclosing
        # callable is the handler's semantic owner.
        selected_method = min(
            line_matches,
            key=lambda method: int(getattr(method, "end_lineno", 0) or 0)
            - int(getattr(method, "lineno", 0) or 0),
        )
        target_resolution = "current_ast_enclosing_method_from_line"
    elif len(explicit_matches) > 1 or len(method_matches) > 1:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_SOURCE_METHOD",
            "candidate_count": len(explicit_matches) or len(method_matches),
        }

    candidates = (
        [handler for handler in bare_handlers if handler_belongs_to(handler, selected_method)]
        if selected_method is not None
        else list(bare_handlers)
    )
    if handler_name:
        matches = [handler for handler in candidates if str(handler.name or "") == handler_name]
        if matches:
            candidates = matches
    if source_line is not None:
        matches = [handler for handler in candidates if handler.lineno == source_line]
        if matches:
            candidates = matches

    if len(candidates) != 1:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_BARE_EXCEPT_TARGET" if len(candidates) > 1 else "BARE_EXCEPT_TARGET_NOT_FOUND",
            "candidate_count": len(candidates),
            "target_resolution": target_resolution,
        }

    handler = candidates[0]
    resolved_class, resolved_method, try_node = context(handler)
    target_metadata = {
        "original_handler": "bare_except",
        "source_class": resolved_class,
        "source_method": resolved_method,
        "qualified_source_method": (
            f"{resolved_class}.{resolved_method}"
            if resolved_class and resolved_method else resolved_method
        ),
        "handler_line": handler.lineno,
        "resolved_handler_line": handler.lineno,
        "handler_index": (
            try_node.handlers.index(handler) if try_node is not None else 0
        ),
        "try_line": int(getattr(try_node, "lineno", 0) or 0),
        "handler_name": str(handler.name or ""),
        "original_handler_type": "bare_except",
        "target_resolution": target_resolution,
    }

    # Some callers only need stable target identity for de-duplication or to
    # repair a legacy planner action.  Do not make target resolution depend on
    # whether the exception type has already been proven.
    if not require_specific_exception:
        return {
            "status": "success",
            **target_metadata,
            "replacement_exception": "",
            "exception_resolution_strategy": "target_only",
        }

    replacement = str(target_exception_type or "").strip()
    strategy = "explicit_plan_exception_type" if replacement else ""
    if replacement in {"Exception", "BaseException"}:
        replacement = ""
        strategy = ""

    if not replacement and try_node is not None:
        mysql_error = _python_mysql_error_for_try(tree, try_node)
        if mysql_error:
            mysql_exception_types = {
                mysql_error,
                *_python_database_row_access_exceptions(try_node),
            }
            replacement = _python_exception_tuple_text(
                sorted(mysql_exception_types)
            )
            strategy = "import_and_try_body_context"

    if not replacement and try_node is not None:
        inferred = sorted({
            exception_type
            for _, exception_types in _python_try_risky_statement_exceptions(tree, try_node)
            for exception_type in exception_types
            if exception_type not in {"Exception", "BaseException"}
        })
        if len(inferred) == 1:
            replacement = inferred[0]
            strategy = "try_body_ast_exception_evidence"

    if not _valid_python_exception_type(replacement):
        return {
            "status": "review_required",
            "reason": "SPECIFIC_EXCEPTION_TYPE_NOT_PROVEN",
            "source_class": resolved_class,
            "source_method": resolved_method,
            "handler_line": handler.lineno,
        }

    return {
        "status": "success",
        **target_metadata,
        "replacement_exception": replacement,
        "exception_resolution_strategy": strategy,
    }


def _python_mysql_error_for_try(tree: ast.Module, try_node: ast.Try) -> str:
    """Return a MySQL error class only with import and DB-operation evidence."""
    imported_error_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("mysql.connector"):
            for alias in node.names:
                if alias.name in {"Error", "DatabaseError", "InterfaceError"}:
                    imported_error_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mysql.connector":
                    imported_error_names.add(f"{alias.asname or 'mysql'}.connector.Error")

    if not imported_error_names:
        return ""
    calls = {
        _python_call_name(node.func).lower()
        for statement in try_node.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
    }
    database_operations = {
        "connect", "cursor", "execute", "executemany", "fetchone",
        "fetchall", "fetchmany", "commit", "rollback", "close",
    }
    proven_database_receivers = _python_database_receivers_before_try(tree, try_node)
    has_database_operation = any(
        call.startswith("mysql.connector.")
        or (
            call.split(".")[-1] in database_operations
            and (
                call.rsplit(".", 1)[0] in proven_database_receivers
                or any(
                    token in call
                    for token in ("db", "database", "conn", "connection", "cursor")
                )
            )
        )
        for call in calls
    )
    return sorted(imported_error_names)[0] if has_database_operation else ""


def _python_database_row_access_exceptions(try_node: ast.Try) -> set[str]:
    """Return locally provable row-access failures inside a DB try block.

    ``cursor.fetchone()`` commonly feeds an indexed row immediately afterward.
    Even when the database call itself is covered by the connector's ``Error``
    class, a missing row can make the value non-subscriptable and an incomplete
    sequence can make the index invalid.  Keeping those locally visible failure
    modes in the narrowed handler avoids changing a legacy login/fallback path
    into an uncaught ``TypeError``/``IndexError``.
    """

    fetched_names: set[str] = set()
    for statement in try_node.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if not isinstance(value, ast.Call):
            continue
        call_name = _python_call_name(value.func).lower()
        if call_name.rsplit(".", 1)[-1] != "fetchone":
            continue
        targets = list(statement.targets) if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Name):
                fetched_names.add(target.id)

    if not fetched_names:
        return set()

    for statement in try_node.body:
        for node in ast.walk(statement):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in fetched_names
            ):
                return {"IndexError", "TypeError"}
    return set()


def _python_database_receivers_before_try(
    tree: ast.Module,
    try_node: ast.Try,
) -> set[str]:
    """Prove local names/attributes that hold DB connections or cursors.

    Real legacy code frequently creates a cursor before the ``try`` block and
    then uses a short variable such as ``cur`` inside the protected body.  A
    name-only heuristic therefore misses valid database evidence.  This helper
    follows simple assignment lineage such as::

        cur = self.conn.cursor()
        try:
            cur.execute(...)

    Only direct assignments before the target try are used; no speculative
    interprocedural type inference is performed.
    """

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    owner: ast.AST | None = try_node
    while owner is not None:
        owner = parents.get(owner)
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            break

    statements = list(getattr(owner, "body", []) or [])
    known: set[str] = set()

    def assigned_names(statement: ast.AST) -> list[str]:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        result: list[str] = []
        for target in targets:
            name = _python_call_name(target)
            if name:
                result.append(name.lower())
        return result

    for statement in statements:
        if statement is try_node:
            break
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        if value is None:
            continue
        value_name = _python_call_name(value).lower()
        value_is_db = False
        if isinstance(value, ast.Call):
            call_name = _python_call_name(value.func).lower()
            leaf = call_name.rsplit(".", 1)[-1]
            receiver = call_name.rsplit(".", 1)[0] if "." in call_name else ""
            value_is_db = (
                call_name.startswith("mysql.connector.")
                or leaf in {"connect", "cursor"}
                and (
                    receiver in known
                    or any(
                        token in receiver
                        for token in ("db", "database", "conn", "connection", "cursor")
                    )
                )
            )
        elif value_name:
            value_is_db = value_name in known

        if value_is_db:
            known.update(assigned_names(statement))

    return known


def apply_narrow_exception_handler(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    original_exception_type: str = "",
    target_exception_type: str = "",
    handler_name: str = "",
    source_class: str = "",
    source_method: str = "",
) -> Tuple[str, int]:
    """Narrow broad Python exception handling.

    For a small, already-local try block this rewrites only the ``except``
    header.  For a broad overreaching try block, SCTVA splits the protected
    body and wraps only statements whose likely exceptions can be proven from
    local syntax such as numeric conversion, dictionary/list indexing, file I/O,
    division, or explicit ``raise``.
    """

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0

    # Bare handlers need a specific, evidence-backed type.  This resolution is
    # separate from broad ``except Exception`` narrowing, which can split a
    # try body when there are multiple independently-provable operations.
    if not original_exception_type:
        resolution = resolve_bare_exception_handler(
            source_code,
            source_line=source_line,
            source_class=source_class,
            source_method=source_method,
            handler_name=handler_name,
            target_exception_type=target_exception_type,
        )
        if resolution.get("status") != "success":
            return source_code, 0
        source_line = int(resolution["handler_line"])
        handler_name = str(resolution.get("handler_name") or "")
        target_exception_type = str(resolution["replacement_exception"])

    candidates: list[tuple[ast.Try, ast.ExceptHandler]] = []
    for try_node in (node for node in ast.walk(tree) if isinstance(node, ast.Try)):
        for handler in try_node.handlers:
            handler_type = _python_exception_expression_name(handler.type)
            if original_exception_type:
                if handler_type != original_exception_type:
                    continue
            elif handler.type is not None:
                continue
            if handler_name and str(handler.name or "") != handler_name:
                continue
            candidates.append((try_node, handler))

    if source_line is not None:
        line_matches = [
            candidate for candidate in candidates
            if int(getattr(candidate[1], "lineno", 0) or 0) == source_line
        ]
        if line_matches:
            candidates = line_matches
    if len(candidates) != 1:
        return source_code, 0

    try_node, handler = candidates[0]
    risky_statements = _python_try_risky_statement_exceptions(
        tree,
        try_node,
    )
    inferred_types = sorted({
        exception_type
        for _, exception_types in risky_statements
        for exception_type in exception_types
    })
    if not target_exception_type and inferred_types:
        target_exception_type = ", ".join(inferred_types)

    can_split = (
        original_exception_type == "Exception"
        and target_exception_type != "Exception"
        and len(try_node.body) > 1
        and bool(risky_statements)
        and _python_try_body_is_safe_to_split(try_node)
        and not try_node.orelse
        and not try_node.finalbody
        and len(try_node.handlers) == 1
    )
    if can_split:
        transformed = _split_python_overreaching_try(
            source_code=source_code,
            try_node=try_node,
            handler=handler,
            risky_statements=dict(risky_statements),
        )
        if transformed != source_code:
            try:
                ast.parse(transformed)
            except SyntaxError:
                return source_code, 0
            return transformed, len(risky_statements)

    if not _valid_python_exception_type(target_exception_type):
        return source_code, 0

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


def _python_try_body_is_safe_to_split(try_node: ast.Try) -> bool:
    unsafe_nodes = (
        ast.Try,
        ast.AsyncWith,
        ast.AsyncFor,
        ast.Yield,
        ast.YieldFrom,
        ast.Await,
    )
    for statement in try_node.body:
        for node in ast.walk(statement):
            if isinstance(node, unsafe_nodes):
                return False
    return True


def _split_python_overreaching_try(
    *,
    source_code: str,
    try_node: ast.Try,
    handler: ast.ExceptHandler,
    risky_statements: dict[ast.stmt, tuple[str, ...]],
) -> str:
    lines = source_code.splitlines(keepends=True)
    if not try_node.body or not handler.body:
        return source_code

    try_line = int(getattr(try_node, "lineno", 0) or 0)
    end_line = int(getattr(try_node, "end_lineno", 0) or 0)
    if try_line <= 0 or end_line <= try_line or end_line > len(lines):
        return source_code

    try_indent = _line_indent(lines[try_line - 1])
    body_indent = _line_indent(lines[int(getattr(try_node.body[0], "lineno", try_line + 1)) - 1])
    if len(body_indent) <= len(try_indent):
        body_indent = f"{try_indent}    "

    handler_body_start = int(getattr(handler.body[0], "lineno", 0) or 0)
    handler_body_end = int(getattr(handler, "end_lineno", 0) or 0)
    if handler_body_start <= 0 or handler_body_end < handler_body_start:
        return source_code
    handler_body_lines = lines[handler_body_start - 1:handler_body_end]

    replacement: list[str] = []
    cursor = try_line + 1
    for statement in try_node.body:
        stmt_start = int(getattr(statement, "lineno", cursor) or cursor)
        stmt_end = int(getattr(statement, "end_lineno", stmt_start) or stmt_start)
        segment = lines[cursor - 1:stmt_end]
        cursor = stmt_end + 1
        exception_types = risky_statements.get(statement)
        if exception_types:
            replacement.append(f"{try_indent}try:\n")
            replacement.extend(segment)
            alias = f" as {handler.name}" if handler.name else ""
            type_text = _python_exception_tuple_text(exception_types)
            replacement.append(f"{try_indent}except {type_text}{alias}:\n")
            replacement.extend(handler_body_lines)
        else:
            replacement.extend(
                _dedent_python_try_body_segment(
                    segment,
                    body_indent=body_indent,
                    try_indent=try_indent,
                )
            )

    trailer_end = int(getattr(handler, "lineno", cursor) or cursor) - 1
    if cursor <= trailer_end:
        replacement.extend(
            _dedent_python_try_body_segment(
                lines[cursor - 1:trailer_end],
                body_indent=body_indent,
                try_indent=try_indent,
            )
        )

    return "".join([
        *lines[:try_line - 1],
        *replacement,
        *lines[end_line:],
    ])


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _dedent_python_try_body_segment(
    segment: Sequence[str],
    *,
    body_indent: str,
    try_indent: str,
) -> list[str]:
    dedented: list[str] = []
    for line in segment:
        if line.strip() and line.startswith(body_indent):
            dedented.append(f"{try_indent}{line[len(body_indent):]}")
        else:
            dedented.append(line)
    return dedented


def _python_exception_tuple_text(exception_types: Sequence[str]) -> str:
    unique = tuple(dict.fromkeys(exception_types))
    if len(unique) == 1:
        return unique[0]
    return f"({', '.join(unique)})"


def _python_try_risky_statement_exceptions(
    tree: ast.Module,
    try_node: ast.Try,
) -> list[tuple[ast.stmt, tuple[str, ...]]]:
    known_containers = _python_known_container_types_before(tree, try_node)
    risky: list[tuple[ast.stmt, tuple[str, ...]]] = []
    for statement in try_node.body:
        exception_types = tuple(
            sorted(_python_statement_exception_types(statement, known_containers))
        )
        if exception_types:
            risky.append((statement, exception_types))
        _update_python_known_container_types(statement, known_containers)
    return risky


def _python_known_container_types_before(
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
        _update_python_known_container_types(statement, known)
    return known


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


def _python_statement_exception_types(
    statement: ast.AST,
    known_containers: dict[str, str],
) -> set[str]:
    visitor = _PythonRiskyExceptionVisitor(known_containers)
    visitor.visit(statement)
    return visitor.exception_types


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
            name = _python_exception_expression_name(expression)
            if name and name not in {"Exception", "BaseException"}:
                self.exception_types.add(name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _python_call_name(node.func)
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


def _python_call_name(function: ast.AST) -> str:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        base = _python_call_name(function.value)
        return f"{base}.{function.attr}" if base else function.attr
    return ""


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


class _MoveMethodFeatureEnvyVisitor(ast.NodeVisitor):
    """Collect only dependencies that are safe to carry to another class."""

    def __init__(self, candidate_parameters: set[str]) -> None:
        self.candidate_parameters = candidate_parameters
        self.destination_attribute_counts: dict[str, int] = {
            name: 0 for name in candidate_parameters
        }
        self.source_self_attribute_count = 0
        self.reassigned_destination_parameter = False
        self.has_nested_scope = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.has_nested_scope = True

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.has_nested_scope = True

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.has_nested_scope = True

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.has_nested_scope = True

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            if node.value.id in self.destination_attribute_counts:
                self.destination_attribute_counts[node.value.id] += 1
            elif node.value.id == "self":
                self.source_self_attribute_count += 1
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if (
            node.id in self.candidate_parameters
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            self.reassigned_destination_parameter = True


class _MoveMethodNameRewriter(cst.CSTTransformer):
    def __init__(self, old_name: str) -> None:
        self.old_name = old_name

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.Name:
        if original_node.value == self.old_name:
            return updated_node.with_changes(value="self")
        return updated_node


def resolve_move_method_target(
    source_code: str,
    *,
    method_name: str = "",
    source_method: str = "",
    source_class: str = "",
    destination_class: str = "",
    destination_parameter: str = "",
    source_line: int | None = None,
    allow_unique_inference: bool = False,
) -> dict[str, Any]:
    """Resolve a Feature-Envy Move Method target from real Python AST evidence.

    RDP sometimes supplies filename-derived placeholders rather than symbols.
    This resolver deliberately does *not* guess when multiple candidates exist.
    It succeeds only when the source proves one unambiguous method, source class,
    destination class and envied parameter.
    """

    requested_method = str(method_name or source_method or "").strip()
    requested_source = str(source_class or "").strip()
    requested_destination = str(destination_class or "").strip()
    requested_parameter = str(destination_parameter or "").strip()
    placeholder_class_names = {"", "sourceclass", "unknownclass", "none", "null"}

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}

    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    module_function_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def class_method_node(
        class_node: ast.ClassDef,
        name: str,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        return next(
            (
                item
                for item in class_node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            ),
            None,
        )

    explicit_source_node = classes.get(requested_source) if requested_source else None
    explicit_source_method = (
        class_method_node(explicit_source_node, requested_method)
        if explicit_source_node is not None and requested_method
        else None
    )

    def module_function_not_applicable(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        target_resolution: str,
        requested_method_name: str = "",
    ) -> dict[str, Any]:
        return {
            "status": "not_applicable",
            "reason": "MOVE_METHOD_TARGET_IS_MODULE_FUNCTION",
            "target_kind": "MODULE_FUNCTION",
            "suggested_refactoring": "MOVE_FUNCTION",
            "source_class_resolved": False,
            "destination_class_resolved": requested_destination in classes,
            "target_resolution": target_resolution,
            "method": function.name,
            "lineno": int(getattr(function, "lineno", 0) or 0),
            "end_lineno": int(
                getattr(function, "end_lineno", getattr(function, "lineno", 0)) or 0
            ),
            "requested_method": requested_method_name,
            "requested_source_method": requested_method_name,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "replacements_count": 0,
        }

    # A module-level function is not a Move Method target, even when the RDP
    # plan invents filename-derived classes such as ``SourceClass``.  A real
    # explicitly requested source class takes precedence, however: Python can
    # legally contain both a module function and a class method with the same
    # name, and the class-qualified plan must resolve to the class method.
    if explicit_source_node is None and requested_method in module_function_nodes:
        return module_function_not_applicable(
            module_function_nodes[requested_method],
            target_resolution="module_level_function_ast",
            requested_method_name=requested_method,
        )

    # When the method name is stale/missing, a CUQA/RDP source line can still
    # prove that the smell points at a module function.  In that case Move
    # Method is structurally inapplicable and must not be redirected to a
    # different class method.
    if isinstance(source_line, int) and source_line > 0:
        line_function_matches = [
            node
            for node in module_function_nodes.values()
            if int(getattr(node, "lineno", 0) or 0)
            <= source_line
            <= int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0)
        ]
        if len(line_function_matches) == 1:
            function = line_function_matches[0]
            if explicit_source_node is None:
                return module_function_not_applicable(
                    function,
                    target_resolution="source_line_module_function_guard",
                    requested_method_name=requested_method,
                )

    # An explicitly named class is authoritative.  If it exists but does not
    # own the requested method, do not fall back to a same-named module
    # function or to an unrelated class method.
    if explicit_source_node is not None and requested_method and explicit_source_method is None:
        return {
            "status": "not_applicable",
            "reason": "MOVE_METHOD_TARGET_NOT_FOUND",
            "target_kind": "CLASS_METHOD",
            "source_class_resolved": True,
            "requested_method": requested_method,
            "requested_source_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "replacements_count": 0,
        }

    # Explicit class names are contracts, not hints.  If one of them is
    # absent, do not recover a different class pair and accidentally turn a
    # module function or stale RDP target into a Move Method.
    if requested_source and requested_source not in classes:
        # Preserve semantic recovery for malformed filename-derived plans only
        # when neither the requested method nor source class names a real
        # symbol.  A source-line recovery can still identify the real method.
        malformed_recovery = (
            isinstance(source_line, int)
            and source_line > 0
            and requested_source.lower() not in placeholder_class_names
            and requested_method not in module_function_nodes
            and requested_method == requested_source
            and any(
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == requested_destination
                for owner in classes.values()
                for item in owner.body
            )
        )
        if not malformed_recovery:
            return {
                "status": "not_applicable",
                "reason": "MOVE_METHOD_REQUIRES_SOURCE_AND_DESTINATION_CLASSES",
                "target_kind": "CLASS_METHOD",
                "source_class_resolved": False,
                "missing_class": "source",
                "requested_method": requested_method,
                "requested_source_method": requested_method,
                "requested_source_class": requested_source,
                "requested_destination_class": requested_destination,
                "replacements_count": 0,
            }
    if requested_destination and requested_destination not in classes:
        # A clearly real source method plus an absent destination is an
        # invalid Move Method request.  The legacy malformed-plan recovery
        # below remains available when the method itself is also a stale hint.
        source_has_method = bool(
            requested_source in classes
            and any(
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == requested_method
                for item in classes[requested_source].body
            )
        )
        if source_has_method or requested_method in module_function_nodes:
            return {
                "status": "not_applicable",
                "reason": "MOVE_METHOD_DESTINATION_CLASS_NOT_FOUND",
                "target_kind": "CLASS_METHOD",
                "source_class_resolved": requested_source in classes,
                "destination_class_resolved": False,
                "missing_class": "destination",
                "requested_method": requested_method,
                "requested_source_method": requested_method,
                "requested_source_class": requested_source,
                "requested_destination_class": requested_destination,
                "replacements_count": 0,
            }
    if (
        requested_source
        and requested_destination
        and requested_source == requested_destination
    ):
        return {
            "status": "not_applicable",
            "reason": "SOURCE_AND_DESTINATION_CLASS_MATCH",
            "target_kind": "CLASS_METHOD",
            "source_class_resolved": requested_source in classes,
            "destination_class_resolved": requested_destination in classes,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "replacements_count": 0,
        }
    if (not requested_source or not requested_destination) and not allow_unique_inference:
        return {
            "status": "not_applicable",
            "reason": "MOVE_METHOD_REQUIRES_SOURCE_AND_DESTINATION_CLASSES",
            "target_kind": "CLASS_METHOD",
            "source_class_resolved": requested_source in classes,
            "destination_class_resolved": requested_destination in classes,
            "missing_class": "source" if not requested_source else "destination",
            "requested_method": requested_method,
            "requested_source_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "replacements_count": 0,
        }
    if len(classes) < 2:
        return {
            "status": "not_applicable",
            "reason": "MOVE_METHOD_REQUIRES_SOURCE_AND_DESTINATION_CLASSES",
            "target_kind": "CLASS_METHOD",
            "source_class_resolved": requested_source in classes,
            "destination_class_resolved": requested_destination in classes,
            "requested_method": requested_method,
            "requested_source_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "replacements_count": 0,
        }

    # Track simple assignments such as ``student = Student(...)`` so call-site
    # arguments can be tied back to their concrete class.
    instance_types: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id in classes:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        instance_types[target.id] = node.value.func.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in classes
            ):
                instance_types[node.target.id] = value.func.id
            elif isinstance(node.annotation, ast.Name) and node.annotation.id in classes:
                instance_types[node.target.id] = node.annotation.id

    def normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def annotated_class(argument: ast.arg) -> str:
        annotation = argument.annotation
        if isinstance(annotation, ast.Name) and annotation.id in classes:
            return annotation.id
        if (
            isinstance(annotation, ast.Constant)
            and isinstance(annotation.value, str)
            and annotation.value in classes
        ):
            return annotation.value
        return ""

    def call_destination_classes(method: str, parameter: str) -> set[str]:
        inferred: set[str] = set()
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == method
            ):
                continue

            expression: ast.AST | None = None
            if node.args:
                expression = node.args[0]
            else:
                keyword = next(
                    (item for item in node.keywords if item.arg == parameter),
                    None,
                )
                if keyword is not None:
                    expression = keyword.value

            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id in classes
            ):
                inferred.add(expression.func.id)
            elif isinstance(expression, ast.Name):
                known = instance_types.get(expression.id)
                if known:
                    inferred.add(known)
        return inferred

    candidates: list[dict[str, Any]] = []

    explicit_source_method_exists = bool(
        requested_source in classes
        and requested_method
        and any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == requested_method
            for item in classes[requested_source].body
        )
    )
    if requested_source in classes and requested_method and not explicit_source_method_exists:
        return {
            "status": "not_applicable",
            "reason": "MOVE_METHOD_TARGET_NOT_FOUND",
            "target_kind": "CLASS_METHOD",
            "requested_method": requested_method,
            "requested_source_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
        }

    for owner_name, owner_node in classes.items():
        for method in owner_node.body:
            if not isinstance(method, ast.FunctionDef) or method.name == "__init__":
                continue
            if method.decorator_list or method.args.posonlyargs or method.args.vararg:
                continue
            if len(method.args.args) < 2 or method.args.args[0].arg != "self":
                continue

            parameter_nodes = method.args.args[1:]
            parameter_names = [argument.arg for argument in parameter_nodes]
            attribute_counts = {name: 0 for name in parameter_names}
            source_self_accesses = 0
            reassigned_parameters: set[str] = set()
            nested_scope = False

            for node in ast.walk(method):
                if node is not method and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    nested_scope = True
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id in attribute_counts:
                        attribute_counts[node.value.id] += 1
                    elif node.value.id == "self":
                        source_self_accesses += 1
                if isinstance(node, ast.Name) and node.id in attribute_counts:
                    if isinstance(node.ctx, (ast.Store, ast.Del)):
                        reassigned_parameters.add(node.id)

            if nested_scope:
                continue

            highest = max(attribute_counts.values(), default=0)
            envied = [
                name
                for name, count in attribute_counts.items()
                if count == highest and count > 0
            ]
            if len(envied) != 1 or highest < 2 or highest <= source_self_accesses:
                continue

            parameter = envied[0]
            if parameter in reassigned_parameters:
                continue
            if requested_parameter and requested_parameter != parameter:
                # Keep searching; a malformed explicit parameter should not force
                # an unsafe rewrite.
                continue

            argument = next(item for item in parameter_nodes if item.arg == parameter)
            destination_candidates: set[str] = set()

            annotation_target = annotated_class(argument)
            if annotation_target:
                destination_candidates.add(annotation_target)

            parameter_key = normalize_name(parameter)
            for class_name in classes:
                if class_name == owner_name:
                    continue
                class_key = normalize_name(class_name)
                if (
                    parameter_key == class_key
                    or parameter_key.rstrip("s") == class_key.rstrip("s")
                ):
                    destination_candidates.add(class_name)

            destination_candidates.update(
                call_destination_classes(method.name, parameter)
            )

            if requested_destination in classes and requested_destination != owner_name:
                destination_candidates.add(requested_destination)

            destination_candidates.discard(owner_name)
            if len(destination_candidates) != 1:
                continue

            candidates.append({
                "method": method.name,
                "source_class": owner_name,
                "destination_class": next(iter(destination_candidates)),
                "destination_parameter": parameter,
                "feature_envy_accesses": highest,
                "source_self_accesses": source_self_accesses,
                "lineno": int(getattr(method, "lineno", 0) or 0),
                "end_lineno": int(getattr(method, "end_lineno", getattr(method, "lineno", 0)) or 0),
            })

    if not candidates:
        return {"status": "review_required", "reason": "MOVE_METHOD_TARGET_NOT_FOUND"}

    # Prefer a fully correct explicit plan.
    exact = [
        item
        for item in candidates
        if item["method"] == requested_method
        and item["source_class"] == requested_source
        and item["destination_class"] == requested_destination
    ]
    if len(exact) == 1:
        selected = exact[0]
    elif (
        explicit_source_method_exists
        and requested_destination in classes
    ):
        return {
            "status": "review_required",
            "reason": "DESTINATION_CLASS_NOT_COMPATIBLE",
            "target_kind": "CLASS_METHOD",
            "requested_method": requested_method,
            "requested_source_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
        }
    else:
        selected = None

    # Next prefer the RDP source line when it lies inside exactly one candidate.
    if selected is None and isinstance(source_line, int) and source_line > 0:
        line_matches = [
            item
            for item in candidates
            if item["lineno"] <= source_line <= item["end_lineno"]
        ]
        if len(line_matches) == 1:
            selected = line_matches[0]

    # Common malformed RDP shape: destination_class actually contains the method.
    if selected is None and requested_destination and requested_destination not in classes:
        method_hint = [item for item in candidates if item["method"] == requested_destination]
        if len(method_hint) == 1:
            selected = method_hint[0]

    if selected is None and requested_method:
        method_matches = [item for item in candidates if item["method"] == requested_method]
        if len(method_matches) == 1:
            selected = method_matches[0]

    if selected is None and requested_source:
        source_matches = [item for item in candidates if item["source_class"] == requested_source]
        if len(source_matches) == 1:
            selected = source_matches[0]

    if selected is None and len(candidates) == 1:
        selected = candidates[0]

    if selected is None:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_MOVE_METHOD_TARGET",
            "candidate_count": len(candidates),
        }

    return {
        "status": "success",
        **selected,
        "target_kind": "CLASS_METHOD",
        "source_class_resolved": True,
        "destination_class_resolved": True,
        "requested_method": requested_method,
        "requested_source_method": requested_method,
        "requested_source_class": requested_source,
        "requested_destination_class": requested_destination,
        "requested_destination_parameter": requested_parameter,
    }


def apply_move_method(
    source_code: str,
    *,
    method_name: str = "",
    source_method: str = "",
    source_class: str = "",
    destination_class: str = "",
    destination_parameter: str = "",
    source_line: int | None = None,
) -> Tuple[str, int, dict[str, Any]]:
    """Move one Feature-Envy Python instance method to its data owner.

    The implementation is intentionally conservative.  Before transforming it
    resolves the real target from AST evidence.  This makes the transformer
    tolerant of stale/filename-derived RDP metadata while still refusing
    ambiguous moves.
    """

    requested_target = {
        "method": str(method_name or source_method or "").strip(),
        "source_method": str(source_method or method_name or "").strip(),
        "source_class": str(source_class or "").strip(),
        "destination_class": str(destination_class or "").strip(),
        "destination_parameter": str(destination_parameter or "").strip(),
    }

    resolution = resolve_move_method_target(
        source_code,
        method_name=requested_target["method"],
        source_class=requested_target["source_class"],
        destination_class=requested_target["destination_class"],
        destination_parameter=requested_target["destination_parameter"],
        source_line=source_line,
    )

    if resolution.get("status") != "success":
        already_applied = _detect_already_applied_move_method(
            source_code,
            method_name=requested_target["method"],
            source_class=requested_target["source_class"],
            destination_class=requested_target["destination_class"],
        )
        if already_applied.get("status") == "already_applied":
            return source_code, 0, {
                **already_applied,
                **requested_target,
                "target_resolution": {
                    "status": "already_applied",
                    "reason": "MOVE_METHOD_ALREADY_APPLIED",
                },
                "logic_equivalence": "PASS",
                "receiver_normalization": "PASS",
                "structural_validation": "PASS",
                "plan_compliance": "PASS",
            }
        resolution_status = str(resolution.get("status") or "review_required").strip().lower()
        if resolution_status not in {"not_applicable", "review_required"}:
            resolution_status = "review_required"
        return source_code, 0, {
            "status": resolution_status,
            "reason": str(resolution.get("reason") or "MOVE_METHOD_TARGET_NOT_FOUND"),
            "target_kind": str(resolution.get("target_kind") or "CLASS_METHOD"),
            "suggested_refactoring": str(resolution.get("suggested_refactoring") or ""),
            "source_class_resolved": bool(resolution.get("source_class_resolved", False)),
            "destination_class_resolved": bool(
                resolution.get("destination_class_resolved", False)
            ),
            "replacements_count": 0,
            **requested_target,
            "target_resolution": resolution,
        }

    method_name = str(resolution["method"])
    source_class = str(resolution["source_class"])
    destination_class = str(resolution["destination_class"])
    destination_parameter = str(resolution["destination_parameter"])

    def review(reason: str) -> Tuple[str, int, dict[str, Any]]:
        return source_code, 0, {
            "status": "review_required",
            "reason": reason,
            "method": method_name,
            "source_class": source_class,
            "destination_class": destination_class,
            "destination_parameter": destination_parameter,
            "requested_target": requested_target,
            "target_resolution": resolution,
        }

    if not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or "")
        for value in (method_name, source_class, destination_class)
    ):
        return review("INVALID_MOVE_METHOD_TARGET")
    if source_class == destination_class:
        return review("SOURCE_AND_DESTINATION_CLASS_MATCH")

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return review("SOURCE_PARSE_FAILED")

    source_nodes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == source_class
    ]
    destination_nodes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == destination_class
    ]
    if len(source_nodes) != 1:
        return review("SOURCE_CLASS_NOT_FOUND")
    if len(destination_nodes) != 1:
        return review("DESTINATION_CLASS_NOT_FOUND")
    source_node = source_nodes[0]
    destination_node = destination_nodes[0]

    source_methods = [
        node for node in source_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    if len(source_methods) != 1:
        return review("SOURCE_METHOD_NOT_FOUND")
    source_method = source_methods[0]
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
        for node in destination_node.body
    ):
        return review("DESTINATION_METHOD_ALREADY_EXISTS")
    if source_method.decorator_list or source_method.args.posonlyargs or source_method.args.vararg:
        return review("UNSUPPORTED_METHOD_SIGNATURE")
    if len(source_method.args.args) < 2 or source_method.args.args[0].arg != "self":
        return review("SOURCE_METHOD_MUST_BE_INSTANCE_METHOD")

    positional_parameters = source_method.args.args[1:]
    parameter_names = {argument.arg for argument in positional_parameters}
    visitor = _MoveMethodFeatureEnvyVisitor(parameter_names)
    for statement in source_method.body:
        visitor.visit(statement)
    if visitor.has_nested_scope:
        return review("NESTED_SCOPE_DEPENDENCY")
    if visitor.reassigned_destination_parameter:
        return review("DESTINATION_PARAMETER_REASSIGNED")
    if visitor.source_self_attribute_count:
        return review("SOURCE_CLASS_STATE_DEPENDENCY")

    explicit_destination_parameter = destination_parameter.strip()
    if explicit_destination_parameter:
        if explicit_destination_parameter not in parameter_names:
            return review("DESTINATION_PARAMETER_NOT_FOUND")
        selected_parameter = explicit_destination_parameter
    else:
        highest_count = max(visitor.destination_attribute_counts.values(), default=0)
        candidates = [
            name for name, count in visitor.destination_attribute_counts.items()
            if count == highest_count and count > 0
        ]
        class_hint = re.sub(r"(?<!^)(?=[A-Z])", "_", destination_class).lower()
        if class_hint in candidates:
            selected_parameter = class_hint
        elif len(candidates) == 1:
            selected_parameter = candidates[0]
        else:
            return review("DESTINATION_OBJECT_AMBIGUOUS")

    selected_count = visitor.destination_attribute_counts.get(selected_parameter, 0)
    other_counts = [
        count for name, count in visitor.destination_attribute_counts.items()
        if name != selected_parameter
    ]
    if selected_count <= max(other_counts, default=0):
        return review("DESTINATION_OBJECT_AMBIGUOUS")
    if selected_count < 2:
        return review("INSUFFICIENT_FEATURE_ENVY_EVIDENCE")

    if _move_method_parameter_has_default(source_method, selected_parameter):
        return review("DESTINATION_PARAMETER_DEFAULT_UNSUPPORTED")

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    call_rewrites, call_error = _move_method_call_rewrites(
        tree=tree,
        source_code=source_code,
        parents=parents,
        source_method=source_method,
        source_class=source_class,
        method_name=method_name,
        destination_parameter=selected_parameter,
    )
    if call_error:
        return review(call_error)

    moved_method_text = _render_moved_python_method(
        source_code=source_code,
        method=source_method,
        destination_parameter=selected_parameter,
        destination_indent=" " * (destination_node.col_offset + 4),
    )
    if not moved_method_text:
        return review("METHOD_BODY_REWRITE_FAILED")

    line_offsets = _move_method_line_offsets(source_code)
    method_start = line_offsets[source_method.lineno - 1]
    method_end = _move_method_line_end_offset(source_code, line_offsets, source_method.end_lineno)
    destination_last_body = destination_node.body[-1] if destination_node.body else None
    if destination_last_body is None:
        return review("DESTINATION_CLASS_BODY_NOT_FOUND")
    insertion_offset = _move_method_line_content_end_offset(
        source_code,
        line_offsets,
        destination_last_body.end_lineno,
    )

    source_replacement = ""
    if len(source_node.body) == 1:
        source_replacement = f"{' ' * (source_node.col_offset + 4)}pass\n"
    edits: list[tuple[int, int, str]] = [
        (method_start, method_end, source_replacement),
        (insertion_offset, insertion_offset, f"\n\n{moved_method_text}\n"),
        *call_rewrites,
    ]
    if not _move_method_edits_do_not_overlap(edits):
        return review("OVERLAPPING_MOVE_EDITS")
    transformed = _apply_move_method_edits(source_code, edits)
    try:
        ast.parse(transformed)
    except SyntaxError:
        return review("TRANSFORMED_SOURCE_PARSE_FAILED")

    # Validate the actual semantic move before recording it as successful.
    # The resulting proof is carried through the transformation log so the
    # final structural stage does not incorrectly compare this action against
    # a method later altered by another valid plan step.
    validation_evidence = create_python_move_method_evidence(
        original_code=source_code,
        transformed_code=transformed,
        method_name=method_name,
        source_class=source_class,
        destination_class=destination_class,
        destination_parameter=selected_parameter,
    )
    if validation_evidence.get("status") != "PASS":
        return review(str(validation_evidence.get("reason") or "MOVE_METHOD_SEMANTIC_VALIDATION_FAILED"))

    return transformed, 1 + len(call_rewrites), {
        "status": "success",
        "method": method_name,
        "source_class": source_class,
        "destination_class": destination_class,
        "destination_parameter": selected_parameter,
        "logic_equivalence": "PASS",
        "receiver_normalization": "PASS",
        "source_method_removed": True,
        "destination_method_exists": True,
        "call_sites_updated": True,
        "already_applied": False,
        "structural_validation": "PASS",
        "plan_compliance": "PASS",
        "destination_field_accesses": selected_count,
        "updated_direct_call_sites": len(call_rewrites),
        "move_method_validation_evidence": validation_evidence,
        "requested_target": requested_target,
        "target_resolution": resolution,
    }


def _detect_already_applied_move_method(
    source_code: str,
    *,
    method_name: str,
    source_class: str,
    destination_class: str,
) -> dict[str, Any]:
    if not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or "")
        for value in (method_name, source_class, destination_class)
    ):
        return {"status": "review_required", "reason": "INVALID_MOVE_METHOD_TARGET"}
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}
    source_node = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == source_class),
        None,
    )
    destination_node = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == destination_class),
        None,
    )
    if source_node is None or destination_node is None:
        return {"status": "review_required", "reason": "SOURCE_OR_DESTINATION_CLASS_NOT_FOUND"}
    source_method_exists = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
        for node in source_node.body
    )
    destination_methods = [
        node for node in destination_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    ]
    if source_method_exists or len(destination_methods) != 1:
        return {"status": "review_required", "reason": "MOVE_METHOD_TARGET_NOT_FOUND"}

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    known_source_instances = _move_method_known_source_instances(tree, source_class)
    stale_source_calls = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method_name
        and _move_method_receiver_is_source_instance(
            node.func.value,
            known_instances=known_source_instances,
            source_class=source_class,
            parents=parents,
        )
        for node in ast.walk(tree)
    )
    if stale_source_calls:
        return {"status": "review_required", "reason": "STALE_SOURCE_CALL_SITES_REMAIN"}
    return {
        "status": "already_applied",
        "reason": "MOVE_METHOD_ALREADY_APPLIED",
        "method": method_name,
        "source_class": source_class,
        "destination_class": destination_class,
        "source_method_removed": True,
        "destination_method_exists": True,
        "call_sites_updated": True,
        "already_applied": True,
        "updated_direct_call_sites": 0,
    }


def _move_method_parameter_has_default(method: ast.FunctionDef, parameter: str) -> bool:
    positional = method.args.args
    default_start = len(positional) - len(method.args.defaults)
    return any(
        argument.arg == parameter and index >= default_start
        for index, argument in enumerate(positional)
    )


def _move_method_call_rewrites(
    *,
    tree: ast.Module,
    source_code: str,
    parents: dict[ast.AST, ast.AST],
    source_method: ast.FunctionDef,
    source_class: str,
    method_name: str,
    destination_parameter: str,
) -> tuple[list[tuple[int, int, str]], str]:
    known_instances = _move_method_known_source_instances(tree, source_class)
    line_offsets = _move_method_line_offsets(source_code)
    for attribute in ast.walk(tree):
        if not isinstance(attribute, ast.Attribute) or attribute.attr != method_name:
            continue
        if _move_method_is_descendant(attribute, source_method, parents):
            continue
        parent = parents.get(attribute)
        if not isinstance(parent, ast.Call) or parent.func is not attribute:
            return [], "METHOD_REFERENCE_UNSUPPORTED"
    rewrites: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != method_name or _move_method_is_descendant(node, source_method, parents):
            continue
        receiver_is_source = _move_method_receiver_is_source_instance(
            node.func.value,
            known_instances=known_instances,
            source_class=source_class,
            parents=parents,
        )
        if not receiver_is_source:
            return [], "UNRESOLVED_DIRECT_CALL_SITE"
        destination_expression, remaining_args, remaining_keywords = _move_method_call_destination_argument(
            node,
            destination_parameter,
        )
        if destination_expression is None:
            return [], "DIRECT_CALL_SITE_CANNOT_BE_REWRITTEN"
        rewritten_call = ast.Call(
            func=ast.Attribute(
                value=copy.deepcopy(destination_expression),
                attr=method_name,
                ctx=ast.Load(),
            ),
            args=[copy.deepcopy(argument) for argument in remaining_args],
            keywords=[copy.deepcopy(keyword) for keyword in remaining_keywords],
        )
        rewrites.append((
            _move_method_position_offset(line_offsets, node.lineno, node.col_offset),
            _move_method_position_offset(line_offsets, node.end_lineno, node.end_col_offset),
            ast.unparse(ast.fix_missing_locations(rewritten_call)),
        ))
    return rewrites, ""


def _move_method_known_source_instances(tree: ast.Module, source_class: str) -> set[str]:
    known: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == source_class:
                known.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.annotation, ast.Name) and node.annotation.id == source_class:
                known.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in node.args.args:
                if isinstance(argument.annotation, ast.Name) and argument.annotation.id == source_class:
                    known.add(argument.arg)
    return known


def _move_method_receiver_is_source_instance(
    receiver: ast.AST,
    *,
    known_instances: set[str],
    source_class: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if isinstance(receiver, ast.Name):
        if receiver.id in known_instances:
            return True
        if receiver.id != "self":
            return False
        owner = parents.get(receiver)
        while owner is not None and not isinstance(owner, ast.ClassDef):
            owner = parents.get(owner)
        return isinstance(owner, ast.ClassDef) and owner.name == source_class
    return (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == source_class
    )


def _move_method_call_destination_argument(
    call: ast.Call,
    destination_parameter: str,
) -> tuple[ast.AST | None, Sequence[ast.AST], Sequence[ast.keyword]]:
    if call.args:
        if isinstance(call.args[0], ast.Starred):
            return None, (), ()
        return call.args[0], call.args[1:], call.keywords
    destination_keywords = [
        keyword for keyword in call.keywords
        if keyword.arg == destination_parameter
    ]
    if len(destination_keywords) != 1:
        return None, (), ()
    return (
        destination_keywords[0].value,
        (),
        [keyword for keyword in call.keywords if keyword is not destination_keywords[0]],
    )


def _render_moved_python_method(
    *,
    source_code: str,
    method: ast.FunctionDef,
    destination_parameter: str,
    destination_indent: str,
) -> str:
    lines = source_code.splitlines(keepends=True)
    method_lines = lines[method.lineno - 1:method.end_lineno]
    if len(method_lines) < 2:
        return ""
    copied_method = copy.deepcopy(method)
    copied_method.args.args = [
        argument for argument in copied_method.args.args
        if argument.arg != destination_parameter
    ]
    copied_method.body = [ast.Pass()]
    copied_method.decorator_list = []
    try:
        header = ast.unparse(ast.fix_missing_locations(copied_method)).splitlines()[0]
    except Exception:
        return ""

    source_indent = " " * method.col_offset
    body_indent = f"{source_indent}    "
    body = "".join(method_lines[1:])
    dedented_body_lines = [
        line[len(body_indent):] if line.strip() and line.startswith(body_indent) else line
        for line in body.splitlines(keepends=True)
    ]
    try:
        rewritten_body = cst.parse_module("".join(dedented_body_lines)).visit(
            _MoveMethodNameRewriter(destination_parameter)
        ).code
    except Exception:
        return ""
    indented_body = textwrap.indent(rewritten_body.rstrip("\r\n"), f"{destination_indent}    ")
    return f"{destination_indent}{header}\n{indented_body}"


def _move_method_is_descendant(
    node: ast.AST,
    ancestor: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _move_method_line_offsets(source_code: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source_code):
        offsets.append(match.end())
    return offsets


def _move_method_position_offset(
    line_offsets: Sequence[int],
    line: int | None,
    column: int | None,
) -> int:
    if not isinstance(line, int) or not isinstance(column, int) or line <= 0:
        return -1
    if line > len(line_offsets):
        return -1
    return line_offsets[line - 1] + column


def _move_method_line_end_offset(
    source_code: str,
    line_offsets: Sequence[int],
    line: int | None,
) -> int:
    if not isinstance(line, int) or line <= 0:
        return -1
    return line_offsets[line] if line < len(line_offsets) else len(source_code)


def _move_method_line_content_end_offset(
    source_code: str,
    line_offsets: Sequence[int],
    line: int | None,
) -> int:
    line_start = _move_method_position_offset(line_offsets, line, 0)
    line_end = _move_method_line_end_offset(source_code, line_offsets, line)
    if line_start < 0 or line_end < line_start:
        return -1
    while line_end > line_start and source_code[line_end - 1] in "\r\n":
        line_end -= 1
    return line_end


def _move_method_edits_do_not_overlap(edits: Sequence[tuple[int, int, str]]) -> bool:
    ordered = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    previous_end = -1
    for start, end, _ in ordered:
        if start < 0 or end < start or start < previous_end:
            return False
        previous_end = max(previous_end, end)
    return True


def _apply_move_method_edits(
    source_code: str,
    edits: Sequence[tuple[int, int, str]],
) -> str:
    transformed = source_code
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    return transformed


class _InlineClassFieldRewriter(cst.CSTTransformer):
    def __init__(self, field_names: set[str]) -> None:
        self.field_names = field_names

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if (
            isinstance(original_node.value, cst.Name)
            and original_node.value.value == "self"
            and original_node.attr.value in self.field_names
        ):
            return cst.Name(original_node.attr.value)
        return updated_node


class _InlineClassOwnerFieldRewriter(cst.CSTTransformer):
    def __init__(self, field_mapping: dict[str, str]) -> None:
        self.field_mapping = field_mapping

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if (
            isinstance(original_node.value, cst.Name)
            and original_node.value.value == "self"
            and original_node.attr.value in self.field_mapping
        ):
            return cst.Attribute(
                value=cst.Name("self"),
                attr=cst.Name(self.field_mapping[original_node.attr.value]),
            )
        return updated_node


def resolve_inline_class_target(
    source_code: str,
    *,
    class_to_inline: str = "",
) -> dict[str, Any]:
    """Resolve an Inline Class target from explicit data or owner usage."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}
    # Explicit RDP targets are resolved by qualified class identity first.
    # This accepts ``Class``, ``Outer.Inner`` and module-qualified forms while
    # refusing ambiguous short names instead of silently selecting a top-level
    # class with the same spelling.
    from . import python_inline_class

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    class_names = {node.name for node in classes}
    requested = str(class_to_inline or "").strip()
    explicit = python_inline_class.resolve_python_inline_class_target(
        source_code,
        class_to_inline=requested,
    ) if requested else {}
    if requested and explicit.get("status") == "success":
        result = {
            "status": "success",
            "class_to_inline": str(explicit["class_to_inline"]),
            "strategy": "explicit_plan_target",
            "target_resolution": "explicit_plan_target",
        }
        # Retain the established compact response for top-level classes while
        # carrying qualified evidence when a nested/module-qualified target
        # actually needs it.
        if "." in str(explicit.get("class_to_inline") or "") or "." in requested:
            result["qualified_class_name"] = str(
                explicit.get("qualified_class_name") or ""
            )
            result["class_model"] = dict(explicit.get("class_model") or {})
        return result
    if requested and explicit.get("status") == "review_required":
        return {
            "status": "review_required",
            "reason": str(explicit.get("reason") or "DUPLICATE_EXPLICIT_CLASS_TARGET"),
            "target_resolution": "explicit_plan_target_ambiguous",
        }

    candidates: set[str] = set()
    for owner in classes:
        for node in ast.walk(owner):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "self"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in class_names
                and node.value.func.id != owner.name
            ):
                continue
            candidates.add(node.value.func.id)
    if len(candidates) != 1:
        return {
            "status": "not_applicable" if not candidates else "review_required",
            "reason": (
                "TARGET_CLASS_NOT_FOUND"
                if requested and not candidates
                else (
                    "INLINE_CLASS_TARGET_NOT_FOUND"
                    if not candidates
                    else "AMBIGUOUS_INLINE_CLASS_TARGET"
                )
            ),
            "requested_class_to_inline": requested,
            "target_resolution": "semantic_recovery_failed",
        }
    return {
        "status": "success",
        "class_to_inline": next(iter(candidates)),
        "strategy": "owner_usage_semantic_recovery",
        "target_resolution": "owner_usage_semantic_recovery",
        "requested_class_to_inline": requested,
    }


def apply_inline_class(
    source_code: str,
    *,
    class_to_inline: str,
    prior_transformations: Sequence[dict[str, Any]] | None = None,
    project_source_files: Sequence[dict[str, Any]] | None = None,
    current_file_name: str = "",
) -> Tuple[str, int, dict[str, Any]]:
    """Inline a Python class using the safest supported strategy.

    Supported cases include owned-composition helpers, small module-local
    helpers, empty/transparent local inheritance aliases, and prior-lineage
    cleanup.  Distinct framework/configuration classes are intentionally
    reported as not applicable, while genuinely ambiguous dynamic/inheritance
    cases remain review-required.
    """

    def review(reason: str) -> Tuple[str, int, dict[str, Any]]:
        return source_code, 0, {
            "status": "review_required",
            "reason": reason,
            "class_to_inline": class_to_inline,
        }

    resolution = resolve_inline_class_target(
        source_code,
        class_to_inline=class_to_inline,
    )
    if resolution.get("status") != "success":
        return review(str(resolution.get("reason") or "INLINE_CLASS_TARGET_NOT_FOUND"))
    from . import python_inline_class

    strategy = python_inline_class.select_python_inline_class_strategy(
        source_code,
        class_to_inline=str(resolution["class_to_inline"]),
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
    )
    if strategy.get("status") != "success":
        return source_code, 0, {
            "status": str(strategy.get("status") or "review_required"),
            "reason": str(strategy.get("reason") or "INLINE_CLASS_REVIEW_REQUIRED"),
            "class_to_inline": str(resolution["class_to_inline"]),
            "qualified_class_name": str(strategy.get("qualified_class_name") or ""),
            "strategy": str(strategy.get("strategy") or ""),
            "class_model": dict(strategy.get("class_model") or {}),
            "reference_files": list(strategy.get("reference_files") or []),
        }

    # Direct callers of python_transformers.apply_inline_class must receive the
    # same inheritance support as the TransformationEngine.  Previously only
    # the engine's owned-composition pre-pass could reach the inheritance
    # collapse implementation, so direct calls silently fell back to the old
    # module-function transformer.
    if strategy.get("strategy") == "simple_inheritance_collapse":
        return python_inline_class.apply_owned_inline_class(
            source_code,
            class_to_inline=str(strategy.get("class_name") or resolution["class_to_inline"]),
            project_source_files=project_source_files or [],
            current_file_name=current_file_name,
            prior_transformations=prior_transformations or [],
        )

    class_to_inline = str(strategy.get("class_name") or resolution["class_to_inline"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_to_inline):
        return review("INVALID_CLASS_TARGET")
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return review("SOURCE_PARSE_FAILED")

    classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_to_inline
    ]
    if len(classes) != 1:
        return review("TARGET_CLASS_NOT_FOUND")
    class_node = classes[0]
    # A preceding Move Method can leave its old source class as only a
    # docstring/pass statement.  Resolve that current symbol state before the
    # normal Inline Class strategies: an empty class is not a missing target.
    # It can be removed only when every remaining construction is a dead,
    # side-effect-free local assignment and no other reference remains.
    empty_cleanup = _apply_empty_inline_class_cleanup(
        source_code=source_code,
        tree=tree,
        class_node=class_node,
        class_to_inline=class_to_inline,
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
    )
    if empty_cleanup is not None:
        return empty_cleanup

    owner_result = _apply_inline_class_into_owner(
        source_code=source_code,
        tree=tree,
        class_node=class_node,
        class_to_inline=class_to_inline,
    )
    if owner_result is not None:
        return owner_result

    # Do not inline a class merely because an old RDP plan labelled it Lazy.
    # For example, after ReportPrinter.print_student_report moves to Student,
    # Student owns both meaningful state and behaviour.  The smell has already
    # been resolved by the earlier transformation, so preserving Student is
    # the safe and correct result.
    if (
        _inline_class_has_meaningful_current_responsibility(class_node)
        and _inline_class_was_enriched_by_prior_move(
            class_to_inline,
            prior_transformations or [],
        )
    ):
        return source_code, 0, {
            "status": "satisfied",
            "reason": "SMELL_RESOLVED_BY_PRIOR_REFACTORING",
            "class_to_inline": class_to_inline,
            "plan_compliance": "SATISFIED_BY_PRIOR_REFACTORING",
            "current_symbol_state": "meaningful_state_and_behavior",
            "prior_transformations": list(prior_transformations or []),
        }

    methods = [node for node in class_node.body if isinstance(node, ast.FunctionDef)]
    constructors = [node for node in methods if node.name == "__init__"]
    business_methods = [node for node in methods if node.name != "__init__"]
    if len(constructors) > 1 or not business_methods or len(business_methods) > 2:
        return review("CLASS_RESPONSIBILITY_NOT_SMALL")
    allowed_members = set(methods)
    for member in class_node.body:
        if member in allowed_members:
            continue
        if (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        ):
            continue
        return review("CLASS_STATE_OR_MEMBER_UNSUPPORTED")

    field_values, init_error = _inline_class_constructor_fields(
        constructors[0] if constructors else None
    )
    if init_error:
        return review(init_error)
    field_names = set(field_values)
    for method in business_methods:
        error = _inline_class_method_safety_error(method, field_names)
        if error:
            return review(error)
        method_argument_names = {argument.arg for argument in method.args.args[1:]}
        if method_argument_names & field_names:
            return review("FIELD_AND_METHOD_PARAMETER_NAME_COLLISION")

    top_level_function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    method_names = {method.name for method in business_methods}
    if top_level_function_names & method_names:
        return review("MODULE_FUNCTION_NAME_COLLISION")

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    constructions, direct_calls, usage_error = _inline_class_collect_usages(
        tree=tree,
        parents=parents,
        class_node=class_node,
        class_name=class_to_inline,
        method_names=method_names,
        field_names=field_names,
    )
    if usage_error:
        return review(usage_error)

    rendered_functions = [
        _render_inlined_python_function(
            source_code=source_code,
            method=method,
            field_names=field_names,
        )
        for method in business_methods
    ]
    if not all(rendered_functions):
        return review("METHOD_RENDER_FAILED")

    line_offsets = _move_method_line_offsets(source_code)
    edits: list[tuple[int, int, str]] = []
    class_start = line_offsets[class_node.lineno - 1]
    class_end = _move_method_line_end_offset(source_code, line_offsets, class_node.end_lineno)
    edits.append((class_start, class_end, "\n\n".join(rendered_functions).rstrip() + "\n\n"))

    for construction in constructions:
        statement = construction["statement"]
        instance_name = construction["instance_name"]
        replacement = _inline_class_constructor_replacement(
            indent=" " * statement.col_offset,
            instance_name=instance_name,
            field_values=field_values,
        )
        edits.append((
            line_offsets[statement.lineno - 1],
            _move_method_line_end_offset(source_code, line_offsets, statement.end_lineno),
            replacement,
        ))

    for call in direct_calls:
        replacement = _inline_class_render_call(
            call=call["call"],
            method_name=call["method_name"],
            field_arguments=call["field_arguments"],
        )
        if not replacement:
            return review("CALL_SITE_REWRITE_FAILED")
        node = call["call"]
        edits.append((
            _move_method_position_offset(line_offsets, node.lineno, node.col_offset),
            _move_method_position_offset(line_offsets, node.end_lineno, node.end_col_offset),
            replacement,
        ))

    for field_access in _inline_class_field_accesses(
        tree=tree,
        parents=parents,
        class_node=class_node,
        constructions=constructions,
        field_names=field_names,
    ):
        attribute = field_access["attribute"]
        edits.append((
            _move_method_position_offset(line_offsets, attribute.lineno, attribute.col_offset),
            _move_method_position_offset(line_offsets, attribute.end_lineno, attribute.end_col_offset),
            field_access["replacement"],
        ))

    if not _move_method_edits_do_not_overlap(edits):
        return review("OVERLAPPING_INLINE_EDITS")
    transformed = _apply_move_method_edits(source_code, edits)
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-inline-class>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return review("TRANSFORMED_SOURCE_PARSE_FAILED")

    return transformed, len(edits), {
        "status": "success",
        "class_to_inline": class_to_inline,
        "inlined_methods": [method.name for method in business_methods],
        "inlined_fields": sorted(field_names),
        "removed_instantiations": len(constructions),
        "updated_call_sites": len(direct_calls),
    }


def _apply_empty_inline_class_cleanup(
    *,
    source_code: str,
    tree: ast.Module,
    class_node: ast.ClassDef,
    class_to_inline: str,
    project_source_files: Sequence[dict[str, Any]],
    current_file_name: str,
) -> Tuple[str, int, dict[str, Any]] | None:
    """Remove an empty class only after proving it has no live dependency.

    Returning ``None`` means this is not an empty-class cleanup candidate and
    allows the regular Inline Class strategies to continue.  Any live or
    dynamic reference returns ``review_required`` rather than removing a
    class that callers could still rely on.
    """

    meaningful_body = _inline_class_non_docstring_body(class_node)
    if any(not isinstance(statement, ast.Pass) for statement in meaningful_body):
        return None

    repository_reference = _inline_class_repository_reference(
        project_source_files=project_source_files,
        current_file_name=current_file_name,
        class_name=class_to_inline,
    )
    if repository_reference:
        return source_code, 0, {
            "status": "review_required",
            "reason": "EXTERNAL_REPOSITORY_REFERENCE",
            "class_to_inline": class_to_inline,
            "reference_file": repository_reference,
            "current_symbol_state": "empty_with_external_reference",
        }

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    constructions: list[ast.Assign] = []
    construction_targets: set[ast.Name] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or node.id != class_to_inline:
            continue
        if _move_method_is_descendant(node, class_node, parents):
            continue
        parent = parents.get(node)
        grandparent = parents.get(parent) if parent is not None else None
        if (
            isinstance(parent, ast.Call)
            and parent.func is node
            and not parent.args
            and not parent.keywords
            and isinstance(grandparent, ast.Assign)
            and grandparent.value is parent
            and len(grandparent.targets) == 1
            and isinstance(grandparent.targets[0], ast.Name)
        ):
            constructions.append(grandparent)
            construction_targets.add(grandparent.targets[0])
            continue
        return source_code, 0, {
            "status": "review_required",
            "reason": "EMPTY_CLASS_HAS_LIVE_OR_DYNAMIC_REFERENCE",
            "class_to_inline": class_to_inline,
            "current_symbol_state": "empty_with_live_reference",
        }

    # Each permitted construction must itself be dead.  This protects normal
    # uses such as ``printer = ReportPrinter(); printer.configure()`` and also
    # catches reflection, annotations, imports, isinstance and subclassing as
    # class references in the loop above.
    for target in construction_targets:
        if any(
            isinstance(node, ast.Name)
            and node.id == target.id
            and node is not target
            and not _move_method_is_descendant(node, class_node, parents)
            for node in ast.walk(tree)
        ):
            return source_code, 0, {
                "status": "review_required",
                "reason": "EMPTY_CLASS_INSTANCE_STILL_LIVE",
                "class_to_inline": class_to_inline,
                "current_symbol_state": "empty_with_live_instance",
            }

    line_offsets = _move_method_line_offsets(source_code)
    edits: list[tuple[int, int, str]] = [(
        line_offsets[class_node.lineno - 1],
        _move_method_line_end_offset(source_code, line_offsets, class_node.end_lineno),
        "",
    )]
    for construction in constructions:
        edits.append((
            line_offsets[construction.lineno - 1],
            _move_method_line_end_offset(source_code, line_offsets, construction.end_lineno),
            "",
        ))
    if not _move_method_edits_do_not_overlap(edits):
        return source_code, 0, {
            "status": "review_required",
            "reason": "OVERLAPPING_EMPTY_CLASS_CLEANUP_EDITS",
            "class_to_inline": class_to_inline,
        }
    transformed = _apply_move_method_edits(source_code, edits)
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-empty-inline-class>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return source_code, 0, {
            "status": "review_required",
            "reason": "TRANSFORMED_SOURCE_PARSE_FAILED",
            "class_to_inline": class_to_inline,
        }
    return transformed, len(edits), {
        "status": "success",
        "reason": "SAFE_EMPTY_CLASS_REMOVAL",
        "class_to_inline": class_to_inline,
        "strategy": "empty_class_cleanup_after_prior_refactoring",
        "inline_mode": "empty_class_cleanup",
        "class_was_empty": True,
        "class_removed": True,
        "references_updated": True,
        "removed_instantiations": len(constructions),
        "updated_call_sites": 0,
        "current_symbol_state": "empty_unused_removed",
        "plan_compliance": "PASS",
        "plan_compliance_reason": "SAFE_EMPTY_CLASS_REMOVAL",
    }


def _inline_class_repository_reference(
    *,
    project_source_files: Sequence[dict[str, Any]],
    current_file_name: str,
    class_name: str,
) -> str:
    """Return the first other Python file that may depend on the class."""

    current_path = str(current_file_name or "").replace("\\", "/").lower()
    for item in project_source_files:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or item.get("name") or "")
        normalized = file_name.replace("\\", "/").lower()
        if current_path and normalized == current_path:
            continue
        language = str(item.get("language") or "").strip().lower()
        if language and language != "python" and not normalized.endswith(".py"):
            continue
        source = item.get("source_code")
        if not isinstance(source, str) or not source.strip():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # An unparsable repository peer cannot provide a safe absence
            # proof, so preserve the class for review.
            return file_name or "<unparsed-python-source>"

        local_definition = any(
            isinstance(node, ast.ClassDef) and node.name == class_name
            for node in tree.body
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == class_name for alias in node.names
            ):
                return file_name
            if isinstance(node, ast.Attribute) and node.attr == class_name:
                return file_name
            if (
                isinstance(node, ast.Name)
                and node.id == class_name
                and not local_definition
            ):
                return file_name
    return ""


def build_python_symbol_table(source_code: str) -> dict[str, Any]:
    """Build a compact symbol table from the current Python AST."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "parse_failed", "classes": {}}

    instantiations = Counter(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    method_calls = Counter(
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )
    classes: dict[str, Any] = {}
    for class_node in (
        node for node in tree.body if isinstance(node, ast.ClassDef)
    ):
        methods = [
            node.name
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        instance_fields = sorted({
            node.attr
            for node in ast.walk(class_node)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "cls"}
            and isinstance(node.ctx, ast.Store)
        })
        class_fields = sorted({
            target.id
            for statement in class_node.body
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            for target in (
                statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            )
            if isinstance(target, ast.Name)
        })
        meaningful_body = _inline_class_non_docstring_body(class_node)
        classes[class_node.name] = {
            "methods": methods,
            "instance_fields": instance_fields,
            "class_fields": class_fields,
            "empty": not meaningful_body or all(
                isinstance(statement, ast.Pass) for statement in meaningful_body
            ),
            "instantiations": int(instantiations.get(class_node.name, 0)),
            "bases": [ast.unparse(base) for base in class_node.bases],
        }
    return {
        "status": "success",
        "classes": classes,
        "method_call_counts": dict(method_calls),
    }


def _inline_class_non_docstring_body(class_node: ast.ClassDef) -> list[ast.stmt]:
    body = list(class_node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _inline_class_has_meaningful_current_responsibility(
    class_node: ast.ClassDef,
) -> bool:
    constructor = next(
        (
            node for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        ),
        None,
    )
    business_methods = [
        node for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name != "__init__"
    ]
    owns_state = bool(constructor) and any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(constructor)
    )
    has_real_behavior = any(
        _inline_class_method_has_real_logic(method)
        for method in business_methods
    )
    return owns_state and has_real_behavior


def _inline_class_was_enriched_by_prior_move(
    class_to_inline: str,
    prior_transformations: Sequence[dict[str, Any]],
) -> bool:
    """Return true only when this class gained behaviour in this run."""

    for item in prior_transformations:
        if not isinstance(item, dict):
            continue
        if str(item.get("action_type") or "") != "move_python_method":
            continue
        if str(item.get("status") or "").lower() not in {"success", "already_applied"}:
            continue
        if str(item.get("destination_class") or "") == class_to_inline:
            return True
    return False


def _inline_class_method_has_real_logic(method: ast.FunctionDef) -> bool:
    body = _inline_class_non_docstring_body(method)
    return bool(body) and not all(
        isinstance(statement, ast.Pass)
        or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis
        )
        for statement in body
    )


def _apply_inline_class_into_owner(
    *,
    source_code: str,
    tree: ast.Module,
    class_node: ast.ClassDef,
    class_to_inline: str,
) -> Tuple[str, int, dict[str, Any]] | None:
    """Inline a tiny helper stored as ``self.attribute`` of one owner class."""

    methods = [node for node in class_node.body if isinstance(node, ast.FunctionDef)]
    constructors = [node for node in methods if node.name == "__init__"]
    business_methods = [node for node in methods if node.name != "__init__"]
    if len(constructors) != 1 or not business_methods or len(business_methods) > 2:
        return None
    field_parameters, field_error = _inline_owner_constructor_field_parameters(constructors[0])
    if field_error:
        return None
    field_names = set(field_parameters)
    for method in business_methods:
        if _inline_class_method_safety_error(method, field_names):
            return None

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    ownerships: list[dict[str, Any]] = []
    for owner in (node for node in tree.body if isinstance(node, ast.ClassDef) and node is not class_node):
        if owner.bases or owner.keywords or owner.decorator_list:
            continue
        owner_constructor = next(
            (node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"),
            None,
        )
        if owner_constructor is None:
            continue
        for statement in owner_constructor.body:
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Attribute)
                and isinstance(statement.targets[0].value, ast.Name)
                and statement.targets[0].value.id == "self"
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == class_to_inline
            ):
                continue
            if statement.value.keywords or len(statement.value.args) != len(field_parameters):
                return None
            field_values = {
                field: ast.unparse(statement.value.args[index])
                for index, field in enumerate(field_parameters)
            }
            ownerships.append({
                "owner": owner,
                "constructor_assignment": statement,
                "owner_attribute": statement.targets[0].attr,
                "field_values": field_values,
            })
    if len(ownerships) != 1:
        return None
    ownership = ownerships[0]
    owner = ownership["owner"]
    owner_attribute = str(ownership["owner_attribute"])
    field_mapping = {field: field for field in field_parameters}
    owner_method_names = {
        node.name for node in owner.body if isinstance(node, ast.FunctionDef)
    }
    moved_method_names = {method.name for method in business_methods}
    if owner_method_names & moved_method_names:
        return None
    if _inline_owner_has_field_collisions(
        owner=owner,
        owner_attribute=owner_attribute,
        field_names=field_names,
    ):
        return None

    usages, usage_error = _inline_owner_usages(
        tree=tree,
        parents=parents,
        class_node=class_node,
        owner=owner,
        class_name=class_to_inline,
        owner_attribute=owner_attribute,
        method_names=moved_method_names,
        field_names=field_names,
    )
    if usage_error:
        return source_code, 0, {
            "status": "review_required",
            "reason": usage_error,
            "class_to_inline": class_to_inline,
        }

    rendered_methods = [
        _render_inlined_owner_method(
            source_code=source_code,
            method=method,
            field_mapping=field_mapping,
            destination_indent=" " * (owner.col_offset + 4),
        )
        for method in business_methods
    ]
    if not all(rendered_methods):
        return source_code, 0, {
            "status": "review_required",
            "reason": "OWNER_METHOD_RENDER_FAILED",
            "class_to_inline": class_to_inline,
        }

    line_offsets = _move_method_line_offsets(source_code)
    edits: list[tuple[int, int, str]] = [(
        line_offsets[class_node.lineno - 1],
        _move_method_line_end_offset(source_code, line_offsets, class_node.end_lineno),
        "",
    )]
    assignment = ownership["constructor_assignment"]
    assignment_indent = " " * assignment.col_offset
    constructor_replacement = "".join(
        f"{assignment_indent}self.{field} = {value}\n"
        for field, value in ownership["field_values"].items()
    )
    edits.append((
        line_offsets[assignment.lineno - 1],
        _move_method_line_end_offset(source_code, line_offsets, assignment.end_lineno),
        constructor_replacement,
    ))
    owner_last_body = owner.body[-1] if owner.body else None
    if owner_last_body is None:
        return source_code, 0, {
            "status": "review_required",
            "reason": "OWNER_CLASS_BODY_NOT_FOUND",
            "class_to_inline": class_to_inline,
        }
    rendered_methods_block = "\n\n".join(rendered_methods)
    edits.append((
        _move_method_line_content_end_offset(source_code, line_offsets, owner_last_body.end_lineno),
        _move_method_line_content_end_offset(source_code, line_offsets, owner_last_body.end_lineno),
        f"\n\n{rendered_methods_block}\n",
    ))
    for usage in usages:
        node = usage["node"]
        edits.append((
            _move_method_position_offset(line_offsets, node.lineno, node.col_offset),
            _move_method_position_offset(line_offsets, node.end_lineno, node.end_col_offset),
            usage["replacement"],
        ))
    if not _move_method_edits_do_not_overlap(edits):
        return source_code, 0, {
            "status": "review_required",
            "reason": "OVERLAPPING_OWNER_INLINE_EDITS",
            "class_to_inline": class_to_inline,
        }
    transformed = _apply_move_method_edits(source_code, edits)
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-inline-owner-class>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return source_code, 0, {
            "status": "review_required",
            "reason": "TRANSFORMED_SOURCE_PARSE_FAILED",
            "class_to_inline": class_to_inline,
        }
    return transformed, len(edits), {
        "status": "success",
        "class_to_inline": class_to_inline,
        "strategy": "inline_into_owner_class",
        "destination_class": owner.name,
        "destination_attribute": owner_attribute,
        "inlined_methods": sorted(moved_method_names),
        "inlined_fields": sorted(field_names),
        "updated_call_sites": len(usages),
    }


def _inline_owner_constructor_field_parameters(
    constructor: ast.FunctionDef,
) -> tuple[list[str], str]:
    """Return owner-constructor field mappings for Inline Class.

    A constructor containing only a docstring and/or ``pass`` is a valid
    stateless constructor.  Older logic required at least one argument after
    ``self`` and treated ``pass`` as executable state, which incorrectly
    rejected harmless lazy classes such as ``Main`` and ``Database``.
    """

    if (
        constructor.decorator_list
        or constructor.args.posonlyargs
        or constructor.args.vararg
        or constructor.args.kwonlyargs
        or len(constructor.args.args) < 1
        or constructor.args.args[0].arg != "self"
    ):
        return [], "CONSTRUCTOR_SIGNATURE_UNSUPPORTED"

    parameters = [argument.arg for argument in constructor.args.args[1:]]
    fields: list[str] = []
    body = list(constructor.body)

    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    # ``pass`` carries no state and no side effect.  Ignore it when deciding
    # whether the constructor is safe to inline.
    body = [statement for statement in body if not isinstance(statement, ast.Pass)]

    if not parameters and not body:
        return [], ""

    for statement in body:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name)
            and statement.targets[0].value.id == "self"
            and isinstance(statement.value, ast.Name)
            and statement.value.id in parameters
        ):
            return [], "CONSTRUCTOR_STATE_UNSUPPORTED"
        fields.append(statement.targets[0].attr)

    if len(fields) != len(parameters) or len(set(fields)) != len(fields):
        return [], "CONSTRUCTOR_FIELD_MAPPING_UNSUPPORTED"
    return fields, ""


def _inline_owner_has_field_collisions(
    *,
    owner: ast.ClassDef,
    owner_attribute: str,
    field_names: set[str],
) -> bool:
    for node in ast.walk(owner):
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in field_names
        ):
            continue
        if node.attr != owner_attribute:
            return True
    return False


def _inline_owner_usages(
    *,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    class_node: ast.ClassDef,
    owner: ast.ClassDef,
    class_name: str,
    owner_attribute: str,
    method_names: set[str],
    field_names: set[str],
) -> tuple[list[dict[str, Any]], str]:
    usages: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if _move_method_is_descendant(node, class_node, parents):
            continue
        if isinstance(node, ast.Name) and node.id == class_name:
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and parent.func is node:
                grandparent = parents.get(parent)
                if isinstance(grandparent, ast.Assign) and grandparent.value is parent:
                    continue
            return [], "DYNAMIC_OR_EXTERNAL_CLASS_REFERENCE"
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == owner_attribute
        ):
            continue
        current: ast.AST | None = node
        inside_owner = False
        while current is not None:
            if current is owner:
                inside_owner = True
                break
            current = parents.get(current)
        if not inside_owner:
            return [], "EXTERNAL_OWNER_REFERENCE_UNSUPPORTED"
        parent = parents.get(node)
        if node.attr in method_names and isinstance(parent, ast.Call) and parent.func is node:
            rewritten = ast.Call(
                func=ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr=node.attr, ctx=ast.Load()),
                args=[copy.deepcopy(argument) for argument in parent.args],
                keywords=[copy.deepcopy(keyword) for keyword in parent.keywords],
            )
            usages.append({"node": parent, "replacement": ast.unparse(ast.fix_missing_locations(rewritten))})
        elif node.attr in field_names and isinstance(node.ctx, ast.Load):
            usages.append({"node": node, "replacement": f"self.{node.attr}"})
        else:
            return [], "UNRESOLVED_OWNER_ATTRIBUTE_REFERENCE"
    return usages, ""


def _render_inlined_owner_method(
    *,
    source_code: str,
    method: ast.FunctionDef,
    field_mapping: dict[str, str],
    destination_indent: str,
) -> str:
    lines = source_code.splitlines(keepends=True)
    method_lines = lines[method.lineno - 1:method.end_lineno]
    if len(method_lines) < 2:
        return ""
    copied = copy.deepcopy(method)
    copied.body = [ast.Pass()]
    copied.decorator_list = []
    try:
        header = ast.unparse(ast.fix_missing_locations(copied)).splitlines()[0]
    except Exception:
        return ""
    source_indent = " " * method.col_offset
    body_indent = f"{source_indent}    "
    body = "".join(method_lines[1:])
    dedented = "".join(
        line[len(body_indent):] if line.strip() and line.startswith(body_indent) else line
        for line in body.splitlines(keepends=True)
    )
    try:
        rewritten = cst.parse_module(dedented).visit(
            _InlineClassOwnerFieldRewriter(field_mapping)
        ).code
    except Exception:
        return ""
    return f"{destination_indent}{header}\n{textwrap.indent(rewritten.rstrip(), f'{destination_indent}    ')}"


def _inline_class_constructor_fields(
    constructor: ast.FunctionDef | None,
) -> tuple[dict[str, str], str]:
    """Extract safe literal constructor state for module-function Inline Class.

    ``__init__(self): pass`` is intentionally treated as an empty/stateless
    constructor.  Complex statements, calls, control flow, or non-literal
    field values remain review-required through the existing error codes.
    """

    if constructor is None:
        return {}, ""
    if (
        constructor.decorator_list
        or constructor.args.posonlyargs
        or constructor.args.vararg
        or constructor.args.kwonlyargs
        or len(constructor.args.args) != 1
        or constructor.args.args[0].arg != "self"
    ):
        return {}, "CONSTRUCTOR_SIGNATURE_UNSUPPORTED"

    fields: dict[str, str] = {}
    body = list(constructor.body)

    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    # ``pass`` is syntactic filler only; it does not represent constructor
    # state and must not cause CONSTRUCTOR_STATE_UNSUPPORTED.
    body = [statement for statement in body if not isinstance(statement, ast.Pass)]

    if not body:
        return {}, ""

    for statement in body:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name)
            and statement.targets[0].value.id == "self"
        ):
            return {}, "CONSTRUCTOR_STATE_UNSUPPORTED"
        try:
            ast.literal_eval(statement.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return {}, "CONSTRUCTOR_FIELD_VALUE_UNSUPPORTED"
        field_name = statement.targets[0].attr
        if field_name in fields:
            return {}, "DUPLICATE_CONSTRUCTOR_FIELD"
        fields[field_name] = ast.unparse(statement.value)
    return fields, ""


def _inline_class_method_safety_error(
    method: ast.FunctionDef,
    field_names: set[str],
) -> str:
    if (
        method.decorator_list
        or method.args.posonlyargs
        or method.args.vararg
        or not method.args.args
        or method.args.args[0].arg != "self"
    ):
        return "METHOD_SIGNATURE_UNSUPPORTED"
    parents = {
        child: parent
        for parent in ast.walk(method)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(method):
        if node is not method and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return "NESTED_SCOPE_UNSUPPORTED"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if node.attr not in field_names or not isinstance(node.ctx, ast.Load):
                return "SOURCE_INSTANCE_DEPENDENCY"
        if isinstance(node, ast.Name) and node.id == "self":
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in field_names
                and isinstance(parent.ctx, ast.Load)
            ):
                return "SOURCE_INSTANCE_DEPENDENCY"
    return ""


def _inline_class_collect_usages(
    *,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    class_node: ast.ClassDef,
    class_name: str,
    method_names: set[str],
    field_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    constructions: list[dict[str, Any]] = []
    direct_calls: list[dict[str, Any]] = []
    instances: dict[str, dict[str, str]] = {}

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == class_name
        ):
            continue
        if node.args or node.keywords:
            return [], [], "CONSTRUCTOR_CALL_ARGUMENTS_UNSUPPORTED"
        parent = parents.get(node)
        if isinstance(parent, ast.Assign) and parent.value is node and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            instance_name = parent.targets[0].id
            instances[instance_name] = {
                field: f"{instance_name}_{field}" for field in field_names
            }
            constructions.append({"statement": parent, "instance_name": instance_name})
            continue
        if isinstance(parent, ast.Attribute) and parent.value is node:
            call_parent = parents.get(parent)
            if (
                not field_names
                and parent.attr in method_names
                and isinstance(call_parent, ast.Call)
                and call_parent.func is parent
            ):
                direct_calls.append({
                    "call": call_parent,
                    "method_name": parent.attr,
                    "field_arguments": [],
                })
                continue
        return [], [], "UNRESOLVED_CLASS_CONSTRUCTION"

    for node in ast.walk(tree):
        if _move_method_is_descendant(node, class_node, parents):
            continue
        if isinstance(node, ast.Name) and node.id == class_name:
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and parent.func is node:
                continue
            return [], [], "DYNAMIC_OR_EXTERNAL_CLASS_REFERENCE"
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        instance_name = node.value.id
        if instance_name not in instances:
            continue
        parent = parents.get(node)
        if node.attr in method_names and isinstance(parent, ast.Call) and parent.func is node:
            direct_calls.append({
                "call": parent,
                "method_name": node.attr,
                "field_arguments": list(instances[instance_name].values()),
            })
        elif node.attr in field_names and isinstance(node.ctx, ast.Load):
            continue
        else:
            return [], [], "UNRESOLVED_INSTANCE_REFERENCE"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or node.id not in instances:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Assign) and node in parent.targets:
            continue
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        return [], [], "UNRESOLVED_INSTANCE_REFERENCE"
    return constructions, direct_calls, ""


def _render_inlined_python_function(
    *,
    source_code: str,
    method: ast.FunctionDef,
    field_names: set[str],
) -> str:
    lines = source_code.splitlines(keepends=True)
    method_lines = lines[method.lineno - 1:method.end_lineno]
    if len(method_lines) < 2:
        return ""
    copied = copy.deepcopy(method)
    copied.args.args = [
        *(ast.arg(arg=name) for name in sorted(field_names)),
        *copied.args.args[1:],
    ]
    copied.body = [ast.Pass()]
    copied.decorator_list = []
    try:
        header = ast.unparse(ast.fix_missing_locations(copied)).splitlines()[0]
    except Exception:
        return ""
    source_indent = " " * method.col_offset
    body_indent = f"{source_indent}    "
    body = "".join(method_lines[1:])
    dedented = "".join(
        line[len(body_indent):] if line.strip() and line.startswith(body_indent) else line
        for line in body.splitlines(keepends=True)
    )
    try:
        rewritten = cst.parse_module(dedented).visit(
            _InlineClassFieldRewriter(field_names)
        ).code
    except Exception:
        return ""
    return f"{header}\n{textwrap.indent(rewritten.rstrip(), '    ')}"


def _inline_class_constructor_replacement(
    *,
    indent: str,
    instance_name: str,
    field_values: dict[str, str],
) -> str:
    if not field_values:
        return ""
    return "".join(
        f"{indent}{instance_name}_{field} = {value}\n"
        for field, value in sorted(field_values.items())
    )


def _inline_class_render_call(
    *,
    call: ast.Call,
    method_name: str,
    field_arguments: Sequence[str],
) -> str:
    rewritten = ast.Call(
        func=ast.Name(id=method_name, ctx=ast.Load()),
        args=[
            *(ast.Name(id=name, ctx=ast.Load()) for name in field_arguments),
            *(copy.deepcopy(argument) for argument in call.args),
        ],
        keywords=[copy.deepcopy(keyword) for keyword in call.keywords],
    )
    try:
        return ast.unparse(ast.fix_missing_locations(rewritten))
    except Exception:
        return ""


def _inline_class_field_accesses(
    *,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    class_node: ast.ClassDef,
    constructions: Sequence[dict[str, Any]],
    field_names: set[str],
) -> list[dict[str, Any]]:
    instance_fields = {
        item["instance_name"]: {
            field: f"{item['instance_name']}_{field}" for field in field_names
        }
        for item in constructions
    }
    accesses: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if _move_method_is_descendant(node, class_node, parents):
            continue
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in instance_fields
            and node.attr in field_names
            and isinstance(node.ctx, ast.Load)
        ):
            continue
        accesses.append({
            "attribute": node,
            "replacement": instance_fields[node.value.id][node.attr],
        })
    return accesses


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
        # RDP may identify the containing method while its line points to a
        # dead statement inside that method. Resolve that statement first;
        # otherwise a live method is incorrectly treated as the dead target.
        if source_line is not None:
            statement_target = _find_python_dead_statement_target(
                tree,
                source_line=source_line,
                method_name=method_name,
                class_name=class_name,
            )
            if statement_target is not None:
                dead_code_kind, target = statement_target
                return dead_code_kind, ast.dump(target, include_attributes=False)

        target = _resolve_python_dead_callable_identity(
            tree,
            method_name=method_name,
            class_name=class_name,
            source_line=source_line,
        )
        if target is not None:
            fingerprint = ast.dump(target, include_attributes=False)
            if _is_proven_unused_python_callable(
                tree,
                target=target,
                method_name=target.name,
            ):
                return "unused_callable", fingerprint
            if _has_dynamic_python_callable_reference(tree, target.name):
                return "dynamic_callable", fingerprint
            # The planner can occasionally label a live method as dead code.
            # Preserve that fact as a semantic anchor so the engine can mark
            # the step NOT_APPLICABLE instead of repeatedly warning that the
            # remover could not prove the target was dead.
            return "live_callable", fingerprint
        recovered = _recover_unique_unused_python_callable(
            tree,
            source_line=source_line,
        )
        if recovered is not None:
            return "unused_callable", ast.dump(recovered, include_attributes=False)
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
        fingerprint = ast.dump(target, include_attributes=False)
        if _is_proven_unused_python_callable(
            tree,
            target=target,
            method_name=target.name,
        ):
            return "unused_callable", fingerprint
        if _has_dynamic_python_callable_reference(tree, target.name):
            return "dynamic_callable", fingerprint
        return "live_callable", fingerprint

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


def analyze_python_dead_code_target(
    source_code: str,
    *,
    method_name: str = "",
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
    dead_code_kind: str = "",
    target_statement_fingerprint: str = "",
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
) -> dict[str, Any]:
    """Prove whether one Python dead-code target is safe to remove.

    This is deliberately conservative.  A requested callable is removable only
    when its identity is unique and no repository AST indicates a reference,
    export, framework hook, inheritance dependency, or dynamic lookup.
    Statement-level targets are accepted only when their deadness is proven by
    local control flow or an unused literal assignment.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "REVIEW_REQUIRED", "reason": "SOURCE_PARSE_FAILED"}

    anchored_kinds = {
        "unused_callable",
        "constant_false_branch",
        "unreachable_after_terminator",
        "unused_literal_assignment",
    }
    if (
        dead_code_kind in anchored_kinds - {"unused_callable"}
        and target_statement_fingerprint
    ):
        return {
            "status": "SAFE_TO_REMOVE",
            "target_kind": dead_code_kind,
            "target_fingerprint": target_statement_fingerprint,
            "deadness_evidence": [f"accepted_ast_anchor:{dead_code_kind}"],
        }

    kind, fingerprint = resolve_dead_code_target(
        source_code,
        method_name=method_name,
        class_name=class_name,
        source_line=source_line,
    )
    if kind in {
        "constant_false_branch",
        "unreachable_after_terminator",
        "unused_literal_assignment",
    }:
        return {
            "status": "SAFE_TO_REMOVE",
            "target_kind": kind,
            "target_fingerprint": fingerprint,
            "deadness_evidence": [f"ast_proven_{kind}"],
        }
    if kind == "dynamic_callable":
        return {
            "status": "REVIEW_REQUIRED",
            "target_kind": kind,
            "target_fingerprint": fingerprint,
            "reason": "DYNAMIC_REFERENCE_DETECTED",
        }
    if kind == "live_callable":
        return {
            "status": "NOT_DEAD",
            "target_kind": kind,
            "target_fingerprint": fingerprint,
            "reason": "LOCAL_REFERENCE_DETECTED",
        }
    if not method_name and source_line is not None:
        # A line-only plan action must not expand into the enclosing function
        # after a prior action shifted source lines.  An internal AST anchor can
        # relocate a proven statement later; without one this action is stale.
        return {"status": "NOT_APPLICABLE", "reason": "STALE_LINE_TARGET"}

    if not method_name and class_name:
        classes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        if len(classes) != 1:
            return {"status": "NOT_APPLICABLE", "reason": "CLASS_TARGET_NOT_FOUND"}
        target_class = classes[0]
        target_fingerprint = ast.dump(target_class, include_attributes=False)
        if target_class.decorator_list:
            return {
                "status": "REVIEW_REQUIRED",
                "target_kind": "unused_class",
                "target_fingerprint": target_fingerprint,
                "reason": "DECORATOR_OR_FRAMEWORK_HOOK",
            }
        repository = _python_dead_code_repository_sources(
            source_code=source_code,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
        references: list[str] = []
        uncertain: list[str] = []
        for file_name, candidate_source in repository:
            try:
                candidate_tree = ast.parse(candidate_source)
            except SyntaxError:
                uncertain.append(f"unparseable_repository_file:{file_name}")
                continue
            candidate_parents = {
                child: parent
                for parent in ast.walk(candidate_tree)
                for child in ast.iter_child_nodes(parent)
            }
            class_in_tree = next(
                (
                    node for node in ast.walk(candidate_tree)
                    if isinstance(node, ast.ClassDef)
                    and ast.dump(node, include_attributes=False) == target_fingerprint
                ),
                None,
            )
            for node in ast.walk(candidate_tree):
                if class_in_tree is not None and _python_ast_descends_from(
                    node, class_in_tree, candidate_parents
                ):
                    continue
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == class_name:
                    references.append(f"class_reference:{file_name}:{getattr(node, 'lineno', 0)}")
                elif isinstance(node, ast.Attribute) and node.attr == class_name:
                    references.append(f"class_attribute_reference:{file_name}:{getattr(node, 'lineno', 0)}")
                elif isinstance(node, ast.ImportFrom) and any(
                    alias.name == class_name or alias.asname == class_name for alias in node.names
                ):
                    references.append(f"class_import_reference:{file_name}:{getattr(node, 'lineno', 0)}")
                elif _python_dead_code_export_contains(node, class_name):
                    references.append(f"class_export_reference:{file_name}:{getattr(node, 'lineno', 0)}")
                elif _python_dead_code_dynamic_lookup(node, class_name):
                    uncertain.append(f"dynamic_class_reference:{file_name}:{getattr(node, 'lineno', 0)}")
                elif isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Name) and base.id == class_name
                    or isinstance(base, ast.Attribute) and base.attr == class_name
                    for base in node.bases
                ):
                    references.append(f"inheritance_reference:{file_name}:{getattr(node, 'lineno', 0)}")
        if references:
            return {
                "status": "NOT_DEAD", "target_kind": "unused_class",
                "target_fingerprint": target_fingerprint,
                "reason": "REPOSITORY_REFERENCE_DETECTED", "deadness_evidence": references,
            }
        if uncertain:
            return {
                "status": "REVIEW_REQUIRED", "target_kind": "unused_class",
                "target_fingerprint": target_fingerprint,
                "reason": "REPOSITORY_DEADNESS_UNCERTAIN", "deadness_evidence": uncertain,
            }
        return {
            "status": "SAFE_TO_REMOVE", "target_kind": "unused_class",
            "target_fingerprint": target_fingerprint,
            "deadness_evidence": ["repository_ast_no_class_references", "no_dynamic_or_export_risk"],
        }

    target = _resolve_python_dead_callable_identity(
        tree,
        method_name=method_name,
        class_name=class_name,
        source_line=source_line,
    )
    if target is None:
        return {"status": "NOT_APPLICABLE", "reason": "TARGET_NOT_FOUND"}

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    owner = _python_callable_class_name(target, parents) or ""
    if target.decorator_list or target.name.startswith(("test_", "pytest_")) or (
        target.name.startswith("__") and target.name.endswith("__")
    ):
        return {
            "status": "REVIEW_REQUIRED",
            "target_kind": "callable",
            "target_fingerprint": ast.dump(target, include_attributes=False),
            "reason": "DECORATOR_OR_FRAMEWORK_HOOK",
        }
    if not owner and target.name.lower() in {"main", "app", "application", "cli", "setup", "teardown"}:
        return {
            "status": "REVIEW_REQUIRED",
            "target_kind": "callable",
            "target_fingerprint": ast.dump(target, include_attributes=False),
            "reason": "ENTRY_POINT_OR_FRAMEWORK_HOOK",
        }

    repository = _python_dead_code_repository_sources(
        source_code=source_code,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )
    reference_evidence: list[str] = []
    uncertain_evidence: list[str] = []
    target_fingerprint = ast.dump(target, include_attributes=False)
    for file_name, candidate_source in repository:
        try:
            candidate_tree = ast.parse(candidate_source)
        except SyntaxError:
            uncertain_evidence.append(f"unparseable_repository_file:{file_name}")
            continue
        candidate_parents = {
            child: parent
            for parent in ast.walk(candidate_tree)
            for child in ast.iter_child_nodes(parent)
        }
        target_in_this_tree = next(
            (
                node for node in ast.walk(candidate_tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and ast.dump(node, include_attributes=False) == target_fingerprint
            ),
            None,
        )
        for node in ast.walk(candidate_tree):
            if target_in_this_tree is not None and _python_ast_descends_from(
                node, target_in_this_tree, candidate_parents
            ):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == target.name:
                reference_evidence.append(f"direct_reference:{file_name}:{getattr(node, 'lineno', 0)}")
            elif isinstance(node, ast.Attribute) and node.attr == target.name:
                reference_evidence.append(f"attribute_reference:{file_name}:{getattr(node, 'lineno', 0)}")
            elif isinstance(node, ast.ImportFrom) and any(
                alias.name == target.name or alias.asname == target.name
                for alias in node.names
            ):
                reference_evidence.append(f"import_reference:{file_name}:{getattr(node, 'lineno', 0)}")
            elif _python_dead_code_export_contains(node, target.name):
                reference_evidence.append(f"export_reference:{file_name}:{getattr(node, 'lineno', 0)}")
            elif _python_dead_code_dynamic_lookup(node, target.name):
                uncertain_evidence.append(f"dynamic_reference:{file_name}:{getattr(node, 'lineno', 0)}")

        if owner and _python_dead_code_has_inheritance_risk(candidate_tree, owner, target.name):
            uncertain_evidence.append(f"inheritance_or_override:{file_name}")
        if owner and any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == owner
            for node in ast.walk(candidate_tree)
        ):
            # An instance can invoke the method dynamically, so exact method
            # reachability cannot be proven from the imported snapshot.
            uncertain_evidence.append(f"class_instantiation:{file_name}")

    if reference_evidence:
        return {
            "status": "NOT_DEAD",
            "target_kind": "unused_callable",
            "target_role": "method" if owner else "function",
            "target_fingerprint": target_fingerprint,
            "owner": owner,
            "reason": "REPOSITORY_REFERENCE_DETECTED",
            "deadness_evidence": reference_evidence,
        }
    if uncertain_evidence:
        return {
            "status": "REVIEW_REQUIRED",
            "target_kind": "unused_callable",
            "target_role": "method" if owner else "function",
            "target_fingerprint": target_fingerprint,
            "owner": owner,
            "reason": "REPOSITORY_DEADNESS_UNCERTAIN",
            "deadness_evidence": uncertain_evidence,
        }
    return {
        "status": "SAFE_TO_REMOVE",
        "target_kind": "unused_callable",
        "target_role": "method" if owner else "function",
        "target_fingerprint": target_fingerprint,
        "owner": owner,
        "deadness_evidence": ["repository_ast_no_references", "no_dynamic_or_export_risk"],
    }


def _python_dead_code_repository_sources(
    *,
    source_code: str,
    project_source_files: Sequence[Any] | None,
    current_file_name: str,
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = [(current_file_name or "<current>", source_code)]
    normalized_current = str(current_file_name or "").replace("\\", "/").lower()
    for item in project_source_files or []:
        if isinstance(item, dict):
            file_name = str(item.get("file_name") or item.get("name") or item.get("path") or "")
            code = str(item.get("source_code") or item.get("code") or "")
        else:
            file_name = str(getattr(item, "file_name", "") or getattr(item, "name", ""))
            code = str(getattr(item, "source_code", "") or getattr(item, "code", ""))
        normalized_name = file_name.replace("\\", "/").lower()
        if not code or (normalized_current and normalized_name == normalized_current):
            continue
        if file_name.lower().endswith(".py"):
            sources.append((file_name, code))
    return sources


def resolve_applied_dead_code_identity(
    before_code: str,
    after_code: str,
    *,
    dead_code_kind: str,
) -> str:
    """Return the exact pre-action AST statement removed by one safe action."""
    try:
        before_tree = ast.parse(before_code)
        after_tree = ast.parse(after_code)
    except SyntaxError:
        return ""
    after_dumps = {
        ast.dump(node, include_attributes=False)
        for node in ast.walk(after_tree)
        if isinstance(node, ast.stmt)
    }
    if dead_code_kind == "unused_callable":
        candidates = [
            node for node in ast.walk(before_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.dump(node, include_attributes=False) not in after_dumps
        ]
    elif dead_code_kind == "unused_class":
        candidates = [
            node for node in ast.walk(before_tree)
            if isinstance(node, ast.ClassDef)
            and ast.dump(node, include_attributes=False) not in after_dumps
        ]
    elif dead_code_kind == "constant_false_branch":
        candidates = [
            node for node in ast.walk(before_tree)
            if isinstance(node, ast.If)
            and not node.orelse
            and _static_python_boolean(node.test) is False
            and ast.dump(node, include_attributes=False) not in after_dumps
        ]
    elif dead_code_kind == "unreachable_after_terminator":
        candidates = [
            node for node in _unreachable_python_statements(before_tree)
            if ast.dump(node, include_attributes=False) not in after_dumps
        ]
    elif dead_code_kind == "unused_literal_assignment":
        candidates = [
            node for node in ast.walk(before_tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and ast.dump(node, include_attributes=False) not in after_dumps
        ]
    else:
        candidates = []
    if len(candidates) != 1:
        return ""
    return ast.dump(candidates[0], include_attributes=False)


def _python_ast_descends_from(
    node: ast.AST,
    ancestor: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _python_dead_code_export_contains(node: ast.AST, target_name: str) -> bool:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return False
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
        return False
    value = node.value
    if value is None:
        return False
    return any(
        isinstance(item, ast.Constant) and item.value == target_name
        for item in ast.walk(value)
    )


def _python_dead_code_dynamic_lookup(node: ast.AST, target_name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else ""
    if name not in {"getattr", "setattr", "hasattr", "delattr", "globals", "locals", "vars", "eval", "exec", "__import__", "import_module"}:
        return False
    return any(isinstance(arg, ast.Constant) and arg.value == target_name for arg in ast.walk(node)) or name in {
        "globals", "locals", "vars", "eval", "exec"
    }


def _python_dead_code_has_inheritance_risk(
    tree: ast.Module,
    owner: str,
    method_name: str,
) -> bool:
    for node in (item for item in ast.walk(tree) if isinstance(item, ast.ClassDef)):
        if node.name == owner:
            continue
        inherited = any(
            isinstance(base, ast.Name) and base.id == owner
            or isinstance(base, ast.Attribute) and base.attr == owner
            for base in node.bases
        )
        overrides = any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name
            for item in node.body
        )
        if inherited or overrides:
            return True
    return False


def resolve_dead_code_callable_name(
    source_code: str,
    target_statement_fingerprint: str,
) -> str:
    """Return the unique callable name represented by a dead-code anchor."""

    name, _owner = resolve_dead_code_callable_target(
        source_code,
        target_statement_fingerprint,
    )
    return name


def resolve_dead_code_callable_target(
    source_code: str,
    target_statement_fingerprint: str,
) -> tuple[str, str]:
    """Return the unique anchored callable and its owning class, if any."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return "", ""
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.dump(node, include_attributes=False) == target_statement_fingerprint
    ]
    if len(candidates) != 1:
        return "", ""
    target = candidates[0]
    return target.name, _python_callable_class_name(target, parents) or ""


def _recover_unique_unused_python_callable(
    tree: ast.Module,
    *,
    source_line: Optional[int],
) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Recover a stale RDP callable target without guessing among APIs.

    Recovery is limited to top-level, statically unreferenced callables. A
    source-line match is authoritative when unique. Otherwise, only one
    clearly legacy-named helper may be selected. Framework entry points and
    test hooks are deliberately excluded because they can be invoked without
    a normal AST reference in this module.
    """

    entrypoint_names = {
        "main", "app", "application", "cli", "setup", "teardown",
        "setUp", "tearDown", "setUpClass", "tearDownClass",
    }
    candidates = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name not in entrypoint_names
        and not node.name.startswith(("test_", "pytest_"))
        and _is_proven_unused_python_callable(
            tree,
            target=node,
            method_name=node.name,
        )
    ]
    if source_line is not None:
        line_matches = [
            node for node in candidates
            if _python_statement_span(node)[0] <= source_line <= _python_statement_span(node)[1]
        ]
        if len(line_matches) == 1:
            return line_matches[0]

    legacy_name = re.compile(
        r"(?:^|_)(?:old|legacy|unused|dead|obsolete|deprecated)(?:_|$)",
        re.IGNORECASE,
    )
    legacy_candidates = [node for node in candidates if legacy_name.search(node.name)]
    return legacy_candidates[0] if len(legacy_candidates) == 1 else None


def _find_python_dead_statement_target(
    tree: ast.Module,
    *,
    source_line: int,
    method_name: str = "",
    class_name: Optional[str] = None,
) -> Optional[tuple[str, ast.stmt]]:
    """Find one proven dead statement at a plan line.

    A plan can use ``method`` as the owning context rather than as the item
    to delete.  This helper limits the search to that method and only returns
    AST-proven dead statements, so it cannot turn a live statement into a
    deletion merely because the line was supplied by RDP.
    """

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    scope: ast.AST = tree
    if method_name:
        callable_target = _find_python_dead_callable(
            tree,
            method_name=method_name,
            class_name=class_name,
        )
        if callable_target is None:
            return None
        # The definition line identifies the callable itself, not a statement
        # inside it.  Let the normal callable proof handle that case.
        if int(getattr(callable_target, "lineno", 0) or 0) == source_line:
            return None
        scope = callable_target

    false_branches = [
        node
        for node in ast.walk(scope)
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
        return "constant_false_branch", target

    unreachable = [
        statement
        for statement in _unreachable_python_statements(scope)
        if _python_statement_span(statement)[0] <= source_line <= _python_statement_span(statement)[1]
    ]
    if unreachable:
        target = min(
            unreachable,
            key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0],
        )
        return "unreachable_after_terminator", target

    assignments = [
        statement
        for statement in ast.walk(scope)
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        and _python_statement_span(statement)[0] <= source_line <= _python_statement_span(statement)[1]
    ]
    for statement in sorted(
        assignments,
        key=lambda node: _python_statement_span(node)[1] - _python_statement_span(node)[0],
    ):
        names = _assigned_local_names(statement)
        function = _enclosing_python_function(statement, parents)
        if not names or function is None:
            continue
        loaded_names = {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        if not any(name in loaded_names for name in names):
            return "unused_literal_assignment", statement
    return None


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


def _resolve_python_dead_callable_identity(
    tree: ast.Module,
    *,
    method_name: str,
    class_name: Optional[str],
    source_line: Optional[int] = None,
) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Resolve an RDP callable while treating class and line as fallible hints.

    The method name is semantic identity. RDP line numbers can become stale
    when its analyzed snapshot differs from the submitted source, and legacy
    plans can put a module/file stem in ``source_class``. Each relaxed lookup
    still requires a unique callable, so ambiguity is never guessed through.
    """

    attempts: list[tuple[Optional[str], Optional[int]]] = [
        (class_name, source_line),
    ]
    if source_line is not None:
        attempts.append((class_name, None))
    if class_name:
        attempts.append((None, source_line))
        attempts.append((None, None))
    elif source_line is not None:
        attempts.append((None, None))

    seen: set[tuple[Optional[str], Optional[int]]] = set()
    for owner_hint, line_hint in attempts:
        key = (owner_hint, line_hint)
        if key in seen:
            continue
        seen.add(key)
        target = _find_python_dead_callable(
            tree,
            method_name=method_name,
            class_name=owner_hint,
            source_line=line_hint,
        )
        if target is not None:
            return target
    return None


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


def _anchored_python_callable_remains_unreferenced(
    tree: ast.Module,
    *,
    target: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Revalidate an original-source unused-callable proof after safe actions.

    The original anchor is created only after the strict dynamic-reference
    check passes. Earlier SCTVA actions may introduce generic ``getattr``
    helpers for unrelated members, so the later check focuses on references to
    this exact callable instead of invalidating every original proof globally.
    """

    method_name = target.name
    if target.decorator_list or (
        method_name.startswith("__") and method_name.endswith("__")
    ):
        return False

    for node in ast.walk(tree):
        if node is target:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id == method_name:
                return False
        if isinstance(node, ast.Attribute) and node.attr == method_name:
            return False
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == method_name
        ):
            return False
    return True


def _has_dynamic_python_callable_reference(
    tree: ast.Module,
    method_name: str,
) -> bool:
    """Return whether reflection/import machinery may resolve the callable."""

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
    has_dynamic_lookup = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in dynamic_lookup_names
        for node in ast.walk(tree)
    )
    has_string_reference = any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == method_name
        for node in ast.walk(tree)
    )
    return has_dynamic_lookup and has_string_reference


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
    method_name: str = "",
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
            class_name=class_name,
            method_name=method_name,
            source_line=source_line,
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
    class_name: Optional[str],
    method_name: str,
    source_line: int,
    dead_code_kind: str,
    target_statement_fingerprint: str,
) -> Tuple[str, int]:
    """Relocate and remove exactly one previously resolved dead-code target."""

    scope: ast.AST = tree
    if method_name:
        method_scope = _find_python_dead_callable(
            tree,
            method_name=method_name,
            class_name=class_name,
        )
        if method_scope is None:
            return source_code, 0
        scope = method_scope
    elif class_name:
        class_candidates = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ]
        if len(class_candidates) != 1:
            return source_code, 0
        scope = class_candidates[0]

    if dead_code_kind == "unused_callable":
        candidates = [
            node
            for node in ast.walk(scope)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
        # Earlier safe actions may replace literals inside the unused
        # callable, making the complete AST fingerprint stale. The callable
        # name and reference proof remain stable and are a safer fallback.
        if not candidates and method_name:
            candidates = [
                node
                for node in ast.walk(scope)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ]
        if len(candidates) == 1:
            target = candidates[0]
            if _anchored_python_callable_remains_unreferenced(tree, target=target):
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
            for node in ast.walk(scope)
            if isinstance(node, ast.If)
            and not node.orelse
            and _static_python_boolean(node.test) is False
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    elif dead_code_kind == "unreachable_after_terminator":
        candidates = [
            node
            for node in _unreachable_python_statements(scope)
            if ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    elif dead_code_kind == "unused_literal_assignment":
        candidates = [
            node
            for node in ast.walk(scope)
            if isinstance(node, ast.stmt)
            and ast.dump(node, include_attributes=False) == target_statement_fingerprint
        ]
    else:
        return source_code, 0

    if len(candidates) != 1:
        # Literal introduction and formatting actions can legitimately change
        # an anchored statement's exact AST dump. Match its semantic shape,
        # then require a unique candidate. Never guess among equally shaped
        # live/dead statements.
        candidate_pool = []
        if dead_code_kind == "constant_false_branch":
            candidate_pool = [
                node for node in ast.walk(scope)
                if isinstance(node, ast.If)
                and not node.orelse
                and _static_python_boolean(node.test) is False
            ]
        elif dead_code_kind == "unreachable_after_terminator":
            candidate_pool = list(_unreachable_python_statements(scope))
        elif dead_code_kind == "unused_literal_assignment":
            candidate_pool = [
                node for node in ast.walk(scope)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
            ]
        shape_matches = [
            node for node in candidate_pool
            if _python_dead_anchor_shape(node) == _python_dead_anchor_shape_from_dump(
                target_statement_fingerprint
            )
        ]
        if len(shape_matches) == 1:
            candidates = shape_matches
        elif len(shape_matches) > 1:
            nearest = min(
                shape_matches,
                key=lambda node: abs(_python_statement_span(node)[0] - source_line),
            )
            if sum(
                abs(_python_statement_span(node)[0] - source_line) ==
                abs(_python_statement_span(nearest)[0] - source_line)
                for node in shape_matches
            ) == 1:
                candidates = [nearest]
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


def _python_dead_anchor_shape(node: ast.AST) -> str:
    """Return a stable dead-code shape that ignores safe literal rewrites."""

    class _AnchorNormalizer(ast.NodeTransformer):
        def visit_Constant(self, current: ast.Constant) -> ast.AST:
            return ast.copy_location(
                ast.Constant(value="<literal:normalized>"),
                current,
            )

        def visit_Name(self, current: ast.Name) -> ast.AST:
            if current.id.startswith(("CONSTANT_", "MAGIC_", "EXTRACTED_CONSTANT")):
                return ast.copy_location(
                    ast.Constant(value="<literal:normalized>"),
                    current,
                )
            return current

    normalized = _AnchorNormalizer().visit(copy.deepcopy(node))
    return ast.dump(normalized, include_attributes=False)


def _python_dead_anchor_shape_from_dump(value: str) -> str:
    """Normalize the original AST dump used by an execution anchor."""

    normalized = str(value or "")
    normalized = re.sub(
        r"Constant\(value=(?:[^()]|\([^)]*\))*?(?:, kind=[^)]*)?\)",
        "Constant(value='<literal:normalized>')",
        normalized,
    )
    normalized = re.sub(
        r"Name\(id='(?:CONSTANT_|MAGIC_|EXTRACTED_CONSTANT)[^']*', ctx=[A-Za-z]+\(\)\)",
        "Constant(value='<literal:normalized>')",
        normalized,
    )
    return normalized


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
