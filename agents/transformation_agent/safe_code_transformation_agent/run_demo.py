"""Run all SCTVA sample cases and write JSON outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.utils.io_helpers import read_json, write_json


def run_case_set(agent: SafeCodeTransformationValidationAgent, file_path: Path, output_dir: Path) -> None:
    payload = read_json(file_path)
    cases = payload.get("cases", [])
    for case in cases:
        name = case.get("name", "unnamed")
        request = case.get("request", {})
        result = agent.execute(request)
        write_json(output_dir / f"{name}.result.json", result)
        print(f"[DEMO] {name}: success={result['success']} rollback={result['rollback_occurred']} confidence={result['confidence_score']}")


def run_planner_adapter_demo(adapter: PlannerAdapter, output_dir: Path, test_data_dir: Path) -> None:
    payload = read_json(test_data_dir / "planner_payloads.json")
    java_plan = payload["rdp_samples"]["java"]
    c_plan = payload["rdp_samples"]["c"]
    java_source = payload["example_source_code"]["java"]
    c_source = payload["example_source_code"]["c"]

    java_request_payload = adapter.build_request_from_rdp(
        request_id="planner_demo_java_001",
        language="java",
        source_code=java_source,
        planner_output=java_plan,
        correlation_id=java_plan.get("metadata", {}).get("correlation_id"),
    )
    write_json(output_dir / "planner_adapter_request_java.json", java_request_payload)
    print("[DEMO] planner adapter request generated for Java")

    c_request_payload = adapter.build_request_from_rdp(
        request_id="planner_demo_c_001",
        language="c",
        source_code=c_source,
        planner_output=c_plan,
        correlation_id=c_plan.get("metadata", {}).get("correlation_id"),
    )
    write_json(output_dir / "planner_adapter_request_c.json", c_request_payload)
    print("[DEMO] planner adapter request generated for C")


def main() -> None:
    root = Path(__file__).resolve().parent
    test_data_dir = root / "test_data"
    result_dir = test_data_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    agent = SafeCodeTransformationValidationAgent()
    adapter = PlannerAdapter()

    run_case_set(agent, test_data_dir / "python_cases.json", result_dir)
    run_case_set(agent, test_data_dir / "java_cases.json", result_dir)
    run_case_set(agent, test_data_dir / "c_cases.json", result_dir)
    run_planner_adapter_demo(adapter, result_dir, test_data_dir)

    print(f"\nDemo complete. Results written to: {result_dir}")


if __name__ == "__main__":
    main()
