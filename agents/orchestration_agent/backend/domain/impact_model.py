"""
Per-smell selection impact
==========================
R26-SE-008 | Bandara S M Y M | IT22277886

Replaces the count-ratio projection in domain/metrics.py with a per-smell,
evidence-weighted one. Every score decomposes into named factors, so the UI can
show WHY a number is what it is rather than asking for trust.

The problem this exists to fix
------------------------------
metrics.compute_metrics_after() projects improvement from a COUNT:

    ratio = resolved_count / total_smells

so fixing one SwitchStatements in a 400-line God Class and fixing one Comments
smell produce the identical projected gain. Nothing about how bad the smell is,
how much of the file it covers, or whether the pipeline can even fix it enters
the formula. That is fine as a placeholder and useless as decision support.

Every record here answers both branches — what selecting buys, what deferring
costs — because a panel that only shows upside is an advocacy tool, not a
decision tool.

Tiers
-----
This module is TIER 1 (static): CUQA metrics + the refactoring tables + the
SCTVA capability probe. It is instant and selection-independent, which is what
lets the UI recompute on every checkbox click. It is also approximate, and says
so: every quality figure carries a band rather than pretending to be a point
value. Tier 2 (RDP dry run) and Tier 3 (measured, post-transformation) are
future refinements; `tier` and `model_version` exist so their records stay
distinguishable from these.

Pure functions. All external evidence — SCTVA capabilities, git churn, test
presence, blast radius — arrives as arguments; services/impact_service.py is
what gathers it.
"""

import math

from domain.capability_map import ADVISORY, EXECUTABLE, classify

MODEL_VERSION = "impact-1.0.0"

#: Static-tier error band. Stated, not hidden: the UI renders gains as a range,
#: and the calibration work that would narrow it is not built yet.
STATIC_ERROR_BAND = 0.35

SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.85, "medium": 0.55, "low": 0.25}
RISK_BASE = {"low": 0.20, "medium": 0.50, "high": 0.80}
IMPACT_BASE = {"low": 0.30, "medium": 0.60, "high": 1.00}

#: Detection thresholds — mirror CUQA's detectors so `magnitude` measures how
#: far past its own threshold a smell sits, not raw size.
THRESHOLDS = {
    "cyclomatic_complexity": 10,
    "parameter_count": 4,
    "method_count": 15,
    "nesting_depth": 3,
    "chain_length": 3,
    "primitive_field_count": 5,
    "external_field_accesses": 3,
}

#: Which metric drives each smell type's magnitude.
MAGNITUDE_METRIC = {
    "LongMethod": "cyclomatic_complexity", "LongFunction": "cyclomatic_complexity",
    "Long Method": "cyclomatic_complexity",
    "TooManyParameters": "parameter_count", "Long Parameter List": "parameter_count",
    "LargeClass": "method_count", "GodClass": "method_count",
    "Large Class": "method_count", "God Class": "method_count",
    "DeepNesting": "nesting_depth", "MessageChains": "chain_length",
    "PrimitiveObsession": "primitive_field_count",
    "Primitive Obsession": "primitive_field_count",
    "FeatureEnvy": "external_field_accesses", "Feature Envy": "external_field_accesses",
}

#: Rough per-step review burden in developer-minutes, by SCTVA action. Ordinal
#: estimates, not measurements — they rank options, they do not schedule work.
EFFORT_MINUTES = {
    "extract_method": 12, "remove_dead_code": 4, "rename_symbol": 6,
    "extract_constant": 5, "introduce_constant": 5,
    "replace_unsafe_function": 8, "encapsulate_variable": 10,
    "replace_literal": 5, "fault_injection": 15,
}

#: An advisory smell has to be fixed by hand, which costs more than reviewing a
#: generated diff.
MANUAL_EFFORT_MINUTES = 25

#: Window the deferral interest is quoted over.
CHURN_WINDOW_DAYS = 90

__all__ = [
    "MODEL_VERSION", "STATIC_ERROR_BAND",
    "build_impact_record", "aggregate", "quality_gain",
    "transformation_risk", "deferral_cost",
]


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _magnitude(smell: dict) -> float:
    """How far past its detection threshold this smell sits, normalised 0..1.

    A 40-branch method and an 11-branch method are both "LongMethod"; only this
    distinguishes them. Returns 0.5 when the driving metric is absent, so a
    missing metric never silently reads as "trivial".
    """
    metric_name = MAGNITUDE_METRIC.get(str(smell.get("type") or ""))
    if not metric_name:
        return 0.5

    value = (smell.get("metrics") or {}).get(metric_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.5

    threshold = THRESHOLDS.get(metric_name, 1)
    return _clamp((value - threshold) / (threshold * 2.0))


def _reach(smell: dict) -> float:
    """Share of its file this smell occupies, 0..1."""
    lines = (smell.get("location") or {}).get("lines") or []
    file_loc = (smell.get("metrics") or {}).get("lines_of_code") or 0
    if len(lines) < 2 or not file_loc:
        return 0.2

    span = max(0, (lines[1] or 0) - (lines[0] or 0))
    return _clamp(span / file_loc)


def quality_gain(smell: dict, capability: dict) -> dict:
    """Projected quality-score points recovered by fixing this smell.

        gain = 10 · severity · (0.4 + 0.6·magnitude) · (0.5 + 0.5·reach) · impact

    An advisory smell scores 0 AUTOMATED gain and carries its potential gain
    separately, so the UI can say "worth 6.1 points, but only by hand".
    """
    severity = SEVERITY_WEIGHT.get((smell.get("severity") or "low").lower(), 0.25)
    magnitude = _magnitude(smell)
    reach = _reach(smell)
    impact = IMPACT_BASE.get(capability.get("impact") or "medium", 0.6)

    potential = 10.0 * severity * (0.4 + 0.6 * magnitude) * (0.5 + 0.5 * reach) * impact
    automated = potential if capability.get("status") == EXECUTABLE else 0.0

    location = smell.get("location") or {}
    return {
        "automated_points": round(automated, 2),
        "potential_points": round(potential, 2),
        # Stated band, not a false point value — see STATIC_ERROR_BAND.
        "automated_low": round(automated * (1 - STATIC_ERROR_BAND), 2),
        "automated_high": round(automated * (1 + STATIC_ERROR_BAND), 2),
        "factors": {
            "severity": round(severity, 2),
            "magnitude": round(magnitude, 2),
            "reach": round(reach, 2),
            "refactoring_impact": round(impact, 2),
        },
        "explanation": (
            f"{smell.get('type')} at {location.get('file')}:{smell.get('line')} sits "
            f"{int(magnitude * 100)}% past its detection threshold and spans "
            f"{int(reach * 100)}% of the file; "
            f"{capability.get('refactoring') or 'no mapped refactoring'} has "
            f"{capability.get('impact') or 'unknown'} impact on that class of problem."
        ),
    }


def transformation_risk(smell: dict, capability: dict, blast_radius: int,
                        has_tests: bool) -> dict:
    """Behavioural risk of applying the fix, 0..1, with the drivers named."""
    base = RISK_BASE.get(capability.get("risk") or "medium", 0.5)
    radius_factor = 1.0 + 0.15 * min(max(blast_radius - 1, 0), 6)
    test_factor = 1.0 if has_tests else 1.35
    score = _clamp(base * radius_factor * test_factor)

    band = "low" if score < 0.35 else "medium" if score < 0.65 else "high"

    drivers = []
    if base >= 0.5:
        drivers.append(
            f"{capability.get('refactoring')} is inherently "
            f"{capability.get('risk')}-risk"
        )
    if blast_radius > 1:
        drivers.append(f"{blast_radius} files reference this entity")
    if not has_tests:
        drivers.append("no test file covers this source file")
    if not drivers:
        drivers.append("local edit, single file, covered by tests")

    return {"score": round(score, 2), "band": band, "drivers": drivers}


def deferral_cost(smell: dict, gain: dict, churn: int, days: int = CHURN_WINDOW_DAYS) -> dict:
    """What deferring this smell costs — the axis the current UI has no answer for.

        interest = potential_points · (1 + ln(1 + churn)) · severity_urgency

    Change frequency is the multiplier, and it is the whole idea: debt in code
    nobody edits is dormant; debt in the file the team touches weekly is charged
    every sprint. `churn` is the commit count touching this file in the window.
    """
    urgency = SEVERITY_WEIGHT.get((smell.get("severity") or "low").lower(), 0.25)
    churn = max(int(churn or 0), 0)
    interest = gain["potential_points"] * (1.0 + math.log1p(churn)) * urgency

    if churn >= 10:
        pressure = "high"
        narrative = f"edited {churn} times in {days} days — this debt is charged on every change"
    elif churn >= 3:
        pressure = "medium"
        narrative = f"edited {churn} times in {days} days — moderately active"
    elif churn > 0:
        pressure = "low"
        narrative = f"edited {churn} time{'s' if churn > 1 else ''} in {days} days — deferring is cheap"
    else:
        pressure = "low"
        narrative = (
            f"not edited in {days} days — deferring is cheap"
            if churn == 0 else ""
        )

    return {
        "carried_points": gain["potential_points"],
        "interest_per_quarter": round(interest, 2),
        "change_pressure": pressure,
        "churn_commits": churn,
        "churn_window_days": days,
        "explanation": (
            f"Skipping this leaves {gain['potential_points']} quality points on the table. "
            f"The file was {narrative}."
        ),
    }


def build_impact_record(smell: dict, *, supported_actions=None, blast_radius: int = 1,
                        has_tests: bool = False, churn: int = 0,
                        churn_known: bool = False) -> dict:
    """The full Selection Impact Record for one smell (Tier 1, static)."""
    capability = classify(str(smell.get("type") or ""), supported_actions)
    gain = quality_gain(smell, capability)
    risk = transformation_risk(smell, capability, blast_radius, has_tests)
    defer = deferral_cost(smell, gain, churn)

    action = capability.get("action_type")
    # No action at all means a human does it by hand, which is the expensive
    # case; an unlisted action is a mapped one this table has not costed yet.
    minutes = MANUAL_EFFORT_MINUTES if action is None else EFFORT_MINUTES.get(action, 15)

    location = smell.get("location") or {}
    return {
        "smell_id": smell.get("id"),
        "smell_type": smell.get("type"),
        "severity": smell.get("severity"),
        "file": location.get("file"),
        "line": smell.get("line"),
        "model_version": MODEL_VERSION,
        "tier": "static",
        "error_band": STATIC_ERROR_BAND,
        "capability": capability,
        "if_selected": {
            "quality_gain": gain,
            "risk": risk,
            "effort_minutes": minutes,
            "blast_radius_files": blast_radius,
            "validation": ["syntax", "structural"] + (["behavioural"] if has_tests else []),
        },
        "if_deferred": {**defer, "churn_known": bool(churn_known)},
        "headline": _headline(capability, gain, risk, minutes, defer, churn_known),
    }


def _headline(capability, gain, risk, minutes, defer, churn_known) -> str:
    """One sentence, plain language — what the row's tooltip says."""
    if capability.get("status") == EXECUTABLE:
        tail = (
            f"Skipping it carries {defer['carried_points']} points forward at "
            f"{defer['change_pressure']} change pressure."
            if churn_known else
            f"Skipping it carries {defer['carried_points']} points forward."
        )
        return (
            f"Fixing this recovers ~{gain['automated_points']} quality points at "
            f"{risk['band']} risk (~{minutes} min review). {tail}"
        )

    # The reasons come from several tables and not all of them end in a stop.
    reason = str(capability.get("reason") or "").rstrip()
    if reason and reason[-1] not in ".!?":
        reason += "."

    return (
        f"Real finding, but no automatic fix: {reason} "
        f"Worth ~{gain['potential_points']} points if done by hand. "
        f"Selecting it will not change any code in this run."
    )


def aggregate(records: list, selected_ids, quality_before: float) -> dict:
    """Roll per-smell records up into the selection-level projection.

    Replaces compute_metrics_after()'s count ratio with a sum over the smells
    the developer actually chose.
    """
    selected_ids = set(selected_ids or [])
    sel = [r for r in records if r["smell_id"] in selected_ids]
    skip = [r for r in records if r["smell_id"] not in selected_ids]

    captured = sum(r["if_selected"]["quality_gain"]["automated_points"] for r in sel)
    ceiling = sum(r["if_selected"]["quality_gain"]["automated_points"] for r in records)
    forgone = sum(r["if_deferred"]["carried_points"] for r in skip)
    interest = sum(r["if_deferred"]["interest_per_quarter"] for r in skip)

    executable_selected = [r for r in sel if r["capability"]["status"] == EXECUTABLE]
    advisory_selected = [r for r in sel if r["capability"]["status"] == ADVISORY]
    risks = [r["if_selected"]["risk"]["score"] for r in executable_selected]

    quality_before = float(quality_before or 0.0)
    return {
        "selected_count": len(sel),
        "executable_count": len(executable_selected),
        "advisory_count": len(advisory_selected),
        "skipped_count": len(skip),
        "quality_before": round(quality_before, 1),
        "quality_projected": round(min(100.0, quality_before + captured), 1),
        "quality_ceiling": round(min(100.0, quality_before + ceiling), 1),
        "captured_points": round(captured, 2),
        "ceiling_points": round(ceiling, 2),
        "capture_rate": round(captured / ceiling, 3) if ceiling else 0.0,
        "forgone_points": round(forgone, 2),
        "quarterly_interest": round(interest, 2),
        "effort_minutes": sum(r["if_selected"]["effort_minutes"] for r in sel),
        "max_risk": round(max(risks), 2) if risks else 0.0,
        "mean_risk": round(sum(risks) / len(risks), 2) if risks else 0.0,
        "error_band": STATIC_ERROR_BAND,
        "warnings": _warnings(advisory_selected, executable_selected, skip),
    }


def _warnings(advisory_selected, executable_selected, skipped) -> list:
    """The things worth interrupting the developer about."""
    out = []

    if advisory_selected and not executable_selected:
        out.append({
            "level": "error",
            "message": (
                f"All {len(advisory_selected)} selected smells are advisory-only. "
                "This run will produce no code changes."
            ),
        })
    elif advisory_selected:
        out.append({
            "level": "warning",
            "message": (
                f"{len(advisory_selected)} of your selections have no automatic fix and will "
                "come back as no-ops. They stay in the report as findings."
            ),
        })

    hot = [r for r in skipped
           if r["if_deferred"]["change_pressure"] == "high"
           and r["capability"]["status"] == EXECUTABLE]
    if hot:
        out.append({
            "level": "info",
            "message": (
                f"{len(hot)} skipped smell(s) sit in files the team edits frequently — "
                "the most expensive kind to defer."
            ),
        })

    return out
