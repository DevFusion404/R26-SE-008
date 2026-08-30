# CUQA Evaluation Architecture

## 1. Overview

This document describes the architectural integration of the research-grade **CUQA Code Smell Detection Evaluation Framework**. The evaluation framework operates strictly external to CUQA's production detection engine, ensuring zero modification to production detectors, threshold parameters, or scoring logic.

```
+-----------------------------------------------------------------------+
|                           CUQA Production Engine                      |
|  [Python Parser]      [Java Parser (javalang)]    [C Parser (tree-sitter)]|
|        |                         |                         |          |
|  _PythonSmellVisitor    _analyze_java_smells     _analyze_c_smells    |
|        \                         |                         /          |
|         +------------------------+------------------------+           |
|                                  |                                    |
|                       report_generator.py                             |
|              (generate_file_report / generate_repo_report)            |
+----------------------------------+------------------------------------+
                                   |
                                   | CUQA Output JSON Report
                                   v
+----------------------------------+------------------------------------+
|                    CUQA Evaluation Framework                          |
|                                                                       |
|  1. ground_truth_loader.py  -->  Loads & validates CSV Ground Truth    |
|  2. cuqa_runner.py          -->  Runs CUQA in non-modifying mode     |
|  3. prediction_normalizer.py-->  Normalizes predictions to entity unit|
|  4. matcher.py              -->  Matches predictions vs Ground Truth  |
|  5. metrics.py              -->  Computes TP/FP/FN/TN, P/R/F1, MCC   |
|  6. agreement.py            -->  Computes Cohen's Kappa Inter-Rater   |
|  7. bootstrap.py            -->  Computes 95% Bootstrap CIs           |
|  8. reports.py              -->  Exports JSON/CSV artifacts & Markdown|
+-----------------------------------------------------------------------+
```

---

## 2. CUQA Core Component Inventory

### 2.1 File Structure & Architecture
- **Location**: `agents/cuqa_agent/src/`
- **Main Entry Points**:
  - `main.py`: FastAPI server exposing endpoints `/api/quality-report`, `/api/parse-ast`, `/api/project-structure`.
  - `report_generator.py`: Primary detection engine containing Python AST visitors, Java parsers, C regex/AST heuristics, category enrichment (`SMELL_CATEGORY_MAP`), and quality scoring (`_compute_score`).
  - `ast_parser.py`, `python_ast_parser.py`, `java_ast_parser.py`, `c_ast_parser.py`: Language AST builders and visualizers.
  - `repository_understanding.py`: Structural orientation, entry point detection, static dependency graph construction.

### 2.2 Supported Languages & Detection Parsers
1. **Python (`.py`)**:
   - **Parser**: Standard library `ast` (`pyast`).
   - **Visitor Class**: `_PythonSmellVisitor`.
   - **Detectors**: `LongMethod`, `TooManyParameters`, `SwitchStatements`, `MessageChains`, `UnreachableCode`, `UnusedVariable`, `LargeClass`, `LazyClass`, `PrimitiveObsession`, `InappropriateIntimacy`, `SpeculativeGenerality`, `MagicNumber`, `BareExcept`, `DeadCode`, `DuplicateCode`, `RefusedBequest`, `TemporaryField`, `FeatureEnvy`.
2. **Java (`.java`)**:
   - **Parser**: `javalang` AST parser.
   - **Engine**: `_analyze_java_smells`.
   - **Detectors**: `LongMethod`, `TooManyParameters`, `LargeClass`, `MagicNumber`, `Comments`.
3. **C (`.c`, `.h`)**:
   - **Parser**: Tree-sitter / regex AST helpers (`c_ast_parser.py`).
   - **Engine**: `_analyze_c_smells`.
   - **Detectors**: `LongFunction`, `TooManyParameters`, `DeepNesting`, `DeadCode`, `DuplicateCode`, `MagicNumber`, `UnsafeFunctionUsage`, `GlobalVariable`, `LargeHeaderFile`.

---

## 3. Representation in CUQA Output JSON

CUQA emits a JSON structure with top-level `summary` and `files` array:

```json
{
  "summary": {
    "files_analyzed": 10,
    "total_lines_of_code": 1250,
    "total_code_smells": 14,
    "smell_severity": { "high": 4, "medium": 7, "low": 3 },
    "average_quality_score": 84.2,
    "code_smell_overview": { ... }
  },
  "files": [
    {
      "file": "src/utils.py",
      "language": "python",
      "metrics": { "lines_of_code": 120, "functions": 4, "classes": 1 },
      "code_smells": [
        {
          "type": "LongMethod",
          "message": "Function 'process_data' has 45 lines of code (>30)",
          "line": 24,
          "severity": "high",
          "entity": "process_data",
          "start_line": 24,
          "end_line": 69,
          "parameter_count": 3,
          "cyclomatic_complexity": 7,
          "category": "Bloaters",
          "category_priority": "critical"
        }
      ],
      "quality_score": 88.0
    }
  ]
}
```

### Representation of Key Attributes:
- **File Path**: Relative path in `file` (e.g. `src/utils.py`).
- **Language**: Standardized string in `language` (`python`, `java`, `c`).
- **Smell Type**: String in `type` (e.g., `LongMethod`, `UnsafeFunctionUsage`).
- **Line Number**: Line number in `line` (or `start_line`/`end_line` range when available).
- **Entity Name**: Function, method, class, or variable name in `entity`.
- **Severity**: String in `severity` (`high`, `medium`, `low`).

---

## 4. Entity Level & Matching Strategy Mapping

To avoid treating every smell as a naive file-level binary classification, the evaluation matcher maps predictions to ground-truth records by entity granularity:

| Smell Type | Entity Type | Primary Match Keys | Line Tolerance |
|---|---|---|---|
| `LongMethod` / `LongFunction` | `function` / `method` | `(file_path, smell_type, entity_name)` | ±5 lines |
| `TooManyParameters` | `function` / `method` | `(file_path, smell_type, entity_name)` | ±5 lines |
| `LargeClass` / `LazyClass` | `class` | `(file_path, smell_type, entity_name)` | ±10 lines |
| `DeepNesting` | `function` | `(file_path, smell_type, entity_name)` | ±5 lines |
| `SwitchStatements` | `function` | `(file_path, smell_type, entity_name)` | ±5 lines |
| `UnreachableCode` | `function` | `(file_path, smell_type, entity_name)` | Exact line / range overlap |
| `UnusedVariable` | `function` | `(file_path, smell_type, entity_name, variable_name)` | Exact line |
| `GlobalVariable` | `declaration` | `(file_path, smell_type, entity_name)` | Exact line |
| `UnsafeFunctionUsage` | `function_call` | `(file_path, smell_type, entity_name)` | Exact line |
| `LargeHeaderFile` | `file` | `(file_path, smell_type)` | File-level |
| `Comments` | `file` | `(file_path, smell_type)` | File-level |

---

## 5. Non-Modifying Integration Guarantee

1. **Read-Only Inspection**: `evaluation/cuqa_runner.py` imports `generate_file_report` and `generate_repo_report` from `agents.cuqa_agent.src.report_generator` directly as python functions or executes standard CLI/API invocations.
2. **Zero Modification to Production Rules**: No detector constants, thresholds, or code visitor logic inside `report_generator.py` are altered.
3. **Separate Test Suite**: Evaluation unit tests live exclusively in `evaluation/tests/test_evaluator.py`, keeping CUQA detector unit tests in `agents/cuqa_agent/tests/` untouched.
