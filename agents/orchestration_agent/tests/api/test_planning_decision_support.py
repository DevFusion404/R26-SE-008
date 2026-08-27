"""
api/test_planning_decision_support.py
-------------------------------------
Stage 2 decision support through the real endpoints, and the step-level
feedback it feeds on.

The three claims under test:

  1. THE RECOMMENDATION SURVIVES THE PIPELINE. It is attached when the plan is
     generated, recomputed when the developer changes goal, recomputed when
     Stage 2 is restored from a rollback, and reduced — not left stale —
     when the plan is trimmed to the approved steps. A green badge that
     outlived the evidence behind it is the failure this feature exists to
     prevent.

  2. RDP's OWN OUTPUT IS NOT OVERWRITTEN. score, risk, expected_impact,
     prediction, alternatives, parameters and explanation all survive
     enrichment. RDP says "this is the best candidate"; DIWO says "here is how
     strongly to consider approving it". Two claims, both kept.

  3. STEP-LEVEL FEEDBACK IS USABLE AND COUNTED ONCE. Every row carries the
     smell type, refactoring and severity of the step it describes — "Developer
     rejected plan step 3" is worthless once the re-ranker renumbers — and the
     frontend's modify-then-approve pair does not record the same rejection
     twice.
"""

import json

import pytest

from domain.planning_recommendation import (
    MANUAL_ONLY, NOT_RECOMMENDED, RECOMMENDED, REVIEW,
)

CATEGORIES = (RECOMMENDED, REVIEW, NOT_RECOMMENDED, MANUAL_ONLY)


def plan_of(client, wf_id):
    return client.get(f"/api/workflows/{wf_id}").get_json()["plan"]


def feedback_rows(client, action=None):
    rows = client.get("/api/feedback/export").get_json()["data"]
    return [r for r in rows if action is None or r["action"] == action]


# ─────────────────────────────────────────────────────────────────────────────
# Enrichment
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestPlanEnrichment:
    def test_every_step_carries_a_recommendation(self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)

        assert plan["steps"], "the fixture should produce a plan"
        for step in plan["steps"]:
            support = step["decision_support"]
            assert support["category"] in CATEGORIES
            assert 0 <= support["score"] <= 100
            assert support["reasons"], "every recommendation must explain itself"
            assert support["summary"]
            assert isinstance(support["auto_select_eligible"], bool)

    def test_the_plan_carries_a_summary_that_agrees_with_its_steps(
            self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)
        summary = plan["decision_support_summary"]

        assert summary["total_steps"] == len(plan["steps"])
        for category in CATEGORIES:
            assert summary[category] == sum(
                1 for s in plan["steps"] if s["decision_support"]["category"] == category)
        assert summary["auto_selectable"] == sum(
            1 for s in plan["steps"] if s["decision_support"]["auto_select_eligible"])

    def test_rdp_fields_are_kept_alongside_the_recommendation(
            self, client, at_plan_approval):
        # DIWO adds a layer; it does not replace RDP's answer with its own.
        for step in plan_of(client, at_plan_approval)["steps"]:
            assert "risk" in step and "expected_impact" in step
            assert "parameters" in step and "explanation" in step
            assert step["refactoring"]
            assert step["decision_support"]["score"] is not None

    def test_the_capability_gate_reflects_the_real_sctva_mapper(
            self, client, at_plan_approval):
        by_refactoring = {
            s["refactoring"]: s["decision_support"] for s in plan_of(client, at_plan_approval)["steps"]
        }

        # Extract Class has no safe automatic form, so it must be blue, never
        # green, however well the rest of the step scores.
        if "Extract Class" in by_refactoring:
            assert by_refactoring["Extract Class"]["category"] == MANUAL_ONLY
            assert by_refactoring["Extract Class"]["auto_select_eligible"] is False

        # Extract Method is executable and the fixture's step is complete.
        if "Extract Method" in by_refactoring:
            capability = by_refactoring["Extract Method"]["capability"]
            assert capability["status"] == "executable"
            assert capability["actual_step_mappable"] is True

    def test_the_fallback_plan_is_labelled_as_lacking_rdp_evidence(
            self, client, at_plan_approval):
        # The fixture's RDP agent is unreachable, so this plan came from the
        # local fallback planner and must not be presented as RDP output.
        for step in plan_of(client, at_plan_approval)["steps"]:
            assert any("fallback planner" in w for w in step["decision_support"]["warnings"])

    def test_the_plan_generation_audit_entry_records_what_diwo_advised(
            self, client, at_plan_approval):
        logs = client.get(f"/api/workflows/{at_plan_approval}/audit-logs").get_json()
        generated = next(log for log in logs if log["action"] == "plan_generated")

        assert generated["details"]["recommended"] is not None
        assert generated["details"]["manual_only"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Preference / strategy changes
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestStrategyChange:
    def test_changing_goal_recomputes_the_recommendation(self, client, at_plan_approval):
        response = client.post(
            f"/api/workflows/{at_plan_approval}/plan-preference-update",
            json={"decisions": {}, "preferences": {"developer_strategy": "safety_first"}})
        assert response.status_code == 200

        body = response.get_json()
        assert body["developer_strategy"] == "safety_first"

        plan = body["updated_planning_report"]
        assert plan["decision_support_summary"]["developer_strategy"] == "safety_first"
        for step in plan["steps"]:
            factor = step["decision_support"]["factors"]["strategy_match"]
            assert factor["strategy"] == "safety_first"
            assert factor["strategy_label"] == "Safety First"

    def test_a_goal_expands_to_the_preference_vocabulary_the_reranker_takes(
            self, client, at_plan_approval):
        client.post(f"/api/workflows/{at_plan_approval}/plan-preference-update",
                    json={"decisions": {}, "preferences": {"developer_strategy": "max_improvement"}})

        preferences = plan_of(client, at_plan_approval)["user_preferences"]
        assert preferences["risk_tolerance"] == "aggressive"
        assert preferences["impact_focus"] == "high"

    def test_an_older_client_sending_only_risk_tolerance_still_works(
            self, client, at_plan_approval):
        response = client.post(
            f"/api/workflows/{at_plan_approval}/plan-preference-update",
            json={"decisions": {}, "preferences": {"risk_tolerance": "conservative"}})

        assert response.status_code == 200
        assert response.get_json()["developer_strategy"] == "safety_first"

    def test_an_explicit_preference_is_not_overridden_by_the_goal(
            self, client, at_plan_approval):
        # The goal fills in what the caller left out; it does not overrule what
        # the caller actually asked for.
        client.post(f"/api/workflows/{at_plan_approval}/plan-preference-update",
                    json={"decisions": {},
                          "preferences": {"developer_strategy": "safety_first",
                                          "impact_focus": "low"}})

        assert plan_of(client, at_plan_approval)["user_preferences"]["impact_focus"] == "low"

    def test_the_recommendation_survives_the_reranker(self, client, at_plan_approval):
        # generate_updated_plan_report sorts, filters and renumbers. The
        # recommendation must come out the other side, not be dropped.
        plan = client.post(
            f"/api/workflows/{at_plan_approval}/plan-preference-update",
            json={"decisions": {}, "preferences": {"developer_strategy": "balanced"}},
        ).get_json()["updated_planning_report"]

        assert plan["steps"]
        for step in plan["steps"]:
            assert step["decision_support"]["category"] in CATEGORIES

    def test_step_identity_survives_the_renumbering(self, client, at_plan_approval):
        """The frontend carries decisions across a re-rank by this identity."""
        before = {s["decision_support"]["step_identity"]
                  for s in plan_of(client, at_plan_approval)["steps"]}

        after_plan = client.post(
            f"/api/workflows/{at_plan_approval}/plan-preference-update",
            json={"decisions": {}, "preferences": {"developer_strategy": "safety_first"}},
        ).get_json()["updated_planning_report"]
        after = {s["decision_support"]["step_identity"] for s in after_plan["steps"]}

        assert before == after
        # ...and the step_ids genuinely were renumbered, which is why identity
        # is needed at all.
        assert [s["step_id"] for s in after_plan["steps"]] == list(
            range(1, len(after_plan["steps"]) + 1))


# ─────────────────────────────────────────────────────────────────────────────
# Approval flow
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestApprovalFlow:
    def test_only_approved_steps_are_forwarded(self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)
        decisions = {str(s["step_id"]): ("approve" if i == 0 else "reject")
                     for i, s in enumerate(plan["steps"])}

        response = client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                               json={"decision": "approve", "decisions": decisions})
        approved = response.get_json()["approved_plan"]

        assert [s["step_id"] for s in approved["steps"]] == [plan["steps"][0]["step_id"]]
        assert approved["approval"]["rejected_count"] == len(plan["steps"]) - 1

    def test_the_recommendation_survives_the_approved_plan_reduction(
            self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)
        decisions = {str(s["step_id"]): "approve" for s in plan["steps"][:1]}
        decisions.update({str(s["step_id"]): "reject" for s in plan["steps"][1:]})

        approved = client.post(
            f"/api/workflows/{at_plan_approval}/plan-decision",
            json={"decision": "approve", "decisions": decisions},
        ).get_json()["approved_plan"]

        for step in approved["steps"]:
            assert step["decision_support"]["category"] in CATEGORIES

    def test_the_summary_is_recomputed_over_the_reduced_plan(
            self, client, at_plan_approval):
        # Carrying a summary that counts twelve steps onto a plan holding one
        # is the stale figure that hides a filtering bug.
        plan = plan_of(client, at_plan_approval)
        decisions = {str(s["step_id"]): ("approve" if i == 0 else "reject")
                     for i, s in enumerate(plan["steps"])}

        approved = client.post(
            f"/api/workflows/{at_plan_approval}/plan-decision",
            json={"decision": "approve", "decisions": decisions},
        ).get_json()["approved_plan"]

        summary = approved["decision_support_summary"]
        assert summary["total_steps"] == len(approved["steps"]) == 1
        assert sum(summary[c] for c in CATEGORIES) == 1

    def test_developer_overrides_are_recorded_in_the_audit_trail(
            self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)
        # Approve everything, including whatever DIWO advised against.
        decisions = {str(s["step_id"]): "approve" for s in plan["steps"]}

        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "approve", "decisions": decisions})

        logs = client.get(f"/api/workflows/{at_plan_approval}/audit-logs").get_json()
        approved_log = next(log for log in logs if log["action"] == "plan_approved")
        overrides = approved_log["details"]["overrides"]

        expected = sum(
            1 for s in plan["steps"]
            if s["decision_support"]["category"] in (NOT_RECOMMENDED, MANUAL_ONLY))
        assert overrides["approved_not_recommended"] == expected


# ─────────────────────────────────────────────────────────────────────────────
# Rollback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestRollbackToPlanApproval:
    def test_the_restored_plan_carries_fresh_recommendations(
            self, client, at_transformation):
        response = client.post(f"/api/workflows/{at_transformation}/reset-to-plan-approval",
                               json={"reason": "reconsidering"})
        assert response.status_code == 200

        body = response.get_json()
        assert body["restored_full_plan"] is True

        plan = body["plan"]
        assert plan["decision_support_summary"]["total_steps"] == len(plan["steps"])
        for step in plan["steps"]:
            assert step["decision_support"]["category"] in CATEGORIES

    def test_rejected_steps_come_back_for_reconsideration(self, client, at_transformation):
        # plan_full_json is what makes this possible, and it must not be broken
        # by the enrichment.
        plan = client.post(
            f"/api/workflows/{at_transformation}/reset-to-plan-approval", json={},
        ).get_json()["plan"]

        assert len(plan["steps"]) > 1, "the rejected steps should be offered again"


# ─────────────────────────────────────────────────────────────────────────────
# Step-level feedback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestStepLevelFeedback:
    def _decide(self, client, wf_id, approve_first=1):
        plan = plan_of(client, wf_id)
        decisions = {str(s["step_id"]): ("approve" if i < approve_first else "reject")
                     for i, s in enumerate(plan["steps"])}
        return plan, decisions

    def test_a_rejected_step_records_what_was_rejected(self, client, at_plan_approval):
        plan, decisions = self._decide(client, at_plan_approval)
        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "modify", "decisions": decisions})

        rows = feedback_rows(client, "plan_step_rejected")
        assert rows, "a rejection must produce a step-level row"

        rejected_steps = {s["step_id"]: s for s in plan["steps"]
                          if decisions[str(s["step_id"])] == "reject"}
        assert len(rows) == len(rejected_steps)

        for row in rows:
            # The metadata §21 said was being lost, because build_approved_plan
            # had already removed the step by the time the row was written.
            assert row["smell_type"], "the rejected step's smell type must survive"
            assert row["refactoring_type"], "the rejected refactoring must survive"
            assert row["severity"], "the severity must survive"
            assert row["accepted"] == 0
            assert row["step_key"], "the row must be attributable to a specific step"

    def test_an_approved_step_records_what_was_approved(self, client, at_plan_approval):
        plan, decisions = self._decide(client, at_plan_approval)
        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "approve", "decisions": decisions})

        rows = feedback_rows(client, "plan_step_accepted")
        assert len(rows) == 1
        assert rows[0]["smell_type"]
        assert rows[0]["refactoring_type"]
        assert rows[0]["severity"]
        assert rows[0]["accepted"] == 1

    def test_modify_then_approve_does_not_double_count_a_rejection(
            self, client, at_plan_approval):
        """The frontend sends both for one review. One decision, one row."""
        _, decisions = self._decide(client, at_plan_approval)

        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "modify", "decisions": decisions})
        after_modify = len(feedback_rows(client, "plan_step_rejected"))

        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "approve", "decisions": decisions})
        after_approve = len(feedback_rows(client, "plan_step_rejected"))

        assert after_modify > 0
        assert after_approve == after_modify, "the same rejection was recorded twice"

        keys = [r["step_key"] for r in feedback_rows(client, "plan_step_rejected")]
        assert len(keys) == len(set(keys))

    def test_the_session_level_row_is_not_confused_with_the_step_level_ones(
            self, client, at_plan_approval):
        _, decisions = self._decide(client, at_plan_approval)
        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "approve", "decisions": decisions})

        # One session-level approval, and separately one row per step. The
        # session row must not be counted as evidence about any single step.
        session = feedback_rows(client, "plan_approved")
        assert len(session) == 1
        assert session[0]["step_key"] is None
        assert session[0]["smell_type"] is None

    def test_an_override_is_marked_in_the_row(self, client, at_plan_approval):
        plan = plan_of(client, at_plan_approval)
        decisions = {str(s["step_id"]): "approve" for s in plan["steps"]}
        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "approve", "decisions": decisions})

        overridden = [s for s in plan["steps"]
                      if s["decision_support"]["category"] in (NOT_RECOMMENDED, MANUAL_ONLY)]
        if not overridden:
            pytest.skip("the fixture produced nothing to override")

        rows = {r["step_key"]: r for r in feedback_rows(client, "plan_step_accepted")}
        for step in overridden:
            row = rows[step["decision_support"]["step_identity"]]
            assert "developer override" in row["reason"]

    def test_real_feedback_reaches_the_next_plan(self, app, client, make_workflow, smells):
        """Enough genuine decisions must start influencing the recommendation.

        This is the only path personalisation is allowed to take: real rows in
        feedback_entries, counted. The synthetic records the training script can
        produce never enter this table.
        """
        from db.workflow_repository import plan_step_acceptance_stats

        # Six workflows, each rejecting every step, is past the minimum sample.
        for _ in range(6):
            wf_id = make_workflow(smells)
            client.post(f"/api/workflows/{wf_id}/select-smells",
                        json={"selected_ids": [s["id"] for s in smells]})
            plan = plan_of(client, wf_id)
            decisions = {str(s["step_id"]): ("approve" if i == 0 else "reject")
                         for i, s in enumerate(plan["steps"])}
            client.post(f"/api/workflows/{wf_id}/plan-decision",
                        json={"decision": "approve", "decisions": decisions})

        with app.app_context():
            stats = plan_step_acceptance_stats()
        assert stats["observations"] >= 5
        assert stats["prior"] is not None

        # A new plan now has history to consult for at least one refactoring.
        fresh = make_workflow(smells)
        client.post(f"/api/workflows/{fresh}/select-smells",
                    json={"selected_ids": [s["id"] for s in smells]})
        observed = [
            s["decision_support"]["factors"]["historical_feedback"]
            for s in plan_of(client, fresh)["steps"]
        ]
        assert any(f["status"] == "observed" for f in observed)
        for factor in observed:
            if factor["status"] == "observed":
                assert factor["sample_size"] >= 5
                assert 0 <= factor["value"] <= 1

    def test_only_step_level_actions_are_counted_as_history(self, app, client, at_plan_approval):
        """A session-level approval is not evidence about twelve steps."""
        from db.workflow_repository import plan_step_acceptance_stats

        client.post(f"/api/workflows/{at_plan_approval}/plan-decision",
                    json={"decision": "reject", "feedback": {"reason": "no"}})

        with app.app_context():
            stats = plan_step_acceptance_stats()
        assert stats["observations"] == 0, "plan_rejected is not a step-level decision"


# ─────────────────────────────────────────────────────────────────────────────
# Degradation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
class TestDegradation:
    def test_stage_2_renders_with_no_impact_records(self, client, make_workflow):
        # A workflow whose smells produce no usable impact record still has to
        # reach plan approval with a recommendation on every step.
        smells = [{
            "id": "s1", "type": "LongMethod", "severity": "high", "line": 4,
            "location": {"file": "src/A.java", "class": "A", "method": "m", "lines": [4, 40]},
        }]
        wf_id = make_workflow(smells)
        response = client.post(f"/api/workflows/{wf_id}/select-smells",
                               json={"selected_ids": ["s1"]})

        assert response.status_code == 200
        for step in response.get_json()["plan"]["steps"]:
            assert step["decision_support"]["reasons"]

    def test_no_feedback_history_is_neutral_and_says_so(self, client, at_plan_approval):
        for step in plan_of(client, at_plan_approval)["steps"]:
            factor = step["decision_support"]["factors"]["historical_feedback"]
            assert factor["status"] == "insufficient_data"
            assert factor["sample_size"] == 0

    def test_the_stored_plan_is_valid_json_with_the_recommendation_in_it(
            self, client, at_plan_approval):
        # plan_json and plan_full_json both go through json.dumps; a
        # non-serializable value anywhere in the recommendation would break
        # persistence rather than just a display.
        plan = plan_of(client, at_plan_approval)
        round_tripped = json.loads(json.dumps(plan))
        assert round_tripped["steps"][0]["decision_support"]["score"] == \
            plan["steps"][0]["decision_support"]["score"]
