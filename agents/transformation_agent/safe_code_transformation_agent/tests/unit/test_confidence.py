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
