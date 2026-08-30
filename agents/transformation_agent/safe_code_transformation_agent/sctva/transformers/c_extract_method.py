"""Semantic C Long Function -> Extract Function transformation."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .c_extract_class import CFunction, _parse_c_module
from .extract_method_common import (
    MAX_EXTRACTED_PARAMETERS,
    MIN_EXTRACTED_LOC,
    StatementSpan,
    apply_edits,
    candidate_windows,
    control_complexity,
    direct_c_like_statements,
    has_unsafe_cross_boundary_flow,
    identifiers,
    mask_c_like,
    nonblank_loc,
    normalize_signature,
)


REVIEW_REQUIRED = "review_required"
ALREADY_APPLIED = "already_applied"
_C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else",
    "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long", "register",
    "restrict", "return", "short", "signed", "sizeof", "static", "struct", "switch", "typedef",
    "union", "unsigned", "void", "volatile", "while", "_Bool", "_Complex", "_Imaginary",
}
_C_STANDARD_SHARED_SYMBOLS = {
    "BUFSIZ", "EOF", "EXIT_FAILURE", "EXIT_SUCCESS", "FILENAME_MAX",
    "L_tmpnam", "NULL", "SEEK_CUR", "SEEK_END", "SEEK_SET", "stderr",
    "stdin", "stdout", "TMP_MAX",
}
MAX_EXTRACTED_HELPER_LOC = 40
MAX_CANDIDATE_VALIDATION_ATTEMPTS = 64


@dataclass
class CFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    declarations: dict[str, str]
    defined_before: set[str]
    pointer_like: set[str]
    scope_validation: dict[str, list[str]]


def target_match_count(
    source_code: str,
    *,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
) -> int:
    del source_class
    return len(_resolve_targets(source_code, method_name, method_signature))


def apply_extract_method(
    source_code: str,
    *,
    new_method_name: str,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    source_file: str = "",
    current_file_name: str = "",
    source_resolution_error: str = "",
) -> tuple[str, int, dict[str, Any]]:
    metadata = _base_metadata(method_name, new_method_name, source_class, source_file or current_file_name)
    if source_resolution_error:
        return _review(source_code, source_resolution_error, metadata)
    if not _identifier(method_name):
        return _review(source_code, "INVALID_FUNCTION_TARGET_OR_NAME", metadata)

    targets = _resolve_targets(source_code, method_name, method_signature)
    if not targets:
        return _review(source_code, "FUNCTION_TARGET_NOT_FOUND", metadata)
    if len(targets) != 1:
        return _review(source_code, "AMBIGUOUS_FUNCTION_TARGET", metadata)
    function = targets[0]
    model = _parse_c_module(source_code)

    # Automatically derive or disambiguate helper name if missing or conflicting
    if not _identifier(new_method_name):
        new_method_name = f"extracted_{method_name}"

    helper_collisions = model.functions_by_name.get(new_method_name, [])
    if helper_collisions:
        if re.search(rf"\b{re.escape(new_method_name)}\s*\(", function.body):
            metadata.update({"status": ALREADY_APPLIED, "reason": "ALREADY_APPLIED", "plan_compliance": "PASS"})
            return source_code, 0, metadata
        # Try appending suffix if name collision exists
        counter = 1
        alt_name = f"{new_method_name}_{counter}"
        while model.functions_by_name.get(alt_name):
            counter += 1
            alt_name = f"{new_method_name}_{counter}"
        new_method_name = alt_name
        metadata["extracted_method"] = new_method_name

    statements = direct_c_like_statements(function.body, body_offset=function.open_brace + 1)
    if len(statements) < 2:
        return _review(source_code, "FUNCTION_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK", metadata)

    parameter_declarations = _c_parameter_declarations(function.params_raw)
    before_metrics = _function_metrics(source_code, function)
    candidates, candidate_reason = _select_candidates(
        source_code,
        function,
        statements,
        parameter_declarations,
        start_line=start_line,
        end_line=end_line,
    )
    if not candidates:
        return _review(source_code, candidate_reason, {**metadata, "before_metrics": before_metrics})
    candidate_attempts: list[dict[str, Any]] = []
    last_reason = candidate_reason or "NO_SAFE_COHESIVE_BLOCK"
    for selected, flow in candidates:
        transformed = _rewrite(
            source_code,
            function=function,
            selected=selected,
            flow=flow,
            new_method_name=new_method_name,
        )
        transformed_targets = _resolve_targets(transformed, method_name, method_signature)
        transformed_model = _parse_c_module(transformed)
        if len(transformed_targets) != 1:
            last_reason = "POST_TRANSFORM_TARGET_VALIDATION_FAILED"
            candidate_attempts.append(_candidate_attempt(selected, last_reason))
            continue

        after_function = transformed_targets[0]
        after_metrics = _function_metrics(transformed, after_function)
        helper_matches = transformed_model.functions_by_name.get(new_method_name, [])
        scope_validation = _validate_transformed_scope(
            transformed,
            source_method=method_name,
            helper_name=new_method_name,
            flow=flow,
        )
        helper_loc = (
            _line_of(transformed, helper_matches[0].end - 1)
            - _line_of(transformed, helper_matches[0].start)
            + 1
            if helper_matches
            else 0
        )
        structural_passed = len(helper_matches) == 1 and re.search(
            rf"\b{re.escape(new_method_name)}\s*\(", after_function.body
        ) is not None
        reduction_passed = _meaningfully_reduced(
            before_metrics,
            after_metrics,
            selected,
            threshold=MAX_EXTRACTED_HELPER_LOC,
        )
        helper_smell_free = helper_loc <= MAX_EXTRACTED_HELPER_LOC
        if not _scope_validation_passed(scope_validation):
            last_reason = "UNSAFE_C_EXTRACT_METHOD_DATA_FLOW"
        elif not structural_passed:
            last_reason = "EXTRACT_FUNCTION_STRUCTURE_NOT_PROVEN"
        elif not helper_smell_free:
            last_reason = "HELPER_LONG_FUNCTION_SMELL"
        elif not reduction_passed:
            last_reason = "LONG_FUNCTION_NOT_REDUCED"
        else:
            compile_status, compile_msg = _verify_c_compilation(transformed)
            if compile_status != "FAIL":
                break
            last_reason = f"COMPILATION_FAILED: {compile_msg}"

        candidate_attempts.append(
            _candidate_attempt(
                selected,
                last_reason,
                after_metrics=after_metrics,
                helper_loc=helper_loc,
                scope_validation=scope_validation,
            )
        )
    else:
        return _review(
            source_code,
            last_reason,
            {
                **metadata,
                "before_metrics": before_metrics,
                "candidate_attempts": candidate_attempts,
            },
        )

    metadata.update({
        "status": "success",
        "reason": "extract_function_applied",
        "plan_compliance": "PASS",
        "source_range_hint": {"start_line": start_line, "end_line": end_line},
        "resolved_source_range": {
            "start_line": _line_of(source_code, selected[0].start),
            "end_line": _line_of(source_code, selected[-1].end - 1),
        },
        "inputs": flow.inputs,
        "outputs": flow.outputs,
        "locals": flow.locals,
        "before_loc": before_metrics["loc"],
        "after_loc": after_metrics["loc"],
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "compiler_validation": compile_msg,
        "scope_validation": scope_validation,
        "candidate_attempts": candidate_attempts,
        "validation": {
            "target_resolution": "PASS",
            "data_flow": "PASS" if _scope_validation_passed(scope_validation) else "FAIL",
            "scope": "PASS" if _scope_validation_passed(scope_validation) else "FAIL",
            "structural": "PASS",
            "no_severe_new_smell": "PASS" if helper_smell_free else "FAIL",
            "long_method_reduction": "PASS" if reduction_passed else "FAIL",
            "smell_reduction": "PASS" if reduction_passed else "FAIL",
            "compilation": compile_status,
        },
        "behavioral_safety": "PASSED_COMPILER_AND_STRUCTURAL_VALIDATION" if compile_status == "PASS" else "STRUCTURAL_VALIDATION_ONLY",
    })
    return transformed, 1, metadata


def _resolve_targets(source_code: str, method_name: str, method_signature: str) -> list[CFunction]:
    model = _parse_c_module(source_code)
    return [
        function
        for function in model.functions_by_name.get(method_name, [])
        if _signature_matches(function, method_signature)
    ]


def _signature_matches(function: CFunction, signature: str) -> bool:
    normalized = normalize_signature(signature)
    if not normalized:
        return True
    rendered = f"{function.name}({function.params_raw})"
    return normalized in {normalize_signature(rendered), normalize_signature(function.header)}


def _select_candidates(
    source_code: str,
    function: CFunction,
    statements: Sequence[StatementSpan],
    parameter_declarations: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
    target_loc_threshold: int = MAX_EXTRACTED_HELPER_LOC,
) -> tuple[list[tuple[list[StatementSpan], CFlow]], str]:
    """Return safe extraction alternatives, best first.

    A C function often contains one long loop or conditional.  Treating that
    compound statement as indivisible pushes the entire block into one helper,
    which simply recreates the Long Function smell.  We also inspect complete
    direct statements in nested control blocks, while retaining the enclosing
    function as the data-flow boundary.
    """

    before_loc = _line_of(source_code, function.end - 1) - _line_of(source_code, function.start) + 1
    windows = _candidate_windows_for_function(
        source_code,
        function,
        statements,
        start_line=start_line,
        end_line=end_line,
    )
    module = _parse_c_module(source_code)
    shared_symbols = _c_shared_symbols(source_code, module=module)
    scored: list[tuple[float, list[StatementSpan], CFlow]] = []
    saw_unsafe_data_flow = False
    for window in windows:
        text = source_code[window[0].start:window[-1].end]
        if has_unsafe_cross_boundary_flow(text, language="c"):
            continue
        if re.search(r"\b(?:typedef|struct|union|enum)\b[^;{]*\{", mask_c_like(text)):
            continue
        if window[0].start <= function.open_brace + 1 and window[-1].end >= function.end - 1:
            continue
        flow = _c_flow_for_selection(
            source_code,
            function,
            window,
            parameter_declarations,
            shared_symbols=shared_symbols,
        )
        if flow is None:
            saw_unsafe_data_flow = True
            continue
        if not _scope_validation_passed(flow.scope_validation):
            saw_unsafe_data_flow = True
            continue
        if len(flow.inputs) + len(flow.outputs) > MAX_EXTRACTED_PARAMETERS:
            continue
        loc = nonblank_loc(text)
        complexity = control_complexity(text)
        if not (start_line and end_line) and loc < MIN_EXTRACTED_LOC and complexity <= 1:
            continue

        estimated_after_loc = before_loc - loc + 1
        estimated_helper_loc = loc + 3
        # Never prefer a candidate that is predicted to create another Long
        # Function.  Actual helper metrics are checked again after rewriting.
        if estimated_helper_loc > target_loc_threshold:
            continue

        reduction_bonus = 0.0
        if before_loc > target_loc_threshold:
            if estimated_after_loc <= target_loc_threshold:
                reduction_bonus = 1000.0
            elif estimated_after_loc < before_loc:
                # A sound extraction can still be valuable when a very large
                # function needs several successive, bounded extractions.
                reduction_bonus = min(loc * 3.0, 120.0)

        hint_bonus = 0.0
        if start_line and end_line:
            w_start = _line_of(source_code, window[0].start)
            w_end = _line_of(source_code, max(window[-1].start, window[-1].end - 2))
            if w_start >= start_line and w_end <= end_line:
                hint_bonus = 2000.0
            elif w_start <= end_line and w_end >= start_line:
                hint_bonus = 100.0

        cohesion = len(set(flow.inputs) & identifiers(text)) + len(flow.outputs)
        score = (
            reduction_bonus
            + hint_bonus
            + complexity * 5.0
            + loc * 3.0
            + cohesion
            - (len(flow.inputs) + len(flow.outputs)) * 0.5
        )
        scored.append((score, list(window), flow))
    if not scored:
        return [], "UNSAFE_C_EXTRACT_METHOD_DATA_FLOW" if saw_unsafe_data_flow else "NO_SAFE_COHESIVE_BLOCK"
    scored.sort(key=lambda item: (item[0], nonblank_loc("".join(part.text for part in item[1]))), reverse=True)
    # Candidate generation is intentionally broad, but transformation and
    # compiler validation are comparatively expensive. The best ranked safe
    # alternatives provide useful fallback without unbounded work on a large
    # repository.
    return [
        (window, flow)
        for _, window, flow in scored[:MAX_CANDIDATE_VALIDATION_ATTEMPTS]
    ], ""


def _select_candidate(
    source_code: str,
    function: CFunction,
    statements: Sequence[StatementSpan],
    parameter_declarations: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
    target_loc_threshold: int = MAX_EXTRACTED_HELPER_LOC,
) -> tuple[tuple[list[StatementSpan], CFlow] | None, str]:
    """Compatibility wrapper for callers that need only the best candidate."""

    candidates, reason = _select_candidates(
        source_code,
        function,
        statements,
        parameter_declarations,
        start_line=start_line,
        end_line=end_line,
        target_loc_threshold=target_loc_threshold,
    )
    return (candidates[0], "") if candidates else (None, reason)


def _candidate_windows_for_function(
    source_code: str,
    function: CFunction,
    statements: Sequence[StatementSpan],
    *,
    start_line: int | None,
    end_line: int | None,
) -> list[list[StatementSpan]]:
    """Collect direct windows at the function and safe nested-block levels."""

    groups: list[list[StatementSpan]] = [list(statements)]
    seen_blocks: set[tuple[int, int]] = set()

    def visit(block_start: int, block_end: int) -> None:
        key = (block_start, block_end)
        if key in seen_blocks or block_end <= block_start:
            return
        seen_blocks.add(key)
        nested = direct_c_like_statements(
            source_code[block_start:block_end],
            body_offset=block_start,
        )
        if len(nested) >= 2:
            groups.append(nested)
        for statement in nested:
            for child_start, child_end in _control_block_bodies(source_code, statement):
                visit(child_start, child_end)

    for statement in statements:
        for block_start, block_end in _control_block_bodies(source_code, statement):
            visit(block_start, block_end)

    windows: list[list[StatementSpan]] = []
    seen_windows: set[tuple[int, int]] = set()
    for group in groups:
        for window in candidate_windows(
            group,
            start_line=start_line,
            end_line=end_line,
            source=source_code,
        ):
            key = (window[0].start, window[-1].end)
            if key not in seen_windows:
                seen_windows.add(key)
                windows.append(window)
    return windows


def _control_block_bodies(source_code: str, statement: StatementSpan) -> list[tuple[int, int]]:
    """Return bodies belonging to C control statements, never initializers."""

    text = source_code[statement.start:statement.end]
    masked = mask_c_like(text)
    bodies: list[tuple[int, int]] = []
    for match in re.finditer(r"\{", masked):
        prefix = masked[:match.start()]
        # The last control token before this brace must be uninterrupted by a
        # statement terminator. This excludes C aggregate initializers.
        tail = prefix[max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}")) + 1:]
        if not re.search(r"\b(?:if|else|for|while|switch|do)\b", tail):
            continue
        close = _find_matching_c_delimiter(masked, match.start(), "{", "}")
        if close is not None and close > match.start() + 1:
            bodies.append((statement.start + match.start() + 1, statement.start + close))
    return bodies


def _c_flow_for_selection(
    source_code: str,
    function: CFunction,
    selected: Sequence[StatementSpan],
    parameter_declarations: dict[str, str],
    *,
    shared_symbols: set[str] | None = None,
) -> CFlow | None:
    """Compute data flow against the full function, including nested scopes."""

    selection_start, selection_end = selected[0].start, selected[-1].end
    body_start, body_end = function.open_brace + 1, function.end - 1
    before_text = source_code[body_start:selection_start]
    selected_text = source_code[selection_start:selection_end]
    after_text = source_code[selection_end:body_end]
    before_declarations = _c_local_declarations(before_text)
    selected_declarations = _c_local_declarations(selected_text)
    if shared_symbols is None:
        module = _parse_c_module(source_code)
        shared_symbols = _c_shared_symbols(source_code, module=module)
    declarations = {**parameter_declarations, **before_declarations, **selected_declarations}
    defined_before = set(parameter_declarations) | set(before_declarations)
    pointer_like = {
        name
        for name, declaration in declarations.items()
        if _is_pointer_like_declaration(declaration)
    }
    reads = _c_reads(selected_text)
    direct_writes = _c_writes(selected_text) - (_c_member_writes(selected_text) & pointer_like) - shared_symbols
    address_writes = _c_address_output_writes(selected_text, declarations)
    writes = direct_writes | address_writes | set(selected_declarations)
    reads_after = _c_reads(after_text)
    # A nested extraction can execute repeatedly inside an enclosing loop.
    # A value written in the helper may therefore be required by the next loop
    # iteration even when it is not textually read after the selected range.
    nested_selection = _brace_depth_at(mask_c_like(source_code), selection_start) > 1
    required_after = reads_after | (defined_before if nested_selection else set())
    outputs = sorted(writes & required_after)
    helper_locals = {
        name
        for name in before_declarations
        if name not in outputs
        and _is_assigned_before_read(selected_text, name)
        and _can_redeclare_in_helper(before_declarations[name])
    }
    inputs = sorted((reads & defined_before) - set(outputs) - helper_locals)
    locals_only = sorted((set(selected_declarations) - set(outputs)) | helper_locals)
    missing_inputs = sorted(reads - defined_before - set(selected_declarations) - shared_symbols)
    selected_outputs = set(selected_declarations) & reads_after
    unsupported_selected_outputs = {
        name
        for name in selected_outputs
        if name in pointer_like or _is_multi_declaration(selected_text, name)
    }
    missing_outputs = sorted(
        unsupported_selected_outputs | {
            name for name in (writes & reads_after) if name not in outputs
        }
    )
    scope_validation = {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": missing_inputs,
        "missing_outputs": missing_outputs,
    }
    if any(name not in declarations for name in [*inputs, *outputs]):
        scope_validation["missing_inputs"] = sorted(set(scope_validation["missing_inputs"]) | {
            name for name in [*inputs, *outputs] if name not in declarations
        })
    return CFlow(
        inputs,
        outputs,
        locals_only,
        declarations,
        defined_before,
        pointer_like,
        scope_validation,
    )


def _c_flow(
    source_code: str,
    statements: Sequence[StatementSpan],
    start_index: int,
    end_index: int,
    parameter_declarations: dict[str, str],
) -> CFlow | None:
    before_text = "".join(item.text for item in statements[:start_index])
    selected_text = source_code[statements[start_index].start:statements[end_index - 1].end]
    after_text = "".join(item.text for item in statements[end_index:])
    before_declarations = _c_local_declarations(before_text)
    selected_declarations = _c_local_declarations(selected_text)
    module = _parse_c_module(source_code)
    shared_symbols = _c_shared_symbols(source_code, module=module)
    declarations = {**parameter_declarations, **before_declarations, **selected_declarations}
    defined_before = set(parameter_declarations) | set(before_declarations)
    pointer_like = {
        name
        for name, declaration in declarations.items()
        if _is_pointer_like_declaration(declaration)
    }
    reads = _c_reads(selected_text)
    direct_writes = _c_writes(selected_text) - (_c_member_writes(selected_text) & pointer_like) - shared_symbols
    address_writes = _c_address_output_writes(selected_text, declarations)
    writes = direct_writes | address_writes | set(selected_declarations)
    reads_after = _c_reads(after_text)
    outputs = sorted(writes & reads_after)
    helper_locals = {
        name
        for name in before_declarations
        if name not in outputs
        and _is_assigned_before_read(selected_text, name)
        and _can_redeclare_in_helper(before_declarations[name])
    }
    inputs = sorted((reads & defined_before) - set(outputs) - helper_locals)
    locals_only = sorted((set(selected_declarations) - set(outputs)) | helper_locals)
    missing_inputs = sorted(reads - defined_before - set(selected_declarations) - shared_symbols)
    selected_outputs = set(selected_declarations) & reads_after
    unsupported_selected_outputs = {
        name
        for name in selected_outputs
        if name in pointer_like or _is_multi_declaration(selected_text, name)
    }
    missing_outputs = sorted(
        unsupported_selected_outputs | {
            name for name in (writes & reads_after)
            if name not in outputs
        }
    )
    scope_validation = {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": missing_inputs,
        "missing_outputs": missing_outputs,
    }
    if any(name not in declarations for name in [*inputs, *outputs]):
        scope_validation["missing_inputs"] = sorted(set(scope_validation["missing_inputs"]) | {
            name for name in [*inputs, *outputs] if name not in declarations
        })
    return CFlow(
        inputs,
        outputs,
        locals_only,
        declarations,
        defined_before,
        pointer_like,
        scope_validation,
    )


def _c_parameter_declarations(params_raw: str) -> dict[str, str]:
    if not params_raw.strip() or params_raw.strip() == "void":
        return {}
    result: dict[str, str] = {}
    for raw in _split_top_level(params_raw):
        cleaned = raw.strip()
        function_pointer = re.search(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", cleaned)
        if function_pointer:
            name = function_pointer.group(1)
            result[name] = cleaned.replace(name, "{name}", 1)
            continue
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^]]*\])?\s*$", cleaned)
        if not match:
            continue
        name = match.group(1)
        result[name] = cleaned[:match.start(1)] + "{name}" + cleaned[match.end(1):]
    return result


def _split_top_level(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    parens = brackets = 0
    for char in value:
        if char == "(":
            parens += 1
        elif char == ")" and parens:
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets:
            brackets -= 1
        if char == "," and parens == brackets == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current).strip())
    return [item for item in items if item]


def _c_local_declarations(text: str) -> dict[str, str]:
    """Return simple local declaration templates, including comma declarators.

    This deliberately recognises only declarations that can be reproduced in a
    helper signature.  Ambiguous C declarators are left unresolved and cause
    the Extract Method candidate to be rejected by the data-flow gate.
    """

    masked = mask_c_like(text)
    declarations: dict[str, str] = {}
    for match in re.finditer(r"(?ms)(?P<statement>[^;{}]+;)", masked):
        statement = match.group("statement").strip()
        if not statement or statement.startswith(("return ", "if ", "for ", "while ", "switch ", "case ")):
            continue
        declarations.update(_parse_c_declaration_statement(statement))
    return declarations


def _parse_c_declaration_statement(statement: str) -> dict[str, str]:
    """Parse safe, non-function C declarations into reusable templates."""

    text = statement.strip().rstrip(";").strip()
    if not text or text.startswith(("typedef ", "extern ", "return ")):
        return {}
    declarators = _split_top_level(text)
    if not declarators:
        return {}

    first = declarators[0]
    left = _split_first_top_level(first, "=")[0].strip()
    # Parentheses in an initializer (for example ``sizeof(value)`` or a
    # function call) do not make this a function declaration. Only reject
    # parenthesized declarators such as function pointers.
    if "(" in left:
        return {}
    first_match = re.fullmatch(
        r"(?P<prefix>(?:(?:const|volatile|restrict|signed|unsigned|short|long|struct\s+[A-Za-z_]\w*|union\s+[A-Za-z_]\w*|enum\s+[A-Za-z_]\w*|[A-Za-z_]\w*)\s+)+)(?P<pointers>(?:\*\s*)*)(?P<name>[A-Za-z_]\w*)(?P<suffix>(?:\s*\[[^]]*\])*)",
        left,
    )
    if not first_match:
        return {}
    type_prefix = " ".join(first_match.group("prefix").split())
    if type_prefix.split()[0] in {"if", "for", "while", "switch", "case", "goto"}:
        return {}

    declarations: dict[str, str] = {}
    parsed_first = _declaration_template(
        type_prefix,
        first_match.group("pointers"),
        first_match.group("name"),
        first_match.group("suffix"),
    )
    if parsed_first is None:
        return {}
    name, template = parsed_first
    declarations[name] = template

    for raw in declarators[1:]:
        fragment = _split_first_top_level(raw, "=")[0].strip()
        if "(" in fragment:
            return {}
        match = re.fullmatch(
            r"(?P<pointers>(?:\*\s*)*)(?P<name>[A-Za-z_]\w*)(?P<suffix>(?:\s*\[[^]]*\])*)",
            fragment,
        )
        if not match:
            return {}
        parsed = _declaration_template(
            type_prefix,
            match.group("pointers"),
            match.group("name"),
            match.group("suffix"),
        )
        if parsed is None:
            return {}
        name, template = parsed
        declarations[name] = template
    return declarations


def _declaration_template(
    type_prefix: str,
    pointers: str,
    name: str,
    suffix: str,
) -> tuple[str, str] | None:
    if not _identifier(name):
        return None
    pointer_text = "".join(pointers.split())
    suffix_text = "".join(suffix.split())
    rendered_prefix = f"{type_prefix} {pointer_text}".rstrip()
    return name, f"{rendered_prefix} {{name}}{suffix_text}"


def _split_first_top_level(value: str, separator: str) -> tuple[str, str]:
    parens = brackets = braces = 0
    for index, char in enumerate(value):
        if char == "(":
            parens += 1
        elif char == ")" and parens:
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets:
            brackets -= 1
        elif char == "{":
            braces += 1
        elif char == "}" and braces:
            braces -= 1
        elif char == separator and parens == brackets == braces == 0:
            return value[:index], value[index + 1:]
    return value, ""


def _is_multi_declaration(text: str, name: str) -> bool:
    masked = mask_c_like(text)
    for match in re.finditer(r"(?ms)(?P<statement>[^;{}]+;)", masked):
        statement = match.group("statement")
        if name not in _parse_c_declaration_statement(statement):
            continue
        return len(_split_top_level(statement.rstrip(";"))) > 1
    return False


def _c_object_macros(source_code: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            r"(?m)^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()",
            source_code,
        )
    }


def _c_shared_symbols(source_code: str, *, module: Any | None = None) -> set[str]:
    """Return identifiers that are valid outside a function's local scope.

    ``_parse_c_module`` deliberately keeps its global parser narrow for the
    Extract Class transformation. Extract Method needs one additional C form:
    an object declared immediately after a top-level aggregate definition,
    e.g. ``struct Customer { ... } customer;``. That object is shared state,
    not an undeclared helper/caller dependency.
    """

    current_module = module or _parse_c_module(source_code)
    return (
        set(current_module.globals)
        | _c_aggregate_instance_names(source_code)
        | _c_object_macros(source_code)
        | _C_STANDARD_SHARED_SYMBOLS
    )


def _c_aggregate_instance_names(source_code: str) -> set[str]:
    """Find top-level struct/union/enum instances following a definition."""

    masked = mask_c_like(source_code)
    names: set[str] = set()
    pattern = re.compile(r"\b(?:struct|union|enum)\b[^;{]*\{")
    for match in pattern.finditer(masked):
        if _brace_depth_at(masked, match.start()) != 0:
            continue
        prefix = masked[masked.rfind(";", 0, match.start()) + 1:match.start()]
        if re.search(r"\btypedef\b", prefix):
            continue
        open_brace = masked.find("{", match.start(), match.end())
        close_brace = _find_matching_c_delimiter(masked, open_brace, "{", "}")
        if close_brace is None:
            continue
        semicolon = masked.find(";", close_brace + 1)
        if semicolon < 0:
            continue
        declarators = masked[close_brace + 1:semicolon]
        if not declarators.strip() or "{" in declarators:
            continue
        for raw in _split_top_level(declarators):
            declaration = _split_first_top_level(raw, "=")[0].strip()
            instance = re.search(
                r"(?:\*\s*)*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^]]*\])?\s*$",
                declaration,
            )
            if instance:
                names.add(instance.group(1))
    return names


def _brace_depth_at(masked_source: str, offset: int) -> int:
    return masked_source[:offset].count("{") - masked_source[:offset].count("}")


def _find_matching_c_delimiter(
    masked_source: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    if start < 0 or start >= len(masked_source) or masked_source[start] != opener:
        return None
    depth = 0
    for index in range(start, len(masked_source)):
        char = masked_source[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _declaration_type_identifiers(text: str) -> set[str]:
    result: set[str] = set()
    for template in _c_local_declarations(text).values():
        before_name = template.split("{name}", 1)[0]
        result.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", before_name))
    return result - _C_KEYWORDS


def _is_assigned_before_read(text: str, name: str) -> bool:
    masked = mask_c_like(text)
    first_use = re.search(rf"\b{re.escape(name)}\b", masked)
    assignment = re.search(
        rf"(?<![A-Za-z0-9_.*>&])\b{re.escape(name)}\s*=(?!=)",
        masked,
    )
    return bool(first_use and assignment and first_use.start() == assignment.start())


def _can_redeclare_in_helper(template: str) -> bool:
    normalized = " ".join(template.split())
    return not normalized.startswith(("static ", "extern ", "volatile "))


def _c_writes(text: str) -> set[str]:
    masked = mask_c_like(text)
    writes = set()
    # Match direct variable assignments (e.g. x = ..., x += ...)
    # Negative lookbehind ensures x is not a struct member (obj.x or obj->x)
    # Negative lookahead ensures x is not followed by . or -> or [
    for match in re.finditer(
        r"(?<![A-Za-z0-9_.*&>])([A-Za-z_][A-Za-z0-9_]*)\s*(?![.\-\[\w])(?:\+\+|--|[+\-*/%&|^]?=(?!=))",
        masked,
    ):
        var = match.group(1)
        if var not in _C_KEYWORDS:
            writes.add(var)
    for match in re.finditer(r"(?<![A-Za-z0-9_.*&>])(?:\+\+|--)\s*([A-Za-z_][A-Za-z0-9_]*)", masked):
        var = match.group(1)
        if var not in _C_KEYWORDS:
            writes.add(var)
    # Mutating a member changes the owning aggregate. Treat the base object as
    # an output so nested helpers receive an address rather than a by-value
    # copy that would silently lose the mutation in the caller.
    for var in _c_member_writes(text):
        if var not in _C_KEYWORDS:
            writes.add(var)
    return writes


def _c_member_writes(text: str) -> set[str]:
    masked = mask_c_like(text)
    return {
        match.group(1)
        for match in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:\.|->)\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:\+\+|--|[+\-*/%&|^]?=(?!=))",
            masked,
        )
    }


def _c_address_output_writes(text: str, declarations: dict[str, str]) -> set[str]:
    masked = mask_c_like(text)
    writes: set[str] = set()
    for match in re.finditer(r"&\s*([A-Za-z_][A-Za-z0-9_]*)\b", masked):
        name = match.group(1)
        if name in declarations and name not in _C_KEYWORDS and not _is_pointer_like_declaration(declarations[name]):
            writes.add(name)
    return writes


def _c_reads(text: str) -> set[str]:
    masked = mask_c_like(text)
    names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked))
    declared = set(_c_local_declarations(text))
    calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", masked))
    member_names = set(re.findall(r"(?:\.|->)\s*([A-Za-z_][A-Za-z0-9_]*)", masked))
    type_names = _declaration_type_identifiers(text)
    return names - _C_KEYWORDS - calls - member_names - declared - type_names


def _validate_transformed_scope(
    source_code: str,
    *,
    source_method: str,
    helper_name: str,
    flow: CFlow,
) -> dict[str, list[str]]:
    """Prove that generated helper/caller identifiers remain lexically valid."""

    validation = {
        key: sorted(set(values))
        for key, values in flow.scope_validation.items()
    }
    model = _parse_c_module(source_code)
    helpers = model.functions_by_name.get(helper_name, [])
    callers = model.functions_by_name.get(source_method, [])
    shared = _c_shared_symbols(source_code, module=model)

    if len(helpers) != 1:
        validation["undefined_identifiers"].append(helper_name)
        return _normalized_scope_validation(validation)
    if len(callers) != 1:
        validation["out_of_scope_identifiers"].append(source_method)
        return _normalized_scope_validation(validation)

    helper = helpers[0]
    caller = callers[0]
    helper_defined = (
        set(_c_parameter_declarations(helper.params_raw))
        | set(_c_local_declarations(helper.body))
        | shared
    )
    helper_free = _c_reads(helper.body)
    validation["undefined_identifiers"].extend(sorted(helper_free - helper_defined))

    caller_defined = (
        set(_c_parameter_declarations(caller.params_raw))
        | set(_c_local_declarations(caller.body))
        | shared
    )
    caller_free = _c_reads(caller.body)
    validation["out_of_scope_identifiers"].extend(sorted(caller_free - caller_defined))

    helper_params = set(_c_parameter_declarations(helper.params_raw))
    for name in flow.inputs:
        if name not in helper_params:
            validation["missing_inputs"].append(name)
    for name in flow.outputs:
        output_parameter = f"{name}_out"
        if not any(param == output_parameter or param.startswith(f"{output_parameter}_") for param in helper_params):
            validation["missing_outputs"].append(name)
    return _normalized_scope_validation(validation)


def _normalized_scope_validation(validation: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        "undefined_identifiers": sorted(set(validation.get("undefined_identifiers", []))),
        "out_of_scope_identifiers": sorted(set(validation.get("out_of_scope_identifiers", []))),
        "missing_inputs": sorted(set(validation.get("missing_inputs", []))),
        "missing_outputs": sorted(set(validation.get("missing_outputs", []))),
    }


def _scope_validation_passed(validation: dict[str, list[str]]) -> bool:
    return all(not validation.get(name) for name in (
        "undefined_identifiers",
        "out_of_scope_identifiers",
        "missing_inputs",
        "missing_outputs",
    ))


def _rewrite(
    source_code: str,
    *,
    function: CFunction,
    selected: Sequence[StatementSpan],
    flow: CFlow,
    new_method_name: str,
) -> str:
    raw_start, end = selected[0].start, selected[-1].end
    body_indent = _statement_indent(source_code, raw_start) or "    "
    line_start = source_code.rfind("\n", 0, raw_start) + 1
    start = line_start if not source_code[line_start:raw_start].strip() else raw_start
    selected_text = source_code[start:end]

    scalar_outputs = list(flow.outputs)

    newly_declared_outputs = [
        name for name in scalar_outputs if name not in flow.defined_before
    ]
    for name in newly_declared_outputs:
        selected_text = _remove_selected_declaration(selected_text, name)

    output_parameters = _output_parameter_names(flow)
    selected_text = _rewrite_scalar_output_uses(selected_text, output_parameters)

    if selected_text and not selected_text.endswith(("\n", "\r")):
        selected_text += "\n"

    helper_local_declarations = [
        flow.declarations[name].format(name=name)
        for name in flow.locals
        if name in flow.defined_before
    ]
    if helper_local_declarations:
        selected_text = "".join(
            f"{body_indent}{declaration};\n"
            for declaration in helper_local_declarations
        ) + selected_text

    input_params = [flow.declarations[name].format(name=name) for name in flow.inputs]
    new_output_params = [
        _pointer_declaration(flow.declarations[name], output_parameters[name])
        for name in scalar_outputs
    ]

    params_list = [*input_params, *new_output_params]
    params = ", ".join(params_list) or "void"
    helper = f"static void {new_method_name}({params}) {{\n{selected_text}}}\n\n"

    declaration_line = ""
    if newly_declared_outputs:
        declaration_line = (
            body_indent
            + "; ".join(
                flow.declarations[name].format(name=name)
                for name in newly_declared_outputs
            )
            + ";\n"
        )

    args = [
        *flow.inputs,
        *(f"&{name}" for name in scalar_outputs),
    ]
    call = f"{body_indent}{new_method_name}({', '.join(args)});"
    if source_code[end - 1:end] == "\n":
        call += "\n"
    replacement = declaration_line + call
    return apply_edits(source_code, [(start, end, replacement), (function.start, function.start, helper)])


def _remove_selected_declaration(text: str, name: str) -> str:
    masked = mask_c_like(text)
    match = re.search(
        rf"(?m)^(?P<indent>[ \t]*)(?:(?:const|volatile|signed|unsigned|short|long|struct\s+\w+|union\s+\w+|enum\s+\w+|[A-Za-z_]\w*)\s+)+(?:\*\s*)*\b{re.escape(name)}\b\s*(?P<tail>=|;)",
        masked,
    )
    if not match:
        return text
    if match.group("tail") == ";":
        line_end = text.find("\n", match.end())
        end = len(text) if line_end < 0 else line_end + 1
        return text[:match.start()] + text[end:]
    return text[:match.start()] + match.group("indent") + name + " " + text[match.start("tail"):]


def _rewrite_scalar_output_uses(text: str, output_parameters: dict[str, str]) -> str:
    if not output_parameters:
        return text
    masked = mask_c_like(text)
    edits: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []

    for match in re.finditer(r"&\s*([A-Za-z_][A-Za-z0-9_]*)\b", masked):
        name = match.group(1)
        output_parameter = output_parameters.get(name)
        if output_parameter:
            edits.append((match.start(), match.end(), output_parameter))
            covered.append((match.start(), match.end()))

    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked):
        name = match.group(0)
        output_parameter = output_parameters.get(name)
        if not output_parameter or any(start <= match.start() < end for start, end in covered):
            continue
        edits.append((match.start(), match.end(), f"(*{output_parameter})"))
    return apply_edits(text, edits)


def _output_parameter_names(flow: CFlow) -> dict[str, str]:
    unavailable = set(flow.declarations)
    names: dict[str, str] = {}
    for output in flow.outputs:
        candidate = f"{output}_out"
        suffix = 2
        while candidate in unavailable:
            candidate = f"{output}_out_{suffix}"
            suffix += 1
        names[output] = candidate
        unavailable.add(candidate)
    return names


def _pointer_declaration(template: str, name: str) -> str:
    rendered = template.format(name=f"*{name}")
    return re.sub(r"\*\s*\*", "**", rendered)


def _is_pointer_like_declaration(template: str) -> bool:
    before_name = template.split("{name}", 1)[0] if "{name}" in template else template
    after_name = template.split("{name}", 1)[1] if "{name}" in template else ""
    return "*" in before_name or "[" in after_name


def _function_metrics(source_code: str, function: CFunction) -> dict[str, int]:
    statements = direct_c_like_statements(function.body, body_offset=function.open_brace + 1)
    return {
        "loc": _line_of(source_code, function.end - 1) - _line_of(source_code, function.start) + 1,
        "complexity": control_complexity(function.body),
        "nesting_depth": _brace_nesting(function.body),
        "statement_count": len(statements),
        "responsibility_count": len(statements),
    }


def _meaningfully_reduced(
    before: dict[str, int],
    after: dict[str, int],
    selected: Sequence[StatementSpan],
    threshold: int = 40,
) -> bool:
    selected_loc = sum(nonblank_loc(item.text) for item in selected)
    if selected_loc < MIN_EXTRACTED_LOC:
        return False
    loc_reduction = before["loc"] - after["loc"]
    complexity_reduced = after["complexity"] < before["complexity"]
    if before["loc"] > threshold:
        # A single cohesive extraction need not eliminate a Long Function in
        # one step. A straight-line routine can have no cyclomatic decrease,
        # yet still lose a real responsibility and substantial implementation.
        # Keep a non-trivial LOC floor so formatting-only wrappers never pass.
        required_reduction = max(MIN_EXTRACTED_LOC * 2, (before["loc"] + 12) // 13)
        return after["loc"] <= threshold or (
            loc_reduction >= required_reduction
            and (complexity_reduced or selected_loc >= required_reduction)
        )
    return after["loc"] < before["loc"]


def _brace_nesting(text: str) -> int:
    depth = maximum = 0
    for char in mask_c_like(text):
        if char == "{":
            depth += 1
            maximum = max(maximum, depth)
        elif char == "}":
            depth = max(0, depth - 1)
    return maximum


def _statement_indent(source_code: str, offset: int) -> str:
    line_start = source_code.rfind("\n", 0, offset) + 1
    prefix = source_code[line_start:offset]
    return prefix[: len(prefix) - len(prefix.lstrip(" \t"))]


def _line_of(source_code: str, offset: int) -> int:
    return source_code.count("\n", 0, max(0, offset)) + 1


def _identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def _base_metadata(method_name: str, new_method_name: str, source_class: str, source_file: str) -> dict[str, Any]:
    return {
        "smell": "Long Method",
        "refactoring": "Extract Function",
        "system_refactoring": "Extract Method",
        "language": "c",
        "source_method": method_name,
        "source_module": source_class,
        "source_file": source_file,
        "extracted_method": new_method_name,
        "plan_compliance": "UNKNOWN",
        "behavioral_safety": "NOT_EVALUATED",
    }


def _candidate_attempt(
    selected: Sequence[StatementSpan],
    reason: str,
    *,
    after_metrics: dict[str, int] | None = None,
    helper_loc: int | None = None,
    scope_validation: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Compact diagnostics for rejected candidates; source stays untouched."""

    return {
        "candidate_range": {
            "start_offset": selected[0].start,
            "end_offset": selected[-1].end,
        },
        "candidate_loc": nonblank_loc("".join(item.text for item in selected)),
        "reason": reason,
        **({"after_metrics": after_metrics} if after_metrics else {}),
        **({"helper_loc": helper_loc} if helper_loc is not None else {}),
        **({"scope_validation": scope_validation} if scope_validation else {}),
    }


def _review(source_code: str, reason: str, metadata: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        **metadata,
        "status": REVIEW_REQUIRED,
        "reason": reason,
        "plan_compliance": "FAIL",
        "final_decision": "REVIEW_REQUIRED",
        "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
    }


def _verify_c_compilation(source_code: str, timeout_seconds: int = 10) -> tuple[str, str]:
    compiler = shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        return "UNAVAILABLE", "C compiler not available; compile check skipped."

    source_code = source_code.lstrip("\ufeff")
    with tempfile.TemporaryDirectory() as temp_dir:
        c_file = Path(temp_dir) / "sctva_temp.c"
        c_file.write_text(source_code, encoding="utf-8")

        compile_args = [compiler, "-std=c11", "-fsyntax-only", "-I", str(temp_dir), str(c_file)]
        try:
            proc = subprocess.run(
                compile_args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or proc.stdout or "").strip()
                return "FAIL", f"C compile check failed ({compiler}): {stderr}"
        except Exception as exc:
            return "FAIL", f"C compile check error: {exc}"

    return "PASS", f"{compiler} compile check passed."
