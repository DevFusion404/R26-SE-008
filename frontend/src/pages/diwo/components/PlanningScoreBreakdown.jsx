/**
 * PlanningScoreBreakdown.jsx
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The arithmetic behind a Decision Support Score, shown rather than asserted:
 *
 *     RDP recommendation quality   ███████████░░  29.2 / 35
 *     Expected technical benefit   ██████████░░░  17.5 / 25
 *     Transformation safety        ████████████░  17.6 / 20
 *     Developer strategy match     ████████████░   9.5 / 10
 *     Historical feedback          ██████████░░░   8.2 / 10   (imputed)
 *                                                 ───────────
 *                                                  82 / 100
 *
 * "Recommended — 87" with no explanation is the thing this feature exists to
 * replace, so the breakdown names every term, its weight, and where its value
 * came from. A factor computed from a fallback rather than from evidence says
 * so on its own row: an estimate the developer cannot tell apart from a
 * measurement is worse than no number.
 *
 * It is called a Decision Support Score and never a confidence percentage.
 * Nothing here is calibrated against observed developer behaviour yet, and a
 * "%" would claim otherwise.
 */

import { categoryStyle } from "../utils/planningDecisionSupport";
import { C } from "../diwoTheme.jsx";

const FACTOR_LABEL = {
  rdp_quality: "RDP recommendation quality",
  technical_benefit: "Expected technical benefit",
  transformation_safety: "Transformation safety",
  strategy_match: "Developer strategy match",
  historical_feedback: "Historical developer feedback",
};

/** Display order matches the published weighting table, heaviest first. */
const FACTOR_ORDER = [
  "rdp_quality", "technical_benefit", "transformation_safety",
  "strategy_match", "historical_feedback",
];

/** Where a factor's value came from, when it is not simply "the evidence". */
function provenance(key, factor) {
  if (key === "rdp_quality") {
    if (factor.basis === "derived_from_ratings" || factor.basis === "missing") {
      return "estimated — RDP sent no score for this step";
    }
    if (factor.basis === "ten_point") return "RDP score rescaled from a 1–10 range";
    if (factor.basis === "percent") return "RDP score rescaled from a 0–100 range";
    if (factor.basis === "clamped_high") return "RDP score clamped to the top of the range";
    return null;
  }

  if (key === "technical_benefit") {
    const sources = factor.sources || [];
    if (sources.includes("impact_record") && sources.includes("rdp_prediction")) {
      return "Stage 1 impact record blended with RDP's prediction";
    }
    if (sources.includes("impact_record")) return "from the Stage 1 impact record";
    if (sources.includes("rdp_prediction")) return "from RDP's impact prediction only";
    if (sources.includes("impact_rating")) {
      return "estimated — no impact record or prediction was available";
    }
    return null;
  }

  if (key === "historical_feedback") {
    if (factor.status === "insufficient_data") {
      return factor.message || "not enough historical feedback yet";
    }
    return `${factor.accepted}/${factor.sample_size} similar steps accepted`;
  }

  if (key === "strategy_match") return `scored against ${factor.strategy_label}`;

  if (key === "transformation_safety") {
    const parts = [`${factor.risk_band} risk`];
    if (factor.blast_radius_files > 1) parts.push(`${factor.blast_radius_files} files affected`);
    if ((factor.validation || []).length) parts.push(factor.validation.join(" + "));
    return parts.join(" · ");
  }

  return null;
}

function FactorRow({ name, factor }) {
  const max = factor.max_points || 1;
  const ratio = Math.max(0, Math.min(1, (factor.points || 0) / max));
  const note = provenance(name, factor);
  const estimated = /^estimated|^not enough/.test(note || "");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11 }}>
        <span style={{ color: C.textSub, fontWeight: 600 }}>{FACTOR_LABEL[name] || name}</span>
        <span style={{ color: C.text, fontFamily: "monospace", fontWeight: 700, flexShrink: 0 }}>
          {factor.points}
          <span style={{ color: C.textMuted, fontWeight: 500 }}> / {max}</span>
        </span>
      </div>

      <div style={{ height: 5, borderRadius: 3, background: C.bg, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${ratio * 100}%`,
          background: estimated
            ? `repeating-linear-gradient(90deg, ${C.textMuted} 0 4px, transparent 4px 8px)`
            : C.gradient,
          transition: "width 0.25s",
        }} />
      </div>

      {note && (
        <div style={{ fontSize: 10, color: estimated ? C.warn : C.textMuted }}>
          {estimated && <span aria-hidden="true">⌁ </span>}{note}
        </div>
      )}
    </div>
  );
}

export default function PlanningScoreBreakdown({ support }) {
  if (!support) return null;

  const factors = support.factors || {};
  const style = categoryStyle(support.category);
  const gated = Boolean(support.gate);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div style={{
          fontSize: 10, color: C.textMuted, textTransform: "uppercase",
          letterSpacing: 1, marginBottom: 9,
        }}>
          Decision Support Score — how it was calculated
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {FACTOR_ORDER.filter((key) => factors[key]).map((key) => (
            <FactorRow key={key} name={key} factor={factors[key]} />
          ))}
        </div>

        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginTop: 11, paddingTop: 9, borderTop: `1px solid ${C.borderAcc}`,
        }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: C.textSub }}>
            Decision Support Score
          </span>
          <span style={{ fontSize: 15, fontWeight: 800, fontFamily: "monospace", color: style.color }}>
            {support.score}
            <span style={{ color: C.textMuted, fontSize: 11, fontWeight: 500 }}> / 100</span>
          </span>
        </div>

        <div style={{ fontSize: 10, color: C.textMuted, marginTop: 5, lineHeight: 1.5 }}>
          A weighted sum of the factors above — not a calibrated probability.
          Thresholds: ≥ 80 recommended, ≥ 60 review carefully.
        </div>
      </div>

      {/* A gated category was not decided by the number, and says so, or the
          reader will try to reconcile a 64 with a blue badge. */}
      {gated && (
        <div style={{
          fontSize: 11, color: C.textSub, lineHeight: 1.55,
          background: `${style.color}0d`, border: `1px solid ${style.color}33`,
          borderRadius: 8, padding: "9px 12px",
        }}>
          <b style={{ color: style.color }}>Decided by a safety gate, not by the score.</b>{" "}
          {{
            capability_advisory:
              "The current SCTVA build has no safe automatic form for this refactoring, so it is marked manual regardless of how well it scored.",
            capability_unknown:
              "No refactoring is mapped for this smell type, so there is nothing SCTVA could execute.",
            step_not_mappable:
              "This concrete step is missing parameters the SCTVA action needs, so it would be sent as a no-op.",
            high_risk_cap:
              "High-risk transformations are never marked green automatically, however well they score. You can still approve it.",
          }[support.gate] || "A technical gate overrode the calculated category."}
        </div>
      )}

      {/* Uncertainty is preserved rather than rounded away. */}
      {typeof support.impact?.quality_gain_low === "number" &&
        typeof support.impact?.quality_gain_high === "number" &&
        support.impact.quality_gain_high > 0 && (
          <div style={{ fontSize: 10, color: C.textMuted }}>
            Quality gain is an estimate with a stated band: likely between{" "}
            <b style={{ color: C.textSub }}>+{support.impact.quality_gain_low}</b> and{" "}
            <b style={{ color: C.textSub }}>+{support.impact.quality_gain_high}</b> points.
          </div>
        )}
    </div>
  );
}
