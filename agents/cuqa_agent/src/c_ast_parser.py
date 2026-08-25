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

import os
import re
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
            _TS_PARSER = Parser()
            _TS_PARSER.set_language(_C_LANGUAGE)
            _TREE_SITTER_AVAILABLE = True
        except Exception:
            pass

except Exception:
    # Graceful fallback: If tree-sitter fails to load, flag unavailable to trigger regex parser
    pass



# ---------------------------------------------------------------------------
# Regex-based helpers (used by fallback and metric calculation in both paths)
# ---------------------------------------------------------------------------

# Match a C function definition header: <type> <name> ( ... ) {
# This intentionally keeps it simple — it catches most real-world cases.
_FUNC_DEF_RE = re.compile(
    r"""
    ^                                      # start of line
    (?!.*\b(?:if|for|while|switch|else)\b) # exclude control structures
    [\w\s\*]+?                             # return type (non-greedy)
    \b(?P<name>[A-Za-z_]\w*)              # function name
    \s*\(                                  # opening paren
    (?P<params>[^)]*)                      # parameters (no nested parens)
    \)
    \s*\{                                  # opening brace on same/next line
    """,
    re.VERBOSE | re.MULTILINE,
)

# Single-line // comment
_SL_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)
# Multi-line /* ... */ comment — non-greedy
_ML_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# #include directive
_INCLUDE_RE = re.compile(r"^\s*#include\s*[<\"]", re.MULTILINE)

# Global variable: a declaration at file scope (outside any braces)
# We detect by looking for lines with a type + identifier + optional = value + ;
# outside of function bodies (heuristic — good enough for smell detection).
_GLOBAL_VAR_DECL_RE = re.compile(
    r"^(?!.*\b(?:typedef|struct|enum|union|#)\b)"  # exclude typedef/struct/etc
    r"(?:(?:extern|static|const|volatile|unsigned|signed|long|short|int|"
    r"float|double|char|void|size_t|uint\w*|int\w*)\s+)+"
    r"(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:=|;)",
    re.MULTILINE,
)

# Numeric literals (for magic number detection)
_NUMERIC_LIT_RE = re.compile(
    r"""
    (?<!["\w])           # not preceded by quote or word char (avoid strings/idents)
    -?                   # optional minus
    (?:
        0[xX][0-9a-fA-F]+[UuLl]*  # hex
      | 0[0-7]+[UuLl]*            # octal
      | \d+\.?\d*[UuLl]*          # decimal / float
    )
    (?![\w"])            # not followed by word char or quote
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


def _strip_string_literals(source: str) -> str:
    """
    Remove string literals from C source code.

    VIVA NOTE: Replaces string contents with empty quotes `""` to prevent false positive
    matches when searching for keywords, functions, or numeric literals inside string text.
    """
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', source)


def _strip_comments(source: str) -> str:
    """
    Strip single-line (`//`) and multi-line (`/* ... */`) comments from C source.

    VIVA NOTE: Comment removal ensures that commented-out code or explanations do not trigger
    false code smell warnings or skew cyclomatic complexity metrics.
    """
    s = _ML_COMMENT_RE.sub("", source)
    s = _SL_COMMENT_RE.sub("", s)
    return s


def _count_params(param_str: str) -> int:
    """
    Count function parameters from a raw parameter string declaration.

    VIVA NOTE: Handles `void` parameter lists (e.g. `int main(void)`) by returning 0.
    """
    param_str = param_str.strip()
    if not param_str or param_str == "void":
        return 0
    return len([p for p in param_str.split(",") if p.strip()])


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
        list[dict]: List of function definitions containing name, start_line, end_line, param_count.
    """
    clean = _strip_comments(source)
    clean = _strip_string_literals(clean)
    lines = clean.splitlines()
    text = "\n".join(lines)

    functions = []
    for m in _FUNC_DEF_RE.finditer(text):
        name = m.group("name")
        params = m.group("params")
        # Determine line number (1-indexed)
        line_no = text[: m.start()].count("\n") + 1
        # Find matching closing brace for end_line using depth tracking
        brace_pos = m.end() - 1  # position of '{' in text
        depth = 1
        pos = brace_pos + 1
        end_line = line_no
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        end_line = text[:pos].count("\n") + 1

        functions.append({
            "name": name,
            "line": line_no,
            "start_line": line_no,
            "end_line": end_line,
            "param_count": _count_params(params),
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
    clean = _strip_comments(source)
    clean = _strip_string_literals(clean)
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
    clean = _strip_comments(source)
    clean = _strip_string_literals(clean)
    depth = max_depth = 0
    for ch in clean:
        if ch == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "}":
            depth = max(0, depth - 1)
    return max_depth


def _find_global_vars_regex(source: str) -> list[dict]:
    """
    Detect global variable declarations at file scope.

    -------------------------------------------------------------------------
    VIVA NOTE: Tracks brace depth: variable declarations that occur when brace depth == 0
    reside at global scope. Global state can cause side effects and refactoring risks.
    -------------------------------------------------------------------------
    """
    clean = _strip_comments(source)
    clean = _strip_string_literals(clean)
    lines = clean.splitlines()

    globals_found = []
    depth = 0
    for lineno, line in enumerate(lines, start=1):
        depth += line.count("{") - line.count("}")
        depth = max(0, depth)
        if depth == 0:
            m = _GLOBAL_VAR_DECL_RE.match(line.strip())
            if m:
                name = m.group("name")
                if name not in {"main", "void"}:
                    globals_found.append({"name": name, "line": lineno})
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


def _extract_c_identifier(node) -> Optional[str]:
    """Extract variable identifier from a tree-sitter node."""
    if node.type == "identifier":
        return node.text.decode("utf-8", errors="replace")
    elif node.type == "field_expression":
        text = node.text.decode("utf-8", errors="replace")
        parts = re.split(r"\.|->", text)
        return parts[-1].strip() if parts else text
    elif node.type == "subscript_expression":
        for child in node.children:
            id_name = _extract_c_identifier(child)
            if id_name:
                return id_name
    for child in node.children:
        if child.is_named:
            id_name = _extract_c_identifier(child)
            if id_name:
                return id_name
    return None


def analyze_c_magic_numbers(source: str, filename: str = "untitled.c") -> list[dict]:
    """Detect magic numbers in C code using tree-sitter AST or regex fallback,
    extracting variable context if compared in a relational expression.
    """
    SAFE_NUMBERS = {"0", "1", "-1", "2", "0.0", "1.0", "0f", "1f", "0L", "1L"}
    smells: list[dict] = []

    clean_no_comments = _strip_comments(source)
    clean = _strip_string_literals(clean_no_comments)

    if _TREE_SITTER_AVAILABLE:
        try:
            source_bytes = source.encode("utf-8", errors="replace")
            tree = _TS_PARSER.parse(source_bytes)
            root = tree.root_node

            def _find_literals(n):
                literals = []
                if n.type in ("number_literal", "numeric_literal"):
                    literals.append(n)
                for child in n.children:
                    literals.extend(_find_literals(child))
                return literals

            num_nodes = _find_literals(root)
            relational_ops = {"==", "!=", "<", ">", "<=", ">="}

            for node in num_nodes:
                val = node.text.decode("utf-8", errors="replace").strip()
                if val in SAFE_NUMBERS:
                    continue

                line_no = node.start_point[0] + 1

                parent = node.parent
                while parent and parent.type in ("parenthesized_expression", "cast_expression", "argument_list"):
                    parent = parent.parent

                var_context = None
                if parent and parent.type == "binary_expression":
                    op_node = None
                    for child in parent.children:
                        ch_text = child.text.decode("utf-8", errors="replace")
                        if ch_text in relational_ops:
                            op_node = child
                            break

                    if op_node:
                        for child in parent.children:
                            if child != op_node and not (child.start_byte <= node.start_byte and node.end_byte <= child.end_byte):
                                var_context = _extract_c_identifier(child)
                                if var_context:
                                    break

                if var_context:
                    msg = f"Magic number {val} compared to variable '{var_context}'"
                    smells.append({
                        "type": "MagicNumber",
                        "message": msg,
                        "details": msg,
                        "variable_context": var_context,
                        "line": line_no,
                        "severity": "low",
                        "entity": filename,
                    })
                else:
                    msg = f"Magic number {val} detected"
                    smells.append({
                        "type": "MagicNumber",
                        "message": msg,
                        "details": msg,
                        "line": line_no,
                        "severity": "low",
                        "entity": filename,
                    })
            return smells
        except Exception:
            pass

    COMP_LEFT_RE = re.compile(r'\b([A-Za-z_]\w*)\s*(?:==|!=|<=|>=|<|>)\s*(-?\d+\.?\d*[fFdDlL]?)\b')
    COMP_RIGHT_RE = re.compile(r'\b(-?\d+\.?\d*[fFdDlL]?)\s*(?:==|!=|<=|>=|<|>)\s*([A-Za-z_]\w*)\b')

    for m in _NUMERIC_LIT_RE.finditer(clean):
        val = m.group(0).strip()
        if val not in SAFE_NUMBERS:
            line_no = clean[: m.start()].count("\n") + 1
            split_lines = clean.splitlines()
            line_str = split_lines[line_no - 1] if line_no <= len(split_lines) else ""

            var_context = None
            m_left = COMP_LEFT_RE.search(line_str)
            m_right = COMP_RIGHT_RE.search(line_str)
            if m_left and m_left.group(2).strip() == val:
                var_context = m_left.group(1)
            elif m_right and m_right.group(1).strip() == val:
                var_context = m_right.group(2)

            if var_context:
                msg = f"Magic number {val} compared to variable '{var_context}'"
                smells.append({
                    "type": "MagicNumber",
                    "message": msg,
                    "details": msg,
                    "variable_context": var_context,
                    "line": line_no,
                    "severity": "low",
                    "entity": filename,
                })
            else:
                msg = f"Magic number {val} detected"
                smells.append({
                    "type": "MagicNumber",
                    "message": msg,
                    "details": msg,
                    "line": line_no,
                    "severity": "low",
                    "entity": filename,
                })
    return smells

