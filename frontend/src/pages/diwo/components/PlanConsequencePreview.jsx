/**
 * PlanConsequencePreview.jsx
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The two halves of the decision the developer is actually making, side by
 * side:
 *
 *     IF YOU APPROVE                IF YOU SKIP
 *     ~+6.3 quality points          ~6.3 points left unresolved
 *     ~6 min review                 Long Method remains
 *     1 file changed                Change pressure: high
 *     SCTVA can execute it          Debt interest ~1.4 / quarter
 *
 * Stage 2 used to show only the first column, which framed rejection as the
 * neutral, costless option — the developer could see what approving would buy
 * but never what deferring would keep costing, so "skip it for now" always
 * looked free. Both consequences are real; showing one of them is an argument,
 * not a decision aid.
 *
 * WHERE IT LIVES. Inside PlanStepDrawer, under "Why this recommendation?" —
 * not on the plan cards. Two columns of figures per card is readable once and
 * unreadable twelve times: it turns the plan into a wall of parallel numbers
 * and pushes the tenth step below three screens of evidence for the first
 * nine. The card carries the verdict and the four facts; this is the depth
 * behind it, rendered for the one step the developer opened.
 *
 * EVERY FIGURE COMES FROM THE BACKEND. `decision_support.impact` and
 * `decision_support.deferral` are computed server-side from the Stage 1 impact
 * record. A field the backend left null renders as nothing at all — never as a
 * zero, never as an estimate this file invented. A step with no impact record
 * behind it shows the two headings and says the numbers are unavailable, which
 * is the honest version of the same panel.
 *
 * Also exports `StepFacts` — the Benefit / Risk / Automation / Scope strip.
 * That one DOES stay on the card: four words the developer can scan, so the
 * card answers "what is this?" before they decide whether to open the drawer.
 */

import {
  BENEFIT_COLOR, RISK_COLOR, MANUAL_ONLY,
  formatMinutes, formatPoints,
} from "../utils/planningDecisionSupport";
import { C } from "../diwoTheme.jsx";

// ─── Automation, in words ────────────────────────────────────────────────────

/**
 * What SCTVA can do with this step, from the live capability probe.
 *
 * Three genuinely different answers, and the difference matters more than any
 * other fact on the card: "ready" means approving it produces a code change,
 * "not available" means approving it produces nothing, and "incomplete" means
 * approving it produces an attempt that is likely to fail.
 */
function automationFact(support) {
  const capability = support?.capability;
  if (!capability) return { label: "Unknown", color: C.textMuted, detail: null };

  const status = String(capability.status || "unknown").toLowerCase();

  if (status === "advisory") {
    return {
      label: "Not available",
      color: C.info,
      detail: "SCTVA has no safe automatic form for this refactoring.",
    };
  }
  if (status === "unknown") {
    return {
      label: "Not supported",
      color: C.danger,
      detail: capability.reason || "No SCTVA action is mapped to this refactoring.",
    };
  }
  if (!capability.actual_step_mappable) {
    return {
      label: "Incomplete",
      color: C.warn,
      detail: (capability.missing_requirements || []).length
        ? `Missing: ${capability.missing_requirements.join("; ")}`
        : capability.reason || "This concrete step cannot be mapped to an action.",
    };
  }
  return {
    label: "SCTVA ready ✓",
    color: C.accent,
    detail: capability.action_type ? `Action: ${capability.action_type}` : null,
  };
}

// ─── Benefit / Risk / Automation / Scope ─────────────────────────────────────

function Fact({ label, value, color, detail }) {
  return (
    <div title={detail || undefined} style={{ minWidth: 96 }}>
      <div style={{
        fontSize: 9, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700, marginBottom: 3,
      }}>
        {label}
      </div>
      <div style={{ fontSize: 12, fontWeight: 700, color: color || C.text }}>
        {value}
      </div>
    </div>
  );
}

/**
 * The four facts a developer needs before they need anything else.
 *
 * Benefit is RDP's own impact rating for the step; risk is the backend's
 * EFFECTIVE risk band, which is the worse of RDP's rating and the Stage 1
 * record's — a step both agents call risky and a step only one of them does
 * must not read the same. Scope is the measured blast radius, not a guess from
 * the file count.
 */
export function StepFacts({ step, support }) {
  const impact = support?.impact || {};
  const benefitBand = step?.impact || step?.expected_impact || null;
  const riskBand = impact.risk_band || step?.risk || null;
  const automation = automationFact(support);
  const files = impact.blast_radius_files;
  const manual = support?.category === MANUAL_ONLY;

  const gain = manual ? impact.potential_gain_points : impact.quality_gain_points;

  return (
    <div style={{
      display: "flex", gap: 22, flexWrap: "wrap", alignItems: "flex-start",
      padding: "9px 12px", borderRadius: 8,
      background: C.bg, border: `1px solid ${C.border}`,
    }}>
      <Fact
        label="Benefit"
        value={
          <>
            {benefitBand ? benefitBand.toUpperCase() : "—"}
            {typeof gain === "number" && (
              <span style={{ fontWeight: 500, color: C.textMuted, marginLeft: 6, fontFamily: "monospace" }}>
                {formatPoints(gain)}
              </span>
            )}
          </>
        }
        color={BENEFIT_COLOR[benefitBand] || C.textMuted}
        detail={
          manual
            ? "Potential quality gain if this refactoring is done by hand."
            : "Expected quality gain from this step."
        }
      />
      <Fact
        label="Risk"
        value={riskBand ? riskBand.toUpperCase() : "—"}
        color={RISK_COLOR[riskBand] || C.textMuted}
        detail="Transformation risk — the worse of RDP's rating and the Stage 1 impact record."
      />
      <Fact
        label="Automation"
        value={automation.label}
        color={automation.color}
        detail={automation.detail}
      />
      <Fact
        label="Scope"
        value={typeof files === "number" && files > 0 ? `${files} file${files > 1 ? "s" : ""}` : "—"}
        color={C.textSub}
        detail="Files the transformation is expected to touch."
      />
    </div>
  );
}

// ─── If approved / if skipped ────────────────────────────────────────────────

function Consequence({ title, tone, lines, empty }) {
  return (
    <div style={{
      flex: "1 1 220px", minWidth: 200,
      padding: "9px 12px", borderRadius: 8,
      background: `${tone}08`, borderLeft: `3px solid ${tone}`,
    }}>
      <div style={{
        fontSize: 9, color: tone, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 800, marginBottom: 6,
      }}>
        {title}
      </div>
      {lines.length === 0 ? (
        <div style={{ fontSize: 11, color: C.textMuted, lineHeight: 1.5 }}>{empty}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {lines.map((line) => (
            <div key={line.key} style={{
              fontSize: 11.5, color: C.textSub, lineHeight: 1.5,
              display: "flex", gap: 7, alignItems: "baseline",
            }}>
              <span aria-hidden="true" style={{ color: tone, flexShrink: 0, fontWeight: 700 }}>
                {line.mark}
              </span>
              <span>{line.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Build the "if approved" lines from the impact record.
 *
 * A manual-only step gets a deliberately different first line: its automatic
 * gain this run is zero, and printing its potential gain under "if you
 * approve" would promise a code change SCTVA is not going to make.
 */
function approvedLines(step, support) {
  const impact = support?.impact || {};
  const manual = support?.category === MANUAL_ONLY;
  const lines = [];

  if (manual) {
    if (typeof impact.potential_gain_points === "number") {
      lines.push({
        key: "gain",
        mark: "•",
        text: `${formatPoints(impact.potential_gain_points)} quality points if you refactor it by hand`,
      });
    }
    lines.push({
      key: "auto",
      mark: "⚠",
      text: "No automatic code change — SCTVA cannot execute this refactoring",
    });
  } else if (typeof impact.quality_gain_points === "number") {
    lines.push({
      key: "gain",
      mark: "✓",
      text: `Estimated quality gain ${formatPoints(impact.quality_gain_points)}`
        + (typeof impact.quality_gain_low === "number" && typeof impact.quality_gain_high === "number"
          ? ` (${impact.quality_gain_low}–${impact.quality_gain_high})`
          : ""),
    });
  }

  if (typeof impact.effort_minutes === "number") {
    lines.push({ key: "effort", mark: "✓", text: `Review effort ${formatMinutes(impact.effort_minutes)}` });
  }
  if (typeof impact.blast_radius_files === "number" && impact.blast_radius_files > 0) {
    lines.push({
      key: "files",
      mark: "✓",
      text: `${impact.blast_radius_files} file${impact.blast_radius_files > 1 ? "s" : ""} affected`,
    });
  }

  const automation = automationFact(support);
  if (!manual && support?.capability) {
    lines.push({
      key: "auto",
      mark: automation.color === C.accent ? "✓" : "⚠",
      text: automation.color === C.accent
        ? "SCTVA can perform this transformation automatically"
        : `Automatic transformation ${automation.label.toLowerCase()}`,
    });
  }

  const validation = impact.validation || [];
  if (validation.length > 0) {
    lines.push({
      key: "validation",
      mark: validation.includes("behavioural") ? "✓" : "•",
      text: `Validation available: ${validation.join(", ")}`,
    });
  }

  return lines;
}

/**
 * Build the "if skipped" lines from the deferral record.
 *
 * Deliberately not framed as a warning against skipping. Deferring is a
 * legitimate decision — sometimes the right one — and the developer's job here
 * is to compare two real costs, not to be argued out of one of them.
 */
function skippedLines(step, support) {
  const deferral = support?.deferral || {};
  const lines = [];

  if (typeof deferral.carried_points === "number" && deferral.carried_points > 0) {
    lines.push({
      key: "carried",
      mark: "•",
      text: `${deferral.carried_points} quality point${deferral.carried_points === 1 ? "" : "s"} stay unresolved`,
    });
  }

  if (step?.smell_type) {
    lines.push({ key: "smell", mark: "•", text: `${step.smell_type} remains in the code` });
  }

  // Churn is only quoted when the backend actually measured it. An unknown
  // change pressure printed as "low" would be a guess about this file's future.
  if (deferral.churn_known && deferral.change_pressure) {
    lines.push({ key: "pressure", mark: "•", text: `Change pressure: ${deferral.change_pressure}` });
  }

  if (typeof deferral.interest_per_quarter === "number" && deferral.interest_per_quarter > 0) {
    lines.push({
      key: "interest",
      mark: "•",
      text: `Technical-debt interest ~${deferral.interest_per_quarter} per quarter`,
    });
  }

  return lines;
}

export default function PlanConsequencePreview({ step, support }) {
  if (!support) return null;

  const approved = approvedLines(step, support);
  const skipped = skippedLines(step, support);
  const hasRecord = support.impact?.has_record;

  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <Consequence
        title="If you approve"
        tone={C.accent}
        lines={approved}
        empty={
          hasRecord
            ? "No projected effect was recorded for this step."
            : "No Stage 1 impact record for this smell — the effect could not be projected."
        }
      />
      <Consequence
        title="If you skip"
        tone={C.warn}
        lines={skipped}
        empty="No deferral cost was recorded for this step."
      />
    </div>
  );
}
