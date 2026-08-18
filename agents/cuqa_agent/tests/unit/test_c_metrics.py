"""
unit/test_c_metrics.py
-----------------------
Unit tests for C metric calculation in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import _c_metrics


@pytest.mark.unit
class TestCMetrics:
    def test_c_metrics_calculation(self):
        source = '''\
/* Multiline comment
   line 2
   line 3 */
#include <stdio.h>
#include <stdlib.h>

int g_var = 10;

// Single line comment
void process() {
    if (g_var > 0) {
        printf("positive");
    }
}
'''
        m = _c_metrics(source, "main.c")
        assert m["filename"] == "main.c"
        assert m["classes"] == 0
        assert m["functions"] == 1
        assert m["include_count"] == 2
        assert m["global_variables"] == 1
        assert m["estimated_cyclomatic_complexity"] >= 2  # 1 base + 1 if
        assert 0 <= m["comment_lines"] <= m["lines_of_code"]
