/**
 * PlanStepNavigator.jsx
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The left column of the Refactoring Plan Approval stage: the plan's
 * refactoring METHODS as cards, each folding out to the files it changes.
 * The filters live in the plan header (PlanFilters); "Recommendation"
 * grouping adds a category heading above the cards it holds.
 *
 *     [Select recommended (1)] [✓ Approve All] [✕ Reject All]   [Expand all]
 *     ┌──────────────────────────────────────────────┐
 *     │ 🛠  REFACTORING METHOD                      ▶ │
 *     │     Introduce Constant   [6 files]           │
 *     │     Replaces magic numbers with constants.   │
 *     │     0 of 6 decided   [✓ Approve all] [✕ Reject all]
 *     ├──────────────────────────────────────────────┤   (expanded)
 *     │ ☑  📄 cpp/INIReader.h                         │
 *     │        Magic Numbers                         │
 *     └──────────────────────────────────────────────┘
 *     7 of 7 changes still need a decision.
 *
 * The cards start COLLAPSED, so the first view is the list of methods.
 *
 * THE TICK IS THE APPROVAL. A ticked file is approved and goes to SCTVA; an
 * unticked one the developer decided against is rejected (a red ✕ box), and
 * one nobody has decided yet is an empty box. "Approve all" ticks every file
 * in the card, after which any of them can be unticked. Every change goes
 * through the page's `onBulk`, so ticking a step DIWO advised against still
 * opens the override confirmation.
 *
 * ONE ROW PER PLAN STEP. Two steps that share a method and a file stay two
 * rows with two ticks, identified by step_id, never by filename. Clicking a
 * row's file path opens that step in the workspace on the right.
 *
 * Presentational: decisions, filters and expansion all live in the page.
 */

import { T, tint, ghostButton } from "../codeSmellTheme.js";
import { APPROVE, REJECT, groupStepsByMethod } from "../utils/planningDecisionSupport";
import { describeRefactoring } from "../utils/refactoringDescriptions";

export default function PlanStepNavigator({
  groupMode, decisionCounts, sections, decisions, activeId, onActivate,
  recommendedIds, visibleIds, onBulk, expanded, onToggleExpanded, onExpandAll, onCollapseAll,
  totalSteps, onClearDecisions,
}) {
  const pending = decisionCounts.pending;
  const decided = totalSteps - pending;
  const anyExpanded = expanded.size > 0;
  const showCategoryHeadings = groupMode === "recommendation";
  // Derived, not remembered: the button stays filled only while every step it
  // covers actually carries that verdict.
  const allShownApproved = visibleIds.length > 0 && visibleIds.every((id) => decisions[id] === APPROVE);
  const allShownRejected = visibleIds.length > 0 && visibleIds.every((id) => decisions[id] === REJECT);

  return (
    <aside
      className="pa-nav"
      aria-label="Plan steps"
      style={{
        background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12,
        boxShadow: T.shadowSm, overflow: "hidden",
      }}
    >
      {/* ── Bulk ticks and expansion ───────────────────────────────────── */}
      <div style={{
        padding: "9px 12px", borderBottom: `1px solid ${T.border}`, background: T.sunken,
        display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center",
      }}>
        <button
          type="button"
          onClick={() => onBulk(recommendedIds, APPROVE)}
          disabled={!recommendedIds.length}
          className="dw-focus dw-hover"
          title="Tick (approve) every step DIWO categorised as Recommended"
          style={ghostButton(recommendedIds.length > 0, {
            padding: "5px 10px", fontSize: 11.5,
            color: T.info, border: `1px solid ${T.info}`, background: tint(T.info, 6),
          })}
        >
          Select recommended ({recommendedIds.length})
        </button>
        <GroupActionButton
          tone={T.accent}
          text={T.accentText}
          filled={allShownApproved}
          disabled={!visibleIds.length}
          label={`Approve (tick) all ${visibleIds.length} step(s) shown`}
          onClick={() => onBulk(visibleIds, APPROVE)}
        >
          ✓ Approve All
        </GroupActionButton>
        <GroupActionButton
          tone={T.high}
          text={T.high}
          filled={allShownRejected}
          disabled={!visibleIds.length}
          label={`Reject (untick) all ${visibleIds.length} step(s) shown`}
          onClick={() => onBulk(visibleIds, REJECT)}
        >
          ✕ Reject All
        </GroupActionButton>
        <button
          type="button"
          onClick={anyExpanded ? onCollapseAll : onExpandAll}
          disabled={!sections.length}
          aria-label={anyExpanded ? "Collapse all refactoring methods" : "Expand all refactoring methods to show their files"}
          className="dw-focus dw-hover"
          style={ghostButton(sections.length > 0, { padding: "5px 10px", fontSize: 11.5, marginLeft: "auto" })}
        >
          <span aria-hidden="true" style={{
            display: "inline-block", fontSize: 9,
            transform: anyExpanded ? "rotate(90deg)" : "none", transition: "transform 0.15s",
          }}>▶</span>
          {anyExpanded ? "Collapse all" : "Expand all"}
        </button>
      </div>

      {/* ── Method cards ───────────────────────────────────────────────── */}
      <div className="pa-nav-list" role="list" style={{ padding: "4px 10px 12px" }}>
        {sections.length === 0 && (
          <div style={{ padding: "28px 16px", textAlign: "center", fontSize: 12.5, color: T.text2 }}>
            {totalSteps === 0
              ? "The Refactoring Planning Agent produced no steps — every smell was skipped."
              : "No steps match the current filters."}
          </div>
        )}

        {sections.map((section) => (
          <div key={section.key}>
            {showCategoryHeadings && section.title && (
              <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "10px 4px 2px" }}>
                {section.icon && <span aria-hidden="true" style={{ fontSize: 12, color: section.tone }}>{section.icon}</span>}
                <span style={{ fontSize: 12.5, fontWeight: 800, color: section.tone || T.text }}>{section.title}</span>
                <span style={{ fontSize: 11, fontFamily: T.mono, color: T.text3 }}>
                  {section.steps.length} step{section.steps.length === 1 ? "" : "s"}
                </span>
              </div>
            )}

            {groupStepsByMethod(section.steps).map((method) => {
              const key = `${section.key}:${method.key}`;
              return (
                <MethodGroupCard
                  key={key}
                  method={method.key}
                  steps={method.steps}
                  open={expanded.has(key)}
                  onToggleOpen={() => onToggleExpanded(key)}
                  decisions={decisions}
                  activeId={activeId}
                  onActivate={onActivate}
                  onBulk={onBulk}
                />
              );
            })}
          </div>
        ))}
      </div>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
        padding: "9px 12px", borderTop: `1px solid ${T.border}`, background: T.sunken,
      }}>
        <span aria-live="polite" style={{ fontSize: 12.5, fontWeight: 600, color: pending ? T.text : T.accentText }}>
          {pending
            ? `${pending} of ${totalSteps} change${totalSteps === 1 ? "" : "s"} still need${pending === 1 ? "s" : ""} a decision.`
            : `All ${totalSteps} change${totalSteps === 1 ? "" : "s"} decided.`}
        </span>
        {decided > 0 && (
          <button
            type="button"
            onClick={onClearDecisions}
            className="dw-focus"
            style={{ ...linkButton, marginLeft: "auto", color: T.high }}
            title="Clear every decision and start the review again"
          >
            Reset decisions
          </button>
        )}
      </div>
    </aside>
  );
}

// ─── Method group card ───────────────────────────────────────────────────────

/**
 * One refactoring method and its files. The method name is the largest text
 * in the card; Approve all / Reject all decide only the steps in THIS card.
 */
function MethodGroupCard({ method, steps, open, onToggleOpen, decisions, activeId, onActivate, onBulk }) {
  const ids = steps.map((s) => s.step_id);
  const fileCount = new Set(steps.map((s) => s.target?.file || "(module level)")).size;
  const decidedCount = ids.filter((id) => decisions[id]).length;
  const approvedCount = ids.filter((id) => decisions[id] === APPROVE).length;
  const description = describeRefactoring(method);
  const containsActive = ids.includes(activeId);

  return (
    <section
      aria-label={`Refactoring method ${method}`}
      style={{
        marginTop: 10, borderRadius: 12, overflow: "hidden",
        border: `1px solid ${containsActive ? tint(T.accent, 55) : T.border}`, background: T.surface,
      }}
    >
      <div style={{ padding: "12px 12px 11px", background: T.surface2 }}>
        <button
          type="button"
          onClick={onToggleOpen}
          aria-expanded={open}
          title={open ? `Hide the files of ${method}` : `Show the files of ${method}`}
          className="dw-focus"
          style={{
            display: "flex", gap: 10, alignItems: "flex-start", width: "100%",
            background: "none", border: "none", padding: 0,
            textAlign: "left", cursor: "pointer", color: "inherit", borderRadius: 4,
          }}
        >
          <span aria-hidden="true" style={{
            width: 34, height: 34, borderRadius: 9, flexShrink: 0,
            display: "flex", alignItems: "center", justifyContent: "center",
            background: tint(T.accent, 14), color: T.accentText, fontSize: 16,
          }}>
            🛠
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: "block", fontSize: 10.5, fontWeight: 700, letterSpacing: 0.8, textTransform: "uppercase", color: T.text3 }}>
              Refactoring method
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 2 }}>
              <span style={{ fontSize: 17, fontWeight: 800, color: T.text, lineHeight: 1.25, overflowWrap: "anywhere" }}>
                {method}
              </span>
              <span style={{
                fontSize: 11, fontWeight: 700, padding: "1px 8px", borderRadius: 999,
                background: T.sunken, border: `1px solid ${T.border}`, color: T.text2, whiteSpace: "nowrap",
              }}>
                {fileCount} file{fileCount === 1 ? "" : "s"}
                {steps.length !== fileCount ? ` · ${steps.length} steps` : ""}
              </span>
            </span>
            {description && (
              <span style={{ display: "block", fontSize: 11.5, color: T.text2, lineHeight: 1.45, marginTop: 4 }}>
                {description}
              </span>
            )}
          </span>
          <span aria-hidden="true" style={{
            flexShrink: 0, fontSize: 10, color: T.text3, marginTop: 4, display: "inline-block",
            transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s",
          }}>
            ▶
          </span>
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
          <span style={{ fontSize: 11.5, color: T.text3, marginRight: "auto" }}>
            <b style={{ color: decidedCount === steps.length ? T.accentText : T.text }}>{decidedCount} of {steps.length}</b> decided
            {approvedCount > 0 && <> · {approvedCount} ticked</>}
          </span>
          <GroupActionButton
            tone={T.accent}
            text={T.accentText}
            filled={approvedCount === steps.length}
            label={`Approve (tick) all ${steps.length} ${method} step(s)`}
            onClick={() => onBulk(ids, APPROVE)}
          >
            ✓ Approve all
          </GroupActionButton>
          <GroupActionButton
            tone={T.high}
            text={T.high}
            filled={ids.every((id) => decisions[id] === REJECT)}
            label={`Reject (untick) all ${steps.length} ${method} step(s)`}
            onClick={() => onBulk(ids, REJECT)}
          >
            ✕ Reject all
          </GroupActionButton>
        </div>
      </div>

      {open && steps.map((step) => (
        <MethodFileRow
          key={step.step_id}
          step={step}
          decision={decisions[step.step_id]}
          active={step.step_id === activeId}
          onActivate={() => onActivate(step.step_id)}
          onTick={() => onBulk([step.step_id], decisions[step.step_id] === APPROVE ? REJECT : APPROVE)}
        />
      ))}
    </section>
  );
}

/** Tick box · file icon · file path · code smell. */
function MethodFileRow({ step, decision, active, onActivate, onTick }) {
  const file = step.target?.file || "(module level)";
  const slash = file.lastIndexOf("/");
  const approved = decision === APPROVE;
  const rejected = decision === REJECT;

  return (
    <div
      role="listitem"
      className={active ? undefined : "pa-row"}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 12px 10px 9px",
        borderTop: `1px solid ${T.border}`,
        borderLeft: `3px solid ${active ? T.accent : "transparent"}`,
        background: active ? tint(T.accent, 9) : "transparent",
      }}
    >
      <TickBox
        approved={approved}
        rejected={rejected}
        label={`${approved ? "Untick (reject)" : "Tick (approve)"} step ${step.step_id}: ${step.refactoring} in ${file}`}
        onClick={onTick}
      />

      <span aria-hidden="true" style={{ fontSize: 18, flexShrink: 0, lineHeight: 1 }}>📄</span>

      <button
        type="button"
        onClick={onActivate}
        aria-current={active ? "true" : undefined}
        title={`${file} — open step ${step.step_id}`}
        className="dw-focus"
        style={{
          flex: 1, minWidth: 0, background: "none", border: "none", padding: 0,
          cursor: "pointer", textAlign: "left", color: "inherit", borderRadius: 4,
        }}
      >
        <div style={{
          fontFamily: T.mono, fontSize: 12.5, fontWeight: 700, color: rejected ? T.text3 : T.text,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          textDecoration: rejected ? "line-through" : "none",
        }}>
          {slash >= 0 && <span style={{ color: T.text3, fontWeight: 400 }}>{file.slice(0, slash + 1)}</span>}
          {file.slice(slash + 1)}
        </div>
        {step.smell_type && (
          <div style={{ fontSize: 11.5, color: T.text2, marginTop: 2 }}>{step.smell_type}</div>
        )}
      </button>
    </div>
  );
}

/**
 * Approved = ticked. Rejected = a red ✕ box, so a file the developer turned
 * down is told apart from one nobody has decided yet (an empty box).
 */
function TickBox({ approved, rejected, label, onClick }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={approved}
      aria-label={label}
      title={approved ? "Approved — untick to reject" : rejected ? "Rejected — tick to approve" : "Not decided — tick to approve"}
      onClick={onClick}
      className="dw-focus"
      style={{
        width: 20, height: 20, flexShrink: 0, padding: 0, borderRadius: 5, cursor: "pointer",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 13, fontWeight: 900, lineHeight: 1,
        background: approved ? T.accent : rejected ? tint(T.high, 12) : T.surface,
        color: approved ? T.onAccent : T.high,
        border: `1.5px solid ${approved ? T.accent : rejected ? T.high : T.borderStrong}`,
      }}
    >
      {approved ? "✓" : rejected ? "✕" : ""}
    </button>
  );
}

/** Solid fill once applied: dark green for approve, red for reject. */
const FILLED = { approve: "#15803d", reject: "#dc2626" };

function GroupActionButton({ tone, text, filled = false, disabled = false, onClick, label, children }) {
  const solid = tone === T.high ? FILLED.reject : FILLED.approve;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={filled}
      aria-label={label}
      title={label}
      className="dw-focus"
      style={{
        padding: "5px 10px", borderRadius: 8, fontSize: 11.5, fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
        background: filled ? solid : tint(tone, 10),
        color: filled ? "#ffffff" : text,
        border: `1px solid ${filled ? solid : tint(tone, 50)}`,
        transition: "background 0.15s, color 0.15s",
      }}
    >
      {children}
    </button>
  );
}

const linkButton = {
  background: "none", border: "none", padding: "2px 4px", cursor: "pointer",
  color: T.accentText, fontSize: 11.5, fontWeight: 700, borderRadius: 4,
};
