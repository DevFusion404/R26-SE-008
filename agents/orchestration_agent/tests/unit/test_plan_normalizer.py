"""
unit/test_plan_normalizer.py
----------------------------
Plan shaping: the filtered report becomes an RDP request, RDP's answer becomes
the shape the workflow expects, and the developer's approval reduces it.

build_approved_plan is the second critical hand-off in the system. A rejected
step left in the JSON would be mapped to an SCTVA action and executed.
"""

import pytest

from domain.plan_normalizer import (
    build_approved_plan, build_rdp_plan_input, generate_refactoring_plan,
    generate_updated_plan_report, normalize_rdp_plan,
)


def step(step_id, refactoring="Extract Method", risk="low", impact="high", **extra):
    return {
        "step_id": step_id,
        "smell_id": f"smell_{step_id:03d}",
        "refactoring": refactoring,
        "risk": risk,
        "expected_impact": impact,
        "target": {"class": "Order", "method": "m", "file": "src/Order.java"},
        "parameters": {"source_file": "src/Order.java"},
        **extra,
    }


@pytest.mark.unit
class TestBuildRdpPlanInput:
    def test_files_with_no_selected_smells_are_dropped(self):
        payload = build_rdp_plan_input({"files": [
            {"relative_path": "src/A.java", "code_smells": [{"type": "LongMethod"}]},
            {"relative_path": "src/B.java", "code_smells": []},
        ]})
        assert [f["file"] for f in payload["files"]] == ["src/A.java"]

    def test_because_rdp_names_the_plan_after_the_first_file(self):
        # An unselected file sitting first would name the whole plan after
        # itself, which is why the empty ones are removed here and not later.
        payload = build_rdp_plan_input({"files": [
            {"relative_path": "src/Untouched.java", "code_smells": []},
            {"relative_path": "src/Real.java", "code_smells": [{"type": "LongMethod"}]},
        ]})
        assert payload["files"][0]["file"] == "src/Real.java"

    def test_file_is_the_repo_relative_path_not_the_basename(self):
        payload = build_rdp_plan_input({"files": [
            {"relative_path": "src/util/Helper.java", "file": "Helper.java",
             "code_smells": [{"type": "DeadCode"}]},
        ]})
        assert payload["files"][0]["file"] == "src/util/Helper.java"

    def test_summary_is_recomputed_from_the_files_actually_sent(self):
        payload = build_rdp_plan_input({
            "summary": {"total_code_smells": 99, "files_analyzed": 99},
            "files": [
                {"relative_path": "src/A.java", "code_smells": [{"type": "X", "severity": "high"}]},
                {"relative_path": "src/B.java", "code_smells": []},
            ],
        })
        assert payload["summary"]["total_code_smells"] == 1
        assert payload["summary"]["files_analyzed"] == 1

    def test_repo_name_is_forwarded_when_present(self):
        payload = build_rdp_plan_input({"repo_name": "demo",
                                        "files": [{"relative_path": "a", "code_smells": [{}]}]})
        assert payload["repo_name"] == "demo"

    def test_an_empty_report_produces_an_empty_payload(self):
        assert build_rdp_plan_input({})["files"] == []
        assert build_rdp_plan_input(None)["files"] == []


@pytest.mark.unit
class TestNormalizeRdpPlan:
    def test_string_summary_becomes_an_object(self):
        # RDP serializes summary as prose; every DIWO stage reads
        # plan["summary"]["total_steps"].
        plan = normalize_rdp_plan({"plan_id": "p", "steps": [step(1)],
                                   "summary": "1-step plan addressing 1 smell."})
        assert plan["summary"]["total_steps"] == 1
        assert plan["summary_text"] == "1-step plan addressing 1 smell."

    def test_total_steps_always_matches_the_step_list(self):
        plan = normalize_rdp_plan({"steps": [step(1), step(2)],
                                   "summary": {"total_steps": 99}})
        assert plan["summary"]["total_steps"] == 2

    def test_source_is_stamped_as_the_rdp_agent(self):
        assert normalize_rdp_plan({"steps": []})["source"] == "rdp_agent"

    def test_basenames_are_restored_to_repo_relative_paths(self):
        # RDP flattens every path to its basename; the transformation stage
        # resolves those against the CUQA workspace, so the folders go back on.
        plan_input = {"files": [{"relative_path": "src/util/Helper.java"}]}
        plan = normalize_rdp_plan({
            "steps": [{"step_id": 1, "refactoring": "Extract Method",
                       "target": {"file": "Helper.java"},
                       "parameters": {"source_file": "Helper.java"}}],
            "target": "Helper.java",
        }, plan_input)
        assert plan["steps"][0]["target"]["file"] == "src/util/Helper.java"
        assert plan["steps"][0]["parameters"]["source_file"] == "src/util/Helper.java"
        assert plan["target"] == "src/util/Helper.java"

    def test_an_ambiguous_basename_is_left_alone(self):
        # Two different Helper.java: guessing would point a refactoring at the
        # wrong file, so the basename stays as-is.
        plan_input = {"files": [{"relative_path": "src/a/Helper.java"},
                                {"relative_path": "src/b/Helper.java"}]}
        plan = normalize_rdp_plan({
            "steps": [{"step_id": 1, "refactoring": "Extract Method",
                       "parameters": {"source_file": "Helper.java"}}],
        }, plan_input)
        assert plan["steps"][0]["parameters"]["source_file"] == "Helper.java"

    def test_a_path_that_already_has_folders_is_untouched(self):
        plan_input = {"files": [{"relative_path": "src/Helper.java"}]}
        plan = normalize_rdp_plan({
            "steps": [{"step_id": 1, "refactoring": "X",
                       "parameters": {"source_file": "other/Helper.java"}}],
        }, plan_input)
        assert plan["steps"][0]["parameters"]["source_file"] == "other/Helper.java"

    def test_risk_and_impact_counts_are_derived_when_absent(self):
        plan = normalize_rdp_plan({"steps": [
            step(1, risk="low", impact="high"),
            step(2, risk="high", impact="low"),
        ]})
        assert plan["summary"]["high_impact"] == 1
        assert plan["summary"]["risks"]["low"] == 1
        assert plan["summary"]["risks"]["high"] == 1


@pytest.mark.unit
class TestBuildApprovedPlan:
    """Only approved steps may continue. This is the guarantee SCTVA relies on."""

    def test_rejected_steps_are_removed(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)]}
        approved = build_approved_plan(plan, {"1": "approve", "2": "reject", "3": "reject"})
        assert [s["step_id"] for s in approved["steps"]] == [1]

    def test_pending_steps_are_not_executed(self):
        # A step the developer never decided on is not an implicit approval.
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        approved = build_approved_plan(plan, {"1": "approve"})
        assert [s["step_id"] for s in approved["steps"]] == [1]
        assert approved["approval"]["pending_step_ids"] == [2]

    def test_step_ids_are_preserved_not_renumbered(self):
        # Every action SCTVA reports has to trace back to the reviewed step.
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)]}
        approved = build_approved_plan(plan, {"1": "reject", "2": "approve", "3": "approve"})
        assert [s["step_id"] for s in approved["steps"]] == [2, 3]

    def test_integer_and_string_decision_keys_both_work(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        assert len(build_approved_plan(plan, {1: "approve"})["steps"]) == 1
        assert len(build_approved_plan(plan, {"1": "approve"})["steps"]) == 1

    def test_summary_is_recomputed_against_the_survivors(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)],
                "summary": {"total_steps": 3, "high_impact": 3}}
        approved = build_approved_plan(plan, {"1": "approve", "2": "reject", "3": "reject"})
        assert approved["summary"]["total_steps"] == 1
        assert approved["summary"]["high_impact"] == 1

    def test_what_was_rejected_is_recorded_not_dropped(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        approved = build_approved_plan(plan, {"1": "approve", "2": "reject"})
        assert approved["approval"]["rejected_step_ids"] == [2]
        assert approved["approval"]["approved_count"] == 1
        assert approved["approval"]["original_total_steps"] == 2

    def test_reducing_twice_keeps_the_first_pass_rejections(self):
        # The second pass only sees the survivors, so its own rejected list
        # would come back empty and the audit trail would lose the verdict.
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)]}
        first = build_approved_plan(plan, {"1": "approve", "2": "approve", "3": "reject"})
        second = build_approved_plan(first, {"1": "approve", "2": "reject"})
        assert set(second["approval"]["rejected_step_ids"]) == {2, 3}
        assert second["approval"]["original_total_steps"] == 3

    def test_source_plan_id_survives_a_second_reduction(self):
        plan = {"plan_id": "original", "steps": [step(1), step(2)]}
        first = build_approved_plan(plan, {"1": "approve", "2": "approve"})
        second = build_approved_plan(first, {"1": "approve", "2": "reject"})
        assert second["approval"]["source_plan_id"] == "original"

    def test_rejecting_everything_yields_an_empty_but_valid_plan(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        approved = build_approved_plan(plan, {"1": "reject", "2": "reject"})
        assert approved["steps"] == []
        assert approved["summary"]["total_steps"] == 0

    def test_no_decisions_at_all_approves_nothing(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        assert build_approved_plan(plan, {})["steps"] == []


@pytest.mark.unit
class TestFallbackPlanner:
    def test_one_step_per_selected_smell(self, smells):
        plan = generate_refactoring_plan(smells, "OrderService")
        assert len(plan["steps"]) == 3
        assert plan["summary"]["total_steps"] == 3

    def test_each_step_traces_back_to_its_smell(self, smells):
        plan = generate_refactoring_plan(smells, "OrderService")
        assert [s["smell_id"] for s in plan["steps"]] == [s["id"] for s in smells]

    def test_smell_type_drives_the_refactoring_chosen(self, smells):
        plan = generate_refactoring_plan(smells, "OrderService")
        assert plan["steps"][0]["refactoring"] == "Extract Method"     # LongMethod
        assert plan["steps"][1]["refactoring"] == "Extract Class"      # LargeClass
        assert plan["steps"][2]["refactoring"] == "Remove Dead Code"   # DeadCode

    def test_an_unmapped_smell_type_still_produces_a_step(self):
        plan = generate_refactoring_plan(
            [{"type": "NeverSeenBefore", "id": "x", "location": {}, "metrics": {}}],
            "T.java")
        assert len(plan["steps"]) == 1

    def test_an_empty_selection_yields_an_empty_plan(self):
        plan = generate_refactoring_plan([], "T.java")
        assert plan["steps"] == []
        assert plan["summary"]["total_steps"] == 0


@pytest.mark.unit
class TestPreferenceReranking:
    def test_rejected_steps_are_filtered_out(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)]}
        updated = generate_updated_plan_report(plan, {"2": "reject"}, {})
        assert len(updated["steps"]) == 2

    def test_steps_are_renumbered_after_reranking(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2), step(3)]}
        updated = generate_updated_plan_report(plan, {"1": "reject"}, {})
        assert [s["step_id"] for s in updated["steps"]] == [1, 2]

    def test_approved_steps_rank_above_undecided_ones(self):
        plan = {"plan_id": "p", "steps": [step(1), step(2)]}
        updated = generate_updated_plan_report(plan, {"2": "approve"}, {})
        assert updated["steps"][0]["smell_id"] == "smell_002"

    @pytest.mark.parametrize("tolerance", ["conservative", "balanced", "aggressive"])
    def test_every_risk_tolerance_produces_a_valid_plan(self, tolerance):
        plan = {"plan_id": "p", "steps": [step(1, risk="low"), step(2, risk="high")]}
        updated = generate_updated_plan_report(plan, {}, {"risk_tolerance": tolerance})
        assert updated["summary_meta"]["risk_tolerance"] == tolerance
        assert len(updated["steps"]) == 2

    def test_preferred_refactorings_are_promoted(self):
        plan = {"plan_id": "p", "steps": [
            step(1, refactoring="Extract Method"),
            step(2, refactoring="Remove Dead Code"),
        ]}
        updated = generate_updated_plan_report(
            plan, {}, {"preferred_refactorings": ["Remove Dead Code"]})
        assert updated["steps"][0]["refactoring"] == "Remove Dead Code"

    def test_the_updated_plan_gets_its_own_id(self):
        updated = generate_updated_plan_report({"plan_id": "plan_x", "steps": []}, {}, {})
        assert updated["plan_id"] == "plan_x_updated"
