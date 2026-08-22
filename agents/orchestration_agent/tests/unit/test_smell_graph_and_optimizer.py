"""
unit/test_smell_graph_and_optimizer.py
--------------------------------------
Two pure modules that make the selection panel more than a checkbox list:

  smell_graph          selections are not independent - containment, overlap
                       and clone relationships change the arithmetic
  selection_optimizer  choosing under a review-time budget is a 0/1 knapsack

The optimiser is asserted against exhaustive search, so "optimal" is a proven
claim rather than a hopeful one.
"""

import itertools

import pytest

from domain.capability_map import EXECUTABLE
from domain.impact_model import build_impact_record
from domain.selection_optimizer import (
    DEFAULT_BUDGET_MINUTES, PRESETS, optimise, optimise_preset,
)
from domain.smell_graph import CLONE_OF, CONTAINS, OVERLAPS, build_edges, selection_notes


def smell(sid, stype="LongMethod", severity="high", line=10, lines=(10, 60),
          file="src/Order.java", loc=200, **metrics):
    return {
        "id": sid, "type": stype, "severity": severity, "line": line,
        "entity": metrics.pop("entity", "doThing"),
        "location": {"file": file, "class": "Order", "method": "doThing",
                     "lines": list(lines)},
        "metrics": {"lines_of_code": loc, **metrics},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Smell graph
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSmellGraph:
    @pytest.fixture
    def graph(self):
        return [
            smell("cls", "LargeClass", lines=(1, 200), line=1),
            smell("m1", "LongMethod", lines=(20, 60), line=20),
            smell("m2", "LongMethod", lines=(50, 90), line=50),
            smell("far", "DeadCode", lines=(500, 510), line=500),
            smell("dupA", "DuplicateCode", lines=(300, 320), line=300, file="src/A.java"),
            smell("dupB", "DuplicateCode", lines=(400, 420), line=400, file="src/B.java"),
        ]

    def test_a_class_level_smell_contains_the_members_inside_it(self, graph):
        edges = build_edges(graph)
        contains = [e for e in edges if e["type"] == CONTAINS]
        assert {e["to"] for e in contains} >= {"m1", "m2"}
        assert all(e["from"] == "cls" for e in contains)

    def test_two_methods_sharing_lines_overlap(self, graph):
        edges = build_edges(graph)
        overlaps = [e for e in edges if e["type"] == OVERLAPS]
        assert any({e["from"], e["to"]} == {"m1", "m2"} for e in overlaps)

    def test_the_overlap_note_names_the_shared_range(self, graph):
        edges = build_edges(graph)
        overlap = next(e for e in edges if e["type"] == OVERLAPS
                       and {e["from"], e["to"]} == {"m1", "m2"})
        assert "50" in overlap["note"] and "60" in overlap["note"]

    def test_disjoint_smells_produce_no_edge(self, graph):
        edges = build_edges(graph)
        assert not [e for e in edges if "far" in (e["from"], e["to"])
                    and e["type"] != CLONE_OF]

    def test_clone_pairs_are_linked_across_files(self, graph):
        edges = build_edges(graph)
        clones = [e for e in edges if e["type"] == CLONE_OF]
        assert any({e["from"], e["to"]} == {"dupA", "dupB"} for e in clones)

    def test_smells_in_different_files_do_not_overlap(self):
        edges = build_edges([
            smell("a", lines=(1, 100), file="src/A.java"),
            smell("b", lines=(1, 100), file="src/B.java"),
        ])
        assert not [e for e in edges if e["type"] == OVERLAPS]

    def test_an_empty_or_missing_smell_list_is_safe(self):
        assert build_edges([]) == []
        assert build_edges(None) == []


@pytest.mark.unit
class TestSelectionNotes:
    @pytest.fixture
    def edges(self):
        return build_edges([
            smell("cls", "LargeClass", lines=(1, 200), line=1),
            smell("m1", "LongMethod", lines=(20, 60), line=20),
            smell("m2", "LongMethod", lines=(50, 90), line=50),
            smell("dupA", "DuplicateCode", lines=(300, 320), line=300, file="src/A.java"),
            smell("dupB", "DuplicateCode", lines=(400, 420), line=400, file="src/B.java"),
        ])

    def test_selecting_two_overlapping_smells_warns_about_ordering(self, edges):
        notes = selection_notes(edges, {"m1", "m2"})
        assert any("Ordering conflict" in n["message"] for n in notes)

    def test_selecting_only_one_of_them_does_not(self, edges):
        notes = selection_notes(edges, {"m1"})
        assert not any("Ordering conflict" in n["message"] for n in notes)

    def test_selecting_a_member_without_its_container_is_flagged(self, edges):
        notes = selection_notes(edges, {"m1"})
        assert any("container stays flagged" in n["message"] for n in notes)

    def test_two_members_of_one_container_collapse_into_a_single_note(self, edges):
        # Three identical sentences would bury the notes that actually differ.
        notes = selection_notes(edges, {"m1", "m2"})
        containment = [n for n in notes if "container stays flagged" in n["message"]]
        assert len(containment) == 1
        assert "2 selected smells" in containment[0]["message"]
        assert set(containment[0]["smell_ids"]) == {"cls", "m1", "m2"}

    def test_selecting_one_clone_of_a_pair_warns_about_the_other(self, edges):
        notes = selection_notes(edges, {"dupA"})
        assert any("clone" in n["message"].lower() for n in notes)

    def test_selecting_both_clones_does_not_warn(self, edges):
        notes = selection_notes(edges, {"dupA", "dupB"})
        assert not any("clone" in n["message"].lower() for n in notes)

    def test_an_empty_selection_produces_no_notes(self, edges):
        assert selection_notes(edges, set()) == []

    def test_notes_are_deduplicated(self, edges):
        notes = selection_notes(edges, {"m1", "m2"})
        seen = [(n["level"], n["message"]) for n in notes]
        assert len(seen) == len(set(seen))

    def test_every_note_names_the_smells_it_is_about(self, edges):
        for note in selection_notes(edges, {"m1", "m2", "dupA"}):
            assert note["smell_ids"]


# ─────────────────────────────────────────────────────────────────────────────
# Optimiser
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSelectionOptimizer:
    @pytest.fixture
    def pool(self):
        return [
            build_impact_record(smell("big", "LongMethod", cyclomatic_complexity=40,
                                      lines=(1, 180), loc=200)),      # 12 min
            build_impact_record(smell("dead", "DeadCode", severity="low")),   # 4 min
            build_impact_record(smell("magic", "MagicNumber", severity="low")),  # 5 min
            build_impact_record(smell("cls", "LargeClass", method_count=40)),  # advisory
        ]

    def test_an_unlimited_budget_takes_every_executable_smell(self, pool):
        result = optimise(pool, budget_minutes=1000)
        assert set(result["selected_ids"]) == {"big", "dead", "magic"}

    def test_an_advisory_smell_is_never_proposed(self, pool):
        # Spending budget on a no-op is the defect this feature removes.
        assert "cls" not in optimise(pool, budget_minutes=1000)["selected_ids"]

    def test_and_it_is_reported_as_skipped(self, pool):
        assert optimise(pool, budget_minutes=1000)["skipped_advisory"] == 1

    @pytest.mark.parametrize("budget", [0, 1, 3, 4, 5, 9, 12, 16, 21, 60])
    def test_the_budget_is_never_exceeded(self, pool, budget):
        assert optimise(pool, budget_minutes=budget)["total_minutes"] <= budget

    def test_a_zero_budget_selects_nothing(self, pool):
        assert optimise(pool, budget_minutes=0)["selected_ids"] == []

    def test_a_negative_budget_selects_nothing(self, pool):
        assert optimise(pool, budget_minutes=-10)["selected_ids"] == []

    @pytest.mark.parametrize("budget", [4, 5, 9, 12, 16, 17, 21])
    def test_the_result_matches_exhaustive_search(self, pool, budget):
        """Optimality is asserted, not assumed."""
        items = [r for r in pool if r["capability"]["status"] == EXECUTABLE]
        best = 0.0
        for size in range(len(items) + 1):
            for combo in itertools.combinations(items, size):
                minutes = sum(r["if_selected"]["effort_minutes"] for r in combo)
                if minutes <= budget:
                    best = max(best, sum(
                        r["if_selected"]["quality_gain"]["automated_points"]
                        for r in combo))
        assert optimise(pool, budget_minutes=budget)["total_value"] == pytest.approx(best)

    def test_a_risk_ceiling_excludes_risky_items(self, pool):
        result = optimise(pool, budget_minutes=1000, max_risk=0.0)
        assert result["selected_ids"] == []
        assert result["skipped_risky"] > 0

    def test_an_empty_pool_is_handled(self):
        result = optimise([], budget_minutes=60)
        assert result["selected_ids"] == []
        assert result["total_value"] == 0.0

    def test_all_advisory_pool_selects_nothing(self):
        pool = [build_impact_record(smell("a", "LargeClass")),
                build_impact_record(smell("b", "FeatureEnvy"))]
        result = optimise(pool, budget_minutes=1000)
        assert result["selected_ids"] == []
        assert result["skipped_advisory"] == 2


@pytest.mark.unit
class TestOptimiserPresets:
    @pytest.fixture
    def pool(self):
        return [
            build_impact_record(smell("safe", "DeadCode", severity="low"),
                                blast_radius=1, has_tests=True, churn=0, churn_known=True),
            build_impact_record(smell("risky", "LongMethod", cyclomatic_complexity=40),
                                blast_radius=6, has_tests=False, churn=30, churn_known=True),
        ]

    @pytest.mark.parametrize("preset", list(PRESETS))
    def test_every_preset_runs_and_labels_itself(self, pool, preset):
        result = optimise_preset(pool, preset=preset, budget_minutes=60)
        assert result["preset"] == preset
        assert result["preset_label"]
        assert result["preset_description"]

    def test_safe_wins_excludes_high_risk_items(self, pool):
        result = optimise_preset(pool, preset="safe_wins", budget_minutes=1000)
        chosen = {r["smell_id"] for r in pool if r["smell_id"] in result["selected_ids"]}
        assert "risky" not in chosen

    def test_stop_bleeding_optimises_interest_not_points(self, pool):
        # The high-churn smell is the expensive one to defer, so an
        # interest-driven objective must reach for it.
        result = optimise_preset(pool, preset="stop_bleeding", budget_minutes=12)
        assert "risky" in result["selected_ids"]

    def test_an_unknown_preset_falls_back_to_best_value(self, pool):
        result = optimise_preset(pool, preset="not_a_preset", budget_minutes=60)
        assert result["preset"] == "best_value"

    def test_the_default_budget_is_exposed(self):
        assert DEFAULT_BUDGET_MINUTES > 0
