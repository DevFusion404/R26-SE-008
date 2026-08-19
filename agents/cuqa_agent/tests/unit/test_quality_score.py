"""
unit/test_quality_score.py
---------------------------
Unit tests for _compute_score() in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import _compute_score


@pytest.mark.unit
class TestQualityScore:
    def test_no_smells_score_is_100(self):
        assert _compute_score([], {}) == 100.0

    def test_single_smell_deductions(self):
        assert _compute_score([{"severity": "high"}], {}) == 92.0
        assert _compute_score([{"severity": "medium"}], {}) == 96.0
        assert _compute_score([{"severity": "low"}], {}) == 99.0

    def test_mixed_and_unknown_severities(self):
        smells = [
            {"severity": "high"},    # -8
            {"severity": "medium"},  # -4
            {"severity": "low"},     # -1
            {"severity": "unknown"}, # -2 fallback
        ]
        # 100 - 8 - 4 - 1 - 2 = 85.0
        assert _compute_score(smells, {}) == 85.0

    def test_score_floor_is_zero(self):
        # 20 high smells -> -160 deduction -> capped at 0.0
        many_smells = [{"severity": "high"} for _ in range(20)]
        assert _compute_score(many_smells, {}) == 0.0
