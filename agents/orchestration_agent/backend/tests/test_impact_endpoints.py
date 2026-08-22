"""
Selection-impact endpoints
==========================
R26-SE-008 | Bandara S M Y M | IT22277886

    GET  /api/workflows/<id>/smell-impacts
    POST /api/workflows/<id>/selection-impact
    POST /api/workflows/<id>/optimise-selection

All three are read-only: none of them advances the workflow, calls RDP, or
writes anything the developer has not asked for. That is asserted here, because
a "what if" endpoint with a side effect would be a nasty surprise at Stage 1.

Run from the backend directory:

    python -m tests.test_impact_endpoints
"""

import os
import tempfile
from pathlib import Path

_TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="diwo_impact_rt_"))
os.environ["DIWO_RUNTIME_DIR"] = str(_TEST_RUNTIME)
os.environ["DIWO_DB_PATH"] = str(_TEST_RUNTIME / "diwo_audit.db")

from app import create_app                                    # noqa: E402
from services import impact_service                           # noqa: E402

LONG_METHOD = "src/Order.java:10:0"
LARGE_CLASS = "src/Order.java:60:1"
DEAD_CODE = "src/util/Helper.java:5:0"

SMELLS = [
    {"id": LONG_METHOD, "type": "LongMethod", "severity": "high", "line": 10,
     "entity": "calculateTotal", "quality_score": 62.0,
     "location": {"file": "src/Order.java", "class": "Order",
                  "method": "calculateTotal", "lines": [10, 130]},
     "metrics": {"lines_of_code": 240, "cyclomatic_complexity": 32}},
    {"id": LARGE_CLASS, "type": "LargeClass", "severity": "high", "line": 60,
     "entity": "Order", "quality_score": 62.0,
     "location": {"file": "src/Order.java", "class": "Order",
                  "method": None, "lines": [1, 240]},
     "metrics": {"lines_of_code": 240, "method_count": 38}},
    {"id": DEAD_CODE, "type": "DeadCode", "severity": "low", "line": 5,
     "entity": "unused", "quality_score": 91.0,
     "location": {"file": "src/util/Helper.java", "class": "Helper",
                  "method": "unused", "lines": [5, 9]},
     "metrics": {"lines_of_code": 40}},
]

failures = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main():
    # SCTVA is not assumed to be running; the static tables decide alone.
    impact_service.invalidate_capability_cache()
    impact_service.sctva_supported_actions = lambda *a, **k: None

    app = create_app()
    client = app.test_client()

    wf_id = client.post("/api/workflows", json={
        "target": "OrderService", "language": "java", "smells": SMELLS,
    }).get_json()["workflow_id"]

    print("\n1. GET /smell-impacts")
    res = client.get(f"/api/workflows/{wf_id}/smell-impacts")
    check("-> 200", res.status_code == 200, res.get_data(as_text=True))
    body = res.get_json()
    check("one record per smell", body["count"] == 3, str(body["count"]))
    check("executable and advisory are counted separately",
          body["executable"] == 2 and body["advisory"] == 1,
          f"{body['executable']}/{body['advisory']}")

    by_id = {r["smell_id"]: r for r in body["records"]}
    check("LongMethod maps to extract_method",
          by_id[LONG_METHOD]["capability"]["action_type"] == "extract_method")
    check("LargeClass is advisory with no action",
          by_id[LARGE_CLASS]["capability"]["status"] == "advisory"
          and by_id[LARGE_CLASS]["capability"]["action_type"] is None)
    check("the advisory smell projects zero automated gain",
          by_id[LARGE_CLASS]["if_selected"]["quality_gain"]["automated_points"] == 0.0)
    check("every record carries a headline the row can show",
          all(r["headline"] for r in body["records"]))

    print("\n   the endpoint is read-only")
    check("the workflow stays in smell_review",
          client.get(f"/api/workflows/{wf_id}").get_json()["status"] == "smell_review")
    actions = [e["action"] for e in
               client.get(f"/api/workflows/{wf_id}/audit-logs").get_json()]
    check("and nothing was written to the audit trail",
          actions == ["workflow_started"], str(actions))

    print("\n   records are cached, not recomputed")
    again = client.get(f"/api/workflows/{wf_id}/smell-impacts").get_json()
    check("a second call returns the same records",
          [r["smell_id"] for r in again["records"]]
          == [r["smell_id"] for r in body["records"]])

    print("\n2. POST /selection-impact")
    res = client.post(f"/api/workflows/{wf_id}/selection-impact",
                      json={"selected_ids": [LARGE_CLASS]})
    check("-> 200", res.status_code == 200, res.get_data(as_text=True))
    summary = res.get_json()["summary"]
    check("an advisory-only selection captures nothing",
          summary["capture_rate"] == 0.0 and summary["executable_count"] == 0)
    check("and is reported as an error, not a warning",
          any(w["level"] == "error" for w in summary["warnings"]), str(summary["warnings"]))
    check("the baseline is derived even without a stored CUQA report",
          summary["quality_before"] > 0, str(summary["quality_before"]))

    res = client.post(f"/api/workflows/{wf_id}/selection-impact",
                      json={"selected_ids": [LONG_METHOD, DEAD_CODE]})
    payload = res.get_json()
    summary = payload["summary"]
    check("taking every executable smell captures 100%", summary["capture_rate"] == 1.0)
    check("projected quality exceeds the baseline",
          summary["quality_projected"] > summary["quality_before"])
    check("skipping the class-level smell is reported as forgone",
          summary["forgone_points"] > 0)
    check("the containment note fires (a LongMethod inside an unselected LargeClass)",
          any("enclosing class-level" in n["message"] for n in payload["interaction_notes"]),
          str(payload["interaction_notes"]))

    print("\n   file-wise selection resolves like /select-smells does")
    res = client.post(f"/api/workflows/{wf_id}/selection-impact",
                      json={"selected_files": ["src/util/Helper.java"]})
    check("selected_files expands to that file's smells",
          res.get_json()["summary"]["selected_count"] == 1,
          str(res.get_json()["summary"]["selected_count"]))

    print("\n3. POST /optimise-selection")
    res = client.post(f"/api/workflows/{wf_id}/optimise-selection",
                      json={"preset": "best_value", "budget_minutes": 20})
    check("-> 200", res.status_code == 200, res.get_data(as_text=True))
    result = res.get_json()
    check("it proposes only executable smells",
          LARGE_CLASS not in result["selected_ids"], str(result["selected_ids"]))
    check("and reports the advisory smell it refused to spend budget on",
          result["skipped_advisory"] == 1)
    check("the budget is respected", result["total_minutes"] <= 20,
          str(result["total_minutes"]))

    tight = client.post(f"/api/workflows/{wf_id}/optimise-selection",
                        json={"preset": "best_value", "budget_minutes": 5}).get_json()
    check("a 5-minute budget takes only what fits", tight["total_minutes"] <= 5,
          str(tight["total_minutes"]))
    check("which is the cheap DeadCode fix, not the 12-minute one",
          tight["selected_ids"] == [DEAD_CODE], str(tight["selected_ids"]))

    check("optimising did not change the workflow",
          client.get(f"/api/workflows/{wf_id}").get_json()["status"] == "smell_review")

    print("\n4. validation")
    cases = [
        ("unknown workflow", client.get("/api/workflows/wf_nope/smell-impacts"), 404),
        ("bad preset", client.post(f"/api/workflows/{wf_id}/optimise-selection",
                                   json={"preset": "nope"}), 400),
        ("negative budget", client.post(f"/api/workflows/{wf_id}/optimise-selection",
                                        json={"budget_minutes": -5}), 400),
        ("non-integer budget", client.post(f"/api/workflows/{wf_id}/optimise-selection",
                                           json={"budget_minutes": "sixty"}), 400),
        ("selected_ids not a list", client.post(f"/api/workflows/{wf_id}/selection-impact",
                                                json={"selected_ids": "x"}), 400),
    ]
    for label, response, expected in cases:
        check(f"{label} -> {expected}", response.status_code == expected,
              str(response.status_code))

    print("\n5. an empty selection is valid, not an error")
    res = client.post(f"/api/workflows/{wf_id}/selection-impact", json={})
    check("-> 200", res.status_code == 200)
    check("with a zero capture rate", res.get_json()["summary"]["capture_rate"] == 0.0)
    check("but the ceiling still shown, so the developer sees what is available",
          res.get_json()["summary"]["ceiling_points"] > 0)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
