"""Shared lexical utilities for conservative Extract Method transformations.

Python extraction is AST-driven. Java and C reuse the repository's existing
class/module parsers and use this scanner only to identify complete, direct
statements inside an already-resolved method or function body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


MAX_EXTRACTED_PARAMETERS = 6
MIN_EXTRACTED_LOC = 3


@dataclass(frozen=True)
class StatementSpan:
    start: int
    end: int
    text: str

    @property
    def loc(self) -> int:
        return max(1, self.text.count("\n") + (0 if self.text.endswith("\n") else 1))


def mask_c_like(source: str) -> str:
    """Mask comments and literals while preserving offsets and newlines."""

    chars = list(source)
    state = "code"
    index = 0
    while index < len(chars):
        char = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                state = "line_comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                chars[index] = chars[index + 1] = " "
                state = "block_comment"
                index += 2
                continue
            if char == '"':
                chars[index] = " "
                state = "string"
            elif char == "'":
                chars[index] = " "
                state = "char"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                state = "code"
                index += 2
                continue
            if char != "\n":
                chars[index] = " "
        else:
            quote = '"' if state == "string" else "'"
            if char == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                index += 2
                continue
            if char == quote:
                chars[index] = " "
                state = "code"
            elif char != "\n":
                chars[index] = " "
        index += 1
    return "".join(chars)


def direct_c_like_statements(body: str, *, body_offset: int = 0) -> list[StatementSpan]:
    """Return complete direct statements from a Java/C routine body.

    Compound statements are kept whole, including Java ``else/catch/finally``
    continuations and C ``do ... while`` tails. Malformed bodies return the
    safely parsed prefix; downstream selection rejects an empty candidate.
    """

    masked = mask_c_like(body)
    statements: list[StatementSpan] = []
    index = 0
    while index < len(body):
        index = _skip_space(masked, index)
        if index >= len(body):
            break
        start = index
        end = _statement_end(masked, start)
        if end is None or end <= start:
            break
        statements.append(
            StatementSpan(
                start=body_offset + start,
                end=body_offset + end,
                text=body[start:end],
            )
        )
        index = end
    return statements


def candidate_windows(
    statements: Sequence[StatementSpan],
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    source: str = "",
) -> list[list[StatementSpan]]:
    """Build semantic statement windows, treating line ranges as hints only."""

    candidates: list[list[StatementSpan]] = []
    if start_line and end_line and source:
        hinted = [
            item
            for item in statements
            if _line_of(source, item.end - 1) >= start_line
            and _line_of(source, item.start) <= end_line
        ]
        if hinted:
            candidates.append(hinted)

    count = len(statements)
    max_width = min(4, max(1, count - 1))
    for width in range(max_width, 1, -1):
        for start in range(0, count - width + 1):
            candidates.append(list(statements[start:start + width]))

    for statement in statements:
        if statement.loc >= MIN_EXTRACTED_LOC and "{" in mask_c_like(statement.text):
            candidates.append([statement])

    unique: list[list[StatementSpan]] = []
    seen: set[tuple[int, int]] = set()
    for candidate in candidates:
        key = (candidate[0].start, candidate[-1].end)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def has_unsafe_cross_boundary_flow(text: str, *, language: str) -> bool:
    masked = mask_c_like(text)
    forbidden = ["break", "continue", "return", "yield"]
    if language == "c":
        forbidden.append("goto")
    if language == "java":
        # Moving a direct throw changes the current static behavior signature.
        forbidden.append("throw")
    if any(re.search(rf"\b{word}\b", masked) for word in forbidden):
        return True
    if language == "c" and re.search(r"(?m)^\s*[A-Za-z_][A-Za-z0-9_]*\s*:", masked):
        return True
    return False


def identifiers(text: str) -> set[str]:
    return set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", mask_c_like(text)))


def control_complexity(text: str) -> int:
    masked = mask_c_like(text)
    return 1 + len(
        re.findall(r"\b(?:if|else\s+if|for|while|switch|case|catch)\b|&&|\|\||\?", masked)
    )


def nonblank_loc(text: str) -> int:
    return sum(bool(line.strip()) for line in text.splitlines())


def normalize_signature(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def apply_edits(source: str, edits: Iterable[tuple[int, int, str]]) -> str:
    transformed = source
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        transformed = transformed[:start] + replacement + transformed[end:]
    return transformed


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, max(0, offset)) + 1


def _skip_space(masked: str, index: int) -> int:
    while index < len(masked) and masked[index].isspace():
        index += 1
    return index


def _statement_end(masked: str, start: int) -> int | None:
    parens = brackets = braces = 0
    index = start
    saw_brace = False
    while index < len(masked):
        char = masked[index]
        if char == "(":
            parens += 1
        elif char == ")":
            parens = max(0, parens - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif char == "{" and parens == 0 and brackets == 0:
            braces += 1
            saw_brace = True
        elif char == "}" and parens == 0 and brackets == 0:
            if braces == 0:
                return None
            braces -= 1
            if braces == 0:
                continuation = _compound_continuation(masked, start, index + 1)
                if continuation is not None:
                    index = continuation
                    continue
                tail = _skip_space(masked, index + 1)
                if tail < len(masked) and masked[tail] == ";":
                    return tail + 1
                return index + 1
        elif char == ";" and parens == 0 and brackets == 0 and braces == 0:
            return index + 1
        index += 1
    if saw_brace and braces == 0:
        return len(masked)
    return None


def _compound_continuation(masked: str, statement_start: int, after_brace: int) -> int | None:
    tail = _skip_space(masked, after_brace)
    word_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", masked[tail:])
    if not word_match:
        return None
    word = word_match.group(0)
    if word in {"else", "catch", "finally"}:
        return tail + len(word)
    statement_prefix = masked[statement_start:after_brace].lstrip()
    if word == "while" and statement_prefix.startswith("do"):
        return tail + len(word)
    return None
