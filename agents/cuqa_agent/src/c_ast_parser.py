"""
c_ast_parser.py
---------------
C / C++ Header AST Parser Module for the CUQA Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. PURPOSE:
   Parses C source code (.c) and C header files (.h) into the CUQA AST JSON Schema.
   Computes key C metrics: Function boundaries, Lines of Code (LOC), Cyclomatic Complexity,
   Brace Nesting Depth, Unsafe C Function Calls (`gets`, `strcpy`, `sprintf`),
   Global Variable Declarations, and Contextual Magic Number comparisons.

2. DUAL PARSING ENGINE STRATEGY (Resilience Architecture):
   - Primary Parser: `tree-sitter` (Fast, precise concrete syntax tree generator).
   - Fallback Parser: Regex-based pattern matching parser (`_build_ast_regex`).
   - WHY THIS MATTERS FOR VIVA: C/C++ grammars are notoriously hard to parse without a
     preprocessor. If C header dependencies or C runtime compilers are missing on the host,
     Tree-sitter might fail or be uninstalled. The Regex fallback guarantees 100% server
     uptime without crashing or throwing unhandled errors.

3. SPECIAL FUNCTIONS & ALGORITHMS:
   - `analyze_c_magic_numbers()`: AST & Regex algorithm that locates numeric constants
     and extracts the specific variable name compared against them (e.g. `x > 100`).
   - `_estimate_cyclomatic()`: Computes Cyclomatic Complexity (CC = 1 + decision points).
   - `_max_nesting_depth()`: Tracks brace nesting levels to spot DeepNesting code smells.
===============================================================================
"""

import bisect
import os
import re
from functools import lru_cache
from typing import Optional

# ---------------------------------------------------------------------------
# Dynamic Engine Discovery: Tree-sitter setup with multi-version API support
# ---------------------------------------------------------------------------

_TREE_SITTER_AVAILABLE = False
_C_LANGUAGE = None

try:
    # Support tree-sitter >= 0.21 (tree_sitter_languages package API)
    try:
        from tree_sitter_languages import get_language, get_parser  # type: ignore
        _C_LANGUAGE = get_language("c")
        _TS_PARSER = get_parser("c")
        _TREE_SITTER_AVAILABLE = True
    except ImportError:
        pass

    if not _TREE_SITTER_AVAILABLE:
        # Support tree-sitter < 0.21 (legacy compiled tree_sitter_c API)
        import tree_sitter  # type: ignore
        from tree_sitter import Language, Parser  # type: ignore
        try:
            import tree_sitter_c as tsc  # type: ignore
            _C_LANGUAGE = Language(tsc.language())
            # Language(tsc.language()) needs tree-sitter >= 0.22, where the language
            # is passed to Parser() (Parser.set_language was removed)
            _TS_PARSER = Parser(_C_LANGUAGE)
            _TREE_SITTER_AVAILABLE = True
        except Exception:
            pass

except Exception:
    # Graceful fallback: If tree-sitter fails to load, flag unavailable to trigger regex parser
    pass



# ---------------------------------------------------------------------------
# Regex-based helpers (used by fallback and metric calculation in both paths)
# ---------------------------------------------------------------------------

# Words that start a statement, so they can never be a return type or a function name
_C_STATEMENT_WORDS = r"(?:if|else|for|while|switch|do|return|case|goto|sizeof|typedef)"

# Match a C function definition header: <type> <name> ( ... ) {
# Runs on masked source (comments and literals blanked), so line numbers stay exact.
_FUNC_DEF_RE = re.compile(
    rf"""
    ^[ \t]*                                    # start of line with optional indent
    (?!{_C_STATEMENT_WORDS}\b)                 # a statement, not a declaration
    (?:[A-Za-z_]\w*\b[ \t\*]*(?:\r?\n[ \t\*]*)?)+?  # return type words; '*' and a line break allowed
    (?!{_C_STATEMENT_WORDS}\b)                 # name must not be a keyword
    (?P<name>[A-Za-z_]\w*)                     # function name
    \s*\(                                      # opening paren
    (?P<params>(?:[^()]|\([^()]*\))*)          # parameters, one level of nested parens allowed
    \)
    \s*\{{                                     # opening brace (no semicolon, so not a prototype)
    """,
    re.VERBOSE | re.MULTILINE,
)

# #include directive
_INCLUDE_RE = re.compile(r"^\s*#include\s*[<\"]", re.MULTILINE)

# Global variable: a declaration at file scope (outside any braces).
# const declarations are named constants and extern lines only refer to a
# variable defined elsewhere, so neither is reported.
_GLOBAL_VAR_DECL_RE = re.compile(
    r"^(?!.*\b(?:typedef|struct|enum|union|extern|const)\b)"
    r"(?:(?:static|volatile|unsigned|signed|long|short|int|"
    r"float|double|char|void|size_t|bool|_Bool|uint\w*|int\w*)\s+)+"
    r"\**\s*(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:\[[^\]]*\]\s*)*(?:=|;)",
    re.MULTILINE,
)

# Numeric literals (for magic number detection). The sign is handled separately
# so that "x - 5" is read as a subtraction, not as the literal -5.
_NUMERIC_LIT_RE = re.compile(
    r"""
    (?<![\w.])                                    # not part of an identifier or member
    (?:
        0[xX][0-9a-fA-F]+                         # hex
      | (?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?   # decimal / octal / float
    )
    [uUlLfF]*                                     # type suffix
    (?![\w.])
    """,
    re.VERBOSE,
)

# Unsafe C functions
_UNSAFE_FUNCS = {"gets", "strcpy", "strcat", "sprintf", "scanf"}
_UNSAFE_CALL_RE = re.compile(
    r"\b(?P<fn>" + "|".join(re.escape(f) for f in _UNSAFE_FUNCS) + r")\s*\(",
)

# Braces for nesting depth
_OPEN_BRACE_RE = re.compile(r"\{")
_CLOSE_BRACE_RE = re.compile(r"\}")
_BRACE_RE = re.compile(r"[{}]")

# Lexer pieces for _mask_c_like
_LEX_SPECIAL_RE = re.compile(r"//|/\*|[\"']")
_DQ_BODY_RE = re.compile(r'(?:[^"\\\n]|\\.)*', re.DOTALL)
_SQ_BODY_RE = re.compile(r"(?:[^'\\\n]|\\.)*", re.DOTALL)
_NOT_NEWLINE_RE = re.compile(r"[^\n]")


@lru_cache(maxsize=16)
def _mask_c_like(source: str, mask_comments: bool = True, mask_strings: bool = True) -> str:
    """
    Blank out comments and/or string and character literals in C-family source.

    VIVA NOTE: Every masked character becomes a space and every newline is kept, so
    the result has the same line and column layout as the input. Line numbers counted
    on the masked text point at the right line of the original file. One left-to-right
    scan is used so that "//" inside a string ("http://x") and a quote inside a comment
    (don't) are not mistaken for each other. Also used for Java, which shares the syntax.
    """
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        m = _LEX_SPECIAL_RE.search(source, i)
        if not m:
            out.append(source[i:])
            break
        out.append(source[i:m.start()])
        token, start = m.group(0), m.start()
        if token == "//":
            end = source.find("\n", start)
            end = n if end == -1 else end
            out.append(" " * (end - start) if mask_comments else source[start:end])
        elif token == "/*":
            end = source.find("*/", start + 2)
            end = n if end == -1 else end + 2
            seg = source[start:end]
            out.append(_NOT_NEWLINE_RE.sub(" ", seg) if mask_comments else seg)
        else:
            body = (_DQ_BODY_RE if token == '"' else _SQ_BODY_RE).match(source, start + 1)
            assert body is not None  # both patterns also match an empty body
            end = body.end()
            closed = end < n and source[end] == token
            if closed:
                end += 1
            if mask_strings:
                inner = _NOT_NEWLINE_RE.sub(" ", body.group(0))
                out.append(token + inner + (token if closed else ""))
            else:
                out.append(source[start:end])
        i = end
    return "".join(out)


def _strip_string_literals(source: str) -> str:
    """
    Blank the contents of string and character literals in C source.

    VIVA NOTE: Prevents false positive matches when searching for keywords, functions,
    braces or numeric literals inside string text. Line layout is preserved.
    """
    return _mask_c_like(source, mask_comments=False, mask_strings=True)


def _strip_comments(source: str) -> str:
    """
    Blank single-line (`//`) and multi-line (`/* ... */`) comments in C source.

    VIVA NOTE: Comment removal ensures that commented-out code or explanations do not trigger
    false code smell warnings or skew cyclomatic complexity metrics. Comments are replaced by
    spaces with their newlines kept, so every finding keeps its real line number.
    """
    return _mask_c_like(source, mask_comments=True, mask_strings=False)


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on *sep* only where it is not inside parentheses or brackets."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _count_params(param_str: str) -> int:
    """
    Count function parameters from a raw parameter string declaration.

    VIVA NOTE: Handles `void` parameter lists (e.g. `int main(void)`) by returning 0, and
    does not split on commas inside function-pointer parameters like `void (*cb)(int, int)`.
    """
    param_str = param_str.strip()
    if not param_str or param_str == "void":
        return 0
    return len([p for p in _split_top_level(param_str) if p.strip()])


def _find_functions_regex(source: str) -> list[dict]:
    """
    SPECIAL FALLBACK ALGORITHM: Locate C function definitions using Regex pattern matching.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Regex Function Extraction & Brace Tracking):
    1. Pre-cleans source code by stripping comments and string literals.
    2. Executes `_FUNC_DEF_RE` regex to find function headers (return type, name, params).
    3. Brace-Tracking Stack Loop:
       Once a function header `{` is located, tracks opening `{` and closing `}` braces
       to determine the exact line where the function body terminates (`end_line`).
    -------------------------------------------------------------------------

    Returns:
        list[dict]: List of function definitions containing name, start_line, end_line,
        param_count, and the character span (start_pos, end_pos) in the source.
    """
    text = _mask_c_like(source)

    functions = []
    last_end = -1
    for m in _FUNC_DEF_RE.finditer(text):
        if m.start() < last_end:
            continue  # inside an earlier function body: a statement, not a definition
        params = m.group("params")
        if re.search(r"[;{}]", params):
            continue  # not a parameter list
        line_no = text.count("\n", 0, m.start()) + 1
        # Find matching closing brace for end_line using depth tracking
        depth = 1
        pos = len(text)
        for b in _BRACE_RE.finditer(text, m.end()):
            depth += 1 if b.group(0) == "{" else -1
            if depth == 0:
                pos = b.end()
                break
        end_line = text.count("\n", 0, pos) + 1
        last_end = pos

        functions.append({
            "name": m.group("name"),
            "line": line_no,
            "start_line": line_no,
            "end_line": end_line,
            "param_count": _count_params(params),
            "start_pos": m.start(),
            "end_pos": pos,
        })
    return functions


def _estimate_cyclomatic(source: str) -> int:
    """
    Estimate Cyclomatic Complexity (CC) for C code file.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (McCabe Cyclomatic Complexity Metric):
    - Formula: CC = 1 + Decision Points
    - Scans clean C code for decision keywords/operators:
      `if`, `else if`, `for`, `while`, `case`, `&&`, `||`, `?` (Ternary).
    - Higher CC values indicate complex, hard-to-test control flow branches.
    -------------------------------------------------------------------------
    """
    clean = _mask_c_like(source)
    decision_re = re.compile(
        r"\b(?:if|else\s+if|for|while|case)\b|&&|\|\||\?"
    )
    count = len(decision_re.findall(clean))
    return 1 + count


def _max_nesting_depth(source: str) -> int:
    """
    Calculate maximum brace nesting depth in C code.

    -------------------------------------------------------------------------
    VIVA NOTE: Detects deeply nested control blocks (`if` inside `for` inside `while`).
    Excessive nesting (> 4 or 5 levels) is flagged under the `DeepNesting` code smell.
    -------------------------------------------------------------------------
    """
    clean = _mask_c_like(source)
    depth = max_depth = 0
    for ch in clean:
        if ch == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "}":
            depth = max(0, depth - 1)
    return max_depth


_CONTROL_PAREN_HEAD_RE = re.compile(r"\b(?:if|for|while|switch)\s*$")
_CONTROL_WORD_HEAD_RE = re.compile(r"\b(?:else|do)$")


def _opens_control_block(text: str, brace_pos: int) -> bool:
    """True when the '{' at *brace_pos* opens the body of if/else/for/while/do/switch."""
    before = text[max(0, brace_pos - 4000):brace_pos].rstrip()
    if _CONTROL_WORD_HEAD_RE.search(before):
        return True
    if not before.endswith(")"):
        return False
    depth = 0
    for i in range(len(before) - 1, -1, -1):
        ch = before[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                return bool(_CONTROL_PAREN_HEAD_RE.search(before[:i]))
    return False


def _max_control_nesting(fn_source: str) -> int:
    """
    Deepest nesting of control-flow blocks inside one C function.

    -------------------------------------------------------------------------
    VIVA NOTE: Only braces that open an if/else/for/while/do/switch body count.
    The function's own braces, initializer lists (`= {0}`), case blocks and plain
    scope blocks are not control flow, so they do not add depth. This follows the
    usual definition of the Deep Nesting smell (e.g. SonarQube rule S134).
    -------------------------------------------------------------------------
    """
    text = _mask_c_like(fn_source)
    body_start = text.find("{")
    if body_start == -1:
        return 0
    stack: list[bool] = []
    depth = max_depth = 0
    for b in _BRACE_RE.finditer(text, body_start + 1):
        if b.group(0) == "{":
            is_control = _opens_control_block(text, b.start())
            stack.append(is_control)
            if is_control:
                depth += 1
                max_depth = max(max_depth, depth)
        else:
            if not stack:
                break  # closing brace of the function body
            if stack.pop():
                depth -= 1
    return max_depth


def _find_global_vars_regex(source: str) -> list[dict]:
    """
    Detect global variable declarations at file scope.

    -------------------------------------------------------------------------
    VIVA NOTE: Tracks brace depth: variable declarations on a line that starts at brace
    depth 0 reside at global scope. Global state can cause side effects and refactoring
    risks. Named constants (const) and extern references are not reported.
    -------------------------------------------------------------------------
    """
    masked = _mask_c_like(source)
    directive_lines = _preprocessor_lines(masked)

    globals_found = []
    depth = 0
    for lineno, line in enumerate(masked.splitlines(), start=1):
        if lineno in directive_lines:
            continue  # #define bodies (with continuation lines) are not declarations
        if depth == 0:
            m = _GLOBAL_VAR_DECL_RE.match(line.strip())
            if m:
                name = m.group("name")
                if name not in {"main", "void"}:
                    globals_found.append({"name": name, "line": lineno})
        depth += line.count("{") - line.count("}")
        depth = max(0, depth)
    return globals_found



# ---------------------------------------------------------------------------
# Fallback regex-based AST builder
# ---------------------------------------------------------------------------

def _build_ast_regex(source: str, filename: str) -> dict:
    """Build a simplified CUQA AST JSON using regex (no tree-sitter)."""
    functions = _find_functions_regex(source)
    clean = _strip_comments(source)
    includes = _INCLUDE_RE.findall(clean)

    children = []

    # Include nodes
    for i, _ in enumerate(includes):
        children.append({
            "type": "IncludeDirective",
            "name": f"#include[{i}]",
            "line": None,
            "children": [],
        })

    # Function nodes
    for fn in functions:
        children.append({
            "type": "FunctionDefinition",
            "name": fn["name"],
            "line": fn["line"],
            "children": [],
        })

    return {
        "type": "TranslationUnit",
        "name": filename,
        "children": children,
    }


# ---------------------------------------------------------------------------
# tree-sitter AST builder
# ---------------------------------------------------------------------------

def _build_ast_treesitter(source: str, filename: str) -> dict:
    """Build a CUQA AST JSON using tree-sitter for C."""
    source_bytes = source.encode("utf-8", errors="replace")
    tree = _TS_PARSER.parse(source_bytes)
    root = tree.root_node

    def _convert(node, depth=0, max_depth=10) -> dict:
        result = {"type": node.type, "children": []}
        if node.start_point:
            result["line"] = node.start_point[0] + 1  # 0-indexed → 1-indexed

        # Attach name for named nodes
        if node.type in ("function_definition", "declaration", "preproc_include"):
            for child in node.children:
                if child.type in ("function_declarator", "identifier", "pointer_declarator"):
                    # Dig for the identifier
                    def _get_id(n):
                        if n.type == "identifier":
                            return n.text.decode("utf-8", errors="replace")
                        for c in n.children:
                            r = _get_id(c)
                            if r:
                                return r
                        return None
                    name = _get_id(child)
                    if name:
                        result["name"] = name
                    break
            if node.type == "preproc_include":
                result["type"] = "IncludeDirective"

        if node.type == "function_definition":
            result["type"] = "FunctionDefinition"

        if depth < max_depth:
            for child in node.children:
                if child.is_named:
                    result["children"].append(_convert(child, depth + 1, max_depth))

        return result

    return {
        "type": "TranslationUnit",
        "name": filename,
        "children": [_convert(c) for c in root.children if c.is_named],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_c_file(file_path: str) -> dict:
    """
    Parse a C source file (.c or .h) and return the CUQA AST JSON.

    Args:
        file_path: Absolute or relative path to a .c or .h file.

    Returns:
        dict conforming to CUQA AST schema:
        {
          "file": "<filename>",
          "language": "c",
          "ast": { ... }
        }
    """
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    return parse_c_source(source, os.path.basename(file_path))


def parse_c_source(source: str, filename: str = "untitled.c") -> dict:
    """
    Parse C source provided as a string.

    Tries tree-sitter first; falls back to regex if unavailable or on error.

    Args:
        source:   Raw C source code.
        filename: Original filename (used for schema 'file' field).

    Returns:
        dict conforming to CUQA AST schema.
    """
    if _TREE_SITTER_AVAILABLE:
        try:
            ast_node = _build_ast_treesitter(source, filename)
            return {
                "file": filename,
                "language": "c",
                "ast": ast_node,
                "parser": "tree-sitter",
            }
        except Exception as exc:
            # Fall through to regex fallback
            ast_node = _build_ast_regex(source, filename)
            return {
                "file": filename,
                "language": "c",
                "ast": ast_node,
                "parser": "regex-fallback",
                "parser_warning": f"tree-sitter failed ({exc}); used regex fallback.",
            }

    # Regex fallback
    ast_node = _build_ast_regex(source, filename)
    return {
        "file": filename,
        "language": "c",
        "ast": ast_node,
        "parser": "regex-fallback",
    }


# ---------------------------------------------------------------------------
# Magic number detection
# ---------------------------------------------------------------------------

# Values never reported: 0, 1, 2, -1 and widely recognised sizes, ports and HTTP codes.
_C_SAFE_NUMBER_VALUES = {
    0, 1, 2, -1, 16, 32, 64, 128, 255, 256, 512, 1024, 2048, 4096,
    80, 443, 8080, 100, 200, 404, 500,
}

# Unit conversion factors (ms -> s, s -> min, ...) explain themselves next to * / %
_UNIT_FACTORS = {60, 1000, 3600, 86400, 1000000, 1000000000}

# Test / fixture / spec files: literals there are expected values, not magic numbers
_TEST_FILE_RE = re.compile(
    r"(?:^|[_\-.])(?:test|tests|spec|fixture|fixtures)[_\-.]"
    r"|[_\-.](?:test|tests|spec|fixture|fixtures)$"
)

_DECL_WORDS = r"(?:[A-Za-z_]\w*[\s\*]+)"
_CASE_LABEL_RE = re.compile(r"\bcase[\s(+\-]*$")
_CONST_DECL_RE = re.compile(rf"\s*{_DECL_WORDS}*?const\b")
_NAMED_ASSIGN_RE = re.compile(
    rf"\s*{_DECL_WORDS}*?\**(?P<name>[A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*=(?!=)"
)
_ARRAY_DIM_DECL_RE = re.compile(
    rf"\s*(?!{_C_STATEMENT_WORDS}\b){_DECL_WORDS}+\**[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*\[\s*$"
)
_ASSERT_CALL_RE = re.compile(
    r"\b(?:assert|static_assert|_Static_assert|[A-Z_]*ASSERT\w*|EXPECT_\w+|ck_assert\w*|"
    r"fail_unless|fail_if)\s*\("
)
_MODE_CALL_RE = re.compile(
    r"\b(?:open|openat|creat|mkdir|mkdirat|mkfifo|mkfifoat|chmod|fchmod|fchmodat|umask|"
    r"mknod|mknodat|shm_open|sem_open|mq_open)\s*\("
)
_ENUM_HEAD_RE = re.compile(r"\benum\b[\s\w]*$")
_COMPARE_BEFORE_RE = re.compile(
    r"(?P<var>[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*)\s*(?:\[[^\]]*\]\s*)?"
    r"(?:==|!=|<=|>=|(?<![<\-])<|(?<![>\-])>)\s*-?\s*$"
)
_COMPARE_AFTER_RE = re.compile(
    r"\s*(?:==|!=|<=|>=|<(?!<)|>(?!>))\s*"
    r"(?P<var>[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*)*)"
)
_CONST_NAME_PREFIXES = ("MAX_", "MIN_", "DEFAULT_", "CONST_", "NUM_", "TIMEOUT_", "PORT_", "LIMIT_", "TOTAL_")


def _c_number_value(token: str):
    """Numeric value of a C literal token, or None when it cannot be read."""
    t = token.lower()
    try:
        if t.startswith("0x"):
            return int(t.rstrip("ul"), 16)
        t = t.rstrip("ulf")
        if "." in t or "e" in t:
            return float(t)
        if len(t) > 1 and t.startswith("0"):
            return int(t, 8)
        return int(t)
    except ValueError:
        return None


def _has_unary_minus(text: str, start: int) -> bool:
    """True when the literal at *start* is negated (`= -5`), not subtracted (`x - 5`)."""
    before = text[max(0, start - 40):start].rstrip()
    if not before.endswith("-") or before.endswith("--"):
        return False
    prev = before[:-1].rstrip()
    return not prev or prev[-1] in "=(,[{<>!&|?:+-*/%^~;" or bool(re.search(r"\b(?:return|case)$", prev))


def _preprocessor_lines(text: str) -> set[int]:
    """Line numbers that belong to preprocessor directives, continuation lines included."""
    lines: set[int] = set()
    continued = False
    for no, line in enumerate(text.split("\n"), start=1):
        if continued or line.lstrip().startswith("#"):
            lines.add(no)
            continued = line.rstrip().endswith("\\")
        else:
            continued = False
    return lines


def _statement_before(text: str, pos: int, limit: int) -> tuple[str, str]:
    """
    Return (kind, text) for the statement that holds the literal at *pos*.

    `text` runs from the start of the statement up to the literal. `kind` is
    "enum" inside an enum body, "init_list" inside a brace initializer such as
    `int t[] = {3, 5, 7}`, and "stmt" otherwise. The scan stops at *limit*
    (the opening brace of the enclosing function body).
    """
    depth = 0
    in_init = False
    i = pos - 1
    while i > limit:
        ch = text[i]
        if ch == ";" and depth == 0:
            break
        if ch == "}":
            if depth == 0 and not in_init:
                break
            depth += 1
        elif ch == "{":
            if depth > 0:
                depth -= 1
            else:
                before = text[max(limit, i - 300):i].rstrip()
                if before.endswith(("=", ",", "{")):
                    in_init = True
                elif _ENUM_HEAD_RE.search(before):
                    return "enum", text[i + 1:pos]
                else:
                    break
        i -= 1
    return ("init_list" if in_init else "stmt"), text[i + 1:pos]


def _is_unit_factor(text: str, start: int, end: int, value) -> bool:
    """True for 1000 in `timeout_ms / 1000` or 60 in `minutes * 60`."""
    if abs(value) not in _UNIT_FACTORS:
        return False
    before = text[max(0, start - 40):start].rstrip().rstrip("-").rstrip()
    after = text[end:end + 40].lstrip()
    return before.endswith(("*", "/", "%", "*=", "/=", "%=")) or after[:1] in ("*", "/", "%")


def _compared_variable(statement: str, text: str, end: int) -> Optional[str]:
    """Name of the variable a literal is compared against (`x > 50` -> 'x'), if any."""
    m = _COMPARE_BEFORE_RE.search(statement)
    if not m:
        m = _COMPARE_AFTER_RE.match(text, end)
    if not m:
        return None
    return re.split(r"\.|->", m.group("var"))[-1].strip()


def _is_c_constant_definition(statement: str) -> bool:
    """True when the literal initialises a named constant (const, or UPPER_CASE name)."""
    if _CONST_DECL_RE.match(statement):
        return True
    m = _NAMED_ASSIGN_RE.match(statement)
    if m:
        name = m.group("name")
        return name.isupper() or name.startswith(_CONST_NAME_PREFIXES)
    return False


def analyze_c_magic_numbers(source: str, filename: str = "untitled.c") -> list[dict]:
    """
    Detect magic numbers in C function bodies, with the compared variable when the
    literal sits in a relational expression (e.g. `x > 100`).

    -------------------------------------------------------------------------
    VIVA NOTE (what is NOT a magic number):
    A magic number is an unnamed literal in program logic. These are skipped:
      - 0, 1, 2, -1 and the recognised values in _C_SAFE_NUMBER_VALUES
      - anything outside a function body (globals, enums, array sizes, bit-fields)
      - preprocessor directives (#define, #if ...), including continuation lines
      - const / UPPER_CASE constant definitions, enum values, initializer lists
      - switch case labels and array sizes in local declarations
      - assert / test-macro arguments and whole test or fixture files
      - unit conversion factors next to * / % (timeout_ms / 1000)
      - permission modes passed to open/mkdir/chmod/... (0644)
    Comments, string literals and directives are blanked first with newlines kept,
    so the reported line is always the line the literal is on.
    -------------------------------------------------------------------------
    """
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    if _TEST_FILE_RE.search(stem):
        return []

    # Preprocessor directives are blanked too: they are neither code nor part of
    # the statement that follows them
    masked = _mask_c_like(source)
    directive_lines = _preprocessor_lines(masked)
    text = "\n".join(
        " " * len(line) if no in directive_lines else line
        for no, line in enumerate(masked.split("\n"), start=1)
    )
    newlines = [m.start() for m in re.finditer("\n", text)]
    smells: list[dict] = []

    for fn in _find_functions_regex(source):
        body_start = text.find("{", fn["start_pos"])
        if body_start == -1:
            continue
        for m in _NUMERIC_LIT_RE.finditer(text, body_start + 1, fn["end_pos"]):
            token, start, end = m.group(0), m.start(), m.end()
            line_no = bisect.bisect_left(newlines, start) + 1
            value = _c_number_value(token)
            if value is None:
                continue
            negative = _has_unary_minus(text, start)
            if negative:
                value = -value
            if value in _C_SAFE_NUMBER_VALUES:
                continue

            kind, statement = _statement_before(text, start, body_start)
            if kind != "stmt":
                continue  # enum value or data table entry
            if (
                _CASE_LABEL_RE.search(statement)
                or _is_c_constant_definition(statement)
                or _ARRAY_DIM_DECL_RE.match(statement)
                or _ASSERT_CALL_RE.search(statement)
                or _is_unit_factor(text, start, end, value)
                or (re.fullmatch(r"0[0-7]{3,4}", token) and _MODE_CALL_RE.search(statement))
            ):
                continue

            shown = f"-{token}" if negative else token
            var_context = _compared_variable(statement, text, end)
            smell = {
                "type": "MagicNumber",
                "line": line_no,
                "severity": "low",
                "entity": fn["name"],
            }
            if var_context:
                msg = f"Magic number {shown} compared to variable '{var_context}'"
                smell["variable_context"] = var_context
            else:
                msg = f"Magic number {shown} detected"
            smell["message"] = msg
            smell["details"] = msg
            smells.append(smell)
    return smells
