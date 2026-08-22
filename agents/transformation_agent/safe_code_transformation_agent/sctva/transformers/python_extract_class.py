"""Python Large Class -> Extract Class refactoring support.

The implementation is intentionally conservative. It performs a real same-file
Extract Class only when the source class, candidate responsibility, state
dependencies, and postconditions can be proven from the Python AST.
"""

from __future__ import annotations

import ast
import io
import json
import keyword
import re
import tokenize
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import libcst as cst


REVIEW_REQUIRED = "review_required"
SUCCESS = "success"
ALREADY_APPLIED = "already_applied"


@dataclass
class MethodInfo:
    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    start_line: int
    end_line: int
    fields_read: set[str] = field(default_factory=set)
    fields_written: set[str] = field(default_factory=set)
    self_calls: set[str] = field(default_factory=set)
    unsupported_reason: str = ""
    body_loc: int = 0
    complexity: int = 1

    @property
    def touched_fields(self) -> set[str]:
        return set(self.fields_read) | set(self.fields_written)


@dataclass
class FieldInfo:
    name: str
    initializer: ast.stmt | None = None
    readers: set[str] = field(default_factory=set)
    writers: set[str] = field(default_factory=set)


@dataclass
class ClassAnalysis:
    name: str
    node: ast.ClassDef
    methods: Dict[str, MethodInfo]
    fields: Dict[str, FieldInfo]
    class_loc: int
    class_indent: str
    member_indent: str
    has_bases: bool


@dataclass
class ExtractionCandidate:
    methods: list[str]
    fields: list[str]
    score: float
    reason: str
    expanded_fields: list[str] = field(default_factory=list)
    cohesion: float = 0.0
    isolation: float = 0.0
    boundary_dependencies: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class RepositoryUsage:
    method_references: set[str] = field(default_factory=set)
    field_reads: set[str] = field(default_factory=set)
    field_writes: set[str] = field(default_factory=set)
    evidence: Dict[str, List[str]] = field(default_factory=dict)
    complete: bool = False
    files_scanned: int = 0
    parse_failures: list[str] = field(default_factory=list)


@dataclass
class CompatibilityPlan:
    delegated_methods: list[str] = field(default_factory=list)
    property_fields: list[str] = field(default_factory=list)
    writable_property_fields: list[str] = field(default_factory=list)
    dynamic_method_delegates: list[str] = field(default_factory=list)
    dynamic_field_reads: list[str] = field(default_factory=list)
    use_dynamic_bridge: bool = False
    descriptor_methods: list[str] = field(default_factory=list)
    descriptor_fields: list[str] = field(default_factory=list)
    descriptor_class_name: str = ""
    use_member_descriptors: bool = False
    internal_methods_rewritten: list[str] = field(default_factory=list)
    internal_fields_rewritten: list[str] = field(default_factory=list)
    policy: str = "repository_usage"


class _SelfUsageVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.fields_read: set[str] = set()
        self.fields_written: set[str] = set()
        self.self_calls: set[str] = set()
        self.private_members: set[str] = set()
        self.uses_super = False
        self.has_yield = False

    @staticmethod
    def _self_attr(node: ast.AST) -> str:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            return node.attr
        return ""

    def _remember_private(self, name: str) -> None:
        if name.startswith("__") and not name.endswith("__"):
            self.private_members.add(name)

    def visit_Call(self, node: ast.Call) -> Any:
        attr = self._self_attr(node.func)
        if attr:
            self.self_calls.add(attr)
            self._remember_private(attr)
        elif isinstance(node.func, ast.Name) and node.func.id == "super":
            self.uses_super = True
        else:
            self.visit(node.func)

        for arg in node.args:
            self.visit(arg)
        for keyword_node in node.keywords:
            self.visit(keyword_node.value)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        attr = self._self_attr(node)
        if attr:
            self._remember_private(attr)
            if isinstance(node.ctx, ast.Store):
                self.fields_written.add(attr)
            elif isinstance(node.ctx, ast.Del):
                self.fields_written.add(attr)
            else:
                self.fields_read.add(attr)
            return
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        attr = self._self_attr(node.target)
        if attr:
            self._remember_private(attr)
            self.fields_read.add(attr)
            self.fields_written.add(attr)
            self.visit(node.value)
            return
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> Any:
        self.has_yield = True
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> Any:
        self.has_yield = True
        self.generic_visit(node)


class _FieldInitDependencyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.self_fields: set[str] = set()
        self.self_calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> Any:
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            self.self_calls.add(node.func.attr)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Load)
        ):
            self.self_fields.add(node.attr)
            return
        self.generic_visit(node)


class _SourceMemberReferenceRewriter(cst.CSTTransformer):
    """Redirect remaining source-class references to the extracted component."""

    def __init__(
        self,
        *,
        source_class: str,
        helper_attr: str,
        moved_methods: set[str],
        moved_fields: set[str],
    ) -> None:
        self.source_class = source_class
        self.helper_attr = helper_attr
        self.moved_methods = moved_methods
        self.moved_fields = moved_fields
        self.class_stack: list[str] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self.class_stack.append(node.name.value)
        return True

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        self.class_stack.pop()
        return updated_node

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if self.class_stack != [self.source_class]:
            return updated_node
        if not isinstance(updated_node.value, cst.Name) or updated_node.value.value != "self":
            return updated_node
        member_name = updated_node.attr.value
        if member_name not in self.moved_methods and member_name not in self.moved_fields:
            return updated_node
        return updated_node.with_changes(
            value=cst.Attribute(
                value=cst.Name("self"),
                attr=cst.Name(self.helper_attr),
            )
        )


def apply_extract_class(
    source_code: str,
    *,
    source_file: str = "",
    current_file_name: str = "",
    source_class: str,
    new_class_name: str,
    methods_to_extract: Sequence[str] | None = None,
    fields_to_extract: Sequence[str] | None = None,
    preserve_public_api: bool = True,
    delegation_strategy: str = "wrapper",
    target_file: str = "same_file",
    project_source_files: Sequence[Any] | None = None,
    repository_complete: bool = False,
    behavior_tests: Sequence[Dict[str, Any]] | None = None,
    required_public_methods: Sequence[str] | None = None,
    required_public_fields: Sequence[str] | None = None,
    source_resolution_error: str = "",
) -> tuple[str, int, Dict[str, Any]]:
    """Apply a conservative same-file Extract Class transformation.

    Returns ``(source, replacements, metadata)``. When the transformation is
    unsafe, ``source`` is returned unchanged with status ``review_required`` in
    metadata instead of guessing.
    """

    transformer = ExtractClassRefactoring(source_code)
    return transformer.apply(
        source_file=source_file,
        current_file_name=current_file_name,
        source_class=source_class,
        new_class_name=new_class_name,
        methods_to_extract=methods_to_extract,
        fields_to_extract=fields_to_extract,
        preserve_public_api=preserve_public_api,
        delegation_strategy=delegation_strategy,
        target_file=target_file,
        project_source_files=project_source_files,
        repository_complete=repository_complete,
        behavior_tests=behavior_tests,
        required_public_methods=required_public_methods,
        required_public_fields=required_public_fields,
        source_resolution_error=source_resolution_error,
    )


class ExtractClassRefactoring:
    MIN_INFERRED_METHODS = 2
    MIN_SOURCE_METHODS = 4
    MIN_CANDIDATE_SCORE = 0.62
    LARGE_CLASS_METHOD_THRESHOLD = 20.0
    LARGE_CLASS_LOC_THRESHOLD = 150
    LARGE_CLASS_COMPLEXITY_THRESHOLD = 50.0
    LARGE_CLASS_FIELD_THRESHOLD = 15
    LARGE_CLASS_RESPONSIBILITY_THRESHOLD = 7

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.lines = source_code.splitlines(keepends=True)

    def apply(
        self,
        *,
        source_file: str,
        current_file_name: str,
        source_class: str,
        new_class_name: str,
        methods_to_extract: Sequence[str] | None,
        fields_to_extract: Sequence[str] | None,
        preserve_public_api: bool,
        delegation_strategy: str,
        target_file: str,
        project_source_files: Sequence[Any] | None,
        repository_complete: bool,
        behavior_tests: Sequence[Dict[str, Any]] | None,
        required_public_methods: Sequence[str] | None,
        required_public_fields: Sequence[str] | None,
        source_resolution_error: str,
    ) -> tuple[str, int, Dict[str, Any]]:
        base_metadata = {
            "refactoring": "Extract Class",
            "plan_compliance": "UNKNOWN",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
            "source_class": source_class,
            "source_file": source_file or current_file_name,
            "current_file_name": current_file_name,
            "extracted_class": new_class_name,
            "target_file": target_file or "same_file",
            "delegation_strategy": delegation_strategy or "wrapper",
        }

        if source_resolution_error:
            return self._review(source_resolution_error, base_metadata)

        validation_error = self._validate_plan_values(
            source_class,
            new_class_name,
            target_file,
            source_file=source_file,
            current_file_name=current_file_name,
        )
        if validation_error:
            return self._review(validation_error, base_metadata)

        try:
            tree = ast.parse(self.source_code)
            cst.parse_module(self.source_code)
        except SyntaxError:
            return self._review("SYNTAX_VALIDATION_FAILED", base_metadata)
        except Exception:
            return self._review("CST_PARSE_FAILED", base_metadata)

        source_node = self._find_top_level_class(tree, source_class)
        available_classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        base_metadata["available_classes"] = available_classes

        if source_node is None:
            base_metadata["requested_class"] = source_class
            return self._review("SOURCE_CLASS_NOT_FOUND", base_metadata)

        new_class_node = self._find_top_level_class(tree, new_class_name)
        if new_class_node is not None:
            if self._looks_already_applied(source_node, new_class_name):
                metadata = {
                    **base_metadata,
                    "status": ALREADY_APPLIED,
                    "reason": "ALREADY_APPLIED",
                    "plan_compliance": "PASS",
                    "methods_moved": list(methods_to_extract or []),
                    "fields_moved": list(fields_to_extract or []),
                }
                return self.source_code, 0, metadata
            return self._review("CLASS_NAME_COLLISION", base_metadata)

        symbols = self._module_symbols(tree)
        if new_class_name in symbols:
            return self._review("CLASS_NAME_COLLISION", base_metadata)

        analysis = self._analyse_class(source_node)
        before_metrics = self._class_metrics(analysis)
        base_metadata["before_metrics"] = before_metrics

        candidate = self._select_candidate(
            analysis,
            explicit_methods=list(methods_to_extract or []),
            explicit_fields=list(fields_to_extract or []),
        )
        if isinstance(candidate, str):
            return self._review(candidate, base_metadata)

        base_metadata["candidate_score"] = round(candidate.score, 4)
        base_metadata["candidate_reason"] = candidate.reason
        base_metadata["candidate_cohesion"] = round(candidate.cohesion, 4)
        base_metadata["candidate_isolation"] = round(candidate.isolation, 4)
        base_metadata["boundary_dependencies"] = candidate.boundary_dependencies
        base_metadata["methods_moved"] = candidate.methods
        base_metadata["fields_moved"] = candidate.fields
        if candidate.expanded_fields:
            base_metadata["expanded_fields"] = candidate.expanded_fields

        dependency_error = self._validate_candidate_dependencies(analysis, candidate)
        if dependency_error:
            return self._review(dependency_error, base_metadata)

        repository_usage = self._scan_repository_usage(
            source_class=source_class,
            candidate=candidate,
            project_source_files=project_source_files,
            current_file_name=current_file_name,
            repository_complete=repository_complete,
            behavior_tests=behavior_tests,
        )
        compatibility = self._build_compatibility_plan(
            analysis=analysis,
            candidate=candidate,
            repository_usage=repository_usage,
            preserve_public_api=preserve_public_api,
            required_public_methods=required_public_methods,
            required_public_fields=required_public_fields,
        )
        base_metadata["repository_usage"] = {
            "complete": repository_usage.complete,
            "files_scanned": repository_usage.files_scanned,
            "parse_failures": repository_usage.parse_failures,
            "method_references": sorted(repository_usage.method_references),
            "field_reads": sorted(repository_usage.field_reads),
            "field_writes": sorted(repository_usage.field_writes),
            "evidence": repository_usage.evidence,
        }
        base_metadata["compatibility"] = {
            "policy": compatibility.policy,
            "delegated_methods": compatibility.delegated_methods,
            "property_fields": compatibility.property_fields,
            "writable_property_fields": compatibility.writable_property_fields,
            "dynamic_method_delegates": compatibility.dynamic_method_delegates,
            "dynamic_field_reads": compatibility.dynamic_field_reads,
            "use_dynamic_bridge": compatibility.use_dynamic_bridge,
            "descriptor_methods": compatibility.descriptor_methods,
            "descriptor_fields": compatibility.descriptor_fields,
            "descriptor_class_name": compatibility.descriptor_class_name,
            "use_member_descriptors": compatibility.use_member_descriptors,
            "state_ownership": "helper_only",
            "internal_methods_rewritten": compatibility.internal_methods_rewritten,
            "internal_fields_rewritten": compatibility.internal_fields_rewritten,
        }

        transformed = self._rewrite_source(
            analysis=analysis,
            candidate=candidate,
            new_class_name=new_class_name,
            compatibility=compatibility,
        )

        post_error, post_metadata = self._validate_postconditions(
            transformed,
            source_class=source_class,
            new_class_name=new_class_name,
            candidate=candidate,
            compatibility=compatibility,
            before_metrics=before_metrics,
        )
        base_metadata.update(post_metadata)
        if post_error:
            return self._review(post_error, base_metadata)

        metadata = {
            **base_metadata,
            "status": SUCCESS,
            "reason": "extract_class_applied",
            "plan_compliance": "PASS",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
            "delegates_created": sorted(
                set(compatibility.delegated_methods)
                | set(compatibility.dynamic_method_delegates)
                | set(compatibility.descriptor_methods)
            ),
            "public_fields_preserved": sorted(
                set(compatibility.property_fields)
                | set(compatibility.dynamic_field_reads)
                | set(compatibility.descriptor_fields)
            ),
            "confidence": self._estimate_confidence(base_metadata),
        }
        return transformed, 1, metadata

    @staticmethod
    def _review_payload(reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:
        return "", 0, {
            **metadata,
            "status": REVIEW_REQUIRED,
            "reason": reason,
            "plan_compliance": "FAIL",
            "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
        }

    def _review(self, reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:  # type: ignore[override]
        unchanged, replacements, details = ExtractClassRefactoring._review_payload(reason, metadata)
        return self.source_code if unchanged == "" else unchanged, replacements, details

    @staticmethod
    def _validate_plan_values(
        source_class: str,
        new_class_name: str,
        target_file: str,
        *,
        source_file: str,
        current_file_name: str,
    ) -> str:
        if not source_class:
            return "SOURCE_CLASS_NOT_FOUND"
        if not _is_valid_python_identifier(source_class):
            return "SOURCE_CLASS_NOT_FOUND"
        if not new_class_name or not _is_valid_python_identifier(new_class_name):
            return "INVALID_NEW_CLASS_NAME"
        if source_class == new_class_name:
            return "CLASS_NAME_COLLISION"
        if source_file and current_file_name and not _paths_match(source_file, current_file_name):
            return "SOURCE_FILE_MISMATCH"
        if target_file and str(target_file).strip().lower() not in {"same_file", "same-source-file", "same source file"}:
            return "CIRCULAR_IMPORT_RISK"
        return ""

    @staticmethod
    def _find_top_level_class(tree: ast.Module, class_name: str) -> ast.ClassDef | None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    @staticmethod
    def _module_symbols(tree: ast.Module) -> set[str]:
        symbols: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.add(node.target.id)
        return symbols

    def _unique_module_symbol(self, base_name: str) -> str:
        symbols = self._module_symbols(ast.parse(self.source_code))
        if base_name not in symbols:
            return base_name
        suffix = 2
        while f"{base_name}{suffix}" in symbols:
            suffix += 1
        return f"{base_name}{suffix}"

    def _analyse_class(self, class_node: ast.ClassDef) -> ClassAnalysis:
        class_indent = _line_indent(self.lines[class_node.lineno - 1])
        member_indent = class_indent + "    "
        methods: Dict[str, MethodInfo] = {}
        fields: Dict[str, FieldInfo] = {}

        for statement in class_node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = self._analyse_method(statement)
                methods[method.name] = method

        init_method = methods.get("__init__")
        if init_method:
            for statement in init_method.node.body:
                for field_name in self._assigned_self_fields(statement):
                    fields.setdefault(field_name, FieldInfo(name=field_name, initializer=statement))

        for method in methods.values():
            for field_name in method.fields_read:
                fields.setdefault(field_name, FieldInfo(name=field_name)).readers.add(method.name)
            for field_name in method.fields_written:
                fields.setdefault(field_name, FieldInfo(name=field_name)).writers.add(method.name)

        return ClassAnalysis(
            name=class_node.name,
            node=class_node,
            methods=methods,
            fields=fields,
            class_loc=int(getattr(class_node, "end_lineno", class_node.lineno)) - class_node.lineno + 1,
            class_indent=class_indent,
            member_indent=member_indent,
            has_bases=bool(class_node.bases),
        )

    def _analyse_method(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
        visitor = _SelfUsageVisitor()
        visitor.visit(node)
        unsupported = self._unsupported_method_reason(node, visitor)
        start_line = int(getattr(node, "lineno", 0) or 0)
        end_line = int(getattr(node, "end_lineno", start_line) or start_line)
        return MethodInfo(
            name=node.name,
            node=node,
            start_line=start_line,
            end_line=end_line,
            fields_read=visitor.fields_read,
            fields_written=visitor.fields_written,
            self_calls=visitor.self_calls,
            unsupported_reason=unsupported,
            body_loc=max(0, end_line - start_line + 1),
            complexity=_cyclomatic_complexity(node),
        )

    @staticmethod
    def _unsupported_method_reason(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        visitor: _SelfUsageVisitor,
    ) -> str:
        if isinstance(node, ast.AsyncFunctionDef):
            return "UNSUPPORTED_METHOD_TYPE"
        if node.name == "__init__":
            return "UNSUPPORTED_METHOD_TYPE"
        if node.name.startswith("__") and node.name.endswith("__"):
            return "UNSUPPORTED_METHOD_TYPE"
        if visitor.uses_super:
            return "UNSUPPORTED_INHERITANCE_CASE"
        if visitor.has_yield:
            return "UNSUPPORTED_METHOD_TYPE"
        if visitor.private_members:
            return "PRIVATE_NAME_MANGLING_RISK"
        decorators = [_decorator_name(item) for item in node.decorator_list]
        decorators = [item for item in decorators if item]
        if decorators:
            if any(item in {"staticmethod", "classmethod", "property"} or item.endswith(".setter") for item in decorators):
                return "UNSUPPORTED_METHOD_TYPE"
            return "UNSUPPORTED_DECORATOR"
        return ""

    @staticmethod
    def _assigned_self_fields(statement: ast.stmt) -> list[str]:
        targets: list[ast.AST] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            return []

        names: list[str] = []
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.append(target.attr)
            else:
                return []
        return names

    def _select_candidate(
        self,
        analysis: ClassAnalysis,
        *,
        explicit_methods: list[str],
        explicit_fields: list[str],
    ) -> ExtractionCandidate | str:
        if analysis.has_bases:
            return "UNSUPPORTED_INHERITANCE_CASE"

        if explicit_methods:
            return self._explicit_candidate(analysis, explicit_methods, explicit_fields)

        if len([name for name in analysis.methods if name != "__init__"]) < self.MIN_SOURCE_METHODS:
            return "NO_SAFE_EXTRACTION_CLUSTER"

        candidates: list[ExtractionCandidate] = []
        eligible_methods = {
            method.name: method
            for method in analysis.methods.values()
            if not method.unsupported_reason
            and not method.name.startswith("_")
            and method.name != "__init__"
            and method.touched_fields
        }

        for field_name in sorted(analysis.fields):
            if field_name.startswith("__"):
                continue
            related_names = {
                method.name
                for method in eligible_methods.values()
                if field_name in method.touched_fields
            }
            related_names = self._expand_method_call_cluster(related_names, eligible_methods)
            if len(related_names) < self.MIN_INFERRED_METHODS:
                continue
            related = [eligible_methods[name] for name in sorted(related_names)]
            method_names = [method.name for method in related]
            fields = sorted(set().union(*(method.touched_fields for method in related)))
            external_calls = sorted(
                set().union(*(method.self_calls for method in related)) - set(method_names)
            )
            if external_calls:
                continue
            score, cohesion, isolation, boundary = self._score_candidate(
                analysis,
                related,
                fields,
            )
            candidates.append(
                ExtractionCandidate(
                    methods=method_names,
                    fields=fields,
                    score=score,
                    reason=f"shared_field:{field_name}",
                    cohesion=cohesion,
                    isolation=isolation,
                    boundary_dependencies=boundary,
                )
            )

        if not candidates:
            return "NO_SAFE_EXTRACTION_CLUSTER"

        candidates.sort(
            key=lambda item: (
                item.score,
                item.cohesion,
                item.isolation,
                len(item.methods),
                -len(item.fields),
            ),
            reverse=True,
        )
        selected = candidates[0]
        if selected.score < self.MIN_CANDIDATE_SCORE:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        return selected

    def _explicit_candidate(
        self,
        analysis: ClassAnalysis,
        explicit_methods: list[str],
        explicit_fields: list[str],
    ) -> ExtractionCandidate | str:
        methods: list[str] = []
        missing: list[str] = []
        for method_name in explicit_methods:
            normalized = str(method_name or "").strip()
            if not normalized:
                continue
            if normalized not in analysis.methods:
                missing.append(normalized)
            else:
                methods.append(normalized)
        if missing:
            return "METHOD_NOT_FOUND"
        if len(methods) < self.MIN_INFERRED_METHODS:
            return "NO_SAFE_EXTRACTION_CLUSTER"

        touched = sorted(set().union(*(analysis.methods[name].touched_fields for name in methods)))
        fields = sorted(set(str(item).strip() for item in explicit_fields if str(item).strip()) | set(touched))
        if any(field_name not in analysis.fields for field_name in fields):
            return "FIELD_NOT_FOUND"
        expanded = sorted(set(touched) - set(str(item).strip() for item in explicit_fields if str(item).strip()))
        score, cohesion, isolation, boundary = self._score_candidate(
            analysis,
            [analysis.methods[name] for name in methods],
            fields,
        )
        return ExtractionCandidate(
            methods=methods,
            fields=fields,
            score=score,
            reason="explicit_methods",
            expanded_fields=expanded,
            cohesion=cohesion,
            isolation=isolation,
            boundary_dependencies=boundary,
        )

    @staticmethod
    def _expand_method_call_cluster(
        method_names: set[str],
        eligible_methods: Dict[str, MethodInfo],
    ) -> set[str]:
        expanded = set(method_names)
        changed = True
        while changed:
            changed = False
            for method_name in list(expanded):
                method = eligible_methods.get(method_name)
                if not method:
                    continue
                for called_name in method.self_calls:
                    if called_name in eligible_methods and called_name not in expanded:
                        expanded.add(called_name)
                        changed = True
        return expanded

    def _score_candidate(
        self,
        analysis: ClassAnalysis,
        methods: Sequence[MethodInfo],
        fields: Sequence[str],
    ) -> tuple[float, float, float, Dict[str, List[str]]]:
        if not methods:
            return 0.0, 0.0, 0.0, {}
        method_set = {method.name for method in methods}
        shared_edges = sum(
            1
            for method in methods
            for field_name in fields
            if field_name in method.touched_fields
        )
        max_edges = max(1, len(methods) * max(1, len(fields)))
        cohesion = shared_edges / max_edges
        remaining = [
            method
            for method in analysis.methods.values()
            if method.name not in method_set and method.name != "__init__"
        ]
        outgoing_calls = sorted(set().union(*(method.self_calls for method in methods)) - method_set)
        incoming_calls = sorted(
            method.name
            for method in remaining
            if method.self_calls & method_set
        )
        shared_field_users = sorted(
            f"{method.name}.{field_name}"
            for method in remaining
            for field_name in fields
            if field_name in method.touched_fields
        )
        boundary_edges = len(outgoing_calls) + len(incoming_calls) + len(shared_field_users)
        isolation = max(
            0.0,
            1.0 - (boundary_edges / max(1, shared_edges + boundary_edges)),
        )
        method_weight = min(1.0, len(methods) / 3)
        complexity_weight = min(
            1.0,
            sum(method.complexity for method in methods)
            / max(1, sum(method.complexity for method in analysis.methods.values())),
        )
        score = (
            0.38 * cohesion
            + 0.32 * isolation
            + 0.20 * method_weight
            + 0.10 * complexity_weight
        )
        boundary = {
            "outgoing_method_calls": outgoing_calls,
            "incoming_method_calls": incoming_calls,
            "shared_field_users": shared_field_users,
        }
        return score, cohesion, isolation, boundary

    def _validate_candidate_dependencies(
        self,
        analysis: ClassAnalysis,
        candidate: ExtractionCandidate,
    ) -> str:
        selected = set(candidate.methods)
        if not selected:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        if len(selected) >= len([name for name in analysis.methods if name != "__init__"]):
            return "INSUFFICIENT_CLASS_REDUCTION"

        selected_infos = [analysis.methods[name] for name in candidate.methods]
        for method in selected_infos:
            if method.unsupported_reason:
                return method.unsupported_reason
            if not self._method_signature_lines(method):
                return "UNSUPPORTED_METHOD_TYPE"
            if method.node.args.args and method.node.args.args[0].arg != "self":
                return "UNSUPPORTED_METHOD_TYPE"
            if not method.node.args.args:
                return "UNSUPPORTED_METHOD_TYPE"

        field_set = set(candidate.fields)
        if any(field_name.startswith("__") for field_name in field_set):
            return "PRIVATE_NAME_MANGLING_RISK"
        if any(field_name in analysis.methods for field_name in field_set):
            return "UNRESOLVED_EXTERNAL_REFERENCE"

        external_calls = sorted(set().union(*(method.self_calls for method in selected_infos)) - selected)
        if external_calls:
            return "CROSS_CLASS_DEPENDENCY_TOO_HIGH"

        read_fields = set().union(*(method.fields_read for method in selected_infos))
        written_fields = set().union(*(method.fields_written for method in selected_infos))
        unresolved_reads = [
            field_name
            for field_name in read_fields
            if field_name not in analysis.fields and field_name not in written_fields
        ]
        if unresolved_reads:
            return "UNRESOLVED_EXTERNAL_REFERENCE"

        init_method = analysis.methods.get("__init__")
        init_parameter_names = (
            set(_method_parameter_names(init_method.node.args))
            if init_method
            else set()
        )
        for field_name in field_set:
            field_info = analysis.fields.get(field_name)
            initializer = field_info.initializer if field_info else None
            if initializer is None:
                if field_name in read_fields:
                    return "FIELD_INITIALIZER_NOT_FOUND"
                continue
            dependency_error = self._field_initializer_dependency_error(
                initializer,
                field_set,
                init_parameter_names,
            )
            if dependency_error:
                return dependency_error

        return ""

    @staticmethod
    def _field_initializer_dependency_error(
        initializer: ast.stmt,
        moved_fields: set[str],
        init_parameter_names: set[str],
    ) -> str:
        value: ast.AST | None = None
        if isinstance(initializer, ast.Assign):
            value = initializer.value
        elif isinstance(initializer, ast.AnnAssign):
            value = initializer.value
        if value is None:
            return ""
        visitor = _FieldInitDependencyVisitor()
        visitor.visit(value)
        if visitor.self_calls:
            return "CROSS_CLASS_DEPENDENCY_TOO_HIGH"
        if visitor.self_fields - moved_fields:
            return "CROSS_CLASS_DEPENDENCY_TOO_HIGH"
        loaded_names = {
            node.id
            for node in ast.walk(value)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        if loaded_names & init_parameter_names:
            return "CONSTRUCTOR_DEPENDENCY_UNSUPPORTED"
        return ""

    def _scan_repository_usage(
        self,
        *,
        source_class: str,
        candidate: ExtractionCandidate,
        project_source_files: Sequence[Any] | None,
        current_file_name: str,
        repository_complete: bool,
        behavior_tests: Sequence[Dict[str, Any]] | None,
    ) -> RepositoryUsage:
        usage = RepositoryUsage(complete=repository_complete)
        source_items = list(project_source_files or [])
        if not source_items:
            source_items = [
                {
                    "file_name": current_file_name or "source_code.py",
                    "source_code": self.source_code,
                    "language": "python",
                }
            ]
            usage.complete = False

        for index, item in enumerate(source_items, start=1):
            if isinstance(item, dict):
                file_name = str(item.get("file_name") or item.get("name") or f"file_{index}")
                source = item.get("source_code")
                language = str(item.get("language") or "").strip().lower()
            else:
                file_name = str(getattr(item, "file_name", f"file_{index}"))
                source = getattr(item, "source_code", None)
                language = str(getattr(item, "language", "") or "").strip().lower()
            if not isinstance(source, str):
                continue
            if language and language != "python":
                continue
            if not language and file_name and not file_name.lower().endswith(".py"):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                usage.parse_failures.append(file_name)
                usage.complete = False
                continue
            usage.files_scanned += 1
            _collect_repository_member_usage(
                tree,
                file_name=file_name,
                source_class=source_class,
                method_names=set(candidate.methods),
                field_names=set(candidate.fields),
                usage=usage,
            )

        behavior_text = json.dumps(list(behavior_tests or []), sort_keys=True, default=str)
        for method_name in candidate.methods:
            if re.search(rf"\b{re.escape(method_name)}\b", behavior_text):
                usage.method_references.add(method_name)
                usage.evidence.setdefault(method_name, []).append("behavior_tests")
        for field_name in candidate.fields:
            if re.search(rf"\b{re.escape(field_name)}\b", behavior_text):
                usage.field_reads.add(field_name)
                usage.evidence.setdefault(field_name, []).append("behavior_tests")
        return usage

    def _build_compatibility_plan(
        self,
        *,
        analysis: ClassAnalysis,
        candidate: ExtractionCandidate,
        repository_usage: RepositoryUsage,
        preserve_public_api: bool,
        required_public_methods: Sequence[str] | None,
        required_public_fields: Sequence[str] | None,
    ) -> CompatibilityPlan:
        selected_methods = set(candidate.methods)
        selected_fields = set(candidate.fields)
        required_methods = {
            str(item).strip()
            for item in required_public_methods or []
            if str(item).strip() in selected_methods
        }
        required_fields = {
            str(item).strip()
            for item in required_public_fields or []
            if str(item).strip() in selected_fields
        }

        if not preserve_public_api:
            descriptor_methods: set[str] = set()
            descriptor_fields: set[str] = set()
            policy = "public_api_not_requested"
        else:
            # Extract Class must retain the complete public surface, including
            # members that static repository analysis did not happen to see.
            # Explicit descriptors keep those names discoverable on the source
            # class and forward all instance state to one helper-owned value.
            descriptor_methods = {
                method_name
                for method_name in candidate.methods
                if not method_name.startswith("_")
            } | required_methods | (repository_usage.method_references & selected_methods)
            descriptor_fields = {
                field_name
                for field_name in candidate.fields
                if not field_name.startswith("_")
            } | required_fields | (
                (repository_usage.field_reads | repository_usage.field_writes) & selected_fields
            )
            repository_scope = "complete" if repository_usage.complete else "incomplete"
            policy = f"full_public_api_with_explicit_descriptors_{repository_scope}_repository"

        remaining_methods = [
            method
            for method in analysis.methods.values()
            if method.name not in selected_methods and method.name != "__init__"
        ]
        internal_method_refs = sorted(
            method.name
            for method in remaining_methods
            if method.self_calls & selected_methods
        )
        internal_field_refs = sorted(
            method.name
            for method in remaining_methods
            if method.touched_fields & selected_fields
        )
        return CompatibilityPlan(
            descriptor_methods=sorted(descriptor_methods),
            descriptor_fields=sorted(descriptor_fields),
            descriptor_class_name=(
                self._unique_module_symbol(f"_{analysis.name}ForwardedMember")
                if descriptor_methods or descriptor_fields
                else ""
            ),
            use_member_descriptors=bool(descriptor_methods or descriptor_fields),
            internal_methods_rewritten=internal_method_refs,
            internal_fields_rewritten=internal_field_refs,
            policy=policy,
        )

    def _rewrite_source(
        self,
        *,
        analysis: ClassAnalysis,
        candidate: ExtractionCandidate,
        new_class_name: str,
        compatibility: CompatibilityPlan,
    ) -> str:
        helper_attr = f"_{_snake_case(new_class_name)}"
        helper_lines = self._build_helper_class_lines(
            analysis=analysis,
            candidate=candidate,
            new_class_name=new_class_name,
        )
        edits: list[tuple[int, int, list[str]]] = []

        for method_name in candidate.methods:
            method = analysis.methods[method_name]
            edits.append((method.start_line, method.end_line, []))

        if compatibility.use_member_descriptors:
            bindings = self._member_descriptor_binding_lines(
                source_class=analysis.name,
                new_class_name=new_class_name,
                helper_attr=helper_attr,
                compatibility=compatibility,
            )
            edits.append((analysis.node.end_lineno + 1, analysis.node.end_lineno, bindings))

        edits.extend(self._helper_assignment_edits(analysis, candidate.fields, helper_attr, new_class_name))

        rewritten_lines = _apply_line_edits(list(self.lines), edits)
        support_lines = self._member_descriptor_class_lines(compatibility.descriptor_class_name)
        rewritten_lines[analysis.node.lineno - 1:analysis.node.lineno - 1] = (
            support_lines + (["\n"] if support_lines else []) + helper_lines + ["\n"]
        )
        rewritten = "".join(rewritten_lines)
        module = cst.parse_module(rewritten)
        return module.visit(
            _SourceMemberReferenceRewriter(
                source_class=analysis.name,
                helper_attr=helper_attr,
                moved_methods=set(candidate.methods),
                moved_fields=set(candidate.fields),
            )
        ).code

    @staticmethod
    def _member_descriptor_class_lines(descriptor_name: str) -> list[str]:
        if not descriptor_name:
            return []
        return [
            f"class {descriptor_name}:\n",
            "    def __init__(self, helper_attribute, member_name, helper_type=None):\n",
            "        self._helper_attribute = helper_attribute\n",
            "        self._member_name = member_name\n",
            "        self._helper_type = helper_type\n",
            "\n",
            "    def __get__(self, instance, owner=None):\n",
            "        if instance is None:\n",
            "            return self if self._helper_type is None else getattr(self._helper_type, self._member_name)\n",
            "        helper = object.__getattribute__(instance, self._helper_attribute)\n",
            "        return getattr(helper, self._member_name)\n",
            "\n",
            "    def __set__(self, instance, value):\n",
            "        helper = object.__getattribute__(instance, self._helper_attribute)\n",
            "        setattr(helper, self._member_name, value)\n",
            "\n",
            "    def __delete__(self, instance):\n",
            "        helper = object.__getattribute__(instance, self._helper_attribute)\n",
            "        delattr(helper, self._member_name)\n",
        ]

    @staticmethod
    def _member_descriptor_binding_lines(
        *,
        source_class: str,
        new_class_name: str,
        helper_attr: str,
        compatibility: CompatibilityPlan,
    ) -> list[str]:
        descriptor_name = compatibility.descriptor_class_name
        lines = ["\n"]
        for method_name in compatibility.descriptor_methods:
            lines.append(
                f"{source_class}.{method_name} = {descriptor_name}"
                f"('{helper_attr}', '{method_name}', {new_class_name})\n"
            )
        for field_name in compatibility.descriptor_fields:
            lines.append(
                f"{source_class}.{field_name} = {descriptor_name}"
                f"('{helper_attr}', '{field_name}')\n"
            )
        return lines

    def _build_helper_class_lines(
        self,
        *,
        analysis: ClassAnalysis,
        candidate: ExtractionCandidate,
        new_class_name: str,
    ) -> list[str]:
        lines: list[str] = [f"class {new_class_name}:\n"]
        field_init_lines = self._helper_init_lines(analysis, candidate.fields)
        if field_init_lines:
            lines.append("    def __init__(self):\n")
            lines.extend(field_init_lines)
            lines.append("\n")

        for index, method_name in enumerate(candidate.methods):
            method = analysis.methods[method_name]
            method_lines = self.lines[method.start_line - 1:method.end_line]
            lines.extend(self._normalize_member_block(method_lines, analysis.member_indent))
            if index != len(candidate.methods) - 1:
                lines.append("\n")

        return lines

    def _helper_init_lines(self, analysis: ClassAnalysis, fields: Sequence[str]) -> list[str]:
        result: list[str] = []
        emitted_statement_lines: set[int] = set()
        for field_name in fields:
            field_info = analysis.fields.get(field_name)
            initializer = field_info.initializer if field_info else None
            if initializer is None:
                continue
            start = int(getattr(initializer, "lineno", 0) or 0)
            end = int(getattr(initializer, "end_lineno", start) or start)
            if not start or start in emitted_statement_lines:
                continue
            emitted_statement_lines.add(start)
            for line in self.lines[start - 1:end]:
                result.append(_ensure_indent(line, "        "))
        return result

    @staticmethod
    def _normalize_member_block(method_lines: Sequence[str], member_indent: str) -> list[str]:
        normalized: list[str] = []
        for line in method_lines:
            if not line.strip():
                normalized.append(line)
            elif line.startswith(member_indent):
                normalized.append(line)
            else:
                normalized.append(member_indent + line.lstrip())
        return normalized

    def _method_signature_lines(self, method: MethodInfo) -> list[str]:
        """Return the exact declaration through its top-level colon.

        Python permits both compact methods (``def f(self): return 1``) and
        signatures spread across several lines. Looking only at the first line
        therefore rejects valid code or accidentally retains the inline body.
        Token positions let us preserve the declaration while replacing only
        its implementation with a delegation wrapper.
        """

        method_lines = self.lines[method.start_line - 1:method.end_line]
        if not method_lines:
            return []

        try:
            tokens = tokenize.generate_tokens(io.StringIO("".join(method_lines)).readline)
            seen_def = False
            bracket_depth = 0
            for token_info in tokens:
                token_type, token_text, _, token_end, _ = token_info
                if not seen_def:
                    if token_type == tokenize.NAME and token_text == "def":
                        seen_def = True
                    continue

                if token_type != tokenize.OP:
                    continue
                if token_text in {"(", "[", "{"}:
                    bracket_depth += 1
                    continue
                if token_text in {")", "]", "}"}:
                    bracket_depth = max(0, bracket_depth - 1)
                    continue
                if token_text != ":" or bracket_depth != 0:
                    continue

                end_row, end_column = token_end
                if end_row <= 0 or end_row > len(method_lines):
                    return []
                signature_lines = list(method_lines[:end_row])
                signature_lines[-1] = signature_lines[-1][:end_column]
                return [
                    line if line.endswith(("\n", "\r")) else line + "\n"
                    for line in signature_lines
                ]
        except (IndentationError, tokenize.TokenError):
            return []
        return []

    @staticmethod
    def _source_class_property_insert_line(analysis: ClassAnalysis) -> int:
        init_method = analysis.methods.get("__init__")
        if init_method:
            return init_method.end_line + 1
        if analysis.node.body:
            first = analysis.node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                return int(getattr(first, "end_lineno", first.lineno) or first.lineno) + 1
        return analysis.node.lineno + 1

    def _helper_assignment_edits(
        self,
        analysis: ClassAnalysis,
        fields: Sequence[str],
        helper_attr: str,
        new_class_name: str,
    ) -> list[tuple[int, int, list[str]]]:
        edits: list[tuple[int, int, list[str]]] = []
        init_method = analysis.methods.get("__init__")
        assignment_line = f"{analysis.member_indent}    self.{helper_attr} = {new_class_name}()\n"
        moved_init_statements = self._moved_field_initializers(analysis, fields)

        if init_method is None:
            insert_line = self._source_class_property_insert_line(analysis)
            edits.append(
                (
                    insert_line,
                    insert_line - 1,
                    [
                        f"{analysis.member_indent}def __init__(self):\n",
                        assignment_line,
                        "\n",
                    ],
                )
            )
            return edits

        if moved_init_statements:
            ordered = sorted(moved_init_statements, key=lambda item: int(getattr(item, "lineno", 0) or 0))
            first = ordered[0]
            first_start = int(getattr(first, "lineno", 0) or 0)
            first_end = int(getattr(first, "end_lineno", first_start) or first_start)
            edits.append((first_start, first_end, [assignment_line]))
            for statement in ordered[1:]:
                start = int(getattr(statement, "lineno", 0) or 0)
                end = int(getattr(statement, "end_lineno", start) or start)
                edits.append((start, end, []))
            return edits

        insert_line = self._init_insertion_line(init_method.node)
        edits.append((insert_line, insert_line - 1, [assignment_line]))
        return edits

    def _moved_field_initializers(self, analysis: ClassAnalysis, fields: Sequence[str]) -> list[ast.stmt]:
        moved = set(fields)
        statements: list[ast.stmt] = []
        for field_name in fields:
            field_info = analysis.fields.get(field_name)
            initializer = field_info.initializer if field_info else None
            if initializer is None:
                continue
            assigned = set(self._assigned_self_fields(initializer))
            if not assigned or not assigned <= moved:
                continue
            if initializer not in statements:
                statements.append(initializer)
        return statements

    def _init_insertion_line(self, init_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        if init_node.body:
            first = init_node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                return int(getattr(first, "end_lineno", first.lineno) or first.lineno) + 1
        return init_node.lineno + 1

    def _validate_postconditions(
        self,
        transformed: str,
        *,
        source_class: str,
        new_class_name: str,
        candidate: ExtractionCandidate,
        compatibility: CompatibilityPlan,
        before_metrics: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        try:
            tree = ast.parse(transformed)
            cst.parse_module(transformed)
        except SyntaxError:
            return "SYNTAX_VALIDATION_FAILED", {}
        except Exception:
            return "CST_PARSE_FAILED", {}

        source_node = self._find_top_level_class(tree, source_class)
        extracted_node = self._find_top_level_class(tree, new_class_name)
        if source_node is None:
            return "STRUCTURAL_VALIDATION_FAILED", {}
        if extracted_node is None:
            return "STRUCTURAL_VALIDATION_FAILED", {}

        source_after = ExtractClassRefactoring(transformed)._analyse_class(source_node)
        extracted_after = ExtractClassRefactoring(transformed)._analyse_class(extracted_node)
        moved_found = all(name in extracted_after.methods for name in candidate.methods)
        helper_initialized = self._class_initializes_helper(source_after, new_class_name)
        helper_attr = f"_{_snake_case(new_class_name)}"
        after_metrics = self._class_metrics(source_after, composition_fields={helper_attr})
        extracted_metrics = self._class_metrics(extracted_after)
        dependency_passed, dependency_details = self._validate_extracted_dependencies(
            tree=tree,
            source_class=source_class,
            source_after=source_after,
            extracted_after=extracted_after,
            candidate=candidate,
            compatibility=compatibility,
            helper_attr=helper_attr,
        )
        state_moved = all(
            field_name in extracted_after.fields
            and extracted_after.fields[field_name].initializer is not None
            and (
                field_name not in source_after.fields
                or source_after.fields[field_name].initializer is None
            )
            for field_name in candidate.fields
        )
        meaningful_responsibility = (
            len(candidate.methods) >= self.MIN_INFERRED_METHODS
            and bool(candidate.fields)
            and extracted_metrics["implementation_method_count"] >= self.MIN_INFERRED_METHODS
            and candidate.cohesion >= 0.50
        )

        metric_deltas = {
            "implementation_loc": round(
                float(before_metrics.get("implementation_loc", 0))
                - float(after_metrics.get("implementation_loc", 0)),
                4,
            ),
            "effective_method_count": round(
                float(before_metrics.get("effective_method_count", 0))
                - float(after_metrics.get("effective_method_count", 0)),
                4,
            ),
            "weighted_complexity": round(
                float(before_metrics.get("weighted_complexity", 0))
                - float(after_metrics.get("weighted_complexity", 0)),
                4,
            ),
            "owned_field_count": round(
                float(before_metrics.get("owned_field_count", 0))
                - float(after_metrics.get("owned_field_count", 0)),
                4,
            ),
            "responsibility_count": round(
                float(before_metrics.get("responsibility_count", 0))
                - float(after_metrics.get("responsibility_count", 0)),
                4,
            ),
        }
        metric_reduction_passed = all(value > 0 for value in metric_deltas.values())
        before_smell = self._large_class_evaluation(before_metrics)
        after_smell = self._large_class_evaluation(after_metrics)
        extracted_smell = self._large_class_evaluation(extracted_metrics)
        smell_reduced = (
            metric_reduction_passed
            and (not before_smell["detected"] or not after_smell["detected"])
            and float(after_smell["severity"]) < float(before_smell["severity"])
            and not extracted_smell["detected"]
        )
        structural_passed = moved_found and helper_initialized and state_moved
        descriptor_validation = dependency_details.get("member_descriptors", {})

        metadata = {
            "after_metrics": after_metrics,
            "extracted_class_metrics": extracted_metrics,
            "metric_deltas": metric_deltas,
            "large_class_before": before_smell,
            "large_class_after": after_smell,
            "extracted_class_smells": extracted_smell,
            "post_refactoring_smells": {
                "source_large_class": after_smell["detected"],
                "extracted_large_class": extracted_smell["detected"],
                "serious_new_smell": extracted_smell["detected"],
            },
            "dependency_validation": dependency_details,
            "validation": {
                "syntax": "PASS",
                "structural": "PASS" if structural_passed else "FAIL",
                "dependency": "PASS" if dependency_passed else "FAIL",
                "full_api_preservation": descriptor_validation.get(
                    "api_discoverability", "NOT_APPLICABLE"
                ),
                "state_compatibility": descriptor_validation.get(
                    "state_compatibility", "NOT_APPLICABLE"
                ),
                "single_state_owner": descriptor_validation.get(
                    "single_state_owner", "NOT_APPLICABLE"
                ),
                "meaningful_responsibility": "PASS" if meaningful_responsibility else "FAIL",
                "related_state_moved": "PASS" if state_moved else "FAIL",
                "smell_reduction": "PASS" if smell_reduced else "FAIL",
                "large_class_reduction": "PASS" if smell_reduced else "FAIL",
                "post_smell_detection": (
                    "PASS" if not after_smell["detected"] and not extracted_smell["detected"] else "FAIL"
                ),
                "raw_loc_reduced": (
                    source_after.class_loc < int(before_metrics.get("loc", source_after.class_loc))
                ),
            },
            "smell_reduced": smell_reduced,
        }

        if not structural_passed:
            return "STRUCTURAL_VALIDATION_FAILED", metadata
        if not dependency_passed:
            return "DEPENDENCY_VALIDATION_FAILED", metadata
        if not meaningful_responsibility:
            return "NO_SAFE_EXTRACTION_CLUSTER", metadata
        if not smell_reduced:
            return "INSUFFICIENT_CLASS_REDUCTION", metadata
        return "", metadata

    @staticmethod
    def _validate_extracted_dependencies(
        *,
        tree: ast.Module,
        source_class: str,
        source_after: ClassAnalysis,
        extracted_after: ClassAnalysis,
        candidate: ExtractionCandidate,
        compatibility: CompatibilityPlan,
        helper_attr: str,
    ) -> tuple[bool, Dict[str, Any]]:
        selected_methods = set(candidate.methods)
        selected_fields = set(candidate.fields)
        unresolved_helper_methods: list[str] = []
        unresolved_helper_fields: list[str] = []
        for method_name in candidate.methods:
            method = extracted_after.methods.get(method_name)
            if not method:
                unresolved_helper_methods.append(method_name)
                continue
            unresolved_helper_methods.extend(
                f"{method_name}->{called}"
                for called in sorted(method.self_calls - selected_methods)
            )
            unresolved_helper_fields.extend(
                f"{method_name}->{field_name}"
                for field_name in sorted(method.touched_fields - selected_fields)
            )

        unresolved_source_references: list[str] = []
        for method in source_after.methods.values():
            if method.name == "__init__" or ExtractClassRefactoring._is_property_method(method.node):
                continue
            direct_fields = method.touched_fields & selected_fields
            direct_calls = method.self_calls & selected_methods
            unresolved_source_references.extend(
                f"{method.name}->self.{name}"
                for name in sorted(direct_fields | direct_calls)
            )

        missing_delegates = sorted(
            method_name
            for method_name in compatibility.delegated_methods
            if method_name not in source_after.methods
        )
        missing_properties = sorted(
            field_name
            for field_name in compatibility.property_fields
            if field_name not in source_after.methods
        )
        descriptor_passed, descriptor_details = (
            ExtractClassRefactoring._validate_member_descriptors(
                tree=tree,
                source_class=source_class,
                compatibility=compatibility,
            )
        )
        helper_reference_found = helper_attr in source_after.fields
        details = {
            "unresolved_helper_methods": sorted(unresolved_helper_methods),
            "unresolved_helper_fields": sorted(unresolved_helper_fields),
            "unresolved_source_references": sorted(unresolved_source_references),
            "missing_delegates": missing_delegates,
            "missing_properties": missing_properties,
            "member_descriptors": descriptor_details,
            "helper_reference_found": helper_reference_found,
        }
        passed = not any(
            (
                unresolved_helper_methods,
                unresolved_helper_fields,
                unresolved_source_references,
                missing_delegates,
                missing_properties,
            )
        ) and helper_reference_found and descriptor_passed
        return passed, details

    @staticmethod
    def _validate_member_descriptors(
        *,
        tree: ast.Module,
        source_class: str,
        compatibility: CompatibilityPlan,
    ) -> tuple[bool, Dict[str, Any]]:
        if not compatibility.use_member_descriptors:
            return True, {
                "strategy": "not_required",
                "api_discoverability": "NOT_APPLICABLE",
                "state_compatibility": "NOT_APPLICABLE",
            }

        descriptor_node = ExtractClassRefactoring._find_top_level_class(
            tree,
            compatibility.descriptor_class_name,
        )
        descriptor_protocol = {
            node.name
            for node in descriptor_node.body
            if descriptor_node is not None
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        } if descriptor_node is not None else set()
        required_protocol = {"__get__", "__set__", "__delete__"}

        method_bindings: set[str] = set()
        field_bindings: set[str] = set()
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            value = node.value
            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == source_class
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == compatibility.descriptor_class_name
                and len(value.args) >= 2
                and isinstance(value.args[1], ast.Constant)
                and value.args[1].value == target.attr
            ):
                continue
            if len(value.args) >= 3:
                method_bindings.add(target.attr)
            else:
                field_bindings.add(target.attr)

        missing_methods = sorted(set(compatibility.descriptor_methods) - method_bindings)
        missing_fields = sorted(set(compatibility.descriptor_fields) - field_bindings)
        protocol_complete = required_protocol <= descriptor_protocol
        passed = protocol_complete and not missing_methods and not missing_fields
        return passed, {
            "strategy": "explicit_member_descriptors",
            "descriptor_class": compatibility.descriptor_class_name,
            "protocol_methods": sorted(descriptor_protocol & required_protocol),
            "bound_methods": sorted(method_bindings),
            "bound_fields": sorted(field_bindings),
            "missing_methods": missing_methods,
            "missing_fields": missing_fields,
            "api_discoverability": "PASS" if not missing_methods else "FAIL",
            "state_compatibility": (
                "PASS" if protocol_complete and not missing_fields else "FAIL"
            ),
            "single_state_owner": "PASS" if protocol_complete and not missing_fields else "FAIL",
        }

    @staticmethod
    def _class_initializes_helper(analysis: ClassAnalysis, new_class_name: str) -> bool:
        init_method = analysis.methods.get("__init__")
        if not init_method:
            return False
        for node in ast.walk(init_method.node):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != new_class_name:
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    return True
        return False

    @staticmethod
    def _looks_already_applied(source_node: ast.ClassDef, new_class_name: str) -> bool:
        for node in ast.walk(source_node):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == new_class_name
            ):
                return True
        return False

    @staticmethod
    def _class_metrics(
        analysis: ClassAnalysis,
        *,
        composition_fields: set[str] | None = None,
    ) -> Dict[str, Any]:
        method_nodes = [
            node
            for node in analysis.node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        implementation_methods = [
            node
            for node in method_nodes
            if node.name != "__init__"
            and not ExtractClassRefactoring._is_delegate_method(node)
            and not ExtractClassRefactoring._is_property_method(node)
        ]
        delegates = [node for node in method_nodes if ExtractClassRefactoring._is_delegate_method(node)]
        properties = [node for node in method_nodes if ExtractClassRefactoring._is_property_method(node)]
        composition = set(composition_fields or set())
        owned_fields = {
            field_name
            for field_name, field_info in analysis.fields.items()
            if field_info.initializer is not None and field_name not in composition
        }
        implementation_complexity = sum(_cyclomatic_complexity(node) for node in implementation_methods)
        effective_method_count = (
            len(implementation_methods)
            + (0.15 * len(delegates))
            + (0.10 * len(properties))
        )
        weighted_complexity = (
            implementation_complexity
            + (0.10 * len(delegates))
            + (0.05 * len(properties))
        )
        return {
            "class": analysis.name,
            "loc": analysis.class_loc,
            "method_count": len(method_nodes),
            "field_count": len(analysis.fields),
            "implementation_method_count": len(implementation_methods),
            "implementation_loc": sum(
                ExtractClassRefactoring._method_implementation_loc(method)
                for method in implementation_methods
            ),
            "delegate_method_count": len(delegates),
            "property_method_count": len(properties),
            "effective_method_count": round(effective_method_count, 4),
            "implementation_complexity": implementation_complexity,
            "weighted_complexity": round(weighted_complexity, 4),
            "owned_field_count": len(owned_fields),
            "owned_fields": sorted(owned_fields),
            "responsibility_count": ExtractClassRefactoring._responsibility_count(
                analysis,
                implementation_methods,
                owned_fields,
            ),
        }

    @staticmethod
    def _responsibility_count(
        analysis: ClassAnalysis,
        implementation_methods: Sequence[ast.FunctionDef | ast.AsyncFunctionDef],
        owned_fields: set[str],
    ) -> int:
        if not owned_fields:
            return 1 if implementation_methods else 0
        parents = {field_name: field_name for field_name in owned_fields}

        def find(name: str) -> str:
            while parents[name] != name:
                parents[name] = parents[parents[name]]
                name = parents[name]
            return name

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        implementation_names = {node.name for node in implementation_methods}
        pair_usage: Dict[tuple[str, str], int] = {}
        for method_name in implementation_names:
            method = analysis.methods.get(method_name)
            if not method:
                continue
            fields = sorted(method.touched_fields & owned_fields)
            for left_index, left in enumerate(fields):
                for right in fields[left_index + 1:]:
                    pair = (left, right)
                    pair_usage[pair] = pair_usage.get(pair, 0) + 1
        for (left, right), count in pair_usage.items():
            if count >= 2:
                union(left, right)
        return len({find(field_name) for field_name in owned_fields})

    @classmethod
    def _large_class_evaluation(cls, metrics: Dict[str, Any]) -> Dict[str, Any]:
        ratios = {
            "effective_method_count": (
                float(metrics.get("effective_method_count", 0)) / cls.LARGE_CLASS_METHOD_THRESHOLD
            ),
            "implementation_loc": (
                float(metrics.get("implementation_loc", 0)) / cls.LARGE_CLASS_LOC_THRESHOLD
            ),
            "weighted_complexity": (
                float(metrics.get("weighted_complexity", 0)) / cls.LARGE_CLASS_COMPLEXITY_THRESHOLD
            ),
            "owned_field_count": (
                float(metrics.get("owned_field_count", 0)) / cls.LARGE_CLASS_FIELD_THRESHOLD
            ),
            "responsibility_count": (
                float(metrics.get("responsibility_count", 0))
                / cls.LARGE_CLASS_RESPONSIBILITY_THRESHOLD
            ),
        }
        triggered = sorted(name for name, ratio in ratios.items() if ratio >= 1.0)
        return {
            "detected": bool(triggered),
            "severity": round(max(ratios.values(), default=0.0), 4),
            "triggered_metrics": triggered,
            "ratios": {name: round(value, 4) for name, value in ratios.items()},
        }

    @staticmethod
    def _is_delegate_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return False
        call = node.body[0].value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return False
        receiver = call.func.value
        return (
            call.func.attr == node.name
            and isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == "self"
            and receiver.attr.startswith("_")
        )

    @staticmethod
    def _is_property_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        decorators = {_decorator_name(item) for item in node.decorator_list}
        return "property" in decorators or any(item.endswith(".setter") for item in decorators)

    @staticmethod
    def _method_implementation_loc(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        if not node.body:
            return 0
        start = int(getattr(node.body[0], "lineno", node.lineno) or node.lineno)
        end = int(getattr(node, "end_lineno", start) or start)
        return max(1, end - start + 1)

    @staticmethod
    def _estimate_confidence(metadata: Dict[str, Any]) -> float:
        candidate = float(metadata.get("candidate_score") or 0.0)
        smell_reduction = 1.0 if metadata.get("smell_reduced") else 0.0
        return round(min(0.97, 0.35 + (0.4 * candidate) + (0.25 * smell_reduction)), 4)


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _decorator_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _cyclomatic_complexity(node: ast.AST) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(1, len(child.values) - 1)
        elif isinstance(child, ast.Try):
            complexity += len(child.handlers) + int(bool(child.orelse))
        elif isinstance(child, ast.Match):
            complexity += len(child.cases)
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += len(child.generators)
    return complexity


def _method_parameter_names(args: ast.arguments) -> list[str]:
    names = [arg.arg for arg in list(args.posonlyargs) + list(args.args)]
    names.extend(arg.arg for arg in args.kwonlyargs)
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return [name for name in names if name != "self"]


def _normalize_path(value: str) -> str:
    return "/".join(
        part
        for part in str(value).replace("\\", "/").strip().lower().split("/")
        if part and part != "."
    )


def _paths_match(left: str, right: str) -> bool:
    left_path = _normalize_path(left)
    right_path = _normalize_path(right)
    if not left_path or not right_path:
        return False
    return (
        left_path == right_path
        or left_path.endswith(f"/{right_path}")
        or right_path.endswith(f"/{left_path}")
        or left_path.rsplit("/", 1)[-1] == right_path.rsplit("/", 1)[-1]
    )


def _expression_key(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_key(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _annotation_mentions_class(node: ast.AST | None, source_class: str) -> bool:
    if node is None:
        return False
    return any(
        (
            isinstance(child, ast.Name) and child.id == source_class
        )
        or (
            isinstance(child, ast.Attribute) and child.attr == source_class
        )
        or (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and re.search(rf"\b{re.escape(source_class)}\b", child.value) is not None
        )
        for child in ast.walk(node)
    )


def _is_class_constructor(node: ast.AST | None, source_class: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return (
        isinstance(node.func, ast.Name) and node.func.id == source_class
    ) or (
        isinstance(node.func, ast.Attribute) and node.func.attr == source_class
    )


def _assignment_target_keys(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_assignment_target_keys(item) for item in node.elts))
    key = _expression_key(node)
    return {key} if key else set()


def _collect_repository_member_usage(
    tree: ast.Module,
    *,
    file_name: str,
    source_class: str,
    method_names: set[str],
    field_names: set[str],
    usage: RepositoryUsage,
) -> None:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                if _annotation_mentions_class(arg.annotation, source_class):
                    aliases.add(arg.arg)
        elif isinstance(node, ast.AnnAssign) and _annotation_mentions_class(node.annotation, source_class):
            aliases.update(_assignment_target_keys(node.target))

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            targets: set[str] = set()
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = set().union(*(_assignment_target_keys(target) for target in node.targets))
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = _assignment_target_keys(node.target)
                value = node.value
            if not targets or value is None:
                continue
            value_key = _expression_key(value)
            if _is_class_constructor(value, source_class) or value_key in aliases:
                new_aliases = targets - aliases
                if new_aliases:
                    aliases.update(new_aliases)
                    changed = True

    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    source_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == source_class
    ]

    def inside_source_class(node: ast.AST) -> bool:
        line = int(getattr(node, "lineno", 0) or 0)
        return any(
            class_node.lineno <= line <= int(getattr(class_node, "end_lineno", class_node.lineno))
            for class_node in source_nodes
        )

    def remember(member_name: str, category: str, line: int) -> None:
        evidence = f"{file_name}:{line}:{category}"
        usage.evidence.setdefault(member_name, []).append(evidence)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (
                inside_source_class(node)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                continue
            receiver_key = _expression_key(node.value)
            receiver_is_instance = (
                receiver_key in aliases
                or _is_class_constructor(node.value, source_class)
                or receiver_key == source_class
            )
            if not receiver_is_instance:
                continue
            member_name = node.attr
            parent = parents.get(node)
            if member_name in method_names:
                usage.method_references.add(member_name)
                category = "method_call" if isinstance(parent, ast.Call) and parent.func is node else "method_reference"
                remember(member_name, category, int(getattr(node, "lineno", 0) or 0))
            elif member_name in field_names:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    usage.field_writes.add(member_name)
                    remember(member_name, "field_write", int(getattr(node, "lineno", 0) or 0))
                else:
                    usage.field_reads.add(member_name)
                    remember(member_name, "field_read", int(getattr(node, "lineno", 0) or 0))

        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"getattr", "setattr", "hasattr", "delattr"} or len(node.args) < 2:
            continue
        receiver_key = _expression_key(node.args[0])
        member_arg = node.args[1]
        if receiver_key not in aliases or not isinstance(member_arg, ast.Constant) or not isinstance(member_arg.value, str):
            continue
        member_name = member_arg.value
        if member_name in method_names:
            usage.method_references.add(member_name)
            remember(member_name, "dynamic_method_reference", int(getattr(node, "lineno", 0) or 0))
        elif member_name in field_names:
            if node.func.id in {"setattr", "delattr"}:
                usage.field_writes.add(member_name)
                remember(member_name, "dynamic_field_write", int(getattr(node, "lineno", 0) or 0))
            else:
                usage.field_reads.add(member_name)
                remember(member_name, "dynamic_field_read", int(getattr(node, "lineno", 0) or 0))


def _apply_line_edits(
    lines: list[str],
    edits: Iterable[tuple[int, int, list[str]]],
) -> list[str]:
    for start_line, end_line, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        if start_line <= 0:
            continue
        if end_line < start_line:
            index = min(start_line - 1, len(lines))
            lines[index:index] = replacement
        else:
            start = max(0, start_line - 1)
            end = min(len(lines), end_line)
            lines[start:end] = replacement
    return lines


def _ensure_indent(line: str, indent: str) -> str:
    if not line.strip():
        return line
    return indent + line.lstrip()


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _snake_case(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()
    return value or "extracted_class"


def _is_valid_python_identifier(value: str) -> bool:
    return bool(value) and value.isidentifier() and not keyword.iskeyword(value)
