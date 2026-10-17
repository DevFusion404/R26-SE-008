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
 * The developer works one step at a time: the navigator on the left lists
 * the plan, the workspace on the right shows the open step's full evidence —
 * the smell, the proposed change and its parameters, RDP's score and
 * alternatives, DIWO's reasons, what approving buys and what skipping costs,
 * the five-factor score, SCTVA capability and the developer's own history —
 * with the decision bar pinned underneath it.
 *
 * THREE THINGS THIS PAGE WILL NOT DO
 * ----------------------------------
 * 1. Approving a step is LOCAL and never regenerates the plan. It used to:
 *    every click called onDecisionChange, which POSTed /plan-preference-update,
 *    and that endpoint drops rejected steps, re-sorts the rest, renumbers
 *    step_id from 1 and returns a new plan_id. The new plan_id tripped the
 *    reset below, so the approval the developer had just made was wiped a
 *    moment after it appeared. If the plan is ever replaced, decisions are
 *    carried across by step identity.
 *
 * 2. Nothing here submits a plan except "Send to SCTVA", and only after the
 *    confirmation. `Select recommended` only ticks steps; every decision is
 *    local state until then, and a step the developer did not approve never
 *    leaves this page.
 *
 * 3. A manual-only step is never forwarded as an automatic transformation by
 *    any default or bulk path. It takes a third decision state — `manual` —
 *    which counts as decided and is recorded, but which build_approved_plan()
 *    on the backend leaves out of the approved plan. Forcing one through is
 *    possible, deliberately, but only through the override dialog.
 */

import { useEffect, useState } from "react";
import "../planApproval.css";
import { PLAN_DATA } from "../data/diwoData";
import { T, tint, eyebrow, ghostButton, primaryButton } from "../codeSmellTheme.js";
import PlanApprovalHeader from "../components/PlanApprovalHeader.jsx";
import PlanStepNavigator from "../components/PlanStepNavigator.jsx";
import PlanFilters from "../components/PlanFilters.jsx";
import PlanStepWorkspace from "../components/PlanStepWorkspace.jsx";
import PlanSubmitDialog from "../components/PlanSubmitDialog.jsx";
import OverrideConfirmDialog from "../components/OverrideConfirmDialog";
import {
  APPROVE, REJECT, MANUAL, RECOMMENDED,
  CATEGORY_ORDER,
  actionModel, carryDecisions, categoryOf, categoryStyle,
  countDecisions, forcedManualOnlySteps, groupBreakdown, groupStepsByFile,
  groupStepsByMethod, isOverride, methodOf, methodOptions, supportOf,
} from "../utils/planningDecisionSupport";

/**
 * LAYOUT
 * ------
 *     PlanApprovalHeader   plan identity, decision counters, Send to SCTVA,
 *                          and the filters (PlanFilters)
 *     PlanStepNavigator    bulk selection, grouped step list (left); File
 *                          wise lists each method with its files to approve
 *     PlanStepWorkspace    the open step's evidence and its decision (right)
 *     PlanSubmitDialog     "Confirm plan approval" before the hand-off
 *
 * The evidence that used to sit behind "Why this recommendation?" in a dialog
 * is the workspace now: one step is open at a time, read and decided in place.
 */

export default function RefactoringPlanApprovalPage({
  onApprove,
  onFallback,
  planData,
  planMeta,
  loading = false,
  sending = false,
  submitError = null,
  onDismissSubmitError,
}) {
  const [decisions, setDecisions] = useState({});
  const [opinion, setOpinion] = useState("");
  // Independent filter axes — status and recommendation intersect rather than
  // overwrite, so "the review-carefully steps I have not decided" is askable.
  const [filter, setFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("any");
  const [impactFilter, setImpactFilter] = useState("any");
  const [methodFilter, setMethodFilter] = useState("any");
  // Always grouped by refactoring method: the step list shows the methods the
  // plan applies, each folding out to its files.
  const groupMode = "method";
  // Method cards the developer unfolded in the navigator, keyed
  // `<section>:<method>`. Empty on load: every card starts collapsed.
  const [expanded, setExpanded] = useState(() => new Set());
  // The developer goal sent with every re-plan. The goal selector was removed
  // from this stage, so the plan is always reviewed under the balanced goal.
  const strategy = "balanced";
  // The step open in the workspace, by step_id.
  const [activeId, setActiveId] = useState(null);
  // step_id -> Technical details unfolded, remembered while moving between steps.
  const [detailsOpen, setDetailsOpen] = useState({});
  // Approvals waiting on the override confirmation, one dialog at a time. A
  // bulk approval queues every step DIWO advised against, so it gets exactly
  // the same check a single approval does.
  const [confirmQueue, setConfirmQueue] = useState([]);
  const [submitting, setSubmitting] = useState(false);
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
    setActiveId(null);
    setDetailsOpen({});
    setConfirmQueue([]);
    setSubmitting(false);
  }

  // A new step replaces the workspace content; bring its top back into view.
  useEffect(() => {
    if (activeId === null) return;
    const column = document.querySelector(".pa-detail");
    if (!column || column.getBoundingClientRect().top >= 0) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    column.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [activeId]);

  const stepById = (id) => steps.find((step) => step.step_id === id) || null;

  /** Drop an override reason once the decision it explained stops being an override. */
  const pruneReason = (id, verdict) => {
    if (isOverride(stepById(id), verdict)) return;
    setOverrideReasons((prev) => {
      if (!prev[id]) return prev;
      const trimmed = { ...prev };
      delete trimmed[id];
      return trimmed;
    });
  };

  /**
   * Approve / reject / mark-manual one step. Local only — never regenerates
   * the plan, never calls an agent.
   */
  const decide = (id, val) => {
    setDecisions((prev) => (prev[id] === val ? prev : { ...prev, [id]: val }));
    pruneReason(id, val);
  };

  /** Back to pending. */
  const clearDecision = (id) => {
    setDecisions((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    pruneReason(id, undefined);
  };

  /** True when approving this step must go through the override confirmation. */
  const needsConfirm = (id) =>
    actionModel(categoryOf(stepById(id))).confirmApprove && decisions[id] !== APPROVE;

  /**
   * The single entry point for a decision made in the workspace.
   *
   * Approving a step DIWO advised against goes through the confirmation first.
   * The developer is never blocked — the dialog's own button completes the
   * approval — but the click that overrides a recommendation cannot be the
   * same reflex as the click that follows one.
   */
  const requestDecision = (id, val) => {
    if (val === APPROVE && needsConfirm(id)) {
      setConfirmQueue([id]);
      return;
    }
    decide(id, val);
  };

  /**
   * One verdict for many steps — a group, or the navigator's selection.
   * Approvals DIWO advised against are queued for confirmation; the rest apply
   * at once.
   */
  const bulkDecide = (ids, verdict) => {
    if (verdict === APPROVE) {
      const conflicting = ids.filter(needsConfirm);
      ids.filter((id) => !conflicting.includes(id)).forEach((id) => decide(id, APPROVE));
      if (conflicting.length) setConfirmQueue(conflicting);
    } else {
      ids.forEach((id) => decide(id, verdict));
    }
  };

  const toggleExpanded = (key) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

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

  // ── Filtering ──────────────────────────────────────────────────────────────
  const matchesStatus = (step) => {
    const verdict = decisions[step.step_id];
    if (filter === "approved") return verdict === APPROVE;
    if (filter === "rejected") return verdict === REJECT;
    if (filter === "manual") return verdict === MANUAL;
    if (filter === "pending") return !verdict;
    return true;
  };

  // The recommendation the BACKEND assigned — read, never re-derived here.
  const matchesCategory = (step) => {
    if (categoryFilter === "any") return true;
    if (categoryFilter === "unassessed") return categoryOf(step) === null;
    return categoryOf(step) === categoryFilter;
  };

  const filtered = steps.filter((step) =>
    matchesStatus(step) && matchesCategory(step)
    && (impactFilter === "any" || (step.impact || step.expected_impact) === impactFilter)
    && (methodFilter === "any" || methodOf(step) === methodFilter));

  const anyFilterActive =
    filter !== "all" || categoryFilter !== "any" || impactFilter !== "any" || methodFilter !== "any";
  const clearFilters = () => {
    setFilter("all");
    setCategoryFilter("any");
    setImpactFilter("any");
    setMethodFilter("any");
  };

  const sections = buildSections(filtered, groupMode);
  // The order the navigator shows steps in, which is what Previous / Next walk.
  // The navigator draws each section as refactoring-method cards, so this
  // walks the steps in that same order.
  const ordered = sections.flatMap((section) =>
    groupStepsByMethod(section.steps).flatMap((method) => method.steps));
  const visibleIds = ordered.map((step) => step.step_id);

  // Counts over the WHOLE plan: a filter row whose numbers move as you filter
  // cannot tell you what is there to filter for.
  const counts = countDecisions(steps, decisions);
  const breakdown = groupBreakdown(steps);
  const recommendedIds = steps.filter((s) => categoryOf(s) === RECOMMENDED).map((s) => s.step_id);
  const fileCount = groupStepsByFile(steps).length;
  const forcedManual = forcedManualOnlySteps(steps, decisions).length;

  const activeStep = (activeId !== null && stepById(activeId)) || ordered[0] || null;
  const activeIndex = activeStep ? visibleIds.indexOf(activeStep.step_id) : -1;
  const goTo = (offset) => {
    const target = ordered[activeIndex + offset];
    if (target) setActiveId(target.step_id);
  };
  // The next undecided step after the open one, in list order, wrapping.
  const nextPending = activeIndex >= 0
    ? [...ordered.slice(activeIndex + 1), ...ordered.slice(0, activeIndex)]
      .find((step) => !decisions[step.step_id]) || null
    : null;

  const { approved, pending } = counts;
  const canProceed = approved > 0 && pending === 0;
  const blockedReason = pending > 0
    ? `Decide the ${pending} remaining step${pending > 1 ? "s" : ""} before sending the plan to SCTVA.`
    : approved === 0
      ? "Approve at least one step for automatic transformation."
      : null;

  // Steps where the developer went against the recommendation. Surfaced before
  // submit, not to argue with them, but because a disagreement is worth one
  // sentence of context for whoever reads the audit trail later.
  const overrides = steps.filter((step) => isOverride(step, decisions[step.step_id]));

  const confirmingStep = confirmQueue.length ? stepById(confirmQueue[0]) : null;

  const submit = () => {
    if (!canProceed || sending) return;

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

      <PlanApprovalHeader
        plan={currentPlan}
        totalSteps={steps.length}
        fileCount={fileCount}
        counts={counts}
        willSend={approved}
        forcedManual={forcedManual}
        canProceed={canProceed}
        blockedReason={blockedReason}
        sending={sending}
        sendError={submitError}
        onDismissSendError={onDismissSubmitError}
        onSend={() => canProceed && !sending && setSubmitting(true)}
        onFallback={onFallback}
      >
        <PlanFilters
          filter={filter}
          onFilter={setFilter}
          decisionCounts={counts}
          totalSteps={steps.length}
          categoryFilter={categoryFilter}
          onCategoryFilter={setCategoryFilter}
          breakdown={breakdown}
          methodFilter={methodFilter}
          onMethodFilter={setMethodFilter}
          methods={methodOptions(steps, decisions)}
          impactFilter={impactFilter}
          onImpactFilter={setImpactFilter}
          anyFilterActive={anyFilterActive}
          onClearFilters={clearFilters}
        />
      </PlanApprovalHeader>

      <div className="pa-ws">
        <div className="pa-grid">
          <PlanStepNavigator
            groupMode={groupMode}
            decisionCounts={counts}
            sections={sections}
            decisions={decisions}
            activeId={activeStep?.step_id ?? null}
            onActivate={setActiveId}
            recommendedIds={recommendedIds}
            visibleIds={visibleIds}
            onBulk={bulkDecide}
            expanded={expanded}
            onToggleExpanded={toggleExpanded}
            onExpandAll={() => setExpanded(new Set(
              sections.flatMap((section) =>
                groupStepsByMethod(section.steps).map((method) => `${section.key}:${method.key}`))
            ))}
            onCollapseAll={() => setExpanded(new Set())}
            totalSteps={steps.length}
            onClearDecisions={clearDecisions}
          />

          <PlanStepWorkspace
            // Keyed by step so the workspace opens each step at its top.
            key={activeStep?.step_id ?? "none"}
            step={activeStep}
            decision={activeStep ? decisions[activeStep.step_id] : undefined}
            overrideReason={activeStep ? overrideReasons[activeStep.step_id] : undefined}
            position={activeIndex >= 0 ? activeIndex + 1 : "—"}
            total={ordered.length}
            onDecide={requestDecision}
            onClearDecision={clearDecision}
            onOverrideReason={(id, reason) => setOverrideReasons((prev) => {
              const next = { ...prev };
              if (reason) next[id] = reason;
              else delete next[id];
              return next;
            })}
            onPrev={activeIndex > 0 ? () => goTo(-1) : undefined}
            onNext={activeIndex >= 0 && activeIndex < ordered.length - 1 ? () => goTo(1) : undefined}
            onNextPending={nextPending ? () => setActiveId(nextPending.step_id) : undefined}
            detailsOpen={Boolean(activeStep && detailsOpen[activeStep.step_id])}
            onToggleDetails={() => activeStep && setDetailsOpen((prev) => ({
              ...prev, [activeStep.step_id]: !prev[activeStep.step_id],
            }))}
          />
        </div>
      </div>

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
            setConfirmQueue((queue) => queue.slice(1));
          }}
          // Cancelling leaves THIS step undecided and moves on to the next one
          // a bulk approval queued.
          onCancel={() => setConfirmQueue((queue) => queue.slice(1))}
        />
      )}

      {submitting && (
        <PlanSubmitDialog
          totalSteps={steps.length}
          counts={counts}
          forcedManual={forcedManual}
          overrides={overrides.length}
          notes={opinion}
          onNotes={setOpinion}
          onCancel={() => setSubmitting(false)}
          onConfirm={() => {
            setSubmitting(false);
            submit();
          }}
        />
      )}
    </div>
  );
}

// ─── Grouping ────────────────────────────────────────────────────────────────

/**
 * The visible steps, arranged for the navigator. Every mode ends at the same
 * leaf — a file holding its steps — so switching arrangement changes only how
 * the list is stacked, never what is in it. Plan order is kept inside each
 * group: RDP sequenced the steps, and re-sorting them would hide that.
 */
function buildSections(steps, mode) {
  const asFiles = (list) => groupStepsByFile(list).map((g) => ({ file: g.key, steps: g.steps }));

  // File wise and Method wise share one structure — method, then the files it
  // changes — so Previous / Next walk the list in the order it is drawn. The
  // navigator draws File wise as approvable file rows under each method.
  if (mode === "method" || mode === "file") {
    return groupStepsByMethod(steps).map((group) => ({
      key: `method:${group.key}`,
      title: group.key,
      icon: "🛠",
      tone: T.text,
      files: asFiles(group.steps),
      steps: group.steps,
    }));
  }

  // Category order first, then anything the backend did not assess.
  return [...CATEGORY_ORDER, null]
    .map((category) => {
      const inCategory = steps.filter((step) => categoryOf(step) === category);
      if (inCategory.length === 0) return null;
      const style = categoryStyle(category);
      return {
        key: `cat:${category || "unassessed"}`,
        title: style.section,
        icon: style.sectionIcon,
        tone: style.color,
        files: asFiles(inCategory),
        steps: inCategory,
      };
    })
    .filter(Boolean);
}

/** Goal → the preference pair the backend re-ranker has always taken. */
function preferencesFor(goal) {
  return {
    developer_strategy: goal,
    ...({
      safety_first: { risk_tolerance: "conservative", impact_focus: "medium" },
      balanced: { risk_tolerance: "balanced", impact_focus: "high" },
      max_improvement: { risk_tolerance: "aggressive", impact_focus: "high" },
    }[goal] || { risk_tolerance: "balanced", impact_focus: "high" }),
  };
}

/**
 * Shown only when the plan on screen is NOT a live RDP plan (sample data, or
 * RDP was unavailable). A live plan needs no banner; a fallback one must say
 * that its recommendations were computed without RDP's scoring evidence.
 */
function SourceBanner({ origin, meta }) {
  const warnings = [];
  if (meta?.plan_warning) warnings.push(`RDP agent unavailable: ${meta.plan_warning}`);
  if (origin === "sample") warnings.push("Sample plan (bundled data) — the RDP agent is unavailable.");
  if (origin !== "rdp") warnings.push("Recommendations for this plan were computed without RDP's scoring evidence.");
  if (warnings.length === 0) return null;

  return (
    <div role="note" style={{
      marginBottom: 14, padding: "9px 14px", borderRadius: 10,
      background: tint(T.medium, 8), border: `1px solid ${tint(T.medium, 35)}`,
    }}>
      {warnings.map((w) => (
        <div key={w} style={{ fontSize: 12, color: T.text, lineHeight: 1.6 }}>
          <span aria-hidden="true" style={{ color: T.medium }}>⚠ </span>{w}
        </div>
      ))}
    </div>
  );
}

function LoadingState({ rdpUrl }) {
  return (
    <div role="status" style={{
      textAlign: "center", padding: "48px 24px", borderRadius: 12,
      background: T.surface, border: `1px solid ${T.border}`, boxShadow: T.shadowSm,
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8 }}>
        Generating the refactoring plan…
      </div>
      <div style={{ fontSize: 12.5, color: T.text2, marginBottom: 20 }}>
        The DIWO backend is forwarding your selected smells to {rdpUrl || "the RDP agent"}
        {" "}· interpreting smells, scoring candidates (MCDA), sequencing steps
      </div>
      <div style={{ height: 4, borderRadius: 4, background: T.sunken, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: T.accent, animation: "dwSlide 1.1s ease-in-out infinite" }} />
      </div>
    </div>
  );
}

function ErrorState({ warning, rdpUrl, onUseSample, onFallback }) {
  const nothingToPlan = /no code smells|nothing to plan|not called/i.test(warning || "");

  const hint = nothingToPlan
    ? "Every selected file came through without smells. Go back to Code Smell Review and approve files that still have detected smells."
    : `Start the Refactoring Planning agent before running the DIWO workflow:  cd agents/rdp_agent && python app.py  (serves ${rdpUrl || "http://localhost:5000"})`;

  return (
    <div role="alert" style={{
      padding: "28px", borderRadius: 12, background: T.surface,
      border: `1px solid ${tint(T.high, 45)}`, boxShadow: T.shadowSm,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{
          ...eyebrow, color: T.high, padding: "2px 8px", borderRadius: 4,
          background: tint(T.high, 10), border: `1px solid ${tint(T.high, 30)}`,
        }}>
          {nothingToPlan ? "Nothing to plan" : "No plan available"}
        </span>
        <span style={{ fontSize: 14.5, fontWeight: 700, color: T.text }}>
          Could not load the refactoring plan
        </span>
      </div>

      <div style={{ fontSize: 12.5, color: T.text2, lineHeight: 1.6, marginBottom: 12 }}>
        {warning || "The workflow returned no plan for this smell selection."}
      </div>

      <div style={{
        fontSize: 11.5, color: T.text2, fontFamily: T.mono, lineHeight: 1.6, wordBreak: "break-word",
        background: T.code, border: `1px solid ${T.border}`, borderRadius: 8, padding: "10px 14px", marginBottom: 18,
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="button" onClick={onFallback} className="dw-focus" style={primaryButton(true)}>
          ← Back to Smell Review
        </button>
        <button type="button" onClick={onUseSample} className="dw-focus dw-hover" style={ghostButton(true, { padding: "10px 18px", fontSize: 13 })}>
          Continue with sample data
        </button>
      </div>
    </div>
  );
}
