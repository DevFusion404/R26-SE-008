"""
Stage 2 planning decision support
=================================
R26-SE-008 | Bandara S M Y M | IT22277886

Answers, for every RDP plan step, the question Stage 2 previously left the
developer alone with: "should I approve this?"

The problem this exists to fix
------------------------------
Stage 2 rendered the RDP plan and two buttons. Everything needed to reason
about a step already existed somewhere in the system — RDP's MCDA score, the
Stage 1 impact record, the SCTVA capability probe, the developer's own
preferences — but none of it was joined up on the approval screen, so the
honest description of the interaction was "click Approve twelve times". A
"Select All" button sat at the top of it.

What this module produces is a RECOMMENDATION, not a decision. Nothing here
approves anything: `auto_select_eligible` marks a step the "Select Recommended"
button may pre-tick, and the developer still has to press the forward action.
That boundary is the whole point of the feature.

The score
---------
    RDP recommendation quality        35
    Expected technical benefit        25
    Transformation safety             20
    Developer strategy match          10
    Historical developer feedback     10
                                     ---
    Decision Support Score           100

It is deliberately NOT called a confidence percentage. Nothing here is
calibrated against observed developer behaviour yet; it is a weighted, fully
decomposed sum whose every term is returned alongside it, so the UI can show
the arithmetic rather than ask for trust.

Gates before arithmetic
-----------------------
Three facts override the score outright, because they are about whether the
transformation can happen at all rather than how attractive it is:

  * an ADVISORY capability  -> manual_only     (SCTVA has no safe automatic form)
  * an UNKNOWN capability   -> not_recommended (no refactoring is even mapped)
  * a step map_step() cannot turn into an action -> not_recommended

and one caps it: a high-risk step can never be green, however high RDP scored
it. A gate that a big enough number can talk its way past is not a gate.

Pure functions. Impact records, feedback statistics and the SCTVA action set
all arrive as arguments; services/planning_recommendation_service.py gathers
them.
"""

from domain.capability_map import ADVISORY, EXECUTABLE, UNKNOWN, classify
from domain.sctva_mapper import StepMappingError, map_step

MODEL_VERSION = "planning-recommendation-1.0.0"

# ─────────────────────────────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────────────────────────────

RECOMMENDED = "recommended"
REVIEW = "review"
NOT_RECOMMENDED = "not_recommended"
MANUAL_ONLY = "manual_only"

CATEGORY_LABEL = {
    RECOMMENDED: "Recommended",
    REVIEW: "Review Carefully",
    NOT_RECOMMENDED: "Not Recommended",
    MANUAL_ONLY: "Manual Refactoring Suggested",
}

#: The two thresholds live here and nowhere else. The frontend reads the
#: category off the payload rather than re-deriving it from the score, so these
#: numbers cannot drift between the two halves of the feature.
RECOMMENDED_THRESHOLD = 80
REVIEW_THRESHOLD = 60

# ─────────────────────────────────────────────────────────────────────────────
# Weights
# ─────────────────────────────────────────────────────────────────────────────

WEIGHTS = {
    "rdp_quality": 35,
    "technical_benefit": 25,
    "transformation_safety": 20,
    "strategy_match": 10,
    "historical_feedback": 10,
}

#: Quality-gain points that count as full marks for technical benefit.
#: impact_model.quality_gain tops out at 10.0 for a critical, maximally
#: over-threshold, whole-file smell with a high-impact refactoring; 8 is the
#: point past which the difference stops mattering to the decision.
BENEFIT_REFERENCE_POINTS = 8.0

#: Risk bands, when no impact record supplies a measured risk score. Same
#: numbers as impact_model.RISK_BASE — a step and its smell must not disagree
#: about how risky the same refactoring is.
RISK_BASE = {"low": 0.20, "medium": 0.50, "high": 0.80}
IMPACT_BASE = {"low": 0.30, "medium": 0.60, "high": 1.00}

#: Real, matching developer decisions needed before history is allowed to move
#: the score at all. Below it the factor is neutral, so a refactoring nobody has
#: ever been shown is not punished for being new.
MIN_FEEDBACK_OBSERVATIONS = 5

#: Strength of the prior the acceptance estimate is smoothed toward, in
#: pseudo-observations. Stops "1 reject out of 1" reading as 0% forever.
FEEDBACK_PRIOR_STRENGTH = 5.0
FEEDBACK_DEFAULT_PRIOR = 0.5

# ─────────────────────────────────────────────────────────────────────────────
# Developer strategy
# ─────────────────────────────────────────────────────────────────────────────

SAFETY_FIRST = "safety_first"
BALANCED = "balanced"
MAX_IMPROVEMENT = "max_improvement"

STRATEGY_LABEL = {
    SAFETY_FIRST: "Safety First",
    BALANCED: "Balanced",
    MAX_IMPROVEMENT: "Maximum Improvement",
}

#: The developer-facing goal, expressed in the preference vocabulary the
#: backend re-ranker already speaks. The re-ranker is untouched: this is a
#: relabelling of its inputs, not a replacement for them.
STRATEGY_PREFERENCES = {
    SAFETY_FIRST: {"risk_tolerance": "conservative", "impact_focus": "medium"},
    BALANCED: {"risk_tolerance": "balanced", "impact_focus": "high"},
    MAX_IMPROVEMENT: {"risk_tolerance": "aggressive", "impact_focus": "high"},
}

_RISK_TOLERANCE_STRATEGY = {
    "conservative": SAFETY_FIRST,
    "balanced": BALANCED,
    "aggressive": MAX_IMPROVEMENT,
}

#: How well each risk band serves each goal, 0..1.
_STRATEGY_RISK_FIT = {
    SAFETY_FIRST: {"low": 1.0, "medium": 0.45, "high": 0.0},
    BALANCED: {"low": 0.9, "medium": 0.7, "high": 0.3},
    MAX_IMPROVEMENT: {"low": 0.7, "medium": 0.8, "high": 0.7},
}

#: ...and each impact band. Maximum Improvement weighs this above risk;
#: Safety First does the opposite.
_STRATEGY_IMPACT_FIT = {
    SAFETY_FIRST: {"low": 0.5, "medium": 0.8, "high": 1.0},
    BALANCED: {"low": 0.4, "medium": 0.7, "high": 1.0},
    MAX_IMPROVEMENT: {"low": 0.2, "medium": 0.6, "high": 1.0},
}

#: Relative weight of risk vs impact inside the strategy factor.
_STRATEGY_RISK_WEIGHT = {SAFETY_FIRST: 0.75, BALANCED: 0.5, MAX_IMPROVEMENT: 0.25}

__all__ = [
    "MODEL_VERSION",
    "RECOMMENDED", "REVIEW", "NOT_RECOMMENDED", "MANUAL_ONLY",
    "CATEGORY_LABEL", "RECOMMENDED_THRESHOLD", "REVIEW_THRESHOLD", "WEIGHTS",
    "SAFETY_FIRST", "BALANCED", "MAX_IMPROVEMENT", "STRATEGY_LABEL",
    "STRATEGY_PREFERENCES", "MIN_FEEDBACK_OBSERVATIONS",
    "preferences_for_strategy", "strategy_from_preferences",
    "normalize_rdp_score", "assess_step_mapping", "step_identity", "effective_risk",
    "build_step_recommendation", "summarize_recommendations",
]


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _number(value):
    """The value as a float, or None if it is not a usable number.

    `bool` is excluded on purpose: `True` is an int in Python, and a step
    carrying `score: true` should read as "no score", not as 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _band(value, fallback="medium"):
    """Coerce a rating to low | medium | high."""
    text = str(value or "").strip().lower()
    return text if text in ("low", "medium", "high") else fallback


def preferences_for_strategy(strategy):
    """The backend preference object a developer goal maps to."""
    return dict(STRATEGY_PREFERENCES.get(strategy, STRATEGY_PREFERENCES[BALANCED]))


def strategy_from_preferences(preferences):
    """Recover the developer goal from a preference object.

    An explicit `developer_strategy` wins; otherwise risk_tolerance names it,
    which keeps every existing caller — none of which knows about strategies —
    producing a sensible answer.
    """
    preferences = preferences or {}
    explicit = str(preferences.get("developer_strategy") or "").strip().lower()
    if explicit in STRATEGY_PREFERENCES:
        return explicit

    tolerance = str(preferences.get("risk_tolerance") or "").strip().lower()
    return _RISK_TOLERANCE_STRATEGY.get(tolerance, BALANCED)


def step_identity(step) -> str:
    """A step's identity across plan revisions.

    `step_id` cannot carry it: the preference re-ranker renumbers steps from 1,
    so step 1 of a revised plan is usually a different refactoring than step 1
    of the old one. The frontend keys carried-over decisions on exactly this
    triple, and the step-level feedback rows use it for idempotency, so the two
    must agree — hence one definition, here.
    """
    step = step or {}
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    return "|".join([
        str(step.get("smell_id") or ""),
        str(step.get("refactoring") or ""),
        str(target.get("file") or ""),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Factor 1 — RDP recommendation quality (35)
# ─────────────────────────────────────────────────────────────────────────────

def normalize_rdp_score(value):
    """Map an RDP score onto 0..1, and say which scale was assumed.

    RDP's MCDA path already returns 0..1, but score_candidate_with_impact()
    returns a 1..3 base plus bonuses and the ML path adds more on top, so
    treating every `step.score` as a probability would silently turn a 2.4 into
    "240% confident". Returns (value, basis); (None, "missing") when there is
    no usable number.
    """
    number = _number(value)
    if number is None:
        return None, "missing"
    if number < 0:
        return 0.0, "clamped_negative"
    if number <= 1.0:
        return number, "unit"
    if number <= 10.0:
        return number / 10.0, "ten_point"
    if number <= 100.0:
        return number / 100.0, "percent"
    return 1.0, "clamped_high"


def _selection_margin(step, chosen_value):
    """How far ahead of its best alternative RDP ranked this refactoring.

    A step chosen over five near-equal candidates is a weaker recommendation
    than one chosen by a wide margin, and the plan already carries the losing
    candidates. Returns (confidence 0..1, best_alternative or None); 0.5 —
    neutral — when there is nothing to compare against, because "RDP evaluated
    no alternatives" is not evidence either way.
    """
    alternatives = step.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives or chosen_value is None:
        return 0.5, None

    scores = []
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        value, _ = normalize_rdp_score(alternative.get("score"))
        if value is not None:
            scores.append(value)

    if not scores:
        return 0.5, None

    best = max(scores)
    if chosen_value <= 0:
        return 0.5, best

    # A 25% lead over the runner-up is treated as a decisive selection.
    return _clamp((chosen_value - best) / (chosen_value * 0.25)), best


def _rdp_quality_factor(step):
    """Factor 1. 85% the score itself, 15% how decisively it was selected."""
    raw = step.get("score")
    value, basis = normalize_rdp_score(raw)

    if value is None:
        # No score at all. Fall back to the ratings RDP always sends rather
        # than to a made-up constant, and label the fallback so the UI never
        # presents it as a real MCDA figure.
        impact = _band(step.get("impact") or step.get("expected_impact"))
        risk = _band(step.get("risk"))
        value = _clamp(IMPACT_BASE[impact] - (RISK_BASE[risk] - 0.2) * 0.5)
        basis = "derived_from_ratings"
        margin, best_alternative = 0.5, None
    else:
        margin, best_alternative = _selection_margin(step, value)

    points = WEIGHTS["rdp_quality"] * (0.85 * value + 0.15 * margin)

    return {
        "value": round(value, 3),
        "points": round(points, 1),
        "max_points": WEIGHTS["rdp_quality"],
        "raw_score": _number(raw),
        "basis": basis,
        "scoring_method": step.get("scoring_method"),
        "selection_margin": round(margin, 3),
        "best_alternative_score": (
            round(best_alternative, 3) if best_alternative is not None else None
        ),
        "alternatives_considered": len(step.get("alternatives") or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Factor 2 — Expected technical benefit (25)
# ─────────────────────────────────────────────────────────────────────────────

def _technical_benefit_factor(step, impact_record, capability_status):
    """Factor 2. The Stage 1 impact record leads; RDP's prediction refines it.

    Neither is required. With both, the record carries 65% — it is measured
    against CUQA's own metrics for this smell, while the prediction comes from
    a per-refactoring rule table — and the two are blended rather than one
    discarded.
    """
    sources = []
    record_value = None
    prediction_value = None
    quality_points = None

    gain = ((impact_record or {}).get("if_selected") or {}).get("quality_gain") or {}
    # An advisory step earns 0 automated points by design; its potential is the
    # honest figure for "is this refactoring worth doing at all".
    automated = _number(gain.get("automated_points"))
    potential = _number(gain.get("potential_points"))
    if capability_status == ADVISORY and potential is not None:
        quality_points = potential
    elif automated is not None:
        quality_points = automated
    elif potential is not None:
        quality_points = potential

    if quality_points is not None:
        record_value = _clamp(quality_points / BENEFIT_REFERENCE_POINTS)
        sources.append("impact_record")

    prediction = step.get("prediction")
    if isinstance(prediction, dict):
        maintainability = _number(prediction.get("maintainability_improvement"))
        cohesion = _number(prediction.get("cohesion_change"))
        coupling = _number(prediction.get("coupling_change"))
        parts = []
        if maintainability is not None:
            # RDP documents this as 0..1; normalize defensively anyway.
            parts.append(_clamp(
                maintainability if maintainability <= 1 else maintainability / 100.0))
        if cohesion is not None:
            parts.append(_clamp(0.5 + cohesion / 10.0))
        if coupling is not None:
            parts.append(_clamp(0.5 - coupling / 10.0))
        if parts:
            prediction_value = sum(parts) / len(parts)
            sources.append("rdp_prediction")

    if record_value is not None and prediction_value is not None:
        value = 0.65 * record_value + 0.35 * prediction_value
    elif record_value is not None:
        value = record_value
    elif prediction_value is not None:
        value = prediction_value
    else:
        # Last resort: the impact rating every plan step carries. Discounted,
        # because a rating is a weaker claim than a computed gain.
        value = IMPACT_BASE[_band(step.get("impact") or step.get("expected_impact"))] * 0.7
        sources.append("impact_rating")

    return {
        "value": round(value, 3),
        "points": round(WEIGHTS["technical_benefit"] * value, 1),
        "max_points": WEIGHTS["technical_benefit"],
        "quality_gain_points": round(quality_points, 2) if quality_points is not None else None,
        "quality_gain_low": _number(gain.get("automated_low")),
        "quality_gain_high": _number(gain.get("automated_high")),
        "error_band": _number((impact_record or {}).get("error_band")),
        "sources": sources,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Factor 3 — Transformation safety (20)
# ─────────────────────────────────────────────────────────────────────────────

def effective_risk(step, impact_record):
    """The risk band and score this step is judged on. Returns (band, score).

    There are two risk claims about the same edit, and they can disagree: RDP
    rates the refactoring it chose for THIS step, while the Stage 1 impact
    record rates the smell's generic capability adjusted for blast radius and
    test cover. The worse of the two wins.

    That is not a detail. Trusting the record alone is a real defect: a
    LongMethod in a single, tested file scores "low" there, so an RDP step RDP
    itself marked high-risk would sail straight past the high-risk gate and be
    offered as a green, auto-selectable recommendation — the one outcome §27
    exists to prevent. Trusting the step alone would throw away the blast
    radius and test-coverage evidence the record adds. Taking the maximum keeps
    both, and errs toward asking the developer to look.
    """
    risk_record = ((impact_record or {}).get("if_selected") or {}).get("risk") or {}

    step_band = _band(step.get("risk"))
    record_band = _band(risk_record.get("band"), fallback=step_band)
    band = max(step_band, record_band, key=lambda name: RISK_BASE[name])

    record_score = _number(risk_record.get("score"))
    score = max(
        RISK_BASE[step_band],
        record_score if record_score is not None else RISK_BASE[record_band],
    )
    return band, score


def _transformation_safety_factor(step, impact_record, capability_status, mapping):
    """Factor 3. Risk, blast radius, validation cover and mapping readiness.

    The capability and mapping terms appear here as well as in the gates on
    purpose: the gate decides the category, this decides how far below a green
    step an amber one sits.
    """
    selected = (impact_record or {}).get("if_selected") or {}
    risk_record = selected.get("risk") or {}
    risk_band, risk_score = effective_risk(step, impact_record)

    value = 1.0 - _clamp(risk_score)
    drivers = [d for d in (risk_record.get("drivers") or []) if isinstance(d, str)]

    blast = _number(selected.get("blast_radius_files")) or 1
    if blast > 6:
        value *= 0.75
    elif blast > 3:
        value *= 0.85

    validation = selected.get("validation") if isinstance(selected.get("validation"), list) else []
    if "behavioural" in validation:
        value = _clamp(value * 1.10)
    elif validation:
        # Syntax and structural checks only: SCTVA can prove the file still
        # parses, not that it still does the same thing.
        value *= 0.95

    if capability_status == ADVISORY:
        value *= 0.5
    elif capability_status == UNKNOWN:
        value *= 0.35

    if not mapping["actual_step_mappable"]:
        value *= 0.4

    value = _clamp(value)
    return {
        "value": round(value, 3),
        "points": round(WEIGHTS["transformation_safety"] * value, 1),
        "max_points": WEIGHTS["transformation_safety"],
        "risk_score": round(risk_score, 2),
        "risk_band": risk_band,
        "blast_radius_files": int(blast),
        "validation": validation,
        "drivers": drivers,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Factor 4 — Developer strategy match (10)
# ─────────────────────────────────────────────────────────────────────────────

def _strategy_factor(step, impact_record, strategy, preferred_refactorings):
    """Factor 4. Does this step serve the goal the developer selected?"""
    strategy = strategy if strategy in STRATEGY_PREFERENCES else BALANCED

    # The same worse-of-the-two band the safety factor and the gate use, so a
    # Safety First developer is not told a step matches their goal while the
    # gate is holding it back for being high-risk.
    risk_band, _ = effective_risk(step, impact_record)
    impact_band = _band(step.get("impact") or step.get("expected_impact"))

    risk_weight = _STRATEGY_RISK_WEIGHT[strategy]
    value = (
        risk_weight * _STRATEGY_RISK_FIT[strategy][risk_band]
        + (1.0 - risk_weight) * _STRATEGY_IMPACT_FIT[strategy][impact_band]
    )

    preferred = {str(r) for r in (preferred_refactorings or [])}
    matched_preference = bool(preferred) and step.get("refactoring") in preferred
    if matched_preference:
        value = _clamp(value + 0.15)

    return {
        "value": round(value, 3),
        "points": round(WEIGHTS["strategy_match"] * value, 1),
        "max_points": WEIGHTS["strategy_match"],
        "strategy": strategy,
        "strategy_label": STRATEGY_LABEL[strategy],
        "risk_band": risk_band,
        "impact_band": impact_band,
        "matched_preferred_refactoring": matched_preference,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Factor 5 — Historical developer feedback (10)
# ─────────────────────────────────────────────────────────────────────────────

def _feedback_factor(feedback_stats, evidenced_factors):
    """Factor 5. Real, collected step-level decisions only.

    `feedback_stats` is {observations, accepted, prior?} for THIS
    (smell_type, refactoring) pair, counted from feedback_entries rows the
    developer actually produced. Synthetic training rows never reach here: they
    exist to exercise the ML pipeline, and presenting them back as "your
    history" would be a fabrication.

    Below the sample threshold the factor is IMPUTED at the mean of the four
    evidenced factors rather than pinned at 0.5. Pinning it looks neutral and
    is not: it silently caps every score at 95 and drags a genuinely strong
    step from 90 down to 80, so on a fresh install — where no history exists by
    definition — nothing could ever be green. Imputing at the mean makes the
    missing factor neither help nor hurt, which is what "neutral" has to mean
    for the thresholds to survive the first session.
    """
    stats = feedback_stats or {}
    observations = int(_number(stats.get("observations")) or 0)
    accepted = int(_number(stats.get("accepted")) or 0)
    prior = _number(stats.get("prior"))
    if prior is None:
        prior = FEEDBACK_DEFAULT_PRIOR

    if observations < MIN_FEEDBACK_OBSERVATIONS:
        ratios = [
            factor["points"] / factor["max_points"]
            for factor in evidenced_factors
            if factor["max_points"]
        ]
        value = _clamp(sum(ratios) / len(ratios)) if ratios else 0.5
        return {
            "value": round(value, 3),
            "points": round(WEIGHTS["historical_feedback"] * value, 1),
            "max_points": WEIGHTS["historical_feedback"],
            "sample_size": observations,
            "accepted": accepted,
            "minimum_sample": MIN_FEEDBACK_OBSERVATIONS,
            "status": "insufficient_data",
            "acceptance_rate": None,
            "imputed": True,
            "message": (
                f"Not enough historical feedback yet ({observations} of "
                f"{MIN_FEEDBACK_OBSERVATIONS} matching decisions). This factor is "
                "scored at the average of the others, so it neither helps nor hurts."
            ),
        }

    smoothed = _clamp(
        (accepted + FEEDBACK_PRIOR_STRENGTH * prior)
        / (observations + FEEDBACK_PRIOR_STRENGTH)
    )
    return {
        "value": round(smoothed, 3),
        "points": round(WEIGHTS["historical_feedback"] * smoothed, 1),
        "max_points": WEIGHTS["historical_feedback"],
        "sample_size": observations,
        "accepted": accepted,
        "minimum_sample": MIN_FEEDBACK_OBSERVATIONS,
        "status": "observed",
        "acceptance_rate": round(accepted / observations, 3),
        "smoothed_rate": round(smoothed, 3),
        "prior": round(prior, 3),
        "imputed": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCTVA readiness
# ─────────────────────────────────────────────────────────────────────────────

def assess_step_mapping(step) -> dict:
    """Can SCTVA actually execute THIS step, with the parameters it carries?

    capability_map answers "can this refactoring map to an action", using a
    deliberately over-specified probe step. That is the right question for
    Stage 1 and the wrong one for Stage 2, where a concrete step either does or
    does not carry the parameters the action needs. So the real mapper is run
    against the real step.

    Nothing is executed: map_step is pure, and the returned action is
    discarded. StepMappingError — the mapper's way of saying "supported
    refactoring, insufficient parameters" — is caught and reported as the
    missing requirement it names.
    """
    try:
        action = map_step(step or {})
    except StepMappingError as exc:
        return {
            "actual_step_mappable": False,
            "action_type": None,
            "reason": str(exc),
            "missing_requirements": [str(exc)],
        }
    except Exception as exc:  # pragma: no cover - the mapper is pure, but Stage 2 must not crash
        return {
            "actual_step_mappable": False,
            "action_type": None,
            "reason": f"The step could not be checked against the SCTVA mapper: {exc}",
            "missing_requirements": ["the step could not be mapped"],
        }

    if not action or action.get("action_type") in (None, "noop"):
        return {
            "actual_step_mappable": False,
            "action_type": (action or {}).get("action_type"),
            "reason": "SCTVA has no action for this refactoring; it would be sent as a no-op.",
            "missing_requirements": [],
        }

    return {
        "actual_step_mappable": True,
        "action_type": action["action_type"],
        "reason": f"SCTVA would apply '{action['action_type']}' to this location.",
        "missing_requirements": [],
    }


def _capability_for_step(step, supported_actions):
    """The general capability for the step's smell type.

    Falls back to classifying by refactoring name when the step carries no
    smell type: a fallback plan's steps do not always have one, and answering
    "unknown" there would mark a perfectly executable Extract Method red.
    """
    smell_type = str(step.get("smell_type") or "").strip()
    if smell_type:
        capability = classify(smell_type, supported_actions)
        if capability.get("status") != UNKNOWN:
            return capability

    # No smell type, or one REFACTORING_MAP does not know: decide from the
    # refactoring the step actually names. Imported here rather than at module
    # scope because these are the private tables of capability_map — using them
    # is the exception, and keeping the import local says so.
    from domain.capability_map import (
        ADVISORY_REASON, REQUIRED_PARAMETERS, refactoring_action,
    )
    from domain.sctva_mapper import UNSUPPORTED_REFACTORINGS

    refactoring = str(step.get("refactoring") or "").strip()
    base = {
        "smell_type": smell_type or None,
        "refactoring": refactoring or None,
        "risk": _band(step.get("risk")),
        "impact": _band(step.get("impact") or step.get("expected_impact")),
    }
    if not refactoring:
        return {**base, "status": UNKNOWN, "action_type": None,
                "reason": "The step names no refactoring, so no SCTVA action can be derived."}

    key = refactoring.lower()
    if key in UNSUPPORTED_REFACTORINGS:
        return {**base, "status": ADVISORY, "action_type": None,
                "reason": ADVISORY_REASON.get(key, UNSUPPORTED_REFACTORINGS[key])}

    action = refactoring_action(refactoring)
    if not action or action == "noop":
        return {**base, "status": ADVISORY, "action_type": None,
                "reason": (f"'{refactoring}' has no matching branch in the SCTVA action "
                           "mapper, so a plan step for it is sent as a no-op."),
                "gap": True}

    if supported_actions is not None and action not in supported_actions:
        return {**base, "status": ADVISORY, "action_type": action,
                "reason": f"The running SCTVA build does not expose '{action}'."}

    return {**base, "status": EXECUTABLE, "action_type": action,
            "required_parameters": REQUIRED_PARAMETERS.get(action, []),
            "reason": f"SCTVA applies '{action}' to this location."}


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify(score, capability_status, mapping, risk_band):
    """Technical gates first, arithmetic second. Returns (category, gate)."""
    if capability_status == ADVISORY:
        return MANUAL_ONLY, "capability_advisory"
    if capability_status == UNKNOWN:
        return NOT_RECOMMENDED, "capability_unknown"
    if not mapping["actual_step_mappable"]:
        return NOT_RECOMMENDED, "step_not_mappable"

    if risk_band == "high":
        # A high-risk step can reach amber on merit, never green. Approving it
        # stays available; doing so without reading it does not.
        return (REVIEW if score >= REVIEW_THRESHOLD else NOT_RECOMMENDED), "high_risk_cap"

    if score >= RECOMMENDED_THRESHOLD:
        return RECOMMENDED, None
    if score >= REVIEW_THRESHOLD:
        return REVIEW, None
    return NOT_RECOMMENDED, None


# ─────────────────────────────────────────────────────────────────────────────
# Explanation
# ─────────────────────────────────────────────────────────────────────────────

def _sentence(text):
    """Capitalise a fragment that came from a table without changing its wording."""
    text = str(text or "").strip()
    return text[0].upper() + text[1:] if text else text


def _reasons_and_warnings(step, factors, capability, mapping, plan_source):
    """Human-readable evidence, derived from the factors that produced the score.

    Every recommendation must carry at least one reason. These are generated
    from the numbers above rather than written per category, so a reason can
    never contradict the score it sits next to.
    """
    reasons, warnings = [], []

    rdp = factors["rdp_quality"]
    benefit = factors["technical_benefit"]
    safety = factors["transformation_safety"]
    strategy = factors["strategy_match"]
    history = factors["historical_feedback"]

    # ── Expected benefit ────────────────────────────────────────────────────
    points = benefit["quality_gain_points"]
    suffix = f" (~+{points} points)" if points is not None else ""
    if benefit["value"] >= 0.6:
        reasons.append(f"High expected quality improvement{suffix}")
    elif benefit["value"] >= 0.35:
        reasons.append(f"Moderate expected quality improvement{suffix}")
    else:
        warnings.append(f"Limited expected quality improvement{suffix}")

    # ── Risk ────────────────────────────────────────────────────────────────
    if safety["risk_band"] == "low":
        reasons.append("Low transformation risk")
    elif safety["risk_band"] == "medium":
        warnings.append("Medium transformation risk — read the diff before accepting")
    else:
        warnings.append("High transformation risk requires developer review")

    drivers = safety["drivers"][:2]
    for driver in drivers:
        warnings.append(_sentence(driver))

    if safety["blast_radius_files"] > 1:
        warnings.append(f"{safety['blast_radius_files']} files reference this entity")
    else:
        reasons.append("Only one source file is affected")

    if "behavioural" in (safety["validation"] or []):
        reasons.append("Behavioural validation is available for this file")
    elif safety["validation"] and not any("test" in d.lower() for d in drivers):
        # The impact record's risk drivers already say this when they know it;
        # repeating it as a second warning reads as two separate problems.
        warnings.append("No behavioural tests were found for this file")

    # ── Capability / concrete mapping ───────────────────────────────────────
    status = capability.get("status")
    if status == EXECUTABLE and mapping["actual_step_mappable"]:
        reasons.append(f"SCTVA can execute this transformation ({mapping['action_type']})")
        reasons.append("Required transformation parameters are present on this step")
    elif status == EXECUTABLE:
        warnings.append(
            f"SCTVA supports {step.get('refactoring')}, but this step is incomplete: "
            f"{mapping['reason']}"
        )
    elif status == ADVISORY:
        reasons.append("The code smell is a real finding and the refactoring may improve the design")
        warnings.append(_sentence(
            capability.get("reason") or "SCTVA cannot safely automate this refactoring"))
        warnings.append(
            "Selecting this will not produce an automatic code change in the current SCTVA build")
    else:
        warnings.append(_sentence(
            capability.get("reason") or "No refactoring is mapped for this smell type"))

    # ── RDP ─────────────────────────────────────────────────────────────────
    if rdp["basis"] in ("missing", "derived_from_ratings"):
        warnings.append(
            "RDP did not score this step; its quality is estimated from the impact "
            "and risk ratings"
        )
    elif rdp["value"] >= 0.7:
        reasons.append(f"RDP ranked this refactoring highly ({rdp['value']:.2f})")

    if rdp["best_alternative_score"] is not None:
        if rdp["selection_margin"] >= 0.6:
            reasons.append(
                f"RDP ranked {step.get('refactoring')} clearly ahead of "
                f"{rdp['alternatives_considered']} evaluated alternative(s)"
            )
        elif rdp["selection_margin"] <= 0.25:
            warnings.append("RDP scored an alternative refactoring almost as highly")

    # ── Strategy ────────────────────────────────────────────────────────────
    if strategy["value"] >= 0.7:
        reasons.append(f"Matches your {strategy['strategy_label']} strategy")
    elif strategy["value"] <= 0.4:
        warnings.append(f"Conflicts with your {strategy['strategy_label']} strategy")
    if strategy["matched_preferred_refactoring"]:
        reasons.append("You marked this refactoring as preferred")

    # ── History ─────────────────────────────────────────────────────────────
    if history["status"] == "observed":
        line = (
            f"You have accepted {history['accepted']} of {history['sample_size']} "
            f"similar steps ({int(history['acceptance_rate'] * 100)}%)"
        )
        (reasons if history["value"] >= 0.5 else warnings).append(line)

    if plan_source and plan_source != "rdp_agent":
        warnings.append(
            "This plan came from the DIWO local fallback planner, so RDP-specific "
            "scoring evidence is not available"
        )

    if not reasons:
        reasons.append("No supporting evidence was found for this step — review it manually")

    # Several tables can phrase the same fact, and a card listing one problem
    # twice reads as two problems. Order is preserved: the strongest evidence
    # is generated first and the collapsed card shows only the first two.
    return _unique(reasons), _unique(warnings)


def _unique(lines):
    seen = set()
    return [line for line in lines
            if line and not (line in seen or seen.add(line))]


def _summary_sentence(category, factors, capability, mapping):
    if category == MANUAL_ONLY:
        return (
            "The smell is valid and the refactoring may help, but the current SCTVA build "
            "has no safe automatic form — plan this as manual work."
        )
    if category == NOT_RECOMMENDED and capability.get("status") == UNKNOWN:
        return "No refactoring is mapped for this smell type, so nothing can be transformed."
    if category == NOT_RECOMMENDED and not mapping["actual_step_mappable"]:
        return f"This step cannot be transformed as written: {mapping['reason']}"

    benefit = factors["technical_benefit"]["value"]
    risk_band = factors["transformation_safety"]["risk_band"]

    if category == RECOMMENDED:
        return (
            f"Good expected quality improvement at {risk_band} transformation risk, "
            "with executable SCTVA support and complete parameters."
        )
    if category == REVIEW:
        if risk_band == "high":
            return "High expected benefit, but transformation risk requires developer review."
        # Name the factor that actually held the step back, rather than listing
        # every axis it could have been. The weakest factor is the answer.
        weakest = min(
            factors.values(), key=lambda f: f["points"] / f["max_points"] if f["max_points"] else 1
        )
        limiter = {
            "rdp_quality": "RDP scored this candidate only moderately",
            "technical_benefit": "the expected quality improvement is moderate",
            "transformation_safety": f"transformation safety is limited at {risk_band} risk",
            "strategy_match": "it does not fit the selected developer strategy well",
            "historical_feedback": "your acceptance history for this kind of step is mixed",
        }[next(k for k, v in factors.items() if v is weakest)]
        return (
            f"{'Strong' if benefit >= 0.6 else 'Moderate'} expected benefit, but "
            f"{limiter} — read this step before approving it."
        )
    return "Expected benefit is weak relative to the risk and the available evidence."


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_step_recommendation(step, impact_record=None, developer_strategy=BALANCED,
                              feedback_stats=None, supported_actions=None,
                              preferred_refactorings=None, plan_source=None) -> dict:
    """The full decision-support record for one RDP plan step.

    Never raises on incomplete input: a missing impact record, a missing RDP
    score, an empty prediction and an absent feedback history all degrade to
    stated neutral fallbacks, because Stage 2 has to render whether or not the
    other three agents are up.
    """
    step = step or {}
    strategy = developer_strategy if developer_strategy in STRATEGY_PREFERENCES else BALANCED

    capability = _capability_for_step(step, supported_actions)
    mapping = assess_step_mapping(step)
    status = capability.get("status")

    # The four evidenced factors are computed first; the feedback factor needs
    # them, because with too little history it is imputed at their mean.
    factors = {
        "rdp_quality": _rdp_quality_factor(step),
        "technical_benefit": _technical_benefit_factor(step, impact_record, status),
        "transformation_safety": _transformation_safety_factor(
            step, impact_record, status, mapping),
        "strategy_match": _strategy_factor(
            step, impact_record, strategy, preferred_refactorings),
    }
    factors["historical_feedback"] = _feedback_factor(
        feedback_stats, list(factors.values()))

    score = round(sum(factor["points"] for factor in factors.values()))
    risk_band = factors["transformation_safety"]["risk_band"]
    category, gate = _classify(score, status, mapping, risk_band)
    reasons, warnings = _reasons_and_warnings(
        step, factors, capability, mapping, plan_source)

    selected = (impact_record or {}).get("if_selected") or {}
    deferred = (impact_record or {}).get("if_deferred") or {}
    gain = selected.get("quality_gain") or {}

    return {
        "model_version": MODEL_VERSION,
        "category": category,
        "label": CATEGORY_LABEL[category],
        "score": score,
        "max_score": 100,
        # The ONLY flag "Select Recommended" reads. Green, and nothing else.
        "auto_select_eligible": category == RECOMMENDED,
        "gate": gate,
        "summary": _summary_sentence(category, factors, capability, mapping),
        "reasons": reasons,
        "warnings": warnings,
        "factors": factors,
        "capability": {
            "status": status,
            "action_type": mapping["action_type"] or capability.get("action_type"),
            "actual_step_mappable": mapping["actual_step_mappable"],
            "missing_requirements": mapping["missing_requirements"],
            "reason": capability.get("reason"),
            "required_parameters": capability.get("required_parameters") or [],
        },
        "impact": {
            "quality_gain_points": _number(gain.get("automated_points")),
            "potential_gain_points": _number(gain.get("potential_points")),
            "quality_gain_low": _number(gain.get("automated_low")),
            "quality_gain_high": _number(gain.get("automated_high")),
            "risk_band": risk_band,
            "effort_minutes": _number(selected.get("effort_minutes")),
            "blast_radius_files": factors["transformation_safety"]["blast_radius_files"],
            "validation": factors["transformation_safety"]["validation"],
            "has_record": bool(impact_record),
        },
        "deferral": {
            "carried_points": _number(deferred.get("carried_points")),
            "change_pressure": deferred.get("change_pressure"),
            "interest_per_quarter": _number(deferred.get("interest_per_quarter")),
            "churn_known": bool(deferred.get("churn_known")),
            "explanation": deferred.get("explanation"),
        },
        "developer_strategy": strategy,
        "plan_source": plan_source,
        "step_identity": step_identity(step),
    }


def summarize_recommendations(steps, developer_strategy=BALANCED, plan_source=None) -> dict:
    """Roll the per-step recommendations up into the Stage 2 header block.

    Steps with no `decision_support` are counted under `unclassified` rather
    than silently folded into one of the four categories — a plan half of which
    was never assessed must not read as a plan that was.
    """
    counts = {RECOMMENDED: 0, REVIEW: 0, NOT_RECOMMENDED: 0, MANUAL_ONLY: 0}
    risk_rank = {"low": 0, "medium": 1, "high": 2}

    auto_selectable = 0
    projected_gain = 0.0
    gain_seen = False
    review_minutes = 0
    minutes_seen = False
    total_minutes = 0
    total_minutes_seen = False
    unclassified = 0
    max_risk = None

    for step in steps or []:
        support = (step or {}).get("decision_support")
        if not isinstance(support, dict):
            unclassified += 1
            continue

        category = support.get("category")
        if category in counts:
            counts[category] += 1

        impact = support.get("impact") or {}
        minutes = _number(impact.get("effort_minutes"))

        # The projection and the effort figure describe the SAME set — the
        # steps "Select Recommended" would tick. A header pairing the gain of
        # one set with the review cost of another is a comparison the developer
        # cannot act on; the whole-plan figure is carried separately.
        if support.get("auto_select_eligible"):
            auto_selectable += 1
            points = _number(impact.get("quality_gain_points"))
            if points is not None:
                projected_gain += points
                gain_seen = True
            if minutes is not None:
                review_minutes += int(minutes)
                minutes_seen = True

        if minutes is not None:
            total_minutes += int(minutes)
            total_minutes_seen = True

        band = impact.get("risk_band")
        if band in risk_rank and (max_risk is None or risk_rank[band] > risk_rank[max_risk]):
            max_risk = band

    return {
        "model_version": MODEL_VERSION,
        RECOMMENDED: counts[RECOMMENDED],
        REVIEW: counts[REVIEW],
        NOT_RECOMMENDED: counts[NOT_RECOMMENDED],
        MANUAL_ONLY: counts[MANUAL_ONLY],
        "unclassified": unclassified,
        "total_steps": len(steps or []),
        "auto_selectable": auto_selectable,
        # None rather than 0.0 when nothing could be projected: "this could not
        # be computed" and "this is worth nothing" are different answers.
        "projected_quality_gain": round(projected_gain, 2) if gain_seen else None,
        "estimated_review_minutes": review_minutes if minutes_seen else None,
        "total_review_minutes": total_minutes if total_minutes_seen else None,
        "max_risk": max_risk,
        "developer_strategy": developer_strategy,
        "developer_strategy_label": STRATEGY_LABEL.get(
            developer_strategy, STRATEGY_LABEL[BALANCED]),
        "plan_source": plan_source,
        "thresholds": {
            "recommended": RECOMMENDED_THRESHOLD,
            "review": REVIEW_THRESHOLD,
        },
    }
