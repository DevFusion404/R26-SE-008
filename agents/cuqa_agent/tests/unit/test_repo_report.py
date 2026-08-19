"""
unit/test_repo_report.py
-------------------------
Unit tests for generate_repo_report() in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_repo_report


@pytest.mark.unit
class TestRepoReport:
    def test_empty_repo_report(self):
        rep = generate_repo_report([])
        s = rep["summary"]
        assert s["files_analyzed"] == 0
        assert s["total_lines_of_code"] == 0
        assert s["total_code_smells"] == 0
        assert s["average_quality_score"] == 100
        assert rep["files"] == []

    def test_repo_report_aggregation_arithmetic(self):
        f1 = {
            "file": "a.py",
            "metrics": {"lines_of_code": 100},
            "code_smells": [{"type": "BareExcept", "severity": "medium"}],
            "smell_summary": {"high": 0, "medium": 1, "low": 0},
            "quality_score": 96.0,
        }
        f2 = {
            "file": "b.c",
            "metrics": {"lines_of_code": 200},
            "code_smells": [
                {"type": "LongFunction", "severity": "high"},
                {"type": "MagicNumber", "severity": "low"},
            ],
            "smell_summary": {"high": 1, "medium": 0, "low": 1},
            "quality_score": 91.0,
        }

        rep = generate_repo_report([f1, f2])
        s = rep["summary"]
        assert s["files_analyzed"] == 2
        assert s["total_lines_of_code"] == 300
        assert s["total_code_smells"] == 3
        assert s["smell_severity"]["high"] == 1
        assert s["smell_severity"]["medium"] == 1
        assert s["smell_severity"]["low"] == 1
        assert s["average_quality_score"] == 93.5

    def test_file_report_with_error_handling(self):
        f1 = {"file": "err.py", "error": "SyntaxError"}
        rep = generate_repo_report([f1])
        assert rep["summary"]["files_analyzed"] == 1
        assert rep["summary"]["total_lines_of_code"] == 0
