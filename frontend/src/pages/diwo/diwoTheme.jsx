export const C = {
  bg: "#0d0f14", panel: "#12151c", border: "#1e2330", borderAcc: "#2a3040",
  accent: "#00d4aa", accentDim: "#00a882", accentGlow: "rgba(0,212,170,0.12)",
  warn: "#f59e0b", warnGlow: "rgba(245,158,11,0.12)",
  danger: "#ef4444", dangerGlow: "rgba(239,68,68,0.12)",
  info: "#3b82f6", infoGlow: "rgba(59,130,246,0.12)",
  text: "#e2e8f0", textMuted: "#64748b", textSub: "#94a3b8",
  high: "#ef4444", medium: "#f59e0b", low: "#22c55e",
  gradient: "linear-gradient(135deg, #00d4aa 0%, #3b82f6 100%)",
};

export const severityColor = (s) => ({ high: C.high, medium: C.medium, low: C.low }[s] || C.textMuted);
export const impactColor = (i) => ({ high: C.accent, medium: C.warn, low: C.textSub }[i] || C.textMuted);
export const riskColor = (r) => ({ low: C.low, medium: C.warn, high: C.danger }[r] || C.textMuted);

export const Badge = ({ label, color, bg }) => (
  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1, padding: "2px 8px", borderRadius: 4, background: bg || `${color}18`, color: color || C.textMuted, textTransform: "uppercase", border: `1px solid ${color || C.textMuted}30` }}>{label}</span>
);

export const Pill = ({ label, color }) => (
  <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, background: `${color}15`, color, border: `1px solid ${color}30` }}>{label}</span>
);

export const Card = ({ children, style = {}, glow }) => (
  <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: "20px 24px", ...(glow ? { boxShadow: `0 0 24px ${glow}` } : {}), ...style }}>{children}</div>
);

export const STEPS = ["Code Smell Review", "Refactoring Plan", "Transformation", "Results"];

export function StepBar({ current }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 0, marginBottom: 32 }}>
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={s} style={{ display: "flex", alignItems: "center", flex: i < STEPS.length - 1 ? 1 : "none" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, minWidth: 80 }}>
              <div style={{
                width: 36, height: 36, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                background: done ? C.accent : active ? "transparent" : C.panel,
                border: `2px solid ${done ? C.accent : active ? C.accent : C.border}`,
                color: done ? "#000" : active ? C.accent : C.textMuted,
                fontWeight: 700, fontSize: 14,
                boxShadow: active ? `0 0 16px ${C.accentGlow}` : "none",
                transition: "all 0.3s"
              }}>
                {done ? "✓" : i + 1}
              </div>
              <span style={{ fontSize: 11, color: active ? C.accent : done ? C.textSub : C.textMuted, fontWeight: active ? 700 : 400, whiteSpace: "nowrap" }}>{s}</span>
            </div>
            {i < STEPS.length - 1 && (
              <div style={{ flex: 1, height: 2, background: done ? C.accent : C.border, margin: "0 4px", marginBottom: 20, transition: "background 0.3s" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}
