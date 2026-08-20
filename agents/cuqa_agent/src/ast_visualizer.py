"""
ast_visualizer.py
-----------------
Converts the CUQA AST JSON into display-ready structures.
Also provides utilities for counting nodes, measuring depth, etc.
"""

from typing import Any


def count_nodes(ast_node: dict | None, depth: int = 0) -> dict:
    """Return statistics about the AST tree."""
    stats = {"total": 0, "max_depth": 0, "by_type": {}}
    _count_recursive(ast_node, 0, stats)
    return stats


def _count_recursive(node: dict | None, depth: int, stats: dict):
    if not isinstance(node, dict):
        return
    stats["total"] += 1
    stats["max_depth"] = max(stats["max_depth"], depth)
    node_type = node.get("type", "Unknown")
    stats["by_type"][node_type] = stats["by_type"].get(node_type, 0) + 1
    for child in node.get("children", []):
        _count_recursive(child, depth + 1, stats)


def flatten_ast(ast_node: dict, path: str = "") -> list[dict]:
    """
    Flatten the AST into a list of nodes with path strings.
    Useful for search / filtering in the frontend.
    """
    result = []
    _flatten_recursive(ast_node, path, result)
    return result


def _flatten_recursive(node: dict, path: str, result: list):
    if not isinstance(node, dict):
        return
    name = node.get("name", "")
    current_path = f"{path}/{name}" if name else f"{path}/{node.get('type', '?')}"
    flat = {
        "type": node.get("type"),
        "name": name,
        "line": node.get("line"),
        "path": current_path,
    }
    result.append(flat)
    for child in node.get("children", []):
        _flatten_recursive(child, current_path, result)


def enrich_ast(ast_node: dict, parent_id: str = "root", counter: list | None = None) -> dict:
    """
    Add unique `id` fields to every node so the frontend React tree can use them
    as stable keys. Mutates in-place and returns the node.
    """
    if counter is None:
        counter = [0]
    counter[0] += 1
    ast_node["id"] = f"{parent_id}-{counter[0]}"
    for child in ast_node.get("children", []):
        enrich_ast(child, ast_node["id"], counter)
    return ast_node


def build_summary(parsed_result: dict) -> dict:
    """
    Build a high-level summary from a parsed AST result.

    Args:
        parsed_result: The full CUQA AST JSON dict (with 'ast' key).

    Returns:
        Summary dict.
    """
    ast_node = parsed_result.get("ast", {})
    stats = count_nodes(ast_node)
    return {
        "file": parsed_result.get("file"),
        "language": parsed_result.get("language"),
        "total_nodes": stats["total"],
        "max_depth": stats["max_depth"],
        "node_type_counts": stats["by_type"],
        "error": parsed_result.get("error"),
    }
