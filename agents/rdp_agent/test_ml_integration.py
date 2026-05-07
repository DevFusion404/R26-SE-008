"""End-to-end test for the ML-enhanced RDP Agent pipeline."""

import sys
import json

sys.path.insert(0, ".")

from src.models import QualityReport
from src.pipeline import RDPAgent

report_data = {
    "target": "OrderProcessor.java",
    "smells": [
        {
            "id": "smell_001",
            "type": "Long Method",
            "location": {
                "class": "OrderProcessor",
                "method": "calculateTotal",
                "lines": [10, 85],
            },
            "metrics": {"lines_of_code": 75, "cyclomatic_complexity": 12},
            "severity": "high",
        },
        {
            "id": "smell_002",
            "type": "God Class",
            "location": {"class": "OrderProcessor", "method": ""},
            "metrics": {"method_count": 25, "lines_of_code": 800},
            "severity": "critical",
        },
    ],
}

report = QualityReport.from_dict(report_data)
agent = RDPAgent(ml_config={"enabled": True})
result = agent.process_report_with_trace(report)

# Show ML prediction trace
for ml_t in result["trace"].get("ml_prediction", []):
    print(f"--- ML Predictions for {ml_t['smell_id']} ({ml_t['smell_type']}) ---")
    print(f"ML Available: {ml_t['ml_available']}")
    for p in ml_t["predictions"]:
        print(f"  {p['refactoring']}:")
        print(
            f"    suitability={p['contextual_suitability']:.4f}  "
            f"quality={p['quality_improvement']:.4f}  "
            f"risk={p['behavioral_risk']:.4f}  "
            f"confidence={p['confidence']:.3f}"
        )

# Show candidate scoring methods
print()
for cg in result["trace"]["candidate_generation"]:
    selected = cg["selected"]
    score = cg["selected_score"]
    method = cg.get("scoring_method", "N/A")
    print(f"--- {cg['smell_id']}: Selected '{selected}' (score={score}, method={method}) ---")
    for c in cg["candidates"]:
        c_method = c.get("scoring_method", "N/A")
        print(f"  {c['name']}: score={c['score']}  method={c_method}")

print()
print("Plan:", result["plan"]["summary"])
print()
print("SUCCESS: ML-enhanced pipeline completed!")
