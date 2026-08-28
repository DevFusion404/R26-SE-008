"""Normalized Python AST statement identity used by safe refactorings."""

from __future__ import annotations

import ast
import copy
from typing import Any, Mapping, Sequence


def literal_constant_bindings(tree: ast.Module) -> dict[str, Any]:
    """Return module constants whose values are statically literal."""
    bindings: dict[str, Any] = {}
    for statement in tree.body:
        name = ""
        value: ast.expr | None = None
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            name = statement.targets[0].id
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            name = statement.target.id
            value = statement.value
        if not name or value is None:
            continue
        try:
            bindings[name] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
    return bindings


class _LiteralBindingNormalizer(ast.NodeTransformer):
    def __init__(self, bindings: Mapping[str, Any]) -> None:
        self.bindings = bindings

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.bindings:
            return ast.copy_location(
                ast.Constant(value=self.bindings[node.id]),
                node,
            )
        return node


def normalized_statement_fingerprint(
    statement: ast.stmt,
    *,
    constant_bindings: Mapping[str, Any] | None = None,
) -> str:
    """Fingerprint one complete statement while excluding source formatting."""
    normalized = _LiteralBindingNormalizer(constant_bindings or {}).visit(
        copy.deepcopy(statement)
    )
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, annotate_fields=True, include_attributes=False)


def statement_records(
    statements: Sequence[ast.stmt],
    *,
    constant_bindings: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Describe top-level statements without descending into child blocks."""
    return [
        {
            "ast_type": type(statement).__name__,
            "line": int(getattr(statement, "lineno", 0) or 0),
            "end_line": int(
                getattr(statement, "end_lineno", getattr(statement, "lineno", 0))
                or 0
            ),
            "normalized_fingerprint": normalized_statement_fingerprint(
                statement,
                constant_bindings=constant_bindings,
            ),
        }
        for statement in statements
    ]


def is_docstring_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def meaningful_top_level_statements(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    exclude_direct_returns: bool = False,
) -> list[ast.stmt]:
    body = list(function.body)
    if body and is_docstring_statement(body[0]):
        body = body[1:]
    if exclude_direct_returns:
        body = [statement for statement in body if not isinstance(statement, ast.Return)]
    return body
