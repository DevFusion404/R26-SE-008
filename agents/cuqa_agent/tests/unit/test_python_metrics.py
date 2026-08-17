"""
unit/test_python_metrics.py
----------------------------
Unit tests for Python metric calculation in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import _python_metrics


@pytest.mark.unit
class TestPythonMetrics:
    def test_python_metrics_calculation(self):
        source = '''\
# Comment 1
# Comment 2
import os
from sys import path

def foo():
    pass

class MyClass:
    def bar(self):
        pass
'''
        m = _python_metrics(source, "test.py")
        assert m["filename"] == "test.py"
        assert m["lines_of_code"] == 11
        assert m["comment_lines"] == 2
        assert m["functions"] == 2
        assert m["classes"] == 1
        assert m["coupling"] == 2  # 2 import statements
        assert 0 <= m["comment_lines"] <= m["lines_of_code"]
