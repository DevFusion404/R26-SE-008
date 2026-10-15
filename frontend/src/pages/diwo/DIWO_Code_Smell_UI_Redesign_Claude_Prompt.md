# Claude Implementation Prompt — DIWO Code Smell Approval & Source View UI Redesign

## Role

Act as a senior frontend engineer working on my research project **RefactorIQ — DIWO Agent (Developer Interaction & Workflow Orchestration)**.

Your task is to **redesign the existing Code Smell Approval / Review UI and its source-code View page** using the attached/reference Code Smell Approval design as the visual and information-architecture target.

This is primarily a **UI/UX refactor**. Preserve the existing DIWO business logic, API contracts, workflow state, selection semantics, and backend integration.

---

# 1. Project Context

DIWO is the developer-facing orchestration agent in an agentic code-refactoring workflow:

```text
CUQA
  ↓
DIWO — Code Smell Review / Developer Approval
  ↓
RDP — Refactoring Decision & Planning
  ↓
DIWO — Plan Approval
  ↓
SCTVA — Safe Code Transformation & Validation
  ↓
DIWO — Result Approval / Comparison / Audit
```

For this task, focus only on the first developer gate:

```text
CUQA detected smells
        ↓
DIWO Code Smell Review
        ↓
Developer selects/approves findings
        ↓
Only approved findings are forwarded to RDP
```

The existing page is already functional. **Do not replace working DIWO logic with the mock/static data from the UI reference.**

---

# 2. Existing Files to Inspect First

Before changing code, inspect these files and understand how they work together:

```text
diwo/stages/CodeSmellApprovalPage.jsx
diwo/components/CodeSmellToolbar.jsx
diwo/components/SmellReviewViews.jsx
diwo/components/SelectionSummaryPanel.jsx
diwo/components/SmellCategoryOverview.jsx
diwo/components/QuickSelectDropdown.jsx
diwo/components/ImpactDrawer.jsx
diwo/components/TradeOffPanel.jsx
diwo/components/SourceViewer.jsx
diwo/components/StickyActionBar.jsx
diwo/diwoTheme.jsx
diwo/services/diwoApi.js
diwo/utils/smellGrouping.js
diwo/utils/impactSummary.js
diwo/utils/smellLines.js
diwo/DIWOAgentPage.jsx
```

Also inspect the backend/API usage only to understand the existing contracts. Do **not** redesign backend behavior for this UI task.

---

# 3. Non-Negotiable Functional Rules

## 3.1 Do not break the existing workflow

Keep these behaviors exactly intact:

- CUQA report can come from:
  - the current DIWO workflow,
  - live CUQA through the orchestration backend,
  - sample data only when explicitly selected after a failure.
- The browser must continue calling the **DIWO orchestration backend**, not CUQA/RDP/SCTVA directly.
- Existing loading, stale workspace, retry, sample-data, and error states must remain functional.
- Existing selection must still be sent through the current DIWO workflow.

## 3.2 Preserve all three review modes

The existing DIWO review has:

```text
File wise
Smell wise
Category wise
```

Keep all three.

### File wise

The selection unit is the **whole file**.

Do not add a per-smell checkbox in File-wise mode because the current backend contract treats this mode as file selection.

### Smell wise

The selection unit is the **individual finding**.

Multiple occurrences of the same smell type can still be grouped repository-wide.

### Category wise

This is a frontend grouping of individual smells.

Do **not** introduce a new backend `selection_mode: "category"`.

Continue sending Category-wise selections using the current smell-level contract.

## 3.3 Preserve selection state

Switching between:

```text
Smell wise ↔ Category wise
```

must not lose selected smell IDs.

Filters must only affect what is visible.

A smell hidden by search/severity/category filters must remain selected until the user explicitly deselects it.

## 3.4 Preserve existing smell IDs

Do not change the smell ID mapping:

```text
<relative_path>:<line>:<index-within-file>
```

The frontend ID must continue matching the backend CUQA-normalized smell ID.

## 3.5 Preserve RDP handoff

The existing flow must remain:

```text
Developer selection
    ↓
POST /workflows/<id>/smell-selection-pass
    ↓
filtered report preview / workflow persistence
    ↓
parent onProceed(...)
    ↓
POST /workflows/<id>/select-smells
    ↓
RDP
```

Do not bypass or merge these workflow steps unless an actual bug is found.

## 3.6 Preserve existing impact intelligence

The UI already uses backend-derived information from:

```text
GET  /workflows/<id>/smell-impacts
POST /workflows/<id>/selection-impact
POST /workflows/<id>/optimise-selection
GET  /workflows/<id>/smell-categories
```

Keep these integrations.

Capability must continue coming from the impact/capability model, **not from severity**.

Do not fabricate:

- technical-debt hours,
- quality gain,
- SCTVA capability,
- risk,
- developer effort,
- category,
- impact,
- “recommended” state

when the backend did not supply enough evidence.

If a value is unavailable, hide it or show an honest neutral unavailable state.

---

# 4. Main UI Goal

Redesign the Code Smell Review workspace to follow the visual hierarchy of the supplied Code Smell Approval reference:

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Existing DIWO shell / Step Bar / Phase Title                       │
├─────────────────────────────────────────────────────────────────────┤
│ Repository + CUQA analysis context / key metrics                   │
├───────────────┬─────────────────────────────────┬───────────────────┤
│ Left rail     │ Main findings workspace         │ Selection impact  │
│               │                                 │ / approval        │
│ Summary       │ Search / sort / view mode       │                   │
│ Filters       │                                 │ Selected count    │
│ Severity      │ Group headings                  │ Impact summary    │
│ Category      │ Smell cards / rows              │ RDP handoff       │
│               │                                 │                   │
└───────────────┴─────────────────────────────────┴───────────────────┘
```

The goal is a **clean professional developer tool**, not a marketing dashboard.

The UI should make these questions easy to answer:

1. What did CUQA detect?
2. Where is it?
3. How severe is it?
4. What category does it belong to?
5. What does selecting it mean?
6. Can SCTVA transform it automatically, manually, or not at all?
7. What is the risk / expected impact?
8. What exactly am I sending to RDP?
9. What happens if I leave it out?
10. Can I inspect the original source before deciding?

---

# 5. Important Layout Constraint

`CodeSmellApprovalPage.jsx` is rendered inside `DIWOAgentPage.jsx`.

The parent already displays:

- DIWO header
- workflow StepBar
- current phase title
- current phase description

Therefore:

**Do not add another giant “Code Smell Approval” page hero that duplicates the parent.**

Instead, make the Code Smell Approval component start with a compact **analysis context / repository summary section**.

---

# 6. Code Smell Approval Page — Required UI Changes

## 6.1 Analysis context strip

Create a compact top section that shows real data already available from the workflow/report.

Show relevant values such as:

```text
Repository
Report source
Languages
Files analyzed
Detected smells
High severity count
Average quality score
Category count
CUQA analysis status
```

Also preserve the existing refresh/re-analyze behavior.

If the current CUQA workspace no longer matches the workflow repository, keep the existing stale-workspace warning prominent.

Do not hardcode repository names, commits, branches, languages, or timestamps from the design reference.

---

## 6.2 Three-column desktop workspace

Use a responsive three-column structure on large screens.

Suggested proportions:

```text
Left rail:   250–290px
Main:        flexible, largest area
Right rail:  320–360px
```

The findings list must receive the most horizontal space.

On medium/small screens:

- left filters may move above the findings,
- right selection panel may become normal flow below/above the list,
- nothing important may become unreachable.

Do not force the entire page into a narrow fixed desktop layout.

---

# 7. Left Rail — Summary & Filters

Create a clean left rail inspired by the reference UI.

## 7.1 Smell summary card

Show live counts:

```text
Total detected
Currently selected
Not selected
Affected files
```

Where appropriate also show:

```text
High severity
Medium severity
Auto-fixable
Advisory/manual
```

Only show impact/capability counts when impact data actually exists.

---

## 7.2 Severity filters

Use clear severity controls.

The current CUQA data uses:

```text
high
medium
low
```

Do not invent `critical` unless the real DIWO/CUQA contract actually contains it.

Each severity filter should show a count.

---

## 7.3 Category filters

Use the taxonomy returned by DIWO:

```text
GET /workflows/<id>/smell-categories
```

Show category name and count.

Keep the existing ability to move into Category-wise review.

Do not hardcode the categories from the mock reference.

---

## 7.4 Filter behavior

Keep:

- search
- severity
- category
- smell type

Support combined filtering.

Add a clear/reset action when filters are active.

Selected findings hidden by filters must remain selected.

---

# 8. Main Findings Workspace

## 8.1 Top toolbar

Use a compact toolbar similar to the reference UI.

Include:

```text
Search
Sort
Select all shown
Clear current selection
File wise / Smell wise / Category wise switch
Showing X of Y
Expand all / Collapse all
```

Do not remove useful existing quick-selection behavior.

You may reorganize `QuickSelectDropdown` so it feels visually integrated instead of floating as a separate block.

---

## 8.2 Sorting

If current data supports it safely, provide sorting such as:

```text
Severity
Estimated effort
Expected impact / quality gain
File/name
```

But:

- only sort by values that actually exist,
- if impact records are unavailable, disable or hide impact-based sort options,
- do not invent missing values.

---

# 9. Finding / Smell Card Redesign

Use the reference `SmellCard` idea as the target, but map it to actual DIWO data.

Each individual smell/finding should visually expose, without requiring the user to open a dialog:

```text
Smell type
Severity
Category
Entity / method / class if available
File path
Line / line range
CUQA message
SCTVA capability / advisory state if available
Transformation risk if available
Estimated developer effort if available
```

Keep the UI compact enough for scanning.

---

## 9.1 Selection indication

In Smell-wise and Category-wise mode:

- use a clear checkbox or tri-state group checkbox,
- selected cards/rows should have a subtle accent border/tint,
- clicking the checkbox must not accidentally open the source viewer,
- group-level selection must remain supported.

In File-wise mode:

- select the whole file,
- do not imply that an individual smell can be independently sent.

---

## 9.2 Suggested / optimized selections

If DIWO's optimizer proposes selections, visually indicate that in a truthful way, for example:

```text
DIWO optimized selection
Suggested by selection optimizer
```

Do not label something “CUQA Suggested” unless CUQA itself supplied that recommendation.

The UI reference uses a “Suggested” badge, but in this project the source of a recommendation must remain explicit.

---

# 10. Expandable Details

The reference design expands a smell into code and impact details.

For DIWO, keep the main list scan-friendly.

A finding may expose expandable details such as:

```text
Impact analysis
Expected quality contribution
Transformation risk
Capability status
Review effort
Deferral cost / cost of skipping
Blast radius / test coverage signals if already available
```

However, preserve the existing `ImpactDrawer` if it remains clearer for detailed impact.

A good implementation is:

```text
Compact inline summary
        +
“Impact details” button
        ↓
existing redesigned ImpactDrawer
```

Do not duplicate the exact same metrics in three different panels.

---

# 11. Right Rail — Selection Impact / Approval Panel

Create a sticky right-side panel inspired by the supplied reference.

This panel should answer:

> “What am I about to send to RDP?”

Show live, real selection data.

Recommended content:

```text
Selected
X findings / Y total
or
X files in File-wise mode

Affected files
High / medium severity mix
Auto-transformable count
Advisory/manual/unsupported count
Estimated effort
Expected quality capture/gain
Overall risk
```

Only show values that are supported by current impact data.

---

## 11.1 Selection progress

Show a simple selected/total progress bar.

For File-wise mode, make labels explicit so the developer does not confuse:

```text
Selected files
```

with:

```text
Selected findings
```

The current mode determines the selection unit.

---

## 11.2 Optimizer controls

Preserve the existing DIWO selection optimization / trade-off functionality.

It may live:

- inside the right rail,
- in a collapsible “Optimize selection” section,
- or immediately below the right rail.

Keep:

```text
budgetMinutes
optimiseSelection(...)
interaction notes
quality capture / trade-off information
```

Do not remove research functionality just to match the reference screenshot.

---

# 12. RDP Handoff CTA

There should be one clearly dominant action:

```text
Review & Send to RDP
```

or

```text
Send Approved Findings to RDP
```

Use wording that matches the actual action.

Requirements:

- disabled when nothing is selected,
- displays the current selection count,
- during backend work show the existing two phases:
  - filtering,
  - planning,
- prevent duplicate submission,
- do not create another endpoint.

Avoid having two equally prominent duplicate “Send to RDP” buttons on the same viewport.

If `StickyActionBar` and the new right panel duplicate the same action, consolidate them carefully while retaining mobile usability.

---

# 13. Confirmation Before RDP Handoff

Add or redesign a confirmation dialog inspired by the reference.

Before the final RDP handoff, summarize:

```text
Detected
Approved / selected
Excluded / not selected
Affected files
Estimated effort if available
Risk if available
```

Show the actual selected smells or files.

For smell selections, each row can show:

```text
severity
smell type
file:line
entity
```

The confirm action should call the existing `handleApproveSelection` path.

Do not move workflow persistence into the dialog itself.

---

# 14. “View Code” / Source Viewer Redesign

The existing `SourceViewer.jsx` is an important DIWO feature and must remain functional.

Redesign it to visually match the cleaner Code Smell Approval UI.

## 14.1 Preserve existing behavior

Keep:

- original source is fetched from DIWO backend via `fetchFileSource`,
- browser does not call SCTVA directly,
- loading state,
- 503/SCTVA unavailable state,
- missing/stale file state,
- Esc closes,
- background click closes,
- opening from a finding focuses that smell,
- opening from a file shows all smells,
- focused smell scrolls into view,
- smell line ranges are highlighted,
- all smells in the file remain visible,
- selected smell state remains available in Smell/Category modes.

---

## 14.2 New source-view layout

Use a polished split-panel modal:

```text
┌──────────────────────────────────────────────────────────────────┐
│ Original Source | file path | language | smell count      Close │
├──────────────────┬───────────────────────────────────────────────┤
│ Findings sidebar │ Code viewer                                   │
│                  │                                               │
│ smell #1         │ line no | code                                │
│ smell #2         │ line no | code                                │
│ smell #3         │ highlighted smell range                       │
│                  │                                               │
├──────────────────┴───────────────────────────────────────────────┤
│ Legend / line count / focused smell information                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## 14.3 Findings sidebar

Each smell entry should show:

```text
Severity
Smell type
Entity if available
Line / range
Selected state if applicable
```

Clicking a finding:

- focuses it,
- scrolls code to the correct range,
- does not automatically change approval selection unless the user explicitly uses the selection control.

If per-smell selection is enabled in this mode, make the selection action clearly separate from focus/navigation.

---

## 14.4 Code viewer

Keep line numbers.

Improve readability:

- monospace font,
- adequate line height,
- horizontal scrolling for long lines,
- subtle highlighted range,
- stronger focused-line/range treatment,
- gutter marker for smell anchors,
- overlapping smell coverage must remain understandable.

Do not truncate source code.

Do not syntax-color by making unsafe guesses if no syntax highlighter is already installed.

Avoid adding a large dependency solely for syntax highlighting unless necessary.

---

# 15. Visual Style

Use the **information hierarchy and spacing** of the supplied reference, but integrate it into the existing DIWO product style.

The existing DIWO theme uses:

```text
C.bg
C.panel
C.border
C.borderAcc
C.accent
C.warn
C.danger
C.info
C.text
C.textSub
C.textMuted
```

Prefer using these theme values rather than introducing an unrelated hardcoded design system.

The final UI should feel consistent with the rest of DIWO.

Use:

- subtle card borders,
- modest shadows,
- clean radius,
- strong spacing hierarchy,
- restrained accent color,
- readable typography,
- mono text for paths/code,
- color + text together for severity/risk; do not rely on color only.

Avoid:

- excessive gradients,
- neon/glow effects,
- huge cards,
- oversized headings,
- excessive emoji,
- unnecessary animation,
- fake charts.

---

# 16. Accessibility

Maintain or improve:

- `aria-label`
- `aria-expanded`
- `aria-pressed`
- keyboard accessibility
- visible focus states
- Esc-to-close for dialogs
- semantic buttons instead of clickable `<div>` where possible
- readable contrast
- text labels in addition to severity colors

For modal dialogs, use proper dialog semantics and prevent confusing background interactions.

---

# 17. Refactoring Guidance

This task may require reorganizing components.

That is allowed, but do not rewrite the whole application.

Prefer:

```text
CodeSmellApprovalPage.jsx
    ↓
AnalysisContext / Summary
FilterRail
ReviewToolbar
Smell/File/Category Review views
SelectionImpactPanel
ApprovalConfirmDialog
SourceViewer
```

You may create new presentational components when it improves readability.

Possible new files:

```text
diwo/components/CodeSmellFilterRail.jsx
diwo/components/CodeSmellSelectionPanel.jsx
diwo/components/CodeSmellApprovalDialog.jsx
diwo/components/CodeSmellFindingCard.jsx
```

But reuse existing logic from:

```text
SmellReviewViews.jsx
smellGrouping.js
impactSummary.js
CodeSmellToolbar.jsx
```

Do not fork the same selection logic into multiple components.

---

# 18. Do Not Do These Things

Do **not**:

1. Replace real CUQA data with the example `acme-corp/billing-platform` data.
2. Hardcode smell counts from the reference.
3. Hardcode `Java 17`, commit IDs, repository names, dates, or fake metrics.
4. Change backend endpoint names for visual convenience.
5. Call CUQA/RDP/SCTVA directly from React.
6. Infer SCTVA capability from severity.
7. Turn Category-wise mode into a new backend selection mode.
8. Add per-smell selection to File-wise mode.
9. Clear hidden selections when filters change.
10. Remove stale-workspace/reload handling.
11. Remove sample fallback handling.
12. Remove impact/optimizer functionality.
13. Remove Source Viewer error states.
14. Remove audit-relevant workflow actions.
15. Create a second independent source of truth for selected smells.
16. change working behavior just to copy the reference literally.
17. modify unrelated DIWO stages.
18. redesign the backend unless a concrete bug prevents the UI from working.

---

# 19. Implementation Strategy

Work in this order:

## Step 1 — Understand current behavior

Trace:

```text
CodeSmellApprovalPage
→ SmellReviewViews
→ smellGrouping
→ impact data
→ selectionPayload
→ previewSmellSelection
→ onProceed
→ DIWOAgentPage
→ /select-smells
→ RDP
```

Also trace:

```text
View code
→ SourceViewer
→ fetchFileSource
→ DIWO backend
→ workspace source proxy
```

Do not start styling before understanding these flows.

## Step 2 — Separate logic from layout

Keep current selection and data computation in the existing page where practical.

Move only presentational responsibilities into new components.

## Step 3 — Implement responsive workspace

Add:

- left filter rail,
- main review list,
- right selection/approval panel.

## Step 4 — Redesign findings

Use a compact professional smell card/row with visible:

- severity,
- category,
- path/line,
- CUQA explanation,
- impact summary,
- capability.

## Step 5 — Redesign SourceViewer

Keep its data-loading and line-coverage logic, but modernize the visual layout.

## Step 6 — Add confirmation interaction

Confirm exact selected findings/files before forwarding.

## Step 7 — Regression check

Test all modes and workflow transitions.

---

# 20. Functional Acceptance Criteria

The implementation is complete only when all of these work.

### Data

- [ ] Real current CUQA report renders.
- [ ] Live refresh/re-analyze still works.
- [ ] Workflow-provided report still works.
- [ ] Sample fallback still works.
- [ ] Missing taxonomy degrades safely.
- [ ] Missing impact data degrades safely.

### File-wise

- [ ] Whole file can be selected/deselected.
- [ ] All smells inside selected file are included in payload.
- [ ] No misleading individual smell approval control.
- [ ] Source can be opened for file or finding.

### Smell-wise

- [ ] Individual findings can be selected.
- [ ] Group selection works.
- [ ] Smell type grouping works repository-wide.
- [ ] Switching to Category-wise preserves selection.

### Category-wise

- [ ] Category grouping works.
- [ ] Nested smell/file groups work.
- [ ] Individual selection works.
- [ ] Backend payload remains smell-level.

### Filters

- [ ] Search works.
- [ ] Severity works.
- [ ] Category works.
- [ ] Smell type works.
- [ ] Combined filters work.
- [ ] Hidden selected findings remain selected.
- [ ] Select-all affects only visible items as currently designed.

### Impact

- [ ] Backend impact records still render.
- [ ] Capability remains backend-derived.
- [ ] Impact details open correctly.
- [ ] Optimizer still works.
- [ ] Budget control still works.
- [ ] Trade-off/interaction notes remain accessible.

### Source Viewer

- [ ] Original source loads.
- [ ] Focused smell auto-scrolls.
- [ ] Whole smell range is highlighted.
- [ ] Multiple smell markers remain visible.
- [ ] Error / SCTVA unavailable message works.
- [ ] Missing stale file message works.
- [ ] Esc and backdrop close work.
- [ ] Selection action is separate from focus/navigation.

### RDP Handoff

- [ ] Zero-selection state disables continue.
- [ ] Confirmation shows the real selection.
- [ ] File-wise payload remains correct.
- [ ] Smell/category payload remains correct.
- [ ] `/smell-selection-pass` still runs.
- [ ] `onProceed` still leads to `/select-smells`.
- [ ] Loading phase distinguishes filtering vs planning.
- [ ] Duplicate submit is prevented.

---

# 21. Visual Acceptance Criteria

The page should feel significantly cleaner than the current implementation.

- [ ] Main findings area is the dominant visual region.
- [ ] Filters are easy to scan.
- [ ] Selection impact is visible without scrolling to the bottom on desktop.
- [ ] Path, line and entity are easy to identify.
- [ ] Selected state is unmistakable.
- [ ] Severity/risk/capability are visually distinct.
- [ ] Detailed impact does not overload every row.
- [ ] Source viewer looks like part of the same DIWO application.
- [ ] Desktop layout does not waste width.
- [ ] Mobile/tablet layout remains usable.
- [ ] No duplicated giant page header inside the existing DIWO shell.

---

# 22. Expected Output From You

Implement the changes directly in the existing project.

After implementation, provide:

1. A concise list of files changed.
2. A short explanation of the new component structure.
3. Any new component files added.
4. Confirmation that backend/API contracts were preserved.
5. A short manual test checklist covering:
   - File-wise,
   - Smell-wise,
   - Category-wise,
   - filters,
   - Source Viewer,
   - optimizer/impact,
   - RDP handoff.
6. Mention any behavior you intentionally did **not** change and why.

Do not only give me design suggestions. **Make the code changes.**

---

# 23. Final Design Intent

The resulting DIWO Code Smell Review should communicate this clearly:

> CUQA has detected maintainability problems. DIWO organizes those findings, exposes where they occur and what their likely impact is, allows the developer to inspect the original source and control exactly what is approved, and forwards only the developer-approved findings to RDP for planning.

The redesign must improve **developer control, impact visibility, traceability, and review efficiency** without changing the underlying research workflow.
