"""
unit/test_ast_visualizer.py
----------------------------
Unit tests for ast_visualizer.py utility functions.
"""

import pytest
# pyrefly: ignore [missing-import]
from ast_visualizer import count_nodes, flatten_ast, enrich_ast, build_summary


@pytest.mark.unit
class TestASTVisualizer:
    def test_count_nodes_simple(self):
        ast = {
            "type": "Module",
            "children": [
                {"type": "FunctionDef", "name": "foo", "children": []},
                {"type": "FunctionDef", "name": "bar", "children": [
                    {"type": "Return", "children": []}
                ]},
            ]
        }
        stats = count_nodes(ast)
        assert stats["total"] == 4
        assert stats["max_depth"] == 2
        assert stats["by_type"]["Module"] == 1
        assert stats["by_type"]["FunctionDef"] == 2
        assert stats["by_type"]["Return"] == 1

    def test_count_nodes_empty_or_non_dict(self):
        assert count_nodes({})["total"] == 1
        assert count_nodes(None)["total"] == 0

    def test_flatten_ast(self):
        ast = {
            "type": "Module",
            "name": "mod",
            "children": [
                {"type": "ClassDef", "name": "MyClass", "line": 5, "children": []}
            ]
        }
        flat = flatten_ast(ast)
        assert len(flat) == 2
        assert flat[0]["path"] == "/mod"
        assert flat[1]["path"] == "/mod/MyClass"
        assert flat[1]["line"] == 5

    def test_enrich_ast(self):
        ast = {
            "type": "Root",
            "children": [
                {"type": "ChildA", "children": []},
                {"type": "ChildB", "children": []},
            ]
        }
        enriched = enrich_ast(ast)
        assert "id" in enriched
        assert enriched["id"] == "root-1"
        assert enriched["children"][0]["id"] == "root-1-2"
        assert enriched["children"][1]["id"] == "root-1-3"

    def test_build_summary(self):
        parsed = {
            "file": "test.py",
            "language": "python",
            "ast": {
                "type": "Module",
                "children": [{"type": "FunctionDef", "children": []}]
            }
        }
        summary = build_summary(parsed)
        assert summary["file"] == "test.py"
        assert summary["language"] == "python"
        assert summary["total_nodes"] == 2
        assert summary["max_depth"] == 1
        assert summary["node_type_counts"]["FunctionDef"] == 1
