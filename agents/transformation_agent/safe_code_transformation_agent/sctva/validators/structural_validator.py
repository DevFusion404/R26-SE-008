"""Structural similarity checks for transformed code."""

from __future__ import annotations

import ast
import re
import time
from collections import Counter
from typing import Dict, List, Tuple

from ..constants import DEFAULT_STRUCTURAL_THRESHOLD_JAVA, DEFAULT_STRUCTURAL_THRESHOLD_PYTHON
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso
from ..utils.metrics import (
    cosine_similarity_from_counts,
    count_tokens,
    jaccard_similarity,
    normalized_count_similarity,
)


class StructuralValidator:
    """Checks structure-preserving similarity between original and transformed code."""

    def validate(
        self,
        *,
        language: str,
        original_code: str,
        transformed_code: str,
    ) -> ValidationStepResult:
        start_iso = utc_now_iso()
        started = time.perf_counter()

        if language == "python":
            score, details = self._validate_python(original_code, transformed_code)
            threshold = DEFAULT_STRUCTURAL_THRESHOLD_PYTHON
        else:
            score, details = self._validate_java(original_code, transformed_code)
            threshold = DEFAULT_STRUCTURAL_THRESHOLD_JAVA

        passed = score >= threshold
        message = (
            f"Structural validation passed with score {score:.3f} >= {threshold:.3f}."
            if passed
            else f"Structural validation failed with score {score:.3f} < {threshold:.3f}."
        )

        duration_ms = int((time.perf_counter() - started) * 1000)
        end_iso = utc_now_iso()

        return ValidationStepResult(
            name="structural",
            passed=passed,
            score=score,
            message=message,
            details={"threshold": threshold, **details},
            started_at=start_iso,
            finished_at=end_iso,
            duration_ms=duration_ms,
        )

    def _validate_python(self, original: str, transformed: str) -> Tuple[float, Dict[str, float]]:
        try:
            orig_tree = ast.parse(original)
            trans_tree = ast.parse(transformed)
        except SyntaxError:
            return 0.0, {"node_similarity": 0.0, "signature_similarity": 0.0}

        orig_counts = Counter(type(node).__name__ for node in ast.walk(orig_tree))
        trans_counts = Counter(type(node).__name__ for node in ast.walk(trans_tree))
        node_similarity = cosine_similarity_from_counts(dict(orig_counts), dict(trans_counts))

        orig_signatures = self._python_signatures(orig_tree)
        trans_signatures = self._python_signatures(trans_tree)
        signature_similarity = jaccard_similarity(orig_signatures, trans_signatures)

        score = (0.7 * node_similarity) + (0.3 * signature_similarity)
        return score, {
            "node_similarity": round(node_similarity, 4),
            "signature_similarity": round(signature_similarity, 4),
            "normalized_similarity": round(score, 4),
        }

    @staticmethod
    def _python_signatures(tree: ast.Module) -> List[str]:
        signatures: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                signatures.append(f"func:{node.name}:{len(node.args.args)}")
            elif isinstance(node, ast.AsyncFunctionDef):
                signatures.append(f"async_func:{node.name}:{len(node.args.args)}")
            elif isinstance(node, ast.ClassDef):
                signatures.append(f"class:{node.name}")
        return signatures

    def _validate_java(self, original: str, transformed: str) -> Tuple[float, Dict[str, float]]:
        orig_tokens = self._java_tokens(original)
        trans_tokens = self._java_tokens(transformed)

        token_similarity = cosine_similarity_from_counts(
            count_tokens(orig_tokens),
            count_tokens(trans_tokens),
        )

        orig_class_count = len(re.findall(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*", original))
        trans_class_count = len(re.findall(r"\bclass\s+[A-Za-z_][A-Za-z0-9_]*", transformed))
        class_similarity = normalized_count_similarity(orig_class_count, trans_class_count)

        orig_method_count = len(re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z_][A-Za-z0-9_<>,\[\]]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", original))
        trans_method_count = len(re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z_][A-Za-z0-9_<>,\[\]]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", transformed))
        method_similarity = normalized_count_similarity(orig_method_count, trans_method_count)

        score = (0.6 * token_similarity) + (0.2 * class_similarity) + (0.2 * method_similarity)

        return score, {
            "token_similarity": round(token_similarity, 4),
            "class_count_similarity": round(class_similarity, 4),
            "method_count_similarity": round(method_similarity, 4),
            "normalized_similarity": round(score, 4),
        }

    @staticmethod
    def _java_tokens(code: str) -> List[str]:
        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[{}();.,=+-/*]", code)
