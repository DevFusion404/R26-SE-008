"""
api/test_project_structure_api.py
----------------------------------
API tests for GET /api/project-structure endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestProjectStructureAPI:
    def test_project_structure_no_workspace(self, client: TestClient):
        res = client.get("/api/project-structure")
        assert res.status_code == 400

    def test_project_structure_valid_workspace(self, client: TestClient, make_zip):
        files = {
            "src/main.py": "x = 1",
            "src/java/App.java": "class App {}",
            "docs/README.md": "# Docs",
        }
        zb = make_zip(files, top_dir="struct_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("struct_repo.zip", zb, "application/zip")},
        )

        res = client.get("/api/project-structure")
        assert res.status_code == 200
        data = res.json()
        assert data["repo_name"] == "struct_repo"
        assert data["total_source_files"] == 2
        assert "tree" in data
        tree = data["tree"]
        assert tree["type"] == "directory"
        assert len(tree["children"]) > 0
