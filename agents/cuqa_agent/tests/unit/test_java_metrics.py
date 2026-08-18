"""
unit/test_java_metrics.py
--------------------------
Unit tests for Java metric calculation in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import _java_metrics


@pytest.mark.unit
class TestJavaMetrics:
    def test_java_metrics_calculation(self):
        source = '''\
// Header comment
import java.util.List;
import java.util.Map;

public class App {
    // method comment
    public void run() {}
    public int calc() { return 0; }
}
'''
        m = _java_metrics(source, "App.java")
        assert m["filename"] == "App.java"
        assert m["lines_of_code"] == 9
        assert m["comment_lines"] == 2
        assert m["classes"] == 1
        assert m["functions"] == 2
        assert m["coupling"] == 2
        assert 0 <= m["comment_lines"] <= m["lines_of_code"]
