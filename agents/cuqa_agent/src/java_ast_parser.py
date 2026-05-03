"""
java_ast_parser.py
------------------
Parses Java source files using the `javalang` library.
Converts the parse tree into the standardized CUQA JSON schema.

Install dependency:
    pip install javalang
"""

import os
from typing import Any, Optional

try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_name(node) -> Optional[str]:
    """Extract a name from a javalang node."""
    for attr in ("name", "member", "value", "qualifier"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            return val
    return None


def _get_line(node) -> Optional[int]:
    pos = getattr(node, "position", None)
    if pos:
        return pos.line
    return None


def _build_java_node(node, depth: int = 0, max_depth: int = 10) -> dict:
    """Recursively convert a javalang node to CUQA JSON schema."""
    if node is None:
        return {}

    node_type = type(node).__name__
    name = _get_name(node)
    line = _get_line(node)

    result: dict[str, Any] = {"type": node_type}
    if name:
        result["name"] = name
    if line is not None:
        result["line"] = line

    children = []
    if depth < max_depth:
        for attr_name in getattr(node, "attrs", []):
            child_val = getattr(node, attr_name, None)
            if child_val is None:
                continue
            if isinstance(child_val, list):
                for item in child_val:
                    if hasattr(item, "attrs"):
                        children.append(_build_java_node(item, depth + 1, max_depth))
            elif hasattr(child_val, "attrs"):
                children.append(_build_java_node(child_val, depth + 1, max_depth))

    if children:
        result["children"] = children
    else:
        result["children"] = []

    return result


def _build_class_node(type_decl, depth: int = 0) -> dict:
    """Build a structured class node from a javalang TypeDeclaration."""
    result: dict[str, Any] = {
        "type": type(type_decl).__name__,
        "name": getattr(type_decl, "name", "Unknown"),
        "children": [],
    }

    pos = getattr(type_decl, "position", None)
    if pos:
        result["line"] = pos.line

    # Fields
    for field in getattr(type_decl, "fields", []) or []:
        field_pos = getattr(field, "position", None)
        field_node: dict[str, Any] = {
            "type": "FieldDeclaration",
            "children": [],
        }
        if field_pos:
            field_node["line"] = field_pos.line
        for decl in getattr(field, "declarators", []) or []:
            field_node["name"] = getattr(decl, "name", "")
        result["children"].append(field_node)

    # Methods / Constructors
    for method in getattr(type_decl, "methods", []) or []:
        method_pos = getattr(method, "position", None)
        method_node: dict[str, Any] = {
            "type": "MethodDeclaration",
            "name": getattr(method, "name", ""),
            "children": [],
        }
        if method_pos:
            method_node["line"] = method_pos.line

        # Parameters
        for param in getattr(method, "parameters", []) or []:
            p_type = getattr(param, "type", None)
            type_name = ""
            if p_type:
                type_name = getattr(p_type, "name", "")
            param_node = {
                "type": "Parameter",
                "name": getattr(param, "name", ""),
                "paramType": type_name,
                "children": [],
            }
            method_node["children"].append(param_node)

        result["children"].append(method_node)

    for ctor in getattr(type_decl, "constructors", []) or []:
        ctor_pos = getattr(ctor, "position", None)
        ctor_node: dict[str, Any] = {
            "type": "ConstructorDeclaration",
            "name": getattr(ctor, "name", ""),
            "children": [],
        }
        if ctor_pos:
            ctor_node["line"] = ctor_pos.line
        result["children"].append(ctor_node)

    # Nested types
    for inner in getattr(type_decl, "body", []) or []:
        if hasattr(inner, "body") and hasattr(inner, "name"):
            result["children"].append(_build_class_node(inner, depth + 1))

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_java_file(file_path: str) -> dict:
    """
    Parse a Java source file and return the CUQA AST JSON.

    Args:
        file_path: Absolute or relative path to a .java file.

    Returns:
        dict conforming to CUQA AST schema:
        {
          "file": "<filename>",
          "language": "java",
          "ast": { ... }
        }
    """
    if not JAVALANG_AVAILABLE:
        return {
            "file": os.path.basename(file_path),
            "language": "java",
            "error": "javalang library not installed. Run: pip install javalang",
            "ast": {},
        }

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    return parse_java_source(source, os.path.basename(file_path))


def parse_java_source(source: str, filename: str = "untitled.java") -> dict:
    """Parse Java source provided as a string."""
    if not JAVALANG_AVAILABLE:
        return {
            "file": filename,
            "language": "java",
            "error": "javalang library not installed. Run: pip install javalang",
            "ast": {},
        }

    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as exc:
        return {
            "file": filename,
            "language": "java",
            "error": f"JavaSyntaxError: {exc}",
            "ast": {},
        }
    except Exception as exc:
        return {
            "file": filename,
            "language": "java",
            "error": f"ParseError: {exc}",
            "ast": {},
        }

    # Build the top-level compilation unit
    imports = []
    for imp in getattr(tree, "imports", []) or []:
        imports.append({
            "type": "ImportDeclaration",
            "name": getattr(imp, "path", ""),
            "children": [],
        })

    classes = []
    for type_decl in getattr(tree, "types", []) or []:
        classes.append(_build_class_node(type_decl))

    ast_node = {
        "type": "CompilationUnit",
        "name": filename,
        "children": imports + classes,
    }

    return {
        "file": filename,
        "language": "java",
        "ast": ast_node,
    }
