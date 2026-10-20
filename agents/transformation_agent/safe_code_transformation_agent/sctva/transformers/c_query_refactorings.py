"""Conservative C query-oriented refactorings.

These transformations deliberately support only source-local cases that can be
proved from the translation unit.  C has no reflection or method dispatch, so
removing a parameter or extracting a temporary expression without a complete
call-site/dependency proof would silently change its ABI or evaluation order.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Sequence

from . import c_extract_class


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_C_KEYWORDS = {
    "const", "enum", "false", "float", "int", "long", "NULL", "return",
    "short", "signed", "sizeof", "static", "struct", "true", "unsigned",
    "void", "volatile",
}


def apply_replace_temp_with_query(
    source_code: str,
    *,
    method_name: str,
    temp_name: str = "",
    query_method_name: str = "",
    source_line: int | None = None,
) -> tuple[str, int, Dict[str, Any]]:
    """Replace a pure, stable local temporary with a file-local query helper."""
    function, reason = _resolve_function(source_code, method_name, source_line)
    if function is None:
        return _review(source_code, reason, method=method_name)

    function_source = source_code[function.start:function.end]
    declaration = _find_temp_declaration(function_source, temp_name)
    if declaration is None:
        return _review(source_code, "PURE_TEMPORARY_NOT_FOUND", method=function.name)

    declaration_start, declaration_end, value_type, resolved_temp, expression = declaration
    if not _is_pure_expression(expression):
        return _review(source_code, "TEMPORARY_EXPRESSION_HAS_SIDE_EFFECTS", method=function.name)

    parameter_declarations = _parameter_declarations(function.params_raw)
    dependencies = _expression_identifiers(expression)
    unknown = dependencies - set(parameter_declarations)
    if unknown:
        return _review(
            source_code,
            "TEMPORARY_QUERY_DEPENDENCY_NOT_PARAMETER",
            method=function.name,
            unresolved_identifiers=sorted(unknown),
        )

    tail = function_source[declaration_end:]
    if not _stable_dependencies(tail, dependencies):
        return _review(source_code, "TEMPORARY_QUERY_DEPENDENCY_MUTATED", method=function.name)
    masked_tail = c_extract_class._mask_c_like(tail)
    use_count = len(list(re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(resolved_temp)}\b", masked_tail)))
    if use_count == 0:
        return _review(source_code, "TEMPORARY_HAS_NO_REPLACEABLE_USE", method=function.name)

    query_name = _unique_identifier(
        source_code,
        query_method_name or f"query_{function.name}_{resolved_temp}",
    )
    dependency_names = sorted(dependencies, key=lambda name: _parameter_index(parameter_declarations, name))
    query_parameters = ", ".join(parameter_declarations[name] for name in dependency_names) or "void"
    call_arguments = ", ".join(dependency_names)
    replacement = f"{query_name}({call_arguments})"
    rewritten_tail = _replace_identifier(tail, resolved_temp, replacement)
    rewritten_function = function_source[:declaration_start] + rewritten_tail
    helper = (
        f"static {value_type} {query_name}({query_parameters}) {{\n"
        f"    return {expression.strip()};\n"
        f"}}\n\n"
    )
    transformed = (
        source_code[:function.start]
        + helper
        + rewritten_function
        + source_code[function.end:]
    )
    return transformed, use_count + 1, {
        "status": "success",
        "method": function.name,
        "temporary_name": resolved_temp,
        "query_method_name": query_name,
        "query_dependencies": dependency_names,
        "strategy": "pure_expression_file_local_query",
    }


def apply_replace_parameter_with_method_call(
    source_code: str,
    *,
    method_name: str,
    parameter_name: str = "",
    query_method_name: str = "",
    query_expression: str = "",
    query_parameters: Sequence[str] | None = None,
    source_line: int | None = None,
) -> tuple[str, int, Dict[str, Any]]:
    """Remove one parameter only when every local C call site is provable.

    RDP must identify the parameter and its equivalent query expression.  The
    inferred version would be a semantic guess, so it is intentionally refused.
    """
    function, reason = _resolve_function(source_code, method_name, source_line)
    if function is None:
        return _review(source_code, reason, method=method_name)
    if "static" not in function.return_type.split():
        return _review(source_code, "EXTERNAL_FUNCTION_SIGNATURE_REQUIRES_PROJECT_TRANSACTION", method=function.name)
    if not parameter_name or not query_expression:
        return _review(source_code, "PARAMETER_OR_QUERY_EXPRESSION_MISSING_FROM_PLAN", method=function.name)

    parameter_declarations = _parameter_declarations(function.params_raw)
    if parameter_name not in parameter_declarations:
        return _review(source_code, "PARAMETER_TARGET_NOT_FOUND", method=function.name)
    parameter_names = list(parameter_declarations)
    parameter_index = parameter_names.index(parameter_name)
    declared_parameter_type = _declaration_type(parameter_declarations[parameter_name], parameter_name)
    if not declared_parameter_type:
        return _review(source_code, "PARAMETER_TYPE_NOT_RESOLVED", method=function.name)

    remaining_parameters = [name for name in parameter_names if name != parameter_name]
    requested_dependencies = [str(name) for name in (query_parameters or []) if str(name)]
    dependencies = requested_dependencies or sorted(_expression_identifiers(query_expression))
    if not set(dependencies).issubset(set(remaining_parameters)):
        return _review(source_code, "QUERY_DEPENDENCY_NOT_REMAINING_PARAMETER", method=function.name)
    if not _is_pure_expression(query_expression):
        return _review(source_code, "QUERY_EXPRESSION_HAS_SIDE_EFFECTS", method=function.name)

    calls, call_reason = _local_call_sites(source_code, function)
    if call_reason:
        return _review(source_code, call_reason, method=function.name)
    query_name = _unique_identifier(
        source_code,
        query_method_name or f"query_{function.name}_{parameter_name}",
    )
    dependency_declarations = [parameter_declarations[name] for name in dependencies]
    helper = (
        f"static {declared_parameter_type} {query_name}("
        f"{', '.join(dependency_declarations) or 'void'}) {{\n"
        f"    return {query_expression.strip()};\n"
        f"}}\n\n"
    )
    function_source = source_code[function.start:function.end]
    new_signature = _remove_function_parameter(function_source, function, parameter_index)
    if new_signature is None:
        return _review(source_code, "FUNCTION_SIGNATURE_REWRITE_FAILED", method=function.name)
    call_expression = f"{query_name}({', '.join(dependencies)})"
    rewritten_function = _replace_identifier(new_signature, parameter_name, call_expression)

    edits: list[tuple[int, int, str]] = [
        (function.start, function.end, helper + rewritten_function),
    ]
    for open_paren, close_paren, arguments in calls:
        updated_arguments = [argument for index, argument in enumerate(arguments) if index != parameter_index]
        edits.append((open_paren + 1, close_paren, ", ".join(updated_arguments)))
    transformed = _apply_edits(source_code, edits)
    return transformed, len(calls) + 2, {
        "status": "success",
        "method": function.name,
        "parameter_name": parameter_name,
        "query_method_name": query_name,
        "query_dependencies": dependencies,
        "updated_call_sites": len(calls),
        "strategy": "static_function_local_call_site_update",
    }


def _resolve_function(source: str, method_name: str, source_line: int | None):
    module = c_extract_class._parse_c_module(source)
    matches = list(module.functions_by_name.get(method_name, [])) if method_name else []
    if source_line is not None:
        line_offset = _offset_for_line(source, source_line)
        line_matches = [item for item in matches if item.start <= line_offset < item.end]
        if line_matches:
            matches = line_matches
    if len(matches) != 1:
        return None, "TARGET_FUNCTION_NOT_FOUND" if not matches else "TARGET_FUNCTION_AMBIGUOUS"
    return matches[0], ""


def _find_temp_declaration(function_source: str, requested_name: str):
    masked = c_extract_class._mask_c_like(function_source)
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)(?P<type>(?:const\s+)?(?:unsigned\s+|signed\s+|long\s+|short\s+)?"
        r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\*+)?)\s+(?P<name>" + _IDENTIFIER + r")\s*=\s*(?P<expr>[^;\n]+);"
    )
    for match in pattern.finditer(masked):
        name = match.group("name")
        if requested_name and name != requested_name:
            continue
        return (
            match.start(),
            match.end(),
            function_source[match.start("type"):match.end("type")].strip(),
            name,
            function_source[match.start("expr"):match.end("expr")],
        )
    return None


def _parameter_declarations(params_raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not params_raw.strip() or params_raw.strip() == "void":
        return result
    for declaration in c_extract_class._split_top_level(params_raw, ","):
        match = re.search(rf"({_IDENTIFIER})\s*(?:\[[^]]*\])?\s*$", declaration.strip())
        if match:
            result[match.group(1)] = declaration.strip()
    return result


def _declaration_type(declaration: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*(?:\[[^]]*\])?\s*$", declaration)
    return declaration[:match.start()].strip() if match else ""


def _expression_identifiers(expression: str) -> set[str]:
    masked = c_extract_class._mask_c_like(expression)
    return {
        token
        for token in re.findall(_IDENTIFIER, masked)
        if token not in _C_KEYWORDS and not token.isupper()
    }


def _is_pure_expression(expression: str) -> bool:
    masked = c_extract_class._mask_c_like(expression)
    if not masked.strip() or re.search(r"(?:\+\+|--|(?<![=!<>])=(?!=))", masked):
        return False
    return "(" not in masked and ")" not in masked and "[" not in masked and "]" not in masked


def _stable_dependencies(text: str, dependencies: Iterable[str]) -> bool:
    masked = c_extract_class._mask_c_like(text)
    for name in dependencies:
        escaped = re.escape(name)
        if re.search(rf"\b{escaped}\s*(?:=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=)|(?:\+\+|--)\s*\b{escaped}\b|\b{escaped}\b\s*(?:\+\+|--)", masked):
            return False
    return True


def _replace_identifier(source: str, name: str, replacement: str) -> str:
    masked = c_extract_class._mask_c_like(source)
    edits = [
        (match.start(), match.end(), replacement)
        for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(name)}\b", masked)
    ]
    return _apply_edits(source, edits)


def _local_call_sites(source: str, target: Any):
    module = c_extract_class._parse_c_module(source)
    masked = c_extract_class._mask_c_like(source)
    calls: list[tuple[int, int, list[str]]] = []
    for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(target.name)}\s*\(", masked):
        position = match.start()
        if target.start <= position < target.end:
            continue
        owner = next((function for function in module.functions if function.open_brace < position < function.end), None)
        if owner is None:
            continue
        open_paren = masked.find("(", position)
        close_paren = c_extract_class._matching(masked, open_paren, "(", ")")
        if close_paren is None:
            return [], "CALL_SITE_ARGUMENTS_UNBALANCED"
        arguments = c_extract_class._split_top_level(source[open_paren + 1:close_paren], ",")
        expected_count = len(_parameter_declarations(target.params_raw))
        if len(arguments) != expected_count:
            return [], "CALL_SITE_ARGUMENT_COUNT_AMBIGUOUS"
        calls.append((open_paren, close_paren, arguments))
    return calls, ""


def _remove_function_parameter(function_source: str, function: Any, index: int) -> str | None:
    relative_open = function.open_brace - function.start
    header = function_source[:relative_open]
    name_match = list(re.finditer(rf"{re.escape(function.name)}\s*\(", header))
    if not name_match:
        return None
    open_paren = header.find("(", name_match[-1].start())
    close_paren = c_extract_class._matching(header, open_paren, "(", ")")
    if close_paren is None:
        return None
    declarations = c_extract_class._split_top_level(header[open_paren + 1:close_paren], ",")
    if not 0 <= index < len(declarations):
        return None
    kept = [item.strip() for item_index, item in enumerate(declarations) if item_index != index]
    parameters = ", ".join(kept) or "void"
    return function_source[:open_paren + 1] + parameters + function_source[close_paren:]


def _parameter_index(parameters: Dict[str, str], name: str) -> int:
    return list(parameters).index(name)


def _unique_identifier(source: str, preferred: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", preferred).strip("_") or "query_value"
    if not re.match(r"[A-Za-z_]", base):
        base = "query_" + base
    candidate = base
    suffix = 2
    masked = c_extract_class._mask_c_like(source)
    while re.search(rf"(?<![A-Za-z0-9_]){re.escape(candidate)}\b", masked):
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _apply_edits(source: str, edits: Sequence[tuple[int, int, str]]) -> str:
    transformed = source
    for start, end, replacement in sorted(edits, reverse=True):
        transformed = transformed[:start] + replacement + transformed[end:]
    return transformed


def _offset_for_line(source: str, line_number: int) -> int:
    return sum(len(line) for line in source.splitlines(keepends=True)[:max(0, line_number - 1)])


def _review(source: str, reason: str, **metadata: Any):
    return source, 0, {"status": "review_required", "reason": reason, **metadata}
