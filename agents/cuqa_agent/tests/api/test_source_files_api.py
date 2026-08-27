"""
api/test_source_files_api.py
----------------------------
API tests for raw CUQA workspace source endpoints.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestSourceFilesAPI:
    def test_source_files_no_workspace(self, client: TestClient):
        res = client.post("/api/source-files", json={"file_paths": ["src/main.py"]})
        assert res.status_code == 400

    def test_source_files_requires_list(self, client: TestClient, load_python_repo):
        res = client.post("/api/source-files", json={"file_paths": "src/main.py"})
        assert res.status_code == 400

    def test_source_files_returns_raw_source(self, client: TestClient, load_python_repo):
        res = client.post("/api/source-files", json={"file_paths": ["src/main.py", "missing.py"]})
        assert res.status_code == 200

        data = res.json()
        assert data["imported"] == 1
        assert data["total"] == 2
        assert data["missing"] == ["missing.py"]
        assert data["source"] == "cuqa_workspace"

        file_data = data["files"][0]
        assert file_data["file_name"] == "src/main.py"
        assert file_data["file_path"] == "src/main.py"
        assert file_data["language"] == "python"
        assert file_data["source_mode"] == "raw"
        assert "def add" in file_data["source_code"]

    def test_source_file_alias_returns_raw_source(self, client: TestClient, load_python_repo):
        res = client.post("/api/source-file", json={"file_path": "src/main.py"})
        assert res.status_code == 200
        assert "def add" in res.json()["source_code"]

        res_get = client.get("/api/raw-source", params={"file_path": "src/main.py"})
        assert res_get.status_code == 200
        assert res_get.json()["file_name"] == "src/main.py"

    def test_source_file_rejects_path_traversal(self, client: TestClient, load_python_repo):
        res = client.post("/api/source-file", json={"file_path": "../secret.py"})
        assert res.status_code == 400
        assert "escape" in res.json()["detail"]

    def test_source_files_marks_unsafe_paths_missing(self, client: TestClient, load_python_repo):
        res = client.post("/api/source-files", json={"file_paths": ["../secret.py", "src/main.py"]})
        assert res.status_code == 200

        data = res.json()
        assert "../secret.py" in data["missing"]
        assert data["imported"] == 1
