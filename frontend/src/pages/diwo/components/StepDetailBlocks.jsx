/**
 * StepDetailBlocks.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The middle column of the Refactoring Plan Approval workspace — what the
 * open step is and what it changes. Five blocks, each fact once:
 *
 *     1  Step header        step n of m · verdict · status · ◀ ▶ · method ·
 *                           file / target / fixes chips · one advice line
 *     2  What changes       problem now → DIWO applies → result, code preview
 *     3  Code quality change diverging bars for the metrics RDP predicted
 *     4  Concerns           only when the backend raised any
 *     5  Technical details  collapsed; the only place raw ids and keys appear
 *
 * The score breakdown, risk, impact, strategy and history live in the right
 * Decision Support column and are not repeated here; the verdict pill links
 * to it. Renders, never computes — see utils/stepDetail.js.
 */

import { useEffect, useId, useRef, useState } from "react";
import StrokeIcon from "./StrokeIcon.jsx";
import { fetchFileSource } from "../services/diwoApi";
import { buildDiffRows } from "../utils/diffMapper";
import { APPROVE, MANUAL, REJECT } from "../utils/planningDecisionSupport";
import {
  adviceOf, changeOf, currentSide, fmt, lineCount, lineOf, lineRangeOf, previewOf, previewSide,
  problemOf, qualityRows, rankConcerns, targetNameOf, technicalRows, tokenize, verdictOf,
} from "../utils/stepDetail";

export default function StepDetailBlocks({
  step, support, decision, position, total, onPrev, onNext, onNextPending,
  detailsOpen, onToggleDetails, onShowDecisionSupport,
}) {
  const concerns = rankConcerns(support?.warnings);
  return (
    <div className="sd-col">
      <StepHeader
        step={step}
        support={support}
        decision={decision}
        position={position}
        total={total}
        onPrev={onPrev}
        onNext={onNext}
        onNextPending={onNextPending}
        noConcerns={Boolean(support) && concerns.length === 0}
        onShowDecisionSupport={onShowDecisionSupport}
      />
      <WhatChanges step={step} support={support} />
      <QualityChange step={step} />
      {concerns.length > 0 && <Concerns concerns={concerns} />}
      <TechnicalDetails step={step} support={support} open={detailsOpen} onToggle={onToggleDetails} />
    </div>
  );
}

// ─── 1. Step header ──────────────────────────────────────────────────────────

const STATUS = {
  [APPROVE]: { label: "Approved", icon: "check", tone: "teal" },
  [REJECT]: { label: "Rejected", icon: "x", tone: "red" },
  [MANUAL]: { label: "Manual", icon: "wrench", tone: "blue" },
  pending: { label: "Pending", icon: "clock", tone: "amber" },
};

const VERDICT_TONE = { good: "teal", review: "amber", bad: "red", manual: "blue" };
const SEVERITY_TONE = { high: "red", critical: "red", medium: "amber", low: "blue" };

function StepHeader({
  step, support, decision, position, total, onPrev, onNext, onNextPending, noConcerns, onShowDecisionSupport,
}) {
  const verdict = verdictOf(support);
  const status = STATUS[decision] || STATUS.pending;
  const file = step.target?.file || null;
  const slash = file ? file.lastIndexOf("/") : -1;
  const line = lineOf(step);
  const target = targetNameOf(step);
  const severity = String(step.severity || "").toLowerCase();
  const advice = adviceOf(step, support);

  return (
    <section className="sd-card" aria-label={`Step ${position} of ${total}`}>
      <div className="sd-row" style={{ justifyContent: "space-between" }}>
        <div className="sd-row">
          <span className="sd-eyebrow">Step {position} of {total}</span>
          {verdict && (
            <button
              type="button"
              className={`sd-pill sd-pill--${VERDICT_TONE[verdict.key]} sd-pill-btn sd-focus`}
              onClick={onShowDecisionSupport}
              title="Show the Decision Support breakdown"
            >
              <span className="sd-dot" aria-hidden="true" />
              {verdict.label}
              {typeof support?.score === "number" && <span className="sd-mono"> · {support.score}/100</span>}
            </button>
          )}
          <span className={`sd-pill sd-pill--${status.tone}`}>
            <StrokeIcon name={status.icon} size={13} strokeWidth={2.5} />
            {status.label}
          </span>
          {decision && onNextPending && (
            <button type="button" className="sd-link sd-focus" onClick={onNextPending}>
              Next pending step <StrokeIcon name="arrowRight" size={14} />
            </button>
          )}
        </div>
        <div className="sd-row" style={{ gap: 6 }}>
          <button type="button" className="sd-nav-btn sd-focus" aria-label="Previous step" onClick={onPrev} disabled={!onPrev}>
            <StrokeIcon name="chevronLeft" size={18} />
          </button>
          <span className="sd-mono sd-muted" style={{ fontSize: 14, minWidth: 44, textAlign: "center" }}>{position} / {total}</span>
          <button type="button" className="sd-nav-btn sd-focus" aria-label="Next step" onClick={onNext} disabled={!onNext}>
            <StrokeIcon name="chevronRight" size={18} />
          </button>
        </div>
      </div>

      <h2 className="sd-title">{step.refactoring}</h2>

      <div className="sd-row" style={{ gap: 8 }}>
        <span className="sd-chip">
          <StrokeIcon name="file" size={15} style={{ color: "var(--sd-dim)" }} />
          <span className="sd-mono">
            {file ? (
              <>
                {slash >= 0 && <span className="sd-dim">{file.slice(0, slash + 1)}</span>}
                <span className="sd-bright">{file.slice(slash + 1)}</span>
                {line && <span className="sd-dim">:{line}</span>}
              </>
            ) : "(module level)"}
          </span>
        </span>
        {target && (
          <span className="sd-chip">
            <span className="sd-dim">Target</span>
            <span className="sd-mono sd-bright">{target}</span>
          </span>
        )}
        {step.smell_type && (
          <span className="sd-chip">
            <span className="sd-dim">Fixes</span>
            <span className="sd-bright">{step.smell_type}</span>
            {severity && (
              <span className="sd-row" style={{ gap: 5 }}>
                <span className={`sd-sev sd-sev--${SEVERITY_TONE[severity] || "blue"}`} aria-hidden="true" />
                <span className="sd-muted" style={{ fontSize: 13 }}>{severity}</span>
              </span>
            )}
          </span>
        )}
      </div>

      <div className={`sd-advice sd-advice--${advice.tone}`} role="note">
        <StrokeIcon
          name={advice.tone === "good" ? "checkCircle" : "info"}
          size={18}
          style={{ color: advice.tone === "good" ? "var(--sd-teal-2)" : "var(--sd-amber)", marginTop: 1 }}
        />
        <span>
          {advice.text}
          {noConcerns && (
            <span className="sd-muted" style={{ whiteSpace: "nowrap" }}>
              {" "}· <StrokeIcon name="check" size={13} style={{ display: "inline", verticalAlign: "-2px" }} /> No concerns raised
            </span>
          )}
        </span>
      </div>
    </section>
  );
}

// ─── 2. What changes ─────────────────────────────────────────────────────────

function WhatChanges({ step, support }) {
  const problem = problemOf(step);
  const change = changeOf(step, support);
  const headingId = useId();

  return (
    <section className="sd-card" aria-labelledby={headingId}>
      <h3 id={headingId} className="sd-h3">What changes</h3>

      <div className="sd-tiles">
        <div className="sd-tile">
          <div className="sd-step-label" style={{ color: "var(--sd-red-2)" }}>1 · Problem now</div>
          <div className="sd-tile-title">{problem.title}</div>
          <p className="sd-tile-text">{problem.text}</p>
          {problem.tag && <span className="sd-tag sd-mono">{problem.tag}</span>}
        </div>
        <div className="sd-tile">
          <div className="sd-step-label" style={{ color: "var(--sd-teal-2)" }}>2 · DIWO applies</div>
          <div className="sd-tile-title">{step.refactoring}</div>
          <p className="sd-tile-text">{change.applies}</p>
          <span className="sd-tag">1 file · 1 step</span>
        </div>
        <div className="sd-tile">
          <div className="sd-step-label" style={{ color: "var(--sd-blue)" }}>3 · Result</div>
          <div className="sd-tile-title">{change.title}</div>
          {change.names.length > 0 && (
            <div className="sd-row" style={{ gap: 6, marginBottom: 8 }}>
              {change.names.map((n) => <code key={n} className="sd-code-chip">{n}</code>)}
            </div>
          )}
          <p className="sd-tile-text">{change.result}</p>
        </div>
      </div>

      <CodePreview step={step} />
    </section>
  );
}

/**
 * The SCTVA before/after when the step carries one. At plan time it usually
 * does not — SCTVA runs after the plan is sent — so "Generate preview" loads
 * the current file through the existing workspace-sources call and shows the
 * current code alone, full width, with the smell's lines marked.
 */
function CodePreview({ step }) {
  const file = step.target?.file || step.parameters?.source_file || null;
  const carried = previewOf(step);
  const [loaded, setLoaded] = useState({ status: "idle", source: null, error: null });
  const [fullOpen, setFullOpen] = useState(false);
  const range = lineRangeOf(step);

  const before = carried?.before ?? loaded.source;
  const after = carried?.after ?? null;
  const hasPreview = typeof before === "string";

  const generate = async () => {
    setLoaded({ status: "loading", source: null, error: null });
    try {
      const { source, missing } = await fetchFileSource(file);
      if (missing || source === null) throw new Error("The workspace no longer holds this file.");
      setLoaded({ status: "ready", source, error: null });
    } catch (err) {
      setLoaded({ status: "error", source: null, error: err.message || "Could not load the file." });
    }
  };

  const beforeRuns = hasPreview ? (after !== null ? previewSide(before, after, "before") : currentSide(before, range)) : [];
  const afterRuns = hasPreview && after !== null ? previewSide(before, after, "after") : [];
  const lines = lineCount(before);

  return (
    <div style={{ marginTop: 24 }}>
      <div className="sd-row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <span className="sd-row" style={{ gap: 8, fontSize: 16, fontWeight: 700, color: "var(--sd-heading)" }}>
          <StrokeIcon name="code" size={17} /> Code preview
        </span>
        <span className="sd-row" style={{ gap: 16, fontSize: 13 }}>
          {after !== null && (
            <>
              <span className="sd-row sd-muted" style={{ gap: 6 }}><span className="sd-swatch" style={{ background: "var(--sd-red)" }} />Removed</span>
              <span className="sd-row sd-muted" style={{ gap: 6 }}><span className="sd-swatch" style={{ background: "var(--sd-teal)" }} />Added</span>
            </>
          )}
          {lines && <span className="sd-dim"><span className="sd-mono">{lines}</span> lines in file</span>}
          {hasPreview && (
            <button type="button" className="sd-link sd-focus" onClick={() => setFullOpen(true)}>
              {after !== null ? "View full diff" : "View full file"} <StrokeIcon name="arrowRight" size={14} />
            </button>
          )}
        </span>
      </div>

      {!hasPreview ? (
        <div className="sd-skeleton">
          <div aria-hidden="true" className="sd-skeleton-lines">
            {[72, 54, 88, 40, 66].map((w, i) => <span key={i} style={{ width: `${w}%` }} />)}
          </div>
          <div className="sd-skeleton-body">
            <div style={{ fontWeight: 600, color: "var(--sd-heading)" }}>Preview not generated yet</div>
            <div className="sd-muted" style={{ fontSize: 13 }}>
              {loaded.status === "error"
                ? loaded.error
                : "SCTVA writes the refactored code after the plan is sent. You can load the current code now."}
            </div>
            <button
              type="button"
              className="sd-btn sd-focus"
              onClick={generate}
              disabled={!file || loaded.status === "loading"}
              aria-busy={loaded.status === "loading"}
            >
              <StrokeIcon name="code" size={16} />
              {loaded.status === "loading" ? "Generating…" : "Generate preview"}
            </button>
          </div>
        </div>
      ) : (
        <div className="sd-code-grid">
          <CodePane title="Current code" tone="red" copyLabel="Copy current code" copyText={before} runs={beforeRuns} />
          {/* The refactored side exists only once SCTVA has produced it. */}
          {after !== null && (
            <CodePane title="After refactoring (preview)" tone="teal" copyLabel="Copy refactored code" copyText={after} runs={afterRuns} />
          )}
        </div>
      )}

      {fullOpen && hasPreview && (
        <FullDiffDialog file={file} before={before} after={after} range={range} onClose={() => setFullOpen(false)} />
      )}
    </div>
  );
}

const COLLAPSE_OVER = 15;
const KEEP_EDGE = 6;
const MARKER = { removed: "−", added: "+", modified: "~", affected: "›", same: "" };

function CodePane({ title, tone, copyLabel, copyText, runs, empty }) {
  const [copied, setCopied] = useState(false);
  const [openRuns, setOpenRuns] = useState(() => new Set());
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(copyText || "");
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Clipboard blocked (insecure context) — leave the button as it was. */
    }
  };

  return (
    <div className="sd-code">
      <div className={`sd-code-head sd-code-head--${tone}`}>
        <span className="sd-row" style={{ gap: 8 }}>
          <span className="sd-swatch" style={{ borderRadius: 999, background: tone === "red" ? "var(--sd-red)" : "var(--sd-teal)" }} aria-hidden="true" />
          {title}
        </span>
        {copyText != null && (
          <button type="button" className="sd-icon-btn sd-focus" aria-label={copyLabel} onClick={copy}>
            {copied ? <><StrokeIcon name="check" size={14} /><span style={{ fontSize: 12 }}>Copied</span></> : <StrokeIcon name="copy" size={15} />}
          </button>
        )}
      </div>

      {runs.length === 0 ? (
        <div className="sd-code-empty">{empty || "No changed lines."}</div>
      ) : (
        <div className="sd-code-body" tabIndex={0} aria-label={`${title}, scrollable`}>
          <div className="sd-code-inner">
            {runs.map((run, ri) => {
              const collapsed = run.length > COLLAPSE_OVER && !openRuns.has(ri);
              const head = collapsed ? run.slice(0, KEEP_EDGE) : run;
              const tail = collapsed ? run.slice(run.length - KEEP_EDGE) : [];
              return (
                <div key={ri}>
                  {ri > 0 && <div className="sd-code-gap" aria-hidden="true">⋯</div>}
                  {head.map((l) => <CodeLine key={l.no} line={l} />)}
                  {collapsed && (
                    <button type="button" className="sd-code-more sd-focus" onClick={() => setOpenRuns((p) => new Set(p).add(ri))}>
                      Show {run.length - KEEP_EDGE * 2} more lines
                    </button>
                  )}
                  {tail.map((l) => <CodeLine key={l.no} line={l} />)}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function CodeLine({ line }) {
  return (
    <div className={`sd-line sd-line--${line.kind}`}>
      <span className="sd-line-no">{line.no}</span>
      <span className="sd-line-mark" aria-hidden="true">{MARKER[line.kind]}</span>
      <span className="sd-line-code">
        {line.kind !== "same" && <span className="sd-sr">{line.kind} line: </span>}
        {tokenize(line.text).map((t, i) => (
          <span key={i} className={t.kind === "plain" ? undefined : `sd-tok-${t.kind}`}>{t.text}</span>
        ))}
      </span>
    </div>
  );
}

function FullDiffDialog({ file, before, after, range, onClose }) {
  const closeRef = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  const lines = after !== null
    ? buildDiffRows(before, after).map((r) => ({
      key: r.key,
      no: r.kind === "after" ? r.afterNo : r.beforeNo,
      text: r.text,
      kind: r.kind === "before" ? "removed" : r.kind === "after" ? "added" : "same",
    }))
    : String(before).split("\n").map((text, i) => ({
      key: i,
      no: i + 1,
      text,
      kind: range && i + 1 >= range[0] && i + 1 <= range[1] ? "affected" : "same",
    }));

  return (
    <div className="sd-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="sd-modal"
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}
      >
        <div className="sd-modal-head">
          <div id={titleId} style={{ fontWeight: 600, color: "var(--sd-heading)" }}>
            {after !== null ? "Full diff" : "Current code"} · <span className="sd-mono">{file}</span>
          </div>
          <button ref={closeRef} type="button" className="sd-icon-btn sd-focus" aria-label="Close full diff" onClick={onClose}>
            <StrokeIcon name="x" size={18} />
          </button>
        </div>
        <div className="sd-code-body" style={{ maxHeight: "70vh", overflow: "auto" }}>
          <div className="sd-code-inner">
            {lines.map((l) => <CodeLine key={l.key} line={l} />)}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── 3. Code quality change ──────────────────────────────────────────────────

const EFFECT = {
  better: { label: "Better", icon: "check", color: "var(--sd-teal)" },
  worse: { label: "Worse", icon: "x", color: "var(--sd-red)" },
  same: { label: "No change", icon: null, color: "var(--sd-neutral)" },
};

function QualityChange({ step }) {
  const rows = qualityRows(step);
  const headingId = useId();
  if (rows.length === 0) return null;

  const scale = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
  const summary = rows.map((r) => `${r.tip}, ${EFFECT[r.effect].label.toLowerCase()}`).join("; ");

  return (
    <section className="sd-card" aria-labelledby={headingId}>
      <h3 id={headingId} className="sd-h3">Code quality change</h3>
      <div className="sd-subtitle">How this step changes the code&apos;s metrics</div>

      <div role="img" aria-label={`Code quality change: ${summary}.`} style={{ marginTop: 20 }}>
        <div className="sd-qrow sd-qaxis" aria-hidden="true">
          <span />
          <div className="sd-qticks"><span>−{fmt(scale)}</span><span>0</span><span>+{fmt(scale)}</span></div>
          <span />
        </div>
        {rows.map((r) => {
          const effect = EFFECT[r.effect];
          const width = (Math.abs(r.value) / scale) * 50;
          return (
            <div key={r.key} className="sd-qrow">
              <div>
                <div style={{ color: "var(--sd-text)", fontWeight: 500 }}>{r.label}</div>
                <div className="sd-dim" style={{ fontSize: 12 }}>{r.hint}</div>
              </div>
              <div className="sd-qtrack">
                <span className="sd-qzero" />
                {r.value !== 0 && (
                  <span
                    className="sd-qbar"
                    style={{
                      width: `${width}%`,
                      background: effect.color,
                      ...(r.value > 0
                        ? { left: "50%", borderRadius: "0 4px 4px 0" }
                        : { right: "50%", borderRadius: "4px 0 0 4px" }),
                    }}
                  />
                )}
              </div>
              <div style={{ textAlign: "right", color: effect.color }}>
                <div className="sd-mono" style={{ fontSize: 18, fontWeight: 700, lineHeight: 1.2 }}>
                  {r.value > 0 ? "+" : r.value < 0 ? "−" : ""}{fmt(Math.abs(r.value))}
                </div>
                <div className="sd-row" style={{ gap: 4, justifyContent: "flex-end", fontSize: 12, fontWeight: 600 }}>
                  {effect.icon && <StrokeIcon name={effect.icon} size={12} strokeWidth={3} />}
                  {effect.label}
                </div>
              </div>
              <span className="sd-tip" aria-hidden="true">{r.tip}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ─── 4. Concerns ─────────────────────────────────────────────────────────────

function Concerns({ concerns }) {
  const headingId = useId();
  return (
    <section className="sd-card sd-card--amber" aria-labelledby={headingId}>
      <h3 id={headingId} className="sd-h3">{concerns.length} concern{concerns.length === 1 ? "" : "s"}</h3>
      <ul className="sd-concerns">
        {concerns.map((c) => (
          <li key={c}>
            <StrokeIcon name="alert" size={17} style={{ color: "var(--sd-amber)", marginTop: 2 }} />
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ─── 5. Technical details ────────────────────────────────────────────────────

function TechnicalDetails({ step, support, open, onToggle }) {
  const bodyId = useId();
  const rows = technicalRows(step, support);
  return (
    <section className="sd-card sd-card--flush">
      <button
        type="button"
        className="sd-disclosure sd-focus"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <span className="sd-row" style={{ gap: 10 }}>
          <StrokeIcon name={open ? "chevronDown" : "chevronRight"} size={16} />
          <span style={{ fontWeight: 600, color: "var(--sd-heading)" }}>Technical details</span>
          <span className="sd-dim">· parameters, IDs, scoring</span>
        </span>
        <span className="sd-muted">{open ? "Hide" : "Show"}</span>
      </button>
      <dl id={bodyId} className="sd-kv" hidden={!open}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="sd-mono">{k}</dt>
            <dd className="sd-mono">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
