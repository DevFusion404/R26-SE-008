/**
 * Shared code / diff renderers for the DIWO stages
 * ================================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Transformation stage and the Results stage show the same code the same
 * way, so the renderers live here rather than being copied into both.
 *
 * The diff is hunk-grouped: each change region prints every removed line as
 * one red block, then every line that replaced them as one blue block, with
 * unchanged lines left in place as context between regions. Grouping is what
 * buildDiffSegments() in sctvaApi.js produces; this file only paints it.
 */

import { useMemo } from "react";
import { C } from "../diwoTheme.jsx";
import { buildDiffSegments } from "../services/sctvaApi";

/** Scrollable dark panel every code and diff view sits in. */
export function CodeSurface({ children, maxHeight = 460, style = {} }) {
  return (
    <div
      style={{
        background: "#0b1020",
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        overflow: "auto",
        fontFamily: "Fira Code, Courier New, monospace",
        fontSize: 11,
        lineHeight: 1.6,
        padding: "8px 0",
        maxHeight,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

const DIFF_TONES = {
  before: { rail: "#ef4444", fill: "rgba(239,68,68,0.12)", text: "#fecaca", label: "Original" },
  after: { rail: "#3b82f6", fill: "rgba(59,130,246,0.12)", text: "#bfdbfe", label: "Refactored" },
};

/** "lines 12–15", or "line 12" for a single line. */
const rangeLabel = (rows, key) => {
  const numbers = rows.map((r) => r[key] ?? r.lineNo).filter((n) => typeof n === "number");
  if (numbers.length === 0) return "";
  const lo = Math.min(...numbers);
  const hi = Math.max(...numbers);
  return lo === hi ? `line ${lo}` : `lines ${lo}–${hi}`;
};

function changeTitle(segment) {
  const from = rangeLabel(segment.before, "beforeNo");
  const to = rangeLabel(segment.after, "afterNo");
  if (from && to) return `Replaced ${from} with ${to}`;
  if (from) return `Removed ${from}`;
  if (to) return `Inserted ${to}`;
  return "Change";
}

/** Monospace code block with line numbers. */
export function CodeBlock({ code, emptyMessage = "No code to display." }) {
  const text = String(code ?? "");

  if (!text.trim()) {
    return (
      <div style={{ padding: 20, color: C.textMuted, fontSize: 12, textAlign: "center" }}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={{ minWidth: "min-content" }}>
      {text.split("\n").map((line, idx) => (
        <div
          key={`line-${idx}`}
          style={{ display: "grid", gridTemplateColumns: "56px 1fr", padding: "1px 0" }}
        >
          <span style={{ color: "#64748b", textAlign: "right", paddingRight: 10, userSelect: "none" }}>
            {idx + 1}
          </span>
          <span style={{ whiteSpace: "pre", color: "#cbd5e1", padding: "0 12px 0 6px" }}>
            {line || " "}
          </span>
        </div>
      ))}
    </div>
  );
}

function DiffLine({ row, tone }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "56px 24px 1fr", padding: "1px 0" }}>
      <span style={{ color: "#64748b", textAlign: "right", paddingRight: 8, userSelect: "none" }}>
        {row.lineNo}
      </span>
      <span style={{ color: tone.rail, textAlign: "center", userSelect: "none", fontWeight: 700 }}>
        {row.marker}
      </span>
      <span style={{ whiteSpace: "pre", color: tone.text, padding: "0 12px 0 6px" }}>
        {row.text || " "}
      </span>
    </div>
  );
}

/** One side of a change: every removed line together, or every added line together. */
function DiffGroup({ rows, kind }) {
  if (rows.length === 0) return null;
  const tone = DIFF_TONES[kind];

  return (
    <div style={{ background: tone.fill, borderLeft: `3px solid ${tone.rail}` }}>
      <div
        style={{
          fontSize: 9,
          fontWeight: 700,
          letterSpacing: 1,
          textTransform: "uppercase",
          color: tone.rail,
          padding: "3px 0 3px 62px",
        }}
      >
        {tone.label} · {rows.length} line{rows.length === 1 ? "" : "s"}
      </div>
      {rows.map((row) => (
        <DiffLine key={row.key} row={row} tone={tone} />
      ))}
    </div>
  );
}

/**
 * Hunk-grouped diff. Each change region is framed on its own, showing the
 * removed lines as one red block and the lines that replaced them as one blue
 * block beneath it.
 */
export function DiffBlock({ rows, emptyMessage = "No differences to display for this file." }) {
  const segments = useMemo(() => buildDiffSegments(rows), [rows]);
  const changeCount = segments.filter((s) => s.type === "change").length;

  if (!rows || rows.length === 0 || changeCount === 0) {
    return (
      <div style={{ padding: 20, color: C.textMuted, fontSize: 12, textAlign: "center" }}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={{ minWidth: "min-content" }}>
      {segments.map((segment, idx) => {
        if (segment.type === "context") {
          return (
            <div key={`ctx-${idx}`}>
              {segment.rows.map((row) => (
                <div
                  key={row.key}
                  style={{ display: "grid", gridTemplateColumns: "56px 24px 1fr", padding: "1px 0" }}
                >
                  <span style={{ color: "#3f4a5f", textAlign: "right", paddingRight: 8, userSelect: "none" }}>
                    {row.lineNo}
                  </span>
                  <span style={{ color: "#3f4a5f", textAlign: "center", userSelect: "none" }}>·</span>
                  <span style={{ whiteSpace: "pre", color: "#8a97ab", padding: "0 12px 0 6px" }}>
                    {row.text || " "}
                  </span>
                </div>
              ))}
            </div>
          );
        }

        return (
          <div
            key={`chg-${segment.hunk}-${idx}`}
            style={{
              margin: "8px 8px 8px 0",
              border: `1px solid ${C.borderAcc}`,
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "5px 12px",
                background: "rgba(148,163,184,0.08)",
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.6,
                color: C.textSub,
              }}
            >
              <span style={{ color: C.accent }}>Change {segment.ordinal}/{changeCount}</span>
              <span style={{ color: C.textMuted, fontWeight: 500 }}>{changeTitle(segment)}</span>
            </div>
            <DiffGroup rows={segment.before} kind="before" />
            <DiffGroup rows={segment.after} kind="after" />
          </div>
        );
      })}
    </div>
  );
}

/** Legend for the grouped diff, shared by both stages. */
export function DiffLegend() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8, fontSize: 11, flexWrap: "wrap" }}>
      <span style={{ color: DIFF_TONES.before.rail, fontWeight: 700 }}>- Original</span>
      <span style={{ color: DIFF_TONES.after.rail, fontWeight: 700 }}>+ Refactored</span>
      <span style={{ color: C.textMuted }}>
        Each change shows the replaced lines as one red block, then the lines that replaced
        them as one blue block.
      </span>
    </div>
  );
}
