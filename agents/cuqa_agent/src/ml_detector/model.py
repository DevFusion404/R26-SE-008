"""
model.py
--------
Loads the trained smell model and scores feature vectors in pure Python.

The model is a set of small gradient-boosted tree ensembles, one per smell,
trained offline with scikit-learn (see training/train_model.py) and exported
to JSON. Scoring only needs the standard library, so the CUQA service does not
depend on scikit-learn or numpy at runtime and no pickle file is loaded.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent / "artifacts" / "smell_model.json"
MODEL_FORMAT = "cuqa-smell-model"
SUPPORTED_FORMAT_VERSION = 1

_FLOAT32_MAX = 3.4028234663852886e38


class SmellModelError(Exception):
    """Raised when the model file is missing or not usable."""


def _to_float32(value: float) -> float:
    """Round a value the way scikit-learn does before comparing it to a split.

    scikit-learn stores tree inputs as float32. Applying the same rounding here
    keeps the JSON model's predictions identical to the trained estimator.
    """
    try:
        return struct.unpack("f", struct.pack("f", value))[0]
    except (OverflowError, struct.error):
        return math.copysign(_FLOAT32_MAX, value)


class SmellClassifier:
    """Binary classifier for one smell: boosted regression trees plus a threshold."""

    def __init__(self, smell_type: str, spec: dict):
        self.smell_type = smell_type
        self.entity = spec["entity"]
        self.enabled = bool(spec.get("enabled", True))
        self.threshold = float(spec["threshold"])
        self.severity = spec.get("severity", "medium")
        self.display_name = spec.get("display_name", smell_type)
        self.feature_names = list(spec["features"])
        self.evidence_features = list(spec.get("evidence_features", []))
        self.init_score = float(spec["init_score"])
        self.learning_rate = float(spec["learning_rate"])
        self.metrics = spec.get("metrics", {})
        self._trees = [
            (t["feature"], t["threshold"], t["left"], t["right"], t["value"])
            for t in spec["trees"]
        ]

    def predict_proba(self, features: dict) -> float:
        """Probability that an entity with these feature values has the smell."""
        x = [_to_float32(float(features.get(name, 0.0))) for name in self.feature_names]
        raw = self.init_score
        for feature, threshold, left, right, value in self._trees:
            node = 0
            while feature[node] >= 0:
                node = left[node] if x[feature[node]] <= threshold[node] else right[node]
            raw += self.learning_rate * value[node]
        if raw >= 0:
            return 1.0 / (1.0 + math.exp(-raw))
        exp_raw = math.exp(raw)
        return exp_raw / (1.0 + exp_raw)


class SmellModel:
    """All smell classifiers from one model file."""

    def __init__(self, payload: dict):
        if payload.get("format") != MODEL_FORMAT:
            raise SmellModelError("not a CUQA smell model file")
        if payload.get("format_version") != SUPPORTED_FORMAT_VERSION:
            raise SmellModelError(f"unsupported model format version: {payload.get('format_version')}")
        self.version = str(payload.get("model_version", "unknown"))
        self.metadata = {k: v for k, v in payload.items() if k != "smells"}
        try:
            self.classifiers = {
                smell_type: SmellClassifier(smell_type, spec)
                for smell_type, spec in payload["smells"].items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise SmellModelError(f"model file is incomplete: {exc}") from exc

    def for_entity(self, entity_kind: str, include_disabled: bool = False) -> list[SmellClassifier]:
        return [
            clf for clf in self.classifiers.values()
            if clf.entity == entity_kind and (clf.enabled or include_disabled)
        ]

    def describe(self) -> dict:
        """Summary of the model for the API and for reports."""
        return {
            **self.metadata,
            "smells": {
                smell_type: {
                    "display_name": clf.display_name,
                    "entity": clf.entity,
                    "enabled": clf.enabled,
                    "threshold": clf.threshold,
                    "severity": clf.severity,
                    "n_features": len(clf.feature_names),
                    "n_trees": len(clf._trees),
                    "metrics": clf.metrics,
                }
                for smell_type, clf in self.classifiers.items()
            },
        }


_cache: dict[str, SmellModel] = {}


def load_model(path: str | Path | None = None) -> SmellModel:
    """Load the model file once and reuse it for later calls."""
    model_path = Path(path) if path else MODEL_PATH
    key = str(model_path)
    if key not in _cache:
        try:
            with open(model_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError as exc:
            raise SmellModelError(f"model file not found: {model_path}") from exc
        except (OSError, ValueError) as exc:
            raise SmellModelError(f"model file could not be read: {exc}") from exc
        _cache[key] = SmellModel(payload)
    return _cache[key]
