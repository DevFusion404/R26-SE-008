/**
 * PlanSubmitDialog.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * "Confirm plan approval" — the last look before the approved steps leave
 * DIWO for the Safe Code Transformation & Validation Agent.
 *
 * Presentational. Confirm calls the page's existing submit handler, which
 * hands the decisions to the parent exactly as before; this dialog sends
 * nothing itself and shows no success state of its own — the workflow moving
 * to the Transformation stage is the confirmation.
 */

import { useEffect, useRef } from "react";
import { T, tint, eyebrow, ghostButton, primaryButton } from "../codeSmellTheme.js";

export default function PlanSubmitDialog({
  totalSteps, counts, forcedManual, overrides, notes, onNotes, onConfirm, onCancel,
}) {
  const confirmRef = useRef(null);
  // The parent passes a fresh onCancel on every render — including each
  // keystroke in the notes box — so read it through a ref. Depending on it
  // directly re-ran the focus call and pulled focus out of the textarea.
  const cancelRef = useRef(onCancel);
  useEffect(() => { cancelRef.current = onCancel; }, [onCancel]);

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") cancelRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const willSend = counts.approved;

  return (
    <div
      onClick={onCancel}
      style={{
        position: "fixed", inset: 0, zIndex: 1150, background: T.overlay,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="pa-submit-title"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 100%)", maxHeight: "90vh", overflowY: "auto",
          background: T.surface, border: `1px solid ${T.border}`, borderRadius: 14, boxShadow: T.shadowLg,
        }}
      >
        <div style={{ padding: "18px 20px 12px" }}>
          <div style={eyebrow}>Developer approval</div>
          <h2 id="pa-submit-title" style={{ margin: "4px 0 0", fontSize: 17, fontWeight: 800, color: T.text }}>
            Confirm plan approval
          </h2>
          <p style={{ margin: "6px 0 0", fontSize: 12.5, color: T.text2, lineHeight: 1.6 }}>
            Only the steps you approved are sent to SCTVA. Rejected and manual steps stay in the
            plan record and are not transformed.
          </p>
        </div>

        <div style={{ padding: "0 20px 14px" }}>
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
            <Tile label="Total plan steps" value={totalSteps} />
            <Tile label="Sent to SCTVA" value={willSend} tone={T.accentText} highlight />
            {forcedManual > 0 && <Tile label="Approved, no automatic form" value={forcedManual} tone={T.medium} />}
            <Tile label="Rejected" value={counts.rejected} />
            <Tile label="Manual refactoring" value={counts.manual} />
            <Tile label="Still pending" value={counts.pending} tone={counts.pending ? T.medium : undefined} />
          </div>

          {forcedManual > 0 && (
            <Warn>
              {forcedManual} approved step{forcedManual === 1 ? " has" : "s have"} no safe automatic form in SCTVA
              and may change no code.
            </Warn>
          )}
          {overrides > 0 && (
            <Warn tone={T.info}>
              You went against DIWO on {overrides} step{overrides === 1 ? "" : "s"}. That is recorded with the
              plan — DIWO advises, you decide.
            </Warn>
          )}

          <label style={{ display: "block", marginTop: 14 }}>
            <span style={{ ...eyebrow, display: "block", marginBottom: 6 }}>Notes for the transformation agent (optional)</span>
            <textarea
              value={notes}
              onChange={(e) => onNotes(e.target.value)}
              placeholder="Context, concerns or guidance for the transformation…"
              className="dw-input"
              style={{
                width: "100%", minHeight: 70, resize: "vertical", boxSizing: "border-box",
                padding: "9px 12px", borderRadius: 8, fontSize: 12.5, lineHeight: 1.5,
                background: T.sunken, color: T.text, border: `1px solid ${T.border}`, fontFamily: "inherit",
              }}
            />
          </label>
        </div>

        <div style={{
          display: "flex", justifyContent: "flex-end", gap: 10, flexWrap: "wrap",
          padding: "12px 20px", borderTop: `1px solid ${T.border}`, background: T.surface2,
        }}>
          <button type="button" onClick={onCancel} className="dw-focus dw-hover" style={ghostButton(true, { padding: "9px 16px", fontSize: 12.5 })}>
            Keep reviewing
          </button>
          <button ref={confirmRef} type="button" onClick={onConfirm} className="dw-focus" style={primaryButton(true)}>
            Send {willSend} step{willSend === 1 ? "" : "s"} to SCTVA →
          </button>
        </div>
      </div>
    </div>
  );
}

function Tile({ label, value, tone, highlight = false }) {
  return (
    <div style={{
      padding: "9px 11px", borderRadius: 9,
      background: highlight ? tint(T.accent, 8) : T.sunken,
      border: `1px solid ${highlight ? tint(T.accent, 40) : T.border}`,
    }}>
      <div style={{ fontSize: 18, fontWeight: 800, fontFamily: T.mono, color: tone || T.text }}>{value}</div>
      <div style={{ fontSize: 11, color: T.text3, marginTop: 1 }}>{label}</div>
    </div>
  );
}

function Warn({ tone = T.medium, children }) {
  return (
    <div style={{
      marginTop: 10, padding: "8px 11px", borderRadius: 8, fontSize: 12, lineHeight: 1.5, color: T.text,
      background: tint(tone, 9), border: `1px solid ${tint(tone, 30)}`,
    }}>
      {children}
    </div>
  );
}
