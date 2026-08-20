"""
Approved plan -> SCTVA request
==============================
R26-SE-008 | Bandara S M Y M | IT22277886

The RDP plan speaks in refactoring names ("Extract Method"); the SCTVA agent
executes a fixed action vocabulary ("extract_method", ...). This module owns
that translation, and the normalization of what SCTVA sends back.

Ported from the DIWO frontend's services/sctvaApi.js, which is where this
mapping used to live because the browser called SCTVA directly. It mirrors
sctva/integration/planner_adapter.py::_map_step, including its rule that an
unmappable step becomes a `noop` rather than disappearing — the step count the
developer approved stays visible in the transformation log.

Fidelity note: JavaScript drops `undefined` object properties when it
serializes, but keeps explicit `null`. `UNSET` reproduces that distinction here
so the JSON body posted to SCTVA is byte-for-byte what the browser used to
send. Dropping the difference would silently add `"change_type": null` fields
the agent never saw before.
"""

from datetime import datetime, timezone
from typing import Optional


class _Unset:
    """Type of the UNSET sentinel below. One instance, never constructed again."""

    __slots__ = ()

    def __repr__(self):  # pragma: no cover - debugging aid
        return "UNSET"


#: Marker for a value JavaScript would have left `undefined`. Every test for it
#: is `is UNSET` / `is not UNSET`, never truthiness, so the sentinel needs no
#: __bool__ of its own.
UNSET = _Unset()


class StepMappingError(ValueError):
    """The step names a supported refactoring but lacks its parameters."""


#: Matches sctva/constants.py::SUPPORTED_LANGUAGES.
SUPPORTED_LANGUAGES = {"python", "java", "c"}

LANGUAGE_ALIASES = {
    "py": "python", "python": "python", "python3": "python",
    "java": "java",
    "c": "c", "h": "c", "c/c++": "c",
}

#: Matches the default execution options of the SCTVA contract.
DEFAULT_EXECUTION_OPTIONS = {
    "strict_mode": True,
    "enable_behavior_tests": True,
    "timeout_seconds": 10,
    # javac/gcc are not assumed to be on PATH; syntax validation still runs.
    "require_compilation": False,
    "rollback_on_behavior_failure": True,
    # MUST stay False for the DIWO workflow. SCTVA defaults this to True, and
    # whenever `source_files` is present its LocalRefactorDetector appends
    # refactorings the plan never asked for (agent.py::_local_actions_for_file).
    # In a reviewed workflow that is a correctness bug, not a bonus: a step the
    # developer explicitly REJECTED comes back through the side door. The
    # approved plan is the contract; nothing else runs.
    "enable_sctva_auto_refactoring": False,
}

RENAME_ALIASES = {
    "rename function", "rename method", "rename variable",
    "rename class", "rename parameter", "rename field", "rename attribute",
}

#: Refactorings SCTVA refuses to fake with a rename — see planner_adapter.py.
UNSUPPORTED_REFACTORINGS = {
    "extract class": "requires coordinated multi-file class creation",
    "move method": "requires coordinated edits to source and destination classes",
    "replace conditional with polymorphism": "requires new strategy/subclass definitions",
    "introduce parameter object": "requires a new parameter type and call-site updates",
    "hide delegate": "requires semantic multi-location edits",
    "replace data value with object": "requires semantic multi-location edits",
    "inline class": "requires semantic multi-location edits",
    "collapse hierarchy": "requires semantic multi-location edits",
    "pull up method": "requires semantic multi-location edits",
    "replace parameter with method call": "requires semantic multi-location edits",
}

#: Keys any agent in this project has used to name the file a step touches.
FILE_KEYS = (
    "source_file", "sourceFile", "target_file", "targetFile", "file",
    "file_name", "fileName", "file_path", "filePath", "relative_path", "relativePath",
)
LINE_KEYS = ("source_line", "sourceLine", "line", "start_line", "startLine")
LINE_LIST_KEYS = ("source_lines", "sourceLines", "lines")
START_KEYS = ("start_line", "startLine", "source_line", "sourceLine", "line")
END_KEYS = ("end_line", "endLine", "target_line", "targetLine")


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_path(value) -> str:
    return str(value or "").replace("\\", "/").strip()


def normalize_language(value) -> str:
    key = str(value or "").lower().strip()
    if key in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[key]
    return key if key in SUPPORTED_LANGUAGES else ""


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _get(source, key):
    """dict lookup that returns UNSET for a missing key, like JS `obj.key`."""
    if not isinstance(source, dict):
        return UNSET
    return source.get(key, UNSET)


def _or(*values):
    """Mirror JS `a || b || c`: the first truthy value, else the last one."""
    result = UNSET
    for value in values:
        result = value
        if value is not UNSET and value:
            return value
    return result


def _strip_unset(mapping: dict) -> dict:
    """Drop the keys JavaScript would not have serialized."""
    return {k: v for k, v in mapping.items() if v is not UNSET}


def pick_file(*sources) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in FILE_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def pick_line(*sources):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in LINE_KEYS:
            value = _as_int(source.get(key))
            if value is not None:
                return value
        for key in LINE_LIST_KEYS:
            values = source.get(key)
            if not isinstance(values, list) or not values:
                continue
            first = _as_int(values[0])
            if first is not None:
                return first
    return None


def pick_range(*sources):
    for source in sources:
        if not isinstance(source, dict):
            continue

        start = next((v for v in (_as_int(source.get(k)) for k in START_KEYS) if v is not None), None)
        end = next((v for v in (_as_int(source.get(k)) for k in END_KEYS) if v is not None), None)
        if start is not None and end is not None:
            return [min(start, end), max(start, end)]

        for key in (*LINE_LIST_KEYS, "line_range", "lineRange"):
            values = source.get(key)
            if isinstance(values, list) and values:
                parsed = [v for v in (_as_int(x) for x in values) if v is not None]
                if len(parsed) >= 2:
                    return [min(parsed), max(parsed)]
                if len(parsed) == 1:
                    return [parsed[0], parsed[0]]
            if isinstance(values, dict):
                nested_start = _as_int(values.get("start", values.get("from")))
                nested_end = _as_int(values.get("end", values.get("to")))
                if nested_start is not None and nested_end is not None:
                    return [min(nested_start, nested_end), max(nested_start, nested_end)]
    return [None, None]


def safe_identifier(name) -> str:
    """Mirrors PlannerAdapter._safe_identifier."""
    cleaned = "".join(c if (c.isascii() and (c.isalnum() or c == "_")) else "_"
                      for c in str(name or "").strip())
    if not cleaned:
        return "RenamedSymbol"
    return f"R_{cleaned}" if cleaned[0].isdigit() else cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Plan -> SCTVA actions
# ─────────────────────────────────────────────────────────────────────────────

def map_step(step: dict):
    """Map one RDP step onto an SCTVA action.

    Raises StepMappingError when the step names a refactoring SCTVA supports
    but is missing the parameters it needs; returns None when the refactoring
    has no safe SCTVA equivalent at all. Both outcomes become a noop upstream.
    """
    refactoring = str((step or {}).get("refactoring") or "").strip()
    if not refactoring:
        raise StepMappingError("missing 'refactoring' in step")

    key = refactoring.lower()
    raw_params, raw_target, raw_location = (
        step.get("parameters"), step.get("target"), step.get("location"))
    params: dict = raw_params if isinstance(raw_params, dict) else {}
    target: dict = raw_target if isinstance(raw_target, dict) else {}
    location: dict = raw_location if isinstance(raw_location, dict) else {}

    action = None

    if key.startswith("fault injection"):
        original_logic = _or(_get(params, "original_logic"), _get(params, "old_logic"))
        faulty_logic = params["faulty_logic"] if "faulty_logic" in params else _get(params, "new_logic")
        if (original_logic is UNSET or not original_logic
                or faulty_logic is UNSET or faulty_logic is None):
            raise StepMappingError(
                "fault injection mapping requires original_logic and faulty_logic")
        action = {
            "action_type": "fault_injection",
            "parameters": _strip_unset({
                "original_logic": str(original_logic),
                "faulty_logic": str(faulty_logic),
                "change_type": _get(params, "change_type"),
                "purpose": _get(params, "purpose"),
                "target_class": _get(target, "class"),
                "target_method": _get(target, "method"),
            }),
        }

    elif key in RENAME_ALIASES:
        old_name = _or(_get(params, "old_name"), _get(params, "method"),
                       _get(target, "method"), _get(target, "class"))
        new_name = _or(_get(params, "new_name"), _get(params, "renamed_to"))
        if old_name is UNSET or not old_name or new_name is UNSET or not new_name:
            raise StepMappingError("rename step requires old/new names")
        action = {
            "action_type": "rename_symbol",
            "parameters": {"old_name": str(old_name), "new_name": safe_identifier(new_name)},
        }

    elif key == "extract method":
        method = _or(_get(target, "method"), _get(params, "method"))
        if method is UNSET or not method:
            raise StepMappingError(
                "extract method mapping requires target.method or parameters.method")
        start_line, end_line = pick_range(params, target, location, step)
        if start_line is None or end_line is None:
            raise StepMappingError(
                "extract method mapping requires an executable source range "
                "(start_line/end_line or source_lines/lines)")
        new_name = _or(_get(params, "new_method_name"),
                       _get(params, "extracted_method_name"), f"{method}Core")
        action = {
            "action_type": "extract_method",
            "parameters": _strip_unset({
                "method": str(method),
                "new_method_name": safe_identifier(new_name),
                "start_line": start_line,
                "end_line": end_line,
                "target_class": _or(_get(target, "class"), _get(params, "source_class")),
            }),
        }

    elif key in UNSUPPORTED_REFACTORINGS:
        raise StepMappingError(
            f"{refactoring} {UNSUPPORTED_REFACTORINGS[key]}; "
            "SCTVA will not simulate it with a rename")

    elif key in ("extract constant", "replace magic number with symbolic constant"):
        if "literal_value" not in params:
            raise StepMappingError("extract_constant mapping requires parameters.literal_value")
        action = {
            "action_type": "extract_constant",
            "parameters": {
                "literal_value": params["literal_value"],
                "constant_name": _or(_get(params, "constant_name"), "EXTRACTED_CONSTANT"),
            },
        }

    elif key == "introduce constant":
        literal_value = params["literal_value"] if "literal_value" in params else None
        literal_values = params.get("literal_values") if isinstance(
            params.get("literal_values"), list) else None
        hint = _get(params, "hint")
        if literal_value is None and literal_values is None and (hint is UNSET or not hint):
            raise StepMappingError(
                "introduce constant mapping requires literal_value, literal_values, or hint")
        action = {
            "action_type": "introduce_constant",
            "parameters": _strip_unset({
                "literal_value": literal_value,
                "literal_values": literal_values,
                "constant_name": _or(_get(params, "constant_name"), "EXTRACTED_CONSTANT"),
                "hint": hint,
                "source_file": _get(params, "source_file"),
                "source_line": _get(params, "source_line"),
                "target_class": _or(_get(target, "class"), _get(params, "source_class")),
                "target_method": _or(_get(target, "method"), _get(params, "method")),
            }),
        }

    elif key == "remove dead code":
        method = _or(_get(params, "method"), _get(target, "method"))
        source_line = pick_line(params, target, location, step)
        if (method is UNSET or not method) and source_line is None:
            raise StepMappingError(
                "remove dead code mapping requires parameters.method, target.method, "
                "or a source line")
        action = {
            "action_type": "remove_dead_code",
            "parameters": _strip_unset({
                "method": str(method) if (method is not UNSET and method) else "",
                "class_name": _or(_get(target, "class"), _get(params, "source_class")),
                "source_line": source_line,
            }),
        }

    elif key == "replace unsafe function":
        unsafe_function = _or(_get(params, "unsafe_function"), _get(target, "method"))
        safe_alternative = _get(params, "safe_alternative")
        if (unsafe_function is UNSET or not unsafe_function
                or safe_alternative is UNSET or not safe_alternative):
            raise StepMappingError(
                "replace unsafe function mapping requires unsafe_function and safe_alternative")
        action = {
            "action_type": "replace_unsafe_function",
            "parameters": {
                "unsafe_function": str(unsafe_function),
                "safe_alternative": str(safe_alternative),
                "source_line": pick_line(params, target, location, step),
            },
        }

    elif key == "encapsulate variable":
        variable_name = _or(_get(params, "variable_name"), _get(target, "variable"))
        if variable_name is UNSET or not variable_name:
            raise StepMappingError(
                "encapsulate variable mapping requires parameters.variable_name")
        action = {
            "action_type": "encapsulate_variable",
            "parameters": {
                "variable_name": str(variable_name),
                "getter_name": safe_identifier(
                    _or(_get(params, "getter_name"), f"get_{variable_name}")),
                "setter_name": safe_identifier(
                    _or(_get(params, "setter_name"), f"set_{variable_name}")),
            },
        }

    elif key in ("replace literal", "replace temp with query"):
        if "old_literal" not in params or "new_literal" not in params:
            raise StepMappingError("replace_literal mapping requires old_literal/new_literal")
        action = {
            "action_type": "replace_literal",
            "parameters": {
                "old_literal": params["old_literal"],
                "new_literal": params["new_literal"],
            },
        }

    if action:
        source_file = pick_file(params, target, location, step)
        if source_file and not action["parameters"].get("source_file"):
            action["parameters"]["source_file"] = source_file
        source_line = pick_line(params, target, location, step)
        # `not in` and not `is None`: a mapping that deliberately set
        # source_line to null keeps the null, exactly as the browser did.
        if source_line is not None and "source_line" not in action["parameters"]:
            action["parameters"]["source_line"] = source_line
        action["source_step_id"] = step.get("step_id")
        action["source_refactoring"] = refactoring
        action["warnings"] = []

    return action


def noop_action(step, reason: str, message: str) -> dict:
    return {
        "action_type": "noop",
        "parameters": {
            "reason": reason,
            "refactoring": (step or {}).get("refactoring"),
            "step_id": (step or {}).get("step_id"),
        },
        "source_step_id": (step or {}).get("step_id"),
        "source_refactoring": (step or {}).get("refactoring"),
        "warnings": [message],
    }


def normalize_plan_for_sctva(plan: dict, correlation_id=None) -> dict:
    """Normalize an approved RDP plan into the `refactoring_plan` SCTVA expects.

    Every approved step produces exactly one action so the transformation log
    lines up with the plan the developer signed off on; steps that could not be
    mapped come back as noops carrying the reason.
    """
    if not isinstance(plan, dict):
        raise ValueError("No approved refactoring plan is available to transform.")

    plan_id = str(plan.get("plan_id") or "").strip() or f"diwo_plan_{_now_ms()}"
    raw_steps = plan.get("steps")
    steps: list = raw_steps if isinstance(raw_steps, list) else []

    actions = []
    warnings = []
    unmapped_steps = []

    for idx, step in enumerate(steps):
        position = idx + 1

        if not isinstance(step, dict):
            message = f"Step {position} is malformed and was mapped to noop."
            warnings.append(message)
            unmapped_steps.append(position)
            actions.append(noop_action(None, "malformed_step", message))
            continue

        try:
            mapped = map_step(step)
        except StepMappingError as exc:
            message = f"Step {position} ({step.get('refactoring') or 'unknown'}): {exc}"
            warnings.append(message)
            unmapped_steps.append(position)
            actions.append(noop_action(step, "unmappable_step", message))
            continue

        if mapped:
            actions.append(mapped)
        else:
            message = (f"Step {position} unsupported refactoring "
                       f"'{step.get('refactoring') or 'unknown'}', mapped to noop.")
            warnings.append(message)
            unmapped_steps.append(position)
            actions.append(noop_action(step, "unsupported_refactoring", message))

    if not actions:
        message = "The approved plan produced zero executable actions; using a noop action."
        warnings.append(message)
        actions.append(noop_action(None, "empty_or_non_actionable_plan", message))

    # A plan-level target still scopes actions that named no file of their own.
    raw_target = plan.get("target")
    plan_target = raw_target.strip() if isinstance(raw_target, str) else pick_file(raw_target)
    if plan_target:
        for action in actions:
            if not action["parameters"].get("source_file"):
                action["parameters"]["source_file"] = plan_target

    raw_tests = plan.get("behavior_tests")
    raw_behavior_tests: list = raw_tests if isinstance(raw_tests, list) else []

    return {
        "plan": {
            "plan_id": plan_id,
            "actions": actions,
            "behavior_tests": raw_behavior_tests,
            "metadata": {
                "source_agent": "rdp_agent",
                "source_plan_id": plan_id,
                "correlation_id": correlation_id,
                "adapter_warnings": warnings,
                "malformed_steps": unmapped_steps,
                "planner_metadata": plan.get("metadata") or {},
                # Was "diwo_frontend" while the browser owned this mapping.
                "mapped_by": "diwo_orchestrator",
            },
        },
        "warnings": warnings,
        "executableCount": sum(1 for a in actions if a["action_type"] != "noop"),
        "noopCount": sum(1 for a in actions if a["action_type"] == "noop"),
    }


def collect_plan_source_paths(plan: dict) -> list:
    """Every distinct file the approved steps touch, plan target included."""
    paths = []
    seen = set()

    def add(value):
        normalized = normalize_path(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)

    for step in (plan or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        found = pick_file(step.get("parameters"), step.get("target"),
                          step.get("location"), step)
        if found:
            add(found)

    raw_target = (plan or {}).get("target")
    plan_target = raw_target if isinstance(raw_target, str) else pick_file(raw_target)
    if plan_target:
        add(plan_target)

    return paths


# ─────────────────────────────────────────────────────────────────────────────
# SCTVA response normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_execute_result(raw: dict, source_files: Optional[list] = None) -> dict:
    """Flatten a single-file or multi-file execute response into one shape.

    SCTVA answers a one-file request with the result fields at the top level
    and a many-file request with a `file_results` array; both become `files[]`
    here, each entry carrying the before/after text.

    Diff rows are deliberately NOT built here: rendering a diff is the
    browser's job, and its line-alignment already runs there for the Results
    stage. The frontend adds `diff_rows` from `before`/`after`.
    """
    raw = raw or {}
    before_by_path = {
        normalize_path(f.get("file_name")): f.get("source_code") or ""
        for f in (source_files or []) if isinstance(f, dict)
    }

    file_results = raw.get("file_results")
    per_file = file_results if isinstance(file_results, list) and file_results else [raw]

    files = []
    for idx, entry in enumerate(per_file):
        entry = entry if isinstance(entry, dict) else {}
        path = normalize_path(entry.get("file_name")) or f"source_{idx + 1}"
        before = before_by_path.get(path, "")
        after = entry.get("refactored_code") if isinstance(entry.get("refactored_code"), str) else ""

        files.append({
            # `path` / `after` are the field names the Results stage already
            # reads, so a file entry works unchanged in both stages.
            "path": path,
            "file": path,
            "before": before,
            "after": after,
            "refactored_code": after,
            "changed": bool(after) and after != before,
            "language": entry.get("language") or raw.get("language") or "",
            "success": bool(entry.get("success")),
            "rollback_occurred": bool(entry.get("rollback_occurred")),
            "transformation_applied": bool(entry.get("transformation_applied")),
            "total_replacements": entry.get("total_replacements") or 0,
            "confidence_score": entry.get("confidence_score"),
            "confidence_components": entry.get("confidence_components") or None,
            "validation": entry.get("validation") or None,
            "safety_report": entry.get("safety_report") or None,
            "source_mode": entry.get("source_mode") or "raw",
        })

    # Prefer a file that actually changed: it is what the developer came to see.
    primary = next((f for f in files if f["changed"]), files[0] if files else None)

    return {
        "raw": raw,
        "requestId": raw.get("request_id") or "",
        "language": raw.get("language") or (primary or {}).get("language") or "",
        "success": bool(raw.get("success")),
        "rollbackOccurred": bool(raw.get("rollback_occurred")),
        "transformationApplied": bool(raw.get("transformation_applied")),
        "confidenceScore": raw.get("confidence_score"),
        "confidenceApplicable": raw.get("confidence_applicable") is not False,
        "validationScore": raw.get("validation_score"),
        "totalReplacements": raw.get("total_replacements") or 0,
        "fileSummary": raw.get("file_summary") or {
            "total": len(files),
            "succeeded": sum(1 for f in files if f["success"]),
            "applied": sum(1 for f in files if f["transformation_applied"]),
            "rolled_back": sum(1 for f in files if f["rollback_occurred"]),
            "not_applied": sum(1 for f in files
                               if not f["transformation_applied"] and not f["rollback_occurred"]),
        },
        # Multi-file responses keep validation/safety on each file result only.
        "validation": raw.get("validation") or (primary or {}).get("validation") or None,
        "confidenceComponents": raw.get("confidence_components")
                                or (primary or {}).get("confidence_components") or None,
        "safetyReport": raw.get("safety_report") or (primary or {}).get("safety_report") or None,
        "files": files,
        # Kept flat as well: this is the contract the DIWO workflow state expects.
        "refactored_code": (primary or {}).get("after") or "",
    }


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
