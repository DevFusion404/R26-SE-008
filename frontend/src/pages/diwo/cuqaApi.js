/**
 * CUQA Agent data source
 * ======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Code Understanding & Quality Assessment (CUQA) agent is a separate
 * FastAPI service (default http://localhost:8080) that answers
 *
 *     POST /api/quality-report  ->  { type: "repository" | "file", report: {...} }
 *
 * The Code Smell Review stage reads that report through the DIWO backend proxy
 * (POST /api/cuqa/quality-report), which normalizes every file entry and also
 * returns the flat smell list the workflow endpoints expect.
 *
 * If the DIWO backend itself is down, we fall back to calling the CUQA agent
 * directly — it serves CORS "*", so the browser can read it — and normalize the
 * payload here with the same rules as diwo/orchestrator.normalize_cuqa_report,
 * so the UI renders one shape regardless of which route delivered the data.
 */

export const DIWO_BASE =
  import.meta?.env?.VITE_API_URL || "http://localhost:5001/api";

export const CUQA_BASE = (
  import.meta?.env?.VITE_CUQA_API_URL || "http://localhost:8080"
).replace(/\/+$/, "");

export const CUQA_QUALITY_REPORT_URL = `${CUQA_BASE}/api/quality-report`;

const SEVERITIES = ["high", "medium", "low"];

// ─── Normalization (mirrors backend normalize_cuqa_report) ───────────────────

const baseName = (p) => String(p || "").split(/[\\/]/).pop() || "";

const extOf = (p) => {
  const name = baseName(p);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
};

const normalizeSeverity = (value) => {
  const severity = String(value ?? "low").toLowerCase();
  return SEVERITIES.includes(severity) ? severity : "low";
};

function summarizeFiles(files) {
  const severityTotals = { high: 0, medium: 0, low: 0 };
  files.forEach((f) =>
    (f.code_smells || []).forEach((s) => {
      severityTotals[normalizeSeverity(s.severity)] += 1;
    })
  );

  const scored = files.filter((f) => typeof f.quality_score === "number");
  const average = scored.length
    ? scored.reduce((sum, f) => sum + f.quality_score, 0) / scored.length
    : 0;

  return {
    files_analyzed: files.length,
    total_lines_of_code: files.reduce(
      (sum, f) => sum + ((f.metrics || {}).lines_of_code || 0),
      0
    ),
    total_code_smells: Object.values(severityTotals).reduce((a, b) => a + b, 0),
    smell_severity: severityTotals,
    average_quality_score: Math.round(average * 10) / 10,
  };
}

/**
 * Coerce any CUQA quality-report payload into one repository-shaped report.
 * Accepts the raw envelope ({type, report}), a bare repository report, or a
 * single-file report. Every file entry is guaranteed to carry relative_path,
 * language, metrics, code_smells, smell_summary and quality_score, so the UI
 * never needs defensive checks of its own.
 */
export function normalizeCuqaReport(payload) {
  if (!payload || typeof payload !== "object") {
    throw new Error("CUQA payload must be a JSON object.");
  }

  const report =
    payload.report && typeof payload.report === "object" ? payload.report : payload;
  const isRepo = Array.isArray(report.files);
  const rawFiles = isRepo ? report.files : [report];
  const repoName = isRepo
    ? report.repo_name || payload.repo_name || null
    : report.relative_path || report.file || null;

  const files = rawFiles
    .filter((raw) => raw && typeof raw === "object")
    .map((raw) => {
      const relPath = String(raw.relative_path || raw.file || "unknown").replace(/\\/g, "/");

      const smells = (raw.code_smells || [])
        .filter((s) => s && typeof s === "object")
        .map((s) => ({
          ...s,
          type: s.type || "Unknown",
          severity: normalizeSeverity(s.severity),
        }));

      const smellSummary = { high: 0, medium: 0, low: 0 };
      smells.forEach((s) => {
        smellSummary[s.severity] += 1;
      });

      return {
        file: raw.file || baseName(relPath),
        relative_path: relPath,
        language: String(raw.language || extOf(relPath) || "unknown").toLowerCase(),
        metrics: { filename: baseName(relPath), ...(raw.metrics || {}) },
        code_smells: smells,
        smell_summary: smellSummary,
        quality_score: raw.quality_score ?? 100,
        ...(raw.error ? { error: raw.error } : {}),
      };
    });

  const hasSummary =
    report.summary &&
    typeof report.summary === "object" &&
    "total_code_smells" in report.summary;

  return {
    summary: hasSummary ? report.summary : summarizeFiles(files),
    files,
    repo_name: repoName,
    source: "cuqa",
    report_type: payload.type || (isRepo ? "repository" : "file"),
    generated_at: new Date().toISOString(),
  };
}

// ─── HTTP helpers ────────────────────────────────────────────────────────────

/** An error carrying enough context for the UI to explain what to start/load. */
function cuqaError(message, { status = 0, cuqaUrl = CUQA_BASE, reachable = false } = {}) {
  const err = new Error(message);
  err.status = status;
  err.cuqaUrl = cuqaUrl;
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

/** Most common language among the analysed files (defaults to "java"). */
export function detectPrimaryLanguage(report) {
  const counts = {};
  (report?.files || []).forEach((f) => {
    const language = (f.language || "").toLowerCase();
    if (language && language !== "unknown") counts[language] = (counts[language] || 0) + 1;
  });
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  return ranked.length ? ranked[0][0] : "java";
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
