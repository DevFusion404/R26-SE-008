"""
java_features.py
----------------
Turns Java source code into numeric feature vectors for the ML smell detector.

Every class and every method in a file becomes one entity with a fixed set of
structural measurements (size, complexity, coupling, cohesion, accessor usage).
The measurements are only inputs. No threshold is applied here. The trained
model decides which combinations of values indicate a smell.

The same function is used when the training set is built and when an uploaded
repository is analysed, so the model always sees features computed the same way.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

try:
    import javalang
    from javalang import tree as jt
    from javalang import tokenizer as jtok
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False
    if TYPE_CHECKING:
        raise  # never runs; tells the type checker the names above exist when the flag is True


METHOD_FEATURES: tuple[str, ...] = (
    # size
    "loc", "sloc", "comment_lines", "n_tokens", "n_identifiers",
    "n_distinct_identifiers", "n_operators", "n_literals",
    # control flow
    "n_statements", "cyclomatic_complexity", "max_nesting", "n_params",
    "n_local_vars", "n_branches", "n_loops", "n_switch_cases", "n_try",
    "n_catch", "n_returns", "n_throws", "n_assignments", "n_bool_ops",
    "n_lambdas", "n_object_creations",
    # calls and data access
    "n_method_calls", "n_distinct_calls", "max_chain_length", "n_own_calls",
    "n_own_field_access", "n_foreign_calls", "n_foreign_accessor_calls",
    "n_foreign_field_access", "n_static_refs", "n_calls_on_params",
    "n_calls_on_locals", "n_calls_on_fields", "n_calls_on_types",
    "n_distinct_foreign_targets", "max_calls_one_target", "foreign_access_ratio",
    # declaration
    "is_static", "is_constructor", "is_public", "is_private", "has_body",
    "is_accessor_name", "returns_void",
    # enclosing class context
    "class_n_methods", "class_n_fields", "class_sloc", "sloc_share_of_class",
)

CLASS_FEATURES: tuple[str, ...] = (
    # size
    "loc", "sloc", "comment_lines", "n_tokens", "n_methods", "n_constructors",
    "n_fields", "n_inner_types",
    # visibility and modifiers
    "n_public_methods", "n_private_methods", "n_static_methods",
    "n_abstract_methods", "n_public_fields", "n_private_fields",
    "n_static_fields", "n_final_fields",
    # accessors
    "n_getters", "n_setters", "accessor_ratio", "n_non_accessor_methods",
    "weight_of_class",
    # complexity
    "wmc", "avg_cc", "max_cc", "total_method_sloc", "avg_method_sloc",
    "max_method_sloc", "avg_method_params", "max_method_params", "max_nesting",
    "n_statements", "n_method_calls",
    # coupling
    "n_own_field_access", "n_foreign_calls", "n_foreign_accessor_calls",
    "n_foreign_field_access", "n_distinct_foreign_targets", "n_distinct_types",
    "file_n_imports",
    # cohesion
    "tcc", "methods_using_fields_ratio", "fields_per_method",
    # declaration
    "has_superclass", "n_interfaces", "is_interface", "is_enum", "is_abstract",
    "is_nested", "n_overrides",
)

_ACCESSOR_NAME_RE = re.compile(r"^(get|set|is)[A-Z_]")
_GETTER_NAME_RE = re.compile(r"^(get|is)[A-Z_]")
_SETTER_NAME_RE = re.compile(r"^set[A-Z_]")


class JavaFeatureError(Exception):
    """Raised when a Java file cannot be tokenized or parsed."""


@dataclass
class JavaEntity:
    """One class or method with its line span and feature values."""

    kind: str                     # "class" | "method"
    name: str
    start_line: int
    end_line: int
    features: dict[str, float]
    parent: str | None = None     # enclosing class name
    type_kind: str = ""           # class | interface | enum | annotation | method | constructor
    fingerprint: str = ""         # hash of the entity tokens, only filled on request
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AST traversal helpers
# ---------------------------------------------------------------------------

# Node kinds used while walking a method body.
(_K_OTHER, _K_IF, _K_LOOP, _K_NESTING, _K_TRY, _K_CASE, _K_CATCH, _K_RETURN,
 _K_THROW, _K_TERNARY, _K_BINARY, _K_ASSIGN, _K_LAMBDA, _K_CREATOR, _K_VARDECL,
 _K_SUPER_CALL, _K_CALL, _K_MEMBER) = range(18)

# Per javalang class: (kind, counts as statement, declares a local name, is `this`,
# index of the "selectors" attribute, is type declaration, is method declaration,
# is type reference)
_NODE_INFO: dict[type, tuple] = {}


def _node_info(cls) -> tuple:
    """Classify a javalang node class once, so the walkers need one dict lookup per node."""
    kind = _K_OTHER
    for candidate, classes in (
        (_K_IF, jt.IfStatement),
        (_K_LOOP, (jt.ForStatement, jt.WhileStatement, jt.DoStatement)),
        (_K_TRY, jt.TryStatement),
        (_K_NESTING, (jt.SwitchStatement, jt.SynchronizedStatement)),
        (_K_CASE, jt.SwitchStatementCase),
        (_K_CATCH, jt.CatchClause),
        (_K_RETURN, jt.ReturnStatement),
        (_K_THROW, jt.ThrowStatement),
        (_K_TERNARY, jt.TernaryExpression),
        (_K_BINARY, jt.BinaryOperation),
        (_K_ASSIGN, jt.Assignment),
        (_K_LAMBDA, jt.LambdaExpression),
        (_K_CREATOR, jt.ClassCreator),
        (_K_VARDECL, jt.VariableDeclaration),
        (_K_SUPER_CALL, jt.SuperMethodInvocation),
        (_K_CALL, jt.MethodInvocation),
        (_K_MEMBER, jt.MemberReference),
    ):
        if issubclass(cls, classes):
            kind = candidate
            break
    attrs = list(cls.attrs)
    info = (
        kind,
        issubclass(cls, jt.Statement) and not issubclass(cls, (jt.BlockStatement, jt.CatchClause)),
        issubclass(cls, (jt.VariableDeclarator, jt.FormalParameter, jt.CatchClauseParameter,
                         jt.InferredFormalParameter, jt.TryResource)),
        issubclass(cls, jt.This),
        attrs.index("selectors") if "selectors" in attrs else -1,
        issubclass(cls, jt.TypeDeclaration),
        issubclass(cls, (jt.MethodDeclaration, jt.ConstructorDeclaration)),
        issubclass(cls, jt.ReferenceType),
    )
    _NODE_INFO[cls] = info
    return info


def _type_members(type_node) -> list:
    """Direct member declarations of a class, interface, enum or annotation."""
    body = type_node.body
    if isinstance(type_node, jt.EnumDeclaration):
        return list(getattr(body, "declarations", None) or [])
    return list(body or [])


def _collect_declarations(compilation_unit):
    """Walk the file once without recursion.

    javalang's own walker is recursive and can hit the recursion limit on very
    long expressions, so an explicit stack is used instead.

    Returns:
        types:     list of (type node, enclosing type node or None)
        methods:   list of (method node, enclosing type node or None)
        type_refs: for each type node id, the type names referenced inside it
    """
    types: list[tuple] = []
    methods: list[tuple] = []
    type_refs: dict[int, set] = {}
    info_of = _NODE_INFO
    stack: list[tuple] = [(compilation_unit, None)]
    while stack:
        item, enclosing = stack.pop()
        info = info_of.get(type(item))
        if info is None:
            if isinstance(item, (list, tuple)):
                for child in item:
                    if child is not None and type(child) is not str:
                        stack.append((child, enclosing))
                continue
            if not isinstance(item, jt.Node):
                continue
            info = _node_info(type(item))
        child_enclosing = enclosing
        if info[5]:
            types.append((item, enclosing))
            type_refs[id(item)] = set()
            child_enclosing = item
        elif info[6]:
            methods.append((item, enclosing))
        elif info[7] and item.name and enclosing is not None:
            type_refs[id(enclosing)].add(item.name)
        for child in item.children:
            if child is not None and type(child) is not str:
                stack.append((child, child_enclosing))

    # A type also references everything its nested types reference. Parents are
    # always discovered before their children, so one reverse pass is enough.
    for type_node, enclosing in reversed(types):
        if enclosing is not None:
            type_refs[id(enclosing)] |= type_refs[id(type_node)]
    return types, methods, type_refs


# ---------------------------------------------------------------------------
# Token span helpers
# ---------------------------------------------------------------------------

class _TokenIndex:
    """Token list with position lookup, pre-matched braces and running totals."""

    def __init__(self, tokens: list, lines: list[str]):
        self.tokens = tokens
        self.positions = [(t.position.line, t.position.column) for t in tokens]
        self.brace_close: dict[int, int] = {}
        open_stack: list[int] = []

        # Running totals, so the size of any token range is a few subtractions.
        n = len(tokens)
        self.identifier_total = [0] * (n + 1)
        self.operator_total = [0] * (n + 1)
        self.literal_total = [0] * (n + 1)
        self.line_start_total = [0] * (n + 1)   # tokens that are the first on their line
        self.identifier_value: list = [None] * n
        token_lines: set[int] = set()
        previous_line = None
        for i, tok in enumerate(tokens):
            is_identifier = is_operator = is_literal = 0
            if isinstance(tok, jtok.Identifier):
                is_identifier = 1
                self.identifier_value[i] = tok.value
            elif isinstance(tok, jtok.Operator):
                is_operator = 1
            elif isinstance(tok, jtok.Literal):
                is_literal = 1
            elif isinstance(tok, jtok.Separator):
                if tok.value == "{":
                    open_stack.append(i)
                elif tok.value == "}" and open_stack:
                    self.brace_close[open_stack.pop()] = i
            line = tok.position.line
            self.identifier_total[i + 1] = self.identifier_total[i] + is_identifier
            self.operator_total[i + 1] = self.operator_total[i] + is_operator
            self.literal_total[i + 1] = self.literal_total[i] + is_literal
            self.line_start_total[i + 1] = self.line_start_total[i] + (line != previous_line)
            previous_line = line
            token_lines.add(line)

        # comment_total[k] = lines 1..k that hold text but no token (comment-only lines)
        self.comment_total = [0] * (len(lines) + 1)
        for line_no, text in enumerate(lines, start=1):
            is_comment = line_no not in token_lines and bool(text.strip())
            self.comment_total[line_no] = self.comment_total[line_no - 1] + is_comment

    def index_at(self, position) -> int | None:
        if position is None:
            return None
        i = bisect.bisect_left(self.positions, (position.line, position.column))
        return i if i < len(self.tokens) else None

    def _annotation_start(self, end: int) -> int | None:
        """Index where an annotation ending just before *end* starts, if any."""
        tokens = self.tokens
        p = end - 1
        if p >= 0 and tokens[p].value == ")":
            depth = 0
            while p >= 0:
                value = tokens[p].value
                if value == ")":
                    depth += 1
                elif value == "(":
                    depth -= 1
                    if depth == 0:
                        break
                p -= 1
            p -= 1
        if p < 0 or not isinstance(tokens[p], jtok.Identifier):
            return None
        while p >= 2 and tokens[p - 1].value == "." and isinstance(tokens[p - 2], jtok.Identifier):
            p -= 2
        if p >= 1 and isinstance(tokens[p - 1], jtok.Annotation):
            return p - 1
        return None

    def declaration_start(self, idx: int) -> int:
        """Step back over the modifiers and annotations in front of a declaration."""
        j = idx
        while j > 0:
            if isinstance(self.tokens[j - 1], jtok.Modifier):
                j -= 1
                continue
            annotation = self._annotation_start(j)
            if annotation is None:
                break
            j = annotation
        return j

    def declaration_end(self, idx: int) -> int:
        """Index of the closing brace of a declaration, or of its ';' if it has no body."""
        tokens = self.tokens
        paren_depth = 0
        i = idx
        while i < len(tokens):
            tok = tokens[i]
            if isinstance(tok, jtok.Separator):
                value = tok.value
                if value == "(":
                    paren_depth += 1
                elif value == ")":
                    paren_depth -= 1
                elif paren_depth == 0:
                    if value == "{":
                        return self.brace_close.get(i, len(tokens) - 1)
                    if value == ";":
                        return i
            i += 1
        return len(tokens) - 1


def _span_stats(index: _TokenIndex, start: int, end: int) -> dict:
    """Size measurements of the token range [start, end]."""
    start_line = index.positions[start][0]
    end_line = index.positions[end][0]
    identifiers = set(index.identifier_value[start:end + 1])
    identifiers.discard(None)
    last_line = min(end_line, len(index.comment_total) - 1)
    comment_lines = 0
    if last_line >= start_line:
        comment_lines = index.comment_total[last_line] - index.comment_total[start_line - 1]
    return {
        "start_line": start_line,
        "end_line": end_line,
        "loc": end_line - start_line + 1,
        "sloc": 1 + index.line_start_total[end + 1] - index.line_start_total[start + 1],
        "comment_lines": comment_lines,
        "n_tokens": end - start + 1,
        "n_identifiers": index.identifier_total[end + 1] - index.identifier_total[start],
        "n_distinct_identifiers": len(identifiers),
        "n_operators": index.operator_total[end + 1] - index.operator_total[start],
        "n_literals": index.literal_total[end + 1] - index.literal_total[start],
    }


# ---------------------------------------------------------------------------
# Method measurements
# ---------------------------------------------------------------------------

def _method_stats(node, class_fields: frozenset) -> dict:
    """Walk one method body and count its control-flow and data-access constructs."""
    param_names = {p.name for p in (node.parameters or [])}
    local_names = set(param_names)

    n_statements = n_local_vars = n_branches = n_loops = n_switch_cases = 0
    n_try = n_catch = n_returns = n_throws = n_assignments = n_bool_ops = 0
    n_lambdas = n_object_creations = n_method_calls = max_chain = max_nesting = 0
    n_super_calls = 0
    cc = 1
    call_names: set[str] = set()
    # Calls and member references are classified after the walk, because a
    # local variable can be declared further down than the place it is used.
    invocations: list[tuple] = []   # (member, qualifier root, role)
    member_refs: list[tuple] = []

    info_of = _NODE_INFO
    # Stack entries: (item, nesting depth, role). Role is "" for ordinary nodes,
    # "chained" for a selector on another expression and "this" for this.<member>.
    stack: list[tuple] = [(node.body, 0, "")]
    while stack:
        item, depth, role = stack.pop()
        info = info_of.get(type(item))
        if info is None:
            if isinstance(item, (list, tuple)):
                for child in item:
                    if child is not None and type(child) is not str:
                        stack.append((child, depth, role))
                continue
            if not isinstance(item, jt.Node):
                continue
            info = _node_info(type(item))
        kind = info[0]
        if info[1]:
            n_statements += 1
        if info[2]:
            local_names.add(item.name)

        child_depth = depth
        if kind:
            if kind == _K_IF:
                n_branches += 1
                cc += 1
                level = depth + 1
                if level > max_nesting:
                    max_nesting = level
                stack.append((item.condition, level, ""))
                stack.append((item.then_statement, level, ""))
                # an "else if" continues the same decision level, it is not deeper nesting
                else_depth = depth if isinstance(item.else_statement, jt.IfStatement) else level
                stack.append((item.else_statement, else_depth, ""))
                continue
            if kind == _K_LOOP:
                n_loops += 1
                cc += 1
                child_depth = depth + 1
            elif kind == _K_TRY:
                n_try += 1
                child_depth = depth + 1
            elif kind == _K_NESTING:
                child_depth = depth + 1
            elif kind == _K_CASE:
                labels = len(item.case or [])
                n_switch_cases += labels
                n_branches += labels
                cc += labels
            elif kind == _K_CATCH:
                n_catch += 1
                cc += 1
            elif kind == _K_RETURN:
                n_returns += 1
            elif kind == _K_THROW:
                n_throws += 1
            elif kind == _K_TERNARY:
                n_branches += 1
                cc += 1
            elif kind == _K_BINARY:
                if item.operator in ("&&", "||"):
                    n_bool_ops += 1
                    cc += 1
            elif kind == _K_ASSIGN:
                n_assignments += 1
            elif kind == _K_LAMBDA:
                n_lambdas += 1
            elif kind == _K_CREATOR:
                n_object_creations += 1
            elif kind == _K_VARDECL:
                n_local_vars += len(item.declarators or [])
            elif kind == _K_SUPER_CALL:
                n_method_calls += 1
                n_super_calls += 1
                call_names.add(item.member)
            elif kind == _K_CALL:
                n_method_calls += 1
                call_names.add(item.member)
                chain = 1 + sum(1 for s in (item.selectors or []) if isinstance(s, jt.MethodInvocation))
                if chain > max_chain:
                    max_chain = chain
                invocations.append((item.member, (item.qualifier or "").split(".", 1)[0], role))
            elif kind == _K_MEMBER:
                member_refs.append((item.member, (item.qualifier or "").split(".", 1)[0], role))
            if child_depth > max_nesting:
                max_nesting = child_depth

        selectors_index = info[4]
        for position, child in enumerate(item.children):
            if child is None or type(child) is str:
                continue
            if position == selectors_index:
                for selector_position, selector in enumerate(child):
                    selector_role = "this" if (info[3] and selector_position == 0) else "chained"
                    stack.append((selector, child_depth, selector_role))
            else:
                stack.append((child, child_depth, ""))

    n_own_calls = n_super_calls
    n_own_field_access = n_foreign_calls = n_foreign_accessor_calls = 0
    n_foreign_field_access = n_static_refs = 0
    n_calls_on_params = n_calls_on_locals = n_calls_on_fields = n_calls_on_types = 0
    targets: dict[str, int] = {}
    fields_used: set[str] = set()

    for member, root, role in invocations:
        if role == "this" or (role == "" and root in ("", "this", "super")):
            n_own_calls += 1
            continue
        n_foreign_calls += 1
        if _ACCESSOR_NAME_RE.match(member or ""):
            n_foreign_accessor_calls += 1
        if role == "" and root:
            if root in local_names:
                if root in param_names:
                    n_calls_on_params += 1
                else:
                    n_calls_on_locals += 1
                targets[root] = targets.get(root, 0) + 1
            elif root in class_fields:
                n_calls_on_fields += 1
                n_own_field_access += 1
                fields_used.add(root)
                targets[root] = targets.get(root, 0) + 1
            elif root[:1].isupper():
                n_calls_on_types += 1
            else:
                targets[root] = targets.get(root, 0) + 1

    for member, root, role in member_refs:
        if role == "this" or (role == "" and root == "this"):
            n_own_field_access += 1
            fields_used.add(member)
        elif role == "chained":
            n_foreign_field_access += 1
        elif root == "":
            if member in class_fields and member not in local_names:
                n_own_field_access += 1
                fields_used.add(member)
        elif root[:1].isupper():
            n_static_refs += 1
        else:
            n_foreign_field_access += 1
            targets[root] = targets.get(root, 0) + 1
            if root in class_fields and root not in local_names:
                fields_used.add(root)

    foreign_instance = (n_foreign_calls - n_calls_on_types) + n_foreign_field_access
    total_access = foreign_instance + n_own_calls + n_own_field_access
    return {
        "n_statements": n_statements,
        "n_local_vars": n_local_vars,
        "n_branches": n_branches,
        "n_loops": n_loops,
        "n_switch_cases": n_switch_cases,
        "n_try": n_try,
        "n_catch": n_catch,
        "n_returns": n_returns,
        "n_throws": n_throws,
        "n_assignments": n_assignments,
        "n_bool_ops": n_bool_ops,
        "n_lambdas": n_lambdas,
        "n_object_creations": n_object_creations,
        "n_method_calls": n_method_calls,
        "max_chain_length": max_chain,
        "n_own_calls": n_own_calls,
        "n_own_field_access": n_own_field_access,
        "n_foreign_calls": n_foreign_calls,
        "n_foreign_accessor_calls": n_foreign_accessor_calls,
        "n_foreign_field_access": n_foreign_field_access,
        "n_static_refs": n_static_refs,
        "n_calls_on_params": n_calls_on_params,
        "n_calls_on_locals": n_calls_on_locals,
        "n_calls_on_fields": n_calls_on_fields,
        "n_calls_on_types": n_calls_on_types,
        "max_nesting": max_nesting,
        "cyclomatic_complexity": cc,
        "n_distinct_calls": len(call_names),
        "n_distinct_foreign_targets": len(targets),
        "max_calls_one_target": max(targets.values()) if targets else 0,
        "foreign_access_ratio": foreign_instance / total_access if total_access else 0.0,
        "_fields_used": fields_used,
        "_targets": set(targets),
    }


# ---------------------------------------------------------------------------
# Class measurements
# ---------------------------------------------------------------------------

def _tight_class_cohesion(field_sets: list[set]) -> float:
    """Share of method pairs that use at least one common field (TCC)."""
    n = len(field_sets)
    if n < 2:
        return 0.0
    bit_of: dict[str, int] = {}
    masks = []
    for names in field_sets:
        mask = 0
        for name in names:
            mask |= 1 << bit_of.setdefault(name, len(bit_of))
        masks.append(mask)
    connected = 0
    for i in range(n):
        mi = masks[i]
        if not mi:
            continue
        for j in range(i + 1, n):
            if mi & masks[j]:
                connected += 1
    return connected / (n * (n - 1) / 2)


def _class_features(type_node, span: dict, method_info: dict, n_imports: int, nested: bool,
                    n_distinct_types: int) -> dict:
    members = _type_members(type_node)
    is_interface = isinstance(type_node, jt.InterfaceDeclaration)
    methods = [m for m in members if isinstance(m, jt.MethodDeclaration)]
    constructors = [m for m in members if isinstance(m, jt.ConstructorDeclaration)]
    field_decls = [m for m in members if isinstance(m, jt.FieldDeclaration)]
    inner_types = [m for m in members if isinstance(m, jt.TypeDeclaration)]

    def n_declared(decls) -> int:
        return sum(len(d.declarators or []) for d in decls)

    def with_modifier(decls, modifier: str) -> list:
        return [d for d in decls if modifier in (d.modifiers or ())]

    n_methods = len(methods)
    n_fields = n_declared(field_decls)
    public_methods = [m for m in methods if is_interface or "public" in (m.modifiers or ())]
    n_public_fields = n_declared(with_modifier(field_decls, "public"))

    n_getters = n_setters = n_overrides = 0
    n_public_functional = 0
    for m in methods:
        info = method_info[id(m)]
        n_params = len(m.parameters or [])
        name = m.name or ""
        is_getter = bool(_GETTER_NAME_RE.match(name)) and n_params == 0 and info["n_statements"] <= 1
        is_setter = bool(_SETTER_NAME_RE.match(name)) and n_params == 1 and info["n_statements"] <= 2
        n_getters += is_getter
        n_setters += is_setter
        if not (is_getter or is_setter) and (is_interface or "public" in (m.modifiers or ())):
            n_public_functional += 1
        if any(a.name == "Override" for a in (m.annotations or [])):
            n_overrides += 1
    n_accessors = n_getters + n_setters
    public_members = len(public_methods) + n_public_fields

    callables = methods + constructors
    infos = [method_info[id(m)] for m in callables]
    cc_values = [i["cyclomatic_complexity"] for i in infos]
    sloc_values = [i["sloc"] for i in infos]
    param_values = [len(m.parameters or []) for m in callables]
    n_callables = len(callables)

    def total(key: str) -> int:
        return sum(i[key] for i in infos)

    foreign_targets: set[str] = set()
    for i in infos:
        foreign_targets |= i["_targets"]

    bodied = [method_info[id(m)] for m in methods if m.body is not None]
    field_sets = [i["_fields_used"] for i in bodied]
    modifiers = type_node.modifiers or ()
    implements = getattr(type_node, "implements", None) or []
    extends = getattr(type_node, "extends", None)

    features = {
        "loc": span["loc"],
        "sloc": span["sloc"],
        "comment_lines": span["comment_lines"],
        "n_tokens": span["n_tokens"],
        "n_methods": n_methods,
        "n_constructors": len(constructors),
        "n_fields": n_fields,
        "n_inner_types": len(inner_types),
        "n_public_methods": len(public_methods),
        "n_private_methods": len(with_modifier(methods, "private")),
        "n_static_methods": len(with_modifier(methods, "static")),
        "n_abstract_methods": sum(1 for m in methods if m.body is None),
        "n_public_fields": n_public_fields,
        "n_private_fields": n_declared(with_modifier(field_decls, "private")),
        "n_static_fields": n_declared(with_modifier(field_decls, "static")),
        "n_final_fields": n_declared(with_modifier(field_decls, "final")),
        "n_getters": n_getters,
        "n_setters": n_setters,
        "accessor_ratio": n_accessors / n_methods if n_methods else 0.0,
        "n_non_accessor_methods": n_methods - n_accessors,
        "weight_of_class": n_public_functional / public_members if public_members else 1.0,
        "wmc": sum(cc_values),
        "avg_cc": sum(cc_values) / n_callables if n_callables else 0.0,
        "max_cc": max(cc_values, default=0),
        "total_method_sloc": sum(sloc_values),
        "avg_method_sloc": sum(sloc_values) / n_callables if n_callables else 0.0,
        "max_method_sloc": max(sloc_values, default=0),
        "avg_method_params": sum(param_values) / n_callables if n_callables else 0.0,
        "max_method_params": max(param_values, default=0),
        "max_nesting": max((i["max_nesting"] for i in infos), default=0),
        "n_statements": total("n_statements"),
        "n_method_calls": total("n_method_calls"),
        "n_own_field_access": total("n_own_field_access"),
        "n_foreign_calls": total("n_foreign_calls"),
        "n_foreign_accessor_calls": total("n_foreign_accessor_calls"),
        "n_foreign_field_access": total("n_foreign_field_access"),
        "n_distinct_foreign_targets": len(foreign_targets),
        "n_distinct_types": n_distinct_types,
        "file_n_imports": n_imports,
        "tcc": _tight_class_cohesion(field_sets),
        "methods_using_fields_ratio": (
            sum(1 for s in field_sets if s) / len(field_sets) if field_sets else 0.0
        ),
        "fields_per_method": n_fields / n_methods if n_methods else float(n_fields),
        "has_superclass": 1 if extends else 0,
        "n_interfaces": len(implements) if isinstance(implements, list) else 1,
        "is_interface": 1 if is_interface else 0,
        "is_enum": 1 if isinstance(type_node, jt.EnumDeclaration) else 0,
        "is_abstract": 1 if "abstract" in modifiers else 0,
        "is_nested": 1 if nested else 0,
        "n_overrides": n_overrides,
    }
    return features


def _type_kind(type_node) -> str:
    if isinstance(type_node, jt.InterfaceDeclaration):
        return "interface"
    if isinstance(type_node, jt.EnumDeclaration):
        return "enum"
    if isinstance(type_node, jt.AnnotationDeclaration):
        return "annotation"
    return "class"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_entities(source: str, with_fingerprint: bool = False) -> list[JavaEntity]:
    """Parse a Java file and return one JavaEntity per class and per method.

    Args:
        source: Raw Java source text.
        with_fingerprint: Also hash each entity's tokens. Used when the training
            set is built to find the same code copied into several repositories.

    Raises:
        JavaFeatureError: javalang is missing or the file cannot be parsed.
    """
    if not JAVALANG_AVAILABLE:
        raise JavaFeatureError("javalang is not installed")
    try:
        tokens = list(jtok.tokenize(source))
        tree = javalang.parser.Parser(tokens).parse()
    except RecursionError as exc:
        raise JavaFeatureError("source is nested too deeply to parse") from exc
    except Exception as exc:  # javalang raises several unrelated error types
        raise JavaFeatureError(f"could not parse Java source: {type(exc).__name__}") from exc
    if not tokens:
        return []

    index = _TokenIndex(tokens, source.splitlines())
    types, methods, type_refs = _collect_declarations(tree)
    n_imports = len(tree.imports or [])

    def span_of(node) -> tuple[int, int] | None:
        idx = index.index_at(node.position)
        if idx is None:
            return None
        return index.declaration_start(idx), index.declaration_end(idx)

    def fingerprint(start: int, end: int) -> str:
        if not with_fingerprint:
            return ""
        text = " ".join(t.value for t in tokens[start:end + 1])
        return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:20]

    class_fields: dict[int, frozenset] = {}
    for type_node, _ in types:
        names = set()
        for member in _type_members(type_node):
            if isinstance(member, jt.FieldDeclaration):
                names.update(d.name for d in (member.declarators or []))
        class_fields[id(type_node)] = frozenset(names)

    # Method measurements come first because the class features aggregate them.
    method_info: dict[int, dict] = {}
    method_spans: dict[int, tuple[int, int]] = {}
    for method_node, enclosing in methods:
        fields = class_fields.get(id(enclosing), frozenset()) if enclosing is not None else frozenset()
        info = _method_stats(method_node, fields)
        token_span = span_of(method_node)
        if token_span is not None:
            info.update(_span_stats(index, *token_span))
            method_spans[id(method_node)] = token_span
        else:
            info.update(start_line=0, end_line=0, loc=0, sloc=0, comment_lines=0, n_tokens=0,
                        n_identifiers=0, n_distinct_identifiers=0, n_operators=0, n_literals=0)
        method_info[id(method_node)] = info

    entities: list[JavaEntity] = []
    class_context: dict[int, dict] = {}
    for type_node, enclosing in types:
        token_span = span_of(type_node)
        if token_span is None:
            continue
        span = _span_stats(index, *token_span)
        features = _class_features(type_node, span, method_info, n_imports, enclosing is not None,
                                   len(type_refs[id(type_node)]))
        class_context[id(type_node)] = features
        entities.append(JavaEntity(
            kind="class",
            name=type_node.name or "",
            start_line=span["start_line"],
            end_line=span["end_line"],
            features=features,
            parent=enclosing.name if enclosing is not None else None,
            type_kind=_type_kind(type_node),
            fingerprint=fingerprint(*token_span),
        ))

    for method_node, enclosing in methods:
        token_span = method_spans.get(id(method_node))
        if token_span is None:
            continue
        info = method_info[id(method_node)]
        context = class_context.get(id(enclosing), {}) if enclosing is not None else {}
        modifiers = method_node.modifiers or ()
        is_constructor = isinstance(method_node, jt.ConstructorDeclaration)
        class_sloc = context.get("sloc", 0)

        features = {name: info[name] for name in METHOD_FEATURES if name in info}
        features.update({
            "n_params": len(method_node.parameters or []),
            "is_static": 1 if "static" in modifiers else 0,
            "is_constructor": 1 if is_constructor else 0,
            "is_public": 1 if "public" in modifiers else 0,
            "is_private": 1 if "private" in modifiers else 0,
            "has_body": 1 if method_node.body is not None else 0,
            "is_accessor_name": 1 if _ACCESSOR_NAME_RE.match(method_node.name or "") else 0,
            "returns_void": 1 if (not is_constructor and method_node.return_type is None) else 0,
            "class_n_methods": context.get("n_methods", 0),
            "class_n_fields": context.get("n_fields", 0),
            "class_sloc": class_sloc,
            "sloc_share_of_class": info["sloc"] / class_sloc if class_sloc else 0.0,
        })
        entities.append(JavaEntity(
            kind="method",
            name=method_node.name or "",
            start_line=info["start_line"],
            end_line=info["end_line"],
            features=features,
            parent=enclosing.name if enclosing is not None else None,
            type_kind="constructor" if is_constructor else "method",
            fingerprint=fingerprint(*token_span),
        ))

    entities.sort(key=lambda e: (e.start_line, e.kind != "class", e.end_line))
    return entities


def feature_vector(entity: JavaEntity, feature_names) -> list[float]:
    """Order an entity's features the way a trained model expects them."""
    return [float(entity.features.get(name, 0.0)) for name in feature_names]
