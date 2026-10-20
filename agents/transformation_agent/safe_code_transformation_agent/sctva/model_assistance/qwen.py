"""Safety-bounded local code-model candidate generation.

This module deliberately does not mutate files or accept transformations.  It
turns an already-normalized SCTVA action plus the current source snapshot into
a candidate source file.  The normal SCTVA syntax, structural, behavioral and
invariant validators remain responsible for accepting or rejecting it.

``transformers`` and ``torch`` are imported lazily so that model support is an
optional deployment feature and cannot affect the existing rule-based path.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import textwrap
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from ..contracts import RefactoringAction


DEFAULT_QWEN_MODEL_ID = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
MODEL_ASSISTANCE_REVISION = "java_constant_moved_target_v22_20261019"


_MODEL_RUNTIME_CACHE: Dict[tuple[Any, ...], Dict[str, Any]] = {}
_MODEL_RUNTIME_CACHE_LOCK = threading.Lock()


def _java_constant_declaration_pattern(constant_name: str) -> re.Pattern[str]:
    """Match one bounded Java field declaration for a planned constant.

    Java permits line breaks between declaration tokens, and instruction-tuned
    models commonly append a short line comment. Keeping this grammar in one
    place prevents completion and merge from disagreeing about valid output.
    """

    return re.compile(
        rf"(?m)^[ \t]*(?:(?:public|protected|private|static|final)\s+)*"
        rf"[A-Za-z_$][A-Za-z0-9_$<>\[\].?]*\s+"
        rf"{re.escape(constant_name)}\s*=\s*(?P<initializer>[^;{{}}]+?);"
        rf"[ \t]*(?://[^\r\n]*)?(?:\r?\n)?"
    )


@dataclass(frozen=True)
class QwenSettings:
    """Runtime settings for the Qwen2.5-Coder local-model adapter."""

    enabled: bool = False
    model_id: str = DEFAULT_QWEN_MODEL_ID
    device: str = "auto"
    backend: str = "auto"
    openvino_model_path: str = ""
    max_new_tokens: int = 256
    # Keep the configurable ceiling conservative; _load also reads the checkpoint's
    # actual tokenizer/config limit before generation.
    max_input_tokens: int = 512
    cpu_threads: int = 6
    max_actions_per_request: int = 100
    max_actions_per_file: int = 100
    generation_timeout_seconds: int = 30
    max_generation_seconds_per_request: int = 0
    runtime_cache: bool = True
    allow_unsupported_actions: bool = False
    mode: str = "primary"
    allow_download: bool = False

    def __post_init__(self) -> None:
        if self.model_id != DEFAULT_QWEN_MODEL_ID:
            raise ValueError(
                "SCTVA supports only Qwen/Qwen2.5-Coder-0.5B-Instruct."
            )

    @classmethod
    def from_execution_options(cls, options: Any) -> "QwenSettings":
        return cls(
            enabled=bool(getattr(options, "enable_qwen", False)),
            model_id=DEFAULT_QWEN_MODEL_ID,
            device=str(getattr(options, "qwen_device", "auto") or "auto").strip(),
            backend=str(
                getattr(options, "qwen_backend", "auto") or "auto"
            ).strip().lower(),
            openvino_model_path=str(
                getattr(options, "qwen_openvino_model_path", "") or ""
            ).strip(),
            max_new_tokens=int(getattr(options, "qwen_max_new_tokens", 256) or 256),
            max_input_tokens=int(getattr(options, "qwen_max_input_tokens", 512) or 512),
            cpu_threads=int(getattr(options, "qwen_cpu_threads", 6) or 6),
            max_actions_per_request=int(
                getattr(options, "qwen_max_actions_per_request", 100)
            ),
            max_actions_per_file=int(
                getattr(options, "qwen_max_actions_per_file", 100)
            ),
            generation_timeout_seconds=int(
                getattr(options, "qwen_generation_timeout_seconds", 30) or 30
            ),
            max_generation_seconds_per_request=max(
                0,
                int(
                    getattr(
                        options,
                        "qwen_max_generation_seconds_per_request",
                        0,
                    )
                    or 0
                ),
            ),
            runtime_cache=bool(getattr(options, "qwen_runtime_cache", True)),
            allow_unsupported_actions=bool(
                getattr(options, "qwen_allow_unsupported_actions", False)
            ),
            mode=str(getattr(options, "qwen_mode", "primary") or "primary").strip().lower(),
            allow_download=bool(getattr(options, "qwen_allow_download", False)),
        )


@dataclass(frozen=True)
class QwenCandidate:
    """A candidate result, never an acceptance decision."""

    source_code: str = ""
    status: str = "skipped"
    reason: str = ""
    metadata: Dict[str, Any] | None = None
    target_replacement: str = ""


@dataclass(frozen=True)
class QwenTargetContext:
    """An exact, replaceable source range selected from the current snapshot."""

    start: int
    end: int
    source: str
    kind: str
    name: str
    start_line: int
    end_line: int
    surrounding_context: str = ""
    resolution: str = ""


_CLASS_SCOPED_ACTIONS = {
    "extract_class",
    "extract_python_class",
    "extract_java_class",
    "extract_subclass",
    "introduce_facade",
    "inline_class",
    "inline_python_class",
    "inline_java_class",
    "move_python_method",
    "move_java_method",
    "pull_up_method",
    "replace_data_value_with_object",
    "collapse_hierarchy",
    "replace_conditional_with_polymorphism",
}

_MULTI_CLASS_ACTIONS = {
    "inline_class",
    "inline_python_class",
    "inline_java_class",
    "move_python_method",
    "move_java_method",
    "pull_up_method",
    "collapse_hierarchy",
}

_JAVA_ACTION_REQUIREMENTS = {
    "extract_subclass": (
        "Create the requested subclass as a sibling declaration and move only cohesive, "
        "subclass-specific members into it. Keep the source class concrete and behavior-compatible."
    ),
    "introduce_facade": (
        "Create the requested facade as a sibling declaration. Expose focused delegating methods "
        "without changing the existing subsystem public API or business logic."
    ),
    "replace_temp_with_query": (
        "Replace one derived local temporary in the target method with a private side-effect-free "
        "query method. Preserve evaluation order and do not duplicate side effects."
    ),
    "move_java_method": (
        "Move the requested method from source_class to destination_class and update references in "
        "this resolved source span. Preserve its signature, visibility requirements, and behavior."
    ),
    "pull_up_method": (
        "Move the requested duplicate method from source_class into target_class and remove only "
        "the redundant source declaration. Preserve dispatch and visibility."
    ),
    "replace_data_value_with_object": (
        "Replace the targeted primitive data concept with one small immutable value class and "
        "update only usages inside the resolved class. Preserve the class public behavior."
    ),
    "inline_java_class": (
        "Move the target lazy class state and behavior into its owning class, update references, "
        "and remove the target class declaration only when no references remain."
    ),
    "collapse_hierarchy": (
        "Collapse source_class into parent_class in this resolved span, preserving all callable "
        "behavior and removing only the redundant hierarchy declaration."
    ),
    "replace_parameter_with_method_call": (
        "Remove only the redundant target-method parameter whose value can be obtained by an "
        "existing query, replace its uses with that query, and update visible call sites in span."
    ),
    "replace_conditional_with_polymorphism": (
        "Replace the target type conditional with polymorphic dispatch while preserving every "
        "branch result, exception, and externally visible signature."
    ),
    "hide_delegate": (
        "Replace the message chain in the target method with one delegating method on the owning "
        "class. Preserve the target method signature and return value."
    ),
}


def _first_text(values: Iterable[Any]) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _positive_line(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _line_range(parameters: Dict[str, Any]) -> tuple[int | None, int | None]:
    start = _positive_line(parameters.get("source_line") or parameters.get("start_line"))
    end = _positive_line(parameters.get("end_line"))
    raw_lines = parameters.get("target_lines") or parameters.get("lines")
    if isinstance(raw_lines, (list, tuple)):
        lines = [line for item in raw_lines if (line := _positive_line(item))]
        if lines:
            start = start or min(lines)
            end = end or max(lines)
    raw_range = parameters.get("target_range")
    if isinstance(raw_range, dict):
        start = start or _positive_line(raw_range.get("start") or raw_range.get("start_line"))
        end = end or _positive_line(raw_range.get("end") or raw_range.get("end_line"))
    elif isinstance(raw_range, (list, tuple)):
        lines = [line for item in raw_range if (line := _positive_line(item))]
        if lines:
            start = start or min(lines)
            end = end or max(lines)
    return start, end or start


def _planned_literal_values(parameters: Dict[str, Any]) -> list[Any]:
    """Return explicit or hint-derived literals without mutating the action."""

    if parameters.get("literal_value") is not None:
        return [parameters["literal_value"]]
    raw_values = parameters.get("literal_values")
    if isinstance(raw_values, list):
        return list(raw_values)
    hint = str(parameters.get("hint") or "")
    values: list[Any] = []
    seen: set[Any] = set()
    for text in re.findall(r"-?\d+(?:\.\d+)?", hint):
        value: Any = float(text) if "." in text else int(text)
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _semantic_anchors(parameters: Dict[str, Any]) -> list[str]:
    """Return pre-transformation tokens that can identify an action target.

    New identifiers such as ``constant_name`` are intentionally excluded
    because they do not exist in the current source yet. An anchor is only
    used when it selects one unambiguous structural declaration.
    """

    keys = (
        "literal_value", "literal_values", "old_name", "target_name",
        "symbol", "function", "function_name", "variable", "variable_name",
        "field", "field_name", "unsafe_function", "delegate_member",
        "delegated_member",
    )
    anchors: list[str] = []
    for key in keys:
        value = parameters.get(key)
        values = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values:
            if isinstance(item, bool) or item is None:
                continue
            text = str(item).strip()
            if text and text not in anchors:
                anchors.append(text)
    if not any(key in parameters for key in ("literal_value", "literal_values")):
        for value in _planned_literal_values(parameters):
            text = str(value)
            if text not in anchors:
                anchors.append(text)
    return anchors


def _contains_semantic_anchor(text: str, anchor: str) -> bool:
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", anchor):
        return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(anchor)}(?![A-Za-z0-9_$])", text) is not None
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFlLuU]*", anchor):
        return re.search(rf"(?<![A-Za-z0-9_.]){re.escape(anchor)}(?![A-Za-z0-9_.])", text) is not None
    return anchor in text


def _unique_anchor_match(
    source: str,
    records: Iterable[Any],
    anchors: Iterable[str],
    span: Callable[[Any], tuple[int, int]],
) -> Any | None:
    scored: list[tuple[int, Any]] = []
    for record in records:
        start, end = span(record)
        fragment = source[max(0, start):max(start, end)]
        score = sum(_contains_semantic_anchor(fragment, anchor) for anchor in anchors)
        if score:
            scored.append((score, record))
    if not scored:
        return None
    highest = max(score for score, _ in scored)
    winners = [record for score, record in scored if score == highest]
    return winners[0] if len(winners) == 1 else None


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source):
        offsets.append(match.end())
    return offsets


def _line_start(source: str, offset: int) -> int:
    return source.rfind("\n", 0, max(0, offset)) + 1


def _line_end(source: str, offset: int) -> int:
    newline = source.find("\n", max(0, offset))
    return len(source) if newline < 0 else newline + 1


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, max(0, offset)) + 1


def _imports_context(language: str, source: str) -> str:
    """Return declaration context only; it is never part of the replaced span."""

    lines = source.splitlines()
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return ""
        selected: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                selected.extend(lines[node.lineno - 1:int(getattr(node, "end_lineno", node.lineno))])
        return "\n".join(selected[:30])
    if language == "java":
        return "\n".join(
            line for line in lines[:120]
            if re.match(r"^\s*(?:package|import)\b", line)
        )
    if language == "c":
        return "\n".join(
            line for line in lines[:160]
            if re.match(r"^\s*#\s*include\b", line)
        )
    return ""


def _python_context(source: str, action: RefactoringAction) -> QwenTargetContext | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    parameters = dict(action.parameters or {})
    method_name = _first_text((
        parameters.get("source_method"), parameters.get("method"),
        parameters.get("method_name"), parameters.get("function"),
        parameters.get("function_name"), parameters.get("target_method"),
    ))
    class_name = _first_text((
        parameters.get("class_to_inline"), parameters.get("target_class"),
        parameters.get("source_class"), parameters.get("class_name"),
    ))
    destination_name = _first_text((parameters.get("destination_class"),))
    source_line, _ = _line_range(parameters)
    offsets = _line_offsets(source)
    records: list[dict[str, Any]] = []

    def visit(node: ast.AST, owners: tuple[str, ...] = ()) -> None:
        if isinstance(node, ast.ClassDef):
            current_owners = owners + (node.name,)
            records.append({"node": node, "kind": "class", "name": node.name,
                            "qualified": ".".join(current_owners), "owners": owners})
            for child in node.body:
                visit(child, current_owners)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            records.append({"node": node, "kind": "method" if owners else "function",
                            "name": node.name, "qualified": ".".join(owners + (node.name,)),
                            "owners": owners})
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, owners + (node.name,))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, owners)

    for top_level in tree.body:
        visit(top_level)

    prefer_class = action.action_type in _CLASS_SCOPED_ACTIONS
    anchors = _semantic_anchors(parameters)

    def record_span(record: dict[str, Any]) -> tuple[int, int]:
        node = record["node"]
        start_line = int(getattr(node, "lineno", 1))
        end_line = int(getattr(node, "end_lineno", start_line))
        start = offsets[max(0, start_line - 1)]
        end = offsets[end_line] if end_line < len(offsets) else len(source)
        return start, end

    matches: list[dict[str, Any]] = []
    if prefer_class and class_name:
        matches = [record for record in records if record["kind"] == "class" and (
            record["qualified"] == class_name or record["name"] == class_name
        )]
    if not matches and method_name and not prefer_class:
        matches = [record for record in records if record["kind"] in {"method", "function"}
                   and record["name"] == method_name]
        if class_name:
            owned = [record for record in matches if record["owners"] and (
                record["owners"][-1] == class_name or ".".join(record["owners"]) == class_name
            )]
            matches = owned or matches

    # Overloads/duplicate short names may still be resolved by a current line
    # or a unique pre-transformation literal/symbol inside one declaration.
    if len(matches) > 1 and anchors:
        anchored = _unique_anchor_match(source, matches, anchors, record_span)
        if anchored is not None:
            matches = [anchored]
    if len(matches) > 1 and source_line:
        containing_matches = [record for record in matches if (
            int(getattr(record["node"], "lineno", 0)) <= source_line
            <= int(getattr(record["node"], "end_lineno", 0))
        )]
        if len(containing_matches) == 1:
            matches = containing_matches

    callable_records = [
        record for record in records if record["kind"] in {"method", "function"}
    ]
    if not matches and anchors and not prefer_class:
        anchored = _unique_anchor_match(source, callable_records, anchors, record_span)
        if anchored is not None:
            matches = [anchored]
    if not matches and source_line:
        containing = [record for record in records if (
            int(getattr(record["node"], "lineno", 0)) <= source_line
            <= int(getattr(record["node"], "end_lineno", 0))
        )]
        if prefer_class:
            classes = [record for record in containing if record["kind"] == "class"]
            classes.sort(key=lambda record: (
                int(getattr(record["node"], "end_lineno", 0))
                - int(getattr(record["node"], "lineno", 0))
            ))
            matches = classes[:1]
        else:
            callables = [record for record in containing if record["kind"] in {"method", "function"}]
            callables.sort(key=lambda record: (
                int(getattr(record["node"], "end_lineno", 0))
                - int(getattr(record["node"], "lineno", 0))
            ))
            matches = callables[:1]
    if not matches and class_name:
        matches = [record for record in records if record["kind"] == "class" and (
            record["qualified"] == class_name or record["name"] == class_name
        )]
    if not matches and len(callable_records) == 1 and not prefer_class:
        matches = callable_records
    class_records = [record for record in records if record["kind"] == "class"]
    if not matches and len(class_records) == 1:
        matches = class_records
    if len(matches) != 1:
        return None

    selected = matches[0]
    selected_records = [selected]
    if action.action_type in _MULTI_CLASS_ACTIONS and destination_name:
        destinations = [record for record in records if record["kind"] == "class" and (
            record["qualified"] == destination_name or record["name"] == destination_name
        )]
        if len(destinations) == 1 and destinations[0] is not selected:
            selected_records.append(destinations[0])

    start_lines: list[int] = []
    end_lines: list[int] = []
    for record in selected_records:
        node = record["node"]
        decorator_lines = [item.lineno for item in getattr(node, "decorator_list", [])]
        start_lines.append(min([node.lineno, *decorator_lines]))
        end_lines.append(int(getattr(node, "end_lineno", node.lineno)))
    start_line = min(start_lines)
    end_line = max(end_lines)
    start = offsets[start_line - 1]
    end = offsets[end_line] if end_line < len(offsets) else len(source)
    return QwenTargetContext(
        start=start, end=end, source=source[start:end], kind=selected["kind"],
        name=selected["qualified"], start_line=start_line, end_line=end_line,
        surrounding_context=_imports_context("python", source),
        resolution="python_ast_current_snapshot",
    )


def _c_context(source: str, action: RefactoringAction) -> QwenTargetContext | None:
    from ..transformers.c_extract_class import _mask_c_like, _matching, _parse_c_module

    parameters = dict(action.parameters or {})
    method_name = _first_text((
        parameters.get("source_method"), parameters.get("method"),
        parameters.get("method_name"), parameters.get("target_method"),
        parameters.get("function"),
        parameters.get("function_name"), parameters.get("target_function"),
    ))
    class_name = _first_text((
        parameters.get("source_class"), parameters.get("target_class"),
        parameters.get("class_name"), parameters.get("class_to_inline"),
    ))
    source_line, _ = _line_range(parameters)
    masked = _mask_c_like(source)

    # These two C refactorings introduce a file-scope ``static`` helper and,
    # for parameter replacement, may update another function's local call.
    # A function-only replacement cannot represent that valid C output.
    if action.action_type in {
        "replace_temp_with_query",
        "replace_parameter_with_method_call",
    }:
        return QwenTargetContext(
            start=0,
            end=len(source),
            source=source,
            kind="translation_unit",
            name=method_name or "c_translation_unit",
            start_line=1,
            end_line=max(1, len(source.splitlines())),
            surrounding_context="",
            resolution="c_translation_unit_for_file_scope_query_refactoring",
        )

    # Localized C refactorings should never ask a small model to regenerate a
    # complete function. Resolve the exact anchored source line when it forms
    # a complete statement/directive. Stale planner lines are recovered only
    # when the semantic anchors occur on one unique code line.
    line_scoped_actions = {
        "introduce_constant", "extract_constant", "encapsulate_variable",
        "replace_unsafe_function",
    }
    if action.action_type in line_scoped_actions:
        anchors = _semantic_anchors(parameters)
        candidate_lines: list[int] = []
        if source_line:
            candidate_lines.append(source_line)
        if anchors:
            anchored_lines: set[int] = set()
            for anchor in anchors:
                for line_number, masked_line in enumerate(masked.splitlines(), start=1):
                    if _contains_semantic_anchor(masked_line, anchor):
                        anchored_lines.add(line_number)
            masked_source_lines = masked.splitlines()
            source_line_matches = bool(
                source_line
                and source_line <= len(masked_source_lines)
                and all(
                    _contains_semantic_anchor(masked_source_lines[source_line - 1], anchor)
                    for anchor in anchors
                )
            )
            if source_line_matches:
                candidate_lines = [source_line]
            elif len(anchored_lines) == 1:
                anchored_line = next(iter(anchored_lines))
                candidate_lines = [anchored_line]
            else:
                candidate_lines = []
        source_lines = source.splitlines(keepends=True)
        masked_lines = masked.splitlines(keepends=True)
        for candidate_line in candidate_lines:
            if not (1 <= candidate_line <= len(source_lines)):
                continue
            masked_line = masked_lines[candidate_line - 1]
            stripped = masked_line.strip()
            complete_line = (
                stripped.startswith("#")
                or (
                    masked_line.count("(") == masked_line.count(")")
                    and masked_line.count("[") == masked_line.count("]")
                    and (";" in masked_line or "{" in masked_line or "}" in masked_line)
                )
            )
            if not stripped or not complete_line:
                continue
            start = sum(len(line) for line in source_lines[:candidate_line - 1])
            end = start + len(source_lines[candidate_line - 1])
            kind = "preprocessor" if stripped.startswith("#") else "statement"
            return QwenTargetContext(
                start=start,
                end=end,
                source=source[start:end],
                kind=kind,
                name=method_name or f"line_{candidate_line}",
                start_line=candidate_line,
                end_line=candidate_line,
                surrounding_context=_imports_context("c", source),
                resolution="c_anchored_statement_current_snapshot",
            )
        if source_line or anchors:
            return None
    try:
        functions = list(_parse_c_module(source).functions)
    except (TypeError, ValueError):
        functions = []
    matches = [function for function in functions if function.name == method_name] if method_name else []
    if len(matches) > 1 and source_line:
        line_matches = [function for function in matches if (
            _line_number(source, function.start) <= source_line
            <= _line_number(source, function.end - 1)
        )]
        if len(line_matches) == 1:
            matches = line_matches
    if not matches:
        anchored = _unique_anchor_match(
            source,
            functions,
            _semantic_anchors(parameters),
            lambda function: (function.start, function.end),
        )
        if anchored is not None:
            matches = [anchored]
    if not matches and source_line:
        matches = [function for function in functions if (
            _line_number(source, function.start) <= source_line
            <= _line_number(source, function.end - 1)
        )]
    if not matches and len(functions) == 1 and not source_line and not method_name:
        matches = functions
    if len(matches) == 1:
        function = matches[0]
        start = _line_start(source, function.start)
        end = _line_end(source, function.end)
        return QwenTargetContext(
            start=start, end=end, source=source[start:end], kind="function", name=function.name,
            start_line=_line_number(source, start), end_line=_line_number(source, max(start, end - 1)),
            surrounding_context=_imports_context("c", source),
            resolution="c_symbol_model_current_snapshot",
        )
    if len(matches) > 1:
        return None

    # Headers are not limited to function definitions. They commonly contain
    # prototypes, object-like macros, structs, and (despite a .h extension)
    # C++ class declarations. Resolve those source ranges without treating
    # arbitrary expression calls as declarations.
    records: list[Dict[str, Any]] = []
    function_ranges = [(item.start, item.end) for item in functions]

    def add_record(kind: str, name: str, start: int, end: int) -> None:
        while start < end and source[start].isspace():
            start += 1
        start = _line_start(source, start)
        end = _line_end(source, max(start, end - 1))
        key = (kind, name, start, end)
        if start < end and not any(
            (record["kind"], record["name"], record["start"], record["end"]) == key
            for record in records
        ):
            records.append({"kind": kind, "name": name, "start": start, "end": end})

    # Preserve complete logical preprocessor directives, including continued
    # lines. This lets a line-targeted action safely add an adjacent macro.
    lines = source.splitlines(keepends=True)
    line_offsets: list[int] = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line)
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if line.lstrip().startswith("#"):
            start = line_offsets[line_index]
            logical = line
            end = start + len(line)
            while logical.rstrip("\r\n").endswith("\\") and line_index + 1 < len(lines):
                line_index += 1
                logical = lines[line_index]
                end += len(logical)
            directive = source[start:end]
            directive_match = re.match(
                r"\s*#\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s+([A-Za-z_][A-Za-z0-9_]*))?",
                directive,
            )
            directive_name = " ".join(
                item for item in (directive_match.groups() if directive_match else ()) if item
            ) or "preprocessor"
            add_record("preprocessor", directive_name, start, end)
        line_index += 1

    class_records: list[Dict[str, Any]] = []
    for match in re.finditer(
        r"\b(class|struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)[^;{]*\{",
        masked,
    ):
        open_brace = masked.find("{", match.start(), match.end())
        close_brace = _matching(masked, open_brace, "{", "}")
        if close_brace is None:
            continue
        end = close_brace + 1
        while end < len(masked) and masked[end].isspace():
            end += 1
        if end < len(masked) and masked[end] == ";":
            end += 1
        record = {
            "kind": match.group(1),
            "name": match.group(2),
            "start": _line_start(source, match.start()),
            "end": _line_end(source, end),
        }
        class_records.append(record)
        add_record(record["kind"], record["name"], record["start"], record["end"])

    ignored_call_names = {"if", "for", "while", "switch", "sizeof", "return", "defined"}
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", masked):
        name = match.group(1)
        if name in ignored_call_names or any(start <= match.start() < end for start, end in function_ranges):
            continue
        open_paren = masked.find("(", match.start(), match.end())
        close_paren = _matching(masked, open_paren, "(", ")")
        if close_paren is None:
            continue
        semicolon = masked.find(";", close_paren + 1)
        brace = masked.find("{", close_paren + 1)
        if semicolon < 0 or (brace >= 0 and brace < semicolon):
            continue
        boundary = max(
            masked.rfind(";", 0, match.start()),
            masked.rfind("{", 0, match.start()),
            masked.rfind("}", 0, match.start()),
        )
        declaration_start = boundary + 1
        prefix = masked[declaration_start:match.start()]
        access = list(re.finditer(r"\b(?:public|private|protected)\s*:\s*", prefix))
        if access:
            declaration_start += access[-1].end()
            prefix = masked[declaration_start:match.start()]
        if "=" in prefix or re.search(r"\b(?:return|case|throw|new|delete)\b", prefix):
            continue
        if not prefix.strip() and not any(
            record["start"] <= match.start() < record["end"] for record in class_records
        ):
            continue
        add_record("declaration", name, declaration_start, semicolon + 1)

    candidates = [record for record in records if record["name"] == method_name] if method_name else []
    if not candidates and class_name:
        candidates = [record for record in records if record["name"] == class_name]
    anchors = _semantic_anchors(parameters)
    if not candidates and anchors:
        anchored = _unique_anchor_match(
            source,
            records,
            anchors,
            lambda record: (record["start"], record["end"]),
        )
        if anchored is not None:
            candidates = [anchored]
    if not candidates and source_line:
        candidates = [record for record in records if (
            _line_number(source, record["start"]) <= source_line
            <= _line_number(source, max(record["start"], record["end"] - 1))
        )]
        candidates.sort(key=lambda record: record["end"] - record["start"])
        if candidates:
            smallest_size = candidates[0]["end"] - candidates[0]["start"]
            candidates = [
                record for record in candidates
                if record["end"] - record["start"] == smallest_size
            ]
    if not candidates and not source_line and not method_name and len(records) == 1:
        candidates = records
    if len(candidates) != 1:
        return None
    selected = candidates[0]
    return QwenTargetContext(
        start=selected["start"],
        end=selected["end"],
        source=source[selected["start"]:selected["end"]],
        kind=selected["kind"],
        name=selected["name"],
        start_line=_line_number(source, selected["start"]),
        end_line=_line_number(source, max(selected["start"], selected["end"] - 1)),
        surrounding_context=_imports_context("c", source),
        resolution="c_family_declaration_current_snapshot",
    )


def _java_context(source: str, action: RefactoringAction) -> QwenTargetContext | None:
    from ..transformers.java_extract_class import _parse_java_class

    parameters = dict(action.parameters or {})
    method_name = _first_text((
        parameters.get("source_method"), parameters.get("method"),
        parameters.get("method_name"), parameters.get("target_method"),
    ))
    class_name = _first_text(
        (
            parameters.get("class_to_inline"),
            parameters.get("target_class"),
            parameters.get("source_class"),
            parameters.get("class_name"),
        )
        if action.action_type in {"inline_class", "inline_java_class"}
        else (
            parameters.get("source_class"),
            parameters.get("class_name"),
            parameters.get("target_class"),
        )
    )
    field_name = _first_text((
        parameters.get("field_name"), parameters.get("field"),
        parameters.get("target_field"),
    ))
    destination_name = _first_text((
        parameters.get("destination_class"),
        parameters.get("target_class"),
        parameters.get("parent_class"),
    ))
    source_line, _ = _line_range(parameters)
    class_names = list(dict.fromkeys(re.findall(
        r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", source,
    )))
    classes = [model for name in class_names if (model := _parse_java_class(source, name))]
    selected_classes = [model for model in classes if model.name == class_name] if class_name else []
    prefer_class = action.action_type in _CLASS_SCOPED_ACTIONS
    anchors = _semantic_anchors(parameters)
    all_methods = [(model, method) for model in classes for method in model.methods]
    all_fields = [
        (model, java_field)
        for model in classes
        for java_field in model.fields.values()
    ]
    selected_start: int | None = None
    selected_end: int | None = None
    kind = ""
    name = ""
    selected_class = selected_classes[0] if len(selected_classes) == 1 else None

    # Introduce Constant adds one class member but should not regenerate the
    # complete class. Resolve a literal in an existing field initializer as an
    # exact field-member span; method-contained literals are resolved by the
    # ordinary method path below.
    if (
        action.action_type in {"introduce_constant", "extract_constant"}
        and (field_name or not method_name)
    ):
        field_space = all_fields
        if class_name:
            owned_fields = [item for item in all_fields if item[0].name == class_name]
            field_space = owned_fields or all_fields
        field_matches: list[tuple[Any, Any]] = []
        if field_name:
            named_fields = [
                item for item in field_space if item[1].name == field_name
            ]
            if len(named_fields) == 1:
                field_matches = named_fields
        if source_line:
            line_matches = [
                item for item in field_space
                if _line_number(source, item[1].start) <= source_line
                <= _line_number(source, max(item[1].start, item[1].end - 1))
            ]
            if anchors:
                line_matches = [
                    item for item in line_matches
                    if all(
                        _contains_semantic_anchor(
                            source[item[1].start:item[1].end], anchor
                        )
                        for anchor in anchors
                    )
                ]
            if not field_matches:
                field_matches = line_matches
        if len(field_matches) != 1 and anchors:
            anchored_field = _unique_anchor_match(
                source,
                field_space,
                anchors,
                lambda item: (item[1].start, item[1].end),
            )
            field_matches = [anchored_field] if anchored_field is not None else []
        if len(field_matches) == 1:
            owner, java_field = field_matches[0]
            selected_start, selected_end = java_field.start, java_field.end
            kind = "field"
            name = f"{owner.name}.{java_field.name}"
    if prefer_class and selected_class is None and anchors:
        selected_class = _unique_anchor_match(
            source,
            classes,
            anchors,
            lambda model: (model.start, model.close_brace + 1),
        )
    if prefer_class and selected_class is None and source_line:
        containing_classes = [model for model in classes if (
            _line_number(source, model.start) <= source_line
            <= _line_number(source, model.close_brace)
        )]
        containing_classes.sort(key=lambda model: model.close_brace - model.start)
        if containing_classes:
            selected_class = containing_classes[0]
    if prefer_class and selected_class is None and len(classes) == 1:
        selected_class = classes[0]

    if selected_start is not None and selected_end is not None:
        pass
    elif prefer_class and selected_class is not None:
        selected_start = selected_class.start
        selected_end = selected_class.close_brace + 1
        kind = "class"
        name = selected_class.name
        if action.action_type in _MULTI_CLASS_ACTIONS and destination_name:
            destinations = [model for model in classes if model.name == destination_name]
            if len(destinations) == 1:
                selected_start = min(selected_start, destinations[0].start)
                selected_end = max(selected_end, destinations[0].close_brace + 1)
                name = f"{name},{destinations[0].name}"
    else:
        methods = [item for item in all_methods if method_name and item[1].name == method_name]
        if class_name:
            owned = [item for item in methods if item[0].name == class_name]
            methods = owned or methods
        if len(methods) > 1 and anchors:
            anchored_method = _unique_anchor_match(
                source,
                methods,
                anchors,
                lambda item: (item[1].start, item[1].end),
            )
            if anchored_method is not None:
                methods = [anchored_method]
        if len(methods) > 1 and source_line:
            line_methods = [item for item in methods if (
                _line_number(source, item[1].start) <= source_line
                <= _line_number(source, item[1].end - 1)
            )]
            if len(line_methods) == 1:
                methods = line_methods
        method_search_space = all_methods
        if class_name:
            owned = [item for item in all_methods if item[0].name == class_name]
            method_search_space = owned or all_methods
        if not methods and anchors:
            anchored_method = _unique_anchor_match(
                source,
                method_search_space,
                anchors,
                lambda item: (item[1].start, item[1].end),
            )
            if anchored_method is not None:
                methods = [anchored_method]
        if not methods and source_line:
            methods = [
                item for item in method_search_space
                if _line_number(source, item[1].start) <= source_line
                <= _line_number(source, item[1].end - 1)
            ]
        if not methods and len(method_search_space) == 1:
            methods = method_search_space
        if len(methods) == 1:
            owner, method = methods[0]
            selected_start, selected_end = method.start, method.end
            kind = "method"
            name = f"{owner.name}.{method.name}"
        elif selected_class is not None:
            selected_start = selected_class.start
            selected_end = selected_class.close_brace + 1
            kind = "class"
            name = selected_class.name
        elif not methods and anchors:
            anchored_class = _unique_anchor_match(
                source,
                classes,
                anchors,
                lambda model: (model.start, model.close_brace + 1),
            )
            if anchored_class is not None:
                selected_start = anchored_class.start
                selected_end = anchored_class.close_brace + 1
                kind = "class"
                name = anchored_class.name
        elif not methods and len(classes) == 1:
            selected_start = classes[0].start
            selected_end = classes[0].close_brace + 1
            kind = "class"
            name = classes[0].name
    if selected_start is None or selected_end is None:
        return None

    # A line-scoped constant extraction does not need the model to regenerate
    # a complete method. Once the stable method has been resolved, narrow the
    # replaceable span to the only line containing the planned literal. This
    # keeps small-model prompts below their context limit and prevents edits to
    # unrelated statements. Ambiguous multi-line matches retain the complete
    # method and remain subject to the usual size/safety checks.
    if (
        action.action_type in {"introduce_constant", "extract_constant"}
        and kind == "method"
    ):
        literal_values = _planned_literal_values(parameters)
        if len(literal_values) == 1:
            from ..transformers import java_transformers

            def literal_lines(start: int, end: int) -> list[tuple[int, int]]:
                member_source = source[start:end]
                line_candidates: list[tuple[int, int]] = []
                cursor = 0
                for line in member_source.splitlines(keepends=True):
                    _, replacements = java_transformers._replace_literal(
                        line,
                        literal_values[0],
                        "__SCTVA_PLANNED_CONSTANT__",
                        1,
                    )
                    if replacements > 0:
                        source_line_text = line.rstrip("\r\n")
                        line_candidates.append(
                            (
                                start + cursor,
                                start + cursor + len(source_line_text),
                            )
                        )
                    cursor += len(line)
                return line_candidates

            line_candidates = literal_lines(selected_start, selected_end)
            if len(line_candidates) == 1:
                selected_start, selected_end = line_candidates[0]
                kind = "statement"
            elif not line_candidates:
                # A preceding Extract Method can move the planned statement
                # from its RDP-named owner into a new helper. The old owner is
                # still resolvable, but prompting it cannot satisfy Introduce
                # Constant because the literal no longer exists there. Follow
                # the literal through the current symbol model only when one
                # method now contains one unambiguous target line.
                moved_candidates: list[tuple[Any, Any, int, int]] = []
                for owner, method in all_methods:
                    if method.start == selected_start and method.end == selected_end:
                        continue
                    method_lines = literal_lines(method.start, method.end)
                    if len(method_lines) == 1:
                        moved_candidates.append(
                            (owner, method, method_lines[0][0], method_lines[0][1])
                        )
                    elif len(method_lines) > 1:
                        moved_candidates.extend(
                            (owner, method, start, end)
                            for start, end in method_lines
                        )
                if len(moved_candidates) == 1:
                    moved_owner, moved_method, moved_start, moved_end = (
                        moved_candidates[0]
                    )
                    selected_start, selected_end = moved_start, moved_end
                    kind = "statement"
                    name = f"{moved_owner.name}.{moved_method.name}"
    # A Java method declaration is itself a class-member span.  Replacing that
    # one span with the rewritten source method followed by one sibling helper
    # keeps every unrelated field/method byte-for-byte intact while still
    # allowing Extract Method to add the required declaration.
    if action.action_type == "extract_method":
        if kind != "method":
            return None
        kind = "method_with_sibling_helper"
    if action.action_type in {"introduce_constant", "extract_constant"}:
        if kind == "method":
            kind = "method_with_sibling_constant"
        elif kind == "statement":
            kind = "statement_with_sibling_constant"
        elif kind == "field":
            kind = "field_with_sibling_constant"
        else:
            # A small model must never regenerate an entire Java class for a
            # one-literal refactoring. The deterministic fallback can safely
            # handle plans whose exact member cannot be recovered.
            return None
    start = _line_start(source, selected_start)
    end = _line_end(source, selected_end)
    return QwenTargetContext(
        start=start, end=end, source=source[start:end], kind=kind, name=name,
        start_line=_line_number(source, start), end_line=_line_number(source, max(start, end - 1)),
        surrounding_context=_imports_context("java", source),
        resolution="java_symbol_model_current_snapshot",
    )


class QwenCandidateTransformer:
    """Generate deterministic source-to-source candidates with a local model.

    ``generator`` exists for isolated tests and for an eventual fine-tuned
    in-house serving backend.  Production construction leaves it as ``None``
    and lazily loads the Hugging Face checkpoint on first use.
    """

    def __init__(
        self,
        settings: QwenSettings,
        *,
        generator: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.settings = settings
        self._generator = generator
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._resolved_device = ""
        self._resolved_backend = "injected" if generator is not None else ""
        self._effective_max_input_tokens = settings.max_input_tokens
        self._effective_cpu_threads = settings.cpu_threads
        self._runtime_cache_hit = False
        self._model_load_seconds = 0.0
        self._attempt_count = 0
        self._file_attempt_counts: Dict[str, int] = {}
        self._generation_seconds_used = 0.0
        self._file_generation_seconds: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._runtime_inference_lock = threading.Lock()

    @property
    def provider(self) -> str:
        return "qwen_coder"

    @property
    def display_name(self) -> str:
        return "Qwen2.5-Coder"

    def can_attempt(self, current_file_name: str) -> bool:
        """Return whether inference budget remains without consuming it."""

        return self.attempt_budget_status(current_file_name).get("available") is True

    def attempt_budget_status(self, current_file_name: str) -> Dict[str, Any]:
        """Describe a skipped inference budget so it is not reported as ineligibility."""

        file_key = str(current_file_name or "<unknown>").replace("\\", "/").lower()
        with self._lock:
            file_attempts = self._file_attempt_counts.get(file_key, 0)
            if self._attempt_count >= self.settings.max_actions_per_request:
                reason = "QWEN_REQUEST_ACTION_BUDGET_EXHAUSTED"
            elif file_attempts >= self.settings.max_actions_per_file:
                reason = "QWEN_FILE_ACTION_BUDGET_EXHAUSTED"
            elif (
                self.settings.max_generation_seconds_per_request > 0
                and self._generation_seconds_used
                >= self.settings.max_generation_seconds_per_request
            ):
                reason = "QWEN_REQUEST_TIME_BUDGET_EXHAUSTED"
            else:
                reason = ""
            return {
                "available": not reason,
                "reason": reason,
                "request_attempts_used": self._attempt_count,
                "request_attempt_limit": self.settings.max_actions_per_request,
                "file_attempts_used": file_attempts,
                "file_attempt_limit": self.settings.max_actions_per_file,
                "generation_seconds_used": round(self._generation_seconds_used, 3),
                "generation_seconds_limit": (
                    self.settings.max_generation_seconds_per_request
                ),
            }

    def usage_snapshot(self, current_file_name: str = "") -> Dict[str, float | int]:
        """Return request and optional file-scoped counters atomically.

        Model-assisted file workflows can prepare and validate concurrently.
        Keeping the per-file counters here prevents one worker's generation
        time from being attributed to another file in the UI.
        """

        file_key = str(current_file_name or "").replace("\\", "/").lower()
        with self._lock:
            return {
                "attempt_count": self._attempt_count,
                "generation_seconds": self._generation_seconds_used,
                "file_attempt_count": self._file_attempt_counts.get(file_key, 0),
                "file_generation_seconds": self._file_generation_seconds.get(
                    file_key,
                    0.0,
                ),
            }

    def _bounded_generation_timeout(
        self,
        per_attempt_seconds: float,
        *,
        in_flight_seconds: float = 0.0,
    ) -> float:
        """Keep a generation call inside both per-attempt and request budgets."""

        timeout = max(0.1, float(per_attempt_seconds))
        request_limit = self.settings.max_generation_seconds_per_request
        if request_limit > 0:
            remaining = (
                float(request_limit)
                - self._generation_seconds_used
                - max(0.0, float(in_flight_seconds))
            )
            timeout = min(timeout, max(0.1, remaining))
        return timeout

    def _generation_limits(
        self,
        language: str,
        action: RefactoringAction,
        context: QwenTargetContext | None,
    ) -> tuple[int, int]:
        max_new_tokens = self.settings.max_new_tokens
        timeout_seconds = self.settings.generation_timeout_seconds
        if (
            str(language or "").lower() in {"java", "c"}
            and action.action_type in {"introduce_constant", "extract_constant"}
        ):
            if context is None:
                return max_new_tokens, timeout_seconds
            # The output must contain the transformed target plus a sibling
            # declaration. A fixed 64-token ceiling truncated ordinary Java
            # methods and produced dangling braces or partial declarations.
            estimated_target_tokens = max(1, (len(context.source) + 2) // 3)
            minimum_tokens = (
                96
                if context.kind.startswith(("statement", "field"))
                else 128
            )
            required_tokens = max(minimum_tokens, estimated_target_tokens + 40)
            return min(max_new_tokens, required_tokens), timeout_seconds
        if context is not None and context.kind.startswith("statement"):
            max_new_tokens = min(max_new_tokens, 128)
        return max_new_tokens, timeout_seconds

    @classmethod
    def from_execution_options(cls, options: Any) -> "QwenCandidateTransformer | None":
        settings = QwenSettings.from_execution_options(options)
        return cls(settings) if settings.enabled else None

    def prepare_action(
        self,
        *,
        language: str,
        current_source_code: str,
        reference_source_code: str,
        action: RefactoringAction,
    ) -> tuple[RefactoringAction, Dict[str, Any]]:
        """Resolve hint-only planner data against the original source snapshot.

        RDP plans commonly provide only ``hint`` and an original line number.
        Earlier transformations can move that line before the model runs. For
        Java Introduce Constant, derive the typed literal and stable owning
        member from the original snapshot, then carry those semantic anchors
        into target resolution against the current snapshot.
        """

        normalized_language = str(language or "").strip().lower()
        if normalized_language != "java" or action.action_type not in {
            "introduce_constant",
            "extract_constant",
        }:
            return action, {}

        parameters = dict(action.parameters or {})
        literal_values = _planned_literal_values(parameters)
        if len(literal_values) != 1:
            return action, {}

        from ..transformers import java_transformers

        planned_literal = literal_values[0]
        resolved_literal = planned_literal
        source_line, _ = _line_range(parameters)
        resolution = "explicit_literal"

        # CUQA/RDP can describe digits inside a Java string as a magic number.
        # Match the deterministic transformer's behavior by extracting the
        # complete string token, preserving its Java type and runtime value.
        if (
            source_line is not None
            and isinstance(planned_literal, (int, float))
            and not isinstance(planned_literal, bool)
            and parameters.get("allow_string_literal_extraction", True)
        ):
            code_occurrences = java_transformers._numeric_literal_occurrences(
                reference_source_code,
                planned_literal,
                source_line=source_line,
            )
            number_pattern = re.compile(
                rf"(?<![A-Za-z0-9_.]){re.escape(str(planned_literal))}"
                rf"(?![A-Za-z0-9_.])"
            )
            string_candidates = [
                value
                for value, line_no, _start, _end
                in java_transformers._iter_java_string_literals(reference_source_code)
                if line_no == source_line and number_pattern.search(value)
            ]
            if not code_occurrences and len(string_candidates) == 1:
                resolved_literal = string_candidates[0]
                resolution = "rdp_numeric_hint_resolved_to_java_string"
            elif parameters.get("literal_value") is None:
                resolution = "rdp_hint_numeric_literal"
        elif parameters.get("literal_value") is None:
            resolution = "rdp_hint_literal"

        requested_name = str(
            parameters.get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        constant_name = java_transformers._normalize_constant_name(
            requested_name,
            resolved_literal,
        )
        if (
            isinstance(resolved_literal, str)
            and re.fullmatch(
                r"(?:THRESHOLD_LIMIT|MAGIC_NUMBER|CONSTANT_NUMBER)_[A-Z0-9_]+",
                constant_name,
            )
        ):
            constant_name = java_transformers._constant_name_from_value(
                resolved_literal
            )

        # The rule transformer gives a later action a unique suffix when an
        # earlier action has already used the requested name for a *different*
        # value. Until now the model prompt retained the colliding RDP name,
        # inviting it to emit a second field declaration. The candidate gate
        # correctly rejected that duplicate as ambiguous. Mirror the rule
        # transformer's name resolution before prompting the model.
        literal_text = java_transformers._to_java_literal(resolved_literal)
        existing_declarations = list(
            _java_constant_declaration_pattern(constant_name).finditer(
                current_source_code
            )
        )
        conflicting_constant_name = any(
            re.sub(r"\s+", "", match.group("initializer"))
            != re.sub(r"\s+", "", literal_text)
            for match in existing_declarations
        )
        resolved_name_before_collision = constant_name
        if conflicting_constant_name:
            constant_name = java_transformers._unique_constant_name(
                current_source_code,
                constant_name,
            )

        parameters["literal_value"] = resolved_literal
        parameters["constant_name"] = constant_name
        prepared = replace(action, parameters=parameters)
        reference_context = self.resolve_target_context(
            language="java",
            source_code=reference_source_code,
            action=prepared,
        )
        if reference_context is not None:
            qualified_name = reference_context.name.split(":", 1)[0]
            if "." in qualified_name:
                owner_name, member_name = qualified_name.split(".", 1)
                parameters["source_class"] = owner_name
                if reference_context.kind.startswith("field"):
                    parameters["field_name"] = member_name
                elif reference_context.kind.startswith(("method", "statement")):
                    parameters["source_method"] = member_name
                    parameters["method"] = member_name
            prepared = replace(action, parameters=parameters)

        return prepared, {
            "status": "prepared",
            "strategy": "reference_snapshot_semantic_target",
            "literal_resolution": resolution,
            "planned_literal": planned_literal,
            "resolved_literal": resolved_literal,
            "requested_constant_name": requested_name,
            "resolved_constant_name": constant_name,
            "constant_name_collision_resolved": conflicting_constant_name,
            "conflicting_constant_name": (
                resolved_name_before_collision if conflicting_constant_name else ""
            ),
            "reference_target": (
                reference_context.name if reference_context is not None else ""
            ),
            "reference_target_kind": (
                reference_context.kind if reference_context is not None else ""
            ),
            "current_source_changed": current_source_code != reference_source_code,
        }

    @staticmethod
    def _safe_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Keep model context useful without leaking arbitrary nested payloads."""

        allowed = {
            "source_file", "source_line", "start_line", "end_line", "target_lines",
            "source_class", "destination_class", "source_method", "method",
            "method_name", "function", "function_name", "target_class",
            "target_method", "new_method_name", "new_name", "old_name",
            "constant_name", "literal_value", "literal_values", "old_literal",
            "new_literal", "parameter_object_name", "normalization",
            "class_to_inline", "destination_parameter", "safe_alternative",
            "unsafe_function", "original_exception_type", "target_exception_type",
            "handler_name", "target_name", "target_kind", "symbol", "variable",
            "variable_name", "field", "field_name", "class_name", "class",
            "dead_code_kind", "dead_code_target_resolution",
            "target_statement_fingerprint", "requested_dead_code_method",
            "requested_dead_code_class", "resolved_dead_code_method",
            "resolved_dead_code_class", "description", "smell", "smell_type",
            "new_subclass_name", "facade_class_name", "parent_class",
            "value_object_name", "query_method_name", "replacement_method",
            "parameter_name", "query_expression", "query_parameters",
            "temp_name", "temporary_name", "destination_file",
            "destination_module", "header_file", "functions_to_extract",
            "methods_to_extract", "globals_to_extract", "fields_to_extract",
            "target_range", "source_lines", "semantic_recovery_required",
        }
        normalized: Dict[str, Any] = {}
        for key in sorted(allowed & set(parameters)):
            value = parameters[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                normalized[key] = value
            elif isinstance(value, (list, tuple)) and len(value) <= 16:
                normalized[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
        return normalized

    def build_prompt(
        self,
        *,
        language: str,
        source_code: str,
        action: RefactoringAction,
        current_file_name: str,
    ) -> str:
        """Build a stable training/inference representation from an RDP action."""

        refactoring = str(action.source_refactoring or action.action_type or "").strip()
        target = json.dumps(
            self._safe_parameters(dict(action.parameters or {})),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        parts = [
            "<sctva-task>",
            f"language: {language}",
            f"refactoring: {refactoring}",
            f"action_type: {action.action_type}",
            f"file: {current_file_name}",
            f"target: {target}",
            "constraints: preserve behavior; preserve public API unless the refactoring explicitly changes it; modify only the requested target; return complete transformed source code only.",
            "<source>",
            source_code,
            "</source>",
            "</sctva-task>",
        ]
        if language.lower() == "java" and action.action_type == "extract_method":
            helper_name = str(
                (action.parameters or {}).get("new_method_name")
                or (action.parameters or {}).get("extracted_method_name")
                or ""
            ).strip()
            helper_text = (
                f" named {helper_name}" if helper_name else " with one new private helper"
            )
            parts.insert(
                7,
                "Java Extract Method: retain the original method signature, move one cohesive "
                "statement block into a sibling private method"
                + helper_text
                + ", update the original method to call it, and preserve control flow, scope, "
                "static/instance semantics, and exceptions.",
            )
        return "\n".join(parts)

    def resolve_target_context(
        self,
        *,
        language: str,
        source_code: str,
        action: RefactoringAction,
    ) -> QwenTargetContext | None:
        """Resolve a replaceable target from the current transformed source."""

        normalized_language = str(language or "").strip().lower()
        if normalized_language == "python":
            return _python_context(source_code, action)
        if normalized_language == "java":
            return _java_context(source_code, action)
        if normalized_language == "c":
            return _c_context(source_code, action)
        return None

    def build_target_prompt(
        self,
        *,
        language: str,
        action: RefactoringAction,
        current_file_name: str,
        context: QwenTargetContext,
        include_surrounding_context: bool = True,
        validation_feedback: str = "",
        previous_candidate: str = "",
    ) -> str:
        """Build a compact prompt whose output replaces one exact source range."""

        refactoring = str(action.source_refactoring or action.action_type or "").strip()
        target = json.dumps(
            self._safe_parameters(dict(action.parameters or {})),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if str(language).lower() == "java" and action.action_type in {
            "introduce_constant",
            "extract_constant",
        }:
            from ..transformers import java_transformers

            parameters = action.parameters or {}
            literal_values = _planned_literal_values(parameters)
            if len(literal_values) == 1:
                literal_value = literal_values[0]
                constant_name = str(
                    parameters.get("constant_name") or "EXTRACTED_CONSTANT"
                ).strip()
                java_type = java_transformers._java_type_for_literal(literal_value)
                java_literal = java_transformers._to_java_literal(literal_value)
                required_declaration = (
                    f"private static final {java_type} {constant_name} = "
                    f"{java_literal};"
                )
                parts = [
                    "<sctva-target-task>",
                    "Java Introduce Constant. Return Java source only; no Markdown or explanation.",
                    f"resolved_target: {context.kind}:{context.name}",
                    f"required-declaration: {required_declaration}",
                    (
                        f"Replace only the planned literal {java_literal} in target-source "
                        f"with {constant_name}. Return only the complete transformed target-source. "
                        "Do not emit the constant declaration; SCTVA attaches required-declaration "
                        "after verifying that the returned target performs exactly the planned "
                        "replacement. Do not emit an enclosing class declaration or an "
                        "enclosing-class closing brace. Preserve every other token."
                    ),
                ]
                if validation_feedback:
                    parts.append(
                        "validator-feedback: "
                        + re.sub(r"\s+", " ", validation_feedback).strip()[:500]
                    )
                    # A small CPU model is much more reliable when a rejected
                    # constant candidate is repaired from one exact, bounded
                    # source-to-source template rather than prose alone. The
                    # normal candidate gate still proves the returned output
                    # against the original source before it can be accepted.
                    source_line = parameters.get("source_line")
                    relative_line: int | None = None
                    if isinstance(source_line, (int, float)):
                        relative_line = int(source_line) - context.start_line + 1
                        if relative_line <= 0:
                            relative_line = None
                    repaired_target, replacements = java_transformers._replace_literal(
                        context.source,
                        literal_value,
                        constant_name,
                        relative_line,
                    )
                    if replacements <= 0:
                        repaired_target, replacements = java_transformers._replace_literal(
                            context.source,
                            literal_value,
                            constant_name,
                            None,
                        )
                    if replacements == 1:
                        parts.extend((
                            "repair-template: copy only this exact transformed target; return no "
                            "constant declaration, other source, or explanation.",
                            "<required-repair-target>",
                            repaired_target.lstrip("\r\n"),
                            "</required-repair-target>",
                        ))
                parts.extend((
                    "<target-source>",
                    context.source,
                    "</target-source>",
                    "</sctva-target-task>",
                ))
                return "\n".join(parts)

        if str(language).lower() == "c" and action.action_type in {
            "introduce_constant",
            "extract_constant",
        }:
            from ..transformers import c_transformers

            parameters = action.parameters or {}
            literal_values = _planned_literal_values(parameters)
            if len(literal_values) == 1:
                literal_value = c_transformers.normalize_planned_numeric_literal(
                    literal_values[0]
                )
                if isinstance(literal_value, (int, float)) and not isinstance(
                    literal_value, bool
                ):
                    constant_name = str(
                        parameters.get("constant_name") or "EXTRACTED_CONSTANT"
                    ).strip()
                    literal_text = c_transformers._to_c_literal(literal_value)
                    required_macro = f"#define {constant_name} {literal_text}"
                    parts = [
                        "<sctva-target-task>",
                        "C Introduce Constant. Return C source only; no Markdown or explanation.",
                        f"required-macro: {required_macro}",
                        (
                            f"Replace only the numeric token {literal_text} in target-source "
                            f"with {constant_name}. Return exactly the required macro first, "
                            "then the complete transformed target-source. Preserve every other "
                            "token. Do not add includes, functions, variables, logging, comments, "
                            "examples, or an enclosing program. Emit the required macro exactly once."
                        ),
                    ]
                    if validation_feedback:
                        parts.append(
                            "validator-feedback: "
                            + re.sub(r"\s+", " ", validation_feedback).strip()[:500]
                        )
                    parts.extend((
                        "<target-source>",
                        context.source,
                        "</target-source>",
                        "</sctva-target-task>",
                    ))
                    return "\n".join(parts)

        parts = [
            "<sctva-target-task>",
            f"language: {language}",
            f"refactoring: {refactoring}",
            f"action_type: {action.action_type}",
            f"file: {current_file_name}",
            f"target: {target}",
            f"resolved_target: {context.kind}:{context.name}:{context.start_line}-{context.end_line}",
            "rdp-contract: perform exactly the requested action on the resolved target; do not add other refactorings or alter unrelated declarations.",
            "constraints: preserve behavior, public API, control flow, variable scope, exception behavior, and indentation; modify only target-source; return replacement source for target-source only; do not return a diff, markdown, explanation, or the complete file.",
        ]
        language_contracts = {
            "python": (
                "python-contract: return one syntactically complete replacement for the exact "
                "resolved AST target. Preserve decorators, async state, signature, type hints, "
                "indentation, imports, exception behavior, evaluation order, and local scope "
                "unless the RDP action explicitly changes one of them."
            ),
            "java": (
                "java-contract: return the complete resolved class declaration when resolved_target "
                "is a class; otherwise return only the resolved member or source span without its "
                "enclosing class. Preserve annotations, modifiers, generic parameters, throws "
                "clauses, visibility, overload resolution, exceptions, and evaluation order unless "
                "the requested refactoring explicitly changes one of them."
            ),
            "c": (
                "c-contract: return only the exact resolved declaration, function, or statement "
                "span. Preserve preprocessor directives, macros, storage qualifiers, pointer and "
                "array declarators, ABI-visible signatures, side effects, and evaluation order. "
                "Do not add includes or global declarations unless the RDP action requires them."
            ),
        }
        language_contract = language_contracts.get(str(language).lower())
        if language_contract:
            parts.append(language_contract)
        if str(language).lower() == "java":
            action_requirement = _JAVA_ACTION_REQUIREMENTS.get(action.action_type)
            if action_requirement:
                parts.append(
                    "java-action-requirement: " + action_requirement
                    + " Return a real transformed replacement; an unchanged target does not satisfy the action."
                )
        if validation_feedback:
            parts.extend((
                "<validation-feedback>",
                validation_feedback[:3000],
                "</validation-feedback>",
                "repair-requirement: correct every reported issue while still performing exactly the RDP action.",
            ))
        if previous_candidate:
            parts.extend((
                "<rejected-target-replacement>",
                previous_candidate[:12000],
                "</rejected-target-replacement>",
            ))
        if str(language).lower() == "java" and action.action_type == "extract_method":
            helper_name = str(
                (action.parameters or {}).get("new_method_name")
                or (action.parameters or {}).get("extracted_method_name")
                or ""
            ).strip()
            helper_requirement = (
                f" Extract the selected statements into a new private class method named {helper_name}."
                if helper_name
                else " Extract the selected statements into one new private helper method on the class."
            )
            parts.append(
                "java-extract-method requirement: target-source is exactly one complete class "
                "method member. Return exactly two class members: first the rewritten original "
                "method with its signature, annotations, modifiers, and behavior preserved; then "
                "one new private sibling helper containing only the moved cohesive statements. "
                "Replace the moved statements with exactly one helper call. Do not return the "
                "enclosing class, imports, fields, constructors, or unrelated methods. Preserve "
                "static/instance access, return values, exceptions, evaluation order, side "
                "effects, and local-variable scope. Keep moved statements and all untouched "
                "statements verbatim except for required parameter, return-value, and helper-call "
                "plumbing; do not change existing literals, operators, or invoked method names."
                + helper_requirement
            )
        if str(language).lower() == "java" and action.action_type in {
            "introduce_constant", "extract_constant",
        }:
            parameters = action.parameters or {}
            constant_name = str(
                parameters.get("constant_name") or "EXTRACTED_CONSTANT"
            ).strip()
            literal_value = parameters.get("literal_value")
            if literal_value is None:
                raw_values = parameters.get("literal_values")
                if isinstance(raw_values, list) and len(raw_values) == 1:
                    literal_value = raw_values[0]
            if isinstance(literal_value, bool):
                java_type = "boolean"
                java_literal = "true" if literal_value else "false"
            elif isinstance(literal_value, int):
                java_type = (
                    "int"
                    if -(2**31) <= literal_value <= (2**31 - 1)
                    else "long"
                )
                java_literal = str(literal_value)
            elif isinstance(literal_value, float):
                java_type = "double"
                java_literal = repr(literal_value)
            elif isinstance(literal_value, str):
                java_type = "String"
                java_literal = json.dumps(literal_value, ensure_ascii=True)
            elif literal_value is None:
                java_type = "Object"
                java_literal = "null"
            else:
                java_type = "Object"
                java_literal = json.dumps(str(literal_value), ensure_ascii=True)
            required_declaration = (
                f"private static final {java_type} {constant_name} = "
                f"{java_literal};"
            )
            parts.append(
                "java-introduce-constant requirement: target-source is exactly one complete "
                "Java class member. Return exactly two class members: first one private static "
                "final constant declaration copied exactly from required-declaration; then "
                "the complete original target member with only the planned literal occurrence "
                "replaced by that constant. Do not return the enclosing class, package, imports, "
                "or unrelated members. Emit the required declaration exactly once and do not "
                "append an enclosing-class closing brace. Do not change any other token, literal, operator, call, "
                "modifier, annotation, initializer, or statement. "
                f"required-declaration: {required_declaration}"
            )
        if str(language).lower() == "c" and action.action_type in {
            "introduce_constant", "extract_constant",
        }:
            constant_name = str((action.parameters or {}).get("constant_name") or "EXTRACTED_CONSTANT")
            literal_value = (action.parameters or {}).get("literal_value")
            if literal_value is None:
                raw_values = (action.parameters or {}).get("literal_values")
                if isinstance(raw_values, list) and len(raw_values) == 1:
                    literal_value = raw_values[0]
            if literal_value is None:
                hint_values = re.findall(
                    r"-?\d+(?:\.\d+)?",
                    str((action.parameters or {}).get("hint") or ""),
                )
                if len(hint_values) == 1:
                    literal_value = hint_values[0]
            parts.append(
                "required-c-output: preserve the entire target-source span; do not return a constant declaration alone. "
                f"Add '#define {constant_name} {literal_value}' immediately before the preserved target and replace only the requested numeric token with {constant_name}."
            )
        if str(language).lower() == "c" and action.action_type == "replace_temp_with_query":
            parts.append(
                "c-replace-temp-with-query requirement: identify one local temporary initialized by a pure expression. "
                "Create one file-local static query helper returning exactly that expression, with only the needed "
                "unchanged function parameters. Remove the temporary declaration and replace its later uses with the "
                "helper call. Do not select an expression containing a call, assignment, increment, decrement, array "
                "access, or a dependency that is later mutated."
            )
        if str(language).lower() == "c" and action.action_type == "replace_parameter_with_method_call":
            parts.append(
                "c-replace-parameter-with-method-call requirement: perform this only when target metadata identifies "
                "parameter_name and a pure query_expression, and the target is a static function whose complete local "
                "call-site set can be updated. Create one static query helper, remove only that parameter from the "
                "signature and each local call, and replace only its function-body uses with the helper call. Otherwise "
                "return the unchanged target rather than guessing an API change."
            )
        if include_surrounding_context and context.surrounding_context:
            parts.extend((
                "<declaration-context>",
                context.surrounding_context,
                "</declaration-context>",
            ))
        parts.extend((
            "<target-source>",
            context.source,
            "</target-source>",
            "</sctva-target-task>",
        ))
        return "\n".join(parts)

    @staticmethod
    def _rough_token_count(text: str) -> int:
        return len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]", text))

    def _encode_prompt(self, prompt: str) -> tuple[Any, int]:
        if self._generator is not None:
            return None, self._rough_token_count(prompt)
        prompt = self._model_prompt(prompt)
        encoded = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=False,
            verbose=False,
        )
        return encoded, int(encoded["input_ids"].shape[-1])

    @staticmethod
    def _merge_target_output(
        source_code: str,
        context: QwenTargetContext,
        output: str,
    ) -> str:
        replacement = output
        marker = re.fullmatch(
            r"<target-replacement>\s*(.*?)\s*</target-replacement>",
            replacement,
            re.DOTALL | re.IGNORECASE,
        )
        if marker:
            replacement = marker.group(1)
        original_first_line = next(
            (line for line in context.source.splitlines() if line.strip()),
            "",
        )
        original_indent_match = re.match(r"[ \t]*", original_first_line)
        original_indent = original_indent_match.group(0) if original_indent_match else ""
        replacement = textwrap.dedent(replacement)
        if original_indent:
            replacement = "".join(
                original_indent + line if line.strip() else line
                for line in replacement.splitlines(keepends=True)
            )
        original_newline = "\r\n" if "\r\n" in context.source else "\n"
        if context.source.endswith(("\n", "\r")) and not replacement.endswith(("\n", "\r")):
            replacement += original_newline
        return source_code[:context.start] + replacement + source_code[context.end:]

    @staticmethod
    def _canonicalize_java_constant_target_identifier(
        *,
        language: str,
        output: str,
        context: QwenTargetContext | None,
        action: RefactoringAction,
    ) -> tuple[str, Dict[str, Any]]:
        """Align a model-invented constant alias with the exact RDP contract.

        This is deliberately narrower than repairing source. An alias is
        accepted only when removing its optional declaration and restoring
        the planned literal reconstructs the resolved target exactly, apart
        from formatting whitespace. Any unrelated model edit makes the proof
        fail and leaves the candidate for the normal checkpoint to reject.
        """

        if (
            str(language).lower() != "java"
            or not output
            or context is None
            or context.kind not in {
                "field_with_sibling_constant",
                "method_with_sibling_constant",
                "statement_with_sibling_constant",
            }
            or action.action_type not in {"introduce_constant", "extract_constant"}
        ):
            return output, {}

        parameters = action.parameters or {}
        planned_name = str(
            parameters.get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", planned_name):
            return output, {}
        if re.search(rf"\b{re.escape(planned_name)}\b", output):
            return output, {}

        literal_values = _planned_literal_values(parameters)
        if len(literal_values) != 1:
            return output, {}

        from ..transformers import java_transformers

        literal_text = java_transformers._to_java_literal(literal_values[0])
        source_identifiers = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", context.source))
        java_words = {
            "abstract", "assert", "boolean", "break", "byte", "case", "catch",
            "char", "class", "const", "continue", "default", "do", "double",
            "else", "enum", "extends", "final", "finally", "float", "for",
            "goto", "if", "implements", "import", "instanceof", "int",
            "interface", "long", "native", "new", "package", "private",
            "protected", "public", "record", "return", "short", "static",
            "strictfp", "super", "switch", "synchronized", "this", "throw",
            "throws", "transient", "try", "var", "void", "volatile", "while",
            "true", "false", "null", "String", "Object",
        }
        output_identifiers = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", output))
        aliases = sorted(output_identifiers - source_identifiers - java_words - {planned_name})
        viable: list[tuple[str, str]] = []

        def replace_code_identifier(
            source: str,
            identifier: str,
            replacement: str,
        ) -> tuple[str, int]:
            result: list[str] = []
            state = "code"
            index = 0
            replacements = 0
            while index < len(source):
                char = source[index]
                nxt = source[index + 1] if index + 1 < len(source) else ""
                if state == "code":
                    if char == "/" and nxt == "/":
                        result.append("//")
                        state = "line_comment"
                        index += 2
                        continue
                    if char == "/" and nxt == "*":
                        result.append("/*")
                        state = "block_comment"
                        index += 2
                        continue
                    if source.startswith('"""', index):
                        result.append('"""')
                        state = "text_block"
                        index += 3
                        continue
                    if char == '"':
                        result.append(char)
                        state = "string"
                        index += 1
                        continue
                    if char == "'":
                        result.append(char)
                        state = "char"
                        index += 1
                        continue
                    if char.isalpha() or char in "_$":
                        end = index + 1
                        while end < len(source) and (
                            source[end].isalnum() or source[end] in "_$"
                        ):
                            end += 1
                        token = source[index:end]
                        if token == identifier:
                            result.append(replacement)
                            replacements += 1
                        else:
                            result.append(token)
                        index = end
                        continue
                    result.append(char)
                    index += 1
                    continue

                result.append(char)
                if state == "line_comment" and char in "\r\n":
                    state = "code"
                elif state == "block_comment" and char == "*" and nxt == "/":
                    result.append("/")
                    state = "code"
                    index += 2
                    continue
                elif state == "text_block" and source.startswith('"""', index):
                    result.extend(('"', '"'))
                    state = "code"
                    index += 3
                    continue
                elif state in {"string", "char"}:
                    quote = '"' if state == "string" else "'"
                    if char == "\\" and index + 1 < len(source):
                        result.append(source[index + 1])
                        index += 2
                        continue
                    if char == quote:
                        state = "code"
                index += 1
            return "".join(result), replacements

        for alias in aliases:
            declaration_pattern = re.compile(
                rf"(?m)^[ \t]*(?P<modifiers>(?:(?:public|protected|private|static|final)\s+)*)"
                rf"[A-Za-z_$][A-Za-z0-9_$<>\[\].?]*\s+{re.escape(alias)}\s*=\s*"
                rf"{re.escape(literal_text)}\s*;[ \t]*(?://[^\r\n]*)?(?:\r?\n)?"
            )
            declarations = list(declaration_pattern.finditer(output))
            if len(declarations) > 1:
                continue
            body = output
            if declarations:
                modifiers = set(declarations[0].group("modifiers").split())
                if not {"static", "final"}.issubset(modifiers):
                    continue
                declaration = declarations[0]
                body = body[:declaration.start()] + body[declaration.end():]
            restored, restored_count = replace_code_identifier(
                body,
                alias,
                literal_text,
            )
            if restored_count <= 0:
                continue
            if re.sub(r"\s+", "", restored) != re.sub(r"\s+", "", context.source):
                continue
            aligned, aligned_count = replace_code_identifier(
                body,
                alias,
                planned_name,
            )
            if aligned_count != restored_count:
                continue
            aligned = aligned.strip()
            viable.append((alias, aligned))

        if len(viable) != 1:
            return output, {}
        alias, aligned = viable[0]
        return aligned, {
            "status": "normalized",
            "reason": "model_constant_identifier_aligned_to_rdp_contract",
            "contract": "java_introduce_constant_target_identifier",
            "model_identifier": alias,
            "planned_identifier": planned_name,
        }

    @staticmethod
    def _complete_java_constant_target_output(
        *,
        language: str,
        output: str,
        context: QwenTargetContext | None,
        action: RefactoringAction,
    ) -> tuple[str, Dict[str, Any]]:
        """Complete a declaration-only Java constant candidate safely.

        Qwen occasionally follows the sibling-declaration requirement but
        omits the rewritten target member. The exact member is already
        resolved by SCTVA, so complete only that missing piece with the
        planned literal substitution. Ambiguous or unrelated output is left
        untouched and will be rejected by the normal action checkpoint.
        """

        if (
            str(language).lower() != "java"
            or context is None
            or context.kind not in {
                "field_with_sibling_constant",
                "method_with_sibling_constant",
                "statement_with_sibling_constant",
            }
            or action.action_type not in {"introduce_constant", "extract_constant"}
        ):
            return output, {}

        parameters = action.parameters or {}
        constant_name = str(
            parameters.get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", constant_name):
            return output, {}
        literal_value = parameters.get("literal_value")
        if literal_value is None:
            literal_values = parameters.get("literal_values")
            if isinstance(literal_values, list) and len(literal_values) == 1:
                literal_value = literal_values[0]
        if literal_value is None:
            return output, {}

        from ..transformers import java_transformers

        literal_text = java_transformers._to_java_literal(literal_value)
        java_type = java_transformers._java_type_for_literal(literal_value)
        declaration = (
            f"private static final {java_type} {constant_name} = {literal_text};"
        )
        # Match the planned identifier rather than requiring the model to have
        # already selected the exact Java type/modifiers/value. The canonical
        # declaration below is derived from the RDP action, never from model
        # text, and the normal action gate verifies it after target merging.
        declaration_pattern = _java_constant_declaration_pattern(constant_name)

        # A timed-out generation can stop after producing the complete,
        # correct declaration but before its final semicolon. Repair only an
        # output that consists solely of that exact planned declaration.
        incomplete_declaration = re.fullmatch(
            rf"\s*(?:(?:public|protected|private|static|final)\s+)*"
            rf"[A-Za-z_$][A-Za-z0-9_$<>\[\].?]*\s+"
            rf"{re.escape(constant_name)}\s*=\s*[^;{{}}\r\n]+?"
            rf"\s*(?://[^\r\n]*)?\s*",
            output,
        )
        # CPU generation can stop immediately after ``=``. This remains
        # unambiguous evidence of the planned declaration only when its Java
        # type and requested identifier are both present. The value below is
        # always completed from the RDP contract, never guessed from text.
        declaration_prefix_only = re.fullmatch(
            rf"\s*(?:(?:public|protected|private|static|final)\s+)*"
            rf"{re.escape(java_type)}\s+{re.escape(constant_name)}\s*=\s*",
            output,
        )
        truncated_declaration_completed = False
        if (incomplete_declaration or declaration_prefix_only) and ";" not in output:
            output = declaration
            truncated_declaration_completed = True
        declarations = list(declaration_pattern.finditer(output))
        duplicate_declarations_normalized = False
        if len(declarations) > 1:
            body = output
            for candidate_declaration in reversed(declarations):
                body = (
                    body[:candidate_declaration.start()]
                    + body[candidate_declaration.end():]
                )
            newline = "\r\n" if "\r\n" in context.source else "\n"
            output = declaration + newline + body.lstrip("\r\n")
            declarations = list(declaration_pattern.finditer(output))
            duplicate_declarations_normalized = True

        # Older prompts asked for a complete method. Accept that response shape
        # after statement-level narrowing only when exactly one returned line
        # contains the planned identifier; the normal intent gate still proves
        # that restoring the literal reproduces the original target line.
        if context.kind == "statement_with_sibling_constant":
            body = declaration_pattern.sub("", output, count=1)
            usage_lines = [
                line
                for line in body.splitlines(keepends=True)
                if re.search(rf"\b{re.escape(constant_name)}\b", line)
            ]
            nonempty_lines = [line for line in body.splitlines() if line.strip()]
            if len(usage_lines) == 1 and len(nonempty_lines) > 1:
                newline = "\r\n" if "\r\n" in context.source else "\n"
                declaration_prefix = declaration + newline if declarations else ""
                output = declaration_prefix + usage_lines[0].strip("\r\n")
                declarations = list(declaration_pattern.finditer(output))

        output_without_declaration = (
            declaration_pattern.sub("", output, count=1)
            if declarations
            else output
        )
        target_uses_constant = re.search(
            rf"\b{re.escape(constant_name)}\b",
            output_without_declaration,
        ) is not None
        if declarations and target_uses_constant:
            if duplicate_declarations_normalized:
                return output, {
                    "status": "completed",
                    "reason": "duplicate_constant_declarations_collapsed",
                    "contract": "java_introduce_constant_target_member",
                    "replacements": 1,
                }
            return output, {}
        if not declarations and target_uses_constant:
            newline = "\r\n" if "\r\n" in context.source else "\n"
            return declaration + newline + output_without_declaration.lstrip("\r\n"), {
                "status": "completed",
                "reason": "missing_constant_declaration_restored",
                "contract": "java_introduce_constant_target_member",
                "declaration_source": "rdp_action_contract",
                "model_generated_target": True,
                "replacements": 1,
            }
        if not declarations:
            return output, {}

        source_line = parameters.get("source_line")
        relative_line: int | None = None
        if isinstance(source_line, (int, float)):
            relative_line = int(source_line) - context.start_line + 1
            if relative_line <= 0:
                relative_line = None
        rewritten_member, replacements = java_transformers._replace_literal(
            context.source,
            literal_value,
            constant_name,
            relative_line,
        )
        if replacements <= 0:
            rewritten_member, replacements = java_transformers._replace_literal(
                context.source,
                literal_value,
                constant_name,
                None,
            )
            if replacements != 1:
                return output, {}

        newline = "\r\n" if "\r\n" in context.source else "\n"
        completed = declaration + newline + rewritten_member.lstrip("\r\n")
        return completed, {
            "status": "completed",
            "reason": (
                "truncated_declaration_and_target_completed"
                if truncated_declaration_completed
                else "missing_target_member_restored"
            ),
            "contract": "java_introduce_constant_target_member",
            "replacements": replacements,
        }

    @staticmethod
    def _recover_java_constant_from_complete_output(
        *,
        language: str,
        output: str,
        context: QwenTargetContext | None,
        action: RefactoringAction,
    ) -> tuple[str, Dict[str, Any]]:
        """Project a complete Java response back to its proven target span.

        Recovery is permitted only when the response contains the exact target
        obtained by applying the planned literal substitution to the resolved
        source. Other model edits are discarded rather than merged.
        """

        if (
            str(language).lower() != "java"
            or context is None
            or context.kind not in {
                "field_with_sibling_constant",
                "method_with_sibling_constant",
                "statement_with_sibling_constant",
            }
            or action.action_type not in {"introduce_constant", "extract_constant"}
        ):
            return output, {}

        parameters = action.parameters or {}
        constant_name = str(
            parameters.get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", constant_name):
            return output, {}
        literal_values = _planned_literal_values(parameters)
        if len(literal_values) != 1:
            return output, {}

        from ..transformers import java_transformers

        literal_value = literal_values[0]
        source_line = parameters.get("source_line")
        relative_line: int | None = None
        if isinstance(source_line, (int, float)):
            relative_line = int(source_line) - context.start_line + 1
            if relative_line <= 0:
                relative_line = None
        rewritten_target, replacements = java_transformers._replace_literal(
            context.source,
            literal_value,
            constant_name,
            relative_line,
        )
        if replacements <= 0:
            rewritten_target, replacements = java_transformers._replace_literal(
                context.source,
                literal_value,
                constant_name,
                None,
            )
        if replacements != 1:
            return output, {}

        evidence_output = output
        if output.startswith(("diff --git", "--- ", "+++ ")):
            evidence_output = "\n".join(
                line
                for line in output.splitlines()
                if not line.startswith(("-", "@@", "diff --git", "index "))
                and not line.startswith("+++ ")
            )
        compact_output = re.sub(r"\s+", "", evidence_output)
        compact_target = re.sub(r"\s+", "", rewritten_target)
        compact_original_target = re.sub(r"\s+", "", context.source)
        declaration_pattern = _java_constant_declaration_pattern(constant_name)
        declaration_count = len(declaration_pattern.findall(evidence_output))
        transformed_target_present = bool(
            compact_target and compact_target in compact_output
        )
        unchanged_target_with_declaration = bool(
            declaration_count == 1
            and compact_original_target
            and compact_original_target in compact_output
        )
        if not transformed_target_present and not unchanged_target_with_declaration:
            return output, {}

        literal_text = java_transformers._to_java_literal(literal_value)
        java_type = java_transformers._java_type_for_literal(literal_value)
        declaration = (
            f"private static final {java_type} {constant_name} = {literal_text};"
        )
        newline = "\r\n" if "\r\n" in context.source else "\n"
        projected = declaration + newline + rewritten_target.lstrip("\r\n")
        return projected, {
            "status": "projected",
            "reason": (
                "complete_source_projected_to_exact_target"
                if transformed_target_present
                else "complete_source_declaration_intent_completed"
            ),
            "contract": "java_introduce_constant_target_member",
            "replacements": replacements,
        }

    @staticmethod
    def _complete_c_constant_target_output(
        *,
        language: str,
        output: str,
        context: QwenTargetContext | None,
        action: RefactoringAction,
    ) -> tuple[str, Dict[str, Any]]:
        """Complete a macro-only C constant candidate without free-form edits."""

        if (
            str(language).lower() != "c"
            or context is None
            or action.action_type not in {"introduce_constant", "extract_constant"}
        ):
            return output, {}

        parameters = action.parameters or {}
        constant_name = str(
            parameters.get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", constant_name):
            return output, {}

        literal_values = _planned_literal_values(parameters)
        if len(literal_values) != 1:
            return output, {}

        from ..transformers import c_transformers

        literal_value = c_transformers.normalize_planned_numeric_literal(
            literal_values[0]
        )
        if isinstance(literal_value, bool) or not isinstance(
            literal_value, (int, float)
        ):
            return output, {}
        literal_text = c_transformers._to_c_literal(literal_value)
        macro_pattern = re.compile(
            rf"(?m)^[ \t]*#\s*define\s+{re.escape(constant_name)}\s+"
            rf"{re.escape(literal_text)}[ \t]*(?:\r?\n)?"
        )
        macros = list(macro_pattern.finditer(output))
        if len(macros) != 1:
            return output, {}

        remainder = macro_pattern.sub("", output, count=1).strip()
        # Completion is deliberately limited to a declaration-only response.
        # If the model returned any target body, the normal intent gate must
        # judge that body instead of silently replacing it with rule output.
        if remainder:
            return output, {}

        source_line = parameters.get("source_line")
        relative_line: int | None = None
        if isinstance(source_line, (int, float)):
            relative_line = int(source_line) - context.start_line + 1
            if relative_line <= 0:
                relative_line = None
        completed, replacements, metadata = c_transformers.apply_introduce_constant(
            context.source,
            literal_value,
            constant_name,
            relative_line,
            reference_source_code=context.source,
        )
        if replacements <= 0:
            return output, {}
        return completed.rstrip("\r\n"), {
            "status": "completed",
            "reason": "missing_target_statement_restored",
            "contract": "c_introduce_constant_target_statement",
            "replacements": replacements,
            "target_context": metadata.get("target_context"),
        }

    def _load(self) -> None:
        if self._generator is not None or self._model is not None:
            return
        load_started = time.monotonic()
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise RuntimeError(
                "QWEN_DEPENDENCIES_NOT_INSTALLED: install requirements-qwen.txt"
            ) from exc

        requested = self.settings.device.lower()
        if requested == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        elif requested in {"cpu", "cuda"}:
            device = requested
        else:
            raise RuntimeError("QWEN_INVALID_DEVICE")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("QWEN_CUDA_UNAVAILABLE")

        requested_backend = self.settings.backend.strip().lower()
        if requested_backend not in {"auto", "pytorch", "openvino"}:
            raise RuntimeError("QWEN_INVALID_BACKEND")
        openvino_path = Path(self.settings.openvino_model_path).expanduser() if (
            self.settings.openvino_model_path
        ) else None
        if requested_backend == "auto":
            backend = (
                "openvino"
                if device == "cpu" and openvino_path is not None and openvino_path.exists()
                else "pytorch"
            )
        else:
            backend = requested_backend
        if backend == "openvino" and device != "cpu":
            raise RuntimeError("QWEN_OPENVINO_REQUIRES_CPU_DEVICE")
        if backend == "openvino" and (
            openvino_path is None or not openvino_path.exists()
        ):
            raise RuntimeError("QWEN_OPENVINO_MODEL_PATH_NOT_FOUND")

        effective_cpu_threads = min(
            self.settings.cpu_threads,
            max(1, os.cpu_count() or self.settings.cpu_threads),
        )
        cache_source = str(openvino_path or self.settings.model_id)
        if backend == "openvino":
            # Keep this profile separate from the prior BF16/U8 runtime so a
            # live process cannot reuse numerically reduced cached state.
            cache_source += "|fp32-accuracy-v1"
        cache_key = (
            backend,
            cache_source,
            device,
            bool(self.settings.allow_download),
            int(self.settings.max_input_tokens),
            effective_cpu_threads,
        )
        if self.settings.runtime_cache:
            with _MODEL_RUNTIME_CACHE_LOCK:
                cached = _MODEL_RUNTIME_CACHE.get(cache_key)
            if cached is not None:
                self._tokenizer = cached["tokenizer"]
                self._model = cached["model"]
                self._torch = cached["torch"]
                self._resolved_device = cached["device"]
                self._resolved_backend = cached["backend"]
                self._effective_max_input_tokens = cached["max_input_tokens"]
                self._runtime_inference_lock = cached["inference_lock"]
                self._runtime_cache_hit = True
                self._effective_cpu_threads = effective_cpu_threads
                if device == "cpu" and hasattr(torch, "set_num_threads"):
                    torch.set_num_threads(self._effective_cpu_threads)
                self._model_load_seconds = max(
                    0.0,
                    time.monotonic() - load_started,
                )
                return

        self._effective_cpu_threads = effective_cpu_threads
        if device == "cpu":
            torch.set_num_threads(self._effective_cpu_threads)
            try:
                torch.set_num_interop_threads(min(2, self._effective_cpu_threads))
            except RuntimeError:
                pass

        local_files_only = not self.settings.allow_download
        model_source = (
            str(openvino_path.resolve())
            if backend == "openvino" and openvino_path is not None
            else self.settings.model_id
        )
        if backend == "pytorch" and local_files_only and not Path(model_source).exists():
            from huggingface_hub import snapshot_download

            model_source = snapshot_download(
                repo_id=self.settings.model_id,
                local_files_only=True,
            )
        tokenizer_options: Dict[str, Any] = {
            "local_files_only": local_files_only,
        }
        if backend == "openvino":
            tokenizer_options["fix_mistral_regex"] = True
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            **tokenizer_options,
        )
        if backend == "openvino":
            try:
                from optimum.intel import OVModelForCausalLM
            except ImportError as exc:
                raise RuntimeError(
                    "QWEN_OPENVINO_DEPENDENCIES_NOT_INSTALLED: "
                    "install requirements-openvino.txt"
                ) from exc
            self._model = OVModelForCausalLM.from_pretrained(
                model_source,
                # Optimum Intel 1.x treats every model_id as a Hub repo during
                # its local_files_only pre-check. On Windows that turns an
                # absolute path into an invalid `models--C:\...` cache path.
                # The directory was validated above, and Optimum's normal
                # branch loads an existing directory without network access.
                local_files_only=False,
                export=False,
                device="CPU",
                compile=True,
                ov_config={
                    "INFERENCE_PRECISION_HINT": "f32",
                    "INFERENCE_NUM_THREADS": str(self._effective_cpu_threads),
                    "PERFORMANCE_HINT": "LATENCY",
                },
            )
        else:
            self._model = AutoModelForCausalLM.from_pretrained(
                model_source,
                local_files_only=local_files_only,
                torch_dtype="auto",
            )
            self._model.to(device)
            self._model.eval()
        if hasattr(self._model.config, "use_cache"):
            self._model.config.use_cache = True
        self._torch = torch
        self._resolved_device = device
        self._resolved_backend = backend
        model_limits: list[int] = []
        tokenizer_limit = getattr(self._tokenizer, "model_max_length", None)
        if isinstance(tokenizer_limit, int) and 0 < tokenizer_limit < 1_000_000:
            model_limits.append(tokenizer_limit)
        position_limit = getattr(self._model.config, "n_positions", None)
        if isinstance(position_limit, int) and position_limit > 0:
            model_limits.append(position_limit)
        model_input_limit = min(model_limits) if model_limits else None
        self._effective_max_input_tokens = min(
            self.settings.max_input_tokens,
            model_input_limit
            if isinstance(model_input_limit, int) and model_input_limit > 0
            else self.settings.max_input_tokens,
        )
        self._model_load_seconds = max(0.0, time.monotonic() - load_started)
        if self.settings.runtime_cache:
            runtime = {
                "tokenizer": self._tokenizer,
                "model": self._model,
                "torch": self._torch,
                "device": self._resolved_device,
                "backend": self._resolved_backend,
                "max_input_tokens": self._effective_max_input_tokens,
                "inference_lock": self._runtime_inference_lock,
            }
            with _MODEL_RUNTIME_CACHE_LOCK:
                existing = _MODEL_RUNTIME_CACHE.setdefault(cache_key, runtime)
            if existing is not runtime:
                self._tokenizer = existing["tokenizer"]
                self._model = existing["model"]
                self._torch = existing["torch"]
                self._resolved_device = existing["device"]
                self._resolved_backend = existing["backend"]
                self._effective_max_input_tokens = existing["max_input_tokens"]
                self._runtime_inference_lock = existing["inference_lock"]
                self._runtime_cache_hit = True

    @staticmethod
    def _normalise_model_output(output: str) -> str:
        value = str(output or "").strip()
        fences = list(re.finditer(
            r"```(?:[A-Za-z0-9_+.-]+)?\s*\n(.*?)\n?```",
            value,
            re.DOTALL,
        ))
        # Instruction-tuned models occasionally add one sentence around a
        # single code block. Extracting that sole block is unambiguous; with
        # multiple blocks we deliberately retain the output so syntax safety
        # rejects it instead of guessing which edit was intended.
        if len(fences) == 1:
            value = fences[0].group(1).strip()
        # A CPU generation timeout can leave only an opening fence, or a valid
        # target followed by an unmatched closing fence. Fence marker lines are
        # never valid source for a target replacement, so remove them without
        # guessing at any surrounding prose or code.
        value = re.sub(
            r"(?m)^[ \t]*```(?:[A-Za-z0-9_+.-]+)?[ \t]*(?:\r?\n|$)",
            "",
            value,
        ).strip()
        # The repair prompt names its exact-output block. Some instruction-
        # tuned checkpoints copy those marker lines with the source. They are
        # prompt syntax, never Java source, so remove only known standalone
        # markers before the target contract parser evaluates the replacement.
        value = re.sub(
            r"(?mi)^\s*</?(?:required-repair-output|required-repair-target|"
            r"rejected-target-replacement|"
            r"sctva-target-task|target-source)>\s*(?:\r?\n|$)",
            "",
            value,
        ).strip()
        for prefix in ("TRANSFORMED_SOURCE:", "TRANSFORMED CODE:", "OUTPUT:"):
            if value.upper().startswith(prefix):
                value = value[len(prefix):].lstrip("\r\n ")
        return value

    @staticmethod
    def _repair_java_target_suffix(
        language: str,
        output: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Remove only unmatched closing braces at the generated fragment suffix."""

        if str(language).lower() != "java" or not output:
            return output, {}

        stack: list[int] = []
        unmatched: list[int] = []
        state = "code"
        index = 0
        while index < len(output):
            char = output[index]
            nxt = output[index + 1] if index + 1 < len(output) else ""
            if state == "code":
                if char == "/" and nxt == "/":
                    state = "line_comment"
                    index += 2
                    continue
                if char == "/" and nxt == "*":
                    state = "block_comment"
                    index += 2
                    continue
                if char == '"':
                    state = "string"
                elif char == "'":
                    state = "char"
                elif char == "{":
                    stack.append(index)
                elif char == "}":
                    if stack:
                        stack.pop()
                    else:
                        unmatched.append(index)
            elif state == "line_comment":
                if char == "\n":
                    state = "code"
            elif state == "block_comment":
                if char == "*" and nxt == "/":
                    state = "code"
                    index += 2
                    continue
            elif state in {"string", "char"}:
                quote = '"' if state == "string" else "'"
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    state = "code"
            index += 1

        if not unmatched:
            return output, {}
        suffix = output[unmatched[0]:]
        if any(char not in "} \t\r\n" for char in suffix):
            return output, {}
        unmatched_positions = set(unmatched)
        repaired = "".join(
            char for position, char in enumerate(output)
            if position not in unmatched_positions
        ).rstrip()
        return repaired, {
            "status": "repaired",
            "reason": "unmatched_generated_suffix_brace_removed",
            "removed_braces": len(unmatched),
        }

    @staticmethod
    def _target_output_shape_issue(
        language: str,
        context: QwenTargetContext | None,
        output: str,
    ) -> str:
        """Reject complete-file output before it can be merged into one span."""

        if context is None or not output:
            return ""
        if str(language).lower() == "java" and context.kind in {
            "field_with_sibling_constant",
            "method_with_sibling_constant",
            "statement_with_sibling_constant",
        }:
            if re.search(
                r"(?m)^\s*(?:package\s+|import\s+|"
                r"(?:public\s+)?(?:class|interface|enum|record)\s+)",
                output,
            ):
                return "QWEN_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
        return ""

    @staticmethod
    def _target_merge_output(
        context: QwenTargetContext | None,
        output: str,
        action: RefactoringAction,
    ) -> str:
        """Keep a sibling declaration out of a statement replacement span."""

        if (
            context is None
            or context.kind != "statement_with_sibling_constant"
            or action.action_type not in {"introduce_constant", "extract_constant"}
        ):
            return output
        constant_name = str(
            (action.parameters or {}).get("constant_name") or "EXTRACTED_CONSTANT"
        ).strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", constant_name):
            return output
        declaration_pattern = _java_constant_declaration_pattern(constant_name)
        declarations = list(declaration_pattern.finditer(output))
        if len(declarations) != 1:
            return output
        return declaration_pattern.sub("", output, count=1).lstrip("\r\n")

    def _model_prompt(self, prompt: str) -> str:
        if not hasattr(self._tokenizer, "apply_chat_template"):
            return prompt
        return self._tokenizer.apply_chat_template(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a safe source-to-source refactoring engine. "
                        "Return only the requested transformed source, without explanation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tokenize=False,
            add_generation_prompt=True,
        )

    def propose(
        self,
        *,
        language: str,
        source_code: str,
        action: RefactoringAction,
        current_file_name: str,
        validation_feedback: str = "",
        previous_candidate: str = "",
    ) -> QwenCandidate:
        """Return one candidate or a traceable non-acceptance reason."""

        if not self.settings.enabled:
            return QwenCandidate(reason="QWEN_DISABLED")

        with self._lock:
            file_key = str(current_file_name or "<unknown>").replace("\\", "/").lower()
            file_attempt_count = self._file_attempt_counts.get(file_key, 0)
            if (
                self._attempt_count >= self.settings.max_actions_per_request
                or file_attempt_count >= self.settings.max_actions_per_file
                or (
                    self.settings.max_generation_seconds_per_request > 0
                    and self._generation_seconds_used
                    >= self.settings.max_generation_seconds_per_request
                )
            ):
                budget_reason = (
                    "QWEN_REQUEST_ACTION_BUDGET_EXHAUSTED"
                    if self._attempt_count >= self.settings.max_actions_per_request
                    else "QWEN_FILE_ACTION_BUDGET_EXHAUSTED"
                    if file_attempt_count >= self.settings.max_actions_per_file
                    else "QWEN_REQUEST_TIME_BUDGET_EXHAUSTED"
                )
                return QwenCandidate(
                    reason="QWEN_ACTION_BUDGET_EXHAUSTED",
                    metadata={
                        "provider": self.provider,
                        "model_id": self.settings.model_id,
                        "attempts_used": self._attempt_count,
                        "max_actions_per_request": self.settings.max_actions_per_request,
                        "file_attempts_used": file_attempt_count,
                        "max_actions_per_file": self.settings.max_actions_per_file,
                        "generation_seconds_used": round(
                            self._generation_seconds_used, 3
                        ),
                        "generation_seconds_limit": (
                            self.settings.max_generation_seconds_per_request
                        ),
                        "budget_reason": budget_reason,
                    },
                )
            self._attempt_count += 1
            self._file_attempt_counts[file_key] = file_attempt_count + 1
            attempt_number = self._attempt_count
            try:
                self._load()
                prompt = self.build_prompt(
                    language=language,
                    source_code=source_code,
                    action=action,
                    current_file_name=current_file_name,
                )
                encoded, input_tokens = self._encode_prompt(prompt)
                context: QwenTargetContext | None = None
                context_mode = "full_file"
                full_file_input_tokens = input_tokens
                resolved_context = self.resolve_target_context(
                    language=language,
                    source_code=source_code,
                    action=action,
                )
                # A resolved RDP target is always safer than whole-file
                # regeneration. It also prevents unrelated edits and makes
                # validator feedback actionable for one exact source span.
                use_target_context = resolved_context is not None
                if (
                    resolved_context is None
                    and action.action_type != "noop"
                ):
                    return QwenCandidate(
                        reason="QWEN_TARGET_CONTEXT_NOT_RESOLVED",
                        metadata={
                            "input_tokens": input_tokens,
                            "configured_max_input_tokens": self.settings.max_input_tokens,
                            "model_max_input_tokens": self._effective_max_input_tokens,
                            "context_mode": "target_span_unresolved",
                            "repair_attempt": bool(validation_feedback),
                        },
                    )
                if use_target_context:
                    context = resolved_context
                    if context is None:
                        return QwenCandidate(
                            reason="QWEN_TARGET_CONTEXT_NOT_RESOLVED",
                            metadata={
                                "input_tokens": input_tokens,
                                "configured_max_input_tokens": self.settings.max_input_tokens,
                                "model_max_input_tokens": self._effective_max_input_tokens,
                                "context_mode": "target_span_unresolved",
                            },
                        )
                    prompt = self.build_target_prompt(
                        language=language,
                        action=action,
                        current_file_name=current_file_name,
                        context=context,
                        validation_feedback=validation_feedback,
                        previous_candidate=previous_candidate,
                    )
                    encoded, input_tokens = self._encode_prompt(prompt)
                    if input_tokens > self._effective_max_input_tokens and context.surrounding_context:
                        prompt = self.build_target_prompt(
                            language=language,
                            action=action,
                            current_file_name=current_file_name,
                            context=context,
                            include_surrounding_context=False,
                            validation_feedback=validation_feedback,
                            previous_candidate=previous_candidate,
                        )
                        encoded, input_tokens = self._encode_prompt(prompt)
                    if input_tokens > self._effective_max_input_tokens:
                        return QwenCandidate(
                            reason="QWEN_TARGET_CONTEXT_TOO_LARGE",
                            metadata={
                                "input_tokens": input_tokens,
                                "full_file_input_tokens": full_file_input_tokens,
                                "configured_max_input_tokens": self.settings.max_input_tokens,
                                "model_max_input_tokens": self._effective_max_input_tokens,
                                "context_mode": "target_span",
                                "target_kind": context.kind,
                                "target_name": context.name,
                                "target_start_line": context.start_line,
                                "target_end_line": context.end_line,
                            },
                        )
                    context_mode = "target_span"

                if self._generator is not None:
                    generation_started = time.monotonic()
                    output = self._generator(prompt)
                else:
                    encoded = {
                        key: value.to(self._resolved_device)
                        for key, value in encoded.items()
                    }
                    max_new_tokens, generation_timeout_seconds = self._generation_limits(
                        language,
                        action,
                        context,
                    )
                    generation_started = time.monotonic()
                    with self._runtime_inference_lock:
                        with self._torch.inference_mode():
                            generated = self._model.generate(
                                **encoded,
                                max_new_tokens=max_new_tokens,
                                max_time=self._bounded_generation_timeout(
                                    generation_timeout_seconds
                                ),
                                use_cache=True,
                                do_sample=False,
                                num_beams=1,
                                temperature=None,
                                top_p=None,
                                top_k=None,
                            )
                    output_tokens = generated[0]
                    output_tokens = output_tokens[encoded["input_ids"].shape[-1]:]
                    output = self._tokenizer.decode(
                        output_tokens,
                        skip_special_tokens=True,
                    )

                generation_elapsed_seconds = max(
                    0.0, time.monotonic() - generation_started
                )
                self._generation_seconds_used += generation_elapsed_seconds
                self._file_generation_seconds[file_key] = (
                    self._file_generation_seconds.get(file_key, 0.0)
                    + generation_elapsed_seconds
                )
                model_output = self._normalise_model_output(output)
                model_output, suffix_repair = self._repair_java_target_suffix(
                    language,
                    model_output,
                )
                model_output, target_identifier_normalization = (
                    self._canonicalize_java_constant_target_identifier(
                        language=language,
                        output=model_output,
                        context=context,
                        action=action,
                    )
                )
                output_shape_issue = self._target_output_shape_issue(
                    language,
                    context,
                    model_output,
                )
                target_contract_completion: Dict[str, Any] = {}
                if output_shape_issue or model_output.startswith(
                    ("diff --git", "--- ", "+++ ")
                ):
                    model_output, target_contract_completion = (
                        self._recover_java_constant_from_complete_output(
                            language=language,
                            output=model_output,
                            context=context,
                            action=action,
                        )
                    )
                    if target_contract_completion:
                        output_shape_issue = self._target_output_shape_issue(
                            language,
                            context,
                            model_output,
                        )
                if not output_shape_issue:
                    if not target_contract_completion:
                        model_output, target_contract_completion = (
                            self._complete_java_constant_target_output(
                                language=language,
                                output=model_output,
                                context=context,
                                action=action,
                            )
                        )
                    if not target_contract_completion:
                        model_output, target_contract_completion = (
                            self._complete_c_constant_target_output(
                                language=language,
                                output=model_output,
                                context=context,
                                action=action,
                            )
                        )
                merge_output = self._target_merge_output(
                    context,
                    model_output,
                    action,
                )
                candidate = (
                    self._merge_target_output(source_code, context, merge_output)
                    if context is not None and merge_output and not output_shape_issue
                    else merge_output
                )
                metadata = {
                    "provider": self.provider,
                    "model_id": self.settings.model_id,
                    "adapter_revision": MODEL_ASSISTANCE_REVISION,
                    "device": self._resolved_device or "injected_generator",
                    "backend": self._resolved_backend or "pytorch",
                    "runtime_cache_hit": self._runtime_cache_hit,
                    "model_load_seconds": round(self._model_load_seconds, 3),
                    "attempt_number": attempt_number,
                    "max_actions_per_request": self.settings.max_actions_per_request,
                    "max_actions_per_file": self.settings.max_actions_per_file,
                    "cpu_threads": self._effective_cpu_threads,
                    "generation_timeout_seconds": self.settings.generation_timeout_seconds,
                    "effective_max_new_tokens": (
                        self._generation_limits(language, action, context)[0]
                    ),
                    "generation_elapsed_seconds": round(generation_elapsed_seconds, 3),
                    "request_generation_seconds_used": round(
                        self._generation_seconds_used, 3
                    ),
                    "input_tokens": input_tokens,
                    "full_file_input_tokens": full_file_input_tokens,
                    "context_mode": context_mode,
                    "source_sha256": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
                    "candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest() if candidate else "",
                    "model_output_chars": len(model_output),
                    "model_output_preview": model_output[:400],
                    "generation_phase": "validator_guided_repair" if validation_feedback else "initial",
                    "validation_feedback_supplied": bool(validation_feedback),
                }
                if target_contract_completion:
                    metadata["target_contract_completion"] = target_contract_completion
                if target_identifier_normalization:
                    metadata["target_identifier_normalization"] = (
                        target_identifier_normalization
                    )
                if suffix_repair:
                    metadata["target_output_repair"] = suffix_repair
                if context is not None:
                    metadata.update({
                        "target_kind": context.kind,
                        "target_name": context.name,
                        "target_start_line": context.start_line,
                        "target_end_line": context.end_line,
                        "target_resolution": context.resolution,
                        "target_source_sha256": hashlib.sha256(context.source.encode("utf-8")).hexdigest(),
                        "model_output_sha256": hashlib.sha256(model_output.encode("utf-8")).hexdigest() if model_output else "",
                    })
                if not model_output:
                    return QwenCandidate(reason="QWEN_EMPTY_OUTPUT", metadata=metadata)
                if output_shape_issue:
                    return QwenCandidate(reason=output_shape_issue, metadata=metadata)
                if candidate == source_code:
                    return QwenCandidate(reason="QWEN_NO_SOURCE_CHANGE", metadata=metadata)
                if model_output.startswith(("diff --git", "--- ", "+++ ")):
                    reason = (
                        "QWEN_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
                        if context is not None
                        else "QWEN_OUTPUT_MUST_BE_COMPLETE_SOURCE"
                    )
                    return QwenCandidate(reason=reason, metadata=metadata)
                return QwenCandidate(
                    source_code=candidate,
                    status="candidate_generated",
                    metadata=metadata,
                )
            except Exception as exc:  # The model must never crash SCTVA execution.
                return QwenCandidate(
                    reason=str(exc) or "QWEN_GENERATION_FAILED",
                    metadata={
                        "provider": self.provider,
                        "model_id": self.settings.model_id,
                        "attempt_number": attempt_number,
                        "max_actions_per_request": self.settings.max_actions_per_request,
                    },
                )
