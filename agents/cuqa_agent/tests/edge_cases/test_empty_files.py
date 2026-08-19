"""
edge_cases/test_empty_files.py
-------------------------------
Edge case tests for zero-byte, whitespace-only, and minimal source files.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.edge_case
class TestEmptyFiles:
    @pytest.mark.parametrize("filename, lang", [
        ("empty.py", "python"),
        ("empty.java", "java"),
        ("empty.c", "c"),
        ("empty.h", "c"),
    ])
    def test_zero_byte_source(self, filename: str, lang: str):
        report = generate_file_report("", filename)
        assert report["language"] == lang
        assert report["quality_score"] == 100.0
        assert report["metrics"]["lines_of_code"] == 0
        assert report["code_smells"] == []

    @pytest.mark.parametrize("filename", ["ws.py", "ws.java", "ws.c"])
    def test_whitespace_only_source(self, filename: str):
        report = generate_file_report("   \n\n\t  \n", filename)
        assert report["metrics"]["lines_of_code"] == 3
        assert report["metrics"]["blank_lines"] == 3
        assert report["quality_score"] == 100.0

    @pytest.mark.parametrize("source, filename", [
        ("# Python comment\n", "c.py"),
        ("// Java comment\n/* block */\n", "c.java"),
        ("// C comment\n/* block */\n", "c.c"),
    ])
    def test_comments_only_source(self, source: str, filename: str):
        report = generate_file_report(source, filename)
        assert report["metrics"]["comment_lines"] > 0
        assert report["quality_score"] == 100.0
