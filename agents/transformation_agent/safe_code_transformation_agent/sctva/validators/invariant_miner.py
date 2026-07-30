"""Invariant mining from behavioral fingerprint outputs."""

from __future__ import annotations

import ast
import math
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Sequence

from ..contracts import RefactoringAction
from ..models import ValidationStepResult
from ..utils.io_helpers import utc_now_iso
from .c_support import summarize_c_source


class InvariantMiner:
    """Infers runtime invariants from behavioral fingerprint results.

    This miner is strict enough to catch cases such as:

        Original:    computeTax(100.0) -> 12.0
        Transformed: computeTax(100.0) -> 13.0

    It checks actual observed return values and numeric ranges, not only return type.
    """

    SKIPPED_SCORE = 0.65
    EPSILON = 1e-9

    def mine(
        self,
        *,
        language: str,
        behavioral_step: ValidationStepResult,
        actions: Sequence[RefactoringAction],
        strict_mode: bool,
    ) -> ValidationStepResult:
        start_iso = utc_now_iso()
        started = time.perf_counter()

        details = behavioral_step.details or {}
        language = language.lower().strip()

        fingerprint_status = str(details.get("fingerprint_status", "")).lower()

        if fingerprint_status == "skipped":
            return self._step_result(
                start_iso=start_iso,
                started=started,
                passed=True,
                score=self.SKIPPED_SCORE,
                message="Invariant mining skipped because behavioral fingerprinting was skipped.",
                details={
                    "status": "skipped",
                    "mode": "skipped",
                    "reason": details.get(
                        "fingerprint_summary",
                        "Behavioral fingerprinting was skipped.",
                    ),
                    "preserved_invariants": [],
                    "violated_invariants": [],
                    "original_invariants": {},
                    "transformed_invariants": {},
                    "summary": "Invariant mining was skipped.",
                },
            )

        if language == "python":
            mined = self._mine_python(details)
        elif language == "c":
            mined = self._mine_c(details)
        else:
            mined = self._mine_java(details)

        return self._step_result(
            start_iso=start_iso,
            started=started,
            passed=mined["passed"],
            score=mined["score"],
            message=mined["message"],
            details=mined["details"],
        )

    def _step_result(
        self,
        *,
        start_iso: str,
        started: float,
        passed: bool,
        score: float,
        message: str,
        details: Dict[str, Any],
    ) -> ValidationStepResult:
        return ValidationStepResult(
            name="invariant",
            passed=passed,
            score=score,
            message=message,
            details=details,
            started_at=start_iso,
            finished_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _mine_python(self, details: Dict[str, Any]) -> Dict[str, Any]:
        pairs: List[Dict[str, Any]] = []

        for item in details.get("fingerprints") or []:
            if not isinstance(item, dict):
                continue

            if item.get("original_fingerprint") and item.get("transformed_fingerprint"):
                pairs.append(
                    {
                        "name": item.get("name") or item.get("test_id") or "python_case",
                        "original_fingerprint": item.get("original_fingerprint"),
                        "transformed_fingerprint": item.get("transformed_fingerprint"),
                        "comparison": item.get("comparison") or {},
                        "mode": item.get("mode") or "",
                    }
                )

        if not pairs:
            return self._skipped_result(
                "No Python paired fingerprints were available for invariant mining."
            )

        if any(pair.get("mode") == "static_python_fingerprint" for pair in pairs):
            return self._mine_static_python(pairs)

        return self._mine_from_pairs(pairs, language_label="Python")

    def _mine_static_python(self, pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
        invariants: List[Dict[str, Any]] = []
        preserved: List[Dict[str, Any]] = []
        violations: List[Dict[str, Any]] = []

        for pair in pairs:
            comparison = pair.get("comparison") or {}
            matched = bool(comparison.get("matched", True))
            reason = comparison.get("reason") or "static_fingerprint_match"

            self._append(
                invariants,
                preserved,
                violations,
                name=f"static_fingerprint::{pair.get('name', 'python_case')}",
                ok=matched,
                critical=True,
                reason=reason,
                original=pair.get("original_fingerprint"),
                transformed=pair.get("transformed_fingerprint"),
            )

        summary = (
            "Python static invariants preserved."
            if not violations
            else f"Python static invariant violations detected: {len(violations)}."
        )

        return self._finalize(
            mode="static",
            message=summary,
            original_summary={"mode": "static_python_fingerprint"},
            transformed_summary={"mode": "static_python_fingerprint"},
            invariants=invariants,
            preserved=preserved,
            violations=violations,
        )

    def _mine_java(self, details: Dict[str, Any]) -> Dict[str, Any]:
        pairs: List[Dict[str, Any]] = []

        for item in details.get("java_results") or []:
            if not isinstance(item, dict):
                continue

            if item.get("original_fingerprint") and item.get("transformed_fingerprint"):
                pairs.append(
                    {
                        "name": item.get("name") or "java_case",
                        "original_target_class": item.get("original_target_class")
                        or item.get("target_class"),
                        "original_target_method": item.get("original_target_method")
                        or item.get("target_method"),
                        "transformed_target_class": item.get("transformed_target_class")
                        or item.get("target_class"),
                        "transformed_target_method": item.get("transformed_target_method")
                        or item.get("target_method"),
                        "args": item.get("args") or [],
                        "original_fingerprint": item.get("original_fingerprint"),
                        "transformed_fingerprint": item.get("transformed_fingerprint"),
                        "comparison": item.get("comparison") or {},
                    }
                )

        if not pairs:
            return self._skipped_result(
                "No Java paired fingerprints were available for invariant mining."
            )

        result = self._mine_from_pairs(pairs, language_label="Java")
        result["details"]["java_group_invariants"] = self._group_java_pairs(pairs)

        return result

    def _mine_c(self, details: Dict[str, Any]) -> Dict[str, Any]:
        pairs: List[Dict[str, Any]] = []

        for item in details.get("c_results") or []:
            if not isinstance(item, dict):
                continue

            if item.get("original_fingerprint") and item.get("transformed_fingerprint"):
                pairs.append(
                    {
                        "name": item.get("name") or "c_case",
                        "original_fingerprint": item.get("original_fingerprint"),
                        "transformed_fingerprint": item.get("transformed_fingerprint"),
                        "comparison": item.get("comparison") or {},
                        "mode": item.get("mode") or "",
                    }
                )

        if not pairs:
            return self._skipped_result(
                "No C paired fingerprints were available for invariant mining."
            )

        if any(pair.get("mode") == "static_c_fingerprint" for pair in pairs):
            return self._mine_static_c(pairs)

        return self._mine_from_pairs(pairs, language_label="C")

    def _mine_static_c(self, pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
        invariants: List[Dict[str, Any]] = []
        preserved: List[Dict[str, Any]] = []
        violations: List[Dict[str, Any]] = []

        for pair in pairs:
            comparison = pair.get("comparison") or {}
            matched = bool(comparison.get("matched", True))
            reason = comparison.get("reason") or "static_fingerprint_match"

            self._append(
                invariants,
                preserved,
                violations,
                name=f"static_fingerprint::{pair.get('name', 'c_case')}",
                ok=matched,
                critical=True,
                reason=reason,
                original=pair.get("original_fingerprint"),
                transformed=pair.get("transformed_fingerprint"),
            )

        summary = (
            "C static invariants preserved."
            if not violations
            else f"C static invariant violations detected: {len(violations)}."
        )

        return self._finalize(
            mode="static",
            message=summary,
            original_summary={"mode": "static_c_fingerprint"},
            transformed_summary={"mode": "static_c_fingerprint"},
            invariants=invariants,
            preserved=preserved,
            violations=violations,
        )

    def _mine_from_pairs(
        self,
        pairs: List[Dict[str, Any]],
        *,
        language_label: str,
    ) -> Dict[str, Any]:
        original_fps = [p["original_fingerprint"] for p in pairs]
        transformed_fps = [p["transformed_fingerprint"] for p in pairs]
        comparisons = [p.get("comparison") or {} for p in pairs]

        original_values = [
            self._fingerprint_value(fp)
            for fp in original_fps
        ]

        transformed_values = [
            self._fingerprint_value(fp)
            for fp in transformed_fps
        ]

        original_summary = self._collect_summary(original_values, original_fps)
        transformed_summary = self._collect_summary(transformed_values, transformed_fps)

        invariants: List[Dict[str, Any]] = []
        preserved: List[Dict[str, Any]] = []
        violations: List[Dict[str, Any]] = []

        self._record_execution_success_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_return_type_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_observed_return_value_consistency(
            invariants,
            preserved,
            violations,
            pairs,
            comparisons,
        )

        self._record_non_null_return_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_numeric_value_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_string_value_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_collection_size_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_exception_pattern_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        self._record_boolean_distribution_consistency(
            invariants,
            preserved,
            violations,
            original_summary,
            transformed_summary,
        )

        summary = (
            f"{language_label} invariants preserved."
            if not violations
            else f"{language_label} invariant violations detected: {len(violations)}."
        )

        return self._finalize(
            mode="full",
            message=summary,
            original_summary=original_summary,
            transformed_summary=transformed_summary,
            invariants=invariants,
            preserved=preserved,
            violations=violations,
        )

    def _finalize(
        self,
        *,
        mode: str,
        message: str,
        original_summary: Dict[str, Any],
        transformed_summary: Dict[str, Any],
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        applicable = [
            item
            for item in invariants
            if item.get("status") != "skipped"
        ]

        total = len(applicable)

        if total:
            score = len(preserved) / total
        else:
            score = self.SKIPPED_SCORE

        if violations:
            score = min(score, 0.49)

        if any(item.get("critical") for item in violations):
            score = min(score, 0.20)

        return {
            "passed": len(violations) == 0,
            "score": score,
            "message": message,
            "details": {
                "status": "passed" if not violations else "failed",
                "mode": mode,
                "summary": message,
                "original_invariants": original_summary,
                "transformed_invariants": transformed_summary,
                "preserved_invariants": preserved,
                "violated_invariants": violations,
                "invariants": invariants,
                "total_invariants": total,
                "preserved_count": len(preserved),
                "violated_count": len(violations),
            },
        }

    def _skipped_result(self, reason: str) -> Dict[str, Any]:
        return {
            "passed": True,
            "score": self.SKIPPED_SCORE,
            "message": reason,
            "details": {
                "status": "skipped",
                "mode": "skipped",
                "reason": reason,
                "summary": reason,
                "original_invariants": {},
                "transformed_invariants": {},
                "preserved_invariants": [],
                "violated_invariants": [],
                "invariants": [],
            },
        }

    def _collect_summary(
        self,
        values: List[Any],
        fingerprints: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        successful_values = [
            value
            for value, fingerprint in zip(values, fingerprints)
            if fingerprint and fingerprint.get("success")
        ]

        failed_exceptions = [
            fingerprint.get("exception_type")
            for fingerprint in fingerprints
            if fingerprint
            and not fingerprint.get("success")
            and fingerprint.get("exception_type")
        ]

        numeric_values = [
            value
            for value in successful_values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]

        string_values = [
            value
            for value in successful_values
            if isinstance(value, str)
        ]

        collection_values = [
            value
            for value in successful_values
            if isinstance(value, (list, tuple, set, frozenset, dict))
        ]

        boolean_values = [
            value
            for value in successful_values
            if isinstance(value, bool)
        ]

        return {
            "return_types": sorted(
                {type(value).__name__ for value in successful_values}
            ),
            "successful_count": len(successful_values),
            "failed_count": len(failed_exceptions),
            "all_non_null": (
                all(value is not None for value in successful_values)
                if successful_values
                else False
            ),
            "observed_values": successful_values,
            "numeric": self._numeric_summary(numeric_values),
            "strings": self._string_summary(string_values),
            "collections": self._collection_summary(collection_values),
            "booleans": self._boolean_summary(boolean_values),
            "exceptions": sorted(
                {str(exception) for exception in failed_exceptions if exception}
            ),
        }

    @staticmethod
    def _fingerprint_value(fingerprint: Dict[str, Any] | None) -> Any:
        if not fingerprint or not fingerprint.get("success"):
            return None

        value = fingerprint.get("return_value_repr")

        if value is None:
            return None

        if not isinstance(value, str):
            return value

        text = value.strip()

        if text in {"None", "null"}:
            return None

        if text.lower() == "true":
            return True

        if text.lower() == "false":
            return False

        try:
            return ast.literal_eval(text)
        except Exception:
            try:
                if re_like_number(text):
                    return float(text)
            except Exception:
                pass

            return text

    @staticmethod
    def _numeric_summary(values: List[Any]) -> Dict[str, Any]:
        if not values:
            return {
                "count": 0,
                "min": None,
                "max": None,
                "average": None,
                "values": [],
            }

        numeric_values = [
            float(value)
            for value in values
        ]

        return {
            "count": len(numeric_values),
            "min": min(numeric_values),
            "max": max(numeric_values),
            "average": sum(numeric_values) / len(numeric_values),
            "values": numeric_values,
        }

    @staticmethod
    def _string_summary(values: List[str]) -> Dict[str, Any]:
        if not values:
            return {
                "count": 0,
                "min_length": None,
                "max_length": None,
                "values": [],
            }

        lengths = [
            len(value)
            for value in values
        ]

        return {
            "count": len(values),
            "min_length": min(lengths),
            "max_length": max(lengths),
            "values": values,
        }

    @staticmethod
    def _collection_summary(values: List[Any]) -> Dict[str, Any]:
        if not values:
            return {
                "count": 0,
                "min_size": None,
                "max_size": None,
                "values": [],
            }

        sizes = [
            len(value)
            for value in values
        ]

        return {
            "count": len(values),
            "min_size": min(sizes),
            "max_size": max(sizes),
            "values": values,
        }

    @staticmethod
    def _boolean_summary(values: List[bool]) -> Dict[str, Any]:
        counter = Counter(values)

        return {
            "count": len(values),
            "true": counter.get(True, 0),
            "false": counter.get(False, 0),
            "values": values,
        }

    def _append(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        *,
        name: str,
        ok: bool,
        critical: bool,
        reason: str,
        original: Any,
        transformed: Any,
        status: str = "checked",
    ) -> None:
        record = {
            "name": name,
            "status": "preserved" if ok else "violated",
            "preserved": ok,
            "critical": critical,
            "reason": reason,
            "original": original,
            "transformed": transformed,
        }

        if status == "skipped":
            record["status"] = "skipped"

        invariants.append(record)

        if status == "skipped":
            return

        if ok:
            preserved.append(record)
        else:
            violations.append(record)

    def _record_execution_success_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        ok = (
            original["successful_count"] == transformed["successful_count"]
            and original["failed_count"] == transformed["failed_count"]
        )

        self._append(
            invariants,
            preserved,
            violations,
            name="execution_success_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Execution success/failure counts preserved"
                if ok
                else "Execution success/failure counts changed"
            ),
            original={
                "successful": original["successful_count"],
                "failed": original["failed_count"],
            },
            transformed={
                "successful": transformed["successful_count"],
                "failed": transformed["failed_count"],
            },
        )

    def _record_return_type_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        ok = original["return_types"] == transformed["return_types"]

        self._append(
            invariants,
            preserved,
            violations,
            name="return_type_consistency",
            ok=ok,
            critical=True,
            reason="Return types matched" if ok else "Return types changed",
            original=original["return_types"],
            transformed=transformed["return_types"],
        )

    def _record_observed_return_value_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        pairs: List[Dict[str, Any]],
        comparisons: List[Dict[str, Any]],
    ) -> None:
        original_values = [
            pair["original_fingerprint"].get("return_value_repr")
            for pair in pairs
            if pair["original_fingerprint"].get("success")
        ]

        transformed_values = [
            pair["transformed_fingerprint"].get("return_value_repr")
            for pair in pairs
            if pair["transformed_fingerprint"].get("success")
        ]

        if not original_values and not transformed_values:
            self._append(
                invariants,
                preserved,
                violations,
                name="observed_return_value_consistency",
                ok=True,
                critical=False,
                reason="No successful return values observed",
                original=original_values,
                transformed=transformed_values,
                status="skipped",
            )
            return

        comparison_entries = [
            comparison
            for comparison in comparisons
            if isinstance(comparison, dict) and "matched" in comparison
        ]

        comparison_ok = (
            all(bool(comparison.get("matched")) for comparison in comparison_entries)
            if comparison_entries
            else True
        )

        ok = original_values == transformed_values and comparison_ok

        mismatch_reasons = [
            comparison.get("reason")
            for comparison in comparison_entries
            if isinstance(comparison, dict)
            and not comparison.get("matched")
        ]

        self._append(
            invariants,
            preserved,
            violations,
            name="observed_return_value_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Observed return values preserved"
                if ok
                else "Observed return values changed: "
                + ", ".join(str(reason) for reason in mismatch_reasons)
            ),
            original=original_values,
            transformed=transformed_values,
        )

    def _record_non_null_return_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        original_requires_non_null = (
            original["successful_count"] > 0 and original["all_non_null"]
        )

        transformed_has_null = (
            transformed["successful_count"] > 0 and not transformed["all_non_null"]
        )

        ok = not (original_requires_non_null and transformed_has_null)

        self._append(
            invariants,
            preserved,
            violations,
            name="non_null_return_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Non-null behavior preserved"
                if ok
                else "Transformed code introduced null returns"
            ),
            original=original_requires_non_null,
            transformed=transformed_has_null,
        )

    def _record_numeric_value_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        original_numeric = original["numeric"]
        transformed_numeric = transformed["numeric"]

        if original_numeric["count"] == 0 and transformed_numeric["count"] == 0:
            self._append(
                invariants,
                preserved,
                violations,
                name="numeric_range_consistency",
                ok=True,
                critical=False,
                reason="Numeric invariant not applicable",
                original=original_numeric,
                transformed=transformed_numeric,
                status="skipped",
            )
            return

        if original_numeric["count"] != transformed_numeric["count"]:
            ok = False
        else:
            same_values = (
                len(original_numeric["values"]) == len(transformed_numeric["values"])
                and all(
                    math.isclose(
                        original_value,
                        transformed_value,
                        rel_tol=0.0,
                        abs_tol=self.EPSILON,
                    )
                    for original_value, transformed_value in zip(
                        original_numeric["values"],
                        transformed_numeric["values"],
                    )
                )
            )

            same_range = (
                math.isclose(
                    float(original_numeric["min"]),
                    float(transformed_numeric["min"]),
                    rel_tol=0.0,
                    abs_tol=self.EPSILON,
                )
                and math.isclose(
                    float(original_numeric["max"]),
                    float(transformed_numeric["max"]),
                    rel_tol=0.0,
                    abs_tol=self.EPSILON,
                )
            )

            ok = same_values and same_range

        self._append(
            invariants,
            preserved,
            violations,
            name="numeric_range_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Numeric values and ranges preserved"
                if ok
                else "Transformed numeric values changed from original observed values/range"
            ),
            original=original_numeric,
            transformed=transformed_numeric,
        )

    def _record_string_value_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        original_strings = original["strings"]
        transformed_strings = transformed["strings"]

        if original_strings["count"] == 0 and transformed_strings["count"] == 0:
            self._append(
                invariants,
                preserved,
                violations,
                name="string_length_range_consistency",
                ok=True,
                critical=False,
                reason="String invariant not applicable",
                original=original_strings,
                transformed=transformed_strings,
                status="skipped",
            )
            return

        ok = (
            original_strings["values"] == transformed_strings["values"]
            and original_strings["min_length"] == transformed_strings["min_length"]
            and original_strings["max_length"] == transformed_strings["max_length"]
        )

        self._append(
            invariants,
            preserved,
            violations,
            name="string_length_range_consistency",
            ok=ok,
            critical=False,
            reason=(
                "String values/lengths preserved"
                if ok
                else "String values or length range changed"
            ),
            original=original_strings,
            transformed=transformed_strings,
        )

    def _record_collection_size_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        original_collections = original["collections"]
        transformed_collections = transformed["collections"]

        if original_collections["count"] == 0 and transformed_collections["count"] == 0:
            self._append(
                invariants,
                preserved,
                violations,
                name="collection_size_range_consistency",
                ok=True,
                critical=False,
                reason="Collection invariant not applicable",
                original=original_collections,
                transformed=transformed_collections,
                status="skipped",
            )
            return

        ok = (
            original_collections["min_size"] == transformed_collections["min_size"]
            and original_collections["max_size"] == transformed_collections["max_size"]
        )

        self._append(
            invariants,
            preserved,
            violations,
            name="collection_size_range_consistency",
            ok=ok,
            critical=False,
            reason=(
                "Collection size range preserved"
                if ok
                else "Collection size range changed"
            ),
            original=original_collections,
            transformed=transformed_collections,
        )

    def _record_exception_pattern_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        ok = set(original["exceptions"]) == set(transformed["exceptions"])

        self._append(
            invariants,
            preserved,
            violations,
            name="exception_pattern_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Exception pattern preserved"
                if ok
                else "Exception pattern changed"
            ),
            original=original["exceptions"],
            transformed=transformed["exceptions"],
        )

    def _record_boolean_distribution_consistency(
        self,
        invariants: List[Dict[str, Any]],
        preserved: List[Dict[str, Any]],
        violations: List[Dict[str, Any]],
        original: Dict[str, Any],
        transformed: Dict[str, Any],
    ) -> None:
        original_booleans = original["booleans"]
        transformed_booleans = transformed["booleans"]

        if original_booleans["count"] == 0 and transformed_booleans["count"] == 0:
            self._append(
                invariants,
                preserved,
                violations,
                name="boolean_distribution_consistency",
                ok=True,
                critical=False,
                reason="Boolean invariant not applicable",
                original=original_booleans,
                transformed=transformed_booleans,
                status="skipped",
            )
            return

        ok = (
            original_booleans["true"] == transformed_booleans["true"]
            and original_booleans["false"] == transformed_booleans["false"]
            and original_booleans["values"] == transformed_booleans["values"]
        )

        self._append(
            invariants,
            preserved,
            violations,
            name="boolean_distribution_consistency",
            ok=ok,
            critical=True,
            reason=(
                "Boolean distribution preserved"
                if ok
                else "Boolean distribution changed"
            ),
            original=original_booleans,
            transformed=transformed_booleans,
        )

    def _group_java_pairs(
        self,
        pairs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for pair in pairs:
            key = (
                f"{pair.get('original_target_class') or 'UnknownClass'}."
                f"{pair.get('original_target_method') or 'unknownMethod'}"
            )
            groups[key].append(pair)

        output: Dict[str, Any] = {}

        for key, items in groups.items():
            original_fps = [
                item["original_fingerprint"]
                for item in items
            ]

            transformed_fps = [
                item["transformed_fingerprint"]
                for item in items
            ]

            output[key] = {
                "original": self._collect_summary(
                    [
                        self._fingerprint_value(fingerprint)
                        for fingerprint in original_fps
                    ],
                    original_fps,
                ),
                "transformed": self._collect_summary(
                    [
                        self._fingerprint_value(fingerprint)
                        for fingerprint in transformed_fps
                    ],
                    transformed_fps,
                ),
            }

        return output


def re_like_number(text: str) -> bool:
    try:
        float(text)
        return True
    except Exception:
        return False