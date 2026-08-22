"""Semantic C Long Function -> Extract Function transformation."""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass
class CFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    declarations: dict[str, str]
    defined_before: set[str]


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
    if not _identifier(method_name) or not _identifier(new_method_name):
        return _review(source_code, "INVALID_FUNCTION_TARGET_OR_NAME", metadata)
    targets = _resolve_targets(source_code, method_name, method_signature)
    if not targets:
        return _review(source_code, "FUNCTION_TARGET_NOT_FOUND", metadata)
    if len(targets) != 1:
        return _review(source_code, "AMBIGUOUS_FUNCTION_TARGET", metadata)
    function = targets[0]
    model = _parse_c_module(source_code)
    helper_collisions = model.functions_by_name.get(new_method_name, [])
    if helper_collisions:
        if re.search(rf"\b{re.escape(new_method_name)}\s*\(", function.body):
            metadata.update({"status": ALREADY_APPLIED, "reason": "ALREADY_APPLIED", "plan_compliance": "PASS"})
            return source_code, 0, metadata
        return _review(source_code, "EXTRACTED_FUNCTION_NAME_COLLISION", metadata)

    statements = direct_c_like_statements(function.body, body_offset=function.open_brace + 1)
    if len(statements) < 3:
        return _review(source_code, "FUNCTION_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK", metadata)
    parameter_declarations = _c_parameter_declarations(function.params_raw)
    before_metrics = _function_metrics(source_code, function)
    candidate = _select_candidate(
        source_code,
        function,
        statements,
        parameter_declarations,
        start_line=start_line,
        end_line=end_line,
    )
    if candidate is None:
        return _review(source_code, "NO_SAFE_COHESIVE_BLOCK", {**metadata, "before_metrics": before_metrics})
    selected, flow = candidate
    parameter_count = len(flow.inputs) + len(flow.outputs)
    if parameter_count > MAX_EXTRACTED_PARAMETERS:
        return _review(source_code, "TOO_MANY_PARAMETERS", {**metadata, "before_metrics": before_metrics})
    if any("[" in flow.declarations[name] for name in flow.outputs):
        return _review(source_code, "ARRAY_OUTPUT_REQUIRES_REVIEW", {**metadata, "before_metrics": before_metrics})

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
        return _review(source_code, "POST_TRANSFORM_TARGET_VALIDATION_FAILED", {**metadata, "before_metrics": before_metrics})
    after_function = transformed_targets[0]
    after_metrics = _function_metrics(transformed, after_function)
    helper_matches = transformed_model.functions_by_name.get(new_method_name, [])
    structural_passed = len(helper_matches) == 1 and re.search(
        rf"\b{re.escape(new_method_name)}\s*\(", after_function.body
    ) is not None
    reduction_passed = _meaningfully_reduced(before_metrics, after_metrics, selected)
    if not structural_passed or not reduction_passed:
        reason = "EXTRACT_FUNCTION_STRUCTURE_NOT_PROVEN" if not structural_passed else "LONG_FUNCTION_NOT_REDUCED"
        return _review(
            source_code,
            reason,
            {**metadata, "before_metrics": before_metrics, "after_metrics": after_metrics},
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
        "validation": {
            "target_resolution": "PASS",
            "data_flow": "PASS",
            "structural": "PASS",
            "no_severe_new_smell": "PASS",
            "long_method_reduction": "PASS",
        },
        "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
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


def _select_candidate(
    source_code: str,
    function: CFunction,
    statements: Sequence[StatementSpan],
    parameter_declarations: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
) -> tuple[list[StatementSpan], CFlow] | None:
    windows = candidate_windows(
        statements,
        start_line=start_line,
        end_line=end_line,
        source=source_code,
    )
    scored: list[tuple[float, list[StatementSpan], CFlow]] = []
    for window in windows:
        text = source_code[window[0].start:window[-1].end]
        if has_unsafe_cross_boundary_flow(text, language="c"):
            continue
        if re.search(r"\b(?:typedef|struct|union|enum)\b[^;{]*\{", mask_c_like(text)):
            continue
        first_index = statements.index(window[0])
        last_index = statements.index(window[-1]) + 1
        if len(statements) - len(window) < 1:
            continue
        flow = _c_flow(
            source_code,
            statements,
            first_index,
            last_index,
            parameter_declarations,
        )
        if flow is None or len(flow.inputs) + len(flow.outputs) > MAX_EXTRACTED_PARAMETERS:
            continue
        loc = nonblank_loc(text)
        complexity = control_complexity(text)
        if loc < MIN_EXTRACTED_LOC and complexity <= 1:
            continue
        hint_bonus = 20 if start_line and end_line and _line_of(source_code, window[0].start) <= end_line and _line_of(source_code, window[-1].end - 1) >= start_line else 0
        cohesion = len(set(flow.inputs) & identifiers(text)) + len(flow.outputs)
        score = hint_bonus + complexity * 4 + loc + cohesion - (len(flow.inputs) + len(flow.outputs)) * 0.5
        scored.append((score, list(window), flow))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return scored[0][1], scored[0][2]


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
    declarations = {**parameter_declarations, **before_declarations, **selected_declarations}
    defined_before = set(parameter_declarations) | set(before_declarations)
    reads = _c_reads(selected_text)
    writes = _c_writes(selected_text) | set(selected_declarations)
    reads_after = _c_reads(after_text)
    outputs = sorted(writes & reads_after)
    inputs = sorted((reads & defined_before) - set(outputs))
    locals_only = sorted(writes - set(outputs))
    if any(name not in declarations for name in [*inputs, *outputs]):
        return None
    return CFlow(inputs, outputs, locals_only, declarations, defined_before)


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
    masked = mask_c_like(text)
    declarations: dict[str, str] = {}
    pattern = re.compile(
        r"(?m)(?:^|[;{}])\s*"
        r"((?:(?:const|volatile|signed|unsigned|short|long|struct\s+[A-Za-z_]\w*|union\s+[A-Za-z_]\w*|enum\s+[A-Za-z_]\w*|[A-Za-z_]\w*)\s+)+(?:\*\s*)*)"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(\[[^]]*\])?\s*(?==|;)"
    )
    for match in pattern.finditer(masked):
        prefix = " ".join(match.group(1).split()).replace(" *", "*")
        first = prefix.split()[0] if prefix.split() else ""
        if first in {"return", "if", "for", "while", "switch", "case"}:
            continue
        suffix = match.group(3) or ""
        declarations[match.group(2)] = f"{prefix} {{name}}{suffix}"
    return declarations


def _c_writes(text: str) -> set[str]:
    masked = mask_c_like(text)
    writes = {
        match.group(1)
        for match in re.finditer(
            r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s*(?:\.|->)\s*[A-Za-z_][A-Za-z0-9_]*)?\s*"
            r"(?:\+\+|--|[+\-*/%&|^]?=(?!=))",
            masked,
        )
    }
    writes.update(match.group(1) for match in re.finditer(r"(?:\+\+|--)\s*([A-Za-z_][A-Za-z0-9_]*)", masked))
    return writes


def _c_reads(text: str) -> set[str]:
    masked = mask_c_like(text)
    names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked))
    declared = set(_c_local_declarations(text))
    calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", masked))
    member_names = set(re.findall(r"(?:\.|->)\s*([A-Za-z_][A-Za-z0-9_]*)", masked))
    return names - _C_KEYWORDS - calls - member_names - declared


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
    newly_declared_outputs = [name for name in flow.outputs if name not in flow.defined_before]
    for name in newly_declared_outputs:
        selected_text = _remove_selected_declaration(selected_text, name)
    replacement_names = {name: f"(*{name}_out)" for name in flow.outputs}
    selected_text = _replace_identifiers(selected_text, replacement_names)
    if selected_text and not selected_text.endswith(("\n", "\r")):
        selected_text += "\n"

    input_params = [flow.declarations[name].format(name=name) for name in flow.inputs]
    output_params = [
        _pointer_declaration(flow.declarations[name], f"{name}_out")
        for name in flow.outputs
    ]
    params = ", ".join([*input_params, *output_params]) or "void"
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
    args = [*flow.inputs, *(f"&{name}" for name in flow.outputs)]
    call = f"{body_indent}{new_method_name}({', '.join(args)});"
    if source_code[end - 1:end] == "\n":
        call += "\n"
    replacement = declaration_line + call
    return apply_edits(source_code, [(start, end, replacement), (function.start, function.start, helper)])


def _remove_selected_declaration(text: str, name: str) -> str:
    masked = mask_c_like(text)
    match = re.search(
        rf"(?m)^(?P<indent>[ \t]*)(?:(?:const|volatile|signed|unsigned|short|long|struct\s+\w+|union\s+\w+|enum\s+\w+|[A-Za-z_]\w*)\s+)+(?:\*\s*)*\b{re.escape(name)}\b\s*(?==)",
        masked,
    )
    if not match:
        return text
    return text[:match.start()] + match.group("indent") + name + " " + text[match.end():]


def _replace_identifiers(text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return text
    masked = mask_c_like(text)
    edits: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked):
        replacement = replacements.get(match.group(0))
        if replacement:
            edits.append((match.start(), match.end(), replacement))
    return apply_edits(text, edits)


def _pointer_declaration(template: str, name: str) -> str:
    rendered = template.format(name=f"*{name}")
    return re.sub(r"\*\s*\*", "**", rendered)


def _function_metrics(source_code: str, function: CFunction) -> dict[str, int]:
    statements = direct_c_like_statements(function.body, body_offset=function.open_brace + 1)
    return {
        "loc": _line_of(source_code, function.end - 1) - _line_of(source_code, function.start) + 1,
        "complexity": control_complexity(function.body),
        "nesting_depth": _brace_nesting(function.body),
        "statement_count": len(statements),
        "responsibility_count": len(statements),
    }


def _meaningfully_reduced(before: dict[str, int], after: dict[str, int], selected: Sequence[StatementSpan]) -> bool:
    selected_loc = sum(nonblank_loc(item.text) for item in selected)
    return (
        selected_loc >= MIN_EXTRACTED_LOC
        and after["loc"] <= before["loc"] - 2
        and after["statement_count"] < before["statement_count"]
        and after["complexity"] <= before["complexity"]
        and after["responsibility_count"] < before["responsibility_count"]
    )


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


def _review(source_code: str, reason: str, metadata: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        **metadata,
        "status": REVIEW_REQUIRED,
        "reason": reason,
        "plan_compliance": "FAIL",
        "final_decision": "REVIEW_REQUIRED",
        "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
    }
