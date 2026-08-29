/**
 * SelectionSummaryPanel.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *     SELECTION SUMMARY
 *     Selected findings        37 / 57
 *     Affected files                 5
 *     High severity                  2
 *     Auto-fixable                  31
 *     Advisory                       6
 *     Estimated effort         ~1h 10m
 *
 *     File-wise review
 *     Selecting a file sends every detected smell in it to planning.
 *
 * The quick view, deliberately not the detailed one. TradeOffPanel already
 * answers "what does this selection buy me" with quality capture, forgone
 * points and the optimiser; repeating any of that here would give the developer
 * two panels to reconcile. This one answers only "what have I got selected".
 *
 * Auto-fixable, Advisory and Estimated effort are HIDDEN when no impact records
 * exist, rather than shown as zero. A "0 auto-fixable" on a session where
 * capability was never computed is a claim about SCTVA that nothing supports.
 */

import { C } from "../diwoTheme.jsx";
import { formatEffort } from "../utils/smellGrouping";

/** One line of the summary. `tone` is used only where severity earns it. */
function Row({ label, value, tone, strong = false }) {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", justifyContent: "space-between",
      gap: 12, padding: "6px 0",
    }}>
      <span style={{ fontSize: 11.5, color: C.textMuted }}>{label}</span>
      <span style={{
        fontSize: strong ? 14 : 12.5,
        fontWeight: strong ? 800 : 700,
        color: tone || C.text,
        fontFamily: "monospace",
      }}>
        {value}
      </span>
    </div>
  );
}

const TIPS = {
  file: {
    title: "File-wise review",
    body: "Selecting a file sends every detected smell in it to planning. Switch to Smell wise if you only want some findings from a file.",
  },
  smell: {
    title: "Smell-wise review",
    body: "Use this when the same smell appears across several files. Selecting the parent smell type selects all of its occurrences.",
  },
  category: {
    title: "Category-wise review",
    body: "Use this to review related smell families together. Expand a smell type when you need individual control.",
  },
};

export default function SelectionSummaryPanel({ summary, mode, onClear }) {
  const tip = TIPS[mode] || TIPS.smell;
  const nothing = !summary || summary.selected === 0;

  return (
    <aside style={{
      position: "sticky", top: 12, alignSelf: "start",
      display: "flex", flexDirection: "column", gap: 12,
    }}>
      <div style={{
        background: C.panel, border: `1px solid ${nothing ? C.border : C.accent}`,
        borderRadius: 12, padding: "16px 18px",
      }}>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase",
          color: C.textMuted, marginBottom: 10,
        }}>
          Selection summary
        </div>

        <Row
          label="Selected findings"
          value={`${summary?.selected ?? 0} / ${summary?.total ?? 0}`}
          tone={nothing ? C.textMuted : C.accent}
          strong
        />
        <Row label="Affected files" value={summary?.fileCount ?? 0} />
        {summary?.high > 0 && (
          <Row label="High severity" value={summary.high} tone={C.danger} />
        )}
        {summary?.medium > 0 && (
          <Row label="Medium severity" value={summary.medium} tone={C.warn} />
        )}

        {/* Only when the impact records actually exist. */}
        {summary?.autoFixable !== null && summary?.autoFixable !== undefined && (
          <>
            <div style={{ height: 1, background: C.border, margin: "8px 0" }} />
            <Row label="Auto-fixable" value={summary.autoFixable} tone={C.accent} />
            <Row label="Advisory" value={summary.advisory} tone={C.warn} />
          </>
        )}
        {typeof summary?.effortMinutes === "number" && (
          <Row label="Estimated effort" value={formatEffort(summary.effortMinutes)} />
        )}

        {!nothing && onClear && (
          <button
            type="button"
            onClick={onClear}
            style={{
              width: "100%", marginTop: 12, padding: "7px 12px", borderRadius: 8,
              background: "none", border: `1px solid ${C.border}`,
              color: C.textMuted, fontSize: 11.5, fontWeight: 600, cursor: "pointer",
            }}
          >
            Clear selection
          </button>
        )}
      </div>

      <div style={{
        background: C.panel, border: `1px solid ${C.border}`,
        borderRadius: 12, padding: "13px 16px",
      }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, marginBottom: 5 }}>
          {tip.title}
        </div>
        <div style={{ fontSize: 11.5, color: C.textMuted, lineHeight: 1.6 }}>
          {tip.body}
        </div>
      </div>
    </aside>
  );
}
