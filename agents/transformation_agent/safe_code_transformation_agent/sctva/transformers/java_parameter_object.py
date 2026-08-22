"""Java Introduce Parameter Object refactoring using parsed member boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Sequence

from .java_extract_class import (
    JavaClass,
    JavaMethod,
    _mask_c_like,
    _matching,
    _parse_java_class,
    _split_top_level,
    declared_class_names,
)


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"


@dataclass(frozen=True)
class JavaParameter:
    name: str
    type_name: str
    declaration: str


def apply_introduce_parameter_object(
    source_code: str,
    *,
    method: str,
    parameter_object_name: str,
    source_class: str = "",
    source_file: str = "",
    current_file_name: str = "",
    parameter_name: str = "params",
    project_source_files: Sequence[Any] | None = None,
    source_resolution_error: str = "",
) -> tuple[str, int, Dict[str, Any]]:
    metadata: Dict[str, Any] = {
        "refactoring": "Introduce Parameter Object",
        "language": "java",
        "method": method,
        "source_class": source_class,
        "parameter_object_name": parameter_object_name,
        "parameter_name": parameter_name,
        "source_file": source_file or current_file_name,
        "plan_compliance": "FAIL",
    }
    if source_resolution_error:
        return _review(source_code, source_resolution_error, metadata)
    if not re.fullmatch(_IDENTIFIER, method or ""):
        return _review(source_code, "INVALID_METHOD_NAME", metadata)
    if not re.fullmatch(_IDENTIFIER, parameter_object_name or ""):
        return _review(source_code, "INVALID_PARAMETER_OBJECT_NAME", metadata)
    if not re.fullmatch(_IDENTIFIER, parameter_name or ""):
        return _review(source_code, "INVALID_PARAMETER_NAME", metadata)
    if source_file and current_file_name and not _paths_match(source_file, current_file_name):
        return _review(source_code, "SOURCE_FILE_MISMATCH", metadata)
    if parameter_object_name in declared_class_names(source_code):
        return _review(source_code, "PARAMETER_OBJECT_ALREADY_EXISTS_WITH_LONG_SIGNATURE", metadata)

    models = [
        model for class_name in declared_class_names(source_code)
        if (model := _parse_java_class(source_code, class_name)) is not None
    ]
    target_matches = [
        (model, candidate)
        for model in models
        if not source_class or model.name == source_class
        for candidate in model.methods
        if candidate.name == method and not candidate.is_constructor
    ]
    if len(target_matches) != 1:
        reason = "TARGET_NOT_FOUND" if not target_matches else "AMBIGUOUS_OR_OVERLOADED_TARGET_METHOD"
        return _review(source_code, reason, metadata)
    source_model, target = target_matches[0]
    metadata["source_class"] = source_model.name
    if source_model.nesting_depth and not source_model.is_static:
        return _review(source_code, "NON_STATIC_INNER_CLASS_UNSUPPORTED", metadata)
    if any(
        model.name != source_model.name
        and any(candidate.name == method for candidate in model.methods)
        for model in models
    ):
        return _review(source_code, "AMBIGUOUS_SAME_NAME_CALL_TARGET", metadata)

    parameter_span = _method_parameter_span(source_code, target)
    if parameter_span is None:
        return _review(source_code, "METHOD_SIGNATURE_PARSE_FAILED", metadata)
    param_start, param_end = parameter_span
    params_or_error = _parse_java_parameters(source_code[param_start:param_end])
    if isinstance(params_or_error, str):
        return _review(source_code, params_or_error, metadata)
    parameters = params_or_error
    if len(parameters) < 2:
        return _review(source_code, "PARAMETER_COUNT_NOT_REDUCIBLE", metadata)
    if parameter_name in {item.name for item in parameters}:
        return _review(source_code, "PARAMETER_NAME_COLLISION", metadata)
    if re.search(rf"::\s*{re.escape(method)}\b", _mask_c_like(source_code)):
        return _review(source_code, "METHOD_REFERENCE_CALL_SITE_UNSUPPORTED", metadata)
    external_callers = _java_external_callers(
        method,
        project_source_files,
        current_file_name=current_file_name or source_file,
    )
    if external_callers:
        metadata["unresolved_external_callers"] = external_callers
        return _review(source_code, "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT", metadata)

    call_spans_or_error = _java_call_spans(
        source_code,
        method=method,
        parameter_count=len(parameters),
        declaration_span=parameter_span,
    )
    if isinstance(call_spans_or_error, str):
        return _review(source_code, call_spans_or_error, metadata)
    call_spans = call_spans_or_error
    moved_names = {item.name for item in parameters}
    body_start, body_end = target.open_brace + 1, target.end - 1
    edits: list[tuple[int, int, str]] = [
        (param_start, param_end, f"{parameter_object_name} {parameter_name}"),
    ]
    for start, end in call_spans:
        args_text = source_code[start:end]
        if body_start <= start < body_end:
            args_text = _rewrite_java_parameter_references(
                args_text,
                moved_names=moved_names,
                parameter_name=parameter_name,
            )
        edits.append((start, end, f"new {parameter_object_name}({args_text})"))

    protected = [(start, end) for start, end in call_spans]
    for start, end, name in _java_parameter_reference_spans(
        source_code,
        start=body_start,
        end=body_end,
        names=moved_names,
    ):
        if any(outer_start <= start < outer_end for outer_start, outer_end in protected):
            continue
        edits.append((start, end, f"{parameter_name}.{name}"))

    indent = _member_indent(source_code, source_model)
    class_source = _java_parameter_class_source(
        parameter_object_name,
        parameters,
        indent=indent,
    )
    edits.append((source_model.open_brace + 1, source_model.open_brace + 1, class_source))
    transformed = _apply_edits(source_code, edits)
    validation = _validate_java_result(
        transformed,
        method=method,
        source_class=source_model.name,
        parameter_object_name=parameter_object_name,
        parameter_name=parameter_name,
        original_parameters=parameters,
        original_call_count=len(call_spans),
        originally_used_parameters={
            name for name in moved_names
            if re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(name)}\b", _mask_c_like(target.body))
        },
    )
    metadata.update({
        "parameters_moved": [item.name for item in parameters],
        "parameter_types": {item.name: item.type_name for item in parameters},
        "before_parameter_count": len(parameters),
        "after_parameter_count": 1,
        "call_sites_updated": len(call_spans),
        "validation": validation,
    })
    if "FAIL" in validation.values():
        return _review(source_code, "STRUCTURAL_POSTCONDITION_FAILED", metadata)
    metadata.update({"status": "success", "reason": "parameter_object_introduced", "plan_compliance": "PASS"})
    return transformed, 1, metadata


def _method_parameter_span(source: str, method: JavaMethod) -> tuple[int, int] | None:
    masked = _mask_c_like(source)
    header = masked[method.start:method.open_brace]
    matches = list(re.finditer(rf"\b{re.escape(method.name)}\s*\(", header))
    if not matches:
        return None
    open_local = header.find("(", matches[-1].start())
    close_local = _matching(header, open_local, "(", ")")
    if close_local is None:
        return None
    return method.start + open_local + 1, method.start + close_local


def _parse_java_parameters(raw: str) -> list[JavaParameter] | str:
    parameters: list[JavaParameter] = []
    for declaration in _split_top_level(raw, ","):
        declaration = declaration.strip()
        if not declaration:
            continue
        if "..." in declaration:
            return "VARARGS_PARAMETER_UNSUPPORTED"
        cleaned = re.sub(r"@\w+(?:\s*\([^)]*\))?", "", declaration).strip()
        cleaned = re.sub(r"\bfinal\b", "", cleaned).strip()
        match = re.search(rf"({_IDENTIFIER})\s*(\[\s*\])?\s*$", cleaned)
        if not match:
            return "PARAMETER_DECLARATION_UNSUPPORTED"
        name = match.group(1)
        suffix = (match.group(2) or "").replace(" ", "")
        type_name = (cleaned[:match.start()].strip() + suffix).strip()
        if not type_name:
            return "PARAMETER_TYPE_NOT_FOUND"
        parameters.append(JavaParameter(name=name, type_name=type_name, declaration=declaration))
    return parameters


def _java_call_spans(
    source: str,
    *,
    method: str,
    parameter_count: int,
    declaration_span: tuple[int, int],
) -> list[tuple[int, int]] | str:
    masked = _mask_c_like(source)
    spans: list[tuple[int, int]] = []
    for match in re.finditer(rf"\b{re.escape(method)}\s*\(", masked):
        open_index = masked.find("(", match.start(), match.end())
        close_index = _matching(masked, open_index, "(", ")")
        if close_index is None:
            return "UNBALANCED_CALL_SITE"
        arg_start, arg_end = open_index + 1, close_index
        if arg_start == declaration_span[0] and arg_end == declaration_span[1]:
            continue
        args = [item for item in _split_top_level(source[arg_start:arg_end], ",") if item.strip()]
        if len(args) != parameter_count:
            continue
        if any(item.lstrip().startswith(("*", "...")) for item in args):
            return "SPREAD_CALL_SITE_UNSUPPORTED"
        spans.append((arg_start, arg_end))
    return spans


def _java_parameter_reference_spans(
    source: str,
    *,
    start: int,
    end: int,
    names: set[str],
) -> list[tuple[int, int, str]]:
    masked = _mask_c_like(source)
    spans: list[tuple[int, int, str]] = []
    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", masked[start:end]):
            absolute_start = start + match.start()
            previous = masked[:absolute_start].rstrip()[-1:] or ""
            if previous == ".":
                continue
            spans.append((absolute_start, start + match.end(), name))
    return sorted(spans)


def _rewrite_java_parameter_references(
    text: str,
    *,
    moved_names: set[str],
    parameter_name: str,
) -> str:
    edits = _java_parameter_reference_spans(text, start=0, end=len(text), names=moved_names)
    return _apply_edits(text, [(start, end, f"{parameter_name}.{name}") for start, end, name in edits])


def _java_parameter_class_source(
    class_name: str,
    parameters: Sequence[JavaParameter],
    *,
    indent: str,
) -> str:
    nested = indent + "    "
    fields = "\n".join(f"{nested}{item.type_name} {item.name};" for item in parameters)
    constructor_params = ", ".join(f"{item.type_name} {item.name}" for item in parameters)
    assignments = "\n".join(f"{nested}    this.{item.name} = {item.name};" for item in parameters)
    return (
        f"\n{indent}static class {class_name} {{\n"
        f"{fields}\n\n"
        f"{nested}{class_name}({constructor_params}) {{\n"
        f"{assignments}\n"
        f"{nested}}}\n"
        f"{indent}}}\n"
    )


def _member_indent(source: str, model: JavaClass) -> str:
    line_start = source.rfind("\n", 0, model.start) + 1
    class_indent = source[line_start:model.start]
    if class_indent.strip():
        class_indent = ""
    return class_indent + "    "


def _java_external_callers(
    method: str,
    project_source_files: Sequence[Any] | None,
    *,
    current_file_name: str,
) -> list[str]:
    callers: list[str] = []
    pattern = re.compile(rf"(?:\.|\b)\s*{re.escape(method)}\s*\(")
    for item in project_source_files or []:
        file_name = str(item.get("file_name") if isinstance(item, dict) else getattr(item, "file_name", ""))
        if _paths_match(file_name, current_file_name):
            continue
        source = item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", "")
        if pattern.search(_mask_c_like(str(source or ""))):
            callers.append(file_name)
    return sorted(set(callers))


def _validate_java_result(
    transformed: str,
    *,
    method: str,
    source_class: str,
    parameter_object_name: str,
    parameter_name: str,
    original_parameters: Sequence[JavaParameter],
    original_call_count: int,
    originally_used_parameters: set[str],
) -> Dict[str, str]:
    source_model = _parse_java_class(transformed, source_class)
    object_model = _parse_java_class(transformed, parameter_object_name)
    target = None if source_model is None else next(
        (item for item in source_model.methods if item.name == method),
        None,
    )
    expected = {item.name for item in original_parameters}
    body_accesses = set()
    if target is not None:
        body_accesses = {
            match.group(1)
            for match in re.finditer(
                rf"\b{re.escape(parameter_name)}\s*\.\s*({_IDENTIFIER})\b",
                _mask_c_like(target.body),
            )
        }
    old_calls = 0
    object_calls = 0
    if target is not None:
        declaration_span = _method_parameter_span(transformed, target)
        if declaration_span:
            all_old = _java_call_spans(
                transformed,
                method=method,
                parameter_count=len(original_parameters),
                declaration_span=declaration_span,
            )
            old_calls = len(all_old) if isinstance(all_old, list) else 1
        object_calls = len(re.findall(
            rf"\b{re.escape(method)}\s*\(\s*new\s+{re.escape(parameter_object_name)}\s*\(",
            _mask_c_like(transformed),
        ))
    return {
        "syntax": "PASS" if source_model is not None and object_model is not None else "FAIL",
        "parameter_object": "PASS" if object_model is not None and expected <= set(object_model.fields) else "FAIL",
        "signature_reduction": "PASS" if target is not None and target.parameters == [parameter_name] else "FAIL",
        "body_access": "PASS" if originally_used_parameters <= body_accesses else "FAIL",
        "call_sites": "PASS" if old_calls == 0 and object_calls >= original_call_count else "FAIL",
    }


def _apply_edits(source: str, edits: Sequence[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _paths_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a = left.replace("\\", "/").lower().strip()
    b = right.replace("\\", "/").lower().strip()
    return a == b or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def _review(source: str, reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:
    return source, 0, {**metadata, "status": "review_required", "reason": reason}
