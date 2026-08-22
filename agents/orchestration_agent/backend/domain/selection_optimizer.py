"""
Selection optimiser
===================
R26-SE-008 | Bandara S M Y M | IT22277886

Choosing which smells to fix under a review-time budget is a 0/1 knapsack:
value = projected quality points, weight = review minutes, plus a risk ceiling
as a filter. Smell counts here are in the tens, so exact dynamic programming
runs in microseconds — no heuristic needed, and the result is provably optimal
for the given model.

Three presets, because "best" depends on what the developer is optimising for:

  best_value      maximise Σ automated points within the minute budget
  safe_wins       the same, restricted to low-risk items
  stop_bleeding   maximise Σ deferral interest retired — targets the files the
                  team actually edits, which is a different set from the ones
                  with the scariest severity labels

The optimiser only ever proposes EXECUTABLE smells. Advisory findings cannot
change code, so including them would spend budget on no-ops — which is the
defect this whole feature exists to remove.

Pure: takes impact records, returns ids. services/impact_service.py decides
when to call it.
"""

from domain.capability_map import EXECUTABLE

DEFAULT_BUDGET_MINUTES = 60

PRESETS = {
    "best_value": {
        "label": "Best value",
        "description": "Most quality points inside the time budget.",
        "max_risk": 1.0,
        "objective": "points",
    },
    "safe_wins": {
        "label": "Safe wins",
        "description": "Low-risk fixes only — nothing that needs careful review.",
        "max_risk": 0.35,
        "objective": "points",
    },
    "stop_bleeding": {
        "label": "Stop the bleeding",
        "description": "Targets debt in the files the team edits most.",
        "max_risk": 1.0,
        "objective": "interest",
    },
}

__all__ = ["PRESETS", "DEFAULT_BUDGET_MINUTES", "optimise", "optimise_preset"]


def _value_of(record, objective):
    if objective == "interest":
        return record["if_deferred"]["interest_per_quarter"]
    return record["if_selected"]["quality_gain"]["automated_points"]


def optimise(records, budget_minutes=DEFAULT_BUDGET_MINUTES, max_risk=1.0,
             objective="points"):
    """Exact 0/1 knapsack over the executable records.

    Returns { selected_ids, total_value, total_minutes, considered, skipped_* }.
    Weights are whole minutes, so the DP table is (items × budget) — trivial at
    this scale.
    """
    budget = max(int(budget_minutes or 0), 0)

    executable = [r for r in (records or []) if r["capability"]["status"] == EXECUTABLE]
    items = [r for r in executable if r["if_selected"]["risk"]["score"] <= max_risk]

    if not items or budget <= 0:
        return {
            "selected_ids": [],
            "total_value": 0.0,
            "total_minutes": 0,
            "considered": len(items),
            "skipped_advisory": len(records or []) - len(executable),
            "skipped_risky": len(executable) - len(items),
            "budget_minutes": budget,
            "objective": objective,
        }

    count = len(items)
    table = [[0.0] * (budget + 1) for _ in range(count + 1)]

    for i in range(1, count + 1):
        weight = max(int(items[i - 1]["if_selected"]["effort_minutes"]), 0)
        value = _value_of(items[i - 1], objective)
        row, prev = table[i], table[i - 1]
        for b in range(budget + 1):
            row[b] = prev[b]
            if weight <= b:
                candidate = prev[b - weight] + value
                if candidate > row[b]:
                    row[b] = candidate

    chosen = []
    remaining = budget
    for i in range(count, 0, -1):
        if table[i][remaining] != table[i - 1][remaining]:
            chosen.append(items[i - 1])
            remaining -= max(int(items[i - 1]["if_selected"]["effort_minutes"]), 0)

    chosen.reverse()
    return {
        "selected_ids": [r["smell_id"] for r in chosen],
        "total_value": round(table[count][budget], 2),
        "total_minutes": sum(int(r["if_selected"]["effort_minutes"]) for r in chosen),
        "total_points": round(
            sum(r["if_selected"]["quality_gain"]["automated_points"] for r in chosen), 2),
        "considered": count,
        "skipped_advisory": len(records or []) - len(executable),
        "skipped_risky": len(executable) - len(items),
        "budget_minutes": budget,
        "objective": objective,
    }


def optimise_preset(records, preset="best_value", budget_minutes=DEFAULT_BUDGET_MINUTES):
    """Run one of the named presets."""
    config = PRESETS.get(preset) or PRESETS["best_value"]
    result = optimise(
        records,
        budget_minutes=budget_minutes,
        max_risk=config["max_risk"],
        objective=config["objective"],
    )
    return {
        **result,
        "preset": preset if preset in PRESETS else "best_value",
        "preset_label": config["label"],
        "preset_description": config["description"],
    }
