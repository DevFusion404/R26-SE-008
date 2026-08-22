/**
 * ImpactChip.jsx
 * ==============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The inline answer to "if I tick this box, what do I get?", on every smell row.
 *
 *     [⚡ Auto-fixable]  +4.2 pts · low risk · ~12 min
 *     [⚠ Advisory]      6.1 pts by hand · no auto-fix
 *
 * The chip's colour is driven by capability.status, NOT by severity, and that
 * inversion is the whole point. Ten of the highest-severity smell types CUQA
 * reports map to refactorings SCTVA structurally cannot perform, so the
 * interface's loudest existing signal is anti-correlated with actionability. A
 * LargeClass must not out-shout a LongMethod when only one of them will change
 * any code.
 *
 * Renders nothing when there is no record — if the backend is older or the
 * impact endpoint failed, the smell list behaves exactly as it did before.
 */

import { C } from "../diwoTheme.jsx";

const TONE = {
  executable: { color: C.accent, icon: "⚡", label: "Auto-fixable" },
  advisory: { color: C.warn, icon: "⚠", label: "Advisory" },
  unknown: { color: C.textMuted, icon: "?", label: "Unclassified" },
};

const RISK_COLOR = { low: C.low, medium: C.warn, high: C.danger };

export default function ImpactChip({ record, compact = false, onExplain }) {
  if (!record) return null;

  const status = record.capability?.status || "unknown";
  const tone = TONE[status] || TONE.unknown;
  const gain = record.if_selected?.quality_gain || {};
  const risk = record.if_selected?.risk || {};
  const minutes = record.if_selected?.effort_minutes;
  const isExecutable = status === "executable";

  const facts = isExecutable
    ? [
        `+${gain.automated_points} pts`,
        `${risk.band} risk`,
        minutes ? `~${minutes} min` : null,
      ]
    : [`${gain.potential_points} pts by hand`, "no auto-fix"];

  return (
    <span
      title={record.headline}
      onClick={
        onExplain
          ? (e) => {
              e.stopPropagation();
              onExplain();
            }
          : undefined
      }
      style={{
        display: "inline-flex", alignItems: "center", gap: 7, flexShrink: 0,
        padding: compact ? "2px 8px" : "3px 10px",
        borderRadius: 999,
        background: `${tone.color}12`,
        border: `1px solid ${tone.color}44`,
        fontSize: compact ? 10 : 11,
        cursor: onExplain ? "pointer" : "default",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ color: tone.color, fontWeight: 800 }}>
        {tone.icon} {tone.label}
      </span>

      {facts.filter(Boolean).map((fact, index) => (
        <span key={fact} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <span style={{ color: `${C.textMuted}`, opacity: 0.6 }}>·</span>
          <span
            style={{
              color:
                index === 0 && isExecutable
                  ? C.accent
                  : index === 1 && isExecutable
                    ? RISK_COLOR[risk.band] || C.textSub
                    : C.textSub,
              fontWeight: index === 0 ? 700 : 500,
            }}
          >
            {fact}
          </span>
        </span>
      ))}

      {onExplain && (
        <span style={{ color: C.textMuted, fontWeight: 700, marginLeft: 1 }}>ⓘ</span>
      )}
    </span>
  );
}
