import React, { useEffect, useState } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, Cell
} from "recharts";

function MetricBar({ label, before, after, unit = "", higherIsBetter = true, cardStyle }) {
  const improved = higherIsBetter ? after > before : after < before;
  const pct = before > 0 ? Math.abs(((after - before) / before) * 100).toFixed(1) : 0;
  
  return (
    <div style={cardStyle}>
      <div style={{ fontSize: 13, color: "#cbd5e1", fontWeight: 600, marginBottom: 12, letterSpacing: 0.3 }}>
        {label}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 2 }}>Before</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#f97316" }}>{before}{unit}</div>
        </div>
        <div style={{ fontSize: 18, color: "#64748b", fontWeight: 300 }}>→</div>
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 2 }}>After</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#22c55e" }}>{after}{unit}</div>
        </div>
      </div>
      <div style={{
        fontSize: 11,
        fontWeight: 600,
        color: improved ? "#86efac" : "#fca5a5",
        letterSpacing: 0.2,
      }}>
        {improved ? "✓" : "•"} {pct}% {improved ? "improvement" : "increase"}
      </div>
    </div>
  );
}

export function ComparisonView({ workflow, auditLogs = [], onComplete, onLoadLogs, loading }) {
  const [notes, setNotes] = useState("");
  const mb = workflow?.metrics_before || {};
  const ma = workflow?.metrics_after || {};

  useEffect(() => {
    if (onLoadLogs) onLoadLogs();
  }, []);

  const radarData = [
    { metric: "Complexity",     before: mb.cyclomatic_complexity || 0,     after: ma.cyclomatic_complexity || 0 },
    { metric: "Duplication",    before: mb.code_duplication_pct || 0,      after: ma.code_duplication_pct || 0 },
    { metric: "Maintainability",before: 100 - (mb.maintainability_index||0), after: 100 - (ma.maintainability_index||0) },
    { metric: "Smells",         before: (mb.total_smells||0)*5,            after: (ma.total_smells||0)*5 },
  ];

  const smellBreakdownData = Object.keys(mb.smell_breakdown || {}).map((sev) => ({
    severity: sev,
    before: mb.smell_breakdown[sev] || 0,
    after: (ma.smell_breakdown || {})[sev] || 0,
  }));

  const improvements = ma.improvements || {};

  const STAGE_LABELS = {
    smell_review: "Smell Review", smell_selection: "Smell Selection",
    plan_approval: "Plan Approval", transformation: "Transformation",
    comparison: "Comparison", completed: "Completed", rolled_back: "Rolled Back",
  };

  const KPI_CARD_BASE = {
    borderRadius: 12,
    border: "1px solid #1f2937",
    background: "#0f172a",
    padding: "14px 16px",
    minHeight: 86,
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    boxShadow: "0 6px 20px rgba(2,6,23,0.45)",
  };

  const kpiVariantStyle = (variant) => {
    if (variant === "green") return { borderColor: "rgba(34,197,94,0.35)", background: "linear-gradient(180deg, rgba(34,197,94,0.14), rgba(15,23,42,0.95))", numColor: "#86efac" };
    if (variant === "blue") return { borderColor: "rgba(59,130,246,0.35)", background: "linear-gradient(180deg, rgba(59,130,246,0.14), rgba(15,23,42,0.95))", numColor: "#93c5fd" };
    if (variant === "purple") return { borderColor: "rgba(168,85,247,0.35)", background: "linear-gradient(180deg, rgba(168,85,247,0.14), rgba(15,23,42,0.95))", numColor: "#d8b4fe" };
    return { borderColor: "rgba(249,115,22,0.35)", background: "linear-gradient(180deg, rgba(249,115,22,0.14), rgba(15,23,42,0.95))", numColor: "#fdba74" };
  };

  const diffRows = workflow?.diff_rows || [];
  const files = workflow?.files || [];
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const selectedFile = files[selectedFileIndex] || null;

  useEffect(() => {
    setSelectedFileIndex(0);
  }, [workflow?.files]);

  return (
    <div className="page-card">
      <div className="page-header">
        <div className="header-icon compare">📊</div>
        <div>
          <h1>Before / After Comparison</h1>
          <p>Review the code quality improvements achieved through refactoring.</p>
        </div>
      </div>

      <div className="kpi-row" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 18 }}>
        <div className="kpi-card green" style={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("green").borderColor, background: kpiVariantStyle("green").background }}>
          <div className="kpi-num" style={{ fontSize: 26, fontWeight: 800, color: kpiVariantStyle("green").numColor, lineHeight: 1.1 }}>↓ {improvements.complexity_reduced_by || 0}</div>
          <div className="kpi-label" style={{ fontSize: 12, color: "#cbd5e1", marginTop: 6, letterSpacing: 0.2 }}>Complexity Reduced</div>
        </div>
        <div className="kpi-card blue" style={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("blue").borderColor, background: kpiVariantStyle("blue").background }}>
          <div className="kpi-num" style={{ fontSize: 26, fontWeight: 800, color: kpiVariantStyle("blue").numColor, lineHeight: 1.1 }}>↓ {improvements.duplication_reduced_by || 0}%</div>
          <div className="kpi-label" style={{ fontSize: 12, color: "#cbd5e1", marginTop: 6, letterSpacing: 0.2 }}>Duplication Reduced</div>
        </div>
        <div className="kpi-card purple" style={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("purple").borderColor, background: kpiVariantStyle("purple").background }}>
          <div className="kpi-num" style={{ fontSize: 26, fontWeight: 800, color: kpiVariantStyle("purple").numColor, lineHeight: 1.1 }}>↑ {improvements.maintainability_gained || 0}</div>
          <div className="kpi-label" style={{ fontSize: 12, color: "#cbd5e1", marginTop: 6, letterSpacing: 0.2 }}>Maintainability Gained</div>
        </div>
        <div className="kpi-card orange" style={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("orange").borderColor, background: kpiVariantStyle("orange").background }}>
          <div className="kpi-num" style={{ fontSize: 26, fontWeight: 800, color: kpiVariantStyle("orange").numColor, lineHeight: 1.1 }}>{mb.total_smells || 0} → {ma.total_smells || 0}</div>
          <div className="kpi-label" style={{ fontSize: 12, color: "#cbd5e1", marginTop: 6, letterSpacing: 0.2 }}>Smells Remaining</div>
        </div>
      </div>

      <div className="metrics-section" style={{ marginBottom: 28 }}>
        <h2>Detailed Metrics</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginTop: 12 }}>
          <MetricBar 
            label="Cyclomatic Complexity" 
            before={mb.cyclomatic_complexity||0} 
            after={ma.cyclomatic_complexity||0} 
            higherIsBetter={false}
            cardStyle={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("orange").borderColor, background: kpiVariantStyle("orange").background }}
          />
          <MetricBar 
            label="Code Duplication" 
            before={mb.code_duplication_pct||0} 
            after={ma.code_duplication_pct||0} 
            unit="%" 
            higherIsBetter={false}
            cardStyle={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("blue").borderColor, background: kpiVariantStyle("blue").background }}
          />
          <MetricBar 
            label="Maintainability Index" 
            before={mb.maintainability_index||0} 
            after={ma.maintainability_index||0} 
            higherIsBetter={true}
            cardStyle={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("green").borderColor, background: kpiVariantStyle("green").background }}
          />
          <MetricBar 
            label="Total Smells" 
            before={mb.total_smells||0} 
            after={ma.total_smells||0} 
            higherIsBetter={false}
            cardStyle={{ ...KPI_CARD_BASE, borderColor: kpiVariantStyle("purple").borderColor, background: kpiVariantStyle("purple").background }}
          />
        </div>
      </div>

      <div className="charts-row">
        <div className="chart-box">
          <h3>Quality Radar</h3>
          <ResponsiveContainer width="100%" height={260}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="#334155" />
              <PolarAngleAxis dataKey="metric" tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <Radar name="Before" dataKey="before" stroke="#f97316" fill="#f97316" fillOpacity={0.25} />
              <Radar name="After"  dataKey="after"  stroke="#22c55e" fill="#22c55e" fillOpacity={0.25} />
              <Legend />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <h3>Smell Breakdown</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={smellBreakdownData}>
              <XAxis dataKey="severity" tick={{ fill: "#94a3b8", fontSize: 12 }} />
              <YAxis tick={{ fill: "#94a3b8" }} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }} />
              <Legend />
              <Bar dataKey="before" name="Before" fill="#f97316" radius={[4,4,0,0]} />
              <Bar dataKey="after"  name="After"  fill="#22c55e" radius={[4,4,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* <div className="code-comparison" style={{ marginBottom: 20 }}>
        <h2>Code Changes</h2>
          <div style={{ marginTop: 8 }}>
            {files.length > 0 && (
              <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
                <div style={{ fontSize: 12, color: "#94a3b8" }}>File:</div>
                <select value={selectedFileIndex} onChange={(e) => setSelectedFileIndex(Number(e.target.value))} style={{ padding: 6, borderRadius: 6, background: "#071025", color: "#cbd5e1", border: "1px solid #1f2937" }}>
                  {files.map((f, i) => (
                    <option key={f.path || i} value={i}>{f.path || `file-${i+1}`}</option>
                  ))}
                </select>
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, fontSize: 11 }}>
              <span style={{ color: "#ef4444", fontWeight: 700 }}>- Before line (removed/original)</span>
              <span style={{ color: "#3b82f6", fontWeight: 700 }}>+ After line (refactored)</span>
            </div>
            <div style={{
            background: "#0b1020",
            border: "1px solid #1f2937",
            borderRadius: 10,
            overflow: "auto",
            maxHeight: 360,
            fontFamily: "Fira Code, Courier New, monospace",
            fontSize: 11,
            lineHeight: 1.55,
          }}>
            {(selectedFile ? (selectedFile.diff_rows || []) : diffRows).map((row) => {
              const isBefore = row.kind === "before";
              const isAfter = row.kind === "after";
              return (
                <div
                  key={row.key}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "56px 24px 1fr",
                    gap: 0,
                    padding: "2px 0",
                    background: isBefore
                      ? "rgba(239,68,68,0.14)"
                      : isAfter
                        ? "rgba(59,130,246,0.16)"
                        : "transparent",
                    borderBottom: "1px solid rgba(148,163,184,0.08)",
                  }}
                >
                  <span style={{ color: "#64748b", textAlign: "right", paddingRight: 8, userSelect: "none" }}>{row.lineNo}</span>
                  <span style={{
                    color: isBefore ? "#ef4444" : isAfter ? "#3b82f6" : "#94a3b8",
                    textAlign: "center",
                    userSelect: "none",
                    fontWeight: 700,
                  }}>{row.marker}</span>
                  <span style={{
                    whiteSpace: "pre",
                    color: isBefore ? "#fecaca" : isAfter ? "#bfdbfe" : "#cbd5e1",
                    padding: "0 10px 0 6px",
                  }}>{row.text || " "}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div> */}

      <div className="audit-section">
        <h2>Audit Trail</h2>
        <div className="audit-log">
          {auditLogs.length === 0 && <p className="empty-state">No audit log entries yet.</p>}
          {auditLogs.map((log, idx) => (
            <div key={log.id ?? log.timestamp ?? `${log.time || 'log'}-${idx}`} className="audit-entry">
              <div className="audit-meta">
                <span className="audit-stage">{STAGE_LABELS[log.stage] || log.stage}</span>
                <span className="audit-action">{(log.event || log.action || '').replace(/_/g, " ")}</span>
                <span className={`audit-actor ${log.actor || ''}`}>{log.actor || ''}</span>
              </div>
              <span className="audit-time">{new Date(log.timestamp || log.time || Date.now()).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="feedback-section">
        <label>Final Notes (optional)</label>
        <textarea rows={2} placeholder="Overall notes for this refactoring session..."
                  value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      <button className="primary-btn complete-btn" onClick={() => onComplete && onComplete(notes)} disabled={loading}>
        {loading ? "Completing..." : "✓ Complete Workflow"}
      </button>
    </div>
  );
}

export default ComparisonView;
