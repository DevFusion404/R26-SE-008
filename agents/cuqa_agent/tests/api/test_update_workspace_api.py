"""API tests for POST /api/update-workspace endpoint."""

import pytest
from fastapi.testclient import TestClient
from main import app, _workspace


@pytest.fixture
def client(tmp_path):
    _workspace.update({
        "root": str(tmp_path),
        "source": "zip",
        "repo_name": "test_repo",
        "files": ["main.py"],
    })
    test_file = tmp_path / "main.py"
    test_file.write_text("def old_function(): pass\n", encoding="utf-8")
    return TestClient(app)


def test_update_workspace_overwrites_file(client, tmp_path):
    response = client.post(
        "/api/update-workspace",
        json={
            "files": [
                {
                    "file_path": "main.py",
                    "content": "def new_function(): pass\n"
                }
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["updated_files"] == 1

    updated_text = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert "new_function" in updated_text


def test_update_workspace_rejects_path_traversal(client):
    response = client.post(
        "/api/update-workspace",
        json={
            "files": [
                {
                    "file_path": "../../etc/passwd",
                    "content": "malicious"
                }
            ]
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["errors"]) == 1
    assert "attempts to escape" in data["errors"][0]["error"]
