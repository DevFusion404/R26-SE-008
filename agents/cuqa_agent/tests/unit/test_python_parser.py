"""
unit/test_python_parser.py
---------------------------
Unit tests for python_ast_parser.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from python_ast_parser import parse_python_source, parse_python_file


@pytest.mark.unit
class TestPythonASTParser:
    def test_parse_valid_python_features(self):
        source = '''\
import os
from . import relative_mod

GLOBAL_VAR = 100

@decorator
async def async_func(a, b=1, *args, **kwargs) -> int:
    """Docstring."""
    try:
        async with context() as ctx:
            lst = [x for x in range(10)]
            gen = (x * 2 for x in lst)
            lam = lambda y: y + 1
            return await helper(lam(b))
    except Exception as e:
        pass
    finally:
        cleanup()

class Outer(BaseClass):
    class Inner:
        def nested_method(self):
            def inner_func():
                pass
            return inner_func()
'''
        res = parse_python_source(source, "complex.py")
        assert res["file"] == "complex.py"
        assert res["language"] == "python"
        assert "error" not in res
        assert res["ast"]["type"] == "Module"
        assert len(res["ast"].get("children", [])) > 0

    def test_empty_and_comments_only(self):
        res1 = parse_python_source("", "empty.py")
        assert res1["language"] == "python"
        assert res1["ast"]["type"] == "Module"

        res2 = parse_python_source("# Only a comment\n# Second line\n", "comments.py")
        assert res2["language"] == "python"
        assert res2["ast"]["type"] == "Module"

    def test_deeply_nested_truncation(self):
        # Generate 15 levels of nested parentheses / expressions
        nested_expr = "1 + (" * 15 + "1" + ")" * 15
        source = f"x = {nested_expr}"
        res = parse_python_source(source, "nested.py")
        assert res["language"] == "python"
        assert "ast" in res

    def test_malformed_python_returns_structured_error(self):
        source = "def broken("
        res = parse_python_source(source, "broken.py")
        assert res["file"] == "broken.py"
        assert res["language"] == "python"
        assert "error" in res
        assert "SyntaxError" in res["error"]
        assert res["ast"] == {}

    def test_parse_python_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 42\n")
        res = parse_python_file(str(f))
        assert res["file"] == "test.py"
        assert res["language"] == "python"
        assert "ast" in res
