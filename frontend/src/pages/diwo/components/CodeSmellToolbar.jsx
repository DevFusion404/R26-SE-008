/**
 * CodeSmellToolbar.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The compact toolbar above the findings list:
 *
 *     [File wise | Smell wise | Category wise]     [Search…………]  [Sort ▾]
 *     [✓ Select shown (57)] [✕ Clear (12)]  [Smell type ▾] [Category ▾]
 *     Showing 57 of 57 findings · 6 smell types       Expand all · Collapse
 *     Filtered to: [Category Bloaters ✕] [Severity high ✕]
 *
 * Filters that need room to show their counts (severity, category, smell type)
 * live in the left rail; this strip holds the controls a developer reaches for
 * while reading the list. Select-shown and Clear are the only controls here
 * that change what reaches the planning agent, so they are the only two that
 * look like actions.
 */

import { T, tint, eyebrow, ghostButton } from "../codeSmellTheme.js";

// ─── Mode ────────────────────────────────────────────────────────────────────

const REVIEW_MODES = [
  { key: "file", label: "File wise", hint: "Select whole files" },
  { key: "smell", label: "Smell wise", hint: "Select individual findings, grouped by smell type" },
  { key: "category", label: "Category wise", hint: "Select individual findings, grouped by CUQA category" },
];

/** Segmented control. The active segment is raised, not painted. */
export function ModeSwitch({ mode, onChange, counts }) {
  return (
    <div
      role="tablist"
      aria-label="Review mode"
      style={{
        display: "inline-flex", gap: 3, padding: 3, flexShrink: 0,
        background: T.sunken, border: `1px solid ${T.border}`, borderRadius: 10,
      }}
    >
      {REVIEW_MODES.map((m) => {
        const active = m.key === mode;
        return (
          <button
            key={m.key}
            type="button"
            role="tab"
            aria-selected={active}
            title={m.hint}
            className="dw-focus"
            onClick={() => onChange(m.key)}
            style={{
              display: "flex", alignItems: "center", gap: 7,
              padding: "6px 12px", borderRadius: 7, cursor: "pointer",
              border: `1px solid ${active ? T.border : "transparent"}`,
              background: active ? T.surface : "transparent",
              boxShadow: active ? T.shadowSm : "none",
              color: active ? T.text : T.text2,
              fontSize: 12, fontWeight: 700, whiteSpace: "nowrap",
              transition: "background 0.15s, color 0.15s",
            }}
          >
            {m.label}
            {counts?.[m.key] !== undefined && (
              <span style={{
                fontSize: 10, fontFamily: T.mono, fontWeight: 700,
                padding: "0 6px", borderRadius: 999,
                background: active ? tint(T.accent, 14) : T.surface2,
                color: active ? T.accentText : T.text3,
              }}>
                {counts[m.key]}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ─── Toolbar ─────────────────────────────────────────────────────────────────

const PLACEHOLDER = {
  file: "Search files, smell types, entities…",
  smell: "Search smell types, files, entities, messages…",
  category: "Search categories, smell types, files…",
};

const GROUP_NOUN = { file: "file", smell: "smell type", category: "category" };

/**
 * `sortOptions` entries are { key, label, disabled?, hint? }. An option whose
 * figures do not exist (no impact records) is rendered disabled rather than
 * sorting by a column of blanks.
 */
export function ReviewToolbar({
  mode, onModeChange, modeCounts,
  search, onSearch,
  sort, sortOptions, onSort,
  onSelectAll, onClearSelection, selectableCount, selectionCount,
  onExpandAll, onCollapseAll, groupCount, anyOpen,
  shownFindings, totalFindings,
  quickSelect,
  activeFilters = [], onClearFilters,
}) {
  const noun = GROUP_NOUN[mode] || "group";
  const unit = mode === "file" ? "file" : "finding";

  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12,
      boxShadow: T.shadowSm, padding: 12, marginBottom: 12,
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      {/* Row 1 — how to look: mode, search, sort */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <ModeSwitch mode={mode} onChange={onModeChange} counts={modeCounts} />

        <div style={{ position: "relative", flex: "1 1 220px", minWidth: 0 }}>
          <span aria-hidden="true" style={{
            position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)",
            color: T.text3, fontSize: 13, pointerEvents: "none",
          }}>
            ⌕
          </span>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={PLACEHOLDER[mode] || PLACEHOLDER.smell}
            aria-label="Search findings"
            className="dw-input"
            style={{
              width: "100%", padding: "8px 12px 8px 30px", borderRadius: 9,
              background: T.sunken, border: `1px solid ${T.border}`,
              color: T.text, fontSize: 12.5, boxSizing: "border-box",
            }}
          />
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 7, flexShrink: 0 }}>
          <span style={{ ...eyebrow, letterSpacing: 0.6 }}>Sort</span>
          <select
            value={sort}
            onChange={(e) => onSort(e.target.value)}
            aria-label={`Sort ${noun}s`}
            className="dw-input"
            style={{
              padding: "7px 10px", borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: T.sunken, color: T.text, border: `1px solid ${T.border}`,
              cursor: "pointer",
            }}
          >
            {sortOptions.map((o) => (
              <option key={o.key} value={o.key} disabled={o.disabled}>
                {o.label}{o.disabled ? " (needs impact data)" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Row 2 — what to send: the two bulk verdicts, then quick selection */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button
          type="button"
          onClick={onSelectAll}
          disabled={!selectableCount}
          className="dw-focus"
          title={`Select every ${unit} the current filters show (${selectableCount})`}
          style={{
            display: "inline-flex", alignItems: "center", gap: 7,
            padding: "8px 14px", borderRadius: 8, fontSize: 12, fontWeight: 700,
            cursor: selectableCount ? "pointer" : "not-allowed",
            background: selectableCount ? tint(T.accent, 12) : T.sunken,
            color: selectableCount ? T.accentText : T.text3,
            border: `1px solid ${selectableCount ? tint(T.accent, 45) : T.border}`,
          }}
        >
          <span aria-hidden="true">✓</span>
          Select shown
          <span style={{ fontFamily: T.mono, opacity: 0.8 }}>({selectableCount})</span>
        </button>

        <button
          type="button"
          onClick={onClearSelection}
          disabled={!selectionCount}
          className="dw-focus dw-hover"
          title={selectionCount ? `Clear all ${selectionCount} selected ${unit}(s), including any hidden by filters` : "Nothing is selected"}
          style={ghostButton(Boolean(selectionCount), {
            padding: "8px 14px",
            color: selectionCount ? T.high : T.text3,
            borderColor: selectionCount ? tint(T.high, 40) : T.border,
          })}
        >
          <span aria-hidden="true">✕</span>
          Clear selection
          {selectionCount > 0 && (
            <span style={{ fontFamily: T.mono, opacity: 0.8 }}>({selectionCount})</span>
          )}
        </button>

        {quickSelect && (
          <div style={{
            display: "flex", gap: 8, flexWrap: "wrap", flex: "1 1 360px", minWidth: 0,
          }}>
            {quickSelect}
          </div>
        )}
      </div>

      {/* Row 3 — status line and accordion controls */}
      <div style={{
        display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center",
        paddingTop: 8, borderTop: `1px solid ${T.border}`,
      }}>
        <span style={{ fontSize: 12, color: T.text2 }}>
          Showing <b style={{ color: T.text, fontFamily: T.mono }}>{shownFindings}</b> of{" "}
          <b style={{ color: T.text, fontFamily: T.mono }}>{totalFindings}</b> finding
          {totalFindings === 1 ? "" : "s"} in{" "}
          <b style={{ color: T.text, fontFamily: T.mono }}>{groupCount}</b> {noun}
          {groupCount === 1 ? "" : "s"}
        </span>

        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            type="button"
            onClick={onExpandAll}
            disabled={!groupCount}
            className="dw-focus dw-hover"
            title={groupCount ? `Open every ${noun} and everything nested inside it` : "Nothing to expand"}
            style={ghostButton(Boolean(groupCount), { padding: "5px 10px", fontSize: 11.5 })}
          >
            Expand all
          </button>
          <button
            type="button"
            onClick={onCollapseAll}
            disabled={!anyOpen}
            className="dw-focus dw-hover"
            title={anyOpen ? "Collapse everything back to the group headings" : "Nothing is expanded"}
            style={ghostButton(anyOpen, { padding: "5px 10px", fontSize: 11.5 })}
          >
            Collapse all
          </button>
        </span>
      </div>

      {activeFilters.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={eyebrow}>Filtered to</span>
          {activeFilters.map((f) => (
            <FilterChip key={f.key} label={f.label} value={f.value} tone={f.tone} onClear={f.onClear} />
          ))}
          {onClearFilters && activeFilters.length > 1 && (
            <button
              type="button"
              onClick={onClearFilters}
              className="dw-focus"
              style={{
                background: "none", border: "none", padding: "2px 4px", cursor: "pointer",
                color: T.accentText, fontSize: 11.5, fontWeight: 700,
              }}
            >
              Clear all filters
            </button>
          )}
          <span style={{ fontSize: 11, color: T.text3 }}>
            Selected findings hidden by a filter stay selected.
          </span>
        </div>
      )}
    </div>
  );
}

function FilterChip({ label, value, tone, onClear }) {
  const color = tone || T.accentText;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "3px 6px 3px 10px", borderRadius: 999,
      background: tint(tone || T.accent, 10), border: `1px solid ${tint(tone || T.accent, 35)}`,
      fontSize: 11.5, fontWeight: 600, color,
    }}>
      <span style={{ color: T.text3, fontWeight: 500 }}>{label}</span>
      {value}
      <button
        type="button"
        onClick={onClear}
        aria-label={`Clear the ${label} filter`}
        className="dw-focus"
        style={{
          width: 18, height: 18, borderRadius: "50%", border: "none", cursor: "pointer",
          background: "transparent", color: "inherit", fontWeight: 800, fontSize: 11, lineHeight: 1,
        }}
      >
        ✕
      </button>
    </span>
  );
}

