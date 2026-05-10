"""AST-based Python transformers for safe refactoring actions."""

from __future__ import annotations

import ast
from typing import Any, Tuple


class _RenameSymbolTransformer(ast.NodeTransformer):
    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self.replacements = 0

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == self.old_name:
            self.replacements += 1
            return ast.copy_location(ast.Name(id=self.new_name, ctx=node.ctx), node)
        return self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        if node.arg == self.old_name:
            self.replacements += 1
            node.arg = self.new_name
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if node.name == self.old_name:
            self.replacements += 1
            node.name = self.new_name
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        if node.name == self.old_name:
            self.replacements += 1
            node.name = self.new_name
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        if node.name == self.old_name:
            self.replacements += 1
            node.name = self.new_name
        return self.generic_visit(node)


class _ReplaceLiteralTransformer(ast.NodeTransformer):
    def __init__(self, old_literal: Any, new_literal: Any) -> None:
        self.old_literal = old_literal
        self.new_literal = new_literal
        self.replacements = 0

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if node.value == self.old_literal:
            self.replacements += 1
            return ast.copy_location(ast.Constant(value=self.new_literal), node)
        return self.generic_visit(node)


class _ExtractConstantTransformer(ast.NodeTransformer):
    def __init__(self, literal_value: Any, constant_name: str) -> None:
        self.literal_value = literal_value
        self.constant_name = constant_name
        self.replacements = 0

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if node.value == self.literal_value:
            self.replacements += 1
            return ast.copy_location(ast.Name(id=self.constant_name, ctx=ast.Load()), node)
        return self.generic_visit(node)


def _parse_tree(source_code: str) -> ast.Module:
    return ast.parse(source_code)


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    tree = _parse_tree(source_code)
    transformer = _RenameSymbolTransformer(old_name=old_name, new_name=new_name)
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), transformer.replacements


def apply_replace_literal(source_code: str, old_literal: Any, new_literal: Any) -> Tuple[str, int]:
    tree = _parse_tree(source_code)
    transformer = _ReplaceLiteralTransformer(old_literal=old_literal, new_literal=new_literal)
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), transformer.replacements


def apply_extract_constant(source_code: str, literal_value: Any, constant_name: str) -> Tuple[str, int]:
    tree = _parse_tree(source_code)
    transformer = _ExtractConstantTransformer(literal_value=literal_value, constant_name=constant_name)
    tree = transformer.visit(tree)

    if transformer.replacements > 0:
        has_constant = any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == constant_name for target in node.targets)
            for node in tree.body
        )

        if not has_constant:
            assignment = ast.Assign(
                targets=[ast.Name(id=constant_name, ctx=ast.Store())],
                value=ast.Constant(value=literal_value),
            )
            insert_at = 1 if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str) else 0
            tree.body.insert(insert_at, assignment)

    ast.fix_missing_locations(tree)
    return ast.unparse(tree), transformer.replacements


def apply_inject_syntax_error(source_code: str) -> Tuple[str, int]:
    broken = source_code + "\n\ndef __sctva_broken(:\n    pass\n"
    return broken, 1


def apply_fault_injection(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    if not original_logic:
        raise ValueError("fault_injection requires 'original_logic'.")
    if faulty_logic is None:
        raise ValueError("fault_injection requires 'faulty_logic'.")

    if original_logic not in source_code:
        return source_code, 0

    return source_code.replace(original_logic, faulty_logic, 1), 1


def apply_fault_injection_python(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    """Backward-compatible alias for callers that expect a Python-specific name."""
    return apply_fault_injection(source_code, original_logic, faulty_logic)
