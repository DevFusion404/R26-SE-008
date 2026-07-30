# CUQA Agent — Code Understanding & Quality Assessment

**First stage of the Agentic Intelligent Code Refactoring Assistant pipeline.**

The CUQA Agent ingests a source-code repository (via ZIP upload or GitHub URL),
builds language-aware ASTs, detects code smells, computes quality metrics, and
emits structured JSON consumed by the downstream **RDP Agent**.

---

## Supported Languages

| Language | Extensions | Parser Backend |
|----------|------------|----------------|
| Python   | `.py`      | Built-in `ast` module |
| Java     | `.java`    | `javalang` library |
| **C**    | **`.c` `.h`** | **tree-sitter (optional) → regex fallback** |

---

## Supported File Extensions

```
.py    — Python source files
.java  — Java source files
.c     — C source files
.h     — C header files
```

---

## Quick Start

```bash
# Install required dependencies
pip install -r requirements.txt

# Start the CUQA FastAPI server
cd agents/cuqa_agent/src
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

The API is then available at `http://localhost:8080`.

---

## C Language Analysis Features

### Metrics computed for every `.c` / `.h` file

| Metric | Description |
|--------|-------------|
| `lines_of_code` | Total source lines |
| `blank_lines` | Lines containing only whitespace |
| `comment_lines` | `//` and `/* */` comment lines |
| `functions` | Number of function definitions |
| `classes` | Always `0` (C has no classes) |
| `include_count` | Number of `#include` directives *(C-specific)* |
| `global_variables` | Global variable declarations at file scope *(C-specific)* |
| `estimated_cyclomatic_complexity` | Decision-point count + 1 *(C-specific)* |

### C code smell rules

| Rule | Trigger | Severity |
|------|---------|----------|
| `LongFunction` | Function body > 40 lines | **high** |
| `TooManyParameters` | Function has > 5 parameters | **medium** |
| `DeepNesting` | Brace-nesting depth > 4 | **high** |
| `MagicNumber` | Numeric literal not in `{0, 1, -1, 2}` | **low** |
| `UnsafeFunctionUsage` | Calls to `gets`, `strcpy`, `strcat`, `sprintf`, `scanf` | **high** |
| `GlobalVariable` | Variable declared at file scope (outside any function) | **medium** |
| `LargeHeaderFile` | `.h` file has > 300 lines | **medium** |

---

## Output Schema

Every file report emitted to the RDP Agent follows this structure:

```json
{
  "file": "example.c",
  "language": "c",
  "metrics": {
    "filename": "example.c",
    "lines_of_code": 120,
    "blank_lines": 10,
    "comment_lines": 15,
    "functions": 4,
    "classes": 0,
    "include_count": 3,
    "global_variables": 1,
    "estimated_cyclomatic_complexity": 8
  },
  "code_smells": [
    {
      "type": "UnsafeFunctionUsage",
      "message": "Unsafe function 'strcpy()' detected — prefer a safe alternative",
      "line": 42,
      "severity": "high",
      "entity": "strcpy"
    }
  ],
  "smell_summary": {
    "high": 1,
    "medium": 0,
    "low": 0
  },
  "quality_score": 92.0
}
```

> **RDP compatibility:** The `include_count`, `global_variables`, and
> `estimated_cyclomatic_complexity` fields are **optional** additions inside
> `metrics`. The RDP Agent reads the schema permissively and ignores unknown
> fields — no downstream changes required.

---

## Example API Usage

### Upload a ZIP containing C code

```bash
curl -X POST http://localhost:8080/api/upload-zip \
     -F "file=@my_c_project.zip"
```

### Fetch the quality report for a specific C file

```bash
curl -X POST http://localhost:8080/api/quality-report \
     -H "Content-Type: application/json" \
     -d '{"file_path": "src/main.c"}'
```

### Fetch the full repository report

```bash
curl -X POST http://localhost:8080/api/quality-report \
     -H "Content-Type: application/json" \
     -d '{}'
```

### Parse the AST for a C file

```bash
curl -X POST http://localhost:8080/api/parse-ast \
     -H "Content-Type: application/json" \
     -d '{"file_path": "src/utils.c"}'
```

---

## C Parser — tree-sitter vs Regex Fallback

The C parser (`c_ast_parser.py`) uses a **two-tier strategy**:

### Tier 1 — tree-sitter (preferred)

Produces an accurate, token-level AST. Install **one** of:

```bash
# Option A: language pack (recommended — simplest install)
pip install tree-sitter-language-pack

# Option B: individual packages
pip install tree-sitter tree-sitter-c
```

When tree-sitter is available, the parsed result includes `"parser": "tree-sitter"`.

### Tier 2 — Regex fallback (always available)

If tree-sitter is absent or fails, the parser falls back to regex-based
heuristics automatically. All metrics and smell rules still work; AST node
detail is reduced. The result includes `"parser": "regex-fallback"`.

**No crash occurs regardless of which path is taken.**

---

## Running Tests

```bash
cd agents/cuqa_agent
python -m pytest tests/test_c_support.py -v
```

### What the test suite covers

- `detect_language("file.c")` → `"c"`, `detect_language("file.h")` → `"c"`
- `.c` and `.h` files discovered during ZIP/GitHub scanning
- `parse_source()` returns correct schema for C
- `generate_file_report()` returns `language = "c"`
- All 7 smell rules trigger on `smelly.c`
- `LargeHeaderFile` triggers on synthetic > 300-line header
- Existing Python and Java tests still pass
- Repository-level report contains `summary` + `files` keys

### Sample files

| File | Purpose |
|------|---------|
| `tests/sample_c_files/simple.c` | Clean baseline — two small functions |
| `tests/sample_c_files/smelly.c` | Triggers all 7 C smell rules |
| `tests/sample_c_files/utils.h` | Normal-sized header file |

---

## Known Limitations of C Analysis

| Limitation | Detail |
|------------|--------|
| No preprocessor evaluation | `#define` macros are not expanded before analysis |
| Regex function detection | Excludes complex cases (function pointers, K&R style, deeply nested declarations) |
| Cyclomatic complexity | Estimated from decision-point count on raw source; not per-function |
| Global variable detection | Heuristic — may miss pointer-to-function typedefs or miss some extern declarations |
| Header include chains | CUQA analyses each file independently; cross-file type information is not resolved |
| No tree-sitter required | If tree-sitter is absent the fallback runs, but AST depth/accuracy is reduced |

---

## Project Structure

```
agents/cuqa_agent/
├── src/
│   ├── main.py               # FastAPI server & workspace management
│   ├── ast_parser.py         # Language dispatcher (.py/.java/.c/.h)
│   ├── python_ast_parser.py  # Python AST via built-in ast module
│   ├── java_ast_parser.py    # Java AST via javalang
│   ├── c_ast_parser.py       # C AST via tree-sitter / regex fallback  ← NEW
│   ├── report_generator.py   # Metrics + smell detection for all languages
│   └── ast_visualizer.py     # AST enrichment & summary utilities
├── tests/
│   ├── test_c_support.py     # C language unit tests                   ← NEW
│   └── sample_c_files/
│       ├── simple.c          # Clean C baseline                        ← NEW
│       ├── smelly.c          # All-smells trigger file                 ← NEW
│       └── utils.h           # Header file sample                      ← NEW
└── README.md
```

---

## Pipeline Integration

```
ZIP / GitHub URL
      │
      ▼
 CUQA Agent  ──── JSON report ────►  RDP Agent
  (port 8001)                         (port 8002)
```

The CUQA output schema is **backward-compatible** with the RDP Agent.
C-specific fields (`include_count`, `global_variables`,
`estimated_cyclomatic_complexity`) are **optional** additions — the RDP Agent
ignores any fields it does not recognise.
