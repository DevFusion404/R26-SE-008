"""
Selection impact model
======================
R26-SE-008 | Bandara S M Y M | IT22277886

Covers the three things the impact feature claims, in the order they matter:

  1. THE FEASIBILITY GATE IS TRUTHFUL. A green "auto-fixable" chip on a smell
     that comes back as a no-op is worse than no chip at all, so the gate is
     asserted against the real sctva_mapper, including the four smell types
     whose refactoring name has no branch in it.

  2. THE MODEL DISCRIMINATES. The whole point of replacing the count ratio is
     that two smells with the same count no longer score the same. These tests
     pin the factors that make them differ.

  3. THE OPTIMISER IS OPTIMAL AND SAFE. Exact knapsack, never proposes an
     advisory smell, never exceeds its budget.

Run from the backend directory:

    python -m tests.test_impact_model
"""

from domain.capability_map import (
    ADVISORY, EXECUTABLE, UNKNOWN, classify, classify_all,
)
from domain.impact_model import aggregate, build_impact_record
from domain.selection_optimizer import optimise, optimise_preset
from domain.smell_graph import build_edges, selection_notes

#: The smell types the CUQA agent actually emits, read out of its detectors.
#: `GodClass` is deliberately absent: REFACTORING_MAP carries a "God Class"
#: entry and cuqa_normalizer lists GodClass as class-level, but no CUQA detector
#: produces either spelling, so it can never reach Stage 1.
CUQA_SMELL_TYPES = [
    "BareExcept", "Comments", "DataClumps", "DeadCode", "DeepNesting",
    "DuplicateCode", "FeatureEnvy", "GlobalVariable", "InappropriateIntimacy",
    "LargeClass", "LargeHeaderFile", "LazyClass", "LongFunction", "LongMethod",
    "MagicNumber", "MessageChains", "PrimitiveObsession", "SpeculativeGenerality",
    "SwitchStatements", "TooManyParameters", "UnsafeFunctionUsage",
]

failures = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def smell(sid, stype, severity="high", line=10, lines=(10, 60), loc=200,
          file="src/Order.java", **metrics):
    return {
        "id": sid, "type": stype, "severity": severity, "line": line,
        "entity": metrics.pop("entity", "doThing"),
        "location": {"file": file, "class": "Order", "method": "doThing",
                     "lines": list(lines)},
        "metrics": {"lines_of_code": loc, **metrics},
    }


def main():
    print("\n1. feasibility gate — what the pipeline can actually fix")

    for stype, action in (("LongMethod", "extract_method"),
                          ("DuplicateCode", "extract_method"),
                          ("DeadCode", "remove_dead_code"),
                          ("MagicNumber", "extract_constant"),
                          ("Comments", "rename_symbol")):
        c = classify(stype)
        check(f"{stype} -> {action}",
              c["status"] == EXECUTABLE and c["action_type"] == action,
              f"{c['status']} / {c['action_type']}")

    print("\n   advisory: SCTVA structurally cannot do these")
    for stype in ("LargeClass", "FeatureEnvy", "SwitchStatements",
                  "TooManyParameters", "MessageChains", "PrimitiveObsession",
                  "LazyClass", "SpeculativeGenerality", "DataClumps",
                  "InappropriateIntimacy", "LargeHeaderFile"):
        c = classify(stype)
        check(f"{stype} is advisory", c["status"] == ADVISORY and not c["action_type"],
              f"{c['status']} / {c['action_type']}")

    print("\n   advisory because of a NAME GAP between REFACTORING_MAP and map_step")
    # These four are the finding: SCTVA exposes replace_unsafe_function and
    # encapsulate_variable, but REFACTORING_MAP names the refactorings
    # differently from the branches in map_step, so a plan step for them can
    # never reach the transformer. The gate must not claim otherwise.
    for stype in ("UnsafeFunctionUsage", "GlobalVariable", "DeepNesting", "BareExcept"):
        c = classify(stype)
        check(f"{stype} is advisory and flagged as a gap",
              c["status"] == ADVISORY and c.get("gap") is True,
              f"{c['status']} gap={c.get('gap')}")

    c = classify("NotARealSmell")
    check("an unmapped smell type is 'unknown', not silently executable",
          c["status"] == UNKNOWN)

    print("\n   the live SCTVA action set can veto a mapping")
    c = classify("LongMethod", supported_actions={"rename_symbol"})
    check("extract_method missing from the build -> advisory",
          c["status"] == ADVISORY and c["action_type"] == "extract_method", str(c))
    c = classify("LongMethod", supported_actions={"extract_method"})
    check("present in the build -> executable", c["status"] == EXECUTABLE)

    every = classify_all()
    check("every mapped smell type classifies without raising", len(every) > 30)
    check("no mapped type is left 'unknown'",
          not [k for k, v in every.items() if v["status"] == UNKNOWN])

    # The invariant that actually matters: every smell type the CUQA agent can
    # emit must be classifiable. A new detector shipped without a
    # REFACTORING_MAP entry would otherwise reach Stage 1 as "unknown" and be
    # treated as advisory without anyone noticing.
    unmapped = [s for s in CUQA_SMELL_TYPES if classify(s)["status"] == UNKNOWN]
    check(f"all {len(CUQA_SMELL_TYPES)} smell types CUQA can emit are mapped",
          not unmapped, f"unmapped: {unmapped}")

    print("\n2. the model discriminates where the count ratio could not")

    mild = build_impact_record(smell("a", "LongMethod", cyclomatic_complexity=11,
                                     lines=(10, 20), loc=400))
    severe = build_impact_record(smell("b", "LongMethod", cyclomatic_complexity=40,
                                       lines=(10, 210), loc=400))
    check("a worse, wider LongMethod scores strictly higher than a mild one",
          severe["if_selected"]["quality_gain"]["automated_points"]
          > mild["if_selected"]["quality_gain"]["automated_points"],
          f"{severe['if_selected']['quality_gain']['automated_points']} vs "
          f"{mild['if_selected']['quality_gain']['automated_points']}")

    high = build_impact_record(smell("c", "LongMethod", severity="high"))
    low = build_impact_record(smell("d", "LongMethod", severity="low"))
    check("severity still moves the number",
          high["if_selected"]["quality_gain"]["automated_points"]
          > low["if_selected"]["quality_gain"]["automated_points"])

    advisory = build_impact_record(smell("e", "LargeClass", method_count=40))
    check("an advisory smell has ZERO automated gain",
          advisory["if_selected"]["quality_gain"]["automated_points"] == 0.0)
    check("but keeps its potential gain, for the by-hand case",
          advisory["if_selected"]["quality_gain"]["potential_points"] > 0)
    check("and its headline says no code will change",
          "will not change any code" in advisory["headline"])

    check("every gain carries a band, not a bare point value",
          mild["if_selected"]["quality_gain"]["automated_low"]
          < mild["if_selected"]["quality_gain"]["automated_points"]
          < mild["if_selected"]["quality_gain"]["automated_high"])

    print("\n3. risk names its drivers")
    risky = build_impact_record(smell("f", "LongMethod"), blast_radius=5, has_tests=False)
    safe = build_impact_record(smell("g", "LongMethod"), blast_radius=1, has_tests=True)
    check("more referencing files and no tests -> higher risk",
          risky["if_selected"]["risk"]["score"] > safe["if_selected"]["risk"]["score"])
    check("the missing test suite is named as a driver",
          any("no test file" in d for d in risky["if_selected"]["risk"]["drivers"]),
          str(risky["if_selected"]["risk"]["drivers"]))
    check("behavioural validation is only promised when tests exist",
          "behavioural" in safe["if_selected"]["validation"]
          and "behavioural" not in risky["if_selected"]["validation"])

    print("\n4. deferral cost — the branch the old UI had no answer for")
    cold = build_impact_record(smell("h", "LongMethod"), churn=0, churn_known=True)
    hot = build_impact_record(smell("i", "LongMethod"), churn=14, churn_known=True)
    check("debt in a frequently-edited file charges more interest",
          hot["if_deferred"]["interest_per_quarter"]
          > cold["if_deferred"]["interest_per_quarter"],
          f"{hot['if_deferred']['interest_per_quarter']} vs "
          f"{cold['if_deferred']['interest_per_quarter']}")
    check("and is labelled high pressure", hot["if_deferred"]["change_pressure"] == "high")
    check("a dormant file is labelled low pressure",
          cold["if_deferred"]["change_pressure"] == "low")

    unknown_churn = build_impact_record(smell("j", "LongMethod"))
    check("with no repository, churn is not claimed as known",
          unknown_churn["if_deferred"]["churn_known"] is False)
    check("and the headline drops the pressure clause",
          "change pressure" not in unknown_churn["headline"])

    print("\n5. aggregation and warnings")
    records = [
        build_impact_record(smell("s1", "LongMethod", cyclomatic_complexity=30)),
        build_impact_record(smell("s2", "DeadCode", severity="low")),
        build_impact_record(smell("s3", "LargeClass", method_count=40)),
    ]

    none_selected = aggregate(records, set(), 68.0)
    check("nothing selected -> zero capture", none_selected["capture_rate"] == 0.0)
    check("and the ceiling is still reported",
          none_selected["quality_ceiling"] > none_selected["quality_before"])

    all_selected = aggregate(records, {"s1", "s2", "s3"}, 68.0)
    check("selecting everything captures 100% of the AUTOMATED ceiling",
          all_selected["capture_rate"] == 1.0, str(all_selected["capture_rate"]))
    check("the advisory smell is counted separately",
          all_selected["advisory_count"] == 1 and all_selected["executable_count"] == 2)
    check("a mixed selection warns about the no-ops",
          any(w["level"] == "warning" for w in all_selected["warnings"]),
          str(all_selected["warnings"]))

    advisory_only = aggregate(records, {"s3"}, 68.0)
    check("an advisory-only selection is an ERROR, not a warning",
          any(w["level"] == "error" for w in advisory_only["warnings"]),
          str(advisory_only["warnings"]))
    check("because it would produce no code change at all",
          advisory_only["quality_projected"] == advisory_only["quality_before"])

    print("\n6. interaction graph")
    graph_smells = [
        smell("g1", "LargeClass", lines=(1, 200), line=1),
        smell("g2", "LongMethod", lines=(20, 60), line=20),
        smell("g3", "LongMethod", lines=(50, 90), line=50),
        smell("g4", "DuplicateCode", lines=(300, 320), line=300, file="src/A.java"),
        smell("g5", "DuplicateCode", lines=(400, 420), line=400, file="src/B.java"),
    ]
    edges = build_edges(graph_smells)
    kinds = {e["type"] for e in edges}
    check("containment is detected", "contains" in kinds, str(kinds))
    check("overlap is detected", "overlaps" in kinds, str(kinds))
    check("clone pairs are detected across files", "clone_of" in kinds, str(kinds))

    notes = selection_notes(edges, {"g2", "g3"})
    check("selecting two overlapping smells warns about ordering",
          any("Ordering conflict" in n["message"] for n in notes), str(notes))

    notes = selection_notes(edges, {"g4"})
    check("selecting one clone of a pair warns about the other",
          any("clone" in n["message"].lower() for n in notes), str(notes))

    check("an empty selection produces no notes", selection_notes(edges, set()) == [])

    print("\n7. optimiser")
    pool = [
        build_impact_record(smell("o1", "LongMethod", cyclomatic_complexity=40,
                                  lines=(1, 180), loc=200)),   # 12 min, high value
        build_impact_record(smell("o2", "DeadCode", severity="low")),        # 4 min
        build_impact_record(smell("o3", "MagicNumber", severity="low")),     # 5 min
        build_impact_record(smell("o4", "LargeClass", method_count=40)),     # advisory
    ]

    result = optimise(pool, budget_minutes=1000)
    check("an unlimited budget takes every executable smell",
          set(result["selected_ids"]) == {"o1", "o2", "o3"}, str(result["selected_ids"]))
    check("and never proposes the advisory one", "o4" not in result["selected_ids"])
    check("the advisory smell is reported as skipped", result["skipped_advisory"] == 1)

    tight = optimise(pool, budget_minutes=5)
    check("a tight budget is respected", tight["total_minutes"] <= 5,
          str(tight["total_minutes"]))
    check("and it is spent on the best value that fits",
          len(tight["selected_ids"]) >= 1, str(tight["selected_ids"]))

    zero = optimise(pool, budget_minutes=0)
    check("a zero budget selects nothing", zero["selected_ids"] == [])

    # Exhaustive check: the DP result must equal brute force over all subsets.
    import itertools
    items = [r for r in pool if r["capability"]["status"] == EXECUTABLE]
    budget = 16
    best = 0.0
    for size in range(len(items) + 1):
        for combo in itertools.combinations(items, size):
            minutes = sum(r["if_selected"]["effort_minutes"] for r in combo)
            if minutes <= budget:
                best = max(best, sum(
                    r["if_selected"]["quality_gain"]["automated_points"] for r in combo))
    dp = optimise(pool, budget_minutes=budget)
    check("DP matches exhaustive search — the result is genuinely optimal",
          abs(dp["total_value"] - best) < 1e-6, f"dp={dp['total_value']} brute={best}")

    safe_preset = optimise_preset(pool, preset="safe_wins", budget_minutes=1000)
    check("safe_wins admits only low-risk items",
          all(r["if_selected"]["risk"]["score"] <= 0.35
              for r in pool if r["smell_id"] in safe_preset["selected_ids"]),
          str(safe_preset["selected_ids"]))
    check("and reports its own label", safe_preset["preset_label"] == "Safe wins")

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'ALL CHECKS PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
