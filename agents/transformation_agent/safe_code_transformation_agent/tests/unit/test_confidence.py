from sctva.models import ValidationStepResult
from sctva.scoring.confidence_scorer import ConfidenceScorer


def test_confidence_score_is_deterministic():
    scorer = ConfidenceScorer()
    syntax = ValidationStepResult(name="syntax", passed=True, score=1.0, message="ok")
    structural = ValidationStepResult(name="structural", passed=True, score=0.8, message="ok")
    behavioral = ValidationStepResult(name="behavioral", passed=True, score=0.6, message="ok")

    score1, details1 = scorer.score(syntax=syntax, structural=structural, behavioral=behavioral)
    score2, details2 = scorer.score(syntax=syntax, structural=structural, behavioral=behavioral)

    assert score1 == score2
    assert details1 == details2


def test_confidence_excludes_unavailable_behavioral_runtime_fallback():
    scorer = ConfidenceScorer()
    syntax = ValidationStepResult(name="syntax", passed=True, score=1.0, message="ok")
    structural = ValidationStepResult(
        name="structural", passed=True, score=0.8, message="ok"
    )
    behavioral = ValidationStepResult(
        name="behavioral",
        passed=True,
        score=0.75,
        message="static fallback passed",
        details={
            "fingerprint_status": "degraded_static_passed",
            "score_applicable": False,
        },
    )

    score, details = scorer.score(
        syntax=syntax,
        structural=structural,
        behavioral=behavioral,
    )

    assert round(score, 4) == 0.9
    assert details["behavioral_included"] is False
    assert details["excluded_components"] == ["behavioral"]
    assert "behavioral_component" not in details
    assert details["weights"] == {"syntax": 0.5, "structural": 0.5}


def test_confidence_does_not_report_high_safety_when_structural_validation_fails():
    scorer = ConfidenceScorer()
    syntax = ValidationStepResult(name="syntax", passed=True, score=1.0, message="ok")
    structural = ValidationStepResult(
        name="structural",
        passed=False,
        score=0.99,
        message="Extract Method structural validation failed.",
    )
    behavioral = ValidationStepResult(
        name="behavioral", passed=True, score=1.0, message="ok"
    )
    invariant = ValidationStepResult(
        name="invariant", passed=True, score=1.0, message="ok"
    )

    score, details = scorer.score(
        syntax=syntax,
        structural=structural,
        behavioral=behavioral,
        invariant=invariant,
    )

    assert score == 0.49
    assert details["structural_component"] == 0.0
    assert details["failed_components"] == ["structural"]
    assert details["failure_confidence_cap"] == 0.49
