"""
python_ast_parser.py
--------------------
Python AST Parser Module for the CUQA Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. PURPOSE:
   Parses Python source code using Python's native `ast` standard library module.
   Converts Python's abstract syntax tree into the uniform, JSON-serializable
   CUQA AST Schema consumed by the frontend visualizer and downstream analysis agents.

2. KEY ALGORITHMS & SPECIAL FUNCTIONS:
   - `_build_node()`: Recursive Tree Traversal algorithm that converts python's `ast.AST`
     objects into nested dictionaries (`{"type": ..., "name": ..., "children": [...]}`).
   - Depth Bounding (`max_depth=12`): Prevents stack overflow and memory exhaustion
     when parsing deeply nested Python code structures.
   - Name Extraction (`_node_name()`): Polymorphically inspects different AST node
     attributes (`name`, `id`, `attr`, `arg`) to extract human-readable identifiers.

3. ERROR HANDLING:
   - Syntax Errors (`SyntaxError`): Trapped cleanly; returns an error payload containing
     the line number and error message without crashing the server.
===============================================================================
"""

import ast
import os
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Internal Helper Functions
# ---------------------------------------------------------------------------

def _node_name(node: ast.AST) -> Optional[str]:
    """
    Polymorphically extract a human-readable identifier/name from various Python AST nodes.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE:
    - Different AST nodes store identifiers under different attribute names:
        - FunctionDef / ClassDef  -> node.name (e.g. 'def calculate_total()')
        - Name (Variable reference)-> node.id   (e.g. 'total')
        - Attribute (Property)    -> node.attr (e.g. 'self.total')
        - arg (Function param)    -> node.arg  (e.g. 'param_1')
        - Constant (Literals)     -> node.value (e.g. '42' or '"hello"')
    - Checking attributes dynamically using `getattr` keeps code DRY and flexible.
    -------------------------------------------------------------------------
    """
    for attr in ("name", "id", "attr", "arg"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            return val
    # Handle literal constants (numbers, strings, booleans)
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return None


def _node_line(node: ast.AST) -> Optional[int]:
    """
    Safely extract 1-indexed line number from an AST node.

    VIVA NOTE: Not all AST nodes possess line numbers (e.g., expression contexts,
    operators like Load/Store), so `getattr` handles missing attributes gracefully.
    """
    return getattr(node, "lineno", None)


def _build_node(node: ast.AST, depth: int = 0, max_depth: int = 12) -> dict:
    """
    SPECIAL RECURSIVE ALGORITHM: Convert native Python AST into CUQA JSON Schema.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Tree Traversal & Depth Guard):
    1. Pre-Order Recursive Traversal:
       Processes current node type, name, and line number first, then iterates
       over child nodes using `ast.iter_child_nodes(node)`.
    2. Recursion Guard (`max_depth=12`):
       Limits depth to 12 levels to avoid recursion errors or oversized JSON payloads
       if code has extreme nesting or complex expressions.
    -------------------------------------------------------------------------

    Args:
        node (ast.AST): Current Python AST node.
        depth (int): Current recursion depth level.
        max_depth (int): Maximum depth ceiling for recursive conversion.

    Returns:
        dict: Standardized CUQA node structure.
    """
    node_type = type(node).__name__
    name = _node_name(node)
    line = _node_line(node)

    # Base dictionary for current AST node
    result: dict[str, Any] = {"type": node_type}
    if name:
        result["name"] = name
    if line is not None:
        result["line"] = line

    # Recursive step: process child nodes if below max_depth threshold
    if depth < max_depth:
        children = []
        for child in ast.iter_child_nodes(node):
            children.append(_build_node(child, depth + 1, max_depth))
        if children:
            result["children"] = children
    else:
        result["children"] = []  # Truncate hierarchy at max depth safety limit

    return result


# ---------------------------------------------------------------------------
# Public API Functions
# ---------------------------------------------------------------------------

def parse_python_file(file_path: str) -> dict:
    """
    Parse a Python source file from disk into CUQA AST JSON format.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (File Encoding & Syntax Validation):
    - Uses `encoding="utf-8"` with `errors="replace"` to prevent crashing on
      non-standard character encodings or legacy files.
    - Catches `SyntaxError` raised by `ast.parse` and maps it into structured JSON
      containing line-specific diagnostics.
    -------------------------------------------------------------------------

    Args:
        file_path (str): File path to .py file.

    Returns:
        dict: CUQA AST schema or error dict.
    """
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    try:
        # ast.parse converts raw text into Python AST tree
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        return {
            "file": os.path.basename(file_path),
            "language": "python",
            "error": f"SyntaxError: {exc.msg} at line {exc.lineno}",
            "ast": {},
        }

    # Convert tree into uniform CUQA JSON schema
    ast_json = _build_node(tree)

    return {
        "file": os.path.basename(file_path),
        "language": "python",
        "ast": ast_json,
    }


def parse_python_source(source: str, filename: str = "untitled.py") -> dict:
    """
    Parse raw Python source string into CUQA AST JSON format.

    -------------------------------------------------------------------------
    VIVA NOTE: Useful for parsing code snippets uploaded via web interface or API.
    -------------------------------------------------------------------------
    """
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

