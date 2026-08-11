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
    )
    _C_HELPERS_AVAILABLE = True
except ImportError:
    _C_HELPERS_AVAILABLE = False


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


def _normalize_function_body(func_node: pyast.FunctionDef) -> str:
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
# Code smell detectors (Python)
# ---------------------------------------------------------------------------

class _PythonSmellVisitor(pyast.NodeVisitor):
    """Walk a Python AST and collect code smells with full entity context."""

    def __init__(self):
        self.smells: list[dict] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []  # tracks enclosing function names

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

        body_lines = (node.end_lineno or node.lineno) - node.lineno
        # Exclude self/cls from parameter count (FIX-01)
        real_args = [a for a in node.args.args if a.arg not in ("self", "cls")]
        # FIX-04: compute CC once, reuse for all smells on this function
        cc = _estimate_cc(node)
        end_line = node.end_lineno or node.lineno

        if body_lines > 30:
            # FIX-01: parameter_count
            # FIX-02: start_line / end_line
            # FIX-04: cyclomatic_complexity
            self._add(
                "LongMethod",
                f"Function '{node.name}' has {body_lines} lines (>30)",
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

        self.generic_visit(node)
        self._func_stack.pop()

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
    def visit_Constant(self, node: pyast.Constant):
        if isinstance(node.value, (int, float)) and node.value not in (0, 1, -1, 2, True, False):
            self._add(
                "MagicNumber",
                f"Magic number {node.value}",
                getattr(node, "lineno", None), "low",
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


def _analyze_python_smells(source: str) -> list[dict]:
    try:
        tree = pyast.parse(source)
    except SyntaxError:
        return []

    visitor = _PythonSmellVisitor()
    visitor.visit(tree)
    smells = list(visitor.smells)

    # ── FIX-07: DeadCode — defined-but-unreferenced functions/classes ─────────
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

        for name, lineno in {**defined_funcs, **defined_classes}.items():
            # Skip dunder names and well-known entry-points (false-positive prone)
            if name.startswith("__") or name in (
                "main", "setup", "teardown", "run", "test",
            ):
                continue
            # A name used as a Name node anywhere (call, assign RHS, etc.)
            usage_count = sum(
                1 for node in pyast.walk(tree)
                if isinstance(node, pyast.Name) and node.id == name
            )
            # usage_count == 0 means the name never appears as a Name reference
            if usage_count == 0:
                smells.append({
                    "type": "DeadCode",
                    "message": (
                        f"'{name}' is defined but never referenced within this file — "
                        "consider Remove Dead Code"
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
            params = getattr(node, "parameters", []) or []
            start_line = (
                getattr(node.position, "line", None) if node.position else None
            )

            if start_line is not None:
                end_line = _java_method_end_line(source_lines, start_line)
                method_loc = end_line - start_line
                method_body = "\n".join(source_lines[start_line - 1:end_line])
                cc = _java_cc(method_body)  # FIX-14

                if method_loc > 30:
                    smells.append({
                        "type": "LongMethod",
                        "message": (
                            f"Method '{node.name}' has {method_loc} lines (>30)"
                        ),
                        "line": start_line,
                        "severity": "high",
                        "entity": node.name,
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
                        f"Method '{node.name}' has {len(params)} parameters (>5)"
                    ),
                    "line": line,
                    "severity": "medium",
                    "entity": node.name,
                    "parameter_count": len(params),
                })

        # Existing: LargeClass for Java — now with FIX-03 method_count
        if isinstance(node, (javalang.tree.ClassDeclaration,
                              javalang.tree.InterfaceDeclaration)):
            methods = getattr(node, "methods", []) or []
            if len(methods) > 15:
                line = (
                    getattr(node.position, "line", None) if node.position else None
                )
                smells.append({
                    "type": "LargeClass",
                    "message": (
                        f"Class '{node.name}' has {len(methods)} methods (>15)"
                    ),
                    "line": line,
                    "severity": "high",
                    "entity": node.name,        # ← class name
                    "method_count": len(methods),  # FIX-03 (Java parity)
                })

    # ── FIX-13: MagicNumber for Java ─────────────────────────────────────────
    for m in _JAVA_MAGIC_NUM_RE.finditer(clean_java):
        val = m.group(0).strip()
        if val not in _JAVA_SAFE_NUMS:
            line_no = clean_java[: m.start()].count("\n") + 1
            smells.append({
                "type": "MagicNumber",
                "message": f"Magic number {val} detected",
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

    # ── 1 & 2: LongFunction / TooManyParameters ──────────────────────────────
    funcs = _find_functions_regex(source)
    for fn in funcs:
        body_lines = fn["end_line"] - fn["start_line"]
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
    SAFE_NUMBERS = {"0", "1", "-1", "2", "0.0", "1.0"}
    clean = _strip_string_literals(_strip_comments(source))
    for m in _NUMERIC_LIT_RE.finditer(clean):
        val = m.group(0).strip()
        if val not in SAFE_NUMBERS:
            line_no = clean[: m.start()].count("\n") + 1
            smells.append({
                "type": "MagicNumber",
                "message": f"Magic number {val} detected",
                "line": line_no,
                "severity": "low",
                "entity": filename,
            })

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
# Public API
# ---------------------------------------------------------------------------

def generate_file_report(source: str, filename: str) -> dict:
    """
    Generate a quality report for a single source file.

    Args:
        source:   Raw source code string.
        filename: Original filename (used for language detection).

    Returns:
        CUQA quality report dict for the file.
    """
    ext = os.path.splitext(filename)[-1].lower()

    if ext == ".py":
        smells = _analyze_python_smells(source)
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

    # FIX-08: Comments smell — high comment ratio signals complex code
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

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for smell in smells:
        s = smell.get("severity", "medium")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    return {
        "file": filename,
        "language": language,
        "metrics": metrics,
        "code_smells": smells,
        "smell_summary": severity_counts,
        "quality_score": _compute_score(smells, metrics),
    }


def generate_repo_report(file_reports: list[dict]) -> dict:
    """
    Aggregate per-file reports into a repository-level quality report.

    Args:
        file_reports: List of results from generate_file_report().

    Returns:
        Aggregated CUQA quality report.
    """
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
        },
        "files": file_reports,
    }


def _compute_score(smells: list[dict], metrics: dict) -> float:
    """Compute a 0–100 quality score (higher = better)."""
    score = 100.0
    for smell in smells:
        severity = smell.get("severity", "medium")
        deduction = {"high": 8, "medium": 4, "low": 1}.get(severity, 2)
        score -= deduction
    return max(0.0, round(score, 1))
