# Inter-Agent Data Contracts

R26-SE-008 | Bandara S M Y M | IT22277886

Every hand-off between the four agents is a JSON document with a defined shape.
This folder is the formal statement of those shapes.

> Each specialized agent communicates through a formally defined data contract
> rather than through arbitrary unstructured JSON.

## The chain

```
CUQA Agent  (:8080)
     │  CUQAReport                     cuqa-report.schema.json
     ▼
Orchestration Agent  (:5001)
     │
     │  ── developer accepts / rejects code smells ──
     │
     │  FilteredCUQAReport             filtered-cuqa-report.schema.json
     ▼
RDP Agent  (:5000)
     │  RefactoringPlan                rdp-plan.schema.json
     ▼
Orchestration Agent
     │
     │  ── developer accepts / rejects plan steps ──
     │
     │  ApprovedPlan                   approved-plan.schema.json
     ▼
     │  SCTVARequest                   sctva-request.schema.json
     ▼
SCTVA Agent  (:8002)
     │  TransformationResult           sctva-result.schema.json
     ▼
Orchestration Agent
     │  Workflow                       workflow.schema.json
     ▼
DIWO Frontend
```

The frontend never appears on either side of an agent hand-off: it talks only
to the Orchestration Agent, which owns all three integrations.

## The two contracts that carry the human decision

`FilteredCUQAReport` and `ApprovedPlan` are the reason this project is
*developer-in-the-loop* rather than automated, and each is a subset of the
document before it:

| Before | Decision | After | Guarantee |
|---|---|---|---|
| `CUQAReport` | accept / reject smells | `FilteredCUQAReport` | RDP plans only for smells the developer kept |
| `RefactoringPlan` | accept / reject steps | `ApprovedPlan` | SCTVA executes only steps the developer approved |

Both keep the shape of the document they narrow, so nothing downstream needs a
second parser — only the lists get shorter. Both also record what was excluded
(`summary.excluded_count`, `approval.rejected_step_ids`) rather than dropping
it silently, which is what lets the audit trail agree with what actually ran.

A third guarantee closes the loop at the end: a transformation the developer
**rejects** is written back as its original source, in the archive, in the git
commit and in `workflow.transformation_result.file_decisions`.

## Provenance

These schemas were built **from payloads the running system actually exchanges**
— captured from a live session against the CUQA, RDP and SCTVA agents on the
`C_Backend_CodeSmells_Project` workspace — not from how the JSON ideally ought
to look. Where reality and tidiness disagreed, reality won:

* `codeSmell.line` is nullable, because CUQA attributes some smells to a file
  rather than a line.
* `RefactoringPlan.summary` is an object here although RDP sends a string; the
  Orchestration Agent converts it, and the original text is kept as
  `summary_text`.
* `planStep.smell_id` is documented as NOT always matching the CUQA smell id,
  because RDP re-keys smells internally.
* Objects that agents extend per language (`fileReport.metrics`,
  `planStep.parameters`) are `additionalProperties: true` on purpose.

## Validating

```bash
pip install jsonschema referencing
python shared/contracts/validate_contracts.py <samples_dir>
```

`<samples_dir>` holds one JSON file per contract (`cuqa_report.json`,
`filtered_cuqa_report.json`, `rdp_plan.json`, `approved_plan.json`,
`sctva_request.json`, `sctva_result.json`, `workflow.json`). Capture a fresh
set by driving a workflow through the orchestration backend and saving the
`updated_report`, `plan`, `approved_plan`, and the `request`/`result` from
`POST /api/workflows/<id>/transform`.

The schemas are documentation and a test aid; nothing in the running system
validates against them at request time, so adding one cannot break a workflow.
