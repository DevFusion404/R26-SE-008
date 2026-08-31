"""C Replace Nested Conditional with Guard Clauses transformer."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .c_extract_class import CFunction, _parse_c_module
from .c_extract_method import _verify_c_compilation, _brace_nesting, _statement_indent, _line_of
from .c_transformers import _mask_c_non_code, _find_matching_delimiter


def _invert_c_condition(cond: str) -> str:
    """Invert a C condition expression preserving boolean semantics.

    Handles binary comparisons, simple negation, and wraps complex expressions in !(...).
    """
    stripped = cond.strip()

    # Unwrap balanced outer parentheses if present
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1].strip()
        depth = 0
        balanced = True
        for char in inner:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if balanced and depth == 0:
            stripped = inner

    # Invert explicit negation: !is_ready() -> is_ready() or !(a > b) -> a > b
    if stripped.startswith("!"):
        unnegated = stripped[1:].strip()
        if unnegated.startswith("(") and unnegated.endswith(")"):
            inner = unnegated[1:-1].strip()
            depth = 0
            balanced = True
            for char in inner:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth < 0:
                        balanced = False
                        break
            if balanced and depth == 0:
                return inner
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:\s*\([^)]*\))?", unnegated):
            return unnegated

    # Check for top-level boolean operators (&&, ||) outside parentheses
    top_level_ops = []
    depth = 0
    for i, char in enumerate(stripped):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0:
            if stripped[i:i+2] in {"&&", "||"}:
                top_level_ops.append(stripped[i:i+2])

    if not top_level_ops:
        # Invert simple relational operators
        for op, inv_op in [
            (">=", "<"),
            ("<=", ">"),
            ("==", "!="),
            ("!=", "=="),
            (">", "<="),
            ("<", ">="),
        ]:
            idx = -1
            d = 0
            for i, c in enumerate(stripped):
                if c in "([":
                    d += 1
                elif c in ")]":
                    d -= 1
                elif d == 0 and stripped[i:i+len(op)] == op:
                    # Prevent matching partial operators like >>, <<, ->, <=, >=
                    if op in {">", "<"} and i > 0 and stripped[i-1] in {">", "<", "-"}:
                        continue
                    if op in {">", "<"} and i + 1 < len(stripped) and stripped[i+1] in {">", "<", "="}:
                        continue
                    idx = i
                    break
            if idx != -1:
                left = stripped[:idx].strip()
                right = stripped[idx+len(op):].strip()
                return f"{left} {inv_op} {right}"

    return f"!({stripped})"


def _deduce_return_statement(
    function: CFunction,
    source_code: str,
    *,
    is_inside_loop: bool = False,
) -> str:
    """Determine the correct failure/exit statement for early guard clauses."""
    if is_inside_loop:
        return "continue;"

    header = function.header.strip()
    name = function.name
    name_idx = header.rfind(name)
    ret_type = header[:name_idx].strip() if name_idx > 0 else "void"

    # Void functions exit with bare return
    if "void" in ret_type.split():
        return "return;"

    # Pointer return types exit with NULL
    if "*" in ret_type:
        return "return NULL;"

    # Boolean return types exit with false
    if "bool" in ret_type.split() or "_Bool" in ret_type.split():
        return "return false;"

    # Floating-point return types
    if "float" in ret_type.split() or "double" in ret_type.split():
        return "return 0.0;"

    # Inspect function body for existing trailing return statement to match return convention
    body_text = source_code[function.open_brace + 1:function.end - 1].strip()
    trailing_return_match = re.search(r"return\s+([^;]+)\s*;\s*$", body_text)
    if trailing_return_match:
        val = trailing_return_match.group(1).strip()
        return f"return {val};"

    return "return 0;"


def _find_nested_if_chain(
    body_source: str,
    body_offset: int,
) -> Optional[Dict[str, Any]]:
    """Scan function body for a chain of nested if statements."""
    masked = _mask_c_non_code(body_source)
    if_matches = list(re.finditer(r"\bif\s*\(", masked))
    if not if_matches:
        return None

    for match in if_matches:
        outer_start = match.start()
        cond_open = masked.find("(", match.start())
        cond_close = _find_matching_delimiter(masked, cond_open, "(", ")")
        if cond_close is None:
            continue

        then_open = masked.find("{", cond_close)
        if then_open == -1:
            continue
        between = masked[cond_close + 1:then_open].strip()
        if between:
            continue

        then_close = _find_matching_delimiter(masked, then_open, "{", "}")
        if then_close is None:
            continue

        outer_end = then_close + 1

        # Check if outer if is enclosed inside a loop in body_source
        prefix_body = masked[:outer_start]
        is_inside_loop = bool(re.search(r"\b(for|while|do)\b[^{}]*\{[^{}]*$", prefix_body))

        conditions: List[str] = [body_source[cond_open + 1:cond_close].strip()]
        current_then_open = then_open
        current_then_close = then_close

        innermost_body = ""
        has_nesting = False

        while True:
            inner_content = body_source[current_then_open + 1:current_then_close]
            inner_masked = masked[current_then_open + 1:current_then_close]

            inner_if_match = re.search(r"^\s*if\s*\(", inner_masked)
            if not inner_if_match:
                innermost_body = inner_content
                break

            inner_if_start = current_then_open + 1 + inner_if_match.start()
            inner_cond_open = masked.find("(", inner_if_start)
            inner_cond_close = _find_matching_delimiter(masked, inner_cond_open, "(", ")")
            if inner_cond_close is None or inner_cond_close >= current_then_close:
                innermost_body = inner_content
                break

            inner_then_open = masked.find("{", inner_cond_close)
            if inner_then_open == -1 or inner_then_open >= current_then_close:
                innermost_body = inner_content
                break

            inner_then_close = _find_matching_delimiter(masked, inner_then_open, "{", "}")
            if inner_then_close is None or inner_then_close >= current_then_close:
                innermost_body = inner_content
                break

            prefix = inner_masked[:inner_if_match.start()].strip()
            suffix = inner_masked[inner_then_close + 1 - (current_then_open + 1):].strip()
            if prefix or suffix:
                innermost_body = inner_content
                break

            conditions.append(body_source[inner_cond_open + 1:inner_cond_close].strip())
            has_nesting = True
            current_then_open = inner_then_open
            current_then_close = inner_then_close

        if has_nesting and len(conditions) >= 2:
            return {
                "outer_start": body_offset + outer_start,
                "outer_end": body_offset + outer_end,
                "conditions": conditions,
                "innermost_body": innermost_body,
                "innermost_open": body_offset + current_then_open,
                "innermost_close": body_offset + current_then_close,
                "is_inside_loop": is_inside_loop,
            }

    return None


def apply_replace_nested_conditional_with_guard_clauses(
    source_code: str,
    method_name: Optional[str] = None,
    target_line: Optional[int] = None,
    *,
    source_line: Optional[int] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    target_lines: Optional[Sequence[Any]] = None,
    source_file: str = "",
) -> Tuple[str, int, Dict[str, Any]]:
    """Transform deeply nested C conditionals into early return guard clauses.

    ``target_line`` is retained for older callers.  The engine now sends the
    normalized RDP fields, so accept those aliases here instead of allowing a
    TypeError to escape through the HTTP endpoint.
    """

    def as_line(value: Any) -> Optional[int]:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    normalized_lines = [
        line for line in (as_line(value) for value in (target_lines or ()))
        if line is not None
    ]
    resolved_line = (
        as_line(target_line)
        or as_line(source_line)
        or as_line(start_line)
        or as_line(end_line)
        or (normalized_lines[0] if normalized_lines else None)
    )
    metadata: Dict[str, Any] = {
        "refactoring": "Replace Nested Conditional with Guard Clauses",
        "language": "c",
        "method": method_name or "",
        "source_method": method_name or "",
        "source_file": source_file,
        "source_line": resolved_line,
        "target_lines": normalized_lines,
        "plan_compliance": "FAIL",
    }

    if not source_code or not source_code.strip():
        metadata["status"] = "review_required"
        metadata["reason"] = "EMPTY_SOURCE"
        return source_code, 0, metadata

    module = _parse_c_module(source_code)
    target_function: Optional[CFunction] = None

    if method_name:
        candidates = module.functions_by_name.get(method_name, [])
        if len(candidates) == 1:
            target_function = candidates[0]
        elif len(candidates) > 1:
            if resolved_line is not None:
                for cand in candidates:
                    start_l = _line_of(source_code, cand.start)
                    end_l = _line_of(source_code, cand.end)
                    if start_l <= resolved_line <= end_l:
                        target_function = cand
                        break
            if not target_function:
                metadata["status"] = "review_required"
                metadata["reason"] = "AMBIGUOUS_FUNCTION_TARGET"
                return source_code, 0, metadata

    if not target_function and resolved_line is not None:
        for cand in module.functions:
            start_l = _line_of(source_code, cand.start)
            end_l = _line_of(source_code, cand.end)
            if start_l <= resolved_line <= end_l:
                target_function = cand
                break

    if not target_function:
        # Fallback to finding functions with deep nesting (> 3)
        deep_fns = [f for f in module.functions if _brace_nesting(f.body) > 3]
        if len(deep_fns) == 1:
            target_function = deep_fns[0]
        elif len(module.functions) == 1:
            target_function = module.functions[0]

    if not target_function:
        metadata["status"] = "review_required"
        metadata["reason"] = "TARGET_FUNCTION_NOT_FOUND"
        return source_code, 0, metadata

    metadata["method"] = target_function.name
    metadata["source_method"] = target_function.name
    before_depth = _brace_nesting(target_function.body)
    metadata["before_nesting_depth"] = before_depth

    body_offset = target_function.open_brace + 1
    body_source = source_code[body_offset:target_function.end - 1]

    chain_info = _find_nested_if_chain(body_source, body_offset)
    if not chain_info:
        metadata["status"] = "review_required"
        metadata["reason"] = "NO_NESTED_CONDITIONAL_FOUND"
        return source_code, 0, metadata

    outer_start = chain_info["outer_start"]
    outer_end = chain_info["outer_end"]
    conditions = chain_info["conditions"]
    innermost_body = chain_info["innermost_body"]
    is_inside_loop = chain_info.get("is_inside_loop", False)
    indent = _statement_indent(source_code, outer_start) or "    "

    trailing_text = source_code[outer_end:target_function.end - 1].strip()
    default_ret = _deduce_return_statement(target_function, source_code, is_inside_loop=is_inside_loop)

    trailing_return_match = re.fullmatch(r"return(?:\s+[^;]+)?\s*;", trailing_text)
    is_terminal = (not trailing_text) or bool(trailing_return_match) or is_inside_loop

    if is_terminal:
        exit_stmt = trailing_text if trailing_return_match else default_ret
        guard_clauses: List[str] = []
        for cond in conditions:
            inverted = _invert_c_condition(cond)
            guard_clauses.append(f"{indent}if ({inverted}) {{\n{indent}    {exit_stmt}\n{indent}}}")

        body_lines = innermost_body.strip().splitlines()
        reindented_body = "\n".join(
            (f"{indent}{line.strip()}" if line.strip() else "") for line in body_lines
        )

        replacement_chunk = "\n".join(guard_clauses) + "\n" + (reindented_body if reindented_body else "")
        if not replacement_chunk.endswith("\n"):
            replacement_chunk += "\n"

        if trailing_return_match:
            trailing_start = source_code.find(trailing_text, outer_end)
            transformed = (
                source_code[:outer_start]
                + replacement_chunk
                + source_code[trailing_start + len(trailing_text):]
            )
        else:
            transformed = (
                source_code[:outer_start]
                + replacement_chunk
                + source_code[outer_end:]
            )
    else:
        # If there are statements after the nested if block in non-void function, flatten conditions into single guard
        combined_cond = " && ".join(f"({c})" for c in conditions)
        body_lines = innermost_body.strip().splitlines()
        reindented_body = "\n".join(
            (f"{indent}    {line.strip()}" if line.strip() else "") for line in body_lines
        )

        replacement_chunk = f"{indent}if ({combined_cond}) {{\n{reindented_body}\n{indent}}}\n"
        transformed = source_code[:outer_start] + replacement_chunk + source_code[outer_end:]

    # Recalculate nesting depth
    post_module = _parse_c_module(transformed)
    post_fn = next((f for f in post_module.functions if f.name == target_function.name), None)
    if post_fn:
        after_depth = _brace_nesting(post_fn.body)
    else:
        after_depth = before_depth

    metadata["after_nesting_depth"] = after_depth

    # Compile with GCC / Clang
    comp_status, comp_msg = _verify_c_compilation(transformed)
    metadata["compiler_validation"] = comp_msg
    if comp_status == "FAIL":
        metadata["status"] = "review_required"
        metadata["reason"] = "LOCAL_SOURCE_COMPILATION_ERROR"
        return source_code, 0, metadata

    nesting_reduced = after_depth < before_depth and after_depth <= 4
    metadata["status"] = "success" if nesting_reduced else "review_required"
    metadata["plan_compliance"] = "PASS" if nesting_reduced else "FAIL"
    metadata["nesting_reduced"] = nesting_reduced
    metadata["smell_reduction"] = "PASS" if nesting_reduced else "FAIL"
    metadata["conditions_flattened"] = len(conditions)

    return transformed, 1, metadata


def validate_c_guard_clauses(
    original_code: str,
    transformed_code: str,
    *,
    method: str = "",
) -> Dict[str, Any]:
    """Validate that transformed C code reduced nesting depth safely below CUQA threshold."""
    before_mod = _parse_c_module(original_code)
    after_mod = _parse_c_module(transformed_code)

    before_fn = next((f for f in before_mod.functions if not method or f.name == method), None)
    after_fn = next((f for f in after_mod.functions if not method or f.name == method), None)

    if not before_fn or not after_fn:
        return {"passed": False, "reason": "target_function_missing"}

    before_depth = _brace_nesting(before_fn.body)
    after_depth = _brace_nesting(after_fn.body)

    passed = after_depth <= 4 and after_depth < before_depth

    return {
        "passed": bool(passed),
        "language": "c",
        "method": after_fn.name,
        "before_nesting_depth": before_depth,
        "after_nesting_depth": after_depth,
        "smell_reduction": "PASS" if passed else "FAIL",
        "checks": {
            "function_exists": True,
            "nesting_depth_reduced": after_depth < before_depth,
            "below_cuqa_threshold": after_depth <= 4,
        },
    }
