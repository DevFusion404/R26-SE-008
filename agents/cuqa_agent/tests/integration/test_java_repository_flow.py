"""
integration/test_java_repository_flow.py
-----------------------------------------
Integration test for full end-to-end Java repository processing flow.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestJavaRepositoryFlow:
    def test_full_java_flow(self, client: TestClient, make_zip):
        files = {
            "src/Main.java": "public class Main { public static void main(String[] a) {} }",
            "src/UserService.java": "public class UserService { public void save() {} }",
            "src/Repository.java": "public class Repository { public void find() {} }",
        }
        zb = make_zip(files, top_dir="java_app")

        # 1. Upload
        r1 = client.post("/api/upload-zip", files={"file": ("java_app.zip", zb, "application/zip")})
        assert r1.status_code == 200
        assert r1.json()["primary_language"] == "Java"

        # 2. Files
        r2 = client.get("/api/files")
        assert r2.json()["total"] == 3

        # 3. Structure
        r3 = client.get("/api/project-structure")
        assert r3.json()["total_source_files"] == 3

        # 4. AST
        r4 = client.post("/api/parse-ast", json={"file_path": "src/Main.java"})
        assert r4.json()["parsed"]["language"] == "java"

        # 5. Quality report
        r5 = client.post("/api/quality-report", json={})
        assert r5.json()["report"]["summary"]["files_analyzed"] == 3
