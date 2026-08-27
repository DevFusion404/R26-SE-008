/**
 * PlanningRecommendationBadge.jsx
 * ===============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The one-glance verdict on a Stage 2 plan step:
 *
 *     🟢 RECOMMENDED       87/100
 *     🟡 REVIEW CAREFULLY  71/100
 *     🔴 NOT RECOMMENDED   48/100
 *     🔵 MANUAL ONLY       —
 *
 * Colour is never the only signal: the icon and the word are always rendered,
 * so the four categories are distinguishable in greyscale and to a reader who
 * cannot separate the green from the red.
 *
 * A manual-only step shows no score. Its category came from a capability gate,
 * not from the arithmetic, and putting "64/100" beside "cannot be automated"
 * invites the developer to read the number as a recommendation strength it is
 * not — the gate already decided.
 *
 * Renders nothing without a recommendation, so a plan from an older backend
 * displays exactly as it did before.
 */

import { categoryStyle } from "../utils/planningDecisionSupport";
import { C } from "../diwoTheme.jsx";

export default function PlanningRecommendationBadge({
  support,
  compact = false,
  showScore = true,
}) {
  if (!support) return null;

  const style = categoryStyle(support.category);
  const isManual = support.category === "manual_only";
  const score = typeof support.score === "number" ? support.score : null;

  return (
    <span
      title={support.summary || style.label}
      style={{
        display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0,
        padding: compact ? "2px 9px" : "4px 12px",
        borderRadius: 999,
        background: `${style.color}14`,
        border: `1px solid ${style.color}55`,
        fontSize: compact ? 10 : 11,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ color: style.color, fontWeight: 800, letterSpacing: 0.6, textTransform: "uppercase" }}>
        <span aria-hidden="true">{style.icon}</span>{" "}
        {compact ? style.short : style.label}
      </span>

      {showScore && !isManual && score !== null && (
        <>
          <span style={{ color: C.textMuted, opacity: 0.6 }}>·</span>
          <span style={{ color: style.color, fontWeight: 700, fontFamily: "monospace" }}>
            {score}
            <span style={{ color: C.textMuted, fontWeight: 500 }}>/100</span>
          </span>
        </>
      )}
    </span>
  );
}

/**
 * The compact category counter used in the summary header and on file headers.
 * `onClick` turns it into a filter control; without it, it is read-only text.
 */
export function CategoryCount({ category, count, active = false, onClick, title }) {
  const style = categoryStyle(category);
  const interactive = typeof onClick === "function";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!interactive}
      title={title || `${count} step(s): ${style.label}`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 7,
        padding: "5px 11px", borderRadius: 8,
        background: active ? `${style.color}22` : C.bg,
        border: `1px solid ${active ? style.color : C.border}`,
        color: C.text, fontSize: 11, fontWeight: 600,
        cursor: interactive ? "pointer" : "default",
        transition: "all 0.15s",
      }}
    >
      <span aria-hidden="true">{style.icon}</span>
      <span style={{ color: count > 0 ? C.text : C.textMuted }}>{style.short}</span>
      <span style={{
        fontFamily: "monospace", fontWeight: 800,
        color: count > 0 ? style.color : C.textMuted,
      }}>
        {count}
      </span>
    </button>
  );
}
