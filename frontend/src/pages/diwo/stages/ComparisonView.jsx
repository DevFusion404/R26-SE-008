import React, { useEffect, useState } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, Cell
} from "recharts";
import { createZip, downloadBlob, normalizeZipPath } from "../utils/zipWriter";
import { buildProjectArchive, resolveWorkflowFiles } from "../utils/projectArchive";
// The trail on this page and the printable report are two renderings of the
// same persisted rows, so both read them through the same narrator rather than
// each inventing its own wording for "what happened".
import { groupByStage, narrateAll } from "../utils/auditNarrative";
import { buildSummaryReportHtml } from "../utils/summaryReport";

/** One colour per workflow stage, so the trail reads as chapters. */
const STAGE_COLORS = {
  smell_review: "#8b5cf6",
  smell_selection: "#3b82f6",
  plan_approval: "#06b6d4",
  transformation: "#f59e0b",
  comparison: "#10b981",
  completed: "#22c55e",
  rolled_back: "#ef4444",
};

const TONE_COLORS = {
  success: "#22c55e",
  warn: "#f59e0b",
  danger: "#ef4444",
  info: "#6366f1",
};

const fmtStamp = (value) => {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

/**
 * One narrated audit event, with its evidence one click away.
 *
 * The headline already carries the substance ("Developer approved 6 steps for
 * transformation"); expanding shows the itemised proof — which smell, which
 * refactoring, which file. Collapsed by default because a repository scan can
 * produce hundreds of rows and this page is also the print source.
 */
function TrailEntry({ entry, open, onToggle }) {
  const tone = TONE_COLORS[entry.tone] || TONE_COLORS.info;
  const clickable = entry.expandable;

  return (
    <div style={{ borderBottom: "1px solid #1f2937" }}>
      <button
        type="button"
        onClick={clickable ? () => onToggle(entry.id) : undefined}
        disabled={!clickable}
        aria-expanded={clickable ? open : undefined}
        style={{
          width: "100%", textAlign: "left", background: "transparent",
          border: "none", padding: "13px 20px",
          cursor: clickable ? "pointer" : "default",
          display: "flex", alignItems: "center", gap: 12,
        }}
      >
        <span style={{
          width: 6, height: 6, borderRadius: "50%", background: tone, flexShrink: 0,
        }} />
        <span style={{
          flex: 1, minWidth: 0, fontSize: 13, color: "#e2e8f0",
          fontWeight: 600, lineHeight: 1.4,
        }}>
          {entry.title}
        </span>
        <span style={{
          display: "flex", alignItems: "center", gap: 6, fontSize: 11.5,
          color: "#94a3b8", background: "#1e293b", padding: "4px 9px",
          borderRadius: 6, border: "1px solid #334155", flexShrink: 0,
        }}>
          <span>{entry.actor === "system" ? "⚙️" : entry.actor === "developer" ? "👤" : "📌"}</span>
          <span>{entry.actor}</span>
        </span>
        <span style={{
          fontSize: 11, color: "#64748b", fontFamily: "monospace",
          flexShrink: 0, minWidth: 140, textAlign: "right",
        }}>
          {fmtStamp(entry.timestamp) || "—"}
        </span>
        <span style={{ color: clickable ? "#64748b" : "transparent", fontWeight: 800, flexShrink: 0 }}>
          {open ? "▾" : "▸"}
        </span>
      </button>

      {open && (
        <div style={{ padding: "0 20px 14px 38px", display: "flex", flexDirection: "column", gap: 10 }}>
          {entry.facts.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
              {entry.facts.map((fact) => (
                <span key={fact.label} style={{ fontSize: 11.5, color: "#94a3b8" }}>
                  {fact.label}{" "}
                  <b style={{ color: "#cbd5e1", fontFamily: "monospace" }}>{fact.value}</b>
                </span>
              ))}
            </div>
          )}

          {entry.groups.map((group) => (
            <div key={group.label}>
              <div style={{
                fontSize: 10.5, textTransform: "uppercase", letterSpacing: 0.5,
                color: "#64748b", fontWeight: 700, marginBottom: 5,
              }}>
                {group.label}
                <span style={{ fontFamily: "monospace", marginLeft: 6 }}>{group.items.length}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {group.items.map((item) => (
                  <div key={item.key} style={{
                    display: "flex", alignItems: "baseline", gap: 8, fontSize: 12,
                    background: "#0b1020", border: "1px solid #1f2937",
                    borderRadius: 6, padding: "5px 9px",
                  }}>
                    <span style={{ color: "#cbd5e1", fontWeight: 600, flex: 1, minWidth: 0 }}>
                      {item.primary}
                      {item.secondary && (
                        <span style={{ color: "#64748b", fontWeight: 400 }}> · {item.secondary}</span>
                      )}
                    </span>
                    {item.file && (
                      <code
                        title={item.file}
                        style={{
                          fontSize: 10.5, color: "#94a3b8", flexShrink: 0,
                          maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {item.file}
                      </code>
                    )}
                    {item.tag && (
                      <span style={{
                        fontSize: 9.5, fontWeight: 700, textTransform: "uppercase",
                        padding: "1px 6px", borderRadius: 8, flexShrink: 0,
                        background: "#1e293b", color: "#94a3b8",
                      }}>
                        {item.tag}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}

          {entry.note && (
            <div style={{ fontSize: 11, color: "#64748b", fontStyle: "italic" }}>{entry.note}</div>
          )}
        </div>
      )}
    </div>
  );
}

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

export function ComparisonView({ workflow, workflowId: propWorkflowId, language: propLanguage, auditLogs = [], onComplete, onLoadLogs, onBack, loading }) {
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

  const smellBreakdownKeys = Array.from(new Set([
    ...(mb.smell_breakdown ? Object.keys(mb.smell_breakdown) : []),
    ...(ma.smell_breakdown ? Object.keys(ma.smell_breakdown) : []),
  ]));

  const smellBreakdownData = smellBreakdownKeys.map((sev) => ({
    severity: sev,
    before: Number((mb.smell_breakdown || {})[sev] || 0),
    after: Number((ma.smell_breakdown || {})[sev] || 0),
  }));

  const improvements = ma.improvements || {};

  // The persisted rows, narrated once per render and shared by the on-page
  // trail and the printable report.
  const narratedTrail = narrateAll(auditLogs);
  const [openTrailIds, setOpenTrailIds] = useState(() => new Set());
  const toggleTrailEntry = (id) =>
    setOpenTrailIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

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

  // ── Download the final sources as one archive ────────────────────────────
  const [zipping, setZipping] = useState(false);

  /**
   * The code that leaves this page per file, honouring the Stage 3 verdict:
   * an accepted file ships its refactored source, a rejected one ships the
   * original it was reverted to. Same rule the Results stage applies before
   * writing to disk, so the archive matches what a commit would contain.
   */
  const finalCodeFor = (f) => {
    const before = f.before ?? "";
    const after = f.after ?? f.refactored_code ?? "";
    return f.decision === "reject" && before ? before : after;
  };

  // A rejected file whose original never reached the frontend cannot be
  // reverted, and shipping its refactored source would hand the developer the
  // change they just turned down. Leave it out instead: the repository on disk
  // still holds the untouched original.
  const isUnrevertable = (f) => f.decision === "reject" && !(f.before ?? "");

  const omittedFiles = files.filter(isUnrevertable);

  const downloadableFiles = files.filter((f) => {
    if (isUnrevertable(f)) return false;
    const code = finalCodeFor(f);
    return typeof code === "string" && code.length > 0;
  });

  const [zipStatus, setZipStatus] = useState(null);
  const [archiveSummary, setArchiveSummary] = useState(null);

  /**
   * Download the whole project as one ZIP.
   *
   * The refactored files come from the workflow; every other file in the
   * repository is read back from the CUQA workspace so the archive is a
   * complete, runnable project rather than a handful of loose sources. If the
   * project structure cannot be reached, it falls back to the changed files
   * alone and says so.
   */
  const handleDownloadZip = async () => {
    if (downloadableFiles.length === 0 || zipping) return;
    setZipping(true);
    setArchiveSummary(null);

    const workflowId = propWorkflowId || workflow?.id || workflow?.workflow_id || "session";
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const finalFiles = resolveWorkflowFiles(files);

    const baseManifest = {
      workflow_id: workflowId,
      target: workflow?.target || null,
      language: propLanguage || workflow?.language || null,
      generated_at: new Date().toISOString(),
      sctva_request_id: workflow?.sctva?.request_id || null,
      confidence_score: workflow?.sctva?.confidence_score ?? null,
      omitted: omittedFiles.map((f) => ({
        path: normalizeZipPath(f.path || f.file || "file"),
        reason: "rejected, and the original source was not available to revert to",
      })),
    };

    let entries;
    let manifest;
    let summary;

    try {
      setZipStatus("Reading project structure…");
      const project = await buildProjectArchive({
        finalFiles,
        onProgress: ({ phase, done, total }) => {
          if (phase === "sources") setZipStatus(`Reading project files… ${done}/${total}`);
          else if (phase === "zipping") setZipStatus("Compressing archive…");
        },
      });

      entries = project.entries;
      manifest = { ...baseManifest, scope: "full_project", ...project.manifest };
      summary = { scope: "full_project", ...project.stats };
    } catch (e) {
      // CUQA down, no repository loaded, project too large — still give the
      // developer the changed files rather than nothing.
      console.warn("Full-project archive unavailable, falling back to changed files", e);
      setZipStatus("Project structure unavailable — packing changed files…");

      entries = finalFiles.map((f) => ({ path: f.path, content: f.content }));
      manifest = {
        ...baseManifest,
        scope: "changed_files_only",
        scope_reason: e.message,
        files: finalFiles.map((f) => ({ path: f.path, state: f.state })),
      };
      summary = {
        scope: "changed_files_only",
        reason: e.message,
        included: entries.length,
        refactored: finalFiles.filter((f) => f.state === "refactored").length,
        reverted: finalFiles.filter((f) => f.state === "reverted_to_original").length,
      };
    }

    try {
      entries = [
        ...entries,
        { path: "REFACTORING_MANIFEST.json", content: JSON.stringify(manifest, null, 2) },
      ];
      const blob = await createZip(entries);
      downloadBlob(blob, `diwo_project_${workflowId}_${stamp}.zip`);
      setArchiveSummary({ ...summary, bytes: blob.size });
    } catch (e) {
      console.error("Failed to build the archive", e);
      alert(`Could not build the ZIP archive: ${e.message}`);
    } finally {
      setZipping(false);
      setZipStatus(null);
    }
  };

  const revertedCount = downloadableFiles.filter((f) => f.decision === "reject").length;

  /**
   * Open the printable session report.
   *
   * The document is built from the PERSISTED audit rows, not from the session
   * log: the previous version tabulated stage names and actor names, so the
   * artefact a reviewer kept recorded that a plan was approved without saying
   * which refactorings it contained or which file each touched. Everything it
   * needed was already in each row's `details`.
   */
  const generateSummaryReport = () => {
    const html = buildSummaryReportHtml({
      workflowId: propWorkflowId || workflow?.id || workflow?.workflow_id || "N/A",
      target: workflow?.target,
      language: propLanguage || workflow?.language,
      rows: auditLogs,
      metricsBefore: mb,
      metricsAfter: ma,
      severityBreakdown: smellBreakdownData,
      acceptedFiles: workflow?.accepted_files || [],
      rejectedFiles: workflow?.rejected_files || [],
      notes,
      sctva: workflow?.sctva || null,
      archive: archiveSummary,
    });

    const reportWindow = window.open("", "_blank");
    if (!reportWindow) {
      alert("The report window was blocked. Allow pop-ups for this site and try again.");
      return;
    }
    reportWindow.document.write(html);
    reportWindow.document.close();
  };

  return (
    <div className="page-card">
      {onBack && (
        <button
          type="button"
          onClick={onBack}
          title="Go back to the Results Review stage"
          style={{
            marginBottom: 14,
            padding: "8px 16px",
            borderRadius: 8,
            border: "1px solid #334155",
            background: "#1e293b",
            color: "#cbd5e1",
            fontSize: 12.5,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          ← Back to Results Review
        </button>
      )}

      <div className="page-header">
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

      <div className="audit-section" style={{ marginBottom: 28 }}>
        <h2 style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 24 }}>📋</span> Audit Trail
        </h2>
        <p style={{ margin: "0 0 16px", fontSize: 12.5, color: "#94a3b8" }}>
          Every recorded step of this session — which files had smells, which were
          selected, what each was refactored into, and what the developer decided.
          {narratedTrail.length > 0 && (
            <> {narratedTrail.length} recorded event(s).</>
          )}
        </p>

        <div className="audit-log" style={{
          background: "#0f172a",
          border: "1px solid #1f2937",
          borderRadius: 12,
          overflow: "hidden",
          boxShadow: "0 6px 20px rgba(2,6,23,0.45)",
        }}>
          {narratedTrail.length === 0 && (
            <p className="empty-state" style={{
              padding: 32, textAlign: "center", color: "#64748b", margin: 0,
            }}>
              No audit log entries yet.
            </p>
          )}

          {groupByStage(narratedTrail).map((stage) => (
            <div key={stage.stage}>
              <div style={{
                padding: "10px 20px", background: "#111c31",
                borderBottom: "1px solid #1f2937",
                display: "flex", alignItems: "center", gap: 10,
              }}>
                <span style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: STAGE_COLORS[stage.stage] || "#6366f1",
                  boxShadow: `0 0 12px ${STAGE_COLORS[stage.stage] || "#6366f1"}40`,
                }} />
                <span style={{
                  fontSize: 12, fontWeight: 800, letterSpacing: 0.4,
                  textTransform: "uppercase",
                  color: STAGE_COLORS[stage.stage] || "#6366f1",
                }}>
                  {stage.label}
                </span>
                <span style={{ fontSize: 11, color: "#64748b", fontFamily: "monospace" }}>
                  {stage.entries.length}
                </span>
              </div>

              {stage.entries.map((entry) => (
                <TrailEntry
                  key={entry.id}
                  entry={entry}
                  open={openTrailIds.has(entry.id)}
                  onToggle={toggleTrailEntry}
                />
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="feedback-section" style={{ marginTop: 12 }}>
        <label style={{ display: "block", marginBottom: 8, fontWeight: 700 }}>Final Notes (optional)</label>
        <textarea rows={6} placeholder="Overall notes for this refactoring session..."
                  value={notes} onChange={(e) => setNotes(e.target.value)}
                  style={{ width: "100%", minHeight: 140, padding: 12, borderRadius: 10, background: "#071025", color: "#e2e8f0", border: "1px solid #1f2937", resize: "vertical", fontFamily: "inherit" }} />
      </div>
      {/* <button className="primary-btn complete-btn" onClick={() => onComplete && onComplete(notes)} disabled={loading}>
        {loading ? "Completing..." : "✓ Complete Workflow"}
      </button> */}
      <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap", alignItems: "center" }}>
  <button
    className="secondary-btn"
    onClick={generateSummaryReport}
    disabled={loading}
    style={{
      padding: "10px 18px",
      borderRadius: 10,
      border: "1px solid #334155",
      background: "#1e293b",
      color: "#e2e8f0",
      fontWeight: 700,
      cursor: "pointer"
    }}
  >
    📄 Generate Summary Report
  </button>

  <button
    className="secondary-btn"
    onClick={handleDownloadZip}
    disabled={loading || zipping || downloadableFiles.length === 0}
    title={
      downloadableFiles.length === 0
        ? "No refactored files are attached to this session."
        : "Download the whole project as one ZIP — refactored files applied, everything else as-is."
    }
    style={{
      padding: "10px 18px",
      borderRadius: 10,
      border: `1px solid ${downloadableFiles.length === 0 ? "#334155" : "rgba(34,197,94,0.45)"}`,
      background: downloadableFiles.length === 0 ? "#1e293b" : "linear-gradient(180deg, rgba(34,197,94,0.16), rgba(15,23,42,0.95))",
      color: downloadableFiles.length === 0 ? "#64748b" : "#86efac",
      fontWeight: 700,
      cursor: loading || zipping || downloadableFiles.length === 0 ? "not-allowed" : "pointer",
    }}
  >
    {zipping ? (zipStatus || "Building archive…") : `⬇ Download Project (.zip)`}
  </button>

  <button
    className="primary-btn complete-btn"
    onClick={() => onComplete && onComplete(notes)}
    disabled={loading}
    title="Close this workflow and write the final record to the audit trail"
    style={{
      marginLeft: "auto",
      padding: "11px 26px",
      borderRadius: 10,
      border: "none",
      background: loading ? "#334155" : "#22c55e",
      color: loading ? "#94a3b8" : "#04210f",
      fontSize: 13.5,
      fontWeight: 800,
      letterSpacing: 0.2,
      cursor: loading ? "wait" : "pointer",
      boxShadow: loading ? "none" : "0 0 22px rgba(34,197,94,0.28)",
    }}
  >
    {loading ? "Completing…" : "✓ Complete Workflow"}
  </button>
</div>

      {downloadableFiles.length > 0 && !archiveSummary && (
        <div style={{ marginTop: 10, fontSize: 12, color: "#94a3b8" }}>
          The archive packs the whole project in its original folder structure: {downloadableFiles.length}{" "}
          changed file(s) applied
          {revertedCount > 0 && (
            <span style={{ color: "#fca5a5" }}>
              {" "}({revertedCount} rejected, included as their original source)
            </span>
          )}
          , every other file copied from the workspace unchanged.
          {omittedFiles.length > 0 && (
            <span style={{ color: "#fdba74" }}>
              {" "}{omittedFiles.length} rejected file(s) had no original to revert to, so the
              workspace copy is used instead.
            </span>
          )}
        </div>
      )}

      {archiveSummary && (
        <div
          style={{
            marginTop: 10,
            padding: "10px 14px",
            borderRadius: 10,
            border: `1px solid ${archiveSummary.scope === "full_project" ? "rgba(34,197,94,0.35)" : "rgba(249,115,22,0.35)"}`,
            background: "#0f172a",
            fontSize: 12,
            color: "#cbd5e1",
          }}
        >
          {archiveSummary.scope === "full_project" ? (
            <>
              <strong style={{ color: "#86efac" }}>Project archive downloaded</strong>
              {archiveSummary.repoName ? ` — ${archiveSummary.repoName}` : ""} ·{" "}
              {archiveSummary.included} file(s), {Math.round((archiveSummary.bytes || 0) / 1024)} KB
              <div style={{ marginTop: 4, color: "#94a3b8" }}>
                {archiveSummary.replacedInPlace} file(s) replaced in place ·{" "}
                {archiveSummary.refactored} refactored · {archiveSummary.reverted} reverted to original ·{" "}
                {archiveSummary.unchanged} unchanged
                {archiveSummary.binarySkipped > 0 && (
                  <span style={{ color: "#fdba74" }}>
                    {" "}· {archiveSummary.binarySkipped} binary file(s) skipped
                  </span>
                )}
                {archiveSummary.unreadable > 0 && (
                  <span style={{ color: "#fdba74" }}>
                    {" "}· {archiveSummary.unreadable} unreadable
                  </span>
                )}
              </div>
              {archiveSummary.addedOutsideTree > 0 && (
                <div style={{ marginTop: 4, color: "#fdba74" }}>
                  {archiveSummary.addedOutsideTree} refactored file(s) had no counterpart in the
                  project tree and were added separately — check their paths in the manifest.
                </div>
              )}
            </>
          ) : (
            <>
              <strong style={{ color: "#fdba74" }}>Changed files only</strong> —{" "}
              {archiveSummary.included} file(s) downloaded. The full project could not be assembled:{" "}
              {archiveSummary.reason}
            </>
          )}
        </div>
      )}

    </div>
  );
}

export default ComparisonView;
