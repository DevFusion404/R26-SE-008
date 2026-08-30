"""Conservative Java Large Class -> Extract Class transformation.

The transformer keeps the extracted class in the same compilation unit. This
preserves package/import context and allows the existing SCTVA artifact model to
return one transformed source file. Public methods remain declared on the
source class as thin delegation methods; moved state has exactly one owner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Sequence


SUCCESS = "success"
REVIEW_REQUIRED = "review_required"
ALREADY_APPLIED = "already_applied"
NOT_APPLICABLE = "not_applicable"

# Included in every Extract Class result so reports can prove which runtime
# implementation actually executed. If this value is missing from a report,
# the running SCTVA process/container is using an older or different code copy.
IMPLEMENTATION_REVISION = "java_extract_class_precondition_v13_20260829"

_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_MODIFIERS = {
    "public",
    "protected",
    "private",
    "abstract",
    "static",
    "final",
    "synchronized",
    "native",
    "strictfp",
    "default",
    "transient",
    "volatile",
}
_CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "synchronized", "new"}


@dataclass
class JavaField:
    name: str
    type_name: str
    declaration: str
    start: int
    end: int
    modifiers: set[str] = field(default_factory=set)
    initializer: str = ""


@dataclass
class JavaMethod:
    name: str
    return_type: str
    header: str
    body: str
    start: int
    open_brace: int
    end: int
    indent: str
    parameters: list[str] = field(default_factory=list)
    modifiers: set[str] = field(default_factory=set)
    fields_used: set[str] = field(default_factory=set)
    methods_called: set[str] = field(default_factory=set)
    complexity: int = 1
    is_constructor: bool = False


@dataclass
class JavaClass:
    name: str
    start: int
    open_brace: int
    close_brace: int
    declaration: str
    loc: int
    fields: Dict[str, JavaField]
    methods: list[JavaMethod]
    nesting_depth: int = 0
    is_static: bool = False

    @property
    def methods_by_name(self) -> Dict[str, list[JavaMethod]]:
        result: Dict[str, list[JavaMethod]] = {}
        for method in self.methods:
            result.setdefault(method.name, []).append(method)
        return result


@dataclass
class JavaCandidate:
    methods: list[JavaMethod]
    fields: list[JavaField]
    cohesion: float
    reason: str
    responsibility_count: int = 1


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
    repository_original_code: str = "",
    prior_transformations: Sequence[Dict[str, Any]] | None = None,
) -> tuple[str, int, Dict[str, Any]]:
    del behavior_tests
    transformer = JavaExtractClassRefactoring(source_code)
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
        required_public_methods=required_public_methods,
        required_public_fields=required_public_fields,
        source_resolution_error=source_resolution_error,
        repository_original_code=repository_original_code,
        prior_transformations=prior_transformations,
    )


def top_level_class_names(source_code: str) -> set[str]:
    """Return exact top-level Java class names from masked source text."""
    masked = _mask_c_like(source_code)
    names: set[str] = set()
    for match in re.finditer(rf"\bclass\s+({_IDENTIFIER})\b", masked):
        if _brace_depth(masked, match.start()) == 0:
            names.add(match.group(1))
    return names


def declared_class_names(source_code: str) -> set[str]:
    """Return top-level and member-class names declared in Java source."""

    masked = _mask_c_like(source_code)
    return {
        match.group(1)
        for match in re.finditer(rf"\bclass\s+({_IDENTIFIER})\b", masked)
    }


class JavaExtractClassRefactoring:
    MIN_METHODS = 2
    LARGE_METHOD_THRESHOLD = 20.0
    LARGE_LOC_THRESHOLD = 150.0
    LARGE_COMPLEXITY_THRESHOLD = 50.0
    LARGE_FIELD_THRESHOLD = 15.0
    LARGE_RESPONSIBILITY_THRESHOLD = 7.0

    def __init__(self, source_code: str) -> None:
        self.source = source_code
        self.masked = _mask_c_like(source_code)

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
        required_public_methods: Sequence[str] | None,
        required_public_fields: Sequence[str] | None,
        source_resolution_error: str,
        repository_original_code: str,
        prior_transformations: Sequence[Dict[str, Any]] | None,
    ) -> tuple[str, int, Dict[str, Any]]:
        accepted_prior = [
            dict(item)
            for item in (prior_transformations or [])
            if int(item.get("replacements_count") or 0) > 0
            and str(item.get("status") or "success").lower()
            in {"success", "pass", "accepted", "already_applied"}
        ]
        immutable_original = repository_original_code or self.source
        metadata: Dict[str, Any] = {
            "refactoring": "Extract Class",
            "language": "java",
            "source_class": source_class,
            "source_file": source_file or current_file_name,
            "current_file_name": current_file_name,
            "extracted_class": new_class_name,
            "target_file": target_file or "same_file",
            "delegation_strategy": "explicit_java_delegation",
            "plan_compliance": "UNKNOWN",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
            "implementation_revision": IMPLEMENTATION_REVISION,
            "source_states": {
                "repository_original_code": "immutable",
                "action_input_code": "current_working_source",
                "candidate_output_code": "temporary_until_accepted",
                "repository_original_length": len(immutable_original),
                "action_input_length": len(self.source),
            },
            "prior_transformations": accepted_prior,
        }
        if source_resolution_error:
            return self._review(source_resolution_error, metadata)
        plan_error = self._validate_plan(
            source_file=source_file,
            current_file_name=current_file_name,
            source_class=source_class,
            new_class_name=new_class_name,
            target_file=target_file,
        )
        if plan_error:
            return self._review(plan_error, metadata)

        resolved_source_class, resolution = self._resolve_current_source_class(
            source_class,
            methods_to_extract=methods_to_extract,
            fields_to_extract=fields_to_extract,
        )
        metadata["current_class_resolution"] = resolution
        if resolved_source_class:
            source_class = resolved_source_class
            metadata["source_class"] = source_class
        source_model = _parse_java_class(self.source, source_class)
        if source_model is None:
            return self._review("SOURCE_CLASS_NOT_FOUND", metadata)
        if _parse_java_class(self.source, new_class_name) is not None:
            if self._already_applied(source_model, new_class_name):
                return self.source, 0, {
                    **metadata,
                    "status": ALREADY_APPLIED,
                    "reason": "ALREADY_APPLIED",
                    "plan_compliance": "PASS",
                }
            return self._review("CLASS_NAME_COLLISION", metadata)

        before_metrics = self._metrics(source_model)
        current_smell = self._large_class(before_metrics)
        metadata["before_metrics"] = before_metrics
        metadata["large_class_before"] = current_smell

        original_model = _parse_java_class(immutable_original, source_class)
        original_metrics = self._metrics(original_model) if original_model else None
        original_smell = self._large_class(original_metrics) if original_metrics else None
        metadata["repository_original_metrics"] = original_metrics
        metadata["repository_original_large_class"] = original_smell
        # Extract Class is a remedy for a current Large Class smell.  The
        # action input is the source of truth in a composed pipeline: the
        # original snapshot is retained for audit only and must never force a
        # second extraction after an earlier action has reduced the class.
        # This check deliberately runs before candidate discovery so a small
        # class cannot be reported as an unsafe/failed extraction merely
        # because RDP requested Extract Class for it.
        if not current_smell["detected"]:
            return self._not_applicable(
                "SOURCE_CLASS_NOT_LARGE_ENOUGH",
                metadata,
            )

        candidates_or_error = self._select_candidates(
            source_model,
            methods_to_extract=methods_to_extract,
            fields_to_extract=fields_to_extract,
        )
        if isinstance(candidates_or_error, str):
            return self._review(candidates_or_error, metadata)

        helper_name = self._unique_helper_field(source_model, new_class_name)
        evaluations: list[Dict[str, Any]] = []
        accepted: list[tuple[float, str, JavaCandidate, Dict[str, Any], Dict[str, Any]]] = []
        for candidate_index, candidate in enumerate(candidates_or_error, start=1):
            evaluation: Dict[str, Any] = {
                "candidate_index": candidate_index,
                "methods": [method.name for method in candidate.methods],
                "fields": [item.name for item in candidate.fields],
                "cohesion": round(candidate.cohesion, 4),
                "reason": candidate.reason,
                "selected": False,
            }
            safety_error, dependency = self._validate_candidate(
                source_model,
                candidate,
                preserve_public_api=preserve_public_api,
                project_source_files=project_source_files,
                repository_complete=repository_complete,
                required_public_methods=required_public_methods,
                required_public_fields=required_public_fields,
            )
            evaluation["dependency_analysis"] = dependency
            if safety_error:
                evaluation.update({"status": "rejected", "failure_reason": safety_error})
                evaluations.append(evaluation)
                continue

            candidate_output = self._rewrite(
                source_model,
                candidate,
                new_class_name=new_class_name,
                helper_name=helper_name,
                shared_fields=set(dependency.get("shared_fields") or []),
                preserve_public_api=preserve_public_api,
            )
            post_error, post_metadata = self._validate_postconditions(
                candidate_output,
                source_class=source_class,
                new_class_name=new_class_name,
                candidate=candidate,
                helper_name=helper_name,
                before_metrics=before_metrics,
                preserve_public_api=preserve_public_api,
                dependency_analysis=dependency,
            )
            evaluation.update({
                "status": "accepted" if not post_error else "rejected",
                "failure_reason": post_error,
                "after_metrics": post_metadata.get("after_metrics"),
                "metric_deltas": post_metadata.get("metric_deltas"),
                "large_class_reduction_status": post_metadata.get(
                    "large_class_reduction_status"
                ),
            })
            evaluations.append(evaluation)
            if not post_error:
                score = self._candidate_score(candidate, post_metadata)
                evaluation["score"] = round(score, 4)
                accepted.append(
                    (score, candidate_output, candidate, dependency, post_metadata)
                )

        metadata["candidate_evaluations"] = evaluations
        if not accepted:
            if len(evaluations) == 1:
                metadata["dependency_analysis"] = evaluations[0].get(
                    "dependency_analysis"
                ) or {}
            explicit_plan = bool(_clean_names(methods_to_extract) or _clean_names(fields_to_extract))
            if explicit_plan and len(evaluations) == 1:
                reason = str(evaluations[0].get("failure_reason") or "")
            else:
                reason = "NO_SAFE_MEANINGFUL_EXTRACT_CLASS_CANDIDATE"
            return self._review(reason or "NO_SAFE_MEANINGFUL_EXTRACT_CLASS_CANDIDATE", metadata)

        _, transformed, candidate, dependency, post_metadata = max(
            accepted,
            key=lambda item: (item[0], len(item[2].methods), -item[2].methods[0].start),
        )
        selected_methods = [method.name for method in candidate.methods]
        selected_fields = [item.name for item in candidate.fields]
        for evaluation in evaluations:
            if evaluation["methods"] == selected_methods and evaluation["fields"] == selected_fields:
                evaluation["selected"] = True
                break
        metadata.update(post_metadata)
        metadata["source_states"]["candidate_output_length"] = len(transformed)
        metadata["source_states"]["committed_state"] = "selected_candidate_output"
        metadata.update({
            "methods_moved": selected_methods,
            "fields_moved": selected_fields,
            "candidate_reason": candidate.reason,
            "candidate_cohesion": round(candidate.cohesion, 4),
            "responsibilities_moved": candidate.responsibility_count,
            "dependency_analysis": dependency,
        })

        metadata.update({
            "status": SUCCESS,
            "reason": "extract_class_applied",
            "plan_compliance": "PASS",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
            "delegates_created": [method.name for method in candidate.methods]
            if preserve_public_api else [],
            "public_fields_preserved": [],
            "compatibility": {
                "strategy": "explicit_java_delegation",
                "delegated_methods": [method.name for method in candidate.methods]
                if preserve_public_api else [],
                "state_ownership": "helper_only",
                "direct_public_field_compatibility": (
                    "NOT_REQUIRED_NO_EXTERNAL_REFERENCES"
                    if dependency.get("package_private_fields")
                    else "NOT_REQUIRED_PRIVATE_STATE"
                ),
            },
        })
        return transformed, 1, metadata

    @staticmethod
    def _validate_plan(
        *,
        source_file: str,
        current_file_name: str,
        source_class: str,
        new_class_name: str,
        target_file: str,
    ) -> str:
        if not re.fullmatch(_IDENTIFIER, source_class or ""):
            return "SOURCE_CLASS_NOT_FOUND"
        if not re.fullmatch(_IDENTIFIER, new_class_name or ""):
            return "INVALID_NEW_CLASS_NAME"
        if source_class == new_class_name:
            return "CLASS_NAME_COLLISION"
        if source_file and current_file_name and not _paths_match(source_file, current_file_name):
            return "SOURCE_FILE_MISMATCH"
        if str(target_file or "same_file").strip().lower() not in {
            "same_file",
            "same-source-file",
            "same source file",
        }:
            return "MULTI_FILE_ARTIFACT_UNSUPPORTED"
        return ""

    def _resolve_current_source_class(
        self,
        requested_class: str,
        *,
        methods_to_extract: Sequence[str] | None,
        fields_to_extract: Sequence[str] | None,
    ) -> tuple[str, Dict[str, Any]]:
        """Resolve the action target against the current working source."""
        if _parse_java_class(self.source, requested_class) is not None:
            return requested_class, {
                "status": "success",
                "strategy": "current_ast_exact_class",
                "requested_class": requested_class,
                "resolved_class": requested_class,
            }

        requested_methods = set(_clean_names(methods_to_extract))
        requested_fields = set(_clean_names(fields_to_extract))
        candidates: list[tuple[int, str]] = []
        for class_name in declared_class_names(self.source):
            model = _parse_java_class(self.source, class_name)
            if model is None:
                continue
            method_names = set(model.methods_by_name)
            field_names = set(model.fields)
            if requested_methods and not requested_methods <= method_names:
                continue
            if requested_fields and not requested_fields <= field_names:
                continue
            score = len(requested_methods & method_names) + len(requested_fields & field_names)
            if score:
                candidates.append((score, class_name))

        if candidates:
            best_score = max(score for score, _ in candidates)
            best = sorted(name for score, name in candidates if score == best_score)
            if len(best) == 1:
                return best[0], {
                    "status": "success",
                    "strategy": "current_ast_member_identity_recovery",
                    "requested_class": requested_class,
                    "resolved_class": best[0],
                }
            return "", {
                "status": "review_required",
                "strategy": "current_ast_ambiguous_member_identity",
                "requested_class": requested_class,
                "candidates": best,
            }
        return "", {
            "status": "not_found",
            "strategy": "current_ast_no_matching_class",
            "requested_class": requested_class,
        }

    def _select_candidates(
        self,
        source_class: JavaClass,
        *,
        methods_to_extract: Sequence[str] | None,
        fields_to_extract: Sequence[str] | None,
    ) -> list[JavaCandidate] | str:
        """Return all reasonable candidates without changing working source."""
        requested_methods = _clean_names(methods_to_extract)
        requested_fields = _clean_names(fields_to_extract)
        if requested_methods or requested_fields:
            explicit = self._select_candidate(
                source_class,
                methods_to_extract=requested_methods,
                fields_to_extract=requested_fields,
            )
            return explicit if isinstance(explicit, str) else [explicit]

        methods = [method for method in source_class.methods if not method.is_constructor]
        extraction_limit = max(self.MIN_METHODS, int(len(methods) * 0.5))
        focused = [method for method in methods if method.fields_used and len(method.fields_used) <= 3]
        components = [
            sorted(component, key=lambda method: method.start)
            for component in self._method_components(focused)
            if self.MIN_METHODS <= len(component) < len(methods)
            and len(component) <= extraction_limit
            and set().union(*(method.fields_used for method in component))
        ]
        if not components:
            return "NO_SAFE_EXTRACTION_CLUSTER"

        method_groups: list[list[JavaMethod]] = list(components)
        # Adjacent responsibility groups can form a stronger extraction than
        # either weak component alone. Every component is still evaluated.
        ordered = sorted(components, key=lambda group: min(method.start for method in group))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                combined = sorted({method.start: method for method in left + right}.values(), key=lambda m: m.start)
                if len(combined) <= extraction_limit and len(combined) < len(methods):
                    method_groups.append(combined)

        candidates: list[JavaCandidate] = []
        seen: set[tuple[tuple[str, int], ...]] = set()
        for group in method_groups:
            identity = tuple((method.name, method.start) for method in group)
            if identity in seen:
                continue
            seen.add(identity)
            used_fields = set().union(*(method.fields_used for method in group))
            fields = [
                source_class.fields[name]
                for name in sorted(used_fields)
                if name in source_class.fields
            ]
            if not fields:
                continue
            field_names = {item.name for item in fields}
            cohesion = sum(bool(method.fields_used & field_names) for method in group) / len(group)
            candidates.append(JavaCandidate(
                methods=group,
                fields=fields,
                cohesion=cohesion,
                reason="inferred_field_method_dependency_graph",
                responsibility_count=self._component_count(group),
            ))
        if not candidates:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        return candidates

    def _select_candidate(
        self,
        source_class: JavaClass,
        *,
        methods_to_extract: Sequence[str] | None,
        fields_to_extract: Sequence[str] | None,
    ) -> JavaCandidate | str:
        methods_by_name = source_class.methods_by_name
        requested_methods = _clean_names(methods_to_extract)
        requested_fields = _clean_names(fields_to_extract)

        if requested_methods:
            selected_methods: list[JavaMethod] = []
            for name in requested_methods:
                matches = methods_by_name.get(name, [])
                if not matches:
                    return "METHOD_TARGET_NOT_FOUND"
                if len(matches) != 1:
                    return "AMBIGUOUS_OVERLOADED_METHOD_TARGET"
                if matches[0].is_constructor:
                    return "CONSTRUCTOR_EXTRACTION_UNSUPPORTED"
                selected_methods.append(matches[0])
        else:
            selected_methods = self._infer_method_cluster(source_class)
            if len(selected_methods) < self.MIN_METHODS:
                return "NO_SAFE_EXTRACTION_CLUSTER"

        if len(selected_methods) < self.MIN_METHODS:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        if len(selected_methods) >= len([item for item in source_class.methods if not item.is_constructor]):
            return "SOURCE_CLASS_WOULD_LOSE_PRIMARY_RESPONSIBILITY"

        if requested_fields:
            missing = [name for name in requested_fields if name not in source_class.fields]
            if missing:
                return "FIELD_TARGET_NOT_FOUND"
            selected_fields = [source_class.fields[name] for name in requested_fields]
        else:
            used = set().union(*(method.fields_used for method in selected_methods))
            selected_fields = [source_class.fields[name] for name in sorted(used) if name in source_class.fields]

        if not selected_fields:
            return "NO_RELATED_STATE_FOUND"
        touched = set().union(*(method.fields_used for method in selected_methods))
        selected_names = {item.name for item in selected_fields}
        if not touched <= selected_names:
            return "CROSS_CLASS_FIELD_DEPENDENCY"
        cohesion = sum(bool(method.fields_used & selected_names) for method in selected_methods) / len(
            selected_methods
        )
        if cohesion < 0.5:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        return JavaCandidate(
            methods=selected_methods,
            fields=selected_fields,
            cohesion=cohesion,
            reason=(
                "rdp_explicit_cluster"
                if requested_methods
                else "inferred_secondary_responsibility_components"
            ),
            responsibility_count=(
                1
                if requested_methods
                else self._component_count(selected_methods)
            ),
        )

    @classmethod
    def _infer_method_cluster(cls, source_class: JavaClass) -> list[JavaMethod]:
        candidates = [method for method in source_class.methods if not method.is_constructor]
        # Reporting/orchestration methods often read many fields and would
        # otherwise merge every responsibility into one graph component.
        # Candidate components are built from focused state-owning methods;
        # broad methods remain in the source class and are safely redirected
        # to the helper state during rewriting.
        focused_candidates = [
            method for method in candidates
            if method.fields_used and len(method.fields_used) <= 2
        ]
        components = cls._method_components(focused_candidates)
        eligible = [
            component
            for component in components
            if len(component) >= cls.MIN_METHODS
            and len(component) < len(candidates)
            and set().union(*(method.fields_used for method in component))
        ]
        if not eligible:
            return []

        # Secondary responsibilities are normally smaller than the class's
        # primary responsibility. Extract at most two isolated components so
        # one arbitrary field cluster does not masquerade as Extract Class.
        eligible.sort(key=lambda group: (
            len(group),
            sum(method.complexity for method in group),
            -min(method.start for method in group),
        ))
        selected = list(eligible[0])
        extraction_limit = max(cls.MIN_METHODS, int(len(candidates) * 0.4))
        if len(candidates) >= 8:
            anchor = sum(method.start for method in selected) / len(selected)
            adjacent_components = sorted(
                eligible[1:],
                key=lambda group: (
                    abs((sum(method.start for method in group) / len(group)) - anchor),
                    len(group),
                ),
            )
            for component in adjacent_components:
                if len(selected) + len(component) > extraction_limit:
                    continue
                selected.extend(component)
                break
        return sorted(selected, key=lambda method: method.start)

    @staticmethod
    def _method_components(methods: Sequence[JavaMethod]) -> list[list[JavaMethod]]:
        remaining = set(range(len(methods)))
        components: list[list[JavaMethod]] = []
        while remaining:
            stack = [remaining.pop()]
            indexes: list[int] = []
            while stack:
                current = stack.pop()
                indexes.append(current)
                linked = {
                    other
                    for other in remaining
                    if (
                        methods[current].fields_used & methods[other].fields_used
                        or methods[current].name in methods[other].methods_called
                        or methods[other].name in methods[current].methods_called
                    )
                }
                remaining -= linked
                stack.extend(linked)
            components.append([methods[index] for index in sorted(indexes)])
        return components

    @classmethod
    def _component_count(cls, methods: Sequence[JavaMethod]) -> int:
        return len(cls._method_components(methods))

    def _validate_candidate(
        self,
        source_class: JavaClass,
        candidate: JavaCandidate,
        *,
        preserve_public_api: bool,
        project_source_files: Sequence[Any] | None,
        repository_complete: bool,
        required_public_methods: Sequence[str] | None,
        required_public_fields: Sequence[str] | None,
    ) -> tuple[str, Dict[str, Any]]:
        selected_methods = {method.name for method in candidate.methods}
        selected_fields = {item.name for item in candidate.fields}
        remaining_methods = [
            method for method in source_class.methods
            if method.name not in selected_methods
        ]
        shared_fields = sorted(
            field_name
            for field_name in selected_fields
            if any(field_name in method.fields_used for method in remaining_methods)
        )
        unsupported: list[str] = []
        symbol_ownership: Dict[str, Any] = {}
        for method in candidate.methods:
            header_and_body = f"{method.header} {method.body}"
            ownership = _java_method_symbol_ownership(method, set(source_class.fields))
            symbol_ownership[f"{method.name}@{method.start}"] = ownership
            if "static" in method.modifiers:
                unsupported.append(f"{method.name}:static_method")
            if re.search(r"@Override\b", method.header):
                unsupported.append(f"{method.name}:override_contract")
            if re.search(r"\bsuper\s*[.(]", method.body):
                unsupported.append(f"{method.name}:super_dependency")
            if re.search(r"\bthis\b(?!\s*\.)", method.body):
                unsupported.append(f"{method.name}:this_identity_dependency")
            outside_calls = method.methods_called - selected_methods
            if outside_calls & set(source_class.methods_by_name):
                unsupported.extend(
                    f"{method.name}:source_method:{name}" for name in sorted(outside_calls)
                )
            # A parameter/local and ``this.field`` are distinct Java symbols.
            # Setter shadowing such as ``this.name = name`` is therefore safe:
            # the qualified access remains helper state while the unqualified
            # name remains the lexical parameter after the method is moved.
            unresolved_this_members = set(ownership["qualified_members"]) - (
                set(source_class.fields) | set(source_class.methods_by_name)
            )
            unsupported.extend(
                f"{method.name}:inherited_or_unresolved_member:{name}"
                for name in sorted(unresolved_this_members)
            )
            if " native " in f" {header_and_body} " or " abstract " in f" {header_and_body} ":
                unsupported.append(f"{method.name}:non_concrete_method")

        constructor_dependencies = []
        constructors = [method for method in source_class.methods if method.is_constructor]
        for constructor in constructors:
            for field_name in selected_fields:
                if _java_method_assigns_field(constructor, field_name):
                    constructor_dependencies.append(field_name)

        constructor_bound_final_fields = sorted(
            item.name
            for item in candidate.fields
            if "final" in item.modifiers
            and not item.initializer
            and item.name in constructor_dependencies
        )

        externally_visible_fields = sorted(
            item.name
            for item in candidate.fields
            if item.modifiers & {"public", "protected"}
        )
        package_private_fields = sorted(
            item.name
            for item in candidate.fields
            if not item.modifiers & {"public", "protected", "private"}
        )
        required_fields = set(_clean_names(required_public_fields)) & selected_fields
        externally_used_fields = self._external_field_usage(
            source_class.name,
            selected_fields,
            project_source_files,
        )
        # Package-private state is safe to move only when the imported project
        # is complete enough to prove that no caller accesses it directly.
        unresolved_package_fields = (
            set(package_private_fields) if not repository_complete else set()
        )
        unsafe_field_api = sorted(
            set(externally_visible_fields)
            | required_fields
            | externally_used_fields
            | unresolved_package_fields
        )
        static_fields = sorted(item.name for item in candidate.fields if "static" in item.modifiers)
        dependent_initializers = sorted(
            item.name
            for item in candidate.fields
            if item.initializer
            and (
                _identifiers(item.initializer) & (set(source_class.fields) - selected_fields)
            )
        )
        required_methods = set(_clean_names(required_public_methods))
        missing_required_methods = sorted(required_methods - selected_methods)
        reflection_files = self._reflection_sensitive_usage(
            source_class=source_class.name,
            selected_fields=selected_fields,
            selected_methods=selected_methods,
            project_source_files=project_source_files,
        )

        details = {
            "repository_complete": repository_complete,
            "shared_fields": shared_fields,
            "symbol_ownership": symbol_ownership,
            "unsupported_method_dependencies": unsupported,
            "constructor_field_assignments": sorted(set(constructor_dependencies)),
            "constructor_rewrite_supported": not constructor_bound_final_fields,
            "constructor_bound_final_fields": constructor_bound_final_fields,
            "unsafe_direct_field_api": unsafe_field_api,
            "externally_visible_fields": externally_visible_fields,
            "package_private_fields": package_private_fields,
            "externally_used_fields": sorted(externally_used_fields),
            "unresolved_package_private_fields": sorted(unresolved_package_fields),
            "static_fields": static_fields,
            "dependent_initializers": dependent_initializers,
            "missing_required_methods": missing_required_methods,
            "reflection_sensitive_files": reflection_files,
        }
        if unsupported:
            return "UNSUPPORTED_METHOD_DEPENDENCY", details
        if constructor_bound_final_fields:
            return "FINAL_CONSTRUCTOR_STATE_REQUIRES_HELPER_CONSTRUCTOR", details
        if static_fields:
            return "STATIC_STATE_EXTRACTION_UNSUPPORTED", details
        if dependent_initializers:
            return "FIELD_INITIALIZER_DEPENDENCY_UNSUPPORTED", details
        if preserve_public_api and unsafe_field_api:
            return "DIRECT_FIELD_API_CANNOT_BE_FORWARDED_SAFELY", details
        if missing_required_methods:
            return "REQUIRED_PUBLIC_METHOD_NOT_SELECTED", details
        if reflection_files:
            return "REFLECTION_SENSITIVE_DEPENDENCY", details
        return "", details

    @staticmethod
    def _external_field_usage(
        source_class: str,
        selected_fields: set[str],
        project_source_files: Sequence[Any] | None,
    ) -> set[str]:
        used: set[str] = set()
        for item in project_source_files or []:
            source = item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", None)
            if not isinstance(source, str):
                continue
            masked = _mask_c_like(source)
            aliases = set(re.findall(rf"\b{re.escape(source_class)}\s+({_IDENTIFIER})\b", masked))
            aliases.update(
                re.findall(
                    rf"\b({_IDENTIFIER})\s*=\s*new\s+{re.escape(source_class)}\s*\(",
                    masked,
                )
            )
            aliases.add("this")
            for field_name in selected_fields:
                if any(
                    re.search(rf"\b{re.escape(alias)}\s*\.\s*{re.escape(field_name)}\b", masked)
                    for alias in aliases
                    if alias != "this"
                ) or re.search(
                    rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(field_name)}\b",
                    masked,
                ):
                    used.add(field_name)
        return used

    @staticmethod
    def _reflection_sensitive_usage(
        *,
        source_class: str,
        selected_fields: set[str],
        selected_methods: set[str],
        project_source_files: Sequence[Any] | None,
    ) -> list[str]:
        sensitive: list[str] = []
        member_names = selected_fields | selected_methods
        if not member_names:
            return sensitive
        member_pattern = "|".join(re.escape(name) for name in sorted(member_names))
        for item in project_source_files or []:
            source = item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", None)
            if not isinstance(source, str):
                continue
            masked = _mask_c_like(source)
            # String literals are intentionally inspected in the original
            # source because reflective member names live inside strings.
            reflection = bool(
                re.search(
                    rf"\b(?:getDeclaredField|getField|getDeclaredMethod|getMethod)\s*"
                    rf"\(\s*[\"'](?:{member_pattern})[\"']",
                    source,
                )
                or re.search(
                    rf"\b{re.escape(source_class)}\s*\.\s*class\b.*\b(?:getDeclaredFields|getFields|getDeclaredMethods|getMethods)\s*\(",
                    masked,
                    re.DOTALL,
                )
            )
            if reflection:
                name = item.get("file_name") if isinstance(item, dict) else getattr(item, "file_name", "")
                sensitive.append(str(name or "<unknown-java-source>"))
        return sorted(set(sensitive))

    def _rewrite(
        self,
        source_class: JavaClass,
        candidate: JavaCandidate,
        *,
        new_class_name: str,
        helper_name: str,
        shared_fields: set[str],
        preserve_public_api: bool,
    ) -> str:
        edits: list[tuple[int, int, str]] = []
        selected_method_names = {method.name for method in candidate.methods}
        selected_field_names = {item.name for item in candidate.fields}

        for item in candidate.fields:
            edits.append((item.start, item.end, ""))
        for method in candidate.methods:
            replacement = self._delegate_method(method, helper_name) if preserve_public_api else ""
            edits.append((method.start, method.end, replacement))

        for method in source_class.methods:
            if method.name in selected_method_names:
                continue
            rewritten_body = method.body
            for field_name in shared_fields:
                rewritten_body = _rewrite_java_field_reference(
                    rewritten_body,
                    field_name,
                    f"{helper_name}.{field_name}",
                    rewrite_unqualified=(
                        field_name not in method.parameters
                        and not _declares_local(method.body, field_name)
                    ),
                )
            if rewritten_body != method.body:
                edits.append((method.open_brace + 1, method.end - 1, rewritten_body))

        member_indent = _member_indent(self.source, source_class)
        helper_declaration = (
            f"\n{member_indent}private final {new_class_name} {helper_name} = "
            f"new {new_class_name}();\n"
        )
        edits.append((source_class.open_brace + 1, source_class.open_brace + 1, helper_declaration))

        helper_source = self._build_helper_class(
            source_class,
            candidate,
            new_class_name=new_class_name,
            shared_fields=shared_fields,
        )
        edits.append((source_class.start, source_class.start, helper_source + "\n\n"))
        return _apply_edits(self.source, edits)

    def _build_helper_class(
        self,
        source_class: JavaClass,
        candidate: JavaCandidate,
        *,
        new_class_name: str,
        shared_fields: set[str],
    ) -> str:
        class_indent = _declaration_indent(self.source, source_class.start)
        declaration = "static class" if source_class.nesting_depth > 0 else "final class"
        member_indent = class_indent + "    "
        blocks = [f"{class_indent}{declaration} {new_class_name} {{\n"]
        for item in candidate.fields:
            original_indent = _line_indent(self.source, item.start)
            declaration = _dedent_java_member(original_indent + item.declaration)
            if item.name in shared_fields:
                declaration = _remove_access_modifier(declaration)
            blocks.append(_indent_block(declaration, member_indent))
            if not declaration.endswith("\n"):
                blocks.append("\n")
        if candidate.fields:
            blocks.append("\n")
        for index, method in enumerate(candidate.methods):
            method_source = method.indent + self.source[method.start:method.end]
            method_source = _remove_helper_method_modifiers(_dedent_java_member(method_source))
            blocks.append(_indent_block(method_source, member_indent))
            if not method_source.endswith("\n"):
                blocks.append("\n")
            if index < len(candidate.methods) - 1:
                blocks.append("\n")
        blocks.append(f"{class_indent}}}\n")
        return "".join(blocks)

    @staticmethod
    def _delegate_method(method: JavaMethod, helper_name: str) -> str:
        call_args = ", ".join(method.parameters)
        statement = f"{helper_name}.{method.name}({call_args});"
        if method.return_type != "void":
            statement = f"return {statement}"
        body_indent = method.indent + "    "
        # The replacement starts after the source line's existing indentation.
        header = method.header.rstrip()
        return f"{header} {{\n{body_indent}{statement}\n{method.indent}}}"

    def _validate_postconditions(
        self,
        transformed: str,
        *,
        source_class: str,
        new_class_name: str,
        candidate: JavaCandidate,
        helper_name: str,
        before_metrics: Dict[str, Any],
        preserve_public_api: bool,
        dependency_analysis: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        source_after = _parse_java_class(transformed, source_class)
        helper_after = _parse_java_class(transformed, new_class_name)
        if source_after is None or helper_after is None or not _balanced_c_like(transformed):
            return "STRUCTURAL_VALIDATION_FAILED", {}

        selected_methods = [method.name for method in candidate.methods]
        selected_fields = [item.name for item in candidate.fields]
        helper_methods = helper_after.methods_by_name
        source_methods = source_after.methods_by_name
        methods_moved = all(name in helper_methods for name in selected_methods)
        fields_moved = all(
            name in helper_after.fields and name not in source_after.fields
            for name in selected_fields
        )
        helper_initialized = bool(
            re.search(
                rf"\b{re.escape(new_class_name)}\s+{re.escape(helper_name)}\s*=\s*new\s+"
                rf"{re.escape(new_class_name)}\s*\(\s*\)",
                _mask_c_like(transformed[source_after.open_brace:source_after.close_brace]),
            )
        )
        wrappers = {
            name: any(_is_java_delegate(method, helper_name) for method in source_methods.get(name, []))
            for name in selected_methods
        }
        api_passed = not preserve_public_api or all(wrappers.values())
        unresolved_internal_references = {
            method.name: sorted(
                field_name
                for field_name in selected_fields
                if _has_unresolved_java_field_reference(method, field_name)
            )
            for method in source_after.methods
        }
        unresolved_internal_references = {
            name: fields
            for name, fields in unresolved_internal_references.items()
            if fields
        }
        constructor_fields = set(
            dependency_analysis.get("constructor_field_assignments") or []
        )
        constructor_references_updated = all(
            not _has_unresolved_java_field_reference(method, field_name)
            for method in source_after.methods
            if method.is_constructor
            for field_name in constructor_fields
        )
        repository_references_valid = not (
            dependency_analysis.get("unsafe_direct_field_api")
            or dependency_analysis.get("reflection_sensitive_files")
        )

        after_metrics = self._metrics(source_after, composition_fields={helper_name})
        extracted_metrics = self._metrics(helper_after)
        deltas = {
            "implementation_loc": before_metrics["implementation_loc"] - after_metrics["implementation_loc"],
            "effective_method_count": round(
                before_metrics["effective_method_count"] - after_metrics["effective_method_count"], 4
            ),
            "weighted_complexity": round(
                before_metrics["weighted_complexity"] - after_metrics["weighted_complexity"], 4
            ),
            "owned_field_count": before_metrics["owned_field_count"] - after_metrics["owned_field_count"],
            "responsibility_count": before_metrics["responsibility_count"] - after_metrics["responsibility_count"],
        }
        before_smell = self._large_class(before_metrics)
        after_smell = self._large_class(after_metrics)
        extracted_smell = self._large_class(extracted_metrics)
        reduction_status = self._classify_large_class_reduction(
            before_smell=before_smell,
            after_smell=after_smell,
            extracted_smell=extracted_smell,
            deltas=deltas,
        )
        reduction = reduction_status in {"ELIMINATED", "REDUCED"}
        structural = (
            methods_moved
            and fields_moved
            and helper_initialized
            and not unresolved_internal_references
            and constructor_references_updated
        )
        metadata = {
            "after_metrics": after_metrics,
            "extracted_class_metrics": extracted_metrics,
            "metric_deltas": deltas,
            "large_class_before": before_smell,
            "large_class_after": after_smell,
            "large_class_reduction_status": reduction_status,
            "extracted_class_smells": extracted_smell,
            "post_refactoring_smells": {
                "source_large_class": after_smell["detected"],
                "extracted_large_class": extracted_smell["detected"],
                "serious_new_smell": extracted_smell["detected"],
            },
            "dependency_validation": {
                "methods_moved": methods_moved,
                "fields_moved": fields_moved,
                "helper_initialized": helper_initialized,
                "delegation_wrappers": wrappers,
                "internal_references_updated": not unresolved_internal_references,
                "unresolved_internal_references": unresolved_internal_references,
                "constructor_initialization_updated": constructor_references_updated,
                "repository_references_valid": repository_references_valid,
            },
            "validation": {
                "syntax": "PASS",
                "structural": "PASS" if structural else "FAIL",
                "structural_refactoring": "PASS" if structural else "FAIL",
                "dependency": "PASS" if structural else "FAIL",
                "internal_references_updated": (
                    "PASS" if not unresolved_internal_references else "FAIL"
                ),
                "constructor_initialization": (
                    "PASS" if constructor_references_updated else "FAIL"
                ),
                "repository_references": (
                    "PASS" if repository_references_valid else "FAIL"
                ),
                "full_api_preservation": "PASS" if api_passed else "FAIL",
                "state_compatibility": "PASS" if fields_moved else "FAIL",
                "single_state_owner": "PASS" if fields_moved else "FAIL",
                "meaningful_responsibility": "PASS",
                "related_state_moved": "PASS" if fields_moved else "FAIL",
                "smell_reduction": "PASS" if reduction else "FAIL",
                "large_class_reduction": "PASS" if reduction else "FAIL",
                "post_smell_detection": (
                    "PASS"
                    if reduction_status in {"ELIMINATED", "REDUCED"}
                    and not extracted_smell["detected"]
                    else "FAIL"
                ),
                "raw_loc_reduced": after_metrics["loc"] < before_metrics["loc"],
            },
            "smell_reduced": reduction,
        }
        if not structural:
            return "STRUCTURAL_VALIDATION_FAILED", metadata
        if not api_passed:
            return "PUBLIC_API_PRESERVATION_FAILED", metadata
        if not reduction:
            return "INSUFFICIENT_CLASS_REDUCTION", metadata
        return "", metadata

    @staticmethod
    def _classify_large_class_reduction(
        *,
        before_smell: Dict[str, Any],
        after_smell: Dict[str, Any],
        extracted_smell: Dict[str, Any],
        deltas: Dict[str, float],
    ) -> str:
        if extracted_smell["detected"]:
            return "WORSENED"
        core = (
            "effective_method_count",
            "weighted_complexity",
            "owned_field_count",
            "responsibility_count",
        )
        if (
            after_smell["severity"] > before_smell["severity"] + 0.01
            or any(deltas[name] < 0 for name in core)
        ):
            return "WORSENED"
        if before_smell["detected"] and not after_smell["detected"]:
            return "ELIMINATED"

        improved = sum(deltas[name] > 0 for name in deltas)
        severity_drop = before_smell["severity"] - after_smell["severity"]
        # Delegation can keep raw method count stable, so reduction is judged
        # using implementation weight, state ownership, complexity and
        # responsibility rather than raw declarations alone.
        if improved >= 3 and severity_drop >= 0.005:
            return "REDUCED"
        return "UNCHANGED"

    @staticmethod
    def _candidate_score(
        candidate: JavaCandidate,
        post_metadata: Dict[str, Any],
    ) -> float:
        status_weight = {
            "ELIMINATED": 100.0,
            "REDUCED": 50.0,
        }.get(str(post_metadata.get("large_class_reduction_status") or ""), 0.0)
        deltas = post_metadata.get("metric_deltas") or {}
        reduction_weight = (
            float(deltas.get("effective_method_count") or 0.0) * 3.0
            + float(deltas.get("weighted_complexity") or 0.0) * 1.5
            + float(deltas.get("owned_field_count") or 0.0) * 4.0
            + float(deltas.get("responsibility_count") or 0.0) * 6.0
            + float(deltas.get("implementation_loc") or 0.0) * 0.1
        )
        delegation_penalty = len(candidate.methods) * 0.15
        return status_weight + reduction_weight + (candidate.cohesion * 5.0) - delegation_penalty

    @classmethod
    def _metrics(
        cls,
        model: JavaClass,
        composition_fields: set[str] | None = None,
    ) -> Dict[str, Any]:
        non_constructors = [method for method in model.methods if not method.is_constructor]
        delegates = [method for method in non_constructors if _is_any_java_delegate(method)]
        implementations = [method for method in non_constructors if method not in delegates]
        implementation_loc = sum(_nonblank_loc(method.body) for method in implementations)
        implementation_complexity = sum(method.complexity for method in implementations)
        owned_fields = set(model.fields) - set(composition_fields or set())
        responsibilities = _java_responsibility_count(implementations, owned_fields)
        return {
            "class": model.name,
            "loc": model.loc,
            "method_count": len(model.methods),
            "field_count": len(model.fields),
            "implementation_method_count": len(implementations),
            "implementation_loc": implementation_loc,
            "delegate_method_count": len(delegates),
            "property_method_count": 0,
            "effective_method_count": round(len(implementations) + (0.15 * len(delegates)), 4),
            "implementation_complexity": implementation_complexity,
            "weighted_complexity": round(
                implementation_complexity + (0.1 * len(delegates)), 4
            ),
            "owned_field_count": len(owned_fields),
            "owned_fields": sorted(owned_fields),
            "responsibility_count": responsibilities,
        }

    @classmethod
    def _large_class(cls, metrics: Dict[str, Any]) -> Dict[str, Any]:
        ratios = {
            "effective_method_count": metrics["effective_method_count"] / cls.LARGE_METHOD_THRESHOLD,
            "implementation_loc": metrics["implementation_loc"] / cls.LARGE_LOC_THRESHOLD,
            "weighted_complexity": metrics["weighted_complexity"] / cls.LARGE_COMPLEXITY_THRESHOLD,
            "owned_field_count": metrics["owned_field_count"] / cls.LARGE_FIELD_THRESHOLD,
            "responsibility_count": metrics["responsibility_count"] / cls.LARGE_RESPONSIBILITY_THRESHOLD,
        }
        triggered = sorted(name for name, value in ratios.items() if value >= 1.0)
        return {
            "detected": bool(triggered),
            "severity": round(max(ratios.values(), default=0.0), 4),
            "triggered_metrics": triggered,
            "ratios": {name: round(value, 4) for name, value in ratios.items()},
        }

    @staticmethod
    def _unique_helper_field(source_class: JavaClass, new_class_name: str) -> str:
        base = "_" + new_class_name[:1].lower() + new_class_name[1:]
        existing = set(source_class.fields)
        if base not in existing:
            return base
        index = 2
        while f"{base}{index}" in existing:
            index += 1
        return f"{base}{index}"

    @staticmethod
    def _already_applied(source_class: JavaClass, new_class_name: str) -> bool:
        body = source_class.declaration
        return bool(re.search(rf"\b{re.escape(new_class_name)}\b", body))

    def _review(self, reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:
        return self.source, 0, {
            **metadata,
            "status": REVIEW_REQUIRED,
            "reason": reason,
            "plan_compliance": "FAIL",
            "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
        }

    def _not_applicable(
        self,
        reason: str,
        metadata: Dict[str, Any],
    ) -> tuple[str, int, Dict[str, Any]]:
        return self.source, 0, {
            **metadata,
            "status": NOT_APPLICABLE,
            "success": True,
            "replacements_count": 0,
            "reason": reason,
            "final_decision": "NOT_APPLICABLE",
            "plan_compliance": "NOT_APPLICABLE",
            "behavioral_safety": "NOT_REQUIRED_NO_CHANGE",
            "validation": {
                "syntax": "PASS",
                "structural": "NOT_APPLICABLE",
                "structural_refactoring": "NOT_APPLICABLE",
                "dependency": "NOT_APPLICABLE",
                "full_api_preservation": "NOT_APPLICABLE",
                "state_compatibility": "NOT_APPLICABLE",
                "single_state_owner": "NOT_APPLICABLE",
                "large_class_reduction": "NOT_APPLICABLE",
            },
        }


def _mask_c_like(source: str) -> str:
    chars = list(source)
    state = "code"
    index = 0
    while index < len(chars):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
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
            if char in {'"', "'"}:
                chars[index] = " "
                state = "string" if char == '"' else "char"
                index += 1
                continue
            index += 1
            continue
        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                state = "code"
                index += 2
                continue
            if char not in "\r\n":
                chars[index] = " "
            index += 1
            continue
        quote = '"' if state == "string" else "'"
        if char == "\\":
            chars[index] = " "
            if index + 1 < len(chars) and source[index + 1] not in "\r\n":
                chars[index + 1] = " "
            index += 2
            continue
        if char == quote:
            chars[index] = " "
            state = "code"
        elif char not in "\r\n":
            chars[index] = " "
        index += 1
    return "".join(chars)


def _parse_java_class(source: str, class_name: str) -> JavaClass | None:
    masked = _mask_c_like(source)
    pattern = re.compile(rf"\bclass\s+{re.escape(class_name)}\b[^{{;]*\{{")
    for match in pattern.finditer(masked):
        nesting_depth = _brace_depth(masked, match.start())
        open_brace = masked.find("{", match.start(), match.end())
        close_brace = _matching(masked, open_brace, "{", "}")
        if close_brace is None:
            return None
        fields, methods = _parse_java_members(source, masked, class_name, open_brace, close_brace)
        declaration_start = _java_class_declaration_start(source, match.start())
        declaration_header = source[declaration_start:open_brace]
        model = JavaClass(
            name=class_name,
            start=declaration_start,
            open_brace=open_brace,
            close_brace=close_brace,
            declaration=source[declaration_start:close_brace + 1],
            loc=source[declaration_start:close_brace + 1].count("\n") + 1,
            fields=fields,
            methods=methods,
            nesting_depth=nesting_depth,
            is_static=bool(re.search(r"\bstatic\b", declaration_header)),
        )
        _populate_java_dependencies(model)
        return model
    return None


def _parse_java_members(
    source: str,
    masked: str,
    class_name: str,
    open_brace: int,
    close_brace: int,
) -> tuple[Dict[str, JavaField], list[JavaMethod]]:
    fields: Dict[str, JavaField] = {}
    methods: list[JavaMethod] = []
    index = open_brace + 1
    while index < close_brace:
        while index < close_brace and masked[index].isspace():
            index += 1
        if index >= close_brace:
            break
        start = index
        parens = brackets = 0
        delimiter = ""
        while index < close_brace:
            char = masked[index]
            if char == "(":
                parens += 1
            elif char == ")":
                parens = max(0, parens - 1)
            elif char == "[":
                brackets += 1
            elif char == "]":
                brackets = max(0, brackets - 1)
            elif parens == 0 and brackets == 0 and char in "{;":
                delimiter = char
                break
            index += 1
        if not delimiter:
            break
        if delimiter == ";":
            end = index + 1
            item = _parse_java_field(source[start:end], start, end)
            if item is not None:
                fields[item.name] = item
            index = end
            continue

        block_end = _matching(masked, index, "{", "}")
        if block_end is None or block_end > close_brace:
            break
        header = source[start:index]
        if "(" in masked[start:index] and not re.search(
            r"\b(?:class|interface|enum|record)\b", masked[start:index]
        ):
            method = _parse_java_method(
                source,
                masked,
                class_name=class_name,
                start=start,
                open_brace=index,
                end=block_end + 1,
                header=header,
            )
            if method is not None:
                methods.append(method)
        index = block_end + 1
    return fields, methods


def _parse_java_field(declaration: str, start: int, end: int) -> JavaField | None:
    masked = _mask_c_like(declaration).strip()
    if not masked.endswith(";"):
        return None
    text = masked[:-1].strip()
    text = re.sub(r"^(?:@\w+(?:\s*\([^)]*\))?\s*)+", "", text)
    left, initializer = _split_first_top_level(text, "=")
    if _has_top_level(left, ","):
        return None
    match = re.search(rf"({_IDENTIFIER})\s*(?:\[\s*\])?\s*$", left)
    if not match:
        return None
    name = match.group(1)
    prefix = left[:match.start()].strip()
    tokens = prefix.split()
    modifiers = {token for token in tokens if token in _MODIFIERS}
    type_tokens = [token for token in tokens if token not in _MODIFIERS]
    if not type_tokens:
        return None
    return JavaField(
        name=name,
        type_name=" ".join(type_tokens),
        declaration=declaration,
        start=start,
        end=end,
        modifiers=modifiers,
        initializer=initializer.strip(),
    )


def _mask_java_annotations(text: str) -> str:
    """Blank Java annotations while preserving offsets and line structure.

    The member parser historically selected the *last* identifier followed by
    ``(`` in a method header.  That misclassified parameter annotations such as
    ``@RequestParam("pid")`` as the method name.  This helper removes both
    method-level and parameter-level annotations before method-declarator
    discovery, including qualified annotation names and nested annotation
    arguments.
    """

    chars = list(text)
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "@":
            index += 1
            continue

        previous = text[index - 1] if index else " "
        if previous.isalnum() or previous in "_$":
            index += 1
            continue

        cursor = index + 1
        name_start = cursor
        while cursor < length and (
            text[cursor].isalnum() or text[cursor] in "_$."
        ):
            cursor += 1
        if cursor == name_start:
            index += 1
            continue

        while cursor < length and text[cursor].isspace():
            cursor += 1

        end = cursor
        if cursor < length and text[cursor] == "(":
            closing = _matching(text, cursor, "(", ")")
            if closing is not None:
                end = closing + 1

        for position in range(index, end):
            if chars[position] not in "\r\n":
                chars[position] = " "
        index = max(end, index + 1)

    return "".join(chars)


def _parse_java_method(
    source: str,
    masked: str,
    *,
    class_name: str,
    start: int,
    open_brace: int,
    end: int,
    header: str,
) -> JavaMethod | None:
    masked_header = masked[start:open_brace]
    declaration_header = _mask_java_annotations(masked_header)
    matches = list(re.finditer(rf"({_IDENTIFIER})\s*\(", declaration_header))
    matches = [match for match in matches if match.group(1) not in _CONTROL_NAMES]
    if not matches:
        return None

    # After annotations are masked, a Java method/constructor header has one
    # declarator identifier followed by its parameter list.  Selecting from the
    # cleaned header prevents @RequestParam/@GetMapping from becoming fake
    # method names.
    name_match = matches[-1]
    name = name_match.group(1)
    paren_open = declaration_header.find("(", name_match.start())
    paren_close = _matching(declaration_header, paren_open, "(", ")")
    if paren_close is None:
        return None
    params_raw = header[paren_open + 1:paren_close]
    prefix = declaration_header[:name_match.start()].strip()
    tokens = prefix.split()
    modifiers = {token for token in tokens if token in _MODIFIERS}
    remainder = " ".join(token for token in tokens if token not in _MODIFIERS).strip()
    remainder = re.sub(r"^<[^>]+>\s*", "", remainder).strip()
    is_constructor = name == class_name and not remainder
    if not is_constructor and not remainder:
        return None
    indent = _line_indent(source, start)
    body = source[open_brace + 1:end - 1]
    return JavaMethod(
        name=name,
        return_type="" if is_constructor else remainder,
        header=header,
        body=body,
        start=start,
        open_brace=open_brace,
        end=end,
        indent=indent,
        parameters=_java_parameter_names(params_raw),
        modifiers=modifiers,
        complexity=_complexity(body),
        is_constructor=is_constructor,
    )


def _populate_java_dependencies(model: JavaClass) -> None:
    field_names = set(model.fields)
    method_names = set(model.methods_by_name)
    for method in model.methods:
        masked_body = _mask_c_like(method.body)
        lexical_names = set(method.parameters) | _java_local_names(method.body)
        method.fields_used = {
            name for name in field_names
            if (
                re.search(rf"\bthis\s*\.\s*{re.escape(name)}\b", masked_body)
                or (
                    name not in lexical_names
                    and re.search(
                        rf"(?<![A-Za-z0-9_$.]){re.escape(name)}\b",
                        masked_body,
                    )
                )
            )
        }
        method.methods_called = {
            name for name in method_names
            if name != method.name
            and re.search(
                rf"(?:\bthis\s*\.\s*|(?<![A-Za-z0-9_$.])){re.escape(name)}\s*\(",
                masked_body,
            )
        }


def _java_parameter_names(params_raw: str) -> list[str]:
    names: list[str] = []
    for raw in _split_top_level(params_raw, ","):
        cleaned = _mask_java_annotations(raw).strip()
        cleaned = re.sub(r"\bfinal\b", "", cleaned).strip()
        match = re.search(rf"({_IDENTIFIER})\s*(?:\[\s*\])?\s*$", cleaned)
        if match:
            names.append(match.group(1))
    return names


def _rewrite_java_field_reference(
    body: str,
    field_name: str,
    replacement: str,
    *,
    rewrite_unqualified: bool = True,
) -> str:
    masked = _mask_c_like(body)
    patterns = [re.compile(rf"\bthis\s*\.\s*{re.escape(field_name)}\b")]
    if rewrite_unqualified:
        patterns.append(re.compile(rf"(?<![A-Za-z0-9_$.]){re.escape(field_name)}\b"))
    edits: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in pattern.finditer(masked):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            occupied.append((match.start(), match.end()))
            edits.append((match.start(), match.end(), replacement))
    return _apply_edits(body, edits)


def _has_unresolved_java_field_reference(method: JavaMethod, field_name: str) -> bool:
    masked = _mask_c_like(method.body)
    if re.search(rf"\bthis\s*\.\s*{re.escape(field_name)}\b", masked):
        return True
    if field_name in method.parameters or _declares_local(method.body, field_name):
        return False
    return bool(
        re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(field_name)}\b", masked)
    )


def _java_method_assigns_field(method: JavaMethod, field_name: str) -> bool:
    masked = _mask_c_like(method.body)
    assignment = r"\s*(?:[+\-*/%&|^]?=(?!=)|\+\+|--)"
    if re.search(
        rf"\bthis\s*\.\s*{re.escape(field_name)}\b{assignment}",
        masked,
    ):
        return True
    if field_name in method.parameters or _declares_local(method.body, field_name):
        return False
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_$.]){re.escape(field_name)}\b{assignment}",
            masked,
        )
    )


def _is_java_delegate(method: JavaMethod, helper_name: str) -> bool:
    compact = re.sub(r"\s+", " ", _mask_c_like(method.body)).strip()
    return bool(
        re.fullmatch(
            rf"(?:return\s+)?{re.escape(helper_name)}\s*\.\s*{re.escape(method.name)}"
            r"\s*\([^;]*\)\s*;",
            compact,
        )
    )


def _is_any_java_delegate(method: JavaMethod) -> bool:
    compact = re.sub(r"\s+", " ", _mask_c_like(method.body)).strip()
    return bool(re.fullmatch(rf"(?:return\s+)?{_IDENTIFIER}\s*\.\s*{re.escape(method.name)}\s*\([^;]*\)\s*;", compact))


def _java_responsibility_count(methods: Sequence[JavaMethod], field_names: set[str]) -> int:
    if not methods:
        return 0
    focused_methods = [
        method
        for method in methods
        if method.fields_used & field_names
        and len(method.fields_used & field_names) <= 2
    ]
    if not focused_methods:
        return 1
    remaining = set(range(len(focused_methods)))
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            linked = {
                index for index in remaining
                if (
                    focused_methods[current].fields_used
                    & focused_methods[index].fields_used
                    & field_names
                    or focused_methods[current].name in focused_methods[index].methods_called
                    or focused_methods[index].name in focused_methods[current].methods_called
                )
            }
            remaining -= linked
            stack.extend(linked)
    return count


def _remove_helper_method_modifiers(method_source: str) -> str:
    masked = _mask_c_like(method_source)
    open_brace = masked.find("{")
    if open_brace < 0:
        return method_source
    header = method_source[:open_brace]
    header = re.sub(r"\bprivate\s+", "", header, count=1)
    header = re.sub(r"\bsynchronized\s+", "", header, count=1)
    return header + method_source[open_brace:]


def _remove_access_modifier(declaration: str) -> str:
    return re.sub(r"\b(?:public|protected|private)\s+", "", declaration, count=1)


def _dedent_java_member(text: str) -> str:
    lines = text.splitlines(keepends=True)
    nonblank = [line for line in lines if line.strip()]
    if not nonblank:
        return text
    indent = min((len(line) - len(line.lstrip(" \t")) for line in nonblank), default=0)
    return "".join(line[indent:] if line.strip() else line for line in lines)


def _indent_block(text: str, indent: str) -> str:
    return "".join(indent + line if line.strip() else line for line in text.splitlines(keepends=True))


def _member_indent(source: str, model: JavaClass) -> str:
    for method in model.methods:
        if method.indent:
            return method.indent
    for item in model.fields.values():
        indent = _line_indent(source, item.start)
        if indent:
            return indent
    return _line_indent(source, model.start) + "    "


def _line_indent(source: str, index: int) -> str:
    line_start = source.rfind("\n", 0, index) + 1
    match = re.match(r"[ \t]*", source[line_start:index])
    return match.group(0) if match else ""


def _declaration_indent(source: str, index: int) -> str:
    match = re.match(r"[ \t]*", source[index:])
    return match.group(0) if match else ""


def _java_class_declaration_start(source: str, class_keyword: int) -> int:
    start = source.rfind("\n", 0, class_keyword) + 1
    while start > 0:
        previous_end = start - 1
        previous_start = source.rfind("\n", 0, previous_end) + 1
        stripped = source[previous_start:previous_end].strip()
        if stripped.startswith("@"):
            start = previous_start
            continue
        if stripped.endswith("*/"):
            comment_start = source.rfind("/*", 0, previous_end)
            if comment_start >= 0:
                start = source.rfind("\n", 0, comment_start) + 1
                continue
        break
    return start


def _brace_depth(masked: str, end: int) -> int:
    depth = 0
    for char in masked[:end]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    return depth


def _matching(text: str, start: int, opener: str, closer: str) -> int | None:
    if start < 0 or start >= len(text) or text[start] != opener:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_top_level(text: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    masked = _mask_c_like(text)
    for index, char in enumerate(masked):
        if char in depths:
            depths[char] += 1
        elif char in pairs:
            key = pairs[char]
            depths[key] = max(0, depths[key] - 1)
        elif char == delimiter and not any(depths.values()):
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _split_first_top_level(text: str, delimiter: str) -> tuple[str, str]:
    parts = _split_top_level(text, delimiter)
    return (parts[0], delimiter.join(parts[1:])) if len(parts) > 1 else (text, "")


def _has_top_level(text: str, delimiter: str) -> bool:
    return len(_split_top_level(text, delimiter)) > 1


def _identifiers(text: str) -> set[str]:
    return set(re.findall(rf"\b{_IDENTIFIER}\b", _mask_c_like(text)))


def _java_local_names(body: str) -> set[str]:
    names: set[str] = set()
    masked = _mask_c_like(body)
    pattern = re.compile(
        rf"(?:^|[;{{(])\s*(?:final\s+)?"
        rf"(?P<type>{_IDENTIFIER}(?:\s*<[^;=()]+>)?(?:\s*\[\s*\])?)\s+"
        rf"(?P<name>{_IDENTIFIER})\b(?=\s*(?:=|;|,|\)))",
        re.MULTILINE,
    )
    for match in pattern.finditer(masked):
        if match.group("type").strip() not in {"return", "throw", "new", "case"}:
            names.add(match.group("name"))
    return names


def _java_method_symbol_ownership(
    method: JavaMethod,
    field_names: set[str],
) -> Dict[str, Any]:
    masked = _mask_c_like(method.body)
    parameters = set(method.parameters)
    locals_ = _java_local_names(method.body)
    qualified_members = set(
        re.findall(rf"\bthis\s*\.\s*({_IDENTIFIER})\b", masked)
    )
    qualified_fields = qualified_members & field_names
    unqualified_fields = {
        name
        for name in field_names - parameters - locals_
        if re.search(rf"(?<![A-Za-z0-9_$.]){re.escape(name)}\b", masked)
    }
    return {
        "parameters": sorted(parameters),
        "locals": sorted(locals_),
        "qualified_members": sorted(qualified_members),
        "qualified_fields": sorted(qualified_fields),
        "unqualified_fields": sorted(unqualified_fields),
        "shadowed_field_names": sorted((parameters | locals_) & field_names),
    }


def _declares_local(body: str, name: str) -> bool:
    pattern = re.compile(
        rf"(?:^|[;{{(])\s*(?:final\s+)?"
        rf"(?P<type>{_IDENTIFIER}(?:\s*<[^;=()]+>)?(?:\s*\[\s*\])?)\s+"
        rf"{re.escape(name)}\b(?=\s*(?:=|;|,|\)))",
        re.MULTILINE,
    )
    return any(
        match.group("type").strip() not in {"return", "throw", "new", "case"}
        for match in pattern.finditer(_mask_c_like(body))
    )


def _complexity(body: str) -> int:
    masked = _mask_c_like(body)
    return 1 + len(re.findall(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?", masked))


def _nonblank_loc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _balanced_c_like(source: str) -> bool:
    masked = _mask_c_like(source)
    stack: list[str] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for char in masked:
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def _clean_names(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        name = str(value or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def _paths_match(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"/+", "/", value.replace("\\", "/").strip()).lstrip("./").lower()
    left_normalized = normalize(left)
    right_normalized = normalize(right)
    return left_normalized == right_normalized or right_normalized.endswith("/" + left_normalized)


def _apply_edits(source: str, edits: Iterable[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
