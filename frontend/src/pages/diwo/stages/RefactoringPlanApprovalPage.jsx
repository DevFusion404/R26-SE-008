/**
 * RefactoringPlanApprovalPage.jsx
 * ===============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 2 of the DIWO workflow: render the plan produced by the Refactoring
 * Decision & Planning (RDP) agent and let the developer decide each step before
 * it reaches the Safe Transformation Agent.
 *
 * This page does NOT call the RDP agent. Planning is owned by the DIWO
 * backend: POST /workflows/<id>/select-smells forwards the *updated* smell
 * report — the developer's selection, with deselected smells and now-empty
 * files removed — to POST http://localhost:5000/generate, and returns the
 * plan, the decision trace and which agent produced it.
 *
 * Data source, in priority order:
 *   1. `planData` — the plan the backend returned with the smell selection.
 *   2. Bundled sample plan (diwoData.PLAN_DATA) — only if the developer opts
 *      in after a failure, and it is labelled as sample data in the UI.
 *
 * The displayed plan travels back up on approve (`onApprove({ plan })`) so the
 * parent forwards the exact steps the developer saw, not whichever copy its own
 * state happens to hold.
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
 * What the page is arranged to answer, in this order:
 *
 *     What does DIWO recommend?      the category band and the badge
 *     Why?                           one sentence, always visible
 *     Can SCTVA execute it?          the Automation fact
 *     What do I want to do?          category-specific actions
 *
 * Everything BEHIND that verdict — the full reason list, what approving buys,
 * what skipping costs, the five-factor score breakdown, RDP's alternatives and
 * the transformation parameters — is one click away in PlanStepDrawer, under
 * "Why this recommendation?". None of it is on the card.
 *
 * That split is the whole layout decision. A card carrying its own approve/skip
 * consequence columns is a readable card and an unreadable page: twelve of them
 * is a wall of parallel figures, and the developer has to scroll past the
 * evidence for eleven steps to reach the twelfth. The card answers "what is
 * this and what does DIWO say"; the drawer answers "why, and what happens
 * either way" — and it answers it for the one step the developer stopped at.
 *
 * THREE THINGS THIS PAGE WILL NOT DO
 * ----------------------------------
 * 1. Approving a step is LOCAL and never regenerates the plan. It used to:
 *    every click called onDecisionChange, which POSTed /plan-preference-update,
 *    and that endpoint drops rejected steps, re-sorts the rest, renumbers
 *    step_id from 1 and returns a new plan_id. The new plan_id tripped the
 *    reset below, so the approval the developer had just made was wiped a
 *    moment after it appeared. Only a developer-goal change asks the backend to
 *    re-rank, and decisions are carried across that by step identity.
 *
 * 2. Nothing here submits a plan. `Select Recommended` sets local state on the
 *    green steps and stops: no agent call, no plan submission, no
 *    transformation. The developer still presses Forward, and a step they did
 *    not approve never leaves this page.
 *
 * 3. A manual-only step is never forwarded as an automatic transformation by
 *    any default or bulk path. It takes a third decision state — `manual` —
 *    which counts as decided and is recorded, but which build_approved_plan()
 *    on the backend leaves out of the approved plan. Forcing one through is
 *    possible, deliberately, but only through the override dialog.
 */

import { useState } from "react";
import { PLAN_DATA } from "../data/diwoData";
import { C, Card, Badge, Pill, impactColor, riskColor, severityColor } from "../diwoTheme.jsx";
import PlanningDecisionSummary from "../components/PlanningDecisionSummary";
import PlanningRecommendationBadge from "../components/PlanningRecommendationBadge";
import PlanStepDrawer from "../components/PlanStepDrawer";
import { StepFacts } from "../components/PlanConsequencePreview";
import OverrideConfirmDialog from "../components/OverrideConfirmDialog";
import {
  APPROVE, REJECT, MANUAL,
  CATEGORY_ORDER, MANUAL_ONLY, NOT_RECOMMENDED, OVERRIDE_REASONS,
  actionModel, approveRecommendedIn, carryDecisions, categoryOf, categoryStyle,
  countDecisions, decideGroup, distributionDelta, groupBreakdown, isOverride,
  planSummary, rejectAll, selectAll, selectRecommended, supportOf,
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

const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "manual", label: "Manual work" },
  { value: "rejected", label: "Rejected" },
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
  // Two independent filter axes. They were one field, which meant picking
  // "Review Carefully" silently dropped "Pending" — and "which steps still
  // need me, of the ones DIWO flagged?" is the question this stage is for.
  // Status and recommendation now intersect instead of overwriting.
  const [filter, setFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("any");
  const [impactFilter, setImpactFilter] = useState("any");
  const [groupMode, setGroupMode] = useState("recommendation");
  const [strategy, setStrategy] = useState("balanced");
  // The step whose explanation dialog is open, by step_id. One at a time — the
  // same shape Stage 1 uses for its impact drawer.
  const [explaining, setExplaining] = useState(null);
  // The step whose override confirmation is open, by step_id.
  const [confirming, setConfirming] = useState(null);
  const [useSample, setUseSample] = useState(false);
  // step_id -> short reason, for decisions that went against the recommendation.
  const [overrideReasons, setOverrideReasons] = useState({});
  // The recommendation distribution before the last goal change, so the effect
  // of changing goal can be shown instead of merely asserted.
  const [baselineSummary, setBaselineSummary] = useState(null);

  // ── Resolve the plan actually being rendered ───────────────────────────────
  const currentPlan = planData || (useSample ? PLAN_DATA : null);
  const origin = planData
    ? (planMeta?.plan_source === "rdp_agent" ? "rdp" : "workflow")
    : useSample
      ? "sample"
      : null;
  const steps = currentPlan?.steps || [];

  // Decisions are keyed by step_id, which only means anything within one plan.
  // When the plan is replaced, carry each decision over to the step it was
  // actually made on instead of dropping the lot. Adjusted during render
  // rather than in an effect — no cascading render, no stale first paint.
  const planKey = currentPlan ? `${origin}:${currentPlan.plan_id}` : null;
  const [prevPlanKey, setPrevPlanKey] = useState(planKey);
  const [prevSteps, setPrevSteps] = useState(steps);
  if (planKey !== prevPlanKey) {
    const carried = carryDecisions(prevSteps, steps, decisions, overrideReasons);
    setPrevPlanKey(planKey);
    setPrevSteps(steps);
    setDecisions(carried.decisions);
    setOverrideReasons(carried.extras);
    setExplaining(null);
    setConfirming(null);
  }

  const stepById = (id) => steps.find((step) => step.step_id === id) || null;

  /**
   * Approve / reject / mark-manual one step. Local only — never regenerates
   * the plan, never calls an agent.
   *
   * An override reason belongs to the decision that provoked it, so a verdict
   * that stops being an override drops its reason rather than carrying a
   * stale justification into the audit trail.
   */
  const decide = (id, val) => {
    setDecisions((prev) => (prev[id] === val ? prev : { ...prev, [id]: val }));

    // Two independent updates rather than one nested inside the other's
    // updater: an updater function has to be pure, and React may call it more
    // than once per commit.
    if (!isOverride(stepById(id), val)) {
      setOverrideReasons((prev) => {
        if (!prev[id]) return prev;
        const trimmed = { ...prev };
        delete trimmed[id];
        return trimmed;
      });
    }
  };

  /**
   * The single entry point for a decision made from a card or the drawer.
   *
   * Approving a step DIWO advised against goes through the confirmation first.
   * The developer is never blocked — the dialog's own button completes the
   * approval — but the click that overrides a recommendation cannot be the
   * same reflex as the click that follows one.
   */
  const requestDecision = (id, val) => {
    const step = stepById(id);
    const category = categoryOf(step);
    const actions = actionModel(category);
    if (val === APPROVE && actions.confirmApprove && decisions[id] !== APPROVE) {
      setConfirming(id);
      return;
    }
    decide(id, val);
  };

  /** Every step in a group takes the same verdict; re-clicking clears it. */
  const decideAllIn = (groupSteps, val) =>
    setDecisions((prev) => decideGroup(groupSteps, val, prev));

  /** The group-scoped form of Select Recommended (§35). */
  const approveRecommendedInGroup = (groupSteps) =>
    setDecisions((prev) => approveRecommendedIn(groupSteps, prev));

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
    // Snapshot what the plan looked like BEFORE the re-rank, so the effect of
    // the change can be shown. Captured here rather than derived later: once
    // the new plan lands, the old distribution is gone.
    setBaselineSummary(planSummary(currentPlan, strategy));
    setStrategy(next);
    onDecisionChange?.({ decisions, preferences: preferencesFor(next) });
  };

  /**
   * Approve exactly the steps the backend marked `auto_select_eligible`.
   *
   * LOCAL STATE ONLY. No RDP call, no SCTVA call, no plan submission. Review,
   * not-recommended and manual-only steps are left pending on purpose: they
   * are the ones worth reading. A step the developer has already decided is
   * left alone — see selectRecommended().
   */
  const applySelectRecommended = () =>
    setDecisions((prev) => selectRecommended(steps, prev));

  const applySelectAll = () =>
    setDecisions((prev) => selectAll(steps, prev));

  /** Reject every step. Local only, like every other bulk action here. */
  const applyRejectAll = () =>
    setDecisions((prev) => rejectAll(steps, prev));

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
  const strategyDelta = distributionDelta(baselineSummary, summary);

  const matchesStatus = (step) => {
    if (filter === "all") return true;
    if (filter === "approved") return decisions[step.step_id] === APPROVE;
    if (filter === "rejected") return decisions[step.step_id] === REJECT;
    if (filter === "manual") return decisions[step.step_id] === MANUAL;
    if (filter === "pending") return !decisions[step.step_id];
    return true;
  };

  // The recommendation the BACKEND assigned — read, never re-derived here.
  // "unassessed" is its own choice rather than being folded into a category,
  // because a step DIWO never scored is a different thing from a step it
  // scored badly, and the developer filtering for one does not want the other.
  const matchesCategory = (step) => {
    if (categoryFilter === "any") return true;
    if (categoryFilter === "unassessed") return categoryOf(step) === null;
    return categoryOf(step) === categoryFilter;
  };

  const matchesImpact = (step) =>
    impactFilter === "any" || (step.impact || step.expected_impact) === impactFilter;

  const filtered = steps.filter(
    (step) => matchesStatus(step) && matchesCategory(step) && matchesImpact(step));

  // Counts for the recommendation pills, taken over the WHOLE plan rather than
  // the filtered set: a filter row whose numbers change as you filter cannot
  // tell you what is there to filter for.
  const planBreakdown = groupBreakdown(steps);
  const anyFilterActive =
    filter !== "all" || categoryFilter !== "any" || impactFilter !== "any";

  const sections = buildSections(filtered, groupMode);
  const counts = countDecisions(steps, decisions);
  const { approved, rejected, manual, pending } = counts;
  const canProceed = approved > 0 && pending === 0;

  const refactoringTypes = [...new Set(steps.map(s => s.refactoring))];
  const summaryText = typeof currentPlan.summary === "string"
    ? currentPlan.summary
    : `Total steps: ${currentPlan.summary?.total_steps || steps.length} · High impact: ${currentPlan.summary?.high_impact || 0}`;
  const skipped = currentPlan.skipped_smells || [];

  // Steps where the developer went against the recommendation. Surfaced before
  // submit, not to argue with them, but because a disagreement is worth one
  // sentence of context for whoever reads the audit trail later.
  const overrides = steps.filter((step) => isOverride(step, decisions[step.step_id]));

  const explainedStep = explaining === null ? null : stepById(explaining);
  const confirmingStep = confirming === null ? null : stepById(confirming);

  const submit = () => {
    if (!canProceed) return;

    // The structured override reasons ride along with the free-text note, so an
    // older backend keeps them without a schema change; they are also sent as
    // their own field for the step-level feedback rows.
    const overrideNote = overrides
      .map((step) => {
        const reason = overrideReasons[step.step_id];
        const verdict = decisions[step.step_id] === APPROVE ? "approved" : "rejected";
        return `step ${step.step_id} (${step.refactoring}) ${verdict} against DIWO's ${categoryOf(step)}${reason ? `: ${reason}` : ""}`;
      })
      .join("; ");

    onApprove({
      decisions,
      opinion: [opinion, overrideNote && `Overrides — ${overrideNote}`]
        .filter(Boolean).join(" | "),
      plan: currentPlan,
      preferences: preferencesFor(strategy),
      override_reasons: overrideReasons,
    });
  };

  return (
    <div>
      <SourceBanner origin={origin} meta={planMeta} />

      {/* ── What RDP produced ───────────────────────────────────────────────
          The plan's own identity comes first, because it is the thing being
          reviewed. DIWO's assessment below is a reading OF this plan, and a
          reading is easier to trust when the reader has already seen what it
          is a reading of — plan id, target, step count, and the smells RDP
          could find no viable refactoring for.

          It also keeps the agents in the order the workflow ran them: RDP
          planned, DIWO assessed, the developer decides. */}
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

      {/* ── What DIWO makes of it ─────────────────────────────────────────── */}
      <PlanningDecisionSummary
        summary={summary}
        totalSteps={steps.length}
        approved={approved}
        rejected={rejected}
        manual={manual}
        pending={pending}
        strategy={strategy}
        onStrategyChange={applyStrategy}
        strategyBusy={loading}
        strategyDelta={strategyDelta}
        onSelectRecommended={applySelectRecommended}
        onSelectAll={applySelectAll}
        onRejectAll={applyRejectAll}
        onClearSelection={clearDecisions}
        activeFilter={categoryFilter}
        onFilterCategory={(category) =>
          setCategoryFilter((prev) => (prev === category ? "any" : category))}
        planSource={planMeta?.plan_source}
      />

      {/* ── Filters ─────────────────────────────────────────────────────────
          Two rows, because they are two different questions and the developer
          routinely asks them together: "what have I not decided yet" (status)
          and "what did DIWO flag" (recommendation). Folding both into one
          field made them mutually exclusive, so "the review-carefully steps I
          still have not decided" — the single most useful view on this page —
          could not be expressed at all. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{
            fontSize: 9.5, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 0.9, fontWeight: 700, width: 74, flexShrink: 0,
          }}>
            Decision
          </span>
          {STATUS_FILTERS.map(f => (
            <button key={f.value} onClick={() => setFilter(f.value)} style={{
              padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, cursor: "pointer",
              background: filter === f.value ? C.accent : C.panel,
              color: filter === f.value ? "#000" : C.textMuted,
              border: `1px solid ${filter === f.value ? C.accent : C.border}`,
            }}>{f.label}</button>
          ))}

          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            <select
              value={groupMode}
              onChange={(e) => setGroupMode(e.target.value)}
              aria-label="Group plan steps by"
              style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}` }}
            >
              <option value="recommendation">Group: recommendation</option>
              <option value="file">Group: file</option>
            </select>
            <select
              value={impactFilter}
              onChange={(e) => setImpactFilter(e.target.value)}
              aria-label="Filter by expected impact"
              style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}` }}
            >
              <option value="any">Impact: any</option>
              <option value="high">Impact: High</option>
              <option value="medium">Impact: Medium</option>
              <option value="low">Impact: Low</option>
            </select>
          </div>
        </div>

        <RecommendationFilterBar
          value={categoryFilter}
          onChange={setCategoryFilter}
          breakdown={planBreakdown}
          total={steps.length}
        />

        {anyFilterActive && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ width: 74, flexShrink: 0 }} />
            <button
              onClick={() => { setFilter("all"); setCategoryFilter("any"); setImpactFilter("any"); }}
              style={{
                padding: "3px 11px", borderRadius: 20, fontSize: 10.5, fontWeight: 600,
                cursor: "pointer", background: "none", color: C.textMuted,
                border: `1px solid ${C.border}`,
              }}
            >
              Clear filters
            </button>
            <span style={{ fontSize: 10.5, color: C.textMuted }}>
              Filters only change what is shown — no decision is altered.
            </span>
          </div>
        )}
      </div>

      {/* Height-capped column: the group cards must not shrink, or a file with
          many steps would be squeezed and its last rows clipped by the card's
          own overflow:hidden instead of scrolling here. */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 14,
        maxHeight: "min(74vh, 820px)", overflowY: "auto", paddingRight: 4,
      }}>
        {filtered.length === 0 && (
          <div style={{ padding: "28px 20px", textAlign: "center", background: C.panel, border: `1px dashed ${C.border}`, borderRadius: 10, color: C.textMuted, fontSize: 13, flexShrink: 0 }}>
            {steps.length === 0
              ? "The Refactoring Planning Agent produced no steps for this report — every smell was skipped. Fall back to Smell Review and select different files."
              : categoryFilter !== "any"
                ? `No ${categoryFilter === "unassessed" ? "unassessed" : categoryStyle(categoryFilter).short.toLowerCase()} step matches the other filters.`
                : "No steps match the current filter."}
          </div>
        )}

        {sections.map(section => (
          <CategorySection
            key={section.key}
            section={section}
            grouped={groupMode === "recommendation"}
            decisions={decisions}
            overrideReasons={overrideReasons}
            onDecide={requestDecision}
            onDecideAllIn={decideAllIn}
            onApproveRecommendedIn={approveRecommendedInGroup}
            onExplain={setExplaining}
            onOverrideReason={(id, reason) =>
              setOverrideReasons((prev) => ({ ...prev, [id]: reason }))}
          />
        ))}
      </div>

      {filtered.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted }}>
          Showing {filtered.length} step{filtered.length > 1 ? "s" : ""} in {sections.length}{" "}
          {groupMode === "recommendation" ? "recommendation group" : "file"}
          {sections.length > 1 ? "s" : ""}
          {filtered.length < steps.length && ` (${steps.length - filtered.length} hidden by the current filter)`}.
        </div>
      )}

      {manual > 0 && (
        <div style={{
          marginTop: 14, padding: "12px 16px", borderRadius: 10,
          background: `${C.info}0a`, border: `1px solid ${C.info}40`,
          fontSize: 12, color: C.textSub, lineHeight: 1.6,
        }}>
          <b style={{ color: C.info }}>🔧 {manual} step{manual > 1 ? "s" : ""} marked for manual work.</b>
          {" "}These stay on your list and are recorded with the plan, but they are
          <b> not</b> forwarded to the Transformation Agent — SCTVA has no safe
          automatic form for them, so approving them would send a step it cannot execute.
        </div>
      )}

      {overrides.length > 0 && (
        <div style={{
          marginTop: 10, padding: "12px 16px", borderRadius: 10,
          background: `${C.warn}0a`, border: `1px solid ${C.warn}40`,
          fontSize: 12, color: C.textSub, lineHeight: 1.6,
        }}>
          <b style={{ color: C.warn }}>ⓘ You went against DIWO on {overrides.length} step{overrides.length > 1 ? "s" : ""}.</b>
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
            ⚠ Decide the {pending} remaining step{pending > 1 ? "s" : ""} to proceed
          </div>
        )}
        {!canProceed && pending === 0 && approved === 0 && (
          <div style={{ display: "flex", alignItems: "center", fontSize: 12, color: C.warn }}>
            ⚠ At least one step must be approved for automatic transformation
          </div>
        )}
        <button onClick={submit} disabled={!canProceed} title={
          `${approved} approved step(s) will be transformed by SCTVA` +
          (manual > 0 ? `; ${manual} marked for manual work are not sent` : "") +
          (rejected > 0 ? `; ${rejected} rejected step(s) are dropped` : "") + "."
        } style={{
          padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: canProceed ? "pointer" : "not-allowed",
          background: canProceed ? C.accent : C.border, color: canProceed ? "#000" : C.textMuted, border: "none",
          boxShadow: canProceed ? `0 0 20px ${C.accentGlow}` : "none", transition: "all 0.2s"
        }}>
          Forward {approved} Approved Step{approved === 1 ? "" : "s"} to Transformation →
          {(rejected > 0 || manual > 0) && (
            <span style={{ fontWeight: 500, opacity: 0.75 }}>
              {" "}({[rejected > 0 && `${rejected} rejected`, manual > 0 && `${manual} manual`]
                .filter(Boolean).join(", ")}, not sent)
            </span>
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
          onDecide={requestDecision}
          onClose={() => setExplaining(null)}
        />
      )}

      {confirmingStep && (
        <OverrideConfirmDialog
          key={confirmingStep.step_id}
          step={confirmingStep}
          support={supportOf(confirmingStep)}
          reason={overrideReasons[confirmingStep.step_id]}
          onReason={(id, reason) =>
            setOverrideReasons((prev) => {
              const next = { ...prev };
              if (reason) next[id] = reason;
              else delete next[id];
              return next;
            })}
          onConfirm={(id) => {
            setDecisions((prev) => ({ ...prev, [id]: APPROVE }));
            setConfirming(null);
          }}
          onCancel={() => setConfirming(null)}
        />
      )}
    </div>
  );
}

// ─── Grouping ────────────────────────────────────────────────────────────────

/**
 * Group the visible steps for display.
 *
 * "recommendation" gives the two-level arrangement: category section → file →
 * steps. Grouping by category first is what makes the recommendation load
 * bearing — a flat list of twelve cards makes "6 recommended, 3 to review" a
 * claim the developer has to verify by reading every card, while a section
 * headed "Recommended (6)" is the claim and the evidence at once.
 *
 * "file" keeps the original arrangement, which is still the better one when the
 * developer is reasoning about a particular class rather than about the plan.
 * The file level is preserved inside the category sections either way, so the
 * path is never lost.
 */
function buildSections(steps, mode) {
  const byFile = (list) => {
    const groups = [];
    const index = new Map();
    list.forEach((step) => {
      const file = step.target?.file || "(module level)";
      let group = index.get(file);
      if (!group) {
        group = { file, steps: [] };
        index.set(file, group);
        groups.push(group);
      }
      group.steps.push(step);
    });
    return groups;
  };

  if (mode === "file") {
    return byFile(steps).map((group) => ({
      key: `file:${group.file}`,
      category: null,
      files: [group],
      steps: group.steps,
    }));
  }

  // Category order first, then anything the backend did not assess.
  const order = [...CATEGORY_ORDER, null];
  return order
    .map((category) => {
      const inCategory = steps.filter((step) => categoryOf(step) === category);
      if (inCategory.length === 0) return null;
      return {
        key: `cat:${category || "unassessed"}`,
        category,
        files: byFile(inCategory),
        steps: inCategory,
      };
    })
    .filter(Boolean);
}

// ─── Sub-components ──────────────────────────────────────────────────────────

/**
 * One recommendation category, with the files it touches inside it.
 *
 * The section header carries the count and the bulk action appropriate to the
 * category: Recommended offers "Approve all N", Manual offers "Add all N to
 * manual work", and Not Recommended offers "Reject all N". Not Recommended is
 * deliberately given no bulk approve — a one-click "approve everything DIWO
 * advised against" is the exact affordance this redesign removed from the top
 * of the page.
 */
function CategorySection({
  section, grouped, decisions, overrideReasons,
  onDecide, onDecideAllIn, onApproveRecommendedIn, onExplain, onOverrideReason,
}) {
  const style = categoryStyle(section.category);
  const total = section.steps.length;
  const counts = countDecisions(section.steps, decisions);

  const bulk = [];
  if (section.category === MANUAL_ONLY) {
    bulk.push({ label: `Add all ${total} to manual work`, verdict: MANUAL, tone: C.info });
    bulk.push({ label: "Skip all", verdict: REJECT, tone: C.danger });
  } else if (section.category === NOT_RECOMMENDED) {
    bulk.push({ label: `Reject all ${total}`, verdict: REJECT, tone: C.danger });
  } else if (section.category) {
    bulk.push({ label: `Approve all ${total}`, verdict: APPROVE, tone: C.accent });
    bulk.push({ label: "Reject all", verdict: REJECT, tone: C.danger });
  }

  return (
    <div style={{ flexShrink: 0 }}>
      {grouped && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "9px 14px", marginBottom: 8, borderRadius: 9,
          background: `${style.color}0d`, border: `1px solid ${style.color}40`,
        }}>
          <span aria-hidden="true" style={{ fontSize: 13 }}>{style.sectionIcon}</span>
          <span style={{ fontSize: 13, fontWeight: 800, color: style.color }}>
            {style.section}
          </span>
          <span style={{
            fontFamily: "monospace", fontSize: 12, fontWeight: 800, color: style.color,
            background: `${style.color}18`, padding: "1px 8px", borderRadius: 20,
          }}>
            {total}
          </span>
          <span style={{ fontSize: 11, color: C.textMuted, flex: "1 1 220px", minWidth: 160 }}>
            {style.blurb}
          </span>

          <span style={{ fontSize: 10.5, color: C.textMuted, display: "flex", gap: 9 }}>
            {counts.approved > 0 && <span style={{ color: C.accent }}>{counts.approved} approved</span>}
            {counts.manual > 0 && <span style={{ color: C.info }}>{counts.manual} manual</span>}
            {counts.rejected > 0 && <span style={{ color: C.danger }}>{counts.rejected} rejected</span>}
            {counts.pending > 0 && <span style={{ color: C.warn }}>{counts.pending} pending</span>}
          </span>

          <span style={{ display: "flex", gap: 7, flexShrink: 0 }}>
            {bulk.map((action) => (
              <button
                key={action.label}
                type="button"
                onClick={() => onDecideAllIn(section.steps, action.verdict)}
                title={`${action.label} — a local decision; nothing is submitted`}
                style={{
                  padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700,
                  cursor: "pointer", border: `1px solid ${action.tone}40`,
                  background: `${action.tone}14`, color: action.tone,
                }}
              >
                {action.label}
              </button>
            ))}
          </span>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {section.files.map((group) => (
          <PlanFileGroup
            key={group.file}
            group={group}
            showBreakdown={!grouped}
            decisions={decisions}
            overrideReasons={overrideReasons}
            onDecide={onDecide}
            onDecideAllIn={onDecideAllIn}
            onApproveRecommendedIn={onApproveRecommendedIn}
            onExplain={onExplain}
            onOverrideReason={onOverrideReason}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * Filter by the recommendation DIWO assigned.
 *
 *     RECOMMENDATION  [All 12] [🟢 Recommended 6] [🟡 Review 3] …
 *
 * The counts are over the whole plan, not the filtered view, so the row
 * doubles as a legend: it says what this plan contains before the developer
 * commits to looking at any of it. A category the plan has none of is shown
 * disabled rather than hidden — "there are no red steps here" is worth
 * knowing, and a row whose buttons come and go between plans is harder to aim
 * at than one that does not move.
 *
 * Every count comes from the categories the BACKEND assigned. This component
 * groups them; it never decides one.
 */
function RecommendationFilterBar({ value, onChange, breakdown, total }) {
  const options = [
    { value: "any", icon: "◈", label: "All", count: total, color: C.textSub },
    ...CATEGORY_ORDER.map((category) => ({
      value: category,
      icon: categoryStyle(category).icon,
      label: categoryStyle(category).short,
      count: breakdown.counts[category],
      color: categoryStyle(category).color,
    })),
  ];

  // Only offered when the plan actually holds steps DIWO could not assess.
  if (breakdown.unassessed > 0) {
    options.push({
      value: "unassessed", icon: "○", label: "Not assessed",
      count: breakdown.unassessed, color: C.textMuted,
    });
  }

  return (
    <div role="radiogroup" aria-label="Filter by DIWO recommendation"
         style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <span style={{
        fontSize: 9.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700, width: 74, flexShrink: 0,
      }}>
        DIWO says
      </span>

      {options.map((option) => {
        const selected = value === option.value;
        const empty = option.value !== "any" && !option.count;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={empty}
            // Clicking the active one clears it, so the row needs no separate
            // reset control of its own.
            onClick={() => onChange(selected ? "any" : option.value)}
            title={
              empty
                ? `This plan has no ${option.label.toLowerCase()} step`
                : `Show only the ${option.count} ${option.label.toLowerCase()} step(s)`
            }
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 12px", borderRadius: 20,
              fontSize: 11, fontWeight: selected ? 800 : 600,
              cursor: empty ? "not-allowed" : "pointer",
              background: selected ? `${option.color}26` : C.panel,
              color: selected ? option.color : C.textMuted,
              border: `1px solid ${selected ? option.color : C.border}`,
              opacity: empty ? 0.4 : 1,
              transition: "all 0.15s",
            }}
          >
            {/* Never colour alone: the icon and the word carry it too. */}
            <span aria-hidden="true">{option.icon}</span>
            <span>{option.label}</span>
            <span style={{
              fontFamily: "monospace", fontWeight: 800,
              color: selected ? option.color : C.textSub,
            }}>
              {option.count ?? 0}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** The file path banner heading each group. Sticky, so the path stays visible
 *  while scrolling a file with many steps. */
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
        fontSize: 13, fontWeight: 700, color, letterSpacing: 1,
        textTransform: "uppercase", flexShrink: 0,
      }}>
        File
      </span>
      <span
        title={file}
        style={{
          fontSize: 14, fontWeight: 700, color: C.text, fontFamily: "monospace",
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
 * One file's planned steps: a sticky file header, then the step cards.
 *
 * The header's primary bulk action is "Approve recommended" — the same rule as
 * the plan-level button, scoped to this file. "All ✓" survives beside it as the
 * secondary, because a file whose steps are all green is a real case, but when
 * the file's steps do NOT all carry the same recommendation the mix is spelled
 * out first: approving four steps at once must not imply that all four are
 * equally safe when one of them is manual-only.
 */
function PlanFileGroup({
  group, showBreakdown, decisions, overrideReasons,
  onDecide, onDecideAllIn, onApproveRecommendedIn, onExplain, onOverrideReason,
}) {
  const total = group.steps.length;
  const counts = countDecisions(group.steps, decisions);
  const breakdown = groupBreakdown(group.steps);

  const allApproved = counts.approved === total;
  const allRejected = counts.rejected === total;
  const borderColor = counts.pending > 0
    ? C.border
    : counts.approved > 0 ? C.accent : counts.manual > 0 ? C.info : C.danger;

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${borderColor}`,
      borderRadius: 10, overflow: "hidden", flexShrink: 0,
      boxShadow: counts.pending === 0 && counts.approved > 0 ? `0 0 12px ${C.accentGlow}` : "none",
      transition: "all 0.2s",
    }}>
      <FilePathBar file={group.file} color={FILE_ACCENT}>
        <Badge label={`${total} step${total > 1 ? "s" : ""}`} color={C.info} />
        <span style={{ marginLeft: "auto", fontSize: 11, flexShrink: 0, display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ color: counts.approved > 0 ? C.accent : C.textMuted }}>{counts.approved}/{total} approved</span>
          {counts.manual > 0 && <span style={{ color: C.info }}>{counts.manual} manual</span>}
          {counts.rejected > 0 && <span style={{ color: C.danger }}>{counts.rejected} rejected</span>}
          {counts.pending > 0 && <span style={{ color: C.warn }}>{counts.pending} pending</span>}
        </span>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          {/* Primary: only what DIWO can vouch for. */}
          {breakdown.autoSelectable > 0 && breakdown.autoSelectable < total && (
            <button
              onClick={() => onApproveRecommendedIn(group.steps)}
              title={`Approve the ${breakdown.autoSelectable} recommended step(s) in this file, leaving the rest for you to read`}
              style={{
                padding: "6px 13px", borderRadius: 7, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "none",
                background: C.accent, color: "#000",
              }}
            >
              ✓ Approve {breakdown.autoSelectable} recommended
            </button>
          )}
          <button
            onClick={() => onDecideAllIn(group.steps, APPROVE)}
            title={
              breakdown.mixed
                ? `Approve all ${total} step(s) for this file — they do NOT all carry the same recommendation`
                : `Approve all ${total} step(s) planned for this file`
            }
            style={{
              padding: "6px 13px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer",
              border: `1px solid ${C.accent}40`,
              background: allApproved ? C.accent : `${C.accent}14`, color: allApproved ? "#000" : C.accent,
              transition: "all 0.2s",
            }}
          >
            All ✓
          </button>
          <button
            onClick={() => onDecideAllIn(group.steps, REJECT)}
            title={`Reject all ${total} step(s) planned for this file`}
            style={{
              padding: "6px 13px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer",
              border: `1px solid ${C.danger}40`,
              background: allRejected ? C.danger : `${C.danger}14`, color: allRejected ? "#fff" : C.danger,
              transition: "all 0.2s",
            }}
          >
            All ✕
          </button>
        </div>
      </FilePathBar>

      {/* What "All ✓" is actually about to approve. Only needed when the file
          groups steps of different categories together — inside a category
          section they are all the same by construction. */}
      {showBreakdown && breakdown.mixed && (
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
          <PlanStepCard
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
 * One planned refactoring step.
 *
 * Two columns. The left one answers, in order: what DIWO says, what the step
 * is, the four facts that decide it, and why DIWO says it in one sentence. The
 * right one is the verdict, in a fixed-width column so that every card in the
 * plan puts Approve and Reject in the same place — the evidence varies in
 * height from step to step, and buttons that move with it make a twelve-step
 * plan a hunt.
 *
 * What is NOT on it: the approve/skip consequence columns, the five-factor
 * score breakdown, the full reason list, the transformation parameters, RDP's
 * prediction and its rejected alternatives. Those are the evidence behind the
 * recommendation, they are together taller than the viewport, and a developer
 * needs them on the two or three steps they stop at — not on all twelve. They
 * live in PlanStepDrawer, opened by "Why this recommendation?", and nothing
 * below this card moves when it opens.
 */
function PlanStepCard({ step, rowIdx, decision, overrideReason, onDecide, onExplain, onOverrideReason }) {
  const support = supportOf(step);
  const category = support?.category || null;
  const style = categoryStyle(category);
  const actions = actionModel(category);

  const decisionColor =
    decision === APPROVE ? C.accent : decision === REJECT ? C.danger : decision === MANUAL ? C.info : null;

  const targetLabel =
    [step.target?.class, step.target?.method].filter(Boolean).join(".") ||
    step.target?.file ||
    "(module level)";

  const override = isOverride(step, decision);

  // The right column holds verdicts only. "Review details" is an action in the
  // model for the review category — it is what that card should lead with —
  // but it opens the same drawer as the "Why this recommendation?" link below,
  // so it is rendered there once rather than as two buttons doing one thing.
  const verdictKinds = actions.order.filter((kind) => kind !== "explain");
  const explainLeads = actions.primary === "explain";

  return (
    <div style={{
      padding: "14px 18px", flexShrink: 0,
      background: decisionColor ? `${decisionColor}0a` : "transparent",
      borderTop: rowIdx > 0 ? `1px solid ${C.border}` : "none",
      borderLeft: `3px solid ${decisionColor || "transparent"}`,
      transition: "all 0.2s",
    }}>
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
      {/* ── Evidence, left ────────────────────────────────────────────────── */}
      <div style={{ flex: "1 1 380px", minWidth: 0 }}>
      {/* ── What DIWO says, first ─────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>Step {step.step_id}</span>
        <PlanningRecommendationBadge support={support} />
        {decision && (
          <span style={{
            fontSize: 10, fontWeight: 800, letterSpacing: 0.7, textTransform: "uppercase",
            padding: "2px 9px", borderRadius: 20,
            background: `${decisionColor}1e`, color: decisionColor,
            border: `1px solid ${decisionColor}55`,
          }}>
            {decision === APPROVE ? "✓ Approved" : decision === REJECT ? "✕ Rejected" : "🔧 Manual work"}
          </span>
        )}
        {override && (
          <span style={{ fontSize: 10, fontWeight: 700, color: C.warn }}>
            ⚠ Against DIWO's advice
          </span>
        )}
      </div>

      {/* ── What the step is ──────────────────────────────────────────────── */}
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text, marginBottom: 3 }}>
        {step.smell_type ? `${step.smell_type} → ` : ""}{step.refactoring}
      </div>
      <div style={{ fontSize: 11.5, color: C.textMuted, fontFamily: "monospace", marginBottom: 9 }}>
        {targetLabel}
        {Array.isArray(step.target?.lines) && step.target.lines.length > 0 && (
          <span style={{ marginLeft: 8 }}>L{step.target.lines.join("-")}</span>
        )}
        {typeof step.score === "number" && (
          <span style={{ marginLeft: 10 }} title={`RDP MCDA score (${step.scoring_method || "mcda"})`}>
            · RDP {step.score.toFixed(2)}
          </span>
        )}
      </div>

      {/* ── The four facts ────────────────────────────────────────────────── */}
      {support
        ? <StepFacts step={step} support={support} />
        : <UnassessedFallback step={step} />}

      {/* ── Why, in one sentence ──────────────────────────────────────────── */}
      {support && (
        <div style={{
          marginTop: 9, padding: "8px 12px", borderRadius: 8,
          background: `${style.color}0a`, borderLeft: `3px solid ${style.color}`,
          fontSize: 12, color: C.textSub, lineHeight: 1.5,
        }}>
          <b style={{ color: style.color }}>{style.verb}:</b> {support.summary}
        </div>
      )}

      {/* ── The way in to the evidence ────────────────────────────────────
             Emphasised on a Review Carefully card, where reading IS the
             recommended next action and approving without opening it is the
             habit this stage exists to interrupt. */}
      <button
        onClick={() => onExplain?.(step.step_id)}
        aria-haspopup="dialog"
        title="Open the full explanation, what approving buys, what skipping costs, the score breakdown and the transformation details"
        style={{
          marginTop: 10, padding: explainLeads ? "7px 14px" : "6px 12px",
          borderRadius: 7, cursor: "pointer",
          background: explainLeads ? `${C.warn}18` : C.bg,
          color: explainLeads ? C.warn : C.textSub,
          border: `1px solid ${explainLeads ? C.warn : C.border}`,
          fontSize: explainLeads ? 12 : 11,
          fontWeight: explainLeads ? 700 : 600,
          display: "inline-flex", alignItems: "center", gap: 6,
        }}
      >
        <span aria-hidden="true">ⓘ</span>
        {!support
          ? "Transformation details"
          : explainLeads
            ? (actions.explainLabel || "Review details")
            : "Why this recommendation?"}
      </button>

      {/* Optional, never blocking. Shown inline for a rejection of a green
          step; an approval of a red one collects it in the confirm dialog. */}
      {override && (
        <div style={{
          marginTop: 10, padding: "9px 12px", borderRadius: 8,
          background: `${C.info}0a`, border: `1px dashed ${C.info}50`,
        }}>
          <div style={{ fontSize: 11, color: C.textSub, marginBottom: 6 }}>
            DIWO marked this <b style={{ color: style.color }}>{style.short.toLowerCase()}</b>, and you{" "}
            {decision === APPROVE ? "approved" : "rejected"} it. Optional — why?
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {OVERRIDE_REASONS.map((reason) => (
              <button
                key={reason}
                onClick={() => onOverrideReason(step.step_id, overrideReason === reason ? "" : reason)}
                aria-pressed={overrideReason === reason}
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

      {/* ── Decision, right ────────────────────────────────────────────────
             Every card puts its verdicts in the same place at the same width,
             so a developer working down a twelve-step plan is not re-locating
             the buttons on each card as the evidence above them changes
             height. The column wraps underneath on a narrow viewport rather
             than squeezing the evidence into a ribbon of one-word lines. */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 7,
        flexShrink: 0, width: 190, minWidth: 190,
      }}>
        <div style={{
          fontSize: 9, color: C.textMuted, textTransform: "uppercase",
          letterSpacing: 0.9, fontWeight: 700,
        }}>
          Your decision
        </div>

        {verdictKinds.map((kind) => (
          <StepActionButton
            key={kind}
            kind={kind}
            actions={actions}
            primary={actions.primary === kind}
            decision={decision}
            block
            onClick={() => onDecide(step.step_id, kind)}
          />
        ))}

        {/* Deciding against the advice is allowed, and says so. */}
        {decision === APPROVE && category === NOT_RECOMMENDED && (
          <span style={{ fontSize: 10, color: C.danger, lineHeight: 1.4 }}>
            &#9888; Approved against DIWO advice
          </span>
        )}
        {decision === APPROVE && category === MANUAL_ONLY && (
          <span style={{ fontSize: 10, color: C.warn, lineHeight: 1.4 }}>
            &#9888; SCTVA cannot execute this &mdash; it may change no code
          </span>
        )}
        {decision === MANUAL && (
          <span style={{ fontSize: 10, color: C.info, lineHeight: 1.4 }}>
            &#9432; Yours to do by hand &mdash; not sent to SCTVA
          </span>
        )}
      </div>
    </div>
    </div>
  );
}

/**
 * One action button, styled by whether it leads for this category.
 *
 * The primary action is filled; the rest are outlined. That difference is the
 * mechanism by which the recommendation reaches the developer's hands rather
 * than only their eyes: on a red card the filled button is Reject, on a blue
 * one it is "Add to manual work", and approving is still right there but is no
 * longer the path of least resistance.
 */
function StepActionButton({ kind, actions, primary, decision, block = false, onClick }) {
  const spec = {
    [APPROVE]: { label: actions.approveLabel || "✓ Approve", tone: C.accent, on: "#000" },
    [REJECT]: { label: actions.rejectLabel || "✕ Reject", tone: C.danger, on: "#fff" },
    [MANUAL]: { label: actions.manualLabel || "🔧 Manual work", tone: C.info, on: "#fff" },
    explain: { label: actions.explainLabel || "Review details", tone: C.warn, on: "#000" },
  }[kind];

  if (!spec) return null;

  const active = kind !== "explain" && decision === kind;

  return (
    <button
      type="button"
      onClick={onClick}
      title={
        kind === APPROVE && actions.confirmApprove
          ? "DIWO advised against this — you will be asked to confirm"
          : undefined
      }
      style={{
        padding: primary ? "8px 16px" : "7px 13px",
        borderRadius: 7,
        fontSize: 12,
        fontWeight: 700,
        cursor: "pointer",
        width: block ? "100%" : undefined,
        textAlign: "center",
        border: active || primary ? "none" : `1px solid ${spec.tone}40`,
        background: active ? spec.tone : primary ? `${spec.tone}22` : `${spec.tone}10`,
        color: active ? spec.on : spec.tone,
        boxShadow: primary && !active ? `inset 0 0 0 1px ${spec.tone}66` : "none",
        opacity: primary ? 1 : 0.92,
        transition: "all 0.2s",
      }}
    >
      {spec.label}
    </button>
  );
}

/**
 * A step the backend produced no recommendation for.
 *
 * Stage 2 has to render whether or not the assessment ran — an older backend,
 * the bundled sample plan, or an enrichment that failed. The card falls back to
 * the RDP evidence it does have and says plainly that the recommendation is
 * missing, rather than showing an empty badge that could be read as "no
 * concerns".
 */
function UnassessedFallback({ step }) {
  return (
    <div style={{
      padding: "9px 12px", borderRadius: 8,
      background: C.bg, border: `1px dashed ${C.border}`,
    }}>
      <div style={{ fontSize: 11.5, color: C.textMuted, lineHeight: 1.5, marginBottom: 7 }}>
        <b style={{ color: C.textSub }}>DIWO decision support is unavailable for this step.</b>{" "}
        Review the RDP evidence below manually.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Pill
          label={`Impact: ${step.impact || step.expected_impact || "medium"}`}
          color={impactColor(step.impact || step.expected_impact || "medium")}
        />
        <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
        {step.smell_type && <Pill label={step.smell_type} color={severityColor(step.severity)} />}
        {typeof step.score === "number" && (
          <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
            RDP {step.score.toFixed(2)}
          </span>
        )}
      </div>
      {step.explanation && (
        <div style={{ fontSize: 11.5, color: C.textSub, lineHeight: 1.55, marginTop: 7 }}>
          {step.explanation}
        </div>
      )}
    </div>
  );
}

/** Goal → the preference pair the backend re-ranker has always taken. */
function preferencesFor(strategy) {
  return {
    developer_strategy: strategy,
    ...({
      safety_first: { risk_tolerance: "conservative", impact_focus: "medium" },
      balanced: { risk_tolerance: "balanced", impact_focus: "high" },
      max_improvement: { risk_tolerance: "aggressive", impact_focus: "high" },
    }[strategy] || { risk_tolerance: "balanced", impact_focus: "high" }),
  };
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
