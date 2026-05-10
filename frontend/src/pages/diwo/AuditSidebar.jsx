import { C } from "./diwoTheme.jsx";

export default function AuditSidebar({ phase, auditLog }) {
  return (
    <div style={{ width: 280, background: C.panel, borderLeft: `1px solid ${C.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
      <div style={{ padding: "16px 18px", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, color: C.textMuted }}>Audit Log</div>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {auditLog.slice().reverse().map((entry, i) => (
          <div key={i} style={{ fontSize: 11, borderLeft: `2px solid ${entry.type === "success" ? C.accent : entry.type === "danger" ? C.danger : entry.type === "warn" ? C.warn : C.info}`, paddingLeft: 8 }}>
            <div style={{ color: C.textMuted, marginBottom: 2 }}>{entry.time}</div>
            <div style={{ color: C.textSub, lineHeight: 1.4 }}>{entry.event}</div>
          </div>
        ))}
      </div>
      <div style={{ padding: "14px 16px", borderTop: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1, color: C.textMuted, marginBottom: 8 }}>Agent Pipeline</div>
        {[
          { name: "Code Understanding", active: phase >= 0, done: phase > 0 },
          { name: "Refactoring Planning", active: phase >= 1, done: phase > 1 },
          { name: "Workflow Orchestration", active: true, done: false },
          { name: "Transformation", active: phase >= 2, done: phase > 2 },
          { name: "Validation", active: phase >= 2, done: phase >= 3 },
        ].map(({ name, active, done }) => (
          <div key={name} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: done ? C.accent : active ? C.warn : C.border, flexShrink: 0 }} />
            <span style={{ fontSize: 11, color: done ? C.accent : active ? C.text : C.textMuted }}>{name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
