"""
End-to-end DIWO workflow check
==============================
R26-SE-008 | Bandara S M Y M | IT22277886

Drives the whole workflow through the Flask test client, with the CUQA / RDP /
SCTVA agents deliberately not running, so the fallback paths are exercised too.

What it asserts is the part of the system that is easy to break in a
restructure and impossible to notice from a route table:

  * step 4 -> 7   the report forwarded for planning contains ONLY the smells
                  the developer kept
  * step 10 -> 12 the approved plan contains ONLY the steps the developer
                  approved, and records what was rejected
  * step 15 -> 16 a rejected file is archived as its original source
  * the audit trail records each decision

Run from the backend directory:

    python -m tests.test_workflow_end_to_end
"""

import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

# Point the backend at a throwaway database BEFORE importing it: config
# resolves the path at import time, and this test must never write into
# runtime/database/diwo_audit.db, which holds the real workflow history.
_TEST_DB = Path(tempfile.gettempdir()) / "diwo_e2e_test.db"
_TEST_DB.unlink(missing_ok=True)
os.environ["DIWO_DB_PATH"] = str(_TEST_DB)

from app import create_app  # noqa: E402

SMELLS = [
    {
        "id": "src/Order.java:10:0",
        "type": "LongMethod",
        "severity": "high",
        "message": "calculateTotal is 120 lines long",
        "line": 10,
        "entity": "calculateTotal",
        "language": "java",
        "relative_path": "src/Order.java",
        "location": {"file": "src/Order.java", "class": "Order",
                     "method": "calculateTotal", "lines": [10, 130]},
        "metrics": {"lines_of_code": 240, "quality_score": 41},
    },
    {
        "id": "src/Order.java:60:1",
        "type": "TooManyParameters",
        "severity": "medium",
        "message": "applyDiscount takes 7 parameters",
        "line": 60,
        "entity": "applyDiscount",
        "language": "java",
        "relative_path": "src/Order.java",
        "location": {"file": "src/Order.java", "class": "Order",
                     "method": "applyDiscount", "lines": [60, 78]},
        "metrics": {"lines_of_code": 240, "quality_score": 41},
    },
    {
        "id": "src/util/Helper.java:5:0",
        "type": "DeadCode",
        "severity": "low",
        "message": "unusedHelper is never called",
        "line": 5,
        "entity": "unusedHelper",
        "language": "java",
        "relative_path": "src/util/Helper.java",
        "location": {"file": "src/util/Helper.java", "class": "Helper",
                     "method": "unusedHelper", "lines": [5, 9]},
        "metrics": {"lines_of_code": 40, "quality_score": 88},
    },
]

KEPT = SMELLS[0]["id"]
DROPPED = {SMELLS[1]["id"], SMELLS[2]["id"]}

ORIGINAL_ORDER = "class Order { void calculateTotal() { /* long */ } }"
REFACTORED_ORDER = "class Order { void calculateTotal() { extracted(); } }"
ORIGINAL_HELPER = "class Helper { void unusedHelper() {} }"

failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main():
    app = create_app()
    client = app.test_client()

    print("\n1. create workflow")
    res = client.post("/api/workflows", json={
        "target": "OrderService", "language": "java", "smells": SMELLS,
    })
    check("POST /api/workflows -> 201", res.status_code == 201, res.get_data(as_text=True))
    wf_id = res.get_json()["workflow_id"]
    check("metrics_before present", "metrics_before" in res.get_json())

    print("\n2. preview the selection (no planning, no state change)")
    res = client.post(f"/api/workflows/{wf_id}/smell-selection-pass",
                      json={"selected_ids": [KEPT], "selection_mode": "smell"})
    check("smell-selection-pass -> 200", res.status_code == 200, res.get_data(as_text=True))
    preview = res.get_json()
    check("preview does not advance the stage", preview["status"] == "smell_review")
    check("workflow row still at smell_review",
          client.get(f"/api/workflows/{wf_id}").get_json()["status"] == "smell_review")

    print("\n3. commit the selection  (step 4 -> 7: CUQA -> filtered report -> RDP)")
    res = client.post(f"/api/workflows/{wf_id}/select-smells",
                      json={"selected_ids": [KEPT], "selection_mode": "smell",
                            "feedback": {"reason": "focus on the hot method"}})
    check("select-smells -> 200", res.status_code == 200, res.get_data(as_text=True))
    body = res.get_json()
    check("advanced to plan_approval", body["status"] == "plan_approval")
    check("selected/excluded counts", body["selected_count"] == 1 and body["excluded_count"] == 2)

    forwarded = [s.get("type")
                 for f in body["updated_report"]["files"]
                 for s in f.get("code_smells") or []]
    check("filtered report carries only the kept smell",
          forwarded == ["LongMethod"], f"got {forwarded}")

    rdp_input = build_rdp_input_from(body["updated_report"])
    check("RDP payload drops files with no kept smells",
          [f["file"] for f in rdp_input["files"]] == ["src/Order.java"],
          f"got {[f['file'] for f in rdp_input['files']]}")
    check("RDP payload smell total matches the selection",
          rdp_input["summary"]["total_code_smells"] == 1)

    check("fallback plan is labelled as a fallback",
          body["plan_source"] == "diwo_local_fallback", body["plan_source"])
    plan = body["plan"]
    check("plan has one step for the one kept smell", len(plan["steps"]) == 1)
    check("plan step traces back to the kept smell", plan["steps"][0]["smell_id"] == KEPT)
    check("no rejected smell reached the planner",
          not (DROPPED & {s["smell_id"] for s in plan["steps"]}))

    print("\n4. plan approval  (step 10 -> 12: only approved steps continue)")
    # Re-plan with all three smells so there is something to reject.
    client.post(f"/api/workflows/{wf_id}/reset-to-smell-review", json={})
    res = client.post(f"/api/workflows/{wf_id}/select-smells",
                      json={"selected_ids": [s["id"] for s in SMELLS], "selection_mode": "smell"})
    plan = res.get_json()["plan"]
    check("three-smell plan has three steps", len(plan["steps"]) == 3, str(len(plan["steps"])))

    decisions = {str(plan["steps"][0]["step_id"]): "approve",
                 str(plan["steps"][1]["step_id"]): "reject",
                 str(plan["steps"][2]["step_id"]): "reject"}
    res = client.post(f"/api/workflows/{wf_id}/plan-decision",
                      json={"decision": "approve", "decisions": decisions})
    check("plan-decision approve -> 200", res.status_code == 200, res.get_data(as_text=True))
    body = res.get_json()
    approved = body["approved_plan"]
    check("approved plan keeps only the approved step", len(approved["steps"]) == 1,
          str(len(approved["steps"])))
    check("approved plan records the rejected step ids",
          len(approved["approval"]["rejected_step_ids"]) == 2,
          str(approved["approval"]["rejected_step_ids"]))
    check("approved summary recounted, not carried over",
          approved["summary"]["total_steps"] == 1)
    check("advanced to transformation", body["status"] == "transformation")

    print("\n5. transformation decision  (step 15 -> 16: rejected file keeps its original)")
    res = client.post(f"/api/workflows/{wf_id}/transformation-decision", json={
        "decision": "accept",
        "accepted_files": ["src/Order.java"],
        "rejected_files": ["src/util/Helper.java"],
        "files": [
            {"path": "src/Order.java", "content": REFACTORED_ORDER, "state": "refactored"},
            {"path": "src/util/Helper.java", "content": ORIGINAL_HELPER,
             "state": "reverted_to_original"},
        ],
        "feedback": {"rating": 4, "reason": "helper change was not worth it"},
    })
    check("transformation-decision -> 200", res.status_code == 200, res.get_data(as_text=True))
    body = res.get_json()
    check("advanced to comparison", body["status"] == "comparison")
    check("archive was built", bool(body.get("archive")), str(body.get("archive_error")))
    check("archive holds both files", body["archive"]["file_count"] == 2)
    check("per-file verdict echoed back",
          body["accepted_files"] == ["src/Order.java"]
          and body["rejected_files"] == ["src/util/Helper.java"])

    print("\n6. archive contents  (whole project, rejected file as original)")
    res = client.get(f"/api/workflows/{wf_id}/refactored-archive")
    check("refactored-archive -> 200", res.status_code == 200)
    with zipfile.ZipFile(io.BytesIO(res.get_data())) as zf:
        names = sorted(zf.namelist())
        check("folder structure preserved",
              "src/Order.java" in names and "src/util/Helper.java" in names, str(names))
        check("accepted file archived as refactored",
              zf.read("src/Order.java").decode() == REFACTORED_ORDER)
        check("rejected file archived as its ORIGINAL source",
              zf.read("src/util/Helper.java").decode() == ORIGINAL_HELPER)
        manifest = json.loads(zf.read("REFACTORING_MANIFEST.json"))
        states = {e["path"]: e["state"] for e in manifest["files"]}
        check("manifest marks the rejected file reverted",
              states["src/util/Helper.java"] == "reverted_to_original", str(states))

    print("\n7. completion and audit trail")
    res = client.post(f"/api/workflows/{wf_id}/complete", json={"notes": "done"})
    check("complete -> 200", res.status_code == 200, res.get_data(as_text=True))
    check("status completed", res.get_json()["status"] == "completed")

    logs = client.get(f"/api/workflows/{wf_id}/audit-logs").get_json()
    actions = [entry["action"] for entry in logs]
    for expected in ("workflow_started", "smells_selected", "plan_generated",
                     "plan_approved", "transformation_completed",
                     "transformation_accepted", "refactoring_reverted",
                     "archive_built", "workflow_completed"):
        check(f"audit log records {expected}", expected in actions, str(actions))

    feedback = client.get("/api/feedback/export").get_json()
    check("feedback export returns rows", feedback["count"] > 0)

    print("\n8. stage guard still refuses out-of-order calls")
    res = client.post(f"/api/workflows/{wf_id}/plan-decision", json={"decision": "approve"})
    check("plan-decision after completion -> 400", res.status_code == 400)
    check("guard message shape unchanged", "error" in res.get_json())

    print("\n9. agent reachability endpoints answer without the agents running")
    for path in ("/api/cuqa/status", "/api/rdp/status", "/api/sctva/status"):
        res = client.get(path)
        check(f"GET {path} -> 200", res.status_code == 200)
        check(f"{path} reports reachable=False", res.get_json()["reachable"] is False)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


def build_rdp_input_from(updated_report):
    from domain.plan_normalizer import build_rdp_plan_input
    return build_rdp_plan_input(updated_report)


if __name__ == "__main__":
    raise SystemExit(main())
