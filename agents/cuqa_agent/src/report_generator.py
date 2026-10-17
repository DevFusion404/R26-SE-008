"""
report_generator.py
-------------------
Generates the CUQA Quality Report JSON for a parsed codebase.
Produces code smell detection, complexity metrics, and a structured
report suitable for downstream consumption by the RDP Agent.

Supported languages: Python, Java, C (.c / .h)
"""

import os
import re
import ast as pyast
import hashlib
from collections import defaultdict, deque
from typing import Any

try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False

# Import shared regex helpers from c_ast_parser (no duplication)
try:
    from c_ast_parser import (
        _find_functions_regex,
        _find_global_vars_regex,
        _mask_c_like,
        _strip_comments,
        _INCLUDE_RE,
        _UNSAFE_CALL_RE,
        _C_STATEMENT_WORDS,
        _max_control_nesting,
        _preprocessor_lines,
        _estimate_cyclomatic,
        analyze_c_magic_numbers,
    )
    _C_HELPERS_AVAILABLE = True
except ImportError:
    try:
        from .c_ast_parser import (
            _find_functions_regex,
            _find_global_vars_regex,
            _mask_c_like,
            _strip_comments,
            _INCLUDE_RE,
            _UNSAFE_CALL_RE,
            _C_STATEMENT_WORDS,
            _max_control_nesting,
            _preprocessor_lines,
            _estimate_cyclomatic,
            analyze_c_magic_numbers,
        )
        _C_HELPERS_AVAILABLE = True
    except ImportError:
        try:
            from agents.cuqa_agent.src.c_ast_parser import (
                _find_functions_regex,
                _find_global_vars_regex,
                _mask_c_like,
                _strip_comments,
                _INCLUDE_RE,
                _UNSAFE_CALL_RE,
                _C_STATEMENT_WORDS,
                _max_control_nesting,
                _preprocessor_lines,
                _estimate_cyclomatic,
                analyze_c_magic_numbers,
            )
            _C_HELPERS_AVAILABLE = True
        except ImportError:
            _C_HELPERS_AVAILABLE = False

# ML smell detector for Java (optional — reports stay rule-based when it is missing)
try:
    from ml_detector import detector as _ml_detector
except ImportError:
    try:
        from .ml_detector import detector as _ml_detector
    except ImportError:
        try:
            from agents.cuqa_agent.src.ml_detector import detector as _ml_detector
        except ImportError:
            _ml_detector = None



# ---------------------------------------------------------------------------
# Code-smell category taxonomy
# ---------------------------------------------------------------------------
#
# Each entry maps a smell type string → (category_name, category_priority).
# Category priority is independent of per-smell severity and reflects the
# architectural risk class of the group as a whole.
#
# Priority ladder:
#   critical — structural/security problems that block safe refactoring
#   medium   — design-quality issues that raise maintenance cost
#   low      — minor cleanliness / redundancy issues
# ---------------------------------------------------------------------------

SMELL_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    # ── Bloaters ─────────────────────────────────────────────────────────────
    # Code that has grown excessively large and is hard to work with.
    "LongMethod":           ("Bloaters", "critical"),
    "LongFunction":         ("Bloaters", "critical"),  # C alias
    "LargeClass":           ("Bloaters", "critical"),
    "TooManyParameters":    ("Bloaters", "critical"),
    "PrimitiveObsession":   ("Bloaters", "critical"),
    "DataClumps":           ("Bloaters", "critical"),

    # ── Object-Orientation Abusers ───────────────────────────────────────────
    # Incorrect or incomplete application of OO principles.
    "SwitchStatements":     ("Object-Orientation Abusers", "medium"),
    "RefusedBequest":       ("Object-Orientation Abusers", "medium"),
    "TemporaryField":       ("Object-Orientation Abusers", "medium"),
    "AlternativeClassesWithDifferentInterfaces":
                            ("Object-Orientation Abusers", "medium"),

    # ── Change Preventers ────────────────────────────────────────────────────
    # Smells that make it hard to change one thing without changing others.
    "DuplicateCode":                        ("Change Preventers", "critical"),
    "DivergentChange":                      ("Change Preventers", "critical"),
    "ShotgunSurgery":                       ("Change Preventers", "critical"),
    "ParallelInheritanceHierarchies":       ("Change Preventers", "critical"),

    # ── Dispensables ─────────────────────────────────────────────────────────
    # Pointless things whose absence would make the code cleaner.
    "DeadCode":             ("Dispensables", "low"),
    "UnreachableCode":      ("Dispensables", "low"),
    "UnusedVariable":       ("Dispensables", "low"),
    "LazyClass":            ("Dispensables", "low"),
    "Comments":             ("Dispensables", "low"),
    "SpeculativeGenerality":("Dispensables", "low"),
    "DataClass":            ("Dispensables", "low"),

    # ── Couplers ─────────────────────────────────────────────────────────────
    # Smells that cause excessive coupling between classes/modules.
    "FeatureEnvy":          ("Couplers", "medium"),
    "InappropriateIntimacy":("Couplers", "medium"),
    "MessageChains":        ("Couplers", "medium"),
    "MiddleMan":            ("Couplers", "medium"),

    # ── Security / Language-Specific ─────────────────────────────────────────
    # Language-level or security-sensitive smells.
    "UnsafeFunctionUsage":  ("Security / Language-Specific", "critical"),
    "DeepNesting":          ("Security / Language-Specific", "critical"),
    "GlobalVariable":       ("Security / Language-Specific", "medium"),
    "LargeHeaderFile":      ("Security / Language-Specific", "medium"),
    "BareExcept":           ("Security / Language-Specific", "medium"),
    "MagicNumber":          ("Security / Language-Specific", "low"),
}

# Ordered list of all canonical category names (for stable output ordering)
_CATEGORY_ORDER: list[str] = [
    "Bloaters",
    "Object-Orientation Abusers",
    "Change Preventers",
    "Dispensables",
    "Couplers",
    "Security / Language-Specific",
    "Uncategorized",
]

# Priority for categories not covered by any mapped smell
_CATEGORY_PRIORITY_DEFAULTS: dict[str, str] = {
    "Bloaters":                        "critical",
    "Object-Orientation Abusers":      "medium",
    "Change Preventers":               "critical",
    "Dispensables":                    "low",
    "Couplers":                        "medium",
    "Security / Language-Specific":    "critical",
    "Uncategorized":                   "low",
}


# ---------------------------------------------------------------------------
# Python analysis helpers  (FIX-04, FIX-06, FIX-09, FIX-10)
# ---------------------------------------------------------------------------

def _estimate_cc(func_node: pyast.AST) -> int:
    """Estimate cyclomatic complexity of a Python function/method node.

    FIX-04: used to attach cyclomatic_complexity to every function smell
    and to the SwitchStatements smell (FIX-06).
    """
    cc = 1
    for n in pyast.walk(func_node):
        if isinstance(n, (pyast.If, pyast.For, pyast.While,
                          pyast.ExceptHandler, pyast.BoolOp)):
            cc += 1
    return cc


def _longest_elif_chain(func_node: pyast.AST) -> int:
    """Number of elif branches in the longest single if/elif chain of a function.

    FIX-06: an elif in Python AST is an If node that is the only element of
    another If node's orelse list. Separate if/elif statements are separate
    chains and are not added together.
    """
    links = {
        id(n.orelse[0]) for n in pyast.walk(func_node)
        if isinstance(n, pyast.If) and len(n.orelse) == 1 and isinstance(n.orelse[0], pyast.If)
    }
    longest = 0
    for n in pyast.walk(func_node):
        if isinstance(n, pyast.If) and id(n) not in links:  # head of a chain
            count, current = 0, n
            while len(current.orelse) == 1 and isinstance(current.orelse[0], pyast.If):
                count += 1
                current = current.orelse[0]
            longest = max(longest, count)
    return longest


# String / bytes methods that return a transformed copy of the same value
_VALUE_METHODS = {
    "replace", "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "title",
    "capitalize", "split", "rsplit", "splitlines", "join", "format", "encode",
    "decode", "removeprefix", "removesuffix", "zfill", "ljust", "rjust", "center",
}


def _expr_root(node: pyast.AST | None) -> str | None:
    """Leftmost name of a dotted expression (`cst.CSTTransformer` -> 'cst')."""
    while isinstance(node, (pyast.Attribute, pyast.Call, pyast.Subscript)):
        node = node.func if isinstance(node, pyast.Call) else node.value
    return node.id if isinstance(node, pyast.Name) else None


def _chain_depth(node: pyast.expr, namespace_roots: set[str] | frozenset = frozenset()) -> int:  # type: ignore[type-arg]
    """Return the depth of an attribute/call chain starting at *node*.

    FIX-10: e.g. a.b().c().d() has depth 3.
    A chain that starts at self/cls does not count its first hop (the object's own
    field). A chain that starts at an imported module or class only counts the hops
    after its first call, because walking a namespace (os.path.join) is not a
    message chain.
    """
    steps: list[str] = []
    current = node
    while isinstance(current, (pyast.Attribute, pyast.Call)):
        if isinstance(current, pyast.Attribute):
            # str / bytes transformations return a new value of the same kind:
            # a pipeline, not a walk through other objects
            steps.append("value" if current.attr in _VALUE_METHODS else "attr")
            current = current.value
        else:  # Call
            steps.append("call")
            current = current.func
    steps.reverse()  # root first
    depth = steps.count("attr")
    root = current.id if isinstance(current, pyast.Name) else None
    if root in ("self", "cls"):
        return max(0, depth - 1)
    if root in namespace_roots:
        if "call" not in steps:
            return 0
        return steps[steps.index("call"):].count("attr")
    return depth


def _normalize_function_body(func_node: pyast.FunctionDef | pyast.AsyncFunctionDef) -> str:
    """Return a normalised structural fingerprint of a function body.

    FIX-09: used for duplicate-body detection (ignores names/values).
    """
    return "|".join(type(n).__name__ for n in pyast.walk(func_node))


def _statement_count(func_node: pyast.AST) -> int:
    """Statements inside a function, not counting its docstring."""
    count = 0
    for n in pyast.walk(func_node):
        if n is func_node or not isinstance(n, pyast.stmt):
            continue
        if isinstance(n, pyast.Expr) and isinstance(n.value, pyast.Constant) and isinstance(n.value.value, str):
            continue
        count += 1
    return count


def _walk_local(node: pyast.AST):
    """Like ast.walk, but never enters nested function, class or lambda definitions,
    which are analysed on their own. Visits in the same breadth-first order as ast.walk."""
    todo = deque(pyast.iter_child_nodes(node))
    while todo:
        n = todo.popleft()
        if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef, pyast.Lambda)):
            continue
        yield n
        todo.extend(pyast.iter_child_nodes(n))


def _imported_names(tree: pyast.AST) -> set[str]:
    """Names bound by import statements: modules, aliases and imported symbols."""
    names: set[str] = set()
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, pyast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _expr_name(node: pyast.AST | None) -> str | None:
    """Last name of a Name / Attribute / Call / Subscript expression (`a.b.C()` -> 'C')."""
    if isinstance(node, pyast.Call):
        node = node.func
    if isinstance(node, pyast.Subscript):
        node = node.value
    if isinstance(node, pyast.Name):
        return node.id
    if isinstance(node, pyast.Attribute):
        return node.attr
    return None


# ---------------------------------------------------------------------------
# Java analysis helpers  (FIX-12, FIX-14)
# ---------------------------------------------------------------------------

def _java_method_end_line(source_lines: list[str], start_line: int) -> int:
    """Scan forward from *start_line* (1-indexed) and return the line where
    the Java method's closing brace appears.

    FIX-12: needed to compute method LOC for Java.
    """
    depth = 0
    found_open = False
    for i, line in enumerate(source_lines[start_line - 1:], start=start_line):
        depth += line.count("{") - line.count("}")
        if depth > 0:
            found_open = True
        if found_open and depth <= 0:
            return i
    return start_line  # fallback: single-line declaration


# FIX-14: regex for Java branch keywords used in CC estimation
_JAVA_BRANCH_RE = re.compile(
    r'\b(if|for|while|switch|catch)\b|(\&\&|\|\|)',
    re.MULTILINE,
)


def _java_cc(method_body: str) -> int:
    """Estimate cyclomatic complexity of a Java method body string.

    FIX-14: CC = 1 + number of branch points.
    """
    return 1 + len(_JAVA_BRANCH_RE.findall(method_body))


# FIX-13: values never reported as Java magic numbers
_JAVA_SAFE_VALUES = {0, 1, 2, -1}
_JAVA_RELATIONAL_OPS = {"==", "!=", "<", ">", "<=", ">="}

# Unit conversion factors (ms -> s, s -> min, ...) explain themselves next to * / %.
# Kept in step with c_ast_parser._UNIT_FACTORS.
_UNIT_FACTORS = {60, 1000, 3600, 86400, 1000000, 1000000000}


# ---------------------------------------------------------------------------
# Repository-wide name index (for cross-file dead code detection)
# ---------------------------------------------------------------------------

def build_repo_name_index(sources: list[tuple[str, str]]) -> set[str]:
    """Build a set of every name *referenced* (not defined) across the entire
    Python repository.

    Used by the dead-code detector: if a function/class name appears anywhere
    in this index, it is live and must NOT be flagged as dead code — even if
    it is never called within its own file.

    Algorithm
    ---------
    For every Python source file passed in ``sources``:
    1. Parse with ``ast.parse``; skip files that fail.
    2. Walk the resulting AST and collect:
       - Every ``Name`` node whose context is ``Load`` (a plain reference).
       - Every ``Attribute.attr`` string (handles ``obj.method()`` calls and
         ``module.ClassName`` accesses).
       - Every aliased import target (``import X as Y`` → add ``X`` and ``Y``;
         ``from M import F as G`` → add ``F`` and ``G``).
    3. Union all collected sets.

    The resulting ``set[str]`` is compared against defined function/class names
    when deciding whether to emit a ``DeadCode`` smell.  Presence in the index
    means the name is referenced somewhere in the repo → not dead.
    """
    live: set[str] = set()
    for _filename, src in sources:
        try:
            tree = pyast.parse(src)
        except SyntaxError:
            continue
        for node in pyast.walk(tree):
            # Plain name references (Load context = actually used, not defined)
            if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
                live.add(node.id)
            # Attribute accesses: obj.method, module.Class, etc.
            elif isinstance(node, pyast.Attribute):
                live.add(node.attr)
            # import X / import X as Y  →  both X and Y are "known alive"
            elif isinstance(node, pyast.Import):
                for alias in node.names:
                    live.add(alias.name.split(".")[0])   # top-level package
                    if alias.asname:
                        live.add(alias.asname)
            # from M import F / from M import F as G
            elif isinstance(node, pyast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        live.add(alias.name)
                    if alias.asname:
                        live.add(alias.asname)
    return live


# ---------------------------------------------------------------------------
# Unreachable-code & unused-variable helpers  (pure functions, no side-effects)
# ---------------------------------------------------------------------------

def _detect_unreachable_code(
    func_node: pyast.FunctionDef | pyast.AsyncFunctionDef,
) -> list[tuple[int, str]]:
    """Detect statements that can never execute because an earlier sibling
    terminates control flow unconditionally.

    Scanned statement lists
    -----------------------
    - The function body itself
    - The ``body`` / ``orelse`` / ``finalbody`` / ``handlers`` of nested
      ``if``, ``for``, ``while``, ``try``, and ``with`` blocks inside the
      function — detected one level deep (sufficient for most real smells).

    Terminal nodes
    --------------
    ``Return``, ``Raise``, ``Break``, ``Continue`` — each makes every
    subsequent sibling in the **same block** unreachable.

    Returns
    -------
    List of ``(lineno, node_type_label)`` for every unreachable statement.
    """
    TERMINALS = (pyast.Return, pyast.Raise, pyast.Break, pyast.Continue)

    def _scan_block(stmts: list) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        terminal_seen = False
        for stmt in stmts:
            if terminal_seen:
                found.append((stmt.lineno, type(stmt).__name__))
            elif isinstance(stmt, TERMINALS):
                terminal_seen = True
        return found

    results: list[tuple[int, str]] = []

    # Top-level function body
    results.extend(_scan_block(func_node.body))

    # Nested blocks (if/for/while/with/try bodies, except handlers, match cases).
    # Nested functions and classes are skipped: they are reported on their own.
    for stmt in _walk_local(func_node):
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(stmt, attr, None)
            if isinstance(block, list) and block:
                results.extend(_scan_block(block))

    results.sort()
    return results


def _detect_unused_variables(
    func_node: pyast.FunctionDef | pyast.AsyncFunctionDef,
) -> list[tuple[int, str]]:
    """Detect local variables that are assigned inside *func_node* but never
    subsequently read within that same function.

    What counts as a store (definition)
    ------------------------------------
    - ``Name`` with ``Store`` context (plain ``x = ...``)
    - ``AnnAssign`` with a ``Name`` target (``x: int = ...``)
    - Loop variables in ``For`` statements
    - ``NamedExpr`` walrus operator targets (``y := expr``)

    What counts as a load (use)
    ---------------------------
    - ``Name`` with ``Load`` context anywhere inside the function

    Exclusions
    ----------
    - Names equal to ``"_"`` (conventional throwaway)
    - Names starting with ``"__"`` (dunder)
    - Function parameters (already declared by the signature)
    - Names used in augmented assignment (``x += 1``) — the ``AugAssign``
      target appears as ``Store`` in CPython's AST but semantically the
      variable must already have a value, so it is *read first*.
    - Names declared ``global`` / ``nonlocal`` — writing them changes state
      outside the function, so they are not unused locals.
    - Assignments inside nested functions — those are checked on their own.

    Returns
    -------
    List of ``(lineno, variable_name)`` for each unused local.
    """
    outer_scope: set[str] = set()
    for node in _walk_local(func_node):
        if isinstance(node, (pyast.Global, pyast.Nonlocal)):
            outer_scope.update(node.names)

    # Collect parameter names — these are not "unused variables"
    param_names: set[str] = set()
    for arg in (
        func_node.args.args
        + func_node.args.posonlyargs
        + func_node.args.kwonlyargs
        + ([func_node.args.vararg] if func_node.args.vararg else [])
        + ([func_node.args.kwarg] if func_node.args.kwarg else [])
    ):
        param_names.add(arg.arg)

    # Augmented-assignment targets (x += 1): the variable must exist, treat as load
    aug_names: set[str] = set()
    for node in pyast.walk(func_node):
        if isinstance(node, pyast.AugAssign) and isinstance(node.target, pyast.Name):
            aug_names.add(node.target.id)

    # Bare annotations (x: int  with NO value) generate a Name/Store node in
    # pyast.walk but are NOT real assignments — exclude them from the store set.
    bare_ann_names: set[str] = set()
    for node in pyast.walk(func_node):
        if (
            isinstance(node, pyast.AnnAssign)
            and isinstance(node.target, pyast.Name)
            and node.value is None          # bare annotation, no assignment
        ):
            bare_ann_names.add(node.target.id)

    stored: dict[str, int] = {}   # name → first-assignment lineno
    # Reads anywhere, nested functions and lambdas included (closures read outer locals)
    loaded: set[str] = {
        node.id for node in pyast.walk(func_node)
        if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load)
    }

    for node in _walk_local(func_node):
        # Plain assignment target
        if isinstance(node, pyast.Name):
            if isinstance(node.ctx, pyast.Store):
                name = node.id
                if (
                    name != "_"
                    and not name.startswith("__")
                    and name not in param_names
                    and name not in aug_names
                    and name not in bare_ann_names  # exclude bare type hints
                    and name not in stored          # keep the first assignment lineno
                ):
                    stored[name] = node.lineno
        # Annotated assignment:  x: int = 5
        elif isinstance(node, pyast.AnnAssign) and isinstance(node.target, pyast.Name):
            name = node.target.id
            if (
                node.value is not None   # skip bare annotations like  x: int
                and name != "_"
                and not name.startswith("__")
                and name not in param_names
                and name not in aug_names
                and name not in stored
            ):
                stored[name] = node.target.lineno
        # Walrus operator  (y := expr)
        elif isinstance(node, pyast.NamedExpr):
            name = node.target.id
            if name != "_" and not name.startswith("__") and name not in param_names:
                stored.setdefault(name, node.target.lineno)
        # For loop variable
        elif isinstance(node, pyast.For) and isinstance(node.target, pyast.Name):
            name = node.target.id
            if name != "_" and not name.startswith("__") and name not in param_names:
                stored.setdefault(name, node.target.lineno)

    unused = [
        (lineno, name) for name, lineno in stored.items()
        if name not in loaded and name not in outer_scope
    ]
    unused.sort(key=lambda t: t[0])
    return unused


_CONST_PREFIXES = ("MAX_", "MIN_", "DEFAULT_", "CONST_", "NUM_", "TIMEOUT_", "PORT_", "LIMIT_", "TOTAL_")


def _is_const_like_name(name: str | None) -> bool:
    return bool(name) and (name.isupper() or name.startswith(_CONST_PREFIXES))


def _count_code_lines(source_lines: list[str], start_line: int, end_line: int) -> int:
    """Count non-blank and non-comment lines of code within [start_line, end_line] (1-indexed)."""
    if not source_lines:
        return max(0, end_line - start_line)
    count = 0
    in_multiline = False
    for i in range(max(0, start_line - 1), min(len(source_lines), end_line)):
        line = source_lines[i].strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith(('"""', "'''")):
            if line.endswith(('"""', "'''")) and len(line) > 3:
                continue
            in_multiline = not in_multiline
            continue
        if in_multiline:
            if line.endswith(('"""', "'''")):
                in_multiline = False
            continue
        count += 1
    return count


def _python_literal_is_not_magic(node: pyast.Constant) -> bool:
    """Return True when a numeric literal is not a magic number in its context.

    Not magic: constant definitions (MAX_RETRY = 5, including UPPER_CASE
    collections), class attributes and dataclass field defaults, parameter
    defaults (def f(timeout=30)), keyword arguments (connect(retries=3)),
    entries of list/tuple/set/dict displays (data tables), assert statements,
    and unit conversion factors next to * / % (seconds * 1000).
    """
    parent = getattr(node, "parent", None)
    in_collection = False
    while isinstance(parent, (pyast.UnaryOp, pyast.BinOp, pyast.Tuple, pyast.List, pyast.Set, pyast.Dict)):
        if isinstance(parent, (pyast.Tuple, pyast.List, pyast.Set, pyast.Dict)):
            in_collection = True
        parent = getattr(parent, "parent", None)

    direct = getattr(node, "parent", None)
    if isinstance(direct, pyast.UnaryOp):
        direct = getattr(direct, "parent", None)
    if abs(node.value) in _UNIT_FACTORS:
        if isinstance(direct, pyast.BinOp) and isinstance(direct.op, (pyast.Mult, pyast.Div, pyast.FloorDiv, pyast.Mod)):
            return True
        if isinstance(direct, pyast.AugAssign) and isinstance(direct.op, (pyast.Mult, pyast.Div, pyast.FloorDiv, pyast.Mod)):
            return True

    if in_collection or isinstance(parent, (pyast.keyword, pyast.arguments)):
        return True

    if isinstance(parent, (pyast.Assign, pyast.AnnAssign)):
        if isinstance(getattr(parent, "parent", None), pyast.ClassDef):
            return True  # class attribute / field default: the attribute names it
        targets = parent.targets if isinstance(parent, pyast.Assign) else [parent.target]
        for target in targets:
            elts = target.elts if isinstance(target, (pyast.Tuple, pyast.List)) else [target]
            for elt in elts:
                name = elt.id if isinstance(elt, pyast.Name) else getattr(elt, "attr", "")
                if _is_const_like_name(name):
                    return True

    ancestor = parent
    while ancestor is not None and not isinstance(ancestor, pyast.stmt):
        ancestor = getattr(ancestor, "parent", None)
    return isinstance(ancestor, pyast.Assert)


# ---------------------------------------------------------------------------
# Code smell detectors (Python)
# ---------------------------------------------------------------------------

# Base classes and decorators of classes that are small by design (not LazyClass)
_SMALL_BY_DESIGN_BASES = {
    "Exception", "BaseException", "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag",
    "NamedTuple", "TypedDict", "Protocol", "BaseModel", "BaseSettings", "ABC",
}
_RECORD_DECORATORS = {"dataclass", "define", "frozen", "mutable", "attrs", "s"}

# Underscore attributes that are public API by convention (namedtuple, Django models)
_PUBLIC_UNDERSCORE_ATTRS = {"_replace", "_asdict", "_fields", "_make", "_field_defaults", "_meta"}


class _PythonSmellVisitor(pyast.NodeVisitor):
    """Walk a Python AST and collect code smells with full entity context."""

    def __init__(self):
        self.smells: list[dict] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []  # tracks enclosing function names
        self.source_lines: list[str] = []
        self.import_names: set[str] = set()     # modules / symbols bound by imports
        self.subclassed_names: set[str] = set() # every name used as a base class in the file
        self.repo_names: set[str] | None = None # names referenced anywhere in the repository

    def _add(self, smell_type: str, message: str, line: int | None,
             severity: str = "medium", entity: str | None = None, **extra: Any):
        """Append a smell dict — entity is always resolved.

        **extra kwargs are merged into the smell dict to carry optional
        RDP-facing fields (parameter_count, start_line, end_line, …).
        """
        resolved = (
            entity
            or (self._func_stack[-1] if self._func_stack else None)
            or (self._class_stack[-1] if self._class_stack else None)
        )
        smell: dict = {
            "type": smell_type,
            "message": message,
            "line": line,
            "severity": severity,
            "entity": resolved,
        }
        smell.update(extra)
        self.smells.append(smell)

    # ── Long method / Too many parameters ────────────────────────────────────
    def visit_FunctionDef(self, node: pyast.FunctionDef):
        self._func_stack.append(node.name)

        end_line = node.end_lineno or node.lineno
        code_lines = _count_code_lines(self.source_lines, node.lineno + 1, end_line) if self.source_lines else ((node.end_lineno or node.lineno) - node.lineno)
        # Exclude self/cls from parameter count (FIX-01); keyword-only parameters count too
        real_args = [
            a for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            if a.arg not in ("self", "cls")
        ]
        # FIX-04: compute CC once, reuse for all smells on this function
        cc = _estimate_cc(node)

        if code_lines > 30:
            # FIX-01: parameter_count
            # FIX-02: start_line / end_line
            # FIX-04: cyclomatic_complexity
            self._add(
                "LongMethod",
                f"Function '{node.name}' has {code_lines} lines of code (>30)",
                node.lineno, "high", entity=node.name,
                parameter_count=len(real_args),
                start_line=node.lineno,
                end_line=end_line,
                cyclomatic_complexity=cc,
            )

        if len(real_args) > 5:
            # FIX-01: parameter_count
            # FIX-02: start_line / end_line
            self._add(
                "TooManyParameters",
                f"Function '{node.name}' has {len(real_args)} parameters (>5)",
                node.lineno, "medium", entity=node.name,
                parameter_count=len(real_args),
                start_line=node.lineno,
                end_line=end_line,
            )

        # FIX-06: SwitchStatements — one if/elif chain with >= 4 elif branches
        elif_count = _longest_elif_chain(node)
        if elif_count >= 4:
            self._add(
                "SwitchStatements",
                f"Function '{node.name}' has an if/elif chain with {elif_count} elif branches (>=4) — "
                "consider Replace Conditional with Polymorphism",
                node.lineno, "medium", entity=node.name,
                cyclomatic_complexity=cc,
                start_line=node.lineno,
                end_line=end_line,
            )

        # FIX-10: MessageChains — detect deep attribute/call chains (>= 3)
        for child in _walk_local(node):
            if isinstance(child, (pyast.Attribute, pyast.Call)):
                depth = _chain_depth(child, self.import_names)
                if depth >= 3:
                    self._add(
                        "MessageChains",
                        f"Method chain of depth {depth} detected in '{node.name}' — "
                        "consider Hide Delegate",
                        getattr(child, "lineno", node.lineno), "low",
                        entity=node.name,
                        chain_length=depth,
                    )
                    break  # one report per function is sufficient

        # ── UnreachableCode — real dead statements after return/raise ─────────
        unreachable = _detect_unreachable_code(node)
        for lineno, label in unreachable:
            self._add(
                "UnreachableCode",
                f"Unreachable statement '{label}' after unconditional return/raise "
                f"in '{node.name}' — consider Remove Dead Code",
                lineno, "low", entity=node.name,
                start_line=node.lineno,
                end_line=end_line,
            )

        # ── UnusedVariable — assigned but never read locals ───────────────────
        unused_vars = _detect_unused_variables(node)
        for lineno, varname in unused_vars:
            self._add(
                "UnusedVariable",
                f"Variable '{varname}' is assigned in '{node.name}' but never used — "
                "consider Remove Dead Code or inline the value",
                lineno, "low", entity=node.name,
                variable_name=varname,
            )

        self.generic_visit(node)
        self._func_stack.pop()

    # pyrefly: ignore [bad-override]
    visit_AsyncFunctionDef = visit_FunctionDef

    # ── Large class / Lazy class / Phase 4 class-level detectors ─────────────
    def visit_ClassDef(self, node: pyast.ClassDef):
        self._class_stack.append(node.name)
        methods = [n for n in node.body if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))]
        method_count = len(methods)
        class_loc = (node.end_lineno or node.lineno) - node.lineno
        base_names = [b for b in (_expr_name(base) for base in node.bases) if b]
        decorator_names = {_expr_name(d) for d in node.decorator_list}
        is_abstract = (
            any(b in ("ABC", "ABCMeta") for b in base_names)
            or any(kw.arg == "metaclass" and _expr_name(kw.value) == "ABCMeta" for kw in node.keywords)
            or any("abstractmethod" in {_expr_name(d) for d in m.decorator_list} for m in methods)
            or bool(re.match(r"(?:Abstract|Base)[A-Z]", node.name))
            or node.name.endswith("Mixin")
        )

        if method_count > 15:
            # FIX-03: method_count
            self._add(
                "LargeClass",
                f"Class '{node.name}' has {method_count} methods (>15)",
                node.lineno, "high", entity=node.name,
                method_count=method_count,
            )

        # FIX-11: LazyClass — near-empty class (≤ 2 methods, < 30 lines).
        # Classes that are small by design are not lazy: exceptions, enums,
        # dataclasses / attrs / pydantic models, typed records, protocols,
        # abstract interfaces and test classes.
        special_purpose = (
            is_abstract
            or node.name.startswith("Test")
            or any(_expr_root(base) in self.import_names for base in node.bases)
            or any(b in _SMALL_BY_DESIGN_BASES or b.endswith(("Error", "Exception", "Warning"))
                   for b in base_names)
            or bool(decorator_names & _RECORD_DECORATORS)
        )
        if method_count <= 2 and class_loc < 30 and not special_purpose:
            self._add(
                "LazyClass",
                f"Class '{node.name}' has only {method_count} methods and "
                f"{class_loc} lines — consider Inline Class or Collapse Hierarchy",
                node.lineno, "low", entity=node.name,
                method_count=method_count,
            )

        # FIX-18: PrimitiveObsession — 4+ primitive-annotated instance fields
        # (class-level annotations and `self.x: int = ...`; method locals do not count)
        primitive_types = {"str", "int", "float", "bool"}
        field_annotations = [n for n in node.body if isinstance(n, pyast.AnnAssign)]
        for method in methods:
            field_annotations.extend(
                n for n in pyast.walk(method)
                if isinstance(n, pyast.AnnAssign)
                and isinstance(n.target, pyast.Attribute)
                and isinstance(n.target.value, pyast.Name)
                and n.target.value.id == "self"
            )
        primitive_count = sum(
            1 for child in field_annotations if _expr_name(child.annotation) in primitive_types
        )
        if primitive_count >= 4:
            self._add(
                "PrimitiveObsession",
                f"Class '{node.name}' has {primitive_count} primitive-typed fields — "
                "consider Replace Data Value with Object or Introduce Parameter Object",
                node.lineno, "medium", entity=node.name,
                primitive_field_count=primitive_count,
            )

        # FIX-19: InappropriateIntimacy — access to a private attribute of another object.
        # Not counted: dunder attributes (obj.__dict__), the public namedtuple API
        # (_replace, _asdict, ...), Django's _meta, private names of imported
        # modules (sys._getframe), and another instance of the same class (`other`
        # in __eq__, or a parameter annotated with this class).
        intimacy_found = False
        for item in node.body:
            if intimacy_found:
                break
            same_class_params: set[str] = set()
            if isinstance(item, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                is_dunder = item.name.startswith("__") and item.name.endswith("__")
                for arg in item.args.posonlyargs + item.args.args + item.args.kwonlyargs:
                    ann = arg.annotation
                    ann_name = ann.value if isinstance(ann, pyast.Constant) else _expr_name(ann)
                    if is_dunder or ann_name == node.name:
                        same_class_params.add(arg.arg)
            for child in pyast.walk(item):
                if not (isinstance(child, pyast.Attribute) and isinstance(child.value, pyast.Name)):
                    continue
                attr, owner = child.attr, child.value.id
                if (
                    not attr.startswith("_")
                    or (attr.startswith("__") and attr.endswith("__"))
                    or attr in _PUBLIC_UNDERSCORE_ATTRS
                    or owner in ("self", "cls")
                    or owner in self.import_names
                    or owner in same_class_params
                    or owner == node.name
                ):
                    continue
                self._add(
                    "InappropriateIntimacy",
                    f"Class '{node.name}' accesses private attribute "
                    f"'{attr}' of '{owner}' — consider Move Method or "
                    "Introduce Facade",
                    getattr(child, "lineno", node.lineno), "medium",
                    entity=node.name,
                )
                intimacy_found = True  # one report per class
                break

        # FIX-20: SpeculativeGenerality — an abstract class / base / mixin that
        # nothing extends. A class that merely inherits from a base (e.g. a
        # pydantic BaseModel) is concrete and is not reported.
        if is_abstract:
            extended = node.name in self.subclassed_names or (
                self.repo_names is not None and node.name in self.repo_names
            )
            if not extended:
                scope = "this repository" if self.repo_names is not None else "this file"
                self._add(
                    "SpeculativeGenerality",
                    f"Abstract class '{node.name}' has no subclasses in {scope} — "
                    "consider Collapse Hierarchy (possible Speculative Generality)",
                    node.lineno, "low", entity=node.name,
                )

        self.generic_visit(node)
        self._class_stack.pop()

    # ── Magic numbers (entity resolved via stack) ─────────────────────────────
    def _extract_py_variable(self, node: pyast.AST) -> str | None:
        """Extract variable name from an expression node in Python AST."""
        if isinstance(node, pyast.Name):
            return node.id
        elif isinstance(node, pyast.Attribute):
            if isinstance(node.value, pyast.Name) and node.value.id == "self":
                return node.attr
            val_id = self._extract_py_variable(node.value)
            return f"{val_id}.{node.attr}" if val_id else node.attr
        elif isinstance(node, pyast.Call):
            for arg in node.args:
                v = self._extract_py_variable(arg)
                if v:
                    return v
            if isinstance(node.func, pyast.Name):
                return node.func.id
        elif isinstance(node, pyast.UnaryOp):
            return self._extract_py_variable(node.operand)
        elif isinstance(node, pyast.BinOp):
            return self._extract_py_variable(node.left) or self._extract_py_variable(node.right)
        return None

    def visit_Constant(self, node: pyast.Constant):
        if (
            isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
            and node.value not in (0, 1, -1, 2)
        ):
            if _python_literal_is_not_magic(node):
                return  # Named or data value -> NOT a magic number

            parent = getattr(node, "parent", None)
            while parent and isinstance(parent, (pyast.UnaryOp, pyast.BinOp)):
                parent = getattr(parent, "parent", None)

            var_context = None
            if isinstance(parent, pyast.Compare):
                comp_nodes = [parent.left] + list(parent.comparators)
                for comp_node in comp_nodes:
                    if comp_node is not node and not (isinstance(comp_node, pyast.Constant) and comp_node.value == node.value):
                        v = self._extract_py_variable(comp_node)
                        if v:
                            var_context = v
                            break

            if var_context:
                msg = f"Magic number {node.value} compared to variable '{var_context}'"
                self._add(
                    "MagicNumber",
                    msg,
                    getattr(node, "lineno", None), "low",
                    details=msg,
                    variable_context=var_context,
                )
            else:
                msg = f"Magic number {node.value}"
                self._add(
                    "MagicNumber",
                    msg,
                    getattr(node, "lineno", None), "low",
                    details=msg,
                )

    # ── Bare except (entity resolved via stack) ───────────────────────────────
    def visit_ExceptHandler(self, node: pyast.ExceptHandler):
        if node.type is None:
            self._add(
                "BareExcept",
                "Bare 'except:' clause catches all exceptions",
                node.lineno, "medium",
            )
        self.generic_visit(node)


def _analyze_python_smells(
    source: str,
    repo_ref_index: set[str] | None = None,
) -> list[dict]:
    """Analyse a single Python source file for code smells.

    Parameters
    ----------
    source:
        Raw source text of the file.
    repo_ref_index:
        Optional set of all names referenced anywhere in the repository
        (built by :func:`build_repo_name_index`).  When provided, dead-code
        detection uses repository-wide visibility: a function/class is only
        flagged if it is absent from **both** the current file's own AST walk
        *and* this cross-file index.  When ``None``, the check is restricted
        to the current file only (single-file analysis mode).
    """
    try:
        tree = pyast.parse(source)
        for parent in pyast.walk(tree):
            for child in pyast.iter_child_nodes(parent):
                try:
                    setattr(child, "parent", parent)
                except (AttributeError, TypeError):
                    pass
    except SyntaxError:
        return []

    import_names = _imported_names(tree)
    visitor = _PythonSmellVisitor()
    visitor.source_lines = source.splitlines()
    visitor.import_names = import_names
    visitor.subclassed_names = {
        name for node in pyast.walk(tree) if isinstance(node, pyast.ClassDef)
        for name in (_expr_name(base) for base in node.bases) if name
    }
    visitor.repo_names = repo_ref_index
    visitor.visit(tree)
    smells = list(visitor.smells)

    # ── DeadCode — defined-but-unreferenced functions/classes ─────────────────
    #
    # REPOSITORY-WIDE mode (repo_ref_index provided)
    # -----------------------------------------------
    # A function/class is dead only when its name is absent from:
    #   1. The current file's AST (same-file call sites), AND
    #   2. The repository-wide name index (cross-file imports/usages).
    # This eliminates false positives where a function is exported and
    # consumed by another module — the most common cause of spurious
    # re-detection after refactoring.
    #
    # SINGLE-FILE mode (repo_ref_index is None)
    # ------------------------------------------
    # Falls back to checking only within the current file.  The message
    # explicitly says "within this file" to avoid misleading the developer.
    #
    # Never checked: public methods (they can be called through inheritance,
    # a framework or another module, which static analysis cannot see) and
    # decorated definitions (routes, fixtures, properties, CLI commands are
    # registered by their decorator, not called by name).
    try:
        defined: dict[str, int] = {}
        for node in pyast.walk(tree):
            if not isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
                continue
            if node.decorator_list:
                continue
            if isinstance(getattr(node, "parent", None), pyast.ClassDef):
                is_private = node.name.startswith("_") and not node.name.startswith("__")
                if not is_private:
                    continue
            defined.setdefault(node.name, node.lineno)

        # Build the set of names actually used inside THIS file: Load-context
        # names (calls, references), attribute names (self._helper(), obj.run)
        # and the strings listed in __all__.
        locally_loaded: set[str] = set()
        for node in pyast.walk(tree):
            if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
                locally_loaded.add(node.id)
            elif isinstance(node, pyast.Attribute):
                locally_loaded.add(node.attr)
            elif (
                isinstance(node, pyast.Assign)
                and any(isinstance(t, pyast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (pyast.List, pyast.Tuple))
            ):
                locally_loaded.update(
                    e.value for e in node.value.elts
                    if isinstance(e, pyast.Constant) and isinstance(e.value, str)
                )

        # Names that are "definitely alive": present in local load set OR repo index
        alive: set[str] = locally_loaded
        if repo_ref_index is not None:
            alive = locally_loaded | repo_ref_index

        # Determine scope label for the smell message
        scope_label = "this repository" if repo_ref_index is not None else "this file"

        # Well-known entry-point / framework names that must never be flagged
        _SAFE_NAMES = {
            "main", "setup", "teardown", "run", "test", "app",
            "handler", "lambda_handler", "wsgi", "asgi",
        }

        for name, lineno in defined.items():
            # Skip dunder names — they are called via protocol, not by name
            if name.startswith("__"):
                continue
            # Skip well-known framework entry points
            if name in _SAFE_NAMES or name.startswith(("test_", "Test")):
                continue
            # Only flag if the name is genuinely absent from the live set
            if name not in alive:
                smells.append({
                    "type": "DeadCode",
                    "message": (
                        f"'{name}' is defined but never referenced in "
                        f"{scope_label} — consider Remove Dead Code"
                    ),
                    "line": lineno,
                    "severity": "low",
                    "entity": name,
                })
    except Exception:
        pass

    # ── FIX-09: DuplicateCode — hash normalised function body structures ───────
    # Bodies with fewer than 3 statements (getters, one-line __init__, stubs)
    # are too small to be a meaningful clone and are not compared.
    try:
        body_hashes: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for node in pyast.walk(tree):
            if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                if _statement_count(node) < 3:
                    continue
                body_str = _normalize_function_body(node)
                h = hashlib.md5(body_str.encode()).hexdigest()
                body_hashes[h].append((node.name, node.lineno))

        reported_keys: set[tuple] = set()
        for h, funcs in body_hashes.items():
            if len(funcs) >= 2:
                key = tuple(sorted(f[0] for f in funcs))
                if key not in reported_keys:
                    reported_keys.add(key)
                    names = [f[0] for f in funcs]
                    smells.append({
                        "type": "DuplicateCode",
                        "message": (
                            f"Functions {names} have structurally identical bodies — "
                            "consider Extract Method or Pull Up Method"
                        ),
                        "line": funcs[0][1],
                        "severity": "medium",
                        "entity": funcs[0][0],
                        "duplicate_group": names,
                    })
    except Exception:
        pass

    # ── FIX-16: FeatureEnvy — a method uses another object's features more than its own ──
    # Only instance methods are checked (a module function has no class to envy
    # from). The envied object must be a parameter or local variable: imported
    # modules (os.path.join, json.dumps) are library calls, not envy. Distinct
    # attribute names are counted, so calling one method five times counts once.
    try:
        for cls in pyast.walk(tree):
            if not isinstance(cls, pyast.ClassDef):
                continue
            for node in cls.body:
                if not isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                    continue
                if not node.args.args or node.args.args[0].arg != "self":
                    continue
                if node.name.startswith(("test_", "visit_", "leave_", "depart_")):
                    continue  # test assertions and AST-visitor callbacks
                local_names = {
                    a.arg for a in node.args.posonlyargs + node.args.args[1:] + node.args.kwonlyargs
                }
                local_names.update(
                    n.id for n in pyast.walk(node)
                    if isinstance(n, pyast.Name) and isinstance(n.ctx, pyast.Store)
                )
                local_names -= import_names
                self_attrs: set[str] = set()
                foreign: dict[str, set[str]] = defaultdict(set)
                for child in pyast.walk(node):
                    if isinstance(child, pyast.Attribute) and isinstance(child.value, pyast.Name):
                        owner = child.value.id
                        if owner == "self":
                            self_attrs.add(child.attr)
                        elif owner in local_names:
                            foreign[owner].add(child.attr)
                if not foreign:
                    continue
                envied, envied_attrs = max(foreign.items(), key=lambda kv: len(kv[1]))
                if len(envied_attrs) >= 3 and len(envied_attrs) > len(self_attrs):
                    smells.append({
                        "type": "FeatureEnvy",
                        "message": (
                            f"Method '{node.name}' uses {len(envied_attrs)} attributes of "
                            f"'{envied}' but only {len(self_attrs)} of its own class — "
                            "consider Move Method"
                        ),
                        "line": node.lineno,
                        "severity": "medium",
                        "entity": node.name,
                        "external_field_accesses": len(envied_attrs),
                        "self_field_accesses": len(self_attrs),
                        "envied_object": envied,
                    })
    except Exception:
        pass

    # ── FIX-17: DataClumps — same 3+ params appearing in multiple functions ──
    try:
        func_param_sets: list[tuple[str, int, frozenset]] = []
        for node in pyast.walk(tree):
            if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                params = frozenset(
                    a.arg for a in node.args.args
                    if a.arg not in ("self", "cls")
                )
                if len(params) >= 3:
                    func_param_sets.append((node.name, node.lineno, params))

        clump_map: dict[frozenset, list[tuple[str, int]]] = defaultdict(list)
        seen_pairs: set[tuple] = set()
        for i, (fn, fl, ps) in enumerate(func_param_sets):
            for j, (ofn, ofl, ops) in enumerate(func_param_sets):
                if i >= j:
                    continue
                if fn == ofn:
                    continue  # same name: an override sharing its parent's signature
                common = ps & ops
                if len(common) >= 3:
                    pair = (min(fn, ofn), max(fn, ofn))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        clump_map[frozenset(common)].append((fn, fl))

        reported_clumps: set[frozenset] = set()
        for clump_params, appearances in clump_map.items():
            if clump_params not in reported_clumps:
                reported_clumps.add(clump_params)
                param_list = sorted(clump_params)
                smells.append({
                    "type": "DataClumps",
                    "message": (
                        f"Parameter group {param_list} appears together in multiple "
                        "functions — consider Introduce Parameter Object"
                    ),
                    "line": appearances[0][1],
                    "severity": "medium",
                    "entity": appearances[0][0],
                    "clump_parameters": param_list,
                })
    except Exception:
        pass

    return smells


def _python_metrics(source: str, filename: str) -> dict:
    lines = source.splitlines()
    blank = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("#"))
    loc = len(lines)
    import_count = 0
    try:
        tree = pyast.parse(source)
        functions = sum(
            1 for n in pyast.walk(tree)
            if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))
        )
        classes = sum(1 for n in pyast.walk(tree) if isinstance(n, pyast.ClassDef))
        # FIX-05: coupling metric — count import statements as proxy
        import_count = sum(
            1 for n in pyast.walk(tree)
            if isinstance(n, (pyast.Import, pyast.ImportFrom))
        )
    except SyntaxError:
        functions = classes = 0
    return {
        "filename": filename,
        "lines_of_code": loc,
        "blank_lines": blank,
        "comment_lines": comment,
        "functions": functions,
        "classes": classes,
        "coupling": import_count,   # FIX-05
    }


# ---------------------------------------------------------------------------
# Code smell detectors (Java - structural heuristics)
# ---------------------------------------------------------------------------

def _java_number_value(raw: str):
    """Value of a decimal Java number literal, or None for strings, chars, booleans,
    null and bit patterns written in hex or binary (masks are not reported)."""
    t = raw.replace("_", "").lower()
    if not t or t[0] not in "0123456789." or t.startswith(("0x", "0b")):
        return None
    t = t.rstrip("lfd")
    try:
        if "." in t or "e" in t:
            return float(t)
        if len(t) > 1 and t.startswith("0"):
            return int(t, 8)
        return int(t)
    except ValueError:
        return None


def _java_literal_is_not_magic(literal, value, ancestors: list) -> bool:
    """
    True when a numeric literal is not a magic number in its context: constant
    definitions (final / UPPER_CASE / interface constants), enum constant arguments,
    annotations, array initializer lists and sizes, case labels, assert statements,
    hashCode() bodies, and unit conversion factors next to * / %.
    """
    tree = javalang.tree
    for anc in ancestors:
        if isinstance(anc, (tree.ConstantDeclaration, tree.EnumConstantDeclaration,
                            tree.Annotation, tree.ElementValuePair,
                            tree.ArrayInitializer, tree.AssertStatement)):
            return True
        if isinstance(anc, (tree.FieldDeclaration, tree.LocalVariableDeclaration)):
            if "final" in (getattr(anc, "modifiers", None) or ()):
                return True
        if isinstance(anc, tree.VariableDeclarator) and _is_const_like_name(getattr(anc, "name", None)):
            return True
        if isinstance(anc, tree.Assignment):
            if _is_const_like_name(getattr(getattr(anc, "expressionl", None), "member", None)):
                return True
        if isinstance(anc, tree.MethodDeclaration) and getattr(anc, "name", None) == "hashCode":
            return True
        if isinstance(anc, tree.SwitchStatementCase):
            if any(c is literal for c in (getattr(anc, "case", None) or [])):
                return True
        if isinstance(anc, tree.ArrayCreator):
            if any(d is literal for d in (getattr(anc, "dimensions", None) or [])):
                return True

    parent = ancestors[-1] if ancestors else None
    if abs(value) in _UNIT_FACTORS:
        if isinstance(parent, tree.BinaryOperation) and getattr(parent, "operator", None) in ("*", "/", "%"):
            return True
        if isinstance(parent, tree.Assignment) and getattr(parent, "type", None) in ("*=", "/=", "%="):
            return True
    return False


def _analyze_java_smells(source: str) -> list[dict]:
    smells = []
    if not JAVALANG_AVAILABLE:
        return smells
    try:
        tree = javalang.parse.parse(source)
    except Exception:
        return smells

    # Comments, strings and char literals blanked (newlines kept) so a brace inside
    # them cannot move the end of a method, and comment lines are not counted as code
    masked = _mask_c_like(source) if _C_HELPERS_AVAILABLE else source
    source_lines = masked.splitlines()

    for _, node in tree:
        # ── FIX-12 + FIX-14: LongMethod for Java (methods and constructors) ──
        if isinstance(node, (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration)):
            method_name = getattr(node, "name", "") or ""
            params = getattr(node, "parameters", []) or []
            start_line = (
                getattr(node.position, "line", None) if node.position else None
            )

            if start_line is not None:
                end_line = _java_method_end_line(source_lines, start_line)
                method_loc = _count_code_lines(source_lines, start_line + 1, end_line)
                method_body = "\n".join(source_lines[start_line - 1:end_line])
                cc = _java_cc(method_body)  # FIX-14

                if method_loc > 30:
                    smells.append({
                        "type": "LongMethod",
                        "message": (
                            f"Method '{method_name}' has {method_loc} lines (>30)"
                        ),
                        "line": start_line,
                        "severity": "high",
                        "entity": method_name,
                        "start_line": start_line,       # FIX-12
                        "end_line": end_line,            # FIX-12
                        "parameter_count": len(params), # FIX-12
                        "cyclomatic_complexity": cc,     # FIX-14
                    })

            # Existing: TooManyParameters for Java
            if len(params) > 5:
                smells.append({
                    "type": "TooManyParameters",
                    "message": (
                        f"Method '{method_name}' has {len(params)} parameters (>5)"
                    ),
                    "line": start_line,
                    "severity": "medium",
                    "entity": method_name,
                    "parameter_count": len(params),
                })

        # Existing: LargeClass for Java — now with FIX-03 method_count
        if isinstance(node, (javalang.tree.ClassDeclaration,
                              javalang.tree.InterfaceDeclaration)):
            class_name = getattr(node, "name", "") or ""
            methods = getattr(node, "methods", []) or []
            if len(methods) > 15:
                line = (
                    getattr(node.position, "line", None) if node.position else None
                )
                smells.append({
                    "type": "LargeClass",
                    "message": (
                        f"Class '{class_name}' has {len(methods)} methods (>15)"
                    ),
                    "line": line,
                    "severity": "high",
                    "entity": class_name,        # ← class name
                    "method_count": len(methods),  # FIX-03 (Java parity)
                })

    # ── FIX-13: MagicNumber for Java ─────────────────────────────────────────
    # Taken from the javalang AST only, so numbers inside strings and comments are
    # never seen and every literal is reported once, on its own line.
    for path, node in tree:
        if not isinstance(node, javalang.tree.Literal):
            continue
        raw = str(getattr(node, "value", "") or "").strip()
        value = _java_number_value(raw)
        if value is None:
            continue
        if "-" in (getattr(node, "prefix_operators", None) or []):
            value, raw = -value, f"-{raw}"
        if value in _JAVA_SAFE_VALUES:
            continue

        ancestors = [p for p in (path or []) if isinstance(p, javalang.tree.Node)]
        if _java_literal_is_not_magic(node, value, ancestors):
            continue

        pos = getattr(node, "position", None)
        line_no = getattr(pos, "line", None) if pos else None
        entity = next(
            (getattr(a, "name", None) for a in reversed(ancestors)
             if isinstance(a, (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration))),
            None,
        )

        parent = ancestors[-1] if ancestors else None
        var_context = None
        if isinstance(parent, javalang.tree.BinaryOperation):
            op = getattr(parent, "operator", getattr(parent, "op", None))
            if op in _JAVA_RELATIONAL_OPS:
                operandl = getattr(parent, "operandl", None)
                operandr = getattr(parent, "operandr", None)
                sibling = operandr if operandl is node else operandl
                if sibling is not None:
                    if isinstance(sibling, javalang.tree.MemberReference):
                        var_context = getattr(sibling, "member", None)
                    elif isinstance(sibling, javalang.tree.VariableDeclarator):
                        var_context = getattr(sibling, "name", None)
                    elif hasattr(sibling, "member") and getattr(sibling, "member"):
                        var_context = getattr(sibling, "member")
                    elif hasattr(sibling, "name") and getattr(sibling, "name"):
                        var_context = getattr(sibling, "name")

        if var_context:
            msg = f"Magic number {raw} compared to variable '{var_context}'"
            smells.append({
                "type": "MagicNumber",
                "message": msg,
                "details": msg,
                "variable_context": var_context,
                "line": line_no,
                "severity": "low",
                "entity": entity,
            })
        else:
            msg = f"Magic number {raw} detected"
            smells.append({
                "type": "MagicNumber",
                "message": msg,
                "details": msg,
                "line": line_no,
                "severity": "low",
                "entity": entity,
            })

    return smells


def _java_metrics(source: str, filename: str) -> dict:
    lines = source.splitlines()
    blank = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("//") or l.strip().startswith("*"))
    loc = len(lines)
    classes = functions = 0
    # FIX-15: coupling metric — count import statements
    import_count = len(re.findall(r'^\s*import\s+', source, re.MULTILINE))
    if JAVALANG_AVAILABLE:
        try:
            tree = javalang.parse.parse(source)
            for _, node in tree:
                if isinstance(node, (javalang.tree.ClassDeclaration,
                                     javalang.tree.InterfaceDeclaration)):
                    classes += 1
                if isinstance(node, javalang.tree.MethodDeclaration):
                    functions += 1
        except Exception:
            pass
    return {
        "filename": filename,
        "lines_of_code": loc,
        "blank_lines": blank,
        "comment_lines": comment,
        "functions": functions,
        "classes": classes,
        "coupling": import_count,   # FIX-15
    }


# ---------------------------------------------------------------------------
# C metrics and smell detection
# ---------------------------------------------------------------------------

def _c_metrics(source: str, filename: str) -> dict:
    """Compute C-specific metrics that extend the base CUQA schema."""
    lines = source.splitlines()
    loc = len(lines)
    blank = sum(1 for ln in lines if not ln.strip())

    # Comment lines: single-line (//) and lines inside /* */ blocks
    clean_no_ml = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.DOTALL)
    comment = sum(
        1 for ln in clean_no_ml.splitlines()
        if ln.strip().startswith("//") or ln.strip().startswith("*")
    )
    # Count lines that were inside /* */ (replaced with newlines above)
    ml_comment_lines = sum(
        m.group(0).count("\n") for m in re.finditer(r"/\*.*?\*/", source, re.DOTALL)
    )
    comment = comment + ml_comment_lines

    functions = 0
    include_count = 0
    global_vars = 0
    estimated_cyclomatic = 0

    if _C_HELPERS_AVAILABLE:
        funcs = _find_functions_regex(source)
        functions = len(funcs)
        include_count = len(_INCLUDE_RE.findall(_strip_comments(source)))
        global_vars = len(_find_global_vars_regex(source))
        estimated_cyclomatic = _estimate_cyclomatic(source)
    else:
        # Basic fallback without helpers
        functions = len(re.findall(r"^\w[\w\s\*]+\w\s*\([^)]*\)\s*\{", source, re.MULTILINE))
        include_count = len(re.findall(r"^\s*#include", source, re.MULTILINE))

    return {
        "filename": filename,
        "lines_of_code": loc,
        "blank_lines": blank,
        "comment_lines": min(comment, loc),  # cap at total lines
        "functions": functions,
        "function_count": functions,
        "method_count": functions,
        "classes": 0,  # C has no classes
        "class_count": 0,
        # C-specific extras (RDP-compatible: optional fields)
        "include_count": include_count,
        "global_variables": global_vars,
        "estimated_cyclomatic_complexity": estimated_cyclomatic,
    }


_C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double",
    "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long",
    "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct",
    "switch", "typedef", "union", "unsigned", "void", "volatile", "while",
}

# A file-scope `type name(...);` line: a prototype, which declares but does not use a function
_C_PROTOTYPE_PREFIX_RE = re.compile(r"^\s*(?:[A-Za-z_]\w*[\s\*]+)+\**$")
_C_PROTOTYPE_SUFFIX_RE = re.compile(r"\s*\((?:[^();{}]|\([^()]*\))*\)\s*;")


def _c_function_is_referenced(masked: str, fn: dict, funcs: list[dict]) -> bool:
    """True when a C function's name is used anywhere other than its own definition,
    its own body (recursion) or a prototype."""
    name = fn["name"]
    for m in re.finditer(rf"\b{re.escape(name)}\b", masked):
        pos = m.start()
        if fn["start_pos"] <= pos < fn["end_pos"]:
            continue  # its own header or a recursive call
        if any(f["start_pos"] <= pos < f["end_pos"] for f in funcs):
            return True  # called (or its address taken) inside another function
        line_start = masked.rfind("\n", 0, pos) + 1
        prefix = masked[line_start:pos]
        first_word = prefix.split(None, 1)[0] if prefix.strip() else ""
        is_prototype = (
            _C_PROTOTYPE_PREFIX_RE.match(prefix)
            and not re.fullmatch(_C_STATEMENT_WORDS, first_word)
            and _C_PROTOTYPE_SUFFIX_RE.match(masked, m.end())
        )
        if not is_prototype:
            return True  # e.g. a function-pointer table or a macro
    return False


def _analyze_c_smells(source: str, filename: str, repo_ref_index: set[str] | None = None) -> list[dict]:
    """
    Detect C code smells using AST and regex heuristics.

    Rules:
      1. LongFunction         - function body > 40 lines of code     [high]
      2. TooManyParameters    - function with > 5 params             [medium]
      3. DeepNesting          - control-flow nesting depth > 3       [high]
      4. DuplicateCode        - structurally identical functions     [medium]
      5. DeadCode             - unused static or unreferenced func   [medium]
      6. MagicNumber          - un-aliased numeric literals          [low]
      7. UnsafeFunctionUsage  - gets/strcpy/strcat/sprintf/scanf     [high]
      8. GlobalVariable       - global variable declaration          [medium]
      9. LargeHeaderFile      - .h file > 300 lines                  [medium]

    All rules work on the masked source (comments and literals blanked, newlines
    kept), so every reported line is the real line in the file.
    """
    if not _C_HELPERS_AVAILABLE:
        return []

    smells: list[dict] = []
    ext = os.path.splitext(filename)[-1].lower()

    funcs = _find_functions_regex(source)
    masked = _mask_c_like(source)
    masked_lines = masked.splitlines()
    source_lines = source.splitlines()

    for fn in funcs:
        span = {"start_line": fn["start_line"], "end_line": fn["end_line"]}
        body_lines = _count_code_lines(masked_lines, fn["start_line"] + 1, fn["end_line"])
        if body_lines > 40:
            smells.append({
                "type": "LongFunction",
                "message": f"Function '{fn['name']}' has {body_lines} lines (>40)",
                "line": fn["line"],
                "severity": "high",
                "entity": fn["name"],
                **span,
            })
        if fn["param_count"] > 5:
            smells.append({
                "type": "TooManyParameters",
                "message": f"Function '{fn['name']}' has {fn['param_count']} parameters (>5)",
                "line": fn["line"],
                "severity": "medium",
                "entity": fn["name"],
                "parameter_count": fn["param_count"],
                **span,
            })

        # ── DeepNesting per function ─────────────────────────────────────────
        fn_source = "\n".join(source_lines[fn["start_line"] - 1 : fn["end_line"]])
        fn_depth = _max_control_nesting(fn_source)
        if fn_depth > 3:
            smells.append({
                "type": "DeepNesting",
                "message": (
                    f"Control-flow nesting depth in function '{fn['name']}' is {fn_depth} (>3)"
                ),
                "line": fn["line"],
                "severity": "high",
                "entity": fn["name"],
                "nesting_depth": fn_depth,
                **span,
            })

        # ── DeadCode per function ────────────────────────────────────────────
        # Unused static inline functions in a header are normal: every file that
        # includes the header gets its own copy, so headers are not checked.
        fn_name = fn["name"]
        if fn_name != "main" and ext != ".h":
            header = masked[fn["start_pos"]:masked.find("{", fn["start_pos"])]
            is_static = bool(re.search(r"\bstatic\b", header))
            is_called_locally = _c_function_is_referenced(masked, fn, funcs)
            if is_static:
                if not is_called_locally:
                    smells.append({
                        "type": "DeadCode",
                        "message": f"Unused static function '{fn_name}' is never called",
                        "line": fn["line"],
                        "severity": "medium",
                        "entity": fn_name,
                    })
            elif repo_ref_index is not None:
                is_called_repo = fn_name in repo_ref_index or is_called_locally
                if not is_called_repo:
                    smells.append({
                        "type": "DeadCode",
                        "message": f"C function '{fn_name}' is never referenced or called in repository",
                        "line": fn["line"],
                        "severity": "medium",
                        "entity": fn_name,
                    })

    # ── DuplicateCode for C functions ─────────────────────────────────────────
    # Type-2 clones: bodies equal once identifiers and literals are normalised.
    # Bodies with fewer than 3 statements (getters, one-line wrappers) are too
    # small to be a meaningful clone and are not compared.
    body_hashes: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for fn in funcs:
        body = masked[masked.find("{", fn["start_pos"]):fn["end_pos"]]
        if body.count(";") < 3:
            continue
        normalized = re.sub(
            r"\b[A-Za-z_]\w*\b",
            lambda m: m.group(0) if m.group(0) in _C_KEYWORDS else "ID",
            body,
        )
        normalized = re.sub(r"\b\d[\w.]*", "N", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        h = hashlib.md5(normalized.encode()).hexdigest()
        body_hashes[h].append((fn["name"], fn["line"]))

    reported_dups: set[tuple] = set()
    for h, duplicate_funcs in body_hashes.items():
        if len(duplicate_funcs) >= 2:
            key = tuple(sorted(f[0] for f in duplicate_funcs))
            if key not in reported_dups:
                reported_dups.add(key)
                names = [f[0] for f in duplicate_funcs]
                for fname, fline in duplicate_funcs:
                    smells.append({
                        "type": "DuplicateCode",
                        "message": f"C function '{fname}' is structurally identical to {names}",
                        "line": fline,
                        "severity": "medium",
                        "entity": fname,
                        "duplicate_group": names,
                    })

    # ── MagicNumber ────────────────────────────────────────────────────────
    c_magic_smells = analyze_c_magic_numbers(source, filename)
    smells.extend(c_magic_smells)

    # ── UnsafeFunctionUsage ────────────────────────────────────────────────
    # Skips preprocessor lines (e.g. `#define strcpy(a, b) banned_strcpy`), member
    # calls (`ops->sprintf(`) and declarations of a function with the same name.
    directive_lines = _preprocessor_lines(masked)
    for m in _UNSAFE_CALL_RE.finditer(masked):
        fn_name = m.group("fn")
        line_no = masked.count("\n", 0, m.start()) + 1
        if line_no in directive_lines:
            continue
        if masked[max(0, m.start() - 2):m.start()].rstrip().endswith(("->", ".")):
            continue
        line_start = masked.rfind("\n", 0, m.start()) + 1
        prefix = masked[line_start:m.start()]
        first_word = prefix.split(None, 1)[0] if prefix.strip() else ""
        if _C_PROTOTYPE_PREFIX_RE.match(prefix) and not re.fullmatch(_C_STATEMENT_WORDS, first_word):
            continue
        smells.append({
            "type": "UnsafeFunctionUsage",
            "message": f"Unsafe function '{fn_name}()' detected — prefer a safe alternative",
            "line": line_no,
            "severity": "high",
            "entity": fn_name,
        })

    # ── GlobalVariable ────────────────────────────────────────────────────
    for gv in _find_global_vars_regex(source):
        smells.append({
            "type": "GlobalVariable",
            "message": f"Global variable '{gv['name']}' declared at file scope",
            "line": gv["line"],
            "severity": "medium",
            "entity": gv["name"],
        })

    # ── LargeHeaderFile ───────────────────────────────────────────────────
    if ext == ".h":
        loc = len(source.splitlines())
        if loc > 300:
            smells.append({
                "type": "LargeHeaderFile",
                "message": f"Header file '{filename}' has {loc} lines (>300)",
                "line": 1,
                "severity": "medium",
                "entity": filename,
            })

    return smells


# ---------------------------------------------------------------------------
# Category enrichment helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Comments smell
# ---------------------------------------------------------------------------

_LICENSE_HINT_RE = re.compile(r"copyright|licen[cs]e|spdx|all rights reserved", re.IGNORECASE)
_C_STYLE_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _explanatory_comment_lines(source: str, language: str) -> int:
    """Comment lines that count towards the Comments smell.

    Documentation is not a sign of code that needs explaining, so it is left
    out: Javadoc blocks (/** ... */), a license header at the top of the file,
    and the shebang / encoding lines of a Python script. Python docstrings are
    strings, not comments, and were never counted.
    """
    if language == "python":
        lines = source.splitlines()
        header_end = 0
        while header_end < len(lines) and (
            not lines[header_end].strip() or lines[header_end].lstrip().startswith("#")
        ):
            header_end += 1
        skip_header = bool(_LICENSE_HINT_RE.search("\n".join(lines[:header_end])))
        count = 0
        for i, line in enumerate(lines):
            text = line.strip()
            if not text.startswith("#"):
                continue
            if i < header_end and (
                skip_header or text.startswith("#!") or (i < 2 and "coding" in text)
            ):
                continue
            count += 1
        return count

    text = _mask_c_like(source, mask_comments=False) if _C_HELPERS_AVAILABLE else source
    comment_lines: set[int] = set()
    for m in _C_STYLE_COMMENT_RE.finditer(text):
        body = m.group(0)
        if body.startswith("/**"):
            continue  # Javadoc API documentation
        if not text[:m.start()].strip() and _LICENSE_HINT_RE.search(body):
            continue  # license header
        first = text.count("\n", 0, m.start()) + 1
        comment_lines.update(range(first, first + body.count("\n") + 1))
    return len(comment_lines)


def _comments_smell(source: str, language: str, filename: str, loc: int) -> dict | None:
    """Comments smell: more than 30% explanatory comment lines in a file over 50 lines."""
    if loc <= 50:
        return None
    comment_lines = _explanatory_comment_lines(source, language)
    if comment_lines / loc <= 0.3:
        return None
    return {
        "type": "Comments",
        "message": (
            f"Comment ratio {comment_lines / loc:.0%} (>30%) suggests "
            "overly complex code — consider Extract Method or Rename Method"
        ),
        "line": None,
        "severity": "low",
        "entity": filename,
    }


def _is_test_file(filename: str) -> bool:
    """Test files: their literals are expected values, not magic numbers."""
    stem, ext = os.path.splitext(os.path.basename(filename))
    if ext.lower() == ".java":
        return bool(re.match(r"Test[A-Z]", stem) or re.search(r"[a-z0-9](?:Test|Tests|IT)$", stem))
    low = stem.lower()
    return low.startswith("test_") or low.endswith(("_test", "_tests")) or low in ("conftest", "tests")


def _enrich_smells_with_category(smells: list[dict]) -> None:
    """Stamp each smell dict in-place with 'category' and 'category_priority'.

    Smells whose type is not present in SMELL_CATEGORY_MAP receive the
    fallback values 'Uncategorized' / 'low' so the schema remains stable.
    The existing 'severity' field on each smell is never modified.
    """
    for smell in smells:
        smell_type = smell.get("type", "")
        category, priority = SMELL_CATEGORY_MAP.get(
            smell_type, ("Uncategorized", "low")
        )
        smell["category"] = category
        smell["category_priority"] = priority


def _build_smell_overview(smells: list[dict]) -> dict:
    """Build the 'code_smell_overview' block from an already-enriched smell list.

    Returns a dict keyed by category name (in _CATEGORY_ORDER order) where
    each value contains:
        priority  — the category-level priority string
        count     — number of smell instances detected in this category
        smells    — deduplicated list of smell type strings found

    Categories with zero occurrences are still included so the consumer
    always sees a complete, stable schema.
    """
    # Initialise every known category with zero count
    overview: dict[str, dict] = {
        cat: {
            "priority": _CATEGORY_PRIORITY_DEFAULTS[cat],
            "count": 0,
            "smells": [],
        }
        for cat in _CATEGORY_ORDER
    }

    for smell in smells:
        cat = smell.get("category", "Uncategorized")
        if cat not in overview:
            # Dynamically created category (shouldn't happen, but be safe)
            overview[cat] = {
                "priority": smell.get("category_priority", "low"),
                "count": 0,
                "smells": [],
            }
        overview[cat]["count"] += 1
        smell_type = smell.get("type", "")
        if smell_type and smell_type not in overview[cat]["smells"]:
            overview[cat]["smells"].append(smell_type)

    return overview


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Public API Functions
# ---------------------------------------------------------------------------

def generate_file_report(
    source: str,
    filename: str,
    repo_ref_index: set[str] | None = None,
    detector: str | None = None,
) -> dict:
    """
    SPECIAL FUNCTION: Generate a comprehensive Quality & Code Smell Report for a single file.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Single File Quality Analysis Workflow):
    1. Language Routing: Dispatches file to language-specific smell detectors & metric analyzers
       (`.py` -> Python visitors, `.java` -> Java visitors, `.c`/`.h` -> C detectors).
    2. Comments Smell heuristic: Triggers if LOC > 50 and comment ratio > 30% (signals over-complex code).
    3. Category Enrichment (`_enrich_smells_with_category`): Maps each smell to its canonical taxonomy group
       (Bloaters, OO Abusers, Change Preventers, Dispensables, Couplers, Security).
    4. Quality Scoring (`_compute_score`): Computes 0-100 score by deducting points per smell based on severity.
    5. Dead-Code Accuracy: When `repo_ref_index` is supplied (by `generate_repo_report`), Python dead-code
       detection checks across the entire repository — functions/classes imported by other files are never
       flagged.  Without the index (single-file mode), detection is scoped to the file itself.
    6. ML Smell Detection (Java only): the trained model in `ml_detector` scores every class and method.
       Its findings are always attached under `ml_analysis`.  They are merged into `code_smells` only
       when the detection mode is "hybrid" or "ml", so the default rule-based output is unchanged.
    -------------------------------------------------------------------------

    Args:
        source (str): Raw source code text.
        filename (str): Target filename for language detection and entity tagging.
        repo_ref_index (set[str] | None): Optional cross-file name index built by
            `build_repo_name_index`.  Pass ``None`` (default) for standalone single-file analysis.
        detector (str | None): "rules", "hybrid", "ml" or "off".  ``None`` (default) reads the
            CUQA_SMELL_DETECTOR environment variable and falls back to "rules".  "off" skips the
            model completely.

    Returns:
        dict: Complete CUQA Quality Report dict for the file.
    """
    ext = os.path.splitext(filename)[-1].lower()

    if ext == ".py":
        # Pass the repo index so dead-code detection is cross-file aware
        smells = _analyze_python_smells(source, repo_ref_index=repo_ref_index)
        metrics = _python_metrics(source, filename)
        language = "python"
    elif ext == ".java":
        smells = _analyze_java_smells(source)
        metrics = _java_metrics(source, filename)
        language = "java"
    elif ext in (".c", ".h"):
        smells = _analyze_c_smells(source, filename, repo_ref_index=repo_ref_index)
        metrics = _c_metrics(source, filename)
        language = "c"
    else:
        return {
            "file": filename,
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'",
        }

    if _is_test_file(filename):
        smells = [s for s in smells if s["type"] != "MagicNumber"]

    # Comments smell heuristic — high comment density often indicates complex or unreadable logic
    if language in ("python", "java"):
        comments_smell = _comments_smell(source, language, filename, metrics.get("lines_of_code", 0))
        if comments_smell:
            smells.append(comments_smell)

    # ML smell detection (Java only) — additive unless the mode is "hybrid" or "ml"
    ml_analysis = None
    detection_mode = "rules"
    requested_mode = _ml_detector.resolve_mode(detector) if _ml_detector is not None else "off"
    if language == "java" and requested_mode != "off":
        ml_analysis = _ml_detector.detect_smells(source)
        if requested_mode != "rules" and ml_analysis["status"] == "ok":
            smells = _ml_detector.merge_with_rule_smells(
                smells, ml_analysis["smells"], requested_mode
            )
            detection_mode = requested_mode
        _enrich_smells_with_category(ml_analysis["smells"])

    # Enrich each smell with category + category_priority fields
    _enrich_smells_with_category(smells)

    # Build structured overview grouped by category taxonomy
    smell_overview = _build_smell_overview(smells)

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for smell in smells:
        s = smell.get("severity", "medium")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    report = {
        "file": filename,
        "language": language,
        "metrics": metrics,
        "code_smells": smells,
        "code_smell_overview": smell_overview,
        "smell_summary": severity_counts,
        "quality_score": _compute_score(smells, metrics),
    }
    if ml_analysis is not None:
        report["detection_mode"] = detection_mode
        report["ml_analysis"] = ml_analysis
    return report


def generate_repo_report(
    file_reports: list[dict],
    sources: list[tuple[str, str]] | None = None,
) -> dict:
    """
    SPECIAL FUNCTION: Aggregate single-file reports into a repository-level quality report.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Repository Aggregation & RDP Integration):
    - Sums total Lines of Code (LOC) and total code smells across all analyzed files.
    - Aggregates severity distributions (High, Medium, Low).
    - Computes repository-wide average quality score (0.0 to 100.0).
    - Produces a consolidated `code_smell_overview` across all files for downstream RDP Agent consumption.

    ACCURACY NOTE — Cross-file Dead Code Detection:
    When `sources` is provided (list of (filename, source_text) pairs for every Python file
    in the repo), this function:
      1. Calls `build_repo_name_index(sources)` once to gather every name referenced anywhere
         in the repository (imports, attribute accesses, Load-context Names).
      2. Re-analyses each Python file's smells using that index so that functions/classes
         imported by other modules are never falsely flagged as dead code.
      3. Rebuilds each Python file report's `code_smells`, `code_smell_overview`,
         `smell_summary`, and `quality_score` in-place with the more accurate results.
    Non-Python files (Java, C) are not affected.
    -------------------------------------------------------------------------

    Args:
        file_reports (list[dict]): Array of file report dictionaries generated by `generate_file_report`.
        sources (list[tuple[str,str]] | None): Optional list of ``(relative_path, source_text)``
            pairs for **all** Python files in the repo.  Supply this from the
            `/api/quality-report` endpoint to enable repository-wide dead-code accuracy.

    Returns:
        dict: Repository-level quality report dictionary.
    """
    # ── Repository-wide dead-code re-analysis (Python only) ──────────────────
    # When source texts are available, rebuild a cross-file name index and
    # re-run Python smell detection for every Python file with that index.
    # This is the core fix: a function is only "dead" if NOTHING anywhere in
    # the repo references it — not just nothing in the same file.
    if sources:
        py_sources = [
            (fname, src) for fname, src in sources
            if os.path.splitext(fname)[-1].lower() == ".py"
        ]
        if py_sources:
            repo_index = build_repo_name_index(py_sources)

            # Re-run analysis for every Python file report and update it in place
            source_map: dict[str, str] = {fname: src for fname, src in py_sources}
            for report in file_reports:
                fname = report.get("file", "")
                if os.path.splitext(fname)[-1].lower() != ".py":
                    continue
                src = source_map.get(fname)
                if src is None:
                    continue
                # Re-detect smells with full cross-file context
                accurate_smells = _analyze_python_smells(src, repo_ref_index=repo_index)
                if _is_test_file(fname):
                    accurate_smells = [s for s in accurate_smells if s["type"] != "MagicNumber"]
                metrics = report.get("metrics", {})
                # Reapply comments smell heuristic
                comments_smell = _comments_smell(src, "python", fname, metrics.get("lines_of_code", 0))
                if comments_smell:
                    accurate_smells.append(comments_smell)
                _enrich_smells_with_category(accurate_smells)
                severity_counts = {"high": 0, "medium": 0, "low": 0}
                for smell in accurate_smells:
                    s = smell.get("severity", "medium")
                    severity_counts[s] = severity_counts.get(s, 0) + 1
                # Update report in-place with accurate results
                report["code_smells"] = accurate_smells
                report["code_smell_overview"] = _build_smell_overview(accurate_smells)
                report["smell_summary"] = severity_counts
                report["quality_score"] = _compute_score(accurate_smells, metrics)

    # ── Aggregate cross-file metrics ─────────────────────────────────────────
    total_loc = sum(r.get("metrics", {}).get("lines_of_code", 0) for r in file_reports)
    total_smells = sum(len(r.get("code_smells", [])) for r in file_reports)
    high_smells = sum(r.get("smell_summary", {}).get("high", 0) for r in file_reports)
    medium_smells = sum(r.get("smell_summary", {}).get("medium", 0) for r in file_reports)
    low_smells = sum(r.get("smell_summary", {}).get("low", 0) for r in file_reports)
    files_analyzed = len(file_reports)
    avg_score = (
        sum(r.get("quality_score", 100) for r in file_reports) / files_analyzed
        if files_analyzed else 100
    )

    # Collect and aggregate code_smell_overview across all files
    all_smells: list[dict] = []
    for r in file_reports:
        all_smells.extend(r.get("code_smells", []))

    _enrich_smells_with_category(all_smells)
    repo_smell_overview = _build_smell_overview(all_smells)

    summary = {
        "files_analyzed": files_analyzed,
        "total_lines_of_code": total_loc,
        "total_code_smells": total_smells,
        "smell_severity": {
            "high": high_smells,
            "medium": medium_smells,
            "low": low_smells,
        },
        "average_quality_score": round(avg_score, 1),
        "code_smell_overview": repo_smell_overview,
    }

    # Repository-level view of the ML detector's findings (Java files only)
    ml_blocks = [
        r["ml_analysis"] for r in file_reports if isinstance(r.get("ml_analysis"), dict)
    ]
    if ml_blocks:
        ml_by_type: dict[str, int] = defaultdict(int)
        for block in ml_blocks:
            for smell in block.get("smells", []):
                ml_by_type[smell.get("type", "")] += 1
        summary["ml_code_smells"] = {
            "model_version": next(
                (b.get("model_version") for b in ml_blocks if b.get("model_version")), None
            ),
            "files_scored": sum(1 for b in ml_blocks if b.get("status") == "ok"),
            "total": sum(ml_by_type.values()),
            "by_type": dict(ml_by_type),
        }

    return {
        "summary": summary,
        "files": file_reports,
    }


def _compute_score(smells: list[dict], metrics: dict) -> float:
    """
    SPECIAL ALGORITHM: Compute a 0–100 Code Quality Score.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Quality Score Formula):
    - Base Score = 100.0
    - Point Deductions:
        - High Severity Smell   (-8 points)
        - Medium Severity Smell (-4 points)
        - Low Severity Smell    (-1 point)
    - Minimum Floor: 0.0 points.
    -------------------------------------------------------------------------
    """
    score = 100.0
    for smell in smells:
        severity = smell.get("severity", "medium")
        deduction = {"high": 8, "medium": 4, "low": 1}.get(severity, 2)
        score -= deduction
    return max(0.0, round(score, 1))

