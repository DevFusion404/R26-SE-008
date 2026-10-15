/**
 * codeSmellUi.jsx
 * ===============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Small presentational primitives shared by the Code Smell Review stage,
 * built on the theme tokens in codeSmellTheme.js so they read correctly in
 * both light and dark mode.
 */

import { T, tint, sevTone } from "./codeSmellTheme.js";

/** HIGH / MEDIUM / LOW — colour plus the word, so it survives colour blindness. */
export function SeverityTag({ severity, compact = false }) {
  const level = (severity || "unknown").toLowerCase();
  const tone = sevTone(level);
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: compact ? "1px 6px" : "2px 8px", borderRadius: 999,
      fontSize: compact ? 9.5 : 10, fontWeight: 800, letterSpacing: 0.5,
      textTransform: "uppercase", whiteSpace: "nowrap",
      color: tone, background: tint(tone, 12), border: `1px solid ${tint(tone, 32)}`,
    }}>
      <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: tone }} />
      {level}
    </span>
  );
}

/** A neutral or toned pill for metadata — category, capability, risk. */
export function Chip({ children, tone, title, mono = false }) {
  const color = tone || T.text2;
  return (
    <span
      title={title}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        padding: "1px 8px", borderRadius: 999, whiteSpace: "nowrap",
        fontSize: 10.5, fontWeight: 600, lineHeight: "18px",
        fontFamily: mono ? T.mono : "inherit",
        color,
        background: tone ? tint(tone, 10) : T.sunken,
        border: `1px solid ${tone ? tint(tone, 30) : T.border}`,
      }}
    >
      {children}
    </span>
  );
}

/** A small indeterminate spinner, for a wait whose length cannot be predicted. */
export function Spinner({ size = 13, color = "currentColor" }) {
  return (
    <span
      aria-hidden="true"
      style={{
        width: size, height: size, borderRadius: "50%", flexShrink: 0,
        border: `2px solid ${color}`, borderTopColor: "transparent",
        display: "inline-block", animation: "dwSpin 0.7s linear infinite",
      }}
    />
  );
}
