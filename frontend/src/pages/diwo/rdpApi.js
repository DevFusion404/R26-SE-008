/**
 * RDP Agent data source
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Refactoring Decision & Planning (RDP) agent is a separate Flask service
 * (default http://localhost:5000) that answers
 *
 *     POST /generate  ->  { success: true, plan: {...}, trace: {...} }
 *
 * It accepts a CUQA-shaped quality report ({ files: [...], summary: {...} }) and
 * translates it internally (app._translate_cuqa_to_rdp), so the very report the
 * Code Smell Review stage renders can be posted here unchanged — no second
 * format to maintain on the frontend.
 *
 * The plan it returns only carries the transformation-facing fields per step:
 *
 *     { step_id, smell_id, refactoring, target, parameters, explanation }
 *
 * The impact / risk / complexity ratings and the MCDA score the approval page
 * shows are not on the step — they live in the trace, on the candidate that was
 * selected for that smell. enrichPlanWithTrace() folds them back onto every step
 * so the page renders one shape whether the plan came from the RDP agent or from
 * the DIWO backend.
 */

import API_CONFIG, { buildApiUrl } from "../../config/api.config";

export const RDP_BASE = (
  API_CONFIG.RDP_AGENT?.baseURL || "http://localhost:5000"
).replace(/\/+$/, "");

export const RDP_GENERATE_URL = buildApiUrl("RDP_AGENT", "generate") || `${RDP_BASE}/generate`;
export const RDP_HEALTH_URL = buildApiUrl("RDP_AGENT", "health") || `${RDP_BASE}/health`;

const RATINGS = ["low", "medium", "high"];

/** Coerce a knowledge-base rating to one of low | medium | high. */
const rating = (value, fallback = "medium") => {
  const normalized = String(value ?? "").toLowerCase();
  return RATINGS.includes(normalized) ? normalized : fallback;
};

const round = (value, dp = 3) => {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  const factor = 10 ** dp;
  return Math.round(value * factor) / factor;
};

// ─── Errors ──────────────────────────────────────────────────────────────────

/** An error carrying enough context for the UI to explain what to start/fix. */
function rdpError(message, { status = 0, rdpUrl = RDP_GENERATE_URL, reachable = false } = {}) {
  const err = new Error(message);
  err.status = status;
  err.rdpUrl = rdpUrl;
  err.reachable = reachable;
  return err;
}

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}

// ─── Request building ────────────────────────────────────────────────────────

const smellKey = (file, smell) => `${file}|${smell?.type ?? ""}|${smell?.line ?? ""}`;

/**
 * Index every smell of a report by file|type|line.
 *
 * Used to re-attach the fields the DIWO backend's filtered report drops
 * (entity, start_line, end_line, per-smell metrics). Those fields decide
 * whether RDP can build usable parameters: without start_line/end_line an
 * Extract Method step has no source_lines, and the plan generator logs a
 * parameter warning instead of telling the Transformation Agent what to move.
 */
function indexSmells(report) {
  const index = new Map();
  (report?.files || []).forEach((file) => {
    const path = file.relative_path || file.file;
    (file.code_smells || []).forEach((smell) => index.set(smellKey(path, smell), smell));
  });
  return index;
}

/**
 * Merge file metrics, letting the richer report fill the gaps.
 *
 * The DIWO filtered report rebuilds metrics from each smell and can emit 0 for
 * fields the smell never carried; a 0 must never overwrite a real measurement.
 */
function mergeMetrics(base = {}, override = {}) {
  const merged = { ...base };
  Object.entries(override).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== 0) merged[key] = value;
    else if (!(key in merged)) merged[key] = value;
  });
  return merged;
}

/**
 * Build the POST /generate body from the report the workflow is holding.
 *
 * `report` is the authority on *what* to plan (it is the developer's smell
 * selection); `fullReport` — the unfiltered CUQA report, when available — is
 * only used to enrich the selected smells with detail the filtered copy lost.
 * Files with no remaining smells are dropped: RDP would skip them anyway, and
 * the first file in the list becomes the plan's target.
 */
export function buildPlanInput(report, fullReport = null) {
  const details = fullReport ? indexSmells(fullReport) : new Map();
  const fullFiles = new Map(
    (fullReport?.files || []).map((f) => [f.relative_path || f.file, f])
  );

  const files = (report?.files || [])
    .filter((file) => (file.code_smells || []).length > 0)
    .map((file) => {
      const path = file.relative_path || file.file || "unknown";
      const source = fullFiles.get(path);

      return {
        file: path,
        relative_path: path,
        language: file.language || source?.language || "unknown",
        metrics: mergeMetrics(source?.metrics, file.metrics),
        quality_score: file.quality_score ?? source?.quality_score ?? 100,
        code_smells: (file.code_smells || []).map((smell) => ({
          ...(details.get(smellKey(path, smell)) || {}),
          ...smell,
        })),
      };
    });

  return {
    files,
    summary: {
      ...(report?.summary || {}),
      files_analyzed: files.length,
      total_code_smells: files.reduce((sum, f) => sum + f.code_smells.length, 0),
    },
    ...(report?.repo_name ? { repo_name: report.repo_name } : {}),
  };
}

// ─── Plan enrichment ─────────────────────────────────────────────────────────

/**
 * Fold the trace's decision data back onto each plan step.
 *
 * For every step the trace is searched by smell_id for the candidate the
 * decision engine picked, which is where impact / risk / complexity and the
 * MCDA score live. Rejected-but-viable candidates come along as `alternatives`
 * so the developer can see what the agent weighed before approving the step.
 */
export function enrichPlanWithTrace(plan, trace) {
  const selections = new Map(
    (trace?.candidate_generation || []).map((entry) => [entry.smell_id, entry])
  );
  const impacts = new Map(
    (trace?.impact_prediction || []).map((entry) => [entry.smell_id, entry.predictions || []])
  );
  const mcda = new Map(
    (trace?.mcda_selection || []).map((entry) => [entry.smell_id, entry.predictions || []])
  );
  const inputs = new Map(
    (trace?.input_summary?.smells || []).map((smell) => [smell.id, smell])
  );

  const steps = (plan?.steps || []).map((step) => {
    const selection = selections.get(step.smell_id);
    const candidates = selection?.candidates || [];
    const chosen = candidates.find((c) => c.name === step.refactoring) || {};
    const source = inputs.get(step.smell_id) || null;

    const mcdaScore = (mcda.get(step.smell_id) || []).find(
      (m) => m.refactoring === step.refactoring
    );
    const score = selection?.selected_score ?? mcdaScore?.final_score ?? null;

    return {
      ...step,
      impact: rating(chosen.impact),
      risk: rating(chosen.risk),
      complexity: rating(chosen.complexity),
      score: round(score),
      scoring_method: selection?.scoring_method || null,
      smell_type: selection?.smell_type || source?.type || null,
      severity: selection?.severity || source?.severity || null,
      location: source?.location || null,
      smell_metrics: source?.metrics || null,
      prediction:
        (impacts.get(step.smell_id) || []).find((p) => p.refactoring === step.refactoring) || null,
      alternatives: candidates
        .filter((c) => c.name !== step.refactoring && c.preconditions_met)
        .map((c) => ({
          name: c.name,
          score: round(c.score),
          impact: rating(c.impact),
          risk: rating(c.risk),
        })),
    };
  });

  const generation = trace?.plan_generation || {};

  return {
    ...plan,
    steps,
    source: "rdp",
    skipped_smells: generation.skipped_smells || [],
    smells_skipped: generation.smells_skipped ?? 0,
    total_smells: trace?.input_summary?.total_smells ?? steps.length,
    reordered: Boolean(trace?.dependency_analysis?.reordered),
  };
}

// ─── HTTP ────────────────────────────────────────────────────────────────────

/**
 * Generate a refactoring plan for the given quality report.
 *
 * Resolves to { plan, trace, rdpUrl, generatedAt } where `plan.steps` are
 * already enriched with impact / risk / score. Rejects with an error carrying
 * `status` and `reachable` so the caller can tell "agent not running" from
 * "agent ran and refused the report".
 */
export async function generateRefactoringPlan({ report, fullReport = null, signal } = {}) {
  const payload = buildPlanInput(report, fullReport);

  if (payload.files.length === 0) {
    throw rdpError(
      "The selected report contains no code smells, so there is nothing to plan. " +
        "Go back to Code Smell Review and select at least one file with smells.",
      { status: 422, reachable: true }
    );
  }

  let res;
  try {
    res = await fetch(RDP_GENERATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw rdpError(
      `The Refactoring Planning agent (${RDP_GENERATE_URL}) could not be reached. ` +
        "Start it with: cd agents/rdp_agent && python app.py",
      { status: 503 }
    );
  }

  const json = await readJson(res);
  if (!res.ok) {
    throw rdpError(json.error || `RDP agent returned HTTP ${res.status}.`, {
      status: res.status,
      reachable: true,
    });
  }

  const plan = json.plan;
  // A circular dependency between steps is reported as HTTP 200 with an error
  // inside the plan, so it has to be checked separately from the status code.
  if (!plan || plan.error) {
    throw rdpError(
      plan?.error || "The RDP agent returned no plan for this report.",
      { status: res.status, reachable: true }
    );
  }

  return {
    plan: enrichPlanWithTrace(plan, json.trace),
    trace: json.trace || null,
    rdpUrl: RDP_BASE,
    generatedAt: new Date().toISOString(),
    filesPlanned: payload.files.length,
    smellsSubmitted: payload.summary.total_code_smells,
  };
}

/**
 * Is the RDP agent up? Never rejects — the caller renders whatever it learns.
 */
export async function checkRdpHealth({ signal } = {}) {
  try {
    const res = await fetch(RDP_HEALTH_URL, { signal });
    if (!res.ok) return { reachable: false, rdpUrl: RDP_BASE, status: res.status };
    return { ...(await readJson(res)), reachable: true, rdpUrl: RDP_BASE, status: res.status };
  } catch (e) {
    if (e.name === "AbortError") throw e;
    return { reachable: false, rdpUrl: RDP_BASE, status: 0 };
  }
}
