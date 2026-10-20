"""
unit/test_diwo_metrics.py
-------------------------
The DIWO evaluation metrics, computed from hand-built rows whose expected
numbers can be checked by eye.
"""

import json

from evaluation.diwo_metrics import compute_metrics, format_report


def _log(action, ts, stage="x", **details):
    return {"action": action, "stage": stage, "timestamp": ts, "details_json": json.dumps(details)}


def _dataset():
    workflows = [
        {"id": "wf_a", "status": "comparison", "created_at": "2026-08-30T10:00:00+00:00"},
        {"id": "wf_b", "status": "plan_approval", "created_at": "2026-08-30T11:00:00+00:00"},
        {"id": "wf_c", "status": "smell_review", "created_at": "2026-08-30T12:00:00+00:00"},
    ]
    logs = {
        "wf_a": [
            _log("smells_selected", "2026-08-30T10:02:00+00:00", selected_count=8, excluded_count=2),
            _log("plan_generated", "2026-08-30T10:02:30+00:00", source="rdp_agent"),
            _log("plan_approved", "2026-08-30T10:06:00+00:00"),
            _log("sctva_transformation_executed", "2026-08-30T10:06:30+00:00",
                 success=True, rollback=False, actions=4, executable=3, noops=1,
                 files_sent=2, files_missing=0),
            _log("transformation_completed", "2026-08-30T10:07:00+00:00", passed=3, failed=1),
            _log("transformation_accepted", "2026-08-30T10:10:00+00:00",
                 accepted_files=["a.c", "b.c"], rejected_files=["c.c"]),
        ],
        "wf_b": [
            _log("smells_selected", "2026-08-30T11:01:00+00:00", selected_count=2, excluded_count=0),
            _log("plan_generated", "2026-08-30T11:01:30+00:00", source="fallback"),
            _log("reset_to_smell_review", "2026-08-30T11:03:00+00:00"),
        ],
    }
    feedback = [
        {"workflow_id": "wf_a", "action": "plan_step_accepted", "step_key": "s1",
         "reason": "ok [DIWO: recommended]"},
        {"workflow_id": "wf_a", "action": "plan_step_accepted", "step_key": "s1",   # duplicate
         "reason": "ok [DIWO: recommended]"},
        {"workflow_id": "wf_a", "action": "plan_step_rejected", "step_key": "s2",
         "reason": "no [DIWO: recommended; developer override]"},
        {"workflow_id": "wf_a", "action": "plan_step_rejected", "step_key": "s3",
         "reason": "risky [DIWO: not_recommended]"},
        {"workflow_id": "wf_a", "action": "plan_step_accepted", "step_key": "s4",
         "reason": "[DIWO: review]"},
        {"workflow_id": "wf_a", "action": "transformation_accepted", "rating": 4},
        {"workflow_id": "wf_a", "action": "plan_approved", "rating": 5},
    ]
    return workflows, logs, feedback


def test_workflow_effectiveness():
    a = compute_metrics(*_dataset())["A_workflow_effectiveness"]
    funnel = {row["stage"]: row["workflows"] for row in a["funnel"]}
    assert funnel["started"] == 3
    assert funnel["smells_selected"] == 2
    assert funnel["results_accepted"] == 1
    assert a["end_to_end_success_rate"] == round(1 / 3, 4)
    assert a["plan_to_acceptance_rate"] == 0.5
    assert a["rework"]["workflows_with_a_step_back"] == 1


def test_decision_support_dedupes_and_scores_agreement():
    b = compute_metrics(*_dataset())["B_decision_support"]
    assert b["smell_selection"] == {"selected": 10, "excluded": 2, "selection_rate": round(10 / 12, 4)}
    assert b["plan_steps_decided"] == 4                # the duplicate s1 row is dropped
    assert b["confusion"]["recommended"] == {"approved": 1, "rejected": 1}
    assert b["step_approval_rate"] == 0.5
    # Agreeing: recommended+approved, not_recommended+rejected, review (any) = 3 of 4.
    assert b["recommendation_agreement_rate"] == 0.75
    # Without the review step: recommended (1 of 2) + not_recommended (1 of 1).
    assert b["decisive_agreement_rate"] == round(2 / 3, 4)
    assert b["recommended_precision"] == 0.5
    assert b["not_recommended_rejection_rate"] == 1.0
    assert b["override_rate"] == 0.25


def test_orchestration():
    c = compute_metrics(*_dataset())["C_orchestration"]
    assert c["plans"] == {"from_rdp_agent": 1, "fallback": 1, "rdp_availability": 0.5}
    assert c["sctva"]["executable_action_rate"] == 0.75
    assert c["sctva"]["noop_rate"] == 0.25
    assert c["transformation_steps"]["step_pass_rate"] == 0.75
    assert c["results_review"]["file_acceptance_rate"] == round(2 / 3, 4)


def test_efficiency_and_satisfaction():
    m = compute_metrics(*_dataset())
    assert m["D_efficiency"]["smell_review_minutes"]["median"] == 1.5   # 2 and 1 min
    assert m["D_efficiency"]["end_to_end_minutes"] == {
        "n": 1, "median": 10.0, "mean": 10.0, "min": 10.0, "max": 10.0}
    assert m["E_satisfaction"]["mean_rating"] == 4.5
    assert m["E_satisfaction"]["share_4_or_5"] == 1.0


def test_empty_dataset_does_not_divide_by_zero():
    m = compute_metrics([], {}, [])
    assert m["A_workflow_effectiveness"]["end_to_end_success_rate"] is None
    assert "DIWO agent evaluation" in format_report({**m, "dataset": {**m["dataset"], "provider": "t"}})
