import { useState } from "react";
import { REFACTORED_CODE_SNIPPET, SCTVA_DATA } from "./data/diwoData";
import { Badge, C, Card, Pill } from "./diwoTheme.jsx";

export default function ResultsApprovalPage({ onRestart, onRollback }) {
  const [tab, setTab] = useState("summary");
  const confidence = SCTVA_DATA.confidence_score * 100;
  const comps = SCTVA_DATA.confidence_components;

  const valChecks = [
    { label: "Syntax Validation", status: "passed", detail: "Compilation successful. No errors.", score: comps.syntax_component },
    { label: "Structural Analysis", status: "passed", detail: "AST structural integrity maintained.", score: comps.structural_component },
    { label: "Behavioral Probes", status: "passed", detail: "10 Java runtime probes executed. All matched.", score: comps.behavioral_component },
    { label: "Invariant Mining", status: "passed", detail: "All program invariants preserved.", score: comps.invariant_component },
  ];

  const metrics = [
    { label: "Methods Extracted", before: 0, after: 8, unit: "methods", positive: true },
    { label: "Classes Extracted", before: 0, after: 3, unit: "classes", positive: true },
    { label: "Avg Cyclomatic Complexity", before: 35, after: 12, unit: "", positive: true },
    { label: "Longest Method (LOC)", before: 220, after: 45, unit: "lines", positive: true },
    { label: "Total Refactoring Actions", before: 0, after: 39, unit: "applied", positive: true },
    { label: "Confidence Score", before: "-", after: "100%", unit: "", positive: true },
  ];

  return (
    <div>
      <Card glow={C.accentGlow} style={{ marginBottom: 20, textAlign: "center", padding: "28px" }}>
        <div style={{ fontSize: 56, fontWeight: 900, fontFamily: "monospace", background: C.gradient, WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
          {confidence.toFixed(0)}%
        </div>
        <div style={{ fontSize: 14, color: C.textSub, marginTop: 4, marginBottom: 12 }}>Transformation Confidence Score</div>
        <div style={{ display: "flex", justifyContent: "center", gap: 12, flexWrap: "wrap" }}>
          <Pill label="✓ Transformation Successful" color={C.accent} />
          <Pill label="✓ No Rollback Required" color={C.accent} />
          <Pill label="✓ All Validations Passed" color={C.accent} />
        </div>
        <div style={{ marginTop: 12, fontSize: 12, color: C.textMuted }}>{SCTVA_DATA.safety_report.summary}</div>
      </Card>

      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${C.border}`, paddingBottom: 0 }}>
        {[["summary", "Summary"], ["validation", "Validation Report"], ["code", "Refactored Code"], ["invariants", "Invariants"]].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} style={{
            padding: "8px 16px", borderRadius: "8px 8px 0 0", fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
            background: tab === key ? C.accent : "transparent", color: tab === key ? "#000" : C.textMuted,
            borderBottom: tab === key ? "none" : "none"
          }}>{label}</button>
        ))}
      </div>

      {tab === "summary" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
          {metrics.map(({ label, before, after, unit, positive }) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>{label}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {before !== "-" && before !== 0 && <div style={{ fontSize: 18, color: C.textMuted, fontFamily: "monospace", textDecoration: "line-through" }}>{before}{unit ? ` ${unit}` : ""}</div>}
                {before !== "-" && before !== 0 && <span style={{ color: C.textMuted, fontSize: 12 }}>→</span>}
                <div style={{ fontSize: 22, fontWeight: 800, color: positive ? C.accent : C.danger, fontFamily: "monospace" }}>{after}{unit ? ` ${unit}` : ""}</div>
              </div>
            </div>
          ))}
          <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 10 }}>Confidence Formula</div>
            <div style={{ fontSize: 11, color: C.textSub, fontFamily: "monospace", marginBottom: 10 }}>{comps.formula}</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[["Syntax (35%)", comps.syntax_component], ["Structural (35%)", comps.structural_component], ["Behavioral (30%)", comps.behavioral_component], ["Invariant (15%)", comps.invariant_component]].map(([lbl, val]) => (
                <div key={lbl} style={{ background: C.bg, borderRadius: 6, padding: "8px 12px", textAlign: "center" }}>
                  <div style={{ fontSize: 16, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>{(val * 100).toFixed(2)}%</div>
                  <div style={{ fontSize: 10, color: C.textMuted }}>{lbl}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "validation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {valChecks.map(({ label, detail, score }) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.accent}30`, borderRadius: 8, padding: "14px 18px", display: "flex", alignItems: "center", gap: 16 }}>
              <div style={{ width: 36, height: 36, borderRadius: "50%", background: `${C.accent}20`, border: `2px solid ${C.accent}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <span style={{ color: C.accent, fontWeight: 900 }}>✓</span>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, color: C.text, fontSize: 13 }}>{label}</div>
                <div style={{ fontSize: 12, color: C.textSub, marginTop: 2 }}>{detail}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>{(score * 100).toFixed(2)}%</div>
                <Pill label="PASSED" color={C.accent} />
              </div>
            </div>
          ))}
          <Card style={{ marginTop: 4 }}>
            <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 8, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>Safety Messages</div>
            {SCTVA_DATA.safety_report.human_messages.map((msg, i) => (
              <div key={i} style={{ fontSize: 12, color: C.textSub, padding: "4px 0", borderBottom: i < SCTVA_DATA.safety_report.human_messages.length - 1 ? `1px solid ${C.border}` : "none" }}>
                <span style={{ color: C.accent, marginRight: 8 }}>→</span>{msg}
              </div>
            ))}
          </Card>
        </div>
      )}

      {tab === "code" && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ fontSize: 12, color: C.textMuted }}>ECommerceSystem.java · {SCTVA_DATA.language.toUpperCase()} · 36,957 chars</div>
            <Badge label="Refactored Output" color={C.accent} />
          </div>
          <pre style={{ background: "#0a0c10", border: `1px solid ${C.border}`, borderRadius: 10, padding: "20px", overflowX: "auto", overflowY: "auto", maxHeight: 360, fontSize: 11.5, color: "#a8d8b9", fontFamily: "'Fira Code', 'Courier New', monospace", lineHeight: 1.7, margin: 0 }}>
            {REFACTORED_CODE_SNIPPET}
          </pre>
        </div>
      )}

      {tab === "invariants" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: C.textSub, marginBottom: 4 }}>Preserved program invariants verified post-transformation:</div>
          {["execution_success_consistency", "return_type_consistency", "non_null_return_consistency", "exception_pattern_consistency"].map(inv => (
            <div key={inv} style={{ background: C.panel, border: `1px solid ${C.accent}20`, borderRadius: 8, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: C.accent }}>✓</span>
              <span style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub }}>{inv}</span>
              <Pill label="PRESERVED" color={C.accent} />
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 12, marginTop: 24, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button onClick={onRollback} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30` }}>
          ↩ Request Rollback
        </button>
        <button onClick={onRestart} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.info}15`, color: C.info, border: `1px solid ${C.info}30` }}>
          ↺ New Refactoring Session
        </button>
        <button style={{ padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: C.accent, color: "#000", border: "none", boxShadow: `0 0 20px ${C.accentGlow}` }}>
          ✓ Accept & Commit Changes
        </button>
      </div>
    </div>
  );
}
