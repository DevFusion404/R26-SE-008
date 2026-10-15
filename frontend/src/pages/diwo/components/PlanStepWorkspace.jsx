/**
 * PlanStepWorkspace.jsx
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The right column of the Refactoring Plan Approval stage: everything about
 * the ONE step open in the navigator, and the decision for it.
 *
 *     Step #3 · Extract Method · Order.total · src/Order.java L40–88   ◀ ▶
 *     DIWO recommends this step: …
 *     ┌ Original code smell ───────────┐  ┌ Decision support score ┐
 *     ├ Proposed refactoring ──────────┤  ├ Transformation safety  ┤
 *     ├ RDP recommendation ────────────┤  └ Your history ──────────┘
 *     └ DIWO assessment (if approved / if skipped)
 *     [✓ Approve] [✕ Reject] [🔧 Manual] [○ Pending]          (sticky)
 *
 * This replaces the "Why this recommendation?" dialog: the evidence the
 * dialog held is now always on screen for the step being decided, so the
 * developer reads and decides in one place.
 *
 * Renders, never computes. The recommendation, its score, its reasons and the
 * SCTVA capability verdict all arrive on `step.decision_support` from the DIWO
 * backend; a field the backend did not send is left out, not invented.
 */

import { T, tint, eyebrow, ghostButton, riskTone } from "../codeSmellTheme.js";
import { Chip } from "../codeSmellUi.jsx";
import PlanningRecommendationBadge from "./PlanningRecommendationBadge";
import PlanningScoreBreakdown from "./PlanningScoreBreakdown";
import PlanConsequencePreview from "./PlanConsequencePreview";
import {
  APPROVE, MANUAL, MANUAL_ONLY, NOT_RECOMMENDED, OVERRIDE_REASONS, REJECT,
  actionModel, categoryStyle, isOverride, supportOf,
} from "../utils/planningDecisionSupport";
import { DECISION_META, linesOf, scopeOf } from "../utils/planUi";

export default function PlanStepWorkspace({
  step, decision, overrideReason, position, total,
  onDecide, onClearDecision, onOverrideReason, onPrev, onNext,
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
  const style = categoryStyle(category);
  const meta = DECISION_META[decision];
  const file = step.target?.file || null;
  const lines = linesOf(step);

  return (
    <section className="pa-detail" aria-label={`Step ${step.step_id}`}>
      {/* ── Heading ────────────────────────────────────────────────────── */}
      <div style={{ ...card, padding: "14px 16px", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 320px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ ...eyebrow, color: T.text2 }}>Step #{step.step_id}</span>
              <PlanningRecommendationBadge support={support} />
              {meta && (
                <Chip tone={meta.tone}>{meta.icon} {meta.label}</Chip>
              )}
              {!meta && <Chip tone={T.medium}>Pending</Chip>}
              {isOverride(step, decision) && <Chip tone={T.medium}>⚠ Against DIWO advice</Chip>}
            </div>
            <h2 style={{ margin: "6px 0 0", fontSize: 19, fontWeight: 800, color: T.text }}>
              {step.refactoring}
            </h2>
            <div style={{ fontSize: 12.5, color: T.text2, fontFamily: T.mono, marginTop: 3 }}>
              {scopeOf(step)}
            </div>
            {(file || lines) && (
              <div style={{ fontSize: 11.5, color: T.text3, fontFamily: T.mono, marginTop: 2, wordBreak: "break-all" }}>
                {file}{file && lines ? " · " : ""}{lines}
              </div>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
            <button type="button" onClick={onPrev} disabled={!onPrev} aria-label="Previous step" className="dw-focus dw-hover"
              style={ghostButton(Boolean(onPrev), { padding: "6px 10px" })}>◀</button>
            <span style={{ fontSize: 11.5, color: T.text3, fontFamily: T.mono }}>{position} / {total}</span>
            <button type="button" onClick={onNext} disabled={!onNext} aria-label="Next step" className="dw-focus dw-hover"
              style={ghostButton(Boolean(onNext), { padding: "6px 10px" })}>▶</button>
          </div>
        </div>

        <div style={{
          marginTop: 12, padding: "9px 12px", borderRadius: 8, fontSize: 12.5, lineHeight: 1.55,
          color: T.text, background: tint(style.color, 8), borderLeft: `3px solid ${style.color}`,
        }}>
          {support
            ? <><b style={{ color: style.color }}>{style.verb}:</b> {support.summary}</>
            : <><b style={{ color: T.text2 }}>DIWO decision support is unavailable for this step.</b> Review the RDP evidence below yourself.</>}
        </div>
      </div>

      <div className="pa-detail-grid">
        {/* ── Main evidence ──────────────────────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <Section title="Original code smell">
            <div className="pa-fields">
              <Field label="Code smell" value={step.smell_type} />
              <Field label="Severity" value={step.severity} tone={sevTone(step.severity)} />
              <Field label="Category" value={step.smell_category || step.category} />
              <Field label="Smell ID" value={step.smell_id} mono />
              <Field label="Affected scope" value={scopeOf(step)} mono />
              <Field label="Lines" value={lines || null} mono />
            </div>
          </Section>

          <Section title="Proposed refactoring">
            <div className="pa-fields">
              <Field label="Method" value={step.refactoring} />
              <Field label="Target" value={scopeOf(step)} mono />
              <Field
                label="Expected impact"
                value={(step.impact || step.expected_impact || "").toUpperCase() || null}
                tone={{ high: T.accentText, medium: T.medium, low: T.text2 }[step.impact || step.expected_impact]}
              />
              <Field label="Risk" value={step.risk ? step.risk.toUpperCase() : null} tone={riskTone(step.risk)} />
            </div>
            {step.explanation && (
              <p style={{ margin: "12px 0 0", fontSize: 12.5, color: T.text2, lineHeight: 1.6 }}>{step.explanation}</p>
            )}
            <Parameters step={step} />
          </Section>

          <RdpRecommendation step={step} />

          {support && (
            <Section title="DIWO assessment">
              <Evidence support={support} />
              <div style={{ ...eyebrow, margin: "14px 0 8px" }}>If approved · if rejected</div>
              <PlanConsequencePreview step={step} support={support} />
            </Section>
          )}
        </div>

        {/* ── Decision support side panels ───────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {support && (
            <Section title="Decision support">
              <PlanningScoreBreakdown support={support} />
            </Section>
          )}
          <Safety step={step} support={support} />
          {support?.factors?.historical_feedback && (
            <History feedback={support.factors.historical_feedback} />
          )}
        </div>
      </div>

      <DecisionBar
        step={step}
        category={category}
        decision={decision}
        overrideReason={overrideReason}
        onDecide={onDecide}
        onClearDecision={onClearDecision}
        onOverrideReason={onOverrideReason}
      />
    </section>
  );
}

// ─── Sections ────────────────────────────────────────────────────────────────

/** Transformation parameters, verbatim — they are what SCTVA executes. */
function Parameters({ step }) {
  const params = Object.entries(step.parameters || {});
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ ...eyebrow, marginBottom: 7 }}>Transformation parameters</div>
      {params.length === 0 ? (
        <div style={{ fontSize: 12, color: T.text3 }}>No parameters — RDP inferred nothing to configure.</div>
      ) : (
        <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "minmax(110px, max-content) 1fr", gap: "5px 12px" }}>
          {params.map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt style={{ fontSize: 11.5, color: T.text3, fontFamily: T.mono }}>{key}</dt>
              <dd style={{ margin: 0, fontSize: 12, color: T.text, fontFamily: T.mono, wordBreak: "break-word" }}>
                {typeof value === "object" ? JSON.stringify(value) : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** RDP's own score, prediction and the alternatives it ranked below this one. */
function RdpRecommendation({ step }) {
  const prediction = step.prediction;
  const alternatives = step.alternatives || [];
  const hasScore = typeof step.score === "number";
  if (!hasScore && !prediction && alternatives.length === 0) return null;

  return (
    <Section title="RDP recommendation">
      <div className="pa-fields">
        {hasScore && <Field label="RDP score" value={step.score.toFixed(2)} mono />}
        {step.scoring_method && <Field label="Scoring method" value={step.scoring_method} />}
        {prediction && (
          <>
            <Field label="Complexity after" value={prediction.predicted_complexity_after} mono />
            <Field label="Coupling change" value={prediction.coupling_change} mono
              tone={prediction.coupling_change <= 0 ? T.accentText : T.medium} />
            <Field label="Cohesion change" value={prediction.cohesion_change} mono
              tone={prediction.cohesion_change >= 0 ? T.accentText : T.medium} />
            <Field label="Maintainability" value={prediction.maintainability_improvement != null ? `+${prediction.maintainability_improvement}` : null} mono tone={T.accentText} />
            <Field label="Risk score" value={prediction.risk_score} mono tone={riskTone(step.risk)} />
          </>
        )}
      </div>

      {alternatives.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ ...eyebrow, marginBottom: 7 }}>Alternatives considered</div>
          <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: "hidden" }}>
            <AltRow name={step.refactoring} score={step.score} chosen />
            {alternatives.map((alt) => (
              <AltRow key={alt.name} name={alt.name} score={alt.score} reason={alt.reason || alt.explanation} />
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}

function AltRow({ name, score, chosen = false, reason }) {
  return (
    <div style={{
      display: "flex", gap: 10, alignItems: "baseline", padding: "7px 10px",
      borderTop: chosen ? "none" : `1px solid ${T.border}`,
      background: chosen ? tint(T.accent, 8) : "transparent",
    }}>
      <span aria-hidden="true" style={{ width: 12, color: T.accentText, fontWeight: 800 }}>{chosen ? "✓" : ""}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12.5, fontWeight: chosen ? 700 : 500, color: chosen ? T.text : T.text2 }}>{name}</span>
        {chosen && <span style={{ fontSize: 11, color: T.text3 }}> · selected by RDP</span>}
        {reason && <div style={{ fontSize: 11.5, color: T.text3, marginTop: 2 }}>{reason}</div>}
      </span>
      {typeof score === "number" && (
        <span style={{ fontSize: 12, fontFamily: T.mono, fontWeight: 700, color: chosen ? T.accentText : T.text2 }}>
          {score.toFixed(2)}
        </span>
      )}
    </div>
  );
}

/** What DIWO weighed: the backend's reasons for, and its warnings against. */
function Evidence({ support }) {
  const reasons = support.reasons || [];
  const warnings = support.warnings || [];
  return (
    <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
      <EvidenceList title="In favour" tone={T.accentText} mark="✓" items={reasons} empty="No supporting factors were reported." />
      <EvidenceList title="Concerns" tone={T.medium} mark="⚠" items={warnings} empty="No concerns were raised for this step." />
    </div>
  );
}

function EvidenceList({ title, tone, mark, items, empty }) {
  return (
    <div style={{ padding: "9px 11px", borderRadius: 8, background: tint(tone, 6), border: `1px solid ${tint(tone, 22)}` }}>
      <div style={{ ...eyebrow, color: tone, marginBottom: 6 }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ fontSize: 12, color: T.text3 }}>{empty}</div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
          {items.map((item) => (
            <li key={item} style={{ display: "flex", gap: 7, fontSize: 12, color: T.text, lineHeight: 1.5 }}>
              <span aria-hidden="true" style={{ color: tone, fontWeight: 700, flexShrink: 0 }}>{mark}</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

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

// ─── Decision bar ────────────────────────────────────────────────────────────

/**
 * Sticky at the bottom of the workspace. Approve goes through the page's
 * requestDecision, so approving against DIWO still opens the confirmation;
 * Pending clears the decision.
 */
function DecisionBar({ step, category, decision, overrideReason, onDecide, onClearDecision, onOverrideReason }) {
  const actions = actionModel(category);
  const override = isOverride(step, decision);

  const options = [
    { key: APPROVE, label: actions.approveLabel?.replace(/^✓\s*/, "") || "Approve", icon: "✓", hint: "Send to SCTVA", tone: T.accent, text: T.accentText },
    { key: REJECT, label: actions.rejectLabel?.replace(/^✕\s*/, "") || "Reject", icon: "✕", hint: "Do not refactor", tone: T.high, text: T.high },
    { key: MANUAL, label: "Manual", icon: "🔧", hint: "Record, do not transform", tone: T.info, text: T.info },
    { key: null, label: "Pending", icon: "○", hint: "Decide later", tone: T.text3, text: T.text2 },
  ];
  const suggested = actions.primary === "explain" ? null : actions.primary;

  return (
    <div style={{
      position: "sticky", bottom: 0, zIndex: 5, marginTop: 14,
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12,
      boxShadow: T.shadowMd, padding: "11px 14px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ ...eyebrow, marginRight: 4 }}>Your decision</span>
        <div role="radiogroup" aria-label={`Decision for step ${step.step_id}`} style={{ display: "flex", gap: 6, flexWrap: "wrap", flex: 1 }}>
          {options.map((o) => {
            const active = (decision || null) === o.key;
            return (
              <button
                key={o.label}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => (o.key ? onDecide(step.step_id, o.key) : onClearDecision(step.step_id))}
                className="dw-focus"
                title={o.key === APPROVE && actions.confirmApprove ? "DIWO advised against this — you will be asked to confirm" : o.hint}
                style={{
                  display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 1,
                  padding: "6px 12px", borderRadius: 8, cursor: "pointer", minWidth: 96,
                  background: active ? tint(o.tone, 16) : T.surface,
                  border: `${active ? 2 : 1}px solid ${active ? o.tone : T.border}`,
                }}
              >
                <span style={{ fontSize: 12.5, fontWeight: 700, color: active ? o.text : T.text }}>
                  <span aria-hidden="true">{o.icon}</span> {o.label}
                  {suggested === o.key && <span style={{ fontSize: 10, fontWeight: 700, color: T.text3 }}> · DIWO</span>}
                </span>
                <span style={{ fontSize: 10.5, color: T.text3 }}>{o.hint}</span>
              </button>
            );
          })}
        </div>
      </div>

      {decision === APPROVE && category === MANUAL_ONLY && (
        <Note tone={T.medium}>⚠ Not eligible for automatic transformation — SCTVA has no safe automatic form for this refactoring, so it may change no code.</Note>
      )}
      {decision === APPROVE && category === NOT_RECOMMENDED && (
        <Note tone={T.high}>⚠ Approved against DIWO's recommendation.</Note>
      )}
      {decision === MANUAL && (
        <Note tone={T.info}>ⓘ Recorded with the plan as yours to do by hand — not sent to SCTVA.</Note>
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
    </div>
  );
}

// ─── Primitives ──────────────────────────────────────────────────────────────

const card = {
  background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12, boxShadow: T.shadowSm,
};

function Section({ title, children }) {
  return (
    <section style={{ ...card, padding: "13px 15px" }}>
      <h3 style={{ ...eyebrow, margin: "0 0 10px", color: T.text2 }}>{title}</h3>
      {children}
    </section>
  );
}

/** A label/value pair. Renders nothing when the value is missing. */
function Field({ label, value, tone, mono = false }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10.5, color: T.text3, fontWeight: 600, marginBottom: 2 }}>{label}</div>
      <div style={{
        fontSize: 12.5, fontWeight: 600, color: tone || T.text, wordBreak: "break-word",
        fontFamily: mono ? T.mono : "inherit",
      }}>
        {String(value)}
      </div>
    </div>
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

const sevTone = (s) => ({ high: T.high, critical: T.high, medium: T.medium, low: T.low }[String(s || "").toLowerCase()]);
