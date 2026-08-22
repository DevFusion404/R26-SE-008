/**
 * Smell → source line mapping
 * ===========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Works out which lines of a file each code smell occupies, so the source
 * viewer can mark them. Pure — no React, no network — which is why it lives in
 * utils/ and can be tested on its own.
 *
 * The rules mirror the backend exactly
 * (backend/domain/cuqa_normalizer.py::cuqa_report_to_smells):
 *
 *     start = start_line || line
 *     end   = end_line   || start
 *
 * Keeping them identical is what stops the viewer and the report disagreeing
 * about where a smell lives. A LongMethod carrying start_line/end_line marks
 * its whole body; a MagicNumber carrying only `line` marks that one line.
 */

/** Ranking used when several smells cover one line — the worst one colours it. */
export const SEVERITY_RANK = { high: 3, medium: 2, low: 1 };

/**
 * A smell whose range spans an implausible number of lines would build a
 * needlessly huge coverage map. Past this, only the anchor line is marked.
 */
export const MAX_RANGE_LINES = 2000;

/**
 * Resolve one report smell to the lines it occupies.
 *
 * Returns { line, start, end }. `line` is the line the smell is *reported* on
 * (the anchor, where the dot goes); start..end is the span the bar covers.
 * All three are 0 when the smell carries no usable line — CUQA attributes some
 * findings to a file rather than a position, and those must not mark line 1.
 */
export function smellLines(smell) {
  const line = Number(smell?.line) || 0;
  const start = Number(smell?.start_line) || line;
  const end = Number(smell?.end_line) || start;

  if (!(start >= 1)) return { line: 0, start: 0, end: 0 };
  return { line: line >= 1 ? line : start, start, end: Math.max(start, end) };
}

/** Attach the resolved line span to every smell. */
export function withLines(smells = []) {
  return smells.map((smell) => ({ ...smell, ...smellLines(smell) }));
}

/**
 * Build `line number -> { smells, anchors, worst }` for a file.
 *
 * `smells`  every smell covering the line (anywhere in its span)
 * `anchors` the smells actually reported ON that line — the dot column, so the
 *           start of a long range stays findable inside it
 * `worst`   the highest severity covering the line, which colours the bar
 */
export function buildCoverage(smellsWithLines = []) {
  const map = new Map();

  const touch = (lineNo, smell, isAnchor) => {
    if (lineNo < 1) return;
    let entry = map.get(lineNo);
    if (!entry) {
      entry = { smells: [], anchors: [], worst: null };
      map.set(lineNo, entry);
    }
    entry.smells.push(smell);
    if (isAnchor) entry.anchors.push(smell);
    if ((SEVERITY_RANK[smell.severity] || 0) > (SEVERITY_RANK[entry.worst] || 0)) {
      entry.worst = smell.severity;
    }
  };

  smellsWithLines.forEach((smell) => {
    if (smell.start < 1) return;

    const anchor = smell.line || smell.start;
    if (smell.end - smell.start > MAX_RANGE_LINES) {
      touch(anchor, smell, true);
      return;
    }
    for (let ln = smell.start; ln <= smell.end; ln += 1) {
      touch(ln, smell, ln === anchor);
    }
  });

  return map;
}
