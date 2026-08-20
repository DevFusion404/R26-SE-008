"""
POST /api/workflows/<id>/transform
==================================
R26-SE-008 | Bandara S M Y M | IT22277886

The endpoint that moved the SCTVA call out of the browser and behind the
orchestrator. SCTVA itself is stubbed at the client boundary, so the test
covers what the orchestrator is responsible for:

  * only the APPROVED plan reaches the agent
  * the workspace read and the execute call are both made, in that order
  * the reply is normalized into the shape the Transformation stage renders
  * SCTVA's own failure statuses are passed through, not flattened to 500
  * the stage guard still applies

Run from the backend directory:

    python -m tests.test_transform_endpoint
"""

import os
import tempfile
from pathlib import Path

# Redirect every generated artefact - database, saved reports, project ZIPs -
# into a throwaway directory. config resolves these at import time, so this has
# to happen BEFORE the backend is imported. Without it a test run leaves rows
# in runtime/database/diwo_audit.db and stray ZIPs in runtime/archives/.
_TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="diwo_test_runtime_"))
os.environ["DIWO_RUNTIME_DIR"] = str(_TEST_RUNTIME)
os.environ["DIWO_DB_PATH"] = str(_TEST_RUNTIME / "diwo_audit.db")

from app import create_app                      # noqa: E402
from clients import sctva_client                # noqa: E402
from services import transformation_service     # noqa: E402

SMELLS = [
    {
        "id": "src/Order.java:10:0",
        "type": "LongMethod",
        "severity": "high",
        "line": 10,
        "location": {"file": "src/Order.java", "class": "Order",
                     "method": "calculateTotal", "lines": [10, 130]},
        "metrics": {},
    },
    {
        "id": "src/util/Helper.java:5:0",
        "type": "DeadCode",
        "severity": "low",
        "line": 5,
        "location": {"file": "src/util/Helper.java", "class": "Helper",
                     "method": "unusedHelper", "lines": [5, 9]},
        "metrics": {},
    },
]

ORIGINAL = "class Order { void calculateTotal() { /* 120 lines */ } }"
REFACTORED = "class Order { void calculateTotal() { calcCore(); } }"

calls = {"sources": [], "execute": []}
failures = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def stub_sources(file_paths, timeout=60):
    calls["sources"].append(list(file_paths))
    return {
        "files": [{"file_name": "src/Order.java", "source_code": ORIGINAL, "language": "java"}],
        "missing": ["src/util/Helper.java"],
        "imported": 1,
        "total": len(file_paths),
    }


def stub_execute(payload, timeout=120):
    calls["execute"].append(payload)
    return {
        "request_id": payload["request_id"],
        "language": payload["language"],
        "success": True,
        "transformation_applied": True,
        "rollback_occurred": False,
        "confidence_score": 0.91,
        "total_replacements": 1,
        "file_results": [{
            "file_name": "src/Order.java",
            "refactored_code": REFACTORED,
            "success": True,
            "transformation_applied": True,
            "rollback_occurred": False,
            "language": "java",
            "total_replacements": 1,
            "validation": {"syntax": {"passed": True}},
        }],
    }


def main():
    transformation_service.fetch_workspace_sources = stub_sources
    transformation_service.sctva_execute = stub_execute

    app = create_app()
    client = app.test_client()

    print("\n1. reach plan approval, then approve ONE of two steps")
    wf_id = client.post("/api/workflows", json={
        "target": "OrderService", "language": "java", "smells": SMELLS,
    }).get_json()["workflow_id"]
    plan = client.post(f"/api/workflows/{wf_id}/select-smells",
                       json={"selected_ids": [s["id"] for s in SMELLS]}).get_json()["plan"]
    check("plan has two steps", len(plan["steps"]) == 2, str(len(plan["steps"])))

    approved = client.post(f"/api/workflows/{wf_id}/plan-decision", json={
        "decision": "approve",
        "decisions": {str(plan["steps"][0]["step_id"]): "approve",
                      str(plan["steps"][1]["step_id"]): "reject"},
    }).get_json()["approved_plan"]
    check("stored plan reduced to the approved step", len(approved["steps"]) == 1)

    print("\n2. transform  (browser sends no plan; backend uses the approved one)")
    res = client.post(f"/api/workflows/{wf_id}/transform", json={})
    check("transform -> 200", res.status_code == 200, res.get_data(as_text=True))
    body = res.get_json()

    check("SCTVA workspace read happened", len(calls["sources"]) == 1)
    check("SCTVA execute happened", len(calls["execute"]) == 1)

    request = calls["execute"][0]
    actions = request["refactoring_plan"]["actions"]
    check("exactly one action, for the one approved step", len(actions) == 1, str(len(actions)))
    check("the REJECTED step never reached SCTVA",
          all("Helper" not in str(a.get("parameters", {}).get("source_file", "")) for a in actions),
          str(actions))
    check("mapping is attributed to the orchestrator",
          request["refactoring_plan"]["metadata"]["mapped_by"] == "diwo_orchestrator")
    check("auto-refactoring stays off (a rejected step must not sneak back in)",
          request["execution_options"]["enable_sctva_auto_refactoring"] is False)
    check("source text was attached", len(request["source_files"]) == 1)
    check("language resolved", request["language"] == "java")

    print("\n3. the response is the shape the Transformation stage renders")
    result = body["result"]
    check("normalized files[]", len(result["files"]) == 1)
    check("before came from the workspace", result["files"][0]["before"] == ORIGINAL)
    check("after came from SCTVA", result["files"][0]["after"] == REFACTORED)
    check("file marked changed", result["files"][0]["changed"] is True)
    check("flat refactored_code kept", result["refactored_code"] == REFACTORED)
    check("camelCase summary fields kept", result["requestId"] == request["request_id"]
          and result["confidenceScore"] == 0.91)
    check("no diff_rows server-side (browser renders them)",
          "diff_rows" not in result["files"][0])
    check("missing sources reported", body["sources"]["missing"] == ["src/util/Helper.java"])
    check("mapping counts returned", body["mapping"]["executableCount"] == 1)
    check("agent url reported", body["sctva_url"].startswith("http"))

    logs = [e["action"] for e in client.get(f"/api/workflows/{wf_id}/audit-logs").get_json()]
    check("execution recorded in the audit trail",
          "sctva_transformation_executed" in logs, str(logs))

    print("\n4. SCTVA failures keep their own status")

    def boom(payload, timeout=120):
        raise sctva_client.SCTVAError("SCTVA agent is not reachable at http://localhost:8002",
                                      status=503)

    transformation_service.sctva_execute = boom
    res = client.post(f"/api/workflows/{wf_id}/transform", json={})
    check("agent down -> 503, not 500", res.status_code == 503, str(res.status_code))
    check("error names the agent url", "sctva_url" in res.get_json(), str(res.get_json()))

    def no_sources(file_paths, timeout=60):
        return {"files": [], "missing": list(file_paths), "imported": 0, "total": len(file_paths)}

    transformation_service.sctva_execute = stub_execute
    transformation_service.fetch_workspace_sources = no_sources
    res = client.post(f"/api/workflows/{wf_id}/transform", json={})
    check("no readable source -> 422", res.status_code == 422, str(res.status_code))
    check("422 lists the missing paths", res.get_json().get("missing") == ["src/Order.java"],
          str(res.get_json()))

    print("\n5. stage guard")
    transformation_service.fetch_workspace_sources = stub_sources
    fresh = client.post("/api/workflows", json={
        "target": "X", "language": "java", "smells": SMELLS,
    }).get_json()["workflow_id"]
    res = client.post(f"/api/workflows/{fresh}/transform", json={})
    check("transform at smell_review -> 400", res.status_code == 400, str(res.status_code))
    res = client.post("/api/workflows/wf_does_not_exist/transform", json={})
    check("unknown workflow -> 404", res.status_code == 404)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
