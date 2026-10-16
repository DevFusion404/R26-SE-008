/**
 * PlanStepWorkspace.jsx
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The right column of the Refactoring Plan Approval stage: everything about
 * the ONE step open in the navigator, and the decision for it.
 *
 *     ┌ Step header · verdict · ◀ ▶ ───┐  ┌ Decision support score ┐
 *     ├ What changes + code preview ───┤  ├ Transformation safety  ┤
 *     ├ Code quality change ───────────┤  └ Your history ──────────┘
 *     ├ Concerns (only when raised) ───┤
 *     └ Technical details (collapsed) ─┘
 *                                       ┌ Your decision ─────────┐
 *                                       │ [✓ Approve] [✕ Reject] │
 *                                       └────────────────────────┘
 *
 * The middle column is StepDetailBlocks; it does not repeat the score
 * breakdown, risk, impact, strategy or history shown on the right.
 *
 * This replaces the "Why this recommendation?" dialog: the evidence the
 * dialog held is now always on screen for the step being decided, so the
 * developer reads and decides in one place.
 *
 * Renders, never computes. The recommendation, its score, its reasons and the
 * SCTVA capability verdict all arrive on `step.decision_support` from the DIWO
 * backend; a field the backend did not send is left out, not invented.
 */

import { T, tint, eyebrow, riskTone } from "../codeSmellTheme.js";
import { Chip } from "../codeSmellUi.jsx";
import PlanningScoreBreakdown from "./PlanningScoreBreakdown";
import StepDetailBlocks from "./StepDetailBlocks.jsx";
import {
  APPROVE, MANUAL, MANUAL_ONLY, NOT_RECOMMENDED, OVERRIDE_REASONS, REJECT,
  actionModel, isOverride, supportOf,
} from "../utils/planningDecisionSupport";

/** The right column's Decision Support card — the verdict pill scrolls here. */
const DECISION_SUPPORT_ID = "pa-decision-support";

function showDecisionSupport() {
  const panel = document.getElementById(DECISION_SUPPORT_ID);
  if (!panel) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  panel.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
  panel.classList.remove("sd-flash");
  // Restart the highlight animation on repeated clicks.
  void panel.offsetWidth;
  panel.classList.add("sd-flash");
}

export default function PlanStepWorkspace({
  step, decision, overrideReason, position, total,
  onDecide, onClearDecision, onOverrideReason, onPrev, onNext, onNextPending,
  detailsOpen = false, onToggleDetails,
}) {
  if (!step) {
    return (
      <div className="pa-detail" style={{
        background: T.surface, border: `1px dashed ${T.borderStrong}`, borderRadius: 12,
        padding: "48px 24px", textAlign: "center", color: T.text2, fontSize: 13,
      }}>
        Select a step in the list to review it.
      </div>
    );
  }

  const support = supportOf(step);
  const category = support?.category || null;

  return (
    <section className="pa-detail" aria-label={`Step ${step.step_id}`}>
      <div className="pa-detail-grid">
        {/* ── What the step is and what it changes ──────────────────────── */}
        <StepDetailBlocks
          step={step}
          support={support}
          decision={decision}
          position={position}
          total={total}
          onPrev={onPrev}
          onNext={onNext}
          onNextPending={onNextPending}
          detailsOpen={detailsOpen}
          onToggleDetails={onToggleDetails}
          onShowDecisionSupport={showDecisionSupport}
        />

        {/* ── Decision support side panels ───────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {support && (
            <Section title="Decision support" id={DECISION_SUPPORT_ID}>
              <PlanningScoreBreakdown support={support} />
            </Section>
          )}
          <Safety step={step} support={support} />
          {support?.factors?.historical_feedback && (
            <History feedback={support.factors.historical_feedback} />
          )}
          <DecisionCard
            step={step}
            category={category}
            decision={decision}
            overrideReason={overrideReason}
            onDecide={onDecide}
            onClearDecision={onClearDecision}
            onOverrideReason={onOverrideReason}
          />
        </div>
      </div>
    </section>
  );
}

// ─── Sections ────────────────────────────────────────────────────────────────

/** SCTVA capability for this refactoring and this concrete step, plus validation coverage. */
function Safety({ step, support }) {
  const capability = support?.capability;
  const impact = support?.impact || {};
  const riskBand = impact.risk_band || step.risk || null;
  const validation = impact.validation || [];
  const missing = capability?.missing_requirements || [];
  const status = String(capability?.status || "unknown").toLowerCase();

  const automation = !capability
    ? { label: "Transformation capability unknown", tone: T.text3 }
    : status === "advisory"
      ? { label: "Manual-only transformation", tone: T.info }
      : status === "unknown"
        ? { label: "Unsupported transformation", tone: T.high }
        : !capability.actual_step_mappable
          ? { label: "Automatic — but this step is incomplete", tone: T.medium }
          : { label: "Automatic transformation supported", tone: T.accentText };

  return (
    <Section title="Transformation safety">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: automation.tone }}>{automation.label}</div>
        {capability?.action_type && (
          <div style={{ fontSize: 11.5, color: T.text3, fontFamily: T.mono }}>SCTVA action: {capability.action_type}</div>
        )}
        {riskBand && (
          <div style={{ fontSize: 12, color: T.text2 }}>
            Risk: <b style={{ color: riskTone(riskBand) }}>{riskBand.toUpperCase()}</b>
            {typeof impact.blast_radius_files === "number" && impact.blast_radius_files > 0 && (
              <> · {impact.blast_radius_files} file{impact.blast_radius_files > 1 ? "s" : ""} affected</>
            )}
          </div>
        )}
        {capability?.reason && <div style={{ fontSize: 11.5, color: T.text2, lineHeight: 1.5 }}>{capability.reason}</div>}
        {missing.length > 0 && (
          <div style={{ fontSize: 11.5, color: T.medium, lineHeight: 1.5 }}>Missing: {missing.join("; ")}</div>
        )}
        {riskBand === "high" && (
          <div role="note" style={{
            fontSize: 11.5, color: T.text, padding: "6px 9px", borderRadius: 7,
            background: tint(T.high, 9), border: `1px solid ${tint(T.high, 30)}`,
          }}>
            ⚠ High-risk change — review the parameters before approving.
          </div>
        )}
        {validation.length > 0 && (
          <div>
            <div style={{ ...eyebrow, margin: "4px 0 5px" }}>Validation coverage</div>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              {validation.map((v) => <Chip key={v} tone={T.accentText}>✓ {v}</Chip>)}
            </div>
            <div style={{ fontSize: 10.5, color: T.text3, marginTop: 5 }}>
              Checks available to SCTVA — not results; nothing has run yet.
            </div>
          </div>
        )}
      </div>
    </Section>
  );
}

/** The developer's own record with this refactoring — only ever the real one. */
function History({ feedback }) {
  const observed = feedback.status === "observed";
  const total = feedback.sample_size || 0;
  const accepted = feedback.accepted || 0;
  const pct = total ? Math.round((accepted / total) * 100) : 0;

  return (
    <Section title="Your history">
      {observed ? (
        <>
          <div style={{ fontSize: 12.5, color: T.text2, lineHeight: 1.6 }}>
            You accepted <b style={{ color: T.text }}>{accepted} of {total}</b> similar refactorings.
          </div>
          <div style={{ display: "flex", height: 6, borderRadius: 4, overflow: "hidden", marginTop: 8, background: T.sunken }}
               role="img" aria-label={`${pct}% accepted, ${100 - pct}% rejected`}>
            <div style={{ width: `${pct}%`, background: T.accent }} />
            <div style={{ width: `${100 - pct}%`, background: tint(T.high, 55) }} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: T.text3, marginTop: 4 }}>
            <span>{pct}% accepted</span>
            <span>{100 - pct}% rejected</span>
          </div>
        </>
      ) : (
        <div style={{ fontSize: 12, color: T.text2, lineHeight: 1.6 }}>
          <b style={{ color: T.medium }}>Not enough data yet.</b>{" "}
          {typeof feedback.sample_size === "number" && typeof feedback.minimum_sample === "number"
            ? `${feedback.sample_size} of ${feedback.minimum_sample} matching decisions recorded. `
            : ""}
          This factor is scored at the average of the others, so it neither helps nor hurts the recommendation.
        </div>
      )}
    </Section>
  );
}

// ─── Decision ────────────────────────────────────────────────────────────────

/**
 * Approve / Reject, under the decision-support panels. Approve goes through
 * the page's requestDecision, so approving against DIWO still opens the
 * confirmation; clicking the active button again clears the decision.
 */
function DecisionCard({ step, category, decision, overrideReason, onDecide, onClearDecision, onOverrideReason }) {
  const actions = actionModel(category);
  const override = isOverride(step, decision);

  const options = [
    { key: APPROVE, label: "Approve", done: "Approved", icon: "✓", hint: "Send to SCTVA", solid: "#15803d", text: T.accentText, tone: T.accent },
    { key: REJECT, label: "Reject", done: "Rejected", icon: "✕", hint: "Do not refactor", solid: "#dc2626", text: T.high, tone: T.high },
  ];

  return (
    <section style={{ ...card, padding: "13px 15px" }} aria-label={`Decision for step ${step.step_id}`}>
      <h3 style={{ ...eyebrow, margin: "0 0 10px", color: T.text2 }}>Your decision</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 8 }}>
        {options.map((o) => {
          const active = decision === o.key;
          return (
            <button
              key={o.key}
              type="button"
              aria-pressed={active}
              onClick={() => (active ? onClearDecision(step.step_id) : onDecide(step.step_id, o.key))}
              className="dw-focus"
              title={active
                ? "Click again to clear the decision"
                : o.key === APPROVE && actions.confirmApprove ? "DIWO advised against this — you will be asked to confirm" : o.hint}
              style={{
                minHeight: 44, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 7,
                padding: "0 14px", borderRadius: 10, cursor: "pointer", fontSize: 14, fontWeight: 700,
                background: active ? o.solid : tint(o.tone, 10),
                color: active ? "#ffffff" : o.text,
                border: `1px solid ${active ? o.solid : tint(o.tone, 50)}`,
                transition: "background 0.15s, color 0.15s",
              }}
            >
              <span aria-hidden="true">{o.icon}</span> {active ? o.done : o.label}
            </button>
          );
        })}
      </div>

      {decision === APPROVE && category === MANUAL_ONLY && (
        <Note tone={T.medium}>⚠ Not eligible for automatic transformation — SCTVA has no safe automatic form for this refactoring, so it may change no code.</Note>
      )}
      {decision === APPROVE && category === NOT_RECOMMENDED && (
        <Note tone={T.high}>⚠ Approved against DIWO's recommendation.</Note>
      )}
      {decision === MANUAL && (
        <Note tone={T.info}>Marked as manual work — recorded with the plan, not sent to SCTVA.</Note>
      )}

      {override && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11.5, color: T.text2, marginBottom: 5 }}>
            You {decision === APPROVE ? "approved" : "rejected"} this against DIWO&apos;s advice. Optional — why?
          </div>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            {OVERRIDE_REASONS.map((reason) => {
              const picked = overrideReason === reason;
              return (
                <button
                  key={reason}
                  type="button"
                  onClick={() => onOverrideReason(step.step_id, picked ? "" : reason)}
                  aria-pressed={picked}
                  className="dw-focus"
                  style={{
                    padding: "3px 10px", borderRadius: 999, fontSize: 11, fontWeight: 600, cursor: "pointer",
                    background: picked ? tint(T.info, 16) : T.surface,
                    color: picked ? T.info : T.text2,
                    border: `1px solid ${picked ? T.info : T.border}`,
                  }}
                >
                  {reason}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

// ─── Primitives ──────────────────────────────────────────────────────────────

const card = {
  background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, boxShadow: T.shadowSm,
};

function Section({ title, id, children }) {
  return (
    <section id={id} style={{ ...card, padding: "13px 15px" }}>
      <h3 style={{ ...eyebrow, margin: "0 0 10px", color: T.text2 }}>{title}</h3>
      {children}
    </section>
  );
}

function Note({ tone, children }) {
  return (
    <div style={{
      marginTop: 8, fontSize: 11.5, lineHeight: 1.5, color: T.text,
      padding: "6px 10px", borderRadius: 7, background: tint(tone, 9), border: `1px solid ${tint(tone, 30)}`,
    }}>
      {children}
    </div>
  );
}
