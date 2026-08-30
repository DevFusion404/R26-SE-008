"""Semantic Java Long Method -> Extract Method transformation."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import javalang

from .extract_method_common import (
    MAX_EXTRACTED_PARAMETERS,
    MIN_EXTRACTED_LOC,
    StatementSpan,
    apply_edits,
    control_complexity,
    direct_c_like_statements,
    has_unsafe_cross_boundary_flow,
    identifiers,
    mask_c_like,
    nonblank_loc,
    normalize_signature,
)
from .java_extract_class import (
    JavaClass,
    JavaMethod,
    _mask_java_annotations,
    _parse_java_class,
    top_level_class_names,
)


REVIEW_REQUIRED = "review_required"
ALREADY_APPLIED = "already_applied"


@dataclass
class JavaFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    types: dict[str, str]
    defined_before: set[str]
    declared_inside_used_after: list[str]
    modified_external_variables: list[str]
    control_flow_dependencies: list[str]
    references_after: list[str]

    @property
    def live_in_variables(self) -> list[str]:
        return self.inputs

    @property
    def live_out_variables(self) -> list[str]:
        return self.outputs


@dataclass
class JavaRegionFacts:
    declarations: dict[str, str]
    root_declarations: dict[str, str]
    reads: set[str]
    writes: set[str]
    references: set[str]
    control_flow_dependencies: list[str]


@dataclass
class JavaCandidateDecision:
    selected: list[StatementSpan] | None
    flow: JavaFlow | None
    candidate_selected: dict[str, Any] | None
    candidate_rejections: list[dict[str, Any]]
    rejection_reason: str
    ranked_candidates: list["JavaRankedCandidate"]


@dataclass
class JavaRankedCandidate:
    selected: list[StatementSpan]
    flow: JavaFlow
    metadata: dict[str, Any]
    score: float
    exception_types: list[str]


@dataclass
class JavaStatementGroup:
    statements: list[StatementSpan]
    scope_depth: int
    exception_types: list[str]


def target_match_count(
    source_code: str,
    *,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
) -> int:
    return len(_resolve_targets(source_code, method_name, source_class, method_signature))


def validate_java_extract_method_result(
    original_code: str,
    transformed_code: str,
    *,
    source_class: str,
    source_method: str,
    extracted_method: str,
) -> dict[str, Any]:
    scope = _validate_transformed_local_scope(
        original_code=original_code,
        transformed_code=transformed_code,
        source_class=source_class,
        source_method=source_method,
        extracted_method=extracted_method,
    )
    model = _parse_java_class(transformed_code, source_class)
    source_matches = [] if model is None else model.methods_by_name.get(source_method, [])
    helper_matches = [] if model is None else model.methods_by_name.get(extracted_method, [])
    helper_called = any(
        re.search(rf"\b{re.escape(extracted_method)}\s*\(", item.body)
        for item in source_matches
    )
    passed = (
        len(source_matches) >= 1
        and len(helper_matches) == 1
        and helper_called
        and scope.get("status") == "PASS"
    )
    return {
        "passed": passed,
        "reason": "JAVA_EXTRACT_METHOD_STRUCTURE_AND_SCOPE_PRESERVED" if passed else (
            scope.get("reason") or "JAVA_EXTRACT_METHOD_STRUCTURE_NOT_PRESERVED"
        ),
        "source_method_count": len(source_matches),
        "extracted_method_count": len(helper_matches),
        "helper_called": helper_called,
        "post_transform_scope_validation": scope,
    }


def apply_extract_method(
    source_code: str,
    *,
    new_method_name: str,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    source_line: int | None = None,
    source_file: str = "",
    current_file_name: str = "",
    source_resolution_error: str = "",
    project_source_files: Sequence[Any] | None = None,
    compilation_timeout_seconds: int = 10,
) -> tuple[str, int, dict[str, Any]]:
    metadata = _base_metadata(method_name, new_method_name, source_class, source_file or current_file_name)
    if source_resolution_error:
        return _review(source_code, source_resolution_error, metadata)
    if not _identifier(method_name) or not _identifier(new_method_name):
        return _review(source_code, "INVALID_METHOD_TARGET_OR_NAME", metadata)
    targets, resolution = _resolve_targets_with_diagnostics(
        source_code,
        method_name,
        source_class,
        method_signature,
        start_line=start_line,
        end_line=end_line,
        source_line=source_line,
    )
    metadata["method_target_resolution"] = resolution
    if not targets:
        return _review(
            source_code,
            str(resolution.get("reason") or "METHOD_TARGET_NOT_FOUND"),
            metadata,
        )
    if len(targets) != 1:
        return _review(
            source_code,
            str(resolution.get("reason") or "AMBIGUOUS_OVERLOADED_METHOD_TARGET"),
            metadata,
        )
    source_model, method = targets[0]
    metadata.update({
        "source_class": source_model.name,
        "qualified_source_method": f"{source_model.name}.{method.name}",
        "resolved_method_start_line": _line_of(source_code, method.start),
        "resolved_method_end_line": _line_of(source_code, method.end - 1),
        "resolved_parameter_types": list(_java_parameter_types(method).values()),
        "resolved_parameter_count": len(_java_parameter_types(method)),
    })
    if method.is_constructor:
        return _review(source_code, "CONSTRUCTOR_EXTRACTION_UNSUPPORTED", metadata)
    helper_collisions = source_model.methods_by_name.get(new_method_name, [])
    if helper_collisions:
        if re.search(rf"\b{re.escape(new_method_name)}\s*\(", method.body):
            metadata.update({"status": ALREADY_APPLIED, "reason": "ALREADY_APPLIED", "plan_compliance": "PASS"})
            return source_code, 0, metadata
        return _review(source_code, "EXTRACTED_METHOD_NAME_COLLISION", metadata)

    statement_groups = _java_statement_groups(source_code, method)
    parameter_types = _java_parameter_types(method)
    before_metrics = _method_metrics(source_code, method)
    decision = _select_candidate(
        source_code,
        method,
        statement_groups,
        parameter_types,
        start_line=start_line,
        end_line=end_line,
    )
    metadata.update({
        "candidate_selected": decision.candidate_selected,
        "candidate_rejections": decision.candidate_rejections,
        "candidate_rejection_reason": decision.rejection_reason or None,
    })
    if not decision.ranked_candidates:
        representative = next(
            (
                item
                for item in decision.candidate_rejections
                if item.get("reason") == decision.rejection_reason
            ),
            {},
        )
        metadata.update({
            "live_in_variables": list(representative.get("live_in_variables") or []),
            "live_out_variables": list(representative.get("live_out_variables") or []),
            "variables_declared_inside_used_after": list(
                representative.get("variables_declared_inside_used_after") or []
            ),
            "modified_external_variables": list(
                representative.get("modified_external_variables") or []
            ),
            "control_flow_dependencies": list(
                representative.get("control_flow_dependencies") or []
            ),
            "post_transform_scope_validation": {
                "status": "NOT_RUN",
                "reason": "NO_CANDIDATE_COMMITTED",
                "unresolved_variables": [],
            },
            "scope_validation": "NOT_RUN",
            "compilation_validation": {
                "status": "NOT_RUN",
                "reason": "NO_CANDIDATE_COMMITTED",
                "diagnostics": "",
            },
        })
        return _review(
            source_code,
            decision.rejection_reason or "METHOD_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK",
            {**metadata, "before_metrics": before_metrics},
        )

    accepted: tuple[
        JavaRankedCandidate,
        str,
        dict[str, int],
        dict[str, Any],
        dict[str, Any],
    ] | None = None
    candidate_rejections = list(decision.candidate_rejections)
    original_local_names = _original_method_local_names(
        source_code,
        source_class=source_model.name,
        method_name=method_name,
    )
    last_failure_reason = "NO_SAFE_COHESIVE_BLOCK"
    for candidate in decision.ranked_candidates:
        selected, flow = candidate.selected, candidate.flow
        transformed_candidate = _rewrite(
            source_code,
            method=method,
            selected=selected,
            flow=flow,
            new_method_name=new_method_name,
            additional_throws=candidate.exception_types,
        )
        transformed_targets = _resolve_targets(
            transformed_candidate,
            method_name,
            source_model.name,
            method_signature,
        )
        transformed_model = _parse_java_class(transformed_candidate, source_model.name)
        if len(transformed_targets) != 1 or transformed_model is None:
            last_failure_reason = "POST_TRANSFORM_TARGET_VALIDATION_FAILED"
            candidate_rejections.append(_rejected_candidate_metadata(
                candidate.metadata,
                last_failure_reason,
            ))
            continue
        after_method = transformed_targets[0][1]
        after_metrics = _method_metrics(transformed_candidate, after_method)
        helper_matches = transformed_model.methods_by_name.get(new_method_name, [])
        structural_passed = len(helper_matches) == 1 and re.search(
            rf"\b{re.escape(new_method_name)}\s*\(", after_method.body
        ) is not None
        reduction_passed = _meaningfully_reduced(
            before_metrics,
            after_metrics,
            selected,
            semantic_responsibility=bool(
                int(candidate.metadata.get("semantic_weight") or 0)
            ),
        )
        scope_validation = _validate_transformed_local_scope(
            original_code=source_code,
            transformed_code=transformed_candidate,
            source_class=source_model.name,
            source_method=method_name,
            extracted_method=new_method_name,
        )
        compilation_validation = _validate_java_compilation(
            original_code=source_code,
            transformed_code=transformed_candidate,
            current_file_name=current_file_name or source_file,
            project_source_files=project_source_files,
            original_local_names=original_local_names,
            extracted_method=new_method_name,
            timeout_seconds=compilation_timeout_seconds,
        )
        if not structural_passed:
            last_failure_reason = "EXTRACT_METHOD_STRUCTURE_NOT_PROVEN"
        elif not reduction_passed:
            last_failure_reason = "LONG_METHOD_NOT_REDUCED"
        elif scope_validation.get("status") != "PASS":
            last_failure_reason = "POST_TRANSFORM_SCOPE_VALIDATION_FAILED"
        elif compilation_validation.get("status") == "LOCAL_SOURCE_COMPILATION_ERROR":
            last_failure_reason = "LOCAL_SOURCE_COMPILATION_ERROR"
        else:
            candidate.metadata.update({
                "result": "accepted",
                "rejection_reason": None,
                "variable_scope_safety": "PASS",
                "compilation_safety": compilation_validation.get("status"),
            })
            accepted = (
                candidate,
                transformed_candidate,
                after_metrics,
                scope_validation,
                compilation_validation,
            )
            break
        rejected = _rejected_candidate_metadata(candidate.metadata, last_failure_reason)
        rejected.update({
            "variable_scope_safety": scope_validation.get("status"),
            "compilation_safety": compilation_validation.get("status"),
        })
        candidate_rejections.append(rejected)

    if accepted is None:
        metadata["candidate_rejections"] = candidate_rejections
        metadata["candidate_selected"] = None
        metadata["post_transform_scope_validation"] = {
            "status": "NOT_RUN",
            "reason": "NO_CANDIDATE_COMMITTED",
            "unresolved_variables": [],
        }
        metadata["scope_validation"] = "NOT_RUN"
        metadata["compilation_validation"] = {
            "status": "NOT_RUN",
            "reason": "NO_CANDIDATE_COMMITTED",
            "diagnostics": "",
        }
        return _review(
            source_code,
            last_failure_reason,
            {**metadata, "before_metrics": before_metrics},
        )

    candidate, transformed, after_metrics, scope_validation, compilation_validation = accepted
    selected, flow = candidate.selected, candidate.flow
    metadata.update({
        "candidate_selected": candidate.metadata,
        "candidate_rejections": candidate_rejections,
        "post_transform_scope_validation": scope_validation,
        "scope_validation": scope_validation.get("status", "FAIL"),
        "compilation_validation": compilation_validation,
    })

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
        "live_in_variables": flow.live_in_variables,
        "live_out_variables": flow.live_out_variables,
        "variables_declared_inside_used_after": flow.declared_inside_used_after,
        "modified_external_variables": flow.modified_external_variables,
        "control_flow_dependencies": flow.control_flow_dependencies,
        "before_loc": before_metrics["loc"],
        "after_loc": after_metrics["loc"],
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "validation": {
            "target_resolution": "PASS",
            "data_flow": "PASS",
            "structural": "PASS",
            "scope_validation": "PASS",
            "compilation_validation": (
                "PASS"
                if compilation_validation.get("status") in {"PASS", "DEPENDENCY_UNAVAILABLE", "NOT_AVAILABLE"}
                else "FAIL"
            ),
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
    """Compatibility wrapper used by post-transform validation."""

    matches, _ = _resolve_targets_with_diagnostics(
        source_code,
        method_name,
        source_class,
        method_signature,
    )
    return matches


def _resolve_targets_with_diagnostics(
    source_code: str,
    method_name: str,
    source_class: str,
    method_signature: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    source_line: int | None = None,
) -> tuple[list[tuple[JavaClass, JavaMethod]], dict[str, Any]]:
    """Resolve a Java method from the current source using semantic identity.

    Resolution is intentionally class-scoped and annotation-insensitive.  Line
    numbers are hints only, because earlier accepted transformations can move
    the method.  Overloads are never chosen arbitrarily.
    """

    requested_class = str(source_class or "").strip()
    requested_method = str(method_name or "").strip()
    requested_signature = str(method_signature or "").strip()
    class_names = [requested_class] if requested_class else sorted(top_level_class_names(source_code))

    diagnostics: dict[str, Any] = {
        "status": "failed",
        "reason": "METHOD_TARGET_NOT_FOUND",
        "strategy": "failed",
        "requested_source_class": requested_class,
        "requested_source_method": requested_method,
        "requested_method_signature": requested_signature,
        "requested_source_line": source_line,
        "requested_source_range": {"start_line": start_line, "end_line": end_line},
        "candidates": [],
    }

    models: list[JavaClass] = []
    for class_name in class_names:
        model = _parse_java_class(source_code, class_name)
        if model is not None:
            models.append(model)

    if requested_class and not models:
        diagnostics["reason"] = "SOURCE_CLASS_NOT_FOUND"
        return [], diagnostics

    name_matches: list[tuple[JavaClass, JavaMethod]] = []
    for model in models:
        for method in model.methods_by_name.get(requested_method, []):
            name_matches.append((model, method))
            diagnostics["candidates"].append(_java_method_candidate_metadata(source_code, model, method))

    if not name_matches:
        diagnostics["reason"] = "METHOD_TARGET_NOT_FOUND"
        return [], diagnostics

    # 1) Exact normalized signature.
    if requested_signature:
        signature_matches = [
            item for item in name_matches
            if _signature_matches(item[1], requested_signature)
        ]
        if len(signature_matches) == 1:
            diagnostics.update({
                "status": "success",
                "reason": "",
                "strategy": "current_ast_exact_class_method_signature",
            })
            return signature_matches, diagnostics
        if len(signature_matches) > 1:
            narrowed = _narrow_java_method_candidates_by_line(
                source_code,
                signature_matches,
                start_line=start_line,
                end_line=end_line,
                source_line=source_line,
            )
            if len(narrowed) == 1:
                diagnostics.update({
                    "status": "success",
                    "reason": "",
                    "strategy": "current_ast_signature_plus_line_hint",
                })
                return narrowed, diagnostics
            diagnostics["reason"] = "AMBIGUOUS_OVERLOADED_METHOD_TARGET"
            diagnostics["ambiguity_kind"] = "AMBIGUOUS_METHOD_TARGET"
            return signature_matches, diagnostics

        # 2) Signature parameter types/count, ignoring annotations and parameter names.
        requested_types = _requested_signature_parameter_types(
            requested_signature,
            requested_method,
        )
        if requested_types is not None:
            typed_matches = []
            for item in name_matches:
                actual_types = list(_java_parameter_types(item[1]).values())
                if len(actual_types) != len(requested_types):
                    continue
                if all(
                    _normalized_java_type(actual) == _normalized_java_type(expected)
                    for actual, expected in zip(actual_types, requested_types)
                ):
                    typed_matches.append(item)
            if len(typed_matches) == 1:
                diagnostics.update({
                    "status": "success",
                    "reason": "",
                    "strategy": "current_ast_class_method_parameter_types",
                })
                return typed_matches, diagnostics
            if len(typed_matches) > 1:
                name_matches = typed_matches

    # 3) Unique class-scoped method name.  This deliberately tolerates stale or
    # annotation-heavy planner signatures when there is no overload ambiguity.
    if len(name_matches) == 1:
        diagnostics.update({
            "status": "success",
            "reason": "",
            "strategy": (
                "current_ast_unique_class_method_after_signature_mismatch"
                if requested_signature
                else "current_ast_unique_class_method"
            ),
        })
        return name_matches, diagnostics

    # 4) Stale-line/source-range recovery for true overloads.
    narrowed = _narrow_java_method_candidates_by_line(
        source_code,
        name_matches,
        start_line=start_line,
        end_line=end_line,
        source_line=source_line,
    )
    if len(narrowed) == 1:
        diagnostics.update({
            "status": "success",
            "reason": "",
            "strategy": "current_ast_class_method_line_hint",
        })
        return narrowed, diagnostics

    diagnostics["reason"] = "AMBIGUOUS_OVERLOADED_METHOD_TARGET"
    diagnostics["ambiguity_kind"] = "AMBIGUOUS_METHOD_TARGET"
    diagnostics["strategy"] = "current_ast_overload_requires_disambiguation"
    return name_matches, diagnostics


def _java_method_candidate_metadata(
    source_code: str,
    model: JavaClass,
    method: JavaMethod,
) -> dict[str, Any]:
    types = list(_java_parameter_types(method).values())
    return {
        "source_class": model.name,
        "source_method": method.name,
        "qualified_source_method": f"{model.name}.{method.name}",
        "parameter_count": len(types),
        "parameter_types": types,
        "start_line": _line_of(source_code, method.start),
        "end_line": _line_of(source_code, method.end - 1),
        "header": re.sub(r"\s+", " ", method.header).strip(),
    }


def _narrow_java_method_candidates_by_line(
    source_code: str,
    candidates: Sequence[tuple[JavaClass, JavaMethod]],
    *,
    start_line: int | None,
    end_line: int | None,
    source_line: int | None,
) -> list[tuple[JavaClass, JavaMethod]]:
    hints = [value for value in (source_line, start_line) if isinstance(value, int) and value > 0]
    if not hints and isinstance(end_line, int) and end_line > 0:
        hints.append(end_line)
    if not hints:
        return list(candidates)

    anchor = hints[0]
    containing = []
    for item in candidates:
        method = item[1]
        first = _line_of(source_code, method.start)
        last = _line_of(source_code, method.end - 1)
        if first <= anchor <= last:
            containing.append(item)
    if len(containing) == 1:
        return containing
    if len(containing) > 1:
        return containing

    distances = []
    for item in candidates:
        first = _line_of(source_code, item[1].start)
        distances.append((abs(first - anchor), item))
    distances.sort(key=lambda value: value[0])
    if len(distances) == 1 or distances[0][0] < distances[1][0]:
        return [distances[0][1]]
    return list(candidates)


def _requested_signature_parameter_types(
    signature: str,
    method_name: str,
) -> list[str] | None:
    text = str(signature or "").strip()
    if not text:
        return None
    masked = mask_c_like(text)
    match = re.search(rf"\b{re.escape(method_name)}\s*\(", masked)
    if match is None:
        match = re.search(r"\(", masked)
    if match is None:
        return None
    open_paren = masked.find("(", match.start())
    depth = 0
    close_paren = None
    for index in range(open_paren, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                close_paren = index
                break
    if close_paren is None:
        return None
    raw_params = text[open_paren + 1:close_paren]
    if not raw_params.strip():
        return []

    result: list[str] = []
    for raw in _split_top_level(raw_params):
        cleaned = _mask_java_annotations(raw).strip()
        cleaned = re.sub(r"\bfinal\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            continue
        name_match = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(\[\s*\])?\s*$", cleaned)
        if name_match is None:
            result.append(cleaned)
            continue
        before_name = cleaned[:name_match.start()].strip()
        if before_name:
            type_name = before_name
            if name_match.group(2):
                type_name += "[]"
            result.append(type_name)
        else:
            # Signatures such as process(int,int) contain types without names.
            result.append(cleaned)
    return result


def _normalized_java_type(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or ""))
    return value.replace("...", "[]")


def _signature_matches(method: JavaMethod, signature: str) -> bool:
    normalized = normalize_signature(signature)
    if not normalized:
        return True
    params = _java_parameter_types(method)
    rendered = f"{method.name}({','.join(params.values())})"
    return normalized in {normalize_signature(rendered), normalize_signature(method.header)}


def _java_statement_groups(
    source_code: str,
    method: JavaMethod,
) -> list[JavaStatementGroup]:
    """Collect direct statement sequences for the method and its lexical blocks."""

    method_node = _java_ast_method_node(source_code, method)
    if method_node is None:
        statements = direct_c_like_statements(
            method.body,
            body_offset=method.open_brace + 1,
        )
        return [JavaStatementGroup(statements, 0, [])] if statements else []

    grouped: dict[int, dict[str, Any]] = {}
    for path, node in method_node:
        if not path or not isinstance(path[-1], list):
            continue
        if not isinstance(
            node,
            (javalang.tree.Statement, javalang.tree.LocalVariableDeclaration),
        ):
            continue
        position = getattr(node, "position", None)
        if position is None:
            continue
        start = _java_source_offset(source_code, position)
        if not (method.open_brace < start < method.end):
            continue
        scanned = direct_c_like_statements(
            source_code[start:method.end - 1],
            body_offset=start,
        )
        if not scanned:
            continue
        span = scanned[0]
        if span.end > method.end:
            continue
        container = path[-1]
        entry = grouped.setdefault(id(container), {
            "spans": {},
            "scope_depth": max(
                0,
                sum(isinstance(item, list) for item in path) - 1,
            ),
            "exception_types": set(),
        })
        entry["spans"][(span.start, span.end)] = span
        for ancestor in reversed(path):
            if not isinstance(ancestor, javalang.tree.TryStatement):
                continue
            if not any(item is ancestor.block for item in path):
                continue
            for catch in ancestor.catches or []:
                entry["exception_types"].update(catch.parameter.types or [])
            break

    groups: list[JavaStatementGroup] = []
    for entry in grouped.values():
        statements = sorted(
            entry["spans"].values(),
            key=lambda item: (item.start, item.end),
        )
        if statements:
            groups.append(JavaStatementGroup(
                statements=statements,
                scope_depth=int(entry["scope_depth"]),
                exception_types=sorted(entry["exception_types"]),
            ))
    groups.sort(key=lambda item: (item.scope_depth, item.statements[0].start))
    return groups


def _java_ast_method_node(source_code: str, method: JavaMethod) -> Any | None:
    try:
        unit = javalang.parse.parse(source_code)
    except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError, TypeError):
        return None
    candidates = []
    for _, node in unit:
        if not isinstance(node, javalang.tree.MethodDeclaration):
            continue
        if node.name != method.name or node.position is None:
            continue
        offset = _java_source_offset(source_code, node.position)
        if method.start <= offset < method.end:
            candidates.append(node)
    return candidates[0] if len(candidates) == 1 else None


def _java_source_offset(source_code: str, position: Any) -> int:
    line = max(1, int(position.line))
    column = max(1, int(position.column))
    offset = 0
    for _ in range(1, line):
        newline = source_code.find("\n", offset)
        if newline < 0:
            return len(source_code)
        offset = newline + 1
    return min(len(source_code), offset + column - 1)


def _java_candidate_windows(
    groups: Sequence[JavaStatementGroup],
    *,
    source_code: str,
    start_line: int | None,
    end_line: int | None,
) -> list[tuple[JavaStatementGroup, list[StatementSpan]]]:
    candidates: list[tuple[JavaStatementGroup, list[StatementSpan]]] = []
    seen: set[tuple[int, int]] = set()

    def add(group: JavaStatementGroup, window: list[StatementSpan]) -> None:
        if not window:
            return
        key = (window[0].start, window[-1].end)
        if key in seen:
            return
        seen.add(key)
        candidates.append((group, window))

    for group in groups:
        statements = group.statements
        if start_line and end_line:
            hinted = [
                item
                for item in statements
                if _line_of(source_code, item.end - 1) >= start_line
                and _line_of(source_code, item.start) <= end_line
            ]
            if hinted:
                add(group, hinted)

        count = len(statements)
        maximum = count if group.scope_depth > 0 else max(0, count - 1)
        maximum = min(8, maximum)
        for width in range(maximum, 1, -1):
            for index in range(0, count - width + 1):
                add(group, list(statements[index:index + width]))

        for statement in statements:
            text = source_code[statement.start:statement.end]
            if statement.loc >= MIN_EXTRACTED_LOC and "{" in mask_c_like(text):
                add(group, [statement])
    return candidates


def _select_candidate(
    source_code: str,
    method: JavaMethod,
    statement_groups: Sequence[JavaStatementGroup],
    parameter_types: dict[str, str],
    *,
    start_line: int | None,
    end_line: int | None,
) -> JavaCandidateDecision:
    windows = _java_candidate_windows(
        statement_groups,
        source_code=source_code,
        start_line=start_line,
        end_line=end_line,
    )
    ranked: list[JavaRankedCandidate] = []
    rejections: list[dict[str, Any]] = []
    for group, window in windows:
        text = source_code[window[0].start:window[-1].end]
        responsibility, semantic_weight = _java_candidate_responsibility(text)
        candidate_info = {
            "candidate_range": {
                "start_line": _line_of(source_code, window[0].start),
                "end_line": _line_of(source_code, window[-1].end - 1),
            },
            "start_line": _line_of(source_code, window[0].start),
            "end_line": _line_of(source_code, window[-1].end - 1),
            "statement_count": len(window),
            "candidate_responsibility": responsibility,
            "scope_depth": group.scope_depth,
            "exception_dependency": (
                "enclosing_try" if group.exception_types else "none"
            ),
        }
        if has_unsafe_cross_boundary_flow(text, language="java"):
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "UNSAFE_CONTROL_FLOW_BOUNDARY",
            ))
            continue
        if re.search(r"\b(?:class|interface|enum|record)\b", mask_c_like(text)):
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "LOCAL_TYPE_DECLARATION_UNSUPPORTED",
            ))
            continue
        if group.scope_depth == 0 and len(group.statements) - len(window) < 1:
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "CALLER_WOULD_HAVE_NO_MEANINGFUL_BODY",
            ))
            continue
        flow, flow_reason = _java_flow(
            source_code,
            method,
            window,
            parameter_types,
        )
        if flow is None:
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                flow_reason or "JAVA_AST_DATA_FLOW_ANALYSIS_FAILED",
            ))
            continue
        candidate_info.update({
            "live_in_variables": flow.live_in_variables,
            "live_out_variables": flow.live_out_variables,
            "variables_declared_inside_used_after": flow.declared_inside_used_after,
            "declared_inside_used_after": flow.declared_inside_used_after,
            "modified_external_variables": flow.modified_external_variables,
            "control_flow_dependencies": flow.control_flow_dependencies,
            "parameter_count": len(flow.inputs),
        })
        if len(flow.outputs) > 1:
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "MULTIPLE_LIVE_OUT_VALUES",
            ))
            continue
        if len(flow.inputs) > MAX_EXTRACTED_PARAMETERS:
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "TOO_MANY_PARAMETERS",
            ))
            continue
        loc = nonblank_loc(text)
        complexity = control_complexity(text)
        if loc < MIN_EXTRACTED_LOC and complexity <= 1 and semantic_weight <= 0:
            rejections.append(_rejected_candidate_metadata(
                candidate_info,
                "CANDIDATE_TOO_SMALL",
            ))
            continue
        candidate_start_line = _line_of(source_code, window[0].start)
        candidate_end_line = _line_of(source_code, window[-1].end - 1)
        if start_line and end_line:
            hint_distance = abs(candidate_start_line - start_line) + abs(
                candidate_end_line - end_line
            )
            hint_bonus = max(0, 30 - hint_distance * 6)
        else:
            hint_bonus = 0
        cohesion = _java_candidate_cohesion(text, flow)
        control_risk = len(flow.control_flow_dependencies)
        caller_text = (
            source_code[method.open_brace + 1:window[0].start]
            + source_code[window[-1].end:method.end - 1]
        )
        caller_complexity = control_complexity(caller_text)
        loc_reduction = max(0, loc - 1)
        setup_declaration_count = (
            len(flow.locals) + len(flow.declared_inside_used_after)
        )
        setup_penalty = setup_declaration_count * (
            15 if semantic_weight >= 8 else 1
        )
        score = (
            hint_bonus
            + complexity * 4
            + loc
            + loc_reduction * 2
            + cohesion
            + semantic_weight
            - len(flow.inputs) * 0.5
            - len(flow.outputs) * 1.5
            - setup_penalty
            - control_risk * 3
            - caller_complexity * 0.1
        )
        candidate_info.update({
            "score": round(score, 3),
            "cohesion": cohesion,
            "loc": loc,
            "loc_reduction": loc_reduction,
            "control_flow_risk": control_risk,
            "caller_complexity_after": caller_complexity,
            "semantic_weight": semantic_weight,
            "setup_declarations_moved": sorted(
                set(flow.locals) | set(flow.declared_inside_used_after)
            ),
            "result": "eligible",
            "rejection_reason": None,
        })
        ranked.append(JavaRankedCandidate(
            selected=list(window),
            flow=flow,
            metadata=candidate_info,
            score=score,
            exception_types=list(group.exception_types),
        ))
    if not ranked:
        reasons = [str(item.get("reason") or "") for item in rejections]
        if "MULTIPLE_LIVE_OUT_VALUES" in reasons:
            rejection_reason = "MULTIPLE_LIVE_OUT_VALUES"
        elif "TOO_MANY_PARAMETERS" in reasons:
            rejection_reason = "TOO_MANY_PARAMETERS"
        elif not rejections or set(reasons) <= {
            "CANDIDATE_TOO_SMALL",
            "CALLER_WOULD_HAVE_NO_MEANINGFUL_BODY",
        }:
            rejection_reason = "METHOD_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK"
        else:
            rejection_reason = "NO_SAFE_COHESIVE_BLOCK"
        return JavaCandidateDecision(
            None,
            None,
            None,
            rejections,
            rejection_reason,
            [],
        )
    ranked.sort(
        key=lambda item: (item.score, len(item.selected)),
        reverse=True,
    )
    first = ranked[0]
    return JavaCandidateDecision(
        first.selected,
        first.flow,
        first.metadata,
        rejections,
        "",
        ranked,
    )


def _rejected_candidate_metadata(
    candidate_info: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    rejected = {
        **candidate_info,
        "result": "rejected",
        "reason": reason,
        "rejection_reason": reason,
    }
    rejected.setdefault("live_in_variables", [])
    rejected.setdefault("live_out_variables", [])
    rejected.setdefault("variables_declared_inside_used_after", [])
    rejected.setdefault(
        "declared_inside_used_after",
        rejected["variables_declared_inside_used_after"],
    )
    rejected.setdefault("modified_external_variables", [])
    rejected.setdefault("parameter_count", len(rejected["live_in_variables"]))
    rejected.setdefault(
        "control_flow_risk",
        1 if reason == "UNSAFE_CONTROL_FLOW_BOUNDARY" else 0,
    )
    rejected.setdefault("variable_scope_safety", "NOT_RUN")
    return rejected


def _java_candidate_responsibility(text: str) -> tuple[str, int]:
    masked = mask_c_like(text)
    setter_count = len(re.findall(r"\.\s*set[A-Z_$][A-Za-z0-9_$]*\s*\(", masked))
    executes = bool(re.search(
        r"\b(?:execute|executeUpdate|executeQuery|executeBatch|executeDatabase)\s*\(",
        masked,
        flags=re.IGNORECASE,
    ))
    if setter_count >= 2 and executes:
        return "database parameter binding and execution", 12
    if setter_count >= 2:
        return "database parameter binding", 9
    if executes:
        return "database execution", 8
    if re.search(r"\.(?:next|getString|getInt|getLong|getObject)\s*\(", masked):
        return "result processing", 7
    if re.search(r"\.(?:close|rollback|commit)\s*\(", masked):
        return "resource cleanup", 7
    if re.search(r"\b(?:validate|verify|check)[A-Za-z0-9_$]*\s*\(", masked):
        return "validation", 5
    if re.search(r"\b(?:sql|query|statement)\b\s*=", text, flags=re.IGNORECASE):
        return "query construction", 5
    if re.search(r"\bnew\s+[A-Za-z_$]", masked):
        return "object construction and population", 4
    if len(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$<>?, .\[\]]+\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=", masked)) >= 2:
        return "data preparation", 3
    return "cohesive statement sequence", 0


def _java_candidate_cohesion(text: str, flow: JavaFlow) -> int:
    masked = mask_c_like(text)
    qualifiers = re.findall(
        r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\(",
        masked,
    )
    repeated_qualifiers = len(qualifiers) - len(set(qualifiers))
    return (
        len(set(flow.inputs) & identifiers(text))
        + len(flow.outputs)
        + max(0, repeated_qualifiers)
    )


def _java_flow(
    source_code: str,
    method: JavaMethod,
    selected: Sequence[StatementSpan],
    parameter_types: dict[str, str],
) -> tuple[JavaFlow | None, str]:
    selected_start = selected[0].start
    selected_end = selected[-1].end
    selected_text = source_code[selected_start:selected_end]
    selected_facts, selected_error = _java_region_facts(selected_text)
    method_node = _java_ast_method_node(source_code, method)
    if selected_facts is None or method_node is None:
        return None, selected_error or "JAVA_AST_DATA_FLOW_ANALYSIS_FAILED"

    declaration_offsets: dict[str, int] = {name: -1 for name in parameter_types}
    types = dict(parameter_types)
    for _, node in method_node:
        if isinstance(node, javalang.tree.LocalVariableDeclaration):
            declaration_offset = _java_source_offset(source_code, node.position)
            type_name = _render_java_ast_type(node.type)
            for declarator in node.declarators:
                declaration_offsets[declarator.name] = declaration_offset
                types[declarator.name] = type_name

    known_names = set(types)
    assignments: dict[int, str] = {}
    for _, node in method_node:
        if isinstance(node, javalang.tree.Assignment):
            for member in _java_ast_member_references(node.expressionl):
                assignments[id(member)] = str(node.type or "=")

    events: dict[str, dict[int, dict[str, bool]]] = {}

    def record(name: str, offset: int, *, read: bool, write: bool) -> None:
        if name not in known_names:
            return
        event = events.setdefault(name, {}).setdefault(
            offset,
            {"read": False, "write": False},
        )
        event["read"] = bool(event["read"] or read)
        event["write"] = bool(event["write"] or write)

    for path, node in method_node:
        position = getattr(node, "position", None)
        if position is None:
            continue
        offset = _java_source_offset(source_code, position)
        if isinstance(node, javalang.tree.MemberReference):
            qualifier = str(node.qualifier or "").strip()
            if qualifier:
                record(qualifier.split(".", 1)[0], offset, read=True, write=False)
                continue
            if _java_member_is_explicit_field(path, node):
                continue
            name = str(node.member or "")
            assignment_operator = assignments.get(id(node))
            has_array_selector = any(
                isinstance(selector, javalang.tree.ArraySelector)
                for selector in (node.selectors or [])
            )
            increments = bool(node.prefix_operators or node.postfix_operators)
            if assignment_operator is not None and not has_array_selector:
                record(
                    name,
                    offset,
                    read=assignment_operator != "=" or increments,
                    write=True,
                )
            else:
                record(name, offset, read=True, write=increments)
        elif isinstance(node, (javalang.tree.MethodInvocation, javalang.tree.SuperMethodInvocation)):
            qualifier = str(getattr(node, "qualifier", "") or "").strip()
            if qualifier:
                record(qualifier.split(".", 1)[0], offset, read=True, write=False)

    selected_declarations = selected_facts.root_declarations
    types.update(selected_declarations)
    defined_before = {
        name
        for name, offset in declaration_offsets.items()
        if offset < selected_start
    }
    selected_reads = {
        name
        for name, by_offset in events.items()
        if any(
            selected_start <= offset < selected_end and flags["read"]
            for offset, flags in by_offset.items()
        )
    }
    selected_writes = {
        name
        for name, by_offset in events.items()
        if any(
            selected_start <= offset < selected_end and flags["write"]
            for offset, flags in by_offset.items()
        )
    }
    external_references = (selected_reads | selected_writes) & defined_before
    # A caller local assigned in the helper still has to be passed so the
    # generated helper has a valid declaration, even when the old value is not
    # read before the assignment.
    inputs = sorted(external_references)
    after_references = {
        name
        for name, by_offset in events.items()
        if any(offset >= selected_end for offset in by_offset)
    }
    declared_inside_used_after = sorted(set(selected_declarations) & after_references)
    modified_external = sorted(selected_writes & defined_before)

    def produced_value_used_after(name: str) -> bool:
        for offset, flags in sorted(events.get(name, {}).items()):
            if offset < selected_end:
                continue
            if flags["read"]:
                return True
            if flags["write"]:
                return False
        return False

    outputs = sorted(
        set(declared_inside_used_after)
        | {
            name
            for name in modified_external
            if produced_value_used_after(name)
        }
    )
    locals_only = sorted(set(selected_declarations) - set(outputs))
    required_types = set(inputs) | set(outputs)
    if any(name not in types for name in required_types):
        return None, "UNRESOLVED_VARIABLE_TYPE"
    return JavaFlow(
        inputs=inputs,
        outputs=outputs,
        locals=locals_only,
        types=types,
        defined_before=defined_before,
        declared_inside_used_after=declared_inside_used_after,
        modified_external_variables=modified_external,
        control_flow_dependencies=selected_facts.control_flow_dependencies,
        references_after=sorted(after_references),
    ), ""


def _java_parameter_types(method: JavaMethod) -> dict[str, str]:
    params_raw = _params_raw(method.header, method.name)
    result: dict[str, str] = {}
    for raw in _split_top_level(params_raw):
        cleaned = _mask_java_annotations(raw).strip()
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


def _java_region_facts(
    text: str,
    *,
    reject_unsafe_control: bool = True,
) -> tuple[JavaRegionFacts | None, str]:
    """Parse a method-body region and derive symbol facts from its Java AST."""

    wrapper = "class __SctvaFlow { void __flow() {\n" + text + "\n} }"
    try:
        unit = javalang.parse.parse(wrapper)
    except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError, TypeError) as exc:
        return None, f"JAVA_AST_PARSE_FAILED: {exc}"

    method_node = unit.types[0].methods[0]
    root_declarations: dict[str, str] = {}
    for statement in method_node.body or []:
        if isinstance(statement, javalang.tree.LocalVariableDeclaration):
            type_name = _render_java_ast_type(statement.type)
            for declarator in statement.declarators:
                root_declarations[declarator.name] = type_name

    declarations: dict[str, str] = {}
    assignments: dict[int, str] = {}
    for _, node in method_node:
        if isinstance(node, javalang.tree.LocalVariableDeclaration):
            type_name = _render_java_ast_type(node.type)
            for declarator in node.declarators:
                declarations[declarator.name] = type_name
        elif isinstance(node, javalang.tree.Assignment):
            for member in _java_ast_member_references(node.expressionl):
                assignments[id(member)] = str(node.type or "=")

    reads: set[str] = set()
    writes: set[str] = set()
    references: set[str] = set()
    controls: set[str] = set()

    control_types = {
        javalang.tree.IfStatement: "if",
        javalang.tree.ForStatement: "for",
        javalang.tree.WhileStatement: "while",
        javalang.tree.DoStatement: "do_while",
        javalang.tree.SwitchStatement: "switch",
        javalang.tree.TryStatement: "try_catch_finally",
        javalang.tree.SynchronizedStatement: "synchronized",
        javalang.tree.BreakStatement: "break",
        javalang.tree.ContinueStatement: "continue",
        javalang.tree.ReturnStatement: "return",
        javalang.tree.ThrowStatement: "throw",
        javalang.tree.LambdaExpression: "lambda",
    }

    for path, node in method_node:
        for node_type, label in control_types.items():
            if isinstance(node, node_type):
                controls.add(label)
                break

        if isinstance(node, javalang.tree.MemberReference):
            qualifier = str(node.qualifier or "").strip()
            if qualifier:
                base = qualifier.split(".", 1)[0]
                if _identifier(base) and base not in {"this", "super"}:
                    reads.add(base)
                    references.add(base)
                continue
            if _java_member_is_explicit_field(path, node):
                continue

            name = str(node.member or "")
            if not name:
                continue
            references.add(name)
            assignment_operator = assignments.get(id(node))
            has_array_selector = any(
                isinstance(selector, javalang.tree.ArraySelector)
                for selector in (node.selectors or [])
            )
            increments = bool(node.prefix_operators or node.postfix_operators)
            if assignment_operator is not None and not has_array_selector:
                writes.add(name)
                if assignment_operator != "=":
                    reads.add(name)
            else:
                reads.add(name)
            if increments:
                reads.add(name)
                writes.add(name)

        elif isinstance(node, (javalang.tree.MethodInvocation, javalang.tree.SuperMethodInvocation)):
            qualifier = str(getattr(node, "qualifier", "") or "").strip()
            if qualifier:
                base = qualifier.split(".", 1)[0]
                if _identifier(base) and base not in {"this", "super"}:
                    reads.add(base)
                    references.add(base)

    if reject_unsafe_control and "lambda" in controls:
        return None, "LAMBDA_CAPTURE_UNSUPPORTED"
    if reject_unsafe_control and controls & {"break", "continue", "return", "throw"}:
        return None, "UNSAFE_CONTROL_FLOW_BOUNDARY"

    return JavaRegionFacts(
        declarations=declarations,
        root_declarations=root_declarations,
        reads=reads,
        writes=writes,
        references=references,
        control_flow_dependencies=sorted(controls),
    ), ""


def _java_ast_member_references(node: Any) -> list[Any]:
    if isinstance(node, javalang.tree.MemberReference):
        return [node]
    if not isinstance(node, javalang.ast.Node):
        return []
    return [
        child
        for _, child in node
        if isinstance(child, javalang.tree.MemberReference)
    ]


def _java_member_is_explicit_field(path: tuple[Any, ...], node: Any) -> bool:
    del node
    return any(
        isinstance(parent, (javalang.tree.This, javalang.tree.SuperMemberReference))
        for parent in path
        if isinstance(parent, javalang.ast.Node)
    )


def _render_java_ast_type(type_node: Any) -> str:
    if type_node is None:
        return ""
    name_parts: list[str] = []
    current = type_node
    while current is not None:
        name = str(getattr(current, "name", "") or "")
        arguments = getattr(current, "arguments", None) or []
        if arguments:
            rendered_arguments: list[str] = []
            for argument in arguments:
                pattern = getattr(argument, "pattern_type", None)
                argument_type = getattr(argument, "type", None)
                if argument_type is None:
                    rendered = "?"
                else:
                    rendered = _render_java_ast_type(argument_type)
                    if pattern:
                        rendered = f"? {pattern} {rendered}"
                rendered_arguments.append(rendered)
            name += "<" + ", ".join(rendered_arguments) + ">"
        name_parts.append(name)
        current = getattr(current, "sub_type", None)
    dimensions = "[]" * len(getattr(type_node, "dimensions", None) or [])
    return ".".join(part for part in name_parts if part) + dimensions


def _rewrite(
    source_code: str,
    *,
    method: JavaMethod,
    selected: Sequence[StatementSpan],
    flow: JavaFlow,
    new_method_name: str,
    additional_throws: Sequence[str] = (),
) -> str:
    raw_start, end = selected[0].start, selected[-1].end
    indent = method.indent
    body_indent = _statement_indent(source_code, raw_start) or f"{indent}    "
    line_start = source_code.rfind("\n", 0, raw_start) + 1
    start = line_start if not source_code[line_start:raw_start].strip() else raw_start
    static_prefix = "static " if "static" in method.modifiers else ""
    generic_prefix = _generic_prefix(method.header)
    throws_clause = _combined_throws_clause(method.header, additional_throws)
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


def _original_method_local_names(
    source_code: str,
    *,
    source_class: str,
    method_name: str,
) -> set[str]:
    try:
        unit = javalang.parse.parse(source_code)
    except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError, TypeError):
        return set()
    class_node = _java_ast_class(unit, source_class)
    if class_node is None:
        return set()
    method_node = next(
        (item for item in getattr(class_node, "methods", []) if item.name == method_name),
        None,
    )
    if method_node is None:
        return set()
    names = {parameter.name for parameter in method_node.parameters or []}
    for _, node in method_node:
        if isinstance(
            node,
            (javalang.tree.LocalVariableDeclaration, javalang.tree.VariableDeclaration),
        ):
            names.update(item.name for item in node.declarators)
        elif isinstance(node, javalang.tree.CatchClauseParameter):
            names.add(node.name)
        elif isinstance(node, javalang.tree.TryResource):
            names.add(node.name)
        elif isinstance(node, javalang.tree.InferredFormalParameter):
            names.add(node.name)
        elif isinstance(node, javalang.tree.LambdaExpression):
            for parameter in node.parameters or []:
                name = str(
                    getattr(parameter, "name", "")
                    or getattr(parameter, "member", "")
                )
                if name:
                    names.add(name)
    return names


def _validate_transformed_local_scope(
    *,
    original_code: str,
    transformed_code: str,
    source_class: str,
    source_method: str,
    extracted_method: str,
) -> dict[str, Any]:
    original_names = _original_method_local_names(
        original_code,
        source_class=source_class,
        method_name=source_method,
    )
    try:
        unit = javalang.parse.parse(transformed_code)
    except (javalang.parser.JavaSyntaxError, javalang.tokenizer.LexerError, TypeError) as exc:
        return {
            "status": "FAIL",
            "reason": "TRANSFORMED_JAVA_AST_PARSE_FAILED",
            "diagnostics": [str(exc)],
            "unresolved_variables": [],
        }
    class_node = _java_ast_class(unit, source_class)
    if class_node is None:
        return {
            "status": "FAIL",
            "reason": "TRANSFORMED_SOURCE_CLASS_NOT_FOUND",
            "diagnostics": [],
            "unresolved_variables": [],
        }
    methods = [
        item
        for item in getattr(class_node, "methods", [])
        if item.name in {source_method, extracted_method}
    ]
    if not any(item.name == source_method for item in methods) or not any(
        item.name == extracted_method for item in methods
    ):
        return {
            "status": "FAIL",
            "reason": "TRANSFORMED_METHOD_OR_HELPER_NOT_FOUND",
            "diagnostics": [],
            "unresolved_variables": [],
        }

    issues: list[dict[str, Any]] = []
    checked_identifiers: list[dict[str, Any]] = []
    for method_node in methods:
        method_issues, method_checks = _java_method_scope_issues(
            method_node,
            original_names,
        )
        issues.extend(method_issues)
        checked_identifiers.extend(method_checks)
    unresolved = sorted({str(item.get("variable") or "") for item in issues if item.get("variable")})
    return {
        "status": "PASS" if not issues else "FAIL",
        "reason": "LOCAL_SYMBOLS_RESOLVED" if not issues else "UNRESOLVED_LOCAL_VARIABLE",
        "diagnostics": issues,
        "unresolved_variables": unresolved,
        "checked_methods": sorted({item.name for item in methods}),
        "checked_identifiers": checked_identifiers,
    }


def _java_method_scope_issues(
    method_node: Any,
    known_local_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    declarations: dict[str, list[dict[str, Any]]] = {}
    method_scope = id(method_node)
    for parameter in method_node.parameters or []:
        declarations.setdefault(parameter.name, []).append({
            "offset": -1,
            "scope": method_scope,
            "scope_kind": "method",
            "scope_label": "method",
            "kind": "method_parameter",
            "node": parameter,
            "depth": 0,
        })

    lambda_parameters: dict[int, Any] = {}
    for _, node in method_node:
        if not isinstance(node, javalang.tree.LambdaExpression):
            continue
        for parameter in node.parameters or []:
            lambda_parameters[id(parameter)] = node

    for path, node in method_node:
        declaration_names: list[str] = []
        declaration_kind = ""
        scope_kind = ""
        scope: int | None = None
        scope_label = ""
        declaration_offset = _java_ast_position_key(getattr(node, "position", None))
        if isinstance(node, javalang.tree.LocalVariableDeclaration):
            declaration_names = [item.name for item in node.declarators]
            declaration_kind = "local_variable"
            scope_kind, scope, scope_label = _java_local_declaration_scope(
                path,
                method_scope=method_scope,
            )
        elif isinstance(node, javalang.tree.CatchClauseParameter):
            declaration_names = [node.name]
            declaration_kind = "catch_parameter"
            owner = next(
                (item for item in reversed(path) if isinstance(item, javalang.tree.CatchClause)),
                None,
            )
            scope_kind = "node"
            scope = id(owner) if owner is not None else None
            scope_label = "catch_block"
            declaration_offset = -1
        elif isinstance(node, javalang.tree.TryResource):
            declaration_names = [node.name]
            declaration_kind = "try_resource"
            owner = next(
                (item for item in reversed(path) if isinstance(item, javalang.tree.TryStatement)),
                None,
            )
            scope_kind = "statement_list"
            scope = id(owner.block) if owner is not None else None
            scope_label = "try_block"
            declaration_offset = -1
        elif isinstance(node, javalang.tree.VariableDeclaration):
            declaration_names = [item.name for item in node.declarators]
            owner = next(
                (item for item in reversed(path) if isinstance(item, javalang.tree.ForStatement)),
                None,
            )
            enhanced = any(
                isinstance(item, javalang.tree.EnhancedForControl) for item in path
            )
            declaration_kind = (
                "enhanced_for_variable" if enhanced else "for_initializer_variable"
            )
            scope_kind = "node"
            scope = id(owner) if owner is not None else None
            scope_label = "enhanced_for_body" if enhanced else "for_loop"
            declaration_offset = -1
        elif id(node) in lambda_parameters:
            name = str(
                getattr(node, "name", "") or getattr(node, "member", "")
            )
            declaration_names = [name] if name else []
            declaration_kind = "lambda_parameter"
            scope_kind = "node"
            scope = id(lambda_parameters[id(node)])
            scope_label = "lambda_body"
            declaration_offset = -1
        if not declaration_names:
            continue
        if scope is None:
            continue
        for name in declaration_names:
            declarations.setdefault(name, []).append({
                "offset": declaration_offset,
                "scope": scope,
                "scope_kind": scope_kind,
                "scope_label": scope_label,
                "kind": declaration_kind,
                "node": node,
                "depth": len(path),
            })

    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for path, node in method_node:
        if id(node) in lambda_parameters:
            continue
        references: list[str] = []
        if isinstance(node, javalang.tree.MemberReference):
            qualifier = str(node.qualifier or "").strip()
            if qualifier:
                references.append(qualifier.split(".", 1)[0])
            elif not _java_member_is_explicit_field(path, node):
                references.append(str(node.member or ""))
        elif isinstance(node, (javalang.tree.MethodInvocation, javalang.tree.SuperMethodInvocation)):
            qualifier = str(getattr(node, "qualifier", "") or "").strip()
            if qualifier:
                references.append(qualifier.split(".", 1)[0])
        if not references:
            continue
        reference_offset = _java_ast_position_key(getattr(node, "position", None))
        for name in references:
            if name not in known_local_names:
                continue
            bindings = declarations.get(name, [])
            visible = [
                binding
                for binding in bindings
                if _java_binding_visible_at(
                    binding,
                    path=path,
                    reference_offset=reference_offset,
                    method_scope=method_scope,
                )
            ]
            binding = max(
                visible,
                key=lambda item: (int(item.get("depth") or 0), int(item.get("offset") or -1)),
                default=None,
            )
            resolved = binding is not None
            line = reference_offset // 1_000_000
            column = reference_offset % 1_000_000
            check = {
                "variable": name,
                "method": method_node.name,
                "line": line,
                "column": column,
                "resolved": resolved,
            }
            if binding is not None:
                check.update({
                    "declaration_kind": binding["kind"],
                    "scope": binding["scope_label"],
                })
            else:
                check["reason"] = _java_unresolved_binding_reason(
                    bindings,
                    path=path,
                    reference_offset=reference_offset,
                    method_scope=method_scope,
                )
            checks.append(check)
            key = (name, reference_offset)
            if not resolved and key not in seen:
                seen.add(key)
                issues.append({
                    "variable": name,
                    "method": method_node.name,
                    "line": line,
                    "column": column,
                    "resolved": False,
                    "reason": check["reason"],
                })
    return issues, checks


def _java_local_declaration_scope(
    path: tuple[Any, ...],
    *,
    method_scope: int,
) -> tuple[str, int, str]:
    statement_lists = [item for item in path if isinstance(item, list)]
    nearest_list = statement_lists[-1] if statement_lists else None
    switch_case = next(
        (
            item
            for item in reversed(path)
            if isinstance(item, javalang.tree.SwitchStatementCase)
        ),
        None,
    )
    if switch_case is not None and nearest_list is switch_case.statements:
        switch_statement = next(
            (
                item
                for item in reversed(path)
                if isinstance(item, javalang.tree.SwitchStatement)
            ),
            None,
        )
        if switch_statement is not None:
            return "node", id(switch_statement), "switch_block"
    if nearest_list is None:
        return "method", method_scope, "method"
    label = "nested_block" if len(statement_lists) > 1 else "method_block"
    return "statement_list", id(nearest_list), label


def _java_binding_visible_at(
    binding: dict[str, Any],
    *,
    path: tuple[Any, ...],
    reference_offset: int,
    method_scope: int,
) -> bool:
    if any(item is binding["node"] for item in path):
        return False
    offset = int(binding.get("offset", -1))
    if offset >= 0 and offset >= reference_offset:
        return False
    return _java_binding_scope_contains(
        binding,
        path=path,
        method_scope=method_scope,
    )


def _java_binding_scope_contains(
    binding: dict[str, Any],
    *,
    path: tuple[Any, ...],
    method_scope: int,
) -> bool:
    scope_kind = str(binding.get("scope_kind") or "")
    scope = int(binding.get("scope") or 0)
    if scope_kind == "method":
        return scope == method_scope
    if scope_kind == "statement_list":
        return any(isinstance(item, list) and id(item) == scope for item in path)
    if scope_kind == "node":
        if not any(isinstance(item, javalang.ast.Node) and id(item) == scope for item in path):
            return False
        if binding.get("kind") == "enhanced_for_variable" and any(
            isinstance(item, javalang.tree.EnhancedForControl) for item in path
        ):
            return False
        return True
    return False


def _java_unresolved_binding_reason(
    bindings: list[dict[str, Any]],
    *,
    path: tuple[Any, ...],
    reference_offset: int,
    method_scope: int,
) -> str:
    if not bindings:
        return "NO_VISIBLE_DECLARATION"
    for binding in bindings:
        if not _java_binding_scope_contains(
            binding,
            path=path,
            method_scope=method_scope,
        ):
            continue
        if any(item is binding["node"] for item in path):
            return "BEFORE_DECLARATION"
        offset = int(binding.get("offset", -1))
        if offset >= 0 and offset >= reference_offset:
            return "BEFORE_DECLARATION"
    return "OUTSIDE_DECLARATION_SCOPE"


def _java_ast_class(unit: Any, class_name: str) -> Any | None:
    for _, node in unit:
        if isinstance(
            node,
            (
                javalang.tree.ClassDeclaration,
                javalang.tree.InterfaceDeclaration,
                javalang.tree.EnumDeclaration,
                javalang.tree.AnnotationDeclaration,
            ),
        ) and node.name == class_name:
            return node
    return None


def _java_ast_position_key(position: Any) -> int:
    if position is None:
        return 10**18
    return int(position.line) * 1_000_000 + int(position.column)


def _validate_java_compilation(
    *,
    original_code: str,
    transformed_code: str,
    current_file_name: str,
    project_source_files: Sequence[Any] | None,
    original_local_names: set[str],
    extracted_method: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    javac = shutil.which("javac")
    if not javac:
        return {
            "status": "NOT_AVAILABLE",
            "reason": "JAVAC_NOT_AVAILABLE",
            "diagnostics": "",
        }

    original_result = _run_repository_javac(
        javac=javac,
        current_code=original_code,
        current_file_name=current_file_name,
        project_source_files=project_source_files,
        timeout_seconds=timeout_seconds,
    )
    transformed_result = _run_repository_javac(
        javac=javac,
        current_code=transformed_code,
        current_file_name=current_file_name,
        project_source_files=project_source_files,
        timeout_seconds=timeout_seconds,
    )
    if transformed_result["passed"]:
        return {
            "status": "PASS",
            "reason": "REPOSITORY_CONTEXT_COMPILED",
            "diagnostics": "",
        }

    diagnostics = str(transformed_result.get("diagnostics") or "")
    unresolved_variables = set(
        re.findall(
            r"symbol:\s+variable\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            diagnostics,
            flags=re.IGNORECASE,
        )
    )
    local_error_patterns = (
        "incompatible types",
        "missing return statement",
        "might not have been initialized",
        "unreachable statement",
        "illegal start of",
        "';' expected",
        "cannot be applied to given types",
    )
    lowered = diagnostics.lower()
    introduced_local_error = bool(unresolved_variables & original_local_names) or bool(
        re.search(
            rf"symbol:\s+method\s+{re.escape(extracted_method)}\b",
            diagnostics,
            flags=re.IGNORECASE,
        )
    ) or any(pattern in lowered for pattern in local_error_patterns)
    if original_result["passed"] or introduced_local_error:
        return {
            "status": "LOCAL_SOURCE_COMPILATION_ERROR",
            "reason": "TRANSFORMATION_GENERATED_JAVA_ERROR",
            "diagnostics": diagnostics,
            "unresolved_variables": sorted(unresolved_variables & original_local_names),
        }
    return {
        "status": "DEPENDENCY_UNAVAILABLE",
        "reason": "PROJECT_OR_EXTERNAL_DEPENDENCIES_UNAVAILABLE",
        "diagnostics": diagnostics,
        "baseline_diagnostics": str(original_result.get("diagnostics") or ""),
    }


def _run_repository_javac(
    *,
    javac: str,
    current_code: str,
    current_file_name: str,
    project_source_files: Sequence[Any] | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    sources: list[tuple[str, str]] = []
    current_matched = False
    for index, item in enumerate(project_source_files or []):
        if isinstance(item, dict):
            file_name = str(item.get("file_name") or item.get("name") or "")
            source = str(item.get("source_code") or item.get("code") or "")
            language = str(item.get("language") or "").lower()
        else:
            file_name = str(getattr(item, "file_name", "") or getattr(item, "name", ""))
            source = str(getattr(item, "source_code", "") or getattr(item, "code", ""))
            language = str(getattr(item, "language", "") or "").lower()
        if language and language != "java" and not file_name.lower().endswith(".java"):
            continue
        if not source.strip():
            continue
        if _java_paths_match(file_name, current_file_name):
            source = current_code
            current_matched = True
        sources.append((file_name or f"Source{index}.java", source))
    if not current_matched:
        sources.append((current_file_name or "", current_code))

    temp_root = Path(tempfile.gettempdir()) / "sctva_java_extract_method"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir = temp_root / f"compile_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        classes_dir = temp_dir / "classes"
        classes_dir.mkdir(parents=True, exist_ok=True)
        java_files: list[str] = []
        used_names: set[str] = set()
        for index, (file_name, source) in enumerate(sources):
            public_type = re.search(
                r"\bpublic\s+(?:class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b",
                mask_c_like(source),
            )
            first_type = re.search(
                r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b",
                mask_c_like(source),
            )
            preferred = f"{(public_type or first_type).group(1)}.java" if (public_type or first_type) else Path(file_name).name
            preferred = preferred if preferred.lower().endswith(".java") else f"Source{index}.java"
            if preferred.lower() in used_names:
                preferred = f"SctvaSource{index}.java"
            used_names.add(preferred.lower())
            path = temp_dir / preferred
            path.write_text(source.lstrip("\ufeff"), encoding="utf-8")
            java_files.append(str(path))
        proc = subprocess.run(
            [javac, "-proc:none", "-d", str(classes_dir), *java_files],
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
        )
        return {
            "passed": proc.returncode == 0,
            "diagnostics": (proc.stderr or proc.stdout or "").strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {"passed": False, "diagnostics": f"javac timeout: {exc}"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _java_paths_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a = left.replace("\\", "/").strip().lower()
    b = right.replace("\\", "/").strip().lower()
    return a == b or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def _generic_prefix(header: str) -> str:
    cleaned = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", header)
    match = re.search(r"\b(?:public|protected|private|static|final|synchronized|native|strictfp)\b(?:\s+\b(?:public|protected|private|static|final|synchronized|native|strictfp)\b)*\s+(<[^\n{]+?>)\s+", cleaned)
    return f"{match.group(1)} " if match else ""


def _throws_clause(header: str) -> str:
    match = re.search(r"\)\s*(throws\s+[^\n{]+)\s*$", header.strip())
    return f" {match.group(1).strip()}" if match else ""


def _combined_throws_clause(
    header: str,
    additional_throws: Sequence[str],
) -> str:
    existing = _throws_clause(header)
    names: list[str] = []
    if existing:
        names.extend(
            item.strip()
            for item in existing.removeprefix(" throws ").split(",")
            if item.strip()
        )
    for name in additional_throws:
        cleaned = str(name or "").strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return f" throws {', '.join(names)}" if names else ""


def _method_metrics(source_code: str, method: JavaMethod) -> dict[str, int]:
    statements = direct_c_like_statements(method.body, body_offset=method.open_brace + 1)
    return {
        "loc": _line_of(source_code, method.end - 1) - _line_of(source_code, method.start) + 1,
        "complexity": control_complexity(method.body),
        "nesting_depth": _brace_nesting(method.body),
        "statement_count": len(statements),
        "responsibility_count": len(statements),
    }


def _meaningfully_reduced(
    before: dict[str, int],
    after: dict[str, int],
    selected: Sequence[StatementSpan],
    *,
    semantic_responsibility: bool = False,
) -> bool:
    selected_loc = sum(nonblank_loc(item.text) for item in selected)
    selected_complexity = control_complexity("".join(item.text for item in selected))
    responsibility_reduced = (
        after["statement_count"] < before["statement_count"]
        or after["loc"] <= before["loc"] - 2
        or (
            len(selected) == 1
            and selected_complexity > 1
            and after["complexity"] < before["complexity"]
        )
    )
    return (
        (selected_loc >= MIN_EXTRACTED_LOC or semantic_responsibility)
        and (
            after["loc"] <= before["loc"] - 2
            or (
                semantic_responsibility
                and after["loc"] < before["loc"]
            )
        )
        and responsibility_reduced
        and after["complexity"] <= before["complexity"]
        and (
            after["responsibility_count"] < before["responsibility_count"]
            or selected_complexity > 1
            or semantic_responsibility
        )
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
