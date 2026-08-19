"""
edge_cases/test_encoding_cases.py
----------------------------------
Edge case tests for file encodings, Unicode characters, and invalid byte sequences.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.edge_case
class TestEncodingCases:
    def test_utf8_with_bom(self):
        source = "\ufeff# UTF-8 BOM comment\nx = 1\n"
        report = generate_file_report(source, "bom.py")
        assert report["language"] == "python"

    def test_sinhala_and_emoji_characters(self):
        source = '''\
# සිංහල සටහන - Sinhala comment
def සාදරයෙන්_පිළිගනිමු():  # Sinhala identifier
    msg = "🚀 Hello World! 🎉"
    return msg
'''
        report = generate_file_report(source, "sinhala.py")
        assert report["language"] == "python"

    def test_invalid_utf8_replacement_resilience(self):
        # Bytes containing invalid UTF-8 (0xFF 0xFE)
        raw_bytes = b"x = 1 # \xff\xfe invalid bytes\n"
        source = raw_bytes.decode("utf-8", errors="replace")
        report = generate_file_report(source, "invalid.py")
        assert report["language"] == "python"
        assert report["metrics"]["lines_of_code"] == 1
