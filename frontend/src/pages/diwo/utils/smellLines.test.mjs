/**
 * smellLines.test.mjs
 * ===================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Guards the line maths behind the Code Smell Review source viewer: which
 * lines each smell marks, which line carries its dot, and which severity wins
 * when smells overlap. Getting this wrong points the developer at the wrong
 * code, which is worse than not showing the source at all.
 *
 * Dependency-free so it runs with the toolchain already in the project:
 *
 *     npm run test:diwo          (from frontend/)
 */

import { smellLines, withLines, buildCoverage, MAX_RANGE_LINES } from "./smellLines.js";

let failures = 0;
const check = (label, cond, detail = "") => {
  if (!cond) failures += 1;
  console.log(`  [${cond ? "PASS" : "FAIL"}] ${label}${!cond && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, got, want) =>
  check(label, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);

console.log("\n1. smellLines mirrors the backend rules");
eq("line only (MagicNumber)", smellLines({ line: 6 }), { line: 6, start: 6, end: 6 });
eq("start+end (LongFunction)", smellLines({ line: 5, start_line: 5, end_line: 80 }), { line: 5, start: 5, end: 80 });
eq("start_line only", smellLines({ start_line: 12 }), { line: 12, start: 12, end: 12 });
eq("end_line before start is clamped", smellLines({ line: 40, end_line: 10 }), { line: 40, start: 40, end: 40 });
eq("anchor outside its own span still marks", smellLines({ line: 3, start_line: 10, end_line: 20 }), { line: 3, start: 10, end: 20 });

console.log("\n2. a file-level smell must not mark line 1");
eq("line 0", smellLines({ line: 0 }), { line: 0, start: 0, end: 0 });
eq("line null", smellLines({ line: null }), { line: 0, start: 0, end: 0 });
eq("no line fields at all", smellLines({}), { line: 0, start: 0, end: 0 });
check("and it contributes no coverage",
  buildCoverage(withLines([{ id: "a", severity: "low", line: null }])).size === 0);

console.log("\n3. coverage over a realistic file");
const smells = [
  { id: "s1", type: "LongFunction",        severity: "high",   line: 5,  start_line: 5, end_line: 9 },
  { id: "s2", type: "MagicNumber",         severity: "low",    line: 7 },
  { id: "s3", type: "UnsafeFunctionUsage", severity: "medium", line: 7 },
  { id: "s4", type: "GlobalVariable",      severity: "medium", line: 20 },
];
const cov = buildCoverage(withLines(smells));

eq("lines covered", [...cov.keys()].sort((a, b) => a - b), [5, 6, 7, 8, 9, 20]);
check("line 4 untouched", !cov.has(4));
check("line 10 untouched (range ends at 9)", !cov.has(10));

eq("line 5: anchor of the long function", cov.get(5).anchors.map(s => s.id), ["s1"]);
eq("line 6: inside the span, no anchor", cov.get(6).anchors.map(s => s.id), []);
check("line 6 still gets a bar", cov.get(6).smells.length === 1);

eq("line 7: three smells overlap", cov.get(7).smells.map(s => s.id), ["s1", "s2", "s3"]);
eq("line 7: two are anchored there", cov.get(7).anchors.map(s => s.id), ["s2", "s3"]);
check("line 7 bar takes the WORST severity", cov.get(7).worst === "high", cov.get(7).worst);
check("line 20 bar is medium", cov.get(20).worst === "medium", cov.get(20).worst);

console.log("\n4. severity precedence is order-independent");
const lowFirst = buildCoverage(withLines([
  { id: "l", severity: "low", line: 3 },
  { id: "h", severity: "high", line: 3 },
]));
const highFirst = buildCoverage(withLines([
  { id: "h", severity: "high", line: 3 },
  { id: "l", severity: "low", line: 3 },
]));
check("low then high -> high", lowFirst.get(3).worst === "high", lowFirst.get(3).worst);
check("high then low -> high", highFirst.get(3).worst === "high", highFirst.get(3).worst);

console.log("\n5. a runaway range cannot blow up the map");
const huge = buildCoverage(withLines([
  { id: "big", severity: "high", line: 1, start_line: 1, end_line: MAX_RANGE_LINES + 5000 },
]));
check("only the anchor is marked", huge.size === 1, `size ${huge.size}`);
check("and it is the reported line", huge.has(1));

const atLimit = buildCoverage(withLines([
  { id: "ok", severity: "high", line: 1, start_line: 1, end_line: MAX_RANGE_LINES + 1 },
]));
check("a range exactly at the limit is still fully marked",
  atLimit.size === MAX_RANGE_LINES + 1, `size ${atLimit.size}`);

console.log(`\n${failures ? `FAILED: ${failures} check(s)` : "ALL CHECKS PASSED"}`);
process.exit(failures ? 1 : 0);
