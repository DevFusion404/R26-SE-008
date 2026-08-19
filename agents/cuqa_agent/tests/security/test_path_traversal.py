"""
security/test_path_traversal.py
--------------------------------
CRITICAL SECURITY TESTS: Path traversal prevention in /api/parse-ast and /api/quality-report.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.security
class TestPathTraversalSecurity:
    @pytest.mark.parametrize("traversal_path", [
        "../secret.py",
        "../../secret.py",
        "../../../etc/passwd",
        "src/../../secret.py",
        "..\\..\\windows\\system32\\calc.exe",
    ])
    def test_parse_ast_rejects_path_traversal(self, client: TestClient, load_python_repo, traversal_path: str):
        res = client.post("/api/parse-ast", json={"file_path": traversal_path})
        assert res.status_code == 400
        assert "escape" in res.json()["detail"]

    @pytest.mark.parametrize("traversal_path", [
        "../secret.py",
        "../../secret.py",
        "../../../etc/passwd",
        "src/../../secret.py",
    ])
    def test_quality_report_rejects_path_traversal(self, client: TestClient, load_python_repo, traversal_path: str):
        res = client.post("/api/quality-report", json={"file_path": traversal_path})
        assert res.status_code == 400
        assert "escape" in res.json()["detail"]
