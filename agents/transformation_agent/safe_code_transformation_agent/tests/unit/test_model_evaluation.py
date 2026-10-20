from sctva.model_assistance.evaluation import build_model_evaluation


def test_model_evaluation_counts_only_model_candidates_and_retained_output():
    metrics = build_model_evaluation(
        model_attempt_count=4,
        model_candidate_count=3,
        model_action_count=1,
        model_generation_seconds=20.0,
        model_validated_accuracy=96.5,
        model_output_retained=True,
        model_used=True,
        model_candidates=[
            {"status": "ACCEPTED"},
            {"status": "REJECTED_AT_ACTION_CHECKPOINT"},
            {"status": "PASSED_ACTION_CHECKPOINT_FILE_REJECTED"},
        ],
    )

    assert metrics["candidate_generation_rate_percent"] == 75.0
    assert metrics["action_checkpoint_pass_rate_percent"] == 66.7
    assert metrics["accepted_action_rate_percent"] == 33.3
    assert metrics["final_model_output_retained"] is True
    assert metrics["validated_quality_score_percent"] == 96.5
    assert metrics["average_generation_seconds_per_attempt"] == 5.0


def test_model_evaluation_does_not_credit_rule_based_fallback():
    metrics = build_model_evaluation(
        model_attempt_count=2,
        model_candidate_count=1,
        model_action_count=0,
        model_generation_seconds=10.0,
        model_validated_accuracy=99.0,
        model_output_retained=False,
        model_used=False,
        model_candidates=[{"status": "REJECTED_AT_ACTION_CHECKPOINT"}],
    )

    assert metrics["final_model_output_retained"] is False
    assert metrics["validated_quality_score_percent"] is None
    assert metrics["accepted_action_rate_percent"] == 0.0
