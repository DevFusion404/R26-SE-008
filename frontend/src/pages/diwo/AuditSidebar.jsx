/**
 * AuditSidebar.jsx
 * ================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The running audit trail and pipeline status, docked to the right of the
 * workflow. It can be minimised to a rail so a wide stage — the Results
 * project tree, the side-by-side diffs — gets the full window, and expanded
 * again from that rail. The sidebar owns its own width, so collapsing it needs
 * nothing from the page around it.
 */

import { useState } from "react";
import { C } from "./diwoTheme.jsx";

const entryColor = (type) =>
  type === "success" ? C.accent : type === "danger" ? C.danger : type === "warn" ? C.warn : C.info;

export default function AuditSidebar({ phase, auditLog = [] }) {
  const [collapsed, setCollapsed] = useState(false);

  // ── Minimised: a rail with the count and a way back ──────────────────────
  if (collapsed) {
    return (
      <div style={{
        width: 46, background: C.panel, borderLeft: `1px solid ${C.border}`,
        display: "flex", flexDirection: "column", alignItems: "center",
        padding: "12px 0", gap: 14, flexShrink: 0,
      }}>
        <button
          onClick={() => setCollapsed(false)}
          title="Expand the audit log"
          style={{
            width: 28, height: 28, borderRadius: 6, cursor: "pointer",
            background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
            fontSize: 12, fontWeight: 900, lineHeight: 1,
          }}
        >
          ‹
        </button>

        <div style={{
          writingMode: "vertical-rl", transform: "rotate(180deg)",
          fontSize: 11, fontWeight: 700, letterSpacing: 1,
          textTransform: "uppercase", color: C.textMuted,
        }}>
          Audit Log
        </div>

        {auditLog.length > 0 && (
          <div style={{
            fontSize: 10, fontWeight: 800, fontFamily: "monospace",
            color: C.accent, background: `${C.accent}15`,
            border: `1px solid ${C.accent}40`, borderRadius: 10, padding: "2px 6px",
          }}>
            {auditLog.length}
          </div>
        )}
      </div>
    );
  }

  // ── Expanded ─────────────────────────────────────────────────────────────
  return (
    <div style={{ width: 280, background: C.panel, borderLeft: `1px solid ${C.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
      <div style={{ padding: "16px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, color: C.textMuted }}>
          Audit Log
          {auditLog.length > 0 && (
            <span style={{ color: C.textSub, marginLeft: 6, fontFamily: "monospace" }}>({auditLog.length})</span>
          )}
        </div>
        <button
          onClick={() => setCollapsed(true)}
          title="Minimise the audit log"
          style={{
            width: 24, height: 24, borderRadius: 6, cursor: "pointer", flexShrink: 0,
            background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
            fontSize: 12, fontWeight: 900, lineHeight: 1,
          }}
        >
          ›
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
        {auditLog.slice().reverse().map((entry, i) => (
          <div key={i} style={{ fontSize: 11, borderLeft: `2px solid ${entryColor(entry.type)}`, paddingLeft: 8 }}>
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
