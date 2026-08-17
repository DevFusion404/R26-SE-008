"""
integration/test_c_repository_flow.py
--------------------------------------
Integration test for full end-to-end C repository processing flow.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestCRepositoryFlow:
    def test_full_c_flow(self, client: TestClient, make_zip):
        files = {
            "src/main.c": "#include \"service.h\"\nint main() { return 0; }",
            "src/service.c": "#include \"service.h\"\nvoid run() {}",
            "src/service.h": "#pragma once\nvoid run();",
        }
        zb = make_zip(files, top_dir="c_app")

        # 1. Upload
        r1 = client.post("/api/upload-zip", files={"file": ("c_app.zip", zb, "application/zip")})
        assert r1.status_code == 200
        assert r1.json()["primary_language"] == "C"

        # 2. Files
        r2 = client.get("/api/files")
        assert r2.json()["total"] == 3

        # 3. Structure
        r3 = client.get("/api/project-structure")
        assert r3.json()["total_source_files"] == 3

        # 4. AST
        r4 = client.post("/api/parse-ast", json={"file_path": "src/main.c"})
        assert r4.json()["parsed"]["language"] == "c"

        # 5. Quality report
        r5 = client.post("/api/quality-report", json={})
        assert r5.json()["report"]["summary"]["files_analyzed"] == 3
