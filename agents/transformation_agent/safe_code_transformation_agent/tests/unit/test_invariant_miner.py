from sctva.models import ValidationStepResult
from sctva.validators.invariant_miner import InvariantMiner


def _behavioral_step(details):
    return ValidationStepResult(
        name="behavioral",
        passed=True,
        score=1.0,
        message="behavioral ok",
        details=details,
    )


def _python_pair(original_fp, transformed_fp):
    return {
        "original_fingerprint": original_fp,
        "transformed_fingerprint": transformed_fp,
    }


def _success_fp(value, return_type):
    return {
        "success": True,
        "return_value_repr": repr(value),
        "return_type": return_type,
        "exception_type": None,
        "exception_message_category": None,
        "timeout": False,
    }


def _failure_fp(exception_type, message="boom"):
    return {
        "success": False,
        "return_value_repr": None,
        "return_type": None,
        "exception_type": exception_type,
        "exception_message_category": message,
        "timeout": False,
    }


def test_invariant_miner_preserves_return_type_invariant():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp(1, "int"), _success_fp(1, "int"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is True
    assert "return_type_consistency" in [item["name"] for item in result.details["preserved_invariants"]]


def test_invariant_miner_reports_return_type_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp(1, "int"), _success_fp("1", "str"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "return_type_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_non_null_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp(1, "int"), _success_fp(None, "NoneType"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "non_null_return_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_numeric_range_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp(1, "int"), _success_fp(5, "int"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "numeric_range_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_string_length_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp("ab", "str"), _success_fp("abcd", "str"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "string_length_range_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_collection_size_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_success_fp([1], "list"), _success_fp([1, 2], "list"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "collection_size_range_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_exception_pattern_violation():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [_python_pair(_failure_fp("ValueError"), _failure_fp("TypeError"))],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "exception_pattern_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_reports_boolean_distribution_change():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "fingerprints": [
                _python_pair(_success_fp(True, "bool"), _success_fp(True, "bool")),
                _python_pair(_success_fp(False, "bool"), _success_fp(True, "bool")),
            ],
        }
    )

    result = miner.mine(language="python", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is False
    assert "boolean_distribution_consistency" in [item["name"] for item in result.details["violated_invariants"]]


def test_invariant_miner_skips_when_behavioral_fingerprinting_skipped():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "skipped",
            "fingerprint_summary": "No Java harness/test command available; fingerprinting skipped.",
        }
    )

    result = miner.mine(language="java", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is True
    assert result.details["status"] == "skipped"
    assert result.details["mode"] == "skipped"


def test_invariant_miner_handles_c_static_fingerprints():
    miner = InvariantMiner()
    step = _behavioral_step(
        {
            "fingerprint_status": "passed",
            "c_results": [
                {
                    "name": "static_c_summary",
                    "mode": "static_c_fingerprint",
                    "original_fingerprint": {"function_count": 1, "macros": {}},
                    "transformed_fingerprint": {"function_count": 1, "macros": {"MAGIC_NUMBER_0_12": "0.12"}},
                    "comparison": {"matched": True, "reason": "static_summary_match"},
                }
            ],
        }
    )

    result = miner.mine(language="c", behavioral_step=step, actions=[], strict_mode=False)

    assert result.passed is True
    assert "C static invariants preserved." in result.message
