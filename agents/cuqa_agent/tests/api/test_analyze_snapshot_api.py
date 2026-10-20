"""
api/test_analyze_snapshot_api.py
--------------------------------
API tests for POST /api/analyze-snapshot — isolated analysis of one uploaded
repository snapshot, used by the DIWO orchestrator for GitHub pull requests.

The property that matters most: the active workspace is never read or changed.
"""

import os
import sys
import tempfile
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
import main as cuqa_main

LONG_FUNCTION = "def long_function():\n" + "".join(f"    x{i} = {i}\n" for i in range(80))


def post_snapshot(client, zip_bytes, name="octo-demo-abc1234.zip", repo_name="octo/demo"):
    return client.post(
        "/api/analyze-snapshot",
        files={"file": (name, zip_bytes, "application/zip")},
        data={"repo_name": repo_name},
    )


@pytest.mark.api
class TestAnalyzeSnapshotAPI:
    def test_returns_a_repository_report(self, client: TestClient, make_zip):
        zb = make_zip({"pkg/app.py": LONG_FUNCTION, "README.md": "# hi"},
                      top_dir="octo-demo-abc1234")
        res = post_snapshot(client, zb)
        assert res.status_code == 200
        body = res.json()
        assert body["type"] == "repository"
        assert body["report"]["repo_name"] == "octo/demo"
        assert body["snapshot"] == {"top_level_dir": "octo-demo-abc1234",
                                    "files_found": 1, "files_analyzed": 1}
        [file_report] = body["report"]["files"]
        assert file_report["relative_path"] == "pkg/app.py"
        assert any(s["type"] == "LongMethod" for s in file_report["code_smells"])

    def test_does_not_touch_the_active_workspace(self, client: TestClient, make_zip):
        loaded = client.post("/api/upload-zip", files={
            "file": ("developer_repo.zip", make_zip({"dev.py": "x = 1\n"}, top_dir="d"),
                     "application/zip")})
        assert loaded.status_code == 200
        before = dict(cuqa_main._workspace)

        res = post_snapshot(client, make_zip({"pr.py": LONG_FUNCTION}, top_dir="snap"))
        assert res.status_code == 200

        assert cuqa_main._workspace == before
        assert client.post("/api/quality-report", json={}).json()["report"]["repo_name"] \
            == "developer_repo"

    def test_works_with_no_repository_loaded(self, client: TestClient, make_zip):
        assert cuqa_main._workspace["root"] is None
        assert post_snapshot(client, make_zip({"a.py": "x = 1\n"}, top_dir="t")).status_code == 200
        assert cuqa_main._workspace["root"] is None

    def test_leaves_no_temporary_files_behind(self, client: TestClient, make_zip):
        def snapshot_dirs():
            return {d for d in os.listdir(tempfile.gettempdir())
                    if d.startswith("cuqa_snapshot_")}
        before = snapshot_dirs()
        post_snapshot(client, make_zip({"a.py": "x = 1\n"}, top_dir="t"))
        post_snapshot(client, b"not a zip")
        assert snapshot_dirs() == before

    def test_snapshot_without_supported_files_has_no_smells(self, client: TestClient, make_zip):
        res = post_snapshot(client, make_zip({"README.md": "# docs"}, top_dir="t"))
        assert res.status_code == 200
        assert res.json()["report"]["files"] == []
        assert res.json()["snapshot"]["files_found"] == 0

    def test_rejects_non_zip_names(self, client: TestClient):
        assert post_snapshot(client, b"x", name="snapshot.tar.gz").status_code == 400

    def test_rejects_corrupt_zip(self, client: TestClient):
        assert post_snapshot(client, b"definitely not a zip").status_code == 400

    def test_rejects_zip_slip(self, client: TestClient, make_zip):
        zb = make_zip({"../evil.py": "x = 1\n"})
        assert post_snapshot(client, zb).status_code == 400
