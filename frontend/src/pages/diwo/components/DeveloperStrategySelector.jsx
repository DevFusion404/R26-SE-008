/**
 * DeveloperStrategySelector.jsx
 * =============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *     Developer Goal
 *     [ 🛡 Safety First ]  [ ⚖ Balanced ✓ ]  [ 🚀 Maximum Improvement ]
 *
 * Replaces the two technical dropdowns Stage 2 used to carry — "Risk:
 * Balanced" and "Impact: High" — with the question the developer can actually
 * answer. The dropdowns asked them to hold the re-ranker's weighting model in
 * their head; this asks what they are trying to achieve on this run.
 *
 * The backend vocabulary is unchanged: each goal expands server-side to the
 * risk_tolerance / impact_focus pair the re-ranker has always taken, so this
 * is a relabelling of the same control rather than a replacement for it.
 *
 * Changing the goal DOES ask the backend to re-rank the plan, which is the one
 * thing allowed to replace the plan mid-review. Individual Approve/Reject
 * clicks never do — see the note at the top of RefactoringPlanApprovalPage.
 */

import { STRATEGY_OPTIONS } from "../utils/planningDecisionSupport";
import { C } from "../diwoTheme.jsx";

export default function DeveloperStrategySelector({ value, onChange, disabled = false }) {
  const active = STRATEGY_OPTIONS.some((o) => o.value === value) ? value : "balanced";

  return (
    <div>
      <div style={{
        fontSize: 10, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 1, marginBottom: 8, fontWeight: 700,
      }}>
        Developer Goal
      </div>

      <div role="radiogroup" aria-label="Developer goal"
           style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {STRATEGY_OPTIONS.map((option) => {
          const selected = option.value === active;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => !selected && onChange?.(option.value)}
              title={option.hint}
              style={{
                display: "flex", alignItems: "center", gap: 7,
                padding: "8px 15px", borderRadius: 9,
                background: selected ? `${C.accent}18` : C.bg,
                border: `1px solid ${selected ? C.accent : C.border}`,
                color: selected ? C.accent : C.textSub,
                fontSize: 12, fontWeight: selected ? 700 : 600,
                cursor: disabled ? "not-allowed" : "pointer",
                opacity: disabled ? 0.55 : 1,
                transition: "all 0.18s",
              }}
            >
              <span aria-hidden="true">{option.icon}</span>
              <span>{option.label}</span>
              {/* Not colour alone: the selected goal carries a tick. */}
              {selected && <span aria-hidden="true" style={{ fontWeight: 800 }}>✓</span>}
            </button>
          );
        })}
      </div>

      <div style={{ fontSize: 11, color: C.textMuted, marginTop: 7 }}>
        {STRATEGY_OPTIONS.find((o) => o.value === active)?.hint}
        {" — changing this re-ranks the plan; your existing decisions are kept."}
      </div>
    </div>
  );
}
