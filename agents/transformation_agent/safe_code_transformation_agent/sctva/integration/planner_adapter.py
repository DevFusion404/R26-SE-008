"""Adapter that maps RDP planner output into SCTVA request payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..constants import ACTION_FAULT_INJECTION, ACTION_NOOP


class PlannerAdapterError(ValueError):
    """Raised when planner payload is malformed or unsupported."""


class PlannerAdapter:
    """Validates and normalizes planner output into SCTVA-compatible actions."""

    def normalize_plan(
        self,
        planner_output: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
        preserve_step_count: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(planner_output, dict):
            raise PlannerAdapterError("Planner output must be an object.")

        plan_id = str(planner_output.get("plan_id", "")).strip()
        if not plan_id:
            raise PlannerAdapterError("Planner output missing required field 'plan_id'.")

        steps = planner_output.get("steps")
        if not isinstance(steps, list):
            raise PlannerAdapterError("Planner output field 'steps' must be a list.")

        actions: List[Dict[str, Any]] = []
        warnings: List[str] = []
        malformed_steps: List[int] = []

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                malformed_steps.append(idx)
                if preserve_step_count:
                    msg = f"Step {idx} is malformed and was mapped to noop."
                    warnings.append(msg)
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "malformed_step",
                                "step_index": idx,
                            },
                            "source_step_id": None,
                            "source_refactoring": None,
                            "warnings": [msg],
                        }
                    )
                continue

            try:
                action = self._map_step(step)
            except PlannerAdapterError as exc:
                malformed_steps.append(idx)
                warnings.append(f"Step {idx}: {exc}")
                if preserve_step_count:
                    msg = f"Step {idx} is malformed and was mapped to noop."
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "malformed_step",
                                "step_index": idx,
                            },
                            "source_step_id": step.get("step_id"),
                            "source_refactoring": step.get("refactoring"),
                            "warnings": [msg, str(exc)],
                        }
                    )
                continue

            if action is None:
                msg = (
                    f"Step {idx} unsupported refactoring "
                    f"'{step.get('refactoring', 'unknown')}', mapped to noop."
                )
                warnings.append(msg)
                if preserve_step_count:
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "unsupported_refactoring",
                                "refactoring": step.get("refactoring"),
                            },
                            "source_step_id": step.get("step_id"),
                            "source_refactoring": step.get("refactoring"),
                            "warnings": [msg],
                        }
                    )
            else:
                actions.append(action)

        if not actions:
            msg = "Planner payload produced zero executable actions; using noop action."
            warnings.append(msg)
            actions.append(
                {
                    "action_type": ACTION_NOOP,
                    "parameters": {
                        "reason": "empty_or_non_actionable_plan",
                    },
                    "source_step_id": None,
                    "source_refactoring": None,
                    "warnings": [msg],
                }
            )

        metadata = {
            "source_agent": "rdp_agent",
            "source_plan_id": plan_id,
            "correlation_id": correlation_id,
            "adapter_warnings": warnings,
            "malformed_steps": malformed_steps,
            "planner_metadata": planner_output.get("metadata", {}),
        }

        return {
            "plan_id": plan_id,
            "actions": actions,
            "behavior_tests": planner_output.get("behavior_tests", []),
            "metadata": metadata,
        }

    def build_request_from_rdp(
        self,
        *,
        request_id: str,
        language: str,
        source_code: str,
        planner_output: Dict[str, Any],
        execution_options: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_plan = self.normalize_plan(
            planner_output,
            correlation_id=correlation_id,
        )

        return {
            "request_id": request_id,
            "language": language,
            "source_code": source_code,
            "refactoring_plan": normalized_plan,
            "execution_options": execution_options
            or {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "timeout_seconds": 10,
                "require_compilation": language.lower() == "java",
            },
        }

    def _map_step(self, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        refactoring = str(step.get("refactoring", "")).strip()

        if not refactoring:
            raise PlannerAdapterError("missing 'refactoring' in step")

        ref_key = refactoring.lower()
        params = step.get("parameters") or {}
        target = step.get("target") or {}

        if not isinstance(params, dict):
            raise PlannerAdapterError("'parameters' must be an object when provided")

        if not isinstance(target, dict):
            raise PlannerAdapterError("'target' must be an object when provided")

        action: Optional[Dict[str, Any]] = None

        rename_aliases = {
            "rename method",
            "rename variable",
            "rename class",
            "rename parameter",
            "rename field",
            "rename attribute",
        }

        # IMPORTANT FIX:
        # Accept any custom fault-injection refactoring name.
        # Examples:
        # - Fault Injection
        # - Fault Injection - Change Return Value
        # - Fault Injection - Numeric Range Violation
        # - Fault Injection - Change Logic
        #
        # Before this fix, "Fault Injection - Numeric Range Violation"
        # was mapped to noop, so no wrong code change happened.
        if ref_key.startswith("fault injection"):
            original_logic = params.get("original_logic") or params.get("old_logic")

            if "faulty_logic" in params:
                faulty_logic = params.get("faulty_logic")
            else:
                faulty_logic = params.get("new_logic")

            if not original_logic or faulty_logic is None:
                raise PlannerAdapterError(
                    "fault injection mapping requires original_logic and faulty_logic"
                )

            action = {
                "action_type": ACTION_FAULT_INJECTION,
                "parameters": {
                    "original_logic": str(original_logic),
                    "faulty_logic": str(faulty_logic),
                    "change_type": params.get("change_type"),
                    "purpose": params.get("purpose"),
                    "target_class": target.get("class"),
                    "target_method": target.get("method"),
                },
            }

        elif ref_key in rename_aliases:
            old_name = params.get("old_name") or target.get("method") or target.get("class")
            new_name = params.get("new_name") or params.get("renamed_to")

            if not old_name or not new_name:
                raise PlannerAdapterError("rename step requires old/new names")

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(str(new_name)),
                },
            }

        elif ref_key == "extract method":
            old_name = target.get("method") or params.get("method")

            if not old_name:
                raise PlannerAdapterError(
                    "extract method mapping requires target.method or parameters.method"
                )

            new_name = params.get("new_method_name") or f"{old_name}Core"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(str(new_name)),
                },
            }

        elif ref_key == "extract class":
            old_name = params.get("source_class") or target.get("class")

            if not old_name:
                raise PlannerAdapterError(
                    "extract class mapping requires source_class/target.class"
                )

            new_name = params.get("new_class_name") or f"{old_name}Extracted"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(str(new_name)),
                },
            }

        elif ref_key == "move method":
            old_name = params.get("method") or target.get("method")

            if not old_name:
                raise PlannerAdapterError("move method mapping requires method name")

            destination = params.get("destination_class")

            if destination and str(destination).strip() != "<inferred_target_class>":
                suffix = self._to_pascal_case(str(destination))
            else:
                suffix = "Moved"

            new_name = f"{old_name}In{suffix}"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(new_name),
                },
            }

        elif ref_key == "replace conditional with polymorphism":
            old_name = target.get("method") or params.get("method")

            if not old_name:
                raise PlannerAdapterError(
                    "replace conditional with polymorphism requires a method target"
                )

            new_name = f"{old_name}Polymorphic"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(new_name),
                },
            }

        elif ref_key == "introduce parameter object":
            old_name = params.get("method") or target.get("method")

            if not old_name:
                raise PlannerAdapterError(
                    "introduce parameter object mapping requires a method"
                )

            po_name = params.get("parameter_object_name")
            suffix = self._to_pascal_case(str(po_name)) if po_name else "ParamObject"
            new_name = f"{old_name}With{suffix}"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(new_name),
                },
            }

        elif ref_key in {
            "hide delegate",
            "replace data value with object",
            "inline class",
            "collapse hierarchy",
            "pull up method",
            "replace parameter with method call",
        }:
            old_name = target.get("method") or target.get("class")

            if not old_name:
                raise PlannerAdapterError(
                    f"{refactoring} mapping requires a method or class target"
                )

            suffix = self._to_pascal_case(ref_key.replace(" ", "_"))
            new_name = f"{old_name}{suffix}"

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(new_name),
                },
            }

        elif ref_key in {
            "extract constant",
            "replace magic number with symbolic constant",
        }:
            if "literal_value" not in params:
                raise PlannerAdapterError(
                    "extract_constant mapping requires parameters.literal_value"
                )

            action = {
                "action_type": "extract_constant",
                "parameters": {
                    "literal_value": params["literal_value"],
                    "constant_name": params.get("constant_name", "EXTRACTED_CONSTANT"),
                },
            }

        elif ref_key == "introduce constant":
            literal_value = params.get("literal_value") if "literal_value" in params else None
            literal_values = params.get("literal_values") if isinstance(params.get("literal_values"), list) else None
            hint = params.get("hint")

            if literal_value is None and not literal_values and not hint:
                raise PlannerAdapterError(
                    "introduce constant mapping requires literal_value, literal_values, or hint"
                )

            action = {
                "action_type": "introduce_constant",
                "parameters": {
                    "literal_value": literal_value,
                    "literal_values": literal_values,
                    "constant_name": params.get("constant_name", "EXTRACTED_CONSTANT"),
                    "hint": hint,
                    "source_file": params.get("source_file"),
                    "source_line": params.get("source_line"),
                    "target_class": target.get("class") or params.get("source_class"),
                    "target_method": target.get("method") or params.get("method"),
                },
            }

        elif ref_key == "remove dead code":
            method = params.get("method") or target.get("method")
            if not method:
                raise PlannerAdapterError(
                    "remove dead code mapping requires parameters.method or target.method"
                )

            action = {
                "action_type": "remove_dead_code",
                "parameters": {
                    "method": str(method),
                    "class_name": target.get("class") or params.get("source_class"),
                },
            }

        elif ref_key in {
            "replace literal",
            "replace temp with query",
        }:
            if "old_literal" not in params or "new_literal" not in params:
                raise PlannerAdapterError(
                    "replace_literal mapping requires old_literal/new_literal"
                )

            action = {
                "action_type": "replace_literal",
                "parameters": {
                    "old_literal": params["old_literal"],
                    "new_literal": params["new_literal"],
                },
            }

        elif ref_key == "inject syntax error":
            action = {
                "action_type": "inject_syntax_error",
                "parameters": {},
            }

        if action:
            action["source_step_id"] = step.get("step_id")
            action["source_refactoring"] = refactoring
            action["warnings"] = []

        return action

    @staticmethod
    def _safe_identifier(name: str) -> str:
        cleaned = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in name.strip()
        )

        if not cleaned:
            return "RenamedSymbol"

        if cleaned[0].isdigit():
            cleaned = f"R_{cleaned}"

        return cleaned

    @staticmethod
    def _to_pascal_case(value: str) -> str:
        parts = [
            p
            for p in "".join(
                ch if ch.isalnum() else " "
                for ch in value
            ).split()
            if p
        ]

        if not parts:
            return "Target"

        return "".join(p[0].upper() + p[1:] for p in parts)