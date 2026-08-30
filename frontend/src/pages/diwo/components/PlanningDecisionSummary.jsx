/**
 * PlanningDecisionSummary.jsx
 * ===========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Stage 2 header: what DIWO makes of the whole plan, and the one bulk
 * action that is safe to offer.
 *
 *     REFACTORING PLAN REVIEW              12 RDP steps
 *     Developer Goal   [🛡][⚖ ✓][🚀]
 *
 *     🟢 Recommended 7   🟡 Review 3   🔴 Not recommended 1   🔵 Manual 1
 *
 *     Projected gain +14.3   Review effort ~42 min   Highest risk HIGH
 *
 *     [ ✓ Select 7 Recommended ]        [ Select all 12 ]
 *
 * "Select Recommended" replaces "Select All" as the primary action. That is
 * the point of the redesign: the old primary button's entire function was to
 * approve twelve steps without reading any of them, and it sat where the eye
 * lands first.
 *
 * Select All survives as a secondary action because rejecting it outright
 * would just push developers into clicking twelve times, but it states what it
 * is about to include before it does it.
 *
 * NEITHER BUTTON SUBMITS ANYTHING. Both set local approve/reject state on the
 * page. The developer still presses "Forward N Approved Steps" afterwards, and
 * that is the only action that reaches the Transformation stage.
 */

import DeveloperStrategySelector from "./DeveloperStrategySelector";
import { CategoryCount } from "./PlanningRecommendationBadge";
import {
  CATEGORY_ORDER, MANUAL_ONLY, RISK_COLOR,
  categoryStyle, formatMinutes, formatPoints, strategyLabel,
} from "../utils/planningDecisionSupport";
import { C, Card } from "../diwoTheme.jsx";

function Metric({ label, value, color = C.text, title }) {
  return (
    <div title={title} style={{ background: C.bg, borderRadius: 8, padding: "11px 12px" }}>
      <div style={{ fontSize: 18, fontWeight: 800, color, fontFamily: "monospace" }}>
        {value}
      </div>
      <div style={{
        fontSize: 9.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.8, marginTop: 2,
      }}>
        {label}
      </div>
    </div>
  );
}

/**
 * What a goal change did to the recommendation distribution.
 *
 * The developer goal is a control the developer has no reason to touch unless
 * they can see it working. "Safety First moved 2 steps out of Recommended and
 * into Review" is that evidence; the same three buttons with no visible
 * consequence are decoration.
 *
 * Rendered only when there is a real before-and-after to compare — the parent
 * passes null when no previous distribution exists, and a comparison against
 * an absent baseline would be an invented one.
 */
function StrategyDelta({ delta }) {
  if (!delta) return null;

  return (
    <div style={{
      marginTop: 12, padding: "9px 13px", borderRadius: 8,
      background: C.bg, border: `1px solid ${C.borderAcc}`,
      fontSize: 11, color: C.textSub,
      display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
    }}>
      <span style={{ color: C.textMuted }}>
        <b style={{ color: C.textSub }}>{strategyLabel(delta.from)}</b>
        {" → "}
        <b style={{ color: C.accent }}>{strategyLabel(delta.to)}</b>
      </span>

      {!delta.moved ? (
        <span style={{ color: C.textMuted }}>
          — the recommendation for every step stayed the same.
        </span>
      ) : (
        delta.changes.map((change) => {
          const style = categoryStyle(change.category);
          const shifted = change.delta !== 0;
          return (
            <span key={change.category} style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              color: shifted ? style.color : C.textMuted,
              fontWeight: shifted ? 700 : 500,
            }}>
              <span aria-hidden="true">{style.icon}</span>
              {style.short}
              <span style={{ fontFamily: "monospace" }}>
                {change.from} → {change.to}
              </span>
              {shifted && (
                <span style={{ fontFamily: "monospace" }}>
                  ({change.delta > 0 ? "+" : ""}{change.delta})
                </span>
              )}
            </span>
          );
        })
      )}
    </div>
  );
}

export default function PlanningDecisionSummary({
  summary,
  totalSteps,
  approved,
  rejected,
  manual = 0,
  pending,
  strategy,
  onStrategyChange,
  strategyDelta,
  activeFilter,
  onFilterCategory,
  strategyBusy = false,
  planSource,
}) {
  const unclassified = summary?.unclassified ?? 0;
  const maxRisk = summary?.max_risk || null;

  return (
    <Card style={{ marginBottom: 16 }} glow={C.accentGlow}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "flex-start", gap: 16, flexWrap: "wrap",
      }}>
        <div>
          <div style={{
            fontSize: 11, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 1.2, fontWeight: 700,
          }}>
            Refactoring Plan Review
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginTop: 5 }}>
            {totalSteps} RDP step{totalSteps === 1 ? "" : "s"} to review
            <span style={{ fontSize: 12, fontWeight: 500, color: C.textMuted, marginLeft: 10 }}>
              {approved} approved · {manual > 0 ? `${manual} manual · ` : ""}
              {rejected} rejected · {pending} pending
            </span>
          </div>
        </div>

        <DeveloperStrategySelector
          value={strategy}
          onChange={onStrategyChange}
          disabled={strategyBusy}
        />
      </div>

      <StrategyDelta delta={strategyDelta} />

      {/* ── What DIWO makes of the plan ─────────────────────────────────── */}
      <div style={{ marginTop: 18 }}>
        <div style={{
          fontSize: 10, color: C.textMuted, textTransform: "uppercase",
          letterSpacing: 1, marginBottom: 8, fontWeight: 700,
        }}>
          DIWO Decision Support
          {planSource && planSource !== "rdp_agent" && (
            <span style={{ color: C.warn, marginLeft: 8, textTransform: "none", letterSpacing: 0 }}>
              · based on a fallback plan, without RDP scoring evidence
            </span>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {CATEGORY_ORDER.map((category) => (
            <CategoryCount
              key={category}
              category={category}
              count={summary?.[category] ?? 0}
              active={activeFilter === category}
              onClick={onFilterCategory ? () => onFilterCategory(category) : undefined}
              title={
                onFilterCategory
                  ? `Show only these steps (${summary?.[category] ?? 0})`
                  : undefined
              }
            />
          ))}
          {unclassified > 0 && (
            <span
              title="These steps arrived without a recommendation — review them manually."
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "5px 11px", borderRadius: 8, background: C.bg,
                border: `1px dashed ${C.border}`, color: C.textMuted, fontSize: 11,
              }}
            >
              ○ Not assessed <b style={{ fontFamily: "monospace" }}>{unclassified}</b>
            </span>
          )}
        </div>
      </div>

      {/* ── The projection, for the recommended set only ─────────────────── */}
      <div style={{
        display: "grid", gap: 8, marginTop: 14,
        gridTemplateColumns: "repeat(auto-fit, minmax(132px, 1fr))",
      }}>
        <Metric
          label="Projected quality gain"
          value={formatPoints(summary?.projected_quality_gain)}
          color={C.accent}
          title="Estimated quality points recovered by the recommended steps. Approximate — the impact model states an error band."
        />
        <Metric
          label="Review effort"
          value={formatMinutes(summary?.estimated_review_minutes)}
          color={C.text}
          title="Rough review time for the recommended steps, so the gain and the cost describe the same set."
        />
        <Metric
          label="Whole plan effort"
          value={formatMinutes(summary?.total_review_minutes)}
          color={C.textSub}
          title="Review time if every step in the plan were approved."
        />
        <Metric
          label="Highest risk"
          value={maxRisk ? maxRisk.toUpperCase() : "—"}
          color={RISK_COLOR[maxRisk] || C.textMuted}
          title="The highest transformation-risk band present anywhere in this plan."
        />
      </div>

      {/* The bulk verdicts used to sit here. They moved to their own row on
          the page, directly under the review-mode switch: this card is the
          ASSESSMENT, and controls that act on every step at once read as part
          of the assessment when they are inside it. What stays is the warning
          below, which is about a decision in progress rather than a control. */}



      <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted, lineHeight: 1.55 }}>
        DIWO recommends; you decide. Selecting steps only marks them locally —
        nothing is transformed until you press{" "}
        <b style={{ color: C.textSub }}>Forward Approved Steps</b>, and only the
        steps you approved for automatic transformation are sent. Steps marked
        for manual work are recorded with the plan and stay with you.
      </div>
    </Card>
  );
}
