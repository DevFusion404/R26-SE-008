/**
 * CodeSmellApprovalPage.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 1 of the DIWO workflow: render the CUQA agent's quality report and let
 * the developer pick which files/smells go forward to the Refactoring Planning
 * Agent.
 *
 * Data source, in priority order:
 *   1. `reportData` prop — the report the parent already holds (the workflow's
 *      filtered report, or the one returned by POST /workflows/from-cuqa).
 *   2. Live CUQA report — POST /api/cuqa/quality-report on the DIWO backend,
 *      which proxies the CUQA agent's POST http://localhost:8080/api/quality-report.
 *      If the DIWO backend is down, diwoApi calls the CUQA agent directly.
 *   3. Bundled sample report (diwoData.CUQA_DATA) — only if the developer opts
 *      in after a failure, and it is labelled as sample data in the UI.
 *
 * Either mode can open the ORIGINAL source behind a finding: the eye button on
 * a smell opens its file scrolled to that smell, the one on a file header opens
 * the file with every smell in it marked. See components/SourceViewer.jsx.
 *
 * Selection impact
 * ----------------
 * Every row also carries what picking it buys and what skipping it costs, from
 * GET /workflows/<id>/smell-impacts. The records are selection-independent, so
 * they are fetched once and aggregated locally on each click — the footer has
 * to react on the same frame as the checkbox. The backend is still the
 * authority: POST /selection-impact is called when the developer proceeds, and
 * that projection is what reaches the audit trail.
 *
 * The single most important thing on screen is the capability chip. Ten of the
 * highest-severity smell types CUQA reports map to refactorings SCTVA cannot
 * perform, so severity alone was actively misleading about what a selection
 * would achieve.
 *
 * All of it degrades: no records means no chips, no panel, and Stage 1 behaves
 * exactly as it did before.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CUQA_DATA } from "../data/diwoData";
import { C, Card, Badge, severityColor } from "../diwoTheme.jsx";
import SourceViewer from "../components/SourceViewer.jsx";
import ImpactChip from "../components/ImpactChip.jsx";
import ImpactDrawer from "../components/ImpactDrawer.jsx";
import TradeOffPanel from "../components/TradeOffPanel.jsx";
import { DEFAULT_BUDGET_MINUTES } from "../utils/impactPresets";
import { qualityBaseline, summariseSelection } from "../utils/impactSummary";
import {
  analyseSelectionImpact, fetchQualityReport, fetchSmellImpacts,
  optimiseSelection, previewSmellSelection,
} from "../services/diwoApi";


const num = (value, fallback = 0) => (typeof value === "number" ? value : fallback);
const round = (value, dp = 0) => {
  const factor = 10 ** dp;
  return Math.round(num(value) * factor) / factor;
};

/**
 * Smell id — must match the backend exactly.
 *
 * cuqa_report_to_smells() and filter_cuqa_report() both build ids as
 * `<relative_path>:<line>:<index-within-file>`, so an id computed here from the
 * rendered report resolves to the same smell server-side without a lookup.
 * `index` is the smell's position in the file's own code_smells list.
 */
const smellId = (relativePath, smell, index) =>
  `${relativePath}:${smell?.line || 0}:${index}`;

/** Every file path bar is drawn in one colour — the theme's blue. Files used to
 *  get a hashed colour each, which made the list read as eight competing
 *  categories when the only thing being said was "this is a file path". The
 *  severity colours are the ones that carry meaning on this screen, so nothing
 *  else should compete with them. */
const FILE_BAR_COLOR = C.info;

export default function CodeSmellApprovalPage({
  onProceed,
  workflowId,
  reportData,
  onReportLoaded,
}) {
  // "smell" — pick individual smells, so a file can be partially refactored.
  //            The default: opening the DIWO Agent lands on smell-wise review.
  // "file"  — pick whole files, every smell in them goes forward.
  const [mode, setMode] = useState("smell");
  const [selected, setSelected] = useState(new Set());          // file paths
  const [selectedIds, setSelectedIds] = useState(new Set());    // smell ids
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Which file's original source is open, and which smell inside it is focused.
  // { file, focusId } — focusId is null when the whole file was opened.
  const [viewing, setViewing] = useState(null);

  // ── Selection impact ───────────────────────────────────────────────────────
  // Map<smell_id, record>. Null until loaded, and stays null if the endpoint
  // is unavailable — every consumer below treats that as "render nothing".
  const [impacts, setImpacts] = useState(null);
  const [explaining, setExplaining] = useState(null);   // smell id in the drawer
  const [optimising, setOptimising] = useState(false);
  const [budgetMinutes, setBudgetMinutes] = useState(DEFAULT_BUDGET_MINUTES);
  const [interactionNotes, setInteractionNotes] = useState([]);

  // ── CUQA report loading ────────────────────────────────────────────────────
  const [fetched, setFetched] = useState(null);   // { report, via, cuqaUrl, ... }
  const [loading, setLoading] = useState(!reportData);
  const [loadError, setLoadError] = useState(null);
  const [useSample, setUseSample] = useState(false);

  // Kept in a ref so an inline parent callback cannot re-trigger the fetch.
  const onReportLoadedRef = useRef(onReportLoaded);
  useEffect(() => {
    onReportLoadedRef.current = onReportLoaded;
  });

  const applyReport = useCallback((result) => {
    setFetched(result);
    setUseSample(false);
    setSelected(new Set());
    setSelectedIds(new Set());
    setLoadError(null);
    setLoading(false);
    setViewing(null);
    setExplaining(null);
    setInteractionNotes([]);
    onReportLoadedRef.current?.(result);
  }, []);

  const applyLoadError = useCallback((error) => {
    if (error.name === "AbortError") return;   // unmounted / superseded
    setFetched(null);
    setLoadError(error);
    setLoading(false);
  }, []);

  /** Manual re-analyze — runs from an event handler, so setState is safe here. */
  const reloadReport = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    fetchQualityReport().then(applyReport).catch(applyLoadError);
  }, [applyReport, applyLoadError]);

  // A report handed down by the parent is always the newer truth: it is either
  // the workflow's filtered report or the one that seeded the workflow, so drop
  // any locally fetched copy and the current selection. Adjusted during render
  // rather than in an effect — no cascading render, no stale first paint.
  const [prevReportData, setPrevReportData] = useState(reportData);
  if (reportData !== prevReportData) {
    setPrevReportData(reportData);
    if (reportData) {
      setFetched(null);
      setLoadError(null);
      setUseSample(false);
      setSelected(new Set());
      setSelectedIds(new Set());
      setLoading(false);
      setViewing(null);
      setImpacts(null);
      setExplaining(null);
      setInteractionNotes([]);
    }
  }

  // Initial load. `loading` already starts as !reportData, so the effect only
  // has to resolve the request — nothing is set synchronously.
  useEffect(() => {
    if (reportData) return undefined;
    const controller = new AbortController();
    fetchQualityReport({ signal: controller.signal }).then(applyReport).catch(applyLoadError);
    return () => controller.abort();
  }, [reportData, applyReport, applyLoadError]);

  // Impact records depend only on the workflow's smells, so this runs once per
  // workflow. A failure is swallowed on purpose: the chips and the panel
  // disappear, the smell list keeps working.
  useEffect(() => {
    if (!workflowId) return undefined;
    const controller = new AbortController();

    fetchSmellImpacts(workflowId, { signal: controller.signal })
      .then((payload) => {
        setImpacts(new Map((payload.records || []).map((r) => [r.smell_id, r])));
      })
      .catch(() => setImpacts(null));

    return () => controller.abort();
  }, [workflowId]);

  // ── Resolve the report actually being rendered ─────────────────────────────
  // `fetched` only outranks `reportData` after an explicit re-analyze, because
  // the effect above clears it whenever the parent supplies a new report.
  const sourceReport = fetched?.report || reportData || (useSample ? CUQA_DATA : null);
  const origin = fetched ? "cuqa" : reportData ? "workflow" : useSample ? "sample" : null;

  const allFiles = sourceReport?.files || [];
  const filesWithSmells = allFiles.filter((f) => (f.code_smells || []).length > 0);
  const hiddenCount = allFiles.length - filesWithSmells.length;

  const isSmellMode = mode === "smell";
  const term = search.trim().toLowerCase();

  // Every smell in the report, flattened once and tagged with the id the
  // backend will resolve it by. Both modes read from this list.
  const smellRows = filesWithSmells.flatMap((f) =>
    (f.code_smells || []).map((smell, idx) => ({
      id: smellId(f.relative_path, smell, idx),
      file: f.relative_path,
      language: f.language,
      smell,
    }))
  );

  const filtered = filesWithSmells.filter((f) => {
    const matchSev = filter === "all" || f.code_smells.some((s) => s.severity === filter);
    const matchSearch =
      term === "" ||
      (f.relative_path || "").toLowerCase().includes(term) ||
      f.code_smells.some((s) => (s.type || "").toLowerCase().includes(term));
    return matchSev && matchSearch;
  });

  const filteredRows = smellRows.filter(({ file, smell }) => {
    const matchSev = filter === "all" || smell.severity === filter;
    const matchSearch =
      term === "" ||
      file.toLowerCase().includes(term) ||
      (smell.type || "").toLowerCase().includes(term) ||
      (smell.entity || "").toLowerCase().includes(term) ||
      (smell.message || "").toLowerCase().includes(term);
    return matchSev && matchSearch;
  });

  // Smell rows stay grouped under their file so the developer keeps the file
  // context while picking individual smells.
  const groups = [];
  const groupByFile = new Map();
  for (const row of filteredRows) {
    let group = groupByFile.get(row.file);
    if (!group) {
      group = { file: row.file, language: row.language, rows: [] };
      groupByFile.set(row.file, group);
      groups.push(group);
    }
    group.rows.push(row);
  }

  const toggleFile = (path) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });
  };

  const toggleSmell = (id) => {
    setSelectedIds((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  /** Select / deselect every visible smell of one file (smell mode header). */
  const toggleGroup = (group) => {
    const allOn = group.rows.every((r) => selectedIds.has(r.id));
    setSelectedIds((prev) => {
      const n = new Set(prev);
      group.rows.forEach((r) => (allOn ? n.delete(r.id) : n.add(r.id)));
      return n;
    });
  };

  const selectAll = () =>
    isSmellMode
      ? setSelectedIds(new Set(filteredRows.map((r) => r.id)))
      : setSelected(new Set(filtered.map((f) => f.relative_path)));

  const clearAll = () => (isSmellMode ? setSelectedIds(new Set()) : setSelected(new Set()));

  const selectedRows = isSmellMode
    ? smellRows.filter((r) => selectedIds.has(r.id))
    : smellRows.filter((r) => selected.has(r.file));

  const selectedFiles = Array.from(new Set(selectedRows.map((r) => r.file)));
  const selectedSmells = selectedRows.map(({ id, file, smell }) => ({
    id,
    file,
    type: smell.type,
    line: smell.line,
    severity: smell.severity,
    message: smell.message,
    ...(smell.entity ? { entity: smell.entity } : {}),
  }));

  const highCount = selectedSmells.filter((s) => s.severity === "high").length;
  const selectionCount = isSmellMode ? selectedIds.size : selected.size;

  // Every smell of the file being viewed — NOT just the ones passing the
  // current filter. The point of opening the source is to see the file as CUQA
  // reported it; hiding findings because a severity chip is active would show
  // clean code that is not actually clean.
  const viewerFile = viewing?.file || null;
  const viewerSmells = viewerFile
    ? smellRows
        .filter((r) => r.file === viewerFile)
        .map(({ id, smell }) => ({ id, ...smell }))
    : [];
  const viewerLanguage =
    viewerFile ? smellRows.find((r) => r.file === viewerFile)?.language : "";

  // Aggregated on every render rather than fetched: the panel has to move with
  // the checkbox, and this is a sum over records already in memory.
  const impactRecords = impacts ? Array.from(impacts.values()) : null;
  const selectedImpactIds = new Set(selectedRows.map((r) => r.id));
  const impactSummary = impactRecords
    ? summariseSelection(impactRecords, selectedImpactIds, qualityBaseline(sourceReport))
    : null;

  const explainedRecord = explaining ? impacts?.get(explaining) : null;
  const explainedSmell = explaining
    ? smellRows.find((r) => r.id === explaining)?.smell
    : null;

  /** Ask the backend for a budgeted selection, then apply it. */
  const handleOptimise = async (preset) => {
    if (!workflowId || optimising) return;
    setOptimising(true);
    try {
      const result = await optimiseSelection(workflowId, {
        preset,
        budget_minutes: budgetMinutes,
      });
      const ids = new Set(result.selected_ids || []);
      setSelectedIds(ids);
      // The optimiser only ever proposes individual smells, so switching to
      // smell mode is what makes its answer visible and editable.
      setMode("smell");
      setSelected(new Set());
    } catch (error) {
      console.error("Optimise failed:", error);
      alert(error.message);
    } finally {
      setOptimising(false);
    }
  };

  // File mode sends the file paths and lets the backend expand them to every
  // smell inside. Smell mode must NOT send them: the backend ORs files with
  // smells, so a file path would pull back the smells just deselected. It sends
  // the resolved ids instead, with the descriptors as the fallback match.
  const selectionPayload = isSmellMode
    ? {
        selected_ids: Array.from(selectedIds),
        selected_smells: selectedSmells,
        selection_mode: "smell",
      }
    : {
        selected_files: selectedFiles,
        selected_smells: selectedSmells,
        selection_mode: "file",
      };

  const handleApproveSelection = async () => {
    if (selectionCount === 0 || isSubmitting) return;

    if (!workflowId) {
      onProceed?.({ ...selectionPayload, selected_files: selectedFiles });
      return;
    }

    setIsSubmitting(true);
    try {
      // The authoritative projection, recorded alongside the selection. Local
      // aggregation drives the live panel; this is what the audit trail gets.
      if (impacts) {
        analyseSelectionImpact(workflowId, selectionPayload)
          .then((result) => setInteractionNotes(result.interaction_notes || []))
          .catch(() => {});
      }

      const report = await previewSmellSelection(workflowId, {
        ...selectionPayload,
        feedback: {
          reason: isSmellMode
            ? "Smell-wise selection in CodeSmellApprovalPage"
            : "File-wise selection in CodeSmellApprovalPage",
        },
      });
      onProceed?.(report);
    } catch (error) {
      console.error("Smell approval failed:", error);
      alert(error.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Loading / error / empty states ─────────────────────────────────────────
  if (loading && !sourceReport) {
    return <LoadingState />;
  }

  if (!sourceReport) {
    return (
      <ErrorState
        error={loadError}
        onRetry={reloadReport}
        onUseSample={() => {
          setUseSample(true);
          setLoadError(null);
        }}
      />
    );
  }

  const summary = sourceReport.summary || {};

  return (
    <div>
      <SourceBanner
        origin={origin}
        report={sourceReport}
        meta={fetched}
        loading={loading}
        error={loadError}
        onRefresh={reloadReport}
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 24 }}>
        {[
          { label: "Files Analyzed", val: num(summary.files_analyzed), color: C.accent },
          { label: "Total Smells", val: num(summary.total_code_smells), color: C.warn },
          { label: "High Severity", val: num(summary.smell_severity?.high), color: C.danger },
          { label: "Avg Quality", val: `${round(summary.average_quality_score, 1)}%`, color: C.accent },
        ].map(({ label, val, color }) => (
          <Card key={label} style={{ textAlign: "center", padding: "16px" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color, fontFamily: "monospace" }}>{val}</div>
            <div style={{ fontSize: 11, color: C.textMuted, marginTop: 4, textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
          </Card>
        ))}
      </div>

      <ModeSwitch
        mode={mode}
        onChange={setMode}
        fileCount={filesWithSmells.length}
        smellCount={smellRows.length}
      />

      <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={isSmellMode ? "Search smells, files, entities…" : "Search files or smell types..."}
          style={{ flex: 1, minWidth: 200, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 14px", color: C.text, fontSize: 13, outline: "none" }}
        />
        {["all", "high", "medium", "low"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", border: "none", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted,
          }}>{f}</button>
        ))}
        <button onClick={selectAll} style={{ padding: "7px 14px", borderRadius: 8, fontSize: 12, background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer" }}>Select All</button>
        <button onClick={clearAll} style={{ padding: "7px 14px", borderRadius: 8, fontSize: 12, background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer" }}>Clear</button>
      </div>

      {/* The cards are flex items in a height-capped column, so they must not
          shrink: a file with many smells would otherwise be squeezed and — with
          the group's overflow:hidden — have its last rows clipped instead of
          scrolled. Every group keeps its natural height and this list scrolls.
          Smell mode gets a taller viewport, since one file can be many rows. */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 10,
        maxHeight: isSmellMode ? "min(72vh, 760px)" : 420,
        overflowY: "auto", paddingRight: 4,
      }}>
        {(isSmellMode ? filteredRows.length : filtered.length) === 0 && (
          <div style={{ padding: "28px 20px", textAlign: "center", background: C.panel, border: `1px dashed ${C.border}`, borderRadius: 10, color: C.textMuted, fontSize: 13, flexShrink: 0 }}>
            {filesWithSmells.length === 0
              ? "The CUQA agent reported no code smells in this workspace — nothing to refactor."
              : isSmellMode
                ? "No smells match the current search / severity filter."
                : "No files match the current search / severity filter."}
          </div>
        )}

        {isSmellMode && groups.map(group => (
          <SmellGroup
            key={group.file}
            group={group}
            selectedIds={selectedIds}
            onToggleSmell={toggleSmell}
            onToggleGroup={toggleGroup}
            onViewFile={(file) => setViewing({ file, focusId: null })}
            onViewSmell={(file, id) => setViewing({ file, focusId: id })}
            impacts={impacts}
            onExplain={setExplaining}
          />
        ))}

        {!isSmellMode && filtered.map(f => {
          const isSelected = selected.has(f.relative_path);
          const smells = f.code_smells || [];
          const hasHigh = smells.some(s => s.severity === "high");
          const metrics = f.metrics || {};
          return (
            <div key={f.relative_path} onClick={() => toggleFile(f.relative_path)} style={{
              background: isSelected ? `${C.accent}0d` : C.panel,
              border: `1px solid ${isSelected ? C.accent : C.border}`,
              borderRadius: 10, overflow: "hidden", cursor: "pointer", flexShrink: 0,
              transition: "all 0.2s", boxShadow: isSelected ? `0 0 12px ${C.accentGlow}` : "none"
            }}>
              <FilePathBar
                file={f.relative_path}
                leading={<Checkbox on={isSelected} size={20} />}
              >
                <Badge label={f.language || "unknown"} color={C.info} />
                {hasHigh && <Badge label="HIGH SEVERITY" color={C.danger} />}
                <span style={{ marginLeft: "auto", fontSize: 11, color: C.warn, flexShrink: 0 }}>
                  {smells.length} smell{smells.length > 1 ? "s" : ""}
                </span>
                <ViewButton
                  label="View source"
                  onClick={() => setViewing({ file: f.relative_path, focusId: null })}
                />
              </FilePathBar>

              <div style={{ padding: "12px 18px" }}>
                <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 11, color: C.textMuted }}>{num(metrics.lines_of_code)} LOC</span>
                  <span style={{ fontSize: 11, color: C.textMuted }}>{num(metrics.functions)} functions</span>
                  <span style={{ fontSize: 11, color: num(f.quality_score) >= 95 ? C.accent : C.warn }}>Quality: {round(f.quality_score, 1)}%</span>
                </div>
                {isSelected && (
                  <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.borderAcc}` }}>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {smells.map((smell, idx) => (
                        <div
                          key={`${smell.type}-${smell.line ?? "x"}-${idx}`}
                          title={`${smell.message || smell.type}\n\nClick to open this smell in the original file`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setViewing({
                              file: f.relative_path,
                              focusId: smellId(f.relative_path, smell, idx),
                            });
                          }}
                          style={{ background: `${severityColor(smell.severity)}10`, border: `1px solid ${severityColor(smell.severity)}30`, borderRadius: 6, padding: "4px 10px", display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}
                        >
                          <div style={{ width: 6, height: 6, borderRadius: "50%", background: severityColor(smell.severity) }} />
                          <span style={{ fontSize: 11, color: C.textSub }}>{smell.type}</span>
                          {smell.entity && <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace" }}>{smell.entity}</span>}
                          {smell.line ? <span style={{ fontSize: 10, color: C.textMuted }}>L{smell.line}</span> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {isSmellMode && filteredRows.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted }}>
          Showing all {filteredRows.length} smell{filteredRows.length > 1 ? "s" : ""} across {groups.length} file{groups.length > 1 ? "s" : ""}
          {filteredRows.length < smellRows.length && ` (${smellRows.length - filteredRows.length} filtered out)`} — scroll the list for the rest.
        </div>
      )}

      {hiddenCount > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted }}>
          {hiddenCount} analysed file{hiddenCount > 1 ? "s" : ""} with no detected smells {hiddenCount > 1 ? "are" : "is"} hidden.
        </div>
      )}

      <div style={{ marginTop: 20, padding: "16px 20px", background: selectionCount > 0 ? `${C.accent}0a` : C.panel, border: `1px solid ${selectionCount > 0 ? C.accent : C.border}`, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          {selectionCount > 0 ? (
            <span style={{ fontSize: 13, color: C.text }}>
              {isSmellMode ? (
                <>
                  <span style={{ color: C.accent, fontWeight: 700 }}>{selectedSmells.length}</span> smell{selectedSmells.length > 1 ? "s" : ""} selected ·{" "}
                  <span style={{ color: C.warn, fontWeight: 700 }}>{selectedFiles.length}</span> file{selectedFiles.length > 1 ? "s" : ""} affected ·{" "}
                </>
              ) : (
                <>
                  <span style={{ color: C.accent, fontWeight: 700 }}>{selected.size}</span> file{selected.size > 1 ? "s" : ""} selected ·{" "}
                  <span style={{ color: C.warn, fontWeight: 700 }}>{selectedSmells.length}</span> smells ·{" "}
                </>
              )}
              {highCount > 0 && <span style={{ color: C.danger, fontWeight: 700 }}>{highCount} high severity</span>}
            </span>
          ) : (
            <span style={{ fontSize: 13, color: C.textMuted }}>
              {isSmellMode
                ? "Select individual code smells to proceed to the Refactoring Plan Agent"
                : "Select files with code smells to proceed to the Refactoring Plan Agent"}
            </span>
          )}
        </div>
        <button onClick={handleApproveSelection} disabled={selectionCount === 0 || isSubmitting} style={{
          padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: selectionCount > 0 ? "pointer" : "not-allowed",
          background: selectionCount > 0 ? C.accent : C.border, color: selectionCount > 0 ? "#000" : C.textMuted, border: "none",
          boxShadow: selectionCount > 0 ? `0 0 20px ${C.accentGlow}` : "none", transition: "all 0.2s"
        }}>
          {isSubmitting ? "Generating report..." : "Approve Selected Smells →"}
        </button>
      </div>

      {impactSummary && (
        <TradeOffPanel
          summary={impactSummary}
          interactionNotes={interactionNotes}
          optimising={optimising}
          onOptimise={workflowId ? handleOptimise : undefined}
          budgetMinutes={budgetMinutes}
          onBudgetChange={setBudgetMinutes}
        />
      )}

      {explainedRecord && (
        <ImpactDrawer
          record={explainedRecord}
          smell={explainedSmell}
          isSelected={selectedIds.has(explaining)}
          onToggleSmell={isSmellMode ? toggleSmell : undefined}
          onClose={() => setExplaining(null)}
        />
      )}

      {viewerFile && (
        <SourceViewer
          // Keyed by file: opening a different file mounts a fresh viewer, so
          // its loading state resets without an effect writing state.
          key={viewerFile}
          file={viewerFile}
          language={viewerLanguage}
          smells={viewerSmells}
          focusId={viewing?.focusId || null}
          // Smell mode selects individual smells, so the viewer can tick them
          // too. File mode selects whole files — offering a per-smell tick
          // there would suggest a partial selection the mode cannot express.
          selectedIds={isSmellMode ? selectedIds : null}
          onToggleSmell={isSmellMode ? toggleSmell : undefined}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

/** The eye affordance. Stops propagation so opening a file never also toggles
 *  its selection — the row and its header are both click targets already. */
function ViewButton({ label = "View", onClick, compact = false }) {
  return (
    <button
      title="Open the original file with the code smells marked"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      style={{
        display: "flex", alignItems: "center", gap: 5, flexShrink: 0,
        padding: compact ? "2px 8px" : "5px 12px",
        borderRadius: 7, cursor: "pointer",
        fontSize: compact ? 10 : 11, fontWeight: 700,
        background: C.panel, color: C.textSub, border: `1px solid ${C.border}`,
      }}
    >
      <span aria-hidden="true">👁</span>
      {label}
    </button>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

/** Selection box. `half` draws the partial state a file gets when only some of
 *  its smells are ticked. */
function Checkbox({ on, half = false, size = 18 }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 4, flexShrink: 0,
      border: `2px solid ${on || half ? C.accent : C.border}`,
      background: on ? C.accent : "transparent",
      display: "flex", alignItems: "center", justifyContent: "center",
      transition: "all 0.2s",
    }}>
      {on && <span style={{ color: "#000", fontSize: size * 0.6, fontWeight: 900 }}>✓</span>}
      {!on && half && <div style={{ width: size / 2, height: 2, background: C.accent }} />}
    </div>
  );
}

/** The file path header both modes share: the path called out by name, over a
 *  blue border that separates one file's block from the next. */
function FilePathBar({ file, leading, sticky = false, onClick, children }) {
  return (
    <div onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 10, padding: "10px 16px",
      background: `${FILE_BAR_COLOR}12`,
      borderBottom: `2px solid ${FILE_BAR_COLOR}`,
      borderLeft: `4px solid ${FILE_BAR_COLOR}`,
      ...(onClick ? { cursor: "pointer" } : {}),
      // Sticky must sit directly on the scrolling list's descendant chain — a
      // wrapper sized to the bar itself would leave it nothing to travel in.
      ...(sticky ? { position: "sticky", top: 0, zIndex: 1 } : {}),
    }}>
      {leading}
      <span style={{
        fontSize: 18, fontWeight: 700, color: FILE_BAR_COLOR, letterSpacing: 1,
        textTransform: "uppercase", flexShrink: 0,
      }}>
        File Path
      </span>
      <span
        title={file}
        style={{
          fontSize: 18, fontWeight: 700, color: C.text, fontFamily: "monospace",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          minWidth: 0, flexShrink: 1,
        }}
      >
        {file}
      </span>
      {children}
    </div>
  );
}

const MODES = [
  {
    key: "file",
    label: "File wise",
    icon: "📁",
    hint: "Pick whole files — every smell in a selected file is sent to the Refactoring Plan Agent.",
  },
  {
    key: "smell",
    label: "Smell wise",
    icon: "🔎",
    hint: "Pick individual smells — only the smells you tick are sent, so a file can be partially refactored.",
  },
];

function ModeSwitch({ mode, onChange, fileCount, smellCount }) {
  const active = MODES.find((m) => m.key === mode) || MODES[0];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>
          Selection mode
        </span>
        <div style={{ display: "flex", gap: 4, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 4 }}>
          {MODES.map((m) => {
            const isActive = m.key === mode;
            return (
              <button
                key={m.key}
                onClick={() => onChange(m.key)}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "7px 16px", borderRadius: 7, border: "none", cursor: "pointer",
                  fontSize: 12, fontWeight: 700, transition: "all 0.2s",
                  background: isActive ? C.accent : "transparent",
                  color: isActive ? "#000" : C.textMuted,
                  boxShadow: isActive ? `0 0 14px ${C.accentGlow}` : "none",
                }}
              >
                <span>{m.icon}</span>
                {m.label}
                <span style={{ fontSize: 10, fontWeight: 600, opacity: 0.75 }}>
                  ({m.key === "file" ? fileCount : smellCount})
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div style={{ fontSize: 11, color: C.textMuted, marginTop: 8 }}>{active.hint}</div>
    </div>
  );
}

/** One file's smells in smell-wise mode: a header that toggles the whole file,
 *  then a checkbox per individual smell.
 *
 *  The card is never height-capped — it grows with the number of smells, so all
 *  10 rows of a 10-smell file are laid out one under the other. flexShrink: 0
 *  keeps the parent's fixed-height column from squeezing (and clipping) it. */
function SmellGroup({ group, selectedIds, onToggleSmell, onToggleGroup, onViewFile,
                     onViewSmell, impacts, onExplain }) {
  const selectedInGroup = group.rows.filter((r) => selectedIds.has(r.id)).length;
  const total = group.rows.length;
  const allOn = selectedInGroup === total;
  const someOn = selectedInGroup > 0 && !allOn;

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${selectedInGroup > 0 ? C.accent : C.border}`,
      borderRadius: 10, overflow: "hidden", flexShrink: 0,
      boxShadow: selectedInGroup > 0 ? `0 0 12px ${C.accentGlow}` : "none",
      transition: "all 0.2s",
    }}>
      {/* Sticky so the file a row belongs to stays visible while scrolling
          through a long smell list. */}
      <FilePathBar
        file={group.file}
        leading={<Checkbox on={allOn} half={someOn} />}
        onClick={() => onToggleGroup(group)}
        sticky
      >
        <Badge label={group.language || "unknown"} color={C.info} />
        <Badge label={`${total} smell${total > 1 ? "s" : ""}`} color={C.warn} />
        <span style={{ marginLeft: "auto", fontSize: 11, color: selectedInGroup > 0 ? C.accent : C.textMuted, flexShrink: 0 }}>
          {selectedInGroup}/{total} selected
        </span>
        <ViewButton label="View source" onClick={() => onViewFile?.(group.file)} />
      </FilePathBar>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {group.rows.map(({ id, smell }, rowIdx) => {
          const isSelected = selectedIds.has(id);
          const color = severityColor(smell.severity);
          return (
            <div
              key={id}
              onClick={() => onToggleSmell(id)}
              style={{
                display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 16px",
                cursor: "pointer", flexShrink: 0,
                background: isSelected ? `${C.accent}0a` : "transparent",
                borderTop: rowIdx > 0 ? `1px solid ${C.border}` : "none",
                transition: "background 0.2s",
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: 4, marginTop: 2, flexShrink: 0,
                border: `2px solid ${isSelected ? C.accent : C.border}`,
                background: isSelected ? C.accent : "transparent",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                {isSelected && <span style={{ color: "#000", fontSize: 10, fontWeight: 900 }}>✓</span>}
              </div>

              <span style={{
                fontSize: 10, color: C.textMuted, fontFamily: "monospace", marginTop: 3,
                minWidth: 18, textAlign: "right", flexShrink: 0,
              }}>
                {rowIdx + 1}.
              </span>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <div style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, fontWeight: 700, color: isSelected ? C.text : C.textSub }}>{smell.type}</span>
                  <Badge label={smell.severity || "unknown"} color={color} />
                  {smell.entity && (
                    <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>{smell.entity}</span>
                  )}
                  {smell.line ? <span style={{ fontSize: 10, color: C.textMuted }}>L{smell.line}</span> : null}
                </div>
                {smell.message && (
                  <div style={{ fontSize: 11, color: C.textMuted, marginTop: 4, lineHeight: 1.5 }}>{smell.message}</div>
                )}
                {impacts?.get(id) && (
                  <div style={{ marginTop: 6 }}>
                    <ImpactChip
                      record={impacts.get(id)}
                      compact
                      onExplain={() => onExplain?.(id)}
                    />
                  </div>
                )}
              </div>

              <ViewButton compact onClick={() => onViewSmell?.(group.file, id)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

const VIA_LABEL = {
  "diwo-proxy": "via DIWO backend",
  "cuqa-direct": "direct from CUQA agent",
};

function SourceBanner({ origin, report, meta, loading, error, onRefresh }) {
  const isSample = origin === "sample";
  const color = isSample ? C.warn : origin === "workflow" ? C.info : C.accent;

  const label =
    isSample
      ? "Sample report (bundled data — CUQA agent unavailable)"
      : origin === "workflow"
        ? "Report from the current DIWO workflow"
        : "Live CUQA quality report";

  const details = [];
  if (report?.repo_name) details.push(report.repo_name);
  if (report?.report_type) details.push(report.report_type);
  if (meta?.cuqaUrl && !isSample && origin === "cuqa") details.push(meta.cuqaUrl);
  if (meta?.via && VIA_LABEL[meta.via]) details.push(VIA_LABEL[meta.via]);

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
      marginBottom: 16, padding: "10px 16px", borderRadius: 10,
      background: `${color}0a`, border: `1px solid ${color}40`, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: `0 0 8px ${color}`, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{label}</div>
          {details.length > 0 && (
            <div style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis" }}>
              {details.join(" · ")}
            </div>
          )}
          {error && (
            <div style={{ fontSize: 11, color: C.danger, marginTop: 2 }}>Refresh failed: {error.message}</div>
          )}
        </div>
      </div>
      <button
        onClick={onRefresh}
        disabled={loading}
        style={{
          padding: "6px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600,
          background: C.panel, color: loading ? C.textMuted : C.textSub,
          border: `1px solid ${C.border}`, cursor: loading ? "wait" : "pointer", flexShrink: 0,
        }}
      >
        {loading ? "Analyzing…" : isSample ? "↻ Retry CUQA" : "↻ Re-analyze"}
      </button>
    </div>
  );
}

function LoadingState() {
  return (
    <Card style={{ textAlign: "center", padding: "48px 24px" }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 8 }}>
        Requesting quality report from the CUQA agent…
      </div>
      <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 20 }}>
        POST /api/quality-report · analysing the loaded workspace (up to 50 files)
      </div>
      <div style={{ height: 4, borderRadius: 4, background: C.border, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: C.gradient, animation: "diwoSlide 1.1s ease-in-out infinite" }} />
      </div>
      <style>{`@keyframes diwoSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }`}</style>
    </Card>
  );
}

function ErrorState({ error, onRetry, onUseSample }) {
  const status = error?.status;
  const noRepo = status === 400 || status === 404;

  const hint = noRepo
    ? "The CUQA agent is running but has no repository loaded. Open the CUQA UI and load/upload a repository, then re-analyze."
    : "Start the CUQA agent before running the DIWO workflow:  cd agents/cuqa_agent/src && uvicorn main:app --port 8080";

  return (
    <Card style={{ padding: "32px 28px", borderColor: `${C.danger}50` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <Badge label={noRepo ? "NO REPOSITORY" : "CUQA UNAVAILABLE"} color={C.danger} />
        <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
          Could not load the code smell report
        </span>
      </div>

      <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.6, marginBottom: 12 }}>
        {error?.message || "Unknown error while contacting the CUQA agent."}
      </div>

      <div style={{
        fontSize: 11, color: C.textMuted, fontFamily: "monospace", lineHeight: 1.6,
        background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px", marginBottom: 18,
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button onClick={onRetry} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 700,
          background: C.accent, color: "#000", border: "none", cursor: "pointer",
          boxShadow: `0 0 16px ${C.accentGlow}`,
        }}>
          ↻ Retry
        </button>
        <button onClick={onUseSample} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer",
        }}>
          Continue with sample data
        </button>
      </div>
    </Card>
  );
}
