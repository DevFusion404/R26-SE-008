"""
python_ast_parser.py
--------------------
Parses Python source files using Python's built-in `ast` module.
Converts the AST into the standardized CUQA JSON schema.
"""

import ast
import os
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _node_name(node: ast.AST) -> Optional[str]:
    """Extract a meaningful name from an AST node."""
    for attr in ("name", "id", "attr", "arg"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            return val
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return None


def _node_line(node: ast.AST) -> Optional[int]:
    return getattr(node, "lineno", None)


def _build_node(node: ast.AST, depth: int = 0, max_depth: int = 12) -> dict:
    """Recursively build the CUQA JSON schema node."""
    node_type = type(node).__name__
    name = _node_name(node)
    line = _node_line(node)

    result: dict[str, Any] = {"type": node_type}
    if name:
        result["name"] = name
    if line is not None:
        result["line"] = line

    if depth < max_depth:
        children = []
        for child in ast.iter_child_nodes(node):
            children.append(_build_node(child, depth + 1, max_depth))
        if children:
            result["children"] = children
    else:
        result["children"] = []  # truncated

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_python_file(file_path: str) -> dict:
    """
    Parse a Python source file and return the CUQA AST JSON.

    Args:
        file_path: Absolute or relative path to a .py file.

    Returns:
        dict conforming to CUQA AST schema:
        {
          "file": "<filename>",
          "language": "python",
          "ast": { ... }
        }
    """
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        return {
            "file": os.path.basename(file_path),
            "language": "python",
            "error": f"SyntaxError: {exc.msg} at line {exc.lineno}",
            "ast": {},
        }

    ast_json = _build_node(tree)

    return {
        "file": os.path.basename(file_path),
        "language": "python",
        "ast": ast_json,
    }


def parse_python_source(source: str, filename: str = "untitled.py") -> dict:
    """Parse Python source provided as a string."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return {
            "file": filename,
            "language": "python",
            "error": f"SyntaxError: {exc.msg} at line {exc.lineno}",
            "ast": {},
        }
    return {
        "file": filename,
        "language": "python",
        "ast": _build_node(tree),
    }
