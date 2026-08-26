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
 * key no longer pointed at the step it was made on. Only a developer-goal
 * change asks the backend to re-rank, and decisions are carried across that by
 * step identity rather than by position.
 *
 * DECISION SUPPORT
 * ----------------
 * Each step arrives carrying `decision_support`, computed by the DIWO backend
 * (domain/planning_recommendation.py) from RDP's own score, the Stage 1 impact
 * record, the live SCTVA capability probe, the developer's goal and — once
 * enough real observations exist — their own acceptance history. This page
 * RENDERS that assessment; it never computes one. Two implementations of one
 * scoring formula would be two answers to the same question.
 *
 * The recommendation is advice. `Select Recommended` sets local state on the
 * green steps and nothing else: it calls no agent, submits no plan and starts
 * no transformation. The developer still presses Forward, and a step they did
 * not approve never leaves this page.
 */

import { useState } from "react";
import { PLAN_DATA } from "../data/diwoData";
import { C, Card, Badge, Pill, impactColor, riskColor, severityColor } from "../diwoTheme.jsx";
import PlanningDecisionSummary from "../components/PlanningDecisionSummary";
import PlanningRecommendationBadge from "../components/PlanningRecommendationBadge";
import PlanStepDrawer from "../components/PlanStepDrawer";
import {
  CATEGORY_ORDER, categoryOf, categoryStyle,
  groupBreakdown, isAutoSelectable, planSummary, supportOf,
} from "../utils/planningDecisionSupport";

/**
 * One accent colour for every file header.
 *
 * This used to be hashed from the path, giving each file its own colour out of
 * eight. On a plan touching several files that painted a column of unrelated
 * greens, ambers and reds down the page — the same four colours the
 * recommendation badges use to mean something specific. A file header is
 * structure, not status, so it takes one neutral accent and leaves the palette
 * to the signal that needs it.
 */
const FILE_ACCENT = C.info;

const STATUS_FILTERS = ["all", "approved", "rejected", "pending"];

/**
 * Structured reasons offered when the developer overrides a recommendation.
 * Optional — the workflow is never blocked on one. They exist because a
 * disagreement with DIWO is the most informative feedback the system can
 * collect, and a free-text box collects it least often.
 */
const OVERRIDE_REASONS = [
  "Too risky", "Not useful", "Wrong refactoring", "Too much scope",
  "Prefer manual change", "Insufficient explanation", "Other",
];

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
  const [impactFilter, setImpactFilter] = useState("any");
  const [strategy, setStrategy] = useState("balanced");
  // The step whose explanation dialog is open, by step_id. One at a time — the
  // same shape Stage 1 uses for its impact drawer.
  const [explaining, setExplaining] = useState(null);
  const [useSample, setUseSample] = useState(false);
  // step_id -> short reason, for decisions that went against the recommendation.
  const [overrideReasons, setOverrideReasons] = useState({});

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
  // usually a different refactoring than step 1 of the old one. The backend
  // uses the same triple for its feedback rows (domain step_identity).
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
    const reasonsByIdentity = new Map();
    prevSteps.forEach((step) => {
      const decision = decisions[step.step_id];
      if (decision) byIdentity.set(identityOf(step), decision);
      const reason = overrideReasons[step.step_id];
      if (reason) reasonsByIdentity.set(identityOf(step), reason);
    });

    const carried = {};
    const carriedReasons = {};
    steps.forEach((step) => {
      const identity = identityOf(step);
      const decision = byIdentity.get(identity);
      if (decision) carried[step.step_id] = decision;
      const reason = reasonsByIdentity.get(identity);
      if (reason) carriedReasons[step.step_id] = reason;
    });

    setPrevPlanKey(planKey);
    setPrevSteps(steps);
    setDecisions(carried);
    setOverrideReasons(carriedReasons);
    setExplaining(null);
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
   * Ask the backend to re-rank and re-score the plan for a new developer goal.
   * This is the only thing that replaces the plan mid-review, and decisions
   * survive it via the identity carry-over above.
   *
   * The goal is sent as `developer_strategy`; the backend expands it to the
   * risk_tolerance / impact_focus pair the re-ranker has always taken, and
   * both are sent as well so an older backend still understands the request.
   */
  const applyStrategy = (next) => {
    setStrategy(next);
    const preferences = {
      developer_strategy: next,
      ...({
        safety_first: { risk_tolerance: "conservative", impact_focus: "medium" },
        balanced: { risk_tolerance: "balanced", impact_focus: "high" },
        max_improvement: { risk_tolerance: "aggressive", impact_focus: "high" },
      }[next] || { risk_tolerance: "balanced", impact_focus: "high" }),
    };
    onDecisionChange?.({ decisions, preferences });
  };

  /**
   * Approve exactly the steps the backend marked `auto_select_eligible`.
   *
   * LOCAL STATE ONLY. No RDP call, no SCTVA call, no plan submission — the
   * developer still presses Forward, and may change any of these before they
   * do. Review, not-recommended and manual-only steps are left pending on
   * purpose: they are the ones worth reading.
   */
  const selectRecommended = () =>
    setDecisions((prev) => {
      const next = { ...prev };
      steps.forEach((step) => {
        if (isAutoSelectable(step)) next[step.step_id] = "approve";
      });
      return next;
    });

  const selectAll = () =>
    setDecisions(Object.fromEntries(steps.map((step) => [step.step_id, "approve"])));

  const clearDecisions = () => {
    setDecisions({});
    setOverrideReasons({});
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

  const summary = planSummary(currentPlan, strategy);

  const matchesStatus = (step) => {
    if (filter === "all") return true;
    if (filter === "approved") return decisions[step.step_id] === "approve";
    if (filter === "rejected") return decisions[step.step_id] === "reject";
    if (filter === "pending") return !decisions[step.step_id];
    // Anything else is a recommendation category.
    return categoryOf(step) === filter;
  };

  const matchesImpact = (step) =>
    impactFilter === "any" || (step.impact || step.expected_impact) === impactFilter;

  const filtered = steps.filter((step) => matchesStatus(step) && matchesImpact(step));

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

  // Steps where the developer went against the recommendation. Surfaced before
  // submit, not to argue with them, but because a disagreement is worth one
  // sentence of context for whoever reads the audit trail later.
  const overrides = steps.filter((step) => {
    const category = categoryOf(step);
    const verdict = decisions[step.step_id];
    if (!category || !verdict) return false;
    return (verdict === "approve" && (category === "not_recommended" || category === "manual_only"))
      || (verdict === "reject" && category === "recommended");
  });

  const explainedStep = explaining === null
    ? null
    : steps.find((step) => step.step_id === explaining) || null;

  const submit = () => {
    if (!canProceed) return;

    // The structured override reasons ride along with the free-text note, so
    // the backend's plan-decision feedback keeps them without a schema change.
    const overrideNote = overrides
      .map((step) => {
        const reason = overrideReasons[step.step_id];
        const verdict = decisions[step.step_id] === "approve" ? "approved" : "rejected";
        return `step ${step.step_id} (${step.refactoring}) ${verdict} against DIWO's ${categoryOf(step)}${reason ? `: ${reason}` : ""}`;
      })
      .join("; ");

    onApprove({
      decisions,
      opinion: [opinion, overrideNote && `Overrides — ${overrideNote}`]
        .filter(Boolean).join(" | "),
      plan: currentPlan,
      preferences: {
        developer_strategy: strategy,
        ...({
          safety_first: { risk_tolerance: "conservative", impact_focus: "medium" },
          balanced: { risk_tolerance: "balanced", impact_focus: "high" },
          max_improvement: { risk_tolerance: "aggressive", impact_focus: "high" },
        }[strategy]),
      },
      override_reasons: overrideReasons,
    });
  };

  return (
    <div>
      <SourceBanner origin={origin} meta={planMeta} />

      <PlanningDecisionSummary
        summary={summary}
        totalSteps={steps.length}
        approved={approved}
        rejected={rejected}
        pending={pending}
        strategy={strategy}
        onStrategyChange={applyStrategy}
        strategyBusy={loading}
        onSelectRecommended={selectRecommended}
        onSelectAll={selectAll}
        onClearSelection={clearDecisions}
        activeFilter={filter}
        onFilterCategory={(category) =>
          setFilter((prev) => (prev === category ? "all" : category))}
        planSource={planMeta?.plan_source}
      />

      {/* The RDP plan's own identity, kept intact: DIWO's assessment sits
          above it, it does not replace it. */}
      <Card style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Refactoring Planning Agent Output</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 6 }}>
              {currentPlan.plan_id}
              {currentPlan.target && (
                <span style={{ fontSize: 12, fontWeight: 500, color: C.textMuted, marginLeft: 10, fontFamily: "monospace" }}>
                  {currentPlan.target}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: C.textSub, maxWidth: 620 }}>{summaryText}</div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {refactoringTypes.map(t => <Badge key={t} label={t} color={C.info} />)}
          </div>
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

      {/* ── Filters ─────────────────────────────────────────────────────────
          Status here; recommendation categories are the counters in the header
          above, which double as filters. Two rows of pills would crowd the
          control strip for no extra reach. */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
        {STATUS_FILTERS.map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, cursor: "pointer", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted, border: `1px solid ${filter === f ? C.accent : C.border}`
          }}>{f}</button>
        ))}

        {CATEGORY_ORDER.includes(filter) && (
          <span style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700,
            background: `${categoryStyle(filter).color}18`,
            border: `1px solid ${categoryStyle(filter).color}`,
            color: categoryStyle(filter).color,
          }}>
            {categoryStyle(filter).icon} {categoryStyle(filter).label}
            <button onClick={() => setFilter("all")} title="Clear this filter" style={{
              background: "none", border: "none", color: "inherit", cursor: "pointer",
              marginLeft: 6, padding: 0, fontWeight: 800,
            }}>✕</button>
          </span>
        )}

        <select
          value={impactFilter}
          onChange={(e) => setImpactFilter(e.target.value)}
          aria-label="Filter by expected impact"
          style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}`, marginLeft: "auto" }}
        >
          <option value="any">Impact: any</option>
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
            overrideReasons={overrideReasons}
            onDecide={decide}
            onDecideGroup={decideGroup}
            onExplain={setExplaining}
            onOverrideReason={(id, reason) =>
              setOverrideReasons((prev) => ({ ...prev, [id]: reason }))}
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

      {overrides.length > 0 && (
        <div style={{
          marginTop: 14, padding: "12px 16px", borderRadius: 10,
          background: `${C.info}0a`, border: `1px solid ${C.info}40`,
          fontSize: 12, color: C.textSub, lineHeight: 1.6,
        }}>
          <b style={{ color: C.info }}>ⓘ You went against DIWO on {overrides.length} step{overrides.length > 1 ? "s" : ""}.</b>
          {" "}That is exactly what this stage is for — DIWO advises, you decide.
          A one-word reason on those steps (optional) helps DIWO learn where its
          recommendations do not fit how you work.
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

      {/* Mounted only while a step is being explained, so a twelve-step plan
          never builds twelve score breakdowns nobody opened. Looked up against
          `steps` rather than `filtered`: changing a filter must not yank the
          dialog out from under the developer reading it. */}
      {explainedStep && (
        <PlanStepDrawer
          // Keyed by step, so opening a different one mounts a fresh dialog
          // scrolled back to the top rather than mid-way down the last.
          key={explainedStep.step_id}
          step={explainedStep}
          decision={decisions[explainedStep.step_id]}
          onDecide={decide}
          onClose={() => setExplaining(null)}
        />
      )}
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
        fontSize: 16, fontWeight: 700, color, letterSpacing: 1,
        textTransform: "uppercase", flexShrink: 0,
      }}>
        File Path
      </span>
      <span
        title={file}
        style={{
          fontSize: 16, fontWeight: 700, color: C.text, fontFamily: "monospace",
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
 *
 * When the file's steps do not all carry the same recommendation, the mix is
 * spelled out above the "All ✓" button. Approving four steps at once must not
 * imply that all four are equally safe when one of them is manual-only.
 */
function PlanFileGroup({
  group, decisions, overrideReasons,
  onDecide, onDecideGroup, onExplain, onOverrideReason,
}) {
  const total = group.steps.length;
  const approved = group.steps.filter(s => decisions[s.step_id] === "approve").length;
  const rejected = group.steps.filter(s => decisions[s.step_id] === "reject").length;
  const pending = total - approved - rejected;

  const allApproved = approved === total;
  const allRejected = rejected === total;
  const borderColor = pending > 0 ? C.border : approved > 0 ? C.accent : C.danger;
  const color = FILE_ACCENT;
  const breakdown = groupBreakdown(group.steps);

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
            title={
              breakdown.mixed
                ? `Approve all ${total} step(s) for this file — they do NOT all carry the same recommendation`
                : `Approve all ${total} step(s) planned for this file`
            }
            style={{
              padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
              background: allApproved ? C.accent : `${C.accent}18`, color: allApproved ? "#000" : C.accent,
              transition: "all 0.2s",
            }}
          >
           All ✓
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
            All ✕
          </button>
        </div>
      </FilePathBar>

      {/* §47: what "All ✓" is actually about to approve. */}
      {breakdown.mixed && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "7px 16px", background: `${C.warn}0a`,
          borderBottom: `1px solid ${C.border}`, fontSize: 11, color: C.textSub,
        }}>
          <span style={{ color: C.warn, fontWeight: 700 }}>⚠ Mixed recommendations:</span>
          {CATEGORY_ORDER.filter((c) => breakdown.counts[c] > 0).map((c) => (
            <span key={c} style={{ color: categoryStyle(c).color, fontWeight: 600 }}>
              {categoryStyle(c).icon} {breakdown.counts[c]} {categoryStyle(c).short.toLowerCase()}
            </span>
          ))}
          {breakdown.unassessed > 0 && (
            <span style={{ color: C.textMuted }}>○ {breakdown.unassessed} not assessed</span>
          )}
          <span style={{ color: C.textMuted }}>— approving the file approves all of them.</span>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column" }}>
        {group.steps.map((step, rowIdx) => (
          <PlanStepRow
            key={step.step_id}
            step={step}
            rowIdx={rowIdx}
            decision={decisions[step.step_id]}
            overrideReason={overrideReasons[step.step_id]}
            onDecide={onDecide}
            onExplain={onExplain}
            onOverrideReason={onOverrideReason}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * One planned refactoring step: a fixed-height row that answers the
 * approve/reject question and nothing more.
 *
 * The recommendation badge and score, the refactoring, impact, risk, smell
 * type, RDP's own score, SCTVA status, the target, RDP's explanation, and
 * DIWO's verdict in one sentence. That is the whole row, so twelve of them are
 * still a list a developer can scan.
 *
 * Everything else — the full reason list, the projected effect, the deferral
 * cost, the score breakdown, the transformation parameters, RDP's prediction
 * and its rejected alternatives — lives in PlanStepDrawer, opened from the
 * button below. It is taller than the viewport, so expanding it here would
 * push the rest of the plan off screen; and it is never built for a step
 * nobody opened.
 */
function PlanStepRow({
  step, rowIdx, decision, overrideReason,
  onDecide, onExplain, onOverrideReason,
}) {
  const support = supportOf(step);
  const category = support?.category || null;
  const style = categoryStyle(category);

  const bgColor =
    decision === "approve" ? `${C.accent}0a` : decision === "reject" ? `${C.danger}0a` : "transparent";
  const targetLabel =
    [step.target?.class, step.target?.method].filter(Boolean).join(".") ||
    step.target?.file ||
    "(module level)";

  // Only the capability is read here, for the SCTVA chip in the header. The
  // impact, deferral and factor figures belong to the drawer.
  const capability = support?.capability;

  const isOverride =
    (decision === "approve" && (category === "not_recommended" || category === "manual_only")) ||
    (decision === "reject" && category === "recommended");

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
            <PlanningRecommendationBadge support={support} />
            <Badge label={step.refactoring} color={C.info} />
            <Pill label={`Impact: ${step.impact || step.expected_impact || "medium"}`} color={impactColor(step.impact || step.expected_impact || "medium")} />
            <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
            {step.smell_type && (
              <Pill label={step.smell_type} color={severityColor(step.severity)} />
            )}
            {/* RDP's own score is preserved beside DIWO's, never replaced by it. */}
            {typeof step.score === "number" && (
              <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace" }} title={`RDP MCDA score (${step.scoring_method || "mcda"})`}>
                RDP {step.score.toFixed(2)}
              </span>
            )}
            {capability && (
              <span
                title={capability.reason || ""}
                style={{
                  fontSize: 10, fontFamily: "monospace", fontWeight: 700,
                  color: capability.actual_step_mappable ? C.accent : C.warn,
                }}
              >
                SCTVA {String(capability.status || "unknown").toUpperCase()}
                {capability.actual_step_mappable ? " ✓" : " ⚠"}
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

          {/* The verdict in one sentence, always visible — a badge and a number
              with nothing behind them is exactly what this stage replaced.
              Everything that BACKS it up sits behind the toggle: a card
              carrying the full reason list, the projected effect, the deferral
              cost and the score breakdown is unreadable twelve times down a
              page, and the developer only needs that depth on the steps they
              stop at. */}
          {support && (
            <div style={{
              marginTop: 9, padding: "8px 12px", borderRadius: 8,
              background: `${style.color}0a`, borderLeft: `3px solid ${style.color}`,
              fontSize: 12, color: C.textSub, lineHeight: 1.5,
            }}>
              <b style={{ color: style.color }}>{style.verb}:</b> {support.summary}
            </div>
          )}

          {/* Opens the explanation in a dialog rather than expanding here. The
              evidence is taller than the viewport, so inline expansion pushed
              every other step off screen and cost the developer their place in
              the plan. Nothing below this row moves when it is clicked. */}
          <button
            onClick={() => onExplain?.(step.step_id)}
            aria-haspopup="dialog"
            title="Open the full explanation, score breakdown and transformation details"
            style={{
              marginTop: 9, padding: "5px 12px", borderRadius: 7, cursor: "pointer",
              background: C.bg, color: C.textSub, border: `1px solid ${C.border}`,
              fontSize: 11, fontWeight: 600,
              display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            <span aria-hidden="true">ⓘ</span>
            {support ? "Why this recommendation? · Transformation details" : "Transformation details"}
          </button>

          {/* §48: optional, never blocking. */}
          {isOverride && (
            <div style={{
              marginTop: 10, padding: "9px 12px", borderRadius: 8,
              background: `${C.info}0a`, border: `1px dashed ${C.info}50`,
            }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 6 }}>
                DIWO marked this <b style={{ color: style.color }}>{style.short.toLowerCase()}</b>, and you{" "}
                {decision === "approve" ? "approved" : "rejected"} it. Optional — why?
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {OVERRIDE_REASONS.map((reason) => (
                  <button
                    key={reason}
                    onClick={() => onOverrideReason(step.step_id, overrideReason === reason ? "" : reason)}
                    style={{
                      padding: "3px 10px", borderRadius: 20, fontSize: 10.5, fontWeight: 600,
                      cursor: "pointer",
                      background: overrideReason === reason ? `${C.info}25` : C.bg,
                      color: overrideReason === reason ? C.info : C.textMuted,
                      border: `1px solid ${overrideReason === reason ? C.info : C.border}`,
                    }}
                  >
                    {reason}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 8 }}>
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

          {/* Approving a step DIWO warned about is allowed, and says so. */}
          {decision === "approve" && category === "not_recommended" && (
            <span style={{ fontSize: 10, color: C.danger, maxWidth: 150, textAlign: "right", lineHeight: 1.4 }}>
              ⚠ Approved against DIWO's advice
            </span>
          )}
          {decision === "approve" && category === "manual_only" && (
            <span style={{ fontSize: 10, color: C.info, maxWidth: 150, textAlign: "right", lineHeight: 1.4 }}>
              ⓘ Will not change code automatically
            </span>
          )}
        </div>
      </div>
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
          {origin !== "rdp" && (
            <div style={{ fontSize: 11, color: C.warn, marginTop: 2 }}>
              Recommendations for this plan were computed without RDP's scoring evidence.
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
