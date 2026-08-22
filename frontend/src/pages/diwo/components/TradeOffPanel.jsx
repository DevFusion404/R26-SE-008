/**
 * TradeOffPanel.jsx
 * =================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Replaces the count-only footer of the Code Smell Review stage.
 *
 * The old footer said "7 smells selected · 3 files affected · 1 high severity",
 * which counts things without answering anything. This says how much of the
 * available improvement the current selection actually captures, what the
 * rejections cost, and which selections will come back as no-ops.
 *
 * The capture bar is the reframing: "you are capturing 41% of what this run
 * could achieve" is a different question from "you have ticked 7 boxes".
 *
 * Degrades to the original counters when no impact records are available, so a
 * missing backend never blocks the stage.
 */

import { C } from "../diwoTheme.jsx";
import { PRESETS } from "../utils/impactPresets";

const LEVEL = {
  error: { color: C.danger, icon: "⛔" },
  warning: { color: C.warn, icon: "⚠" },
  info: { color: C.info, icon: "ℹ" },
};

const RISK_COLOR = { low: C.low, medium: C.warn, high: C.danger };

export default function TradeOffPanel({
  summary,
  interactionNotes = [],
  optimising = false,
  onOptimise,
  budgetMinutes,
  onBudgetChange,
}) {
  if (!summary) return null;

  const capturePct = Math.round((summary.capture_rate || 0) * 100);
  const headroom = Math.max(0, summary.quality_ceiling - summary.quality_projected);
  const notes = [...(summary.warnings || []), ...interactionNotes];

  return (
    <div style={{
      marginTop: 16, padding: "16px 20px", borderRadius: 10,
      background: C.panel, border: `1px solid ${C.border}`,
    }}>
      {/* ── Capture bar ───────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <span style={{
          fontSize: 10, fontWeight: 800, letterSpacing: 1.2, color: C.textMuted,
          textTransform: "uppercase", flexShrink: 0,
        }}>
          Quality capture
        </span>
        <div style={{
          flex: 1, height: 8, borderRadius: 4, background: C.border,
          overflow: "hidden", minWidth: 120,
        }}>
          <div style={{
            height: "100%", width: `${capturePct}%`,
            background: C.gradient, transition: "width 0.25s ease",
          }} />
        </div>
        <span style={{
          fontSize: 14, fontWeight: 800, color: capturePct > 0 ? C.accent : C.textMuted,
          fontFamily: "monospace", flexShrink: 0, minWidth: 44, textAlign: "right",
        }}>
          {capturePct}%
        </span>
      </div>

      <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 14 }}>
        Now <Mono>{summary.quality_before}</Mono> → projected{" "}
        <Mono tone={C.accent}>{summary.quality_projected}</Mono>
        {"  ·  ceiling "}
        <Mono tone={C.warn}>{summary.quality_ceiling}</Mono> if every fixable smell were taken
        {headroom > 0.05 && <> · <Mono tone={C.warn}>{headroom.toFixed(1)}</Mono> pts unclaimed</>}
        {" · ±"}{Math.round((summary.error_band ?? 0.35) * 100)}% static estimate
      </div>

      {/* ── Two rows: what is in, what is out ─────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <Side
          title="Selected"
          accent={C.accent}
          facts={[
            `${summary.selected_count} smell${summary.selected_count === 1 ? "" : "s"}`,
            `${summary.executable_count} auto-fixable`,
            summary.advisory_count ? `${summary.advisory_count} advisory` : null,
            `~${summary.effort_minutes} min`,
            summary.executable_count
              ? `max risk ${riskBand(summary.max_risk)}`
              : null,
          ]}
          riskBandName={summary.executable_count ? riskBand(summary.max_risk) : null}
        />
        <Side
          title="Skipped"
          accent={C.warn}
          facts={[
            `${summary.skipped_count} smell${summary.skipped_count === 1 ? "" : "s"}`,
            `${summary.forgone_points} pts forgone`,
            `${summary.quarterly_interest} pts/quarter interest`,
          ]}
        />
      </div>

      {/* ── Warnings and interaction notes ────────────────────────────────── */}
      {notes.length > 0 && (
        <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 6 }}>
          {notes.map((note, index) => {
            const tone = LEVEL[note.level] || LEVEL.info;
            return (
              <div
                key={`${note.level}-${index}`}
                style={{
                  display: "flex", gap: 8, alignItems: "flex-start",
                  padding: "7px 11px", borderRadius: 7,
                  background: `${tone.color}0d`, border: `1px solid ${tone.color}33`,
                }}
              >
                <span style={{ color: tone.color, flexShrink: 0 }}>{tone.icon}</span>
                <span style={{ fontSize: 11, color: C.textSub, lineHeight: 1.55 }}>
                  {note.message}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Optimiser ─────────────────────────────────────────────────────── */}
      {onOptimise && (
        <div style={{
          marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.border}`,
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <span style={{
            fontSize: 10, fontWeight: 800, letterSpacing: 1, color: C.textMuted,
            textTransform: "uppercase",
          }}>
            Optimise for me
          </span>

          {PRESETS.map((preset) => (
            <button
              key={preset.key}
              disabled={optimising}
              title={preset.description}
              onClick={() => onOptimise(preset.key)}
              style={{
                padding: "6px 13px", borderRadius: 7, fontSize: 11, fontWeight: 700,
                background: C.bg, color: optimising ? C.textMuted : C.textSub,
                border: `1px solid ${C.border}`,
                cursor: optimising ? "wait" : "pointer",
              }}
            >
              {preset.label}
            </button>
          ))}

          <label style={{
            display: "flex", alignItems: "center", gap: 6, marginLeft: "auto",
            fontSize: 11, color: C.textMuted,
          }}>
            Budget
            <input
              type="number"
              min={5}
              step={5}
              value={budgetMinutes}
              onChange={(e) => onBudgetChange?.(Number(e.target.value))}
              style={{
                width: 62, background: C.bg, border: `1px solid ${C.border}`,
                borderRadius: 6, padding: "4px 8px", color: C.text, fontSize: 11,
                outline: "none",
              }}
            />
            min
          </label>
        </div>
      )}
    </div>
  );
}

function riskBand(score) {
  if (score < 0.35) return "low";
  return score < 0.65 ? "medium" : "high";
}

function Mono({ children, tone = C.textSub }) {
  return (
    <span style={{ fontFamily: "monospace", fontWeight: 700, color: tone }}>{children}</span>
  );
}

function Side({ title, accent, facts, riskBandName }) {
  return (
    <div style={{
      padding: "10px 13px", borderRadius: 8,
      background: `${accent}08`, border: `1px solid ${accent}26`,
    }}>
      <div style={{
        fontSize: 10, fontWeight: 800, letterSpacing: 1, color: accent,
        textTransform: "uppercase", marginBottom: 6,
      }}>
        {title}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px" }}>
        {facts.filter(Boolean).map((fact) => (
          <span
            key={fact}
            style={{
              fontSize: 11,
              color: riskBandName && fact.startsWith("max risk")
                ? RISK_COLOR[riskBandName]
                : C.textSub,
            }}
          >
            {fact}
          </span>
        ))}
      </div>
    </div>
  );
}
