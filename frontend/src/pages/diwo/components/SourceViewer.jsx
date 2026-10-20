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
import { T, tint, eyebrow, ghostButton, sevTone } from "../codeSmellTheme.js";
import { SeverityTag, Chip, Spinner } from "../codeSmellUi.jsx";
import { TriCheckbox } from "./QuickSelectDropdown.jsx";
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
  // Where the file's text comes from. Defaults to the CUQA workspace; a
  // GitHub-linked workflow passes a loader that reads the analysed commit.
  loadSource = fetchFileSource,
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

    loadSource(file, { signal: controller.signal })
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
    // loadSource is fixed for an instance: the parent keys the viewer by file
    // and passes a loader bound to one workflow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        background: T.overlay, backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Original source of ${file}`}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(1280px, 100%)", height: "min(88vh, 920px)",
          background: T.surface, border: `1px solid ${T.border}`, borderRadius: 14,
          display: "flex", flexDirection: "column", overflow: "hidden",
          boxShadow: T.shadowLg,
        }}
      >
        <Header
          file={file}
          language={language}
          total={marked.length}
          counts={counts}
          lineCount={state.loading || state.error || state.missing ? null : lines.length}
          onClose={onClose}
        />

        <div className="dw-sv-body">
          <SmellList
            smells={marked}
            focused={focused}
            selectedIds={selectedIds}
            onFocus={setFocused}
            onToggleSmell={onToggleSmell}
          />

          <div style={{ minWidth: 0, minHeight: 0, overflow: "auto", background: T.code }}>
            {state.loading && <Notice title="Reading the original file…" detail={file} loading />}

            {!state.loading && state.error && (
              <Notice
                tone={T.high}
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
                tone={T.medium}
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

        <Footer counts={counts} lineCount={lines.length} focusedSmell={focusedSmell} />
      </div>
    </div>
  );
}

// ─── Header ──────────────────────────────────────────────────────────────────

function Header({ file, language, total, counts, lineCount, onClose }) {
  const slash = file.lastIndexOf("/");
  const dir = slash >= 0 ? file.slice(0, slash + 1) : "";
  const name = slash >= 0 ? file.slice(slash + 1) : file;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12, padding: "12px 16px",
      borderBottom: `1px solid ${T.border}`, background: T.surface, flexShrink: 0,
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
          <span style={eyebrow}>Original source</span>
          {language && <Chip tone={T.info}>{language}</Chip>}
          <Chip>{total} smell{total === 1 ? "" : "s"}</Chip>
          {["high", "medium", "low"].filter((s) => counts[s]).map((s) => (
            <Chip key={s} tone={sevTone(s)}>{counts[s]} {s}</Chip>
          ))}
          {typeof lineCount === "number" && (
            <span style={{ fontSize: 11, color: T.text3 }}>{lineCount} lines</span>
          )}
        </div>
        <div title={file} style={{
          fontSize: 14, fontFamily: T.mono, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          <span style={{ color: T.text3 }}>{dir}</span>
          <span style={{ color: T.text, fontWeight: 700 }}>{name}</span>
        </div>
      </div>

      <button
        type="button"
        onClick={onClose}
        aria-label="Close the source viewer"
        className="dw-focus dw-hover"
        style={ghostButton(true, { flexShrink: 0 })}
      >
        Close <span aria-hidden="true">✕</span>
      </button>
    </div>
  );
}

// ─── Smell list ──────────────────────────────────────────────────────────────

/**
 * Focus and selection are two separate controls: the row body focuses the
 * smell in the code, the checkbox (Smell / Category wise only) changes what
 * is sent to planning. Clicking to look never selects.
 */
function SmellList({ smells, focused, selectedIds, onFocus, onToggleSmell }) {
  return (
    <div className="dw-sv-list" style={{
      borderRight: `1px solid ${T.border}`, overflowY: "auto",
      background: T.surface, minHeight: 0,
    }}>
      <div style={{
        ...eyebrow, padding: "10px 14px",
        borderBottom: `1px solid ${T.border}`, position: "sticky", top: 0, background: T.surface, zIndex: 1,
        display: "flex", justifyContent: "space-between",
      }}>
        <span>Findings in this file</span>
        {onToggleSmell && selectedIds && (
          <span style={{ color: T.accentText }}>
            {smells.filter((s) => selectedIds.has(s.id)).length} selected
          </span>
        )}
      </div>

      {smells.length === 0 && (
        <div style={{ padding: "16px 14px", fontSize: 12, color: T.text2 }}>
          No smells were reported in this file.
        </div>
      )}

      {smells.map((smell, idx) => {
        const color = sevTone(smell.severity);
        const isFocused = smell.id === focused;
        const isSelected = Boolean(selectedIds?.has(smell.id));

        return (
          <div
            key={smell.id}
            className={isFocused ? undefined : "dw-sv-item"}
            style={{
              display: "flex", gap: 8, alignItems: "flex-start",
              padding: "10px 12px 10px 11px",
              borderBottom: `1px solid ${T.border}`,
              borderLeft: `3px solid ${isFocused ? color : "transparent"}`,
              background: isFocused ? tint(color, 10) : "transparent",
            }}
          >
            {onToggleSmell && (
              <button
                type="button"
                onClick={() => onToggleSmell(smell.id)}
                aria-pressed={isSelected}
                aria-label={`${isSelected ? "Deselect" : "Select"} ${smell.type}${smell.start ? ` at line ${smell.start}` : ""}`}
                title={isSelected ? "Selected for planning — click to remove" : "Select for planning"}
                className="dw-focus"
                style={{ background: "none", border: "none", padding: 2, marginTop: 1, cursor: "pointer", display: "flex", flexShrink: 0, borderRadius: 4 }}
              >
                <TriCheckbox state={isSelected ? "all" : "none"} size={15} />
              </button>
            )}

            <button
              type="button"
              onClick={() => onFocus(smell.id)}
              aria-current={isFocused ? "true" : undefined}
              className="dw-focus"
              style={{
                flex: 1, minWidth: 0, background: "none", border: "none", padding: 0,
                textAlign: "left", cursor: "pointer", color: "inherit", borderRadius: 4,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 10.5, color: T.text3, fontFamily: T.mono, minWidth: 18 }}>
                  {idx + 1}.
                </span>
                <SeverityTag severity={smell.severity} compact />
                {smell.start > 0 && (
                  <span style={{ marginLeft: "auto", fontSize: 10.5, color: T.text2, fontFamily: T.mono, flexShrink: 0 }}>
                    L{smell.start}{smell.end > smell.start ? `–${smell.end}` : ""}
                  </span>
                )}
              </div>
              <div style={{
                fontSize: 12.5, fontWeight: 700, color: T.text, marginTop: 4,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {smell.type}
              </div>
              {smell.entity && (
                <div style={{ fontSize: 11, color: T.text2, fontFamily: T.mono, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {smell.entity}
                </div>
              )}
              {isFocused && smell.message && (
                <div style={{ fontSize: 11.5, color: T.text2, marginTop: 4, lineHeight: 1.5 }}>
                  {smell.message}
                </div>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ─── Code ────────────────────────────────────────────────────────────────────

function CodeLines({ lines, coverage, focusedSmell, focusLineRef }) {
  const focusAnchor = focusedSmell?.line || focusedSmell?.start || 0;
  const focusTone = focusedSmell ? sevTone(focusedSmell.severity) : null;

  return (
    <div style={{
      fontFamily: T.mono, fontSize: 12.5, lineHeight: "21px", padding: "8px 0",
      minWidth: "max-content",
    }}>
      {lines.map((text, index) => {
        const lineNo = index + 1;
        const hit = coverage.get(lineNo);
        const inFocus =
          focusedSmell && focusedSmell.start > 0 &&
          lineNo >= focusedSmell.start && lineNo <= focusedSmell.end;
        const isAnchor = lineNo === focusAnchor;
        const color = hit ? sevTone(hit.worst) : null;
        const anchored = Boolean(hit && hit.anchors.length);

        return (
          <div
            key={lineNo}
            ref={isAnchor ? focusLineRef : null}
            style={{
              display: "flex", alignItems: "stretch",
              background: inFocus
                ? tint(focusTone, 16)
                : hit ? tint(color, 6) : "transparent",
              boxShadow: isAnchor && focusedSmell ? `inset 0 0 0 1px ${tint(focusTone, 55)}` : "none",
            }}
          >
            {/* Marker bar: every line a smell covers, coloured by the worst
                severity on it; solid where a smell is anchored. */}
            <div style={{
              width: 4, flexShrink: 0,
              background: color || "transparent",
              opacity: anchored ? 1 : 0.45,
            }} />

            <span style={{
              width: 52, flexShrink: 0, textAlign: "right", paddingRight: 10,
              color: hit ? color : T.text3,
              fontWeight: anchored ? 700 : 400,
              userSelect: "none",
            }}>
              {lineNo}
            </span>

            <span style={{ width: 16, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              {anchored && (
                <span
                  title={hit.anchors.map((s) => `${s.type}${s.entity ? ` · ${s.entity}` : ""}`).join("\n")}
                  style={{ width: 7, height: 7, borderRadius: "50%", background: color }}
                />
              )}
            </span>

            <pre style={{
              margin: 0, paddingRight: 16, whiteSpace: "pre", color: T.text,
              fontFamily: "inherit", fontSize: "inherit", lineHeight: "inherit",
            }}>
              {text || " "}
            </pre>

            {/* Kept on the line: the code pane scrolls horizontally, so the
                label is reachable on long lines too. */}
            {anchored && (
              <span style={{
                flexShrink: 0, alignSelf: "center", margin: "0 12px 0 8px",
                fontFamily: "inherit", fontSize: 10.5, fontWeight: 700, color,
                background: tint(color, 12), border: `1px solid ${tint(color, 35)}`,
                borderRadius: 5, padding: "0 7px", whiteSpace: "nowrap", lineHeight: "17px",
              }}>
                {hit.anchors.length === 1 ? hit.anchors[0].type : `${hit.anchors.length} smells`}
              </span>
            )}
          </div>
        );
      })}

      {lines.length === 0 && (
        <div style={{ padding: "24px 20px", color: T.text2, fontSize: 12, fontFamily: "inherit" }}>
          This file is empty.
        </div>
      )}
    </div>
  );
}

// ─── Chrome ──────────────────────────────────────────────────────────────────

function Footer({ counts, lineCount, focusedSmell }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
      padding: "8px 16px", borderTop: `1px solid ${T.border}`,
      background: T.surface2, fontSize: 11.5, color: T.text2, flexShrink: 0,
    }}>
      {focusedSmell ? (
        <span style={{ minWidth: 0 }}>
          Focused:{" "}
          <b style={{ color: sevTone(focusedSmell.severity) }}>{focusedSmell.type}</b>
          {focusedSmell.start > 0 && (
            <span style={{ fontFamily: T.mono }}>
              {" "}· L{focusedSmell.start}{focusedSmell.end > focusedSmell.start ? `–${focusedSmell.end}` : ""}
            </span>
          )}
        </span>
      ) : (
        <span>{lineCount} line{lineCount === 1 ? "" : "s"} · select a finding to focus it</span>
      )}
      {["high", "medium", "low"].map((sev) => (
        <span key={sev} style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span aria-hidden="true" style={{ width: 4, height: 12, background: sevTone(sev), borderRadius: 1 }} />
          {sev} ({counts[sev] || 0})
        </span>
      ))}
      <span style={{ marginLeft: "auto", color: T.text3 }}>
        Bar = lines a smell covers · dot = reported line · Esc closes
      </span>
    </div>
  );
}

function Notice({ title, detail, hint, tone, loading = false }) {
  return (
    <div role={loading ? "status" : "alert"} style={{ padding: "36px 28px", maxWidth: 720 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13.5, fontWeight: 700, color: tone || T.text, marginBottom: 6 }}>
        {loading && <Spinner />}
        {title}
      </div>
      {detail && (
        <div style={{ fontSize: 12, color: T.text2, fontFamily: T.mono, lineHeight: 1.6, wordBreak: "break-word" }}>
          {detail}
        </div>
      )}
      {hint && (
        <div style={{
          marginTop: 14, fontSize: 11.5, color: T.text2, fontFamily: T.mono, lineHeight: 1.6,
          background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "10px 14px",
        }}>
          {hint}
        </div>
      )}
    </div>
  );
}
