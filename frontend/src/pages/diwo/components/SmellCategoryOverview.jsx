/**
 * SmellCategoryOverview.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *     🗂  CODE SMELL CATEGORY OVERVIEW              5 categories · 9 types
 *
 *     [ All 41 ]  [ Bloaters 18 · 4 types ]  [ Change Preventers 9 · 2 ]
 *     [ Couplers 7 · 2 ]  [ Dispensables 5 · 1 ]  [ Security 2 · 1 ]
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
 *
 * Every chip is also a filter. Clicking one narrows the list to that category;
 * clicking it again clears it, so the bar needs no separate reset.
 */

import { C } from "../diwoTheme.jsx";

/**
 * Category priority is CUQA's architectural risk class for the group, and it
 * is NOT the same axis as a smell's severity — a category can be `critical`
 * while holding only low-severity findings. Kept in the theme's palette so it
 * reads as a related-but-different signal rather than a competing one.
 */
const PRIORITY_COLOR = {
  critical: C.danger,
  medium: C.warn,
  low: C.low,
};

const ALL = "all";

export default function SmellCategoryOverview({
  taxonomy,
  active = ALL,
  onSelect,
  loading = false,
}) {
  // No taxonomy means the endpoint is unavailable or the workflow has no
  // smells. Either way there is nothing truthful to show, so the panel simply
  // does not appear — Stage 1 behaves exactly as it did before it existed.
  if (!taxonomy || !(taxonomy.categories || []).length) {
    return null;
  }

  const categories = taxonomy.categories || [];

  return (
    <div style={{
      marginBottom: 16, padding: "14px 16px", borderRadius: 10,
      background: C.panel, border: `1px solid ${C.border}`,
    }}>
      <div style={{
        display: "flex", alignItems: "baseline", gap: 10,
        flexWrap: "wrap", marginBottom: 11,
      }}>
        <span style={{
          fontSize: 11, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 1, color: C.textMuted,
        }}>
          <span aria-hidden="true">🗂</span> Code Smell Category Overview
        </span>
        <span style={{ fontSize: 11, color: C.textMuted }}>
          {taxonomy.category_count} categor{taxonomy.category_count === 1 ? "y" : "ies"} ·{" "}
          {taxonomy.type_count} distinct smell type{taxonomy.type_count === 1 ? "" : "s"} ·{" "}
          {taxonomy.total_smells} finding{taxonomy.total_smells === 1 ? "" : "s"}
        </span>
        {loading && (
          <span style={{ fontSize: 10.5, color: C.textMuted, fontStyle: "italic" }}>
            refreshing…
          </span>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip
          label="All categories"
          count={taxonomy.total_smells}
          detail={`${taxonomy.type_count} type${taxonomy.type_count === 1 ? "" : "s"}`}
          color={C.textSub}
          selected={active === ALL}
          onClick={() => onSelect?.(ALL)}
        />

        {categories.map((category) => (
          <Chip
            key={category.category}
            label={category.category}
            count={category.count}
            detail={`${category.type_count} type${category.type_count === 1 ? "" : "s"}`}
            priority={category.priority}
            color={PRIORITY_COLOR[category.priority] || C.textSub}
            selected={active === category.category}
            onClick={() => onSelect?.(active === category.category ? ALL : category.category)}
            title={
              `${category.category} — ${category.priority} priority\n` +
              `${category.count} finding(s) across ${category.file_count} file(s)\n` +
              category.types.map((t) => `  ${t.type} ×${t.count}`).join("\n")
            }
          />
        ))}
      </div>
    </div>
  );
}

function Chip({ label, count, detail, priority, color, selected, onClick, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={selected}
      style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "6px 12px", borderRadius: 20, cursor: "pointer",
        // Teal marks the ACTIVE chip and nothing else. Giving each category its
        // own strong colour turned a triage bar into a rainbow, and made the
        // one thing worth spotting — which filter is on — the hardest to see.
        background: selected ? `${C.accent}1e` : C.bg,
        border: `1px solid ${selected ? C.accent : C.border}`,
        color: selected ? C.accent : C.textSub,
        fontSize: 11.5, fontWeight: selected ? 800 : 600,
        transition: "all 0.15s",
      }}
    >
      {/* Priority is shown as a dot AND named in the tooltip — never colour
          alone, since these chips are the page's primary triage signal. */}
      {priority && (
        <span
          aria-hidden="true"
          style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }}
        />
      )}
      <span>{label}</span>
      <span style={{
        fontFamily: "monospace", fontWeight: 800,
        color: selected ? C.accent : color,
      }}>
        {count}
      </span>
      {detail && (
        <span style={{ fontSize: 10, color: C.textMuted, fontWeight: 500 }}>{detail}</span>
      )}
    </button>
  );
}
