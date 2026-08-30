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
    coordinated_project_callers: Sequence[Dict[str, Any]] | None = None,
    target_parameter_count: int | None = None,
    parameter_types: Sequence[str] | None = None,
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
    if len(target_matches) > 1 and target_parameter_count is not None:
        target_matches = [
            item for item in target_matches
            if len(item[1].parameters) == int(target_parameter_count)
        ]
    if len(target_matches) > 1 and parameter_types:
        expected_types = [re.sub(r"\s+", "", str(item)) for item in parameter_types]
        narrowed: list[tuple[JavaClass, JavaMethod]] = []
        for model, candidate in target_matches:
            span = _method_parameter_span(source_code, candidate)
            parsed = _parse_java_parameters(source_code[span[0]:span[1]]) if span else ""
            if isinstance(parsed, list) and [re.sub(r"\s+", "", item.type_name) for item in parsed] == expected_types:
                narrowed.append((model, candidate))
        target_matches = narrowed
    if len(target_matches) != 1:
        reason = "TARGET_NOT_FOUND" if not target_matches else "AMBIGUOUS_OR_OVERLOADED_TARGET_METHOD"
        return _review(source_code, reason, metadata)
    source_model, target = target_matches[0]
    metadata["source_class"] = source_model.name
    if source_model.nesting_depth and not source_model.is_static:
        return _review(source_code, "NON_STATIC_INNER_CLASS_UNSUPPORTED", metadata)
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
    metadata.update({
        "parameters_moved": [item.name for item in parameters],
        "parameter_types": {item.name: item.type_name for item in parameters},
        "before_parameter_count": len(parameters),
        "after_parameter_count": 1,
    })
    if parameter_name in {item.name for item in parameters}:
        return _review(source_code, "PARAMETER_NAME_COLLISION", metadata)
    if re.search(rf"::\s*{re.escape(method)}\b", _mask_c_like(source_code)):
        return _review(source_code, "METHOD_REFERENCE_CALL_SITE_UNSUPPORTED", metadata)
    call_resolution = resolve_project_call_sites(
        target_source=source_code,
        target_class=source_model.name,
        method=method,
        parameters=parameters,
        project_source_files=project_source_files,
        current_file_name=current_file_name or source_file,
    )
    metadata["cross_file_call_site_resolution"] = call_resolution
    if call_resolution["unresolved"]:
        return _review(source_code, "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT", metadata)
    resolved_callers = call_resolution["resolved"]
    expected_callers = {
        _normalize_path(str(item.get("file_name") or ""))
        for item in coordinated_project_callers or []
        if str(item.get("file_name") or "").strip()
    }
    actual_callers = {
        _normalize_path(str(item.get("file_name") or ""))
        for item in resolved_callers
    }
    if actual_callers and actual_callers != expected_callers:
        metadata["unresolved_external_callers"] = resolved_callers
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
        "call_sites_updated": len(call_spans),
        "coordinated_external_call_sites": len(resolved_callers),
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
        f"\n{indent}public static class {class_name} {{\n"
        f"{fields}\n\n"
        f"{nested}public {class_name}({constructor_params}) {{\n"
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


def resolve_project_call_sites(
    *,
    target_source: str,
    target_class: str,
    method: str,
    parameters: Sequence[JavaParameter],
    project_source_files: Sequence[Any] | None,
    current_file_name: str,
) -> Dict[str, Any]:
    """Resolve external Java invocations of one exact method conservatively.

    This intentionally uses the parser model already used by SCTVA rather than
    a name-only scan.  Same-name declarations and calls on typed unrelated
    receivers are diagnostic *ignored* candidates, never false callers.
    """
    package_match = re.search(r"(?m)^\s*package\s+([A-Za-z_$][\w.$]*)\s*;", _mask_c_like(target_source))
    target_package = package_match.group(1) if package_match else ""
    target_fqn = f"{target_package}.{target_class}" if target_package else target_class
    signature = f"{target_fqn}.{method}({', '.join(item.type_name for item in parameters)})"
    resolved: list[Dict[str, Any]] = []
    ignored: list[Dict[str, Any]] = []
    unresolved: list[Dict[str, Any]] = []

    for item in project_source_files or []:
        file_name = str(item.get("file_name") if isinstance(item, dict) else getattr(item, "file_name", ""))
        source = str(item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", ""))
        if not file_name or _paths_match(file_name, current_file_name):
            continue
        for candidate in _java_method_invocation_candidates(source, method):
            diagnostic = {
                "file_name": file_name,
                "line": candidate["line"],
                "receiver": candidate["receiver"],
                "method": method,
                "argument_count": candidate["argument_count"],
                "target_class": target_fqn,
                "target_signature": signature,
            }
            if candidate["argument_count"] != len(parameters):
                diagnostic["resolution"] = "ignored_arity_mismatch"
                ignored.append(diagnostic)
                continue
            resolution = _resolve_java_invocation_owner(
                source=source,
                candidate=candidate,
                target_class=target_class,
                target_fqn=target_fqn,
                method=method,
            )
            diagnostic.update(resolution)
            if resolution["status"] == "resolved":
                resolved.append(diagnostic)
            elif resolution["status"] == "unresolved":
                unresolved.append(diagnostic)
            else:
                ignored.append(diagnostic)

    return {
        "status": "review_required" if unresolved else "success",
        "target_class": target_fqn,
        "target_signature": signature,
        "resolved": resolved,
        "ignored": ignored,
        "unresolved": unresolved,
    }


def apply_coordinated_call_site_update(
    source_code: str,
    *,
    target_class: str,
    method: str,
    parameter_object_name: str,
    parameter_count: int,
    target_signature: str = "",
) -> tuple[str, int, Dict[str, Any]]:
    """Rewrite every pre-resolved invocation in one external Java file."""
    metadata: Dict[str, Any] = {
        "refactoring": "Introduce Parameter Object - coordinated call site",
        "language": "java",
        "target_class": target_class,
        "method": method,
        "parameter_object_name": parameter_object_name,
        "target_signature": target_signature,
        "reclassified_action_type": "noop",
    }
    if not all(re.fullmatch(_IDENTIFIER, value or "") for value in (target_class, method, parameter_object_name)):
        return _review(source_code, "INVALID_COORDINATED_CALL_SITE_TARGET", metadata)

    target_fqn = target_signature.rsplit(".", 1)[0] if "." in target_signature else target_class
    candidates = _java_method_invocation_candidates(source_code, method)
    edits: list[tuple[int, int, str]] = []
    diagnostics: list[Dict[str, Any]] = []
    for candidate in candidates:
        if candidate["argument_count"] != parameter_count:
            continue
        resolution = _resolve_java_invocation_owner(
            source=source_code,
            candidate=candidate,
            target_class=target_class,
            target_fqn=target_fqn,
            method=method,
        )
        if resolution["status"] == "unresolved":
            diagnostic = {
                "line": candidate["line"],
                "receiver": candidate["receiver"],
                "method": method,
                "target_signature": target_signature,
                **resolution,
            }
            return _review(source_code, "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT", {
                **metadata,
                "call_site_diagnostics": [*diagnostics, diagnostic],
            })
        if resolution["status"] != "resolved":
            continue
        args = source_code[candidate["argument_start"]:candidate["argument_end"]]
        edits.append((
            candidate["argument_start"],
            candidate["argument_end"],
            f"new {target_class}.{parameter_object_name}({args})",
        ))
        diagnostics.append({
            "line": candidate["line"],
            "receiver": candidate["receiver"],
            "resolved_class": resolution.get("resolved_class"),
            "resolution": resolution.get("resolution"),
        })
    if not edits:
        return _review(source_code, "COORDINATED_CALL_SITE_NOT_FOUND", metadata)
    transformed = _apply_edits(source_code, edits)
    metadata.update({
        "status": "success",
        "reason": "coordinated_parameter_object_call_sites_updated",
        "plan_compliance": "PASS",
        "call_sites_updated": len(edits),
        "call_site_diagnostics": diagnostics,
    })
    return transformed, len(edits), metadata


def _java_method_invocation_candidates(source: str, method: str) -> list[Dict[str, Any]]:
    masked = _mask_c_like(source)
    candidates: list[Dict[str, Any]] = []
    for match in re.finditer(rf"\b{re.escape(method)}\s*\(", masked):
        open_index = masked.find("(", match.start(), match.end())
        close_index = _matching(masked, open_index, "(", ")")
        if close_index is None:
            continue
        tail = masked[close_index + 1:]
        # A declaration is followed by a body (or throws + body), not by an
        # expression delimiter.  This also eliminates same-name definitions.
        if re.match(r"\s*(?:throws\s+[\w.$\s,]+)?\s*\{", tail):
            continue
        prefix = masked[:match.start()].rstrip()
        receiver_match = re.search(r"(?P<receiver>(?:[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*|new\s+[A-Za-z_$][\w$.]*\s*\([^)]*\)))\s*\.\s*$", prefix)
        receiver = receiver_match.group("receiver").replace(" ", "") if receiver_match else ""
        args = [part for part in _split_top_level(source[open_index + 1:close_index], ",") if part.strip()]
        candidates.append({
            "method_start": match.start(),
            "argument_start": open_index + 1,
            "argument_end": close_index,
            "argument_count": len(args),
            "receiver": receiver,
            "line": source.count("\n", 0, match.start()) + 1,
        })
    return candidates


def _resolve_java_invocation_owner(
    *,
    source: str,
    candidate: Dict[str, Any],
    target_class: str,
    target_fqn: str,
    method: str,
) -> Dict[str, str]:
    masked = _mask_c_like(source)
    receiver = str(candidate.get("receiver") or "")
    imports = set(re.findall(r"(?m)^\s*import\s+([A-Za-z_$][\w.$]*)\s*;", masked))
    static_imports = set(re.findall(r"(?m)^\s*import\s+static\s+([A-Za-z_$][\w.$]*)\s*;", masked))
    aliases = {target_class, target_fqn}
    if target_fqn in imports:
        aliases.add(target_class)
    type_map = _java_local_type_map(masked)

    if not receiver:
        if f"{target_fqn}.{method}" in static_imports or f"{target_class}.{method}" in static_imports:
            return {"status": "resolved", "resolved_class": target_fqn, "resolution": "static_import"}
        if any(item.endswith(f".{method}") for item in static_imports):
            return {"status": "ignored", "resolved_class": "", "resolution": "static_import_other_class"}
        if _java_source_declares_method(masked, method):
            return {"status": "ignored", "resolved_class": "", "resolution": "local_same_name_method"}
        return {"status": "unresolved", "resolved_class": "", "resolution": "unqualified_call_owner_unknown"}

    if receiver.startswith("new"):
        constructed = re.search(r"new\s+([A-Za-z_$][\w$.]*)", receiver)
        owner = constructed.group(1).split(".")[-1] if constructed else ""
    else:
        receiver_name = receiver.split(".")[-1]
        owner = type_map.get(receiver_name, receiver_name)
        if receiver_name == target_class or receiver == target_fqn:
            return {"status": "resolved", "resolved_class": target_fqn, "resolution": "explicit_target_class_receiver"}
        if receiver_name not in type_map and receiver_name[:1].islower():
            return {"status": "unresolved", "resolved_class": "", "resolution": "receiver_type_unknown"}
    owner_simple = owner.split(".")[-1]
    if owner in aliases or owner_simple == target_class:
        return {"status": "resolved", "resolved_class": target_fqn, "resolution": "receiver_type"}
    if owner:
        return {"status": "ignored", "resolved_class": owner, "resolution": "receiver_resolves_to_other_class"}
    return {"status": "unresolved", "resolved_class": "", "resolution": "receiver_owner_unknown"}


def _java_local_type_map(masked: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_$][\w$.]*(?:\s*<[^;=(){}]+>)?)\s+([A-Za-z_$][\w$]*)\s*(?=[=;,)])", masked):
        type_name = re.sub(r"<.*>", "", match.group(1)).strip()
        result[match.group(2)] = type_name
    return result


def _java_source_declares_method(masked: str, method: str) -> bool:
    return bool(re.search(rf"\b{re.escape(method)}\s*\([^;{{}}]*\)\s*(?:throws\s+[\w.$\s,]+)?\s*\{{", masked))


def _normalize_path(value: str) -> str:
    return "/".join(part for part in str(value).replace("\\", "/").lower().split("/") if part and part != ".")


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
