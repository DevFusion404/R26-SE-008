"""
api/test_quality_report_api.py
-------------------------------
API tests for POST /api/quality-report endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestQualityReportAPI:
    def test_quality_report_no_workspace(self, client: TestClient):
        res = client.post("/api/quality-report", json={})
        assert res.status_code == 400

    def test_quality_report_single_file(self, client: TestClient, load_python_repo):
        res = client.post("/api/quality-report", json={"file_path": "src/main.py"})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "file"
        assert "report" in data
        assert data["report"]["file"] == "main.py"
        assert data["report"]["language"] == "python"

    def test_quality_report_whole_repository(self, client: TestClient, load_python_repo):
        res = client.post("/api/quality-report", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "repository"
        assert "report" in data
        assert data["report"]["summary"]["files_analyzed"] == 2

    def test_quality_report_50_file_cap(self, client: TestClient, make_zip):
        files = {f"src/file_{i}.py": "x = 1" for i in range(60)}
        zb = make_zip(files, top_dir="cap_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("cap_repo.zip", zb, "application/zip")},
        )

        res = client.post("/api/quality-report", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "repository"
        # Processing is capped at 50 source files!
        assert data["report"]["summary"]["files_analyzed"] == 50
