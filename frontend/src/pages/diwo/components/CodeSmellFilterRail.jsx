/**
 * CodeSmellFilterRail.jsx
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The filter row above the Code Smell Review findings list:
 *
 *     SEVERITY       All · High · Medium · Low, each with its count
 *     CATEGORY       CUQA categories, each with its count
 *
 * Smell-type filtering lives in the toolbar's Smell type dropdown.
 *
 * Purely presentational: every count is handed in, already derived from the
 * rows the page holds, and every click calls straight back to the page's own
 * filter state. Filters narrow what is SHOWN; nothing here touches selection.
 */

import { T, tint, eyebrow, panelStyle, sevTone } from "../codeSmellTheme.js";
import { categoryIcon } from "../utils/smellIcons";

// Section labels a step larger than the shared eyebrow, to match the
// Smell Selection section beside this panel.
const eyebrowLg = { ...eyebrow, fontSize: 12 };


const PRIORITY_TONE = { critical: T.high, high: T.high, medium: T.medium, low: T.text3 };

export default function CodeSmellFilterRail({
  severityCounts,
  severity, onSeverity,
  categories, categoryFilter, onCategory,
  filtersActive, onClearFilters,
}) {
  return (
    <aside className="dw-rail" aria-label="Summary and filters">
      <Section title="Severity">
        <div role="group" aria-label="Filter by severity" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {["all", "high", "medium", "low"].map((level) => (
            <FilterRow
              key={level}
              active={severity === level}
              onClick={() => onSeverity(level)}
              dot={level === "all" ? null : sevTone(level)}
              label={level === "all" ? "All severities" : level[0].toUpperCase() + level.slice(1)}
              count={level === "all" ? severityCounts.total : severityCounts[level] || 0}
            />
          ))}
        </div>
      </Section>

      {categories.length > 0 && (
        <Section title="Category" hint="Selecting one switches File wise to Category wise">
          <div role="group" aria-label="Filter by category" style={{ display: "flex", flexDirection: "column", gap: 2, maxHeight: 190, overflowY: "auto" }}>
            <FilterRow
              active={categoryFilter === "all"}
              onClick={() => onCategory("all")}
              label="All categories"
              count={severityCounts.total}
            />
            {categories.map((c) => (
              <FilterRow
                key={c.category}
                active={categoryFilter === c.category}
                onClick={() => onCategory(categoryFilter === c.category ? "all" : c.category)}
                icon={categoryIcon(c.category)}
                label={c.category}
                count={c.count}
                trailing={c.priority ? (
                  <span
                    title={`${c.priority} priority (CUQA taxonomy)`}
                    style={{
                      fontSize: 12, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
                      color: PRIORITY_TONE[c.priority] || T.text3,
                    }}
                  >
                    {c.priority}
                  </span>
                ) : null}
              />
            ))}
          </div>
        </Section>
      )}

      {filtersActive && (
        <button
          type="button"
          onClick={onClearFilters}
          className="dw-focus dw-hover"
          style={{
            ...panelStyle, boxShadow: "none",
            padding: "9px 12px", fontSize: 14, fontWeight: 700, cursor: "pointer",
            color: T.accentText, textAlign: "center",
          }}
        >
          Reset all filters
        </button>
      )}
    </aside>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────────

function Section({ title, hint, children }) {
  return (
    <section style={{ ...panelStyle, padding: 12 }}>
      <div style={{ ...eyebrowLg, marginBottom: 8 }} title={hint}>{title}</div>
      {children}
    </section>
  );
}

function FilterRow({ active, onClick, label, count, dot, icon, trailing }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={active ? "dw-focus" : "dw-focus dw-hover"}
      style={{
        display: "flex", alignItems: "center", gap: 8, width: "100%", minHeight: 30,
        padding: "6px 8px", borderRadius: 7, cursor: "pointer", textAlign: "left",
        background: active ? tint(T.accent, 12) : "transparent",
        border: `1px solid ${active ? tint(T.accent, 40) : "transparent"}`,
        color: active ? T.text : T.text2,
        fontSize: 14, fontWeight: active ? 700 : 500,
      }}
    >
      {dot && <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: dot, flexShrink: 0 }} />}
      {icon && <span aria-hidden="true" style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>}
      <span style={{ flex: 1, minWidth: 0, whiteSpace: "normal", overflowWrap: "anywhere", lineHeight: 1.35 }}>
        {label}
      </span>
      {trailing}
      <span style={{
        fontFamily: T.mono, fontSize: 13, fontWeight: 700, flexShrink: 0,
        padding: "0 7px", borderRadius: 999,
        background: active ? tint(T.accent, 18) : T.sunken,
        color: active ? T.accentText : T.text3,
      }}>
        {count}
      </span>
    </button>
  );
}
