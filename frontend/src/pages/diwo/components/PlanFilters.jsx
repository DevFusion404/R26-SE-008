/**
 * PlanFilters.jsx
 * ===============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * How the plan is arranged and narrowed, shown inside the plan header:
 *
 *     DECISION                     DIWO RECOMMENDATION           METHOD & IMPACT
 *     All 8 · Pending 8 · …        🟢 Recommended 4 · …          [All methods ▾] [Any impact ▾]
 *
 * Filters change only what the step list shows — never a decision. Every
 * count is over the whole plan, so the row says what there is to filter for.
 */

import { T, tint, eyebrow } from "../codeSmellTheme.js";
import { CATEGORY_ORDER, categoryStyle } from "../utils/planningDecisionSupport";

const DECISION_FILTERS = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "manual", label: "Manual" },
];

export default function PlanFilters({
  filter, onFilter, decisionCounts, totalSteps,
  categoryFilter, onCategoryFilter, breakdown,
  methodFilter, onMethodFilter, methods,
  impactFilter, onImpactFilter,
  anyFilterActive, onClearFilters,
}) {
  const recommendationFilters = [
    ...CATEGORY_ORDER.map((category) => ({
      value: category,
      icon: categoryStyle(category).icon,
      label: categoryStyle(category).short,
      count: breakdown.counts[category] || 0,
      tone: categoryStyle(category).color,
    })),
    ...(breakdown.unassessed > 0
      ? [{ value: "unassessed", icon: "○", label: "Not assessed", count: breakdown.unassessed, tone: T.text3 }]
      : []),
  ];

  return (
    <div style={{ display: "flex", gap: "10px 18px", flexWrap: "wrap", alignItems: "flex-end" }}>
      <Group label="Decision">
        {DECISION_FILTERS.map((f) => (
          <FilterChip
            key={f.value}
            active={filter === f.value}
            onClick={() => onFilter(f.value)}
            label={f.label}
            count={f.value === "all" ? totalSteps : decisionCounts[f.value]}
          />
        ))}
      </Group>

      <Group label="DIWO recommendation">
        {recommendationFilters.map((f) => (
          <FilterChip
            key={f.value}
            active={categoryFilter === f.value}
            disabled={!f.count}
            onClick={() => onCategoryFilter(categoryFilter === f.value ? "any" : f.value)}
            label={<><span aria-hidden="true">{f.icon}</span> {f.label}</>}
            count={f.count}
            tone={f.tone}
          />
        ))}
      </Group>

      <Group label="Method & impact">
        <select
          value={methodFilter}
          onChange={(e) => onMethodFilter(e.target.value)}
          aria-label="Filter by refactoring method"
          className="dw-input"
          style={selectStyle}
        >
          <option value="any">All methods ({methods.length})</option>
          {methods.map((m) => (
            <option key={m.key} value={m.key}>{m.label} · {m.findingCount}</option>
          ))}
        </select>
        <select
          value={impactFilter}
          onChange={(e) => onImpactFilter(e.target.value)}
          aria-label="Filter by expected impact"
          className="dw-input"
          style={selectStyle}
        >
          <option value="any">Any impact</option>
          <option value="high">High impact</option>
          <option value="medium">Medium impact</option>
          <option value="low">Low impact</option>
        </select>
        {anyFilterActive && (
          <button
            type="button"
            onClick={onClearFilters}
            className="dw-focus"
            style={{
              background: "none", border: "none", padding: "4px", cursor: "pointer",
              color: T.accentText, fontSize: 12, fontWeight: 700, borderRadius: 4,
            }}
          >
            Clear filters
          </button>
        )}
      </Group>
    </div>
  );
}

function Group({ label, children }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ ...eyebrow, marginBottom: 6 }}>{label}</div>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", alignItems: "center" }}>{children}</div>
    </div>
  );
}

function FilterChip({ active, disabled = false, onClick, label, count, tone }) {
  const color = tone || T.accentText;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className="dw-focus"
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        padding: "4px 9px", borderRadius: 999, fontSize: 12, fontWeight: active ? 700 : 600,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.45 : 1,
        background: active ? tint(tone || T.accent, 14) : T.surface,
        color: active ? color : T.text2,
        border: `1px solid ${active ? tint(tone || T.accent, 50) : T.border}`,
        whiteSpace: "nowrap",
      }}
    >
      {label}
      <span style={{ fontFamily: T.mono, fontSize: 11, color: active ? color : T.text3 }}>{count ?? 0}</span>
    </button>
  );
}

const selectStyle = {
  padding: "6px 9px", borderRadius: 7, fontSize: 12, minWidth: 128,
  background: T.surface, color: T.text, border: `1px solid ${T.border}`, cursor: "pointer",
};
