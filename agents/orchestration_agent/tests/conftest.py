"""
conftest.py
-----------
Global pytest fixtures for the DIWO Orchestration Agent test suite.

R26-SE-008 | Bandara S M Y M | IT22277886

Provides:
  - Runtime isolation (every test writes to its own throwaway runtime/ tree)
  - Flask test client bound to the DIWO backend
  - Smell / plan / report factories matching the real CUQA and RDP shapes
  - Stubs for the three specialized agents, so no test needs a live CUQA,
    RDP or SCTVA process and none of them touches the network

Two things are set up before the backend is imported, and the order matters:

  1. sys.path — backend modules import each other as top-level packages
     (`from config import ...`, `from db.database import ...`), so
     agents/orchestration_agent/backend must be on the path.

  2. DIWO_RUNTIME_DIR / DIWO_DB_PATH — config.py resolves both at import
     time. Without this the suite would open the developer's real
     runtime/database/diwo_audit.db and write test workflows into three
     months of genuine history.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make the DIWO backend importable, and redirect every generated artefact
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_SUITE_RUNTIME = Path(tempfile.mkdtemp(prefix="diwo_tests_runtime_"))
os.environ["DIWO_RUNTIME_DIR"] = str(_SUITE_RUNTIME)
os.environ["DIWO_DB_PATH"] = str(_SUITE_RUNTIME / "diwo_audit.db")
# Never let a test clone into the developer's real ~/DIWO/repos.
os.environ["DIWO_CLONE_ROOT"] = str(_SUITE_RUNTIME / "clones")

# pyrefly: ignore [missing-import]
from app import create_app                       # noqa: E402
import config                                    # noqa: E402
from db.database import DB_PATH                  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Remove the throwaway runtime tree when the run ends."""
    shutil.rmtree(_SUITE_RUNTIME, ignore_errors=True)


# ---------------------------------------------------------------------------
# Guard: a misconfigured suite must fail loudly, not corrupt real data
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def runtime_is_isolated():
    """Assert once that nothing is pointed at the developer's real database."""
    real = (BACKEND_DIR / "runtime" / "database" / "diwo_audit.db").resolve()
    assert Path(DB_PATH).resolve() != real, (
        f"The test suite is pointed at the REAL database ({DB_PATH}). "
        "DIWO_RUNTIME_DIR / DIWO_DB_PATH must be set before the backend is imported."
    )
    assert str(_SUITE_RUNTIME) in str(config.RUNTIME_DIR), (
        f"Runtime tree is not isolated: {config.RUNTIME_DIR}"
    )
    yield


# ---------------------------------------------------------------------------
# Database isolation between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_database(app):
    """Truncate every table before each test.

    The workflow tables are global state: a test that lists workflows would
    otherwise see rows created by whichever tests ran before it, which makes
    failures depend on execution order.
    """
    with app.app_context():
        from db.database import get_db
        db = get_db()
        for table in ("smell_impacts", "feedback_entries", "audit_logs", "workflows"):
            db.execute(f"DELETE FROM {table}")
        db.commit()
    yield


# ---------------------------------------------------------------------------
# Application / client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """The DIWO Flask app, built once for the session."""
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    """Flask test client bound to the DIWO backend."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Agent stubs — no test may depend on a live CUQA / RDP / SCTVA
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_live_agents(monkeypatch):
    """Cut the suite off from the network entirely.

    A suite whose results depend on which agents happen to be running on the
    developer's machine is not a suite. Tests that need an agent to succeed opt
    in through the stub fixtures below.

    The cut is made at urllib.request.urlopen rather than at each client's
    `_request`, because that is the only level that catches everything:
    rdp_client.probe_rdp calls urlopen DIRECTLY instead of going through
    `_request`, so a `_request`-level stub leaves it making a real call to
    localhost:5000 — and it then reports reachable=True whenever the developer
    happens to have the RDP agent running.
    """
    import urllib.error
    import urllib.request

    def no_network(*args, **kwargs):
        raise urllib.error.URLError("network access is disabled in the test suite")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)

    # planning_service and transformation_service bind these at import time, so
    # patching the client module alone would not reach them.
    from clients import rdp_client, sctva_client
    from services import planning_service, transformation_service

    def unreachable_rdp(*args, **kwargs):
        raise rdp_client.RDPError("RDP agent is not reachable (stubbed).", status=503)

    def unreachable_sctva(*args, **kwargs):
        raise sctva_client.SCTVAError("SCTVA agent is not reachable (stubbed).", status=503)

    monkeypatch.setattr(planning_service, "rdp_generate_plan", unreachable_rdp)
    monkeypatch.setattr(transformation_service, "sctva_execute", unreachable_sctva)
    monkeypatch.setattr(transformation_service, "fetch_workspace_sources", unreachable_sctva)

    # The impact service caches SCTVA's action set across calls; without this a
    # capability probe from one test would leak into the next.
    from services import impact_service
    impact_service.invalidate_capability_cache()
    monkeypatch.setattr(impact_service, "sctva_supported_actions", lambda *a, **k: None)


@pytest.fixture
def stub_sctva_execute(monkeypatch):
    """Give SCTVA a working /sctva/execute and workspace reader.

    Returns the call log: {"sources": [...], "execute": [...]} so a test can
    assert what the orchestrator actually sent.
    """
    from services import transformation_service

    calls = {"sources": [], "execute": []}

    def sources(file_paths, timeout=60):
        calls["sources"].append(list(file_paths))
        return {
            "files": [
                {"file_name": p, "source_code": f"// original {p}\n", "language": "java"}
                for p in file_paths
            ],
            "missing": [],
            "imported": len(file_paths),
            "total": len(file_paths),
        }

    def execute(payload, timeout=120):
        calls["execute"].append(payload)
        return {
            "request_id": payload["request_id"],
            "language": payload["language"],
            "success": True,
            "transformation_applied": True,
            "rollback_occurred": False,
            "confidence_score": 0.93,
            "total_replacements": 1,
            "file_results": [
                {
                    "file_name": f["file_name"],
                    "refactored_code": f"// refactored {f['file_name']}\n",
                    "success": True,
                    "transformation_applied": True,
                    "rollback_occurred": False,
                    "language": "java",
                    "total_replacements": 1,
                }
                for f in payload["source_files"]
            ],
        }

    monkeypatch.setattr(transformation_service, "fetch_workspace_sources", sources)
    monkeypatch.setattr(transformation_service, "sctva_execute", execute)
    return calls


@pytest.fixture
def stub_rdp_plan(monkeypatch):
    """Give the RDP agent a working POST /generate.

    The plan mirrors what the real agent returns: a `summary` STRING (not an
    object) and basenames rather than repo-relative paths, because those two
    quirks are what normalize_rdp_plan exists to absorb.
    """
    from services import planning_service

    calls = []

    def generate(report, timeout=120):
        calls.append(report)
        steps = []
        for index, file_report in enumerate(report.get("files") or [], start=1):
            for smell in file_report.get("code_smells") or []:
                steps.append({
                    "step_id": len(steps) + 1,
                    "smell_id": f"smell_{len(steps) + 1:03d}",
                    "refactoring": "Extract Method",
                    "target": {
                        # Basename only — exactly what RDP sends.
                        "file": (file_report.get("file") or "").split("/")[-1],
                        "class": "X",
                        "method": smell.get("entity") or "m",
                        "lines": [smell.get("line") or 1],
                    },
                    "parameters": {
                        "source_file": (file_report.get("file") or "").split("/")[-1],
                        "source_line": smell.get("line") or 1,
                        "new_method_name": "extracted",
                        "source_lines": [smell.get("line") or 1, (smell.get("line") or 1) + 5],
                    },
                    "explanation": "stubbed RDP step",
                })
        return {
            "plan": {
                "plan_id": "plan_stub_001",
                "target": (report.get("files") or [{}])[0].get("file", "unknown"),
                "steps": steps,
                "summary": f"{len(steps)}-step plan generated by the stub RDP agent.",
            },
            "trace": {"candidate_generation": [], "mcda_selection": []},
        }

    monkeypatch.setattr(planning_service, "rdp_generate_plan", generate)
    return calls


@pytest.fixture
def stub_cuqa_report(monkeypatch):
    """Give the CUQA agent a working POST /api/quality-report."""
    from clients import cuqa_client

    def request(method, path, body=None, timeout=120):
        if path == cuqa_client.QUALITY_REPORT_PATH:
            return {"type": "repository", "report": CUQA_REPORT}
        if path == cuqa_client.FILES_PATH:
            return {"repo_name": "demo-repo",
                    "files": [f["relative_path"] for f in CUQA_REPORT["files"]],
                    "total": len(CUQA_REPORT["files"])}
        if path == cuqa_client.PROJECT_STRUCTURE_PATH:
            return PROJECT_STRUCTURE
        raise cuqa_client.CUQAError(f"unexpected CUQA path {path}", status=404)

    monkeypatch.setattr(cuqa_client, "_request", request)


# ---------------------------------------------------------------------------
# Data factories — the shapes the real agents produce
# ---------------------------------------------------------------------------

def make_smell(smell_id, smell_type="LongMethod", severity="high", line=10,
               file="src/Order.java", lines=(10, 60), loc=240, **metrics):
    """One entry of the flat smell list cuqa_report_to_smells() produces."""
    entity = metrics.pop("entity", "calculateTotal")
    quality = metrics.pop("quality_score", 62.0)
    return {
        "id": smell_id,
        "type": smell_type,
        "severity": severity,
        "message": f"{smell_type} detected",
        "line": line,
        "entity": entity,
        "language": "java",
        "relative_path": file,
        "quality_score": quality,
        "location": {"file": file, "class": "Order", "method": entity,
                     "lines": list(lines)},
        "metrics": {"lines_of_code": loc, "quality_score": quality, **metrics},
        "source": "cuqa",
    }


#: Three smells across two files: one auto-fixable, one advisory, one cheap.
DEFAULT_SMELLS = [
    make_smell("src/Order.java:10:0", "LongMethod", "high", 10,
               lines=(10, 130), cyclomatic_complexity=32),
    make_smell("src/Order.java:60:1", "LargeClass", "high", 60,
               lines=(1, 240), entity="Order", method_count=38),
    make_smell("src/util/Helper.java:5:0", "DeadCode", "low", 5,
               file="src/util/Helper.java", lines=(5, 9), loc=40,
               entity="unusedHelper", quality_score=91.0),
]

#: A normalized CUQA repository report, as the DIWO backend stores it.
CUQA_REPORT = {
    "summary": {
        "files_analyzed": 2,
        "total_lines_of_code": 280,
        "total_code_smells": 3,
        "smell_severity": {"high": 2, "medium": 0, "low": 1},
        "average_quality_score": 76.5,
    },
    "files": [
        {
            "file": "Order.java",
            "relative_path": "src/Order.java",
            "language": "java",
            "metrics": {"filename": "Order.java", "lines_of_code": 240,
                        "functions": 12, "classes": 1},
            "code_smells": [
                {"type": "LongMethod", "severity": "high", "line": 10,
                 "entity": "calculateTotal", "start_line": 10, "end_line": 130,
                 "message": "calculateTotal is 120 lines long",
                 "cyclomatic_complexity": 32},
                {"type": "LargeClass", "severity": "high", "line": 60,
                 "entity": "Order", "start_line": 1, "end_line": 240,
                 "message": "Order has 38 methods", "method_count": 38},
            ],
            "smell_summary": {"high": 2, "medium": 0, "low": 0},
            "quality_score": 62.0,
        },
        {
            "file": "Helper.java",
            "relative_path": "src/util/Helper.java",
            "language": "java",
            "metrics": {"filename": "Helper.java", "lines_of_code": 40,
                        "functions": 3, "classes": 1},
            "code_smells": [
                {"type": "DeadCode", "severity": "low", "line": 5,
                 "entity": "unusedHelper", "start_line": 5, "end_line": 9,
                 "message": "unusedHelper is never called"},
            ],
            "smell_summary": {"high": 0, "medium": 0, "low": 1},
            "quality_score": 91.0,
        },
    ],
    "repo_name": "demo-repo",
    "source": "cuqa",
    "report_type": "repository",
}

PROJECT_STRUCTURE = {
    "repo_name": "demo-repo",
    "source": "upload",
    "total_source_files": 2,
    "tree": {
        "name": "demo-repo", "type": "directory",
        "children": [
            {"name": "src", "type": "directory", "children": [
                {"name": "Order.java", "type": "file", "path": "src/Order.java",
                 "language": "java"},
                {"name": "util", "type": "directory", "children": [
                    {"name": "Helper.java", "type": "file",
                     "path": "src/util/Helper.java", "language": "java"},
                ]},
            ]},
        ],
    },
}


@pytest.fixture
def smells():
    """A fresh copy of the default smell list (tests mutate their inputs)."""
    return json.loads(json.dumps(DEFAULT_SMELLS))


@pytest.fixture
def cuqa_report():
    """A fresh copy of the default CUQA report."""
    return json.loads(json.dumps(CUQA_REPORT))


# ---------------------------------------------------------------------------
# Workflow factories
# ---------------------------------------------------------------------------

@pytest.fixture
def make_workflow(client, smells):
    """Create a workflow from a smell list and return its id."""
    def _make(smell_list=None, target="OrderService", language="java"):
        response = client.post("/api/workflows", json={
            "target": target,
            "language": language,
            "smells": smell_list if smell_list is not None else smells,
        })
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.get_json()["workflow_id"]
    return _make


@pytest.fixture
def workflow_id(make_workflow):
    """A workflow sitting at stage 1 (smell_review)."""
    return make_workflow()


@pytest.fixture
def at_plan_approval(client, workflow_id, smells):
    """A workflow advanced to plan_approval, using the local fallback planner.

    RDP is unreachable by default, so the plan comes from the offline
    generator — which is the right default here: it makes the fixture
    deterministic and exercises the fallback path the stage guards care about.
    """
    response = client.post(f"/api/workflows/{workflow_id}/select-smells",
                           json={"selected_ids": [s["id"] for s in smells]})
    assert response.status_code == 200, response.get_data(as_text=True)
    return workflow_id


@pytest.fixture
def at_transformation(client, at_plan_approval):
    """A workflow advanced to the transformation stage with one approved step."""
    plan = client.get(f"/api/workflows/{at_plan_approval}").get_json()["plan"]
    decisions = {str(step["step_id"]): ("approve" if index == 0 else "reject")
                 for index, step in enumerate(plan["steps"])}
    response = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                           json={"decision": "approve", "decisions": decisions})
    assert response.status_code == 200, response.get_data(as_text=True)
    return at_plan_approval


@pytest.fixture
def at_comparison(client, at_transformation):
    """A workflow that has accepted its transformation, with an archive built."""
    response = client.post(
        f"/api/workflows/{at_transformation}/transformation-decision",
        json={
            "decision": "accept",
            "accepted_files": ["src/Order.java"],
            "rejected_files": ["src/util/Helper.java"],
            "files": [
                {"path": "src/Order.java", "content": "class Order { refactored(); }",
                 "state": "refactored"},
                {"path": "src/util/Helper.java", "content": "class Helper { original(); }",
                 "state": "reverted_to_original"},
            ],
        })
    assert response.status_code == 200, response.get_data(as_text=True)
    return at_transformation
