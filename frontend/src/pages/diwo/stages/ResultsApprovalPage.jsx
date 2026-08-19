/**
 * ResultsApprovalPage.jsx
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 4 of the DIWO workflow: review what the Safe Code Transformation &
 * Validation Agent produced, then commit it.
 *
 * The per-file verdict taken in the Transformation stage is honoured here.
 * Each file entry arrives carrying `decision`, its original text (`before`)
 * and the agent's output (`after`), and this page derives one `finalCode` from
 * them:
 *
 *     accepted -> finalCode = after   (the refactored source)
 *     rejected -> finalCode = before  (reverted to the original source)
 *
 * `finalCode` is what the page displays, what the download writes, and what is
 * POSTed to /api/diwo/apply-and-push — so rejecting a file genuinely reverts
 * it instead of only hiding it from the view.
 *
 * Every number on this page comes from the agent's own response (`sctva`):
 * the confidence score and its components, the four validation stages, the
 * transformation log and the mined invariants.
 */

import { useMemo, useState } from "react";
import { Badge, C, Card, Pill } from "../diwoTheme.jsx";
import { CodeBlock, CodeSurface, DiffBlock, DiffLegend } from "../components/DiffView.jsx";
import { buildDiffRows } from "../services/sctvaApi";
import { buildProjectArchive } from "../utils/projectArchive";
import { createZip, downloadBlob } from "../utils/zipWriter";
import { applyAndPush } from "../services/diwoApi";

const VALIDATION_LABELS = {
  syntax: ["Syntax Validation", "Compilation / parse check of the transformed source."],
  structural: ["Structural Analysis", "AST shape compared against the original."],
  behavioral: ["Behavioral Probes", "Runtime or static behavioural fingerprint comparison."],
  invariant: ["Invariant Mining", "Program invariants mined before and after."],
};

const COMPONENT_LABELS = [
  ["syntax_component", "Syntax"],
  ["structural_component", "Structural"],
  ["behavioral_component", "Behavioral"],
  ["invariant_component", "Invariant"],
];

const pct = (value, dp = 1) =>
  typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(dp)}%` : "—";

// ─── Project outcome model ───────────────────────────────────────────────────
/**
 * Where each analysed file ended up after the four stages. The order below is
 * the order the pipeline drops a file: detected → selected → planned →
 * approved → transformed → accepted. A file is labelled by the first gate it
 * failed to pass, so "why was this smell never fixed?" has one answer per file.
 */
const OUTCOMES = {
  refactored: {
    label: "Refactored",
    color: C.accent,
    hint: "Smells detected, selected, planned, transformed and kept.",
  },
  change_rejected: {
    label: "Change rejected",
    color: C.danger,
    hint: "SCTVA refactored it, but the change was rejected in the Transformation stage — the file is reverted to its original.",
  },
  no_change: {
    label: "No change produced",
    color: C.textMuted,
    hint: "Approved steps ran, but the agent returned this file unchanged.",
  },
  not_transformed: {
    label: "Not transformed",
    color: C.warn,
    hint: "Steps were approved, but the file never came back from the Transformation stage (no readable source, or the run skipped it).",
  },
  plan_rejected: {
    label: "Plan rejected",
    color: C.warn,
    hint: "Smells were selected, but every planned step for this file was rejected in Plan Approval.",
  },
  not_planned: {
    label: "No plan produced",
    color: C.info,
    hint: "Smells were selected, but the RDP agent produced no refactoring step for this file.",
  },
  not_selected: {
    label: "Not selected",
    color: "#ec4899",
    hint: "Code smells were detected, but the file was not selected in Code Smell Review.",
  },
  clean: {
    label: "No smells",
    color: C.textMuted,
    hint: "Analysed by CUQA and no code smells were detected.",
  },
};

const norm = (value = "") => String(value).replace(/\\/g, "/");

const countBy = (steps, pick) => {
  const map = new Map();
  (steps || []).forEach((step) => {
    const key = norm(pick(step) || "");
    if (!key) return;
    map.set(key, (map.get(key) || 0) + 1);
  });
  return map;
};

/**
 * Fold the four stages into one row per analysed file.
 *
 * Sources: the CUQA report (every file analysed, with its smells), the filtered
 * report the smell selection produced, the plan before approval, the approved
 * plan, and the per-file verdicts from the Transformation stage.
 */
function classifyProject({ analysedReport, selectedReport, plan, approvedPlan }, fileEntries) {
  const analysed = analysedReport?.files || [];
  const haveSelection = Boolean(selectedReport?.files);
  const havePlan = Boolean(plan?.steps || approvedPlan?.steps);

  const selectedSmellsByFile = new Map();
  (selectedReport?.files || []).forEach((f) =>
    selectedSmellsByFile.set(norm(f.relative_path || f.file), (f.code_smells || []).length)
  );

  const plannedByFile = countBy(plan?.steps, (s) => s.target?.file);
  const approvedByFile = countBy(approvedPlan?.steps, (s) => s.target?.file);
  const transformedByPath = new Map(fileEntries.map((f) => [norm(f.path), f]));

  const rows = analysed.map((f) => {
    const path = norm(f.relative_path || f.file || "unknown");
    const smells = (f.code_smells || []).length;
    const selected = haveSelection ? selectedSmellsByFile.get(path) ?? 0 : smells;
    const planned = plannedByFile.get(path) || 0;
    const approvedSteps = approvedByFile.get(path) || 0;
    const entry = transformedByPath.get(path) || null;

    let outcome;
    if (smells === 0) outcome = "clean";
    else if (selected === 0) outcome = "not_selected";
    else if (havePlan && planned === 0 && approvedSteps === 0) outcome = "not_planned";
    else if (havePlan && approvedSteps === 0) outcome = "plan_rejected";
    else if (entry?.rejected) outcome = "change_rejected";
    else if (entry?.changed) outcome = "refactored";
    else if (entry) outcome = "no_change";
    else outcome = "not_transformed";

    return {
      path,
      name: path.split("/").pop(),
      language: f.language || "",
      quality_score: f.quality_score,
      smells,
      selected,
      planned,
      approvedSteps,
      outcome,
      severities: (f.code_smells || []).reduce((acc, s) => {
        acc[s.severity] = (acc[s.severity] || 0) + 1;
        return acc;
      }, {}),
    };
  });

  // Files SCTVA touched that CUQA never reported (rare, but they must not
  // vanish from a view whose job is to account for every file).
  const known = new Set(rows.map((r) => r.path));
  fileEntries.forEach((f) => {
    const path = norm(f.path);
    if (known.has(path)) return;
    rows.push({
      path,
      name: path.split("/").pop(),
      language: "",
      smells: 0,
      selected: 0,
      planned: 0,
      approvedSteps: 0,
      outcome: f.rejected ? "change_rejected" : f.changed ? "refactored" : "no_change",
      severities: {},
      unreported: true,
    });
  });

  rows.sort((a, b) => a.path.localeCompare(b.path));

  const withSmells = rows.filter((r) => r.smells > 0);
  const of = (...outcomes) => withSmells.filter((r) => outcomes.includes(r.outcome));

  return {
    rows,
    withSmells,
    refactored: of("refactored"),
    notSelected: of("not_selected"),
    planDropped: of("plan_rejected", "not_planned"),
    changeRejected: of("change_rejected", "no_change", "not_transformed"),
    totals: {
      analysed: rows.length,
      smellFiles: withSmells.length,
      smells: withSmells.reduce((n, r) => n + r.smells, 0),
      smellsSelected: withSmells.reduce((n, r) => n + r.selected, 0),
      smellsRefactored: withSmells
        .filter((r) => r.outcome === "refactored")
        .reduce((n, r) => n + r.selected, 0),
    },
  };
}

/** Nest a flat list of rows into folder nodes for the tree view. */
function buildTree(rows) {
  const root = { name: "", dirs: new Map(), files: [] };

  rows.forEach((row) => {
    const parts = row.path.split("/").filter(Boolean);
    const fileName = parts.pop() || row.path;
    let node = root;
    parts.forEach((part) => {
      if (!node.dirs.has(part)) node.dirs.set(part, { name: part, dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    });
    node.files.push({ ...row, name: fileName });
  });

  return root;
}

function TreeNode({ node, depth = 0, onSelect, selectedPath }) {
  const dirs = Array.from(node.dirs.values()).sort((a, b) => a.name.localeCompare(b.name));
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <>
      {dirs.map((dir) => (
        <div key={`dir-${dir.name}-${depth}`}>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "3px 4px", paddingLeft: 4 + depth * 14,
            fontSize: 11, color: C.textMuted, fontFamily: "monospace",
          }}>
            <span>📁</span>
            <span style={{ fontWeight: 700 }}>{dir.name}</span>
          </div>
          <TreeNode node={dir} depth={depth + 1} onSelect={onSelect} selectedPath={selectedPath} />
        </div>
      ))}

      {files.map((file) => {
        const outcome = OUTCOMES[file.outcome] || OUTCOMES.clean;
        const isSelected = selectedPath === file.path;
        return (
          <div
            key={file.path}
            title={`${file.path}\n${outcome.label} — ${outcome.hint}`}
            onClick={onSelect ? () => onSelect(file) : undefined}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "4px 6px", paddingLeft: 4 + depth * 14,
              marginLeft: 2, marginBottom: 2, borderRadius: 5,
              cursor: onSelect ? "pointer" : "default",
              background: isSelected ? `${C.accent}14` : file.smells > 0 ? `${outcome.color}0c` : "transparent",
              // Every file that CUQA flagged carries a red rail, whatever
              // happened to it afterwards; the badge says how it ended up.
              borderLeft: `3px solid ${file.smells > 0 ? C.danger : "transparent"}`,
              border: `1px solid ${isSelected ? C.accent : "transparent"}`,
            }}
          >
            <span style={{ fontSize: 10 }}>{file.smells > 0 ? "🔴" : "📄"}</span>
            <span style={{
              fontSize: 11, fontFamily: "monospace", color: file.smells > 0 ? C.text : C.textMuted,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 1, minWidth: 0,
            }}>
              {file.name}
            </span>
            {file.smells > 0 && (
              <span style={{ fontSize: 9, color: C.danger, flexShrink: 0 }}>
                {file.smells} smell{file.smells > 1 ? "s" : ""}
              </span>
            )}
            {/* A clean file needs no verdict — badging every one of them would
                bury the handful that actually went through the pipeline. */}
            {file.outcome !== "clean" && (
              <span style={{ marginLeft: "auto", flexShrink: 0 }}>
                <Badge label={outcome.label} color={outcome.color} />
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}

/** One bordered tree panel: a titled, scrollable folder view of some rows. */
function TreePanel({ title, subtitle, color, rows, empty, onSelect, selectedPath, maxHeight = 420 }) {
  const tree = useMemo(() => buildTree(rows), [rows]);

  return (
    <div style={{
      background: C.panel, border: `1px solid ${color}55`, borderRadius: 10,
      overflow: "hidden", display: "flex", flexDirection: "column", minWidth: 0,
    }}>
      <div style={{
        padding: "10px 14px", background: `${color}12`,
        borderBottom: `2px solid ${color}`, borderLeft: `4px solid ${color}`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13, fontWeight: 800, color: C.text }}>{title}</span>
          <Badge label={`${rows.length} file${rows.length === 1 ? "" : "s"}`} color={color} />
        </div>
        {subtitle && (
          <div style={{ fontSize: 11, color: C.textMuted, marginTop: 4, lineHeight: 1.5 }}>{subtitle}</div>
        )}
      </div>

      <div style={{ padding: 8, overflow: "auto", maxHeight }}>
        {rows.length === 0 ? (
          <div style={{ fontSize: 11, color: C.textMuted, padding: "14px 8px", textAlign: "center" }}>
            {empty}
          </div>
        ) : (
          <TreeNode node={tree} onSelect={onSelect} selectedPath={selectedPath} />
        )}
      </div>
    </div>
  );
}

export default function ResultsApprovalPage({
  onRestart,
  onRollback,
  onAccept,
  refactoredCode,
  diffRows = [],
  files = [],
  sctva = null,
  repositoryPath = "",
  // { analysedReport, selectedReport, plan, approvedPlan } — everything the
  // earlier stages decided, so this page can account for every analysed file.
  projectContext = null,
}) {
  // Opens on the project tree: "what happened to every smell" is the question
  // this stage exists to answer. Falls back to the first available tab when the
  // page is rendered without the earlier stages' data.
  const [tab, setTab] = useState("tree");
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const [view, setView] = useState("final");
  const [showActionChoice, setShowActionChoice] = useState(false);
  const [branchName, setBranchName] = useState("refactoring/diwo-changes");
  const [repoPath, setRepoPath] = useState(repositoryPath || "");
  const [commitMessage, setCommitMessage] = useState("refactor: apply DIWO agent refactorings");
  const [pushToOrigin, setPushToOrigin] = useState(true);
  const [pushStatus, setPushStatus] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);

  // ── Resolve each file to the code that will actually be written ───────────
  const fileEntries = useMemo(() => {
    const source =
      files.length > 0
        ? files
        : refactoredCode
          ? [
              {
                path: `refactored_code.${String(sctva?.language || "txt").toLowerCase()}`,
                before: "",
                after: refactoredCode,
                diff_rows: diffRows,
                decision: "accept",
              },
            ]
          : [];

    return source.map((f, idx) => {
      const path = f.path || f.file || `file-${idx}`;
      const before = f.before ?? "";
      const after = f.after ?? f.refactored_code ?? "";
      const rejected = f.decision === "reject";
      // A reject can only be honoured when the original text came through.
      const revertable = Boolean(before);

      return {
        ...f,
        path,
        before,
        after,
        rejected,
        revertable,
        finalCode: rejected && revertable ? before : after,
        changed: f.changed ?? (Boolean(after) && after !== before),
        diff_rows: f.diff_rows || buildDiffRows(before, after),
      };
    });
  }, [files, refactoredCode, diffRows, sctva]);

  // Files the developer accepted in the Transformation stage are pre-selected
  // for the commit; rejected ones are reverted and have nothing to write.
  const [stagedFiles, setStagedFiles] = useState(() => {
    const initial = new Set();
    (files || []).forEach((f, idx) => {
      if (f.decision === "accept") initial.add(f.path || f.file || `file-${idx}`);
    });
    if (initial.size === 0 && files.length === 0 && refactoredCode) {
      initial.add(`refactored_code.${String(sctva?.language || "txt").toLowerCase()}`);
    }
    return initial;
  });

  // Every analysed file, folded through the four stages. Null when the page is
  // rendered without the earlier stages' data (a direct/offline render).
  const project = useMemo(
    () => (projectContext ? classifyProject(projectContext, fileEntries) : null),
    [projectContext, fileEntries]
  );

  const tabs = [
    ...(project ? [["tree", `Project Tree (${project.totals.smellFiles} smell file(s))`]] : []),
    ["code", "Refactored Code"],
    ["validation", "Validation Report"],
    ["summary", "Summary"],
    ["invariants", "Invariants"],
  ];
  // The tree tab disappears without projectContext, so never leave `tab`
  // pointing at a tab that is not on screen.
  const activeTab = tabs.some(([key]) => key === tab) ? tab : tabs[0][0];

  /** Jump from a tree row to that file in the Refactored Code tab. */
  const openFileInCodeTab = (row) => {
    const idx = fileEntries.findIndex((f) => norm(f.path) === norm(row.path));
    if (idx === -1) return;
    setSelectedFileIndex(idx);
    setTab("code");
  };

  const activeFile = fileEntries[selectedFileIndex] || fileEntries[0] || null;
  const acceptedEntries = fileEntries.filter((f) => !f.rejected && f.changed);
  const revertedEntries = fileEntries.filter((f) => f.rejected);
  const stagedEntries = fileEntries.filter((f) => stagedFiles.has(f.path));

  const toggleStaged = (path) => {
    setStagedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  // ── Validation, taken from the agent's own report ─────────────────────────
  const validation = activeFile?.validation || sctva?.validation || null;
  const components = activeFile?.confidence_components || sctva?.confidence_components || null;
  const safetyReport = activeFile?.safety_report || sctva?.safety_report || null;
  const confidence = sctva?.confidence_score ?? activeFile?.confidence_score ?? null;
  const invariantDetails = validation?.invariant?.details || null;
  const invariantRecords = invariantDetails?.invariants || [];

  const appliedReplacements = acceptedEntries.reduce(
    (sum, f) => sum + (Number(f.total_replacements) || 0),
    0
  );

  // ── Commit actions — always write finalCode, never the rejected output ────
  const decisionPayload = () => ({
    acceptedFiles: stagedEntries.map((f) => ({ ...f, after: f.finalCode })),
    revertedFiles: revertedEntries.map((f) => f.path),
  });

  /**
   * Download one ZIP holding the whole project, not one file per click.
   *
   * The selected files carry their final code; every other file in the
   * repository is read back from the CUQA workspace so the archive extracts
   * as a complete project. If that structure is unreachable, the archive
   * falls back to the selected files alone.
   */
  const handleAcceptAndDownload = async () => {
    if (stagedEntries.length === 0) {
      alert("Select at least one file to write before downloading.");
      return;
    }

    setIsProcessing(true);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const finalFiles = stagedEntries.map((f) => ({
      path: f.path,
      content: f.finalCode,
      state: f.rejected ? "reverted_to_original" : "refactored",
    }));

    const baseManifest = {
      savedAt: new Date().toISOString(),
      request_id: sctva?.request_id || null,
      confidence_score: sctva?.confidence_score ?? null,
      reverted: revertedEntries.map((f) => f.path),
    };

    let entries;
    let manifest;

    try {
      const project = await buildProjectArchive({ finalFiles });
      entries = project.entries;
      manifest = { ...baseManifest, scope: "full_project", ...project.manifest };
    } catch (e) {
      console.warn("Full-project archive unavailable, packing selected files only", e);
      entries = finalFiles.map((f) => ({ path: f.path, content: f.content }));
      manifest = {
        ...baseManifest,
        scope: "selected_files_only",
        scope_reason: e.message,
        written: finalFiles.map((f) => ({ path: f.path, state: f.state })),
      };
    }

    try {
      const blob = await createZip([
        ...entries,
        { path: "REFACTORING_MANIFEST.json", content: JSON.stringify(manifest, null, 2) },
      ]);
      downloadBlob(blob, `diwo_project_${stamp}.zip`);
    } catch (e) {
      console.error("Failed to build the archive", e);
      alert(`Could not build the ZIP archive: ${e.message}`);
      setIsProcessing(false);
      return;
    }

    setIsProcessing(false);
    setShowActionChoice(false);
    if (onAccept) onAccept(decisionPayload());
  };

  /**
   * Write the refactored project onto a branch of a git repository.
   *
   * The payload is the SAME entry list the ZIP download packs: the whole
   * project, with the accepted refactorings replacing the originals in place.
   * Sending only the changed files — what this did before — left a freshly
   * cloned repository holding those files and nothing else, and told the user
   * nothing about whether the branch was actually committed or pushed.
   */
  const handlePushToGitHub = async () => {
    if (!branchName.trim()) {
      alert("Please enter a branch name.");
      return;
    }
    if (!repoPath.trim()) {
      alert("Enter a GitHub repository URL or the path to a local clone.");
      return;
    }
    if (stagedEntries.length === 0) {
      alert("Select at least one file to write before sharing to GitHub.");
      return;
    }

    setIsProcessing(true);
    setPushStatus("Collecting the full project…");

    const finalFiles = stagedEntries.map((f) => ({
      path: f.path,
      content: f.finalCode,
      state: f.rejected ? "reverted_to_original" : "refactored",
    }));

    // Whole project when the workspace is reachable; the accepted files alone
    // if it is not — writing a partial set into an existing clone still leaves
    // every other file in place, so it degrades sensibly.
    let payloadFiles;
    let scope;
    try {
      const project = await buildProjectArchive({ finalFiles });
      payloadFiles = project.entries.map((e) => ({ path: e.path, after: e.content }));
      scope = `full project (${project.entries.length} files, ${project.stats.replacedInPlace} replaced)`;
    } catch (e) {
      console.warn("Full-project push unavailable, sending selected files only", e);
      payloadFiles = finalFiles.map((f) => ({ path: f.path, after: f.content }));
      scope = `selected files only (${payloadFiles.length}) — ${e.message}`;
    }

    try {
      setPushStatus(`Writing ${payloadFiles.length} file(s) to '${branchName}'…`);
      const result = await applyAndPush({
        files: payloadFiles,
        branch_name: branchName.trim(),
        repository_path: repoPath.trim(),
        commit_message: commitMessage.trim() || `refactor: DIWO agent changes (${branchName.trim()})`,
        commit: true,
        push: pushToOrigin,
      });

      const lines = [
        result.message,
        "",
        `Repository: ${result.repository}${result.cloned ? " (clone of your GitHub URL)" : ""}`,
        `Branch:     ${result.branch}${result.base_branch ? ` (from ${result.base_branch})` : ""}`,
        `Written:    ${result.written_count} file(s) — ${scope}`,
        `Changed:    ${(result.staged_files || []).length} file(s) differ from the branch`,
      ];
      if (result.committed) lines.push(`Commit:     ${result.commit_sha}`);
      if (result.commit_error) lines.push(`Commit failed: ${result.commit_error}`);
      if (result.pushed) lines.push(`Pushed:     ${result.branch_url || "origin/" + result.branch}`);
      if (result.push_error) lines.push(`Not pushed: ${result.push_error}`);
      lines.push(
        result.github_desktop_opened
          ? "\nGitHub Desktop is opening on this repository."
          : `\nGitHub Desktop could not be launched (${result.github_desktop_detail || "not installed"}). Open the repository above manually.`
      );

      alert(lines.join("\n"));

      setShowActionChoice(false);
      if (onAccept) onAccept({ ...decisionPayload(), githubResult: result });
    } catch (error) {
      console.error("Failed to push to GitHub:", error);
      alert(`Could not write the branch: ${error.message}`);
    } finally {
      setIsProcessing(false);
      setPushStatus("");
    }
  };

  const statusColor = sctva?.rollback_occurred ? C.danger : sctva?.success ? C.accent : C.warn;

  return (
    <div>
      {/* ── Headline ──────────────────────────────────────────────────────── */}
      <Card glow={`${statusColor}20`} style={{ marginBottom: 20, textAlign: "center", padding: "24px" }}>
        <div style={{ fontSize: 48, fontWeight: 900, fontFamily: "monospace", color: statusColor }}>
          {typeof confidence === "number" ? pct(confidence, 0) : "N/A"}
        </div>
        <div style={{ fontSize: 13, color: C.textSub, marginTop: 2, marginBottom: 14 }}>
          Transformation Confidence Score
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
          <Pill
            label={sctva?.success ? "✓ Transformation Successful" : "⚠ Transformation Incomplete"}
            color={sctva?.success ? C.accent : C.warn}
          />
          <Pill
            label={sctva?.rollback_occurred ? "↩ Rolled Back by Agent" : "✓ No Agent Rollback"}
            color={sctva?.rollback_occurred ? C.danger : C.accent}
          />
          <Pill label={`${acceptedEntries.length} file(s) kept refactored`} color={C.accent} />
          <Pill
            label={`${revertedEntries.length} file(s) reverted to original`}
            color={revertedEntries.length ? C.danger : C.textMuted}
          />
        </div>
        {safetyReport?.summary && (
          <div style={{ marginTop: 14, fontSize: 12, color: C.textMuted }}>{safetyReport.summary}</div>
        )}
        {!sctva && (
          <div style={{ marginTop: 14, fontSize: 12, color: C.warn }}>
            No agent report attached — these results came from the DIWO backend's simulated
            transformation.
          </div>
        )}
      </Card>

      {/* ── Tabs ──────────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${C.border}` }}>
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            style={{
              padding: "8px 16px",
              borderRadius: "8px 8px 0 0",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              border: "none",
              background: activeTab === key ? C.accent : "transparent",
              color: activeTab === key ? "#000" : C.textMuted,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Summary ───────────────────────────────────────────────────────── */}
      {activeTab === "summary" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
          {[
            ["Files Transformed", fileEntries.length, ""],
            ["Kept Refactored", acceptedEntries.length, "files"],
            ["Reverted to Original", revertedEntries.length, "files"],
            ["Replacements Applied", appliedReplacements, ""],
            ["Validation Score", pct(sctva?.validation_score), ""],
            ["Confidence Score", typeof confidence === "number" ? pct(confidence) : "N/A", ""],
          ].map(([label, value, unit]) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>
                {label}
              </div>
              <div style={{ fontSize: 22, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>
                {value}
                {unit ? ` ${unit}` : ""}
              </div>
            </div>
          ))}

          {components && (
            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 10 }}>
                Confidence Formula
              </div>
              <div style={{ fontSize: 11, color: C.textSub, fontFamily: "monospace", marginBottom: 10, wordBreak: "break-word" }}>
                {components.formula}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {COMPONENT_LABELS.filter(([key]) => key in components).map(([key, label]) => {
                  const weight = components.weights?.[label.toLowerCase()];
                  return (
                    <div key={key} style={{ background: C.bg, borderRadius: 6, padding: "8px 12px", textAlign: "center" }}>
                      <div style={{ fontSize: 16, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>
                        {pct(components[key], 2)}
                      </div>
                      <div style={{ fontSize: 10, color: C.textMuted }}>
                        {label}
                        {typeof weight === "number" ? ` (${(weight * 100).toFixed(0)}%)` : ""}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {revertedEntries.length > 0 && (
            <div style={{ background: C.panel, border: `1px solid ${C.danger}40`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
              <div style={{ fontSize: 11, color: C.danger, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8, fontWeight: 700 }}>
                Reverted files
              </div>
              <div style={{ fontSize: 12, color: C.textSub, marginBottom: 8 }}>
                Rejected in the Transformation stage. The original source is what this page shows
                and what any download or commit will write.
              </div>
              {revertedEntries.map((f) => (
                <div key={f.path} style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub, padding: "2px 0" }}>
                  <span style={{ color: C.danger, marginRight: 8 }}>↩</span>
                  {f.path}
                  {!f.revertable && (
                    <span style={{ color: C.warn, marginLeft: 8 }}>
                      (original text unavailable — cannot revert)
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Validation report ─────────────────────────────────────────────── */}
      {activeTab === "validation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {fileEntries.length > 1 && (
            <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 2 }}>
              Showing the report for <span style={{ color: C.text, fontFamily: "monospace" }}>{activeFile?.path}</span>.
              Pick another file in the Refactored Code tab to see its report.
            </div>
          )}

          {!validation && (
            <Card>
              <div style={{ fontSize: 12, color: C.textMuted }}>
                No validation report is attached to this result.
              </div>
            </Card>
          )}

          {validation &&
            Object.entries(VALIDATION_LABELS).map(([key, [label, blurb]]) => {
              const step = validation[key];
              if (!step) return null;
              const color = step.passed ? C.accent : C.danger;
              return (
                <div key={key} style={{ background: C.panel, border: `1px solid ${color}30`, borderRadius: 8, padding: "14px 18px", display: "flex", alignItems: "center", gap: 16 }}>
                  <div style={{ width: 36, height: 36, borderRadius: "50%", background: `${color}20`, border: `2px solid ${color}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <span style={{ color, fontWeight: 900 }}>{step.passed ? "✓" : "✕"}</span>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 700, color: C.text, fontSize: 13 }}>{label}</div>
                    <div style={{ fontSize: 12, color: C.textSub, marginTop: 2 }}>{step.message || blurb}</div>
                    {typeof step.duration_ms === "number" && (
                      <div style={{ fontSize: 10, color: C.textMuted, marginTop: 4 }}>
                        completed in {step.duration_ms} ms
                      </div>
                    )}
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 18, fontWeight: 800, color, fontFamily: "monospace" }}>
                      {pct(step.score, 2)}
                    </div>
                    <Pill label={step.passed ? "PASSED" : "FAILED"} color={color} />
                  </div>
                </div>
              );
            })}

          {safetyReport?.transformation_log?.length > 0 && (
            <Card style={{ marginTop: 4 }}>
              <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Transformation Log
              </div>
              {safetyReport.transformation_log.map((entry) => (
                <div key={`log-${entry.action_index}`} style={{ padding: "6px 0", borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
                      #{entry.action_index}
                    </span>
                    <span style={{ fontSize: 12, color: C.text, fontFamily: "monospace" }}>
                      {entry.action_type}
                    </span>
                    <Badge
                      label={`${entry.replacements_count} replacement(s)`}
                      color={entry.replacements_count > 0 ? C.accent : C.textMuted}
                    />
                  </div>
                  {(entry.warnings || []).map((w, i) => (
                    <div key={`w-${i}`} style={{ fontSize: 11, color: C.warn, marginTop: 3, paddingLeft: 24 }}>
                      ⚑ {w}
                    </div>
                  ))}
                </div>
              ))}
            </Card>
          )}

          {safetyReport && (
            <Card style={{ marginTop: 4 }}>
              <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 8, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Safety Messages
              </div>
              {safetyReport.rollback_reason && (
                <div style={{ fontSize: 12, color: C.danger, padding: "4px 0" }}>
                  ↩ {safetyReport.rollback_reason}
                </div>
              )}
              {(safetyReport.risk_flags || []).map((flag) => (
                <div key={flag} style={{ fontSize: 12, color: C.warn, padding: "4px 0" }}>
                  ⚑ {flag}
                </div>
              ))}
              {(safetyReport.human_messages || []).map((msg, i) => (
                <div key={`m-${i}`} style={{ fontSize: 12, color: C.textSub, padding: "4px 0" }}>
                  <span style={{ color: C.accent, marginRight: 8 }}>→</span>
                  {msg}
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {/* ── Project tree ──────────────────────────────────────────────────── */}
      {activeTab === "tree" && project && (
        <div>
          {/* What happened to the smells CUQA found, in one line each. */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, marginBottom: 16 }}>
            {[
              { label: "Files analysed", val: project.totals.analysed, color: C.textSub },
              { label: "Files with smells", val: project.totals.smellFiles, color: C.danger },
              { label: "Refactored", val: project.refactored.length, color: C.accent },
              { label: "Not selected", val: project.notSelected.length, color: OUTCOMES.not_selected.color },
              { label: "Plan dropped", val: project.planDropped.length, color: C.warn },
              { label: "Change not kept", val: project.changeRejected.length, color: C.danger },
            ].map(({ label, val, color }) => (
              <div key={label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderLeft: `3px solid ${color}`, borderRadius: 8, padding: "12px 14px" }}>
                <div style={{ fontSize: 22, fontWeight: 800, color, fontFamily: "monospace" }}>{val}</div>
                <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginTop: 2 }}>{label}</div>
              </div>
            ))}
          </div>

          <Card style={{ marginBottom: 16, padding: "14px 18px" }}>
            <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.7 }}>
              CUQA detected <b style={{ color: C.danger }}>{project.totals.smells}</b> smell(s) across{" "}
              <b style={{ color: C.danger }}>{project.totals.smellFiles}</b> file(s).{" "}
              <b style={{ color: C.accent }}>{project.totals.smellsSelected}</b> were selected for refactoring, and{" "}
              <b style={{ color: C.accent }}>{project.refactored.length}</b> file(s) came through the pipeline
              refactored and kept.{" "}
              <b style={{ color: C.warn }}>{project.withSmells.length - project.refactored.length}</b> file(s)
              with smells were <b>not</b> refactored — the trees below say where each one stopped.
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
              {Object.entries(OUTCOMES)
                .filter(([key]) => key !== "clean")
                .map(([key, o]) => (
                  <span key={key} title={o.hint}>
                    <Badge label={o.label} color={o.color} />
                  </span>
                ))}
              <span style={{ fontSize: 11, color: C.textMuted }}>
                🔴 red rail = CUQA detected code smells in that file
              </span>
            </div>
          </Card>

          {/* Full project on the left; the files that never made it through on
              the right, one tree per stage that dropped them. */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1.2fr) minmax(300px, 1fr)", gap: 12, alignItems: "start" }}>
            <TreePanel
              title="Whole project"
              subtitle="Every file CUQA analysed, in its folder structure. Files with detected smells carry a red rail; the badge is where the file ended up. Click a refactored file to open it in the Refactored Code tab."
              color={C.info}
              rows={project.rows}
              empty="No analysed files were reported."
              onSelect={openFileInCodeTab}
              selectedPath={activeFile ? norm(activeFile.path) : null}
              maxHeight={560}
            />

            <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
              <TreePanel
                title="Refactored"
                subtitle="Smells detected, selected, planned, approved, transformed and kept."
                color={C.accent}
                rows={project.refactored}
                empty="No file was refactored and kept."
                onSelect={openFileInCodeTab}
                selectedPath={activeFile ? norm(activeFile.path) : null}
                maxHeight={200}
              />
              <TreePanel
                title="Smells found, not selected"
                subtitle="Detected in Code Smell Review but left out of the selection, so they never reached the planner."
                color={OUTCOMES.not_selected.color}
                rows={project.notSelected}
                empty="Every file with smells was selected."
                maxHeight={200}
              />
              <TreePanel
                title="Dropped at planning"
                subtitle="Selected, but the plan steps were rejected in Plan Approval — or the RDP agent produced no step for the file."
                color={C.warn}
                rows={project.planDropped}
                empty="No selected file was dropped at planning."
                maxHeight={200}
              />
              <TreePanel
                title="Change not kept"
                subtitle="Reached the Transformation stage but was rejected there, came back unchanged, or never returned from the agent."
                color={C.danger}
                rows={project.changeRejected}
                empty="Every transformed file was kept."
                onSelect={openFileInCodeTab}
                selectedPath={activeFile ? norm(activeFile.path) : null}
                maxHeight={200}
              />
            </div>
          </div>
        </div>
      )}

      {/* ── Refactored code ───────────────────────────────────────────────── */}
      {activeTab === "code" && (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <div style={{ width: 270, flexShrink: 0, maxHeight: 460, overflow: "auto", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8, padding: "0 4px" }}>
              Files ({fileEntries.length})
            </div>
            {fileEntries.length === 0 && (
              <div style={{ color: C.textMuted, fontSize: 12, padding: 8 }}>No files available.</div>
            )}
            {fileEntries.map((f, idx) => {
              const isStaged = stagedFiles.has(f.path);
              const rail = f.rejected ? C.danger : C.accent;
              return (
                <div
                  key={f.path}
                  onClick={() => setSelectedFileIndex(idx)}
                  style={{
                    padding: 10,
                    borderRadius: 6,
                    cursor: "pointer",
                    marginBottom: 6,
                    background: selectedFileIndex === idx ? `${C.accent}12` : "transparent",
                    border: `1px solid ${selectedFileIndex === idx ? C.accent : C.border}`,
                    borderLeft: `3px solid ${rail}`,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>
                    {f.path.split("/").pop()}
                  </div>
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 3, wordBreak: "break-all" }}>
                    {f.path}
                  </div>
                  <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Badge
                      label={f.rejected ? "reverted" : f.changed ? "refactored" : "unchanged"}
                      color={f.rejected ? C.danger : f.changed ? C.accent : C.textMuted}
                    />
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleStaged(f.path);
                    }}
                    style={{
                      marginTop: 8,
                      width: "100%",
                      padding: "5px 10px",
                      borderRadius: 6,
                      cursor: "pointer",
                      border: `1px solid ${isStaged ? C.accent : C.border}`,
                      background: isStaged ? C.accent : "transparent",
                      color: isStaged ? "#000" : C.textMuted,
                      fontSize: 11,
                      fontWeight: 700,
                    }}
                  >
                    {isStaged ? "✓ Will be written" : "Write this file"}
                  </button>
                </div>
              );
            })}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 12, flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: C.textSub, fontFamily: "monospace", wordBreak: "break-all" }}>
                {activeFile?.path || "No file"}
                {activeFile?.language ? ` · ${String(activeFile.language).toUpperCase()}` : ""}
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {[
                  ["final", activeFile?.rejected ? "Final Code (original)" : "Final Code"],
                  ["diff", "Diff"],
                  ["before", "Original"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => setView(key)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 700,
                      cursor: "pointer",
                      border: `1px solid ${view === key ? C.accent : C.border}`,
                      background: view === key ? C.accent : "transparent",
                      color: view === key ? "#000" : C.textMuted,
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {activeFile?.rejected && (
              <div style={{ marginBottom: 10, padding: "10px 14px", borderRadius: 8, background: `${C.danger}10`, border: `1px solid ${C.danger}40`, fontSize: 12, color: C.textSub }}>
                <span style={{ color: C.danger, fontWeight: 700 }}>↩ Reverted. </span>
                {activeFile.revertable
                  ? "This refactoring was rejected, so the original source is shown and is what will be written. The Diff tab still shows the change that was declined."
                  : "This refactoring was rejected, but the original source did not reach this page, so it cannot be reverted here."}
              </div>
            )}

            {view === "diff" && <DiffLegend />}
            {view === "diff" && activeFile?.rejected && (
              <div style={{ fontSize: 11, color: C.warn, marginBottom: 8 }}>
                Declined change — shown for reference only; it will not be written.
              </div>
            )}

            <CodeSurface>
              {view === "diff" ? (
                <DiffBlock rows={activeFile?.diff_rows} />
              ) : (
                <CodeBlock
                  code={view === "before" ? activeFile?.before : activeFile?.finalCode}
                  emptyMessage={
                    view === "before"
                      ? "Original source is not available for this file."
                      : "No code available for this file."
                  }
                />
              )}
            </CodeSurface>
          </div>
        </div>
      )}

      {/* ── Invariants ────────────────────────────────────────────────────── */}
      {activeTab === "invariants" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: C.textSub, marginBottom: 4 }}>
            {invariantDetails?.summary ||
              "Program invariants mined from the original and the transformed source."}
          </div>

          {invariantRecords.length === 0 && (
            <Card>
              <div style={{ fontSize: 12, color: C.textMuted }}>
                {invariantDetails
                  ? `No individual invariants were recorded (status: ${invariantDetails.status || "unknown"}).`
                  : "No invariant report is attached to this result."}
              </div>
            </Card>
          )}

          {invariantRecords.map((inv, idx) => {
            const preserved = inv.status === "preserved" || inv.preserved;
            const skipped = inv.status === "skipped";
            const color = skipped ? C.textMuted : preserved ? C.accent : C.danger;
            return (
              <div key={`${inv.name}-${idx}`} style={{ background: C.panel, border: `1px solid ${color}25`, borderRadius: 8, padding: "12px 16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ color }}>{skipped ? "–" : preserved ? "✓" : "✕"}</span>
                  <span style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub }}>{inv.name}</span>
                  <Pill
                    label={skipped ? "SKIPPED" : preserved ? "PRESERVED" : "VIOLATED"}
                    color={color}
                  />
                  {inv.critical && !preserved && <Badge label="critical" color={C.danger} />}
                </div>
                {inv.reason && (
                  <div style={{ fontSize: 11, color: C.textMuted, marginTop: 6 }}>{inv.reason}</div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── Actions ───────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, marginTop: 24, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, color: C.textSub }}>
          Files to write: <span style={{ color: C.accent, fontWeight: 700 }}>{stagedEntries.length}</span> / {fileEntries.length}
          {revertedEntries.length > 0 && (
            <span style={{ color: C.danger, marginLeft: 10 }}>
              ({revertedEntries.length} reverted to original)
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={onRollback} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30` }}>
            ↩ Request Rollback
          </button>
          <button onClick={onRestart} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.info}15`, color: C.info, border: `1px solid ${C.info}30` }}>
            ↺ New Refactoring Session
          </button>
          <button onClick={() => setShowActionChoice(true)} style={{ padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: C.accent, color: "#000", border: "none", boxShadow: `0 0 20px ${C.accentGlow}` }}>
            ✓ Accept &amp; Commit Changes
          </button>
        </div>
      </div>

      {showActionChoice && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, maxWidth: 480, boxShadow: "0 20px 60px rgba(0,0,0,0.3)" }}>
            <h2 style={{ marginTop: 0, color: C.text, marginBottom: 12 }}>How would you like to proceed?</h2>
            <p style={{ color: C.textSub, marginBottom: 8 }}>
              {stagedEntries.length} file(s) will be written.
            </p>
            {revertedEntries.some((f) => stagedFiles.has(f.path)) && (
              <p style={{ color: C.warn, fontSize: 12, marginBottom: 16 }}>
                Rejected files in this set are written back as their original source.
              </p>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
              <div
                onClick={handleAcceptAndDownload}
                style={{ background: C.panel, border: `2px solid ${C.border}`, borderRadius: 8, padding: 16, cursor: "pointer", transition: "all 0.2s" }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = C.accent)}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = C.border)}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ fontSize: 24 }}>💾</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text, marginBottom: 4 }}>
                      {isProcessing ? "Building archive…" : "Download Project (.zip)"}
                    </div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>
                      One archive holding the whole project in its folder structure — your accepted
                      changes applied, every other file as-is.
                    </div>
                  </div>
                </div>
              </div>

              <div style={{ background: C.panel, border: `2px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 24 }}>🚀</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text }}>Push to GitHub</div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>
                      Writes the whole project — your accepted changes applied, every other file
                      as-is — onto its own branch, commits it, and opens GitHub Desktop there.
                    </div>
                  </div>
                </div>

                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>
                    GitHub repository URL or local clone path
                  </label>
                  <input
                    type="text"
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder="https://github.com/user/repo.git  —  or  C:\\Users\\You\\repo"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.bg, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, boxSizing: "border-box" }}
                  />
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 4, lineHeight: 1.5 }}>
                    A URL is cloned once into <span style={{ fontFamily: "monospace" }}>~/DIWO/repos/</span> and
                    reused on later runs, so GitHub Desktop can open the same working copy.
                  </div>
                </div>

                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Branch name</label>
                  <input
                    type="text"
                    value={branchName}
                    onChange={(e) => setBranchName(e.target.value)}
                    placeholder="e.g., refactoring/diwo-changes"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.bg, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, boxSizing: "border-box" }}
                  />
                </div>

                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Commit message</label>
                  <input
                    type="text"
                    value={commitMessage}
                    onChange={(e) => setCommitMessage(e.target.value)}
                    placeholder="refactor: apply DIWO agent refactorings"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.bg, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, boxSizing: "border-box" }}
                  />
                </div>

                <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={pushToOrigin}
                    onChange={(e) => setPushToOrigin(e.target.checked)}
                    style={{ accentColor: C.accent, cursor: "pointer" }}
                  />
                  <span style={{ fontSize: 12, color: C.textSub }}>
                    Push the branch to <span style={{ fontFamily: "monospace" }}>origin</span> after committing
                  </span>
                </label>

                <button
                  onClick={handlePushToGitHub}
                  disabled={isProcessing}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.accent, color: "#000", border: "none", fontWeight: 700, fontSize: 12, cursor: isProcessing ? "not-allowed" : "pointer", opacity: isProcessing ? 0.6 : 1 }}
                >
                  {isProcessing
                    ? pushStatus || "Processing…"
                    : `✓ Commit${pushToOrigin ? " & Push" : ""} Branch, then open GitHub Desktop`}
                </button>
              </div>

              <button
                onClick={() => setShowActionChoice(false)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.border, color: C.text, border: "none", fontWeight: 700, fontSize: 12, cursor: "pointer" }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
