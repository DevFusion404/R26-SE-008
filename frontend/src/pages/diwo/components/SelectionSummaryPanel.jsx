/**
 * SelectionSummaryPanel.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *     SELECTION SUMMARY                                    [Clear selection]
 *     Selected findings   Affected files   High severity   Auto-fixable   …
 *            37 / 57                  5               2             31
 *
 * The quick view, deliberately not the detailed one. TradeOffPanel already
 * answers "what does this selection buy me" with quality capture, forgone
 * points and the optimiser; repeating any of that here would give the developer
 * two panels to reconcile. This one answers only "what have I got selected".
 *
 * It USED TO BE a sticky right-hand column, which cost the findings list a
 * third of the page's width — the widest thing on the stage, and the one thing
 * on it that is actually read line by line. It now runs full width underneath
 * the list, laid out like the stat cards at the top of the stage, so the
 * numbers read left-to-right instead of as a tall stack of two-word rows.
 *
 * The per-mode explainer card that sat under it is gone. It described the
 * selection mode the developer had just chosen from a control three rows above,
 * which is a caption for a decision already made.
 *
 * Auto-fixable, Advisory and Estimated effort are HIDDEN when no impact records
 * exist, rather than shown as zero. A "0 auto-fixable" on a session where
 * capability was never computed is a claim about SCTVA that nothing supports.
 */

import { C } from "../diwoTheme.jsx";
import { formatEffort } from "../utils/smellGrouping";

/** One figure. `tone` is used only where severity earns it. */
function Stat({ label, value, tone, strong = false }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{
        fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {label}
      </div>
      <div style={{
        fontSize: strong ? 22 : 18,
        fontWeight: 800,
        color: tone || C.text,
        fontFamily: "monospace",
        lineHeight: 1.25,
        marginTop: 4,
      }}>
        {value}
      </div>
    </div>
  );
}

export default function SelectionSummaryPanel({ summary, onClear }) {
  const nothing = !summary || summary.selected === 0;
  const hasImpact = summary?.autoFixable !== null && summary?.autoFixable !== undefined;

  return (
    <section style={{
      marginTop: 14,
      background: C.panel,
      border: `1px solid ${nothing ? C.border : C.accent}`,
      borderRadius: 12,
      padding: "14px 18px",
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 12, flexWrap: "wrap", marginBottom: 12,
      }}>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase",
          color: C.textMuted,
        }}>
          Selection summary
        </div>

        {!nothing && onClear && (
          <button
            type="button"
            onClick={onClear}
            style={{
              padding: "6px 14px", borderRadius: 8,
              background: "none", border: `1px solid ${C.border}`,
              color: C.textMuted, fontSize: 11.5, fontWeight: 600, cursor: "pointer",
            }}
          >
            Clear selection
          </button>
        )}
      </div>

      <div style={{
        display: "grid", gap: 14,
        gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
      }}>
        <Stat
          label="Selected findings"
          value={`${summary?.selected ?? 0} / ${summary?.total ?? 0}`}
          tone={nothing ? C.textMuted : C.accent}
          strong
        />
        <Stat label="Affected files" value={summary?.fileCount ?? 0} />
        {summary?.high > 0 && (
          <Stat label="High severity" value={summary.high} tone={C.danger} />
        )}
        {summary?.medium > 0 && (
          <Stat label="Medium severity" value={summary.medium} tone={C.warn} />
        )}

        {/* Only when the impact records actually exist. */}
        {hasImpact && (
          <>
            <Stat label="Auto-fixable" value={summary.autoFixable} tone={C.accent} />
            <Stat label="Advisory" value={summary.advisory} tone={C.warn} />
          </>
        )}
        {typeof summary?.effortMinutes === "number" && (
          <Stat label="Estimated effort" value={formatEffort(summary.effortMinutes)} />
        )}
      </div>
    </section>
  );
}
