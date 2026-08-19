"""
edge_cases/test_boundary_values.py
----------------------------------
Edge case tests for exact smell threshold boundaries.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.edge_case
class TestBoundaryValues:
    def test_python_long_method_exact_boundary_30_vs_31(self):
        body_30 = "\n".join([f"    x_{i} = {i}" for i in range(29)])
        src_30 = f"def fn():\n{body_30}\n    return 0\n"
        r30 = generate_file_report(src_30, "test.py")
        assert not any(s["type"] == "LongMethod" for s in r30["code_smells"])

        body_31 = "\n".join([f"    x_{i} = {i}" for i in range(30)])
        src_31 = f"def fn():\n{body_31}\n    return 0\n"
        r31 = generate_file_report(src_31, "test.py")
        assert any(s["type"] == "LongMethod" for s in r31["code_smells"])

    def test_c_long_function_exact_boundary_40_vs_41(self):
        lines_40 = "\n".join([f"    int x{i} = {i};" for i in range(38)])
        src_40 = f"int fn(void) {{\n{lines_40}\n    return 0;\n}}\n"
        r40 = generate_file_report(src_40, "test.c")
        assert not any(s["type"] == "LongFunction" for s in r40["code_smells"])

        lines_41 = "\n".join([f"    int x{i} = {i};" for i in range(39)])
        src_41 = f"int fn(void) {{\n{lines_41}\n    return 0;\n}}\n"
        r41 = generate_file_report(src_41, "test.c")
        assert any(s["type"] == "LongFunction" for s in r41["code_smells"])
