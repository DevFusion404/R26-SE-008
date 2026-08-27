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
from collections import defaultdict
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
        _strip_comments,
        _strip_string_literals,
        _INCLUDE_RE,
        _UNSAFE_CALL_RE,
        _NUMERIC_LIT_RE,
        _max_nesting_depth,
        _estimate_cyclomatic,
        analyze_c_magic_numbers,
    )
    _C_HELPERS_AVAILABLE = True
except ImportError:
    _C_HELPERS_AVAILABLE = False



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


def _count_elif_branches(func_node: pyast.AST) -> int:
    """Count elif branches within a function node.

    FIX-06: an elif in Python AST is an If node that appears as the first
    element of another If node's orelse list.
    """
    count = 0
    for n in pyast.walk(func_node):
        if isinstance(n, pyast.If):
            for orelse_item in n.orelse:
                if isinstance(orelse_item, pyast.If):
                    count += 1
    return count


def _chain_depth(node: pyast.expr) -> int:  # type: ignore[type-arg]
    """Return the depth of an attribute/call chain starting at *node*.

    FIX-10: e.g. a.b().c().d() has depth 3.
    """
    depth = 0
    current = node
    while isinstance(current, (pyast.Attribute, pyast.Call)):
        if isinstance(current, pyast.Attribute):
            depth += 1
            current = current.value
        else:  # Call
            current = current.func
    return depth


def _normalize_function_body(func_node: pyast.FunctionDef | pyast.AsyncFunctionDef) -> str:
    """Return a normalised structural fingerprint of a function body.

    FIX-09: used for duplicate-body detection (ignores names/values).
    """
    return "|".join(type(n).__name__ for n in pyast.walk(func_node))


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


# FIX-13: regex + safe-set for Java magic number detection
_JAVA_MAGIC_NUM_RE = re.compile(r'(?<![.\w])\b(\d+\.?\d*[fFdDlL]?)\b(?![\w.])')
_JAVA_SAFE_NUMS = {"0", "1", "-1", "2", "0.0", "1.0", "0f", "1f", "0L", "1L"}


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

    # Nested blocks one level deep
    for stmt in pyast.walk(func_node):
        if stmt is func_node:
            continue
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(stmt, attr, None)
            if isinstance(block, list) and block:
                results.extend(_scan_block(block))
        # try/except handlers
        if isinstance(stmt, pyast.Try):
            for handler in (stmt.handlers or []):
                results.extend(_scan_block(handler.body))

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

    Returns
    -------
    List of ``(lineno, variable_name)`` for each unused local.
    """
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
    loaded: set[str] = set()

    for node in pyast.walk(func_node):
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
            elif isinstance(node.ctx, pyast.Load):
                loaded.add(node.id)
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

    unused = [(lineno, name) for name, lineno in stored.items() if name not in loaded]
    unused.sort(key=lambda t: t[0])
    return unused


_CONST_PREFIXES = ("MAX_", "MIN_", "DEFAULT_", "CONST_", "NUM_", "TIMEOUT_", "PORT_", "LIMIT_", "TOTAL_")

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


def _is_python_constant_assignment(node: pyast.Constant) -> bool:
    """Return True if node is assigned to a constant identifier (e.g. MAX_RETRY = 5)."""
    parent = getattr(node, "parent", None)
    while parent and isinstance(parent, (pyast.UnaryOp, pyast.BinOp)):
        parent = getattr(parent, "parent", None)

    if not parent:
        return False

    def _is_const_name(name: str) -> bool:
        if not name:
            return False
        if name.isupper() or any(name.startswith(p) for p in _CONST_PREFIXES):
            return True
        return False

    if isinstance(parent, pyast.Assign):
        for target in parent.targets:
            if isinstance(target, pyast.Name) and _is_const_name(target.id):
                return True
            elif isinstance(target, pyast.Attribute) and _is_const_name(target.attr):
                return True
            elif isinstance(target, (pyast.Tuple, pyast.List)):
                for elt in target.elts:
                    name = elt.id if isinstance(elt, pyast.Name) else getattr(elt, "attr", "")
                    if _is_const_name(name):
                        return True

    elif isinstance(parent, pyast.AnnAssign):
        target = parent.target
        name = target.id if isinstance(target, pyast.Name) else getattr(target, "attr", "")
        if _is_const_name(name):
            return True

    return False


# ---------------------------------------------------------------------------
# Code smell detectors (Python)
# ---------------------------------------------------------------------------

class _PythonSmellVisitor(pyast.NodeVisitor):
    """Walk a Python AST and collect code smells with full entity context."""

    def __init__(self):
        self.smells: list[dict] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []  # tracks enclosing function names
        self.source_lines: list[str] = []

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
        # Exclude self/cls from parameter count (FIX-01)
        real_args = [a for a in node.args.args if a.arg not in ("self", "cls")]
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

        # FIX-06: SwitchStatements — if/elif chains with >= 4 elif branches
        elif_count = _count_elif_branches(node)
        if elif_count >= 4:
            self._add(
                "SwitchStatements",
                f"Function '{node.name}' has {elif_count} elif branches (>=4) — "
                "consider Replace Conditional with Polymorphism",
                node.lineno, "medium", entity=node.name,
                cyclomatic_complexity=cc,
                start_line=node.lineno,
                end_line=end_line,
            )

        # FIX-10: MessageChains — detect deep attribute/call chains (>= 3)
        for child in pyast.walk(node):
            if isinstance(child, (pyast.Attribute, pyast.Call)):
                depth = _chain_depth(child)
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
        method_count = sum(
            1 for n in pyast.walk(node)
            if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))
        )
        class_loc = (node.end_lineno or node.lineno) - node.lineno

        if method_count > 15:
            # FIX-03: method_count
            self._add(
                "LargeClass",
                f"Class '{node.name}' has {method_count} methods (>15)",
                node.lineno, "high", entity=node.name,
                method_count=method_count,
            )

        # FIX-11: LazyClass — near-empty class (≤ 2 methods, < 30 lines)
        if method_count <= 2 and class_loc < 30:
            self._add(
                "LazyClass",
                f"Class '{node.name}' has only {method_count} methods and "
                f"{class_loc} lines — consider Inline Class or Collapse Hierarchy",
                node.lineno, "low", entity=node.name,
                method_count=method_count,
            )

        # FIX-18: PrimitiveObsession — 4+ primitive-annotated instance fields
        primitive_types = {"str", "int", "float", "bool"}
        primitive_count = 0
        for child in pyast.walk(node):
            if isinstance(child, pyast.AnnAssign):
                ann = child.annotation
                ann_name = (
                    ann.id if isinstance(ann, pyast.Name)
                    else getattr(ann, "attr", None)
                )
                if ann_name in primitive_types:
                    primitive_count += 1
        if primitive_count >= 4:
            self._add(
                "PrimitiveObsession",
                f"Class '{node.name}' has {primitive_count} primitive-typed fields — "
                "consider Replace Data Value with Object or Introduce Parameter Object",
                node.lineno, "medium", entity=node.name,
                primitive_field_count=primitive_count,
            )

        # FIX-19: InappropriateIntimacy — access to private attr of external obj
        for child in pyast.walk(node):
            if isinstance(child, pyast.Attribute):
                attr = child.attr
                if (attr.startswith("_")
                        and isinstance(child.value, pyast.Name)
                        and child.value.id not in ("self", "cls")):
                    self._add(
                        "InappropriateIntimacy",
                        f"Class '{node.name}' accesses private attribute "
                        f"'{attr}' of '{child.value.id}' — consider Move Method or "
                        "Introduce Facade",
                        getattr(child, "lineno", node.lineno), "medium",
                        entity=node.name,
                    )
                    break  # one report per class

        # FIX-20: SpeculativeGenerality — ABC / Mixin / Base with no siblings
        base_names: list[str] = []
        for base in node.bases:
            if isinstance(base, pyast.Name):
                base_names.append(base.id)
            elif isinstance(base, pyast.Attribute):
                base_names.append(base.attr)
        is_abstract = any(
            b in ("ABC", "ABCMeta")
            or "Mixin" in b
            or b.startswith("Base")
            or b.endswith("Base")
            for b in base_names
        )
        if is_abstract:
            self._add(
                "SpeculativeGenerality",
                f"Class '{node.name}' extends {base_names} — verify it has concrete "
                "subclasses in the codebase (possible Speculative Generality)",
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
        if isinstance(node.value, (int, float)) and node.value not in (0, 1, -1, 2, True, False):
            if _is_python_constant_assignment(node):
                return  # Constant assignment -> NOT a magic number

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

    visitor = _PythonSmellVisitor()
    visitor.source_lines = source.splitlines()
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
    try:
        defined_funcs: dict[str, int] = {}
        defined_classes: dict[str, int] = {}
        for node in pyast.walk(tree):
            if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                if node.name not in defined_funcs:
                    defined_funcs[node.name] = node.lineno
            elif isinstance(node, pyast.ClassDef):
                if node.name not in defined_classes:
                    defined_classes[node.name] = node.lineno

        # Build the set of names actually used inside THIS file (Load context)
        # We use Load-context Name nodes which represent actual references, not
        # definitions — this is more precise than the old approach that counted
        # all Name occurrences (which included the definition itself).
        locally_loaded: set[str] = {
            node.id
            for node in pyast.walk(tree)
            if isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load)
        }

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

        for name, lineno in {**defined_funcs, **defined_classes}.items():
            # Skip dunder names — they are called via protocol, not by name
            if name.startswith("__"):
                continue
            # Skip well-known framework entry points
            if name in _SAFE_NAMES or name.startswith("test_"):
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
    try:
        body_hashes: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for node in pyast.walk(tree):
            if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                body_str = _normalize_function_body(node)
                # Skip trivial bodies (too short to be meaningful)
                if len(body_str) > 30:
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

    # ── FIX-16: FeatureEnvy — method accesses more external attrs than self ───
    try:
        for node in pyast.walk(tree):
            if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                self_acc = 0
                ext_acc = 0
                for child in pyast.walk(node):
                    if isinstance(child, pyast.Attribute):
                        if (isinstance(child.value, pyast.Name)
                                and child.value.id in ("self", "cls")):
                            self_acc += 1
                        else:
                            ext_acc += 1
                if ext_acc > self_acc and ext_acc >= 3:
                    smells.append({
                        "type": "FeatureEnvy",
                        "message": (
                            f"Method '{node.name}' accesses {ext_acc} external attributes "
                            f"vs {self_acc} self attributes — consider Move Method"
                        ),
                        "line": node.lineno,
                        "severity": "medium",
                        "entity": node.name,
                        "external_field_accesses": ext_acc,
                        "self_field_accesses": self_acc,
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

def _is_java_constant_line(line_str: str) -> bool:
    if not line_str:
        return False
    if "final" in line_str or "#define" in line_str:
        return True
    m = re.search(r'\b([A-Za-z0-9_]*[A-Z][A-Z0-9_]*)\s*=\s*', line_str)
    if m:
        name = m.group(1)
        if name.isupper() or any(name.startswith(p) for p in _CONST_PREFIXES):
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

    source_lines = source.splitlines()

    # Strip comments for magic-number scan (FIX-13)
    clean_java = re.sub(r'//.*?$', '', source, flags=re.MULTILINE)
    clean_java = re.sub(r'/\*.*?\*/', '', clean_java, flags=re.DOTALL)

    for _, node in tree:
        # ── FIX-12 + FIX-14: LongMethod for Java ─────────────────────────────
        if isinstance(node, javalang.tree.MethodDeclaration):
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
                line = (
                    getattr(node.position, "line", None) if node.position else None
                )
                smells.append({
                    "type": "TooManyParameters",
                    "message": (
                        f"Method '{method_name}' has {len(params)} parameters (>5)"
                    ),
                    "line": line,
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
    COMP_LEFT_RE = re.compile(r'\b([A-Za-z_]\w*)\s*(?:==|!=|<=|>=|<|>)\s*(-?\d+\.?\d*[fFdDlL]?)\b')
    COMP_RIGHT_RE = re.compile(r'\b(-?\d+\.?\d*[fFdDlL]?)\s*(?:==|!=|<=|>=|<|>)\s*([A-Za-z_]\w*)\b')

    java_magic_found = set()
    if JAVALANG_AVAILABLE and tree:
        for path, node in tree:
            if isinstance(node, javalang.tree.Literal):
                val = getattr(node, "value", None)
                if val is None:
                    continue
                raw_val = str(val).strip()
                clean_val = raw_val.rstrip("fFlLdD")
                if clean_val not in _JAVA_SAFE_NUMS and raw_val not in _JAVA_SAFE_NUMS:
                    try:
                        float(clean_val)
                    except ValueError:
                        continue

                    pos = getattr(node, "position", None)
                    line_no = getattr(pos, "line", None) if pos else None
                    line_str = source_lines[line_no - 1] if line_no and line_no <= len(source_lines) else ""
                    if _is_java_constant_line(line_str):
                        continue  # Constant definition -> NOT a magic number

                    parent = path[-1] if path else None
                    var_context = None
                    if isinstance(parent, javalang.tree.BinaryOperation):
                        relational_ops = {"==", "!=", "<", ">", "<=", ">="}
                        op = getattr(parent, "operator", getattr(parent, "op", None))
                        if op in relational_ops:
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

                    java_magic_found.add((raw_val, line_no))
                    if var_context:
                        msg = f"Magic number {raw_val} compared to variable '{var_context}'"
                        smells.append({
                            "type": "MagicNumber",
                            "message": msg,
                            "details": msg,
                            "variable_context": var_context,
                            "line": line_no,
                            "severity": "low",
                            "entity": None,
                        })
                    else:
                        msg = f"Magic number {raw_val} detected"
                        smells.append({
                            "type": "MagicNumber",
                            "message": msg,
                            "details": msg,
                            "line": line_no,
                            "severity": "low",
                            "entity": None,
                        })

    for m in _JAVA_MAGIC_NUM_RE.finditer(clean_java):
        val = m.group(0).strip()
        if val not in _JAVA_SAFE_NUMS:
            line_no = clean_java[: m.start()].count("\n") + 1
            if (val, line_no) in java_magic_found:
                continue

            split_lines = clean_java.splitlines()
            line_str = split_lines[line_no - 1] if line_no <= len(split_lines) else ""
            if _is_java_constant_line(line_str):
                continue  # Constant definition -> NOT a magic number

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
                    "entity": None,
                })
            else:
                msg = f"Magic number {val} detected"
                smells.append({
                    "type": "MagicNumber",
                    "message": msg,
                    "details": msg,
                    "line": line_no,
                    "severity": "low",
                    "entity": None,
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
        "classes": 0,  # C has no classes
        # C-specific extras (RDP-compatible: optional fields)
        "include_count": include_count,
        "global_variables": global_vars,
        "estimated_cyclomatic_complexity": estimated_cyclomatic,
    }


def _analyze_c_smells(source: str, filename: str) -> list[dict]:
    """
    Detect C code smells using regex heuristics.

    Rules:
      1. LongFunction         - function body > 40 lines            [high]
      2. TooManyParameters    - function with > 5 params             [medium]
      3. DeepNesting          - nesting depth > 4                    [high]
      4. MagicNumber          - numeric literals not in {0,1,-1,2}  [low]
      5. UnsafeFunctionUsage  - gets/strcpy/strcat/sprintf/scanf     [high]
      6. GlobalVariable       - global variable declaration          [medium]
      7. LargeHeaderFile      - .h file > 300 lines                  [medium]
    """
    if not _C_HELPERS_AVAILABLE:
        return []

    smells: list[dict] = []
    ext = os.path.splitext(filename)[-1].lower()

    source_lines = source.splitlines()
    funcs = _find_functions_regex(source)
    for fn in funcs:
        body_lines = _count_code_lines(source_lines, fn["start_line"] + 1, fn["end_line"])
        if body_lines > 40:
            smells.append({
                "type": "LongFunction",
                "message": f"Function '{fn['name']}' has {body_lines} lines (>40)",
                "line": fn["line"],
                "severity": "high",
                "entity": fn["name"],
            })
        if fn["param_count"] > 5:
            smells.append({
                "type": "TooManyParameters",
                "message": f"Function '{fn['name']}' has {fn['param_count']} parameters (>5)",
                "line": fn["line"],
                "severity": "medium",
                "entity": fn["name"],
            })

    # ── 3: DeepNesting ────────────────────────────────────────────────────────
    max_depth = _max_nesting_depth(source)
    if max_depth > 4:
        smells.append({
            "type": "DeepNesting",
            "message": f"Maximum nesting depth is {max_depth} (>4)",
            "line": None,
            "severity": "high",
            "entity": filename,
        })

    # ── 4: MagicNumber ────────────────────────────────────────────────────────
    c_magic_smells = analyze_c_magic_numbers(source, filename)
    smells.extend(c_magic_smells)
    clean = _strip_string_literals(_strip_comments(source))


    # ── 5: UnsafeFunctionUsage ────────────────────────────────────────────────
    for m in _UNSAFE_CALL_RE.finditer(clean):
        fn_name = m.group("fn")
        line_no = clean[: m.start()].count("\n") + 1
        smells.append({
            "type": "UnsafeFunctionUsage",
            "message": f"Unsafe function '{fn_name}()' detected — prefer a safe alternative",
            "line": line_no,
            "severity": "high",
            "entity": fn_name,
        })

    # ── 6: GlobalVariable ────────────────────────────────────────────────────
    for gv in _find_global_vars_regex(source):
        smells.append({
            "type": "GlobalVariable",
            "message": f"Global variable '{gv['name']}' declared at file scope",
            "line": gv["line"],
            "severity": "medium",
            "entity": gv["name"],
        })

    # ── 7: LargeHeaderFile ───────────────────────────────────────────────────
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
    -------------------------------------------------------------------------

    Args:
        source (str): Raw source code text.
        filename (str): Target filename for language detection and entity tagging.
        repo_ref_index (set[str] | None): Optional cross-file name index built by
            `build_repo_name_index`.  Pass ``None`` (default) for standalone single-file analysis.

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
        smells = _analyze_c_smells(source, filename)
        metrics = _c_metrics(source, filename)
        language = "c"
    else:
        return {
            "file": filename,
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'",
        }

    # Comments smell heuristic — high comment density often indicates complex or unreadable logic
    if language in ("python", "java"):
        loc = metrics.get("lines_of_code", 0)
        comment_lines = metrics.get("comment_lines", 0)
        if loc > 50 and comment_lines / loc > 0.3:
            smells.append({
                "type": "Comments",
                "message": (
                    f"Comment ratio {comment_lines / loc:.0%} (>30%) suggests "
                    "overly complex code — consider Extract Method or Rename Method"
                ),
                "line": None,
                "severity": "low",
                "entity": filename,
            })

    # Enrich each smell with category + category_priority fields
    _enrich_smells_with_category(smells)

    # Build structured overview grouped by category taxonomy
    smell_overview = _build_smell_overview(smells)

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for smell in smells:
        s = smell.get("severity", "medium")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    return {
        "file": filename,
        "language": language,
        "metrics": metrics,
        "code_smells": smells,
        "code_smell_overview": smell_overview,
        "smell_summary": severity_counts,
        "quality_score": _compute_score(smells, metrics),
    }


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
                metrics = report.get("metrics", {})
                # Reapply comments smell heuristic
                loc = metrics.get("lines_of_code", 0)
                comment_lines = metrics.get("comment_lines", 0)
                if loc > 50 and comment_lines / max(loc, 1) > 0.3:
                    accurate_smells.append({
                        "type": "Comments",
                        "message": (
                            f"Comment ratio {comment_lines / loc:.0%} (>30%) suggests "
                            "overly complex code — consider Extract Method or Rename Method"
                        ),
                        "line": None,
                        "severity": "low",
                        "entity": fname,
                    })
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

    return {
        "summary": {
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
        },
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

