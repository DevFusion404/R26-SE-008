"""
api/test_ml_smells_api.py
--------------------------
API tests for the ML smell detection endpoints:
GET /api/ml-model, POST /api/ml-smells and the `detector` option of POST /api/quality-report.
"""

import pytest
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
from ml_detector.java_features import JAVALANG_AVAILABLE

pytestmark = [
    pytest.mark.api,
    pytest.mark.skipif(not JAVALANG_AVAILABLE, reason="javalang not installed"),
]


def _long_method_java() -> str:
    body = []
    for i in range(60):
        body.append(f"        if (input > {i} && flag) {{")
        body.append(f"            total += helper(input, {i});")
        body.append(f"            for (int k{i} = 0; k{i} < {i}; k{i}++) {{ total -= k{i}; }}")
        body.append("        }")
    return (
        "public class Calculator {\n"
        "    private boolean flag;\n"
        "    public int compute(int input) {\n"
        "        int total = 0;\n"
        + "\n".join(body) + "\n"
        "        return total;\n"
        "    }\n"
        "    private int helper(int a, int b) { return a + b; }\n"
        "}\n"
    )


@pytest.fixture
def load_mixed_repo(client: TestClient, make_zip):
    files = {
        "src/Calculator.java": _long_method_java(),
        "src/Greeter.java": "public class Greeter { public String greet() { return \"hi\"; } }",
        "src/Broken.java": "public class Broken { void x( { ",
        "tools/helper.py": "def helper():\n    return 1\n",
    }
    zb = make_zip(files, top_dir="mixed_repo")
    res = client.post("/api/upload-zip", files={"file": ("mixed_repo.zip", zb, "application/zip")})
    assert res.status_code == 200
    return files


class TestMLModelAPI:
    def test_model_description(self, client: TestClient):
        res = client.get("/api/ml-model")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        assert data["language"] == "java"
        assert {"LongMethod", "LargeClass", "DataClass", "FeatureEnvy"} <= set(data["smells"])
        assert "trees" not in data["smells"]["LongMethod"]  # the description stays small


class TestMLSmellsAPI:
    def test_no_workspace(self, client: TestClient):
        assert client.post("/api/ml-smells", json={}).status_code == 400

    def test_repository_scan(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/ml-smells", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "repository"
        assert data["repo_name"] == "mixed_repo"
        assert data["model"]["available"] is True

        summary = data["summary"]
        assert summary["java_files_total"] == 3
        assert summary["files_scored"] == 2       # Broken.java cannot be parsed
        assert summary["by_type"]["LongMethod"] == 1
        assert summary["truncated"] is False

        by_file = {f["file"]: f for f in data["files"]}
        assert set(by_file) == {"src/Calculator.java", "src/Greeter.java", "src/Broken.java"}
        assert by_file["src/Broken.java"]["status"] == "parse_failed"
        assert by_file["src/Greeter.java"]["smells"] == []
        assert by_file["src/Calculator.java"]["smells"][0]["entity"] == "compute"

    def test_single_file(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/ml-smells", json={"file_path": "src/Calculator.java"})
        assert res.status_code == 200
        data = res.json()
        assert data["type"] == "file"
        assert data["result"]["file"] == "src/Calculator.java"
        assert data["result"]["status"] == "ok"
        assert [s["type"] for s in data["result"]["smells"]] == ["LongMethod"]

    def test_non_java_file_is_rejected(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/ml-smells", json={"file_path": "tools/helper.py"})
        assert res.status_code == 400

    def test_missing_file(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/ml-smells", json={"file_path": "src/Nope.java"})
        assert res.status_code == 404

    def test_path_traversal_is_blocked(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/ml-smells", json={"file_path": "../../etc/passwd"})
        assert res.status_code == 400


class TestQualityReportDetectorOption:
    def test_default_report_attaches_ml_analysis(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/quality-report", json={"file_path": "src/Calculator.java"})
        assert res.status_code == 200
        report = res.json()["report"]
        assert report["detection_mode"] == "rules"
        assert report["ml_analysis"]["status"] == "ok"
        assert {s["detected_by"] for s in report["code_smells"]} == {"rule"}

    def test_hybrid_detector(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/quality-report",
                          json={"file_path": "src/Calculator.java", "detector": "hybrid"})
        assert res.status_code == 200
        report = res.json()["report"]
        assert report["detection_mode"] == "hybrid"
        by_type = {s["type"]: s["detected_by"] for s in report["code_smells"]}
        assert by_type["LongMethod"] == "model"
        assert by_type["MagicNumber"] == "rule"

    def test_repository_report_with_ml_detector(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/quality-report", json={"detector": "ml"})
        assert res.status_code == 200
        report = res.json()["report"]
        assert report["summary"]["files_analyzed"] == 4
        assert report["summary"]["ml_code_smells"]["by_type"]["LongMethod"] == 1
        modes = {f["file"]: f.get("detection_mode") for f in report["files"]}
        assert modes["Calculator.java"] == "ml"
        assert modes["Broken.java"] == "rules"   # falls back when the file cannot be parsed
        assert modes["helper.py"] is None        # Python reports are unchanged

    def test_invalid_detector_is_rejected(self, client: TestClient, load_mixed_repo):
        res = client.post("/api/quality-report", json={"detector": "neural"})
        assert res.status_code == 400
