"""
security/test_github_url_validation.py
---------------------------------------
SECURITY TESTS: Strict domain validation for POST /api/github-repo endpoint.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.security
class TestGitHubURLValidationSecurity:
    @pytest.mark.parametrize("invalid_url", [
        "https://github.com.evil.com/user/repo",
        "https://evilgithub.com/user/repo",
        "https://evil.com/github.com/user/repo",
        "https://github.com@evil.com/user/repo",
        "http://localhost:8000/repo",
        "http://127.0.0.1/repo",
        "file:///etc/passwd",
        "ftp://github.com/user/repo",
    ])
    def test_github_url_spoofing_rejected(self, client: TestClient, invalid_url: str):
        res = client.post("/api/github-repo", json={"url": invalid_url})
        # Pydantic HttpUrl validation or FastAPI 400 rejection
        assert res.status_code in (400, 422)

    def test_valid_github_url_accepted(self, client: TestClient, monkeypatch):
        # Mock requests.get to return 404 so we know domain validation passed
        monkeypatch.setattr("requests.get", lambda *a, **kw: type("R", (), {"status_code": 404})())
        res = client.post("/api/github-repo", json={"url": "https://github.com/validuser/validrepo"})
        assert res.status_code == 502  # Passed domain validation, failed on 404 download!
