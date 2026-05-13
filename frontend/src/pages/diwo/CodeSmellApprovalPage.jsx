import { useState } from "react";
import { CUQA_DATA } from "./data/diwoData";
import { C, Card, Badge, severityColor } from "./diwoTheme.jsx";

const BASE = import.meta?.env?.VITE_API_URL || "http://localhost:5001/api";

export default function CodeSmellApprovalPage({ onProceed, workflowId, reportData }) {
  const [selected, setSelected] = useState(new Set());
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const sourceReport = reportData || CUQA_DATA;
  const filesWithSmells = (sourceReport.files || []).filter(f => (f.code_smells || []).length > 0);

  const filtered = filesWithSmells.filter(f => {
    const matchSev = filter === "all" || f.code_smells.some(s => s.severity === filter);
    const matchSearch = search === "" || f.relative_path.toLowerCase().includes(search.toLowerCase()) || f.code_smells.some(s => s.type.toLowerCase().includes(search.toLowerCase()));
    return matchSev && matchSearch;
  });

  const toggleFile = (path) => {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(path)) n.delete(path); else n.add(path);
      return n;
    });
  };

  const selectAll = () => setSelected(new Set(filtered.map(f => f.relative_path)));
  const clearAll = () => setSelected(new Set());

  const selectedFiles = Array.from(selected);
  const selectedSmells = filesWithSmells
    .filter(f => selected.has(f.relative_path))
    .flatMap(f => (f.code_smells || []).map(smell => ({
      file: f.relative_path,
      type: smell.type,
      line: smell.line,
      severity: smell.severity,
      message: smell.message,
    })));
  const highCount = selectedSmells.filter(s => s.severity === "high").length;

  const handleApproveSelection = async () => {
    if (selected.size === 0 || isSubmitting) return;

    if (!workflowId) {
      onProceed?.({ selected_files: selectedFiles, selected_smells: selectedSmells });
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`${BASE}/workflows/${workflowId}/smell-selection-pass`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_files: selectedFiles,
          selected_smells: selectedSmells,
          feedback: { reason: "Selected in CodeSmellApprovalPage" },
        }),
      });

      if (!response.ok) {
        let message = `HTTP ${response.status}: ${response.statusText}`;
        try {
          const errorData = await response.json();
          message = errorData.error || message;
        } catch {
          /* keep fallback message */
        }
        throw new Error(message);
      }

      const report = await response.json();
      onProceed?.(report);
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error("Smell approval failed:", error);
      alert(error.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 24 }}>
        {[
          { label: "Files Analyzed", val: sourceReport.summary?.files_analyzed || 0, color: C.accent },
          { label: "Total Smells", val: sourceReport.summary?.total_code_smells || 0, color: C.warn },
          { label: "High Severity", val: sourceReport.summary?.smell_severity?.high || 0, color: C.danger },
          { label: "Avg Quality", val: `${sourceReport.summary?.average_quality_score || 0}%`, color: C.accent },
        ].map(({ label, val, color }) => (
          <Card key={label} style={{ textAlign: "center", padding: "16px" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color, fontFamily: "monospace" }}>{val}</div>
            <div style={{ fontSize: 11, color: C.textMuted, marginTop: 4, textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
          </Card>
        ))}
      </div>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search files or smell types..."
          style={{ flex: 1, minWidth: 200, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 14px", color: C.text, fontSize: 13, outline: "none" }}
        />
        {["all", "high", "medium", "low"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", border: "none", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted,
          }}>{f}</button>
        ))}
        <button onClick={selectAll} style={{ padding: "7px 14px", borderRadius: 8, fontSize: 12, background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer" }}>Select All</button>
        <button onClick={clearAll} style={{ padding: "7px 14px", borderRadius: 8, fontSize: 12, background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer" }}>Clear</button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 420, overflowY: "auto", paddingRight: 4 }}>
        {filtered.map(f => {
          const isSelected = selected.has(f.relative_path);
          const hasHigh = (f.code_smells || []).some(s => s.severity === "high");
          return (
            <div key={f.relative_path} onClick={() => toggleFile(f.relative_path)} style={{
              background: isSelected ? `${C.accent}0d` : C.panel,
              border: `1px solid ${isSelected ? C.accent : C.border}`,
              borderRadius: 10, padding: "14px 18px", cursor: "pointer",
              transition: "all 0.2s", boxShadow: isSelected ? `0 0 12px ${C.accentGlow}` : "none"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{
                  width: 20, height: 20, borderRadius: 4, border: `2px solid ${isSelected ? C.accent : C.border}`,
                  background: isSelected ? C.accent : "transparent", display: "flex", alignItems: "center", justifyContent: "center",
                  flexShrink: 0, transition: "all 0.2s"
                }}>
                  {isSelected && <span style={{ color: "#000", fontSize: 12, fontWeight: 900 }}>✓</span>}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: C.text, fontFamily: "monospace" }}>{f.relative_path}</span>
                    <Badge label={f.language} color={C.info} />
                    {hasHigh && <Badge label="HIGH SEVERITY" color={C.danger} />}
                  </div>
                  <div style={{ display: "flex", gap: 16, marginTop: 6, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 11, color: C.textMuted }}>{f.metrics.lines_of_code} LOC</span>
                    <span style={{ fontSize: 11, color: C.textMuted }}>{f.metrics.functions} functions</span>
                    <span style={{ fontSize: 11, color: C.warn }}>{(f.code_smells || []).length} smell{(f.code_smells || []).length > 1 ? "s" : ""}</span>
                    <span style={{ fontSize: 11, color: f.quality_score >= 95 ? C.accent : C.warn }}>Quality: {f.quality_score}%</span>
                  </div>
                </div>
              </div>
              {isSelected && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.borderAcc}` }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {(f.code_smells || []).map((smell, idx) => (
                      <div key={idx} style={{ background: `${severityColor(smell.severity)}10`, border: `1px solid ${severityColor(smell.severity)}30`, borderRadius: 6, padding: "4px 10px", display: "flex", alignItems: "center", gap: 6 }}>
                        <div style={{ width: 6, height: 6, borderRadius: "50%", background: severityColor(smell.severity) }} />
                        <span style={{ fontSize: 11, color: C.textSub }}>{smell.type}</span>
                        <span style={{ fontSize: 10, color: C.textMuted }}>L{smell.line}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: 20, padding: "16px 20px", background: selected.size > 0 ? `${C.accent}0a` : C.panel, border: `1px solid ${selected.size > 0 ? C.accent : C.border}`, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          {selected.size > 0 ? (
            <span style={{ fontSize: 13, color: C.text }}>
              <span style={{ color: C.accent, fontWeight: 700 }}>{selected.size}</span> file{selected.size > 1 ? "s" : ""} selected ·{" "}
              <span style={{ color: C.warn, fontWeight: 700 }}>{selectedSmells.length}</span> smells ·{" "}
              {highCount > 0 && <span style={{ color: C.danger, fontWeight: 700 }}>{highCount} high severity</span>}
            </span>
          ) : (
            <span style={{ fontSize: 13, color: C.textMuted }}>Select files with code smells to proceed to the Refactoring Plan Agent</span>
          )}
        </div>
        <button onClick={handleApproveSelection} disabled={selected.size === 0 || isSubmitting} style={{
          padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: selected.size > 0 ? "pointer" : "not-allowed",
          background: selected.size > 0 ? C.accent : C.border, color: selected.size > 0 ? "#000" : C.textMuted, border: "none",
          boxShadow: selected.size > 0 ? `0 0 20px ${C.accentGlow}` : "none", transition: "all 0.2s"
        }}>
          {isSubmitting ? "Generating report..." : "Approve Selected Smells →"}
        </button>
      </div>
    </div>
  );
}
