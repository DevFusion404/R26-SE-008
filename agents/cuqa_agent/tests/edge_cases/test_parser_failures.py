"""
edge_cases/test_parser_failures.py
-----------------------------------
Edge case tests for resilience against parser failures and malformed source files.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report, generate_repo_report
from fastapi.testclient import TestClient


@pytest.mark.edge_case
class TestParserFailures:
    def test_malformed_python_file_report(self):
        source = "def broken("
        report = generate_file_report(source, "broken.py")
        assert report["language"] == "python"
        # No crash, returns report with empty/partial smells
        assert "quality_score" in report

    def test_malformed_java_file_report(self):
        source = "public class Broken {"
        report = generate_file_report(source, "Broken.java")
        assert report["language"] == "java"
        assert "quality_score" in report

    def test_malformed_c_file_report(self):
        source = "int main( {"
        report = generate_file_report(source, "broken.c")
        assert report["language"] == "c"
        assert "quality_score" in report

    def test_repo_with_49_valid_and_1_malformed_file(self, client: TestClient, make_zip):
        files = {f"src/valid_{i}.py": f"x_{i} = {i}\n" for i in range(49)}
        files["src/broken.py"] = "def broken("

        zb = make_zip(files, top_dir="mixed_malformed_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("mixed_malformed_repo.zip", zb, "application/zip")},
        )

        res = client.post("/api/quality-report", json={})
        assert res.status_code == 200
        repo_rep = res.json()["report"]
        assert repo_rep["summary"]["files_analyzed"] == 50
        # 49 valid files processed clean, 1 malformed handled without crashing!
