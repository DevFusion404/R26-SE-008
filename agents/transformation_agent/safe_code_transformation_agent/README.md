# Safe Code Transformation and Validation Agent (SCTVA)

SCTVA is a safety-first execution agent that consumes source code plus a structured refactoring plan and returns transformed code, multi-level validation outputs, rollback decisions, confidence scoring, and a detailed safety report.

## Architecture

- Core orchestrator: `sctva/agent.py`
- Contracts and schemas: `sctva/contracts.py`
- Transformers: `sctva/transformers/*`
- Validators: `sctva/validators/*`
- Rollback: `sctva/rollback/rollback_manager.py`
- Confidence scoring: `sctva/scoring/confidence_scorer.py`
- Reporting: `sctva/reporting/safety_reporter.py`
- Integration: `sctva/integration/planner_adapter.py`, `sctva/integration/api.py`

## Supported Languages

- `python`
- `java`

Any other language is rejected with a clear contract validation error.

## Validation Order

SCTVA validates in this exact order:

1. Syntax validation
2. Structural validation
3. Behavioral validation

If any stage fails, rollback is automatically triggered.

## Action Support

Implemented actions:

- `rename_symbol`
- `extract_constant`
- `replace_literal`
- `inject_syntax_error` (negative testing)
- `noop` (adapter-safe placeholder)

Python transformations are AST-based (`ast.NodeTransformer`).
Java transformations are conservative text-based with syntax and structural checks.

## Integration with RDP Agent

Use `PlannerAdapter` to map RDP plan output into SCTVA request format while preserving planner metadata and correlation IDs.

Direct function-call integration:

```python
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter

agent = SafeCodeTransformationValidationAgent()
adapter = PlannerAdapter()

sctva_request = adapter.build_request_from_rdp(
    request_id="req_001",
    language="java",
    source_code=java_source,
    planner_output=rdp_plan,
    correlation_id="corr_123",
)

result = agent.execute(sctva_request)
```

REST integration endpoints:

- `POST /sctva/execute`
- `POST /sctva/execute_from_rdp` (alias for manual payload; RDP adapter flow is commented out)
- `GET /sctva/health`

Status code behavior:

- `200`: execution completed (including rollback outcomes)
- `400`: invalid input payload
- `422`: unsupported planner shape/mapping
- `500`: internal execution error

## Demo

```bash
python run_demo.py
```

Demo results are written to `test_data/results/*.result.json`.

## Run API Server

```bash
python app.py
```

Defaults to port `8002`. Set `SCTVA_PORT` to override, and `SCTVA_ALLOW_ORIGIN` to control CORS.

The same server also serves the UI at `http://localhost:8002/`.

## Run UI

1. Start the SCTVA server with `python app.py` from this folder.
2. Open `http://localhost:8002/` in your browser.
3. Attach a `.java` or `.py` file, or click `Use Sample File` to load the bundled `OrderProcessor.java` sample.
4. Edit the refactoring plan if needed, then click `Run Transformation`.

The UI shows the original code on the left, the transformed code on the right, and the change log / safety report below.

## Manual Payload Example

Use a full SCTVA request payload (see `sctva/integration/planner_payload_example.json`).

```bash
curl -X POST http://localhost:8002/sctva/execute \
    -H "Content-Type: application/json" \
    -d @sctva/integration/planner_payload_example.json
```

## Tests

```bash
pytest tests -q
```

## Example Payloads

- Planner adapter input: `test_data/planner_payloads.json`
- General request example: `sctva/integration/planner_payload_example.json`

## Limitations

- Java behavioral validation uses conservative mock checks unless external test harness is integrated.
- Java compile checks depend on local `javac` availability.
- Adapter maps unsupported RDP refactorings to `noop` actions to keep execution auditable and non-destructive.

## Next Upgrades

1. Add Java runtime test harness adapters (Maven/Gradle/JUnit).
2. Expand action coverage beyond core four actions.
3. Add sandboxed subprocess isolation for Python behavior execution.
4. Add persistence for execution traces and historical confidence analytics.
