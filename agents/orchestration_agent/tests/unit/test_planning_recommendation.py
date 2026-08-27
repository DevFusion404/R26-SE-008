"""
unit/test_planning_recommendation.py
------------------------------------
Stage 2 decision support: the recommendation attached to every RDP plan step.

Three things are asserted here, in the order they matter:

  1. THE SAFETY GATES ARE NOT NEGOTIABLE. An advisory capability, an unknown
     one, and a concrete step the real SCTVA mapper cannot map each decide the
     category outright — and a high-risk step can never be marked green,
     however high RDP scored it. A gate a big enough number can talk its way
     past is not a gate, and `auto_select_eligible` is what the "Select
     Recommended" button reads.

  2. IT DEGRADES, IT DOES NOT CRASH. Stage 2 has to render when CUQA never
     ran, when RDP sent no score, when there is no prediction and when the
     feedback table is empty. Every one of those is a stated fallback here.

  3. THE ARITHMETIC IS THE PUBLISHED ARITHMETIC. 35/25/20/10/10, every term
     exposed, and no factor able to exceed its own weight.
"""

import pytest

from domain.impact_model import build_impact_record
from domain.planning_recommendation import (
    BALANCED, MANUAL_ONLY, MAX_IMPROVEMENT, MIN_FEEDBACK_OBSERVATIONS,
    NOT_RECOMMENDED, RECOMMENDED, RECOMMENDED_THRESHOLD, REVIEW,
    REVIEW_THRESHOLD, SAFETY_FIRST, WEIGHTS, assess_step_mapping,
    build_step_recommendation, normalize_rdp_score, preferences_for_strategy,
    step_identity, strategy_from_preferences, summarize_recommendations,
)


def step(**overrides):
    """A complete, executable Extract Method step — the happy path to perturb."""
    base = {
        "step_id": 1,
        "smell_id": "src/Order.java:10:0",
        "smell_type": "LongMethod",
        "severity": "high",
        "refactoring": "Extract Method",
        "risk": "low",
        "expected_impact": "high",
        "score": 0.84,
        "target": {"class": "Order", "method": "calculateTotal",
                   "file": "src/Order.java", "lines": [10, 130]},
        "parameters": {"source_file": "src/Order.java", "start_line": 10,
                       "end_line": 130, "new_method_name": "calculateTotalCore"},
        "explanation": "Apply Extract Method on Order.calculateTotal.",
    }
    base.update(overrides)
    return base


def impact_record(smell_type="LongMethod", severity="high", **kwargs):
    """A real Stage 1 record, built by the real impact model."""
    smell = {
        "id": "src/Order.java:10:0",
        "type": smell_type,
        "severity": severity,
        "line": 10,
        "entity": "calculateTotal",
        "location": {"file": "src/Order.java", "class": "Order",
                     "method": "calculateTotal", "lines": [10, 130]},
        "metrics": {"lines_of_code": 240, "cyclomatic_complexity": 32},
    }
    options = {"blast_radius": 1, "has_tests": True, "churn": 12, "churn_known": True}
    options.update(kwargs)
    return build_impact_record(smell, **options)


# ─────────────────────────────────────────────────────────────────────────────
# 1-2. The happy path, and the middle of the range
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestCategories:
    def test_executable_high_benefit_low_risk_is_recommended(self):
        result = build_step_recommendation(step(), impact_record=impact_record())

        assert result["category"] == RECOMMENDED
        assert result["auto_select_eligible"] is True
        assert result["score"] >= RECOMMENDED_THRESHOLD
        assert result["capability"]["status"] == "executable"
        assert result["capability"]["actual_step_mappable"] is True

    def test_moderate_step_is_review_not_recommended(self):
        # Worth doing, but medium risk and a middling MCDA score: the developer
        # should read this one rather than batch-approve it.
        result = build_step_recommendation(
            step(risk="medium", expected_impact="high", score=0.72))

        assert result["category"] == REVIEW
        assert result["auto_select_eligible"] is False
        assert REVIEW_THRESHOLD <= result["score"] < RECOMMENDED_THRESHOLD

    def test_weak_step_is_not_recommended(self):
        result = build_step_recommendation(
            step(risk="medium", expected_impact="medium", score=0.35))

        assert result["category"] == NOT_RECOMMENDED
        assert result["auto_select_eligible"] is False
        assert result["score"] < REVIEW_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# 3-6. The gates
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSafetyGates:
    def test_advisory_capability_is_manual_only(self):
        # FeatureEnvy -> Move Method: a real finding SCTVA cannot automate.
        result = build_step_recommendation(
            step(smell_type="FeatureEnvy", refactoring="Move Method"),
            impact_record=impact_record("FeatureEnvy"),
        )

        assert result["category"] == MANUAL_ONLY
        assert result["gate"] == "capability_advisory"
        assert result["auto_select_eligible"] is False

    def test_manual_only_says_no_code_will_change(self):
        result = build_step_recommendation(
            step(smell_type="FeatureEnvy", refactoring="Move Method"))

        assert any("will not produce an automatic code change" in w
                   for w in result["warnings"])
        # ...but the smell itself is still acknowledged as legitimate.
        assert any("real finding" in r for r in result["reasons"])

    def test_unknown_capability_is_not_recommended(self):
        result = build_step_recommendation(
            step(smell_type="NotASmellCUQAEmits", refactoring=""))

        assert result["category"] == NOT_RECOMMENDED
        assert result["gate"] == "capability_unknown"
        assert result["auto_select_eligible"] is False

    def test_executable_refactoring_with_incomplete_parameters_is_not_selectable(self):
        # Extract Method IS executable, but this concrete step carries no
        # source range, so map_step turns it into a no-op at transform time.
        result = build_step_recommendation(
            step(parameters={}, target={"file": "src/Order.java"}),
            impact_record=impact_record(),
        )

        assert result["capability"]["status"] == "executable"
        assert result["capability"]["actual_step_mappable"] is False
        assert result["category"] == NOT_RECOMMENDED
        assert result["gate"] == "step_not_mappable"
        assert result["auto_select_eligible"] is False
        assert result["capability"]["missing_requirements"]

    def test_high_risk_cannot_be_green_however_high_rdp_scored_it(self):
        result = build_step_recommendation(
            step(risk="high", score=0.99, expected_impact="high"),
            impact_record=impact_record(),
            developer_strategy=MAX_IMPROVEMENT,
        )

        assert result["category"] != RECOMMENDED
        assert result["auto_select_eligible"] is False
        assert result["gate"] == "high_risk_cap"
        assert any("risk" in w.lower() for w in result["warnings"])

    def test_high_risk_step_can_still_reach_review_on_merit(self):
        result = build_step_recommendation(
            step(risk="high", score=0.95), impact_record=impact_record())
        assert result["category"] == REVIEW


# ─────────────────────────────────────────────────────────────────────────────
# 7-9. Graceful degradation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestGracefulDegradation:
    def test_missing_impact_record_still_produces_a_recommendation(self):
        result = build_step_recommendation(step(), impact_record=None)

        assert result["category"] in (RECOMMENDED, REVIEW, NOT_RECOMMENDED, MANUAL_ONLY)
        assert result["impact"]["has_record"] is False
        assert result["reasons"]

    def test_missing_rdp_score_falls_back_and_labels_the_fallback(self):
        result = build_step_recommendation(step(score=None))
        factor = result["factors"]["rdp_quality"]

        assert factor["basis"] == "derived_from_ratings"
        assert factor["raw_score"] is None
        assert 0 <= factor["points"] <= WEIGHTS["rdp_quality"]
        # The estimate must never be presented as a real MCDA figure.
        assert any("did not score this step" in w for w in result["warnings"])

    def test_missing_prediction_and_alternatives_do_not_crash(self):
        result = build_step_recommendation(
            step(prediction=None, alternatives=None), impact_record=impact_record())
        assert result["score"] > 0

    def test_a_step_with_almost_nothing_on_it_still_returns_a_reason(self):
        result = build_step_recommendation({"step_id": 9})

        assert result["reasons"], "every recommendation must carry at least one reason"
        assert result["summary"]

    @pytest.mark.parametrize("bad_score", [None, "high", True, float("nan"), float("inf")])
    def test_unusable_scores_are_treated_as_absent(self, bad_score):
        value, basis = normalize_rdp_score(bad_score)
        assert value is None and basis == "missing"


# ─────────────────────────────────────────────────────────────────────────────
# 9-10. Historical feedback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestHistoricalFeedback:
    def test_no_history_is_neutral_and_says_so(self):
        result = build_step_recommendation(step(), impact_record=impact_record())
        factor = result["factors"]["historical_feedback"]

        assert factor["status"] == "insufficient_data"
        assert factor["sample_size"] == 0
        assert "Not enough historical feedback yet" in factor["message"]

    def test_a_new_refactoring_is_not_punished_for_having_no_history(self):
        """The neutral case must not cost a good step its recommendation.

        Pinning the absent factor at 0.5 would cap every score at 95 and drag a
        strong step from 90 to 80, so on a fresh install — where no history
        exists by definition — nothing could ever be green. It is imputed at
        the mean of the evidenced factors instead.
        """
        result = build_step_recommendation(step(), impact_record=impact_record())
        factors = result["factors"]

        evidenced = [factors[k]["points"] / factors[k]["max_points"]
                     for k in ("rdp_quality", "technical_benefit",
                               "transformation_safety", "strategy_match")]
        mean = sum(evidenced) / len(evidenced)

        assert factors["historical_feedback"]["imputed"] is True
        assert factors["historical_feedback"]["value"] == pytest.approx(mean, abs=0.002)
        assert result["category"] == RECOMMENDED

    def test_a_tiny_sample_is_ignored_entirely(self):
        """One rejection out of one must not read as 0% forever."""
        one_reject = build_step_recommendation(
            step(), impact_record=impact_record(),
            feedback_stats={"observations": 1, "accepted": 0})
        no_history = build_step_recommendation(step(), impact_record=impact_record())

        assert one_reject["factors"]["historical_feedback"]["status"] == "insufficient_data"
        assert one_reject["score"] == no_history["score"]

    def test_sufficient_real_feedback_moves_the_score(self):
        accepting = build_step_recommendation(
            step(), impact_record=impact_record(),
            feedback_stats={"observations": 12, "accepted": 12, "prior": 0.6})
        rejecting = build_step_recommendation(
            step(), impact_record=impact_record(),
            feedback_stats={"observations": 12, "accepted": 0, "prior": 0.6})

        assert accepting["score"] > rejecting["score"]
        assert accepting["factors"]["historical_feedback"]["status"] == "observed"
        assert accepting["factors"]["historical_feedback"]["sample_size"] == 12

    def test_the_estimate_is_smoothed_toward_the_prior(self):
        """A small-but-sufficient sample must not swing to 0 or 1."""
        factor = build_step_recommendation(
            step(),
            feedback_stats={"observations": MIN_FEEDBACK_OBSERVATIONS,
                            "accepted": 0, "prior": 0.6},
        )["factors"]["historical_feedback"]

        assert factor["acceptance_rate"] == 0.0        # what was observed
        assert factor["value"] > 0.0                   # what is used
        assert factor["value"] < 0.5

    def test_history_never_overrides_a_gate(self):
        """A perfect acceptance record cannot make an advisory step green."""
        result = build_step_recommendation(
            step(smell_type="FeatureEnvy", refactoring="Move Method"),
            feedback_stats={"observations": 50, "accepted": 50, "prior": 0.9})

        assert result["category"] == MANUAL_ONLY
        assert result["auto_select_eligible"] is False


# ─────────────────────────────────────────────────────────────────────────────
# The published formula
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestScoreComposition:
    def test_the_weights_are_the_documented_weights(self):
        assert WEIGHTS == {
            "rdp_quality": 35, "technical_benefit": 25,
            "transformation_safety": 20, "strategy_match": 10,
            "historical_feedback": 10,
        }
        assert sum(WEIGHTS.values()) == 100

    def test_the_score_is_the_sum_of_its_factors(self):
        result = build_step_recommendation(step(), impact_record=impact_record())
        total = sum(f["points"] for f in result["factors"].values())

        assert result["score"] == round(total)
        assert result["max_score"] == 100

    def test_no_factor_can_exceed_its_own_weight(self):
        for variant in (step(), step(score=999), step(risk="high"),
                        step(score=None), step(parameters={})):
            result = build_step_recommendation(
                variant, impact_record=impact_record(),
                feedback_stats={"observations": 40, "accepted": 40})
            for name, factor in result["factors"].items():
                assert 0 <= factor["points"] <= WEIGHTS[name], name
            assert 0 <= result["score"] <= 100

    def test_every_factor_is_exposed_rather_than_hidden(self):
        factors = build_step_recommendation(step())["factors"]
        assert set(factors) == set(WEIGHTS)
        for factor in factors.values():
            assert "value" in factor and "points" in factor and "max_points" in factor

    @pytest.mark.parametrize("raw,expected,basis", [
        (0.84, 0.84, "unit"),
        (1.0, 1.0, "unit"),
        (2.4, 0.24, "ten_point"),      # score_candidate_with_impact returns 1..3+
        (87, 0.87, "percent"),
        (500, 1.0, "clamped_high"),
        (-3, 0.0, "clamped_negative"),
    ])
    def test_rdp_scores_are_normalized_by_the_scale_they_are_on(self, raw, expected, basis):
        value, reported = normalize_rdp_score(raw)
        assert value == pytest.approx(expected)
        assert reported == basis

    def test_rdp_ranking_alternatives_is_credited_and_explained(self):
        decisive = build_step_recommendation(
            step(alternatives=[{"name": "Method Object", "score": 0.30}]))
        marginal = build_step_recommendation(
            step(alternatives=[{"name": "Method Object", "score": 0.83}]))

        assert decisive["factors"]["rdp_quality"]["points"] > \
            marginal["factors"]["rdp_quality"]["points"]
        assert any("ahead of" in r for r in decisive["reasons"])
        assert any("almost as highly" in w for w in marginal["warnings"])

    def test_no_alternatives_is_neutral_not_a_penalty(self):
        """"RDP evaluated nothing else" is not evidence either way."""
        factor = build_step_recommendation(step(alternatives=[]))["factors"]["rdp_quality"]
        assert factor["selection_margin"] == 0.5
        assert factor["best_alternative_score"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Developer strategy
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDeveloperStrategy:
    def test_safety_first_punishes_risk_more_than_maximum_improvement(self):
        risky = step(risk="high", expected_impact="high")
        safety = build_step_recommendation(risky, developer_strategy=SAFETY_FIRST)
        maximum = build_step_recommendation(risky, developer_strategy=MAX_IMPROVEMENT)

        assert safety["factors"]["strategy_match"]["points"] < \
            maximum["factors"]["strategy_match"]["points"]

    def test_safety_first_names_the_conflict_in_words(self):
        result = build_step_recommendation(
            step(risk="high", expected_impact="low"), developer_strategy=SAFETY_FIRST)
        assert any("Safety First" in w for w in result["warnings"])

    def test_a_matching_step_says_which_strategy_it_matches(self):
        result = build_step_recommendation(step(), developer_strategy=BALANCED)
        assert any("Balanced" in r for r in result["reasons"])

    def test_preferred_refactorings_are_still_honoured(self):
        with_preference = build_step_recommendation(
            step(), preferred_refactorings=["Extract Method"])
        without = build_step_recommendation(step())

        assert with_preference["factors"]["strategy_match"]["matched_preferred_refactoring"]
        assert with_preference["factors"]["strategy_match"]["points"] >= \
            without["factors"]["strategy_match"]["points"]

    def test_strategies_map_onto_the_existing_preference_vocabulary(self):
        assert preferences_for_strategy(SAFETY_FIRST) == \
            {"risk_tolerance": "conservative", "impact_focus": "medium"}
        assert preferences_for_strategy(BALANCED) == \
            {"risk_tolerance": "balanced", "impact_focus": "high"}
        assert preferences_for_strategy(MAX_IMPROVEMENT) == \
            {"risk_tolerance": "aggressive", "impact_focus": "high"}

    @pytest.mark.parametrize("preferences,expected", [
        ({"developer_strategy": "safety_first"}, SAFETY_FIRST),
        ({"risk_tolerance": "aggressive"}, MAX_IMPROVEMENT),
        ({"risk_tolerance": "conservative"}, SAFETY_FIRST),
        ({}, BALANCED),
        (None, BALANCED),
        ({"developer_strategy": "nonsense"}, BALANCED),
    ])
    def test_a_strategy_is_recovered_from_an_older_preference_object(
            self, preferences, expected):
        # Every existing caller sends risk_tolerance and knows nothing about
        # strategies; none of them may start producing a wrong answer.
        assert strategy_from_preferences(preferences) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Concrete SCTVA readiness
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStepMapping:
    def test_a_complete_step_maps_to_a_real_action(self):
        result = assess_step_mapping(step())
        assert result["actual_step_mappable"] is True
        assert result["action_type"] == "extract_method"

    def test_the_missing_requirement_is_named_not_just_flagged(self):
        # The method is known, the source range is not — so the developer is
        # told which parameter is missing, not merely that one is.
        result = assess_step_mapping(
            step(parameters={"source_file": "src/Order.java"},
                 target={"file": "src/Order.java", "method": "calculateTotal"}))

        assert result["actual_step_mappable"] is False
        assert result["missing_requirements"]
        assert "source range" in result["missing_requirements"][0]

    def test_an_unsupported_refactoring_is_unmappable(self):
        assert assess_step_mapping(step(refactoring="Move Method"))["actual_step_mappable"] is False

    def test_a_malformed_step_is_reported_not_raised(self):
        for bad in (None, {}, {"refactoring": None}, {"refactoring": 42}):
            assert assess_step_mapping(bad)["actual_step_mappable"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Step identity
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStepIdentity:
    def test_identity_survives_the_renumbering_the_re_ranker_performs(self):
        before = step(step_id=1)
        after = step(step_id=7)
        assert step_identity(before) == step_identity(after)

    def test_different_refactorings_on_one_smell_are_different_steps(self):
        assert step_identity(step()) != step_identity(step(refactoring="Rename Method"))

    def test_identity_matches_the_frontend_triple(self):
        # RefactoringPlanApprovalPage keys carried-over decisions on exactly
        # this; a mismatch would silently drop a developer's verdict.
        assert step_identity(step()) == "src/Order.java:10:0|Extract Method|src/Order.java"

    def test_a_missing_field_does_not_raise(self):
        assert step_identity({}) == "||"
        assert step_identity(None) == "||"


# ─────────────────────────────────────────────────────────────────────────────
# Plan-level summary
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSummary:
    def _plan_steps(self):
        return [
            {**step(step_id=1),
             "decision_support": build_step_recommendation(
                 step(step_id=1), impact_record=impact_record())},
            {**step(step_id=2, risk="high"),
             "decision_support": build_step_recommendation(
                 step(step_id=2, risk="high"), impact_record=impact_record())},
            {**step(step_id=3, smell_type="FeatureEnvy", refactoring="Move Method"),
             "decision_support": build_step_recommendation(
                 step(step_id=3, smell_type="FeatureEnvy", refactoring="Move Method"))},
        ]

    def test_counts_match_the_step_categories(self):
        steps = self._plan_steps()
        summary = summarize_recommendations(steps)

        assert summary["total_steps"] == 3
        assert summary[RECOMMENDED] == 1
        assert summary[REVIEW] == 1
        assert summary[MANUAL_ONLY] == 1
        assert summary["auto_selectable"] == 1

    def test_the_gain_and_the_effort_describe_the_same_set(self):
        """The header pairs them, so they must not count different steps."""
        steps = self._plan_steps()
        summary = summarize_recommendations(steps)

        green = [s for s in steps if s["decision_support"]["auto_select_eligible"]]
        assert summary["projected_quality_gain"] == pytest.approx(
            sum(s["decision_support"]["impact"]["quality_gain_points"] for s in green))
        assert summary["estimated_review_minutes"] == sum(
            s["decision_support"]["impact"]["effort_minutes"] for s in green)
        # ...and the whole-plan figure is carried separately rather than conflated.
        assert summary["total_review_minutes"] >= summary["estimated_review_minutes"]

    def test_an_unassessed_step_is_counted_as_unassessed_not_guessed(self):
        summary = summarize_recommendations([step(), *self._plan_steps()])
        assert summary["unclassified"] == 1
        assert summary["total_steps"] == 4

    def test_max_risk_is_the_highest_band_present(self):
        assert summarize_recommendations(self._plan_steps())["max_risk"] == "high"

    def test_an_empty_plan_reports_nothing_rather_than_zero(self):
        # "could not be computed" and "is worth nothing" are different answers.
        summary = summarize_recommendations([])
        assert summary["projected_quality_gain"] is None
        assert summary["estimated_review_minutes"] is None
        assert summary["max_risk"] is None
        assert summary["total_steps"] == 0

    def test_thresholds_are_published_with_the_summary(self):
        summary = summarize_recommendations([])
        assert summary["thresholds"] == {"recommended": RECOMMENDED_THRESHOLD,
                                         "review": REVIEW_THRESHOLD}


# ─────────────────────────────────────────────────────────────────────────────
# Explainability
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestExplainability:
    @pytest.mark.parametrize("variant", [
        step(),
        step(risk="high"),
        step(score=None),
        step(parameters={}),
        step(smell_type="FeatureEnvy", refactoring="Move Method"),
        step(smell_type="Nope", refactoring=""),
        {},
    ])
    def test_every_recommendation_explains_itself(self, variant):
        result = build_step_recommendation(variant)
        assert result["reasons"], "a bare 'Recommended — 87' is what this replaces"
        assert result["summary"]
        assert result["label"]

    def test_a_fallback_plan_says_rdp_evidence_is_missing(self):
        result = build_step_recommendation(step(), plan_source="diwo_local_fallback")
        assert any("fallback planner" in w for w in result["warnings"])

    def test_an_rdp_plan_makes_no_such_claim(self):
        result = build_step_recommendation(step(), plan_source="rdp_agent")
        assert not any("fallback planner" in w for w in result["warnings"])

    def test_uncertainty_bands_are_preserved_not_rounded_away(self):
        result = build_step_recommendation(step(), impact_record=impact_record())
        assert result["impact"]["quality_gain_low"] < result["impact"]["quality_gain_points"]
        assert result["impact"]["quality_gain_high"] > result["impact"]["quality_gain_points"]

    def test_deferral_cost_is_carried_through_from_the_impact_record(self):
        result = build_step_recommendation(step(), impact_record=impact_record(churn=15))
        assert result["deferral"]["carried_points"] > 0
        assert result["deferral"]["change_pressure"] == "high"
        assert result["deferral"]["churn_known"] is True
