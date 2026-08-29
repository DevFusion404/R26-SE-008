"""AST-guided, source-preserving Python Extract Method refactoring."""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .extract_method_common import MAX_EXTRACTED_PARAMETERS, MIN_EXTRACTED_LOC, nonblank_loc
from .python_ast_fingerprints import (
    literal_constant_bindings,
    meaningful_top_level_statements,
    statement_records,
)


REVIEW_REQUIRED = "review_required"
NOT_APPLICABLE = "not_applicable"
ALREADY_APPLIED = "already_applied"


@dataclass
class PythonTarget:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    parent_class: ast.ClassDef | None
    siblings: list[ast.stmt]


@dataclass
class PythonFlow:
    inputs: list[str]
    outputs: list[str]
    locals: list[str]
    writes: set[str]
    reads: set[str]


def target_match_count(
    source_code: str,
    *,
    method_name: str,
    source_class: str = "",
    method_signature: str = "",
) -> int:
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 0
    return len(_find_targets(tree, method_name, source_class, method_signature))


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
    metadata = _base_metadata(
        method_name=method_name,
        new_method_name=new_method_name,
        source_class=source_class,
        source_file=source_file or current_file_name,
    )
    if source_resolution_error:
        return _review(source_code, source_resolution_error, metadata)
    if not _valid_identifier(method_name) or not _valid_identifier(new_method_name):
        return _review(source_code, "INVALID_METHOD_TARGET_OR_NAME", metadata)
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(source_code, "SOURCE_PARSE_FAILED", metadata)

    targets, resolved_source_class = _find_targets_with_stale_class_recovery(
        tree,
        method_name=method_name,
        source_class=source_class,
        method_signature=method_signature,
    )
    if not targets:
        return _review(source_code, "METHOD_TARGET_NOT_FOUND", metadata)
    if len(targets) != 1:
        return _review(source_code, "AMBIGUOUS_METHOD_TARGET", metadata)
    target = targets[0]
    if any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == new_method_name
        for item in target.siblings
    ):
        if _target_calls_helper(target.node, new_method_name):
            metadata.update({"status": ALREADY_APPLIED, "reason": "ALREADY_APPLIED", "plan_compliance": "PASS"})
            return source_code, 0, metadata
        return _review(source_code, "EXTRACTED_METHOD_NAME_COLLISION", metadata)

    body = list(target.node.body)
    if body and _is_docstring(body[0]):
        body = body[1:]
    # Two top-level statements can still contain a substantial cohesive block,
    # for example a heading print followed by a large try/except workflow.
    if len(body) < 2:
        return _not_applicable(
            source_code,
            "METHOD_HAS_NO_MEANINGFUL_EXTRACTABLE_BLOCK",
            metadata,
        )

    before_metrics = _method_metrics(target.node)
    candidate = _select_candidate(
        target,
        body,
        start_line=start_line,
        end_line=end_line,
    )
    if candidate is None:
        return _review(source_code, "NO_SAFE_COHESIVE_BLOCK", {**metadata, "before_metrics": before_metrics})
    selected, flow = candidate
    if len(flow.inputs) + len(flow.outputs) > MAX_EXTRACTED_PARAMETERS:
        return _review(source_code, "TOO_MANY_PARAMETERS", {**metadata, "before_metrics": before_metrics})

    context = _method_context(target, source_code)
    if context["unsupported_reason"]:
        return _review(source_code, context["unsupported_reason"], {**metadata, "before_metrics": before_metrics})

    transformed = _rewrite(
        source_code,
        target=target,
        selected=selected,
        flow=flow,
        new_method_name=new_method_name,
        context=context,
    )
    try:
        transformed_tree = ast.parse(transformed)
    except SyntaxError:
        return _review(source_code, "CANDIDATE_SYNTAX_FAILED", {**metadata, "before_metrics": before_metrics})
    transformed_targets, _ = _find_targets_with_stale_class_recovery(
        transformed_tree,
        method_name=method_name,
        source_class=resolved_source_class,
        method_signature=method_signature,
    )
    if len(transformed_targets) != 1:
        return _review(source_code, "POST_TRANSFORM_TARGET_VALIDATION_FAILED", {**metadata, "before_metrics": before_metrics})
    after_metrics = _method_metrics(transformed_targets[0].node)
    helper_matches, _ = _find_targets_with_stale_class_recovery(
        transformed_tree,
        method_name=new_method_name,
        source_class=resolved_source_class,
        method_signature="",
    )
    reduction_passed = _meaningfully_reduced(before_metrics, after_metrics, selected)
    structural_passed = len(helper_matches) == 1 and _target_calls_helper(transformed_targets[0].node, new_method_name)
    if not structural_passed or not reduction_passed:
        reason = "EXTRACT_METHOD_STRUCTURE_NOT_PROVEN" if not structural_passed else "LONG_METHOD_NOT_REDUCED"
        return _review(
            source_code,
            reason,
            {**metadata, "before_metrics": before_metrics, "after_metrics": after_metrics},
        )

    selected_start = min(item.lineno for item in selected)
    selected_end = max(getattr(item, "end_lineno", item.lineno) for item in selected)
    constant_bindings = literal_constant_bindings(tree)
    selected_statement_records = statement_records(
        selected,
        constant_bindings=constant_bindings,
    )
    caller_statement_records = statement_records(
        meaningful_top_level_statements(target.node, exclude_direct_returns=True),
        constant_bindings=constant_bindings,
    )
    metadata.update({
        "status": "success",
        "reason": "extract_method_applied",
        "plan_compliance": "PASS",
        "source_range_hint": {"start_line": start_line, "end_line": end_line},
        "resolved_source_range": {"start_line": selected_start, "end_line": selected_end},
        "inputs": flow.inputs,
        "outputs": flow.outputs,
        "locals": flow.locals,
        "selected_ast_statements": selected_statement_records,
        "pre_extraction_caller_ast_statements": caller_statement_records,
        "selected_top_level_statement_count": len(selected_statement_records),
        "statement_identity": "normalized_python_ast",
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
    if resolved_source_class != source_class:
        metadata["source_class_resolution"] = "stale_module_class_ignored"
    return transformed, 1, metadata


def _find_targets(
    tree: ast.Module,
    method_name: str,
    source_class: str,
    method_signature: str,
) -> list[PythonTarget]:
    targets: list[PythonTarget] = []
    normalized_signature = re.sub(r"\s+", "", method_signature or "")
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if source_class or item.name != method_name:
                continue
            target = PythonTarget(item, None, tree.body)
            if _signature_matches(target.node, normalized_signature):
                targets.append(target)
        elif isinstance(item, ast.ClassDef):
            if source_class and item.name != source_class:
                continue
            for member in item.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == method_name:
                    target = PythonTarget(member, item, item.body)
                    if _signature_matches(target.node, normalized_signature):
                        targets.append(target)
    return targets


def _find_targets_with_stale_class_recovery(
    tree: ast.Module,
    *,
    method_name: str,
    source_class: str,
    method_signature: str,
) -> tuple[list[PythonTarget], str]:
    """Resolve a unique method while rejecting an RDP module-name-as-class error.

    Some RDP plans place a Python filename stem in ``target.class``.  Python
    module functions do not have a class owner, so a strict lookup would miss
    an otherwise exact and unambiguous method.  Keep strict resolution first;
    only drop the class constraint when the routine name identifies exactly one
    function or method in the parsed module.
    """

    strict_matches = _find_targets(
        tree,
        method_name,
        source_class,
        method_signature,
    )
    if strict_matches or not source_class:
        return strict_matches, source_class

    recovered_matches = _find_targets(tree, method_name, "", method_signature)
    if len(recovered_matches) != 1 and method_signature:
        # RDP signatures can be stale too. A unique routine name is still a
        # safe identity; multiple candidates remain deliberately ambiguous.
        recovered_matches = _find_targets(tree, method_name, "", "")
    if len(recovered_matches) != 1:
        return [], source_class

    recovered = recovered_matches[0]
    return recovered_matches, recovered.parent_class.name if recovered.parent_class else ""


def _signature_matches(node: ast.FunctionDef | ast.AsyncFunctionDef, normalized_signature: str) -> bool:
    if not normalized_signature:
        return True
    rendered = f"{node.name}({','.join(arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs])})"
    return normalized_signature in {re.sub(r"\s+", "", rendered), re.sub(r"\s+", "", ast.unparse(node.args))}


def _select_candidate(
    target: PythonTarget,
    body: list[ast.stmt],
    *,
    start_line: int | None,
    end_line: int | None,
) -> tuple[list[ast.stmt], PythonFlow] | None:
    windows: list[list[ast.stmt]] = []
    hinted_window: list[ast.stmt] | None = None
    if start_line and end_line:
        hinted = [
            item for item in body
            if getattr(item, "end_lineno", item.lineno) >= start_line and item.lineno <= end_line
        ]
        # A plan range is semantic intent. If it identifies a proper subset of
        # the routine, do not silently extract a different larger block merely
        # because that block has a higher generic complexity score.
        if hinted and len(hinted) < len(body):
            hinted_window = hinted
            windows.append(hinted)
    max_width = min(4, len(body) - 1)
    for width in range(max_width, 1, -1):
        for index in range(0, len(body) - width + 1):
            windows.append(body[index:index + width])
    for item in body:
        if isinstance(item, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)):
            windows.append([item])

    unique: list[list[ast.stmt]] = []
    seen: set[tuple[int, int]] = set()
    for window in windows:
        key = (window[0].lineno, getattr(window[-1], "end_lineno", window[-1].lineno))
        if key not in seen:
            seen.add(key)
            unique.append(window)

    scored: list[tuple[float, list[ast.stmt], PythonFlow]] = []
    candidate_windows = unique
    if hinted_window is not None:
        candidate_windows = [
            window for window in unique
            if window[0] is hinted_window[0] and window[-1] is hinted_window[-1]
        ]

    for window in candidate_windows:
        if _unsafe_python_flow(window):
            continue
        start_index = body.index(window[0])
        end_index = body.index(window[-1]) + 1
        if len(body) - len(window) < 1:
            continue
        loc = getattr(window[-1], "end_lineno", window[-1].lineno) - window[0].lineno + 1
        complexity = _python_complexity_nodes(window)
        if loc < MIN_EXTRACTED_LOC and complexity <= 1:
            continue
        flow = _python_flow(target.node, body, start_index, end_index)
        if len(flow.inputs) + len(flow.outputs) > MAX_EXTRACTED_PARAMETERS:
            continue
        shared_names = len(flow.reads & flow.writes)
        hint_bonus = 20 if start_line and end_line and window[0].lineno <= end_line and getattr(window[-1], "end_lineno", 0) >= start_line else 0
        score = hint_bonus + (complexity * 4) + loc + shared_names - (len(flow.inputs) * 0.5)
        scored.append((score, window, flow))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return scored[0][1], scored[0][2]


def _python_flow(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    body: list[ast.stmt],
    start_index: int,
    end_index: int,
) -> PythonFlow:
    params = {
        arg.arg
        for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    }
    if function.args.vararg:
        params.add(function.args.vararg.arg)
    if function.args.kwarg:
        params.add(function.args.kwarg.arg)
    defined_before = params | _stored_names(body[:start_index])
    reads = _loaded_names(body[start_index:end_index])
    writes = _stored_names(body[start_index:end_index])
    reads_after = _loaded_names(body[end_index:])
    implicit = {"self", "cls"}
    inputs = sorted((reads & defined_before) - implicit)
    outputs = sorted(writes & reads_after)
    locals_only = sorted(writes - set(outputs))
    return PythonFlow(inputs, outputs, locals_only, writes, reads)


class _ScopedNameVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loads: set[str] = set()
        self.stores: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loads.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.stores.add(node.id)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # ``total += value`` reads the old ``total`` before writing the new
        # value. Treating it as store-only creates a helper-local unbound name.
        if isinstance(node.target, ast.Name):
            self.loads.add(node.target.id)
            self.stores.add(node.target.id)
        else:
            self.visit(node.target)
        self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _loaded_names(statements: Iterable[ast.stmt]) -> set[str]:
    visitor = _ScopedNameVisitor()
    for statement in statements:
        visitor.visit(statement)
    return visitor.loads - set(dir(builtins))


def _stored_names(statements: Iterable[ast.stmt]) -> set[str]:
    visitor = _ScopedNameVisitor()
    for statement in statements:
        visitor.visit(statement)
    return visitor.stores


def _unsafe_python_flow(statements: Sequence[ast.stmt]) -> bool:
    forbidden = (
        ast.Return,
        ast.Yield,
        ast.YieldFrom,
        ast.Break,
        ast.Continue,
        ast.Global,
        ast.Nonlocal,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Lambda,
    )
    return any(isinstance(node, forbidden) for statement in statements for node in ast.walk(statement))


def _method_context(target: PythonTarget, source_code: str) -> dict[str, Any]:
    node = target.node
    decorators = {ast.unparse(item) for item in node.decorator_list}
    args = [*node.args.posonlyargs, *node.args.args]
    receiver = args[0].arg if args else ""
    if target.parent_class is None:
        kind = "module"
    elif "staticmethod" in decorators:
        kind = "static"
    elif "classmethod" in decorators:
        kind = "class"
    else:
        kind = "instance"
    unsupported = ""
    if kind == "instance" and receiver != "self":
        unsupported = "UNSUPPORTED_INSTANCE_RECEIVER"
    elif kind == "class" and receiver != "cls":
        unsupported = "UNSUPPORTED_CLASS_RECEIVER"
    indent = source_code.splitlines(keepends=True)[node.lineno - 1]
    sibling_indent = indent[: len(indent) - len(indent.lstrip(" \t"))]
    return {
        "kind": kind,
        "receiver": receiver,
        "class_name": target.parent_class.name if target.parent_class else "",
        "sibling_indent": sibling_indent,
        "body_indent": sibling_indent + "    ",
        "async": isinstance(node, ast.AsyncFunctionDef),
        "unsupported_reason": unsupported,
        "annotations": _parameter_annotations(node),
    }


def _parameter_annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    result: dict[str, str] = {}
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        if arg.annotation is not None:
            result[arg.arg] = ast.unparse(arg.annotation)
    return result


def _rewrite(
    source_code: str,
    *,
    target: PythonTarget,
    selected: Sequence[ast.stmt],
    flow: PythonFlow,
    new_method_name: str,
    context: dict[str, Any],
) -> str:
    lines = source_code.splitlines(keepends=True)
    selected_start = selected[0].lineno - 1
    selected_end = getattr(selected[-1], "end_lineno", selected[-1].lineno)
    body_indent = context["body_indent"]
    call_args = ", ".join(flow.inputs)
    if context["kind"] == "instance":
        call_target = f"self.{new_method_name}"
    elif context["kind"] == "class":
        call_target = f"cls.{new_method_name}"
    elif context["kind"] == "static":
        call_target = f"{context['class_name']}.{new_method_name}"
    else:
        call_target = new_method_name
    awaited = "await " if context["async"] else ""
    call_expression = f"{awaited}{call_target}({call_args})"
    if flow.outputs:
        replacement = f"{body_indent}{', '.join(flow.outputs)} = {call_expression}\n"
    else:
        replacement = f"{body_indent}{call_expression}\n"

    sibling_indent = context["sibling_indent"]
    decorators = ""
    helper_params = list(flow.inputs)
    if context["kind"] == "instance":
        helper_params.insert(0, "self")
    elif context["kind"] == "class":
        decorators = f"{sibling_indent}@classmethod\n"
        helper_params.insert(0, "cls")
    elif context["kind"] == "static":
        decorators = f"{sibling_indent}@staticmethod\n"
    annotations = context["annotations"]
    rendered_params = [
        f"{name}: {annotations[name]}" if name in annotations else name
        for name in helper_params
    ]
    async_prefix = "async " if context["async"] else ""
    helper = (
        f"\n{decorators}{sibling_indent}{async_prefix}def {new_method_name}({', '.join(rendered_params)}):\n"
        + "".join(lines[selected_start:selected_end])
    )
    if flow.outputs:
        helper += f"{body_indent}return {', '.join(flow.outputs)}\n"

    transformed_lines = lines[:selected_start] + [replacement] + lines[selected_end:]
    removed_count = selected_end - selected_start
    insertion_index = target.node.end_lineno + (1 - removed_count)
    transformed_lines[insertion_index:insertion_index] = [helper]
    return "".join(transformed_lines)


def _method_metrics(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
    complexity_nodes = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.BoolOp, ast.IfExp)
    complexity = 1 + sum(isinstance(item, complexity_nodes) for item in ast.walk(node))
    nesting = _python_nesting(node)
    return {
        "loc": getattr(node, "end_lineno", node.lineno) - node.lineno + 1,
        "complexity": complexity,
        "nesting_depth": nesting,
        "statement_count": len(node.body),
        "responsibility_count": sum(not _is_docstring(item) for item in node.body),
    }


def _python_nesting(node: ast.AST) -> int:
    nested_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)

    def visit(item: ast.AST, depth: int) -> int:
        child_depth = depth + 1 if isinstance(item, nested_types) else depth
        return max([child_depth, *(visit(child, child_depth) for child in ast.iter_child_nodes(item))])

    return visit(node, 0)


def _python_complexity_nodes(statements: Sequence[ast.stmt]) -> int:
    types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.BoolOp, ast.IfExp)
    return 1 + sum(isinstance(node, types) for statement in statements for node in ast.walk(statement))


def _meaningfully_reduced(
    before: dict[str, int],
    after: dict[str, int],
    selected: Sequence[ast.stmt],
) -> bool:
    selected_loc = getattr(selected[-1], "end_lineno", selected[-1].lineno) - selected[0].lineno + 1
    logic_reduced = (
        after["statement_count"] < before["statement_count"]
        or after["responsibility_count"] < before["responsibility_count"]
        or after["complexity"] < before["complexity"]
        or after["nesting_depth"] < before["nesting_depth"]
    )
    return (
        selected_loc >= MIN_EXTRACTED_LOC
        and after["loc"] <= before["loc"] - 2
        and after["complexity"] <= before["complexity"]
        and logic_reduced
    )


def _target_calls_helper(node: ast.FunctionDef | ast.AsyncFunctionDef, helper_name: str) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        if isinstance(item.func, ast.Name) and item.func.id == helper_name:
            return True
        if isinstance(item.func, ast.Attribute) and item.func.attr == helper_name:
            return True
    return False


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _valid_identifier(value: str) -> bool:
    return bool(value and value.isidentifier() and not re.match(r"^\d", value))


def _base_metadata(*, method_name: str, new_method_name: str, source_class: str, source_file: str) -> dict[str, Any]:
    return {
        "smell": "Long Method",
        "refactoring": "Extract Method",
        "language": "python",
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


def _not_applicable(
    source_code: str,
    reason: str,
    metadata: dict[str, Any],
) -> tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        **metadata,
        "status": NOT_APPLICABLE,
        "reason": reason,
        "plan_compliance": "NOT_APPLICABLE",
        "final_decision": "NOT_APPLICABLE",
        "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
    }
