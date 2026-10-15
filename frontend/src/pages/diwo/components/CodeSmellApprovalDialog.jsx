/**
 * CodeSmellApprovalDialog.jsx
 * ===========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The last look before the selection leaves DIWO for the Refactoring Planning
 * Agent: how much was detected, how much is going, how much stays behind, and
 * the exact files or findings that make up the payload.
 *
 * Presentational only. Confirm calls the page's existing
 * handleApproveSelection — the dialog never talks to the backend, so the
 * /smell-selection-pass -> onProceed -> /select-smells sequence has exactly
 * one owner.
 */

import { useEffect, useMemo, useRef } from "react";
import { T, tint, eyebrow, ghostButton, primaryButton, riskTone } from "../codeSmellTheme.js";
import { SeverityTag } from "../codeSmellUi.jsx";
import { formatEffort } from "../utils/smellGrouping";
import { smellLines } from "../utils/smellLines";

const SEVERITY_RANK = { high: 0, medium: 1, low: 2 };

function riskBand(score) {
  if (score < 0.35) return "low";
  return score < 0.65 ? "medium" : "high";
}

export default function CodeSmellApprovalDialog({
  mode,
  selectedRows,
  selectedFiles,
  totalFindings,
  summary,
  impactSummary,
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null);
  const isFile = mode === "file";

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onCancel?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Worst first, then by address: the order a reviewer wants to double-check in.
  const rows = useMemo(() => [...selectedRows].sort((a, b) =>
    (SEVERITY_RANK[a.smell.severity] ?? 9) - (SEVERITY_RANK[b.smell.severity] ?? 9)
    || a.file.localeCompare(b.file)
    || (a.smell.line || 0) - (b.smell.line || 0)), [selectedRows]);

  const perFile = useMemo(() => {
    const counts = new Map();
    selectedRows.forEach((r) => counts.set(r.file, (counts.get(r.file) || 0) + 1));
    return selectedFiles.map((file) => ({ file, count: counts.get(file) || 0 }));
  }, [selectedRows, selectedFiles]);

  const warnings = (impactSummary?.warnings || []).filter((w) => w.level !== "info");
  const risk = impactSummary?.executable_count ? riskBand(impactSummary.max_risk) : null;

  return (
    <div
      onClick={onCancel}
      style={{
        position: "fixed", inset: 0, zIndex: 1150, background: T.overlay,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dw-approve-title"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 100%)", maxHeight: "90vh",
          display: "flex", flexDirection: "column", overflow: "hidden",
          background: T.surface, border: `1px solid ${T.border}`, borderRadius: 14,
          boxShadow: T.shadowLg,
        }}
      >
        <div style={{ padding: "18px 20px 14px", borderBottom: `1px solid ${T.border}` }}>
          <div style={eyebrow}>Developer approval</div>
          <h2 id="dw-approve-title" style={{ margin: "4px 0 0", fontSize: 17, fontWeight: 800, color: T.text }}>
            Send approved findings to the Refactoring Planning Agent?
          </h2>
          <p style={{ margin: "6px 0 0", fontSize: 12.5, color: T.text2, lineHeight: 1.6 }}>
            RDP will plan refactorings for these {isFile ? "files" : "findings"} only. Everything not selected
            stays in the CUQA report and is not planned.
          </p>
        </div>

        <div style={{ overflowY: "auto", padding: "14px 20px" }}>
          <div style={{
            display: "grid", gap: 8,
            gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))",
          }}>
            <Tile label="Detected" value={totalFindings} />
            <Tile label="Approved" value={selectedRows.length} tone={T.accentText} />
            <Tile label="Excluded" value={totalFindings - selectedRows.length} />
            <Tile label="Affected files" value={summary.fileCount} />
            {typeof summary.effortMinutes === "number" && (
              <Tile label="Review effort" value={formatEffort(summary.effortMinutes)} />
            )}
            {risk && <Tile label="Max risk" value={risk} tone={riskTone(risk)} />}
          </div>

          {warnings.map((w, i) => (
            <div
              key={i}
              role="alert"
              style={{
                marginTop: 10, padding: "8px 11px", borderRadius: 8, fontSize: 12, lineHeight: 1.5,
                color: T.text,
                background: tint(w.level === "error" ? T.high : T.medium, 9),
                border: `1px solid ${tint(w.level === "error" ? T.high : T.medium, 32)}`,
              }}
            >
              <span aria-hidden="true">{w.level === "error" ? "⛔ " : "⚠ "}</span>{w.message}
            </div>
          ))}

          <div style={{ ...eyebrow, margin: "16px 0 8px" }}>
            {isFile ? `${selectedFiles.length} selected file${selectedFiles.length === 1 ? "" : "s"}` : `${rows.length} selected finding${rows.length === 1 ? "" : "s"}`}
          </div>

          <div style={{
            border: `1px solid ${T.border}`, borderRadius: 10, overflow: "hidden",
            background: T.sunken,
          }}>
            {isFile
              ? perFile.map(({ file, count }) => (
                <div key={file} style={listRow}>
                  <span style={{ flex: 1, minWidth: 0, fontFamily: T.mono, fontSize: 12, color: T.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={file}>
                    {file}
                  </span>
                  <span style={{ fontSize: 11.5, color: T.text2, flexShrink: 0 }}>
                    <b style={{ fontFamily: T.mono, color: T.text }}>{count}</b> finding{count === 1 ? "" : "s"}
                  </span>
                </div>
              ))
              : rows.map((row) => {
                const { start, end } = smellLines(row.smell);
                return (
                  <div key={row.id} style={listRow}>
                    <SeverityTag severity={row.smell.severity} compact />
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.text, flexShrink: 0 }}>
                      {row.smell.type}
                    </span>
                    <span
                      title={row.file}
                      style={{ flex: 1, minWidth: 0, fontFamily: T.mono, fontSize: 11.5, color: T.text2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      {row.file}{start ? `:${start}${end > start ? `–${end}` : ""}` : ""}
                      {row.smell.entity ? <span style={{ color: T.text3 }}> · {row.smell.entity}</span> : null}
                    </span>
                  </div>
                );
              })}
          </div>
        </div>

        <div style={{
          display: "flex", justifyContent: "flex-end", gap: 10, flexWrap: "wrap",
          padding: "12px 20px", borderTop: `1px solid ${T.border}`, background: T.surface2,
        }}>
          <button type="button" onClick={onCancel} className="dw-focus dw-hover" style={ghostButton(true, { padding: "9px 16px", fontSize: 12.5 })}>
            Keep reviewing
          </button>
          <button ref={confirmRef} type="button" onClick={onConfirm} className="dw-focus" style={primaryButton(true)}>
            Send {isFile ? `${selectedFiles.length} file${selectedFiles.length === 1 ? "" : "s"}` : `${rows.length} finding${rows.length === 1 ? "" : "s"}`} to RDP →
          </button>
        </div>
      </div>
    </div>
  );
}

const listRow = {
  display: "flex", alignItems: "center", gap: 9,
  padding: "8px 12px", borderTop: `1px solid ${T.border}`, marginTop: -1,
};

function Tile({ label, value, tone }) {
  return (
    <div style={{ padding: "9px 11px", borderRadius: 9, background: T.sunken, border: `1px solid ${T.border}` }}>
      <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono, color: tone || T.text, textTransform: "capitalize" }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: T.text3, marginTop: 1 }}>{label}</div>
    </div>
  );
}
