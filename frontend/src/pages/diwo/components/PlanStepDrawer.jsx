/**
 * PlanStepDrawer.jsx
 * ==================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * "Why this recommendation?" for one Stage 2 plan step, in a dialog.
 *
 * This content used to expand inline underneath the step row. That works for a
 * paragraph and not for this: the reason list, the projected effect, the
 * deferral cost, the five-factor score breakdown, the SCTVA mapping verdict,
 * the transformation parameters, RDP's prediction and its rejected alternatives
 * are together taller than the viewport, so opening one step pushed every other
 * step off screen and the developer lost their place in the plan they were
 * reviewing. A dialog leaves the list exactly where it was.
 *
 * Same shell as ImpactDrawer, which answers the equivalent question at Stage 1
 * ("what does picking this smell buy me?"), so the two explanation surfaces
 * behave identically: click the backdrop or press Escape to dismiss, the panel
 * scrolls internally, and the decision buttons are repeated in the footer so a
 * developer who has just read the evidence can act on it without hunting for
 * the row again.
 *
 * Renders nothing without a step. Everything inside degrades independently — a
 * step with no `decision_support` still shows its transformation details.
 */

import { useEffect } from "react";
import PlanningRecommendationBadge from "./PlanningRecommendationBadge";
import PlanningScoreBreakdown from "./PlanningScoreBreakdown";
import { categoryStyle, formatMinutes, formatPoints } from "../utils/planningDecisionSupport";
import { C, Badge, Pill, impactColor, riskColor, severityColor } from "../diwoTheme.jsx";

const ghostButton = {
  padding: "6px 13px", borderRadius: 8, fontSize: 12, fontWeight: 700,
  background: C.panel, color: C.textSub, border: `1px solid ${C.border}`,
  cursor: "pointer", flexShrink: 0,
};

export default function PlanStepDrawer({ step, decision, onDecide, onClose }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!step) return null;

  const support = step.decision_support && typeof step.decision_support === "object"
    ? step.decision_support
    : null;
  const category = support?.category || null;
  const style = categoryStyle(category);
  const capability = support?.capability;

  const targetLabel =
    [step.target?.class, step.target?.method].filter(Boolean).join(".") ||
    step.target?.file ||
    "(module level)";

  return (
    <div
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Step ${step.step_id}: why this recommendation`}
      style={{
        position: "fixed", inset: 0, zIndex: 1100,
        background: "rgba(4,6,10,0.72)", backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(940px, 100%)", maxHeight: "88vh", overflowY: "auto",
          background: C.bg, border: `1px solid ${C.borderAcc}`, borderRadius: 14,
          boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 12, padding: "16px 20px",
          borderBottom: `1px solid ${C.border}`, background: C.panel,
          position: "sticky", top: 0, zIndex: 2,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
                Step {step.step_id}
              </span>
              <PlanningRecommendationBadge support={support} />
              <span style={{ fontSize: 15, fontWeight: 800, color: C.text }}>
                {step.smell_type ? `${step.smell_type} → ` : ""}{step.refactoring}
              </span>
            </div>
            <div style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
              {step.target?.file || "(module level)"}
              {targetLabel !== step.target?.file ? ` · ${targetLabel}` : ""}
              {Array.isArray(step.target?.lines) && step.target.lines.length > 0
                ? ` · L${step.target.lines.join("-")}`
                : ""}
            </div>
          </div>

          <button onClick={onClose} style={ghostButton}>Close ✕</button>
        </div>

        {/* ── The facts the collapsed row showed, repeated so the dialog
               stands on its own rather than sending the reader back. ─────── */}
        <div style={{
          display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
          padding: "12px 20px", borderBottom: `1px solid ${C.border}`,
        }}>
          <Pill
            label={`Impact: ${step.impact || step.expected_impact || "medium"}`}
            color={impactColor(step.impact || step.expected_impact || "medium")}
          />
          <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
          {step.smell_type && (
            <Pill label={step.smell_type} color={severityColor(step.severity)} />
          )}
          {typeof step.score === "number" && (
            <span
              title={`RDP MCDA score (${step.scoring_method || "mcda"})`}
              style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}
            >
              RDP {step.score.toFixed(2)}
            </span>
          )}
          {capability && (
            <span
              title={capability.reason || ""}
              style={{
                fontSize: 11, fontFamily: "monospace", fontWeight: 700,
                color: capability.actual_step_mappable ? C.accent : C.warn,
              }}
            >
              SCTVA {String(capability.status || "unknown").toUpperCase()}
              {capability.actual_step_mappable ? " ✓" : " ⚠"}
            </span>
          )}
        </div>

        {step.explanation && (
          <div style={{
            padding: "12px 20px", borderBottom: `1px solid ${C.border}`,
            fontSize: 12, color: C.textSub, lineHeight: 1.6,
          }}>
            {step.explanation}
          </div>
        )}

        {/* ── DIWO's verdict, then the evidence for it ───────────────────── */}
        {support && (
          <div style={{
            padding: "14px 20px", borderBottom: `1px solid ${C.border}`,
            background: `${style.color}0a`, borderLeft: `3px solid ${style.color}`,
          }}>
            <div style={{ fontSize: 12.5, color: C.textSub, lineHeight: 1.6 }}>
              <b style={{ color: style.color }}>{style.verb}:</b> {support.summary}
            </div>
          </div>
        )}

        <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 18 }}>
          {support && <RecommendationEvidence support={support} />}
          {support && <PlanningScoreBreakdown support={support} />}
          <StepDetails step={step} support={support} />
        </div>

        {/* ── Decide without going back to hunt for the row ──────────────── */}
        {onDecide && (
          <div style={{
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
            padding: "12px 20px", borderTop: `1px solid ${C.border}`,
            background: C.panel, position: "sticky", bottom: 0,
          }}>
            <button
              onClick={() => { onDecide(step.step_id, "approve"); onClose?.(); }}
              style={{
                padding: "9px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "none",
                background: decision === "approve" ? C.accent : `${C.accent}18`,
                color: decision === "approve" ? "#000" : C.accent,
              }}
            >
              ✓ Approve this step
            </button>
            <button
              onClick={() => { onDecide(step.step_id, "reject"); onClose?.(); }}
              style={{
                padding: "9px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "none",
                background: decision === "reject" ? C.danger : `${C.danger}18`,
                color: decision === "reject" ? "#fff" : C.danger,
              }}
            >
              ✕ Reject this step
            </button>

            <span style={{ fontSize: 11, color: C.textMuted, marginLeft: "auto" }}>
              {decision
                ? `Currently ${decision === "approve" ? "approved" : "rejected"}.`
                : "No decision yet."}
              {" "}Nothing is transformed until you forward the plan.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────────

/** A labelled figure in the "expected effect" strip. */
function Fact({ label, value, color = C.text, title }) {
  return (
    <span title={title} style={{ display: "inline-flex", flexDirection: "column", gap: 1 }}>
      <span style={{ fontSize: 9, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.6 }}>
        {label}
      </span>
      <span style={{ fontSize: 12, fontWeight: 700, color, fontFamily: "monospace" }}>{value}</span>
    </span>
  );
}

/**
 * The evidence behind one recommendation: why DIWO said it, what the step is
 * expected to buy, and what skipping it costs. Every line comes from the
 * backend's own factors, so a reason can never contradict the score it sits
 * beside.
 */
function RecommendationEvidence({ support }) {
  const category = support.category;
  const impact = support.impact;
  const deferral = support.deferral;
  const reasons = support.reasons || [];
  const warnings = support.warnings || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {(reasons.length > 0 || warnings.length > 0) && (
        <div>
          <div style={{
            fontSize: 10, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 1, marginBottom: 8,
          }}>
            {category === "recommended" ? "Why DIWO recommends this" : "What DIWO weighed"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {reasons.map((reason) => (
              <div key={reason} style={{ fontSize: 12, color: C.textSub, display: "flex", gap: 8 }}>
                <span style={{ color: C.accent, fontWeight: 700, flexShrink: 0 }}>✓</span>
                <span>{reason}</span>
              </div>
            ))}
            {warnings.map((warning) => (
              <div key={warning} style={{ fontSize: 12, color: C.textSub, display: "flex", gap: 8 }}>
                <span style={{ color: C.warn, fontWeight: 700, flexShrink: 0 }}>⚠</span>
                <span>{warning}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {impact && (
        <div>
          <div style={{
            fontSize: 10, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 1, marginBottom: 8,
          }}>
            Expected effect
          </div>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <Fact
              label="Quality gain"
              value={formatPoints(
                category === "manual_only"
                  ? impact.potential_gain_points
                  : impact.quality_gain_points
              )}
              color={category === "manual_only" ? C.info : C.accent}
              title={
                category === "manual_only"
                  ? "Potential gain if this is done by hand. The automatic gain this run is 0."
                  : "Estimated quality points recovered. Approximate — the band is shown with the score breakdown."
              }
            />
            <Fact
              label="Blast radius"
              value={impact.blast_radius_files
                ? `${impact.blast_radius_files} file${impact.blast_radius_files > 1 ? "s" : ""}`
                : "—"}
            />
            <Fact label="Review effort" value={formatMinutes(impact.effort_minutes)} />
            <Fact
              label="Validation"
              value={(impact.validation || []).length ? impact.validation.join(" · ") : "—"}
              color={(impact.validation || []).includes("behavioural") ? C.accent : C.textSub}
              title="Checks SCTVA can run against the result."
            />
            {category === "manual_only" && (
              <Fact
                label="Automatic gain this run" value="0" color={C.warn}
                title="Selecting this will not produce an automatic code change in the current SCTVA build."
              />
            )}
          </div>
        </div>
      )}

      {deferral?.carried_points ? (
        <div>
          <div style={{
            fontSize: 10, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 1, marginBottom: 6,
          }}>
            If deferred
          </div>
          <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.6 }}>
            {deferral.carried_points} quality point
            {deferral.carried_points === 1 ? "" : "s"} remain unresolved.
            {deferral.churn_known && deferral.change_pressure
              ? ` This file has ${deferral.change_pressure} recent change pressure.`
              : ""}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The parameters block is what the Safe Transformation Agent actually executes,
 * so it is shown verbatim: a placeholder like "<parent>" here is the developer's
 * only warning that a step will not transform cleanly. When the backend's
 * mapping check already found the step incomplete, that verdict is shown above
 * the parameters rather than left to be spotted by eye.
 */
function StepDetails({ step, support }) {
  const params = Object.entries(step.parameters || {});
  const prediction = step.prediction;
  const capability = support?.capability;
  const missing = capability?.missing_requirements || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {capability && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            SCTVA executability
          </div>
          <div style={{ fontSize: 11.5, color: C.textSub, lineHeight: 1.65 }}>
            <div>
              Capability for this refactoring:{" "}
              <b style={{ color: capability.status === "executable" ? C.accent : C.warn }}>
                {String(capability.status || "unknown").toUpperCase()}
              </b>
              {capability.action_type && (
                <span style={{ fontFamily: "monospace", color: C.textMuted }}> · {capability.action_type}</span>
              )}
            </div>
            <div>
              This concrete step:{" "}
              <b style={{ color: capability.actual_step_mappable ? C.accent : C.danger }}>
                {capability.actual_step_mappable ? "mappable ✓" : "not mappable ⚠"}
              </b>
            </div>
            {capability.reason && (
              <div style={{ color: C.textMuted, marginTop: 2 }}>{capability.reason}</div>
            )}
            {missing.length > 0 && (
              <div style={{ color: C.warn, marginTop: 3 }}>
                Missing: {missing.join("; ")}
              </div>
            )}
          </div>
        </div>
      )}

      <div>
        <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
          Transformation parameters · smell {step.smell_id}
        </div>
        {params.length === 0 ? (
          <div style={{ fontSize: 11.5, color: C.textMuted }}>No parameters — the agent inferred nothing to configure.</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {params.map(([key, value]) => (
              <span key={key} style={{
                fontSize: 11, fontFamily: "monospace", padding: "3px 8px", borderRadius: 6,
                background: C.panel, border: `1px solid ${C.border}`, color: C.textSub,
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
            RDP predicted effect
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, fontSize: 11.5, color: C.textSub }}>
            <span>complexity after: <b style={{ color: C.text }}>{prediction.predicted_complexity_after}</b></span>
            <span>coupling: <b style={{ color: prediction.coupling_change <= 0 ? C.accent : C.warn }}>{prediction.coupling_change}</b></span>
            <span>cohesion: <b style={{ color: prediction.cohesion_change >= 0 ? C.accent : C.warn }}>{prediction.cohesion_change}</b></span>
            <span>maintainability: <b style={{ color: C.accent }}>+{prediction.maintainability_improvement}</b></span>
            <span>risk: <b style={{ color: riskColor(step.risk) }}>{prediction.risk_score}</b></span>
          </div>
        </div>
      )}

      {/* The alternatives are RDP's reasoning, so show the selection as a
          comparison rather than as a list of orphaned numbers. */}
      {step.alternatives?.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Why this refactoring?
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: 11.5, color: C.text, display: "flex", gap: 8 }}>
              <span style={{ color: C.accent, fontWeight: 800 }}>✓</span>
              <span style={{ fontWeight: 700, flex: 1 }}>{step.refactoring}</span>
              {typeof step.score === "number" && (
                <span style={{ fontFamily: "monospace", color: C.accent }}>{step.score.toFixed(2)}</span>
              )}
              <span style={{ color: C.textMuted }}>selected by RDP</span>
            </div>
            {step.alternatives.map(alt => (
              <div key={alt.name} style={{ fontSize: 11.5, color: C.textMuted, display: "flex", gap: 8, paddingLeft: 22 }}>
                <span style={{ flex: 1 }}>{alt.name}</span>
                {typeof alt.score === "number" && (
                  <span style={{ fontFamily: "monospace" }}>{alt.score.toFixed(2)}</span>
                )}
              </div>
            ))}
          </div>
          {/* Only claimed when the numbers actually support it. */}
          {typeof step.score === "number" &&
            step.alternatives.every((alt) => typeof alt.score !== "number" || alt.score < step.score) && (
              <div style={{ fontSize: 11, color: C.textMuted, marginTop: 6 }}>
                RDP ranked {step.refactoring} highest among the {step.alternatives.length + 1} evaluated candidate(s).
              </div>
            )}
        </div>
      )}

      {support?.factors?.historical_feedback && (
        <div style={{ fontSize: 11, color: C.textMuted }}>
          <b style={{ color: C.textSub }}>Historical feedback:</b>{" "}
          {support.factors.historical_feedback.status === "observed"
            ? `${support.factors.historical_feedback.accepted} of ${support.factors.historical_feedback.sample_size} similar steps accepted previously.`
            : support.factors.historical_feedback.message || "Not enough historical feedback yet."}
        </div>
      )}
    </div>
  );
}
