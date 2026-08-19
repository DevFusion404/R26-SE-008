"""
api/test_upload_zip_api.py
--------------------------
API tests for POST /api/upload-zip endpoint.
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import io
import zipfile
import pytest
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
import main as cuqa_main


@pytest.mark.api
class TestUploadZipAPI:
    def test_upload_valid_python_zip(self, client: TestClient, make_zip):
        zb = make_zip({"main.py": "x = 1\n"}, top_dir="my_python_repo")
        res = client.post(
            "/api/upload-zip",
            files={"file": ("my_python_repo.zip", zb, "application/zip")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["repo_name"] == "my_python_repo"
        assert data["files_found"] == 1
        assert data["primary_language"] == "Python"
        assert data["is_polyglot"] is False

    def test_upload_polyglot_zip(self, client: TestClient, make_zip):
        files = {
            "app.py": "x = 1",
            "Main.java": "public class Main {}",
            "core.c": "int main() { return 0; }",
            "core.h": "#pragma once",
        }
        zb = make_zip(files, top_dir="poly_repo")
        res = client.post(
            "/api/upload-zip",
            files={"file": ("poly_repo.zip", zb, "application/zip")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["files_found"] == 4
        assert data["is_polyglot"] is True
        assert set(data["detected_languages"]) == {"Python", "Java", "C"}

    def test_upload_empty_zip_or_no_source(self, client: TestClient, make_zip):
        zb = make_zip({"readme.txt": "hello"}, top_dir="no_source")
        res = client.post(
            "/api/upload-zip",
            files={"file": ("no_source.zip", zb, "application/zip")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["files_found"] == 0

    def test_upload_capped_response_files(self, client: TestClient, make_zip):
        files = {f"src/file_{i}.py": "x = 1" for i in range(120)}
        zb = make_zip(files, top_dir="big_repo")
        res = client.post(
            "/api/upload-zip",
            files={"file": ("big_repo.zip", zb, "application/zip")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["files_found"] == 120
        assert len(data["source_files"]) == 100  # Capped at 100 in response

    def test_upload_non_zip_file_fails(self, client: TestClient):
        res = client.post(
            "/api/upload-zip",
            files={"file": ("test.txt", b"plain text", "text/plain")},
        )
        assert res.status_code == 400

    def test_upload_corrupted_zip_fails(self, client: TestClient):
        res = client.post(
            "/api/upload-zip",
            files={"file": ("corrupt.zip", b"PK\x03\x04corruptedbytes", "application/zip")},
        )
        assert res.status_code == 400

    def test_upload_exceeds_max_zip_size(self, client: TestClient, monkeypatch, make_zip):
        # Set max zip size to 10 bytes for test
        monkeypatch.setattr(cuqa_main, "MAX_ZIP_SIZE_BYTES", 10)
        big_data = "x = 1\n" * 500
        zb = make_zip({"main.py": big_data})

        res = client.post(
            "/api/upload-zip",
            files={"file": ("too_big.zip", zb, "application/zip")},
        )
        assert res.status_code == 413
