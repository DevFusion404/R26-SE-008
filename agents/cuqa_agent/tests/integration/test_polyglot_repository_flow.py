"""
integration/test_polyglot_repository_flow.py
----------------------------------------------
Integration test for polyglot (Python + Java + C) repository flow.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestPolyglotRepositoryFlow:
    def test_full_polyglot_flow(self, client: TestClient, make_zip):
        files = {
            "python/analyzer.py": "def analyze(): pass",
            "java/LegacyService.java": "public class LegacyService {}",
            "c/legacy.c": "int main() { return 0; }",
            "c/legacy.h": "#pragma once",
        }
        zb = make_zip(files, top_dir="polyglot_app")

        # 1. Upload
        r1 = client.post("/api/upload-zip", files={"file": ("polyglot_app.zip", zb, "application/zip")})
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["is_polyglot"] is True
        assert set(data1["detected_languages"]) == {"Python", "Java", "C"}
        assert data1["files_found"] == 4

        # 2. Files
        r2 = client.get("/api/files")
        assert r2.json()["total"] == 4

        # 3. Structure
        r3 = client.get("/api/project-structure")
        assert r3.json()["total_source_files"] == 4

        # 4. Quality report for polyglot codebase
        r4 = client.post("/api/quality-report", json={})
        assert r4.status_code == 200
        report = r4.json()["report"]
        assert report["summary"]["files_analyzed"] == 4
        langs = {f["language"] for f in report["files"]}
        assert langs == {"python", "java", "c"}
