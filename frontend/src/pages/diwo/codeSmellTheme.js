/**
 * codeSmellTheme.js
 * =================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Theme-aware tokens and small primitives for the Code Smell Review stage.
 *
 * Every value is a CSS variable defined in codeSmellTheme.css with a dark and
 * a light value, so the same component reads correctly in both themes without
 * subscribing to ThemeContext. Tints are built with color-mix() rather than by
 * appending a hex alpha, because a CSS variable cannot take a hex suffix.
 *
 * Colour carries meaning here and stays scarce: accent = selected / ready,
 * red / amber / green = high / medium / low severity, everything else neutral.
 * Severity is always written out as well, never signalled by colour alone.
 */

import "./codeSmellTheme.css";

export const T = {
  page: "var(--dw-page)",
  surface: "var(--dw-surface)",
  surface2: "var(--dw-surface-2)",
  sunken: "var(--dw-sunken)",
  code: "var(--dw-code-bg)",
  hover: "var(--dw-hover)",
  border: "var(--dw-border)",
  borderStrong: "var(--dw-border-strong)",

  text: "var(--dw-text)",
  text2: "var(--dw-text-2)",
  text3: "var(--dw-text-3)",

  accent: "var(--dw-accent)",
  accentText: "var(--dw-accent-text)",
  onAccent: "var(--dw-on-accent)",

  high: "var(--dw-high)",
  medium: "var(--dw-medium)",
  low: "var(--dw-low)",
  info: "var(--dw-info)",
  violet: "var(--dw-violet)",

  shadowSm: "var(--dw-shadow-sm)",
  shadowMd: "var(--dw-shadow-md)",
  shadowLg: "var(--dw-shadow-lg)",
  overlay: "var(--dw-overlay)",

  mono: "var(--dw-mono)",
};

/** `color` at `pct`% over transparent — works with CSS variables. */
export const tint = (color, pct) => `color-mix(in srgb, ${color} ${pct}%, transparent)`;

export const sevTone = (severity) =>
  ({ high: T.high, medium: T.medium, low: T.low }[(severity || "").toLowerCase()] || T.text3);

export const riskTone = (band) =>
  ({ low: T.low, medium: T.medium, high: T.high }[band] || T.text2);

/** The small uppercase label that heads a panel section. */
export const eyebrow = {
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: 0.9,
  textTransform: "uppercase",
  color: T.text3,
};

export const panelStyle = {
  background: T.surface,
  border: `1px solid ${T.border}`,
  borderRadius: 12,
  boxShadow: T.shadowSm,
};

/** A quiet bordered button. `enabled` drives colour and cursor. */
export const ghostButton = (enabled = true, extra = {}) => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "7px 12px",
  borderRadius: 8,
  fontSize: 12,
  fontWeight: 600,
  background: T.surface,
  color: enabled ? T.text2 : T.text3,
  border: `1px solid ${T.border}`,
  cursor: enabled ? "pointer" : "not-allowed",
  opacity: enabled ? 1 : 0.6,
  whiteSpace: "nowrap",
  ...extra,
});

/** The one filled button style — the action a panel exists for. */
export const primaryButton = (enabled = true, extra = {}) => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  padding: "10px 18px",
  borderRadius: 9,
  border: "none",
  fontSize: 13,
  fontWeight: 700,
  background: enabled ? T.accent : T.sunken,
  color: enabled ? T.onAccent : T.text3,
  cursor: enabled ? "pointer" : "not-allowed",
  boxShadow: enabled ? T.shadowSm : "none",
  ...extra,
});
