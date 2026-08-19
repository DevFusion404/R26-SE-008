"""
security/test_malicious_input.py
---------------------------------
SECURITY TESTS: Resilience to malformed JSON payloads and unexpected inputs.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.security
class TestMaliciousInputSecurity:
    def test_malformed_json_body(self, client: TestClient):
        res = client.post(
            "/api/parse-ast",
            content="invalid json {{{",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 422

    def test_array_instead_of_object(self, client: TestClient):
        res = client.post("/api/parse-ast", json=["not", "an", "object"])
        assert res.status_code in (400, 422)

    def test_null_body(self, client: TestClient):
        res = client.post("/api/parse-ast", json=None)
        assert res.status_code in (400, 422)

    def test_extremely_large_file_path(self, client: TestClient, load_python_repo):
        large_path = "a/" * 1000 + "file.py"
        res = client.post("/api/parse-ast", json={"file_path": large_path})
        assert res.status_code in (400, 404)
