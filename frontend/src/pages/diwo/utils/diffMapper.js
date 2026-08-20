/**
 * Diff rendering helpers
 * ======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Line alignment and the row/segment shapes the Transformation, Results and
 * Comparison stages render. Pure presentation: no network, no agent knowledge.
 *
 * Extracted from the former services/sctvaApi.js when the SCTVA call moved
 * behind the orchestration backend. The diff itself deliberately stayed in the
 * browser — the backend returns `before` and `after` per file, and the rows
 * are built here, where they are drawn.
 */

// ─── Diff ────────────────────────────────────────────────────────────────────

/** Guard against the LCS table exploding on very large files (~2000×2000). */
const MAX_LCS_CELLS = 4_000_000;

/**
 * Longest-common-subsequence alignment of two line arrays.
 *
 * Returns ops in reading order: {type: "same" | "del" | "add", a, b} where `a`
 * indexes `beforeLines` and `b` indexes `afterLines` (null when the op has no
 * counterpart on that side). Falls back to positional pairing when the table
 * would be too large to be worth building in the browser.
 */
function alignLines(a, b) {
  const n = a.length;
  const m = b.length;

  if (n === 0) return b.map((_, j) => ({ type: "add", a: null, b: j }));
  if (m === 0) return a.map((_, i) => ({ type: "del", a: i, b: null }));

  if (n * m > MAX_LCS_CELLS) {
    const ops = [];
    for (let i = 0; i < Math.max(n, m); i += 1) {
      const left = i < n ? a[i] : null;
      const right = i < m ? b[i] : null;
      if (left !== null && right !== null && left === right) {
        ops.push({ type: "same", a: i, b: i });
      } else {
        if (left !== null) ops.push({ type: "del", a: i, b: null });
        if (right !== null) ops.push({ type: "add", a: null, b: i });
      }
    }
    return ops;
  }

  // dp[i][j] = LCS length of a[i:] and b[j:], flattened row-major.
  const width = m + 1;
  const dp = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i * width + j] =
        a[i] === b[j]
          ? dp[(i + 1) * width + (j + 1)] + 1
          : Math.max(dp[(i + 1) * width + j], dp[i * width + (j + 1)]);
    }
  }

  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ type: "same", a: i, b: j });
      i += 1;
      j += 1;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      ops.push({ type: "del", a: i, b: null });
      i += 1;
    } else {
      ops.push({ type: "add", a: null, b: j });
      j += 1;
    }
  }
  while (i < n) { ops.push({ type: "del", a: i, b: null }); i += 1; }
  while (j < m) { ops.push({ type: "add", a: null, b: j }); j += 1; }

  return ops;
}

/**
 * Hunk-grouped before/after rows.
 *
 * Consecutive changed lines are emitted as one block of removals followed by
 * one block of additions — lines 2-5 in red together, then the refactored
 * 2-6 in blue together — rather than alternating -/+ line by line. Rows of a
 * change region share a `hunk` number so the UI can frame each region.
 *
 * The alignment is a real LCS, not positional: a single inserted line shifts
 * everything below it, and a positional diff would report the whole rest of
 * the file as changed.
 *
 * Row shape stays {key, kind, lineNo, text, marker} for the Results stage,
 * with `beforeNo` / `afterNo` / `hunk` added for the richer renderer.
 */
export function buildDiffRows(beforeCode, afterCode) {
  const beforeLines = String(beforeCode || "").split("\n");
  const afterLines = String(afterCode || "").split("\n");

  // Trim the identical head and tail first: refactorings touch a small part of
  // a file, so this usually leaves the LCS table tiny.
  let prefix = 0;
  while (
    prefix < beforeLines.length &&
    prefix < afterLines.length &&
    beforeLines[prefix] === afterLines[prefix]
  ) {
    prefix += 1;
  }

  let suffix = 0;
  while (
    suffix < beforeLines.length - prefix &&
    suffix < afterLines.length - prefix &&
    beforeLines[beforeLines.length - 1 - suffix] === afterLines[afterLines.length - 1 - suffix]
  ) {
    suffix += 1;
  }

  const ops = [];
  for (let i = 0; i < prefix; i += 1) ops.push({ type: "same", a: i, b: i });

  alignLines(
    beforeLines.slice(prefix, beforeLines.length - suffix),
    afterLines.slice(prefix, afterLines.length - suffix)
  ).forEach((op) => {
    ops.push({
      type: op.type,
      a: op.a === null ? null : op.a + prefix,
      b: op.b === null ? null : op.b + prefix,
    });
  });

  for (let k = 0; k < suffix; k += 1) {
    ops.push({ type: "same", a: beforeLines.length - suffix + k, b: afterLines.length - suffix + k });
  }

  const rows = [];
  let cursor = 0;
  let hunk = 0;

  while (cursor < ops.length) {
    if (ops[cursor].type === "same") {
      const op = ops[cursor];
      rows.push({
        key: `same-${op.a}-${op.b}`,
        kind: "same",
        marker: " ",
        lineNo: op.a + 1,
        beforeNo: op.a + 1,
        afterNo: op.b + 1,
        text: beforeLines[op.a],
        hunk: null,
      });
      cursor += 1;
      continue;
    }

    // One change region: collect every removal and every addition in it, then
    // emit all removals before all additions so each side reads as a block.
    const removed = [];
    const added = [];
    while (cursor < ops.length && ops[cursor].type !== "same") {
      (ops[cursor].type === "del" ? removed : added).push(ops[cursor]);
      cursor += 1;
    }
    hunk += 1;

    removed.forEach((op) => {
      rows.push({
        key: `before-${op.a}`,
        kind: "before",
        marker: "-",
        lineNo: op.a + 1,
        beforeNo: op.a + 1,
        afterNo: null,
        text: beforeLines[op.a],
        hunk,
      });
    });
    added.forEach((op) => {
      rows.push({
        key: `after-${op.b}`,
        kind: "after",
        marker: "+",
        lineNo: op.b + 1,
        beforeNo: null,
        afterNo: op.b + 1,
        text: afterLines[op.b],
        hunk,
      });
    });
  }

  return rows;
}

/**
 * Fold diff rows into renderable segments.
 *
 * Unchanged runs become one `context` segment; each change region becomes one
 * `change` segment holding its removed lines and its added lines separately,
 * which is what lets the UI paint one red group followed by one blue group.
 * Change segments carry a 1-based `ordinal` so the UI can label them.
 */
export function buildDiffSegments(rows) {
  const segments = [];
  let changeCount = 0;

  (rows || []).forEach((row) => {
    const last = segments[segments.length - 1];

    if (row.kind === "same") {
      if (last && last.type === "context") last.rows.push(row);
      else segments.push({ type: "context", rows: [row] });
      return;
    }

    // Rows carry a hunk number when they came from buildDiffRows; diff rows
    // persisted by the DIWO backend do not, so fall back to grouping by run:
    // a removal that follows an addition opens a new region.
    const sameRegion =
      last &&
      last.type === "change" &&
      (row.hunk != null ? last.hunk === row.hunk : !(row.kind === "before" && last.after.length > 0));

    if (sameRegion) {
      (row.kind === "before" ? last.before : last.after).push(row);
      return;
    }

    changeCount += 1;
    segments.push({
      type: "change",
      ordinal: changeCount,
      hunk: row.hunk ?? changeCount,
      before: row.kind === "before" ? [row] : [],
      after: row.kind === "after" ? [row] : [],
    });
  });

  return segments;
}
