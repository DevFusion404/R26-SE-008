"""C Large Module -> Extract Component transformation.

C has no classes. This dedicated C refactoring extracts a state struct and
state-aware helper functions. Existing function symbols remain compatibility
wrappers and selected internal globals acquire one owner in the component.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Sequence


SUCCESS = "success"
REVIEW_REQUIRED = "review_required"
ALREADY_APPLIED = "already_applied"
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_STORAGE = {"static", "extern", "register", "auto", "_Thread_local", "thread_local"}


@dataclass
class CGlobal:
    name: str
    type_name: str
    declaration: str
    start: int
    end: int
    storage: set[str] = field(default_factory=set)
    initializer: str = ""


@dataclass
class CFunction:
    name: str
    return_type: str
    header: str
    params_raw: str
    body: str
    start: int
    open_brace: int
    end: int
    parameters: list[str] = field(default_factory=list)
    globals_used: set[str] = field(default_factory=set)
    functions_called: set[str] = field(default_factory=set)
    complexity: int = 1


@dataclass
class CModule:
    functions: list[CFunction]
    globals: Dict[str, CGlobal]

    @property
    def functions_by_name(self) -> Dict[str, list[CFunction]]:
        result: Dict[str, list[CFunction]] = {}
        for function in self.functions:
            result.setdefault(function.name, []).append(function)
        return result


@dataclass
class CCandidate:
    functions: list[CFunction]
    globals: list[CGlobal]
    cohesion: float
    reason: str


def apply_extract_component(
    source_code: str,
    *,
    source_file: str = "",
    current_file_name: str = "",
    source_class: str = "",
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
    del behavior_tests
    return CExtractComponentRefactoring(source_code).apply(
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
    )


# Compatibility for callers created before the C operation was separated from
# the generic Extract Class action. New dispatch always uses the C-specific API.
apply_extract_class = apply_extract_component


class CExtractComponentRefactoring:
    MIN_FUNCTIONS = 2
    LARGE_FUNCTION_THRESHOLD = 20.0
    LARGE_LOC_THRESHOLD = 180.0
    LARGE_COMPLEXITY_THRESHOLD = 50.0
    LARGE_GLOBAL_THRESHOLD = 12.0
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
    ) -> tuple[str, int, Dict[str, Any]]:
        module_name = source_class or _file_stem(source_file or current_file_name) or "CModule"
        metadata: Dict[str, Any] = {
            "refactoring": "Extract Component",
            "sctva_action": "extract_c_component",
            "legacy_rdp_action": "Extract Class",
            "language": "c",
            "source_class": module_name,
            "source_file": source_file or current_file_name,
            "current_file_name": current_file_name,
            "extracted_component": new_class_name,
            "extracted_class": new_class_name,
            "target_file": target_file or "same_file",
            "delegation_strategy": "explicit_c_function_wrappers",
            "plan_compliance": "UNKNOWN",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
        }
        if source_resolution_error:
            return self._review(source_resolution_error, metadata)
        plan_error = self._validate_plan(
            source_file=source_file,
            current_file_name=current_file_name,
            new_class_name=new_class_name,
            target_file=target_file,
        )
        if plan_error:
            return self._review(plan_error, metadata)

        if re.search(rf"\btypedef\s+struct\s+{re.escape(new_class_name)}\b", self.masked):
            if re.search(rf"\b_{re.escape(_snake_case(new_class_name))}\b", self.masked):
                return self.source, 0, {
                    **metadata,
                    "status": ALREADY_APPLIED,
                    "reason": "ALREADY_APPLIED",
                    "plan_compliance": "PASS",
                }
            return self._review("COMPONENT_NAME_COLLISION", metadata)

        module = _parse_c_module(self.source)
        before_metrics = self._metrics(module)
        metadata["before_metrics"] = before_metrics
        candidate_or_error = self._select_candidate(
            module,
            methods_to_extract=methods_to_extract,
            fields_to_extract=fields_to_extract,
        )
        if isinstance(candidate_or_error, str):
            return self._review(candidate_or_error, metadata)
        candidate = candidate_or_error
        metadata.update({
            "methods_moved": [function.name for function in candidate.functions],
            "fields_moved": [item.name for item in candidate.globals],
            "candidate_reason": candidate.reason,
            "candidate_cohesion": round(candidate.cohesion, 4),
        })

        safety_error, dependency = self._validate_candidate(
            module,
            candidate,
            preserve_public_api=preserve_public_api,
            project_source_files=project_source_files,
            repository_complete=repository_complete,
            required_public_methods=required_public_methods,
            required_public_fields=required_public_fields,
        )
        metadata["dependency_analysis"] = dependency
        if safety_error:
            return self._review(safety_error, metadata)

        state_name = self._unique_state_name(module, new_class_name)
        function_prefix = f"{new_class_name}_"
        transformed = self._rewrite(
            module,
            candidate,
            new_class_name=new_class_name,
            state_name=state_name,
            function_prefix=function_prefix,
            shared_globals=set(dependency.get("shared_globals") or []),
            preserve_public_api=preserve_public_api,
        )
        post_error, post_metadata = self._validate_postconditions(
            transformed,
            candidate=candidate,
            new_class_name=new_class_name,
            state_name=state_name,
            function_prefix=function_prefix,
            before_metrics=before_metrics,
            preserve_public_api=preserve_public_api,
        )
        metadata.update(post_metadata)
        if post_error:
            return self._review(post_error, metadata)

        metadata.update({
            "status": SUCCESS,
            "reason": "extract_component_applied",
            "plan_compliance": "PASS",
            "behavioral_safety": "PENDING_PIPELINE_VALIDATION",
            "delegates_created": [function.name for function in candidate.functions]
            if preserve_public_api else [],
            "public_fields_preserved": [],
            "compatibility": {
                "strategy": "explicit_c_function_wrappers",
                "delegated_methods": [function.name for function in candidate.functions]
                if preserve_public_api else [],
                "state_ownership": "component_only",
                "direct_global_compatibility": "NOT_REQUIRED_STATIC_STATE",
            },
        })
        return transformed, 1, metadata

    @staticmethod
    def _validate_plan(
        *,
        source_file: str,
        current_file_name: str,
        new_class_name: str,
        target_file: str,
    ) -> str:
        if not re.fullmatch(_IDENTIFIER, new_class_name or ""):
            return "INVALID_NEW_COMPONENT_NAME"
        if source_file and current_file_name and not _paths_match(source_file, current_file_name):
            return "SOURCE_FILE_MISMATCH"
        if current_file_name and not current_file_name.lower().endswith(".c"):
            return "C_HEADER_COMPONENT_EXTRACTION_UNSUPPORTED"
        if str(target_file or "same_file").strip().lower() not in {
            "same_file",
            "same-source-file",
            "same source file",
        }:
            return "MULTI_FILE_ARTIFACT_UNSUPPORTED"
        return ""

    def _select_candidate(
        self,
        module: CModule,
        *,
        methods_to_extract: Sequence[str] | None,
        fields_to_extract: Sequence[str] | None,
    ) -> CCandidate | str:
        functions_by_name = module.functions_by_name
        requested_functions = _clean_names(methods_to_extract)
        requested_globals = _clean_names(fields_to_extract)
        if requested_functions:
            selected_functions: list[CFunction] = []
            for name in requested_functions:
                matches = functions_by_name.get(name, [])
                if not matches:
                    return "FUNCTION_TARGET_NOT_FOUND"
                if len(matches) != 1:
                    return "AMBIGUOUS_FUNCTION_TARGET"
                selected_functions.append(matches[0])
        else:
            selected_functions = self._infer_function_cluster(module)
            if len(selected_functions) < self.MIN_FUNCTIONS:
                return "NO_SAFE_EXTRACTION_CLUSTER"

        if len(selected_functions) < self.MIN_FUNCTIONS:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        if len(selected_functions) >= len(module.functions):
            return "SOURCE_MODULE_WOULD_LOSE_PRIMARY_RESPONSIBILITY"

        if requested_globals:
            missing = [name for name in requested_globals if name not in module.globals]
            if missing:
                return "GLOBAL_TARGET_NOT_FOUND"
            selected_globals = [module.globals[name] for name in requested_globals]
        else:
            used = set().union(*(function.globals_used for function in selected_functions))
            selected_globals = [module.globals[name] for name in sorted(used) if name in module.globals]
        if not selected_globals:
            return "NO_RELATED_STATE_FOUND"

        touched = set().union(*(function.globals_used for function in selected_functions))
        selected_names = {item.name for item in selected_globals}
        if not touched <= selected_names:
            return "CROSS_COMPONENT_GLOBAL_DEPENDENCY"
        cohesion = sum(bool(function.globals_used & selected_names) for function in selected_functions) / len(
            selected_functions
        )
        if cohesion < 0.5:
            return "NO_SAFE_EXTRACTION_CLUSTER"
        return CCandidate(
            functions=selected_functions,
            globals=selected_globals,
            cohesion=cohesion,
            reason="rdp_explicit_cluster" if requested_functions else "inferred_shared_state_cluster",
        )

    @staticmethod
    def _infer_function_cluster(module: CModule) -> list[CFunction]:
        best: list[CFunction] = []
        for global_name in module.globals:
            cluster = [function for function in module.functions if global_name in function.globals_used]
            if len(cluster) >= 2 and len(cluster) > len(best) and len(cluster) < len(module.functions):
                best = cluster
        return best

    def _validate_candidate(
        self,
        module: CModule,
        candidate: CCandidate,
        *,
        preserve_public_api: bool,
        project_source_files: Sequence[Any] | None,
        repository_complete: bool,
        required_public_methods: Sequence[str] | None,
        required_public_fields: Sequence[str] | None,
    ) -> tuple[str, Dict[str, Any]]:
        selected_functions = {function.name for function in candidate.functions}
        selected_globals = {item.name for item in candidate.globals}
        remaining_functions = [
            function for function in module.functions if function.name not in selected_functions
        ]
        shared_globals = sorted(
            name for name in selected_globals
            if any(name in function.globals_used for function in remaining_functions)
        )
        unsupported: list[str] = []
        for function in candidate.functions:
            if "..." in function.params_raw:
                unsupported.append(f"{function.name}:variadic")
            for name in selected_globals:
                if name in function.parameters or _declares_c_local(function.body, name):
                    unsupported.append(f"{function.name}:shadowed_global:{name}")
            if re.search(rf"&\s*(?:{'|'.join(re.escape(name) for name in selected_functions)})\b", function.body):
                unsupported.append(f"{function.name}:function_pointer_dependency")

        non_static_globals = sorted(
            item.name for item in candidate.globals if "static" not in item.storage
        )
        complex_globals = sorted(
            item.name for item in candidate.globals
            if "[" in item.declaration
            or re.search(rf"\(\s*\*\s*{re.escape(item.name)}\s*\)", item.declaration)
        )
        dependent_initializers = sorted(
            item.name for item in candidate.globals
            if item.initializer
            and (_identifiers(item.initializer) & (set(module.globals) - selected_globals))
        )
        external_global_usage = self._external_global_usage(
            selected_globals,
            project_source_files,
            current_source=self.source,
        )
        required_globals = set(_clean_names(required_public_fields)) & selected_globals
        required_functions = set(_clean_names(required_public_methods))
        missing_required_functions = sorted(required_functions - selected_functions)
        details = {
            "repository_complete": repository_complete,
            "shared_globals": shared_globals,
            "unsupported_function_dependencies": unsupported,
            "non_static_globals": non_static_globals,
            "complex_globals": complex_globals,
            "dependent_initializers": dependent_initializers,
            "external_global_usage": sorted(external_global_usage),
            "required_direct_globals": sorted(required_globals),
            "missing_required_functions": missing_required_functions,
        }
        if unsupported:
            return "UNSUPPORTED_FUNCTION_DEPENDENCY", details
        if complex_globals:
            return "COMPLEX_GLOBAL_DECLARATION_UNSUPPORTED", details
        if dependent_initializers:
            return "GLOBAL_INITIALIZER_DEPENDENCY_UNSUPPORTED", details
        if preserve_public_api and (non_static_globals or external_global_usage or required_globals):
            return "EXTERNAL_GLOBAL_API_CANNOT_BE_FORWARDED_SAFELY", details
        if missing_required_functions:
            return "REQUIRED_PUBLIC_FUNCTION_NOT_SELECTED", details
        return "", details

    @staticmethod
    def _external_global_usage(
        selected_globals: set[str],
        project_source_files: Sequence[Any] | None,
        *,
        current_source: str,
    ) -> set[str]:
        used: set[str] = set()
        for item in project_source_files or []:
            source = item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", None)
            if not isinstance(source, str) or source == current_source:
                continue
            masked = _mask_c_like(source)
            for name in selected_globals:
                if re.search(rf"\bextern\b[^;]*\b{re.escape(name)}\b|\b{re.escape(name)}\b", masked):
                    used.add(name)
        return used

    def _rewrite(
        self,
        module: CModule,
        candidate: CCandidate,
        *,
        new_class_name: str,
        state_name: str,
        function_prefix: str,
        shared_globals: set[str],
        preserve_public_api: bool,
    ) -> str:
        edits: list[tuple[int, int, str]] = []
        selected_function_names = {function.name for function in candidate.functions}
        for item in candidate.globals:
            edits.append((item.start, item.end, ""))

        state_block = self._state_block(
            candidate,
            new_class_name=new_class_name,
            state_name=state_name,
            function_prefix=function_prefix,
        )
        insertion = min(item.start for item in candidate.globals)
        edits.append((insertion, insertion, state_block + "\n"))

        for function in candidate.functions:
            helper = self._helper_function(
                function,
                candidate,
                new_class_name=new_class_name,
                state_name=state_name,
                function_prefix=function_prefix,
            )
            wrapper = self._wrapper_function(function, state_name, function_prefix) if preserve_public_api else ""
            edits.append((function.start, function.end, helper + "\n\n" + wrapper))

        for function in module.functions:
            if function.name in selected_function_names:
                continue
            body = function.body
            for global_name in shared_globals:
                body = _rewrite_c_identifier(body, global_name, f"{state_name}.{global_name}")
            if body != function.body:
                edits.append((function.open_brace + 1, function.end - 1, body))
        return _apply_edits(self.source, edits)

    def _state_block(
        self,
        candidate: CCandidate,
        *,
        new_class_name: str,
        state_name: str,
        function_prefix: str,
    ) -> str:
        lines = [f"typedef struct {new_class_name} {{\n"]
        for item in candidate.globals:
            lines.append(f"    {item.type_name} {item.name};\n")
        lines.append(f"}} {new_class_name};\n\n")
        initialized = [item for item in candidate.globals if item.initializer]
        if initialized:
            lines.append(f"static {new_class_name} {state_name} = {{\n")
            for item in initialized:
                lines.append(f"    .{item.name} = {item.initializer},\n")
            lines.append("};\n")
        else:
            lines.append(f"static {new_class_name} {state_name};\n")
        lines.append("\n")
        for function in candidate.functions:
            params = _helper_c_params(new_class_name, function.params_raw)
            lines.append(
                f"static {_normalize_c_type(function.return_type)} "
                f"{function_prefix}{function.name}({params});\n"
            )
        return "".join(lines)

    def _helper_function(
        self,
        function: CFunction,
        candidate: CCandidate,
        *,
        new_class_name: str,
        state_name: str,
        function_prefix: str,
    ) -> str:
        del state_name
        params = _helper_c_params(new_class_name, function.params_raw)
        body = function.body
        for item in candidate.globals:
            body = _rewrite_c_identifier(body, item.name, f"state->{item.name}")
        for selected in candidate.functions:
            body = _rewrite_c_call(body, selected.name, f"{function_prefix}{selected.name}", "state")
        return (
            f"static {_normalize_c_type(function.return_type)} "
            f"{function_prefix}{function.name}({params}) {{{body}}}"
        )

    @staticmethod
    def _wrapper_function(function: CFunction, state_name: str, function_prefix: str) -> str:
        call_args = ", ".join(function.parameters)
        args = f"&{state_name}" + (f", {call_args}" if call_args else "")
        statement = f"{function_prefix}{function.name}({args});"
        if _normalize_c_type(function.return_type) != "void":
            statement = "return " + statement
        return f"{function.header.rstrip()} {{\n    {statement}\n}}"

    def _validate_postconditions(
        self,
        transformed: str,
        *,
        candidate: CCandidate,
        new_class_name: str,
        state_name: str,
        function_prefix: str,
        before_metrics: Dict[str, Any],
        preserve_public_api: bool,
    ) -> tuple[str, Dict[str, Any]]:
        if not _balanced_c_like(transformed):
            return "STRUCTURAL_VALIDATION_FAILED", {}
        after_module = _parse_c_module(transformed)
        source_function_names = [function.name for function in candidate.functions]
        helper_names = [function_prefix + name for name in source_function_names]
        functions_by_name = after_module.functions_by_name
        helpers_found = all(name in functions_by_name for name in helper_names)
        wrappers = {
            name: any(
                _is_c_delegate(function, state_name, function_prefix)
                for function in functions_by_name.get(name, [])
            )
            for name in source_function_names
        }
        api_passed = not preserve_public_api or all(wrappers.values())
        state_struct = _parse_c_struct_fields(transformed, new_class_name)
        state_fields = {item.name for item in candidate.globals}
        state_moved = state_fields <= state_struct and not (state_fields & set(after_module.globals))
        state_instance = bool(
            re.search(rf"\bstatic\s+{re.escape(new_class_name)}\s+{re.escape(state_name)}\b", transformed)
        )

        after_metrics = self._metrics(
            after_module,
            excluded_function_prefix=function_prefix,
            composition_globals={state_name},
        )
        extracted_functions = [
            function for function in after_module.functions if function.name.startswith(function_prefix)
        ]
        extracted_metrics = self._metrics(
            CModule(functions=extracted_functions, globals={name: item for name, item in after_module.globals.items() if name == state_name})
        )
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
        before_smell = self._large_module(before_metrics)
        after_smell = self._large_module(after_metrics)
        extracted_smell = self._large_module(extracted_metrics)
        reduction = (
            all(value > 0 for value in deltas.values())
            and after_smell["severity"] < before_smell["severity"]
            and (not before_smell["detected"] or not after_smell["detected"])
            and not extracted_smell["detected"]
        )
        structural = helpers_found and state_moved and state_instance
        metadata = {
            "after_metrics": after_metrics,
            "extracted_class_metrics": extracted_metrics,
            "metric_deltas": deltas,
            "large_class_before": before_smell,
            "large_class_after": after_smell,
            "extracted_class_smells": extracted_smell,
            "post_refactoring_smells": {
                "source_large_class": after_smell["detected"],
                "extracted_large_class": extracted_smell["detected"],
                "serious_new_smell": extracted_smell["detected"],
            },
            "dependency_validation": {
                "helper_functions_found": helpers_found,
                "state_fields_moved": state_moved,
                "state_instance_found": state_instance,
                "delegation_wrappers": wrappers,
            },
            "validation": {
                "syntax": "PASS",
                "structural": "PASS" if structural else "FAIL",
                "dependency": "PASS" if structural else "FAIL",
                "full_api_preservation": "PASS" if api_passed else "FAIL",
                "state_compatibility": "PASS" if state_moved else "FAIL",
                "single_state_owner": "PASS" if state_moved else "FAIL",
                "meaningful_responsibility": "PASS",
                "related_state_moved": "PASS" if state_moved else "FAIL",
                "smell_reduction": "PASS" if reduction else "FAIL",
                "large_class_reduction": "PASS" if reduction else "FAIL",
                "post_smell_detection": (
                    "PASS" if not after_smell["detected"] and not extracted_smell["detected"] else "FAIL"
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

    @classmethod
    def _metrics(
        cls,
        module: CModule,
        *,
        excluded_function_prefix: str = "",
        composition_globals: set[str] | None = None,
    ) -> Dict[str, Any]:
        visible_functions = [
            function for function in module.functions
            if not excluded_function_prefix or not function.name.startswith(excluded_function_prefix)
        ]
        delegates = [function for function in visible_functions if _is_any_c_delegate(function)]
        implementations = [function for function in visible_functions if function not in delegates]
        owned_globals = set(module.globals) - set(composition_globals or set())
        implementation_loc = sum(_nonblank_loc(function.body) for function in implementations)
        complexity = sum(function.complexity for function in implementations)
        responsibilities = _c_responsibility_count(implementations, owned_globals)
        return {
            "class": "CModule",
            "loc": sum(_nonblank_loc(function.header + function.body) for function in visible_functions)
            + len(owned_globals),
            "method_count": len(visible_functions),
            "field_count": len(owned_globals),
            "implementation_method_count": len(implementations),
            "implementation_loc": implementation_loc,
            "delegate_method_count": len(delegates),
            "property_method_count": 0,
            "effective_method_count": round(len(implementations) + 0.15 * len(delegates), 4),
            "implementation_complexity": complexity,
            "weighted_complexity": round(complexity + 0.1 * len(delegates), 4),
            "owned_field_count": len(owned_globals),
            "owned_fields": sorted(owned_globals),
            "responsibility_count": responsibilities,
        }

    @classmethod
    def _large_module(cls, metrics: Dict[str, Any]) -> Dict[str, Any]:
        ratios = {
            "effective_method_count": metrics["effective_method_count"] / cls.LARGE_FUNCTION_THRESHOLD,
            "implementation_loc": metrics["implementation_loc"] / cls.LARGE_LOC_THRESHOLD,
            "weighted_complexity": metrics["weighted_complexity"] / cls.LARGE_COMPLEXITY_THRESHOLD,
            "owned_field_count": metrics["owned_field_count"] / cls.LARGE_GLOBAL_THRESHOLD,
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
    def _unique_state_name(module: CModule, new_class_name: str) -> str:
        base = "_" + _snake_case(new_class_name)
        symbols = set(module.globals) | set(module.functions_by_name)
        if base not in symbols:
            return base
        index = 2
        while f"{base}_{index}" in symbols:
            index += 1
        return f"{base}_{index}"

    def _review(self, reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:
        return self.source, 0, {
            **metadata,
            "status": REVIEW_REQUIRED,
            "reason": reason,
            "plan_compliance": "FAIL",
            "behavioral_safety": "NOT_EVALUATED_NO_CHANGE",
        }


def _parse_c_module(source: str) -> CModule:
    masked = _mask_c_like(source)
    globals_by_name: Dict[str, CGlobal] = {}
    functions: list[CFunction] = []
    index = 0
    while index < len(source):
        while index < len(source) and masked[index].isspace():
            index += 1
        if index >= len(source):
            break
        if source[index] == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        start = index
        parens = brackets = 0
        delimiter = ""
        while index < len(source):
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
            item = _parse_c_global(source[start:end], start, end)
            if item is not None:
                globals_by_name[item.name] = item
            index = end
            continue
        block_end = _matching(masked, index, "{", "}")
        if block_end is None:
            break
        header = source[start:index]
        if "(" in masked[start:index] and not re.match(
            r"\s*(?:typedef\s+)?(?:struct|union|enum)\b",
            masked[start:index],
        ):
            function = _parse_c_function(source, masked, start, index, block_end + 1, header)
            if function is not None:
                functions.append(function)
        index = block_end + 1
        while index < len(source) and masked[index].isspace():
            index += 1
        if index < len(source) and masked[index] == ";":
            index += 1
    module = CModule(functions=functions, globals=globals_by_name)
    _populate_c_dependencies(module)
    return module


def _parse_c_global(declaration: str, start: int, end: int) -> CGlobal | None:
    masked = _mask_c_like(declaration).strip()
    if not masked.endswith(";") or masked.startswith("typedef"):
        return None
    text = masked[:-1].strip()
    left, initializer = _split_first_top_level(text, "=")
    if "(" in left:
        return None
    if _has_top_level(left, ","):
        return None
    match = re.search(rf"({_IDENTIFIER})\s*$", left)
    if not match:
        return None
    name = match.group(1)
    prefix = left[:match.start()].strip()
    tokens = prefix.replace("*", " * ").split()
    storage = {token for token in tokens if token in _STORAGE}
    type_tokens = [token for token in tokens if token not in _STORAGE]
    if not type_tokens:
        return None
    type_name = " ".join(type_tokens).replace(" *", "*")
    return CGlobal(
        name=name,
        type_name=type_name,
        declaration=declaration,
        start=start,
        end=end,
        storage=storage,
        initializer=initializer.strip(),
    )


def _parse_c_function(
    source: str,
    masked: str,
    start: int,
    open_brace: int,
    end: int,
    header: str,
) -> CFunction | None:
    masked_header = masked[start:open_brace]
    matches = list(re.finditer(rf"({_IDENTIFIER})\s*\(", masked_header))
    if not matches:
        return None
    name_match = matches[-1]
    name = name_match.group(1)
    if name in {"if", "for", "while", "switch"}:
        return None
    paren_open = masked_header.find("(", name_match.start())
    paren_close = _matching(masked_header, paren_open, "(", ")")
    if paren_close is None:
        return None
    return_type = header[:name_match.start()].strip()
    if not return_type or "#" in return_type:
        return None
    params_raw = header[paren_open + 1:paren_close].strip()
    body = source[open_brace + 1:end - 1]
    return CFunction(
        name=name,
        return_type=return_type,
        header=header,
        params_raw=params_raw,
        body=body,
        start=start,
        open_brace=open_brace,
        end=end,
        parameters=_c_parameter_names(params_raw),
        complexity=_complexity(body),
    )


def _populate_c_dependencies(module: CModule) -> None:
    global_names = set(module.globals)
    function_names = set(module.functions_by_name)
    for function in module.functions:
        masked = _mask_c_like(function.body)
        function.globals_used = {
            name for name in global_names
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}\b", masked)
        }
        function.functions_called = {
            name for name in function_names
            if name != function.name and re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*\(", masked)
        }


def _c_parameter_names(params_raw: str) -> list[str]:
    if not params_raw or params_raw.strip() == "void":
        return []
    names: list[str] = []
    for raw in _split_top_level(params_raw, ","):
        cleaned = raw.strip()
        pointer_name = re.search(rf"\(\s*\*\s*({_IDENTIFIER})\s*\)", cleaned)
        if pointer_name:
            names.append(pointer_name.group(1))
            continue
        match = re.search(rf"({_IDENTIFIER})\s*(?:\[[^]]*\])?\s*$", cleaned)
        if match:
            names.append(match.group(1))
    return names


def _helper_c_params(new_class_name: str, params_raw: str) -> str:
    cleaned = params_raw.strip()
    if not cleaned or cleaned == "void":
        return f"{new_class_name} *state"
    return f"{new_class_name} *state, {cleaned}"


def _rewrite_c_identifier(body: str, name: str, replacement: str) -> str:
    masked = _mask_c_like(body)
    pattern = re.compile(rf"(?<![A-Za-z0-9_.>]){re.escape(name)}\b")
    edits = [(match.start(), match.end(), replacement) for match in pattern.finditer(masked)]
    return _apply_edits(body, edits)


def _rewrite_c_call(body: str, old_name: str, new_name: str, state_arg: str) -> str:
    masked = _mask_c_like(body)
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old_name)}\s*\(")
    edits: list[tuple[int, int, str]] = []
    for match in pattern.finditer(masked):
        open_paren = masked.find("(", match.start(), match.end())
        next_nonspace = open_paren + 1
        while next_nonspace < len(masked) and masked[next_nonspace].isspace():
            next_nonspace += 1
        separator = "" if next_nonspace < len(masked) and masked[next_nonspace] == ")" else ", "
        edits.append((match.start(), open_paren + 1, f"{new_name}({state_arg}{separator}"))
    return _apply_edits(body, edits)


def _is_c_delegate(function: CFunction, state_name: str, function_prefix: str) -> bool:
    compact = re.sub(r"\s+", " ", _mask_c_like(function.body)).strip()
    return bool(
        re.fullmatch(
            rf"(?:return\s+)?{re.escape(function_prefix + function.name)}\s*\(\s*&"
            rf"{re.escape(state_name)}(?:\s*,[^;]*)?\)\s*;",
            compact,
        )
    )


def _is_any_c_delegate(function: CFunction) -> bool:
    compact = re.sub(r"\s+", " ", _mask_c_like(function.body)).strip()
    return bool(re.fullmatch(rf"(?:return\s+)?{_IDENTIFIER}\s*\([^;]*\)\s*;", compact))


def _parse_c_struct_fields(source: str, struct_name: str) -> set[str]:
    masked = _mask_c_like(source)
    match = re.search(rf"\btypedef\s+struct\s+{re.escape(struct_name)}\s*\{{", masked)
    if not match:
        return set()
    open_brace = masked.find("{", match.start(), match.end())
    close_brace = _matching(masked, open_brace, "{", "}")
    if close_brace is None:
        return set()
    fields: set[str] = set()
    for declaration in source[open_brace + 1:close_brace].split(";"):
        name_match = re.search(rf"({_IDENTIFIER})\s*$", declaration.strip())
        if name_match:
            fields.add(name_match.group(1))
    return fields


def _c_responsibility_count(functions: Sequence[CFunction], global_names: set[str]) -> int:
    if not functions:
        return 0
    remaining = set(range(len(functions)))
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            linked = {
                index for index in remaining
                if (
                    functions[current].globals_used & functions[index].globals_used & global_names
                    or functions[current].name in functions[index].functions_called
                    or functions[index].name in functions[current].functions_called
                )
            }
            remaining -= linked
            stack.extend(linked)
    return count


def _declares_c_local(body: str, name: str) -> bool:
    pattern = re.compile(
        rf"(?:^|[;{{(])\s*(?P<type>(?:const\s+|volatile\s+|unsigned\s+|signed\s+|struct\s+)*"
        rf"{_IDENTIFIER}(?:\s*\*)*)\s+{re.escape(name)}\b(?=\s*(?:=|;|,|\)|\[))",
        re.MULTILINE,
    )
    return any(
        match.group("type").strip().split()[0] not in {"return", "goto", "sizeof", "case"}
        for match in pattern.finditer(_mask_c_like(body))
    )


def _normalize_c_type(type_name: str) -> str:
    tokens = [token for token in type_name.split() if token not in _STORAGE and token != "inline"]
    return " ".join(tokens)


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
    masked = _mask_c_like(text)
    parts: list[str] = []
    start = 0
    parens = brackets = braces = 0
    for index, char in enumerate(masked):
        if char == "(":
            parens += 1
        elif char == ")":
            parens = max(0, parens - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif char == "{":
            braces += 1
        elif char == "}":
            braces = max(0, braces - 1)
        elif char == delimiter and not (parens or brackets or braces):
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


def _complexity(body: str) -> int:
    masked = _mask_c_like(body)
    return 1 + len(re.findall(r"\b(?:if|for|while|case)\b|&&|\|\||\?", masked))


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


def _snake_case(value: str) -> str:
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9_]+", "_", value).strip("_") or "component"


def _file_stem(value: str) -> str:
    normalized = value.replace("\\", "/").rsplit("/", 1)[-1]
    return normalized.rsplit(".", 1)[0]


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
