"""Semantic Java Long Method -> Extract Method transformation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

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
from .java_extract_class import JavaClass, JavaMethod, _parse_java_class, top_level_class_names


REVIEW_REQUIRED = "review_required"
ALREADY_APPLIED = "already_applied"
_JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
    "const", "continue", "default", "do", "double", "else", "enum", "extends", "false",
    "final", "finally", "float", "for", "goto", "if", "implements", "import", "instanceof",
    "int", "interface", "long", "native", "new", "null", "package", "private", "protected",
    "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized",
    "this", "throw", "throws", "transient", "true", "try", "void", "volatile", "while",
}


@dataclass
class JavaFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    types: dict[str, str]
    defined_before: set[str]


def target_match_count(
    source_code: str,
    *,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
) -> int:
    return len(_resolve_targets(source_code, method_name, source_class, method_signature))


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
        return _review(source_code, "INVALID_METHOD_TARGET_OR_NAME", metadata)
    targets = _resolve_targets(source_code, method_name, source_class, method_signature)
    if not targets:
        return _review(source_code, "METHOD_TARGET_NOT_FOUND", metadata)
    if len(targets) != 1:
        return _review(source_code, "AMBIGUOUS_OVERLOADED_METHOD_TARGET", metadata)
    source_model, method = targets[0]
    metadata["source_class"] = source_model.name
    if method.is_constructor:
        return _review(source_code, "CONSTRUCTOR_EXTRACTION_UNSUPPORTED", metadata)
    helper_collisions = source_model.methods_by_name.get(new_method_name, [])
    if helper_collisions:
        if re.search(rf"\b{re.escape(new_method_name)}\s*\(", method.body):
            metadata.update({"status": ALREADY_APPLIED, "reason": "ALREADY_APPLIED", "plan_compliance": "PASS"})
            return source_code, 0, metadata
        return _review(source_code, "EXTRACTED_METHOD_NAME_COLLISION", metadata)

    statements = direct_c_like_statements(method.body, body_offset=method.open_brace + 1)
    if len(statements) < 3:
        return _review(source_code, "METHOD_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK", metadata)
    parameter_types = _java_parameter_types(method)
    before_metrics = _method_metrics(source_code, method)
    candidate = _select_candidate(
        source_code,
        source_model,
        method,
        statements,
        parameter_types,
        start_line=start_line,
        end_line=end_line,
    )
    if candidate is None:
        return _review(source_code, "NO_SAFE_COHESIVE_BLOCK", {**metadata, "before_metrics": before_metrics})
    selected, flow = candidate
    if len(flow.inputs) > MAX_EXTRACTED_PARAMETERS:
        return _review(source_code, "TOO_MANY_PARAMETERS", {**metadata, "before_metrics": before_metrics})
    if len(flow.outputs) > 1:
        return _review(source_code, "MULTIPLE_JAVA_OUTPUTS_REQUIRE_REVIEW", {**metadata, "before_metrics": before_metrics})

    transformed = _rewrite(
        source_code,
        method=method,
        selected=selected,
        flow=flow,
        new_method_name=new_method_name,
    )
    transformed_targets = _resolve_targets(transformed, method_name, source_model.name, method_signature)
    transformed_model = _parse_java_class(transformed, source_model.name)
    if len(transformed_targets) != 1 or transformed_model is None:
        return _review(source_code, "POST_TRANSFORM_TARGET_VALIDATION_FAILED", {**metadata, "before_metrics": before_metrics})
    after_method = transformed_targets[0][1]
    after_metrics = _method_metrics(transformed, after_method)
    helper_matches = transformed_model.methods_by_name.get(new_method_name, [])
    structural_passed = len(helper_matches) == 1 and re.search(
        rf"\b{re.escape(new_method_name)}\s*\(", after_method.body
    ) is not None
    reduction_passed = _meaningfully_reduced(before_metrics, after_metrics, selected)
    if not structural_passed or not reduction_passed:
        reason = "EXTRACT_METHOD_STRUCTURE_NOT_PROVEN" if not structural_passed else "LONG_METHOD_NOT_REDUCED"
        return _review(
            source_code,
            reason,
            {**metadata, "before_metrics": before_metrics, "after_metrics": after_metrics},
        )

    metadata.update({
        "status": "success",
        "reason": "extract_method_applied",
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


def _resolve_targets(
    source_code: str,
    method_name: str,
    source_class: str,
    method_signature: str,
) -> list[tuple[JavaClass, JavaMethod]]:
    class_names = [source_class] if source_class else sorted(top_level_class_names(source_code))
    matches: list[tuple[JavaClass, JavaMethod]] = []
    for class_name in class_names:
        model = _parse_java_class(source_code, class_name)
        if model is None:
            continue
        for method in model.methods_by_name.get(method_name, []):
            if _signature_matches(method, method_signature):
                matches.append((model, method))
    return matches


def _signature_matches(method: JavaMethod, signature: str) -> bool:
    normalized = normalize_signature(signature)
    if not normalized:
        return True
    params = _java_parameter_types(method)
    rendered = f"{method.name}({','.join(params.values())})"
    return normalized in {normalize_signature(rendered), normalize_signature(method.header)}


def _select_candidate(
    source_code: str,
    source_model: JavaClass,
    method: JavaMethod,
    statements: Sequence[StatementSpan],
    parameter_types: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
) -> tuple[list[StatementSpan], JavaFlow] | None:
    windows = candidate_windows(
        statements,
        start_line=start_line,
        end_line=end_line,
        source=source_code,
    )
    scored: list[tuple[float, list[StatementSpan], JavaFlow]] = []
    for window in windows:
        text = source_code[window[0].start:window[-1].end]
        if has_unsafe_cross_boundary_flow(text, language="java"):
            continue
        if re.search(r"\b(?:class|interface|enum|record)\b|->", mask_c_like(text)):
            continue
        first_index = statements.index(window[0])
        last_index = statements.index(window[-1]) + 1
        if len(statements) - len(window) < 1:
            continue
        flow = _java_flow(
            source_code,
            source_model,
            statements,
            first_index,
            last_index,
            parameter_types,
        )
        if flow is None or len(flow.outputs) > 1 or len(flow.inputs) > MAX_EXTRACTED_PARAMETERS:
            continue
        loc = nonblank_loc(text)
        complexity = control_complexity(text)
        if loc < MIN_EXTRACTED_LOC and complexity <= 1:
            continue
        hint_bonus = 20 if start_line and end_line and _line_of(source_code, window[0].start) <= end_line and _line_of(source_code, window[-1].end - 1) >= start_line else 0
        cohesion = len(set(flow.inputs) & identifiers(text)) + len(flow.outputs)
        score = hint_bonus + complexity * 4 + loc + cohesion - len(flow.inputs) * 0.5
        scored.append((score, list(window), flow))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return scored[0][1], scored[0][2]


def _java_flow(
    source_code: str,
    source_model: JavaClass,
    statements: Sequence[StatementSpan],
    start_index: int,
    end_index: int,
    parameter_types: dict[str, str],
) -> JavaFlow | None:
    before_text = "".join(item.text for item in statements[:start_index])
    selected_text = source_code[statements[start_index].start:statements[end_index - 1].end]
    after_text = "".join(item.text for item in statements[end_index:])
    before_declarations = _java_local_declarations(before_text)
    selected_declarations = _java_local_declarations(selected_text)
    types = {**parameter_types, **before_declarations, **selected_declarations}
    defined_before = set(parameter_types) | set(before_declarations)
    fields = set(source_model.fields)
    reads = _java_reads(selected_text)
    writes = _java_writes(selected_text) | set(selected_declarations)
    reads_after = _java_reads(after_text)
    inputs = sorted((reads & defined_before) - fields)
    outputs = sorted(writes & reads_after)
    locals_only = sorted(writes - set(outputs))
    required_types = set(inputs) | set(outputs)
    if any(name not in types for name in required_types):
        return None
    return JavaFlow(inputs, outputs, locals_only, types, defined_before)


def _java_parameter_types(method: JavaMethod) -> dict[str, str]:
    params_raw = _params_raw(method.header, method.name)
    result: dict[str, str] = {}
    for raw in _split_top_level(params_raw):
        cleaned = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", raw).strip()
        cleaned = re.sub(r"\bfinal\s+", "", cleaned).strip()
        match = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(\[\s*\])?\s*$", cleaned)
        if not match:
            continue
        name = match.group(1)
        type_name = cleaned[:match.start()].strip()
        if match.group(2):
            type_name += "[]"
        if type_name:
            result[name] = type_name
    return result


def _params_raw(header: str, method_name: str) -> str:
    masked = mask_c_like(header)
    match = re.search(rf"\b{re.escape(method_name)}\s*\(", masked)
    if not match:
        return ""
    start = masked.find("(", match.start())
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return header[start + 1:index]
    return ""


def _split_top_level(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    angles = parens = brackets = 0
    for char in value:
        if char == "<":
            angles += 1
        elif char == ">" and angles:
            angles -= 1
        elif char == "(":
            parens += 1
        elif char == ")" and parens:
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets:
            brackets -= 1
        if char == "," and angles == parens == brackets == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current).strip())
    return [item for item in items if item]


def _java_local_declarations(text: str) -> dict[str, str]:
    masked = mask_c_like(text)
    declarations: dict[str, str] = {}
    pattern = re.compile(
        r"(?m)(?:^|[;{}])\s*(?:final\s+)?"
        r"([A-Za-z_$][A-Za-z0-9_$<>?,.\[\]\s]*?)\s+"
        r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?==|;)"
    )
    for match in pattern.finditer(masked):
        type_name = " ".join(match.group(1).split())
        if type_name and type_name.split()[0] not in _JAVA_KEYWORDS - {"boolean", "byte", "char", "double", "float", "int", "long", "short"}:
            declarations[match.group(2)] = type_name
    return declarations


def _java_writes(text: str) -> set[str]:
    masked = mask_c_like(text)
    writes = {
        match.group(1)
        for match in re.finditer(
            r"(?<![A-Za-z0-9_$.])([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\+\+|--|[+\-*/%&|^]?=(?!=))",
            masked,
        )
    }
    writes.update(match.group(1) for match in re.finditer(r"(?:\+\+|--)\s*([A-Za-z_$][A-Za-z0-9_$]*)", masked))
    return writes


def _java_reads(text: str) -> set[str]:
    masked = mask_c_like(text)
    names = set(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", masked))
    declared = set(_java_local_declarations(text))
    method_calls = set(re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", masked))
    type_names = set(re.findall(r"\bnew\s+([A-Za-z_$][A-Za-z0-9_$]*)", masked))
    return names - _JAVA_KEYWORDS - method_calls - type_names - declared


def _rewrite(
    source_code: str,
    *,
    method: JavaMethod,
    selected: Sequence[StatementSpan],
    flow: JavaFlow,
    new_method_name: str,
) -> str:
    raw_start, end = selected[0].start, selected[-1].end
    indent = method.indent
    body_indent = _statement_indent(source_code, raw_start) or f"{indent}    "
    line_start = source_code.rfind("\n", 0, raw_start) + 1
    start = line_start if not source_code[line_start:raw_start].strip() else raw_start
    static_prefix = "static " if "static" in method.modifiers else ""
    generic_prefix = _generic_prefix(method.header)
    throws_clause = _throws_clause(method.header)
    params = ", ".join(f"{flow.types[name]} {name}" for name in flow.inputs)
    args = ", ".join(flow.inputs)
    output = flow.outputs[0] if flow.outputs else ""
    return_type = flow.types[output] if output else "void"
    call = f"{new_method_name}({args})"
    if output:
        declaration = f"{flow.types[output]} " if output not in flow.defined_before else ""
        replacement = f"{body_indent}{declaration}{output} = {call};"
    else:
        replacement = f"{body_indent}{call};"
    if source_code[end - 1:end] == "\n":
        replacement += "\n"
    selected_text = source_code[start:end]
    if selected_text and not selected_text.endswith(("\n", "\r")):
        selected_text += "\n"
    helper = (
        f"\n{indent}private {static_prefix}{generic_prefix}{return_type} {new_method_name}({params}){throws_clause} {{\n"
        f"{selected_text}"
    )
    if output:
        helper += f"{body_indent}return {output};\n"
    helper += f"{indent}}}\n"
    return apply_edits(source_code, [(method.end, method.end, helper), (start, end, replacement)])


def _generic_prefix(header: str) -> str:
    cleaned = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", header)
    match = re.search(r"\b(?:public|protected|private|static|final|synchronized|native|strictfp)\b(?:\s+\b(?:public|protected|private|static|final|synchronized|native|strictfp)\b)*\s+(<[^\n{]+?>)\s+", cleaned)
    return f"{match.group(1)} " if match else ""


def _throws_clause(header: str) -> str:
    match = re.search(r"\)\s*(throws\s+[^\n{]+)\s*$", header.strip())
    return f" {match.group(1).strip()}" if match else ""


def _method_metrics(source_code: str, method: JavaMethod) -> dict[str, int]:
    statements = direct_c_like_statements(method.body, body_offset=method.open_brace + 1)
    return {
        "loc": _line_of(source_code, method.end - 1) - _line_of(source_code, method.start) + 1,
        "complexity": control_complexity(method.body),
        "nesting_depth": _brace_nesting(method.body),
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
    return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", value or ""))


def _base_metadata(method_name: str, new_method_name: str, source_class: str, source_file: str) -> dict[str, Any]:
    return {
        "smell": "Long Method",
        "refactoring": "Extract Method",
        "language": "java",
        "source_method": method_name,
        "source_class": source_class,
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
