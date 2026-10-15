/**
 * CodeSmellSelectionPanel.jsx
 * ===========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The right rail of the Code Smell Review workspace. It answers one question:
 * "what am I about to send to the Refactoring Planning Agent?"
 *
 *     SELECTION FOR RDP                         Smell wise
 *     12 findings  of 57
 *     ██████░░░░░░░░░░░░░░░░
 *     Files 4 · High 2 · Medium 6 · Low 4
 *     Auto-fixable 9 · Advisory 3 · Effort ~1h 10m
 *     [ Review & send to RDP (12) ]
 *     ▸ Quality & optimiser
 *
 * The selection UNIT follows the mode, and the labels say so: File wise counts
 * files (with the findings they carry underneath), the other two count
 * findings. Capability, effort and the trade-off section only appear when the
 * backend supplied impact records.
 *
 * This holds the stage's one dominant action. On narrow screens the panel
 * drops below the list, so MobileActionBar repeats the action in a sticky
 * footer — CSS shows exactly one of the two at any width.
 */

import { useState } from "react";
import { T, tint, eyebrow, panelStyle, primaryButton, sevTone } from "../codeSmellTheme.js";
import { Spinner } from "../codeSmellUi.jsx";
import { formatEffort } from "../utils/smellGrouping";
import TradeOffPanel from "./TradeOffPanel.jsx";

const MODE_LABEL = { file: "File wise", smell: "Smell wise", category: "Category wise" };

export default function CodeSmellSelectionPanel({
  mode,
  selectionCount,
  findingCount,
  totalFindings,
  totalFiles,
  summary,
  impactSummary,
  interactionNotes,
  optimising,
  onOptimise,
  budgetMinutes,
  onBudgetChange,
  optimised,
  isSubmitting,
  submitPhase,
  onReview,
  onClear,
}) {
  const [showTradeOff, setShowTradeOff] = useState(true);
  const isFile = mode === "file";
  const total = isFile ? totalFiles : totalFindings;
  const pct = total ? Math.round((selectionCount / total) * 100) : 0;
  const nothing = selectionCount === 0;

  return (
    <aside
      aria-label="Selection for the Refactoring Planning Agent"
      style={{
        ...panelStyle,
        border: `1px solid ${nothing ? T.border : tint(T.accent, 55)}`,
        maxHeight: "calc(100vh - 120px)", overflowY: "auto",
        display: "flex", flexDirection: "column",
      }}
    >
      <div style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span style={eyebrow}>Selection for RDP</span>
          <span style={{
            fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
            background: T.sunken, border: `1px solid ${T.border}`, color: T.text2,
          }}>
            {MODE_LABEL[mode]}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 10 }} aria-live="polite">
          <span style={{
            fontSize: 30, fontWeight: 800, fontFamily: T.mono, lineHeight: 1,
            color: nothing ? T.text3 : T.accentText,
          }}>
            {selectionCount}
          </span>
          <span style={{ fontSize: 13, color: T.text2 }}>
            {isFile ? "file" : "finding"}{selectionCount === 1 ? "" : "s"} selected
            <span style={{ color: T.text3 }}> of {total}</span>
          </span>
        </div>
        {isFile && !nothing && (
          <div style={{ fontSize: 11.5, color: T.text2, marginTop: 4 }}>
            covering <b style={{ fontFamily: T.mono, color: T.text }}>{findingCount}</b> finding
            {findingCount === 1 ? "" : "s"} — every smell in a selected file is sent
          </div>
        )}

        <div
          role="progressbar"
          aria-label={`Selected ${isFile ? "files" : "findings"}`}
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          style={{
            height: 6, borderRadius: 4, marginTop: 12, overflow: "hidden",
            background: T.sunken, border: `1px solid ${T.border}`,
          }}
        >
          <div style={{ width: `${pct}%`, height: "100%", background: T.accent, transition: "width 0.2s" }} />
        </div>

        {nothing ? (
          <p style={{ fontSize: 12, color: T.text2, lineHeight: 1.6, margin: "12px 0 0" }}>
            {isFile
              ? "Tick the files whose findings should be planned. Only selected files are forwarded."
              : "Tick findings, or whole smell types and categories. Only selected findings are forwarded."}
          </p>
        ) : (
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 12,
          }}>
            <Stat label="Affected files" value={summary.fileCount} />
            <Stat label="High severity" value={summary.high} dot={sevTone("high")} />
            <Stat label="Medium" value={summary.medium} dot={sevTone("medium")} />
            <Stat label="Low" value={summary.low} dot={sevTone("low")} />
            {summary.autoFixable !== null && summary.autoFixable !== undefined && (
              <>
                <Stat label="Auto-fixable" value={summary.autoFixable} tone={T.accentText} />
                <Stat label="Advisory" value={summary.advisory} tone={summary.advisory ? T.medium : undefined} />
              </>
            )}
            {typeof summary.effortMinutes === "number" && (
              <Stat label="Review effort" value={formatEffort(summary.effortMinutes)} wide />
            )}
          </div>
        )}

        {optimised && !nothing && (
          <div style={{
            marginTop: 10, padding: "7px 10px", borderRadius: 8, fontSize: 11.5, lineHeight: 1.5,
            background: tint(T.violet, 9), border: `1px solid ${tint(T.violet, 30)}`, color: T.text,
          }}>
            <b style={{ color: T.violet }}>DIWO optimised selection</b> — {optimised.label}, {optimised.count}{" "}
            finding{optimised.count === 1 ? "" : "s"} proposed. Edits you make since are kept.
          </div>
        )}

        <ReviewButton
          selectionCount={selectionCount}
          isFile={isFile}
          isSubmitting={isSubmitting}
          submitPhase={submitPhase}
          onReview={onReview}
          style={{ width: "100%", marginTop: 14 }}
        />

        {!nothing && !isSubmitting && (
          <button
            type="button"
            onClick={onClear}
            className="dw-focus"
            style={{
              width: "100%", marginTop: 6, padding: "6px", border: "none", background: "none",
              color: T.text3, fontSize: 11.5, fontWeight: 600, cursor: "pointer", borderRadius: 6,
            }}
          >
            Clear selection
          </button>
        )}
      </div>

      {impactSummary && (
        <div style={{ borderTop: `1px solid ${T.border}` }}>
          <button
            type="button"
            onClick={() => setShowTradeOff((v) => !v)}
            aria-expanded={showTradeOff}
            className="dw-focus dw-hover"
            style={{
              width: "100%", display: "flex", alignItems: "center", gap: 8,
              padding: "11px 16px", border: "none", background: "transparent",
              cursor: "pointer", color: T.text, fontSize: 12.5, fontWeight: 700, textAlign: "left",
            }}
          >
            <span aria-hidden="true" style={{
              fontSize: 9, color: T.text3, display: "inline-block",
              transform: showTradeOff ? "rotate(90deg)" : "none", transition: "transform 0.15s",
            }}>
              ▶
            </span>
            Impact & optimiser
            <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 600, color: T.text3 }}>
              {Math.round((impactSummary.capture_rate || 0) * 100)}% captured
            </span>
          </button>
          {showTradeOff && (
            <div style={{ padding: "0 16px 16px" }}>
              <TradeOffPanel
                embedded
                summary={impactSummary}
                interactionNotes={interactionNotes}
                optimising={optimising}
                onOptimise={onOptimise}
                budgetMinutes={budgetMinutes}
                onBudgetChange={onBudgetChange}
              />
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

/** The stage's one submit action. Opens the confirmation; never submits directly. */
export function ReviewButton({ selectionCount, isFile, isSubmitting, submitPhase, onReview, style }) {
  const ready = selectionCount > 0 && !isSubmitting;
  return (
    <button
      type="button"
      onClick={onReview}
      disabled={!ready}
      aria-busy={isSubmitting}
      className="dw-focus"
      title={isSubmitting ? "Working — the Refactoring Planning Agent is generating the plan" : undefined}
      style={primaryButton(ready || isSubmitting, {
        cursor: isSubmitting ? "wait" : ready ? "pointer" : "not-allowed",
        opacity: isSubmitting ? 0.85 : 1,
        ...style,
      })}
    >
      {isSubmitting && <Spinner />}
      {isSubmitting
        ? submitPhase === "planning" ? "Planning refactorings…" : "Filtering selection…"
        : selectionCount > 0
          ? `Review & send to RDP (${selectionCount} ${isFile ? "file" : "finding"}${selectionCount === 1 ? "" : "s"})`
          : `Select ${isFile ? "files" : "findings"} to continue`}
    </button>
  );
}

/** Sticky footer for the single-column layout, hidden by CSS once the rail is beside the list. */
export function MobileActionBar({ selectionCount, findingCount, isFile, isSubmitting, submitPhase, onReview }) {
  return (
    <div className="dw-mobile-cta">
      <div style={{
        ...panelStyle,
        boxShadow: T.shadowMd,
        border: `1px solid ${selectionCount ? tint(T.accent, 55) : T.border}`,
        padding: "11px 14px",
        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
      }}>
        <span style={{ fontSize: 12.5, color: selectionCount ? T.text : T.text3, flex: "1 1 160px" }}>
          {selectionCount > 0 ? (
            <>
              <b style={{ fontFamily: T.mono, color: T.accentText }}>{selectionCount}</b>{" "}
              {isFile ? `file${selectionCount === 1 ? "" : "s"} · ${findingCount} findings` : `finding${selectionCount === 1 ? "" : "s"}`} selected
            </>
          ) : "Nothing selected yet"}
        </span>
        <ReviewButton
          selectionCount={selectionCount}
          isFile={isFile}
          isSubmitting={isSubmitting}
          submitPhase={submitPhase}
          onReview={onReview}
        />
      </div>
    </div>
  );
}

function Stat({ label, value, tone, dot, wide = false }) {
  return (
    <div style={{
      gridColumn: wide ? "1 / -1" : undefined,
      padding: "7px 9px", borderRadius: 8, background: T.sunken, border: `1px solid ${T.border}`,
      display: "flex", alignItems: "center", gap: 6, minWidth: 0,
    }}>
      {dot && <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: "50%", background: dot, flexShrink: 0 }} />}
      <span style={{ fontSize: 11, color: T.text3, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
      <b style={{ fontSize: 13, fontFamily: T.mono, color: tone || T.text }}>{value}</b>
    </div>
  );
}
