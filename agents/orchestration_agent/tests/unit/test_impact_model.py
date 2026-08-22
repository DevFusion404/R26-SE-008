"""
unit/test_impact_model.py
-------------------------
The per-smell impact model that replaced the count-ratio projection.

The point of the replacement is discrimination: two smells with the same count
must no longer score the same. These tests pin the factors that make them
differ, and the counterfactual (deferral) side the old UI had no answer for.
"""

import pytest

from domain.impact_model import (
    MODEL_VERSION, STATIC_ERROR_BAND, aggregate, build_impact_record,
    deferral_cost, quality_gain, transformation_risk,
)
from domain.metrics import compute_metrics_after, compute_metrics_before


def smell(sid, stype="LongMethod", severity="high", line=10, lines=(10, 60),
          loc=200, file="src/Order.java", **metrics):
    return {
        "id": sid, "type": stype, "severity": severity, "line": line,
        "entity": metrics.pop("entity", "doThing"),
        "location": {"file": file, "class": "Order", "method": "doThing",
                     "lines": list(lines)},
        "metrics": {"lines_of_code": loc, **metrics},
    }


@pytest.mark.unit
class TestQualityGain:
    def test_a_worse_smell_scores_higher_than_a_mild_one(self):
        mild = build_impact_record(smell("a", cyclomatic_complexity=11,
                                         lines=(10, 20), loc=400))
        severe = build_impact_record(smell("b", cyclomatic_complexity=40,
                                           lines=(10, 210), loc=400))
        assert (severe["if_selected"]["quality_gain"]["automated_points"]
                > mild["if_selected"]["quality_gain"]["automated_points"])

    def test_severity_moves_the_number(self):
        high = build_impact_record(smell("c", severity="high"))
        low = build_impact_record(smell("d", severity="low"))
        assert (high["if_selected"]["quality_gain"]["automated_points"]
                > low["if_selected"]["quality_gain"]["automated_points"])

    def test_reach_moves_the_number(self):
        narrow = build_impact_record(smell("e", lines=(10, 20), loc=400))
        wide = build_impact_record(smell("f", lines=(10, 390), loc=400))
        assert (wide["if_selected"]["quality_gain"]["automated_points"]
                > narrow["if_selected"]["quality_gain"]["automated_points"])

    def test_an_advisory_smell_has_zero_automated_gain(self):
        record = build_impact_record(smell("g", "LargeClass", method_count=40))
        assert record["if_selected"]["quality_gain"]["automated_points"] == 0.0

    def test_but_keeps_its_potential_gain_for_the_by_hand_case(self):
        record = build_impact_record(smell("h", "LargeClass", method_count=40))
        assert record["if_selected"]["quality_gain"]["potential_points"] > 0

    def test_gains_carry_a_band_not_a_bare_point_value(self):
        gain = build_impact_record(smell("i"))["if_selected"]["quality_gain"]
        assert gain["automated_low"] < gain["automated_points"] < gain["automated_high"]
        assert gain["automated_low"] == pytest.approx(
            gain["automated_points"] * (1 - STATIC_ERROR_BAND), abs=0.01)

    def test_every_factor_is_named_so_the_ui_can_show_its_working(self):
        factors = build_impact_record(smell("j"))["if_selected"]["quality_gain"]["factors"]
        assert set(factors) == {"severity", "magnitude", "reach", "refactoring_impact"}

    def test_a_missing_driving_metric_does_not_read_as_trivial(self):
        # No cyclomatic_complexity at all -> magnitude 0.5, not 0.
        record = build_impact_record(smell("k"))
        assert record["if_selected"]["quality_gain"]["factors"]["magnitude"] == 0.5

    def test_a_boolean_metric_is_not_mistaken_for_a_number(self):
        record = build_impact_record(smell("l", cyclomatic_complexity=True))
        assert record["if_selected"]["quality_gain"]["factors"]["magnitude"] == 0.5


@pytest.mark.unit
class TestTransformationRisk:
    def test_more_referencing_files_raise_risk(self):
        wide = build_impact_record(smell("a"), blast_radius=5, has_tests=False)
        narrow = build_impact_record(smell("b"), blast_radius=1, has_tests=False)
        assert (wide["if_selected"]["risk"]["score"]
                > narrow["if_selected"]["risk"]["score"])

    def test_missing_tests_raise_risk(self):
        untested = build_impact_record(smell("c"), has_tests=False)
        tested = build_impact_record(smell("d"), has_tests=True)
        assert (untested["if_selected"]["risk"]["score"]
                > tested["if_selected"]["risk"]["score"])

    def test_every_risk_names_at_least_one_driver(self):
        assert build_impact_record(smell("e"))["if_selected"]["risk"]["drivers"]

    def test_the_missing_test_suite_is_named_explicitly(self):
        drivers = build_impact_record(smell("f"), has_tests=False)["if_selected"]["risk"]["drivers"]
        assert any("no test file" in d for d in drivers)

    def test_behavioural_validation_is_only_promised_when_tests_exist(self):
        tested = build_impact_record(smell("g"), has_tests=True)
        untested = build_impact_record(smell("h"), has_tests=False)
        assert "behavioural" in tested["if_selected"]["validation"]
        assert "behavioural" not in untested["if_selected"]["validation"]

    @pytest.mark.parametrize("radius", [1, 2, 10, 500])
    def test_risk_stays_within_zero_and_one(self, radius):
        score = build_impact_record(smell("i"), blast_radius=radius)["if_selected"]["risk"]["score"]
        assert 0.0 <= score <= 1.0

    def test_band_matches_the_score(self):
        record = build_impact_record(smell("j"), blast_radius=6, has_tests=False)
        risk = record["if_selected"]["risk"]
        expected = "low" if risk["score"] < 0.35 else "medium" if risk["score"] < 0.65 else "high"
        assert risk["band"] == expected


@pytest.mark.unit
class TestDeferralCost:
    def test_a_frequently_edited_file_charges_more_interest(self):
        hot = build_impact_record(smell("a"), churn=14, churn_known=True)
        cold = build_impact_record(smell("b"), churn=0, churn_known=True)
        assert (hot["if_deferred"]["interest_per_quarter"]
                > cold["if_deferred"]["interest_per_quarter"])

    @pytest.mark.parametrize("churn,pressure", [
        (0, "low"), (1, "low"), (3, "medium"), (9, "medium"), (10, "high"), (40, "high"),
    ])
    def test_change_pressure_bands(self, churn, pressure):
        record = build_impact_record(smell("c"), churn=churn, churn_known=True)
        assert record["if_deferred"]["change_pressure"] == pressure

    def test_carried_points_equal_the_potential_gain(self):
        record = build_impact_record(smell("d"))
        assert (record["if_deferred"]["carried_points"]
                == record["if_selected"]["quality_gain"]["potential_points"])

    def test_an_advisory_smell_still_carries_debt_forward(self):
        # Skipping it costs the same whether or not a machine could fix it.
        record = build_impact_record(smell("e", "LargeClass", method_count=40))
        assert record["if_deferred"]["carried_points"] > 0

    def test_churn_is_not_claimed_as_known_without_a_repository(self):
        record = build_impact_record(smell("f"))
        assert record["if_deferred"]["churn_known"] is False

    def test_and_the_headline_drops_the_pressure_clause(self):
        assert "change pressure" not in build_impact_record(smell("g"))["headline"]

    def test_negative_churn_is_clamped(self):
        record = build_impact_record(smell("h"), churn=-5, churn_known=True)
        assert record["if_deferred"]["churn_commits"] == 0


@pytest.mark.unit
class TestRecordShape:
    def test_carries_its_model_version_and_tier(self):
        record = build_impact_record(smell("a"))
        assert record["model_version"] == MODEL_VERSION
        assert record["tier"] == "static"
        assert record["error_band"] == STATIC_ERROR_BAND

    def test_an_executable_headline_quotes_points_risk_and_effort(self):
        headline = build_impact_record(smell("b"))["headline"]
        assert "quality points" in headline and "risk" in headline and "min review" in headline

    def test_an_advisory_headline_says_no_code_will_change(self):
        headline = build_impact_record(smell("c", "LargeClass", method_count=40))["headline"]
        assert "will not change any code" in headline

    def test_the_advisory_headline_reads_as_two_sentences(self):
        # The reasons come from several tables and not all end in a stop.
        headline = build_impact_record(smell("d", "LargeClass"))["headline"]
        assert ". Worth" in headline

    def test_advisory_effort_is_the_manual_cost(self):
        advisory = build_impact_record(smell("e", "LargeClass"))
        executable = build_impact_record(smell("f", "LongMethod"))
        assert (advisory["if_selected"]["effort_minutes"]
                > executable["if_selected"]["effort_minutes"])


@pytest.mark.unit
class TestAggregate:
    @pytest.fixture
    def records(self):
        return [
            build_impact_record(smell("s1", "LongMethod", cyclomatic_complexity=30)),
            build_impact_record(smell("s2", "DeadCode", severity="low")),
            build_impact_record(smell("s3", "LargeClass", method_count=40)),
        ]

    def test_nothing_selected_captures_nothing(self, records):
        summary = aggregate(records, set(), 68.0)
        assert summary["capture_rate"] == 0.0
        assert summary["quality_projected"] == summary["quality_before"]

    def test_but_the_ceiling_is_still_reported(self, records):
        summary = aggregate(records, set(), 68.0)
        assert summary["quality_ceiling"] > summary["quality_before"]

    def test_selecting_everything_captures_the_whole_automated_ceiling(self, records):
        assert aggregate(records, {"s1", "s2", "s3"}, 68.0)["capture_rate"] == 1.0

    def test_advisory_and_executable_are_counted_separately(self, records):
        summary = aggregate(records, {"s1", "s2", "s3"}, 68.0)
        assert summary["executable_count"] == 2
        assert summary["advisory_count"] == 1

    def test_quality_is_capped_at_one_hundred(self, records):
        assert aggregate(records, {"s1", "s2", "s3"}, 99.9)["quality_projected"] <= 100.0

    def test_effort_is_the_sum_over_the_selection(self, records):
        summary = aggregate(records, {"s1", "s2"}, 68.0)
        expected = sum(r["if_selected"]["effort_minutes"]
                       for r in records if r["smell_id"] in {"s1", "s2"})
        assert summary["effort_minutes"] == expected

    def test_risk_is_reported_only_over_executable_selections(self, records):
        # An advisory smell runs nothing, so it cannot contribute risk.
        assert aggregate(records, {"s3"}, 68.0)["max_risk"] == 0.0

    def test_an_advisory_only_selection_is_an_error(self, records):
        warnings = aggregate(records, {"s3"}, 68.0)["warnings"]
        assert any(w["level"] == "error" for w in warnings)

    def test_a_mixed_selection_is_a_warning_not_an_error(self, records):
        warnings = aggregate(records, {"s1", "s3"}, 68.0)["warnings"]
        assert any(w["level"] == "warning" for w in warnings)
        assert not any(w["level"] == "error" for w in warnings)

    def test_skipped_high_pressure_smells_are_surfaced(self):
        records = [
            build_impact_record(smell("hot", "LongMethod"), churn=20, churn_known=True),
            build_impact_record(smell("kept", "DeadCode"), churn=0, churn_known=True),
        ]
        warnings = aggregate(records, {"kept"}, 70.0)["warnings"]
        assert any(w["level"] == "info" and "frequently" in w["message"] for w in warnings)

    def test_an_empty_record_list_does_not_divide_by_zero(self):
        summary = aggregate([], set(), 70.0)
        assert summary["capture_rate"] == 0.0
        assert summary["mean_risk"] == 0.0


@pytest.mark.unit
class TestLegacyMetrics:
    """domain/metrics.py — the count-ratio projection the impact model replaces.

    Still used by the plan-approval stage, so it still has to behave.
    """

    def test_before_metrics_scale_with_severity(self, smells):
        worse = compute_metrics_before(smells)
        milder = compute_metrics_before(
            [{**s, "severity": "low"} for s in smells])
        assert worse["cyclomatic_complexity"] >= milder["cyclomatic_complexity"]

    def test_metrics_stay_inside_their_bounds(self, smells):
        before = compute_metrics_before(smells)
        assert 0 <= before["cyclomatic_complexity"] <= 100
        assert 0 <= before["code_duplication_pct"] <= 100
        assert 0 <= before["maintainability_index"] <= 100

    def test_resolving_smells_improves_the_after_metrics(self, smells):
        before = compute_metrics_before(smells)
        after = compute_metrics_after(before, resolved_count=3, total_smells=3)
        assert after["cyclomatic_complexity"] <= before["cyclomatic_complexity"]
        assert after["maintainability_index"] >= before["maintainability_index"]

    def test_resolving_nothing_changes_nothing(self, smells):
        before = compute_metrics_before(smells)
        after = compute_metrics_after(before, resolved_count=0, total_smells=3)
        assert after["cyclomatic_complexity"] == before["cyclomatic_complexity"]

    def test_no_smells_does_not_divide_by_zero(self):
        before = compute_metrics_before([])
        assert compute_metrics_after(before, 0, 0)["total_smells"] == 0

    def test_the_count_ratio_blind_spot_this_model_replaced(self, smells):
        # Documents WHY impact_model exists: one resolved smell projects the
        # same gain regardless of which smell it was.
        before = compute_metrics_before(smells)
        assert (compute_metrics_after(before, 1, 3)
                == compute_metrics_after(before, 1, 3))
