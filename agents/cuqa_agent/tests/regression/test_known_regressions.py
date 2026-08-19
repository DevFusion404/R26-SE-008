"""
regression/test_known_regressions.py
------------------------------------
Regression tests for all identified security bugs and edge case defects.
"""

import io
import zipfile
import pytest
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.regression
class TestKnownRegressions:
    def test_zip_entry_cannot_escape_workspace(self, client: TestClient):
        """Zip Slip regression test."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../../escape.py", "x = 1")
        zb = buf.getvalue()

        res = client.post(
            "/api/upload-zip",
            files={"file": ("slip.zip", zb, "application/zip")},
        )
        assert res.status_code == 400

    def test_quality_report_rejects_parent_directory_traversal(self, client: TestClient, load_python_repo):
        """Path traversal regression test."""
        res = client.post("/api/quality-report", json={"file_path": "../../etc/passwd"})
        assert res.status_code == 400

    def test_github_hostname_must_be_exact(self, client: TestClient):
        """GitHub hostname spoofing regression test."""
        res = client.post("/api/github-repo", json={"url": "https://github.com.evil.com/user/repo"})
        assert res.status_code in (400, 422)

    def test_magic_number_inside_c_string_not_reported(self):
        """String literal false positive regression test."""
        source = 'void fn() { printf("Port is 8080"); }\n'
        rep = generate_file_report(source, "test.c")
        magic_smells = [s for s in rep["code_smells"] if s["type"] == "MagicNumber"]
        assert len(magic_smells) == 0
