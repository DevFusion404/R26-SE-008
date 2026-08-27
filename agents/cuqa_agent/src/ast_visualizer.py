"""
ast_visualizer.py
-----------------
AST Visualization & Tree Enrichment Module for the CUQA Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. PURPOSE:
   Transforms raw, deeply nested CUQA AST JSON trees into display-ready, enriched
   structures for frontend visualization (e.g. React tree views, metric badges).

2. KEY SPECIAL FUNCTIONS:
   - `enrich_ast()`: Mutates AST in-place to assign stable, deterministic `id` attributes
     (e.g., 'root-1', 'root-1-2'). This enables React to efficiently render tree components
     using stable element keys (`key={node.id}`).
   - `flatten_ast()`: Converts 2D tree hierarchy into a 1D flat array with file path strings.
     Crucial for frontend search bars, filtering by node type, or fast indexed lookups.
   - `count_nodes()`: Computes total AST node count, maximum tree depth, and type counts.
===============================================================================
"""

from typing import Any


def count_nodes(ast_node: dict | None, depth: int = 0) -> dict:
    """
    Compute tree metrics (total node count, maximum depth, node type histogram).

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Tree Analysis Algorithm):
    - Uses Depth-First Search (DFS) recursive traversal (`_count_recursive`).
    - Returns a statistical dictionary summarizing AST size and structural depth.
    -------------------------------------------------------------------------

    Args:
        ast_node (dict | None): Root or sub-tree AST node.
        depth (int): Initial depth offset (default 0).

    Returns:
        dict: Statistical summary dict `{"total": int, "max_depth": int, "by_type": dict}`.
    """
    stats = {"total": 0, "max_depth": 0, "by_type": {}}
    _count_recursive(ast_node, 0, stats)
    return stats


def _count_recursive(node: dict | None, depth: int, stats: dict):
    """Internal recursive DFS helper for node statistics computation."""
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
    SPECIAL ALGORITHM: Flatten AST hierarchy into a single 1D array of nodes.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Tree Flattening Purpose):
    - Hierarchical trees are difficult to search or filter rapidly in UI text inputs.
    - `flatten_ast` builds a path string for each node (e.g. `/CompilationUnit/MyClass/myMethod`)
      and returns a flat list. This allows instant string searching without re-traversing the tree.
    -------------------------------------------------------------------------

    Args:
        ast_node (dict): The root AST node dictionary.
        path (str): Parent path string context.

    Returns:
        list[dict]: List of flat node objects containing `type`, `name`, `line`, and `path`.
    """
    result = []
    _flatten_recursive(ast_node, path, result)
    return result


def _flatten_recursive(node: dict, path: str, result: list):
    """Internal recursive DFS helper for tree flattening."""
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
    SPECIAL FUNCTION: Enrich AST nodes with unique, deterministic IDs for React UI rendering.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (React Key Optimization):
    - React tree view components require unique `key` props for DOM node reconciliation.
    - If AST nodes lack unique IDs, React re-renders the entire tree on every state update,
      causing severe UI lag.
    - `enrich_ast` recursively mutates nodes in-place, creating hierarchical IDs
      (e.g., `root-1`, `root-1-2`, `root-1-3`).
    - Passing a single-element list `counter=[0]` acts as a mutable reference pass across recursion.
    -------------------------------------------------------------------------

    Args:
        ast_node (dict): Target AST node to enrich.
        parent_id (str): ID string of parent node.
        counter (list | None): Mutable single-element integer counter reference.

    Returns:
        dict: The mutated AST dictionary with added `id` fields.
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
    Construct a concise execution summary from a parsed AST payload.

    -------------------------------------------------------------------------
    VIVA NOTE: Combines filename, language, total node counts, max depth,
    node breakdown histogram, and error status into a single response payload.
    -------------------------------------------------------------------------

    Args:
        parsed_result (dict): The complete CUQA parse payload.

    Returns:
        dict: High-level summary dictionary.
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

