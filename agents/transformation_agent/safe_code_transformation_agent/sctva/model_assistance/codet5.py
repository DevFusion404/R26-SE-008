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
import difflib
import hashlib
import json
import os
import re
import textwrap
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from ..contracts import RefactoringAction


DEFAULT_CODET5_MODEL_ID = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
MODEL_ASSISTANCE_REVISION = "openvino_cpu_throughput_v12_20261018"


_MODEL_RUNTIME_CACHE: Dict[tuple[Any, ...], Dict[str, Any]] = {}
_MODEL_RUNTIME_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class CodeT5Settings:
    """Runtime settings for the optional local-model adapter."""

    enabled: bool = False
    model_id: str = DEFAULT_CODET5_MODEL_ID
    device: str = "auto"
    backend: str = "auto"
    openvino_model_path: str = ""
    max_new_tokens: int = 256
    max_bundle_output_tokens: int = 192
    # Keep the configurable ceiling conservative; _load also reads the checkpoint's
    # actual tokenizer/config limit before generation.
    max_input_tokens: int = 512
    cpu_threads: int = 4
    max_actions_per_request: int = 100
    max_actions_per_file: int = 100
    enable_file_bundling: bool = False
    enable_generic_bundling: bool = False
    max_actions_per_bundle: int = 5
    max_generic_actions_per_bundle: int = 2
    generation_timeout_seconds: int = 30
    max_generation_seconds_per_request: int = 0
    runtime_cache: bool = True
    allow_unsupported_actions: bool = False
    mode: str = "primary"
    allow_download: bool = False

    @classmethod
    def from_execution_options(cls, options: Any) -> "CodeT5Settings":
        return cls(
            enabled=bool(getattr(options, "enable_codet5", False)),
            model_id=str(
                getattr(options, "codet5_model_id", DEFAULT_CODET5_MODEL_ID)
                or DEFAULT_CODET5_MODEL_ID
            ).strip(),
            device=str(getattr(options, "codet5_device", "auto") or "auto").strip(),
            backend=str(
                getattr(options, "codet5_backend", "auto") or "auto"
            ).strip().lower(),
            openvino_model_path=str(
                getattr(options, "codet5_openvino_model_path", "") or ""
            ).strip(),
            max_new_tokens=int(getattr(options, "codet5_max_new_tokens", 256) or 256),
            max_bundle_output_tokens=max(
                64,
                int(
                    getattr(options, "codet5_max_bundle_output_tokens", 192)
                    or 192
                ),
            ),
            max_input_tokens=int(getattr(options, "codet5_max_input_tokens", 512) or 512),
            cpu_threads=int(getattr(options, "codet5_cpu_threads", 4) or 4),
            max_actions_per_request=int(
                getattr(options, "codet5_max_actions_per_request", 100)
            ),
            max_actions_per_file=int(
                getattr(options, "codet5_max_actions_per_file", 100)
            ),
            enable_file_bundling=bool(
                getattr(options, "codet5_enable_file_bundling", False)
            ),
            enable_generic_bundling=bool(
                getattr(options, "codet5_enable_generic_bundling", False)
            ),
            max_actions_per_bundle=max(
                2,
                int(getattr(options, "codet5_max_actions_per_bundle", 5) or 5),
            ),
            max_generic_actions_per_bundle=max(
                2,
                int(
                    getattr(
                        options,
                        "codet5_max_generic_actions_per_bundle",
                        2,
                    )
                    or 2
                ),
            ),
            generation_timeout_seconds=int(
                getattr(options, "codet5_generation_timeout_seconds", 30) or 30
            ),
            max_generation_seconds_per_request=max(
                0,
                int(
                    getattr(
                        options,
                        "codet5_max_generation_seconds_per_request",
                        0,
                    )
                    or 0
                ),
            ),
            runtime_cache=bool(getattr(options, "codet5_runtime_cache", True)),
            allow_unsupported_actions=bool(
                getattr(options, "codet5_allow_unsupported_actions", False)
            ),
            mode=str(getattr(options, "codet5_mode", "primary") or "primary").strip().lower(),
            allow_download=bool(getattr(options, "codet5_allow_download", False)),
        )


@dataclass(frozen=True)
class CodeT5Candidate:
    """A candidate result, never an acceptance decision."""

    source_code: str = ""
    status: str = "skipped"
    reason: str = ""
    metadata: Dict[str, Any] | None = None
    target_replacement: str = ""


@dataclass(frozen=True)
class CodeT5TargetContext:
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
    "inline_class",
    "inline_python_class",
    "move_python_method",
    "replace_conditional_with_polymorphism",
}

_MULTI_CLASS_ACTIONS = {
    "inline_class",
    "inline_python_class",
    "move_python_method",
}

# These operations can be proven inside a bounded source span. Broader
# repository/multi-class transformations remain explicit single-action
# barriers until they have an equally strong composition contract.
_GENERIC_FILE_BUNDLE_ACTIONS = {
    "remove_dead_code",
    "replace_literal",
    "narrow_exception_handler",
    "replace_unsafe_function",
    "replace_nested_conditional_with_guard_clauses",
}

_BUNDLE_DESTRUCTIVE_ACTIONS = {
    "remove_dead_code",
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


def _python_context(source: str, action: RefactoringAction) -> CodeT5TargetContext | None:
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
    return CodeT5TargetContext(
        start=start, end=end, source=source[start:end], kind=selected["kind"],
        name=selected["qualified"], start_line=start_line, end_line=end_line,
        surrounding_context=_imports_context("python", source),
        resolution="python_ast_current_snapshot",
    )


def _c_context(source: str, action: RefactoringAction) -> CodeT5TargetContext | None:
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
            return CodeT5TargetContext(
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
        return CodeT5TargetContext(
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
    return CodeT5TargetContext(
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


def _java_context(source: str, action: RefactoringAction) -> CodeT5TargetContext | None:
    from ..transformers.java_extract_class import _parse_java_class

    parameters = dict(action.parameters or {})
    method_name = _first_text((
        parameters.get("source_method"), parameters.get("method"),
        parameters.get("method_name"), parameters.get("target_method"),
    ))
    class_name = _first_text((
        parameters.get("class_to_inline"), parameters.get("target_class"),
        parameters.get("source_class"), parameters.get("class_name"),
    ))
    field_name = _first_text((
        parameters.get("field_name"), parameters.get("field"),
        parameters.get("target_field"),
    ))
    destination_name = _first_text((parameters.get("destination_class"),))
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

            member_source = source[selected_start:selected_end]
            member_offset = selected_start
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
                    source_line = line.rstrip("\r\n")
                    line_candidates.append(
                        (
                            member_offset + cursor,
                            member_offset + cursor + len(source_line),
                        )
                    )
                cursor += len(line)
            if len(line_candidates) == 1:
                selected_start, selected_end = line_candidates[0]
                kind = "statement"
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
    return CodeT5TargetContext(
        start=start, end=end, source=source[start:end], kind=kind, name=name,
        start_line=_line_number(source, start), end_line=_line_number(source, max(start, end - 1)),
        surrounding_context=_imports_context("java", source),
        resolution="java_symbol_model_current_snapshot",
    )


class CodeT5CandidateTransformer:
    """Generate deterministic source-to-source candidates with a local model.

    ``generator`` exists for isolated tests and for an eventual fine-tuned
    in-house serving backend.  Production construction leaves it as ``None``
    and lazily loads the Hugging Face checkpoint on first use.
    """

    def __init__(
        self,
        settings: CodeT5Settings,
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
        self._is_encoder_decoder = True
        self._effective_max_input_tokens = settings.max_input_tokens
        self._effective_cpu_threads = settings.cpu_threads
        self._runtime_cache_hit = False
        self._model_load_seconds = 0.0
        self._attempt_count = 0
        self._file_attempt_counts: Dict[str, int] = {}
        self._generation_seconds_used = 0.0
        self._lock = threading.Lock()
        self._runtime_inference_lock = threading.Lock()

    @property
    def provider(self) -> str:
        return "qwen_coder" if "qwen" in self.settings.model_id.lower() else "codet5_plus"

    @property
    def display_name(self) -> str:
        return "Qwen2.5-Coder" if self.provider == "qwen_coder" else "CodeT5+"

    def can_attempt(self, current_file_name: str) -> bool:
        """Return whether inference budget remains without consuming it."""

        return self.attempt_budget_status(current_file_name).get("available") is True

    def attempt_budget_status(self, current_file_name: str) -> Dict[str, Any]:
        """Describe a skipped inference budget so it is not reported as ineligibility."""

        file_key = str(current_file_name or "<unknown>").replace("\\", "/").lower()
        with self._lock:
            file_attempts = self._file_attempt_counts.get(file_key, 0)
            if self._attempt_count >= self.settings.max_actions_per_request:
                reason = "CODET5_REQUEST_ACTION_BUDGET_EXHAUSTED"
            elif file_attempts >= self.settings.max_actions_per_file:
                reason = "CODET5_FILE_ACTION_BUDGET_EXHAUSTED"
            elif (
                self.settings.max_generation_seconds_per_request > 0
                and self._generation_seconds_used
                >= self.settings.max_generation_seconds_per_request
            ):
                reason = "CODET5_REQUEST_TIME_BUDGET_EXHAUSTED"
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

    def usage_snapshot(self) -> Dict[str, float | int]:
        """Return monotonic request counters for accurate per-file telemetry."""

        with self._lock:
            return {
                "attempt_count": self._attempt_count,
                "generation_seconds": self._generation_seconds_used,
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
        context: CodeT5TargetContext | None,
    ) -> tuple[int, int]:
        max_new_tokens = self.settings.max_new_tokens
        timeout_seconds = self.settings.generation_timeout_seconds
        if (
            str(language or "").lower() in {"java", "c"}
            and action.action_type in {"introduce_constant", "extract_constant"}
        ):
            return min(max_new_tokens, 64), min(timeout_seconds, 30)
        if context is not None and context.kind.startswith("statement"):
            max_new_tokens = min(max_new_tokens, 128)
        return max_new_tokens, timeout_seconds

    @classmethod
    def from_execution_options(cls, options: Any) -> "CodeT5CandidateTransformer | None":
        settings = CodeT5Settings.from_execution_options(options)
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
    ) -> CodeT5TargetContext | None:
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
        context: CodeT5TargetContext,
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
                        f"with {constant_name}. Return the required declaration first, then "
                        "the complete transformed target-source: exactly two class members/source "
                        "fragments. Emit the required declaration exactly once. Do not emit an "
                        "enclosing class declaration or an enclosing-class closing brace. "
                        "Preserve every other token."
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
        context: CodeT5TargetContext,
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
    def _complete_java_constant_target_output(
        *,
        language: str,
        output: str,
        context: CodeT5TargetContext | None,
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
        declaration_pattern = re.compile(
            rf"(?m)^[ \t]*(?:(?:public|protected|private|static|final)\s+)*"
            rf"[A-Za-z_$][A-Za-z0-9_$<>\[\].?]*\s+"
            rf"{re.escape(constant_name)}\s*=\s*[^;\r\n]+;"
            rf"[ \t]*(?:\r?\n)?"
        )
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
            "reason": "missing_target_member_restored",
            "contract": "java_introduce_constant_target_member",
            "replacements": replacements,
        }

    @staticmethod
    def _complete_c_constant_target_output(
        *,
        language: str,
        output: str,
        context: CodeT5TargetContext | None,
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
                AutoConfig,
                AutoModelForCausalLM,
                AutoModelForSeq2SeqLM,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise RuntimeError(
                "CODET5_DEPENDENCIES_NOT_INSTALLED: install requirements-codet5.txt"
            ) from exc

        requested = self.settings.device.lower()
        if requested == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        elif requested in {"cpu", "cuda"}:
            device = requested
        else:
            raise RuntimeError("CODET5_INVALID_DEVICE")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CODET5_CUDA_UNAVAILABLE")

        requested_backend = self.settings.backend.strip().lower()
        if requested_backend not in {"auto", "pytorch", "openvino"}:
            raise RuntimeError("CODET5_INVALID_BACKEND")
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
            raise RuntimeError("CODET5_OPENVINO_REQUIRES_CPU_DEVICE")
        if backend == "openvino" and (
            openvino_path is None or not openvino_path.exists()
        ):
            raise RuntimeError("CODET5_OPENVINO_MODEL_PATH_NOT_FOUND")

        cache_source = str(openvino_path or self.settings.model_id)
        if backend == "openvino":
            # A process must not reuse a runtime compiled with the older FP32
            # activation/KV-cache profile after this throughput profile ships.
            cache_source += "|bf16-u8-kv-v1"
        cache_key = (
            backend,
            cache_source,
            device,
            bool(self.settings.allow_download),
            int(self.settings.max_input_tokens),
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
                self._is_encoder_decoder = cached["is_encoder_decoder"]
                self._effective_max_input_tokens = cached["max_input_tokens"]
                self._runtime_inference_lock = cached["inference_lock"]
                self._runtime_cache_hit = True
                self._effective_cpu_threads = min(
                    self.settings.cpu_threads,
                    max(1, os.cpu_count() or self.settings.cpu_threads),
                )
                if device == "cpu" and hasattr(torch, "set_num_threads"):
                    torch.set_num_threads(self._effective_cpu_threads)
                self._model_load_seconds = max(
                    0.0,
                    time.monotonic() - load_started,
                )
                return

        self._effective_cpu_threads = min(
            self.settings.cpu_threads,
            max(1, os.cpu_count() or self.settings.cpu_threads),
        )
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
        config = AutoConfig.from_pretrained(
            model_source,
            local_files_only=local_files_only,
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
        self._is_encoder_decoder = bool(
            getattr(config, "is_encoder_decoder", False)
        )
        model_class = (
            AutoModelForSeq2SeqLM
            if self._is_encoder_decoder
            else AutoModelForCausalLM
        )
        if backend == "openvino":
            try:
                from optimum.intel import OVModelForCausalLM, OVModelForSeq2SeqLM
            except ImportError as exc:
                raise RuntimeError(
                    "CODET5_OPENVINO_DEPENDENCIES_NOT_INSTALLED: "
                    "install requirements-openvino.txt"
                ) from exc
            openvino_model_class = (
                OVModelForSeq2SeqLM
                if self._is_encoder_decoder
                else OVModelForCausalLM
            )
            self._model = openvino_model_class.from_pretrained(
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
                    # Keep the exported FP32 weights unchanged while using
                    # the CPU's native reduced-precision execution path.
                    "INFERENCE_PRECISION_HINT": "bf16",
                    # These are runtime-only OpenVINO optimizations. SCTVA's
                    # normal candidate validators still decide acceptance.
                    "DYNAMIC_QUANTIZATION_GROUP_SIZE": "32",
                    "KV_CACHE_PRECISION": "u8",
                    "PERFORMANCE_HINT": "LATENCY",
                },
            )
        else:
            self._model = model_class.from_pretrained(
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
                "is_encoder_decoder": self._is_encoder_decoder,
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
                self._is_encoder_decoder = existing["is_encoder_decoder"]
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
        context: CodeT5TargetContext | None,
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
                return "CODET5_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
        return ""

    @staticmethod
    def _target_merge_output(
        context: CodeT5TargetContext | None,
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
        declaration_pattern = re.compile(
            rf"(?m)^[ \t]*(?:(?:public|protected|private|static|final)\s+)*"
            rf"[A-Za-z_$][A-Za-z0-9_$<>\[\].?]*\s+"
            rf"{re.escape(constant_name)}\s*=\s*[^;\r\n]+;"
            rf"[ \t]*(?:\r?\n)?"
        )
        declarations = list(declaration_pattern.finditer(output))
        if len(declarations) != 1:
            return output
        return declaration_pattern.sub("", output, count=1).lstrip("\r\n")

    def _model_prompt(self, prompt: str) -> str:
        if self._is_encoder_decoder or not hasattr(self._tokenizer, "apply_chat_template"):
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

    @staticmethod
    def _bundle_constant_spec(
        language: str,
        action_index: int,
        action: RefactoringAction,
    ) -> Dict[str, Any] | None:
        """Return a compact reversible contract for one constant action."""

        if action.action_type not in {"introduce_constant", "extract_constant"}:
            return None
        parameters = action.parameters or {}
        values = _planned_literal_values(parameters)
        if len(values) != 1:
            return None
        name = str(parameters.get("constant_name") or "EXTRACTED_CONSTANT").strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
            return None

        normalized_language = str(language or "").strip().lower()
        value = values[0]
        if normalized_language == "java":
            from ..transformers import java_transformers

            literal = java_transformers._to_java_literal(value)
            declaration = (
                f"private static final {java_transformers._java_type_for_literal(value)} "
                f"{name} = {literal};"
            )
        elif normalized_language == "c":
            from ..transformers import c_transformers

            value = c_transformers.normalize_planned_numeric_literal(value)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None
            literal = c_transformers._to_c_literal(value)
            declaration = f"#define {name} {literal}"
        else:
            return None

        return {
            "action_index": action_index,
            "action_type": action.action_type,
            "constant_name": name,
            "literal": literal,
            "required_declaration": declaration,
        }

    @staticmethod
    def bundle_kind(actions: Sequence[tuple[int, RefactoringAction]]) -> str:
        """Return the composition contract shared by an action sequence."""

        if len(actions) < 2:
            return ""
        action_types = {action.action_type for _index, action in actions}
        if action_types <= {"introduce_constant", "extract_constant"}:
            return "constant_bundle"
        if action_types <= _GENERIC_FILE_BUNDLE_ACTIONS:
            return "bounded_local_bundle"
        return ""

    def _bundle_generic_spec(
        self,
        action_index: int,
        action: RefactoringAction,
    ) -> Dict[str, Any]:
        return {
            "action_index": action_index,
            "action_type": action.action_type,
            "source_step_id": action.source_step_id,
            "source_refactoring": str(action.source_refactoring or ""),
            "parameters": self._safe_parameters(dict(action.parameters or {})),
        }

    @staticmethod
    def _bundle_action_symbols(action: RefactoringAction) -> tuple[set[str], set[str]]:
        parameters = dict(action.parameters or {})
        reads: set[str] = set()
        writes: set[str] = set()
        for key in (
            "old_name", "source_method", "method", "method_name",
            "target_method", "function", "function_name", "unsafe_function",
            "variable", "variable_name", "field", "field_name",
        ):
            value = parameters.get(key)
            if isinstance(value, str) and value.strip():
                reads.add(value.strip())
        for key in (
            "new_name", "new_method_name", "constant_name",
            "parameter_object_name", "safe_alternative",
        ):
            value = parameters.get(key)
            if isinstance(value, str) and value.strip():
                writes.add(value.strip())
        return reads, writes

    def _bundle_pair_compatible(
        self,
        *,
        language: str,
        source_code: str,
        left: tuple[int, RefactoringAction],
        right: tuple[int, RefactoringAction],
    ) -> bool:
        left_context = self.resolve_target_context(
            language=language,
            source_code=source_code,
            action=left[1],
        )
        right_context = self.resolve_target_context(
            language=language,
            source_code=source_code,
            action=right[1],
        )
        if left_context is None or right_context is None:
            return False

        exact_same_scope = (
            left_context.start == right_context.start
            and left_context.end == right_context.end
        )
        disjoint = (
            left_context.end <= right_context.start
            or right_context.end <= left_context.start
        )
        if not exact_same_scope and not disjoint:
            return False
        both_constant_actions = all(
            item[1].action_type in {"introduce_constant", "extract_constant"}
            for item in (left, right)
        )
        # Generic actions share one candidate only when they resolve to the
        # same owner span. Regenerating the gap between disjoint methods made
        # prompts larger and reduced acceptance accuracy. Constant bundles
        # retain their reversible disjoint-span contract.
        if not both_constant_actions and not exact_same_scope:
            return False
        if exact_same_scope and (
            left[1].action_type in _BUNDLE_DESTRUCTIVE_ACTIONS
            or right[1].action_type in _BUNDLE_DESTRUCTIVE_ACTIONS
        ):
            return False

        if (
            left[1].action_type == "replace_literal"
            and right[1].action_type == "replace_literal"
        ):
            left_parameters = left[1].parameters or {}
            right_parameters = right[1].parameters or {}
            try:
                left_values = {
                    left_parameters.get("old_literal"),
                    left_parameters.get("new_literal"),
                }
                right_values = {
                    right_parameters.get("old_literal"),
                    right_parameters.get("new_literal"),
                }
                if left_values.intersection(right_values):
                    return False
            except TypeError:
                # Non-scalar literal payloads are not safe bundle contracts.
                return False

        left_reads, left_writes = self._bundle_action_symbols(left[1])
        right_reads, right_writes = self._bundle_action_symbols(right[1])
        if (
            left_writes.intersection(right_reads | right_writes)
            or right_writes.intersection(left_reads | left_writes)
        ):
            return False
        return True

    def plan_dependency_aware_bundles(
        self,
        *,
        language: str,
        source_code: str,
        actions: Sequence[tuple[int, RefactoringAction]],
        current_file_name: str,
        allow_generic: bool = True,
    ) -> list[list[tuple[int, RefactoringAction]]]:
        """Create ordered, token-safe bundles while retaining action identity."""

        bundles: list[list[tuple[int, RefactoringAction]]] = []
        pending: list[tuple[int, RefactoringAction]] = []
        pending_kind = ""

        def flush() -> None:
            nonlocal pending, pending_kind
            if len(pending) >= 2:
                for partition in self.partition_bundle_actions(
                    language=language,
                    source_code=source_code,
                    actions=pending,
                    current_file_name=current_file_name,
                ):
                    if len(partition) >= 2:
                        bundles.append(partition)
            pending = []
            pending_kind = ""

        for item in actions:
            single_type = item[1].action_type
            item_kind = (
                "constant_bundle"
                if single_type in {"introduce_constant", "extract_constant"}
                else "bounded_local_bundle"
                if (
                    allow_generic
                    and self.settings.enable_generic_bundling
                    and single_type in _GENERIC_FILE_BUNDLE_ACTIONS
                )
                else ""
            )
            if not item_kind:
                flush()
                continue
            if pending and (
                pending_kind != item_kind
                or len(pending) >= (
                    self.settings.max_actions_per_bundle
                    if item_kind == "constant_bundle"
                    else self.settings.max_generic_actions_per_bundle
                )
                or any(
                    not self._bundle_pair_compatible(
                        language=language,
                        source_code=source_code,
                        left=existing,
                        right=item,
                    )
                    for existing in pending
                )
            ):
                flush()
            pending.append(item)
            pending_kind = item_kind
        flush()
        return bundles

    def bundle_candidate_scope_issue(
        self,
        *,
        language: str,
        original_source: str,
        candidate_source: str,
        actions: Sequence[tuple[int, RefactoringAction]],
    ) -> str:
        """Reject generic bundle edits that escape all resolved target spans."""

        contexts = [
            self.resolve_target_context(
                language=language,
                source_code=original_source,
                action=action,
            )
            for _index, action in actions
        ]
        if any(context is None for context in contexts):
            return "CODET5_BUNDLE_TARGET_NOT_RESOLVED"
        spans = [
            (context.start, context.end)
            for context in contexts
            if context is not None
        ]
        matcher = difflib.SequenceMatcher(
            None,
            original_source,
            candidate_source,
            autojunk=False,
        )
        for tag, original_start, original_end, _candidate_start, _candidate_end in (
            matcher.get_opcodes()
        ):
            if tag == "equal":
                continue
            if original_start == original_end:
                allowed = any(start <= original_start <= end for start, end in spans)
            else:
                allowed = any(
                    start <= original_start and original_end <= end
                    for start, end in spans
                )
            if not allowed:
                return "CODET5_BUNDLE_CHANGE_OUTSIDE_RESOLVED_TARGETS"
        return ""

    def partition_bundle_actions(
        self,
        *,
        language: str,
        source_code: str,
        actions: Sequence[tuple[int, RefactoringAction]],
        current_file_name: str,
    ) -> list[list[tuple[int, RefactoringAction]]]:
        """Split a compatible run before inference when its prompt cannot fit.

        Singleton partitions deliberately return to the established per-action
        path. Preflight loads/tokenizes the prompt but never consumes an action
        budget because no generation has happened yet.
        """

        items = list(actions)
        if len(items) < 2:
            return [items] if items else []

        normalized_language = str(language or "").strip().lower()

        def fits(candidate_actions: Sequence[tuple[int, RefactoringAction]]) -> bool:
            bundle_kind = self.bundle_kind(candidate_actions)
            if not bundle_kind:
                return False
            specs: list[Dict[str, Any]] = []
            contexts: list[CodeT5TargetContext] = []
            names: set[str] = set()
            for action_index, action in candidate_actions:
                spec = (
                    self._bundle_constant_spec(
                        normalized_language,
                        action_index,
                        action,
                    )
                    if bundle_kind == "constant_bundle"
                    else self._bundle_generic_spec(action_index, action)
                )
                context = self.resolve_target_context(
                    language=normalized_language,
                    source_code=source_code,
                    action=action,
                )
                if spec is None or context is None:
                    return False
                if bundle_kind == "constant_bundle":
                    if spec["constant_name"] in names:
                        return False
                    names.add(spec["constant_name"])
                specs.append(spec)
                contexts.append(context)

            start = min(context.start for context in contexts)
            end = max(context.end for context in contexts)
            combined_context = CodeT5TargetContext(
                start=start,
                end=end,
                source=source_code[start:end],
                kind="file_action_bundle",
                name=bundle_kind,
                start_line=_line_number(source_code, start),
                end_line=_line_number(source_code, max(start, end - 1)),
                resolution=f"dependency_aware_{bundle_kind}",
            )
            prompt = self.build_bundle_prompt(
                language=normalized_language,
                current_file_name=current_file_name,
                context=combined_context,
                action_specs=specs,
                bundle_kind=bundle_kind,
            )
            _encoded, input_tokens = self._encode_prompt(prompt)
            estimated_output_tokens = (
                self._rough_token_count(combined_context.source)
                + ((20 if bundle_kind == "constant_bundle" else 64) * len(specs))
            )
            bundle_output_limit = min(
                self.settings.max_new_tokens,
                self.settings.max_bundle_output_tokens,
            )
            return (
                input_tokens <= self._effective_max_input_tokens
                and estimated_output_tokens <= bundle_output_limit
            )

        with self._lock:
            try:
                self._load()
                partitions: list[list[tuple[int, RefactoringAction]]] = []
                pending: list[tuple[int, RefactoringAction]] = []
                for item in items:
                    proposed = [*pending, item]
                    if len(proposed) <= 1 or fits(proposed):
                        pending = proposed
                        continue
                    if pending:
                        partitions.append(pending)
                    pending = [item]
                if pending:
                    partitions.append(pending)
                return partitions
            except Exception:
                # Preflight is an optimization only. Preserve the prior path
                # so normal model error reporting and rule fallback still run.
                return [items]

    @staticmethod
    def _complete_java_bundle_output(
        source_code: str,
        model_output: str,
    ) -> str:
        """Recognize an unambiguous complete Java source candidate.

        Acceptance is still decided later by collective reversibility plus the
        normal SCTVA validators. This only avoids merging a complete file into
        a smaller target span.
        """

        if not model_output or model_output.startswith(("diff --git", "--- ", "+++ ")):
            return ""
        type_pattern = re.compile(
            r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
        )
        source_types = set(type_pattern.findall(source_code))
        output_types = set(type_pattern.findall(model_output))
        if not source_types.intersection(output_types):
            return ""
        if not re.search(
            r"(?m)^\s*(?:package\s+|import\s+|"
            r"(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*"
            r"(?:class|interface|enum|record)\s+)",
            model_output,
        ):
            return ""
        # Output normalization strips surrounding whitespace. Restore only the
        # source file's final newline so the bundle scope gate does not mistake
        # an EOF formatting difference for an edit outside the target spans.
        if source_code.endswith("\r\n") and not model_output.endswith(("\r", "\n")):
            return model_output + "\r\n"
        if source_code.endswith("\n") and not model_output.endswith("\n"):
            return model_output + "\n"
        return model_output

    def build_bundle_prompt(
        self,
        *,
        language: str,
        current_file_name: str,
        context: CodeT5TargetContext,
        action_specs: Sequence[Dict[str, Any]],
        bundle_kind: str = "constant_bundle",
        validation_feedback: str = "",
        previous_candidate: str = "",
    ) -> str:
        """Build one bounded prompt that applies several compatible actions."""

        action_contract = json.dumps(
            list(action_specs),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        if bundle_kind == "constant_bundle":
            bundle_requirement = (
                "Perform every listed Introduce/Extract Constant action exactly once. "
                "Emit every required_declaration exactly as written and replace only its "
                "planned literal occurrence with constant_name. Preserve all other tokens, "
                "declarations, operators, calls, control flow, comments, and formatting."
            )
        else:
            bundle_requirement = (
                "Apply the listed RDP actions sequentially in action_index order. Every action "
                "must be visible in the returned source. Preserve behavior and public APIs unless "
                "an action explicitly requires that API change. Do not invent refactorings, rename "
                "unlisted symbols, or modify code outside the resolved target scopes."
            )
        parts = [
            "<sctva-file-action-bundle>",
            f"language: {language}",
            f"file: {current_file_name}",
            f"bundle-kind: {bundle_kind}",
            f"action-count: {len(action_specs)}",
            f"actions: {action_contract}",
            bundle_requirement,
            (
                "Return only the complete transformed target-source span. Do not return "
                "Markdown, explanation, a diff, package/import lines, or an enclosing class/program."
            ),
        ]
        if validation_feedback:
            parts.extend((
                "<validation-feedback>",
                re.sub(r"\s+", " ", validation_feedback).strip()[:1200],
                "</validation-feedback>",
                "Repair every reported issue while still applying every listed action exactly once.",
            ))
        if previous_candidate:
            parts.extend((
                "<rejected-target-replacement>",
                previous_candidate[:8000],
                "</rejected-target-replacement>",
            ))
        parts.extend((
            "<target-source>",
            context.source,
            "</target-source>",
            "</sctva-file-action-bundle>",
        ))
        return "\n".join(parts)

    def propose_bundle(
        self,
        *,
        language: str,
        source_code: str,
        actions: Sequence[tuple[int, RefactoringAction]],
        current_file_name: str,
        validation_feedback: str = "",
        previous_candidate: str = "",
    ) -> CodeT5Candidate:
        """Generate one candidate for a bounded compatible action bundle.

        Constant bundles use their reversible declaration contract. Other
        bundles are restricted to dependency-planned local target spans and
        must pass scope plus multi-action structural validation in the engine.
        """

        if not self.settings.enabled or not self.settings.enable_file_bundling:
            return CodeT5Candidate(reason="CODET5_FILE_BUNDLING_DISABLED")
        normalized_language = str(language or "").strip().lower()
        if normalized_language not in {"python", "java", "c"}:
            return CodeT5Candidate(reason="CODET5_BUNDLE_LANGUAGE_UNSUPPORTED")
        bundle_kind = self.bundle_kind(actions)
        if not bundle_kind:
            return CodeT5Candidate(reason="CODET5_BUNDLE_CONTRACT_UNSUPPORTED")
        bundle_limit = (
            self.settings.max_actions_per_bundle
            if bundle_kind == "constant_bundle"
            else self.settings.max_generic_actions_per_bundle
        )
        if not 2 <= len(actions) <= bundle_limit:
            return CodeT5Candidate(reason="CODET5_BUNDLE_SIZE_UNSUPPORTED")

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
                    "CODET5_REQUEST_ACTION_BUDGET_EXHAUSTED"
                    if self._attempt_count >= self.settings.max_actions_per_request
                    else "CODET5_FILE_ACTION_BUDGET_EXHAUSTED"
                    if file_attempt_count >= self.settings.max_actions_per_file
                    else "CODET5_REQUEST_TIME_BUDGET_EXHAUSTED"
                )
                return CodeT5Candidate(
                    reason="CODET5_ACTION_BUDGET_EXHAUSTED",
                    metadata={
                        "available": False,
                        "reason": budget_reason,
                        "request_attempts_used": self._attempt_count,
                        "request_attempt_limit": self.settings.max_actions_per_request,
                        "file_attempts_used": file_attempt_count,
                        "file_attempt_limit": self.settings.max_actions_per_file,
                        "generation_seconds_used": round(
                            self._generation_seconds_used,
                            3,
                        ),
                        "generation_seconds_limit": (
                            self.settings.max_generation_seconds_per_request
                        ),
                    },
                )

            specs: list[Dict[str, Any]] = []
            contexts: list[CodeT5TargetContext] = []
            names: set[str] = set()
            for action_index, action in actions:
                spec = (
                    self._bundle_constant_spec(
                        normalized_language,
                        action_index,
                        action,
                    )
                    if bundle_kind == "constant_bundle"
                    else self._bundle_generic_spec(action_index, action)
                )
                context = self.resolve_target_context(
                    language=normalized_language,
                    source_code=source_code,
                    action=action,
                )
                if spec is None or context is None:
                    return CodeT5Candidate(reason="CODET5_BUNDLE_TARGET_NOT_RESOLVED")
                if bundle_kind == "constant_bundle":
                    if spec["constant_name"] in names:
                        return CodeT5Candidate(reason="CODET5_BUNDLE_DUPLICATE_CONSTANT_NAME")
                    names.add(spec["constant_name"])
                specs.append(spec)
                contexts.append(context)

            start = min(context.start for context in contexts)
            end = max(context.end for context in contexts)
            combined_context = CodeT5TargetContext(
                start=start,
                end=end,
                source=source_code[start:end],
                kind="file_action_bundle",
                name=bundle_kind,
                start_line=_line_number(source_code, start),
                end_line=_line_number(source_code, max(start, end - 1)),
                resolution=f"dependency_aware_{bundle_kind}",
            )

            attempt_number = self._attempt_count + 1
            try:
                self._load()
                prompt = self.build_bundle_prompt(
                    language=normalized_language,
                    current_file_name=current_file_name,
                    context=combined_context,
                    action_specs=specs,
                    bundle_kind=bundle_kind,
                    validation_feedback=validation_feedback,
                    previous_candidate=previous_candidate,
                )
                encoded, input_tokens = self._encode_prompt(prompt)
                repair_prompt_compacted = False
                if input_tokens > self._effective_max_input_tokens and validation_feedback:
                    # A rejected candidate can be much larger than the target
                    # span. Keep the validator guidance, but do not let that
                    # inspection artifact make the bounded repair ineligible.
                    compact_feedback = re.sub(
                        r"\s+",
                        " ",
                        validation_feedback,
                    ).strip()[:600]
                    prompt = self.build_bundle_prompt(
                        language=normalized_language,
                        current_file_name=current_file_name,
                        context=combined_context,
                        action_specs=specs,
                        bundle_kind=bundle_kind,
                        validation_feedback=compact_feedback,
                        previous_candidate="",
                    )
                    encoded, input_tokens = self._encode_prompt(prompt)
                    repair_prompt_compacted = True
                if input_tokens > self._effective_max_input_tokens:
                    return CodeT5Candidate(
                        reason="CODET5_BUNDLE_CONTEXT_TOO_LARGE",
                        metadata={
                            "input_tokens": input_tokens,
                            "model_max_input_tokens": self._effective_max_input_tokens,
                            "bundle_action_count": len(specs),
                            "repair_prompt_compacted": repair_prompt_compacted,
                        },
                    )

                estimated_output_tokens = (
                    self._rough_token_count(combined_context.source)
                    + ((20 if bundle_kind == "constant_bundle" else 64) * len(specs))
                )
                bundle_output_limit = min(
                    self.settings.max_new_tokens,
                    self.settings.max_bundle_output_tokens,
                )
                if estimated_output_tokens > bundle_output_limit:
                    return CodeT5Candidate(
                        reason="CODET5_BUNDLE_OUTPUT_TOO_LARGE",
                        metadata={
                            "estimated_output_tokens": estimated_output_tokens,
                            "max_new_tokens": bundle_output_limit,
                            "configured_max_new_tokens": self.settings.max_new_tokens,
                            "max_bundle_output_tokens": (
                                self.settings.max_bundle_output_tokens
                            ),
                            "bundle_action_count": len(specs),
                        },
                    )

                # Count only an actual generation. Context/output preflight is
                # free so an oversized bundle can fall back to smaller spans.
                self._attempt_count += 1
                self._file_attempt_counts[file_key] = file_attempt_count + 1
                attempt_number = self._attempt_count

                generation_started = time.monotonic()
                if self._generator is not None:
                    output = self._generator(prompt)
                else:
                    encoded = {
                        key: value.to(self._resolved_device)
                        for key, value in encoded.items()
                    }
                    with self._runtime_inference_lock:
                        with self._torch.inference_mode():
                            generated = self._model.generate(
                                **encoded,
                                max_new_tokens=min(
                                    bundle_output_limit,
                                    max(96, estimated_output_tokens),
                                ),
                                max_time=self._bounded_generation_timeout(
                                    self.settings.generation_timeout_seconds
                                ),
                                use_cache=True,
                                do_sample=False,
                                num_beams=1,
                                temperature=None,
                                top_p=None,
                                top_k=None,
                            )
                    output_tokens = generated[0]
                    if not self._is_encoder_decoder:
                        output_tokens = output_tokens[encoded["input_ids"].shape[-1]:]
                    output = self._tokenizer.decode(
                        output_tokens,
                        skip_special_tokens=True,
                    )

                elapsed = max(0.0, time.monotonic() - generation_started)
                self._generation_seconds_used += elapsed
                model_output = self._normalise_model_output(output)
                model_output, suffix_repair = self._repair_java_target_suffix(
                    normalized_language,
                    model_output,
                )
                output_shape_issue = ""
                complete_source_output = ""
                if normalized_language == "java":
                    complete_source_output = self._complete_java_bundle_output(
                        source_code,
                        model_output,
                    )
                if (
                    normalized_language == "java"
                    and not complete_source_output
                    and re.search(
                        r"(?m)^\s*(?:package\s+|import\s+|"
                        r"(?:public\s+)?(?:class|interface|enum|record)\s+)",
                        model_output,
                    )
                ):
                    output_shape_issue = "CODET5_BUNDLE_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
                candidate = (
                    complete_source_output
                    or self._merge_target_output(
                        source_code,
                        combined_context,
                        model_output,
                    )
                    if model_output and not output_shape_issue
                    else ""
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
                    "generation_elapsed_seconds": round(elapsed, 3),
                    "request_generation_seconds_used": round(
                        self._generation_seconds_used,
                        3,
                    ),
                    "input_tokens": input_tokens,
                    "effective_max_new_tokens": min(
                        bundle_output_limit,
                        max(96, estimated_output_tokens),
                    ),
                    "context_mode": "file_action_bundle",
                    "target_kind": combined_context.kind,
                    "target_start_line": combined_context.start_line,
                    "target_end_line": combined_context.end_line,
                    "bundle_mode": bundle_kind,
                    "bundle_action_count": len(specs),
                    "bundle_action_indexes": [spec["action_index"] for spec in specs],
                    "bundle_actions": specs,
                    "bundle_candidate_number": 2 if validation_feedback else 1,
                    "generation_phase": (
                        "validator_guided_bundle_repair"
                        if validation_feedback
                        else "initial_bundle"
                    ),
                    "repair_prompt_compacted": repair_prompt_compacted,
                    "source_sha256": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
                    "candidate_sha256": (
                        hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                        if candidate
                        else ""
                    ),
                    "model_output_chars": len(model_output),
                    "model_output_preview": model_output[:400],
                }
                if complete_source_output:
                    metadata.update({
                        "context_mode": "full_file_bundle_output",
                        "output_shape": "complete_java_source",
                    })
                if suffix_repair:
                    metadata["target_output_repair"] = suffix_repair
                reason = ""
                if not model_output:
                    reason = "CODET5_EMPTY_OUTPUT"
                elif output_shape_issue:
                    reason = output_shape_issue
                elif candidate == source_code:
                    reason = "CODET5_NO_SOURCE_CHANGE"
                return CodeT5Candidate(
                    source_code="" if reason else candidate,
                    status="skipped" if reason else "candidate_generated",
                    reason=reason,
                    metadata=metadata,
                    target_replacement=model_output,
                )
            except Exception as exc:
                return CodeT5Candidate(
                    reason=f"CODET5_BUNDLE_RUNTIME_ERROR: {exc}",
                    metadata={
                        "provider": self.provider,
                        "model_id": self.settings.model_id,
                        "attempt_number": attempt_number,
                        "bundle_action_count": len(actions),
                    },
                )

    def propose_batch(
        self,
        *,
        language: str,
        source_code: str,
        actions: Sequence[tuple[int, RefactoringAction]],
        current_file_name: str,
    ) -> Dict[int, CodeT5Candidate]:
        """Generate independent candidates in one tensor-batched inference call.

        Batching deliberately keeps the existing one-action prompt and output
        contract.  It is limited to non-overlapping Introduce/Extract Constant
        targets, which can be generated from the same source snapshot and then
        safely rebased as earlier actions are accepted.  Every candidate still
        passes through the normal per-action SCTVA gates in the engine.
        """

        if not self.settings.enabled or len(actions) < 2:
            return {}
        if str(language or "").strip().lower() != "java":
            return {}

        with self._lock:
            file_key = str(current_file_name or "<unknown>").replace("\\", "/").lower()
            file_attempt_count = self._file_attempt_counts.get(file_key, 0)
            consumed_attempts = 0
            remaining = min(
                self.settings.max_actions_per_request - self._attempt_count,
                self.settings.max_actions_per_file - file_attempt_count,
            )
            if remaining < 2:
                return {}

            try:
                self._load()
                prepared: list[Dict[str, Any]] = []
                occupied_ranges: list[tuple[int, int]] = []
                for action_index, action in actions:
                    if action.action_type not in {"introduce_constant", "extract_constant"}:
                        continue
                    context = self.resolve_target_context(
                        language=language,
                        source_code=source_code,
                        action=action,
                    )
                    if context is None:
                        continue
                    if any(
                        context.start < occupied_end and context.end > occupied_start
                        for occupied_start, occupied_end in occupied_ranges
                    ):
                        continue
                    prompt = self.build_target_prompt(
                        language=language,
                        action=action,
                        current_file_name=current_file_name,
                        context=context,
                    )
                    _encoded, input_tokens = self._encode_prompt(prompt)
                    if input_tokens > self._effective_max_input_tokens and context.surrounding_context:
                        prompt = self.build_target_prompt(
                            language=language,
                            action=action,
                            current_file_name=current_file_name,
                            context=context,
                            include_surrounding_context=False,
                        )
                        _encoded, input_tokens = self._encode_prompt(prompt)
                    if input_tokens > self._effective_max_input_tokens:
                        continue
                    occupied_ranges.append((context.start, context.end))
                    prepared.append({
                        "action_index": action_index,
                        "action": action,
                        "context": context,
                        "prompt": prompt,
                        "input_tokens": input_tokens,
                    })
                    if len(prepared) >= remaining:
                        break

                if len(prepared) < 2:
                    return {}

                first_attempt = self._attempt_count + 1
                self._attempt_count += len(prepared)
                self._file_attempt_counts[file_key] = file_attempt_count + len(prepared)
                consumed_attempts = len(prepared)

                generation_started = time.monotonic()
                generation_call_count = 1
                if self._generator is not None:
                    outputs = [self._generator(item["prompt"]) for item in prepared]
                    generated_item_count = len(prepared)
                else:
                    tokenizer = self._tokenizer
                    if getattr(tokenizer, "pad_token_id", None) is None:
                        tokenizer.pad_token = tokenizer.eos_token
                    original_padding_side = getattr(tokenizer, "padding_side", "right")
                    if not self._is_encoder_decoder:
                        tokenizer.padding_side = "left"
                    outputs: list[str | None] = [None] * len(prepared)
                    uncached = list(enumerate(prepared))
                    # Bound resident KV-cache/matrix memory on CPU while still
                    # amortizing tokenizer and model overhead across actions.
                    micro_batch_size = (
                        8
                        if self._resolved_backend == "openvino"
                        else 4
                        if self._resolved_device == "cpu"
                        else 8
                    )
                    chunks = [
                        uncached[start:start + micro_batch_size]
                        for start in range(0, len(uncached), micro_batch_size)
                    ]
                    generation_call_count = len(chunks)
                    generated_item_count = len(uncached)
                    try:
                        for chunk in chunks:
                            model_prompts = [
                                self._model_prompt(item["prompt"])
                                for _item_index, item in chunk
                            ]
                            encoded = tokenizer(
                                model_prompts,
                                return_tensors="pt",
                                padding=True,
                                truncation=False,
                                verbose=False,
                            )
                            encoded = {
                                key: value.to(self._resolved_device)
                                for key, value in encoded.items()
                            }
                            max_new_tokens = max(
                                self._generation_limits(
                                    language,
                                    item["action"],
                                    item["context"],
                                )[0]
                                for _item_index, item in chunk
                            )
                            timeout_seconds = max(
                                self._generation_limits(
                                    language,
                                    item["action"],
                                    item["context"],
                                )[1]
                                for _item_index, item in chunk
                            )
                            with self._runtime_inference_lock:
                                with self._torch.inference_mode():
                                    generated = self._model.generate(
                                        **encoded,
                                        max_new_tokens=max_new_tokens,
                                        max_time=self._bounded_generation_timeout(
                                            timeout_seconds,
                                            in_flight_seconds=max(
                                                0.0,
                                                time.monotonic() - generation_started,
                                            ),
                                        ),
                                        use_cache=True,
                                        do_sample=False,
                                        num_beams=1,
                                        temperature=None,
                                        top_p=None,
                                        top_k=None,
                                    )
                            prompt_width = int(encoded["input_ids"].shape[-1])
                            for (item_index, item), output_tokens in zip(
                                chunk,
                                generated,
                            ):
                                if not self._is_encoder_decoder:
                                    output_tokens = output_tokens[prompt_width:]
                                decoded = self._tokenizer.decode(
                                    output_tokens,
                                    skip_special_tokens=True,
                                )
                                outputs[item_index] = decoded
                    finally:
                        tokenizer.padding_side = original_padding_side

                if len(outputs) != len(prepared) or any(
                    output is None for output in outputs
                ):
                    raise RuntimeError("CODET5_BATCH_OUTPUT_COUNT_MISMATCH")

                generation_elapsed = (
                    max(0.0, time.monotonic() - generation_started)
                    if generated_item_count
                    else 0.0
                )
                self._generation_seconds_used += generation_elapsed
                candidates: Dict[int, CodeT5Candidate] = {}
                per_generated_action_elapsed = generation_elapsed / max(
                    1,
                    generated_item_count,
                )

                for offset, (item, output) in enumerate(zip(prepared, outputs)):
                    action = item["action"]
                    context = item["context"]
                    model_output = self._normalise_model_output(str(output))
                    model_output, suffix_repair = self._repair_java_target_suffix(
                        language,
                        model_output,
                    )
                    output_shape_issue = self._target_output_shape_issue(
                        language,
                        context,
                        model_output,
                    )
                    completion: Dict[str, Any] = {}
                    if not output_shape_issue:
                        model_output, completion = self._complete_java_constant_target_output(
                            language=language,
                            output=model_output,
                            context=context,
                            action=action,
                        )
                    merge_output = self._target_merge_output(context, model_output, action)
                    candidate_source = (
                        self._merge_target_output(source_code, context, merge_output)
                        if merge_output and not output_shape_issue
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
                        "attempt_number": first_attempt + offset,
                        "generation_elapsed_seconds": round(
                            per_generated_action_elapsed, 3
                        ),
                        "batch_generation_elapsed_seconds": round(generation_elapsed, 3),
                        "batch_size": len(prepared),
                        "batch_generation_calls": generation_call_count,
                        "batch_position": offset + 1,
                        "batch_mode": "independent_target_tensor_batch",
                        "request_generation_seconds_used": round(
                            self._generation_seconds_used, 3
                        ),
                        "input_tokens": item["input_tokens"],
                        "context_mode": "target_span",
                        "target_kind": context.kind,
                        "target_name": context.name,
                        "target_start_line": context.start_line,
                        "target_end_line": context.end_line,
                        "target_resolution": context.resolution,
                        "source_sha256": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
                        "candidate_sha256": (
                            hashlib.sha256(candidate_source.encode("utf-8")).hexdigest()
                            if candidate_source
                            else ""
                        ),
                        "model_output_chars": len(model_output),
                        "model_output_preview": model_output[:400],
                        "generation_phase": "initial_batch",
                    }
                    if completion:
                        metadata["target_contract_completion"] = completion
                    if suffix_repair:
                        metadata["target_output_repair"] = suffix_repair
                    reason = ""
                    if not model_output:
                        reason = "CODET5_EMPTY_OUTPUT"
                    elif output_shape_issue:
                        reason = output_shape_issue
                    elif candidate_source == source_code:
                        reason = "CODET5_NO_SOURCE_CHANGE"
                    elif model_output.startswith(("diff --git", "--- ", "+++ ")):
                        reason = "CODET5_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
                    candidates[item["action_index"]] = CodeT5Candidate(
                        source_code="" if reason else candidate_source,
                        status="skipped" if reason else "candidate_generated",
                        reason=reason,
                        metadata=metadata,
                        target_replacement=merge_output,
                    )
                return candidates
            except Exception:
                # A batching-specific failure must not disturb the established
                # single-action path. The engine will call propose() normally.
                if consumed_attempts:
                    self._attempt_count -= consumed_attempts
                    self._file_attempt_counts[file_key] = file_attempt_count
                return {}

    def rebase_batched_candidate(
        self,
        *,
        language: str,
        current_source_code: str,
        action: RefactoringAction,
        candidate: CodeT5Candidate,
    ) -> CodeT5Candidate:
        """Reapply a prefetched target replacement to the latest file snapshot."""

        metadata = dict(candidate.metadata or {})
        replacement = candidate.target_replacement
        if not replacement:
            return candidate
        context = self.resolve_target_context(
            language=language,
            source_code=current_source_code,
            action=action,
        )
        if context is None:
            return CodeT5Candidate(
                reason="CODET5_BATCH_TARGET_REBASE_FAILED",
                metadata=metadata,
            )
        metadata["batch_rebased"] = True
        metadata["target_start_line"] = context.start_line
        metadata["target_end_line"] = context.end_line
        return replace(
            candidate,
            source_code=self._merge_target_output(
                current_source_code,
                context,
                replacement,
            ),
            metadata=metadata,
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
    ) -> CodeT5Candidate:
        """Return one candidate or a traceable non-acceptance reason."""

        if not self.settings.enabled:
            return CodeT5Candidate(reason="CODET5_DISABLED")

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
                    "CODET5_REQUEST_ACTION_BUDGET_EXHAUSTED"
                    if self._attempt_count >= self.settings.max_actions_per_request
                    else "CODET5_FILE_ACTION_BUDGET_EXHAUSTED"
                    if file_attempt_count >= self.settings.max_actions_per_file
                    else "CODET5_REQUEST_TIME_BUDGET_EXHAUSTED"
                )
                return CodeT5Candidate(
                    reason="CODET5_ACTION_BUDGET_EXHAUSTED",
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
                context: CodeT5TargetContext | None = None
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
                    return CodeT5Candidate(
                        reason="CODET5_TARGET_CONTEXT_NOT_RESOLVED",
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
                        return CodeT5Candidate(
                            reason="CODET5_TARGET_CONTEXT_NOT_RESOLVED",
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
                        return CodeT5Candidate(
                            reason="CODET5_TARGET_CONTEXT_TOO_LARGE",
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
                    if not self._is_encoder_decoder:
                        output_tokens = output_tokens[encoded["input_ids"].shape[-1]:]
                    output = self._tokenizer.decode(
                        output_tokens,
                        skip_special_tokens=True,
                    )

                generation_elapsed_seconds = max(
                    0.0, time.monotonic() - generation_started
                )
                self._generation_seconds_used += generation_elapsed_seconds
                model_output = self._normalise_model_output(output)
                model_output, suffix_repair = self._repair_java_target_suffix(
                    language,
                    model_output,
                )
                output_shape_issue = self._target_output_shape_issue(
                    language,
                    context,
                    model_output,
                )
                target_contract_completion: Dict[str, Any] = {}
                if not output_shape_issue:
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
                    return CodeT5Candidate(reason="CODET5_EMPTY_OUTPUT", metadata=metadata)
                if output_shape_issue:
                    return CodeT5Candidate(reason=output_shape_issue, metadata=metadata)
                if candidate == source_code:
                    return CodeT5Candidate(reason="CODET5_NO_SOURCE_CHANGE", metadata=metadata)
                if model_output.startswith(("diff --git", "--- ", "+++ ")):
                    reason = (
                        "CODET5_OUTPUT_MUST_BE_TARGET_REPLACEMENT"
                        if context is not None
                        else "CODET5_OUTPUT_MUST_BE_COMPLETE_SOURCE"
                    )
                    return CodeT5Candidate(reason=reason, metadata=metadata)
                return CodeT5Candidate(
                    source_code=candidate,
                    status="candidate_generated",
                    metadata=metadata,
                )
            except Exception as exc:  # The model must never crash SCTVA execution.
                return CodeT5Candidate(
                    reason=str(exc) or "CODET5_GENERATION_FAILED",
                    metadata={
                        "provider": self.provider,
                        "model_id": self.settings.model_id,
                        "attempt_number": attempt_number,
                        "max_actions_per_request": self.settings.max_actions_per_request,
                    },
                )
