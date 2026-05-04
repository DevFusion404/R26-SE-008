# Refactoring Decision & Planning Agent (RDP Agent)

A research-grade, modular Python agent that analyses code quality reports and produces structured refactoring plans. Part of a multi-agent refactoring system.

## Architecture

```
Code Understanding Agent ──► RDP Agent ──► Safe Transformation Agent
     (quality_report.json)   (this repo)    (refactoring_plan.json)
```

The RDP Agent follows an **8-step pipeline**, each implemented as an independent, extensible module:

| Step | Responsibility | Module |
|------|---------------|--------|
| 1 | Interpret detected problems from code smells | `problem_interpreter.py` |
| 2 | Map code smells to refactoring techniques | `knowledge_base.py` |
| 3 | Generate multiple candidate strategies | `candidate_generator.py` |
| 3b | **Predict quality-metric impact of each candidate** | `impact_predictor.py` |
| 4 | Evaluate strategies using impact-aware weighted scoring | `decision_engine.py` |
| 5 | Analyze dependencies between refactorings | `dependency_analyzer.py` |
| 6 | Determine execution order (topological sort) | `dependency_analyzer.py` |
| 7 | Generate structured, machine-executable plan | `plan_generator.py` |

## Quick Start

### Prerequisites

- Python 3.10+
- Flask (for the Web UI): `pip install flask`
- (Optional) PyYAML for YAML config support: `pip install pyyaml`
- (Optional) pytest for running tests: `pip install pytest`

Install all dependencies at once:

```bash
pip install flask pyyaml pytest
```

### Option 1 — Web UI (Recommended)

Start the Flask development server:

```bash
python app.py
```

Then open your browser and navigate to:

```
http://localhost:5000
```

From the web interface you can:

1. **Drag & drop** (or browse) a `.json` quality report file
2. Click **Generate Refactoring Plan**
3. View the plan summary and individual refactoring steps
4. **Copy** the JSON to clipboard or **Download** it as a file

### Option 2 — Command Line

#### Run with Sample Data

```bash
python -m rdp_agent --input quality_report.json --output refactoring_plan.json
```

#### Run with Custom Config

```bash
python -m rdp_agent --input quality_report.json --output plan.json --config config.yaml
```

### Option 3 — Python API

```python
# Convenience functions
from rdp_agent import generate_plan, generate_plan_from_dict

plan = generate_plan("quality_report.json", "plan.json")
plan_dict = generate_plan_from_dict(report_data)

# OOP interface (full control)
from rdp_agent import RDPAgent, QualityReport

agent = RDPAgent()
report = QualityReport.from_dict(data)
plan = agent.process_report(report)

# Custom components (extensible)
from rdp_agent import RDPAgent, RefactoringKnowledgeBase, DecisionEngine

custom_kb = RefactoringKnowledgeBase(catalog=my_catalog)
custom_engine = DecisionEngine(weights={"risk_weight": 0.6, "impact_weight": 0.3})
agent = RDPAgent(knowledge_base=custom_kb, engine=custom_engine)

# With custom impact prediction rules
from rdp_agent import ImpactPredictor

predictor = ImpactPredictor(rules=my_custom_rules)
agent = RDPAgent(impact_predictor=predictor)
```

### Run Tests

```bash
pytest test_rdp_agent.py -v
```

## Project Structure

```
├── rdp_agent/                    # Main package (modular architecture)
│   ├── __init__.py               # Public API & backward-compatible re-exports
│   ├── __main__.py               # python -m rdp_agent support
│   ├── models.py                 # Data models (CodeSmell, QualityReport, ImpactPrediction, ...)
│   ├── knowledge_base.py         # Refactoring catalog (13 smell types) & dependency graph
│   ├── problem_interpreter.py    # Precondition evaluation (Step 1)
│   ├── impact_predictor.py       # Refactoring Impact Prediction (Step 3b) — NEW
│   ├── decision_engine.py        # Weighted scoring with impact-aware mode (Step 4)
│   ├── candidate_generator.py    # Filter, score & select best candidate (Steps 2-3)
│   ├── dependency_analyzer.py    # Topological sort with deadlock resolution (Steps 5-6)
│   ├── plan_generator.py         # Plan assembly, explanations & parameters (Step 7)
│   ├── config.py                 # YAML/JSON configuration loader
│   ├── pipeline.py               # RDPAgent orchestrator class + convenience functions
│   └── cli.py                    # CLI entry point (argparse)
├── app.py                        # Flask web server (Web UI entry point)
├── config.yaml                   # Configurable weights, thresholds, log level
├── test_rdp_agent.py             # pytest test suite (52 tests)
├── quality_report.json           # Sample input from Code Understanding Agent
├── templates/
│   └── index.html                # Web UI template (upload form, pipeline trace & results)
├── static/
│   ├── style.css                 # Web UI styles (dark theme)
│   └── favicon.png               # Web UI favicon
└── README.md                     # This file
```

## Input Format

The agent expects a JSON file with the following structure:

```json
{
  "target": "OrderProcessor.java",
  "smells": [
    {
      "id": "smell_001",
      "type": "Long Method",
      "location": { "class": "OrderProcessor", "method": "calculateTotal", "lines": [10, 160] },
      "metrics": { "lines_of_code": 150, "cyclomatic_complexity": 30 },
      "severity": "high"
    }
  ],
  "metrics_summary": { "total_lines": 850 }
}
```

> **Note:** The `file_name` field is also supported as a fallback for `target`, ensuring compatibility with the Code Understanding Agent's output format.

## Output Format

The generated plan follows this structure:

```json
{
  "plan_id": "plan_20250321_135600",
  "target": "OrderProcessor.java",
  "steps": [
    {
      "step_id": 1,
      "smell_id": "smell_001",
      "refactoring": "Extract Method",
      "target": { "class": "OrderProcessor", "method": "calculateTotal" },
      "parameters": { "source_lines": [10, 160], "new_method_name": "extracted_calculateTotal" },
      "explanation": "Extract Method on OrderProcessor.calculateTotal to address Long Method smell. ..."
    }
  ],
  "summary": "3-step plan addressing 3 of 7 detected smells..."
}
```

## Supported Smell Types

| Smell Type | Candidate Refactorings |
|---|---|
| Long Method | Extract Method, Replace Temp with Query, Introduce Parameter Object |
| God Class | Extract Class, Extract Subclass |
| Feature Envy | Move Method |
| Duplicate Code | Extract Method, Pull Up Method |
| Data Clumps | Introduce Parameter Object, Extract Class |
| Shotgun Surgery | Move Method, Inline Class |
| Switch Statements | Replace Conditional with Polymorphism |
| Lazy Class | Inline Class, Collapse Hierarchy |
| Speculative Generality | Collapse Hierarchy, Remove Dead Code |
| Primitive Obsession | Replace Data Value with Object, Introduce Parameter Object |
| Long Parameter List | Introduce Parameter Object, Replace Parameter with Method Call |
| Message Chains | Hide Delegate |
| Comments | Extract Method, Rename Method |

## Configuration

Edit `config.yaml` to adjust behaviour without changing code:

```yaml
weights:
  complexity_weight: 0.2           # Lower complexity is better
  risk_weight: 0.4                 # Lower risk is better
  impact_weight: 0.4               # Higher impact is better
  impact_prediction_weight: 0.3    # Weight of predicted quality-metric bonus

severity_order:
  critical: 4
  high: 3
  medium: 2
  low: 1

log_level: INFO
```

## Scoring Formula

**Base score:**

```
base_score = complexity_weight × (4 - complexity) + risk_weight × (4 - risk) + impact_weight × impact
```

Where `low=1, medium=2, high=3`. Higher score = better candidate.

**Impact-aware score** (when predictions are available):

```
impact_bonus = complexity_reduction + coupling_bonus + cohesion_bonus + maintainability - risk_penalty
final_score  = base_score + impact_prediction_weight × impact_bonus
```

The Impact Predictor estimates these metrics using a configurable heuristic rules table.

## Impact Prediction

The **Refactoring Impact Prediction** module (Step 3b) estimates the expected quality-metric changes *before* the decision engine scores candidates. For each viable refactoring it predicts:

| Metric | Description |
|--------|-------------|
| `predicted_complexity_after` | Estimated cyclomatic complexity after refactoring |
| `coupling_change` | Expected coupling change (negative = reduction = better) |
| `cohesion_change` | Expected cohesion change (positive = improvement) |
| `maintainability_improvement` | Estimated maintainability gain (0–1) |
| `risk_score` | Risk of introducing defects (0–1, lower is safer) |

**Example:**

Input smell: `Long Method` with complexity `18`

```json
{
  "refactoring": "Extract Method",
  "predicted_complexity_after": 11.7,
  "coupling_change": -2.0,
  "cohesion_change": 3.0,
  "maintainability_improvement": 0.25,
  "risk_score": 0.2
}
```

The prediction rules table (`DEFAULT_PREDICTION_RULES`) covers 15 refactoring techniques and can be customized by passing a `rules` dict to `ImpactPredictor`.

## Extensibility

The modular design makes it easy to extend the agent:

- **Add new smell types** — extend the catalog in `RefactoringKnowledgeBase`
- **Custom scoring** — subclass `DecisionEngine` with your own strategy
- **Custom impact rules** — pass a custom rules table to `ImpactPredictor`
- **New preconditions** — subclass `ProblemInterpreter` and override `_evaluate_precondition()`
- **Custom dependencies** — pass a custom dependency graph to `RefactoringKnowledgeBase`
- **Alternative sequencing** — subclass `DependencyAnalyzer` with a different algorithm

## License

MIT