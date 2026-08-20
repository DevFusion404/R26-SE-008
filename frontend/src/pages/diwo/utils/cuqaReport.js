/**
 * CUQA quality-report normalization (pure helpers)
 * ================================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Extracted from the former diwo/cuqaApi.js, which mixed these pure shape
 * helpers with the HTTP calls. The transport now lives in
 * services/diwoApi.js; only the shape rules are here.
 *
 * These rules mirror the backend's domain/cuqa_normalizer.normalize_cuqa_report
 * so the UI renders one shape whether the report arrived through the DIWO
 * backend proxy or straight from the CUQA agent's fallback route.
 */

const SEVERITIES = ["high", "medium", "low"];

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
