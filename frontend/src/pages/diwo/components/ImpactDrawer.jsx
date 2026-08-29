/**
 * ImpactDrawer.jsx
 * ================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The counterfactual, side by side: what selecting this smell buys, and what
 * skipping it costs. Two columns rather than one, because a panel that only
 * shows upside is an advocacy tool, not a decision tool.
 *
 * Every figure on screen traces to a named input — the WHY row is the model's
 * own `quality_gain.explanation`, the risk bullets are `risk.drivers`, the
 * deferral line is `if_deferred.explanation`. Nothing is unattributed, which is
 * the property that lets a developer disagree with a number instead of just
 * distrusting it.
 *
 * Quality gains are shown as a BAND, not a point value. The static tier carries
 * ±35% by construction; presenting 4.2 as though it were measured would be the
 * kind of false precision the whole design is arguing against.
 */

import { useEffect } from "react";
import { C, Badge, severityColor } from "../diwoTheme.jsx";
import { smellIcon } from "../utils/smellIcons";

const RISK_COLOR = { low: C.low, medium: C.warn, high: C.danger };
const PRESSURE_COLOR = { low: C.textMuted, medium: C.warn, high: C.danger };

export default function ImpactDrawer({ record, smell, onClose, onToggleSmell, isSelected }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!record) return null;

  const capability = record.capability || {};
  const selectedSide = record.if_selected || {};
  const deferred = record.if_deferred || {};
  const gain = selectedSide.quality_gain || {};
  const risk = selectedSide.risk || {};
  const isExecutable = capability.status === "executable";

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1100,
        background: "rgba(4,6,10,0.72)", backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(940px, 100%)", maxHeight: "88vh", overflowY: "auto",
          background: C.bg, border: `1px solid ${C.borderAcc}`, borderRadius: 14,
          boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 12, padding: "16px 20px",
          borderBottom: `1px solid ${C.border}`, background: C.panel,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
              {/* The same glyph the list uses, so the dialog is recognisably
                  about the row that opened it. */}
              <span
                role="img"
                aria-label={record.smell_type}
                style={{
                  width: 24, height: 24, borderRadius: 7, flexShrink: 0, lineHeight: 1,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  fontSize: 14,
                  background: `${severityColor(record.severity)}1e`,
                  border: `1px solid ${severityColor(record.severity)}55`,
                }}
              >
                {smellIcon(record.smell_type, smell?.category)}
              </span>
              <span style={{ fontSize: 15, fontWeight: 800, color: C.text }}>
                {record.smell_type}
              </span>
              <Badge label={record.severity || "unknown"} color={severityColor(record.severity)} />
              <Badge
                label={isExecutable ? "AUTO-FIXABLE" : "ADVISORY"}
                color={isExecutable ? C.accent : C.warn}
              />
            </div>
            <div style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
              {record.file}{record.line ? `:${record.line}` : ""}
              {smell?.entity ? ` · ${smell.entity}` : ""}
            </div>
          </div>

          <button onClick={onClose} style={ghostButton}>Close ✕</button>
        </div>

        {/* ── The two branches ───────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
          <Column title="IF YOU SELECT" accent={C.accent}>
            <Row
              label="Quality"
              value={
                isExecutable
                  ? `+${gain.automated_points} pts`
                  : `no automated gain`
              }
              tone={isExecutable ? C.accent : C.textMuted}
              hint={
                isExecutable
                  ? `range ${gain.automated_low}–${gain.automated_high} pts`
                  : `${gain.potential_points} pts if fixed by hand`
              }
            />
            <Row label="Refactoring" value={capability.refactoring || "—"} />
            <Row
              label="SCTVA action"
              value={capability.action_type || "none — no-op"}
              tone={isExecutable ? C.accent : C.warn}
              mono
            />
            <Row
              label="Risk"
              value={`${(risk.band || "—").toUpperCase()} (${risk.score})`}
              tone={RISK_COLOR[risk.band] || C.textSub}
            />
            <Bullets items={risk.drivers} />
            <Row label="Effort" value={`~${selectedSide.effort_minutes} min review`} />
            <Row label="Blast radius" value={`${selectedSide.blast_radius_files} file(s)`} />
            <Row
              label="Validation"
              value={(selectedSide.validation || []).join(", ") || "—"}
            />
          </Column>

          <Column title="IF YOU SKIP" accent={C.warn} bordered>
            <Row
              label="Carries forward"
              value={`${deferred.carried_points} pts`}
              tone={C.warn}
            />
            <Row
              label="Interest"
              value={`${deferred.interest_per_quarter} pts / quarter`}
              tone={C.warn}
              hint="debt weighted by how often this file is edited"
            />
            <Row
              label="Change pressure"
              value={
                deferred.churn_known
                  ? `${(deferred.change_pressure || "—").toUpperCase()} · ${deferred.churn_commits} commits / ${deferred.churn_window_days}d`
                  : "unknown — no git history available"
              }
              tone={
                deferred.churn_known
                  ? PRESSURE_COLOR[deferred.change_pressure] || C.textSub
                  : C.textMuted
              }
            />
            <Row label="Risk now" value="none — the file is untouched" tone={C.low} />
            <Row label="Next CUQA run" value="re-detected, unchanged" />
            <div style={{ fontSize: 11, color: C.textMuted, lineHeight: 1.6, marginTop: 10 }}>
              {deferred.explanation}
            </div>
          </Column>
        </div>

        {/* ── Why ────────────────────────────────────────────────────────── */}
        <div style={{
          padding: "14px 20px", borderTop: `1px solid ${C.border}`, background: C.panel,
        }}>
          <div style={{
            fontSize: 10, fontWeight: 800, letterSpacing: 1, color: C.textMuted,
            textTransform: "uppercase", marginBottom: 6,
          }}>
            Why this number
          </div>
          <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.65 }}>
            {gain.explanation}
          </div>
          {!isExecutable && (
            <div style={{ fontSize: 12, color: C.warn, lineHeight: 1.65, marginTop: 8 }}>
              {capability.reason}
            </div>
          )}

          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 12 }}>
            {Object.entries(gain.factors || {}).map(([name, value]) => (
              <Factor key={name} name={name} value={value} />
            ))}
          </div>

          <div style={{ fontSize: 10, color: C.textMuted, marginTop: 12 }}>
            {record.tier === "static" ? "Static model" : record.tier} ·{" "}
            ±{Math.round((record.error_band || 0) * 100)}% · {record.model_version}
          </div>
        </div>

        {onToggleSmell && (
          <div style={{ padding: "12px 20px", borderTop: `1px solid ${C.border}` }}>
            <button
              onClick={() => onToggleSmell(record.smell_id)}
              style={{
                padding: "9px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "none",
                background: isSelected ? C.border : C.accent,
                color: isSelected ? C.textSub : "#000",
              }}
            >
              {isSelected ? "Remove from selection" : "Add to selection"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────────

const ghostButton = {
  padding: "6px 13px", borderRadius: 8, fontSize: 12, fontWeight: 700,
  background: C.panel, color: C.textSub, border: `1px solid ${C.border}`,
  cursor: "pointer", flexShrink: 0,
};

function Column({ title, accent, bordered = false, children }) {
  return (
    <div style={{
      padding: "16px 20px",
      borderLeft: bordered ? `1px solid ${C.border}` : "none",
      minWidth: 0,
    }}>
      <div style={{
        fontSize: 10, fontWeight: 800, letterSpacing: 1.2, color: accent,
        textTransform: "uppercase", marginBottom: 12,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value, tone = C.text, hint, mono = false }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 9, alignItems: "baseline" }}>
      <span style={{
        fontSize: 11, color: C.textMuted, width: 108, flexShrink: 0,
      }}>
        {label}
      </span>
      <span style={{ minWidth: 0 }}>
        <span style={{
          fontSize: 12, fontWeight: 700, color: tone,
          fontFamily: mono ? "monospace" : "inherit",
        }}>
          {value}
        </span>
        {hint && (
          <span style={{ fontSize: 10, color: C.textMuted, marginLeft: 8 }}>{hint}</span>
        )}
      </span>
    </div>
  );
}

function Bullets({ items }) {
  if (!items?.length) return null;
  return (
    <ul style={{ margin: "0 0 10px 118px", padding: 0, listStyle: "none" }}>
      {items.map((item) => (
        <li key={item} style={{ fontSize: 11, color: C.textMuted, lineHeight: 1.6 }}>
          • {item}
        </li>
      ))}
    </ul>
  );
}

const FACTOR_LABEL = {
  severity: "severity",
  magnitude: "past threshold",
  reach: "share of file",
  refactoring_impact: "refactoring impact",
};

function Factor({ name, value }) {
  return (
    <div style={{ minWidth: 96 }}>
      <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 3 }}>
        {FACTOR_LABEL[name] || name}
      </div>
      <div style={{
        height: 4, borderRadius: 2, background: C.border, overflow: "hidden", marginBottom: 3,
      }}>
        <div style={{
          height: "100%", width: `${Math.min(100, Math.max(0, value * 100))}%`,
          background: C.gradient,
        }} />
      </div>
      <div style={{ fontSize: 10, color: C.textSub, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}
