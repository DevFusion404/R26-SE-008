/**
 * SmellCategoryOverview.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *   🗂 Code Smell Category Overview  [491 total]      Click a row to expand
 *   ┌───────────────────────┬───────────────────────┬───────────────────────┐
 *   │ 📦 Bloaters ·CRITICAL │ 🧩 OO Abusers ·MEDIUM │ 🔒 Change Prev ·CRIT  │
 *   │ ███                25›│ █                   3›│ ██                  7›│
 *   ├───────────────────────┼───────────────────────┼───────────────────────┤
 *   │ 🗑️ Dispensables ·LOW  │ 🔗 Couplers ·MEDIUM   │ 🛡️ Security ·CRITICAL │
 *   │ █████             118›│ ████               97›│ █████████         241›│
 *   └───────────────────────┴───────────────────────┴───────────────────────┘
 *
 * CUQA's own taxonomy — Bloaters, Object-Orientation Abusers, Change
 * Preventers, Dispensables, Couplers, Security / Language-Specific — rendered
 * as the first thing Stage 1 shows, because "what KIND of problem does this
 * repository have" is the question a developer asks before "which file".
 *
 * The counts come from the ORCHESTRATOR
 * (GET /workflows/<id>/smell-categories), computed over the smells this
 * workflow holds. Not from CUQA directly, and not recounted here: the numbers
 * on this bar have to be the same numbers the checkboxes below it operate on.
 * Every figure on screen — count, share, type breakdown — is derived from that
 * payload; none of it is written into this file.
 *
 * EVERY CATEGORY IS SHOWN, including the ones this repository has none of.
 * They come from `taxonomy.catalog`, which the orchestrator builds from CUQA's
 * own CATEGORY_ORDER. A bar listing only the categories with findings answers
 * "here are two categories" but not "and none of the other five" — and the
 * second half is the more useful thing to learn about a codebase. A zero row
 * is evidence, so it is rendered plainly and reads 0.
 *
 * The count in the HEADER still counts only the categories that have findings.
 * Seven rows on screen and "2 of 7 categories" above them are both true, and
 * they are answering different questions.
 *
 * Two gestures, two outcomes
 * --------------------------
 * Clicking a ROW expands it, listing the smell types inside that category with
 * their own counts. Filtering the list below is a separate, named action
 * inside that panel. They used to be the same click, which meant looking up
 * what a category contained silently rearranged the worklist underneath it.
 *
 * A category with no findings expands to nothing, so it does not open at all.
 */

import { Fragment, useState } from "react";
import { C } from "../diwoTheme.jsx";
import { categoryIcon } from "../utils/smellIcons";

const ALL = "all";

/**
 * A colour per category, so a row is identifiable at a glance and the bars can
 * be compared down a column.
 *
 * This is the one place on the page that spends colour on identity rather than
 * on severity — everything below it keeps the small palette, where teal means
 * selected and red/amber/green mean severity. The ACTIVE row is marked with a
 * teal ring rather than a colour change, so "which filter is on" stays legible
 * against seven different hues.
 */
const CATEGORY_TONE = {
  Bloaters: "#ef4444",
  "Object-Orientation Abusers": "#eab308",
  "Change Preventers": "#f97316",
  Dispensables: "#3b82f6",
  Couplers: "#a855f7",
  "Security / Language-Specific": "#ef4444",
  Uncategorized: "#64748b",
};

/**
 * Category priority is CUQA's architectural risk class for the group, and it
 * is NOT the same axis as a smell's severity — a category can be `critical`
 * while holding only low-severity findings.
 */
const PRIORITY_TONE = {
  critical: "#ef4444",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#3b82f6",
};

const SEVERITY_TONE = { high: C.high, medium: C.medium, low: C.low };

const CSS = `
.diwo-cat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
@media (max-width: 1080px) {
  .diwo-cat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 660px) {
  .diwo-cat-grid { grid-template-columns: minmax(0, 1fr); }
}
.diwo-cat-row {
  transition: background 0.15s ease;
}
.diwo-cat-row[data-live="yes"]:hover {
  background: rgba(255, 255, 255, 0.035);
}
.diwo-cat-chev {
  transition: transform 0.18s ease;
}
.diwo-cat-row[aria-expanded="true"] .diwo-cat-chev {
  transform: rotate(90deg);
}
.diwo-cat-panel {
  animation: diwoCatOpen 0.18s ease-out;
}
@keyframes diwoCatOpen {
  from { opacity: 0; transform: translateY(-5px); }
  to   { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .diwo-cat-row, .diwo-cat-chev, .diwo-cat-panel { transition: none; animation: none; }
}
`;

export default function SmellCategoryOverview({
  taxonomy,
  active = ALL,
  onSelect,
  loading = false,
}) {
  // Which category is unfolded. One at a time: each panel spans the full grid
  // width, and two of them open at once pushes the rows below out of the
  // arrangement the reader just scanned.
  const [open, setOpen] = useState(null);

  // No taxonomy means the endpoint is unavailable or the workflow has no
  // smells. Either way there is nothing truthful to show, so the panel simply
  // does not appear — Stage 1 behaves exactly as it did before it existed.
  if (!taxonomy || !(taxonomy.categories || []).length) {
    return null;
  }

  // `catalog` is every category; `categories` is only those with findings.
  // The fallback keeps this bar working against an orchestrator that predates
  // the catalog rather than rendering nothing.
  const rows = (taxonomy.catalog || []).length
    ? taxonomy.catalog
    : (taxonomy.categories || []).map((c) => ({ ...c, present: true }));

  // The type breakdown lives only on the worklist rows.
  const detailOf = new Map((taxonomy.categories || []).map((c) => [c.category, c]));
  const total = taxonomy.total_smells || 0;

  return (
    <div style={{ marginBottom: 16 }}>
      <style>{CSS}</style>

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        flexWrap: "wrap", marginBottom: 9,
      }}>
        <span aria-hidden="true" style={{ fontSize: 14 }}>🗂</span>
        <span style={{
          fontSize: 11.5, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 1, color: C.text,
        }}>
          Code Smell Category Overview
        </span>

        <span style={{
          padding: "2px 9px", borderRadius: 20, fontSize: 11, fontWeight: 700,
          background: `${C.accent}14`, border: `1px solid ${C.accent}45`,
          color: C.accent, fontFamily: "monospace",
        }}>
          {total} total
        </span>

        <span style={{ fontSize: 10.5, color: C.textMuted }}>
          {taxonomy.category_count} of {rows.length} categories present ·{" "}
          {taxonomy.type_count} distinct smell type{taxonomy.type_count === 1 ? "" : "s"}
        </span>

        {loading && (
          <span style={{ fontSize: 10.5, color: C.textMuted, fontStyle: "italic" }}>
            refreshing…
          </span>
        )}

        <span style={{ marginLeft: "auto", fontSize: 10.5, color: C.textMuted }}>
          Click a row to expand
        </span>
      </div>

      {/* ── The grid ───────────────────────────────────────────────────── */}
      <div
        className="diwo-cat-grid"
        style={{
          borderRadius: 10, overflow: "hidden",
          background: C.panel, border: `1px solid ${C.border}`,
        }}
      >
        {rows.map((row) => {
          const detail = detailOf.get(row.category);
          const empty = !row.present || row.count === 0;
          const expanded = open === row.category;
          const isActive = active === row.category;
          const tone = CATEGORY_TONE[row.category] || C.textMuted;
          // The share this category carries, from the payload's own total.
          const share = total > 0 ? (row.count / total) * 100 : 0;

          return (
            <Fragment key={row.category}>
              <button
                type="button"
                className="diwo-cat-row"
                data-live={empty ? "no" : "yes"}
                disabled={empty}
                // Omitted, not false, on a category with nothing to open: a
                // screen reader announcing "collapsed" promises a panel that
                // does not exist.
                aria-expanded={empty ? undefined : expanded}
                onClick={empty ? undefined : () => setOpen(expanded ? null : row.category)}
                title={empty
                  ? `${row.category} — ${row.priority} priority\nNo findings in this repository`
                  : `${row.category} — ${row.priority} priority\n` +
                    `${row.count} finding(s) across ${row.file_count} file(s)\n` +
                    `${share.toFixed(1)}% of all findings`}
                style={{
                  // Inset rather than a real border: the lines then land inside
                  // the rounded container and an incomplete last row draws no
                  // stray edge across the gap where cells do not exist.
                  boxShadow: `inset -1px -1px 0 ${C.border}`,
                  padding: "10px 12px 9px",
                  background: isActive ? `${C.accent}0d` : "transparent",
                  // Sides spelled out rather than `border: "none"` plus a
                  // left override: borderLeft changes with `isActive`, and
                  // React warns when it updates a longhand while the matching
                  // shorthand is also set.
                  borderTop: "none",
                  borderRight: "none",
                  borderBottom: "none",
                  borderLeft: `2px solid ${isActive ? C.accent : "transparent"}`,
                  textAlign: "left",
                  cursor: empty ? "default" : "pointer",
                  opacity: empty ? 0.5 : 1,
                  display: "flex", flexDirection: "column", gap: 7,
                  minWidth: 0,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  <span aria-hidden="true" style={{ fontSize: 13, flexShrink: 0 }}>
                    {categoryIcon(row.category)}
                  </span>

                  <span style={{
                    fontSize: 12, fontWeight: 700, color: C.text,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    minWidth: 0,
                  }}>
                    {row.category}
                  </span>

                  <PriorityBadge priority={row.priority} />

                  <span style={{
                    marginLeft: "auto", flexShrink: 0,
                    fontSize: 15, fontWeight: 800, fontFamily: "monospace",
                    color: empty ? C.textMuted : tone,
                  }}>
                    {row.count}
                  </span>

                  <span
                    aria-hidden="true"
                    className="diwo-cat-chev"
                    style={{
                      flexShrink: 0, fontSize: 11, color: C.textMuted,
                      display: "inline-block",
                    }}
                  >
                    ›
                  </span>
                </div>

                {/* Share of all findings — the bar is the only place the
                    relative size of a category is visible at a glance. */}
                <div style={{
                  height: 3, borderRadius: 3, background: C.border, overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%", width: `${share}%`, background: tone,
                    borderRadius: 3, transition: "width 0.25s ease",
                  }} />
                </div>
              </button>

              {expanded && detail && (
                <CategoryPanel
                  detail={detail}
                  tone={tone}
                  share={share}
                  isActive={isActive}
                  onFilter={() => onSelect?.(isActive ? ALL : row.category)}
                />
              )}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}

/** CUQA's architectural risk class for the category, named rather than implied. */
function PriorityBadge({ priority }) {
  if (!priority) return null;
  const tone = PRIORITY_TONE[priority] || C.textMuted;
  return (
    <span style={{
      flexShrink: 0, display: "inline-flex", alignItems: "center", gap: 4,
      padding: "1px 7px", borderRadius: 20,
      background: `${tone}18`, border: `1px solid ${tone}45`, color: tone,
      fontSize: 8.5, fontWeight: 800, letterSpacing: 0.6, textTransform: "uppercase",
    }}>
      <span aria-hidden="true" style={{
        width: 4, height: 4, borderRadius: "50%", background: tone,
      }} />
      {priority}
    </span>
  );
}

/**
 * What is actually inside one category: its smell types, each with its own
 * count, spread of files and worst severity.
 *
 * Spans the whole grid so the breakdown is read as a list rather than squeezed
 * into a third of the width, and so it sits directly under the row that opened
 * it in every column layout.
 */
export function CategoryPanel({ detail, tone, share, isActive, onFilter }) {
  const types = detail.types || [];

  return (
    <div
      className="diwo-cat-panel"
      style={{
        gridColumn: "1 / -1",
        boxShadow: `inset 0 -1px 0 ${C.border}`,
        background: C.bg,
        borderLeft: `2px solid ${tone}`,
        padding: "11px 14px 12px",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        flexWrap: "wrap", marginBottom: 9,
      }}>
        <span style={{
          fontSize: 9.5, fontWeight: 800, letterSpacing: 0.9,
          textTransform: "uppercase", color: C.textMuted,
        }}>
          {types.length} smell type{types.length === 1 ? "" : "s"} in this category
        </span>
        <span style={{ fontSize: 10.5, color: C.textMuted }}>
          {detail.count} finding{detail.count === 1 ? "" : "s"} ·{" "}
          {detail.file_count} file{detail.file_count === 1 ? "" : "s"} ·{" "}
          {share.toFixed(1)}% of all findings
        </span>

        {/* Filtering is a NAMED action, not a side effect of looking. */}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onFilter(); }}
          style={{
            marginLeft: "auto", padding: "5px 12px", borderRadius: 7,
            cursor: "pointer", fontSize: 10.5, fontWeight: 700,
            background: isActive ? `${C.accent}1e` : C.panel,
            color: isActive ? C.accent : C.textSub,
            border: `1px solid ${isActive ? C.accent : C.border}`,
          }}
        >
          {isActive ? "Clear this filter" : "Show only this category"}
        </button>
      </div>

      <div style={{
        display: "grid", gap: 6,
        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
      }}>
        {types.map((type) => {
          const severity = SEVERITY_TONE[type.worst_severity] || C.textMuted;
          return (
            <div
              key={type.type}
              title={Object.entries(type.severities || {})
                .map(([level, n]) => `${n} ${level}`).join(" · ")}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "6px 10px", borderRadius: 7,
                background: C.panel, border: `1px solid ${C.border}`,
                minWidth: 0,
              }}
            >
              <span aria-hidden="true" style={{
                width: 5, height: 5, borderRadius: "50%", background: severity, flexShrink: 0,
              }} />
              <span style={{
                fontSize: 11.5, color: C.text, fontWeight: 600,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                minWidth: 0,
              }}>
                {type.type}
              </span>
              <span style={{ fontSize: 9.5, color: C.textMuted, flexShrink: 0 }}>
                {type.file_count} file{type.file_count === 1 ? "" : "s"}
              </span>
              <span style={{
                marginLeft: "auto", flexShrink: 0,
                fontSize: 12, fontWeight: 800, fontFamily: "monospace", color: tone,
              }}>
                {type.count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
