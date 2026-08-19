"""
api/test_parse_ast_api.py
-------------------------
API tests for POST /api/parse-ast endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestParseASTAPI:
    def test_parse_ast_no_workspace(self, client: TestClient):
        res = client.post("/api/parse-ast", json={"file_path": "main.py"})
        assert res.status_code == 400

    def test_parse_ast_missing_or_empty_path(self, client: TestClient, load_python_repo):
        res1 = client.post("/api/parse-ast", json={})
        assert res1.status_code == 400

        res2 = client.post("/api/parse-ast", json={"file_path": ""})
        assert res2.status_code == 400

    def test_parse_ast_nonexistent_file(self, client: TestClient, load_python_repo):
        res = client.post("/api/parse-ast", json={"file_path": "nonexistent.py"})
        assert res.status_code == 404

    def test_parse_ast_valid_python(self, client: TestClient, load_python_repo):
        res = client.post("/api/parse-ast", json={"file_path": "src/main.py"})
        assert res.status_code == 200
        data = res.json()
        assert "parsed" in data
        assert "summary" in data
        parsed = data["parsed"]
        assert parsed["language"] == "python"
        assert "ast" in parsed
        assert "id" in parsed["ast"]  # Enriched with IDs

    def test_parse_ast_valid_c_file(self, client: TestClient, load_c_repo):
        res = client.post("/api/parse-ast", json={"file_path": "src/main.c"})
        assert res.status_code == 200
        data = res.json()
        assert data["parsed"]["language"] == "c"
