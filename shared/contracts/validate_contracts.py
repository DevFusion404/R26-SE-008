"""
Contract validator
==================
R26-SE-008 | Bandara S M Y M | IT22277886

Validates captured inter-agent payloads against the schemas in this folder.

The schemas were BUILT FROM the payloads the running system actually exchanges,
not the other way round, so this script is what keeps them honest: capture a
fresh set of samples after a change and run it again.

    python shared/contracts/validate_contracts.py <samples_dir>

<samples_dir> holds one JSON file per contract, named after it:
cuqa_report.json, filtered_cuqa_report.json, rdp_plan.json, approved_plan.json,
sctva_request.json, sctva_result.json, workflow.json.

Requires: pip install jsonschema
"""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).parent

PAIRS = [
    ("cuqa-report.schema.json", "cuqa_report.json"),
    ("filtered-cuqa-report.schema.json", "filtered_cuqa_report.json"),
    ("rdp-plan.schema.json", "rdp_plan.json"),
    ("approved-plan.schema.json", "approved_plan.json"),
    ("sctva-request.schema.json", "sctva_request.json"),
    ("sctva-result.schema.json", "sctva_result.json"),
    ("workflow.schema.json", "workflow.json"),
]


def load_registry():
    registry = Registry()
    schemas = {}
    for path in sorted(CONTRACTS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, schemas


def main(samples_dir):
    samples = Path(samples_dir)
    registry, schemas = load_registry()

    errors = 0
    for schema_name, sample_name in PAIRS:
        sample = samples / sample_name
        if not sample.exists():
            print(f"[SKIP] {schema_name:36} no sample at {sample}")
            continue

        validator = Draft202012Validator(schemas[schema_name], registry=registry)
        data = json.loads(sample.read_text(encoding="utf-8"))
        found = sorted(validator.iter_errors(data), key=lambda e: list(e.path))

        if found:
            errors += len(found)
            print(f"[FAIL] {schema_name:36} <- {sample_name}")
            for e in found[:8]:
                where = "/".join(str(p) for p in e.path) or "<root>"
                print(f"         {where}: {e.message[:150]}")
        else:
            print(f"[ OK ] {schema_name:36} <- {sample_name}")

    print(f"\n{errors} validation error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
