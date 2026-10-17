/**
 * TradeOffPanel.jsx
 * =================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * What the current selection buys and what the rejections cost, plus the
 * selection optimiser.
 *
 * The capture bar is the reframing: "you are capturing 41% of what this run
 * could achieve" is a different question from "you have ticked 7 boxes".
 *
 * Lives inside the right-hand selection panel, so it is laid out for a narrow
 * column: everything stacks, and the two Selected / Skipped sides wrap onto
 * separate rows when there is no room for both. `embedded` drops the outer
 * card chrome when the parent already supplies it.
 *
 * Every figure comes from summariseSelection() over the backend's impact
 * records; nothing is rendered when they are absent.
 */

import { T, tint, eyebrow, riskTone } from "../codeSmellTheme.js";
import { PRESETS } from "../utils/impactPresets";

// Section labels a step larger than the shared eyebrow, to match the
// Smell Selection section beside this panel.
const eyebrowLg = { ...eyebrow, fontSize: 12 };


const LEVEL = {
  error: { color: T.high, icon: "⛔" },
  warning: { color: T.medium, icon: "⚠" },
  info: { color: T.info, icon: "ℹ" },
};

export default function TradeOffPanel({
  summary,
  interactionNotes = [],
  optimising = false,
  onOptimise,
  budgetMinutes,
  onBudgetChange,
  embedded = false,
}) {
  if (!summary) return null;

  const capturePct = Math.round((summary.capture_rate || 0) * 100);
  const headroom = Math.max(0, summary.quality_ceiling - summary.quality_projected);
  const notes = [...(summary.warnings || []), ...interactionNotes];

  return (
    <div style={embedded ? {} : {
      marginTop: 16, padding: "16px 18px", borderRadius: 12,
      background: T.surface, border: `1px solid ${T.border}`, boxShadow: T.shadowSm,
    }}>
      {/* ── Capture bar ───────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <span style={eyebrowLg}>Quality capture</span>
        <span style={{
          fontSize: 18, fontWeight: 800, fontFamily: T.mono,
          color: capturePct > 0 ? T.accentText : T.text3,
        }}>
          {capturePct}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Share of available automated quality gain captured by this selection"
        aria-valuenow={capturePct}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{ height: 6, borderRadius: 4, background: T.sunken, border: `1px solid ${T.border}`, overflow: "hidden" }}
      >
        <div style={{ height: "100%", width: `${capturePct}%`, background: T.accent, transition: "width 0.25s ease" }} />
      </div>

      <div style={{ fontSize: 13, color: T.text2, marginTop: 8, lineHeight: 1.6 }}>
        Quality <Mono>{summary.quality_before}</Mono> → <Mono tone={T.accentText}>{summary.quality_projected}</Mono>
        {" "}projected · ceiling <Mono tone={T.medium}>{summary.quality_ceiling}</Mono>
        {headroom > 0.05 && <> · <Mono tone={T.medium}>{headroom.toFixed(1)}</Mono> pts unclaimed</>}
        <div style={{ color: T.text3, fontSize: 12 }}>
          ±{Math.round((summary.error_band ?? 0.35) * 100)}% static estimate
        </div>
      </div>

      {/* ── What is in, what is out ───────────────────────────────────────── */}
      <div style={{
        display: "grid", gap: 8, marginTop: 12,
        gridTemplateColumns: "repeat(auto-fit, minmax(128px, 1fr))",
      }}>
        <Side
          title="Selected"
          accent={T.accent}
          titleTone={T.accentText}
          facts={[
            `${summary.selected_count} smell${summary.selected_count === 1 ? "" : "s"}`,
            `${summary.executable_count} auto-fixable`,
            summary.advisory_count ? `${summary.advisory_count} advisory` : null,
            `~${summary.effort_minutes} min`,
            summary.executable_count ? { text: `max risk ${riskBand(summary.max_risk)}`, tone: riskTone(riskBand(summary.max_risk)) } : null,
          ]}
        />
        <Side
          title="Skipped"
          accent={T.medium}
          titleTone={T.medium}
          facts={[
            `${summary.skipped_count} smell${summary.skipped_count === 1 ? "" : "s"}`,
            `${summary.forgone_points} pts forgone`,
            `${summary.quarterly_interest} pts/quarter interest`,
          ]}
        />
      </div>

      {/* ── Warnings and interaction notes ────────────────────────────────── */}
      {notes.length > 0 && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
          {notes.map((note, index) => {
            const tone = LEVEL[note.level] || LEVEL.info;
            return (
              <div
                key={`${note.level}-${index}`}
                role={note.level === "error" ? "alert" : undefined}
                style={{
                  display: "flex", gap: 8, alignItems: "flex-start",
                  padding: "8px 10px", borderRadius: 8,
                  background: tint(tone.color, 9), border: `1px solid ${tint(tone.color, 30)}`,
                }}
              >
                <span aria-hidden="true" style={{ color: tone.color, flexShrink: 0 }}>{tone.icon}</span>
                <span style={{ fontSize: 13, color: T.text, lineHeight: 1.5 }}>
                  {note.message}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Optimiser ─────────────────────────────────────────────────────── */}
      {onOptimise && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${T.border}` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
            <span style={eyebrowLg}>Optimise selection</span>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: T.text2 }}>
              Budget
              <input
                type="number"
                min={5}
                step={5}
                value={budgetMinutes}
                onChange={(e) => onBudgetChange?.(Number(e.target.value))}
                aria-label="Review budget in minutes"
                className="dw-input"
                style={{
                  width: 60, background: T.sunken, border: `1px solid ${T.border}`,
                  borderRadius: 6, padding: "4px 7px", color: T.text, fontSize: 13,
                  fontFamily: T.mono,
                }}
              />
              min
            </label>
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {PRESETS.map((preset) => (
              <button
                key={preset.key}
                type="button"
                disabled={optimising}
                title={preset.description}
                onClick={() => onOptimise(preset.key)}
                className="dw-focus dw-hover"
                style={{
                  padding: "6px 11px", borderRadius: 7, fontSize: 13, fontWeight: 700,
                  background: T.surface, color: optimising ? T.text3 : T.text2,
                  border: `1px solid ${T.border}`,
                  cursor: optimising ? "wait" : "pointer",
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div style={{ fontSize: 12, color: T.text3, marginTop: 6, lineHeight: 1.5 }}>
            Replaces the current selection with DIWO's suggestion and switches to Smell wise so you can edit it.
          </div>
        </div>
      )}
    </div>
  );
}

function riskBand(score) {
  if (score < 0.35) return "low";
  return score < 0.65 ? "medium" : "high";
}

function Mono({ children, tone = T.text }) {
  return (
    <span style={{ fontFamily: T.mono, fontWeight: 700, color: tone }}>{children}</span>
  );
}

function Side({ title, accent, titleTone, facts }) {
  return (
    <div style={{
      padding: "9px 11px", borderRadius: 8,
      background: tint(accent, 6), border: `1px solid ${tint(accent, 24)}`,
    }}>
      <div style={{ ...eyebrowLg, color: titleTone, marginBottom: 5 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {facts.filter(Boolean).map((fact) => {
          const text = typeof fact === "string" ? fact : fact.text;
          return (
            <span key={text} style={{ fontSize: 13, color: typeof fact === "string" ? T.text2 : fact.tone }}>
              {text}
            </span>
          );
        })}
      </div>
    </div>
  );
}
