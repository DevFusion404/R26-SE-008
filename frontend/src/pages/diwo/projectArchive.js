/**
 * Whole-project archive assembly
 * ==============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Produces the archive the developer takes away at the end of a session: the
 * complete project, not just the handful of files the agents touched.
 *
 *   untouched files  → their original source, straight from the workspace
 *   refactored files → the code the developer accepted
 *   rejected files   → the original they were reverted to
 *
 * Two services supply the pieces, because neither has both halves:
 *
 *   GET  http://localhost:8080/api/project-structure   (CUQA)
 *        the file tree of the loaded repository — names and paths only.
 *
 *   POST http://localhost:8002/sctva/cuqa-sources      (SCTVA)
 *        reads those paths back out of the CUQA temp workspace as text.
 *        Capped at 1000 paths per call, so requests are batched.
 *
 * The DIWO workflow's own files are overlaid last, so a refactored file always
 * wins over the original copy read from the workspace.
 */

import { fetchWorkspaceSources } from "./sctvaApi";
import { normalizeZipPath } from "./zipWriter";

export const CUQA_BASE = (
  import.meta?.env?.VITE_CUQA_API_URL || "http://localhost:8080"
).replace(/\/+$/, "");

export const PROJECT_STRUCTURE_URL = `${CUQA_BASE}/api/project-structure`;

/** SCTVA truncates the path list at 1000 entries, so send it in batches. */
const SOURCE_BATCH_SIZE = 400;

/** Hard ceiling so a huge repository cannot lock up the tab. */
const MAX_PROJECT_FILES = 3000;

/**
 * Extensions read as binary. `/sctva/cuqa-sources` decodes everything as UTF-8
 * with replacement characters, so including these would ship corrupted files
 * rather than the originals. They are reported instead of silently mangled.
 */
const BINARY_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "svgz", "tiff",
  "pdf", "zip", "jar", "war", "ear", "tar", "gz", "bz2", "7z", "rar",
  "class", "o", "so", "dll", "exe", "bin", "dat", "pyc", "pyd",
  "ttf", "otf", "woff", "woff2", "eot",
  "mp3", "mp4", "wav", "avi", "mov", "webm",
  "db", "sqlite", "sqlite3",
]);

const extensionOf = (path) => {
  const name = String(path || "").split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
};

export const isBinaryPath = (path) => BINARY_EXTENSIONS.has(extensionOf(path));

function projectError(message, { status = 0, reachable = false } = {}) {
  const err = new Error(message);
  err.status = status;
  err.reachable = reachable;
  return err;
}

// ─── Matching a workflow file to its place in the project ────────────────────

const matchKey = (value) => normalizeZipPath(value, "").toLowerCase();
const baseNameOf = (value) => String(value || "").split("/").pop();

/**
 * Find the refactored file belonging to a project path.
 *
 * The agents do not always name a file the same way the project tree does —
 * CUQA can report "demo/src/Main.java" where SCTVA answers "src/Main.java" —
 * so an exact-only lookup silently misses and the refactored copy ends up
 * beside the original instead of replacing it. The fallbacks mirror
 * SCTVAAgentPage.findResultForSource and SCTVA's own `_file_matches`:
 *
 *   1. exact path
 *   2. one path is a suffix of the other
 *   3. same file name, but only when that name is unique on both sides —
 *      two different Helper.java must never overwrite each other
 */
function findOverlayForPath(projectPath, overlays, { uniqueProjectBases, uniqueOverlayBases }) {
  const target = matchKey(projectPath);
  if (!target) return null;

  const exact = overlays.find((o) => !o.consumed && o.key === target);
  if (exact) return exact;

  const suffix = overlays.find(
    (o) => !o.consumed && (o.key.endsWith(`/${target}`) || target.endsWith(`/${o.key}`))
  );
  if (suffix) return suffix;

  const base = baseNameOf(target);
  if (uniqueProjectBases.has(base) && uniqueOverlayBases.has(base)) {
    return overlays.find((o) => !o.consumed && o.base === base) || null;
  }

  return null;
}

/** Names that appear exactly once in a list of paths. */
function uniqueBaseNames(paths) {
  const counts = new Map();
  paths.forEach((p) => {
    const base = baseNameOf(matchKey(p));
    counts.set(base, (counts.get(base) || 0) + 1);
  });
  return new Set([...counts.entries()].filter(([, n]) => n === 1).map(([base]) => base));
}

// ─── CUQA project structure ──────────────────────────────────────────────────

/** Fetch the loaded repository's file tree from the CUQA agent. */
export async function fetchProjectStructure({ signal } = {}) {
  let res;
  try {
    res = await fetch(PROJECT_STRUCTURE_URL, { signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw projectError(
      `The Code Understanding agent (${CUQA_BASE}) could not be reached, so the full project ` +
        "structure is unavailable. Start it with: uvicorn main:app --port 8080 " +
        "(from agents/cuqa_agent/src).",
      { status: 503 }
    );
  }

  let json = {};
  try {
    json = await res.json();
  } catch {
    json = {};
  }

  if (!res.ok) {
    throw projectError(
      json.detail || json.error || `CUQA returned HTTP ${res.status} for the project structure.`,
      { status: res.status, reachable: true }
    );
  }

  return {
    repoName: json.repo_name || null,
    source: json.source || null,
    totalSourceFiles: json.total_source_files ?? null,
    tree: json.tree || null,
  };
}

/** Depth-first list of every file node in a CUQA tree. */
export function flattenProjectTree(node, out = []) {
  if (!node || typeof node !== "object") return out;

  if (node.type === "file") {
    const path = normalizeZipPath(node.path || node.name, "");
    if (path) out.push({ path, name: node.name || path.split("/").pop(), language: node.language || null });
    return out;
  }

  (node.children || []).forEach((child) => flattenProjectTree(child, out));
  return out;
}

// ─── Archive assembly ────────────────────────────────────────────────────────

/**
 * Read the original text of many workspace files, batching around the
 * 1000-path cap on /sctva/cuqa-sources.
 */
async function readWorkspaceFiles(paths, { signal, onProgress } = {}) {
  const found = new Map();
  const missing = [];

  for (let i = 0; i < paths.length; i += SOURCE_BATCH_SIZE) {
    const batch = paths.slice(i, i + SOURCE_BATCH_SIZE);
    const result = await fetchWorkspaceSources(batch, { signal });

    (result.files || []).forEach((f) => {
      const path = normalizeZipPath(f.file_name, "");
      if (path) found.set(path, f.source_code ?? "");
    });
    (result.missing || []).forEach((p) => missing.push(p));

    onProgress?.({
      phase: "sources",
      done: Math.min(i + batch.length, paths.length),
      total: paths.length,
    });
  }

  return { found, missing };
}

/**
 * Build the entry list for a whole-project archive.
 *
 * @param {{path: string, content: string, state: string}[]} finalFiles
 *        the DIWO workflow's files, already resolved to the code the developer
 *        settled on (accepted → refactored, rejected → original).
 * @returns {Promise<{entries, stats, manifest}>}
 */
export async function buildProjectArchive({ finalFiles = [], signal, onProgress } = {}) {
  onProgress?.({ phase: "structure" });
  const structure = await fetchProjectStructure({ signal });

  const projectFiles = flattenProjectTree(structure.tree);
  if (projectFiles.length === 0) {
    throw projectError(
      "The CUQA agent has a repository loaded but its project structure contains no files.",
      { status: 422, reachable: true }
    );
  }
  if (projectFiles.length > MAX_PROJECT_FILES) {
    throw projectError(
      `The project has ${projectFiles.length} files, above the ${MAX_PROJECT_FILES} this archive ` +
        "builder handles in the browser. Use the repository directly for a project this size.",
      { status: 413, reachable: true }
    );
  }

  // The workflow's files, ready to be matched onto their place in the tree.
  const overlays = finalFiles
    .filter((f) => typeof f.content === "string" && f.content.length > 0)
    .map((f) => {
      const key = matchKey(f.path);
      return { key, base: baseNameOf(key), content: f.content, state: f.state || "refactored", consumed: false };
    })
    .filter((o) => o.key);

  const matchContext = {
    uniqueProjectBases: uniqueBaseNames(projectFiles.map((f) => f.path)),
    uniqueOverlayBases: uniqueBaseNames(overlays.map((o) => o.key)),
  };

  // Resolve every project file to its refactored replacement first, so a file
  // that is being replaced is never also read back from the workspace.
  const assignments = projectFiles.map((file) => {
    const overlay = findOverlayForPath(file.path, overlays, matchContext);
    if (overlay) overlay.consumed = true;
    return { file, overlay };
  });

  const binary = assignments.filter((a) => !a.overlay && isBinaryPath(a.file.path)).map((a) => a.file);
  const binaryPaths = new Set(binary.map((f) => f.path));

  const toRead = assignments
    .filter((a) => !a.overlay && !binaryPaths.has(a.file.path))
    .map((a) => a.file.path);

  const { found, missing } = await readWorkspaceFiles(toRead, { signal, onProgress });

  onProgress?.({ phase: "zipping" });

  const entries = [];
  const manifestFiles = [];

  // Walk the project order so the archive mirrors the repository layout, and
  // write the refactored content AT THE PROJECT PATH — replacing the original
  // file rather than sitting next to it.
  assignments.forEach(({ file, overlay }) => {
    if (overlay) {
      entries.push({ path: file.path, content: overlay.content });
      manifestFiles.push({
        path: file.path,
        state: overlay.state,
        ...(overlay.key === matchKey(file.path) ? {} : { matched_from: overlay.key }),
      });
      return;
    }
    if (binaryPaths.has(file.path)) return;

    if (found.has(file.path)) {
      entries.push({ path: file.path, content: found.get(file.path) });
      manifestFiles.push({ path: file.path, state: "unchanged" });
    }
  });

  // Only a workflow file with no counterpart anywhere in the tree is added on
  // its own — for example a file the agents created.
  const unplaced = overlays.filter((o) => !o.consumed);
  unplaced.forEach((o) => {
    entries.push({ path: o.key, content: o.content });
    manifestFiles.push({ path: o.key, state: o.state, added: true });
  });

  const stats = {
    projectFiles: projectFiles.length,
    included: entries.length,
    refactored: manifestFiles.filter((f) => f.state === "refactored").length,
    reverted: manifestFiles.filter((f) => f.state === "reverted_to_original").length,
    unchanged: manifestFiles.filter((f) => f.state === "unchanged").length,
    replacedInPlace: assignments.filter((a) => a.overlay).length,
    addedOutsideTree: unplaced.length,
    binarySkipped: binary.length,
    unreadable: missing.length,
    repoName: structure.repoName,
  };

  return {
    entries,
    stats,
    manifest: {
      repo_name: structure.repoName,
      source: structure.source,
      files: manifestFiles,
      skipped_binary: binary.map((f) => f.path),
      unreadable: missing,
      counts: {
        project_files: stats.projectFiles,
        included: stats.included,
        refactored: stats.refactored,
        reverted_to_original: stats.reverted,
        unchanged: stats.unchanged,
        replaced_in_place: stats.replacedInPlace,
        added_outside_tree: stats.addedOutsideTree,
        skipped_binary: stats.binarySkipped,
        unreadable: stats.unreadable,
      },
    },
  };
}

/**
 * The code that leaves the workflow per file, honouring the Stage 3 verdict.
 *
 * Accepted files carry their refactored source; rejected files carry the
 * original they were reverted to. A rejected file whose original never reached
 * the frontend is dropped rather than shipping the change that was declined —
 * the workspace copy then supplies the untouched original instead.
 */
export function resolveWorkflowFiles(files = []) {
  return (files || [])
    .map((f) => {
      const before = f.before ?? "";
      const after = f.after ?? f.refactored_code ?? "";
      const rejected = f.decision === "reject";
      if (rejected && !before) return null;
      return {
        path: normalizeZipPath(f.path || f.file || f.relative_path, ""),
        content: rejected ? before : after,
        state: rejected ? "reverted_to_original" : "refactored",
      };
    })
    .filter((f) => f && f.path && f.content);
}
