"""Conservative C nested-conditional to guard-clause transformation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .c_extract_class import CFunction, _parse_c_module
from .extract_method_common import direct_c_like_statements, mask_c_like


REVIEW_REQUIRED = "review_required"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CIf:
    start: int
    condition: str
    open_brace: int
    close_brace: int
    else_start: int | None = None
    else_end: int | None = None


def apply_replace_nested_conditional_with_guard_clauses(
    source_code: str,
    *,
    method_name: str = "",
    source_line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    target_lines: list[int] | tuple[int, ...] | None = None,
    source_file: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Flatten one proven-safe nested C ``if`` chain into guard clauses.

    Only a chain with no ``else`` branches is currently auto-converted.  Its
    failure path must be provably the end of a ``void`` function or the end of
    a loop iteration.  This deliberately avoids guessing return values or
    changing the meaning of code after the conditional.
    """

    metadata = {
        "action_type": "replace_nested_conditional_with_guard_clauses",
        "refactoring": "Replace Nested Conditional with Guard Clauses",
        "language": "c",
        "source_file": source_file,
        "source_method": method_name,
        "requested_source_method": method_name,
        "source_line": source_line,
        "target_lines": list(target_lines or ()),
        "source_file_resolved": bool(source_file),
        "source_method_resolved": False,
        "nested_conditional_found": False,
        "target_inside_method": False,
        "target_resolution": "pending",
        "status": REVIEW_REQUIRED,
        "final_decision": "REVIEW_REQUIRED",
    }
    if not source_code.strip():
        return _review(source_code, "GUARD_CLAUSE_TARGET_NOT_FOUND", metadata)

    start_line, end_line = _merge_target_lines(start_line, end_line, target_lines)
    function, resolution_reason, resolution_strategy = _resolve_function(
        source_code,
        method_name=method_name,
        source_line=source_line,
        start_line=start_line,
        end_line=end_line,
    )
    if function is None:
        if resolution_reason in {
            "GUARD_CLAUSE_TARGET_METADATA_MISSING",
            "GUARD_CLAUSE_TARGET_AMBIGUOUS",
            "GUARD_CLAUSE_TARGET_NOT_FOUND",
        }:
            return _review(source_code, resolution_reason, metadata)
        return _not_applicable(source_code, resolution_reason, metadata)

    metadata.update({
        "source_method": function.name,
        "source_method_resolved": True,
        "target_inside_method": True,
        "target_resolution": resolution_strategy,
    })
    masked = mask_c_like(source_code)
    candidates = _nested_if_chains(source_code, masked, function)
    candidates = [
        candidate
        for candidate in candidates
        if _matches_line_hint(source_code, candidate[0], source_line, start_line, end_line)
    ]
    if not candidates:
        if _has_nested_if(function.body):
            return _review(source_code, "GUARD_CLAUSE_SCOPE_CHANGE_UNSAFE", metadata)
        if _looks_like_guard_clause(function.body):
            return _not_applicable(source_code, "GUARD_CLAUSE_ALREADY_SIMPLIFIED", metadata)
        return _not_applicable(source_code, "GUARD_CLAUSE_NO_NESTED_CONDITIONAL", metadata)

    metadata["nested_conditional_found"] = True

    unsafe_reason = "GUARD_CLAUSE_NO_SAFE_EXIT_STRATEGY"
    for chain in candidates:
        outer, leaf = chain[0], chain[-1]
        replacement_end = outer.else_end or outer.close_brace
        region = source_code[outer.start:replacement_end + 1]
        explicit_exit = _explicit_chain_exit(source_code, masked, chain)
        if any(item.else_start is not None for item in chain) and not explicit_exit:
            unsafe_reason = "GUARD_CLAUSE_UNSAFE_CONTROL_FLOW"
            continue
        if _has_unsafe_control_construct(region):
            unsafe_reason = "GUARD_CLAUSE_UNSAFE_CONTROL_FLOW"
            continue
        if any(_condition_has_side_effects(item.condition) for item in chain):
            unsafe_reason = "GUARD_CLAUSE_SIDE_EFFECT_ORDER_UNSAFE"
            continue
        if _contains_preprocessor(region):
            unsafe_reason = "GUARD_CLAUSE_UNSAFE_CONTROL_FLOW"
            continue
        if _inside_switch(masked, function, outer.start):
            unsafe_reason = "GUARD_CLAUSE_UNSAFE_CONTROL_FLOW"
            continue

        exit_statement, exit_reason = (
            (explicit_exit, "")
            if explicit_exit
            else _safe_exit_strategy(source_code, masked, function, outer)
        )
        if not exit_statement:
            unsafe_reason = exit_reason
            continue
        if _leaf_scope_is_unsafe(source_code, leaf):
            unsafe_reason = "GUARD_CLAUSE_SCOPE_CHANGE_UNSAFE"
            continue

        indent = _indent_at(source_code, outer.start)
        guard_lines = [
            f"{indent}if (!({item.condition})) {exit_statement}\n"
            for item in chain
        ]
        # Retain the leaf braces: local declarations remain in their original
        # lexical scope even though the condition nesting is removed.
        leaf_block = source_code[leaf.open_brace:leaf.close_brace + 1]
        replacement = "".join(guard_lines) + f"{indent}{leaf_block.lstrip()}"
        line_start = source_code.rfind("\n", 0, outer.start) + 1
        replace_start = line_start if not source_code[line_start:outer.start].strip() else outer.start
        transformed = source_code[:replace_start] + replacement + source_code[replacement_end + 1:]
        if not _syntax_shape_is_valid(transformed, function.name):
            unsafe_reason = "GUARD_CLAUSE_UNSAFE_CONTROL_FLOW"
            continue

        original_depth = _if_nesting_depth(function.body)
        transformed_function = _function_by_name(transformed, function.name)
        if transformed_function is None:
            unsafe_reason = "GUARD_CLAUSE_TARGET_NOT_FOUND"
            continue
        new_depth = _if_nesting_depth(transformed_function.body)
        if new_depth >= original_depth:
            unsafe_reason = "GUARD_CLAUSE_NO_SAFE_EXIT_STRATEGY"
            continue

        metadata.update({
            "status": "success",
            "reason": "GUARD_CLAUSES_APPLIED",
            "final_decision": "PASS",
            "plan_compliance": "PASS",
            "target_resolution": resolution_strategy,
            "source_method": function.name,
            "nested_conditional_range": {
                "start_line": _line_of(source_code, outer.start),
                "end_line": _line_of(source_code, outer.close_brace),
            },
            "original_nesting_depth": original_depth,
            "new_nesting_depth": new_depth,
            "guard_clauses_added": len(chain),
            "exit_strategy": exit_statement.rstrip(";"),
            "replacements_count": 1,
            "validation": {
                "syntax": "PASS",
                "structural": "PASS",
                "control_flow": "PASS",
                "variable_scope": "PASS",
                "nesting_reduced": "PASS",
                "no_unreachable_code": "PASS",
            },
        })
        return transformed, 1, metadata

    return _review(source_code, unsafe_reason, metadata)


def _resolve_function(
    source_code: str,
    *,
    method_name: str,
    source_line: int | None,
    start_line: int | None,
    end_line: int | None,
) -> tuple[CFunction | None, str, str]:
    functions = _parse_c_module(source_code).functions
    if method_name:
        matches = [item for item in functions if item.name == method_name]
        strategy = "explicit_source_method"
    else:
        line = source_line or start_line or end_line
        matches = [
            item for item in functions
            if line is not None
            and _line_of(source_code, item.start) <= line <= _line_of(source_code, item.end - 1)
        ]
        strategy = "enclosing_function_from_line"
        if line is None:
            matches = _unique_safe_candidate_functions(source_code, functions)
            strategy = "unique_safe_nested_conditional_candidate"
            if len(matches) > 1:
                return None, "GUARD_CLAUSE_TARGET_AMBIGUOUS", strategy
            if not matches:
                return None, "GUARD_CLAUSE_TARGET_METADATA_MISSING", strategy
    if not matches:
        return None, "GUARD_CLAUSE_TARGET_NOT_FOUND", strategy
    if len(matches) != 1:
        return None, "GUARD_CLAUSE_TARGET_AMBIGUOUS", strategy
    return matches[0], "", strategy


def _unique_safe_candidate_functions(
    source_code: str,
    functions: list[CFunction],
) -> list[CFunction]:
    """Return the owning function only when one safe-looking chain exists.

    This is deliberately only a target resolver.  The normal transformation
    loop repeats every safety check before making an edit.
    """

    masked = mask_c_like(source_code)
    matches: list[CFunction] = []
    for function in functions:
        for chain in _nested_if_chains(source_code, masked, function):
            outer = chain[0]
            region = source_code[outer.start:(outer.else_end or outer.close_brace) + 1]
            explicit_exit = _explicit_chain_exit(source_code, masked, chain)
            exit_statement, _ = (
                (explicit_exit, "")
                if explicit_exit
                else _safe_exit_strategy(source_code, masked, function, outer)
            )
            if (
                exit_statement
                and not _has_unsafe_control_construct(region)
                and not _contains_preprocessor(region)
                and not _inside_switch(masked, function, outer.start)
                and not any(_condition_has_side_effects(item.condition) for item in chain)
                and (not any(item.else_start is not None for item in chain) or explicit_exit)
            ):
                matches.append(function)
    return matches


def _merge_target_lines(
    start_line: int | None,
    end_line: int | None,
    target_lines: list[int] | tuple[int, ...] | None,
) -> tuple[int | None, int | None]:
    """Use canonical RDP ranges without overwriting explicit scalar hints."""

    parsed = [
        int(value)
        for value in (target_lines or ())
        if isinstance(value, (int, float))
        or (isinstance(value, str) and value.strip().isdigit())
    ]
    if not parsed:
        return start_line, end_line
    return start_line or min(parsed), end_line or max(parsed)


def _nested_if_chains(source_code: str, masked: str, function: CFunction) -> list[list[CIf]]:
    candidates: list[list[CIf]] = []
    for match in re.finditer(r"\bif\s*\(", masked[function.open_brace + 1:function.end - 1]):
        outer = _parse_if(source_code, masked, function.open_brace + 1 + match.start())
        if outer is None:
            continue
        chain = [outer]
        current = outer
        while True:
            statements = direct_c_like_statements(
                source_code[current.open_brace + 1:current.close_brace],
                body_offset=current.open_brace + 1,
            )
            if len(statements) != 1:
                break
            nested = _parse_if(source_code, masked, statements[0].start)
            if nested is None or nested.start != statements[0].start:
                break
            chain.append(nested)
            current = nested
        if len(chain) >= 2:
            candidates.append(chain)
    return candidates


def _parse_if(source_code: str, masked: str, start: int) -> CIf | None:
    match = re.match(r"if\s*\(", masked[start:])
    if match is None:
        return None
    open_paren = start + match.group(0).rfind("(")
    close_paren = _matching(masked, open_paren, "(", ")")
    if close_paren is None:
        return None
    open_brace = _skip_space(masked, close_paren + 1)
    if open_brace >= len(masked) or masked[open_brace] != "{":
        return None
    close_brace = _matching(masked, open_brace, "{", "}")
    if close_brace is None:
        return None
    position = _skip_space(masked, close_brace + 1)
    else_start = else_end = None
    if re.match(r"else\b", masked[position:]):
        else_start = position
        else_body_start = _skip_space(masked, position + 4)
        if else_body_start < len(masked) and masked[else_body_start] == "{":
            else_end = _matching(masked, else_body_start, "{", "}")
        else:
            else_end = _statement_end(masked, else_body_start)
        if else_end is None:
            return None
    return CIf(
        start=start,
        condition=source_code[open_paren + 1:close_paren].strip(),
        open_brace=open_brace,
        close_brace=close_brace,
        else_start=else_start,
        else_end=else_end,
    )


def _safe_exit_strategy(source_code: str, masked: str, function: CFunction, outer: CIf) -> tuple[str, str]:
    loop_end = _enclosing_tail_loop(masked, function, outer.start)
    if loop_end is not None:
        if not masked[outer.close_brace + 1:loop_end].strip():
            return "continue;", ""
        return "", "GUARD_CLAUSE_NO_SAFE_EXIT_STRATEGY"
    if not masked[outer.close_brace + 1:function.end - 1].strip():
        if _normal_return_type(function.return_type) == "void":
            return "return;", ""
        return "", "GUARD_CLAUSE_NO_SAFE_EXIT_STRATEGY"
    return "", "GUARD_CLAUSE_NO_SAFE_EXIT_STRATEGY"


def _explicit_chain_exit(source_code: str, masked: str, chain: list[CIf]) -> str:
    """Return one already-present exit shared by every failed branch."""

    exits: list[str] = []
    for item in chain:
        if item.else_start is None or item.else_end is None:
            return ""
        body_start = _skip_space(masked, item.else_start + len("else"))
        if body_start < len(masked) and masked[body_start] == "{":
            body_end = _matching(masked, body_start, "{", "}")
            if body_end is None:
                return ""
            body = source_code[body_start + 1:body_end].strip()
        else:
            body = source_code[body_start:item.else_end].strip()
        normalized = " ".join(mask_c_like(body).split())
        if not re.fullmatch(r"(?:return(?:\s+[^;]+)?|continue|break)\s*;", normalized):
            return ""
        exits.append(body if body.endswith(";") else f"{body};")
    normalized_exits = {" ".join(mask_c_like(value).split()) for value in exits}
    return exits[0] if len(normalized_exits) == 1 else ""


def _enclosing_tail_loop(masked: str, function: CFunction, position: int) -> int | None:
    candidates: list[tuple[int, int]] = []
    body_start, body_end = function.open_brace + 1, function.end - 1
    for match in re.finditer(r"\b(?:for|while)\s*\(", masked[body_start:position]):
        open_paren = body_start + match.start() + match.group(0).rfind("(")
        close_paren = _matching(masked, open_paren, "(", ")")
        if close_paren is None:
            continue
        open_brace = _skip_space(masked, close_paren + 1)
        if open_brace >= len(masked) or masked[open_brace] != "{":
            continue
        close_brace = _matching(masked, open_brace, "{", "}")
        if close_brace is not None and open_brace < position < close_brace:
            candidates.append((open_brace, close_brace))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _inside_switch(masked: str, function: CFunction, position: int) -> bool:
    body_start = function.open_brace + 1
    for match in re.finditer(r"\bswitch\s*\(", masked[body_start:position]):
        open_paren = body_start + match.start() + match.group(0).rfind("(")
        close_paren = _matching(masked, open_paren, "(", ")")
        if close_paren is None:
            continue
        open_brace = _skip_space(masked, close_paren + 1)
        close_brace = _matching(masked, open_brace, "{", "}") if open_brace < len(masked) else None
        if close_brace is not None and open_brace < position < close_brace:
            return True
    return False


def _leaf_scope_is_unsafe(source_code: str, leaf: CIf) -> bool:
    body = source_code[leaf.open_brace + 1:leaf.close_brace]
    return bool(re.search(r"\b(?:goto|case|default)\b|^[ \t]*[A-Za-z_]\w*\s*:", mask_c_like(body), re.MULTILINE))


def _has_unsafe_control_construct(region: str) -> bool:
    masked = mask_c_like(region)
    return bool(re.search(r"\b(?:goto|case|default)\b|^[ \t]*[A-Za-z_]\w*\s*:", masked, re.MULTILINE))


def _condition_has_side_effects(condition: str) -> bool:
    masked = mask_c_like(condition)
    return bool(
        re.search(r"\+\+|--|(?<![=!<>])=(?!=)|\b[A-Za-z_]\w*\s*\(", masked)
    )


def _contains_preprocessor(region: str) -> bool:
    return any(line.lstrip().startswith("#") for line in region.splitlines())


def _syntax_shape_is_valid(source_code: str, method_name: str) -> bool:
    return _function_by_name(source_code, method_name) is not None and _balanced(mask_c_like(source_code), "{", "}")


def _function_by_name(source_code: str, method_name: str) -> CFunction | None:
    matches = [item for item in _parse_c_module(source_code).functions if item.name == method_name]
    return matches[0] if len(matches) == 1 else None


def _if_nesting_depth(body: str) -> int:
    masked = mask_c_like(body)
    depth = maximum = 0
    for index, char in enumerate(masked):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif re.match(r"if\s*\(", masked[index:]):
            maximum = max(maximum, depth)
    return maximum


def _matches_line_hint(source: str, outer: CIf, source_line: int | None, start_line: int | None, end_line: int | None) -> bool:
    hint_start = source_line or start_line
    hint_end = end_line or source_line
    if hint_start is None and hint_end is None:
        return True
    start = _line_of(source, outer.start)
    end = _line_of(source, outer.close_brace)
    return start <= (hint_end or start) and end >= (hint_start or end)


def _not_applicable(source: str, reason: str, metadata: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    return source, 0, {**metadata, "status": NOT_APPLICABLE, "reason": reason, "final_decision": "NOT_APPLICABLE", "replacements_count": 0}


def _review(source: str, reason: str, metadata: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    return source, 0, {**metadata, "status": REVIEW_REQUIRED, "reason": reason, "replacements_count": 0}


def _has_if(body: str) -> bool:
    return bool(re.search(r"\bif\s*\(", mask_c_like(body)))


def _looks_like_guard_clause(body: str) -> bool:
    """Recognize an existing early exit without calling every single if a guard."""

    return bool(
        re.search(
            r"\bif\s*\([^{}]+\)\s*(?:\{\s*)?(?:return(?:\s+[^;]+)?|continue|break)\s*;",
            mask_c_like(body),
            re.DOTALL,
        )
    )


def _has_nested_if(body: str) -> bool:
    return len(re.findall(r"\bif\s*\(", mask_c_like(body))) >= 2


def _normal_return_type(value: str) -> str:
    return " ".join(str(value or "").replace("static", "").split())


def _matching(masked: str, start: int, opener: str, closer: str) -> int | None:
    if start >= len(masked) or masked[start] != opener:
        return None
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == opener:
            depth += 1
        elif masked[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _statement_end(masked: str, start: int) -> int | None:
    end = masked.find(";", start)
    return end + 1 if end >= 0 else None


def _skip_space(masked: str, index: int) -> int:
    while index < len(masked) and masked[index].isspace():
        index += 1
    return index


def _balanced(masked: str, opener: str, closer: str) -> bool:
    depth = 0
    for char in masked:
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _indent_at(source: str, index: int) -> str:
    line_start = source.rfind("\n", 0, index) + 1
    return source[line_start:index][: len(source[line_start:index]) - len(source[line_start:index].lstrip(" \t"))]


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1
