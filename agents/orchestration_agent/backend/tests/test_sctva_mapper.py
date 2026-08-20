"""
SCTVA plan-mapper golden test
=============================
R26-SE-008 | Bandara S M Y M | IT22277886

domain/sctva_mapper.py is a port of the mapping that used to run in the DIWO
browser (frontend services/sctvaApi.js) before the SCTVA call moved behind the
orchestrator. The request SCTVA receives therefore had to stay byte-identical,
or a plan that transformed correctly before the move would quietly start
producing different actions.

fixtures/sctva_mapper_golden.json is the output of the ORIGINAL JavaScript
mapper, captured by running it under Node against fixtures/sctva_plan_cases.json.
This test replays those cases through the Python port and asserts an exact
match, so the fixture keeps pinning the port to the behaviour it replaced.

The cases cover every branch of map_step: each supported refactoring, each
"supported but under-specified" error path, the unsupported-refactoring noop,
malformed steps, an empty plan, plan-target fallback, and the JS
undefined-vs-null serialization rule.

Run from the backend directory:

    python -m tests.test_sctva_mapper
"""

import json
from pathlib import Path

from domain.sctva_mapper import collect_plan_source_paths, normalize_plan_for_sctva

FIXTURES = Path(__file__).parent / "fixtures"


def diff(expected, actual, path=""):
    """Deep comparison that reports the exact JSON path of every difference."""
    if isinstance(expected, bool) != isinstance(actual, bool):
        return [f"{path}: {expected!r} != {actual!r}"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                out.append(f"{path}.{key}: unexpected key (got {actual[key]!r})")
            elif key not in actual:
                out.append(f"{path}.{key}: missing key (expected {expected[key]!r})")
            else:
                out += diff(expected[key], actual[key], f"{path}.{key}")
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        out = []
        if len(expected) != len(actual):
            out.append(f"{path}: length {len(expected)} != {len(actual)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            out += diff(e, a, f"{path}[{i}]")
        return out
    if expected != actual:
        return [f"{path}: {expected!r} != {actual!r}"]
    return []


def main():
    cases = json.loads((FIXTURES / "sctva_plan_cases.json").read_text(encoding="utf-8"))
    golden = json.loads((FIXTURES / "sctva_mapper_golden.json").read_text(encoding="utf-8"))
    by_name = {entry["name"]: entry for entry in golden}

    failures = []
    for case in cases:
        plan = {"plan_id": case["plan_id"], "target": case.get("target"), "steps": case["steps"]}

        mapping = normalize_plan_for_sctva(plan, correlation_id=plan["plan_id"])
        # The only intended difference: the mapping moved agents.
        assert mapping["plan"]["metadata"]["mapped_by"] == "diwo_orchestrator"
        mapping["plan"]["metadata"]["mapped_by"] = "<mapper>"

        actual = {"mapping": mapping, "paths": collect_plan_source_paths(plan)}
        expected = {k: v for k, v in by_name[case["name"]].items() if k != "name"}

        differences = diff(expected, actual, case["name"])
        mark = "FAIL" if differences else "PASS"
        print(f"  [{mark}] {case['name']}")
        for line in differences:
            print(f"          {line}")
        failures += differences

    print(f"\n{'FAILED: %d difference(s)' % len(failures) if failures else 'ALL CASES MATCH THE ORIGINAL JS MAPPER'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
