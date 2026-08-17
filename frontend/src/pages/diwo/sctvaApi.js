/**
 * SCTVA Agent data source
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Safe Code Transformation & Validation Agent (SCTVA) is a separate Flask
 * service (default http://localhost:8002) that answers
 *
 *     POST /sctva/execute        ->  the transformation + validation result
 *     POST /sctva/cuqa-sources   ->  raw source text for CUQA-analysed files
 *
 * Two things have to be assembled before /sctva/execute can run, and neither
 * is carried by the approved plan on its own:
 *
 *  1. SOURCE TEXT. The CUQA report describes files but never ships their
 *     contents. SCTVA solves this itself: /sctva/cuqa-sources takes the
 *     relative paths from the report and reads them back out of the CUQA
 *     temp workspace (%TEMP%/cuqa_*), returning entries already shaped like
 *     the `source_files` field of an execute request.
 *
 *  2. ACTIONS. The RDP plan speaks in refactoring names ("Extract Method");
 *     SCTVA executes a fixed action vocabulary (`extract_method`, …). The
 *     mapping below mirrors sctva/integration/planner_adapter.py::_map_step,
 *     including its rule that an unmappable step becomes a `noop` rather than
 *     disappearing — the step count the developer approved stays visible in
 *     the transformation log.
 *
 * The response is normalized to one shape whether SCTVA transformed a single
 * file (fields at the top level) or several (a `file_results` array), so the
 * UI renders the same structure in both cases.
 */

import API_CONFIG, { buildApiUrl } from "../../config/api.config";

export const SCTVA_BASE = (
  API_CONFIG.TRANSFORMATION_AGENT?.baseURL || "http://localhost:8002"
).replace(/\/+$/, "");

export const SCTVA_EXECUTE_URL =
  buildApiUrl("TRANSFORMATION_AGENT", "execute") || `${SCTVA_BASE}/sctva/execute`;
export const SCTVA_SOURCES_URL = `${SCTVA_BASE}/sctva/cuqa-sources`;
export const SCTVA_HEALTH_URL =
  buildApiUrl("TRANSFORMATION_AGENT", "health") || `${SCTVA_BASE}/health`;

/** Matches sctva/constants.py::SUPPORTED_LANGUAGES. */
const SUPPORTED_LANGUAGES = new Set(["python", "java", "c"]);

const LANGUAGE_ALIASES = {
  py: "python", python: "python", python3: "python",
  java: "java",
  c: "c", h: "c", "c/c++": "c",
};

/** Matches the default execution options of the SCTVA contract. */
export const DEFAULT_EXECUTION_OPTIONS = {
  strict_mode: true,
  enable_behavior_tests: true,
  timeout_seconds: 10,
  // javac/gcc are not assumed to be on PATH; syntax validation still runs.
  require_compilation: false,
  rollback_on_behavior_failure: true,
  // MUST stay false for the DIWO workflow. SCTVA defaults this to true, and
  // whenever `source_files` is present its LocalRefactorDetector appends
  // refactorings the plan never asked for (agent.py::_local_actions_for_file).
  // In a reviewed workflow that is a correctness bug, not a bonus: a step the
  // developer explicitly REJECTED comes back through the side door — a
  // rejected "Introduce Constant" reappeared as `#define MAGIC_NUMBER_32 32`
  // in testing. The approved plan is the contract; nothing else runs.
  enable_sctva_auto_refactoring: false,
};

// ─── Errors ──────────────────────────────────────────────────────────────────

/** An error carrying enough context for the UI to explain what to start/fix. */
function sctvaError(message, { status = 0, sctvaUrl = SCTVA_BASE, reachable = false, details = null } = {}) {
  const err = new Error(message);
  err.status = status;
  err.sctvaUrl = sctvaUrl;
  err.reachable = reachable;
  err.details = details;
  return err;
}

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}

// ─── Small helpers ───────────────────────────────────────────────────────────

const normalizePath = (value) => String(value || "").replace(/\\/g, "/").trim();

export function normalizeLanguage(value) {
  const key = String(value || "").toLowerCase().trim();
  return LANGUAGE_ALIASES[key] || (SUPPORTED_LANGUAGES.has(key) ? key : "");
}

const asInt = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && /^\d+$/.test(value.trim())) return Number(value.trim());
  return null;
};

/** Keys any agent in this project has used to name the file a step touches. */
const FILE_KEYS = [
  "source_file", "sourceFile", "target_file", "targetFile", "file",
  "file_name", "fileName", "file_path", "filePath", "relative_path", "relativePath",
];

const LINE_KEYS = ["source_line", "sourceLine", "line", "start_line", "startLine"];
const LINE_LIST_KEYS = ["source_lines", "sourceLines", "lines"];
const START_KEYS = ["start_line", "startLine", "source_line", "sourceLine", "line"];
const END_KEYS = ["end_line", "endLine", "target_line", "targetLine"];

function pickFile(...sources) {
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    for (const key of FILE_KEYS) {
      const value = source[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return "";
}

function pickLine(...sources) {
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    for (const key of LINE_KEYS) {
      const value = asInt(source[key]);
      if (value !== null) return value;
    }
    for (const key of LINE_LIST_KEYS) {
      const values = source[key];
      if (!Array.isArray(values) || values.length === 0) continue;
      const first = asInt(values[0]);
      if (first !== null) return first;
    }
  }
  return null;
}

function pickRange(...sources) {
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;

    const start = START_KEYS.map((k) => asInt(source[k])).find((v) => v !== null) ?? null;
    const end = END_KEYS.map((k) => asInt(source[k])).find((v) => v !== null) ?? null;
    if (start !== null && end !== null) return [Math.min(start, end), Math.max(start, end)];

    for (const key of [...LINE_LIST_KEYS, "line_range", "lineRange"]) {
      const values = source[key];
      if (Array.isArray(values) && values.length) {
        const parsed = values.map(asInt).filter((v) => v !== null);
        if (parsed.length >= 2) return [Math.min(...parsed), Math.max(...parsed)];
        if (parsed.length === 1) return [parsed[0], parsed[0]];
      }
      if (values && typeof values === "object" && !Array.isArray(values)) {
        const nestedStart = asInt(values.start ?? values.from);
        const nestedEnd = asInt(values.end ?? values.to);
        if (nestedStart !== null && nestedEnd !== null) {
          return [Math.min(nestedStart, nestedEnd), Math.max(nestedStart, nestedEnd)];
        }
      }
    }
  }
  return [null, null];
}

/** Mirrors PlannerAdapter._safe_identifier. */
function safeIdentifier(name) {
  const cleaned = String(name || "")
    .trim()
    .replace(/[^A-Za-z0-9_]/g, "_");
  if (!cleaned) return "RenamedSymbol";
  return /^\d/.test(cleaned) ? `R_${cleaned}` : cleaned;
}

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

// ─── Plan → SCTVA actions ────────────────────────────────────────────────────

const RENAME_ALIASES = new Set([
  "rename function", "rename method", "rename variable",
  "rename class", "rename parameter", "rename field", "rename attribute",
]);

/** Refactorings SCTVA refuses to fake with a rename — see planner_adapter.py. */
const UNSUPPORTED_REFACTORINGS = {
  "extract class": "requires coordinated multi-file class creation",
  "move method": "requires coordinated edits to source and destination classes",
  "replace conditional with polymorphism": "requires new strategy/subclass definitions",
  "introduce parameter object": "requires a new parameter type and call-site updates",
  "hide delegate": "requires semantic multi-location edits",
  "replace data value with object": "requires semantic multi-location edits",
  "inline class": "requires semantic multi-location edits",
  "collapse hierarchy": "requires semantic multi-location edits",
  "pull up method": "requires semantic multi-location edits",
  "replace parameter with method call": "requires semantic multi-location edits",
};

class StepMappingError extends Error {}

/**
 * Map one RDP step onto an SCTVA action.
 *
 * Throws StepMappingError when the step names a refactoring SCTVA supports but
 * is missing the parameters it needs; returns null when the refactoring has no
 * safe SCTVA equivalent at all. Both outcomes become a noop upstream.
 */
function mapStep(step) {
  const refactoring = String(step?.refactoring || "").trim();
  if (!refactoring) throw new StepMappingError("missing 'refactoring' in step");

  const key = refactoring.toLowerCase();
  const params = step.parameters && typeof step.parameters === "object" ? step.parameters : {};
  const target = step.target && typeof step.target === "object" ? step.target : {};
  const location = step.location && typeof step.location === "object" ? step.location : {};

  let action = null;

  if (key.startsWith("fault injection")) {
    const originalLogic = params.original_logic ?? params.old_logic;
    const faultyLogic = "faulty_logic" in params ? params.faulty_logic : params.new_logic;
    if (!originalLogic || faultyLogic === undefined || faultyLogic === null) {
      throw new StepMappingError("fault injection mapping requires original_logic and faulty_logic");
    }
    action = {
      action_type: "fault_injection",
      parameters: {
        original_logic: String(originalLogic),
        faulty_logic: String(faultyLogic),
        change_type: params.change_type,
        purpose: params.purpose,
        target_class: target.class,
        target_method: target.method,
      },
    };
  } else if (RENAME_ALIASES.has(key)) {
    const oldName = params.old_name || params.method || target.method || target.class;
    const newName = params.new_name || params.renamed_to;
    if (!oldName || !newName) throw new StepMappingError("rename step requires old/new names");
    action = {
      action_type: "rename_symbol",
      parameters: { old_name: String(oldName), new_name: safeIdentifier(newName) },
    };
  } else if (key === "extract method") {
    const method = target.method || params.method;
    if (!method) {
      throw new StepMappingError("extract method mapping requires target.method or parameters.method");
    }
    const [startLine, endLine] = pickRange(params, target, location, step);
    if (startLine === null || endLine === null) {
      throw new StepMappingError(
        "extract method mapping requires an executable source range (start_line/end_line or source_lines/lines)"
      );
    }
    const newName = params.new_method_name || params.extracted_method_name || `${method}Core`;
    action = {
      action_type: "extract_method",
      parameters: {
        method: String(method),
        new_method_name: safeIdentifier(newName),
        start_line: startLine,
        end_line: endLine,
        target_class: target.class || params.source_class,
      },
    };
  } else if (key in UNSUPPORTED_REFACTORINGS) {
    throw new StepMappingError(
      `${refactoring} ${UNSUPPORTED_REFACTORINGS[key]}; SCTVA will not simulate it with a rename`
    );
  } else if (key === "extract constant" || key === "replace magic number with symbolic constant") {
    if (!("literal_value" in params)) {
      throw new StepMappingError("extract_constant mapping requires parameters.literal_value");
    }
    action = {
      action_type: "extract_constant",
      parameters: {
        literal_value: params.literal_value,
        constant_name: params.constant_name || "EXTRACTED_CONSTANT",
      },
    };
  } else if (key === "introduce constant") {
    const literalValue = "literal_value" in params ? params.literal_value : null;
    const literalValues = Array.isArray(params.literal_values) ? params.literal_values : null;
    const hint = params.hint;
    if (literalValue === null && !literalValues && !hint) {
      throw new StepMappingError("introduce constant mapping requires literal_value, literal_values, or hint");
    }
    action = {
      action_type: "introduce_constant",
      parameters: {
        literal_value: literalValue,
        literal_values: literalValues,
        constant_name: params.constant_name || "EXTRACTED_CONSTANT",
        hint,
        source_file: params.source_file,
        source_line: params.source_line,
        target_class: target.class || params.source_class,
        target_method: target.method || params.method,
      },
    };
  } else if (key === "remove dead code") {
    const method = params.method || target.method;
    const sourceLine = pickLine(params, target, location, step);
    if (!method && sourceLine === null) {
      throw new StepMappingError(
        "remove dead code mapping requires parameters.method, target.method, or a source line"
      );
    }
    action = {
      action_type: "remove_dead_code",
      parameters: {
        method: String(method || ""),
        class_name: target.class || params.source_class,
        source_line: sourceLine,
      },
    };
  } else if (key === "replace unsafe function") {
    const unsafeFunction = params.unsafe_function || target.method;
    const safeAlternative = params.safe_alternative;
    if (!unsafeFunction || !safeAlternative) {
      throw new StepMappingError("replace unsafe function mapping requires unsafe_function and safe_alternative");
    }
    action = {
      action_type: "replace_unsafe_function",
      parameters: {
        unsafe_function: String(unsafeFunction),
        safe_alternative: String(safeAlternative),
        source_line: pickLine(params, target, location, step),
      },
    };
  } else if (key === "encapsulate variable") {
    const variableName = params.variable_name || target.variable;
    if (!variableName) {
      throw new StepMappingError("encapsulate variable mapping requires parameters.variable_name");
    }
    action = {
      action_type: "encapsulate_variable",
      parameters: {
        variable_name: String(variableName),
        getter_name: safeIdentifier(params.getter_name || `get_${variableName}`),
        setter_name: safeIdentifier(params.setter_name || `set_${variableName}`),
      },
    };
  } else if (key === "replace literal" || key === "replace temp with query") {
    if (!("old_literal" in params) || !("new_literal" in params)) {
      throw new StepMappingError("replace_literal mapping requires old_literal/new_literal");
    }
    action = {
      action_type: "replace_literal",
      parameters: { old_literal: params.old_literal, new_literal: params.new_literal },
    };
  }

  if (action) {
    const sourceFile = pickFile(params, target, location, step);
    if (sourceFile && !action.parameters.source_file) action.parameters.source_file = sourceFile;
    const sourceLine = pickLine(params, target, location, step);
    if (sourceLine !== null && action.parameters.source_line === undefined) {
      action.parameters.source_line = sourceLine;
    }
    action.source_step_id = step.step_id ?? null;
    action.source_refactoring = refactoring;
    action.warnings = [];
  }

  return action;
}

const noopAction = (step, reason, message) => ({
  action_type: "noop",
  parameters: { reason, refactoring: step?.refactoring ?? null, step_id: step?.step_id ?? null },
  source_step_id: step?.step_id ?? null,
  source_refactoring: step?.refactoring ?? null,
  warnings: [message],
});

/**
 * Normalize an approved RDP plan into the `refactoring_plan` SCTVA expects.
 *
 * Every approved step produces exactly one action so the transformation log
 * lines up with the plan the developer signed off on; steps that could not be
 * mapped come back as noops carrying the reason.
 */
export function normalizePlanForSctva(plan, { correlationId = null } = {}) {
  if (!plan || typeof plan !== "object") {
    throw sctvaError("No approved refactoring plan is available to transform.", { status: 422, reachable: true });
  }

  const planId = String(plan.plan_id || "").trim() || `diwo_plan_${Date.now()}`;
  const steps = Array.isArray(plan.steps) ? plan.steps : [];

  const actions = [];
  const warnings = [];
  const unmappedSteps = [];

  steps.forEach((step, idx) => {
    const position = idx + 1;

    if (!step || typeof step !== "object") {
      const message = `Step ${position} is malformed and was mapped to noop.`;
      warnings.push(message);
      unmappedSteps.push(position);
      actions.push(noopAction(null, "malformed_step", message));
      return;
    }

    let mapped;
    try {
      mapped = mapStep(step);
    } catch (e) {
      const message = `Step ${position} (${step.refactoring || "unknown"}): ${e.message}`;
      warnings.push(message);
      unmappedSteps.push(position);
      actions.push(noopAction(step, "unmappable_step", message));
      return;
    }

    if (mapped) {
      actions.push(mapped);
    } else {
      const message = `Step ${position} unsupported refactoring '${step.refactoring || "unknown"}', mapped to noop.`;
      warnings.push(message);
      unmappedSteps.push(position);
      actions.push(noopAction(step, "unsupported_refactoring", message));
    }
  });

  if (actions.length === 0) {
    const message = "The approved plan produced zero executable actions; using a noop action.";
    warnings.push(message);
    actions.push(noopAction(null, "empty_or_non_actionable_plan", message));
  }

  // A plan-level target still scopes actions that named no file of their own.
  const planTarget = typeof plan.target === "string" ? plan.target.trim() : pickFile(plan.target);
  if (planTarget) {
    actions.forEach((action) => {
      if (!action.parameters.source_file) action.parameters.source_file = planTarget;
    });
  }

  return {
    plan: {
      plan_id: planId,
      actions,
      behavior_tests: Array.isArray(plan.behavior_tests) ? plan.behavior_tests : [],
      metadata: {
        source_agent: "rdp_agent",
        source_plan_id: planId,
        correlation_id: correlationId,
        adapter_warnings: warnings,
        malformed_steps: unmappedSteps,
        planner_metadata: plan.metadata || {},
        mapped_by: "diwo_frontend",
      },
    },
    warnings,
    executableCount: actions.filter((a) => a.action_type !== "noop").length,
    noopCount: actions.filter((a) => a.action_type === "noop").length,
  };
}

/** Every distinct file the approved steps touch, plan target included. */
export function collectPlanSourcePaths(plan) {
  const paths = new Set();

  (plan?.steps || []).forEach((step) => {
    if (!step || typeof step !== "object") return;
    const found = pickFile(step.parameters, step.target, step.location, step);
    if (found) paths.add(normalizePath(found));
  });

  const planTarget = typeof plan?.target === "string" ? plan.target : pickFile(plan?.target);
  if (planTarget) paths.add(normalizePath(planTarget));

  return [...paths].filter(Boolean);
}

// ─── HTTP ────────────────────────────────────────────────────────────────────

/**
 * Read the raw text of CUQA-analysed files back out of the CUQA temp workspace.
 *
 * Returns entries already shaped for the `source_files` field of an execute
 * request. Files SCTVA could not locate come back in `missing`; transformation
 * still runs on whatever was found, because a plan spanning ten files should
 * not be blocked by one stale path.
 */
export async function fetchWorkspaceSources(filePaths, { signal } = {}) {
  let res;
  try {
    res = await fetch(SCTVA_SOURCES_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_paths: filePaths }),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw sctvaError(
      `The Safe Transformation agent (${SCTVA_BASE}) could not be reached. ` +
        "Start it with: cd agents/transformation_agent/safe_code_transformation_agent && python app.py",
      { status: 503 }
    );
  }

  const json = await readJson(res);
  if (!res.ok) {
    throw sctvaError(json.error || `SCTVA returned HTTP ${res.status} while importing sources.`, {
      status: res.status,
      reachable: true,
    });
  }

  return {
    files: json.files || [],
    missing: json.missing || [],
    imported: json.imported ?? (json.files || []).length,
    total: json.total ?? filePaths.length,
  };
}

/** POST the assembled request to /sctva/execute. */
export async function postExecute(payload, { signal } = {}) {
  let res;
  try {
    res = await fetch(SCTVA_EXECUTE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw sctvaError(
      `The Safe Transformation agent (${SCTVA_EXECUTE_URL}) could not be reached. ` +
        "Start it with: cd agents/transformation_agent/safe_code_transformation_agent && python app.py",
      { status: 503 }
    );
  }

  const json = await readJson(res);
  if (!res.ok) {
    throw sctvaError(json.error || `SCTVA agent returned HTTP ${res.status}.`, {
      status: res.status,
      reachable: true,
    });
  }
  return json;
}

// ─── Response normalization ──────────────────────────────────────────────────

/**
 * Flatten a single-file or multi-file execute response into one shape.
 *
 * SCTVA answers a one-file request with the result fields at the top level and
 * a many-file request with a `file_results` array; both become `files[]` here,
 * each entry carrying the before/after text and the rendered diff rows the
 * Transformation and Results stages display.
 */
export function normalizeExecuteResult(raw, sourceFiles = []) {
  const beforeByPath = new Map(
    (sourceFiles || []).map((f) => [normalizePath(f.file_name), f.source_code || ""])
  );

  const perFile =
    Array.isArray(raw?.file_results) && raw.file_results.length > 0 ? raw.file_results : [raw || {}];

  const files = perFile.map((entry, idx) => {
    const path = normalizePath(entry.file_name) || `source_${idx + 1}`;
    const before = beforeByPath.get(path) ?? "";
    const after = typeof entry.refactored_code === "string" ? entry.refactored_code : "";

    return {
      // `path` / `after` / `diff_rows` are the field names the Results stage
      // already reads, so a file entry works unchanged in both stages.
      path,
      file: path,
      before,
      after,
      refactored_code: after,
      diff_rows: buildDiffRows(before, after),
      changed: Boolean(after) && after !== before,
      language: entry.language || raw?.language || "",
      success: Boolean(entry.success),
      rollback_occurred: Boolean(entry.rollback_occurred),
      transformation_applied: Boolean(entry.transformation_applied),
      total_replacements: entry.total_replacements ?? 0,
      confidence_score: entry.confidence_score ?? null,
      confidence_components: entry.confidence_components || null,
      validation: entry.validation || null,
      safety_report: entry.safety_report || null,
      source_mode: entry.source_mode || "raw",
    };
  });

  // Prefer a file that actually changed: it is what the developer came to see.
  const primary = files.find((f) => f.changed) || files[0] || null;

  return {
    raw,
    requestId: raw?.request_id || "",
    language: raw?.language || primary?.language || "",
    success: Boolean(raw?.success),
    rollbackOccurred: Boolean(raw?.rollback_occurred),
    transformationApplied: Boolean(raw?.transformation_applied),
    confidenceScore: raw?.confidence_score ?? null,
    confidenceApplicable: raw?.confidence_applicable !== false,
    validationScore: raw?.validation_score ?? null,
    totalReplacements: raw?.total_replacements ?? 0,
    fileSummary: raw?.file_summary || {
      total: files.length,
      succeeded: files.filter((f) => f.success).length,
      applied: files.filter((f) => f.transformation_applied).length,
      rolled_back: files.filter((f) => f.rollback_occurred).length,
      not_applied: files.filter((f) => !f.transformation_applied && !f.rollback_occurred).length,
    },
    // Multi-file responses keep validation/safety on each file result only.
    validation: raw?.validation || primary?.validation || null,
    confidenceComponents: raw?.confidence_components || primary?.confidence_components || null,
    safetyReport: raw?.safety_report || primary?.safety_report || null,
    files,
    // Kept flat as well: this is the contract the DIWO workflow state expects.
    refactored_code: primary?.after || "",
    diff_rows: primary?.diff_rows || [],
  };
}

// ─── Orchestration ───────────────────────────────────────────────────────────

/**
 * Run the approved plan through SCTVA and return the normalized result.
 *
 * Resolves to { result, request, sources, mapping } — `request` is the exact
 * payload that was POSTed, kept so the UI can show what the agent was asked to
 * do next to what it reported back. `onStage` is called with "mapping",
 * "sources", "executing" and "complete" so a caller can report real progress
 * instead of animating a guess.
 */
export async function executeTransformation({
  plan,
  language,
  requestId,
  executionOptions,
  onStage = () => {},
  signal,
} = {}) {
  onStage("mapping");
  const mapping = normalizePlanForSctva(plan, { correlationId: plan?.plan_id || null });

  const paths = collectPlanSourcePaths(plan);
  if (paths.length === 0) {
    throw sctvaError(
      "The approved plan does not name any source file, so SCTVA has nothing to transform. " +
        "Re-run the plan stage against a CUQA report that carries file paths.",
      { status: 422, reachable: true }
    );
  }

  onStage("sources");
  const sources = await fetchWorkspaceSources(paths, { signal });
  if (sources.files.length === 0) {
    throw sctvaError(
      `SCTVA could not read the source of any planned file (${sources.missing.length} missing). ` +
        "The CUQA temp workspace is where it looks, so re-run the analysis in the Code Smell " +
        "Review stage to recreate it, then transform again.",
      { status: 422, reachable: true, details: { missing: sources.missing } }
    );
  }

  const resolvedLanguage =
    normalizeLanguage(language) ||
    normalizeLanguage(sources.files[0]?.language) ||
    "";

  if (!resolvedLanguage) {
    throw sctvaError(
      `SCTVA does not support language '${language || "unknown"}'. Supported: c, java, python.`,
      { status: 422, reachable: true }
    );
  }

  const request = {
    request_id: requestId || `sctva_diwo_${Date.now()}`,
    language: resolvedLanguage,
    source_files: sources.files,
    refactoring_plan: mapping.plan,
    execution_options: { ...DEFAULT_EXECUTION_OPTIONS, ...(executionOptions || {}) },
  };

  onStage("executing");
  const raw = await postExecute(request, { signal });
  onStage("complete");

  return {
    result: normalizeExecuteResult(raw, sources.files),
    request,
    sources,
    mapping,
    sctvaUrl: SCTVA_BASE,
    executedAt: new Date().toISOString(),
  };
}

/** Is the SCTVA agent up? Never rejects — the caller renders whatever it learns. */
export async function checkSctvaHealth({ signal } = {}) {
  try {
    const res = await fetch(SCTVA_HEALTH_URL, { signal });
    if (!res.ok) return { reachable: false, sctvaUrl: SCTVA_BASE, status: res.status };
    return { ...(await readJson(res)), reachable: true, sctvaUrl: SCTVA_BASE, status: res.status };
  } catch (e) {
    if (e.name === "AbortError") throw e;
    return { reachable: false, sctvaUrl: SCTVA_BASE, status: 0 };
  }
}
