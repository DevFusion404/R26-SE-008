"""
java_ast_parser.py
------------------
Java AST Parser Module for the CUQA Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. PURPOSE:
   Parses Java source files (.java) using the external `javalang` library.
   Extracts Java Object-Oriented constructs (Classes, Interfaces, Fields,
   Methods, Parameters, Constructors) into the CUQA AST JSON Schema.

2. KEY ALGORITHMS & SPECIAL FUNCTIONS:
   - `_build_class_node()`: Specialized OOP extraction function. Extracts structured
     class components (Field Declarations, Method Declarations, Parameter types,
     Constructors, and Nested Inner Classes).
   - `_build_java_node()`: Generic recursive converter for javalang parse trees.
   - Dynamic Dependency Check (`JAVALANG_AVAILABLE`): Prevents server crash if `javalang`
     is not installed, returning a clear error dictionary instead.

3. DEPENDENCY:
   Requires `javalang` (`pip install javalang`).
===============================================================================
"""

import os
from typing import Any, Optional

# Safe import check for javalang library
try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal Helper Functions
# ---------------------------------------------------------------------------

def _get_name(node) -> Optional[str]:
    """
    Extract identifier/name from a `javalang` AST node.

    VIVA NOTE: Javalang nodes use different property names depending on node type:
    - Classes/Methods/Variables -> `name`
    - Method invocations      -> `member`
    - Literal values          -> `value`
    - Package/Qualifiers      -> `qualifier`
    """
    for attr in ("name", "member", "value", "qualifier"):
        val = getattr(node, attr, None)
        if val and isinstance(val, str):
            return val
    return None


def _get_line(node) -> Optional[int]:
    """
    Extract line number from javalang node position object.

    VIVA NOTE: Javalang attaches a `position` object containing `.line` and `.column`
    to most AST nodes.
    """
    pos = getattr(node, "position", None)
    if pos:
        return pos.line
    return None


def _build_java_node(node, depth: int = 0, max_depth: int = 10) -> dict:
    """
    Recursively convert a raw javalang AST node into the standardized CUQA JSON format.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Generic Java Tree Traversal):
    - `javalang` nodes define their child attributes in a special `.attrs` list.
    - This function iterates through `node.attrs`, recursively calling `_build_java_node`
      for each child node or list of child nodes up to `max_depth`.
    -------------------------------------------------------------------------
    """
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
        # javalang provides an 'attrs' attribute listing all child fields on the node
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
    """
    SPECIAL FUNCTION: Extract Java Class Structure (Fields, Methods, Constructors).

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Structured Class Parser):
    - Unlike raw generic AST dumps, this function explicitly extracts OOP components:
      1. Class / Interface Name & Line number
      2. FieldDeclarations (Member variables)
      3. MethodDeclarations (Functions & their parameter types)
      4. ConstructorDeclarations
      5. Inner Classes / Nested Types (Recursively invokes `_build_class_node`)
    - This clean structure allows the report generator and code smell detectors to evaluate
      Class-level metrics (e.g., LargeClass, TooManyParameters, FeatureEnvy).
    -------------------------------------------------------------------------

    Args:
        type_decl: A `javalang.tree.ClassDeclaration` or `InterfaceDeclaration`.
        depth: Recursion depth for nested inner classes.

    Returns:
        dict: High-level class node containing child fields and methods.
    """
    result: dict[str, Any] = {
        "type": type(type_decl).__name__,
        "name": getattr(type_decl, "name", "Unknown"),
        "children": [],
    }

    pos = getattr(type_decl, "position", None)
    if pos:
        result["line"] = pos.line

    # 1. Extract Class Fields (Variables)
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

    # 2. Extract Methods & Method Parameters
    for method in getattr(type_decl, "methods", []) or []:
        method_pos = getattr(method, "position", None)
        method_node: dict[str, Any] = {
            "type": "MethodDeclaration",
            "name": getattr(method, "name", ""),
            "children": [],
        }
        if method_pos:
            method_node["line"] = method_pos.line

        # Extract parameters and parameter types
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

    # 3. Extract Class Constructors
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

    # 4. Extract Nested / Inner Classes recursively
    for inner in getattr(type_decl, "body", []) or []:
        if hasattr(inner, "body") and hasattr(inner, "name"):
            result["children"].append(_build_class_node(inner, depth + 1))

    return result


# ---------------------------------------------------------------------------
# Public API Functions
# ---------------------------------------------------------------------------

def parse_java_file(file_path: str) -> dict:
    """
    Parse a Java source file from disk into CUQA AST JSON format.

    -------------------------------------------------------------------------
    VIVA NOTE: Handles missing javalang library gracefully by returning an error dict
    instead of failing. Reads files with UTF-8 encoding and error replacement.
    -------------------------------------------------------------------------
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
    """
    Parse Java source string into CUQA AST JSON format.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (CompilationUnit Structure):
    - Calls `javalang.parse.parse(source)` to produce a full compilation unit.
    - Extracts package imports (`ImportDeclaration`) and top-level class declarations
      (`CompilationUnit` -> `children` = `imports + classes`).
    - Catches `JavaSyntaxError` and generic parse exceptions cleanly.
    -------------------------------------------------------------------------
    """
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

    # Extract Import declarations
    imports = []
    for imp in getattr(tree, "imports", []) or []:
        imports.append({
            "type": "ImportDeclaration",
            "name": getattr(imp, "path", ""),
            "children": [],
        })

    # Extract Class declarations using specialized class parser
    classes = []
    for type_decl in getattr(tree, "types", []) or []:
        classes.append(_build_class_node(type_decl))

    # Assemble final top-level CompilationUnit node
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

