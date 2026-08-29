"""Owned-composition Inline Class transformer for Python.

This module handles the common Fowler-style Inline Class shape where a tiny
helper object is owned by exactly one other class, for example::

    class CustomerContact:
        def __init__(self, phone):
            self.phone = phone

        def formatted_phone(self):
            return f"Phone: {self.phone}"

    class Customer:
        def __init__(self, phone):
            self.contact = CustomerContact(phone)

The safe transformation is::

    class Customer:
        def __init__(self, phone):
            self.phone = phone

        def formatted_phone(self):
            return f"Phone: {self.phone}"

The implementation is intentionally conservative.  If ownership, constructor
mapping, dynamic usage, inheritance, naming, or call-site rewriting is
ambiguous, it returns ``review_required`` without changing source code.
"""

from __future__ import annotations

import ast
import copy
import re
import textwrap
from typing import Any, Sequence, Tuple


def _review(
    source_code: str,
    *,
    class_to_inline: str,
    reason: str,
    **metadata: Any,
) -> Tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        "status": "review_required",
        "reason": reason,
        "class_to_inline": class_to_inline,
        **metadata,
    }


def _not_applicable(
    source_code: str,
    *,
    class_to_inline: str,
    reason: str,
) -> Tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        "status": "not_applicable",
        "reason": reason,
        "class_to_inline": class_to_inline,
    }


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _class_nodes_with_qualified_names(
    tree: ast.Module,
) -> list[tuple[ast.ClassDef, str, str]]:
    """Return every class, including nested classes, by lexical identity."""

    result: list[tuple[ast.ClassDef, str, str]] = []

    def visit_body(body: Sequence[ast.stmt], parent: str = "") -> None:
        for statement in body:
            if not isinstance(statement, ast.ClassDef):
                continue
            qualified = f"{parent}.{statement.name}" if parent else statement.name
            result.append((statement, qualified, parent))
            visit_body(statement.body, qualified)

    visit_body(tree.body)
    return result


def build_python_inline_class_model(
    source_code: str,
    *,
    module_name: str = "",
) -> dict[str, Any]:
    """Build a conservative, serialisable class/reference model for Inline Class.

    It deliberately records evidence rather than guessing framework semantics.
    The transformation strategies use this model to choose an apply/review
    path, while the safety report can expose the reason for a review decision.
    """

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "parse_failed", "classes": []}

    class_entries = _class_nodes_with_qualified_names(tree)
    qualified_by_node = {node: qualified for node, qualified, _ in class_entries}
    short_counts: dict[str, int] = {}
    for node, _, _ in class_entries:
        short_counts[node.name] = short_counts.get(node.name, 0) + 1

    imports = {
        str(alias.name)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    models: list[dict[str, Any]] = []
    for node, qualified, parent in class_entries:
        methods = [
            item.name for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        fields = sorted({
            item.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Name)
            and item.value.id == "self"
            and isinstance(item.ctx, ast.Store)
        })
        bases = [_dotted_name(base) or ast.unparse(base) for base in node.bases]
        decorators = [_dotted_name(item) or ast.unparse(item) for item in node.decorator_list]
        metaclass = next(
            (
                _dotted_name(keyword.value) or ast.unparse(keyword.value)
                for keyword in node.keywords
                if keyword.arg == "metaclass"
            ),
            "",
        )
        subclasses = [
            other_qualified
            for other_node, other_qualified, _ in class_entries
            if any(
                _dotted_name(base).split(".")[-1] == node.name
                for base in other_node.bases
            )
        ]
        parents = _parents(tree)
        instantiations = sum(
            isinstance(item, ast.Call)
            and (
                _dotted_name(item.func) == qualified
                or (
                    not parent
                    and isinstance(item.func, ast.Name)
                    and item.func.id == node.name
                )
            )
            for item in ast.walk(tree)
            if not _is_descendant(item, node, parents)
        )
        super_calls = sum(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "super"
            for item in ast.walk(node)
        )
        type_identity_checks = sum(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in {"isinstance", "issubclass", "type"}
            and any(
                isinstance(argument, ast.Name) and argument.id == node.name
                for argument in item.args
            )
            for item in ast.walk(tree)
            if not _is_descendant(item, node, parents)
        )
        registrations = sum(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr.lower() in {"register", "register_class", "register_model", "add"}
            and any(
                isinstance(argument, ast.Name) and argument.id == node.name
                for argument in item.args
            )
            for item in ast.walk(tree)
            if not _is_descendant(item, node, parents)
        )
        annotation_references = sum(
            isinstance(item, ast.Name)
            and item.id == node.name
            and isinstance(
                parents.get(item),
                (ast.arg, ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef),
            )
            for item in ast.walk(tree)
            if not _is_descendant(item, node, parents)
        )
        dynamic_references = sum(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in {"getattr", "hasattr", "setattr", "eval", "globals", "locals"}
            and any(
                isinstance(argument, ast.Constant)
                and argument.value == node.name
                for argument in item.args
            )
            for item in ast.walk(tree)
            if not _is_descendant(item, node, parents)
        )
        models.append({
            "qualified_name": f"{module_name}.{qualified}" if module_name else qualified,
            "local_qualified_name": qualified,
            "class_name": node.name,
            "parent_qualified_name": parent,
            "line_range": [node.lineno, node.end_lineno],
            "bases": bases,
            "subclasses": subclasses,
            "decorators": decorators,
            "metaclass": metaclass,
            "methods": methods,
            "fields": fields,
            "constructor_present": "__init__" in methods,
            "instantiations": instantiations,
            "super_calls": super_calls,
            "type_identity_checks": type_identity_checks,
            "registration_references": registrations,
            "annotation_references": annotation_references,
            "dynamic_references": dynamic_references,
            "imports": sorted(imports),
            "short_name_ambiguous": short_counts[node.name] > 1,
        })
    return {"status": "success", "module_name": module_name, "classes": models}


def build_python_inline_repository_model(
    project_source_files: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Collect class-model evidence for every parseable Python repository file."""

    files: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    for item in project_source_files:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or item.get("name") or item.get("path") or "")
        language = str(item.get("language") or "").lower()
        code = item.get("source_code") or item.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        if language and language != "python" and not file_name.lower().endswith(".py"):
            continue
        module_name = file_name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
        model = build_python_inline_class_model(code, module_name=module_name)
        files.append({"file_name": file_name, "status": model.get("status")})
        for class_model in model.get("classes") or []:
            classes.append({**class_model, "source_file": file_name})
    return {"status": "success", "files": files, "classes": classes}


def resolve_python_inline_class_target(
    source_code: str,
    *,
    class_to_inline: str,
    module_name: str = "",
) -> dict[str, Any]:
    """Resolve ``Class``, ``Outer.Inner`` and ``module.Class`` unambiguously."""

    model = build_python_inline_class_model(source_code, module_name=module_name)
    if model.get("status") != "success":
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}
    requested = str(class_to_inline or "").strip()
    if not requested:
        return {"status": "not_applicable", "reason": "INLINE_CLASS_TARGET_NOT_FOUND"}
    normalized = requested.replace("/", ".").replace("\\", ".")
    normalized = normalized.removesuffix(".py")
    candidates = [
        item for item in model["classes"]
        if normalized in {
            item["qualified_name"],
            item["local_qualified_name"],
        }
    ]
    if not candidates and "." in normalized:
        candidates = [
            item for item in model["classes"]
            if item["local_qualified_name"] == normalized.split(".", 1)[-1]
        ]
    if not candidates and "." not in normalized:
        candidates = [
            item for item in model["classes"]
            if item["class_name"] == normalized
        ]
    if len(candidates) != 1:
        return {
            "status": "review_required" if len(candidates) > 1 else "not_applicable",
            "reason": (
                "AMBIGUOUS_QUALIFIED_INLINE_CLASS_TARGET"
                if len(candidates) > 1 else "INLINE_CLASS_TARGET_NOT_FOUND"
            ),
            "requested_class_to_inline": requested,
            "class_model": model,
        }
    candidate = candidates[0]
    return {
        "status": "success",
        "class_to_inline": candidate["local_qualified_name"],
        "class_name": candidate["class_name"],
        "qualified_class_name": candidate["qualified_name"],
        "target_resolution": "qualified_python_class_model",
        "class_model": candidate,
    }


def _direct_class_member_profile(
    source_code: str,
    *,
    local_qualified_name: str,
) -> dict[str, Any]:
    """Describe only members declared directly by the target class.

    Inline Class decisions must distinguish executable behaviour from framework
    configuration.  A nested ``Meta`` class or a class-level field is not the
    same thing as an empty marker subclass and must never be silently discarded.
    """

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "parse_failed"}

    entries = _class_nodes_with_qualified_names(tree)
    matches = [
        node
        for node, qualified, _ in entries
        if qualified == local_qualified_name
    ]
    if len(matches) != 1:
        return {"status": "not_found"}

    node = matches[0]
    methods = [
        item
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    business_methods = [item for item in methods if item.name != "__init__"]
    nested_classes = [item.name for item in node.body if isinstance(item, ast.ClassDef)]

    class_fields: list[str] = []
    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    class_fields.append(target.id)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            class_fields.append(item.target.id)

    non_doc = _strip_docstring(node.body)
    non_pass = [item for item in non_doc if not isinstance(item, ast.Pass)]

    return {
        "status": "success",
        "node": node,
        "methods": methods,
        "business_methods": business_methods,
        "class_fields": sorted(set(class_fields)),
        "nested_classes": nested_classes,
        "pass_only": not non_pass,
    }


def _function_arguments_equivalent(
    left: ast.arguments,
    right: ast.arguments,
) -> bool:
    """Compare two Python signatures while ignoring the self parameter name."""

    def model(args: ast.arguments) -> tuple[Any, ...]:
        positional = [*args.posonlyargs, *args.args]
        if positional:
            positional = positional[1:]
        return (
            tuple(item.arg for item in positional),
            tuple(ast.dump(item, include_attributes=False) for item in args.defaults),
            args.vararg.arg if args.vararg else None,
            tuple(item.arg for item in args.kwonlyargs),
            tuple(
                None if item is None else ast.dump(item, include_attributes=False)
                for item in args.kw_defaults
            ),
            args.kwarg.arg if args.kwarg else None,
        )

    return model(left) == model(right)


def _is_trivial_forwarding_init(
    method: ast.FunctionDef | ast.AsyncFunctionDef | None,
    *,
    class_name: str,
    base_init: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    """Return True only for a semantically transparent ``super().__init__``.

    A forwarding constructor is safe to erase only when its public signature is
    the same as the base constructor and it forwards every parameter unchanged.
    This avoids unsafe collapses such as ``super().__init__(x, enabled=True)``.
    """

    if method is None or base_init is None:
        return False
    if isinstance(method, ast.AsyncFunctionDef) or isinstance(base_init, ast.AsyncFunctionDef):
        return False
    if method.decorator_list or base_init.decorator_list:
        return False
    if not _function_arguments_equivalent(method.args, base_init.args):
        return False

    body = _strip_docstring(method.body)
    if len(body) != 1 or not isinstance(body[0], ast.Expr):
        return False
    call = body[0].value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "__init__"
        and isinstance(call.func.value, ast.Call)
    ):
        return False

    super_call = call.func.value
    if not (isinstance(super_call.func, ast.Name) and super_call.func.id == "super"):
        return False
    if super_call.keywords:
        return False
    if len(super_call.args) not in {0, 2}:
        return False
    if len(super_call.args) == 2:
        if not (
            isinstance(super_call.args[0], ast.Name)
            and super_call.args[0].id == class_name
            and isinstance(super_call.args[1], ast.Name)
            and super_call.args[1].id == "self"
        ):
            return False

    positional = [*method.args.posonlyargs, *method.args.args]
    parameter_names = [item.arg for item in positional[1:]] if positional else []
    expected_args: list[tuple[str, str]] = [("name", name) for name in parameter_names]
    if method.args.vararg:
        expected_args.append(("star", method.args.vararg.arg))

    actual_args: list[tuple[str, str]] = []
    for item in call.args:
        if isinstance(item, ast.Name):
            actual_args.append(("name", item.id))
        elif isinstance(item, ast.Starred) and isinstance(item.value, ast.Name):
            actual_args.append(("star", item.value.id))
        else:
            return False
    if actual_args != expected_args:
        return False

    expected_keywords = [(name, name) for name in (item.arg for item in method.args.kwonlyargs)]
    if method.args.kwarg:
        expected_keywords.append((None, method.args.kwarg.arg))
    actual_keywords: list[tuple[str | None, str]] = []
    for item in call.keywords:
        if not isinstance(item.value, ast.Name):
            return False
        actual_keywords.append((item.arg, item.value.id))
    return actual_keywords == expected_keywords


def _local_base_node(
    source_code: str,
    *,
    base_name: str,
) -> ast.ClassDef | None:
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    short = base_name.rsplit(".", 1)[-1]
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == short
    ]
    return matches[0] if len(matches) == 1 else None


def select_python_inline_class_strategy(
    source_code: str,
    *,
    class_to_inline: str,
    module_name: str = "",
    project_source_files: Sequence[dict[str, Any]] | None = None,
    current_file_name: str = "",
) -> dict[str, Any]:
    """Choose a safe Inline Class strategy or an explicit non-apply outcome.

    The goal is not to force every class to disappear.  Inline Class is applied
    when its semantic preconditions are proven, reported ``not_applicable``
    when the class has a distinct framework/configuration responsibility, and
    reported ``review_required`` only when the available repository evidence is
    genuinely insufficient.
    """

    resolution = resolve_python_inline_class_target(
        source_code,
        class_to_inline=class_to_inline,
        module_name=module_name,
    )
    if resolution.get("status") != "success":
        return resolution

    item = dict(resolution["class_model"])
    profile = _direct_class_member_profile(
        source_code,
        local_qualified_name=str(item.get("local_qualified_name") or item.get("class_name") or ""),
    )

    if item["dynamic_references"]:
        return {
            **resolution,
            "status": "review_required",
            "reason": "DYNAMIC_CLASS_REFERENCE_REQUIRES_REVIEW",
            "strategy": "dynamic_reference_protection",
        }

    # A concrete registration is positive evidence that the class object itself
    # is part of a runtime contract.  This is not uncertainty; Inline Class is
    # structurally inappropriate for that target.
    if item["registration_references"]:
        return {
            **resolution,
            "status": "not_applicable",
            "reason": "FRAMEWORK_CLASS_IDENTITY_REQUIRED",
            "strategy": "framework_identity_protection",
        }

    if item["metaclass"]:
        return {
            **resolution,
            "status": "review_required",
            "reason": "METACLASS_SEMANTICS_REQUIRE_REVIEW",
            "strategy": "metaclass_preservation",
        }
    if item["decorators"]:
        return {
            **resolution,
            "status": "review_required",
            "reason": "DECORATOR_SEMANTICS_REQUIRE_REVIEW",
            "strategy": "decorator_preservation",
        }
    if item["parent_qualified_name"]:
        return {
            **resolution,
            "status": "review_required",
            "reason": "NESTED_CLASS_REQUIRES_UNAMBIGUOUS_OWNER_MIGRATION",
            "strategy": "nested_class_analysis",
        }

    # A base used by several subclasses is not a Lazy Class candidate for a
    # one-destination Inline Class operation.  The planner needs a hierarchy
    # refactoring instead (for example Collapse Hierarchy with explicit scope).
    if item["subclasses"]:
        if len(item["subclasses"]) > 1:
            return {
                **resolution,
                "status": "not_applicable",
                "reason": "BASE_CLASS_SHARED_BY_MULTIPLE_SUBCLASSES",
                "strategy": "hierarchy_refactoring_required",
            }
        return {
            **resolution,
            "status": "review_required",
            "reason": "BASE_CLASS_INLINE_REQUIRES_EXPLICIT_DESTINATION",
            "strategy": "inheritance_aware_analysis",
        }

    if item["bases"]:
        if len(item["bases"]) > 1:
            return {
                **resolution,
                "status": "review_required",
                "reason": "MULTIPLE_INHERITANCE_REQUIRES_REVIEW",
                "strategy": "inheritance_aware_analysis",
            }
        if item["type_identity_checks"]:
            return {
                **resolution,
                "status": "review_required",
                "reason": "POLYMORPHIC_TYPE_IDENTITY_REQUIRED",
                "strategy": "inheritance_aware_analysis",
            }

        if profile.get("status") != "success":
            return {
                **resolution,
                "status": "review_required",
                "reason": "INHERITANCE_MEMBER_MODEL_UNAVAILABLE",
                "strategy": "inheritance_aware_analysis",
            }

        class_fields = list(profile.get("class_fields") or [])
        nested_classes = list(profile.get("nested_classes") or [])
        business_methods = list(profile.get("business_methods") or [])
        if class_fields or nested_classes:
            return {
                **resolution,
                "status": "not_applicable",
                "reason": "INHERITANCE_CLASS_HAS_CONFIGURATION_OR_STATE",
                "strategy": "distinct_class_contract_preservation",
                "class_fields": class_fields,
                "nested_classes": nested_classes,
            }

        base_name = str(item["bases"][0])
        base_node = _local_base_node(source_code, base_name=base_name)
        if business_methods:
            # If the base is not local, this is typically a framework/library
            # extension point.  The per-file engine cannot safely transfer the
            # override into that external class.
            if base_node is None:
                return {
                    **resolution,
                    "status": "not_applicable",
                    "reason": "EXTERNAL_BASE_CLASS_CONTRACT_REQUIRED",
                    "strategy": "external_base_identity_protection",
                }
            return {
                **resolution,
                "status": "review_required",
                "reason": "INHERITANCE_COLLAPSE_REQUIRES_REPOSITORY_PROOF",
                "strategy": "inheritance_aware_analysis",
            }

        target_node = profile.get("node")
        constructor = None
        if isinstance(target_node, ast.ClassDef):
            constructor = next(
                (
                    member for member in target_node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == "__init__"
                ),
                None,
            )
        base_init = None
        if isinstance(base_node, ast.ClassDef):
            base_init = next(
                (
                    member for member in base_node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == "__init__"
                ),
                None,
            )

        # Empty marker subclasses and exact forwarding subclasses are genuine
        # safe inheritance-collapse candidates when the base is local.
        if base_node is not None and (
            bool(profile.get("pass_only"))
            or _is_trivial_forwarding_init(
                constructor,
                class_name=str(item.get("class_name") or ""),
                base_init=base_init,
            )
        ):
            return {
                **resolution,
                "status": "success",
                "strategy": "simple_inheritance_collapse",
            }

        if item["super_calls"]:
            return {
                **resolution,
                "status": "review_required",
                "reason": "SUPER_CALL_SEMANTICS_REQUIRE_REVIEW",
                "strategy": "inheritance_aware_analysis",
            }
        return {
            **resolution,
            "status": "review_required",
            "reason": "INHERITANCE_COLLAPSE_REQUIRES_REPOSITORY_PROOF",
            "strategy": "inheritance_aware_analysis",
        }

    # A plain/module-local Inline Class operation is only safe when the target
    # symbol is not imported, instantiated, inherited from, or otherwise
    # referenced by another repository file.  The per-file transformation
    # engine cannot atomically rewrite those peers, so preserve the class and
    # report the exact dependency set instead of removing it locally.
    external_reference_files = _repository_reference_files(
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
        class_name=str(item.get("class_name") or class_to_inline),
    )
    if external_reference_files:
        return {
            **resolution,
            "status": "review_required",
            "reason": "EXTERNAL_CLASS_REFERENCES_REQUIRE_REPOSITORY_INLINE",
            "strategy": "repository_atomic_inline_required",
            "reference_files": external_reference_files,
        }

    return {
        **resolution,
        "status": "success",
        "strategy": "composition_or_plain_inline",
    }

def _apply_simple_inheritance_collapse(
    source_code: str,
    *,
    class_to_inline: str,
    project_source_files: Sequence[dict[str, Any]] | None,
    current_file_name: str,
) -> Tuple[str, int, dict[str, Any]]:
    """Collapse an empty marker subclass into its direct base when proven safe."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(source_code, class_to_inline=class_to_inline, reason="SOURCE_PARSE_FAILED")
    targets = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_to_inline
    ]
    if len(targets) != 1 or len(targets[0].bases) != 1:
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="SIMPLE_INHERITANCE_TARGET_NOT_FOUND",
        )
    target = targets[0]
    base_name = _dotted_name(target.bases[0])
    if not base_name:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INHERITANCE_BASE_REFERENCE_UNRESOLVED",
        )
    base_short_name = base_name.rsplit(".", 1)[-1]
    base_nodes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == base_short_name
    ]
    if len(base_nodes) != 1 or any(
        isinstance(node, ast.FunctionDef) and node.name == "__init_subclass__"
        for node in base_nodes[0].body
    ):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INHERITANCE_BASE_HOOK_OR_REFERENCE_REQUIRES_REVIEW",
        )

    body = _strip_docstring(target.body)
    non_pass = [statement for statement in body if not isinstance(statement, ast.Pass)]
    if non_pass:
        if len(non_pass) != 1 or not isinstance(
            non_pass[0], (ast.FunctionDef, ast.AsyncFunctionDef)
        ) or non_pass[0].name != "__init__":
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="INHERITANCE_CLASS_HAS_IMPLEMENTATION",
            )
        base_init = next(
            (
                member for member in base_nodes[0].body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name == "__init__"
            ),
            None,
        )
        if not _is_trivial_forwarding_init(
            non_pass[0],
            class_name=class_to_inline,
            base_init=base_init,
        ):
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="INHERITANCE_CLASS_HAS_IMPLEMENTATION",
            )
    external_reference_files = _repository_reference_files(
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
        class_name=class_to_inline,
    )
    if external_reference_files:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="EXTERNAL_INHERITANCE_REFERENCE_REQUIRES_REVIEW",
            reference_files=external_reference_files,
        )

    parents = _parents(tree)
    offsets = _line_offsets(source_code)
    edits: list[tuple[int, int, str]] = [(
        _position_offset(offsets, target.lineno, 0),
        _line_end_offset(source_code, offsets, target.end_lineno),
        "",
    )]
    constructor_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == class_to_inline):
            continue
        if _is_descendant(node, target, parents):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Call) and parent.func is node:
            start = _position_offset(offsets, node.lineno, node.col_offset)
            end = _position_offset(offsets, node.end_lineno, node.end_col_offset)
            edits.append((start, end, base_name))
            constructor_calls += 1
            continue
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INHERITANCE_REFERENCE_REQUIRES_REWRITE",
        )
    if not _edits_do_not_overlap(edits):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="OVERLAPPING_INHERITANCE_COLLAPSE_EDITS",
        )
    transformed = _apply_edits(source_code, edits)
    try:
        after_tree = ast.parse(transformed)
        compile(transformed, "<sctva-inline-inheritance-collapse>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TRANSFORMED_SOURCE_PARSE_FAILED",
        )
    if any(
        isinstance(node, ast.ClassDef) and node.name == class_to_inline
        for node in after_tree.body
    ):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TARGET_CLASS_STILL_PRESENT",
        )
    return transformed, len(edits), {
        "status": "success",
        "inline_mode": "inheritance_collapse",
        "strategy": "simple_inheritance_collapse",
        "class_to_inline": class_to_inline,
        "destination_class": base_short_name,
        "inlined_fields": [],
        "inlined_methods": [],
        "updated_call_sites": constructor_calls,
        "inheritance_references_removed": True,
    }


def _line_offsets(source_code: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source_code):
        offsets.append(match.end())
    return offsets


def _position_offset(
    line_offsets: Sequence[int],
    line: int | None,
    column: int | None,
) -> int:
    if not isinstance(line, int) or not isinstance(column, int) or line <= 0:
        return -1
    if line > len(line_offsets):
        return -1
    return line_offsets[line - 1] + column


def _line_end_offset(
    source_code: str,
    line_offsets: Sequence[int],
    line: int | None,
) -> int:
    if not isinstance(line, int) or line <= 0:
        return -1
    return line_offsets[line] if line < len(line_offsets) else len(source_code)


def _edits_do_not_overlap(edits: Sequence[tuple[int, int, str]]) -> bool:
    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    previous_end = -1
    for start, end, _ in ordered:
        if start < 0 or end < start or start < previous_end:
            return False
        previous_end = max(previous_end, end)
    return True


def _apply_edits(
    source_code: str,
    edits: Sequence[tuple[int, int, str]],
) -> str:
    transformed = source_code
    for start, end, replacement in sorted(
        edits,
        key=lambda item: item[0],
        reverse=True,
    ):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    return transformed


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_descendant(
    node: ast.AST,
    ancestor: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _class_method(
    owner: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | None:
    matches = [
        item
        for item in owner.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    ]
    return matches[0] if len(matches) == 1 else None


def _strip_docstring(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    statements = list(body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


def _constructor_model(
    constructor: ast.FunctionDef | None,
) -> tuple[list[str], dict[str, ast.AST], dict[str, ast.AST], str]:
    """Return constructor params, defaults, and ``self.field`` expressions."""

    if constructor is None:
        return [], {}, {}, "CONSTRUCTOR_REQUIRED_FOR_OWNED_INLINE"

    args = constructor.args
    if (
        constructor.decorator_list
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or not args.args
        or args.args[0].arg != "self"
    ):
        return [], {}, {}, "CONSTRUCTOR_SIGNATURE_UNSUPPORTED"

    parameters = [argument.arg for argument in args.args[1:]]
    defaults: dict[str, ast.AST] = {}
    if args.defaults:
        default_parameter_names = parameters[-len(args.defaults):]
        defaults = {
            name: copy.deepcopy(value)
            for name, value in zip(default_parameter_names, args.defaults)
        }

    fields: dict[str, ast.AST] = {}
    parameter_names = set(parameters)

    constructor_body = [
        statement
        for statement in _strip_docstring(constructor.body)
        if not isinstance(statement, ast.Pass)
    ]

    for statement in constructor_body:
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name)
            and statement.targets[0].value.id == "self"
            and isinstance(statement.targets[0].ctx, ast.Store)
        ):
            return [], {}, {}, "CONSTRUCTOR_STATE_UNSUPPORTED"

        field_name = statement.targets[0].attr
        if field_name in fields:
            return [], {}, {}, "DUPLICATE_CONSTRUCTOR_FIELD"

        # Permit parameter-derived/literal expressions, but reject calls,
        # attribute reads, comprehensions, lambdas, etc.  Inline Class must not
        # duplicate hidden side effects from the helper constructor.
        for node in ast.walk(statement.value):
            if isinstance(
                node,
                (
                    ast.Call,
                    ast.Attribute,
                    ast.Subscript,
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.Await,
                    ast.Yield,
                    ast.YieldFrom,
                ),
            ):
                return [], {}, {}, "CONSTRUCTOR_FIELD_EXPRESSION_UNSAFE"
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in parameter_names:
                    return [], {}, {}, "CONSTRUCTOR_FIELD_EXTERNAL_DEPENDENCY"

        fields[field_name] = copy.deepcopy(statement.value)

    if not fields:
        return [], {}, {}, "NO_CONSTRUCTOR_FIELDS"

    return parameters, defaults, fields, ""


def _bind_constructor_arguments(
    *,
    call: ast.Call,
    parameters: Sequence[str],
    defaults: dict[str, ast.AST],
) -> tuple[dict[str, ast.AST], str]:
    if any(keyword.arg is None for keyword in call.keywords):
        return {}, "CONSTRUCTOR_STAR_ARGUMENTS_UNSUPPORTED"
    if len(call.args) > len(parameters):
        return {}, "CONSTRUCTOR_ARGUMENT_COUNT_MISMATCH"

    bound: dict[str, ast.AST] = {}
    for name, value in zip(parameters, call.args):
        bound[name] = copy.deepcopy(value)

    for keyword in call.keywords:
        if keyword.arg not in parameters:
            return {}, "CONSTRUCTOR_UNKNOWN_KEYWORD"
        if keyword.arg in bound:
            return {}, "CONSTRUCTOR_DUPLICATE_ARGUMENT"
        bound[keyword.arg] = copy.deepcopy(keyword.value)

    for name in parameters:
        if name not in bound and name in defaults:
            bound[name] = copy.deepcopy(defaults[name])

    missing = [name for name in parameters if name not in bound]
    if missing:
        return {}, "CONSTRUCTOR_MISSING_ARGUMENT"

    return bound, ""


class _ParameterSubstituter(ast.NodeTransformer):
    def __init__(self, values: dict[str, ast.AST]) -> None:
        self.values = values

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.values:
            return ast.copy_location(copy.deepcopy(self.values[node.id]), node)
        return node


def _render_bound_field_expression(
    expression: ast.AST,
    bound: dict[str, ast.AST],
) -> str:
    rewritten = _ParameterSubstituter(bound).visit(copy.deepcopy(expression))
    ast.fix_missing_locations(rewritten)
    try:
        return ast.unparse(rewritten)
    except Exception:
        return ""


def _method_safety_error(
    method: ast.FunctionDef,
    *,
    field_names: set[str],
    method_names: set[str],
) -> str:
    args = method.args
    if (
        method.decorator_list
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or not args.args
        or args.args[0].arg != "self"
    ):
        return "METHOD_SIGNATURE_UNSUPPORTED"

    parents = _parents(method)
    for node in ast.walk(method):
        if node is not method and isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return "NESTED_SCOPE_UNSUPPORTED"

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and node.attr not in field_names | method_names:
                return "SOURCE_INSTANCE_DEPENDENCY"

        if isinstance(node, ast.Name) and node.id == "self":
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in field_names | method_names
            ):
                return "SOURCE_INSTANCE_DEPENDENCY"

    return ""


def _is_field_centric_expression(
    expression: ast.AST,
    *,
    allowed_names: set[str],
) -> bool:
    """Return whether an expression only reads helper state/arguments.

    This intentionally accepts formatting and small arithmetic over fields. A
    wrapper does not become an independent responsibility merely because a
    getter formats ``self.name`` before returning it.
    """

    for node in ast.walk(expression):
        if isinstance(node, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in allowed_names and node.id != "self":
                return False
        if isinstance(node, ast.Attribute):
            if not (
                isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                return False
    return True


def _is_trivial_wrapper_method(
    method: ast.FunctionDef,
    *,
    field_names: set[str],
    method_names: set[str],
) -> bool:
    """Recognise accessors, mutators, and one-hop forwarding methods.

    The previous implementation counted every method other than ``__init__``
    as a responsibility.  That rejected harmless value wrappers with several
    getters/setters.  This test is deliberately narrow: control flow, calls to
    arbitrary collaborators, nested scopes, and non-field state remain real
    behaviour and therefore do not pass as trivial.
    """

    body = _strip_docstring(method.body)
    if len(body) != 1:
        return False
    statement = body[0]
    arguments = {argument.arg for argument in method.args.args[1:]}
    allowed_names = arguments | {"self"}

    if isinstance(statement, ast.Return):
        value = statement.value
        if value is None:
            return True
        # A direct forwarding call such as ``return self.label()`` stays a
        # small façade.  The called helper is validated separately.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "self"
            and value.func.attr in method_names
            and all(
                _is_field_centric_expression(
                    argument,
                    allowed_names=allowed_names,
                )
                for argument in value.args
            )
            and all(
                keyword.arg is not None
                and _is_field_centric_expression(
                    keyword.value,
                    allowed_names=allowed_names,
                )
                for keyword in value.keywords
            )
        ):
            return True
        return _is_field_centric_expression(value, allowed_names=allowed_names)

    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Attribute)
        and isinstance(statement.targets[0].value, ast.Name)
        and statement.targets[0].value.id == "self"
        and statement.targets[0].attr in field_names
    ):
        return _is_field_centric_expression(
            statement.value,
            allowed_names=allowed_names,
        )

    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        call = statement.value
        return (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            and call.func.attr in field_names
        )
    return False


def _inline_class_responsibility_profile(
    *,
    business_methods: Sequence[ast.FunctionDef],
    field_names: set[str],
) -> dict[str, Any]:
    """Measure semantic wrapper responsibility without inflating boilerplate."""

    method_names = {method.name for method in business_methods}
    trivial = [
        method.name
        for method in business_methods
        if _is_trivial_wrapper_method(
            method,
            field_names=field_names,
            method_names=method_names,
        )
    ]
    meaningful = [
        method.name
        for method in business_methods
        if method.name not in trivial
    ]
    return {
        "state_field_count": len(field_names),
        "method_count": len(business_methods),
        "trivial_methods": trivial,
        "meaningful_methods": meaningful,
        "effective_responsibility_count": len(meaningful),
    }


def _repository_reference_files(
    *,
    project_source_files: Sequence[dict[str, Any]],
    current_file_name: str,
    class_name: str,
) -> list[str]:
    """Find conservative cross-file references to a class symbol.

    The per-file engine cannot safely commit a partial cross-file rewrite.
    Detecting these references here keeps the class intact and gives the
    coordinator a precise reason to schedule an atomic repository edit.
    """

    current_path = str(current_file_name or "").replace("\\", "/").lower()
    references: list[str] = []
    for item in project_source_files:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or item.get("name") or item.get("path") or "")
        normalized_name = file_name.replace("\\", "/").lower()
        if current_path and normalized_name == current_path:
            continue
        language = str(item.get("language") or "").strip().lower()
        if language and language != "python" and not normalized_name.endswith(".py"):
            continue
        code = item.get("source_code") or item.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        try:
            peer_tree = ast.parse(code)
        except SyntaxError:
            # A source file which cannot be analysed prevents a reliable
            # absence proof for a public class name.
            references.append(file_name or "<unparsed-python-source>")
            continue
        defines_same_class = any(
            isinstance(node, ast.ClassDef) and node.name == class_name
            for node in peer_tree.body
        )
        for node in ast.walk(peer_tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == class_name for alias in node.names
            ):
                references.append(file_name)
                break
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[-1] == class_name for alias in node.names
            ):
                references.append(file_name)
                break
            if isinstance(node, ast.Name) and node.id == class_name and not defines_same_class:
                references.append(file_name)
                break
            if isinstance(node, ast.Attribute) and node.attr == class_name:
                references.append(file_name)
                break
    return sorted({name for name in references if name})



def _repository_inline_normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _repository_inline_source_entry(
    project_source_files: Sequence[dict[str, Any]],
    file_name: str,
) -> dict[str, Any] | None:
    wanted = _repository_inline_normalize_path(file_name)
    matches = [
        item
        for item in project_source_files
        if isinstance(item, dict)
        and _repository_inline_normalize_path(
            str(item.get("file_name") or item.get("name") or item.get("path") or "")
        )
        == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _repository_inline_bound_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _repository_inline_unique_aliases(
    *,
    tree: ast.Module,
    class_name: str,
    method_names: Sequence[str],
) -> dict[str, str]:
    bound = _repository_inline_bound_module_names(tree)
    aliases: dict[str, str] = {}
    reserved = set(bound)
    for method_name in method_names:
        candidate = method_name
        if candidate in reserved:
            stem = f"_sctva_{class_name.lower()}_{method_name}"
            candidate = stem
            suffix = 2
            while candidate in reserved:
                candidate = f"{stem}_{suffix}"
                suffix += 1
        aliases[method_name] = candidate
        reserved.add(candidate)
    return aliases


def _repository_inline_nearest_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current
        current = parents.get(current)
    return None


def _repository_inline_is_descendant(
    node: ast.AST,
    ancestor: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = parents.get(node)
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _repository_inline_class_uses_inherited_contract(
    class_node: ast.ClassDef,
    *,
    method_names: set[str],
) -> str:
    for node in ast.walk(class_node):
        if node is class_node:
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
            return "SUBCLASS_SUPER_CALL_REQUIRES_REVIEW"
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "cls"}
            and node.attr in method_names
        ):
            return "SUBCLASS_USES_INHERITED_MEMBER_REQUIRES_REWRITE"
    return ""


def _repository_inline_render_import(
    node: ast.ImportFrom,
    *,
    class_name: str,
    method_aliases: dict[str, str],
) -> str:
    names: list[ast.alias] = []
    for alias in node.names:
        if alias.name != class_name:
            names.append(ast.alias(name=alias.name, asname=alias.asname))
            continue
        for method_name, local_name in method_aliases.items():
            names.append(
                ast.alias(
                    name=method_name,
                    asname=None if local_name == method_name else local_name,
                )
            )
    replacement = ast.ImportFrom(module=node.module, names=names, level=node.level)
    return ast.unparse(replacement)


def _repository_inline_rewrite_peer(
    source_code: str,
    *,
    class_name: str,
    method_names: Sequence[str],
    file_name: str,
) -> tuple[str, int, dict[str, Any]]:
    """Rewrite one external user of a stateless class to module functions.

    This intentionally supports only evidence that can be proven equivalent:
    direct ``from ... import Class`` imports, no-argument constructions stored
    in a simple local name, direct calls through that local instance, direct
    ``Class().method(...)`` calls, and a single direct inheritance edge whose
    subclass does not use ``super()`` or inherited members.  Anything more
    dynamic remains review-required instead of guessing.
    """

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0, {
            "status": "review_required",
            "reason": "REPOSITORY_PEER_PARSE_FAILED",
            "file_name": file_name,
        }

    methods = tuple(dict.fromkeys(str(name) for name in method_names if str(name)))
    if not methods:
        return source_code, 0, {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_METHOD_SET_EMPTY",
            "file_name": file_name,
        }
    method_set = set(methods)
    parents = _parents(tree)
    line_offsets = _line_offsets(source_code)
    method_aliases = _repository_inline_unique_aliases(
        tree=tree,
        class_name=class_name,
        method_names=methods,
    )

    import_nodes: list[ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != class_name:
                    continue
                if alias.asname:
                    return source_code, 0, {
                        "status": "review_required",
                        "reason": "ALIASED_CLASS_IMPORT_REQUIRES_REVIEW",
                        "file_name": file_name,
                    }
                import_nodes.append(node)
        elif isinstance(node, ast.Import) and any(
            alias.name.split(".")[-1] == class_name for alias in node.names
        ):
            return source_code, 0, {
                "status": "review_required",
                "reason": "MODULE_STYLE_CLASS_IMPORT_REQUIRES_REVIEW",
                "file_name": file_name,
            }
        elif isinstance(node, ast.Attribute) and node.attr == class_name:
            return source_code, 0, {
                "status": "review_required",
                "reason": "QUALIFIED_CLASS_REFERENCE_REQUIRES_REVIEW",
                "file_name": file_name,
            }

    if len({id(node) for node in import_nodes}) > 1:
        return source_code, 0, {
            "status": "review_required",
            "reason": "MULTIPLE_CLASS_IMPORT_SITES_REQUIRE_REVIEW",
            "file_name": file_name,
        }

    edits: list[tuple[int, int, str]] = []
    handled_class_names: set[int] = set()
    rewritten_call_attributes: set[int] = set()
    removed_constructions = 0
    updated_call_sites = 0
    collapsed_inheritance = 0

    if import_nodes:
        import_node = import_nodes[0]
        replacement = _repository_inline_render_import(
            import_node,
            class_name=class_name,
            method_aliases=method_aliases,
        )
        edits.append((
            _position_offset(line_offsets, import_node.lineno, import_node.col_offset),
            _position_offset(line_offsets, import_node.end_lineno, import_node.end_col_offset),
            replacement,
        ))

    # Collapse a direct ``class Child(Target):`` edge only when the subclass
    # has no behavior depending on that inheritance contract.
    for class_node in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
        matching_bases = [
            base for base in class_node.bases
            if isinstance(base, ast.Name) and base.id == class_name
        ]
        if not matching_bases:
            continue
        if (
            len(class_node.bases) != 1
            or len(matching_bases) != 1
            or class_node.keywords
            or class_node.decorator_list
        ):
            return source_code, 0, {
                "status": "review_required",
                "reason": "COMPLEX_SUBCLASS_INHERITANCE_REQUIRES_REVIEW",
                "file_name": file_name,
            }
        inheritance_error = _repository_inline_class_uses_inherited_contract(
            class_node,
            method_names=method_set,
        )
        if inheritance_error:
            return source_code, 0, {
                "status": "review_required",
                "reason": inheritance_error,
                "file_name": file_name,
                "subclass": class_node.name,
            }
        base = matching_bases[0]
        handled_class_names.add(id(base))
        if class_node.lineno != base.lineno:
            return source_code, 0, {
                "status": "review_required",
                "reason": "MULTILINE_SUBCLASS_HEADER_REQUIRES_REVIEW",
                "file_name": file_name,
            }
        lines = source_code.splitlines(keepends=True)
        raw_line = lines[class_node.lineno - 1]
        newline = "\r\n" if raw_line.endswith("\r\n") else "\n" if raw_line.endswith("\n") else ""
        body = raw_line[:-len(newline)] if newline else raw_line
        pattern = re.compile(
            rf"^(?P<indent>\s*)class\s+{re.escape(class_node.name)}\s*\(\s*{re.escape(class_name)}\s*\)\s*:(?P<tail>.*)$"
        )
        match = pattern.match(body)
        if not match:
            return source_code, 0, {
                "status": "review_required",
                "reason": "SUBCLASS_HEADER_REWRITE_UNRESOLVED",
                "file_name": file_name,
            }
        replacement = (
            f"{match.group('indent')}class {class_node.name}:"
            f"{match.group('tail')}{newline}"
        )
        edits.append((
            line_offsets[class_node.lineno - 1],
            _line_end_offset(source_code, line_offsets, class_node.lineno),
            replacement,
        ))
        collapsed_inheritance += 1

    construction_records: list[tuple[ast.Assign, ast.Name, ast.AST]] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == class_name and isinstance(node.ctx, ast.Load)):
            continue
        if id(node) in handled_class_names:
            continue
        parent = parents.get(node)
        grandparent = parents.get(parent) if parent is not None else None

        if isinstance(parent, ast.Call) and parent.func is node:
            if parent.args or parent.keywords:
                return source_code, 0, {
                    "status": "review_required",
                    "reason": "REPOSITORY_INLINE_CONSTRUCTOR_ARGUMENTS_UNSUPPORTED",
                    "file_name": file_name,
                }
            # instance = Target()
            if (
                isinstance(grandparent, ast.Assign)
                and grandparent.value is parent
                and len(grandparent.targets) == 1
                and isinstance(grandparent.targets[0], ast.Name)
            ):
                construction_records.append((grandparent, grandparent.targets[0], node))
                handled_class_names.add(id(node))
                continue
            # Target().method(...)
            if isinstance(grandparent, ast.Attribute) and grandparent.value is parent:
                outer = parents.get(grandparent)
                if (
                    grandparent.attr in method_set
                    and isinstance(outer, ast.Call)
                    and outer.func is grandparent
                ):
                    alias = method_aliases[grandparent.attr]
                    edits.append((
                        _position_offset(line_offsets, grandparent.lineno, grandparent.col_offset),
                        _position_offset(line_offsets, grandparent.end_lineno, grandparent.end_col_offset),
                        alias,
                    ))
                    rewritten_call_attributes.add(id(grandparent))
                    handled_class_names.add(id(node))
                    updated_call_sites += 1
                    continue
            # A standalone side-effect-free Target() can disappear.
            if isinstance(grandparent, ast.Expr) and grandparent.value is parent:
                edits.append((
                    line_offsets[grandparent.lineno - 1],
                    _line_end_offset(source_code, line_offsets, grandparent.end_lineno),
                    "",
                ))
                handled_class_names.add(id(node))
                removed_constructions += 1
                continue

        return source_code, 0, {
            "status": "review_required",
            "reason": "UNSUPPORTED_EXTERNAL_CLASS_REFERENCE",
            "file_name": file_name,
            "line": getattr(node, "lineno", None),
        }

    # Resolve each local instance inside exactly the scope where it is built.
    scope_instance_names: dict[int, set[str]] = {}
    for assignment, target_name, class_name_node in construction_records:
        scope = _repository_inline_nearest_scope(assignment, parents)
        if scope is None:
            return source_code, 0, {
                "status": "review_required",
                "reason": "CONSTRUCTION_SCOPE_UNRESOLVED",
                "file_name": file_name,
            }
        names = scope_instance_names.setdefault(id(scope), set())
        if target_name.id in names:
            return source_code, 0, {
                "status": "review_required",
                "reason": "REPEATED_INSTANCE_NAME_REQUIRES_DATAFLOW_REVIEW",
                "file_name": file_name,
                "instance": target_name.id,
            }
        names.add(target_name.id)

        for candidate in ast.walk(scope):
            if not isinstance(candidate, ast.Name) or candidate.id != target_name.id:
                continue
            if _repository_inline_nearest_scope(candidate, parents) is not scope:
                continue
            if candidate is target_name:
                continue
            candidate_parent = parents.get(candidate)
            if isinstance(candidate.ctx, ast.Store):
                return source_code, 0, {
                    "status": "review_required",
                    "reason": "INSTANCE_REASSIGNMENT_REQUIRES_REVIEW",
                    "file_name": file_name,
                    "instance": target_name.id,
                }
            if (
                isinstance(candidate_parent, ast.Attribute)
                and candidate_parent.value is candidate
                and candidate_parent.attr in method_set
            ):
                call = parents.get(candidate_parent)
                if not (isinstance(call, ast.Call) and call.func is candidate_parent):
                    return source_code, 0, {
                        "status": "review_required",
                        "reason": "BOUND_METHOD_ESCAPE_REQUIRES_REVIEW",
                        "file_name": file_name,
                        "instance": target_name.id,
                    }
                if id(candidate_parent) not in rewritten_call_attributes:
                    alias = method_aliases[candidate_parent.attr]
                    edits.append((
                        _position_offset(
                            line_offsets,
                            candidate_parent.lineno,
                            candidate_parent.col_offset,
                        ),
                        _position_offset(
                            line_offsets,
                            candidate_parent.end_lineno,
                            candidate_parent.end_col_offset,
                        ),
                        alias,
                    ))
                    rewritten_call_attributes.add(id(candidate_parent))
                    updated_call_sites += 1
                continue
            return source_code, 0, {
                "status": "review_required",
                "reason": "INSTANCE_ESCAPES_REPOSITORY_INLINE",
                "file_name": file_name,
                "instance": target_name.id,
                "line": getattr(candidate, "lineno", None),
            }

        edits.append((
            line_offsets[assignment.lineno - 1],
            _line_end_offset(source_code, line_offsets, assignment.end_lineno),
            "",
        ))
        removed_constructions += 1
        handled_class_names.add(id(class_name_node))

    if not import_nodes and (updated_call_sites or collapsed_inheritance or removed_constructions):
        return source_code, 0, {
            "status": "review_required",
            "reason": "CLASS_IMPORT_NOT_FOUND_FOR_REPOSITORY_REWRITE",
            "file_name": file_name,
        }

    if not edits:
        return source_code, 0, {
            "status": "not_applicable",
            "reason": "NO_REWRITABLE_EXTERNAL_REFERENCES",
            "file_name": file_name,
        }
    if not _edits_do_not_overlap(edits):
        return source_code, 0, {
            "status": "review_required",
            "reason": "OVERLAPPING_REPOSITORY_INLINE_EDITS",
            "file_name": file_name,
        }

    transformed = _apply_edits(source_code, edits)
    try:
        transformed_tree = ast.parse(transformed)
        compile(transformed, f"<sctva-repository-inline:{file_name}>", "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        return source_code, 0, {
            "status": "review_required",
            "reason": "TRANSFORMED_REPOSITORY_PEER_PARSE_FAILED",
            "file_name": file_name,
            "error": str(exc),
        }

    stale_names = [
        node
        for node in ast.walk(transformed_tree)
        if isinstance(node, ast.Name) and node.id == class_name
    ]
    stale_imports = [
        node
        for node in ast.walk(transformed_tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == class_name for alias in node.names)
    ]
    stale_bases = [
        node
        for node in ast.walk(transformed_tree)
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == class_name for base in node.bases)
    ]
    if stale_names or stale_imports or stale_bases:
        return source_code, 0, {
            "status": "review_required",
            "reason": "STALE_CLASS_REFERENCE_AFTER_REPOSITORY_INLINE",
            "file_name": file_name,
        }

    return transformed, len(edits), {
        "status": "success",
        "file_name": file_name,
        "method_aliases": method_aliases,
        "removed_instantiations": removed_constructions,
        "updated_call_sites": updated_call_sites,
        "collapsed_inheritance": collapsed_inheritance,
        "import_rewritten": bool(import_nodes),
    }


def plan_repository_inline_class_transaction(
    project_source_files: Sequence[dict[str, Any]],
    *,
    target_file_name: str,
    class_to_inline: str,
) -> dict[str, Any]:
    """Prove that a cross-file Inline Class can be rewritten atomically.

    The current implementation deliberately limits automatic repository Inline
    Class to *stateless* tiny classes.  Stateful helpers continue to use the
    owned-composition strategy or remain review-required because distributing
    per-instance state across arbitrary callers needs stronger interprocedural
    data-flow proof.
    """

    target_entry = _repository_inline_source_entry(project_source_files, target_file_name)
    if target_entry is None:
        return {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_TARGET_FILE_NOT_UNIQUE",
            "target_file": target_file_name,
        }
    target_source = target_entry.get("source_code") or target_entry.get("code")
    if not isinstance(target_source, str):
        return {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_TARGET_SOURCE_MISSING",
            "target_file": target_file_name,
        }

    # Reuse the proven single-file module-function transformer with an isolated
    # target snapshot.  The repository coordinator is responsible for peers.
    from . import python_transformers

    isolated = [
        {
            "file_name": target_file_name,
            "language": "python",
            "source_code": target_source,
        }
    ]
    target_candidate, target_replacements, target_metadata = python_transformers.apply_inline_class(
        target_source,
        class_to_inline=class_to_inline,
        project_source_files=isolated,
        current_file_name=target_file_name,
    )
    if str(target_metadata.get("status") or "") != "success" or target_replacements <= 0:
        return {
            "status": "review_required",
            "reason": str(
                target_metadata.get("reason")
                or "REPOSITORY_INLINE_TARGET_NOT_MODULE_FUNCTION_COMPATIBLE"
            ),
            "target_file": target_file_name,
            "target_metadata": dict(target_metadata),
        }

    inlined_fields = list(target_metadata.get("inlined_fields") or [])
    if inlined_fields:
        return {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_STATEFUL_CLASS_UNSUPPORTED",
            "target_file": target_file_name,
            "inlined_fields": inlined_fields,
        }
    method_names = [
        str(name) for name in target_metadata.get("inlined_methods") or [] if str(name)
    ]
    if not method_names:
        return {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_METHOD_SET_EMPTY",
            "target_file": target_file_name,
        }

    reference_files = _repository_reference_files(
        project_source_files=project_source_files,
        current_file_name=target_file_name,
        class_name=class_to_inline,
    )
    if not reference_files:
        return {
            "status": "not_applicable",
            "reason": "NO_EXTERNAL_REPOSITORY_REFERENCES",
            "target_file": target_file_name,
        }

    peer_plans: list[dict[str, Any]] = []
    for reference_file in reference_files:
        entry = _repository_inline_source_entry(project_source_files, reference_file)
        if entry is None:
            return {
                "status": "review_required",
                "reason": "REPOSITORY_INLINE_REFERENCE_FILE_NOT_UNIQUE",
                "target_file": target_file_name,
                "reference_file": reference_file,
            }
        peer_source = entry.get("source_code") or entry.get("code")
        if not isinstance(peer_source, str):
            return {
                "status": "review_required",
                "reason": "REPOSITORY_INLINE_REFERENCE_SOURCE_MISSING",
                "target_file": target_file_name,
                "reference_file": reference_file,
            }
        _, replacements, metadata = _repository_inline_rewrite_peer(
            peer_source,
            class_name=class_to_inline,
            method_names=method_names,
            file_name=reference_file,
        )
        if str(metadata.get("status") or "") != "success" or replacements <= 0:
            return {
                "status": "review_required",
                "reason": str(metadata.get("reason") or "REPOSITORY_REFERENCE_REWRITE_UNSAFE"),
                "target_file": target_file_name,
                "reference_file": reference_file,
                "peer_metadata": metadata,
            }
        peer_plans.append(metadata)

    return {
        "status": "success",
        "strategy": "repository_atomic_module_function",
        "target_file": target_file_name,
        "class_to_inline": class_to_inline,
        "reference_files": reference_files,
        "method_names": method_names,
        "target_replacements": target_replacements,
        "target_metadata": dict(target_metadata),
        "peer_plans": peer_plans,
        "target_candidate": target_candidate,
    }


def apply_repository_inline_class_transaction(
    project_source_files: Sequence[dict[str, Any]],
    *,
    target_file_name: str,
    class_to_inline: str,
) -> dict[str, Any]:
    """Build an all-or-nothing candidate workspace for repository Inline Class."""

    plan = plan_repository_inline_class_transaction(
        project_source_files,
        target_file_name=target_file_name,
        class_to_inline=class_to_inline,
    )
    if plan.get("status") != "success":
        return plan

    transformed_sources: dict[str, str] = {
        str(target_file_name): str(plan["target_candidate"]),
    }
    replacement_counts: dict[str, int] = {
        str(target_file_name): int(plan.get("target_replacements") or 0),
    }
    peer_metadata: list[dict[str, Any]] = []
    for reference_file in plan.get("reference_files") or []:
        entry = _repository_inline_source_entry(project_source_files, str(reference_file))
        if entry is None:
            return {
                "status": "review_required",
                "reason": "REPOSITORY_INLINE_REFERENCE_FILE_DISAPPEARED",
                "reference_file": reference_file,
            }
        source = str(entry.get("source_code") or entry.get("code") or "")
        transformed, replacements, metadata = _repository_inline_rewrite_peer(
            source,
            class_name=class_to_inline,
            method_names=plan.get("method_names") or [],
            file_name=str(reference_file),
        )
        if str(metadata.get("status") or "") != "success" or replacements <= 0:
            return {
                "status": "review_required",
                "reason": str(metadata.get("reason") or "REPOSITORY_REFERENCE_REWRITE_UNSAFE"),
                "reference_file": reference_file,
                "peer_metadata": metadata,
            }
        transformed_sources[str(reference_file)] = transformed
        replacement_counts[str(reference_file)] = replacements
        peer_metadata.append(metadata)

    candidate_repository: list[dict[str, Any]] = []
    for item in project_source_files:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or item.get("name") or item.get("path") or "")
        candidate_repository.append({
            **item,
            "file_name": file_name,
            "source_code": transformed_sources.get(
                file_name,
                str(item.get("source_code") or item.get("code") or ""),
            ),
        })

    target_candidate = transformed_sources[str(target_file_name)]
    try:
        target_tree = ast.parse(target_candidate)
    except SyntaxError as exc:
        return {
            "status": "review_required",
            "reason": "REPOSITORY_INLINE_TARGET_PARSE_FAILED",
            "error": str(exc),
        }
    if any(
        isinstance(node, ast.ClassDef) and node.name == class_to_inline
        for node in target_tree.body
    ):
        return {
            "status": "review_required",
            "reason": "TARGET_CLASS_STILL_PRESENT_AFTER_REPOSITORY_INLINE",
        }
    missing_methods = [
        method_name
        for method_name in plan.get("method_names") or []
        if not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
            for node in target_tree.body
        )
    ]
    if missing_methods:
        return {
            "status": "review_required",
            "reason": "INLINED_MODULE_FUNCTION_MISSING",
            "missing_methods": missing_methods,
        }

    stale_files = _repository_reference_files(
        project_source_files=candidate_repository,
        current_file_name=target_file_name,
        class_name=class_to_inline,
    )
    if stale_files:
        return {
            "status": "review_required",
            "reason": "STALE_REPOSITORY_CLASS_REFERENCES_AFTER_INLINE",
            "reference_files": stale_files,
        }

    return {
        "status": "success",
        "strategy": "repository_atomic_module_function",
        "class_to_inline": class_to_inline,
        "target_file": target_file_name,
        "reference_files": list(plan.get("reference_files") or []),
        "method_names": list(plan.get("method_names") or []),
        "transformed_sources": transformed_sources,
        "replacement_counts": replacement_counts,
        "target_metadata": dict(plan.get("target_metadata") or {}),
        "peer_metadata": peer_metadata,
        "candidate_repository": candidate_repository,
    }

def _was_enriched_by_prior_move(
    class_to_inline: str,
    prior_transformations: Sequence[dict[str, Any]],
) -> bool:
    """Recognise a class that gained real behavior earlier in this run."""

    return any(
        str(item.get("action_type") or "") == "move_python_method"
        and str(item.get("status") or "").lower() in {"success", "already_applied"}
        and str(item.get("destination_class") or "") == class_to_inline
        for item in prior_transformations
        if isinstance(item, dict)
    )


def _owner_self_fields(owner: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(owner):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Store)
        ):
            fields.add(node.attr)
    return fields


def _owner_method_names(owner: ast.ClassDef) -> set[str]:
    return {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _find_owned_constructions(
    *,
    tree: ast.Module,
    target_class: ast.ClassDef,
) -> list[dict[str, Any]]:
    constructions: list[dict[str, Any]] = []
    for owner in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
        if owner is target_class:
            continue
        constructor = _class_method(owner, "__init__")
        if constructor is None:
            continue
        for statement in ast.walk(constructor):
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = getattr(statement, "value", None)
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == target_class.name
            ):
                continue

            target: ast.AST | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target

            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and isinstance(target.ctx, ast.Store)
            ):
                continue

            constructions.append(
                {
                    "owner_class": owner,
                    "owner_constructor": constructor,
                    "owner_attribute": target.attr,
                    "statement": statement,
                    "call": value,
                }
            )
    return constructions


def _class_reference_is_allowed(
    *,
    node: ast.Name,
    target_class: ast.ClassDef,
    construction_call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if _is_descendant(node, target_class, parents):
        return True
    parent = parents.get(node)
    return parent is construction_call and construction_call.func is node


def _owner_attribute_usage_error(
    *,
    tree: ast.Module,
    target_class: ast.ClassDef,
    owner_attribute: str,
    field_names: set[str],
    method_names: set[str],
    construction_statement: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    allowed_members = field_names | method_names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != owner_attribute:
            continue
        if _is_descendant(node, target_class, parents):
            continue
        if _is_descendant(node, construction_statement, parents):
            continue

        outer = parents.get(node)
        if not (
            isinstance(outer, ast.Attribute)
            and outer.value is node
            and outer.attr in allowed_members
        ):
            return "OWNER_ATTRIBUTE_ESCAPES_OR_IS_USED_AS_OBJECT"
    return ""


def _method_source(
    source_code: str,
    method: ast.FunctionDef,
) -> str:
    lines = source_code.splitlines(keepends=True)
    if method.lineno <= 0 or method.end_lineno is None:
        return ""
    selected = lines[method.lineno - 1:method.end_lineno]
    if not selected:
        return ""
    indent = " " * int(method.col_offset or 0)
    normalized: list[str] = []
    for line in selected:
        if line.strip() and line.startswith(indent):
            normalized.append(line[len(indent):])
        else:
            normalized.append(line)
    return "".join(normalized).rstrip()


def _chain_rewrite_edits(
    *,
    source_code: str,
    tree: ast.Module,
    target_class: ast.ClassDef,
    construction_statement: ast.AST,
    owner_attribute: str,
    field_names: set[str],
    method_names: set[str],
    line_offsets: Sequence[int],
    parents: dict[ast.AST, ast.AST],
) -> tuple[list[tuple[int, int, str]], int, str]:
    edits: list[tuple[int, int, str]] = []
    updated = 0

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == owner_attribute
            and node.attr in field_names | method_names
        ):
            continue
        if _is_descendant(node, target_class, parents):
            continue
        if _is_descendant(node, construction_statement, parents):
            continue

        base_expression = node.value.value
        try:
            base_text = ast.unparse(base_expression)
        except Exception:
            return [], 0, "ATTRIBUTE_REWRITE_FAILED"

        replacement = f"{base_text}.{node.attr}"
        start = _position_offset(line_offsets, node.lineno, node.col_offset)
        end = _position_offset(line_offsets, node.end_lineno, node.end_col_offset)
        if start < 0 or end < start:
            return [], 0, "ATTRIBUTE_REWRITE_POSITION_FAILED"
        edits.append((start, end, replacement))
        updated += 1

    return edits, updated, ""


def apply_owned_inline_class(
    source_code: str,
    *,
    class_to_inline: str,
    preferred_destination_class: str = "",
    preferred_owner_attribute: str = "",
    project_source_files: Sequence[dict[str, Any]] | None = None,
    current_file_name: str = "",
    prior_lineage: Sequence[dict[str, Any]] | None = None,
    prior_transformations: Sequence[dict[str, Any]] | None = None,
) -> Tuple[str, int, dict[str, Any]]:
    """Inline a tiny helper class into its unique owning Python class.

    ``not_applicable`` means the target is not the owned-composition pattern;
    callers may safely try another Inline Class strategy.  ``review_required``
    means the owned pattern was found but cannot be rewritten safely and must
    not fall back to a looser transformation.
    """

    strategy = select_python_inline_class_strategy(
        source_code,
        class_to_inline=class_to_inline,
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
    )
    if strategy.get("status") != "success":
        if strategy.get("status") == "not_applicable":
            return _not_applicable(
                source_code,
                class_to_inline=str(class_to_inline or ""),
                reason=str(strategy.get("reason") or "INLINE_CLASS_TARGET_NOT_FOUND"),
            )
        return _review(
            source_code,
            class_to_inline=str(class_to_inline or ""),
            reason=str(strategy.get("reason") or "INLINE_CLASS_REVIEW_REQUIRED"),
            strategy=strategy.get("strategy"),
            qualified_class_name=strategy.get("qualified_class_name"),
            class_model=strategy.get("class_model"),
            reference_files=list(strategy.get("reference_files") or []),
        )
    if strategy.get("strategy") == "simple_inheritance_collapse":
        return _apply_simple_inheritance_collapse(
            source_code,
            class_to_inline=str(strategy.get("class_name") or class_to_inline),
            project_source_files=project_source_files,
            current_file_name=current_file_name,
        )
    class_to_inline = str(strategy.get("class_name") or class_to_inline)

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(class_to_inline or "")):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INVALID_CLASS_TARGET",
        )

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="SOURCE_PARSE_FAILED",
        )

    target_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_to_inline
    ]
    if len(target_classes) != 1:
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="TARGET_CLASS_NOT_FOUND_OR_NOT_UNIQUE",
        )
    target_class = target_classes[0]

    methods = [
        node
        for node in target_class.body
        if isinstance(node, ast.FunctionDef)
    ]
    constructor = next((item for item in methods if item.name == "__init__"), None)
    business_methods = [item for item in methods if item.name != "__init__"]

    # A prior Move Method can legitimately leave an ownerless ``class X:
    # pass``.  This is not an owned-composition rewrite candidate; allow the
    # main Inline Class transformer to perform its stricter empty-class
    # cleanup analysis instead of incorrectly returning review_required.
    non_docstring_members = list(target_class.body)
    if (
        non_docstring_members
        and isinstance(non_docstring_members[0], ast.Expr)
        and isinstance(non_docstring_members[0].value, ast.Constant)
        and isinstance(non_docstring_members[0].value.value, str)
    ):
        non_docstring_members = non_docstring_members[1:]
    if not business_methods and all(
        isinstance(member, ast.Pass) for member in non_docstring_members
    ):
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="EMPTY_CLASS_CLEANUP_CANDIDATE",
        )

    allowed_members = set(methods)
    for member in target_class.body:
        if member in allowed_members:
            continue
        if (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        ):
            continue
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="CLASS_MEMBER_UNSUPPORTED",
        )

    parameters, defaults, field_expressions, constructor_error = _constructor_model(constructor)
    if constructor_error:
        # A no-argument/literal-state helper may still be handled by the legacy
        # module-function Inline Class strategy.
        if constructor_error in {
            "CONSTRUCTOR_REQUIRED_FOR_OWNED_INLINE",
            "NO_CONSTRUCTOR_FIELDS",
        }:
            return _not_applicable(
                source_code,
                class_to_inline=class_to_inline,
                reason=constructor_error,
            )
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=constructor_error,
        )

    field_names = set(field_expressions)
    method_names = {method.name for method in business_methods}

    responsibility = _inline_class_responsibility_profile(
        business_methods=business_methods,
        field_names=field_names,
    )
    # The class is a value/wrapper object when all exposed methods are simple
    # accessors/mutators/field formatting.  Only real independent behaviour is
    # counted as a responsibility.  Keep a finite API limit to avoid silently
    # moving a large public façade merely because each method is individually
    # short.
    if (
        responsibility["state_field_count"] > 5
        or responsibility["method_count"] > 8
        or responsibility["effective_responsibility_count"] > 0
    ):
        if _was_enriched_by_prior_move(
            class_to_inline,
            prior_transformations or [],
        ):
            # Let the main transformer report the more accurate
            # SMELL_RESOLVED_BY_PRIOR_REFACTORING outcome for a class that was
            # enriched by an earlier accepted Move Method in this pipeline.
            return _not_applicable(
                source_code,
                class_to_inline=class_to_inline,
                reason="CURRENT_CLASS_ENRICHED_BY_PRIOR_REFACTORING",
            )
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="CLASS_RESPONSIBILITY_NOT_SMALL",
            responsibility_profile=responsibility,
        )

    for method in business_methods:
        error = _method_safety_error(
            method,
            field_names=field_names,
            method_names=method_names,
        )
        if error:
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason=error,
            )

    constructions = _find_owned_constructions(
        tree=tree,
        target_class=target_class,
    )
    if preferred_destination_class:
        constructions = [
            item
            for item in constructions
            if item["owner_class"].name == preferred_destination_class
        ]
    if preferred_owner_attribute:
        constructions = [
            item
            for item in constructions
            if item["owner_attribute"] == preferred_owner_attribute
        ]

    if not constructions:
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="NO_UNIQUE_OWNER_COMPOSITION",
        )
    if len(constructions) != 1:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="AMBIGUOUS_OWNER_COMPOSITION",
        )

    construction = constructions[0]
    owner_class: ast.ClassDef = construction["owner_class"]
    owner_attribute = str(construction["owner_attribute"])
    construction_statement: ast.AST = construction["statement"]
    construction_call: ast.Call = construction["call"]

    external_reference_files = _repository_reference_files(
        project_source_files=project_source_files or [],
        current_file_name=current_file_name,
        class_name=class_to_inline,
    )
    if external_reference_files:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="EXTERNAL_REFERENCES",
            reference_files=external_reference_files,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    bound, bind_error = _bind_constructor_arguments(
        call=construction_call,
        parameters=parameters,
        defaults=defaults,
    )
    if bind_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=bind_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    owner_fields = _owner_self_fields(owner_class)
    owner_methods = _owner_method_names(owner_class)
    field_collisions = sorted(field_names & owner_fields)
    method_collisions = sorted(method_names & owner_methods)
    if field_collisions:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="DESTINATION_FIELD_COLLISION",
            collisions=field_collisions,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )
    if method_collisions:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="DESTINATION_METHOD_COLLISION",
            collisions=method_collisions,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    parents = _parents(tree)

    # The helper class must not escape its owner relationship.  A second
    # construction, assignment to a local variable, isinstance check, type
    # alias, decorator, or other reference makes ownership ambiguous.
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Name)
            and node.id == class_to_inline
            and isinstance(node.ctx, ast.Load)
        ):
            continue
        if not _class_reference_is_allowed(
            node=node,
            target_class=target_class,
            construction_call=construction_call,
            parents=parents,
        ):
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="DYNAMIC_OR_EXTERNAL_CLASS_REFERENCE",
                destination_class=owner_class.name,
                owner_attribute=owner_attribute,
            )

    usage_error = _owner_attribute_usage_error(
        tree=tree,
        target_class=target_class,
        owner_attribute=owner_attribute,
        field_names=field_names,
        method_names=method_names,
        construction_statement=construction_statement,
        parents=parents,
    )
    if usage_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=usage_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    method_sources = [
        _method_source(source_code, method)
        for method in business_methods
    ]
    if not all(method_sources):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="METHOD_RENDER_FAILED",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    field_assignment_lines: list[str] = []
    indent = " " * int(getattr(construction_statement, "col_offset", 0) or 0)
    for field_name, expression in field_expressions.items():
        rendered_expression = _render_bound_field_expression(expression, bound)
        if not rendered_expression:
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="CONSTRUCTOR_FIELD_BINDING_FAILED",
                destination_class=owner_class.name,
                owner_attribute=owner_attribute,
            )
        field_assignment_lines.append(
            f"{indent}self.{field_name} = {rendered_expression}\n"
        )

    line_offsets = _line_offsets(source_code)
    edits: list[tuple[int, int, str]] = []

    class_start = _position_offset(line_offsets, target_class.lineno, 0)
    class_end = _line_end_offset(source_code, line_offsets, target_class.end_lineno)
    edits.append((class_start, class_end, ""))

    construction_start = _position_offset(
        line_offsets,
        construction_statement.lineno,
        0,
    )
    construction_end = _line_end_offset(
        source_code,
        line_offsets,
        construction_statement.end_lineno,
    )
    edits.append(
        (
            construction_start,
            construction_end,
            "".join(field_assignment_lines),
        )
    )

    insertion_offset = _line_end_offset(
        source_code,
        line_offsets,
        owner_class.end_lineno,
    )
    if method_sources:
        rendered_methods = "\n\n".join(
            textwrap.indent(method_source, "    ")
            for method_source in method_sources
        )
        edits.append((insertion_offset, insertion_offset, f"\n{rendered_methods}\n"))

    chain_edits, updated_accesses, chain_error = _chain_rewrite_edits(
        source_code=source_code,
        tree=tree,
        target_class=target_class,
        construction_statement=construction_statement,
        owner_attribute=owner_attribute,
        field_names=field_names,
        method_names=method_names,
        line_offsets=line_offsets,
        parents=parents,
    )
    if chain_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=chain_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )
    edits.extend(chain_edits)

    if not _edits_do_not_overlap(edits):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="OVERLAPPING_INLINE_EDITS",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    transformed = _apply_edits(source_code, edits)
    try:
        transformed_tree = ast.parse(transformed)
        compile(transformed, "<sctva-owned-inline-class>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TRANSFORMED_SOURCE_PARSE_FAILED",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    if any(
        isinstance(node, ast.ClassDef) and node.name == class_to_inline
        for node in transformed_tree.body
    ):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TARGET_CLASS_STILL_PRESENT",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    unresolved_class_refs = [
        node
        for node in ast.walk(transformed_tree)
        if isinstance(node, ast.Name) and node.id == class_to_inline
    ]
    if unresolved_class_refs:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="UNRESOLVED_CLASS_REFERENCE_AFTER_INLINE",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    return transformed, len(edits), {
        "status": "success",
        "inline_mode": "owner_class",
        "class_to_inline": class_to_inline,
        "destination_class": owner_class.name,
        "owner_attribute": owner_attribute,
        "inlined_methods": [method.name for method in business_methods],
        "inlined_fields": sorted(field_names),
        "removed_instantiations": 1,
        "updated_owner_member_accesses": updated_accesses,
        "responsibility_profile": responsibility,
        "ownership_graph": {
            "owner_class": owner_class.name,
            "owner_attribute": owner_attribute,
            "construction_count": len(constructions),
            "external_reference_files": external_reference_files,
        },
        "inline_class_lineage": {
            "source_class": class_to_inline,
            "destination_class": owner_class.name,
            "owner_attribute": owner_attribute,
            "moved_fields": sorted(field_names),
            "moved_methods": [method.name for method in business_methods],
            "prior_lineage": list(prior_lineage or []),
        },
    }
