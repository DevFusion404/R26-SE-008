/**
 * CodeSmellToolbar.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Stage 1 header furniture: the four stat cards, the report-source card,
 * the selection-mode segmented control, and the search / severity strip.
 *
 * One rule runs through all of it — the palette carries meaning, so it stays
 * small. Teal marks what is active or good, amber marks medium, red marks high
 * severity, and everything else is neutral. The previous version gave each
 * stat card its own colour and each file its own accent, which meant the two
 * signals that actually matter (severity and selection) had to compete with
 * eight that did not.
 */

import { C } from "../diwoTheme.jsx";

// ─── Stats ───────────────────────────────────────────────────────────────────

function StatCard({ label, value, hint, tone = C.accent }) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.border}`, borderRadius: 11,
      padding: "15px 17px", minWidth: 0,
    }}>
      <div style={{
        fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 26, fontWeight: 800, color: tone, fontFamily: "monospace",
        lineHeight: 1.2, marginTop: 5,
      }}>
        {value}
      </div>
      {hint && (
        <div style={{
          fontSize: 10.5, color: C.textMuted, marginTop: 3,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {hint}
        </div>
      )}
    </div>
  );
}

/**
 * Four equal cards. Only High Severity is allowed a warning colour, and only
 * when it is non-zero: a red "0" is a false alarm.
 */
export function CodeSmellStats({ files, smells, high, quality, languages, categories }) {
  const languageHint = languages?.length
    ? `${languages.slice(0, 2).join(", ")}${languages.length > 2 ? ` +${languages.length - 2}` : ""} scanned`
    : "files scanned";

  return (
    <div style={{
      display: "grid", gap: 12, marginBottom: 14,
      gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
    }}>
      <StatCard label="Files analysed" value={files} hint={languageHint} />
      <StatCard
        label="Total smells"
        value={smells}
        hint={categories ? `across ${categories} categor${categories === 1 ? "y" : "ies"}` : undefined}
      />
      <StatCard
        label="High severity"
        value={high}
        hint={high > 0 ? "requires attention" : "none found"}
        tone={high > 0 ? C.danger : C.textSub}
      />
      <StatCard label="Avg quality" value={quality} hint="overall code quality" />
    </div>
  );
}

// ─── Mode ────────────────────────────────────────────────────────────────────

const REVIEW_MODES = [
  { key: "file", label: "File wise" },
  { key: "smell", label: "Smell wise" },
  { key: "category", label: "Category wise" },
];

/**
 * The orange the active review mode is painted in.
 *
 * Named here rather than pulled from the theme: the palette's amber (C.warn)
 * means medium severity everywhere else on this page, and the mode switch is
 * not a severity. Same family, different job, so it gets its own constant
 * instead of borrowing one whose meaning is already spoken for.
 */
const MODE_ACTIVE = "#ccbbbc";

/** Segmented control. Only the active segment is filled. */
export function ModeSwitch({ mode, onChange, counts }) {
  return (
    <div>
      <div style={{
        fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700, marginBottom: 7,
      }}>
        Selection mode
      </div>
      <div
        role="tablist"
        style={{
          display: "inline-flex", gap: 4, padding: 4,
          background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10,
        }}
      >
        {REVIEW_MODES.map((m) => {
          const active = m.key === mode;
          return (
            <button
              key={m.key}
              role="tab"
              aria-selected={active}
              onClick={() => onChange(m.key)}
              style={{
                display: "flex", alignItems: "center", gap: 7,
                padding: "7px 15px", borderRadius: 7, border: "none", cursor: "pointer",
                background: active ? MODE_ACTIVE : "transparent",
                color: active ? "#0d0f14" : C.textMuted,
                fontSize: 12, fontWeight: 700, transition: "all 0.15s",
              }}
            >
              {m.label}
              {counts?.[m.key] !== undefined && (
                <span style={{ fontSize: 10, opacity: 0.8, fontFamily: "monospace" }}>
                  {counts[m.key]}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Search + severity ───────────────────────────────────────────────────────

const SEVERITY_TONE = { all: C.accent, high: C.danger, medium: C.warn, low: C.textSub };

const PLACEHOLDER = {
  file: "Search files or smell types…",
  smell: "Search smell types, files or entities…",
  category: "Search categories, smell types or files…",
};

export function SearchAndSeverity({
  mode, search, onSearch, severity, onSeverity, onSelectAll, onRejectAll,
  onExpandAll, onCollapseAll, visibleCount, selectedCount, groupCount = 0,
  anyOpen = false,
}) {
  const noun = GROUP_NOUN[mode] || "group";

  return (
    <>
      {/* ── The two bulk verdicts, on their own line ───────────────────────
          Lifted out of the filter strip because they are not filters. Select
          all and Reject all are the only controls on this page that change
          what gets sent to the planning agent, and they were sitting fourth
          in a row of look-at-it controls, styled like them. They are solid
          buttons now, above the line rather than in it. */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        marginBottom: 12, padding: "10px 14px", borderRadius: 10,
        background: C.panel, border: `1px solid ${C.border}`,
      }}>
        <button
          onClick={onSelectAll}
          disabled={!visibleCount}
          title={`Send every ${noun === "file" ? "file" : "finding"} the current filters show to planning — ${visibleCount} item(s)`}
          style={{
            display: "inline-flex", alignItems: "center", gap: 7,
            padding: "9px 18px", borderRadius: 9, border: "none",
            fontSize: 12.5, fontWeight: 700,
            cursor: visibleCount ? "pointer" : "not-allowed",
            background: visibleCount ? C.accent : C.border,
            color: visibleCount ? "#000" : C.textMuted,
          }}
        >
          <span aria-hidden="true" style={{ fontWeight: 900 }}>✓</span>
          Select all
          {visibleCount > 0 && (
            <span style={{ fontFamily: "monospace", opacity: 0.7 }}>({visibleCount})</span>
          )}
        </button>

        <button
          onClick={onRejectAll}
          disabled={!selectedCount}
          title={
            selectedCount
              ? `Clear all ${selectedCount} selected item(s) — nothing is sent to planning`
              : "Nothing is selected"
          }
          style={{
            display: "inline-flex", alignItems: "center", gap: 7,
            padding: "9px 18px", borderRadius: 9,
            fontSize: 12.5, fontWeight: 700,
            cursor: selectedCount ? "pointer" : "not-allowed",
            background: selectedCount ? `${C.danger}1a` : C.bg,
            color: selectedCount ? C.danger : C.textMuted,
            border: `1px solid ${selectedCount ? C.danger : C.border}`,
          }}
        >
          <span aria-hidden="true" style={{ fontWeight: 900 }}>✕</span>
          Reject all
          {selectedCount > 0 && (
            <span style={{ fontFamily: "monospace", opacity: 0.75 }}>({selectedCount})</span>
          )}
        </button>

        <span style={{ fontSize: 11, color: C.textMuted, marginLeft: "auto" }}>
          {selectedCount > 0
            ? `${selectedCount} selected of ${visibleCount} shown`
            : `${visibleCount} ${noun}${visibleCount === 1 ? "" : "s"} shown · nothing selected yet`}
        </span>
      </div>

      {/* ── Ways of looking: search, severity, and the accordion ─────────── */}
      <div style={{
        display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap",
        marginBottom: 14,
      }}>
        <div style={{ flex: "1 1 260px", minWidth: 0 }}>
          <div style={{
            fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 0.9, fontWeight: 700, marginBottom: 7,
          }}>
            Search
          </div>
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={PLACEHOLDER[mode] || PLACEHOLDER.smell}
            aria-label="Search findings"
            style={{
              width: "100%", padding: "9px 13px", borderRadius: 9,
              background: C.panel, border: `1px solid ${C.border}`,
              color: C.text, fontSize: 12.5, outline: "none", boxSizing: "border-box",
            }}
          />
        </div>

        <div>
          <div style={{
            fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 0.9, fontWeight: 700, marginBottom: 7,
          }}>
            Severity
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {["all", "high", "medium", "low"].map((level) => {
              const active = severity === level;
              const tone = SEVERITY_TONE[level];
              return (
                <button
                  key={level}
                  onClick={() => onSeverity(level)}
                  aria-pressed={active}
                  style={{
                    padding: "8px 14px", borderRadius: 8, cursor: "pointer",
                    fontSize: 11.5, fontWeight: 700, textTransform: "capitalize",
                    background: active ? tone : C.panel,
                    color: active ? (level === "medium" || level === "all" ? "#000" : "#fff") : C.textMuted,
                    border: `1px solid ${active ? tone : C.border}`,
                  }}
                >
                  {level}
                </button>
              );
            })}
          </div>
        </div>

        {(onExpandAll || onCollapseAll) && (
          <div>
            <div style={{
              fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
              letterSpacing: 0.9, fontWeight: 700, marginBottom: 7,
            }}>
              Accordion
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={onExpandAll}
                disabled={!groupCount}
                title={
                  groupCount
                    ? `Open every ${noun} and everything nested inside it`
                    : "Nothing to expand"
                }
                style={neutralButton(Boolean(groupCount))}
              >
                Expand all
              </button>
              <button
                onClick={onCollapseAll}
                disabled={!anyOpen}
                title={anyOpen ? "Collapse everything back to the group headings" : "Nothing is expanded"}
                style={neutralButton(anyOpen)}
              >
                Hide all
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

const GROUP_NOUN = { file: "file", smell: "smell type", category: "category" };

const neutralButton = (enabled) => ({
  padding: "8px 14px", borderRadius: 8,
  cursor: enabled ? "pointer" : "not-allowed",
  background: C.panel,
  color: enabled ? C.textSub : C.textMuted,
  border: `1px solid ${C.border}`,
  fontSize: 11.5, fontWeight: 700,
});
