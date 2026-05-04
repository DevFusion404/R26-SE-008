"""
CLI Entry Point
================

Command-line interface for the RDP Agent. Parses arguments and delegates
to the pipeline orchestration functions.

Usage::

    python -m rdp_agent --input quality_report.json --output refactoring_plan.json
    python -m rdp_agent -i report.json -o plan.json -c config.yaml
"""

from __future__ import annotations

import argparse

from .pipeline import generate_plan


def main() -> None:
    """Parse command-line arguments and run the plan generation pipeline."""
    parser = argparse.ArgumentParser(
        description="Refactoring Decision & Planning Agent (RDP Agent)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m rdp_agent --input quality_report.json "
            "--output refactoring_plan.json\n"
            "  python -m rdp_agent -i report.json -o plan.json -c config.yaml"
        ),
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to the quality report JSON (from Code Understanding Agent).",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Path for the output refactoring plan JSON.",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        help="Optional path to a YAML/JSON configuration file.",
    )
    args = parser.parse_args()

    plan = generate_plan(args.input, args.output, args.config)
    print(f"\n[OK] Plan '{plan.plan_id}' generated with {len(plan.steps)} step(s).")
    print(f"   Summary: {plan.summary}")
    print(f"   Output:  {args.output}\n")


if __name__ == "__main__":
    main()
