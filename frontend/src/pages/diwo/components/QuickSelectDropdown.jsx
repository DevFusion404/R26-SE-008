/**
 * QuickSelectDropdown.jsx
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 *     [ Smell Type · 2 selected  ▾ ]
 *     ┌────────────────────────────────────┐
 *     │ Search smell types…                │
 *     │ ☑ Magic Number                     │
 *     │    53 findings · 5 files · 53/53   │
 *     │ ◐ Long Method                      │
 *     │    3 findings · 2 files · 1/3      │
 *     │ ☐ Duplicate Code                   │
 *     │    2 findings · 2 files            │
 *     ├────────────────────────────────────┤
 *     │ Clear these selections             │
 *     └────────────────────────────────────┘
 *
 * Two affordances in one row, deliberately separated:
 *
 *   the CHECKBOX selects every occurrence behind the option
 *   the LABEL navigates to it — switches mode and focuses that group
 *
 * They are different intents. A developer who clicks "Magic Number" to go and
 * look at its 53 occurrences has not asked to select all 53, and a control that
 * conflates the two makes the safe action (look) as expensive as the
 * irreversible-feeling one (select everything).
 *
 * No selection store of its own. Every toggle calls back with the option's
 * rows, and the page applies them to the one `selectedIds` set the tables use —
 * which is why unticking a single occurrence in the table turns this dropdown
 * partial on the same frame, with no refetch.
 *
 * A plain button + popover rather than a native <select>, because a native one
 * cannot carry a tri-state checkbox, per-option counts, or a nested level.
 */

import { useEffect, useRef, useState } from "react";
import { C } from "../diwoTheme.jsx";

/** ☐ / ◐ / ☑ — the state is in the glyph, not only in the colour. */
export function TriCheckbox({ state, size = 15 }) {
  const on = state === "all";
  const half = state === "partial";
  return (
    <span
      aria-hidden="true"
      style={{
        width: size, height: size, borderRadius: 4, flexShrink: 0,
        border: `1.5px solid ${on || half ? C.accent : C.borderAcc}`,
        background: on ? C.accent : "transparent",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        transition: "all 0.15s",
      }}
    >
      {on && <span style={{ color: "#000", fontSize: size * 0.62, fontWeight: 900, lineHeight: 1 }}>✓</span>}
      {half && <span style={{ width: size * 0.5, height: 2, background: C.accent, borderRadius: 1 }} />}
    </span>
  );
}

const stateOf = (selection) =>
  selection?.all ? "all" : selection?.partial ? "partial" : "none";

const SEVERITY_COLOR = { high: C.danger, medium: C.warn, low: C.textMuted };

export default function QuickSelectDropdown({
  label,
  options = [],
  onToggleOption,
  onNavigateOption,
  onClear,
  searchable = true,
  searchPlaceholder = "Search…",
  emptyLabel = "Nothing to show",
  expandable = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());
  const rootRef = useRef(null);

  // Escape and outside-click both close. Bound only while open, so a page with
  // two of these does not carry four idle listeners.
  useEffect(() => {
    if (!open) return undefined;

    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onPointer = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const term = query.trim().toLowerCase();
  const visible = term
    ? options.filter((o) =>
        o.label.toLowerCase().includes(term)
        || (o.children || []).some((c) => c.label.toLowerCase().includes(term)))
    : options;

  const totalSelected = options.reduce((n, o) => n + (o.selection?.selected || 0), 0);
  const activeCount = options.filter((o) => o.selection?.selected > 0).length;

  const toggleExpanded = (key) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div ref={rootRef} style={{ position: "relative", minWidth: 210, flex: "1 1 210px" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "9px 12px", borderRadius: 9, cursor: "pointer",
          background: open ? `${C.accent}12` : C.panel,
          border: `1px solid ${open || activeCount ? C.accent : C.border}`,
          color: C.text, fontSize: 12.5, fontWeight: 600, textAlign: "left",
        }}
      >
        <span style={{ color: C.textMuted, fontSize: 11 }}>{label}</span>
        <span style={{ flex: 1, minWidth: 0, color: activeCount ? C.accent : C.textSub }}>
          {activeCount
            ? `${activeCount} selected · ${totalSelected} finding${totalSelected === 1 ? "" : "s"}`
            : `All ${label.toLowerCase()}s`}
        </span>
        <span aria-hidden="true" style={{ color: C.textMuted, fontSize: 10 }}>
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          style={{
            position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0, zIndex: 40,
            background: C.bg, border: `1px solid ${C.borderAcc}`, borderRadius: 10,
            boxShadow: "0 18px 40px rgba(0,0,0,0.45)", overflow: "hidden",
            minWidth: 300,
          }}
        >
          {searchable && (
            <div style={{ padding: 8, borderBottom: `1px solid ${C.border}` }}>
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                style={{
                  width: "100%", padding: "7px 10px", borderRadius: 7,
                  background: C.panel, border: `1px solid ${C.border}`,
                  color: C.text, fontSize: 12, outline: "none", boxSizing: "border-box",
                }}
              />
            </div>
          )}

          <div style={{ maxHeight: 320, overflowY: "auto" }}>
            {visible.length === 0 && (
              <div style={{ padding: "18px 14px", fontSize: 12, color: C.textMuted, textAlign: "center" }}>
                {emptyLabel}
              </div>
            )}

            {visible.map((option) => (
              <div key={option.key}>
                <Option
                  option={option}
                  onToggle={() => onToggleOption?.(option)}
                  onNavigate={onNavigateOption ? () => { onNavigateOption(option); setOpen(false); } : undefined}
                  expandable={expandable && (option.children || []).length > 0}
                  expanded={expanded.has(option.key)}
                  onExpand={() => toggleExpanded(option.key)}
                />

                {expandable && expanded.has(option.key) && (option.children || []).map((child) => (
                  <Option
                    key={child.key}
                    option={child}
                    nested
                    onToggle={() => onToggleOption?.(child)}
                    onNavigate={onNavigateOption ? () => { onNavigateOption(child); setOpen(false); } : undefined}
                  />
                ))}
              </div>
            ))}
          </div>

          {onClear && (
            <button
              type="button"
              onClick={() => onClear()}
              disabled={totalSelected === 0}
              style={{
                width: "100%", padding: "9px 12px", border: "none",
                borderTop: `1px solid ${C.border}`, background: C.panel,
                color: totalSelected ? C.textSub : C.textMuted,
                fontSize: 11.5, fontWeight: 600,
                cursor: totalSelected ? "pointer" : "not-allowed", textAlign: "left",
              }}
            >
              Clear these selections
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** One option row: tri-state box, then a label that navigates instead. */
function Option({ option, onToggle, onNavigate, nested = false, expandable = false, expanded = false, onExpand }) {
  const state = stateOf(option.selection);
  const selected = option.selection?.selected || 0;

  const meta = [
    `${option.findingCount} finding${option.findingCount === 1 ? "" : "s"}`,
    typeof option.fileCount === "number"
      ? `${option.fileCount} file${option.fileCount === 1 ? "" : "s"}`
      : null,
    typeof option.typeCount === "number"
      ? `${option.typeCount} type${option.typeCount === 1 ? "" : "s"}`
      : null,
    selected ? `${selected}/${option.findingCount} selected` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 9,
      padding: nested ? "7px 12px 7px 34px" : "8px 12px",
      borderTop: nested ? "none" : `1px solid ${C.border}`,
      background: state === "none" ? "transparent" : `${C.accent}0a`,
    }}>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={state === "all"}
        aria-label={`Select all ${option.findingCount} ${option.label} findings`}
        title={`Select every ${option.label} occurrence`}
        style={{
          background: "none", border: "none", padding: "2px 0 0", cursor: "pointer",
          display: "flex", flexShrink: 0,
        }}
      >
        <TriCheckbox state={state} />
      </button>

      <button
        type="button"
        onClick={onNavigate || onToggle}
        title={onNavigate ? `Go to ${option.label}` : undefined}
        style={{
          flex: 1, minWidth: 0, background: "none", border: "none", padding: 0,
          textAlign: "left", cursor: "pointer", color: "inherit",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          {option.worstSeverity && option.worstSeverity !== "unknown" && (
            <span aria-hidden="true" style={{
              width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
              background: SEVERITY_COLOR[option.worstSeverity] || C.textMuted,
            }} />
          )}
          <span style={{
            fontSize: 12.5, fontWeight: 600, color: C.text,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            {option.label}
          </span>
        </div>
        <div style={{ fontSize: 10.5, color: C.textMuted, marginTop: 2 }}>{meta}</div>
      </button>

      {expandable && (
        <button
          type="button"
          onClick={onExpand}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${option.label}`}
          style={{
            background: "none", border: "none", cursor: "pointer", flexShrink: 0,
            color: C.textMuted, fontSize: 10, padding: "2px 4px",
          }}
        >
          {expanded ? "▾" : "▸"}
        </button>
      )}
    </div>
  );
}
