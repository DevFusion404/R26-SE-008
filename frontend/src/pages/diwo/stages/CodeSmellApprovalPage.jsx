/**
 * CodeSmellApprovalPage.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 1 of the DIWO workflow: render the CUQA agent's quality report and let
 * the developer choose which findings continue to the Refactoring Planning
 * Agent.
 *
 * Data source, in priority order:
 *   1. `reportData` prop — the report the parent already holds (the workflow's
 *      filtered report, or the one returned by POST /workflows/from-cuqa).
 *   2. Live CUQA report — POST /api/cuqa/quality-report on the DIWO backend,
 *      which proxies the CUQA agent's own quality-report endpoint. The browser
 *      never calls CUQA itself: every agent hand-off goes through the
 *      orchestration agent, so there is one base URL and one place where the
 *      contract with Agent 1 can drift.
 *   3. Bundled sample report (diwoData.CUQA_DATA) — only if the developer opts
 *      in after a failure, and it is labelled as sample data in the UI.
 *
 * THREE VIEWS OF ONE SET OF FINDINGS
 * ----------------------------------
 *     File wise      file -> the smell types inside it     (whole-file select)
 *     Smell wise     smell type, repository-wide -> occurrences
 *     Category wise  CUQA category -> smell type -> occurrences
 *
 * Smell wise and Category wise both write to `selectedIds`, so switching
 * between them never loses a tick. File wise selects whole paths into
 * `selected` and shows no per-occurrence checkbox — offering one would promise
 * a partial-file selection that the mode cannot express or send.
 *
 * The arrangement that mattered most: Smell wise groups by TYPE across the
 * whole repository. It used to group by file first, so a Magic Number occurring
 * 53 times across 5 files produced five separate "Magic Number" groups and no
 * way to act on all of them at once. It is now one row reading
 * "53 findings · 5 files" — two different facts, kept as two numbers.
 *
 * Every count is derived in utils/smellGrouping from the rows already in
 * memory. Nothing on this page recounts, and no number here is hardcoded.
 *
 * Selection impact
 * ----------------
 * Every occurrence carries what picking it buys and what skipping it costs,
 * from GET /workflows/<id>/smell-impacts. The records are selection-independent,
 * so they are fetched once and aggregated locally on each click — the panel has
 * to react on the same frame as the checkbox. The backend is still the
 * authority: POST /selection-impact is called when the developer proceeds, and
 * that projection is what reaches the audit trail.
 *
 * Capability (auto-fixable vs advisory) comes from `capability.status` and
 * never from severity: ten of the highest-severity smell types CUQA reports
 * map to refactorings SCTVA cannot perform.
 *
 * Every finding carries a one-line impact with no interaction at all —
 * capability, points, risk, effort — and an Impact button that opens the full
 * counterfactual in a DIALOG. The full panel is not tied to the checkbox:
 * unfolding twenty of them underneath twenty ticks turned a list of decisions
 * into a wall of figures, and made the list impossible to scan for the one
 * finding still being weighed.
 *
 * All of it degrades. No impact records means no chips, no capability tags and
 * no effort figures — not zeroes. No taxonomy means no category overview and no
 * category dropdown. Selection keeps working in every case.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CUQA_DATA } from "../data/diwoData";
import { C, Card, Badge } from "../diwoTheme.jsx";
import SourceViewer from "../components/SourceViewer.jsx";
import ImpactDrawer from "../components/ImpactDrawer.jsx";
import TradeOffPanel from "../components/TradeOffPanel.jsx";
import SmellCategoryOverview from "../components/SmellCategoryOverview.jsx";
import QuickSelectDropdown from "../components/QuickSelectDropdown.jsx";
import SelectionSummaryPanel from "../components/SelectionSummaryPanel.jsx";
import {
  CategoryWiseView, FileWiseView, SmellWiseView,
} from "../components/SmellReviewViews.jsx";
import {
  CodeSmellStats, ModeSwitch, SearchAndSeverity,
} from "../components/CodeSmellToolbar.jsx";
import { DEFAULT_BUDGET_MINUTES } from "../utils/impactPresets";
import { qualityBaseline, summariseSelection } from "../utils/impactSummary";
import {
  categoryOptions, expandAllKeys, groupByCategory, groupByFile, groupBySmellType,
  selectionSummary, smellTypeOptions,
} from "../utils/smellGrouping";
import {
  analyseSelectionImpact, fetchProjectStructure, fetchQualityReport,
  fetchSmellCategories, fetchSmellImpacts, optimiseSelection,
  previewSmellSelection,
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

export default function CodeSmellApprovalPage({
  onProceed,
  workflowId,
  reportData,
  onReportLoaded,
  onReloadWorkspace,
}) {
  // ── Review arrangement ─────────────────────────────────────────────────────
  const [mode, setMode] = useState("smell");
  const [selected, setSelected] = useState(new Set());          // file paths
  const [selectedIds, setSelectedIds] = useState(new Set());    // smell ids

  // Four independent filter axes. Deliberately not one field: "the Bloaters I
  // have not triaged" and "every high-severity Magic Number" are ordinary
  // queries, and a single state variable cannot express either.
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [smellTypeFilter, setSmellTypeFilter] = useState("all");

  // Accordion state, one Set for all three views — keys are namespaced by view
  // so they cannot collide. `autoOpen` keeps the first group open until the
  // developer expands or collapses something themselves.
  const [openKeys, setOpenKeys] = useState(() => new Set());
  const [autoOpen, setAutoOpen] = useState(true);

  const [isSubmitting, setIsSubmitting] = useState(false);
  // Which half of the hand-off is running: "filtering" (fast, local to the
  // backend) or "planning" (the RDP agent, which is the one worth waiting on).
  const [submitPhase, setSubmitPhase] = useState(null);

  // Which file's original source is open, and which smell inside it is focused.
  const [viewing, setViewing] = useState(null);
  // The finding whose impact dialog is open. The ROW, not the id: the dialog
  // shows the entity name, which lives on the smell and not on the record.
  const [impactRow, setImpactRow] = useState(null);

  // ── Selection impact ───────────────────────────────────────────────────────
  const [impacts, setImpacts] = useState(null);
  const [optimising, setOptimising] = useState(false);
  const [budgetMinutes, setBudgetMinutes] = useState(DEFAULT_BUDGET_MINUTES);
  const [interactionNotes, setInteractionNotes] = useState([]);

  // ── CUQA report loading ────────────────────────────────────────────────────
  const [fetched, setFetched] = useState(null);   // { report, via, cuqaUrl, ... }
  const [loading, setLoading] = useState(!reportData);
  const [loadError, setLoadError] = useState(null);
  const [useSample, setUseSample] = useState(false);
  // What CUQA currently holds, from the cheap project-structure probe.
  const [workspace, setWorkspace] = useState(null);
  // The orchestrator's grouping of this workflow's smells.
  const [taxonomy, setTaxonomy] = useState(null);

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
    setImpactRow(null);
    setInteractionNotes([]);
    onReportLoadedRef.current?.(result);
  }, []);

  const applyLoadError = useCallback((error) => {
    if (error.name === "AbortError") return;   // unmounted / superseded
    setFetched(null);
    setLoadError(error);
    setLoading(false);
  }, []);

  /**
   * Re-analyze / load the repository CUQA currently holds.
   *
   * This RE-SEEDS THE WORKFLOW when the parent can (`onReloadWorkspace`), and
   * only falls back to a display-only fetch when it cannot. CUQA replaces its
   * workspace on every upload, so refreshing only this page would show
   * repository B against a workflow still holding repository A.
   */
  const reloadReport = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      if (onReloadWorkspace) {
        await onReloadWorkspace();
        setFetched(null);
        setUseSample(false);
        setLoading(false);
        return;
      }
      applyReport(await fetchQualityReport());
    } catch (error) {
      applyLoadError(error);
    }
  }, [onReloadWorkspace, applyReport, applyLoadError]);

  // A report handed down by the parent is always the newer truth. Adjusted
  // during render rather than in an effect — no cascading render, no stale
  // first paint.
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
      setImpactRow(null);
      setImpacts(null);
      setInteractionNotes([]);
    }
  }

  // Initial load. `loading` already starts as !reportData.
  useEffect(() => {
    if (reportData) return undefined;
    const controller = new AbortController();
    fetchQualityReport({ signal: controller.signal }).then(applyReport).catch(applyLoadError);
    return () => controller.abort();
  }, [reportData, applyReport, applyLoadError]);

  /** Which repository CUQA holds right now — cheap, no re-analysis. */
  const probeWorkspace = useCallback((signal) => {
    fetchProjectStructure({ signal })
      .then((structure) => {
        setWorkspace({
          repo_name: structure?.repo_name || null,
          source: structure?.source || null,
          total_source_files: structure?.total_source_files ?? null,
        });
      })
      .catch(() => setWorkspace(null));
  }, []);

  // Probed on mount and on focus — when the developer returns from uploading.
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      await Promise.resolve();
      if (!cancelled) probeWorkspace(controller.signal);
    })();

    const onFocus = () => probeWorkspace();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);

    return () => {
      cancelled = true;
      controller.abort();
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [probeWorkspace]);

  // The category taxonomy, from the ORCHESTRATOR — never from CUQA directly.
  useEffect(() => {
    if (!workflowId) return undefined;
    const controller = new AbortController();
    fetchSmellCategories(workflowId, { signal: controller.signal })
      .then(setTaxonomy)
      .catch(() => setTaxonomy(null));
    return () => controller.abort();
  }, [workflowId]);

  // Impact records depend only on the workflow's smells, so this runs once per
  // workflow. A failure is swallowed on purpose.
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
  const sourceReport = fetched?.report || reportData || (useSample ? CUQA_DATA : null);
  const origin = fetched ? "cuqa" : reportData ? "workflow" : useSample ? "sample" : null;

  const shownRepo = sourceReport?.repo_name || null;
  const liveRepo = workspace?.repo_name || null;
  const staleWorkspace =
    origin !== "sample" && shownRepo && liveRepo && shownRepo !== liveRepo
      ? { shown: shownRepo, live: liveRepo, files: workspace?.total_source_files }
      : null;

  const allFiles = useMemo(() => sourceReport?.files || [], [sourceReport]);
  const filesWithSmells = useMemo(
    () => allFiles.filter((f) => (f.code_smells || []).length > 0),
    [allFiles]
  );
  const hiddenCount = allFiles.length - filesWithSmells.length;

  // Every smell in the report, flattened once and tagged with the id the
  // backend will resolve it by. All three views read from this list.
  const smellRows = useMemo(() => filesWithSmells.flatMap((f) =>
    (f.code_smells || []).map((smell, idx) => ({
      id: smellId(f.relative_path, smell, idx),
      file: f.relative_path,
      language: f.language,
      smell,
    }))
  ), [filesWithSmells]);

  // type -> category, from the backend taxonomy. Used only to fill in a smell
  // the rendered report did not carry a category on; CUQA stamps them, so this
  // is the degraded path.
  const categoryByType = useMemo(
    () => new Map((taxonomy?.types || []).map((row) => [row.type, row.category])),
    [taxonomy]
  );
  const categoryOf = useCallback(
    (row) => row.smell?.category || categoryByType.get(row.smell?.type) || "Uncategorized",
    [categoryByType]
  );
  const categoryOrder = useMemo(
    () => (taxonomy?.categories || []).map((c) => c.category),
    [taxonomy]
  );
  const categoryPriority = useMemo(
    () => new Map((taxonomy?.categories || []).map((c) => [c.category, c.priority])),
    [taxonomy]
  );

  // ── Filtering pipeline ─────────────────────────────────────────────────────
  // search -> severity -> category -> smell type. Filters change what is
  // VISIBLE and never what is selected: a finding hidden by a filter keeps its
  // tick, because a filter is a way of looking, not a way of deciding.
  const term = search.trim().toLowerCase();
  const visibleRows = useMemo(() => smellRows.filter((row) => {
    const { smell, file } = row;
    const matchesSearch = term === ""
      || file.toLowerCase().includes(term)
      || (smell.type || "").toLowerCase().includes(term)
      || (smell.entity || "").toLowerCase().includes(term)
      || (smell.message || "").toLowerCase().includes(term)
      || categoryOf(row).toLowerCase().includes(term);
    const matchesSeverity = severity === "all" || smell.severity === severity;
    const matchesCategory = categoryFilter === "all" || categoryOf(row) === categoryFilter;
    const matchesType = smellTypeFilter === "all" || smell.type === smellTypeFilter;
    return matchesSearch && matchesSeverity && matchesCategory && matchesType;
  }), [smellRows, term, severity, categoryFilter, smellTypeFilter, categoryOf]);

  // ── Grouping, one per view ─────────────────────────────────────────────────
  const smellGroups = useMemo(
    () => groupBySmellType(visibleRows, { impacts, selectedIds }),
    [visibleRows, impacts, selectedIds]
  );
  const catGroups = useMemo(
    () => groupByCategory(visibleRows, { impacts, selectedIds, categoryOf, order: categoryOrder }),
    [visibleRows, impacts, selectedIds, categoryOf, categoryOrder]
  );
  const fileGroups = useMemo(
    () => groupByFile(visibleRows, { impacts, selectedIds, selectedFiles: selected }),
    [visibleRows, impacts, selectedIds, selected]
  );

  // Dropdown options are built from ALL rows, not the filtered ones: the
  // dropdown is how a developer reaches something the current filter hides.
  const typeOptions = useMemo(
    () => smellTypeOptions(smellRows, { impacts, selectedIds }),
    [smellRows, impacts, selectedIds]
  );
  const catOptions = useMemo(
    () => categoryOptions(smellRows, { impacts, selectedIds, categoryOf, order: categoryOrder }),
    [smellRows, impacts, selectedIds, categoryOf, categoryOrder]
  );

  const isFileMode = mode === "file";
  const activeGroups = isFileMode ? fileGroups : mode === "category" ? catGroups : smellGroups;

  // Keep the first group open until the developer touches an accordion.
  const effectiveOpen = useMemo(() => {
    if (!autoOpen || activeGroups.length === 0) return openKeys;
    const next = new Set(openKeys);
    next.add(activeGroups[0].key);
    return next;
  }, [autoOpen, activeGroups, openKeys]);

  /**
   * Expand or collapse one group.
   *
   * Two SEPARATE state updates, and the openKeys updater is pure. It used to
   * call setOpenKeys from inside the setAutoOpen updater, which React invokes
   * twice under StrictMode — so every expand toggled the key on and straight
   * back off, and the button did nothing at all.
   *
   * `autoOpen` is read from the closure rather than from an updater argument:
   * on the first interaction the auto-opened group is folded into the real set,
   * so collapsing the default group actually closes it instead of having the
   * next render put it back.
   */
  const toggleOpen = useCallback((key) => {
    setOpenKeys((prev) => {
      const next = new Set(prev);
      if (autoOpen && activeGroups[0]) next.add(activeGroups[0].key);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setAutoOpen(false);
  }, [autoOpen, activeGroups]);

  /**
   * Open every group in the current view, all the way down.
   *
   * `expandAllKeys` composes the keys the same way the views do, so this opens
   * the nested levels too — an "Expand all" that opened only the headings
   * would be a worse version of clicking them.
   *
   * Scoped to the ACTIVE view: openKeys is shared across all three, and keys
   * for a view that is not on screen would be invisible state the Hide all
   * button then appears to do nothing about.
   */
  const expandAll = useCallback(() => {
    setOpenKeys(expandAllKeys(mode, activeGroups));
    setAutoOpen(false);
  }, [mode, activeGroups]);

  /** Collapse everything, including the group auto-opened on first paint. */
  const collapseAll = useCallback(() => {
    setOpenKeys(new Set());
    setAutoOpen(false);
  }, []);

  // ── Selection ──────────────────────────────────────────────────────────────
  const toggleFile = (path) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const toggleSmell = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /**
   * Tick or untick a whole group — a smell type, a category, or a category's
   * type. Every level in every view routes through here, so there is one
   * selection store and the dropdowns update on the same frame as the tables.
   */
  const toggleRows = useCallback((rows) => {
    const allOn = rows.length > 0 && rows.every((r) => selectedIds.has(r.id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      rows.forEach((r) => (allOn ? next.delete(r.id) : next.add(r.id)));
      return next;
    });
  }, [selectedIds]);

  /**
   * Select everything the current filters show.
   *
   * Scoped to the visible set on purpose: a "select all" that reached past the
   * filters would hand planning findings the developer had deliberately
   * filtered out and never saw.
   */
  const selectAll = () => {
    if (isFileMode) {
      setSelected(new Set(fileGroups.map((g) => g.file)));
      return;
    }
    setSelectedIds((prev) => {
      const next = new Set(prev);
      visibleRows.forEach((r) => next.add(r.id));
      return next;
    });
  };

  /**
   * Reject everything currently selected.
   *
   * In this stage a rejected finding is simply one that is not forwarded —
   * there is no separate reject list, because /select-smells is told what to
   * plan, not what to ignore. So this clears the selection, and the count in
   * the footer is the whole record of the decision.
   */
  const rejectAll = () => (isFileMode ? setSelected(new Set()) : setSelectedIds(new Set()));

  const selectedRows = isFileMode
    ? smellRows.filter((r) => selected.has(r.file))
    : smellRows.filter((r) => selectedIds.has(r.id));

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

  const selectionCount = isFileMode ? selected.size : selectedIds.size;

  // Optional-chained on purpose. Every derived value above runs BEFORE the
  // loading/error guards below, so on the first paint — report not fetched yet —
  // `sourceReport` is still null. A bare `sourceReport.summary` here threw
  // during render, and with no error boundary above it that blanked the entire
  // app, not just this stage.
  const summary = sourceReport?.summary || {};
  const panelSummary = selectionSummary(selectedRows, {
    impacts,
    totalFindings: smellRows.length,
  });

  // Every smell of the file being viewed — NOT just the ones passing the
  // current filter. The point of opening the source is to see the file as CUQA
  // reported it.
  const viewerFile = viewing?.file || null;
  const viewerSmells = viewerFile
    ? smellRows.filter((r) => r.file === viewerFile).map(({ id, smell }) => ({ id, ...smell }))
    : [];
  const viewerLanguage = viewerFile
    ? smellRows.find((r) => r.file === viewerFile)?.language
    : "";

  // Aggregated on every render rather than fetched: the panel has to move with
  // the checkbox, and this is a sum over records already in memory.
  const impactRecords = impacts ? Array.from(impacts.values()) : null;
  const selectedImpactIds = new Set(selectedRows.map((r) => r.id));
  const impactSummary = impactRecords
    ? summariseSelection(impactRecords, selectedImpactIds, qualityBaseline(sourceReport))
    : null;

  const qualityOf = useCallback(
    (file) => allFiles.find((f) => f.relative_path === file)?.quality_score,
    [allFiles]
  );

  const languages = useMemo(
    () => Array.from(new Set(filesWithSmells.map((f) => f.language).filter(Boolean))),
    [filesWithSmells]
  );

  /** Ask the backend for a budgeted selection, then apply it. */
  const handleOptimise = async (preset) => {
    if (!workflowId || optimising) return;
    setOptimising(true);
    try {
      const result = await optimiseSelection(workflowId, {
        preset,
        budget_minutes: budgetMinutes,
      });
      setSelectedIds(new Set(result.selected_ids || []));
      // The optimiser only ever proposes individual smells, so a smell-level
      // view is what makes its answer visible and editable.
      if (isFileMode) setMode("smell");
      setSelected(new Set());
    } catch (error) {
      console.error("Optimise failed:", error);
      alert(error.message);
    } finally {
      setOptimising(false);
    }
  };

  /**
   * The payload, unchanged from before the redesign.
   *
   * File mode sends the file paths and lets the backend expand them to every
   * smell inside. Smell mode must NOT send them: the backend ORs files with
   * smells, so a file path would pull back the smells just deselected.
   *
   * CATEGORY MODE SENDS `selection_mode: "smell"`. Category wise is a frontend
   * grouping of individual smells and nothing more — the backend has no
   * category mode, and inventing one here would be a contract change the
   * server does not implement.
   */
  const selectionPayload = isFileMode
    ? {
        selected_files: selectedFiles,
        selected_smells: selectedSmells,
        selection_mode: "file",
      }
    : {
        selected_ids: Array.from(selectedIds),
        selected_smells: selectedSmells,
        selection_mode: "smell",
      };

  /**
   * Commit the selection and hand off to planning.
   *
   *   POST /smell-selection-pass   filters the report — fast, no agent call
   *   POST /select-smells          forwards it to the RDP agent (the slow one)
   *
   * The second is issued by the PARENT inside `onProceed`, and is awaited so
   * the stage stays disabled and the progress stays visible for the whole
   * hand-off.
   */
  const handleApproveSelection = async () => {
    if (selectionCount === 0 || isSubmitting) return;

    if (!workflowId) {
      onProceed?.({ ...selectionPayload, selected_files: selectedFiles });
      return;
    }

    setIsSubmitting(true);
    setSubmitPhase("filtering");
    try {
      // A side record, deliberately not awaited: blocking the hand-off on it
      // would make the developer wait for a number already on screen.
      if (impacts) {
        analyseSelectionImpact(workflowId, selectionPayload)
          .then((result) => setInteractionNotes(result.interaction_notes || []))
          .catch(() => {});
      }

      const report = await previewSmellSelection(workflowId, {
        ...selectionPayload,
        feedback: {
          reason: isFileMode
            ? "File-wise selection in CodeSmellApprovalPage"
            : `${mode === "category" ? "Category" : "Smell"}-wise selection in CodeSmellApprovalPage`,
        },
      });

      setSubmitPhase("planning");
      await onProceed?.(report);
    } catch (error) {
      console.error("Smell approval failed:", error);
      alert(error.message);
    } finally {
      setIsSubmitting(false);
      setSubmitPhase(null);
    }
  };

  // ── Loading / error states ─────────────────────────────────────────────────
  if (loading && !sourceReport) return <LoadingState />;

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

  const emptyMessage = filesWithSmells.length === 0
    ? "The CUQA agent reported no code smells in this workspace — nothing to refactor."
    : "No findings match the current search, severity or category filters.";

  return (
    <div>
      {staleWorkspace && (
        <StaleWorkspaceBanner info={staleWorkspace} loading={loading} onReload={reloadReport} />
      )}

      <header style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 27, fontWeight: 800, color: C.text, margin: 0 }}>
          Code Smell Review
        </h1>
        <p style={{ fontSize: 13, color: C.textMuted, margin: "6px 0 0", maxWidth: 720, lineHeight: 1.6 }}>
          Review detected code smells and choose which findings should be sent to the
          Refactoring Planning Agent.
        </p>
      </header>

      <CodeSmellStats
        files={num(summary.files_analyzed)}
        smells={num(summary.total_code_smells) || smellRows.length}
        high={num(summary.smell_severity?.high)}
        quality={`${round(summary.average_quality_score, 1)}%`}
        languages={languages}
        categories={taxonomy?.category_count}
      />

      <ReportSourceCard
        origin={origin}
        report={sourceReport}
        meta={fetched}
        loading={loading}
        error={loadError}
        onRefresh={reloadReport}
        reseeds={Boolean(onReloadWorkspace)}
      />

      <SmellCategoryOverview
        taxonomy={taxonomy}
        active={categoryFilter}
        onSelect={(category) => {
          setCategoryFilter(category);
          if (category !== "all" && isFileMode) setMode("category");
        }}
      />

      <div style={{
        display: "flex", gap: 16, alignItems: "flex-end",
        flexWrap: "wrap", marginBottom: 14,
      }}>
        <ModeSwitch
          mode={mode}
          onChange={setMode}
          counts={{
            file: fileGroups.length,
            smell: smellGroups.length,
            category: catGroups.length,
          }}
        />

        <div style={{ flex: "1 1 420px", minWidth: 0 }}>
          <div style={{
            fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 0.9, fontWeight: 700, marginBottom: 7,
          }}>
            Quick selection
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <QuickSelectDropdown
              label="Smell type"
              options={typeOptions}
              searchPlaceholder="Search smell types…"
              emptyLabel="No smell types match"
              onToggleOption={(option) => toggleRows(option.rows)}
              onNavigateOption={(option) => {
                // The label navigates; the checkbox selects. Two intents, two
                // targets — clicking a name to go and look at 53 findings is
                // not a request to select all 53.
                setMode("smell");
                setSmellTypeFilter(option.key);
                setCategoryFilter("all");
                setAutoOpen(false);
                setOpenKeys(new Set([option.key]));
              }}
              onClear={() => {
                const ids = new Set(smellRows.map((r) => r.id));
                setSelectedIds((prev) => new Set([...prev].filter((id) => !ids.has(id))));
              }}
            />

            <QuickSelectDropdown
              label="Category"
              options={catOptions}
              expandable
              searchPlaceholder="Search categories…"
              emptyLabel="No categories available"
              onToggleOption={(option) => toggleRows(option.rows)}
              onNavigateOption={(option) => {
                setMode("category");
                setCategoryFilter(option.rows[0] ? categoryOf(option.rows[0]) : "all");
                setSmellTypeFilter("all");
                setAutoOpen(false);
                setOpenKeys(new Set([option.key]));
              }}
              onClear={() => setSelectedIds(new Set())}
            />
          </div>
        </div>
      </div>

      {(smellTypeFilter !== "all" || categoryFilter !== "all") && (
        <ActiveFilters
          smellType={smellTypeFilter}
          category={categoryFilter}
          onClearType={() => setSmellTypeFilter("all")}
          onClearCategory={() => setCategoryFilter("all")}
        />
      )}

      <SearchAndSeverity
        mode={mode}
        search={search}
        onSearch={setSearch}
        severity={severity}
        onSeverity={setSeverity}
        onSelectAll={selectAll}
        onRejectAll={rejectAll}
        onExpandAll={expandAll}
        onCollapseAll={collapseAll}
        visibleCount={isFileMode ? fileGroups.length : visibleRows.length}
        selectedCount={selectionCount}
        groupCount={activeGroups.length}
        anyOpen={effectiveOpen.size > 0}
      />

      <div style={{
        display: "grid", gap: 16, alignItems: "start",
        gridTemplateColumns: "minmax(0, 3fr) minmax(260px, 1fr)",
      }}>
        <div style={{
          display: "flex", flexDirection: "column", gap: 10,
          maxHeight: "min(72vh, 780px)", overflowY: "auto", paddingRight: 4,
        }}>
          {activeGroups.length === 0 && (
            <div style={{
              padding: "28px 20px", textAlign: "center", background: C.panel,
              border: `1px dashed ${C.border}`, borderRadius: 11,
              color: C.textMuted, fontSize: 13, flexShrink: 0,
            }}>
              {emptyMessage}
            </div>
          )}

          {mode === "smell" && (
            <SmellWiseView
              groups={smellGroups}
              selectedIds={selectedIds}
              openKeys={effectiveOpen}
              onToggleOpen={toggleOpen}
              onToggleRows={toggleRows}
              onToggleSmell={toggleSmell}
              onView={(file, id) => setViewing({ file, focusId: id })}
              onShowImpact={setImpactRow}
              impacts={impacts}
            />
          )}

          {mode === "category" && (
            <CategoryWiseView
              groups={catGroups}
              priorities={categoryPriority}
              selectedIds={selectedIds}
              openKeys={effectiveOpen}
              onToggleOpen={toggleOpen}
              onToggleRows={toggleRows}
              onToggleSmell={toggleSmell}
              onView={(file, id) => setViewing({ file, focusId: id })}
              onShowImpact={setImpactRow}
              impacts={impacts}
            />
          )}

          {isFileMode && (
            <FileWiseView
              groups={fileGroups}
              openKeys={effectiveOpen}
              onToggleOpen={toggleOpen}
              onToggleFile={toggleFile}
              onViewFile={(file) => setViewing({ file, focusId: null })}
              onView={(file, id) => setViewing({ file, focusId: id })}
              onShowImpact={setImpactRow}
              qualityOf={qualityOf}
              impacts={impacts}
            />
          )}
        </div>

        <SelectionSummaryPanel
          summary={panelSummary}
          mode={mode}
          onClear={rejectAll}
        />
      </div>

      <div style={{ marginTop: 10, fontSize: 11, color: C.textMuted, display: "flex", gap: 16, flexWrap: "wrap" }}>
        <span>
          Showing {visibleRows.length} of {smellRows.length} finding
          {smellRows.length === 1 ? "" : "s"} in {activeGroups.length}{" "}
          {isFileMode ? "file" : mode === "category" ? "category" : "smell type"}
          {activeGroups.length === 1 ? "" : "s"}.
        </span>
        {hiddenCount > 0 && (
          <span>
            {hiddenCount} analysed file{hiddenCount > 1 ? "s" : ""} with no detected smells{" "}
            {hiddenCount > 1 ? "are" : "is"} hidden.
          </span>
        )}
      </div>

      {isSubmitting && <SubmitProgress phase={submitPhase} smellCount={selectedSmells.length} />}

      {impactSummary && (
        <div style={{ marginTop: 16 }}>
          <TradeOffPanel
            summary={impactSummary}
            interactionNotes={interactionNotes}
            optimising={optimising}
            onOptimise={workflowId ? handleOptimise : undefined}
            budgetMinutes={budgetMinutes}
            onBudgetChange={setBudgetMinutes}
          />
        </div>
      )}

      <ActionBar
        selectionCount={selectionCount}
        findingCount={selectedRows.length}
        isSubmitting={isSubmitting}
        submitPhase={submitPhase}
        onContinue={handleApproveSelection}
      />

      {impactRow && impacts?.get(impactRow.id) && (
        <ImpactDrawer
          record={impacts.get(impactRow.id)}
          smell={impactRow.smell}
          isSelected={selectedIds.has(impactRow.id)}
          // No select action in File wise: the unit of selection there is the
          // file, so a button offering to add ONE finding would promise
          // something the mode cannot send. The figures still read the same.
          onToggleSmell={isFileMode ? undefined : toggleSmell}
          onClose={() => setImpactRow(null)}
        />
      )}

      {viewerFile && (
        <SourceViewer
          // Keyed by file: opening a different file mounts a fresh viewer.
          key={viewerFile}
          file={viewerFile}
          language={viewerLanguage}
          smells={viewerSmells}
          focusId={viewing?.focusId || null}
          // File mode selects whole files — offering a per-smell tick there
          // would suggest a partial selection the mode cannot express.
          selectedIds={isFileMode ? null : selectedIds}
          onToggleSmell={isFileMode ? undefined : toggleSmell}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

// ─── Page furniture ──────────────────────────────────────────────────────────

/** The chips showing which quick-select filters are narrowing the list. */
function ActiveFilters({ smellType, category, onClearType, onClearCategory }) {
  const chip = (label, value, onClear) => (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 7,
      padding: "5px 11px", borderRadius: 20,
      background: `${C.accent}15`, border: `1px solid ${C.accent}55`,
      fontSize: 11.5, color: C.accent, fontWeight: 600,
    }}>
      <span style={{ color: C.textMuted, fontWeight: 500 }}>{label}</span>
      {value}
      <button
        onClick={onClear}
        aria-label={`Clear the ${label} filter`}
        style={{
          background: "none", border: "none", color: "inherit", cursor: "pointer",
          padding: 0, fontWeight: 800, fontSize: 12, lineHeight: 1,
        }}
      >
        ✕
      </button>
    </span>
  );

  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
      <span style={{
        fontSize: 10.5, color: C.textMuted, textTransform: "uppercase",
        letterSpacing: 0.9, fontWeight: 700,
      }}>
        Filtered to
      </span>
      {smellType !== "all" && chip("Smell type", smellType, onClearType)}
      {category !== "all" && chip("Category", category, onClearCategory)}
    </div>
  );
}

const VIA_LABEL = {
  "diwo-proxy": "via DIWO orchestration backend",
};

/** Where the report on screen came from, and how to reload the repository. */
function ReportSourceCard({ origin, report, meta, loading, error, onRefresh, reseeds }) {
  const isSample = origin === "sample";
  const tone = isSample ? C.warn : origin === "workflow" ? C.info : C.accent;

  const label = isSample
    ? "Sample report (bundled data — CUQA agent unavailable)"
    : origin === "workflow"
      ? "Report from the current DIWO workflow"
      : "Live CUQA quality report";

  const details = [];
  if (report?.repo_name) details.push(report.repo_name);
  if (report?.report_type) details.push(report.report_type);
  if (meta?.via && VIA_LABEL[meta.via]) details.push(VIA_LABEL[meta.via]);

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14,
      marginBottom: 14, padding: "12px 16px", borderRadius: 11,
      background: C.panel, border: `1px solid ${isSample ? `${C.warn}55` : C.border}`,
      flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
        <span aria-hidden="true" style={{
          width: 8, height: 8, borderRadius: "50%", background: tone, flexShrink: 0,
        }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: C.text }}>{label}</div>
          {details.length > 0 && (
            <div style={{
              fontSize: 11, color: C.textMuted,
              fontFamily: "ui-monospace, Menlo, Consolas, monospace",
              overflow: "hidden", textOverflow: "ellipsis", marginTop: 2,
            }}>
              {details.join(" · ")}
            </div>
          )}
          {error && (
            <div style={{ fontSize: 11, color: C.danger, marginTop: 2 }}>
              Refresh failed: {error.message}
            </div>
          )}
        </div>
      </div>

      <button
        onClick={onRefresh}
        disabled={loading}
        title={
          reseeds
            ? "Re-read the repository currently loaded in CUQA and start a fresh workflow for it"
            : "Re-read the quality report from the CUQA agent"
        }
        style={{
          padding: "8px 15px", borderRadius: 8, fontSize: 12, fontWeight: 600,
          background: C.bg, color: loading ? C.textMuted : C.textSub,
          border: `1px solid ${C.border}`, cursor: loading ? "wait" : "pointer", flexShrink: 0,
        }}
      >
        {loading ? "Analyzing…" : isSample ? "Retry CUQA" : "Re-analyze repository"}
      </button>
    </div>
  );
}

/**
 * The sticky footer. `onContinue` is the ONLY submit path — the same
 * handleApproveSelection the page has always used, not a second one.
 */
function ActionBar({ selectionCount, findingCount, isSubmitting, submitPhase, onContinue }) {
  const ready = selectionCount > 0 && !isSubmitting;

  return (
    <div style={{
      position: "sticky", bottom: 0, marginTop: 16, zIndex: 20,
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 14, flexWrap: "wrap",
      padding: "13px 18px", borderRadius: 12,
      background: C.panel, border: `1px solid ${selectionCount ? C.accent : C.border}`,
      boxShadow: "0 -6px 24px rgba(4,6,10,0.45)",
    }}>
      <div style={{ fontSize: 12.5, color: selectionCount ? C.text : C.textMuted }}>
        {selectionCount > 0 ? (
          <>
            <b style={{ color: C.accent, fontFamily: "monospace" }}>{findingCount}</b>{" "}
            finding{findingCount === 1 ? "" : "s"} selected for planning
          </>
        ) : (
          "Select findings to continue to the Refactoring Planning Agent"
        )}
      </div>

      <button
        onClick={onContinue}
        disabled={!ready}
        title={
          isSubmitting
            ? "Working — the Refactoring Planning Agent is generating the plan"
            : undefined
        }
        style={{
          display: "flex", alignItems: "center", gap: 9,
          padding: "10px 22px", borderRadius: 9, border: "none",
          fontSize: 13, fontWeight: 700,
          cursor: isSubmitting ? "wait" : ready ? "pointer" : "not-allowed",
          background: ready || isSubmitting ? C.accent : C.border,
          color: ready || isSubmitting ? "#000" : C.textMuted,
          opacity: isSubmitting ? 0.75 : 1,
          transition: "all 0.2s",
        }}
      >
        {isSubmitting && <Spinner color="#000" />}
        {isSubmitting
          ? submitPhase === "planning" ? "Planning refactorings…" : "Filtering selection…"
          : "Continue to Refactoring Plan →"}
      </button>
    </div>
  );
}

/**
 * CUQA is holding a different repository than the one on screen.
 *
 * Loud on purpose: everything below belongs to the repository named in `shown`,
 * which the developer has since replaced. Nothing on the page is wrong about
 * the OLD repository — it is simply about code that is no longer loaded, which
 * is the kind of stale that gets acted on because it looks healthy.
 */
function StaleWorkspaceBanner({ info, loading, onReload }) {
  return (
    <div style={{
      marginBottom: 14, padding: "13px 16px", borderRadius: 11,
      background: `${C.warn}12`, border: `1px solid ${C.warn}`,
      display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
    }}>
      <span aria-hidden="true" style={{ fontSize: 17 }}>⚠</span>
      <div style={{ flex: 1, minWidth: 240 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: C.warn }}>
          A different repository is loaded in the CUQA agent
        </div>
        <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.55, marginTop: 3 }}>
          This page is showing{" "}
          <b style={{ color: C.text, fontFamily: "monospace" }}>{info.shown}</b>, but CUQA now
          holds <b style={{ color: C.text, fontFamily: "monospace" }}>{info.live}</b>
          {typeof info.files === "number" ? ` (${info.files} source file${info.files === 1 ? "" : "s"})` : ""}.
          The findings below — and the workflow behind them — describe the old repository.
        </div>
      </div>
      <button
        onClick={onReload}
        disabled={loading}
        style={{
          padding: "9px 18px", borderRadius: 8, fontSize: 12.5, fontWeight: 700,
          background: loading ? C.border : C.warn, color: loading ? C.textMuted : "#000",
          border: "none", cursor: loading ? "wait" : "pointer", flexShrink: 0,
          display: "flex", alignItems: "center", gap: 8,
        }}
      >
        {loading && <Spinner color="#000" />}
        {loading ? "Loading…" : `Analyse ${info.live}`}
      </button>
    </div>
  );
}

/** A small indeterminate spinner, for a wait whose length cannot be predicted. */
function Spinner({ size = 13, color = "currentColor" }) {
  return (
    <>
      <span
        aria-hidden="true"
        style={{
          width: size, height: size, borderRadius: "50%", flexShrink: 0,
          border: `2px solid ${color}`, borderTopColor: "transparent",
          display: "inline-block", animation: "diwoSpin 0.7s linear infinite",
        }}
      />
      <style>{"@keyframes diwoSpin { to { transform: rotate(360deg); } }"}</style>
    </>
  );
}

/**
 * The hand-off, step by step. Naming the step that is running — and saying it
 * is another agent doing the work — is the difference between a developer
 * waiting and a developer clicking again because nothing is moving.
 */
function SubmitProgress({ phase, smellCount }) {
  const steps = [
    {
      key: "filtering",
      label: "Filtering the report to your selection",
      detail: `${smellCount} finding${smellCount === 1 ? "" : "s"} kept`,
    },
    {
      key: "planning",
      label: "Refactoring Planning Agent is generating the plan",
      detail: "interpreting smells, scoring candidates (MCDA), sequencing steps",
    },
  ];
  const activeIndex = steps.findIndex((s) => s.key === phase);

  return (
    <div style={{
      marginTop: 12, padding: "12px 16px", borderRadius: 11,
      background: `${C.accent}0a`, border: `1px solid ${C.accent}40`,
    }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((step, i) => {
          const done = activeIndex > i;
          const active = activeIndex === i;
          return (
            <div key={step.key} style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <span style={{ width: 14, display: "inline-flex", justifyContent: "center", flexShrink: 0 }}>
                {done
                  ? <span style={{ color: C.accent, fontWeight: 900, fontSize: 12 }}>✓</span>
                  : active
                    ? <Spinner size={11} color={C.accent} />
                    : <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.border }} />}
              </span>
              <span style={{
                fontSize: 12,
                color: done ? C.accent : active ? C.text : C.textMuted,
                fontWeight: active ? 700 : 500,
              }}>
                {step.label}
              </span>
              <span style={{ fontSize: 11, color: C.textMuted }}>· {step.detail}</span>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 9, fontSize: 11, color: C.textMuted, lineHeight: 1.5 }}>
        Planning runs on another agent over HTTP, so this can take a few seconds on a
        large selection. The page moves to the Refactoring Plan stage on its own —
        no need to click again.
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <Card style={{ textAlign: "center", padding: "48px 24px" }}>
      <div style={{ fontSize: 13.5, fontWeight: 700, color: C.text, marginBottom: 8 }}>
        Analyzing repository…
      </div>
      <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 20 }}>
        Requesting the quality report from CUQA through the DIWO orchestration backend
      </div>
      <div style={{ height: 4, borderRadius: 4, background: C.border, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: C.gradient, animation: "diwoSlide 1.1s ease-in-out infinite" }} />
      </div>
      <style>{"@keyframes diwoSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }"}</style>
    </Card>
  );
}

function ErrorState({ error, onRetry, onUseSample }) {
  const status = error?.status;
  const noRepo = status === 400 || status === 404;

  const hint = noRepo
    ? "The CUQA agent is running but has no repository loaded. Open the CUQA UI, upload or load a repository, then re-analyze."
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
        fontSize: 11, color: C.textMuted,
        fontFamily: "ui-monospace, Menlo, Consolas, monospace", lineHeight: 1.6,
        background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8,
        padding: "10px 14px", marginBottom: 18,
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button onClick={onRetry} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 700,
          background: C.accent, color: "#000", border: "none", cursor: "pointer",
        }}>
          Retry
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
