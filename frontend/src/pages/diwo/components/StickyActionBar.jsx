/**
 * StickyActionBar.jsx
 * ===================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The footer every decision stage ends on: a status line on the left, the
 * stage's own buttons on the right, pinned to the bottom of the viewport.
 *
 *     ┌──────────────────────────────────────────────────────────────┐
 *     │ 6 findings selected for planning       [← Back] [Continue →] │
 *     └──────────────────────────────────────────────────────────────┘
 *
 * STICKY, because the thing these stages have in common is length. A plan of
 * 134 steps or a transformation across 20 files puts its own commit button a
 * screen and a half below the fold, so the developer decides, scrolls, and
 * decides again with no way to act on what they just concluded. The bar
 * follows instead.
 *
 * ONE COMPONENT, not three copies. Stage 1 had this shell inline, and Stages 2
 * and 3 each ended on a plain right-aligned row that scrolled away — three
 * footers doing the same job in three different shapes. Sharing the chrome is
 * what keeps them from drifting again; the contents stay per-stage, because
 * what each stage is committing to is genuinely different.
 *
 * `active` drives the accent border: teal once the stage can be submitted,
 * neutral while it cannot. The border is the only thing carrying that state
 * here, so the bar reads as "ready" or "not yet" before the text is read.
 */

import { C } from "../diwoTheme.jsx";

export default function StickyActionBar({ active = false, status, children }) {
  return (
    <div
      style={{
        position: "sticky",
        bottom: 0,
        marginTop: 16,
        zIndex: 20,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 14,
        flexWrap: "wrap",
        padding: "13px 18px",
        borderRadius: 12,
        background: C.panel,
        border: `1px solid ${active ? C.accent : C.border}`,
        boxShadow: "0 -6px 24px rgba(4,6,10,0.45)",
      }}
    >
      <div style={{ fontSize: 12.5, color: active ? C.text : C.textMuted, minWidth: 0 }}>
        {status}
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {children}
      </div>
    </div>
  );
}
