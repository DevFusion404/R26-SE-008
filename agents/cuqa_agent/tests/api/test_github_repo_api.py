"""
api/test_github_repo_api.py
----------------------------
API tests for POST /api/github-repo endpoint with mocked network requests.
"""

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
import requests


@pytest.mark.api
class TestGitHubRepoAPI:
    def test_github_repo_success(self, client: TestClient, monkeypatch, make_zip):
        zb = make_zip({"main.py": "x = 1\n"})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": str(len(zb))}
        mock_resp.iter_content.return_value = [zb]

        def mock_get(url, **kwargs):
            if "master.zip" in url or "main.zip" in url:
                return mock_resp
            err_resp = MagicMock()
            err_resp.status_code = 404
            return err_resp

        monkeypatch.setattr(requests, "get", mock_get)

        res = client.post("/api/github-repo", json={"url": "https://github.com/octocat/Hello-World"})
        assert res.status_code == 200
        data = res.json()
        assert data["repo_name"] == "Hello-World"
        assert data["files_found"] == 1

    def test_github_repo_branch_fallback(self, client: TestClient, monkeypatch, make_zip):
        zb = make_zip({"App.java": "class App {}\n"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.iter_content.return_value = [zb]

        tried_branches = []

        def mock_get(url, **kwargs):
            for b in ["main", "master", "develop", "trunk"]:
                if f"/{b}.zip" in url:
                    tried_branches.append(b)
                    if b == "develop":
                        return mock_resp
            err = MagicMock()
            err.status_code = 404
            return err

        monkeypatch.setattr(requests, "get", mock_get)

        res = client.post("/api/github-repo", json={"url": "https://github.com/user/myrepo"})
        assert res.status_code == 200
        assert "main" in tried_branches
        assert "master" in tried_branches
        assert "develop" in tried_branches

    def test_github_repo_404_all_branches(self, client: TestClient, monkeypatch):
        mock_404 = MagicMock()
        mock_404.status_code = 404
        monkeypatch.setattr(requests, "get", lambda url, **kw: mock_404)

        res = client.post("/api/github-repo", json={"url": "https://github.com/nonexistent/repo"})
        assert res.status_code == 502

    def test_github_repo_connection_error(self, client: TestClient, monkeypatch):
        def mock_get(url, **kw):
            raise requests.exceptions.ConnectionError("Network error")

        monkeypatch.setattr(requests, "get", mock_get)

        res = client.post("/api/github-repo", json={"url": "https://github.com/user/repo"})
        assert res.status_code == 502
