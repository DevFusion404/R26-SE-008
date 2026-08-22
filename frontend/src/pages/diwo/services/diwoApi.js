/**
 * DIWO Orchestration backend client
 * =================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The ONLY place the DIWO frontend makes a network call. Every agent hand-off
 * goes through the Orchestration Agent (Flask, default http://localhost:5001,
 * blueprints mounted under /api):
 *
 *     DIWO stage  ->  diwoApi.js  ->  Orchestration  ->  CUQA / RDP / SCTVA
 *
 * There is no DIWO -> agent shortcut left. The browser used to post the
 * approved plan straight to SCTVA :8002 and read the project tree straight
 * from CUQA :8080; both now run server-side, which means one base URL, one
 * CORS origin, and one place where an agent contract can drift.
 *
 * Consolidated from: the inline `api` object in DIWOAgentPage.jsx, the raw
 * fetch in CodeSmellApprovalPage.jsx, the HTTP half of the former
 * diwo/cuqaApi.js, and the whole network half of diwo/sctvaApi.js. The pure
 * shaping helpers live in utils/ (cuqaReport, planTrace, diffMapper,
 * transformationMapper).
 *
 * This module is transport only — no workflow rules, no shaping.
 */

import { normalizeCuqaReport, detectPrimaryLanguage } from "../utils/cuqaReport";

// ─── Base URLs ───────────────────────────────────────────────────────────────

/** Orchestration backend, already including its /api prefix. */
export const DIWO_BASE =
  import.meta?.env?.VITE_API_URL || "http://localhost:5001/api";

/**
 * CUQA agent. Used ONLY by the read-only fallback below, for when the DIWO
 * backend itself is unreachable and the Code Smell Review stage would
 * otherwise show nothing at all. No workflow action ever runs against it.
 */
export const CUQA_BASE = (
  import.meta?.env?.VITE_CUQA_API_URL || "http://localhost:8080"
).replace(/\/+$/, "");

export const CUQA_QUALITY_REPORT_URL = `${CUQA_BASE}/api/quality-report`;

// ─── Transport ───────────────────────────────────────────────────────────────

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}

/**
 * Low-level verbs against the orchestration backend. Kept exported because the
 * workflow controller drives a handful of one-off endpoints (complete,
 * audit-logs, the reset routes) that do not warrant a named wrapper each.
 */
export const api = {
  async post(path, body) {
    const res = await fetch(`${DIWO_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
    return json;
  },
  async get(path) {
    const res = await fetch(`${DIWO_BASE}${path}`);
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
    return json;
  },
};

// ─── Stage 1 — CUQA report ingestion (through the orchestrator) ──────────────

/** An error carrying enough context for the UI to explain what to start/load. */
function cuqaError(message, { status = 0, cuqaUrl = CUQA_BASE, reachable = false } = {}) {
  const err = new Error(message);
  err.status = status;
  err.cuqaUrl = cuqaUrl;
  err.reachable = reachable;
  return err;
}

/**
 * Call the CUQA agent directly. Used when the DIWO backend is unreachable so
 * the Code Smell Review stage still shows Agent 1 output (read-only: without a
 * DIWO workflow the selection cannot be persisted).
 */
async function fetchQualityReportDirect({ filePath, signal }) {
  let res;
  try {
    res = await fetch(CUQA_QUALITY_REPORT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filePath ? { file_path: filePath } : {}),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw cuqaError(
      `Neither the DIWO backend (${DIWO_BASE}) nor the CUQA agent ` +
        `(${CUQA_QUALITY_REPORT_URL}) could be reached. Start the CUQA agent with: ` +
        `uvicorn main:app --port 8080 (from agents/cuqa_agent/src).`,
      { status: 503 }
    );
  }

  const json = await readJson(res);
  if (!res.ok) {
    throw cuqaError(
      json.detail || json.error || `CUQA agent returned HTTP ${res.status}.`,
      { status: res.status, cuqaUrl: CUQA_QUALITY_REPORT_URL, reachable: true }
    );
  }

  const report = normalizeCuqaReport(json);
  return {
    report,
    smells: [],
    language: detectPrimaryLanguage(report),
    reportType: report.report_type,
    via: "cuqa-direct",
    cuqaUrl: CUQA_BASE,
  };
}

/**
 * Fetch the CUQA quality report — DIWO proxy first, direct CUQA as fallback.
 *
 * Resolves to { report, smells, language, reportType, via, cuqaUrl }.
 * `smells` is the flat DIWO smell list; it is empty on the direct route.
 */
export async function fetchQualityReport({ filePath = null, signal } = {}) {
  let res;
  try {
    res = await fetch(`${DIWO_BASE}/cuqa/quality-report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filePath ? { file_path: filePath } : {}),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    // DIWO backend itself is down — try Agent 1 directly.
    return fetchQualityReportDirect({ filePath, signal });
  }

  const json = await readJson(res);
  if (!res.ok) {
    // The proxy answered, so this is CUQA's own verdict (400 = no repo loaded,
    // 503 = CUQA not running). Surface it instead of retrying elsewhere.
    throw cuqaError(json.error || `HTTP ${res.status}: ${res.statusText}`, {
      status: res.status,
      cuqaUrl: json.cuqa_url || CUQA_BASE,
      reachable: json.reachable ?? res.status !== 503,
    });
  }

  return {
    report: json.report,
    smells: json.smells || [],
    language: json.language || detectPrimaryLanguage(json.report),
    reportType: json.report_type || "repository",
    via: "diwo-proxy",
    cuqaUrl: json.cuqa_url || CUQA_BASE,
  };
}

/**
 * Is the CUQA agent up, and does it have a repository loaded?
 * Never rejects — the caller renders whatever it learns.
 */
export async function fetchCuqaStatus({ signal } = {}) {
  const offline = {
    reachable: false,
    repo_loaded: false,
    repo_name: null,
    file_count: 0,
    cuqa_url: CUQA_BASE,
    message: "CUQA agent is not reachable.",
  };

  try {
    const res = await fetch(`${DIWO_BASE}/cuqa/status`, { signal });
    if (res.ok) return await res.json();
  } catch (e) {
    if (e.name === "AbortError") throw e;
  }

  // DIWO backend unavailable — ask CUQA directly.
  try {
    const res = await fetch(`${CUQA_BASE}/api/files`, { signal });
    const json = await readJson(res);
    if (!res.ok) {
      return { ...offline, reachable: true, message: json.detail || "No repository loaded in CUQA." };
    }
    return {
      reachable: true,
      repo_loaded: Boolean((json.files || []).length),
      repo_name: json.repo_name || null,
      file_count: json.total ?? (json.files || []).length,
      cuqa_url: CUQA_BASE,
      message: "CUQA workspace loaded.",
    };
  } catch (e) {
    if (e.name === "AbortError") throw e;
    return offline;
  }
}

// ─── Workflow lifecycle ──────────────────────────────────────────────────────

/** POST /workflows/from-cuqa — seed a workflow from the live CUQA report. */
export const startWorkflowFromCuqa = (body = {}) => api.post("/workflows/from-cuqa", body);

/** POST /workflows — seed a workflow from a client-supplied smell list. */
export const startWorkflow = (body) => api.post("/workflows", body);

/** GET /workflows/<id>/audit-logs */
export const fetchAuditLogs = (workflowId) => api.get(`/workflows/${workflowId}/audit-logs`);

/** POST /workflows/<id>/complete */
export const completeWorkflow = (workflowId, notes = "") =>
  api.post(`/workflows/${workflowId}/complete`, { notes });

// ─── Stage 1 → 2 — smell selection ───────────────────────────────────────────

/**
 * POST /workflows/<id>/smell-selection-pass — preview the filtered CUQA report
 * for a selection without advancing the workflow and without calling RDP.
 *
 * Throws with the backend's own `error` message so the stage can show it.
 */
export async function previewSmellSelection(workflowId, body) {
  const response = await fetch(`${DIWO_BASE}/workflows/${workflowId}/smell-selection-pass`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData = await response.json();
      message = errorData.error || message;
    } catch {
      /* keep fallback message */
    }
    throw new Error(message);
  }

  return response.json();
}

/**
 * POST /workflows/<id>/select-smells — commit the selection.
 *
 * This is the single hand-off that filters the CUQA report down to the smells
 * the developer kept and forwards it to the RDP agent; the plan comes back in
 * the response. The browser never posts to RDP itself.
 */
export const selectSmells = (workflowId, body) =>
  api.post(`/workflows/${workflowId}/select-smells`, body);

/** POST /workflows/<id>/reset-to-smell-review */
export const resetToSmellReview = (workflowId, body = {}) =>
  api.post(`/workflows/${workflowId}/reset-to-smell-review`, body);

// ─── Stage 2 → 3 — plan approval ─────────────────────────────────────────────

/** POST /workflows/<id>/plan-preference-update */
export const updatePlanPreferences = (workflowId, body) =>
  api.post(`/workflows/${workflowId}/plan-preference-update`, body);

/**
 * POST /workflows/<id>/plan-decision — approve / reject / modify.
 *
 * On 'approve' the backend reduces the plan to the approved steps and returns
 * it as `approved_plan`; that reduced plan is what Stage 3 forwards to SCTVA,
 * so a rejected step never reaches the transformer.
 */
export const submitPlanDecision = (workflowId, body) =>
  api.post(`/workflows/${workflowId}/plan-decision`, body);

/** POST /workflows/<id>/reset-to-plan-approval */
export const resetToPlanApproval = (workflowId, body = {}) =>
  api.post(`/workflows/${workflowId}/reset-to-plan-approval`, body);

// ─── Stage 3 → 4 — transformation decision ───────────────────────────────────

/**
 * POST /workflows/<id>/transformation-decision — accept or roll back.
 *
 * On accept, `files` carries the final source of every file (a rejected file
 * arrives holding its original source), which is what the backend archives.
 */
export const submitTransformationDecision = (workflowId, body) =>
  api.post(`/workflows/${workflowId}/transformation-decision`, body);

// ─── Stage 3 — transformation via SCTVA (through the orchestrator) ──────────

/**
 * POST /workflows/<id>/transform — run the approved plan through SCTVA.
 *
 * The backend maps the approved steps to SCTVA actions, reads the source text
 * out of the CUQA workspace, posts /sctva/execute and normalizes the reply.
 * Because it defaults to the workflow's stored plan — which plan-decision
 * already reduced to the approved steps — a rejected step cannot be executed
 * even if the caller passes nothing.
 *
 * Resolves to { result, request, mapping, sources, sctva_url, executed_at }.
 * `result.files[]` carry `before`/`after`; add the rendered diff with
 * utils/transformationMapper.hydrateTransformationResult.
 */
export async function runTransformation(workflowId, body = {}, { signal } = {}) {
  const response = await fetch(`${DIWO_BASE}/workflows/${workflowId}/transform`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  const json = await readJson(response);
  if (!response.ok) {
    // The backend passes SCTVA's own status through: 503 = agent not running,
    // 422 = the plan is not executable against the current workspace.
    const error = new Error(json.error || `HTTP ${response.status}: ${response.statusText}`);
    error.status = response.status;
    error.sctvaUrl = json.sctva_url || null;
    error.missing = json.missing || null;
    error.reachable = response.status !== 503;
    throw error;
  }
  return json;
}

/** GET /sctva/status — is the Safe Transformation agent up? Never rejects. */
export async function checkSctvaHealth({ signal } = {}) {
  try {
    const res = await fetch(`${DIWO_BASE}/sctva/status`, { signal });
    if (!res.ok) return { reachable: false, sctvaUrl: null, status: res.status };
    const json = await readJson(res);
    return {
      ...json,
      reachable: Boolean(json.reachable),
      sctvaUrl: json.sctva_url || null,
      status: res.status,
    };
  } catch (e) {
    if (e.name === "AbortError") throw e;
    return { reachable: false, sctvaUrl: null, status: 0 };
  }
}

// ─── Workspace access for the whole-project archive ─────────────────────────

/**
 * GET /cuqa/project-structure — the loaded repository's file tree.
 *
 * Proxied from the CUQA agent by the orchestrator. Needed so the final
 * download is the WHOLE project rather than only the files the agents touched.
 */
export async function fetchProjectStructure({ signal } = {}) {
  const res = await fetch(`${DIWO_BASE}/cuqa/project-structure`, { signal });
  const json = await readJson(res);

  if (!res.ok) {
    const error = new Error(
      json.error || `The project structure could not be read (HTTP ${res.status}).`
    );
    error.status = res.status;
    error.cuqaUrl = json.cuqa_url || CUQA_BASE;
    error.reachable = json.reachable ?? res.status !== 503;
    throw error;
  }

  return {
    repoName: json.repo_name || null,
    source: json.source || null,
    totalSourceFiles: json.total_source_files ?? null,
    tree: json.tree || null,
  };
}

/**
 * The original source of ONE analysed file, for the Code Smell Review viewer.
 *
 * A thin wrapper over /workspace/sources so a caller that wants one file does
 * not have to unpack a batch response. `source` is null when the workspace no
 * longer holds the file — the report describes an analysis that has since been
 * replaced, which the viewer reports rather than showing an empty editor.
 */
export async function fetchFileSource(filePath, { signal } = {}) {
  const { files, missing } = await fetchWorkspaceSources([filePath], { signal });
  const entry = files[0];

  return {
    path: filePath,
    source: typeof entry?.source_code === "string" ? entry.source_code : null,
    language: entry?.language || "",
    missing: missing.includes(filePath) || !entry,
  };
}

/**
 * POST /workspace/sources — the raw text of CUQA-analysed files.
 *
 * The orchestrator batches this against SCTVA's workspace reader, so any
 * number of paths can be sent in one call. Files that could not be located
 * come back in `missing` rather than failing the request.
 */
export async function fetchWorkspaceSources(filePaths, { signal } = {}) {
  const res = await fetch(`${DIWO_BASE}/workspace/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_paths: filePaths }),
    signal,
  });

  const json = await readJson(res);
  if (!res.ok) {
    const error = new Error(
      json.error || `The workspace sources could not be read (HTTP ${res.status}).`
    );
    error.status = res.status;
    error.sctvaUrl = json.sctva_url || null;
    error.reachable = json.reachable ?? res.status !== 503;
    throw error;
  }

  return {
    files: json.files || [],
    missing: json.missing || [],
    imported: json.imported ?? (json.files || []).length,
    total: json.total ?? (filePaths || []).length,
  };
}

// ─── Git integration ─────────────────────────────────────────────────────────

/**
 * POST /diwo/apply-and-push — write the whole project onto a branch, commit
 * and (optionally) push it.
 *
 * Addressed through DIWO_BASE like every other call. It used to be the
 * root-relative "/api/diwo/apply-and-push", which resolved to the same URL
 * only because the Vite dev proxy maps /api to http://localhost:5001 — so it
 * was the one call that silently depended on the dev server being in front.
 */
export async function applyAndPush(body) {
  const response = await fetch(`${DIWO_BASE}/diwo/apply-and-push`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || `HTTP ${response.status}: ${response.statusText}`);
  }
  return result;
}
