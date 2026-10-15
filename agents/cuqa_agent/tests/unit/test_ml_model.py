"""
unit/test_ml_model.py
----------------------
Unit tests for loading and scoring the JSON smell model.
"""

import json
import math

import pytest
# pyrefly: ignore [missing-import]
from ml_detector import model as smell_model
# pyrefly: ignore [missing-import]
from ml_detector.java_features import CLASS_FEATURES, METHOD_FEATURES
# pyrefly: ignore [missing-import]
from ml_detector.model import SmellModel, SmellModelError, load_model

pytestmark = pytest.mark.unit


def _tiny_payload() -> dict:
    """One smell, two stumps: sloc <= 10 and n_params <= 2."""
    return {
        "format": "cuqa-smell-model",
        "format_version": 1,
        "model_version": "test",
        "smells": {
            "LongMethod": {
                "display_name": "Long Method",
                "entity": "method",
                "enabled": True,
                "threshold": 0.5,
                "severity": "high",
                "features": ["sloc", "n_params"],
                "evidence_features": ["sloc"],
                "init_score": -1.0,
                "learning_rate": 0.5,
                "trees": [
                    {"feature": [0, -1, -1], "threshold": [10.0, 0.0, 0.0],
                     "left": [1, -1, -1], "right": [2, -1, -1], "value": [0.0, -2.0, 4.0]},
                    {"feature": [1, -1, -1], "threshold": [2.0, 0.0, 0.0],
                     "left": [1, -1, -1], "right": [2, -1, -1], "value": [0.0, -1.0, 1.0]},
                ],
            },
        },
    }


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class TestScoring:
    def test_tree_traversal_and_sigmoid(self):
        clf = SmellModel(_tiny_payload()).classifiers["LongMethod"]
        # sloc 50 goes right (+4), n_params 1 goes left (-1): -1 + 0.5 * (4 - 1)
        assert clf.predict_proba({"sloc": 50, "n_params": 1}) == pytest.approx(_sigmoid(0.5))
        # sloc 5 goes left (-2), n_params 5 goes right (+1): -1 + 0.5 * (-2 + 1)
        assert clf.predict_proba({"sloc": 5, "n_params": 5}) == pytest.approx(_sigmoid(-1.5))

    def test_split_value_goes_left(self):
        clf = SmellModel(_tiny_payload()).classifiers["LongMethod"]
        on_split = clf.predict_proba({"sloc": 10, "n_params": 2})
        assert on_split == pytest.approx(_sigmoid(-1.0 + 0.5 * (-2.0 - 1.0)))

    def test_missing_feature_counts_as_zero(self):
        clf = SmellModel(_tiny_payload()).classifiers["LongMethod"]
        assert clf.predict_proba({}) == clf.predict_proba({"sloc": 0, "n_params": 0})

    def test_extreme_values_do_not_overflow(self):
        clf = SmellModel(_tiny_payload()).classifiers["LongMethod"]
        assert 0.0 < clf.predict_proba({"sloc": 1e300, "n_params": -1e300}) < 1.0

    def test_for_entity_respects_the_enabled_flag(self):
        payload = _tiny_payload()
        payload["smells"]["LongMethod"]["enabled"] = False
        model = SmellModel(payload)
        assert model.for_entity("method") == []
        assert len(model.for_entity("method", include_disabled=True)) == 1
        assert model.for_entity("class", include_disabled=True) == []


class TestLoading:
    def test_wrong_format_is_rejected(self):
        payload = _tiny_payload()
        payload["format"] = "something-else"
        with pytest.raises(SmellModelError):
            SmellModel(payload)

    def test_unknown_format_version_is_rejected(self):
        payload = _tiny_payload()
        payload["format_version"] = 99
        with pytest.raises(SmellModelError):
            SmellModel(payload)

    def test_incomplete_smell_is_rejected(self):
        payload = _tiny_payload()
        del payload["smells"]["LongMethod"]["trees"]
        with pytest.raises(SmellModelError):
            SmellModel(payload)

    def test_missing_file(self, tmp_path):
        with pytest.raises(SmellModelError):
            load_model(tmp_path / "absent.json")

    def test_corrupt_file(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SmellModelError):
            load_model(path)

    def test_load_from_file(self, tmp_path):
        path = tmp_path / "model.json"
        path.write_text(json.dumps(_tiny_payload()), encoding="utf-8")
        assert load_model(path).version == "test"


class TestShippedModel:
    """Checks on the trained model file that is deployed with the agent."""

    def test_model_file_loads(self):
        model = load_model()
        assert smell_model.MODEL_PATH.exists()
        assert {"LongMethod", "LargeClass", "DataClass", "FeatureEnvy"} <= set(model.classifiers)

    def test_model_file_is_small(self):
        assert smell_model.MODEL_PATH.stat().st_size < 1024 * 1024

    def test_features_match_the_extractor(self):
        # a mismatch here would mean the model is scored on features it was not trained on
        for clf in load_model().classifiers.values():
            expected = METHOD_FEATURES if clf.entity == "method" else CLASS_FEATURES
            assert tuple(clf.feature_names) == tuple(expected)

    def test_thresholds_are_probabilities(self):
        for clf in load_model().classifiers.values():
            assert 0.0 < clf.threshold < 1.0

    def test_describe_reports_every_smell_with_metrics(self):
        description = load_model().describe()
        assert description["language"] == "java"
        for info in description["smells"].values():
            assert {"test_precision", "test_recall", "test_f1"} <= set(info["metrics"])
            assert info["n_trees"] > 0

    def test_long_complex_method_scores_higher_than_a_getter(self):
        clf = load_model().classifiers["LongMethod"]
        getter = dict.fromkeys(METHOD_FEATURES, 0.0)
        getter.update(loc=3, sloc=3, n_tokens=12, n_statements=1, cyclomatic_complexity=1,
                      n_returns=1, has_body=1, is_public=1, is_accessor_name=1)
        long_method = dict(getter, loc=220, sloc=190, n_tokens=1900, n_statements=140,
                           cyclomatic_complexity=45, max_nesting=5, n_local_vars=30,
                           n_branches=30, n_loops=8, n_method_calls=90, n_distinct_calls=40,
                           n_identifiers=700, n_distinct_identifiers=120, n_operators=250,
                           n_assignments=40, is_accessor_name=0)
        assert clf.predict_proba(getter) < clf.threshold
        assert clf.predict_proba(long_method) >= clf.threshold
