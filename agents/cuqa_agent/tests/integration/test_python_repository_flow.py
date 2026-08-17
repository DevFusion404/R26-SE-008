"""
integration/test_python_repository_flow.py
--------------------------------------------
Integration test for full end-to-end Python repository processing flow.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestPythonRepositoryFlow:
    def test_full_python_flow(self, client: TestClient, make_zip):
        files = {
            "src/main.py": "def main():\n    print('hello')\n",
            "src/services.py": "class Service:\n    def run(self):\n        pass\n",
            "src/models.py": "class Model:\n    x = 1\n",
        }
        zb = make_zip(files, top_dir="python_app")

        # 1. Upload ZIP
        res_upload = client.post(
            "/api/upload-zip",
            files={"file": ("python_app.zip", zb, "application/zip")},
        )
        assert res_upload.status_code == 200
        up_data = res_upload.json()
        assert up_data["primary_language"] == "Python"
        assert up_data["is_polyglot"] is False
        assert up_data["files_found"] == 3

        # 2. List files
        res_files = client.get("/api/files")
        assert res_files.status_code == 200
        assert res_files.json()["total"] == 3

        # 3. Project structure
        res_struct = client.get("/api/project-structure")
        assert res_struct.status_code == 200
        assert res_struct.json()["total_source_files"] == 3

        # 4. Parse AST of main.py
        res_ast = client.post("/api/parse-ast", json={"file_path": "src/main.py"})
        assert res_ast.status_code == 200
        assert res_ast.json()["parsed"]["language"] == "python"

        # 5. Quality report for full repo
        res_report = client.post("/api/quality-report", json={})
        assert res_report.status_code == 200
        report = res_report.json()["report"]
        assert report["summary"]["files_analyzed"] == 3
        assert report["summary"]["average_quality_score"] > 0
