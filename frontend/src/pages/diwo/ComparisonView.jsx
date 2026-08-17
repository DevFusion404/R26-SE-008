import React, { useEffect, useState } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, Cell
} from "recharts";
import { createZip, downloadBlob, normalizeZipPath } from "./zipWriter";
import { buildProjectArchive, resolveWorkflowFiles } from "./projectArchive";

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

export function ComparisonView({ workflow, workflowId: propWorkflowId, language: propLanguage, auditLogs = [], onComplete, onLoadLogs, loading }) {
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

  const generateSummaryReport = () => {
    const workflowId = propWorkflowId || workflow?.id || workflow?.workflow_id || "N/A";
    const target = workflow?.target || "N/A";
    const language = propLanguage || workflow?.language || "N/A";

    const format = (log) => {
      const candidates = [log.timestamp, log.time, log.time_raw, log.date, log.created_at, log.at, log.ts];
      let val = candidates.find((x) => x !== undefined && x !== null && x !== "");
      if (!val) return "";
      if (typeof val === "number") {
        const d = new Date(val);
        if (!isNaN(d)) return d.toLocaleString();
        return String(val);
      }
      if (typeof val === "string") {
        const s = val.trim();
        if (/invalid date/i.test(s)) return "(unknown time)";
        const dIso = new Date(s);
        if (!isNaN(dIso)) return dIso.toLocaleString();
        const digits = s.match(/\d{10,}/);
        if (digits) {
          const maybe = parseInt(digits[0], 10);
          const d2 = digits[0].length === 10 ? new Date(maybe * 1000) : new Date(maybe);
          if (!isNaN(d2)) return d2.toLocaleString();
        }
        return s;
      }
      return String(val);
    };

    const smellRows = (smellBreakdownData || []).map(item => `
      <tr>
        <td>${item.severity}</td>
        <td>${item.before}</td>
        <td>${item.after}</td>
      </tr>
    `).join("");

    const auditRows = (auditLogs || []).map(log => `
      <tr>
        <td>${STAGE_LABELS[log.stage] || log.stage || ""}</td>
        <td>${(log.event || log.action || "").replace(/_/g, " ")}</td>
        <td>${log.actor || ""}</td>
        <td>${format(log) || ""}</td>
      </tr>
    `).join("");

    const smellSummary = workflow?.smell_selection_summary || (workflow?.updated_report?.summary && {
      total_smells: workflow.updated_report.summary.total_code_smells || 0,
      selected_count: workflow.updated_report.summary.selected_count || 0,
      excluded_count: 0,
    }) || { total_smells: 0, selected_count: 0, excluded_count: 0 };

    const planSummary = workflow?.plan_approval_summary || { total_steps: 0, approved_count: 0, rejected_count: 0 };

    const acceptedFilesList = (workflow?.accepted_files || []).map(p => `<li>${p}</li>`).join("") || "<li>None</li>";
    const rejectedFilesList = (workflow?.rejected_files || []).map(p => `<li>${p}</li>`).join("") || "<li>None</li>";

    const reportHtml = `
      <html>
        <head>
          <title>DIWO Refactoring Summary Report</title>
          <style>
            body {
              font-family: Arial, sans-serif;
              padding: 30px;
              color: #111827;
              line-height: 1.6;
            }
            h1 {
              color: #0f172a;
              border-bottom: 3px solid #2563eb;
              padding-bottom: 10px;
            }
            h2 {
              color: #1e293b;
              margin-top: 28px;
            }
            table {
              width: 100%;
              border-collapse: collapse;
              margin-top: 12px;
            }
            th, td {
              border: 1px solid #cbd5e1;
              padding: 10px;
              text-align: left;
            }
            th {
              background: #f1f5f9;
            }
            .summary-box {
              background: #f8fafc;
              border: 1px solid #cbd5e1;
              border-radius: 8px;
              padding: 16px;
              margin-top: 16px;
            }
            .success {
              color: #15803d;
              font-weight: bold;
            }
            .warning {
              color: #b45309;
              font-weight: bold;
            }
            .footer {
              margin-top: 40px;
              font-size: 12px;
              color: #64748b;
            }
          </style>
        </head>
        <body>
          <h1>DIWO Refactoring Summary Report</h1>

          <div class="summary-box">
            <p><strong>Workflow ID:</strong> ${workflowId}</p>
            <p><strong>Target:</strong> ${target}</p>
            <p><strong>Language:</strong> ${language}</p>
            <p><strong>Generated At:</strong> ${new Date().toLocaleString()}</p>
          </div>

          <h2>Before vs After Quality Metrics</h2>
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Before</th>
                <th>After</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Cyclomatic Complexity</td>
                <td>${mb.cyclomatic_complexity || 0}</td>
                <td>${ma.cyclomatic_complexity || 0}</td>
                <td>${improvements.complexity_reduced_by || 0} reduced</td>
              </tr>
              <tr>
                <td>Code Duplication</td>
                <td>${mb.code_duplication_pct || 0}%</td>
                <td>${ma.code_duplication_pct || 0}%</td>
                <td>${improvements.duplication_reduced_by || 0}% reduced</td>
              </tr>
              <tr>
                <td>Maintainability Index</td>
                <td>${mb.maintainability_index || 0}</td>
                <td>${ma.maintainability_index || 0}</td>
                <td>${improvements.maintainability_gained || 0} gained</td>
              </tr>
              <tr>
                <td>Total Code Smells</td>
                <td>${mb.total_smells || 0}</td>
                <td>${ma.total_smells || 0}</td>
                <td>${(mb.total_smells || 0) - (ma.total_smells || 0)} resolved</td>
              </tr>
            </tbody>
          </table>

          <h2>Smell Severity Breakdown</h2>
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              ${smellRows}
            </tbody>
          </table>

          <h2>Selection & Approval Summary</h2>
          <div class="summary-box">
            <p><strong>Smells forwarded:</strong> ${smellSummary.selected_count} of ${smellSummary.total_smells} (excluded: ${smellSummary.excluded_count})</p>
            <p><strong>Plan steps approved:</strong> ${planSummary.approved_count || 0} of ${planSummary.total_steps || 0} (rejected: ${planSummary.rejected_count || 0})</p>
            <p><strong>Files accepted for commit:</strong> ${(workflow?.accepted_files || []).length || 0}</p>
          </div>

          <h2>Accepted / Rejected Files</h2>
          <div class="summary-box">
            <div style="display:flex;gap:24px">
              <div style="flex:1"><h3>Accepted</h3><ul>${acceptedFilesList}</ul></div>
              <div style="flex:1"><h3>Rejected</h3><ul>${rejectedFilesList}</ul></div>
            </div>
          </div>

          <h2>Audit Trail Summary</h2>
          <table>
            <thead>
              <tr>
                <th>Stage</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              ${auditRows}
            </tbody>
          </table>

          <h2>Developer Final Notes</h2>
          <div class="summary-box">
            ${notes ? notes : "No final notes provided."}
          </div>

          <h2>Final Summary</h2>
          <div class="summary-box">
            <p class="success">The DIWO Agent completed the comparison stage successfully.</p>
            <p>The report shows the before and after quality metrics, smell reduction, maintainability improvement, and full audit trail of developer decisions.</p>
          </div>

          <div class="footer">
            Generated by Developer Interaction & Workflow Orchestration Agent.
          </div>

          <script>
            window.onload = function() {
              window.print();
            }
          </script>
        </body>
      </html>
    `;

    const reportWindow = window.open("", "_blank");
    reportWindow.document.write(reportHtml);
    reportWindow.document.close();
  };

  const formatAuditDate = (log) => {
    const candidates = [log.timestamp, log.time, log.time_raw, log.date, log.created_at, log.at, log.ts];
    let val = candidates.find((x) => x !== undefined && x !== null && x !== "");
    if (!val) return "";

    // If it's already a number (ms since epoch)
    if (typeof val === "number") {
      const d = new Date(val);
      if (!isNaN(d)) return d.toLocaleString();
      return String(val);
    }

    // If it's a string, try parsing common formats and fallbacks
    if (typeof val === "string") {
      // trim
      const s = val.trim();
      // quick guard against literally 'Invalid Date'
      if (/invalid date/i.test(s)) return "(unknown time)";

      // ISO / RFC parse
      const dIso = new Date(s);
      if (!isNaN(dIso)) return dIso.toLocaleString();

      // try extracting a numeric timestamp from the string
      const digits = s.match(/\d{10,}/);
      if (digits) {
        const maybe = parseInt(digits[0], 10);
        const d2 = digits[0].length === 10 ? new Date(maybe * 1000) : new Date(maybe);
        if (!isNaN(d2)) return d2.toLocaleString();
      }

      // fallback to returning the original text (but not the literal 'Invalid Date')
      return s;
    }

    return String(val);
  };

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

      <div className="audit-section" style={{ marginBottom: 28 }}>
        <h2 style={{ marginBottom: 20, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 24 }}>📋</span> Audit Trail
        </h2>
        <div className="audit-log" style={{
          background: "#0f172a",
          border: "1px solid #1f2937",
          borderRadius: 12,
          overflow: "hidden",
          boxShadow: "0 6px 20px rgba(2,6,23,0.45)"
        }}>
          {auditLogs.length === 0 && (
            <p className="empty-state" style={{ 
              padding: 32, 
              textAlign: "center", 
              color: "#64748b",
              margin: 0
            }}>No audit log entries yet.</p>
          )}
          {auditLogs.map((log, idx) => {
            const stageColors = {
              "Smell Review": "#8b5cf6",
              "Smell Selection": "#3b82f6",
              "Plan Approval": "#06b6d4",
              "Transformation": "#f59e0b",
              "Comparison": "#10b981",
              "Completed": "#22c55e",
              "Rolled Back": "#ef4444"
            };
            const stageBg = stageColors[STAGE_LABELS[log.stage] || log.stage] || "#6366f1";
            
            return (
              <div 
                key={log.id ?? log.timestamp ?? `${log.time || 'log'}-${idx}`} 
                style={{
                  display: "grid",
                  gridTemplateColumns: "140px 1fr 160px 180px",
                  gap: 16,
                  padding: "16px 20px",
                  borderBottom: idx !== auditLogs.length - 1 ? "1px solid #1f2937" : "none",
                  alignItems: "center",
                  transition: "background 0.2s ease",
                  "&:hover": { background: "rgba(15, 23, 42, 0.8)" }
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "rgba(30, 41, 59, 0.5)"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
              >
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8
                }}>
                  <div style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: stageBg,
                    boxShadow: `0 0 12px ${stageBg}40`
                  }} />
                  <span style={{
                    fontSize: 12,
                    fontWeight: 700,
                    color: stageBg,
                    letterSpacing: 0.3,
                    textTransform: "uppercase"
                  }}>
                    {STAGE_LABELS[log.stage] || log.stage || "Unknown"}
                  </span>
                </div>
                
                <div style={{
                  fontSize: 13,
                  color: "#e2e8f0",
                  fontWeight: 500,
                  lineHeight: 1.4
                }}>
                  {(log.event || log.action || '').replace(/_/g, " ")}
                </div>
                
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 12,
                  color: "#94a3b8",
                  backgroundColor: "#1e293b",
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid #334155"
                }}>
                  <span style={{ fontSize: 14 }}>
                    {log.actor === "system" ? "⚙️" : log.actor === "developer" ? "👤" : "📌"}
                  </span>
                  <span>{log.actor ? log.actor.charAt(0).toUpperCase() + log.actor.slice(1) : "—"}</span>
                </div>
                
                <div style={{
                  fontSize: 12,
                  color: "#64748b",
                  textAlign: "right",
                  fontFamily: "Fira Code, monospace"
                }}>
                  {formatAuditDate(log) || "—"}
                </div>
              </div>
            );
          })}
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
  >
    {loading ? "Completing..." : "✓ Complete Workflow"}
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
