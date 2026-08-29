/**
 * smellGrouping.test.mjs
 * ======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The aggregation claims the Code Smell Review makes on screen, asserted
 * against the exact scenario the redesign brief calls out: one smell type
 * spread across many files.
 *
 *   - a type appearing in 5 files is ONE group of 53 findings, not 5 groups
 *   - fileCount is a unique-file count, never a sum (a file carrying two smell
 *     types of the same category must not be counted twice)
 *   - severity is a spread with a worst-present summary, not the first row's
 *   - auto-fixability comes from capability.status, never from severity
 *   - effort sums occurrences once; a category never re-adds its types' totals
 *   - absent impact records omit a field rather than reporting zero
 *
 * Dependency-free:
 *
 *     npm run test:grouping        (from frontend/)
 */

import {
  capabilityOf, categoryOptions, effortMinutes, expandAllKeys, fileCountOf,
  formatEffort, groupByCategory, groupByFile, groupBySmellType, groupRowsByFile,
  selectionState, selectionSummary, severitySpread, smellTypeOptions,
  worstSeverity,
} from "./smellGrouping.js";

let failures = 0;
const check = (label, condition, detail = "") => {
  if (!condition) failures += 1;
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${label}${!condition && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, actual, expected) =>
  check(label, JSON.stringify(actual) === JSON.stringify(expected),
        `got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);

/** A flattened row, exactly the shape CodeSmellApprovalPage builds. */
const row = (file, type, line, severity, category) => ({
  id: `${file}:${line}:0`,
  file,
  language: "java",
  smell: { type, line, severity, category, message: `${type} at ${line}` },
});

// The brief's scenario: Magic Number across five files, plus two other types.
const MAGIC_FILES = [
  ["CommentsHeaven.java", 12],
  ["Utils.java", 9],
  ["PricingEngine.java", 11],
  ["ReportBuilder.java", 8],
  ["AuthService.java", 13],
];

const ROWS = [];
MAGIC_FILES.forEach(([file, n]) => {
  for (let i = 0; i < n; i += 1) {
    ROWS.push(row(file, "MagicNumber", 100 + i, "low", "Security / Language-Specific"));
  }
});
ROWS.push(row("OrderService.java", "LongMethod", 84, "high", "Bloaters"));
ROWS.push(row("Utils.java", "LongMethod", 41, "medium", "Bloaters"));
ROWS.push(row("OrderService.java", "LargeClass", 1, "high", "Bloaters"));
ROWS.push(row("Legacy.java", "LargeClass", 1, "medium", "Bloaters"));

const TOTAL_MAGIC = MAGIC_FILES.reduce((n, [, c]) => n + c, 0);   // 53

console.log("\nCode Smell Review grouping\n");

// ── 1. Smell wise: one group per type, repository-wide ───────────────────────
console.log("Smell wise");
{
  const groups = groupBySmellType(ROWS, { selectedIds: new Set() });
  const magic = groups.find((g) => g.type === "MagicNumber");

  eq("one group per smell type, not per file",
     groups.map((g) => g.type).sort(), ["LargeClass", "LongMethod", "MagicNumber"]);
  eq(`MagicNumber is ONE group of ${TOTAL_MAGIC} findings`, magic.findingCount, TOTAL_MAGIC);
  eq("across 5 files — files, not findings", magic.fileCount, 5);
  check("and not 53 files", magic.fileCount !== TOTAL_MAGIC);
  eq("every occurrence is carried", magic.rows.length, TOTAL_MAGIC);

  // A type spanning severities reports the worst, and the full spread.
  const longMethod = groups.find((g) => g.type === "LongMethod");
  eq("worst severity present wins", longMethod.worstSeverity, "high");
  eq("the spread is kept, not collapsed", longMethod.severity, { high: 1, medium: 1, low: 0 });

  check("worst-severity groups sort first", groups[0].worstSeverity === "high");
}

// ── 2. Category wise: category -> type -> occurrence ─────────────────────────
console.log("\nCategory wise");
{
  const groups = groupByCategory(ROWS, {
    selectedIds: new Set(),
    categoryOf: (r) => r.smell.category,
    order: ["Bloaters", "Security / Language-Specific"],
  });

  const bloaters = groups.find((g) => g.category === "Bloaters");
  const security = groups.find((g) => g.category === "Security / Language-Specific");

  eq("a category holds several smell types", bloaters.typeCount, 2);
  eq("and every finding in them", bloaters.findingCount, 4);
  // OrderService twice, Utils, Legacy -> 3 unique files, NOT 2+2 summed.
  eq("fileCount is the unique union, not a sum of its types", bloaters.fileCount, 3);
  check("summing its types' file counts would over-count",
        bloaters.types.reduce((n, t) => n + t.fileCount, 0) === 4);

  eq("a single-type category is still correct", security.typeCount, 1);
  eq("with its own finding count", security.findingCount, TOTAL_MAGIC);
  eq("and its own file count", security.fileCount, 5);

  eq("taxonomy order is honoured", groups.map((g) => g.category),
     ["Bloaters", "Security / Language-Specific"]);
  eq("the type level is the same shape as smell wise",
     security.types[0].type, "MagicNumber");
}

// ── 3. File wise ─────────────────────────────────────────────────────────────
console.log("\nFile wise");
{
  const groups = groupByFile(ROWS, {
    selectedIds: new Set(),
    selectedFiles: new Set(["Utils.java"]),
  });
  const utils = groups.find((g) => g.file === "Utils.java");

  eq("a file lists the smell types inside it", utils.typeCount, 2);
  eq("with every finding", utils.findingCount, 10);
  eq("a file is one file", utils.fileCount, 1);
  check("whole-file selection is reported", utils.fileSelected === true);
  check("an unselected file says so",
        groups.find((g) => g.file === "Legacy.java").fileSelected === false);
}

// ── 3b. The file level inside a smell type ───────────────────────────────────
console.log("\nFile buckets inside a smell type");
{
  const magic = ROWS.filter((r) => r.smell.type === "MagicNumber");
  const buckets = groupRowsByFile(magic, { selectedIds: new Set() });

  eq("53 findings become 5 file buckets, not 53 rows", buckets.length, 5);
  eq("every occurrence is kept",
     buckets.reduce((n, b) => n + b.findingCount, 0), TOTAL_MAGIC);
  eq("each bucket counts only its own file",
     buckets.find((b) => b.file === "CommentsHeaven.java").findingCount, 12);
  check("a bucket carries the lines it covers",
        buckets[0].lines.length === buckets[0].findingCount);

  // Ticking one file inside a type must not reach that file's OTHER types.
  const utilsBucket = buckets.find((b) => b.file === "Utils.java");
  const picked = new Set(utilsBucket.rows.map((r) => r.id));
  const utilsLongMethod = ROWS.find(
    (r) => r.file === "Utils.java" && r.smell.type === "LongMethod");
  check("selecting a file inside MagicNumber leaves that file's LongMethod alone",
        !picked.has(utilsLongMethod.id));

  const after = groupRowsByFile(magic, { selectedIds: picked });
  check("that bucket reads fully selected",
        after.find((b) => b.file === "Utils.java").selection.all === true);
  check("while the others stay untouched",
        after.filter((b) => b.file !== "Utils.java").every((b) => b.selection.none));

  // The parent type is then partial — the levels agree.
  const type = groupBySmellType(ROWS, { selectedIds: picked })
    .find((g) => g.type === "MagicNumber");
  check("and the parent smell type shows partial", type.selection.partial === true);
  eq("with the file's count", type.selection.selected, 9);
}

// ── 4. Capability comes from the probe, never from severity ──────────────────
console.log("\nCapability");
{
  const impacts = new Map([
    ["OrderService.java:84:0", { capability: { status: "executable" }, if_selected: { effort_minutes: 15 } }],
    ["Utils.java:41:0", { capability: { status: "advisory" }, if_selected: { effort_minutes: 30 } }],
  ]);
  const rows = ROWS.filter((r) => r.smell.type === "LongMethod");

  eq("a mixed group is reported as mixed",
     capabilityOf(rows, impacts).label, "Mixed");
  eq("with both counts",
     [capabilityOf(rows, impacts).executable, capabilityOf(rows, impacts).advisory], [1, 1]);

  // A HIGH severity finding that is executable is still auto-fixable: the two
  // axes are independent, which is the whole point of the capability chip.
  const highOnly = [rows.find((r) => r.smell.severity === "high")];
  eq("a high-severity executable finding is auto-fixable",
     capabilityOf(highOnly, impacts).label, "Auto-fixable");

  check("no impact map means no claim", capabilityOf(rows, null) === null);
  check("records with no capability make no claim",
        capabilityOf(rows, new Map([["x", {}]])) === null);
}

// ── 5. Effort: summed once, never re-added ───────────────────────────────────
console.log("\nEffort");
{
  const impacts = new Map([
    ["OrderService.java:84:0", { if_selected: { effort_minutes: 15 } }],
    ["Utils.java:41:0", { if_selected: { effort_minutes: 30 } }],
    ["OrderService.java:1:0", { if_selected: { effort_minutes: 45 } }],
  ]);
  const bloaters = groupByCategory(ROWS, {
    impacts, selectedIds: new Set(), categoryOf: (r) => r.smell.category,
  }).find((g) => g.category === "Bloaters");

  eq("a category sums its occurrences once", bloaters.effort, 90);
  const sumOfTypes = bloaters.types.reduce((n, t) => n + (t.effort || 0), 0);
  eq("which equals the sum over its types — added once, not twice", sumOfTypes, 90);

  eq("no records means no number, not zero", effortMinutes(ROWS, null), null);
  eq("unrecorded occurrences do not count as 0", effortMinutes(ROWS, new Map()), null);

  eq("minutes format", formatEffort(25), "~25 min");
  eq("hours format", formatEffort(70), "~1h 10m");
  eq("whole hours", formatEffort(120), "~2h");
  eq("absent effort", formatEffort(null), "—");
}

// ── 6. Selection state and partials ──────────────────────────────────────────
console.log("\nSelection");
{
  const magicRows = ROWS.filter((r) => r.smell.type === "MagicNumber");
  const none = selectionState(magicRows, new Set());
  check("nothing selected", none.none && !none.partial && !none.all);

  const one = selectionState(magicRows, new Set([magicRows[0].id]));
  check("one of 53 is partial", one.partial && !one.all);
  eq("and counted", [one.selected, one.total], [1, TOTAL_MAGIC]);

  const all = selectionState(magicRows, new Set(magicRows.map((r) => r.id)));
  check("all 53 is complete", all.all && !all.partial);

  // The brief's acceptance test: unticking one drops the parent to partial.
  const minusOne = new Set(magicRows.map((r) => r.id));
  minusOne.delete(magicRows[0].id);
  const partial = selectionState(magicRows, minusOne);
  check("52 of 53 is partial, not complete", partial.partial && !partial.all);
  eq("and reads 52 / 53", [partial.selected, partial.total], [TOTAL_MAGIC - 1, TOTAL_MAGIC]);
}

// ── 7. Dropdown options stay in step with the table ──────────────────────────
console.log("\nQuick-select options");
{
  const magicRows = ROWS.filter((r) => r.smell.type === "MagicNumber");
  const selectedIds = new Set(magicRows.map((r) => r.id));

  const types = smellTypeOptions(ROWS, { selectedIds });
  const magic = types.find((o) => o.key === "MagicNumber");
  eq("the dropdown reports the same totals as the table",
     [magic.findingCount, magic.fileCount], [TOTAL_MAGIC, 5]);
  check("and the same full-selection state", magic.selection.all === true);
  check("its rows are the ones the table would toggle",
        magic.rows.length === TOTAL_MAGIC);

  selectedIds.delete(magicRows[0].id);
  const after = smellTypeOptions(ROWS, { selectedIds }).find((o) => o.key === "MagicNumber");
  check("unticking one occurrence makes the option partial", after.selection.partial === true);
  eq("with no API call and no refresh",
     [after.selection.selected, after.selection.total], [TOTAL_MAGIC - 1, TOTAL_MAGIC]);

  const cats = categoryOptions(ROWS, {
    selectedIds: new Set(), categoryOf: (r) => r.smell.category,
  });
  const bloaters = cats.find((o) => o.key === "Bloaters");
  eq("a category option nests its smell types",
     bloaters.children.map((c) => c.label).sort(), ["LargeClass", "LongMethod"]);
  check("a nested type carries only its own rows",
        bloaters.children.every((c) => c.rows.every((r) => r.smell.category === "Bloaters")));
  eq("nested rows are not duplicated",
     bloaters.children.reduce((n, c) => n + c.rows.length, 0), bloaters.findingCount);
}

// ── 8. Summary panel ─────────────────────────────────────────────────────────
console.log("\nSelection summary");
{
  const picked = [ROWS[0], ROWS[1], ROWS.find((r) => r.smell.type === "LongMethod")];
  const impacts = new Map([[picked[2].id, {
    capability: { status: "executable" }, if_selected: { effort_minutes: 15 },
  }]]);

  const summary = selectionSummary(picked, { impacts, totalFindings: ROWS.length });
  eq("counts what is selected", [summary.selected, summary.total], [3, ROWS.length]);
  eq("unique affected files", summary.fileCount, 2);
  eq("severity is broken out", [summary.high, summary.low], [1, 2]);
  eq("auto-fixable comes from the records", summary.autoFixable, 1);
  eq("effort is summed", summary.effortMinutes, 15);

  const noImpacts = selectionSummary(picked, { totalFindings: ROWS.length });
  check("without records, capability is omitted rather than zeroed",
        noImpacts.autoFixable === null && noImpacts.advisory === null);
  check("and so is effort", noImpacts.effortMinutes === null);
  check("but the selection itself is still counted", noImpacts.selected === 3);
}

// ── 9. Helpers ───────────────────────────────────────────────────────────────
console.log("\nHelpers");
{
  eq("unique file count", fileCountOf(ROWS.filter((r) => r.smell.type === "MagicNumber")), 5);
  eq("empty spread", severitySpread([]), { high: 0, medium: 0, low: 0 });
  eq("no rows, no severity", worstSeverity([]), "unknown");
  eq("empty input is not a crash", groupBySmellType([]), []);
  eq("null input is not a crash", groupByCategory(null, {}), []);
}

// ── 10. Expand all ───────────────────────────────────────────────────────────
// The keys have to match what SmellReviewViews composes, character for
// character. These assertions spell the expected keys out by hand rather than
// deriving them, so a change to either side has to be made deliberately in
// both — a derived expectation would follow the bug.
console.log("\nExpand all");
{
  const smellGroups = groupBySmellType(ROWS);
  const smellKeys = expandAllKeys("smell", smellGroups);

  check("every smell type is opened",
        smellGroups.every((g) => smellKeys.has(g.type)));
  check("and each type's file buckets under it",
        smellKeys.has("MagicNumber:CommentsHeaven.java"));
  check("a file bucket is namespaced by its type, not bare",
        !smellKeys.has("CommentsHeaven.java"));

  // One key per group, plus one per (type, file) pair actually present.
  const pairs = new Set(ROWS.map((r) => `${r.smell.type}:${r.file}`));
  eq("no key is invented and none is missed",
     smellKeys.size, smellGroups.length + pairs.size);

  const catGroups = groupByCategory(ROWS, { order: [] });
  const catKeys = expandAllKeys("category", catGroups);
  const firstCat = catGroups[0];
  const firstType = firstCat.types[0];

  check("every category is opened", catGroups.every((g) => catKeys.has(g.category)));
  check("its types are namespaced by the category",
        catKeys.has(`${firstCat.category}:${firstType.type}`));
  check("and the files under those types carry both levels",
        catKeys.has(`${firstCat.category}:${firstType.type}:${firstType.rows[0].file}`));

  const fileGroups = groupByFile(ROWS);
  const fileKeys = expandAllKeys("file", fileGroups);
  const firstFile = fileGroups[0];

  check("every file is opened", fileGroups.every((g) => fileKeys.has(g.file)));
  check("with its smell types under it",
        fileKeys.has(`${firstFile.file}:${firstFile.types[0].type}`));
  // File wise renders findings directly under the type — there is no third
  // level, so producing keys for one would be dead state.
  check("and no third level, which File wise does not render",
        ![...fileKeys].some((k) => k.split(":").length > 2));

  eq("file wise opens exactly its two levels",
     fileKeys.size,
     fileGroups.length + fileGroups.reduce((n, g) => n + g.types.length, 0));

  check("no groups, no keys", expandAllKeys("smell", []).size === 0);
  check("null groups is not a crash", expandAllKeys("smell", null).size === 0);
  check("an unknown mode is treated as smell wise",
        expandAllKeys("nonsense", smellGroups).size === smellKeys.size);
  check("the result is a Set, ready to be openKeys",
        expandAllKeys("file", fileGroups) instanceof Set);
}

console.log(`\n${failures === 0 ? "All checks passed." : `${failures} check(s) FAILED.`}\n`);
process.exit(failures === 0 ? 0 : 1);
