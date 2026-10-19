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
- Optional local code-model candidate layer: `sctva/model_assistance/qwen.py`

## Optional Local Code-Model Candidate Layer

SCTVA accepts the existing RDP JSON plan unchanged. The planner adapter first
normalizes that plan and SCTVA resolves the target against the current source.
In `primary` mode the configured local model proposes the first candidate; in
`fallback` mode it
is attempted only when the native transformer produces no change. Every model
proposal is still subject to SCTVA's syntax, structural, behavioral, invariant,
and rollback checks.

The local-model adapter uses only `Qwen/Qwen2.5-Coder-0.5B-Instruct`, an
instruction-tuned checkpoint for source-transformation requests. When a
complete-file prompt exceeds the configured input limit, SCTVA resolves the
current Python AST or Java/C symbol model and
sends only the exact target function, method, or class plus compact declaration
context. Model output is merged back into that exact source span; unrelated
source is never regenerated. Ambiguous targets and target spans that still do
not fit are skipped safely so the rule-based transformer can run.

For Java, the model-first adapter preserves every refactoring name in the
comprehensive RDP vocabulary as an executable semantic action: Extract Method,
Extract Class, Extract Subclass, Replace Conditional with Polymorphism,
Introduce Facade, Replace Temp with Query, Introduce Parameter Object, Move
Method, Pull Up Method, Replace Data Value with Object, Hide Delegate,
Introduce Constant, Inline Class, Collapse Hierarchy, Remove Dead Code, Replace
Parameter with Method Call, and Rename Method. These actions are not reported
as rule support merely because the model can attempt them.

The deterministic Java fallback currently implements Extract Method, Extract
Class, Introduce Parameter Object, Introduce/Extract Constant, Remove Dead Code,
Rename Method, and Hide Delegate when the plan supplies an unambiguous delegate
field/member. The remaining Java actions are model-first and are accepted only
after their action contract and all SCTVA validators pass. Cross-file class
moves also require both participating declarations to be available to the
resolved candidate span; otherwise SCTVA rejects the candidate instead of
claiming a partial refactoring succeeded.

The SCTVA web client enables the local model in `primary` mode by default. Bare library
callers can still omit or disable it explicitly. For a local CPU run, install
the model dependencies into the same Python environment that starts SCTVA:

```bash
pip install -r requirements-qwen.txt
```

CUDA deployments can replace the default CPU-compatible PyTorch package with
the matching wheel from the official PyTorch package index.

Enable it per request; the RDP plan format is not changed:

```json
{
  "execution_options": {
    "enable_qwen": true,
    "qwen_model_id": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
    "qwen_device": "cpu",
    "qwen_backend": "auto",
    "qwen_openvino_model_path": "",
    "qwen_max_input_tokens": 512,
    "qwen_max_new_tokens": 256,
    "qwen_max_bundle_output_tokens": 192,
    "qwen_cpu_threads": 6,
    "qwen_max_actions_per_request": 100,
    "qwen_max_actions_per_file": 100,
    "qwen_enable_file_bundling": false,
    "qwen_enable_generic_bundling": false,
    "qwen_max_actions_per_bundle": 5,
    "qwen_max_generic_actions_per_bundle": 2,
    "qwen_generation_timeout_seconds": 30,
    "qwen_max_generation_seconds_per_request": 0,
    "qwen_runtime_cache": true,
    "qwen_allow_unsupported_actions": false,
    "max_parallel_files": 3,
    "qwen_mode": "primary"
  }
}
```

Set `qwen_allow_unsupported_actions` to `true` only for research experiments
with RDP actions that currently normalize to `noop`. Such generic candidates
receive generic safety validation, but do not claim a method-specific
structural proof. The model never changes files directly and SCTVA rejects its
candidate on any failed validation.

`qwen_mode` defaults to `primary`: the configured model is attempted first and the
existing rule-based transformer is used when the model cannot provide a
syntax-safe candidate. Set it to `fallback` to preserve the earlier behavior
where deterministic transformations are attempted first.

In primary mode SCTVA resolves the current RDP file, symbol, and line metadata
before inference. It generates one model candidate for one RDP action at a
time, using that action's exact target span. This avoids mixing independent
refactoring contracts into one prompt, which reduced local-model candidate
acceptance. Legacy bundle options remain in the request format for backwards
compatibility but are ignored by the SCTVA model-generation engine. A bounded
second candidate is permitted only for validator-guided repair of the same
single action. Syntax, structural, behavioral, and invariant failures
immediately use the deterministic rule transformer.

Every bundle still receives syntax and structural checks, and the complete
file still has to pass SCTVA syntax, structural, behavioral, invariant, and
compiler checks. On CPU, SCTVA runs one model inference at a time but uses up
to three file workers: while Qwen generates one file, other workers may safely
perform planning, candidate validation, and reporting for other files. This
avoids competing model instances while reducing end-to-end project latency.
Set `max_parallel_files` to `1`, `2`, or `3`; model-assisted CPU requests are
capped at three workers. Validators are never bypassed.

The model/tokenizer runtime is cached at process scope, so later SCTVA requests
reuse the loaded checkpoint. For faster CPU inference without weight
quantization, install the optional OpenVINO dependencies and export the same
checkpoint as FP32 IR once:

```powershell
python -m pip install -r requirements-openvino.txt
optimum-cli export openvino --model Qwen/Qwen2.5-Coder-0.5B-Instruct --task text-generation-with-past --weight-format fp32 C:\models\qwen25-coder-05b-openvino
```

Then configure `qwen_backend` as `openvino` and set
`qwen_openvino_model_path` to that directory. With `auto`, SCTVA selects
OpenVINO only when the configured path exists and otherwise retains PyTorch.
OpenVINO inference uses FP32 activation precision and a four-item CPU
micro-batch. The earlier BF16 activation and U8 KV-cache throughput profile was
removed because numerical output stability is more important than its modest
speed improvement for source transformation. This does not change the RDP
action set or bypass candidate validation.
The web client uses a 30-second per-generation timeout and no request-level
generation cutoff. Therefore a slow first file does not prevent later eligible
RDP actions from reaching the model. To reduce CPU latency without dropping
actions, `qwen_max_bundle_output_tokens` limits only multi-action bundle
output. An oversized bundle is split before generation and its independent
constant actions use the existing tensor-batched single-action path. Every
candidate still passes the same validation gates. Set a positive
`qwen_max_generation_seconds_per_request` only when an explicit whole-request
latency cutoff is more important than attempting every eligible action.

For unchanged RDP payloads in a deployed environment, configure SCTVA instead
of changing the RDP JSON:

```text
SCTVA_ENABLE_QWEN=true
SCTVA_QWEN_MODE=primary
SCTVA_QWEN_DEVICE=cpu
SCTVA_QWEN_BACKEND=auto
SCTVA_QWEN_OPENVINO_MODEL_PATH=C:\models\qwen25-coder-05b-openvino
SCTVA_QWEN_MODEL_ID=Qwen/Qwen2.5-Coder-0.5B-Instruct
SCTVA_QWEN_CPU_THREADS=6
SCTVA_QWEN_MAX_ACTIONS_PER_REQUEST=100
SCTVA_QWEN_MAX_ACTIONS_PER_FILE=100
SCTVA_QWEN_ENABLE_FILE_BUNDLING=false
SCTVA_QWEN_ENABLE_GENERIC_BUNDLING=false
SCTVA_QWEN_MAX_ACTIONS_PER_BUNDLE=5
SCTVA_QWEN_MAX_GENERIC_ACTIONS_PER_BUNDLE=2
SCTVA_QWEN_MAX_BUNDLE_OUTPUT_TOKENS=192
SCTVA_QWEN_GENERATION_TIMEOUT_SECONDS=30
SCTVA_QWEN_MAX_GENERATION_SECONDS_PER_REQUEST=0
SCTVA_QWEN_RUNTIME_CACHE=true
```

`SCTVA_QWEN_MAX_INPUT_TOKENS`, `SCTVA_QWEN_MAX_NEW_TOKENS`, and
`SCTVA_QWEN_ALLOW_UNSUPPORTED_ACTIONS` are also available. Both action budgets
count generation attempts, including bounded repairs; one successful bundle
therefore consumes one attempt even when it applies several planned actions.
The request budget caps the complete run, while the per-file budget caps attempts for one source file.
Budget-skipped actions immediately use the deterministic transformer and are
not mislabeled as rejected model outputs.
Request-level
`execution_options` take precedence over these environment values.

The adapter uses local model files only by default, so a deployed service never
waits on Hugging Face network access. Download the checkpoint during setup. Set
`SCTVA_QWEN_ALLOW_DOWNLOAD=true` only when runtime downloads are explicitly
acceptable.

Hugging Face stores a local setup download in the user's cache (normally
`~/.cache/huggingface`), outside this repository. Do not copy checkpoints into
Git or commit model weight files. The production Docker build downloads the
checkpoint into the container image instead, so deployment does not depend on
the developer laptop cache.

The production SCTVA Dockerfile installs CPU PyTorch and bundles the
`Qwen/Qwen2.5-Coder-0.5B-Instruct` checkpoint during image construction. It enables the
model in primary mode while retaining every existing validator and the
rule-based fallback. The response fields `model_enabled`, `model_attempted`,
`model_used`, `model_attempt_reasons`, and `model_status` distinguish a disabled
model from a rejected or retained model candidate.

## Supported Languages

- `python`
- `java`
- `c` (including C-compatible `.h` targets)

For C, model candidates are scoped to a resolved function, declaration,
preprocessor directive, or statement. Exact numeric array extents in local
declarations, function parameters, struct fields, and header prototypes are
eligible for Introduce Constant. Exact numeric bit-field widths are also
eligible when the substitution is reversible. Runtime array indexes, enum
initializers, strings/comments, and ambiguous targets remain protected by the
C safety preflight. Any other language is rejected with a clear contract
validation error.

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
3. Browse for a local `.java` or `.py` file from your computer and load it into the source editor.
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
- Refactorings outside the recognized language vocabularies are mapped to
  auditable `noop` actions; recognized Java model actions retain their semantic
  action type even when no deterministic fallback exists.

## Next Upgrades

1. Add Java runtime test harness adapters (Maven/Gradle/JUnit).
2. Expand action coverage beyond core four actions.
3. Add sandboxed subprocess isolation for Python behavior execution.
4. Add persistence for execution traces and historical confidence analytics.
