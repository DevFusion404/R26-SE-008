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


@dataclass
class CFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    declarations: dict[str, str]
    defined_before: set[str]
    pointer_like: set[str]


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

    # Check extracted helper metrics to ensure it does NOT introduce a LongFunction smell (>40 LOC)
    helper_loc = _line_of(transformed, helper_matches[0].end - 1) - _line_of(transformed, helper_matches[0].start) + 1 if helper_matches else 0

    structural_passed = len(helper_matches) == 1 and re.search(
        rf"\b{re.escape(new_method_name)}\s*\(", after_function.body
    ) is not None
    reduction_passed = _meaningfully_reduced(before_metrics, after_metrics, selected, threshold=40)
    helper_smell_free = helper_loc <= 40

    if not structural_passed or not reduction_passed or not helper_smell_free:
        reason = "EXTRACT_FUNCTION_STRUCTURE_NOT_PROVEN" if not structural_passed else ("HELPER_LONG_FUNCTION_SMELL" if not helper_smell_free else "LONG_FUNCTION_NOT_REDUCED")
        return _review(
            source_code,
            reason,
            {**metadata, "before_metrics": before_metrics, "after_metrics": after_metrics},
        )

    # Compiler validation using GCC/Clang if available
    compile_status, compile_msg = _verify_c_compilation(transformed)
    if compile_status == "FAIL":
        return _review(
            source_code,
            f"COMPILATION_FAILED: {compile_msg}",
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
        "compiler_validation": compile_msg,
        "validation": {
            "target_resolution": "PASS",
            "data_flow": "PASS",
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


def _select_candidate(
    source_code: str,
    function: CFunction,
    statements: Sequence[StatementSpan],
    parameter_declarations: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
    target_loc_threshold: int = 40,
) -> tuple[list[StatementSpan], CFlow] | None:
    before_loc = _line_of(source_code, function.end - 1) - _line_of(source_code, function.start) + 1
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
        if not (start_line and end_line) and loc < MIN_EXTRACTED_LOC and complexity <= 1:
            continue

        estimated_after_loc = before_loc - loc + 2
        estimated_helper_loc = loc + 3

        threshold_reducing_bonus = 0.0
        if before_loc > target_loc_threshold:
            if estimated_after_loc <= target_loc_threshold and estimated_helper_loc <= target_loc_threshold:
                threshold_reducing_bonus = 1000.0
            elif estimated_after_loc <= target_loc_threshold:
                threshold_reducing_bonus = 500.0
            else:
                threshold_reducing_bonus = -500.0

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
            threshold_reducing_bonus
            + hint_bonus
            + complexity * 4.0
            + loc * 2.0
            + cohesion
            - (len(flow.inputs) + len(flow.outputs)) * 0.5
        )
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
    pointer_like = {
        name
        for name, declaration in declarations.items()
        if _is_pointer_like_declaration(declaration)
    }
    reads = _c_reads(selected_text)
    direct_writes = _c_writes(selected_text)
    address_writes = _c_address_output_writes(selected_text, declarations)
    writes = direct_writes | address_writes | set(selected_declarations)
    reads_after = _c_reads(after_text)
    if direct_writes & reads_after & pointer_like:
        return None
    outputs = sorted(name for name in (writes & reads_after) if name not in pointer_like)
    inputs = sorted((reads & defined_before) - set(outputs))
    locals_only = sorted(writes - set(outputs))
    if any(name not in declarations for name in [*inputs, *outputs]):
        return None
    return CFlow(inputs, outputs, locals_only, declarations, defined_before, pointer_like)


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
    return writes


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
    if before["loc"] > threshold:
        return after["loc"] <= threshold
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
