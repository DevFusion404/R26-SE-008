"""
edge_cases/test_large_source_files.py
--------------------------------------
Edge case tests for synthetic large source files (1,000+ LOC).
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.edge_case
class TestLargeSourceFiles:
    def test_synthetic_1000_loc_python_file(self):
        functions = [f"def func_{i}():\n    return {i}\n" for i in range(500)]
        source = "\n".join(functions)
        report = generate_file_report(source, "large.py")
        assert report["metrics"]["lines_of_code"] >= 1000
        assert report["metrics"]["functions"] == 500

    def test_synthetic_1000_loc_c_file(self):
        functions = [f"int func_{i}(void) {{\n    return {i};\n}}\n" for i in range(300)]
        source = "#include <stdio.h>\n" + "\n".join(functions)
        report = generate_file_report(source, "large.c")
        assert report["metrics"]["lines_of_code"] >= 900
        assert report["metrics"]["functions"] == 300
