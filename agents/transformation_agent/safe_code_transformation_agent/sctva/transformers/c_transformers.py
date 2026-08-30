"""Conservative text-based C transformers for safe refactoring actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple


_INCLUDE_RE = re.compile(r"^\s*#\s*include\b.*$", re.MULTILINE)
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b.*$", re.MULTILINE)


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not cleaned:
        cleaned = "VALUE"
    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"
    return cleaned.upper()


def _to_c_literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + _escape_c_string(value) + '"'
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "0"
    return str(value)


def _escape_c_string(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\0":
            escaped.append("\\0")
        else:
            escaped.append(char)
    return "".join(escaped)


def _constant_name_from_value(value: Any) -> str:
    if isinstance(value, bool):
        return f"CONSTANT_BOOL_{str(value).upper()}"
    if value is None:
        return "CONSTANT_NONE"
    if isinstance(value, int):
        if value < 0:
            return f"CONSTANT_NUMBER_NEG_{abs(value)}"
        return f"CONSTANT_NUMBER_{value}"
    if isinstance(value, float):
        text = str(value).replace("-", "NEG_").replace(".", "_")
        text = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
        return f"CONSTANT_NUMBER_{text}"
    if isinstance(value, str):
        return f"CONSTANT_STRING_{_sanitize_identifier(value[:24])}"
    return "CONSTANT_VALUE"


def _normalize_legacy_magic_name(cleaned: str, literal_value: Any) -> str:
    if not cleaned.startswith("MAGIC_"):
        return cleaned
    if cleaned.startswith(("MAGIC_NUMBER_", "MAGIC_STRING_", "MAGIC_BOOL_")) or cleaned in {
        "MAGIC_NONE",
        "MAGIC_VALUE",
    }:
        return _constant_name_from_value(literal_value)
    return f"CONSTANT_{cleaned[len('MAGIC_'):]}"


def _normalize_constant_name(constant_name: Optional[str], literal_value: Any) -> str:
    if not constant_name:
        return _constant_name_from_value(literal_value)

    cleaned = _sanitize_identifier(str(constant_name))
    if cleaned in {"EXTRACTED_CONSTANT", "MAGIC_CONSTANT", "CONSTANT", "VALUE_CONSTANT"}:
        return _constant_name_from_value(literal_value)
    cleaned = _normalize_legacy_magic_name(cleaned, literal_value)
    return cleaned


def _existing_define_names(source_code: str) -> set[str]:
    return {match.group(1) for match in _DEFINE_RE.finditer(source_code)}


def _unique_constant_name(source_code: str, preferred_name: str) -> str:
    existing = _existing_define_names(source_code)
    if preferred_name not in existing:
        return preferred_name
    index = 2
    while f"{preferred_name}_{index}" in existing:
        index += 1
    return f"{preferred_name}_{index}"


def _literal_pattern(literal_value: Any) -> re.Pattern[str]:
    literal_text = _to_c_literal(literal_value)
    escaped = re.escape(literal_text)
    if isinstance(literal_value, (int, float)) or literal_text in {"0", "1"}:
        return re.compile(rf"(?<![A-Za-z0-9_.]){escaped}(?![A-Za-z0-9_.])")
    return re.compile(escaped)


def _should_skip_replacement(line: str, constant_name: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return True
    if stripped.startswith("#define"):
        return True
    if re.search(rf"\b{re.escape(constant_name)}\b\s*=", stripped):
        return True
    return False


_C_NUMERIC_TOKEN_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+\.\d*|\.\d+|\d+)"
    r"(?:[eE][+-]?\d+)?)(?:[uUlLfF]*)"
)


def _inside_square_brackets(masked_source: str, index: int) -> bool:
    """Return whether ``index`` is inside an array/indexing expression."""

    depth = 0
    for char in reversed(masked_source[:index]):
        if char == "]":
            depth += 1
        elif char == "[":
            if depth:
                depth -= 1
            else:
                return True
    return False


_C_TYPE_DECLARATION_RE = re.compile(
    r"\b(?:_Atomic|_Bool|auto|bool|char|const|double|enum|extern|float|"
    r"int|long|register|restrict|short|signed|size_t|static|struct|"
    r"typedef|union|unsigned|void|volatile)\b"
)


def _enclosing_square_bracket_start(masked_source: str, index: int) -> int | None:
    """Return the opening bracket enclosing ``index``, when there is one."""

    depth = 0
    for position in range(index - 1, -1, -1):
        char = masked_source[position]
        if char == "]":
            depth += 1
        elif char == "[":
            if depth:
                depth -= 1
            else:
                return position
    return None


def _looks_like_c_type_declaration(
    masked_source: str,
    position: int,
) -> bool:
    """Conservatively identify a declaration fragment ending at ``position``."""

    boundary = max(
        masked_source.rfind(";", 0, position),
        masked_source.rfind("{", 0, position),
        masked_source.rfind("}", 0, position),
    )
    prefix = masked_source[boundary + 1 : position]

    # An assignment before the bracket is an executable expression such as
    # ``int value = values[15]`` rather than an array/type declaration.
    if "=" in prefix:
        return False
    return bool(_C_TYPE_DECLARATION_RE.search(prefix))


def _is_c_type_or_signature_context(masked_source: str, index: int) -> bool:
    """Return whether a numeric token belongs to a declaration/type construct."""

    bracket_start = _enclosing_square_bracket_start(masked_source, index)
    if bracket_start is not None:
        return _looks_like_c_type_declaration(masked_source, bracket_start)

    return _looks_like_c_type_declaration(masked_source, index)


def _c_numeric_context(
    source_code: str,
    masked_source: str,
    start: int,
    end: int,
) -> str:
    """Classify a numeric token before it is eligible for substitution."""

    line_start = masked_source.rfind("\n", 0, start) + 1
    line_end = masked_source.find("\n", end)
    if line_end < 0:
        line_end = len(masked_source)
    line_prefix = masked_source[line_start:start]
    line_suffix = masked_source[end:line_end]

    # Preprocessor directives are a separate language and are intentionally
    # excluded from this transformation.  This prevents replacements in macro
    # definitions and continued macro lines.
    if masked_source[line_start:line_end].lstrip().startswith("#"):
        return "TARGET_CONTEXT_UNSUPPORTED"

    # Case labels and enum values affect integral-language constructs rather
    # than ordinary runtime expressions.  Leave them for a dedicated,
    # compiler-validated refactoring if one is added later.
    if re.search(r"(?:^|[;{}])\s*case\b", line_prefix) and re.search(
        r":", line_suffix
    ):
        return "TARGET_CONTEXT_UNSUPPORTED"
    last_open = masked_source.rfind("{", 0, start)
    last_close = masked_source.rfind("}", 0, start)
    enum_start = masked_source.rfind("enum", 0, start)
    if enum_start > last_close and enum_start < last_open:
        return "TARGET_CONTEXT_UNSUPPORTED"

    # Function parameter bounds, local/struct arrays, typedefs, and other
    # declaration-only type forms are deliberately out of scope.  Replacing
    # them changes the textual signature and can confuse downstream tools even
    # when a macro has an equivalent value.
    if _inside_square_brackets(masked_source, start):
        if _is_c_type_or_signature_context(masked_source, start):
            return "TARGET_IN_C_TYPE_OR_SIGNATURE_CONTEXT"
        return "TARGET_CONTEXT_UNSUPPORTED"

    previous = start - 1
    while previous >= line_start and masked_source[previous].isspace():
        previous -= 1
    following = end
    while following < line_end and masked_source[following].isspace():
        following += 1
    if previous >= line_start and masked_source[previous] == ":":
        if _is_c_type_or_signature_context(masked_source, start):
            return "TARGET_IN_C_TYPE_OR_SIGNATURE_CONTEXT"
        return "TARGET_CONTEXT_UNSUPPORTED"
    if following < line_end and masked_source[following] == ":":
        if _is_c_type_or_signature_context(masked_source, start):
            return "TARGET_IN_C_TYPE_OR_SIGNATURE_CONTEXT"
        return "TARGET_CONTEXT_UNSUPPORTED"

    # A standalone token in executable C code is the only accepted context.
    return ""


def _ignored_c_context_for_literal(
    source_code: str,
    literal_text: str,
    source_line: Optional[int] = None,
) -> str:
    """Find a matching value inside a char/string/comment for diagnostics."""

    if not literal_text:
        return ""
    index = 0
    state = "code"
    token_start = 0
    while index < len(source_code):
        char = source_code[index]
        nxt = source_code[index + 1] if index + 1 < len(source_code) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state = "line_comment"
                token_start = index + 2
                index += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                token_start = index + 2
                index += 2
                continue
            if char == '"':
                state = "string"
                token_start = index + 1
                index += 1
                continue
            if char == "'":
                state = "char"
                token_start = index + 1
                index += 1
                continue
            index += 1
            continue

        closing = (
            (state == "string" and char == '"')
            or (state == "char" and char == "'")
        )
        if closing:
            token = source_code[token_start:index]
            token_line = _line_for_c_index(source_code, token_start)
            if (source_line is None or token_line == source_line) and re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(literal_text)}(?![A-Za-z0-9_])",
                token,
            ):
                return (
                    "TARGET_IN_CHAR_LITERAL"
                    if state == "char"
                    else "TARGET_IN_STRING_LITERAL"
                )
            state = "code"
            index += 1
            continue
        if state in {"string", "char"} and char == "\\" and nxt:
            index += 2
            continue
        if state == "line_comment" and char == "\n":
            token = source_code[token_start:index]
            token_line = _line_for_c_index(source_code, token_start)
            if (source_line is None or token_line == source_line) and re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(literal_text)}(?![A-Za-z0-9_])",
                token,
            ):
                return "TARGET_IN_COMMENT"
            state = "code"
        elif state == "block_comment" and char == "*" and nxt == "/":
            token = source_code[token_start:index]
            start_line = _line_for_c_index(source_code, token_start)
            end_line = _line_for_c_index(source_code, index)
            if (source_line is None or start_line <= source_line <= end_line) and re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(literal_text)}(?![A-Za-z0-9_])",
                token,
            ):
                return "TARGET_IN_COMMENT"
            state = "code"
            index += 1
        index += 1
    if state == "line_comment":
        token = source_code[token_start:]
        token_line = _line_for_c_index(source_code, token_start)
        if (source_line is None or token_line == source_line) and re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(literal_text)}(?![A-Za-z0-9_])",
            token,
        ):
            return "TARGET_IN_COMMENT"
    elif state == "block_comment":
        token = source_code[token_start:]
        start_line = _line_for_c_index(source_code, token_start)
        end_line = _line_for_c_index(source_code, len(source_code))
        if (source_line is None or start_line <= source_line <= end_line) and re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(literal_text)}(?![A-Za-z0-9_])",
            token,
        ):
            return "TARGET_IN_COMMENT"
    return ""


def _find_c_numeric_literals(
    source_code: str,
    literal_value: Any,
    source_line: Optional[int] = None,
) -> tuple[list[tuple[int, int]], str]:
    """Return safe numeric token spans and a conservative rejection reason."""

    if isinstance(literal_value, bool) or not isinstance(literal_value, (int, float)):
        return [], "TARGET_NOT_C_NUMERIC_LITERAL"

    literal_text = _to_c_literal(literal_value)
    magnitude = literal_text
    sign = ""
    if magnitude[:1] in {"+", "-"}:
        sign, magnitude = magnitude[0], magnitude[1:]
    masked = _mask_c_non_code(source_code)
    spans: list[tuple[int, int]] = []
    rejection_reason = ""

    for match in _C_NUMERIC_TOKEN_RE.finditer(masked):
        start, end = match.span()
        token = source_code[start:end]
        if token != magnitude:
            continue
        if sign:
            sign_index = start - 1
            while sign_index >= 0 and masked[sign_index].isspace():
                sign_index -= 1
            if sign_index < 0 or masked[sign_index] != sign:
                continue
            start = sign_index
        before = masked[start - 1] if start > 0 else ""
        after = masked[end] if end < len(masked) else ""
        if before.isalnum() or before == "_" or after.isalnum() or after == "_":
            continue
        line = _line_for_c_index(source_code, start)
        if source_line is not None and line != source_line:
            continue
        context = _c_numeric_context(source_code, masked, start, end)
        if context:
            rejection_reason = rejection_reason or context
            continue
        spans.append((start, end))

    if spans:
        return spans, ""
    ignored = _ignored_c_context_for_literal(source_code, magnitude, source_line)
    return [], ignored or rejection_reason or "TARGET_NOT_C_NUMERIC_LITERAL"


def analyze_extract_constant_target(
    source_code: str,
    literal_value: Any,
    source_line: Optional[int] = None,
) -> dict[str, Any]:
    """Expose safe C literal classification for engine/report diagnostics."""

    spans, reason = _find_c_numeric_literals(source_code, literal_value, source_line)
    return {
        "eligible": bool(spans),
        "candidate_count": len(spans),
        "reason": reason if not spans else "C_NUMERIC_EXPRESSION",
    }


def _replace_literal(
    source_code: str,
    literal_value: Any,
    constant_name: str,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    if isinstance(literal_value, (int, float)) and not isinstance(literal_value, bool):
        spans, _ = _find_c_numeric_literals(source_code, literal_value, source_line)
        if not spans:
            return source_code, 0
        # Apply from right to left so token offsets remain stable.
        for start, end in reversed(spans):
            source_code = source_code[:start] + constant_name + source_code[end:]
        return source_code, len(spans)

    pattern = _literal_pattern(literal_value)
    lines = source_code.splitlines(keepends=True)
    replacements = 0

    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue
        if _should_skip_replacement(line, constant_name):
            continue
        updated, count = pattern.subn(constant_name, line)
        if count:
            replacements += count
            lines[index - 1] = updated

    return "".join(lines), replacements


def _insert_define(source_code: str, constant_name: str, literal_value: Any) -> str:
    if constant_name in _existing_define_names(source_code):
        return source_code

    define_line = f"#define {constant_name} {_to_c_literal(literal_value)}\n"
    match = _INCLUDE_RE.search(source_code)
    if match:
        insert_at = match.end()
        tail = source_code[insert_at:]
        next_line = tail.find("\n")
        if next_line >= 0:
            insert_at += next_line + 1
        return source_code[:insert_at] + define_line + source_code[insert_at:]

    return define_line + "\n" + source_code.lstrip()


def _insert_static_string_constant(
    source_code: str,
    constant_name: str,
    expression: str,
) -> str:
    if re.search(rf"\b{re.escape(constant_name)}\b\s*\[?\]?\s*=", source_code):
        return source_code

    declaration = f"static const char {constant_name}[] = {expression.strip()};\n"
    match = _INCLUDE_RE.search(source_code)
    if match:
        insert_at = match.end()
        tail = source_code[insert_at:]
        next_line = tail.find("\n")
        if next_line >= 0:
            insert_at += next_line + 1
        return source_code[:insert_at] + declaration + source_code[insert_at:]

    return declaration + "\n" + source_code.lstrip()


def _source_line_in_span(source_code: str, start_idx: int, end_idx: int, source_line: Optional[int]) -> bool:
    if source_line is None:
        return True
    start_line = source_code[:start_idx].count("\n") + 1
    end_line = source_code[:end_idx].count("\n") + 1
    return start_line <= source_line <= end_line


def _replace_identifier_outside_literals(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    result: list[str] = []
    count = 0
    i = 0
    state = "code"
    old_len = len(old_name)

    while i < len(source_code):
        char = source_code[i]
        nxt = source_code[i + 1] if i + 1 < len(source_code) else ""

        if state == "code":
            if char == "/" and nxt == "/":
                state = "line_comment"
                result.extend([char, nxt])
                i += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                result.extend([char, nxt])
                i += 2
                continue
            if char == '"':
                state = "string"
                result.append(char)
                i += 1
                continue
            if char == "'":
                state = "char"
                result.append(char)
                i += 1
                continue
            before = source_code[i - 1] if i > 0 else ""
            after = source_code[i + old_len] if i + old_len < len(source_code) else ""
            if (
                source_code.startswith(old_name, i)
                and not (before.isalnum() or before == "_")
                and not (after.isalnum() or after == "_")
            ):
                result.append(new_name)
                count += 1
                i += old_len
                continue
            result.append(char)
            i += 1
            continue

        result.append(char)
        if state == "line_comment" and char == "\n":
            state = "code"
        elif state == "block_comment" and char == "*" and nxt == "/":
            result.append(nxt)
            i += 1
            state = "code"
        elif state in {"string", "char"}:
            if char == "\\" and nxt:
                result.append(nxt)
                i += 1
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                state = "code"
        i += 1

    return "".join(result), count


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    return _replace_identifier_outside_literals(source_code, old_name, new_name)


def apply_extract_constant(
    source_code: str,
    literal_value: Any,
    constant_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    normalized_name = _normalize_constant_name(constant_name, literal_value)
    preferred_name = _unique_constant_name(source_code, normalized_name)
    transformed, replacements = _replace_literal(
        source_code,
        literal_value,
        preferred_name,
        source_line,
    )
    if replacements == 0 and source_line is not None:
        # Do not fall back to another occurrence when the requested line
        # contains an unsafe character/string/comment/context target.  The
        # legacy fallback is retained only when the line simply missed the
        # literal entirely.
        context = analyze_extract_constant_target(
            source_code,
            literal_value,
            source_line,
        )
        if context["reason"] == "TARGET_NOT_C_NUMERIC_LITERAL":
            transformed, replacements = _replace_literal(
                source_code,
                literal_value,
                preferred_name,
                None,
            )
    if replacements > 0:
        transformed = _insert_define(transformed, preferred_name, literal_value)
    return transformed, replacements


def apply_replace_literal(
    source_code: str,
    old_literal: Any,
    new_literal: Any,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    old_text = _to_c_literal(old_literal)
    new_text = _to_c_literal(new_literal)
    lines = source_code.splitlines(keepends=True)
    replacements = 0
    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue
        updated, count = line.replace(old_text, new_text), line.count(old_text)
        if count:
            lines[index - 1] = updated
            replacements += count
    return "".join(lines), replacements


def apply_normalize_multiline_statement(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    constant_name: str = "SCTVA_EXTRACTED_VALUE",
    normalization: str = "",
) -> Tuple[str, int]:
    del normalization
    preferred_name = _unique_constant_name(source_code, _sanitize_identifier(constant_name or "SCTVA_EXTRACTED_VALUE"))
    string_literal = r'"(?:\\.|[^"\\])*"'
    pattern = re.compile(
        rf"""
        (?P<prefix>\b(?:const\s+)?char\s*(?:\*\s*)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)
        (?P<expr>\s*{string_literal}(?:\s*{string_literal})+\s*)
        (?P<suffix>;)
        """,
        re.VERBOSE | re.DOTALL,
    )

    for match in pattern.finditer(source_code):
        expr = match.group("expr")
        if "\n" not in expr:
            continue
        if not _source_line_in_span(source_code, match.start(), match.end(), source_line):
            continue
        transformed = (
            source_code[: match.start("expr")]
            + preferred_name
            + source_code[match.end("expr") :]
        )
        transformed = _insert_static_string_constant(transformed, preferred_name, expr)
        return transformed, 1

    return source_code, 0


def _mask_c_non_code(source: str) -> str:
    """Keep C token positions while masking comments and literals."""

    masked = list(source)
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 1
                state = "code"
            elif char != "\n":
                masked[index] = " "
        elif state in {"string", "char"}:
            if char == "\\":
                masked[index] = " "
                if index + 1 < len(source):
                    masked[index + 1] = " "
                    index += 1
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        elif char == "/" and nxt == "/":
            masked[index] = masked[index + 1] = " "
            index += 1
            state = "line_comment"
        elif char == "/" and nxt == "*":
            masked[index] = masked[index + 1] = " "
            index += 1
            state = "block_comment"
        elif char == '"':
            masked[index] = " "
            state = "string"
        elif char == "'":
            masked[index] = " "
            state = "char"
        index += 1
    return "".join(masked)


def _find_matching_brace(source: str, start_idx: int) -> Optional[int]:
    source = _mask_c_non_code(source)
    depth = 0
    for idx in range(start_idx, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _line_for_c_index(source: str, index: int) -> int:
    return source.count("\n", 0, max(0, index)) + 1


def _c_brace_depth(source: str, index: int) -> int:
    masked = _mask_c_non_code(source[:index])
    return masked.count("{") - masked.count("}")


def _remove_c_span(source: str, start: int, end: int) -> Tuple[str, int]:
    if start < 0 or end <= start or end > len(source):
        return source, 0
    # Remove a following newline only when the selected statement owns it.
    if end < len(source) and source[end] == "\r":
        end += 1
    if end < len(source) and source[end] == "\n":
        end += 1
    return source[:start] + source[end:], 1


def _is_literal_c_initializer(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned:
        return True
    if re.search(r"[A-Za-z_]", cleaned) and not re.fullmatch(
        r"(?:NULL|true|false|sizeof\s*\([^()]*\)|[uUlLfFxXa-fA-F0-9.+'\"\\\s(){}\[\],|&^~!<>*/%?:-])+",
        cleaned,
    ):
        return False
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", cleaned) and not cleaned.startswith("sizeof"):
        return False
    return True


def _remove_proven_unused_c_declaration(source_code: str, source_line: int) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    if source_line <= 0 or source_line > len(lines):
        return source_code, 0

    line = lines[source_line - 1]
    match = re.match(
        r"^\s*(?:(?:const|volatile|unsigned|signed|short|long|static|register)\s+)*"
        r"(?:struct\s+\w+|enum\s+\w+|union\s+\w+|[A-Za-z_]\w*)"
        r"(?:\s*\*+|\s+)([A-Za-z_]\w*)\s*(?:=\s*(.*?))?;\s*(?://.*)?$",
        line.rstrip("\r\n"),
    )
    if not match:
        return source_code, 0

    variable_name = match.group(1)
    initializer = match.group(2) or ""
    if not _is_literal_c_initializer(initializer):
        return source_code, 0
    if len(re.findall(rf"\b{re.escape(variable_name)}\b", source_code)) != 1:
        return source_code, 0

    return "".join(lines[: source_line - 1] + lines[source_line:]), 1


def _remove_proven_c_false_branch(source_code: str, source_line: int) -> Tuple[str, int]:
    masked = _mask_c_non_code(source_code)
    pattern = re.compile(r"\bif\s*\(\s*0\s*\)\s*\{")
    for match in pattern.finditer(masked):
        brace_index = masked.find("{", match.start(), match.end())
        end_brace = _find_matching_brace(masked, brace_index)
        if end_brace is None:
            continue
        start_line = _line_for_c_index(source_code, match.start())
        end_line = _line_for_c_index(source_code, end_brace)
        if not start_line <= source_line <= end_line:
            continue
        # An else branch has observable behavior and needs a CST move, so retain
        # it for manual review rather than deleting a valid execution path.
        if re.match(r"\s*else\b", masked[end_brace + 1 :]):
            continue
        statement_start = source_code.rfind("\n", 0, match.start()) + 1
        return _remove_c_span(source_code, statement_start, end_brace + 1)
    return source_code, 0


def _remove_proven_c_unreachable_statement(source_code: str, source_line: int) -> Tuple[str, int]:
    masked = _mask_c_non_code(source_code)
    terminators = re.compile(r"\b(?:return|break|continue|goto\s+[A-Za-z_][A-Za-z0-9_]*)\b")
    for match in terminators.finditer(masked):
        terminator_end = masked.find(";", match.end())
        if terminator_end == -1:
            continue
        next_start = terminator_end + 1
        while next_start < len(masked) and masked[next_start].isspace():
            next_start += 1
        if next_start >= len(masked) or masked[next_start] in "}#{":
            continue
        depth = _c_brace_depth(source_code, match.start())
        if _c_brace_depth(source_code, next_start) != depth:
            continue
        statement_end = masked.find(";", next_start)
        next_brace = min(
            [index for index in (masked.find("{", next_start), masked.find("}", next_start)) if index != -1],
            default=-1,
        )
        if statement_end == -1 or (next_brace != -1 and next_brace < statement_end):
            continue
        start_line = _line_for_c_index(source_code, next_start)
        end_line = _line_for_c_index(source_code, statement_end)
        if start_line <= source_line <= end_line:
            return _remove_c_span(source_code, next_start, statement_end + 1)
    return source_code, 0


_C_FUNCTION_DEFINITION_RE = re.compile(
    r"(?ms)^[ \t]*(?P<prefix>(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
)

_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CParameter:
    name: str
    type_name: str
    declaration: str


def _c_function_definitions(source_code: str) -> list[dict[str, Any]]:
    """Return top-level C function definitions with balanced source spans."""

    masked = _mask_c_non_code(source_code)
    definitions: list[dict[str, Any]] = []
    for match in _C_FUNCTION_DEFINITION_RE.finditer(masked):
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "case", "default"}:
            continue
        if _c_brace_depth(masked, match.start()) != 0:
            continue
        brace_index = masked.rfind("{", match.start(), match.end())
        method_end = _find_matching_brace(masked, brace_index)
        if method_end is None:
            continue
        definitions.append({
            "name": name,
            "start": match.start(),
            "name_start": match.start("name"),
            "name_end": match.end("name"),
            "end": method_end + 1,
            "start_line": _line_for_c_index(source_code, match.start()),
            "end_line": _line_for_c_index(source_code, method_end),
            "signature": masked[match.start() : match.end()],
            "static": bool(re.search(r"\bstatic\b", match.group("prefix"))),
        })
    return definitions


def c_function_definition_count(source_code: str, method_name: str) -> int:
    """Count concrete definitions for a named C function in one source file."""

    target = str(method_name or "").strip()
    if not target:
        return 0
    return sum(item["name"] == target for item in _c_function_definitions(source_code))


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
) -> tuple[str, int, dict[str, Any]]:
    """Introduce a C typedef struct for a simple top-level function signature."""

    del source_class
    metadata: dict[str, Any] = {
        "refactoring": "Introduce Parameter Object",
        "language": "c",
        "method": method,
        "parameter_object_name": parameter_object_name,
        "parameter_name": parameter_name,
        "source_file": source_file or current_file_name,
        "plan_compliance": "FAIL",
    }
    if source_resolution_error:
        return _c_parameter_object_review(source_code, source_resolution_error, metadata)
    if not _C_IDENTIFIER_RE.fullmatch(method or ""):
        return _c_parameter_object_review(source_code, "INVALID_FUNCTION_TARGET", metadata)
    if not _C_IDENTIFIER_RE.fullmatch(parameter_object_name or ""):
        return _c_parameter_object_review(source_code, "INVALID_PARAMETER_OBJECT_NAME", metadata)
    if not _C_IDENTIFIER_RE.fullmatch(parameter_name or ""):
        return _c_parameter_object_review(source_code, "INVALID_PARAMETER_NAME", metadata)
    if source_file and current_file_name and not _c_paths_match(source_file, current_file_name):
        return _c_parameter_object_review(source_code, "SOURCE_FILE_MISMATCH", metadata)
    if re.search(rf"\b(?:typedef\s+)?struct\s+{re.escape(parameter_object_name)}\b", _mask_c_non_code(source_code)):
        return _c_parameter_object_review(source_code, "PARAMETER_OBJECT_ALREADY_EXISTS", metadata)

    from .c_extract_class import _parse_c_module
    from .c_extract_method import _verify_c_compilation

    module = _parse_c_module(source_code)
    targets = module.functions_by_name.get(method, [])
    if len(targets) != 1:
        reason = "FUNCTION_TARGET_NOT_FOUND" if not targets else "AMBIGUOUS_FUNCTION_TARGET"
        return _c_parameter_object_review(source_code, reason, metadata)
    function = targets[0]
    if re.search(r"(?m)^\s*#", function.body):
        return _c_parameter_object_review(source_code, "PREPROCESSOR_DIRECTIVE_INSIDE_FUNCTION", metadata)
    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(method)}\s*\(", _mask_c_non_code(function.body)):
        return _c_parameter_object_review(source_code, "RECURSIVE_FUNCTION_NOT_SUPPORTED", metadata)

    parsed_or_error = _parse_c_parameter_object_parameters(function.params_raw)
    if isinstance(parsed_or_error, str):
        return _c_parameter_object_review(source_code, parsed_or_error, metadata)
    parameters = parsed_or_error
    if len(parameters) < 2:
        return _c_parameter_object_review(source_code, "PARAMETER_COUNT_NOT_REDUCIBLE", metadata)
    if parameter_name in {item.name for item in parameters}:
        return _c_parameter_object_review(source_code, "PARAMETER_NAME_COLLISION", metadata)
    shadowed = _c_shadowed_parameter_names(function.body, {item.name for item in parameters})
    if shadowed:
        metadata["shadowed_parameters"] = sorted(shadowed)
        return _c_parameter_object_review(source_code, "NESTED_SCOPE_PARAMETER_SHADOWING", metadata)

    external_callers = _c_external_function_references(
        method,
        source_code,
        project_source_files=project_source_files,
        current_file_name=current_file_name or source_file,
    )
    if external_callers:
        metadata["unresolved_external_callers"] = external_callers
        return _c_parameter_object_review(source_code, "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT", metadata)

    signature_span = _c_function_parameter_span(source_code, function)
    if signature_span is None:
        return _c_parameter_object_review(source_code, "FUNCTION_SIGNATURE_PARSE_FAILED", metadata)

    call_edits_or_error = _c_parameter_object_call_edits(
        source_code,
        method=method,
        object_name=parameter_object_name,
        parameters=parameters,
        definition_name_span=(function.start + function.header.rfind(method), function.start + function.header.rfind(method) + len(method)),
    )
    if isinstance(call_edits_or_error, str):
        return _c_parameter_object_review(source_code, call_edits_or_error, metadata)
    call_edits, call_sites_updated = call_edits_or_error

    body_start = function.open_brace + 1
    body_end = function.end - 1
    body_edits: list[tuple[int, int, str]] = []
    body_masked = _mask_c_non_code(source_code[body_start:body_end])
    for parameter in parameters:
        pattern = re.compile(rf"(?<![A-Za-z0-9_.>]){re.escape(parameter.name)}\b")
        body_edits.extend(
            (body_start + match.start(), body_start + match.end(), f"{parameter_name}.{parameter.name}")
            for match in pattern.finditer(body_masked)
        )

    struct_source = _c_parameter_object_typedef(parameter_object_name, parameters)
    insert_at = _c_parameter_object_insert_index(source_code, function.start)
    edits = [
        (insert_at, insert_at, struct_source),
        (signature_span[0], signature_span[1], f"{parameter_object_name} {parameter_name}"),
        *body_edits,
        *call_edits,
    ]
    transformed = _apply_c_edits(source_code, edits)

    validation = validate_c_parameter_object(
        source_code,
        transformed,
        method=method,
        object_name=parameter_object_name,
        parameter_name=parameter_name,
    )
    if not validation.get("passed"):
        metadata["validation_details"] = validation
        return _c_parameter_object_review(source_code, "STRUCTURAL_POSTCONDITION_FAILED", metadata)

    compile_status, compile_msg = _verify_c_compilation(transformed)
    if compile_status == "FAIL":
        metadata["compiler_validation"] = compile_msg
        return _c_parameter_object_review(source_code, f"COMPILATION_FAILED: {compile_msg}", metadata)

    internal_validation = {
        "parameter_object": "PASS",
        "signature_reduction": "PASS",
        "body_access": "PASS",
        "call_sites": "PASS",
    }
    metadata.update({
        "status": "success",
        "reason": "parameter_object_introduced",
        "plan_compliance": "PASS",
        "parameters_moved": [item.name for item in parameters],
        "parameter_types": {item.name: item.type_name for item in parameters},
        "before_parameter_count": len(parameters),
        "after_parameter_count": 1,
        "call_sites_updated": call_sites_updated,
        "validation": internal_validation,
        "validation_details": validation,
        "compiler_validation": compile_msg,
        "behavioral_safety": "PASSED_COMPILER_AND_STRUCTURAL_VALIDATION" if compile_status == "PASS" else "STRUCTURAL_VALIDATION_ONLY",
    })
    return transformed, 1, metadata


def validate_c_parameter_object(
    original: str,
    transformed: str,
    *,
    method: str,
    object_name: str,
    parameter_name: str,
) -> dict[str, Any]:
    from .c_extract_class import _parse_c_module

    before_module = _parse_c_module(original)
    after_module = _parse_c_module(transformed)
    before_targets = before_module.functions_by_name.get(method, [])
    after_targets = after_module.functions_by_name.get(method, [])
    if len(before_targets) != 1 or len(after_targets) != 1:
        return {"passed": False, "reason": "target_missing_or_ambiguous"}

    before = before_targets[0]
    after = after_targets[0]
    parsed_or_error = _parse_c_parameter_object_parameters(before.params_raw)
    if isinstance(parsed_or_error, str):
        return {"passed": False, "reason": parsed_or_error}
    parameters = parsed_or_error
    fields = _c_parameter_object_fields(transformed, object_name)
    used_before = {
        item.name
        for item in parameters
        if re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(item.name)}\b", _mask_c_non_code(before.body))
    }
    accessed_after = {
        match.group(1)
        for match in re.finditer(
            rf"\b{re.escape(parameter_name)}\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b",
            _mask_c_non_code(after.body),
        )
    }
    after_params = _parse_c_parameter_object_parameters(after.params_raw)
    after_names = [] if isinstance(after_params, str) else [item.name for item in after_params]
    after_types = [] if isinstance(after_params, str) else [item.type_name for item in after_params]
    checks = {
        "parameter_object_created": bool(fields),
        "fields_preserved": [item.name for item in parameters] == list(fields),
        "field_types_preserved": [item.type_name for item in parameters] == [fields.get(item.name) for item in parameters],
        "parameter_count_reduced": len(parameters) > len(after_names),
        "single_parameter_object_argument": after_names == [parameter_name] and after_types == [object_name],
        "body_access_migrated": used_before <= accessed_after,
        "call_sites_updated": _c_direct_old_arity_call_count(transformed, method, len(parameters)) == 0,
    }
    return {
        "passed": all(checks.values()),
        "language": "c",
        "method": method,
        "before_parameter_count": len(parameters),
        "after_parameter_count": len(after_names),
        "fields": list(fields),
        "checks": checks,
    }


def _c_parameter_object_review(
    source_code: str,
    reason: str,
    metadata: dict[str, Any],
) -> tuple[str, int, dict[str, Any]]:
    metadata.update({"status": "review_required", "reason": reason})
    return source_code, 0, metadata


def _parse_c_parameter_object_parameters(params_raw: str) -> list[CParameter] | str:
    cleaned = str(params_raw or "").strip()
    if not cleaned or cleaned == "void":
        return []
    parameters: list[CParameter] = []
    for raw in _split_call_args(cleaned):
        declaration = raw.strip()
        if not declaration:
            continue
        if declaration == "...":
            return "VARARGS_NOT_SUPPORTED"
        if "..." in declaration:
            return "VARARGS_NOT_SUPPORTED"
        if re.search(r"\(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)", declaration):
            return "FUNCTION_POINTER_PARAMETER_NOT_SUPPORTED"
        array_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\[[^]]*\]\s*$", declaration)
        if array_match:
            name = array_match.group(1)
            base_type = declaration[:array_match.start()].strip()
            if not base_type or name in {"void", "const", "volatile", "restrict"}:
                return "PARAMETER_PARSE_FAILED"
            type_name = _normalize_c_type_text(f"{base_type}*")
            parameters.append(CParameter(name=name, type_name=type_name, declaration=declaration))
            continue
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", declaration)
        if not match:
            return "PARAMETER_PARSE_FAILED"
        name = match.group(1)
        type_name = declaration[:match.start()].strip()
        if not type_name or name in {"void", "const", "volatile", "restrict"}:
            return "PARAMETER_PARSE_FAILED"
        parameters.append(CParameter(name=name, type_name=_normalize_c_type_text(type_name), declaration=declaration))
    return parameters


def _normalize_c_type_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("*", " * ")).replace(" *", "*").strip()


def _c_function_parameter_span(source_code: str, function: Any) -> tuple[int, int] | None:
    header = source_code[function.start:function.open_brace]
    masked = _mask_c_non_code(header)
    name_matches = list(re.finditer(rf"\b{re.escape(function.name)}\s*\(", masked))
    if not name_matches:
        return None
    open_index = masked.find("(", name_matches[-1].start())
    close_index = _find_matching_delimiter(masked, open_index, "(", ")")
    if close_index is None:
        return None
    return function.start + open_index + 1, function.start + close_index


def _find_matching_delimiter(source: str, start_idx: int, open_char: str, close_char: str) -> Optional[int]:
    depth = 0
    for idx in range(start_idx, len(source)):
        char = source[idx]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return idx
    return None


def _c_parameter_object_call_edits(
    source_code: str,
    *,
    method: str,
    object_name: str,
    parameters: Sequence[CParameter],
    definition_name_span: tuple[int, int],
) -> tuple[list[tuple[int, int, str]], int] | str:
    masked = _mask_c_non_code(source_code)
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(method)}\s*\(")
    edits: list[tuple[int, int, str]] = []
    for match in pattern.finditer(masked):
        if (match.start(), match.start() + len(method)) == definition_name_span:
            continue
        if _c_brace_depth(masked, match.start()) <= 0:
            continue
        open_index = masked.find("(", match.start(), match.end())
        close_index = _find_matching_delimiter(masked, open_index, "(", ")")
        if close_index is None:
            return "CALL_SITE_PARSE_FAILED"
        args = _split_call_args(source_code[open_index + 1:close_index])
        if len(args) != len(parameters):
            return "CALL_SITE_ARITY_MISMATCH"
        initializer = ", ".join(
            f".{parameter.name} = {argument.strip()}"
            for parameter, argument in zip(parameters, args)
        )
        edits.append((match.start(), close_index + 1, f"{method}(({object_name}){{ {initializer} }})"))
    return edits, len(edits)


def _c_parameter_object_typedef(object_name: str, parameters: Sequence[CParameter]) -> str:
    fields = "\n".join(f"    {item.type_name} {item.name};" for item in parameters)
    return f"typedef struct {{\n{fields}\n}} {object_name};\n\n"


def _c_parameter_object_insert_index(source_code: str, function_start: int) -> int:
    prefix = source_code[:function_start]
    if prefix.endswith("\n\n") or not prefix.strip():
        return function_start
    line_start = source_code.rfind("\n", 0, function_start) + 1
    return line_start


def _apply_c_edits(source: str, edits: Sequence[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


_C_NON_TYPE_KEYWORDS = {
    "return", "case", "goto", "sizeof", "if", "while", "for", "switch",
    "else", "default", "break", "continue", "typedef", "do",
}


def _c_shadowed_parameter_names(body: str, parameter_names: set[str]) -> set[str]:
    masked = _mask_c_non_code(body)
    shadowed: set[str] = set()
    for name in parameter_names:
        matches = re.finditer(
            rf"\b([A-Za-z_][A-Za-z0-9_]*)"
            rf"(?:\s*\*+\s*|\s+){re.escape(name)}\b\s*([=;,\[)])",
            masked,
        )
        for m in matches:
            type_token = m.group(1)
            if type_token not in _C_NON_TYPE_KEYWORDS:
                shadowed.add(name)
    return shadowed


def _c_external_function_references(
    method: str,
    source_code: str,
    *,
    project_source_files: Sequence[Any] | None,
    current_file_name: str,
) -> list[str]:
    external: list[str] = []
    for item in _project_c_sources(
        source_code,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    ):
        if item.get("is_current"):
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(method)}\s*\(", _mask_c_non_code(item["source_code"])):
            external.append(str(item.get("file_name") or "unknown"))
    return external


def _c_direct_old_arity_call_count(source_code: str, method: str, arity: int) -> int:
    masked = _mask_c_non_code(source_code)
    count = 0
    for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(method)}\s*\(", masked):
        if _c_brace_depth(masked, match.start()) <= 0:
            continue
        open_index = masked.find("(", match.start(), match.end())
        close_index = _find_matching_delimiter(masked, open_index, "(", ")")
        if close_index is None:
            continue
        args = _split_call_args(source_code[open_index + 1:close_index])
        if len(args) == arity:
            count += 1
    return count


def _c_parameter_object_fields(source_code: str, object_name: str) -> dict[str, str]:
    match = re.search(
        rf"typedef\s+struct\s*\{{(?P<body>.*?)\}}\s*{re.escape(object_name)}\s*;",
        _mask_c_non_code(source_code),
        flags=re.DOTALL,
    )
    if not match:
        return {}
    fields: dict[str, str] = {}
    for raw in match.group("body").split(";"):
        declaration = raw.strip()
        if not declaration:
            continue
        parsed = _parse_c_parameter_object_parameters(declaration)
        if isinstance(parsed, str) or len(parsed) != 1:
            return {}
        fields[parsed[0].name] = parsed[0].type_name
    return fields


def _c_paths_match(expected: str, actual: str) -> bool:
    expected_path = str(expected or "").replace("\\", "/").lower()
    actual_path = str(actual or "").replace("\\", "/").lower()
    return bool(
        expected_path == actual_path
        or expected_path.endswith(f"/{actual_path}")
        or actual_path.endswith(f"/{expected_path}")
    )


def _project_c_sources(
    source_code: str,
    *,
    project_source_files: Sequence[Any] | None,
    current_file_name: str,
) -> list[dict[str, Any]]:
    """Normalize the repository snapshot used for conservative reference checks."""

    current_path = str(current_file_name or "").replace("\\", "/").lower()
    normalized: list[dict[str, Any]] = []
    current_included = False
    for item in project_source_files or []:
        if isinstance(item, dict):
            file_name = str(
                item.get("file_name") or item.get("name") or item.get("path") or ""
            )
            item_source = str(item.get("source_code") or item.get("code") or "")
            language = str(item.get("language") or "").strip().lower()
        else:
            file_name = str(
                getattr(item, "file_name", "")
                or getattr(item, "name", "")
                or getattr(item, "path", "")
            )
            item_source = str(
                getattr(item, "source_code", "") or getattr(item, "code", "")
            )
            language = str(getattr(item, "language", "") or "").strip().lower()

        normalized_name = file_name.replace("\\", "/")
        relevant = language == "c" or normalized_name.lower().endswith((".c", ".h"))
        if not relevant or not item_source:
            continue
        normalized_path = normalized_name.lower()
        same_current_file = bool(
            current_path
            and (
                normalized_path == current_path
                or normalized_path.endswith(f"/{current_path}")
                or current_path.endswith(f"/{normalized_path}")
            )
        )
        if same_current_file:
            item_source = source_code
            current_included = True
        normalized.append({
            "file_name": normalized_name,
            "source_code": item_source,
            "is_current": same_current_file,
        })

    if not current_included:
        normalized.append({
            "file_name": str(current_file_name or "<current>"),
            "source_code": source_code,
            "is_current": True,
        })
    return normalized


def _identifier_occurrences(source_code: str, identifier: str) -> list[tuple[int, int]]:
    masked = _mask_c_non_code(source_code)
    return [
        (match.start(), match.end())
        for match in re.finditer(rf"\b{re.escape(identifier)}\b", masked)
    ]


def _c_string_mentions_identifier(source_code: str, identifier: str) -> bool:
    """Treat string-based lookup/registration as a possible dynamic reference."""

    string_literal = re.compile(r'"(?:\\.|[^"\\])*"')
    return any(
        re.search(rf"\b{re.escape(identifier)}\b", match.group(0))
        for match in string_literal.finditer(source_code)
    )


def _c_function_reference_metrics(
    target: str,
    candidate: dict[str, Any],
    repository_sources: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Count C references by meaning before allowing a function deletion."""

    metrics = {
        "definition_count": 0,
        "call_count": 0,
        "address_taken": 0,
        "function_pointer_usage": 0,
        "macro_reference": 0,
        "cross_file_reference": 0,
        "remaining_references": 0,
    }
    for item in repository_sources:
        code = str(item.get("source_code") or "")
        definitions = [
            definition
            for definition in _c_function_definitions(code)
            if definition["name"] == target
        ]
        metrics["definition_count"] += len(definitions)
        occurrence_spans = _identifier_occurrences(code, target)
        definition_spans = {
            (int(definition["name_start"]), int(definition["name_end"]))
            for definition in definitions
        }
        occurrence_spans = [span for span in occurrence_spans if span not in definition_spans]
        if item.get("is_current"):
            occurrence_spans = [
                span for span in occurrence_spans
                if span != (candidate["name_start"], candidate["name_end"])
            ]

        masked = _mask_c_non_code(code)
        calls = [
            span for span in occurrence_spans
            if re.match(r"\s*\(", masked[span[1]:])
        ]
        addresses = [
            span for span in occurrence_spans
            if re.search(r"&\s*$", masked[:span[0]])
        ]
        macro_refs = [
            span for span in occurrence_spans
            if masked.rfind("\n", 0, span[0]) + 1 <= span[0]
            and masked[masked.rfind("\n", 0, span[0]) + 1:span[0]].lstrip().startswith("#")
        ]
        non_call_references = [span for span in occurrence_spans if span not in calls]
        non_call_references = [span for span in non_call_references if span not in macro_refs]
        non_call_references = [span for span in non_call_references if span not in addresses]

        metrics["call_count"] += len(calls)
        metrics["address_taken"] += len(addresses)
        metrics["macro_reference"] += len(macro_refs)
        metrics["function_pointer_usage"] += len(non_call_references)
        metrics["remaining_references"] += len(occurrence_spans)
        if not item.get("is_current"):
            metrics["cross_file_reference"] += len(occurrence_spans)
        if _c_string_mentions_identifier(code, target):
            metrics["remaining_references"] += 1
            if not item.get("is_current"):
                metrics["cross_file_reference"] += 1
    return metrics


def resolve_c_dead_code_target(
    source_code: str,
    *,
    target_name: str = "",
    method_name: str = "",
    symbol: str = "",
    function: str = "",
    variable: str = "",
    source_line: Optional[int] = None,
    target_kind: str = "",
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
) -> dict[str, Any]:
    """Resolve one C dead-code target without inferring an unnamed target."""

    requested = next(
        (
            str(value).strip()
            for value in (target_name, method_name, symbol, function, variable)
            if str(value or "").strip()
        ),
        "",
    )
    requested_kind = str(target_kind or "").strip().upper()
    if not method_name and requested_kind in {"VARIABLE", "DECLARATION", "DATA"}:
        variable_target = str(variable or requested).strip()
        return _analyze_c_variable_target(
            source_code,
            variable_target,
            source_line=source_line,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
    if not requested and source_line is None:
        return {
            "status": "review_required",
            "reason": "DEAD_CODE_TARGET_METADATA_MISSING",
            "target": "",
            "target_name": "",
            "target_kind": "",
            "removable": False,
            "candidate_count": 0,
        }
    return analyze_c_dead_code_target(
        source_code,
        requested,
        source_line=source_line,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )


def analyze_c_dead_code_target(
    source_code: str,
    method_name: str = "",
    *,
    source_line: Optional[int] = None,
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
) -> dict[str, Any]:
    """Prove whether one internal C function can be safely removed.

    The proof is deliberately textual and conservative: every C/H file in the
    supplied repository snapshot is scanned, and any identifier occurrence
    outside the target definition blocks removal. This covers ordinary calls,
    function-pointer assignments, preprocessor use, and extern/prototype use.
    """

    requested = str(method_name or "").strip()
    definitions = _c_function_definitions(source_code)
    if requested:
        candidates = [item for item in definitions if item["name"] == requested]
    elif source_line is not None:
        candidates = [
            item for item in definitions
            if item["start_line"] <= source_line <= item["end_line"]
        ]
    else:
        candidates = [item for item in definitions if item["static"] and item["name"] != "main"]

    if len(candidates) != 1:
        return {
            "status": "review_required",
            "reason": "DEAD_CODE_TARGET_NOT_FOUND" if not candidates else "AMBIGUOUS_C_DEAD_CODE_TARGET",
            "target": requested,
            "target_name": requested,
            "target_kind": "FUNCTION" if requested else "",
            "removable": False,
            "candidate_count": len(candidates),
            "definition_count": len(candidates),
        }

    candidate = candidates[0]
    target = str(candidate["name"])
    result = {
        "status": "review_required",
        "target": target,
        "target_name": target,
        "target_kind": "FUNCTION",
        "removable": False,
        "candidate_count": 1,
        "static_internal": bool(candidate["static"]),
        "start_line": int(candidate["start_line"]),
        "end_line": int(candidate["end_line"]),
        "repository_reference_count": 0,
        "definition_count": 1,
        "call_count": 0,
        "address_taken": 0,
        "function_pointer_usage": 0,
        "macro_reference": 0,
        "cross_file_reference": 0,
        "remaining_references": 0,
    }
    if target == "main":
        result.update(status="protected_entry_point", reason="main_is_never_removed")
        return result
    if not candidate["static"]:
        result.update(status="external_linkage", reason="function_is_not_static_internal")
        return result

    repository_sources = _project_c_sources(
        source_code,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )
    reference_metrics = _c_function_reference_metrics(target, candidate, repository_sources)
    result.update(reference_metrics)
    result["repository_reference_count"] = int(reference_metrics["remaining_references"])
    if reference_metrics["remaining_references"]:
        result.update(status="live_reference", reason="repository_reference_found")
        return result

    region = source_code[candidate["start"] : candidate["end"]]
    if re.search(r"(?m)^\s*#", region):
        result.update(status="preprocessor_sensitive", reason="directive_inside_function")
        return result

    result.update(status="proven_dead", removable=True, reason="unused_static_internal_function")
    return result


def _c_variable_declarations(source_code: str, name: str) -> list[dict[str, Any]]:
    """Find simple declaration-only targets suitable for conservative removal."""

    if not _C_IDENTIFIER_RE.fullmatch(str(name or "")):
        return []
    declarations: list[dict[str, Any]] = []
    pattern = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)(?P<storage>(?:(?:static|const|volatile|register|auto)\s+)*)"
        rf"(?P<type>(?:struct\s+[A-Za-z_]\w*|union\s+[A-Za-z_]\w*|enum\s+[A-Za-z_]\w*|[A-Za-z_]\w*))"
        rf"(?P<pointers>\s*\**)\s+{re.escape(name)}\b"
        rf"(?P<array>\s*\[[^\]]*\])?\s*(?:=\s*(?P<initializer>[^;]*))?;[ \t]*(?://.*)?$"
    )
    for match in pattern.finditer(_mask_c_non_code(source_code)):
        line = _line_for_c_index(source_code, match.start())
        declaration = source_code[match.start():match.end()]
        initializer_match = re.search(r"=\s*(.*?)\s*;", declaration, flags=re.DOTALL)
        declarations.append({
            "name": name,
            "start": match.start(),
            "end": match.end(),
            "line": line,
            "declaration": declaration,
            "initializer": str(initializer_match.group(1) if initializer_match else "").strip(),
            "static": bool(match.group("storage")),
        })
    return declarations


def _analyze_c_variable_target(
    source_code: str,
    variable_name: str,
    *,
    source_line: Optional[int],
    project_source_files: Sequence[Any] | None,
    current_file_name: str,
) -> dict[str, Any]:
    candidates = _c_variable_declarations(source_code, variable_name)
    if source_line is not None:
        candidates = [item for item in candidates if item["line"] == source_line]
    base = {
        "target": variable_name,
        "target_name": variable_name,
        "target_kind": "VARIABLE",
        "removable": False,
        "candidate_count": len(candidates),
        "definition_count": len(candidates),
        "call_count": 0,
        "address_taken": 0,
        "function_pointer_usage": 0,
        "macro_reference": 0,
        "cross_file_reference": 0,
        "remaining_references": 0,
    }
    if len(candidates) != 1:
        return {
            **base,
            "status": "review_required",
            "reason": "DEAD_CODE_TARGET_NOT_FOUND" if not candidates else "AMBIGUOUS_C_DEAD_CODE_TARGET",
        }
    candidate = candidates[0]
    base.update({
        "start_line": candidate["line"],
        "end_line": candidate["line"],
        "initializer_side_effect_free": _is_literal_c_initializer(candidate["initializer"]),
    })
    if not base["initializer_side_effect_free"]:
        return {**base, "status": "review_required", "reason": "VARIABLE_INITIALIZER_MAY_HAVE_SIDE_EFFECTS"}

    repository_sources = _project_c_sources(
        source_code,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )
    references = 0
    for item in repository_sources:
        occurrences = _identifier_occurrences(item["source_code"], variable_name)
        if item.get("is_current"):
            occurrences = [
                span for span in occurrences
                if not (span[0] >= candidate["start"] and span[1] <= candidate["end"])
            ]
        references += len(occurrences)
        if _c_string_mentions_identifier(item["source_code"], variable_name):
            references += 1
    base["remaining_references"] = references
    base["repository_reference_count"] = references
    if references:
        return {**base, "status": "live_reference", "reason": "repository_reference_found"}
    base.update(status="proven_dead", removable=True, reason="unused_side_effect_free_variable")
    return base


def proven_unused_static_functions(
    source_code: str,
    *,
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
) -> list[str]:
    """List only repository-proven unused static functions in this file."""

    proven: list[str] = []
    for definition in _c_function_definitions(source_code):
        if not definition["static"] or definition["name"] == "main":
            continue
        analysis = analyze_c_dead_code_target(
            source_code,
            str(definition["name"]),
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
        if analysis.get("removable") is True:
            proven.append(str(definition["name"]))
    return proven


def _remove_proven_unused_c_static_function(
    source_code: str,
    method_name: str,
    *,
    source_line: Optional[int] = None,
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
) -> Tuple[str, int]:
    analysis = analyze_c_dead_code_target(
        source_code,
        method_name,
        source_line=source_line,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )
    if analysis.get("removable") is not True:
        return source_code, 0

    target = str(analysis["target"])
    candidates = [
        item for item in _c_function_definitions(source_code)
        if item["name"] == target
    ]
    if len(candidates) != 1:
        return source_code, 0
    candidate = candidates[0]
    statement_start = source_code.rfind("\n", 0, int(candidate["start"])) + 1
    return _remove_c_span(source_code, statement_start, int(candidate["end"]))


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
    *,
    project_source_files: Sequence[Any] | None = None,
    current_file_name: str = "",
    repository_complete: bool = False,
    variable_name: str = "",
    target_kind: str = "",
) -> Tuple[str, int]:
    del repository_complete
    if variable_name or str(target_kind or "").upper() == "VARIABLE":
        target_variable = variable_name or method_name
        analysis = _analyze_c_variable_target(
            source_code,
            target_variable,
            source_line=source_line,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
        if analysis.get("removable") is not True:
            return source_code, 0
        declaration = _c_variable_declarations(source_code, target_variable)
        if source_line is not None:
            declaration = [item for item in declaration if item["line"] == source_line]
        if len(declaration) != 1:
            return source_code, 0
        return _remove_c_span(source_code, int(declaration[0]["start"]), int(declaration[0]["end"]))

    if not method_name and source_line is not None:
        function_analysis = analyze_c_dead_code_target(
            source_code,
            source_line=source_line,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
        if function_analysis.get("removable") is True:
            return _remove_proven_unused_c_static_function(
                source_code,
                str(function_analysis.get("target") or ""),
                source_line=source_line,
                project_source_files=project_source_files,
                current_file_name=current_file_name,
            )
        for remover in (
            _remove_proven_c_false_branch,
            _remove_proven_c_unreachable_statement,
            _remove_proven_unused_c_declaration,
        ):
            transformed, replacements = remover(source_code, source_line)
            if replacements:
                return transformed, replacements
        return source_code, 0

    if not method_name:
        candidates = proven_unused_static_functions(
            source_code,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
        if len(candidates) != 1:
            return source_code, 0
        method_name = candidates[0]

    return _remove_proven_unused_c_static_function(
        source_code,
        method_name,
        source_line=source_line,
        project_source_files=project_source_files,
        current_file_name=current_file_name,
    )


def _split_c_params(params_raw: str) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    cleaned_params = params_raw.strip()
    if not cleaned_params or cleaned_params == "void":
        return params
    for raw in cleaned_params.split(","):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.match(r"(.+?)([A-Za-z_][A-Za-z0-9_]*)\s*$", cleaned)
        if not match:
            continue
        type_name = match.group(1).strip()
        name = match.group(2).strip()
        if type_name:
            params.append((type_name, name))
    return params


def _infer_c_method_context(source_code: str, source_index: int) -> tuple[str, list[tuple[str, str]], int]:
    prefix = source_code[:source_index]
    method_match = None
    pattern = re.compile(
        r"(?ms)^[ \t]*((?:[A-Za-z_][A-Za-z0-9_]*\s+|\*\s*)+)"
        r"[A-Za-z_][A-Za-z0-9_]*\s*\(([^;{}]*)\)\s*\{"
    )
    for match in pattern.finditer(prefix):
        method_match = match
    if not method_match:
        return "int", [], 0
    return " ".join(method_match.group(1).split()), _split_c_params(method_match.group(2)), method_match.end()


def _c_local_variables(source_code: str, start_idx: int, end_idx: int) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    body_prefix = source_code[start_idx:end_idx]
    pattern = re.compile(
        r"\b((?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)"
    )
    for match in pattern.finditer(body_prefix):
        type_name, name = " ".join(match.group(1).split()), match.group(2)
        if type_name in {"return", "if", "for", "while", "switch"}:
            continue
        declarations.append((type_name, name))
    return declarations


def _referenced_c_variables(selected_source: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", selected_source))
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for type_name, name in candidates:
        if name in identifiers and name not in seen:
            ordered.append((type_name, name))
            seen.add(name)
    return ordered


def _insert_c_helper_before_function(source_code: str, helper: str, source_index: int) -> str:
    function_start = source_code.rfind("\n", 0, source_index)
    if function_start < 0:
        function_start = 0
    else:
        function_start += 1
    return source_code[:function_start] + helper + "\n" + source_code[function_start:]


def apply_extract_method(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    """Backward-compatible entry point for semantic C extraction."""

    from .c_extract_method import _resolve_targets
    from .c_extract_method import apply_extract_method as apply_semantic_extract_method

    source_offset = sum(
        len(line)
        for line in source_code.splitlines(keepends=True)[: max(0, start_line - 1)]
    )
    candidates = []
    for name_match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_code):
        for candidate in _resolve_targets(source_code, name_match.group(1), ""):
            if candidate.start <= source_offset <= candidate.end:
                candidates.append(candidate)
    if not candidates:
        return source_code, 0
    transformed, replacements, _metadata = apply_semantic_extract_method(
        source_code,
        new_method_name=new_method_name,
        method_name=candidates[0].name,
        start_line=start_line,
        end_line=end_line,
    )
    return transformed, replacements


def _extract_full_c_function(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    selected = lines[start_line - 1 : end_line]
    selected_text = "".join(selected)
    signature = re.search(
        r"(?ms)^([ \t]*)((?:[A-Za-z_][A-Za-z0-9_]*\s+|\*\s*)+)"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*\{",
        selected_text,
    )
    if not signature:
        return source_code, 0

    open_brace = selected_text.find("{", signature.end() - 1)
    close_brace = selected_text.rfind("}")
    if open_brace < 0 or close_brace <= open_brace:
        return source_code, 0

    indent = signature.group(1)
    return_type = " ".join(signature.group(2).split())
    function_name = signature.group(3)
    params_raw = signature.group(4).strip()
    params = _split_c_params(params_raw)
    signature_params = params_raw or "void"
    call_args = ", ".join(name for _, name in params)
    body_text = selected_text[open_brace + 1 : close_brace]
    if not body_text.strip():
        return source_code, 0

    body_indent = "    "
    for line in body_text.splitlines(keepends=True):
        if line.strip():
            body_indent = re.match(r"[ \t]*", line).group(0) or "    "
            break

    call = (
        f"{body_indent}{new_method_name}({call_args});\n"
        if return_type == "void"
        else f"{body_indent}return {new_method_name}({call_args});\n"
    )
    original_function = (
        f"{indent}{return_type} {function_name}({signature_params}) {{\n"
        f"{call}"
        f"{indent}}}\n"
    )
    helper = f"static {return_type} {new_method_name}({signature_params}) {{{body_text}}}\n\n"
    return "".join(lines[: start_line - 1] + [helper, original_function] + lines[end_line:]), 1


def _split_call_args(args_raw: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    escape = False
    for char in args_raw:
        if quote:
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def apply_replace_unsafe_function(
    source_code: str,
    unsafe_function: str,
    safe_alternative: str,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    """Replace a known unsafe C function call with its safe equivalent.

    Handles special two-step rewrite for ``scanf`` → ``fgets`` + ``sscanf``:
    a temporary char buffer is introduced and the original format string and
    variable list are preserved so program behaviour is unchanged.
    """
    lines = source_code.splitlines(keepends=True)
    replacements = 0
    call_pattern = re.compile(rf"\b{re.escape(unsafe_function)}\s*\((.*?)\)")

    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue

def _is_c_pointer_variable(var_name: str, source_code: str) -> bool:
    """Return True if var_name is explicitly declared as a pointer (e.g. char *var, void *var)."""
    clean_var = var_name.strip()
    if not clean_var or not clean_var.isidentifier():
        return False
    # Check for pointer declaration on same line: e.g. char *dst, char* dst, void *dst (no newlines between * and name)
    ptr_re = re.compile(rf"\b\w+\s*\*+[^\S\r\n]*{re.escape(clean_var)}\b|\b{re.escape(clean_var)}\s*\[\s*\]")
    return bool(ptr_re.search(source_code))


def apply_replace_unsafe_function(
    source_code: str,
    unsafe_function: str,
    safe_alternative: str,
    target_line: Optional[int] = None,
) -> Tuple[str, int]:
    """
    Replace calls to unsafe C standard functions (gets, strcpy, strcat, sprintf, scanf)
    with safe bounded equivalents (fgets, strncpy, strncat, snprintf).
    Requires a known buffer size for pointers to prevent memory truncation.
    """
    lines = source_code.splitlines(keepends=True)
    replacements = 0

    call_pattern = re.compile(
        rf"\b{re.escape(unsafe_function)}\s*\((.*?)\)",
        re.DOTALL,
    )

    candidate_lines = (
        [target_line]
        if (target_line and 1 <= target_line <= len(lines))
        else list(range(1, len(lines) + 1))
    )

    for index in candidate_lines:
        line = lines[index - 1]
        masked = _mask_c_comments_and_strings(line)
        if unsafe_function not in masked:
            continue

        def replace_call(match: re.Match[str]) -> str:
            nonlocal replacements
            args = _split_call_args(match.group(1))
            if unsafe_function == "gets" and safe_alternative == "fgets" and args:
                buffer = args[0]
                if _is_c_pointer_variable(buffer, source_code):
                    return match.group(0)
                replacements += 1
                return f"fgets({buffer}, sizeof({buffer}), stdin)"
            if unsafe_function == "strcpy" and safe_alternative == "strncpy" and len(args) >= 2:
                destination, source = args[0], args[1]
                if _is_c_pointer_variable(destination, source_code):
                    return match.group(0)  # Require known array buffer size for pointer destination
                replacements += 1
                return f"strncpy({destination}, {source}, sizeof({destination}) - 1);\n    {destination}[sizeof({destination}) - 1] = '\\0'"
            if unsafe_function == "strcat" and safe_alternative == "strncat" and len(args) >= 2:
                destination, source = args[0], args[1]
                if _is_c_pointer_variable(destination, source_code):
                    return match.group(0)
                replacements += 1
                return f"strncat({destination}, {source}, sizeof({destination}) - strlen({destination}) - 1)"
            if unsafe_function == "sprintf" and safe_alternative == "snprintf" and len(args) >= 2:
                destination = args[0]
                rest = ", ".join(args[1:])
                if _is_c_pointer_variable(destination, source_code):
                    return match.group(0)
                replacements += 1
                return f"snprintf({destination}, sizeof({destination}), {rest})"
            if unsafe_function == "scanf" and safe_alternative == "fgets" and len(args) >= 1:
                replacements += 1
                fmt = args[0]
                rest = ", ".join(args[1:])
                leading = re.match(r"^(\s*)", match.string[match.pos:])
                indent = leading.group(1) if leading else ""
                buf_decl = "char _scanf_buf[256]"
                fgets_call = f"fgets(_scanf_buf, sizeof(_scanf_buf), stdin)"
                if rest:
                    sscanf_call = f"sscanf(_scanf_buf, {fmt}, {rest})"
                else:
                    sscanf_call = f"sscanf(_scanf_buf, {fmt})"
                return f"{buf_decl};\n{indent}{fgets_call};\n{indent}{sscanf_call}"

            return match.group(0)

        updated = call_pattern.sub(replace_call, line)
        lines[index - 1] = updated

    return "".join(lines), replacements


def apply_replace_nested_conditional_with_guard_clauses(
    source_code: str,
    method_name: Optional[str] = None,
    target_line: Optional[int] = None,
) -> Tuple[str, int, Dict[str, Any]]:
    """
    Transform nested conditionals in C into guard clauses where safe.
    If transformation cannot be applied safely, returns original code with count=0
    and status='review_required'.
    """
    from .c_guard_clauses import apply_replace_nested_conditional_with_guard_clauses as _apply
    return _apply(source_code, method_name=method_name, target_line=target_line)


def validate_c_guard_clauses(
    original_code: str,
    transformed_code: str,
    *,
    method: str = "",
) -> Dict[str, Any]:
    from .c_guard_clauses import validate_c_guard_clauses as _validate
    return _validate(original_code, transformed_code, method=method)


def _mask_c_comments_and_strings(source_code: str) -> str:
    """Mask comments and literals while retaining offsets and line breaks."""

    masked = list(source_code)
    index = 0
    state = "code"
    while index < len(masked):
        char = masked[index]
        nxt = masked[index + 1] if index + 1 < len(masked) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                masked[index] = " "
                index += 1
                state = "string"
                continue
            if char == "'":
                masked[index] = " "
                index += 1
                state = "char"
                continue
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                masked[index] = " "
        else:
            if char == "\\":
                masked[index] = " "
                if index + 1 < len(masked) and masked[index + 1] != "\n":
                    masked[index + 1] = " "
                index += 2
                continue
            if (state == "string" and char == '"') or (state == "char" and char == "'"):
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        index += 1
    return "".join(masked)


def _c_global_declaration_match(source_code: str, variable_name: str) -> tuple[re.Match[str] | None, str]:
    """Find one deliberately small scalar C global declaration."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable_name or ""):
        return None, "INVALID_VARIABLE_NAME"
    type_part = (
        r"(?:(?:const|volatile|unsigned|signed|short|long)\s+)*"
        r"(?:char|int|float|double|_Bool|size_t)"
        r"(?:\s*\*+\s*(?:const\s*)?)*"
    )
    declaration = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)(?P<storage>static\s+|extern\s+)?"
        rf"(?P<type>{type_part})\s+{re.escape(variable_name)}"
        rf"(?P<array>\s*\[[^\]\n]*\])?\s*(?P<initializer>=\s*[^;\n{{}}]+)?\s*;"
    )
    matches = list(declaration.finditer(source_code))
    if len(matches) != 1:
        array_declaration = re.search(
            rf"(?m)^\s*(?:static\s+|extern\s+)?[^;\n]*\b{re.escape(variable_name)}\s*\[",
            source_code,
        )
        if array_declaration:
            return None, "GLOBAL_ARRAY_UNSUPPORTED"
        return None, "GLOBAL_DECLARATION_NOT_FOUND_OR_AMBIGUOUS"
    match = matches[0]
    if match.group("storage") and match.group("storage").strip() == "extern":
        return None, "EXTERN_GLOBAL_UNSUPPORTED"
    if "volatile" in str(match.group("type") or "").split():
        return None, "VOLATILE_GLOBAL_UNSUPPORTED"
    if match.group("array"):
        return None, "GLOBAL_ARRAY_UNSUPPORTED"
    return match, ""


def _c_function_body_ranges(masked_code: str) -> list[tuple[int, int]]:
    """Return balanced brace ranges; global references must be inside one."""

    ranges: list[tuple[int, int]] = []
    stack: list[int] = []
    for index, char in enumerate(masked_code):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            ranges.append((start + 1, index))
    return ranges


def _c_statement_bounds(masked_code: str, position: int) -> tuple[int, int]:
    start = max(
        masked_code.rfind(";", 0, position),
        masked_code.rfind("{", 0, position),
        masked_code.rfind("}", 0, position),
    ) + 1
    end = masked_code.find(";", position)
    return start, end


def _c_replace_identifier(
    source_code: str,
    masked_code: str,
    *,
    start: int,
    end: int,
    variable_name: str,
    replacement: str,
) -> str:
    pattern = re.compile(rf"\b{re.escape(variable_name)}\b")
    return pattern.sub(replacement, source_code[start:end])


def _c_access_edits(
    source_code: str,
    masked_code: str,
    *,
    declaration: re.Match[str],
    variable_name: str,
    getter_name: str,
    setter_name: str,
    read_only: bool,
) -> tuple[list[tuple[int, int, str]], bool, str]:
    """Build non-overlapping edits for simple whole-statement scalar access."""

    function_ranges = _c_function_body_ranges(masked_code)
    occurrences = list(re.finditer(rf"\b{re.escape(variable_name)}\b", masked_code))
    edits: list[tuple[int, int, str]] = []
    write_ranges: list[tuple[int, int]] = []
    writable = False

    for occurrence in occurrences:
        start, end = occurrence.span()
        if declaration.start() <= start < declaration.end():
            continue
        if any(range_start <= start < range_end for range_start, range_end in write_ranges):
            continue
        if not any(range_start <= start < range_end for range_start, range_end in function_ranges):
            return [], False, "GLOBAL_REFERENCE_OUTSIDE_FUNCTION"
        before = masked_code[max(0, start - 16):start]
        after = masked_code[end:end + 3]
        if "&" in before[-3:] or after.lstrip().startswith("["):
            return [], False, "ADDRESS_OR_ARRAY_ACCESS_UNSUPPORTED"
        # Only '.' and the complete '->' token are C member access.  A plain
        # comparison such as ``quantity > total_stock`` must not be rejected
        # merely because the character immediately before the identifier is
        # '>'.
        if re.search(r"(?:\.|->)\s*$", before):
            return [], False, "MEMBER_ACCESS_UNSUPPORTED"

        statement_start, statement_end = _c_statement_bounds(masked_code, start)
        if statement_end < 0:
            return [], False, "UNTERMINATED_GLOBAL_ACCESS"
        statement_mask = masked_code[statement_start:statement_end].strip()
        source_prefix = source_code[statement_start:start]
        direct_increment = re.fullmatch(
            rf"(?:\+\+\s*{re.escape(variable_name)}|{re.escape(variable_name)}\s*\+\+)",
            statement_mask,
        )
        direct_decrement = re.fullmatch(
            rf"(?:--\s*{re.escape(variable_name)}|{re.escape(variable_name)}\s*--)",
            statement_mask,
        )
        direct_assignment = re.fullmatch(
            rf"{re.escape(variable_name)}\s*(?P<operator>=|\+=|-=)\s*(?P<value>.+)",
            statement_mask,
            re.DOTALL,
        )

        if direct_increment or direct_decrement:
            if read_only:
                return [], False, "WRITE_TO_READ_ONLY_GLOBAL"
            operator = "+" if direct_increment else "-"
            edits.append((
                statement_start,
                statement_end + 1,
                f"{source_prefix}{setter_name}({getter_name}() {operator} 1);",
            ))
            write_ranges.append((statement_start, statement_end + 1))
            writable = True
            continue

        if direct_assignment:
            if read_only:
                return [], False, "WRITE_TO_READ_ONLY_GLOBAL"
            operator = direct_assignment.group("operator")
            operator_match = re.match(
                r"\s*(?P<operator>=|\+=|-=)",
                masked_code[end:statement_end],
            )
            if operator_match is None:
                return [], False, "ASSIGNMENT_OPERATOR_UNSUPPORTED"
            value_start = end + operator_match.end()
            value = _c_replace_identifier(
                source_code,
                masked_code,
                start=value_start,
                end=statement_end,
                variable_name=variable_name,
                replacement=f"{getter_name}()",
            ).strip()
            if not value:
                return [], False, "ASSIGNMENT_VALUE_UNSUPPORTED"
            replacement_value = value if operator == "=" else f"{getter_name}() {operator[0]} ({value})"
            edits.append((
                statement_start,
                statement_end + 1,
                f"{source_prefix}{setter_name}({replacement_value});",
            ))
            write_ranges.append((statement_start, statement_end + 1))
            writable = True
            continue

        # Assignment or ++/-- inside an expression (for example a for-loop or
        # ``total = counter++``) has value semantics that setters cannot safely
        # preserve without a full C AST. Refuse it instead of changing behavior.
        if re.match(r"\s*(?:=|\+=|-=|\+\+|--)", after) or before.rstrip().endswith(("++", "--")):
            return [], False, "COMPLEX_WRITE_CONTEXT_UNSUPPORTED"
        edits.append((start, end, f"{getter_name}()"))

    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    previous_end = -1
    for start, end, _ in ordered:
        if start < previous_end:
            return [], False, "OVERLAPPING_GLOBAL_ACCESS_EDITS"
        previous_end = end
    return edits, writable, ""


def apply_encapsulate_c_variable(
    source_code: str,
    *,
    variable_name: str,
    getter_name: str = "",
    setter_name: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Encapsulate one proven-safe scalar C global using accessors.

    This deliberately supports only a single translation unit and simple
    scalar access patterns. Every other shape is returned as
    ``review_required`` with the original source unchanged.
    """

    def review(reason: str) -> tuple[str, int, dict[str, Any]]:
        return source_code, 0, {
            "status": "review_required",
            "reason": reason,
            "variable_name": variable_name,
        }

    declaration, declaration_error = _c_global_declaration_match(source_code, variable_name)
    if declaration is None:
        return review(declaration_error)
    if any(
        re.match(rf"\s*#.*\b{re.escape(variable_name)}\b", line)
        for line in source_code.splitlines()
    ):
        return review("PREPROCESSOR_DEPENDENT_GLOBAL_UNSUPPORTED")

    type_name = " ".join(str(declaration.group("type") or "").split())
    read_only = "const" in type_name.split()
    getter_name = getter_name or f"get_{variable_name}"
    setter_name = setter_name or f"set_{variable_name}"
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or "") for name in (getter_name, setter_name)):
        return review("INVALID_ACCESSOR_NAME")
    if re.search(rf"\b(?:{re.escape(getter_name)}|{re.escape(setter_name)})\s*\(", source_code):
        return review("ACCESSOR_NAME_COLLISION")

    masked = _mask_c_comments_and_strings(source_code)
    edits, writable, access_error = _c_access_edits(
        source_code,
        masked,
        declaration=declaration,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
        read_only=read_only,
    )
    if access_error:
        return review(access_error)
    if not edits:
        return review("NO_GLOBAL_ACCESS_FOUND")

    initializer = str(declaration.group("initializer") or "").strip()
    static_declaration = (
        f"{declaration.group('indent') or ''}static {type_name} {variable_name}"
        f" {initializer}".rstrip()
        + ";"
    )
    # Avoid a second space before an initializer while preserving its text.
    static_declaration = static_declaration.replace("  =", " =")
    accessor = (
        f"\n\n{type_name} {getter_name}(void) {{\n"
        f"    return {variable_name};\n"
        f"}}\n"
    )
    if writable:
        accessor += (
            f"\nvoid {setter_name}({type_name} value) {{\n"
            f"    {variable_name} = value;\n"
            f"}}\n"
        )
    edits.extend([
        (declaration.start(), declaration.end(), static_declaration + accessor),
    ])
    transformed = source_code
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    return transformed, len(edits), {
        "status": "success",
        "variable_name": variable_name,
        "getter_name": getter_name,
        "setter_name": setter_name if writable else "",
        "read_only": read_only,
        "writable": writable,
        "global_became_static": True,
        "effective_action_parameters": {
            "variable_name": variable_name,
            "getter_name": getter_name,
            "setter_name": setter_name if writable else "",
        },
    }


def apply_encapsulate_variable(
    source_code: str,
    variable_name: str,
    getter_name: str,
    setter_name: str,
) -> Tuple[str, int]:
    """Backward-compatible wrapper for the legacy C action name."""

    transformed, replacements, metadata = apply_encapsulate_c_variable(
        source_code,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
    )
    # Preserve the legacy wrapper contract: callers that explicitly supplied
    # a setter name expect that compatibility accessor even when this file has
    # no current writes.  The dedicated action keeps its stricter minimal-API
    # behavior.
    if (
        replacements > 0
        and metadata.get("status") == "success"
        and setter_name
        and not re.search(rf"\bvoid\s+{re.escape(setter_name)}\s*\(", transformed)
    ):
        declaration, _ = _c_global_declaration_match(source_code, variable_name)
        if declaration is not None:
            type_name = " ".join(str(declaration.group("type") or "").split())
            transformed = (
                transformed.rstrip()
                + f"\n\nvoid {setter_name}({type_name} value) {{\n"
                + f"    {variable_name} = value;\n"
                + "}\n"
            )
    # The legacy API reports one logical refactoring, while the dedicated C
    # implementation reports its declaration/access edit count.
    return transformed, int(replacements > 0 and transformed != source_code)


def _c_accessor_span(masked_code: str, accessor_name: str) -> tuple[int, int] | None:
    match = re.search(rf"\b{re.escape(accessor_name)}\s*\([^)]*\)\s*\{{", masked_code)
    if not match:
        return None
    opening = masked_code.find("{", match.start(), match.end())
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(masked_code)):
        if masked_code[index] == "{":
            depth += 1
        elif masked_code[index] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    return None


def _c_numeric_literal_value(text: str) -> float | int | None:
    value = str(text or "").strip()
    if not value:
        return None
    # Strip common scalar suffixes without trying to evaluate arbitrary C.
    value = re.sub(r"(?i)(?:u|l|f)+$", "", value).strip()
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value, 10)
        if re.fullmatch(
            r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?",
            value,
        ):
            return float(value)
    except ValueError:
        return None
    return None


def _c_initializer_semantically_equal(
    original_initializer: str,
    transformed_initializer: str,
    transformed_code: str,
) -> bool:
    """Compare a literal initializer with a later Introduce Constant macro.

    Structural validation runs after all plan actions.  A valid sequence can
    therefore change ``= 100`` into ``= CONSTANT_100`` after Encapsulate
    Variable has already succeeded.  Treat the two as equal only when the
    macro expands to the same simple scalar literal.
    """

    before = str(original_initializer or "").strip()
    after = str(transformed_initializer or "").strip()
    if before == after:
        return True
    before_expr = before[1:].strip() if before.startswith("=") else before
    after_expr = after[1:].strip() if after.startswith("=") else after
    before_value = _c_numeric_literal_value(before_expr)
    after_value = _c_numeric_literal_value(after_expr)
    if before_value is not None and after_value is not None:
        return float(before_value) == float(after_value)
    if before_value is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", after_expr):
        return False
    macro = re.search(
        rf"(?m)^\s*#\s*define\s+{re.escape(after_expr)}\s+(?P<value>[^\s/]+)",
        transformed_code,
    )
    if macro is None:
        return False
    macro_value = _c_numeric_literal_value(macro.group("value"))
    return macro_value is not None and float(before_value) == float(macro_value)


def validate_c_encapsulated_variable(
    original_code: str,
    transformed_code: str,
    *,
    variable_name: str,
    getter_name: str,
    setter_name: str,
) -> dict[str, Any]:
    """Validate that one C scalar global is no longer directly exposed."""

    before, before_error = _c_global_declaration_match(original_code, variable_name)
    after, after_error = _c_global_declaration_match(transformed_code, variable_name)
    if before is None or after is None:
        return {
            "passed": False,
            "reason": before_error or after_error or "GLOBAL_DECLARATION_NOT_FOUND",
            "checks": {"target_global_existed_before": before is not None},
        }
    original_type = " ".join(str(before.group("type") or "").split())
    transformed_type = " ".join(str(after.group("type") or "").split())
    read_only = "const" in original_type.split()
    original_masked = _mask_c_comments_and_strings(original_code)
    _, writable, access_error = _c_access_edits(
        original_code,
        original_masked,
        declaration=before,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
        read_only=read_only,
    )
    if access_error:
        return {
            "passed": False,
            "reason": f"original_access_not_safe:{access_error}",
            "checks": {"target_global_existed_before": True},
        }

    transformed_masked = _mask_c_comments_and_strings(transformed_code)
    allowed_spans = [span for span in (
        _c_accessor_span(transformed_masked, getter_name),
        _c_accessor_span(transformed_masked, setter_name) if writable else None,
    ) if span]
    direct_accesses = []
    for match in re.finditer(rf"\b{re.escape(variable_name)}\b", transformed_masked):
        if after.start() <= match.start() < after.end():
            continue
        if any(start <= match.start() < end for start, end in allowed_spans):
            continue
        direct_accesses.append(match.start())
    getter_exists = _c_accessor_span(transformed_masked, getter_name) is not None
    setter_exists = _c_accessor_span(transformed_masked, setter_name) is not None
    checks = {
        "target_global_existed_before": True,
        "global_is_static_after": bool(str(after.group("storage") or "").strip() == "static"),
        "getter_exists": getter_exists,
        "setter_exists_when_writable": (not writable) or setter_exists,
        "direct_unsafe_accesses_replaced": not direct_accesses,
        # ``_c_global_declaration_match`` above only succeeds for exactly one
        # scalar definition. Accessor bodies are intentionally excluded from
        # this declaration-level guarantee.
        "no_duplicate_global_declaration": True,
        "original_type_preserved": original_type == transformed_type,
        "original_initializer_preserved": _c_initializer_semantically_equal(
            str(before.group("initializer") or ""),
            str(after.group("initializer") or ""),
            transformed_code,
        ),
    }
    return {
        "passed": all(checks.values()),
        "variable_name": variable_name,
        "getter_name": getter_name,
        "setter_name": setter_name if writable else "",
        "writable": writable,
        "direct_access_count": len(direct_accesses),
        "checks": checks,
    }


def apply_inject_syntax_error(source_code: str) -> Tuple[str, int]:
    broken = source_code.rstrip() + "\nint __sctva_broken = ;\n"
    return broken, 1


def apply_fault_injection(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    if not original_logic:
        raise ValueError("fault_injection requires 'original_logic'.")
    if faulty_logic is None:
        raise ValueError("fault_injection requires 'faulty_logic'.")
    if original_logic not in source_code:
        return source_code, 0
    return source_code.replace(original_logic, faulty_logic, 1), 1


__all__ = [
    "apply_extract_constant",
    "analyze_extract_constant_target",
    "apply_fault_injection",
    "apply_inject_syntax_error",
    "apply_extract_method",
    "apply_introduce_parameter_object",
    "apply_replace_unsafe_function",
    "apply_encapsulate_variable",
    "apply_encapsulate_c_variable",
    "validate_c_encapsulated_variable",
    "apply_remove_dead_code",
    "apply_rename_symbol",
    "apply_replace_literal",
    "apply_normalize_multiline_statement",
    "validate_c_parameter_object",
    "apply_replace_nested_conditional_with_guard_clauses",
    "validate_c_guard_clauses",
]
