/**
 * Transformation result hydration
 * ==============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The orchestration backend normalizes the SCTVA response
 * (backend/domain/sctva_mapper.py::normalize_execute_result) and returns each
 * file with its `before` and `after` text. What it deliberately does NOT send
 * is the rendered diff: building it is presentation work, it would roughly
 * double the response size, and the line-alignment already runs in the browser
 * for the Results stage.
 *
 * This module closes that gap — it is the only transformation-shaping code
 * left on the client. The plan → SCTVA action mapping that used to live here
 * (in services/sctvaApi.js) now runs in the backend, because it is agent
 * integration rather than UI.
 */

import { buildDiffRows } from "./diffMapper";

/**
 * Add the rendered diff to a normalized transformation result.
 *
 * Returns a new object carrying the same fields the Transformation and Results
 * stages already read, plus `diff_rows` on every file and at the top level.
 * Safe to call on a result that already has them (a re-render, or a workflow
 * reloaded from the backend) — existing rows are kept rather than rebuilt.
 */
export function hydrateTransformationResult(result) {
  if (!result || typeof result !== "object") return result;

  const files = (result.files || []).map((file) => ({
    ...file,
    diff_rows: file.diff_rows?.length
      ? file.diff_rows
      : buildDiffRows(file.before || "", file.after || ""),
  }));

  // Prefer a file that actually changed: it is what the developer came to see.
  const primary = files.find((f) => f.changed) || files[0] || null;

  return {
    ...result,
    files,
    diff_rows: result.diff_rows?.length ? result.diff_rows : primary?.diff_rows || [],
  };
}
