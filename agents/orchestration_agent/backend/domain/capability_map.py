"""
Smell → refactoring → SCTVA action feasibility
==============================================
R26-SE-008 | Bandara S M Y M | IT22277886

Answers, at Stage 1, the question the developer cannot currently ask:
"if I select this smell, will any code actually change?"

The chain is already in the codebase, just never traversed forwards:

    CUQA smell type
        └─ plan_normalizer.REFACTORING_MAP        ──► refactoring name
              └─ sctva_mapper.map_step            ──► action_type (or noop)
                    └─ SCTVA /sctva/health        ──► does this build expose it?

Three outcomes:
  EXECUTABLE  SCTVA has a transformer; selecting this can change source code.
  ADVISORY    RDP will plan it, SCTVA will noop it. Real finding, no automated fix.
  UNKNOWN     no refactoring is mapped for the smell type at all.

Why this probes map_step instead of listing a table
---------------------------------------------------
The obvious implementation is a second dict, refactoring -> action_type. It is
also the wrong one: it duplicates the branch list inside
sctva_mapper.map_step() and silently goes stale the moment someone edits either
side. A stale feasibility gate is worse than none, because it puts a green
"auto-fixable" chip on a smell that will come back as a noop — the exact defect
this module exists to remove.

So the classification is DERIVED: each refactoring name is run through the real
map_step() with a synthetic step carrying every parameter any branch could ask
for. If map_step returns an action, the refactoring is mappable; if it returns
None or raises StepMappingError, it is not. The gate cannot drift from the
mapper, because it *is* the mapper.

What this can and cannot promise
--------------------------------
The probe uses a maximally-specified step, so EXECUTABLE means "a real plan
step for this refactoring CAN map to an SCTVA action" — not "will". A concrete
plan step still has to carry that action's required parameters; when it does
not, map_step turns it into a noop at transformation time. `required_parameters`
carries that caveat forward so the UI can be honest about it, and it is the
main reason the static tier is banded at ±35% rather than presented as exact.
"""

from domain.plan_normalizer import REFACTORING_MAP
from domain.sctva_mapper import StepMappingError, UNSUPPORTED_REFACTORINGS, map_step

EXECUTABLE = "executable"
ADVISORY = "advisory"
UNKNOWN = "unknown"

__all__ = [
    "EXECUTABLE", "ADVISORY", "UNKNOWN",
    "classify", "classify_all", "refactoring_action", "known_smell_types",
]

#: Every parameter name any map_step branch reads, with a plausible value.
#: The probe step is deliberately over-specified: the question being asked is
#: "does a mapping exist for this refactoring", not "is this particular plan
#: step complete".
_PROBE_PARAMETERS = {
    "old_name": "probe", "new_name": "probeRenamed",
    "method": "probe", "new_method_name": "probeCore",
    "source_lines": [1, 2], "start_line": 1, "end_line": 2, "source_line": 1,
    "literal_value": 1, "literal_values": [1], "constant_name": "PROBE",
    "hint": "probe",
    "unsafe_function": "strcpy", "safe_alternative": "strncpy",
    "variable_name": "probeVar",
    "old_literal": "a", "new_literal": "b",
    "original_logic": "a < b", "faulty_logic": "a <= b",
    "source_file": "probe/Probe.java",
    "source_class": "Probe",
}

_PROBE_TARGET = {"class": "Probe", "method": "probe", "variable": "probeVar",
                 "file": "probe/Probe.java"}

#: Parameters each action genuinely needs from a real plan step. Used only to
#: explain the caveat above — never to decide executability.
REQUIRED_PARAMETERS = {
    "extract_method": ["a source line range", "the method name"],
    "rename_symbol": ["the old and new names"],
    "extract_constant": ["the literal value"],
    "introduce_constant": ["a literal value, literal list, or hint"],
    "remove_dead_code": ["the method name or a source line"],
    "replace_unsafe_function": ["the unsafe function and its safe replacement"],
    "encapsulate_variable": ["the variable name"],
    "replace_literal": ["the old and new literals"],
    "fault_injection": ["the original and faulty logic"],
}

#: Why an advisory smell is still worth reporting, in the developer's language.
#: Keyed by the lowercased refactoring name.
ADVISORY_REASON = {
    "extract class": "needs a new class file and every call site updated — no safe automatic form yet",
    "move method": "needs coordinated edits in both the source and the destination class",
    "replace conditional with polymorphism": "needs new subclass or strategy definitions",
    "introduce parameter object": "needs a new type plus updates at every call site",
    "hide delegate": "needs semantic edits in several places at once",
    "replace data value with object": "needs semantic edits in several places at once",
    "inline class": "needs semantic edits in several places at once",
    "collapse hierarchy": "needs semantic edits in several places at once",
    "pull up method": "needs semantic edits in several places at once",
    "replace parameter with method call": "needs semantic edits in several places at once",
}

_action_cache = {}


def refactoring_action(refactoring: str):
    """The SCTVA action map_step() would emit for this refactoring, or None.

    Derived by running the real mapper, then cached — the answer depends only
    on the refactoring name, and the mapper is pure.
    """
    key = (refactoring or "").strip().lower()
    if not key:
        return None
    if key in _action_cache:
        return _action_cache[key]

    probe = {
        "step_id": 0,
        "refactoring": refactoring,
        "parameters": dict(_PROBE_PARAMETERS),
        "target": dict(_PROBE_TARGET),
        "location": {"file": "probe/Probe.java", "lines": [1, 2]},
    }

    try:
        action = map_step(probe)
    except StepMappingError:
        action = None

    action_type = action["action_type"] if action else None
    _action_cache[key] = action_type
    return action_type


def classify(smell_type: str, supported_actions=None) -> dict:
    """Classify one CUQA smell type as executable / advisory / unknown.

    `supported_actions` is SCTVA's live set from GET /sctva/health. When it is
    None the static tables decide alone, so this stays usable offline and in
    tests.
    """
    mapped = REFACTORING_MAP.get(smell_type)
    if not mapped:
        return {
            "status": UNKNOWN,
            "smell_type": smell_type,
            "refactoring": None,
            "action_type": None,
            "risk": "medium",
            "impact": "medium",
            "reason": f"No refactoring is mapped for smell type '{smell_type}'.",
        }

    refactoring, risk, impact = mapped
    key = refactoring.lower()
    base = {
        "smell_type": smell_type,
        "refactoring": refactoring,
        "risk": risk,
        "impact": impact,
    }

    if key in UNSUPPORTED_REFACTORINGS:
        return {
            **base, "status": ADVISORY, "action_type": None,
            "reason": ADVISORY_REASON.get(key, UNSUPPORTED_REFACTORINGS[key]),
        }

    action = refactoring_action(refactoring)
    if not action or action == "noop":
        # The refactoring is not on the unsupported list, yet the mapper still
        # cannot turn it into an action. That is a gap between REFACTORING_MAP
        # and map_step rather than a deliberate exclusion, and saying so is how
        # it gets noticed instead of silently producing noops.
        return {
            **base, "status": ADVISORY, "action_type": None,
            "reason": (
                f"'{refactoring}' has no matching branch in the SCTVA action mapper, "
                "so a plan step for it is sent as a no-op."
            ),
            "gap": True,
        }

    if supported_actions is not None and action not in supported_actions:
        return {
            **base, "status": ADVISORY, "action_type": action,
            "reason": f"The running SCTVA build does not expose '{action}'.",
        }

    return {
        **base, "status": EXECUTABLE, "action_type": action,
        "required_parameters": REQUIRED_PARAMETERS.get(action, []),
        "reason": f"SCTVA applies '{action}' to this location.",
    }


def classify_all(supported_actions=None) -> dict:
    """Every known smell type classified — for diagnostics and tests."""
    return {
        smell_type: classify(smell_type, supported_actions)
        for smell_type in REFACTORING_MAP
    }


def known_smell_types() -> list:
    """Smell types REFACTORING_MAP can name a refactoring for."""
    return sorted(REFACTORING_MAP)
