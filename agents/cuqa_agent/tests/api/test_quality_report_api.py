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

    def test_quality_report_covers_every_file(self, client: TestClient, make_zip):
        files = {f"src/file_{i}.py": "x = 1" for i in range(60)}
        zb = make_zip(files, top_dir="big_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("big_repo.zip", zb, "application/zip")},
        )

        res = client.post("/api/quality-report", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "repository"
        # No cap: every source file in the upload is analysed.
        assert data["report"]["summary"]["files_analyzed"] == 60

    def test_quality_report_same_basename_keeps_own_source(self, client: TestClient, make_zip):
        # Two utils.py files: the dead-code re-analysis must use each file's
        # own source, not whichever one was read last.
        files = {
            "pkg_a/utils.py": "def only_in_a():\n    return 1\n",
            "pkg_b/utils.py": "def only_in_b():\n    return 2\n",
        }
        zb = make_zip(files, top_dir="dup_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("dup_repo.zip", zb, "application/zip")},
        )

        res = client.post("/api/quality-report", json={})
        assert res.status_code == 200
        by_path = {f["relative_path"]: f for f in res.json()["report"]["files"]}
        dead_a = [s["entity"] for s in by_path["pkg_a/utils.py"]["code_smells"] if s["type"] == "DeadCode"]
        dead_b = [s["entity"] for s in by_path["pkg_b/utils.py"]["code_smells"] if s["type"] == "DeadCode"]
        assert "only_in_b" not in dead_a
        assert "only_in_a" not in dead_b
