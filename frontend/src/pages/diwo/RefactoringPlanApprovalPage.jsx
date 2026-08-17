/**
 * RefactoringPlanApprovalPage.jsx
 * ===============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 2 of the DIWO workflow: render the plan produced by the Refactoring
 * Decision & Planning (RDP) agent and let the developer approve or reject each
 * step before it reaches the Safe Transformation Agent.
 *
 * This page does NOT call the RDP agent. Planning is owned by the DIWO
 * backend: POST /workflows/<id>/select-smells forwards the *updated* smell
 * report — the developer's selection, with deselected smells and now-empty
 * files removed — to POST http://localhost:5000/generate, and returns the
 * plan, the decision trace and which agent produced it.
 *
 * The page used to POST /generate a second time from the browser, built from
 * `workflow.updated_report || cuqaReport`. Whenever `updated_report` was
 * missing that fell back to the FULL report, so every deselected smell came
 * back and the plan on screen disagreed with the one the backend had stored.
 * One request, one plan, one source of truth — hence `planData` only.
 *
 * Data source, in priority order:
 *   1. `planData` — the plan the backend returned with the smell selection.
 *   2. Bundled sample plan (diwoData.PLAN_DATA) — only if the developer opts
 *      in after a failure, and it is labelled as sample data in the UI.
 *
 * The displayed plan travels back up on approve (`onApprove({ plan })`) so the
 * parent forwards the exact steps the developer saw, not whichever copy its own
 * state happens to hold. Only the steps marked "approve" are transformed —
 * DIWOAgentPage filters on `decisions[step.step_id] === "approve"` before
 * handing the plan to the Safe Transformation Agent.
 *
 * Approving a step is a LOCAL decision and never regenerates the plan.
 * It used to: every click called onDecisionChange, which POSTed
 * /plan-preference-update, and that endpoint drops rejected steps, re-sorts
 * the rest, renumbers step_id from 1 and returns a new plan_id. The new
 * plan_id tripped the reset below, so the approval the developer had just made
 * was wiped a moment after it appeared — and the renumbering meant a decision
 * key no longer pointed at the step it was made on. Preference changes (risk
 * tolerance / impact focus) still ask the backend to re-rank, and decisions
 * are carried across that by step identity rather than by position.
 */

import { useState } from "react";
import { PLAN_DATA } from "./data/diwoData";
import { C, Card, Badge, Pill, impactColor, riskColor, severityColor } from "./diwoTheme.jsx";

/** A stable colour per file, so each file's header is identifiable at a glance. */
const FILE_COLORS = ["#00d4aa", "#3b82f6", "#f59e0b", "#a855f7", "#ec4899", "#22c55e", "#06b6d4", "#f97316"];
const fileColor = (path = "") => {
  let hash = 0;
  for (let i = 0; i < path.length; i += 1) hash = (hash * 31 + path.charCodeAt(i)) >>> 0;
  return FILE_COLORS[hash % FILE_COLORS.length];
};

export default function RefactoringPlanApprovalPage({
  onApprove,
  onFallback,
  planData,
  planMeta,
  loading = false,
  onDecisionChange,
}) {
  const [decisions, setDecisions] = useState({});
  const [opinion, setOpinion] = useState("");
  const [showOpinion, setShowOpinion] = useState(false);
  const [filter, setFilter] = useState("all");
  const [riskTolerance, setRiskTolerance] = useState("balanced");
  const [impactFocus, setImpactFocus] = useState("high");
  const [expanded, setExpanded] = useState(() => new Set());
  const [useSample, setUseSample] = useState(false);

  // ── Resolve the plan actually being rendered ───────────────────────────────
  const currentPlan = planData || (useSample ? PLAN_DATA : null);
  const origin = planData
    ? (planMeta?.plan_source === "rdp_agent" ? "rdp" : "workflow")
    : useSample
      ? "sample"
      : null;
  const steps = currentPlan?.steps || [];

  // A step's identity across plan revisions. step_id cannot be used: the
  // preference re-ranker renumbers steps from 1, so step 1 of a new plan is
  // usually a different refactoring than step 1 of the old one.
  const identityOf = (step) =>
    `${step.smell_id ?? ""}|${step.refactoring ?? ""}|${step.target?.file ?? ""}`;

  // Decisions are keyed by step_id, which only means anything within one plan.
  // When the plan is replaced, carry each decision over to the step it was
  // actually made on instead of dropping the lot. Adjusted during render
  // rather than in an effect — no cascading render, no stale first paint.
  const planKey = currentPlan ? `${origin}:${currentPlan.plan_id}` : null;
  const [prevPlanKey, setPrevPlanKey] = useState(planKey);
  const [prevSteps, setPrevSteps] = useState(steps);
  if (planKey !== prevPlanKey) {
    const byIdentity = new Map();
    prevSteps.forEach((step) => {
      const decision = decisions[step.step_id];
      if (decision) byIdentity.set(identityOf(step), decision);
    });

    const carried = {};
    steps.forEach((step) => {
      const decision = byIdentity.get(identityOf(step));
      if (decision) carried[step.step_id] = decision;
    });

    setPrevPlanKey(planKey);
    setPrevSteps(steps);
    setDecisions(carried);
    setExpanded(new Set());
  }

  /** Approve / reject one step. Local only — never regenerates the plan. */
  const decide = (id, val) =>
    setDecisions((prev) => (prev[id] === val ? prev : { ...prev, [id]: val }));

  /**
   * File-wise decision: every step planned for that file takes the same verdict,
   * so approving a file approves its whole plan and rejecting it drops the lot.
   * Clicking the verdict the file already carries clears it back to pending.
   */
  const decideGroup = (group, val) =>
    setDecisions((prev) => {
      const alreadyAll = group.steps.every((step) => prev[step.step_id] === val);
      const next = { ...prev };
      group.steps.forEach((step) => {
        if (alreadyAll) delete next[step.step_id];
        else next[step.step_id] = val;
      });
      return next;
    });

  /**
   * Ask the backend to re-rank the plan for a new preference. This is the only
   * thing that replaces the plan mid-review, and decisions survive it via the
   * identity carry-over above.
   */
  const applyPreferences = (next) => {
    const preferences = {
      risk_tolerance: next.riskTolerance ?? riskTolerance,
      impact_focus: next.impactFocus ?? impactFocus,
    };
    if (next.riskTolerance !== undefined) setRiskTolerance(next.riskTolerance);
    if (next.impactFocus !== undefined) setImpactFocus(next.impactFocus);
    onDecisionChange?.({ decisions, preferences });
  };

  const toggleExpanded = (id) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const allApproved = steps.length > 0 && steps.every(step => decisions[step.step_id] === "approve");

  const toggleSelectAll = () => {
    if (allApproved) {
      setDecisions({});
      return;
    }
    setDecisions(Object.fromEntries(steps.map(step => [step.step_id, "approve"])));
  };

  // ── Loading / error states ─────────────────────────────────────────────────
  if (loading && !currentPlan) {
    return <LoadingState rdpUrl={planMeta?.rdp_url} />;
  }

  if (!currentPlan) {
    return (
      <ErrorState
        warning={planMeta?.plan_warning}
        rdpUrl={planMeta?.rdp_url}
        onFallback={onFallback}
        onUseSample={() => setUseSample(true)}
      />
    );
  }

  const filtered = filter === "all" ? steps : steps.filter(s => {
    if (filter === "approved") return decisions[s.step_id] === "approve";
    if (filter === "rejected") return decisions[s.step_id] === "reject";
    if (filter === "pending") return !decisions[s.step_id];
    return (s.impact || s.expected_impact) === filter;
  });

  // Steps are grouped under the file they touch, the same way the Code Smell
  // Review page groups smells: the file is the unit of review, and its header
  // decides every step planned for it.
  const groups = [];
  const groupByFile = new Map();
  for (const step of filtered) {
    const file = step.target?.file || "(module level)";
    let group = groupByFile.get(file);
    if (!group) {
      group = { file, steps: [] };
      groupByFile.set(file, group);
      groups.push(group);
    }
    group.steps.push(step);
  }

  const approved = steps.filter(s => decisions[s.step_id] === "approve").length;
  const rejected = steps.filter(s => decisions[s.step_id] === "reject").length;
  const pending = steps.filter(s => !decisions[s.step_id]).length;
  const canProceed = approved > 0 && pending === 0;
  const refactoringTypes = [...new Set(steps.map(s => s.refactoring))];
  const summaryText = typeof currentPlan.summary === "string"
    ? currentPlan.summary
    : `Total steps: ${currentPlan.summary?.total_steps || steps.length} · High impact: ${currentPlan.summary?.high_impact || 0}`;
  const skipped = currentPlan.skipped_smells || [];

  const submit = () => {
    if (!canProceed) return;
    onApprove({
      decisions,
      opinion,
      plan: currentPlan,
      preferences: { risk_tolerance: riskTolerance, impact_focus: impactFocus },
    });
  };

  return (
    <div>
      <SourceBanner origin={origin} meta={planMeta} />

      <Card style={{ marginBottom: 20 }} glow={C.accentGlow}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Refactoring Planning Agent Output</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.text, marginBottom: 6 }}>
              {currentPlan.plan_id}
              {currentPlan.target && (
                <span style={{ fontSize: 12, fontWeight: 500, color: C.textMuted, marginLeft: 10, fontFamily: "monospace" }}>
                  {currentPlan.target}
                </span>
              )}
            </div>
            <div style={{ fontSize: 13, color: C.textSub, maxWidth: 620 }}>{summaryText}</div>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {refactoringTypes.map(t => <Badge key={t} label={t} color={C.info} />)}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginTop: 16 }}>
          {[
            { label: "Total Steps", val: steps.length, color: C.text },
            { label: "Approved", val: approved, color: C.accent },
            { label: "Rejected", val: rejected, color: C.danger },
            { label: "Pending", val: pending, color: C.warn },
          ].map(({ label, val, color }) => (
            <div key={label} style={{ background: C.bg, borderRadius: 8, padding: "12px", textAlign: "center" }}>
              <div style={{ fontSize: 22, fontWeight: 800, color, fontFamily: "monospace" }}>{val}</div>
              <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
            </div>
          ))}
        </div>
        {(skipped.length > 0 || currentPlan.reordered) && (
          <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap", fontSize: 11, color: C.textMuted }}>
            {currentPlan.reordered && (
              <span>↕ Steps were resequenced by the dependency analyzer.</span>
            )}
            {skipped.length > 0 && (
              <span title={skipped.map(s => `${s.smell_id} (${s.smell_type}): ${s.reason}`).join("\n")}>
                ⚠ {skipped.length} smell{skipped.length > 1 ? "s" : ""} skipped — no viable refactoring (hover for detail).
              </span>
            )}
          </div>
        )}
      </Card>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        {["all", "approved", "rejected", "pending", "high", "medium", "low"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, cursor: "pointer", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted, border: `1px solid ${filter === f ? C.accent : C.border}`
          }}>{f}</button>
        ))}
        <button onClick={toggleSelectAll} style={{
          padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700, cursor: "pointer", border: `1px solid ${C.accent}`,
          background: `${C.accent}15`, color: C.accent, textTransform: "uppercase"
        }}>
          {allApproved ? "Deselect All" : "Select All"}
        </button>
        <select
          value={riskTolerance}
          onChange={(e) => applyPreferences({ riskTolerance: e.target.value })}
          style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}` }}
        >
          <option value="conservative">Risk: Conservative</option>
          <option value="balanced">Risk: Balanced</option>
          <option value="aggressive">Risk: Aggressive</option>
        </select>
        <select
          value={impactFocus}
          onChange={(e) => applyPreferences({ impactFocus: e.target.value })}
          style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}` }}
        >
          <option value="high">Impact: High</option>
          <option value="medium">Impact: Medium</option>
          <option value="low">Impact: Low</option>
        </select>
      </div>

      {/* Height-capped column: the group cards must not shrink, or a file with
          many steps would be squeezed and its last rows clipped by the card's
          own overflow:hidden instead of scrolling here. */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 10,
        maxHeight: "min(72vh, 760px)", overflowY: "auto", paddingRight: 4,
      }}>
        {filtered.length === 0 && (
          <div style={{ padding: "28px 20px", textAlign: "center", background: C.panel, border: `1px dashed ${C.border}`, borderRadius: 10, color: C.textMuted, fontSize: 13, flexShrink: 0 }}>
            {steps.length === 0
              ? "The Refactoring Planning Agent produced no steps for this report — every smell was skipped. Fall back to Smell Review and select different files."
              : "No steps match the current filter."}
          </div>
        )}

        {groups.map(group => (
          <PlanFileGroup
            key={group.file}
            group={group}
            decisions={decisions}
            expanded={expanded}
            onDecide={decide}
            onDecideGroup={decideGroup}
            onToggleExpanded={toggleExpanded}
          />
        ))}
      </div>

      {filtered.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted }}>
          Showing {filtered.length} step{filtered.length > 1 ? "s" : ""} across {groups.length} file{groups.length > 1 ? "s" : ""}
          {filtered.length < steps.length && ` (${steps.length - filtered.length} hidden by the current filter)`}.
          {" "}Approving or rejecting a file applies to every step planned for it.
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <button onClick={() => setShowOpinion(!showOpinion)} style={{ background: "none", border: "none", color: C.textSub, cursor: "pointer", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", gap: 6, padding: 0 }}>
          <span style={{ fontSize: 16 }}>{showOpinion ? "▾" : "▸"}</span> Add Developer Opinion / Notes
        </button>
        {showOpinion && (
          <textarea
            value={opinion}
            onChange={e => setOpinion(e.target.value)}
            placeholder="Provide additional context, concerns, or specific guidance for the transformation agent..."
            style={{ width: "100%", marginTop: 8, background: C.panel, border: `1px solid ${C.borderAcc}`, borderRadius: 8, padding: "10px 14px", color: C.text, fontSize: 12, resize: "vertical", minHeight: 80, outline: "none", boxSizing: "border-box" }}
          />
        )}
      </div>

      <div style={{ display: "flex", gap: 12, marginTop: 16, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button onClick={onFallback} style={{
          padding: "10px 22px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer",
          background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30`
        }}>
          ← Fallback to Smell Review
        </button>
        {!canProceed && pending > 0 && (
          <div style={{ display: "flex", alignItems: "center", fontSize: 12, color: C.warn }}>
            ⚠ Review all {pending} pending steps to proceed
          </div>
        )}
        <button onClick={submit} disabled={!canProceed} title={
          rejected > 0
            ? `${approved} approved step(s) will be transformed; ${rejected} rejected step(s) are dropped from the plan.`
            : `${approved} approved step(s) will be transformed.`
        } style={{
          padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: canProceed ? "pointer" : "not-allowed",
          background: canProceed ? C.accent : C.border, color: canProceed ? "#000" : C.textMuted, border: "none",
          boxShadow: canProceed ? `0 0 20px ${C.accentGlow}` : "none", transition: "all 0.2s"
        }}>
          Forward {approved} Approved Step{approved === 1 ? "" : "s"} to Transformation →
          {rejected > 0 && (
            <span style={{ fontWeight: 500, opacity: 0.75 }}> ({rejected} rejected, skipped)</span>
          )}
        </button>
      </div>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

/** The file path banner heading each group. Its border is the file's own colour,
 *  so one file's block is told from the next at a glance. Sticky, so the path
 *  stays visible while scrolling a file with many steps. */
function FilePathBar({ file, color, children }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10, padding: "10px 16px",
      background: `${color}12`,
      borderBottom: `2px solid ${color}`,
      borderLeft: `4px solid ${color}`,
      position: "sticky", top: 0, zIndex: 1,
    }}>
      <span style={{
        fontSize: 18, fontWeight: 700, color, letterSpacing: 1,
        textTransform: "uppercase", flexShrink: 0,
      }}>
        File Path
      </span>
      <span
        title={file}
        style={{
          fontSize: 18, fontWeight: 700, color: C.text, fontFamily: "monospace",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          minWidth: 0, flexShrink: 1,
        }}
      >
        {file}
      </span>
      {children}
    </div>
  );
}

/**
 * One file's planned steps, laid out the way the Code Smell Review page lays
 * out a file's smells: a sticky file header, then the steps row by row.
 *
 * The header decides the whole file — every step in it takes the same verdict —
 * while each row keeps its own buttons for overriding a single step afterwards.
 * The card is never height-capped: it grows with the number of steps, and
 * flexShrink: 0 stops the scrolling parent squeezing it.
 */
function PlanFileGroup({ group, decisions, expanded, onDecide, onDecideGroup, onToggleExpanded }) {
  const total = group.steps.length;
  const approved = group.steps.filter(s => decisions[s.step_id] === "approve").length;
  const rejected = group.steps.filter(s => decisions[s.step_id] === "reject").length;
  const pending = total - approved - rejected;

  const allApproved = approved === total;
  const allRejected = rejected === total;
  const borderColor = pending > 0 ? C.border : approved > 0 ? C.accent : C.danger;
  const color = fileColor(group.file);

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${borderColor}`,
      borderRadius: 10, overflow: "hidden", flexShrink: 0,
      boxShadow: pending === 0 && approved > 0 ? `0 0 12px ${C.accentGlow}` : "none",
      transition: "all 0.2s",
    }}>
      <FilePathBar file={group.file} color={color}>
        <Badge label={`${total} step${total > 1 ? "s" : ""}`} color={C.info} />
        <span style={{ marginLeft: "auto", fontSize: 11, flexShrink: 0, display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ color: approved > 0 ? C.accent : C.textMuted }}>{approved}/{total} approved</span>
          {rejected > 0 && <span style={{ color: C.danger }}>{rejected} rejected</span>}
          {pending > 0 && <span style={{ color: C.warn }}>{pending} pending</span>}
        </span>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button
            onClick={() => onDecideGroup(group, "approve")}
            title={`Approve all ${total} step(s) planned for this file`}
            style={{
              padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
              background: allApproved ? C.accent : `${C.accent}18`, color: allApproved ? "#000" : C.accent,
              transition: "all 0.2s",
            }}
          >
            ✓ Approve File
          </button>
          <button
            onClick={() => onDecideGroup(group, "reject")}
            title={`Reject all ${total} step(s) planned for this file`}
            style={{
              padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
              background: allRejected ? C.danger : `${C.danger}18`, color: allRejected ? "#fff" : C.danger,
              transition: "all 0.2s",
            }}
          >
            ✕ Reject File
          </button>
        </div>
      </FilePathBar>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {group.steps.map((step, rowIdx) => (
          <PlanStepRow
            key={step.step_id}
            step={step}
            rowIdx={rowIdx}
            decision={decisions[step.step_id]}
            isOpen={expanded.has(step.step_id)}
            onDecide={onDecide}
            onToggleExpanded={onToggleExpanded}
          />
        ))}
      </div>
    </div>
  );
}

/** One planned refactoring step — same content and controls as before, now a
 *  row inside its file's group rather than a standalone card. */
function PlanStepRow({ step, rowIdx, decision, isOpen, onDecide, onToggleExpanded }) {
  const bgColor =
    decision === "approve" ? `${C.accent}0a` : decision === "reject" ? `${C.danger}0a` : "transparent";
  const targetLabel =
    [step.target?.class, step.target?.method].filter(Boolean).join(".") ||
    step.target?.file ||
    "(module level)";

  return (
    <div style={{
      padding: "14px 18px", flexShrink: 0, background: bgColor,
      borderTop: rowIdx > 0 ? `1px solid ${C.border}` : "none",
      borderLeft: `3px solid ${decision === "approve" ? C.accent : decision === "reject" ? C.danger : "transparent"}`,
      transition: "all 0.2s",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
            <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>Step {step.step_id}</span>
            <Badge label={step.refactoring} color={C.info} />
            <Pill label={`Impact: ${step.impact || step.expected_impact || "medium"}`} color={impactColor(step.impact || step.expected_impact || "medium")} />
            <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
            {step.smell_type && (
              <Pill label={step.smell_type} color={severityColor(step.severity)} />
            )}
            {typeof step.score === "number" && (
              <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace" }} title={`MCDA score (${step.scoring_method || "mcda"})`}>
                score {step.score.toFixed(2)}
              </span>
            )}
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 4 }}>
            {targetLabel}
            {Array.isArray(step.target?.lines) && step.target.lines.length > 0 && (
              <span style={{ fontSize: 11, fontWeight: 400, color: C.textMuted, marginLeft: 8, fontFamily: "monospace" }}>
                L{step.target.lines.join("-")}
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.5 }}>{step.explanation}</div>

          <button onClick={() => onToggleExpanded(step.step_id)} style={{
            background: "none", border: "none", padding: 0, marginTop: 8, cursor: "pointer",
            color: C.textMuted, fontSize: 11, fontWeight: 600, display: "flex", alignItems: "center", gap: 5,
          }}>
            <span>{isOpen ? "▾" : "▸"}</span> Transformation details
          </button>

          {isOpen && <StepDetails step={step} />}
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button onClick={() => onDecide(step.step_id, "approve")} style={{
            padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
            background: decision === "approve" ? C.accent : `${C.accent}18`, color: decision === "approve" ? "#000" : C.accent,
            transition: "all 0.2s"
          }}>✓ Approve</button>
          <button onClick={() => onDecide(step.step_id, "reject")} style={{
            padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
            background: decision === "reject" ? C.danger : `${C.danger}18`, color: decision === "reject" ? "#fff" : C.danger,
            transition: "all 0.2s"
          }}>✕ Reject</button>
        </div>
      </div>
    </div>
  );
}

/**
 * The parameters block is what the Safe Transformation Agent actually executes,
 * so it is shown verbatim: a placeholder like "<parent>" here is the developer's
 * only warning that a step will not transform cleanly.
 */
function StepDetails({ step }) {
  const params = Object.entries(step.parameters || {});
  const prediction = step.prediction;

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.borderAcc}`, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
          Transformation parameters · smell {step.smell_id}
        </div>
        {params.length === 0 ? (
          <div style={{ fontSize: 11, color: C.textMuted }}>No parameters — the agent inferred nothing to configure.</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {params.map(([key, value]) => (
              <span key={key} style={{
                fontSize: 11, fontFamily: "monospace", padding: "3px 8px", borderRadius: 6,
                background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
              }}>
                <span style={{ color: C.textMuted }}>{key}:</span>{" "}
                {typeof value === "object" ? JSON.stringify(value) : String(value)}
              </span>
            ))}
          </div>
        )}
      </div>

      {prediction && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Predicted effect
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 11, color: C.textSub }}>
            <span>complexity after: <b style={{ color: C.text }}>{prediction.predicted_complexity_after}</b></span>
            <span>coupling: <b style={{ color: prediction.coupling_change <= 0 ? C.accent : C.warn }}>{prediction.coupling_change}</b></span>
            <span>cohesion: <b style={{ color: prediction.cohesion_change >= 0 ? C.accent : C.warn }}>{prediction.cohesion_change}</b></span>
            <span>maintainability: <b style={{ color: C.accent }}>+{prediction.maintainability_improvement}</b></span>
            <span>risk: <b style={{ color: riskColor(step.risk) }}>{prediction.risk_score}</b></span>
          </div>
        </div>
      )}

      {step.alternatives?.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Alternatives considered
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {step.alternatives.map(alt => (
              <span key={alt.name} style={{ fontSize: 11, color: C.textMuted }}>
                {alt.name}
                {typeof alt.score === "number" ? ` (${alt.score.toFixed(2)})` : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const ORIGIN_LABEL = {
  rdp: "Live plan from the Refactoring Planning Agent",
  workflow: "Plan from the current DIWO workflow",
  sample: "Sample plan (bundled data — RDP agent unavailable)",
};

function SourceBanner({ origin, meta }) {
  const color = origin === "rdp" ? C.accent : origin === "workflow" ? C.info : C.warn;

  // Everything shown here comes from the /select-smells response — the page
  // never contacts the RDP agent, so it reports what the backend did.
  const details = [];
  if (origin === "rdp") {
    if (meta?.rdp_url) details.push(meta.rdp_url);
    if (meta?.files_sent) details.push(`${meta.files_sent} file(s)`);
    if (typeof meta?.smells_sent === "number") details.push(`${meta.smells_sent} selected smell(s) sent`);
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
      marginBottom: 16, padding: "10px 16px", borderRadius: 10,
      background: `${color}0a`, border: `1px solid ${color}40`, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: `0 0 8px ${color}`, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{ORIGIN_LABEL[origin] || "Refactoring plan"}</div>
          {details.length > 0 && (
            <div style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis" }}>
              {details.join(" · ")}
            </div>
          )}
          {meta?.plan_warning && (
            <div style={{ fontSize: 11, color: C.warn, marginTop: 2 }}>
              RDP agent unavailable: {meta.plan_warning}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LoadingState({ rdpUrl }) {
  return (
    <Card style={{ textAlign: "center", padding: "48px 24px" }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 8 }}>
        Generating the refactoring plan…
      </div>
      <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 20 }}>
        The DIWO backend is forwarding your selected smells to {rdpUrl || "the RDP agent"}
        {" "}· interpreting smells, scoring candidates (MCDA), sequencing steps
      </div>
      <div style={{ height: 4, borderRadius: 4, background: C.border, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: C.gradient, animation: "diwoSlide 1.1s ease-in-out infinite" }} />
      </div>
      <style>{`@keyframes diwoSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }`}</style>
    </Card>
  );
}

function ErrorState({ warning, rdpUrl, onUseSample, onFallback }) {
  const nothingToPlan = /no code smells|nothing to plan|not called/i.test(warning || "");

  const hint = nothingToPlan
    ? "Every selected file came through without smells. Go back to Code Smell Review and approve files that still have detected smells."
    : `Start the Refactoring Planning agent before running the DIWO workflow:  cd agents/rdp_agent && python app.py  (serves ${rdpUrl || "http://localhost:5000"})`;

  return (
    <Card style={{ padding: "32px 28px", borderColor: `${C.danger}50` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <Badge label={nothingToPlan ? "NOTHING TO PLAN" : "NO PLAN AVAILABLE"} color={C.danger} />
        <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
          Could not load the refactoring plan
        </span>
      </div>

      <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.6, marginBottom: 12 }}>
        {warning || "The workflow returned no plan for this smell selection."}
      </div>

      <div style={{
        fontSize: 11, color: C.textMuted, fontFamily: "monospace", lineHeight: 1.6,
        background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px", marginBottom: 18,
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button onClick={onFallback} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30`, cursor: "pointer",
        }}>
          ← Back to Smell Review
        </button>
        <button onClick={onUseSample} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer",
        }}>
          Continue with sample data
        </button>
      </div>
    </Card>
  );
}
