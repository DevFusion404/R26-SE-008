"""
Before/after code quality metrics
=================================
R26-SE-008 | Bandara S M Y M | IT22277886

The heuristic quality figures the Comparison stage charts. Prototype-grade by
design: they are derived from the smell mix and how much of it the developer
resolved, not measured from the code.

Moved out of diwo/orchestrator.py unchanged.
"""

def compute_metrics_before(smells: list) -> dict:
    critical = sum(1 for s in smells if s.get("severity") == "critical")
    high = sum(1 for s in smells if s.get("severity") == "high")
    medium = sum(1 for s in smells if s.get("severity") == "medium")
    low = sum(1 for s in smells if s.get("severity") == "low")

    # Heuristic complexity score
    complexity = min(100, 30 + critical * 15 + high * 8 + medium * 4 + low * 1)
    duplication = min(100, 10 + len(smells) * 3)
    maintainability = max(0, 100 - complexity - duplication // 2)

    return {
        "cyclomatic_complexity": complexity,
        "code_duplication_pct": duplication,
        "maintainability_index": maintainability,
        "total_smells": len(smells),
        "smell_breakdown": {"critical": critical, "high": high, "medium": medium, "low": low},
    }


def compute_metrics_after(metrics_before: dict, resolved_count: int, total_smells: int) -> dict:
    ratio = resolved_count / max(total_smells, 1)
    complexity_reduction = int(metrics_before["cyclomatic_complexity"] * ratio * 0.7)
    dup_reduction = int(metrics_before["code_duplication_pct"] * ratio * 0.6)

    new_complexity = max(5, metrics_before["cyclomatic_complexity"] - complexity_reduction)
    new_dup = max(0, metrics_before["code_duplication_pct"] - dup_reduction)
    new_maint = min(100, metrics_before["maintainability_index"] + complexity_reduction + dup_reduction // 2)

    before_breakdown = metrics_before.get("smell_breakdown", {})
    after_breakdown = {k: max(0, v - int(v * ratio)) for k, v in before_breakdown.items()}

    return {
        "cyclomatic_complexity": new_complexity,
        "code_duplication_pct": new_dup,
        "maintainability_index": new_maint,
        "total_smells": max(0, total_smells - resolved_count),
        "smell_breakdown": after_breakdown,
        "improvements": {
            "complexity_reduced_by": complexity_reduction,
            "duplication_reduced_by": dup_reduction,
            "maintainability_gained": new_maint - metrics_before["maintainability_index"],
        }
    }
