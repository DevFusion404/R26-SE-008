export const C = {
  bg: "var(--bg-root)", panel: "var(--bg-card)", border: "var(--border)", borderAcc: "var(--border-light)",
  accent: "#00d4aa", accentDim: "#00a882", accentGlow: "rgba(0,212,170,0.12)",
  warn: "#f59e0b", warnGlow: "rgba(245,158,11,0.12)",
  danger: "#ef4444", dangerGlow: "rgba(239,68,68,0.12)",
  info: "#3b82f6", infoGlow: "rgba(59,130,246,0.12)",
  text: "var(--text-primary)", textMuted: "var(--text-muted)", textSub: "var(--text-secondary)",
  high: "#ef4444", medium: "#f59e0b", low: "#22c55e",
  gradient: "linear-gradient(135deg, #00d4aa 0%, #3b82f6 100%)",
};

export const severityColor = (s) => ({ high: C.high, medium: C.medium, low: C.low }[s] || C.textMuted);
export const impactColor = (i) => ({ high: C.accent, medium: C.warn, low: C.textSub }[i] || C.textMuted);
export const riskColor = (r) => ({ low: C.low, medium: C.warn, high: C.danger }[r] || C.textMuted);

export const Badge = ({ label, color, bg }) => (
  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1, padding: "2px 8px", borderRadius: 4, background: bg || `color-mix(in srgb, ${color || C.textMuted} 10%, transparent)`, color: color || C.textMuted, textTransform: "uppercase", border: `1px solid color-mix(in srgb, ${color || C.textMuted} 22%, transparent)` }}>{label}</span>
);

export const Pill = ({ label, color }) => (
  <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, background: `color-mix(in srgb, ${color} 9%, transparent)`, color, border: `1px solid color-mix(in srgb, ${color} 20%, transparent)` }}>{label}</span>
);

export const Card = ({ children, style = {}, glow }) => (
  <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: "20px 24px", ...(glow ? { boxShadow: `0 0 24px ${glow}` } : {}), ...style }}>{children}</div>
);

export const STEPS = ["Code Smell Review", "Refactoring Plan", "Transformation", "Results"];

/**
 * `current` is how far the workflow has progressed; `viewing` is the stage on
 * screen, which can be an earlier, completed one. Only stages the workflow has
 * reached are clickable — a stage not yet reached has nothing to show.
 */
export function StepBar({ current, viewing = current, onSelect }) {
  return (
    <div className="diwo-stepbar" style={{ display: "flex", alignItems: "center", gap: 0, marginBottom: 32 }}>
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === viewing;
        const clickable = Boolean(onSelect) && i <= current && i !== viewing;
        const Tag = clickable ? "button" : "div";
        return (
          <div key={s} style={{ display: "flex", alignItems: "center", flex: i < STEPS.length - 1 ? 1 : "none" }}>
            <Tag
              {...(clickable ? {
                type: "button",
                onClick: () => onSelect(i),
                title: i === current ? `Return to ${s}` : `Review ${s} (completed)`,
                "aria-label": i === current ? `Return to ${s}` : `Review completed stage: ${s}`,
              } : { "aria-current": active ? "step" : undefined })}
              style={{
                display: "flex", flexDirection: "column", alignItems: "center", gap: 6, minWidth: 80,
                background: "none", border: "none", padding: 0, font: "inherit",
                cursor: clickable ? "pointer" : "default",
              }}
            >
              <div style={{
                width: 36, height: 36, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                // The in-progress stage keeps its accent ring while an earlier
                // stage is on screen, so it never reads as "not reached".
                background: done ? C.accent : (active || i === current) ? "transparent" : C.panel,
                border: `2px solid ${done || active || i === current ? C.accent : C.border}`,
                color: done ? "#000" : (active || i === current) ? C.accent : C.textMuted,
                fontWeight: 700, fontSize: 14,
                boxShadow: active ? `0 0 16px ${C.accentGlow}` : "none",
                transition: "all 0.3s"
              }}>
                {done ? "✓" : i + 1}
              </div>
              <span style={{ fontSize: 11, color: active ? C.accent : done ? C.textSub : C.textMuted, fontWeight: active ? 700 : 400, whiteSpace: "nowrap", textDecoration: clickable ? "underline dotted" : "none", textUnderlineOffset: 3 }}>{s}</span>
            </Tag>
            {i < STEPS.length - 1 && (
              <div style={{ flex: 1, height: 2, background: done ? C.accent : C.border, margin: "0 4px", marginBottom: 20, transition: "background 0.3s" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}
