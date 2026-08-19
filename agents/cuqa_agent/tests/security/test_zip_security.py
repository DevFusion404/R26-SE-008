"""
security/test_zip_security.py
------------------------------
CRITICAL SECURITY TESTS: Zip Slip vulnerability prevention during archive extraction.
"""

import io
import zipfile
import pytest
from fastapi.testclient import TestClient


@pytest.mark.security
class TestZipSecurity:
    @pytest.mark.parametrize("evil_filename", [
        "../../evil.py",
        "../../../outside.txt",
        "folder/../../escape.py",
        "/absolute/path/file.py",
    ])
    def test_zip_slip_rejection(self, client: TestClient, evil_filename: str):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(evil_filename, "print('hacked')\n")
        zb = buf.getvalue()

        res = client.post(
            "/api/upload-zip",
            files={"file": ("malicious.zip", zb, "application/zip")},
        )

        assert res.status_code == 400
        assert "Zip Slip" in res.json()["detail"] or "escape" in res.json()["detail"]
