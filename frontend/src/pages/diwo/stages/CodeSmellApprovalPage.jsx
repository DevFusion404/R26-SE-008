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
 *     File wise      file card -> one box per smell type in it
 *     Smell wise     smell type card -> one box per file it appears in
 *     Category wise  category card -> one box per smell type in it
 *
 * Rendered by SmellSelectionSection. All three views write to `selectedIds`,
 * so switching between them never loses a tick. File wise still sends the
 * file-mode payload when the selection is exactly a set of whole files, and
 * the smell-mode payload once single smells inside a file are picked.
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
 * All of it degrades. No impact records means no chips, no capability tags,
 * no effort figures and no effort / gain sorting — not zeroes. No taxonomy
 * means categories fall back to what CUQA stamped on each smell. Selection
 * keeps working in every case.
 *
 * Layout
 * ------
 *     context strip      repository, report source, key metrics, re-analyze
 *     filter rail        summary + severity / category / smell-type filters
 *     findings           toolbar, then the active view's groups and cards
 *     selection panel    what goes to RDP, impact & optimiser, the one CTA
 *
 * Three columns on a wide workspace, two (rail above the list) on a medium
 * one, one on a narrow one with a sticky footer carrying the CTA — see
 * codeSmellTheme.css. Colours come from codeSmellTheme.js tokens, which flip
 * with the app's light / dark theme.
 *
 * Send to RDP opens CodeSmellApprovalDialog first; confirming calls
 * handleApproveSelection, the same single submit path as before.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CUQA_DATA } from "../data/diwoData";
import { T, tint, eyebrow, panelStyle, ghostButton, primaryButton } from "../codeSmellTheme.js";
import { Spinner } from "../codeSmellUi.jsx";
import SourceViewer from "../components/SourceViewer.jsx";
import ImpactDrawer from "../components/ImpactDrawer.jsx";
import CodeSmellContextBar from "../components/CodeSmellContextBar.jsx";
import CodeSmellFilterRail from "../components/CodeSmellFilterRail.jsx";
import CodeSmellSelectionPanel, { MobileActionBar } from "../components/CodeSmellSelectionPanel.jsx";
import CodeSmellApprovalDialog from "../components/CodeSmellApprovalDialog.jsx";
import SmellSelectionSection from "../components/SmellSelectionSection.jsx";
import { DEFAULT_BUDGET_MINUTES, PRESETS } from "../utils/impactPresets";
import { qualityBaseline, summariseSelection } from "../utils/impactSummary";
import { selectionSummary, severitySpread } from "../utils/smellGrouping";
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
  // Opens on Smell wise, with every card collapsed, so the first view is a
  // short list of smell types. File wise and Category wise are one click away,
  // and neither loses a tick when switched to.
  const [mode, setMode] = useState("smell");
  // One selection store for all three views — File wise can pick single smells
  // inside a file, so whole-file selection is derived from it, not kept apart.
  const [selectedIds, setSelectedIds] = useState(new Set());    // smell ids

  // Four independent filter axes. Deliberately not one field: "the Bloaters I
  // have not triaged" and "every high-severity Magic Number" are ordinary
  // queries, and a single state variable cannot express either.
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [smellTypeFilter, setSmellTypeFilter] = useState("all");

  const [isSubmitting, setIsSubmitting] = useState(false);
  // Which half of the hand-off is running: "filtering" (fast, local to the
  // backend) or "planning" (the RDP agent, which is the one worth waiting on).
  // Read only by the Continue button, which is the whole progress indicator —
  // a stepped panel underneath it narrated a wait the button was already
  // narrating, in more words and further from where the developer clicked.
  const [submitPhase, setSubmitPhase] = useState(null);

  // Which file's original source is open, and which smell inside it is focused.
  const [viewing, setViewing] = useState(null);
  // The finding whose impact dialog is open. The ROW, not the id: the dialog
  // shows the entity name, which lives on the smell and not on the record.
  const [impactRow, setImpactRow] = useState(null);
  // The confirmation shown before the hand-off. It only summarises; confirming
  // calls handleApproveSelection, the one submit path this stage has.
  const [confirming, setConfirming] = useState(false);
  // How the cards and the boxes inside them are ordered. View-only.
  const [sort, setSort] = useState("severity");
  // Provenance of the last optimiser run — { ids, label, count } — so a
  // finding it proposed can say so. Display only: `selectedIds` stays the one
  // record of what is selected.
  const [optimised, setOptimised] = useState(null);

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
    setSelectedIds(new Set());
    setLoadError(null);
    setLoading(false);
    setViewing(null);
    setImpactRow(null);
    setConfirming(false);
    setOptimised(null);
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
      setSelectedIds(new Set());
      setLoading(false);
      setViewing(null);
      setImpactRow(null);
      setImpacts(null);
      setConfirming(false);
      setOptimised(null);
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

  // The rows the views render: each tagged with its resolved category and
  // whether the optimiser proposed it. Same ids, same order as smellRows.
  const reviewRows = useMemo(() => smellRows.map((row) => ({
    ...row,
    category: categoryOf(row),
    suggested: Boolean(optimised?.ids.has(row.id)),
  })), [smellRows, categoryOf, optimised]);

  // ── Filtering pipeline ─────────────────────────────────────────────────────
  // search -> severity -> category -> smell type. Filters change what is
  // VISIBLE and never what is selected: a finding hidden by a filter keeps its
  // tick, because a filter is a way of looking, not a way of deciding.
  const term = search.trim().toLowerCase();
  const visibleRows = useMemo(() => reviewRows.filter((row) => {
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
  }), [reviewRows, term, severity, categoryFilter, smellTypeFilter, categoryOf]);

  const isFileMode = mode === "file";

  // ── Selection ──────────────────────────────────────────────────────────────
  const toggleSmell = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /**
   * Tick or untick a set of findings — a card, a box, or anything else. Every
   * level in every view routes through here, so there is one selection store.
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
  const rejectAll = () => setSelectedIds(new Set());

  const selectedRows = smellRows.filter((r) => selectedIds.has(r.id));

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

  // File wise sends the old file-mode payload when what is ticked is exactly a
  // set of whole files. Once a single smell inside a file is picked, the
  // backend's file expansion would pull the rest of that file back in, so the
  // selection goes as individual smells instead.
  const wholeFiles = selectedFiles.length > 0 && selectedFiles.every((file) =>
    smellRows.every((r) => r.file !== file || selectedIds.has(r.id)));
  const payloadMode = isFileMode && wholeFiles ? "file" : "smell";
  const sendsFiles = payloadMode === "file";

  const selectionCount = sendsFiles ? selectedFiles.length : selectedIds.size;

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
      const ids = result.selected_ids || [];
      setSelectedIds(new Set(ids));
      setOptimised({
        ids: new Set(ids),
        label: PRESETS.find((p) => p.key === preset)?.label || preset,
        count: ids.length,
      });
      // The optimiser only ever proposes individual smells, so a smell-level
      // view is what makes its answer visible and editable.
      if (isFileMode) setMode("smell");
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
  const selectionPayload = sendsFiles
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
          reason: sendsFiles
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

  // ── Left rail figures ──────────────────────────────────────────────────────
  // Counted over EVERY finding, not the filtered ones: the rail is where a
  // developer sees what a filter would reveal before applying it.
  const severityCounts = { ...severitySpread(smellRows), total: smellRows.length };
  const railCategories = (() => {
    const counts = new Map();
    reviewRows.forEach((row) => counts.set(row.category, (counts.get(row.category) || 0) + 1));
    const rank = new Map(categoryOrder.map((name, i) => [name, i]));
    return Array.from(counts, ([category, count]) => ({
      category, count, priority: categoryPriority.get(category) || null,
    })).sort((a, b) =>
      (rank.get(a.category) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.category) ?? Number.MAX_SAFE_INTEGER)
      || b.count - a.count || a.category.localeCompare(b.category));
  })();

  const filtersActive = term !== "" || severity !== "all"
    || categoryFilter !== "all" || smellTypeFilter !== "all";
  const clearFilters = () => {
    setSearch("");
    setSeverity("all");
    setCategoryFilter("all");
    setSmellTypeFilter("all");
  };

  /** Open the confirmation — never submits on its own. */
  const openReview = () => {
    if (selectionCount === 0 || isSubmitting) return;
    setConfirming(true);
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
    : "No findings match the current search, severity, category or smell type filters.";

  return (
    <div>
      {staleWorkspace && (
        <StaleWorkspaceBanner info={staleWorkspace} loading={loading} onReload={reloadReport} />
      )}

      <CodeSmellContextBar
        origin={origin}
        report={sourceReport}
        meta={fetched}
        loading={loading}
        error={loadError}
        onRefresh={reloadReport}
        reseeds={Boolean(onReloadWorkspace)}
        stats={{
          files: num(summary.files_analyzed),
          smells: num(summary.total_code_smells) || smellRows.length,
          high: num(summary.smell_severity?.high),
          quality: typeof summary.average_quality_score === "number"
            ? `${round(summary.average_quality_score, 1)}%`
            : null,
          categories: taxonomy?.category_count,
          languages,
        }}
      />

      <div className="dw-ws">
        <div className="dw-grid">
          <div className="dw-area-rail">
            <CodeSmellFilterRail
              severityCounts={severityCounts}
              severity={severity}
              onSeverity={setSeverity}
              categories={railCategories}
              categoryFilter={categoryFilter}
              onCategory={(category) => {
                setCategoryFilter(category);
                if (category !== "all" && isFileMode) setMode("category");
              }}
              filtersActive={filtersActive}
              onClearFilters={clearFilters}
            />
          </div>

          <section className="dw-area-main" aria-label="Findings">
            <SmellSelectionSection
              mode={mode}
              onModeChange={setMode}
              rows={visibleRows}
              allRows={reviewRows}
              selectedIds={selectedIds}
              onToggleRows={toggleRows}
              onSelectShown={selectAll}
              onClearSelection={rejectAll}
              selectedCount={selectedRows.length}
              search={search}
              onSearch={setSearch}
              sort={sort}
              onSort={setSort}
              smellTypeFilter={smellTypeFilter}
              onSmellTypeFilter={setSmellTypeFilter}
              categoryFilter={categoryFilter}
              onCategoryFilter={setCategoryFilter}
              filtersActive={filtersActive}
              onClearFilters={clearFilters}
              emptyMessage={emptyMessage}
              impacts={impacts}
              qualityOf={qualityOf}
              onViewFile={(file, focusId = null) => setViewing({ file, focusId })}
            />

            {hiddenCount > 0 && (
              <div style={{ marginTop: 10, fontSize: 11.5, color: T.text3 }}>
                {hiddenCount} analysed file{hiddenCount > 1 ? "s" : ""} with no detected smells{" "}
                {hiddenCount > 1 ? "are" : "is"} not listed.
              </div>
            )}
          </section>

          <div className="dw-area-side">
            <CodeSmellSelectionPanel
              mode={payloadMode}
              selectionCount={selectionCount}
              findingCount={selectedRows.length}
              totalFindings={smellRows.length}
              totalFiles={filesWithSmells.length}
              summary={panelSummary}
              impactSummary={impactSummary}
              interactionNotes={interactionNotes}
              optimising={optimising}
              onOptimise={workflowId ? handleOptimise : undefined}
              budgetMinutes={budgetMinutes}
              onBudgetChange={setBudgetMinutes}
              optimised={optimised}
              isSubmitting={isSubmitting}
              submitPhase={submitPhase}
              onReview={openReview}
              onClear={rejectAll}
            />
          </div>
        </div>

        <MobileActionBar
          selectionCount={selectionCount}
          findingCount={selectedRows.length}
          isFile={sendsFiles}
          isSubmitting={isSubmitting}
          submitPhase={submitPhase}
          onReview={openReview}
        />
      </div>

      {confirming && (
        <CodeSmellApprovalDialog
          mode={payloadMode}
          selectedRows={selectedRows}
          selectedFiles={selectedFiles}
          totalFindings={smellRows.length}
          summary={panelSummary}
          impactSummary={impactSummary}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            handleApproveSelection();
          }}
        />
      )}

      {impactRow && impacts?.get(impactRow.id) && (
        <ImpactDrawer
          record={impacts.get(impactRow.id)}
          smell={impactRow.smell}
          isSelected={selectedIds.has(impactRow.id)}
          onToggleSmell={toggleSmell}
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
          selectedIds={selectedIds}
          onToggleSmell={toggleSmell}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

// ─── Page furniture ──────────────────────────────────────────────────────────

/**
 * CUQA is holding a different repository than the one on screen.
 *
 * Loud on purpose: everything below belongs to the repository named in `shown`,
 * which the developer has since replaced — the kind of stale that gets acted
 * on because it looks healthy.
 */
function StaleWorkspaceBanner({ info, loading, onReload }) {
  return (
    <div role="alert" style={{
      marginBottom: 14, padding: "13px 16px", borderRadius: 12,
      background: tint(T.medium, 10), border: `1px solid ${tint(T.medium, 60)}`,
      display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
    }}>
      <span aria-hidden="true" style={{ fontSize: 17, color: T.medium }}>⚠</span>
      <div style={{ flex: 1, minWidth: 240 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: T.medium }}>
          A different repository is loaded in the CUQA agent
        </div>
        <div style={{ fontSize: 12, color: T.text2, lineHeight: 1.55, marginTop: 3 }}>
          This page is showing{" "}
          <b style={{ color: T.text, fontFamily: T.mono }}>{info.shown}</b>, but CUQA now
          holds <b style={{ color: T.text, fontFamily: T.mono }}>{info.live}</b>
          {typeof info.files === "number" ? ` (${info.files} source file${info.files === 1 ? "" : "s"})` : ""}.
          The findings below — and the workflow behind them — describe the old repository.
        </div>
      </div>
      <button
        type="button"
        onClick={onReload}
        disabled={loading}
        className="dw-focus"
        style={{
          padding: "9px 18px", borderRadius: 8, fontSize: 12.5, fontWeight: 700,
          background: loading ? T.sunken : T.medium, color: loading ? T.text3 : T.onAccent,
          border: "none", cursor: loading ? "wait" : "pointer", flexShrink: 0,
          display: "flex", alignItems: "center", gap: 8,
        }}
      >
        {loading && <Spinner />}
        {loading ? "Loading…" : `Analyse ${info.live}`}
      </button>
    </div>
  );
}

function LoadingState() {
  return (
    <div role="status" style={{ ...panelStyle, textAlign: "center", padding: "48px 24px" }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: T.text, marginBottom: 8 }}>
        Analyzing repository…
      </div>
      <div style={{ fontSize: 12.5, color: T.text2, marginBottom: 20 }}>
        Requesting the quality report from CUQA through the DIWO orchestration backend
      </div>
      <div style={{ height: 4, borderRadius: 4, background: T.sunken, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: T.accent, animation: "dwSlide 1.1s ease-in-out infinite" }} />
      </div>
    </div>
  );
}

function ErrorState({ error, onRetry, onUseSample }) {
  const status = error?.status;
  const noRepo = status === 400 || status === 404;

  const hint = noRepo
    ? "The CUQA agent is running but has no repository loaded. Open the CUQA UI, upload or load a repository, then re-analyze."
    : "Start the CUQA agent before running the DIWO workflow:  cd agents/cuqa_agent/src && uvicorn main:app --port 8080";

  return (
    <div role="alert" style={{ ...panelStyle, padding: "28px 28px", border: `1px solid ${tint(T.high, 45)}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{
          ...eyebrow, color: T.high, padding: "2px 8px", borderRadius: 4,
          background: tint(T.high, 10), border: `1px solid ${tint(T.high, 30)}`,
        }}>
          {noRepo ? "No repository" : "CUQA unavailable"}
        </span>
        <span style={{ fontSize: 14.5, fontWeight: 700, color: T.text }}>
          Could not load the code smell report
        </span>
      </div>

      <div style={{ fontSize: 12.5, color: T.text2, lineHeight: 1.6, marginBottom: 12 }}>
        {error?.message || "Unknown error while contacting the CUQA agent."}
      </div>

      <div style={{
        fontSize: 11.5, color: T.text2, fontFamily: T.mono, lineHeight: 1.6,
        background: T.code, border: `1px solid ${T.border}`, borderRadius: 8,
        padding: "10px 14px", marginBottom: 18, wordBreak: "break-word",
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="button" onClick={onRetry} className="dw-focus" style={primaryButton(true)}>
          Retry
        </button>
        <button type="button" onClick={onUseSample} className="dw-focus dw-hover" style={ghostButton(true, { padding: "10px 18px", fontSize: 13 })}>
          Continue with sample data
        </button>
      </div>
    </div>
  );
}
