"""
api/test_files_api.py
---------------------
API tests for GET /api/files endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestFilesAPI:
    def test_list_files_no_workspace(self, client: TestClient):
        res = client.get("/api/files")
        assert res.status_code == 400

    def test_list_files_with_loaded_workspace(self, client: TestClient, load_python_repo):
        res = client.get("/api/files")
        assert res.status_code == 200
        data = res.json()
        assert "repo_name" in data
        assert "files" in data
        assert "total" in data
        assert data["total"] == len(data["files"])
        assert data["total"] == 2

    def test_list_files_scaling_count(self, client: TestClient, make_zip):
        files = {f"src/file_{i}.py": "x = 1" for i in range(105)}
        zb = make_zip(files, top_dir="large_files_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("large_files_repo.zip", zb, "application/zip")},
        )

        res = client.get("/api/files")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 105
        assert len(data["files"]) == 105  # GET /api/files returns complete list!
