/**
 * SourceViewer.jsx
 * ================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Shows the ORIGINAL source of one analysed file with its code smells marked
 * in the gutter, so the developer can see what the CUQA agent flagged before
 * deciding whether to send it to the Refactoring Planning Agent.
 *
 * Opened two ways from the Code Smell Review stage:
 *   • from one smell — the file opens scrolled to that smell, highlighted;
 *   • from a file    — every smell in the file is marked, none focused.
 *
 * Where the source comes from
 * ---------------------------
 * The CUQA quality report describes files but never ships their contents, and
 * the CUQA agent exposes no file-content endpoint. The only reader of the
 * analysed workspace is the SCTVA agent's /sctva/cuqa-sources, which the
 * orchestrator proxies at POST /api/workspace/sources — so this component
 * calls diwoApi like every other DIWO screen and never talks to an agent
 * itself. If SCTVA is not running the viewer says so instead of showing an
 * empty editor.
 *
 * Line resolution mirrors the backend exactly
 * (domain/cuqa_normalizer.py::cuqa_report_to_smells):
 *     start = start_line || line
 *     end   = end_line   || start
 * so a LongMethod highlights its whole body while a MagicNumber highlights the
 * single line, and the viewer can never disagree with the report about where a
 * smell lives.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { C, Badge, severityColor } from "../diwoTheme.jsx";
import { fetchFileSource } from "../services/diwoApi";
import { buildCoverage, withLines } from "../utils/smellLines";

export default function SourceViewer({
  file,
  language,
  smells = [],
  focusId = null,
  selectedIds = null,
  onToggleSmell,
  onClose,
}) {
  const [state, setState] = useState({ loading: true, source: null, error: null, missing: false });
  const [focused, setFocused] = useState(focusId);

  const focusLineRef = useRef(null);

  // Re-focus when the caller opens the same file on a different smell. Done
  // during render rather than in an effect: no cascading render, and the first
  // paint already shows the right smell highlighted.
  const [prevFocusId, setPrevFocusId] = useState(focusId);
  if (focusId !== prevFocusId) {
    setPrevFocusId(focusId);
    setFocused(focusId);
  }

  // ── Load the file ─────────────────────────────────────────────────────────
  // `file` never changes for a given instance — the parent keys this component
  // by it — so the initial state above IS the loading state and the effect only
  // has to resolve the request.
  useEffect(() => {
    if (!file) return undefined;
    const controller = new AbortController();
    let alive = true;

    fetchFileSource(file, { signal: controller.signal })
      .then((result) => {
        if (!alive) return;
        setState({
          loading: false,
          source: result.source,
          missing: result.missing || result.source === null,
          error: null,
        });
      })
      .catch((error) => {
        if (!alive || error.name === "AbortError") return;
        setState({ loading: false, source: null, missing: false, error });
      });

    return () => {
      alive = false;
      controller.abort();
    };
  }, [file]);

  // Esc closes, matching every other overlay the developer has used.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const lines = useMemo(
    () => (typeof state.source === "string" ? state.source.split("\n") : []),
    [state.source]
  );

  const marked = useMemo(() => withLines(smells), [smells]);
  const coverage = useMemo(() => buildCoverage(marked), [marked]);

  const focusedSmell = marked.find((s) => s.id === focused) || null;

  // Scroll the focused smell into view once the source is on screen.
  useEffect(() => {
    if (state.loading || !focusLineRef.current) return;
    focusLineRef.current.scrollIntoView({ block: "center", behavior: "auto" });
  }, [state.loading, focused, lines.length]);

  const counts = marked.reduce((acc, s) => {
    acc[s.severity] = (acc[s.severity] || 0) + 1;
    return acc;
  }, {});

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(4,6,10,0.78)", backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(1240px, 100%)", height: "min(86vh, 900px)",
          background: C.bg, border: `1px solid ${C.borderAcc}`, borderRadius: 14,
          display: "flex", flexDirection: "column", overflow: "hidden",
          boxShadow: "0 24px 60px rgba(0,0,0,0.55)",
        }}
      >
        <Header
          file={file}
          language={language}
          total={marked.length}
          counts={counts}
          focusedSmell={focusedSmell}
          onClose={onClose}
        />

        <div style={{ display: "grid", gridTemplateColumns: "296px 1fr", flex: 1, minHeight: 0 }}>
          <SmellList
            smells={marked}
            focused={focused}
            selectedIds={selectedIds}
            onFocus={setFocused}
            onToggleSmell={onToggleSmell}
          />

          <div style={{ minWidth: 0, overflow: "auto", background: C.bg }}>
            {state.loading && <Notice title="Reading the original file…" detail={file} />}

            {!state.loading && state.error && (
              <Notice
                tone={C.danger}
                title="The original file could not be read"
                detail={state.error.message}
                hint={
                  state.error.status === 503
                    ? "The workspace reader lives in the Safe Transformation agent. Start it with:  cd agents/transformation_agent/safe_code_transformation_agent && python app.py"
                    : null
                }
              />
            )}

            {!state.loading && !state.error && state.missing && (
              <Notice
                tone={C.warn}
                title="This file is no longer in the analysed workspace"
                detail={file}
                hint="The report describes an earlier analysis. Re-analyze in the Code Smell Review stage to refresh the workspace, then open the file again."
              />
            )}

            {!state.loading && !state.error && !state.missing && (
              <CodeLines
                lines={lines}
                coverage={coverage}
                focusedSmell={focusedSmell}
                focusLineRef={focusLineRef}
              />
            )}
          </div>
        </div>

        <Legend counts={counts} lineCount={lines.length} />
      </div>
    </div>
  );
}

// ─── Header ──────────────────────────────────────────────────────────────────

function Header({ file, language, total, counts, focusedSmell, onClose }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12, padding: "14px 18px",
      borderBottom: `1px solid ${C.border}`, background: C.panel, flexShrink: 0,
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1, color: C.textMuted, textTransform: "uppercase" }}>
            Original source
          </span>
          {language && <Badge label={language} color={C.info} />}
          <Badge label={`${total} smell${total === 1 ? "" : "s"}`} color={counts.high ? C.danger : C.warn} />
        </div>
        <div title={file} style={{
          fontSize: 15, fontWeight: 700, color: C.text, fontFamily: "monospace",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {file}
        </div>
        {focusedSmell && (
          <div style={{ fontSize: 11, color: C.textMuted, marginTop: 3 }}>
            Focused: <span style={{ color: severityColor(focusedSmell.severity), fontWeight: 700 }}>{focusedSmell.type}</span>
            {focusedSmell.start > 0 && (
              <> · line {focusedSmell.start}{focusedSmell.end > focusedSmell.start ? `–${focusedSmell.end}` : ""}</>
            )}
          </div>
        )}
      </div>

      <button
        onClick={onClose}
        style={{
          padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 700,
          background: C.panel, color: C.textSub, border: `1px solid ${C.border}`,
          cursor: "pointer", flexShrink: 0,
        }}
      >
        Close ✕
      </button>
    </div>
  );
}

// ─── Smell list ──────────────────────────────────────────────────────────────

function SmellList({ smells, focused, selectedIds, onFocus, onToggleSmell }) {
  return (
    <div style={{
      borderRight: `1px solid ${C.border}`, overflowY: "auto",
      background: C.panel, minHeight: 0,
    }}>
      <div style={{
        padding: "10px 14px", fontSize: 10, fontWeight: 700, letterSpacing: 1,
        color: C.textMuted, textTransform: "uppercase",
        borderBottom: `1px solid ${C.border}`, position: "sticky", top: 0, background: C.panel, zIndex: 1,
      }}>
        Smells in this file
      </div>

      {smells.length === 0 && (
        <div style={{ padding: "16px 14px", fontSize: 12, color: C.textMuted }}>
          No smells were reported in this file.
        </div>
      )}

      {smells.map((smell, idx) => {
        const color = severityColor(smell.severity);
        const isFocused = smell.id === focused;
        const isSelected = selectedIds?.has(smell.id);

        return (
          <div
            key={smell.id}
            onClick={() => onFocus(smell.id)}
            style={{
              padding: "10px 14px", cursor: "pointer",
              borderBottom: `1px solid ${C.border}`,
              borderLeft: `3px solid ${isFocused ? color : "transparent"}`,
              background: isFocused ? `${color}14` : "transparent",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace", minWidth: 16 }}>
                {idx + 1}.
              </span>
              <div style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }} />
              <span style={{ fontSize: 12, fontWeight: 700, color: isFocused ? C.text : C.textSub, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {smell.type}
              </span>
              {smell.start > 0 && (
                <span style={{ marginLeft: "auto", fontSize: 10, color: C.textMuted, fontFamily: "monospace", flexShrink: 0 }}>
                  L{smell.start}{smell.end > smell.start ? `–${smell.end}` : ""}
                </span>
              )}
            </div>

            {smell.entity && (
              <div style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace", marginTop: 3, marginLeft: 30 }}>
                {smell.entity}
              </div>
            )}
            {smell.message && (
              <div style={{ fontSize: 11, color: C.textMuted, marginTop: 3, marginLeft: 30, lineHeight: 1.45 }}>
                {smell.message}
              </div>
            )}

            {onToggleSmell && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleSmell(smell.id);
                }}
                style={{
                  marginTop: 7, marginLeft: 30, padding: "4px 10px", borderRadius: 6,
                  fontSize: 10, fontWeight: 700, cursor: "pointer",
                  background: isSelected ? C.accent : "transparent",
                  color: isSelected ? "#000" : C.textMuted,
                  border: `1px solid ${isSelected ? C.accent : C.border}`,
                }}
              >
                {isSelected ? "✓ Selected" : "Select this smell"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Code ────────────────────────────────────────────────────────────────────

function CodeLines({ lines, coverage, focusedSmell, focusLineRef }) {
  const focusAnchor = focusedSmell?.line || focusedSmell?.start || 0;

  return (
    <div style={{ fontFamily: "monospace", fontSize: 12.5, lineHeight: "20px", padding: "8px 0" }}>
      {lines.map((text, index) => {
        const lineNo = index + 1;
        const hit = coverage.get(lineNo);
        const inFocus =
          focusedSmell && focusedSmell.start > 0 &&
          lineNo >= focusedSmell.start && lineNo <= focusedSmell.end;
        const isAnchor = lineNo === focusAnchor;

        const color = hit ? severityColor(hit.worst) : null;

        return (
          <div
            key={lineNo}
            ref={isAnchor ? focusLineRef : null}
            style={{
              display: "flex", alignItems: "stretch",
              background: inFocus
                ? `${severityColor(focusedSmell.severity)}22`
                : hit ? `${color}0e` : "transparent",
              outline: isAnchor && focusedSmell ? `1px solid ${severityColor(focusedSmell.severity)}66` : "none",
              outlineOffset: -1,
            }}
          >
            {/* The marker bar: the file's own margin, coloured by the worst
                severity covering this line. */}
            <div style={{
              width: 4, flexShrink: 0,
              background: color || "transparent",
              opacity: hit && hit.anchors.length ? 1 : 0.55,
            }} />

            <span style={{
              width: 52, flexShrink: 0, textAlign: "right", paddingRight: 12,
              color: hit ? severityColor(hit.worst) : C.textMuted,
              fontWeight: hit && hit.anchors.length ? 700 : 400,
              userSelect: "none",
            }}>
              {lineNo}
            </span>

            {/* Dot column: a filled dot only where a smell is actually
                anchored, so the start of a long range is findable inside it. */}
            <span style={{ width: 14, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              {hit && hit.anchors.length > 0 && (
                <span
                  title={hit.anchors.map((s) => `${s.type}${s.entity ? ` · ${s.entity}` : ""}`).join("\n")}
                  style={{ width: 7, height: 7, borderRadius: "50%", background: severityColor(hit.worst) }}
                />
              )}
            </span>

            <pre style={{
              margin: 0, paddingRight: 20, whiteSpace: "pre", color: hit ? C.text : C.textSub,
              flex: 1, minWidth: 0,
            }}>
              {text || " "}
            </pre>

            {hit && hit.anchors.length > 0 && (
              <span style={{
                flexShrink: 0, alignSelf: "center", marginRight: 12,
                fontSize: 10, fontWeight: 700, color: severityColor(hit.worst),
                background: `${severityColor(hit.worst)}1a`,
                border: `1px solid ${severityColor(hit.worst)}40`,
                borderRadius: 5, padding: "1px 7px", whiteSpace: "nowrap",
              }}>
                {hit.anchors.length === 1
                  ? hit.anchors[0].type
                  : `${hit.anchors.length} smells`}
              </span>
            )}
          </div>
        );
      })}

      {lines.length === 0 && (
        <div style={{ padding: "24px 20px", color: C.textMuted, fontSize: 12 }}>
          This file is empty.
        </div>
      )}
    </div>
  );
}

// ─── Chrome ──────────────────────────────────────────────────────────────────

function Legend({ counts, lineCount }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap",
      padding: "9px 18px", borderTop: `1px solid ${C.border}`,
      background: C.panel, fontSize: 11, color: C.textMuted, flexShrink: 0,
    }}>
      <span>{lineCount} line{lineCount === 1 ? "" : "s"}</span>
      {["high", "medium", "low"].map((sev) => (
        <span key={sev} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 4, height: 12, background: severityColor(sev), borderRadius: 1 }} />
          {sev} ({counts[sev] || 0})
        </span>
      ))}
      <span style={{ marginLeft: "auto" }}>
        A bar marks every line a smell covers; a dot marks the line it is reported on. Esc closes.
      </span>
    </div>
  );
}

function Notice({ title, detail, hint, tone = C.textMuted }) {
  return (
    <div style={{ padding: "36px 28px", maxWidth: 720 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: tone === C.textMuted ? C.text : tone, marginBottom: 6 }}>
        {title}
      </div>
      {detail && (
        <div style={{ fontSize: 12, color: C.textSub, fontFamily: "monospace", lineHeight: 1.6, wordBreak: "break-word" }}>
          {detail}
        </div>
      )}
      {hint && (
        <div style={{
          marginTop: 14, fontSize: 11, color: C.textMuted, fontFamily: "monospace", lineHeight: 1.6,
          background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px",
        }}>
          {hint}
        </div>
      )}
    </div>
  );
}
