"""
api/test_repository_overview_api.py
------------------------------------
API tests for GET /api/repository-overview endpoint.
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# pyrefly: ignore [missing-import]
import main as cuqa_main


@pytest.mark.api
class TestRepositoryOverviewAPI:
    def test_repository_overview_no_workspace_loaded(self, client: TestClient):
        res = client.get("/api/repository-overview")
        assert res.status_code == 400
        assert "No repository loaded" in res.json()["detail"]

    def test_repository_overview_python_repo(self, client: TestClient, make_zip):
        files = {
            "README.md": "# Demo App\nUsage info.",
            "requirements.txt": "requests==2.28.0\npytest\n",
            "src/main.py": "import utils\nif __name__ == '__main__': utils.run()",
            "src/utils.py": "def run(): print('hello')",
        }
        zb = make_zip(files, top_dir="python_demo")
        upload_res = client.post(
            "/api/upload-zip",
            files={"file": ("python_demo.zip", zb, "application/zip")},
        )
        assert upload_res.status_code == 200

        res = client.get("/api/repository-overview")
        assert res.status_code == 200
        data = res.json()

        assert data["repository"]["name"] == "python_demo"
        assert data["repository"]["source_files"] == 2
        assert data["repository"]["primary_language"] == "Python"
        assert data["repository"]["is_polyglot"] is False

        assert len(data["entry_points"]) == 1
        assert data["entry_points"][0]["path"] == "src/main.py"
        assert data["entry_points"][0]["confidence"] == "high"

        assert len(data["recommended_reading_path"]) >= 3
        paths_in_order = [p["path"] for p in data["recommended_reading_path"]]
        assert paths_in_order[0] == "README.md"
        assert paths_in_order[1] == "requirements.txt"
        assert paths_in_order[2] == "src/main.py"

        # Check dependency graph
        nodes = {n["id"] for n in data["dependency_graph"]["nodes"]}
        assert "src/main.py" in nodes
        assert "src/utils.py" in nodes

    def test_repository_overview_java_repo(self, client: TestClient, make_zip):
        files = {
            "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>",
            "src/main/java/com/example/Main.java": (
                "package com.example;\n"
                "public class Main {\n"
                "    public static void main(String[] args) {\n"
                "        System.out.println(\"Hello\");\n"
                "    }\n"
                "}"
            ),
        }
        zb = make_zip(files, top_dir="java_demo")
        client.post(
            "/api/upload-zip",
            files={"file": ("java_demo.zip", zb, "application/zip")},
        )

        res = client.get("/api/repository-overview")
        assert res.status_code == 200
        data = res.json()

        assert data["repository"]["primary_language"] == "Java"
        assert len(data["entry_points"]) == 1
        assert data["entry_points"][0]["language"] == "Java"

        build_tools = [b["name"] for b in data["project_artifacts"]["build_tools"]]
        assert "Maven" in build_tools

    def test_repository_overview_c_repo(self, client: TestClient, make_zip):
        files = {
            "Makefile": "all:\n\tgcc main.c -o main\n",
            "src/main.c": '#include "utils.h"\nint main(void) { return 0; }\n',
            "src/utils.h": "#pragma once\n",
        }
        zb = make_zip(files, top_dir="c_demo")
        client.post(
            "/api/upload-zip",
            files={"file": ("c_demo.zip", zb, "application/zip")},
        )

        res = client.get("/api/repository-overview")
        assert res.status_code == 200
        data = res.json()

        assert data["repository"]["primary_language"] == "C"
        assert len(data["entry_points"]) == 1
        assert data["entry_points"][0]["language"] == "C"

        build_tools = [b["name"] for b in data["project_artifacts"]["build_tools"]]
        assert "Make" in build_tools

    def test_repository_overview_empty_source(self, client: TestClient, make_zip):
        files = {"notes.txt": "just notes, no py/java/c code"}
        zb = make_zip(files, top_dir="empty_source_repo")
        client.post(
            "/api/upload-zip",
            files={"file": ("empty_source_repo.zip", zb, "application/zip")},
        )

        res = client.get("/api/repository-overview")
        assert res.status_code == 200
        data = res.json()

        assert data["repository"]["source_files"] == 0
        assert data["repository"]["primary_language"] is None
        assert len(data["entry_points"]) == 0
        assert len(data["analysis_notes"]) > 0

    def test_existing_apis_unaffected(self, client: TestClient, make_zip):
        files = {"app.py": "x = 1\n"}
        zb = make_zip(files, top_dir="test_unaffected")
        client.post(
            "/api/upload-zip",
            files={"file": ("test_unaffected.zip", zb, "application/zip")},
        )

        health_res = client.get("/api/health")
        assert health_res.status_code == 200
        assert health_res.json()["workspace_loaded"] is True

        files_res = client.get("/api/files")
        assert files_res.status_code == 200
        assert files_res.json()["total"] == 1

        struct_res = client.get("/api/project-structure")
        assert struct_res.status_code == 200
