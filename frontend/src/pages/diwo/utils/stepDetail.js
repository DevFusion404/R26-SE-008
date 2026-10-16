/**
 * Step detail presentation
 * ========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Wording and bands for the middle column of the Refactoring Plan Approval
 * workspace (components/StepDetailBlocks.jsx).
 *
 * PRESENTATION ONLY, like planningDecisionSupport.js: the category, the score,
 * the warnings and the SCTVA capability verdict arrive on the step from the
 * backend. This module turns them into labels and sentences, and turns an
 * SCTVA before/after pair into the few lines worth reading. It scores nothing.
 *
 * Imports only the pure diff aligner, so it runs under plain `node`.
 */

import { buildDiffRows } from "./diffMapper.js";

// ─── Verdict ─────────────────────────────────────────────────────────────────

/**
 * The verdict pill. The backend's category wins — it applies gates (manual
 * only, high-risk cap) a bare score cannot show; the score thresholds are the
 * fallback for a step that carries a score and no category.
 */
export function verdictOf(support) {
  const byCategory = {
    recommended: { key: "good", label: "Recommended" },
    review: { key: "review", label: "Review carefully" },
    not_recommended: { key: "bad", label: "Not recommended" },
    manual_only: { key: "manual", label: "Manual only" },
  }[support?.category];
  if (byCategory) return byCategory;
  const score = support?.score;
  if (typeof score !== "number") return null;
  if (score >= 80) return { key: "good", label: "Recommended" };
  if (score >= 60) return { key: "review", label: "Review carefully" };
  return { key: "bad", label: "Not recommended" };
}

// ─── Facts about the step ────────────────────────────────────────────────────

const firstNumber = (...values) => values.find((v) => typeof v === "number" && v > 0) ?? null;

/** First line the step points at. */
export function lineOf(step) {
  return firstNumber(
    step?.target?.lines?.[0],
    step?.location?.lines?.[0],
    step?.location?.line,
    step?.parameters?.source_line,
    step?.parameters?.source_lines?.[0],
  );
}

/** [first, last] line of the smell, or null. */
export function lineRangeOf(step) {
  const range = [step?.target?.lines, step?.location?.lines, step?.parameters?.source_lines]
    .find((r) => Array.isArray(r) && typeof r[0] === "number" && r[0] > 0);
  if (range) return [range[0], typeof range[1] === "number" ? range[1] : range[0]];
  const line = lineOf(step);
  return line ? [line, line] : null;
}

/** The thing being changed, in the developer's terms. */
export function targetNameOf(step) {
  const p = step?.parameters || {};
  return p.variable_name || step?.target?.variable
    || step?.target?.method || p.target_method || p.target_function || p.source_method || p.method
    || step?.target?.class || p.source_class || p.target_class
    || null;
}

/** Lines of code of the smelly unit: the smell metrics, then the explanation. */
export function locOf(step) {
  const m = step?.smell_metrics || {};
  const direct = firstNumber(m.lines_of_code, m.loc, m.lines);
  if (direct) return direct;
  const match = /(\d+)\s+lines/i.exec(String(step?.explanation || ""));
  return match ? Number(match[1]) : null;
}

/** Can SCTVA run this exact step on its own? */
export function automationOf(support) {
  const capability = support?.capability;
  if (!capability) return { key: "unknown", text: "SCTVA capability was not checked for this step." };
  const status = String(capability.status || "unknown").toLowerCase();
  if (status === "advisory") return { key: "manual", text: "SCTVA has no safe automatic form for this, so it is manual work." };
  if (status === "unknown") return { key: "unsupported", text: "SCTVA has no transformation mapped for this smell." };
  if (!capability.actual_step_mappable) return { key: "incomplete", text: "SCTVA supports it, but this step is missing parameters." };
  return { key: "auto", text: "SCTVA runs it automatically." };
}

/** Advice line under the header: one sentence. */
export function adviceOf(step, support) {
  const verdict = verdictOf(support);
  if (!verdict) return { tone: "review", text: "DIWO decision support is unavailable for this step. Review it yourself." };
  if (verdict.key === "good") {
    return {
      tone: "good",
      text: "DIWO recommends this step: good quality gain, SCTVA can run it automatically and all parameters are present.",
    };
  }
  const concern = rankConcerns(support?.warnings)[0];
  return { tone: "review", text: concern ? `${verdict.label}: ${sentence(concern)}` : support?.summary || verdict.label };
}

/** Backend warnings, most serious first. Stable within a rank. */
export function rankConcerns(warnings) {
  const rank = (w) => (/high|cannot|not |no-op|incomplete|missing|unsupported|no safe/i.test(w) ? 0
    : /medium|conflict|almost|fallback|estimated/i.test(w) ? 1 : 2);
  return (warnings || []).map((w, i) => [w, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([w]) => w);
}

// ─── Tile text ───────────────────────────────────────────────────────────────

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z]/g, "");
const sentence = (s) => { const t = String(s || "").trim(); return /[.!?]$/.test(t) ? t : `${t}.`; };

/** "What is wrong now", one sentence, keyed by normalised smell type. */
const PROBLEM = {
  longfunction: ({ n, loc, at }) => loc ? `${n || "This function"} is ${loc} lines long, so it is hard to read and test.` : `${n || "This function"}${at} does too much to read or test in one go.`,
  longmethod: (a) => PROBLEM.longfunction(a),
  globalvariable: ({ n, at }) => `${n || "This variable"}${at} can be read and changed from anywhere.`,
  godclass: ({ n }) => `${n || "This class"} holds too many responsibilities.`,
  largeclass: ({ n }) => `${n || "This class"} holds too many responsibilities.`,
  magicnumber: ({ at }) => `An unexplained literal${at} hides what the value means.`,
  magicnumbers: ({ at }) => `Unexplained literals${at} hide what the values mean.`,
  longparameterlist: ({ n }) => `${n || "This function"} takes more parameters than a caller can track.`,
  featureenvy: ({ n }) => `${n || "This method"} uses another class's data more than its own.`,
  duplicatecode: ({ at }) => `The same logic${at} is repeated in more than one place.`,
  deadcode: ({ at }) => `Code${at} is never executed or used.`,
  unusedfunction: ({ n }) => `${n || "This function"} is never called.`,
  complexconditional: ({ at }) => `A condition${at} is too complex to read at a glance.`,
  deepnesting: ({ at }) => `Deeply nested blocks${at} make the flow hard to follow.`,
  unsafefunction: ({ at }) => `An unsafe library call${at} can overflow or misbehave.`,
  lazyclass: ({ n }) => `${n || "This class"} does too little to justify existing.`,
};

export function problemOf(step) {
  const n = targetNameOf(step);
  const loc = locOf(step);
  const line = lineOf(step);
  const fn = PROBLEM[norm(step?.smell_type)];
  const text = fn
    ? fn({ n, loc, at: line ? ` on line ${line}` : "" })
    : String(step?.explanation || "").split(/(?<=\.)\s/)[0] || `${step?.smell_type || "A code smell"} was detected.`;
  return { title: step?.smell_type || "Code smell", text, tag: loc ? `${loc} lines` : null };
}

/**
 * Tiles 2 and 3 for one refactoring method: what DIWO applies, and what the
 * code looks like afterwards. Names come from the step's own parameters; a
 * name not on the step is not invented.
 */
export function changeOf(step, support) {
  const p = step?.parameters || {};
  const method = norm(step?.refactoring);
  const n = targetNameOf(step);
  const auto = automationOf(support).text;
  const call = (v) => (v ? `${v}()` : null);
  const names = (...vals) => vals.filter(Boolean);

  if (method === "extractmethod" || method === "extractfunction") {
    return {
      applies: `Moves one block of ${n || "the function"} into a new function. ${auto}`,
      title: "Shorter, named function",
      names: names(call(p.new_method_name || p.method_name)),
      result: `${n || "The original function"} calls the new function instead of holding the code inline.`,
    };
  }
  if (method.startsWith("encapsulate")) {
    const v = p.variable_name || n || "the variable";
    return {
      applies: `Puts ${v} behind a getter and a setter. ${auto}`,
      title: "Access through functions",
      names: names(call(p.getter_name), call(p.setter_name)),
      result: `Other code calls these instead of touching ${v} directly.`,
    };
  }
  if (method.includes("constant") || method === "replaceliteral") {
    return {
      applies: `Replaces the literal with a named constant. ${auto}`,
      title: "Named constant",
      names: names(p.constant_name),
      result: "The value gets a name that says what it means.",
    };
  }
  if (method.startsWith("rename")) {
    return {
      applies: `Renames ${p.old_name || n || "the symbol"}. ${auto}`,
      title: "Clearer name",
      names: names(p.new_name),
      result: "Every use in the file reads the new name.",
    };
  }
  if (method === "removedeadcode") {
    return {
      applies: `Deletes code that never runs. ${auto}`,
      title: "Unused code removed",
      names: [],
      result: "The file is shorter and nothing that runs changes.",
    };
  }
  if (method === "replaceunsafefunction") {
    return {
      applies: `Swaps the unsafe call for a bounded one. ${auto}`,
      title: "Safe library call",
      names: names(call(p.replacement || p.safe_function)),
      result: "The call can no longer overrun its buffer.",
    };
  }
  return {
    applies: `Applies ${step?.refactoring || "the refactoring"} to ${n || "this code"}. ${auto}`,
    title: step?.refactoring || "Code restructured",
    names: Object.entries(p).filter(([k, v]) => /_name$/.test(k) && typeof v === "string" && v).map(([, v]) => v),
    result: String(step?.explanation || "").split(/(?<=\.)\s/)[0] || "The smell is addressed.",
  };
}

// ─── Code quality change ─────────────────────────────────────────────────────

/**
 * Rows for the diverging chart, one per metric RDP predicted. Complexity is
 * charted only when the smell's own complexity is known: RDP predicts the
 * value AFTER, and an absolute value on a change axis would read as a rise.
 */
export function qualityRows(step) {
  const pr = step?.prediction;
  if (!pr) return [];
  const rows = [];
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

  const after = num(pr.predicted_complexity_after);
  const m = step?.smell_metrics || {};
  const before = num(m.cyclomatic_complexity) ?? num(m.complexity);
  if (after !== null && before !== null) {
    rows.push(row("complexity", "Complexity", after - before, true, `Complexity goes from ${fmt(before)} to ${fmt(after)}`));
  }
  [["coupling_change", "Coupling", true], ["cohesion_change", "Cohesion", false],
    ["maintainability_improvement", "Maintainability", false]].forEach(([key, label, lower]) => {
    const v = num(pr[key]);
    if (v !== null) rows.push(row(key, label, v, lower));
  });
  return rows;
}

function row(key, label, value, lowerIsBetter, tip) {
  const effect = value === 0 ? "same" : (value < 0) === lowerIsBetter ? "better" : "worse";
  return {
    key, label, value, effect,
    hint: lowerIsBetter ? "lower is better" : "higher is better",
    tip: tip || (value === 0 ? `${label} does not change` : `${label} ${value < 0 ? "decreases" : "increases"} by ${fmt(Math.abs(value))}`),
  };
}

export function fmt(v) {
  if (typeof v !== "number") return "—";
  return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2)));
}

// ─── Technical details ───────────────────────────────────────────────────────

/** Raw ids, parameters and scores — the only place they appear. */
export function technicalRows(step, support) {
  const rows = new Map();
  const put = (k, v) => { if (v !== null && v !== undefined && v !== "" && !rows.has(k)) rows.set(k, v); };
  put("smell_id", step?.smell_id);
  put("smell_type", step?.smell_type);
  put("source_file", step?.parameters?.source_file ?? step?.target?.file);
  put("source_line", step?.parameters?.source_line ?? lineOf(step));
  Object.entries(step?.parameters || {}).forEach(([k, v]) => put(k, v));
  put("transformation", support?.capability?.action_type);
  put("scoring_method", step?.scoring_method);
  put("rdp_score", step?.score);
  put("risk_score", step?.prediction?.risk_score ?? support?.factors?.transformation_safety?.risk_score);
  return [...rows.entries()].map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)]);
}

// ─── Code preview ────────────────────────────────────────────────────────────

/** The SCTVA before/after pair on the step, or null — usually null at plan time. */
export function previewOf(step) {
  for (const c of [step?.preview, step?.sctva_preview, step?.diff, step?.transformation_preview]) {
    if (c && typeof c === "object" && typeof c.before === "string") {
      return { before: c.before, after: typeof c.after === "string" ? c.after : null };
    }
  }
  return null;
}

const CONTEXT = 2;

/**
 * One side of the preview: changed lines plus CONTEXT lines either side, in
 * runs. An added line in a region that also removed something is "modified".
 */
export function previewSide(before, after, side) {
  const rows = buildDiffRows(before, after);
  const removedHunks = new Set(rows.filter((r) => r.kind === "before").map((r) => r.hunk));
  const lines = rows
    .filter((r) => r.kind === "same" || r.kind === side)
    .map((r) => ({
      no: side === "before" ? r.beforeNo : r.afterNo,
      text: r.text,
      kind: r.kind === "same" ? "same" : side === "before" ? "removed" : removedHunks.has(r.hunk) ? "modified" : "added",
    }));
  return runsAround(lines, (l) => l.kind !== "same");
}

/** Current code only (no SCTVA output yet): the smell's lines, with context. */
export function currentSide(source, range) {
  const lines = String(source || "").split("\n").map((text, i) => ({
    no: i + 1,
    text,
    kind: range && i + 1 >= range[0] && i + 1 <= range[1] ? "affected" : "same",
  }));
  return runsAround(lines, (l) => l.kind === "affected");
}

function runsAround(lines, marked) {
  const keep = new Array(lines.length).fill(false);
  lines.forEach((l, i) => {
    if (!marked(l)) return;
    for (let k = Math.max(0, i - CONTEXT); k <= Math.min(lines.length - 1, i + CONTEXT); k += 1) keep[k] = true;
  });
  const runs = [];
  let run = null;
  lines.forEach((l, i) => {
    if (!keep[i]) { run = null; return; }
    if (!run) { run = []; runs.push(run); }
    run.push(l);
  });
  return runs;
}

export const lineCount = (text) => (typeof text === "string" ? text.split("\n").length : null);

// ─── Syntax colouring ────────────────────────────────────────────────────────

const KEYWORDS = new Set((
  "if else for while do switch case default break continue return goto try catch finally throw throws "
  + "new delete class struct enum union interface extends implements import from package namespace using "
  + "public private protected static const final extern volatile register inline virtual override abstract "
  + "def lambda pass yield async await with as in is not and or None True False null true false this self "
  + "typedef sizeof let var function export include define ifdef ifndef endif"
).split(" "));

const TYPES = new Set((
  "int char void float double long short unsigned signed bool boolean byte size_t String string str "
  + "list dict tuple set object Object Integer FILE uint8_t uint16_t uint32_t uint64_t int32_t int64_t"
).split(" "));

const TOKEN = /(\/\/.*$|#(?=\s|$).*$|\/\*.*?\*\/|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b\d+(?:\.\d+)?[fFlLuU]*\b|#?[A-Za-z_]\w*|\s+|.)/g;

/** {text, kind} tokens: keyword, type, function, number, string, comment, plain. */
export function tokenize(line) {
  const out = [];
  const text = String(line ?? "");
  TOKEN.lastIndex = 0;
  let match;
  while ((match = TOKEN.exec(text)) !== null) {
    const tok = match[0];
    if (!tok) break;
    let kind = "plain";
    if (/^(\/\/|\/\*|#(\s|$))/.test(tok)) kind = "comment";
    else if (/^["']/.test(tok)) kind = "string";
    else if (/^\d/.test(tok)) kind = "number";
    else if (/^#?[A-Za-z_]/.test(tok)) {
      const word = tok.replace(/^#/, "");
      if (KEYWORDS.has(word)) kind = "keyword";
      else if (TYPES.has(word)) kind = "type";
      else if (/^\s*\(/.test(text.slice(match.index + tok.length))) kind = "function";
    }
    const last = out[out.length - 1];
    if (last && kind === "plain" && last.kind === "plain") last.text += tok;
    else out.push({ text: tok, kind });
  }
  return out;
}
