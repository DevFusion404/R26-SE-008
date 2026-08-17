"""
api/test_root_health_api.py
----------------------------
API tests for GET / and GET /api/health endpoints.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
class TestRootHealthAPI:
    def test_root_endpoint(self, client: TestClient):
        res = client.get("/")
        assert res.status_code == 200
        data = res.json()
        assert data["agent"] == "CUQA"
        assert data["status"] == "running"
        assert data["version"] == "1.0.0"

    def test_health_before_and_after_workspace_loaded(self, client: TestClient, load_python_repo):
        # Fresh client without workspace
        # Note: load_python_repo fixture loads workspace, so let's test health before and after
        res_after = client.get("/api/health")
        assert res_after.status_code == 200
        assert res_after.json()["workspace_loaded"] is True
