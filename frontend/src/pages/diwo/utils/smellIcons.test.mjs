/**
 * smellIcons.test.mjs
 * ===================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Dependency-free, like every other suite here: `node smellIcons.test.mjs`.
 *
 * The check that earns its keep is the LAST one — that the icon table and the
 * orchestrator's FALLBACK_CATEGORY name the same 31 smell types. Those two
 * lists are maintained in different languages in different directories, and a
 * type added to one and missed in the other shows up as a silently generic
 * glyph on a smell CUQA is reporting by name.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  CATEGORY_ICON, DEFAULT_ICON, SMELL_ICON, categoryIcon, smellIcon,
} from "./smellIcons.js";

let pass = 0;
let fail = 0;
const ok = (label, cond, detail = "") => {
  if (cond) { pass += 1; console.log(`[PASS] ${label}`); }
  else { fail += 1; console.log(`[FAIL] ${label}${detail ? ` -- ${detail}` : ""}`); }
};
const eq = (label, actual, expected) =>
  ok(label, actual === expected, `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);

// ── Lookup ───────────────────────────────────────────────────────────────────
eq("a known type gets its own glyph", smellIcon("MagicNumber"), "🔢");
eq("nesting is a matryoshka", smellIcon("DeepNesting"), "🪆");
eq("the C alias matches its Java twin", smellIcon("LongFunction"), smellIcon("LongMethod"));

eq(
  "an unknown type falls back to its category",
  smellIcon("SomeNewDetector", "Bloaters"),
  CATEGORY_ICON.Bloaters,
);
eq("an unknown type with no category gets the neutral glyph", smellIcon("SomeNewDetector"), DEFAULT_ICON);
eq("an unknown CATEGORY also degrades", smellIcon("X", "Not A Category"), DEFAULT_ICON);
eq("a known type beats the category it was given", smellIcon("MagicNumber", "Bloaters"), "🔢");
eq("a missing type is not a crash", smellIcon(undefined), DEFAULT_ICON);
eq("nor is a null one", smellIcon(null, null), DEFAULT_ICON);

eq("categories resolve", categoryIcon("Couplers"), "🔗");
eq("an unmapped category degrades", categoryIcon("Nonsense"), DEFAULT_ICON);
eq("no category at all degrades", categoryIcon(undefined), DEFAULT_ICON);

// ── The table itself ─────────────────────────────────────────────────────────
ok(
  "every glyph is a non-empty string",
  Object.values(SMELL_ICON).every((v) => typeof v === "string" && v.length > 0),
);
ok(
  "every category has a glyph",
  Object.values(CATEGORY_ICON).every((v) => typeof v === "string" && v.length > 0),
);

// Distinctness is the point of the change: identical glyphs on two types would
// put the dotted-circle problem back, one level down. LongFunction is the C
// alias of LongMethod and is meant to share.
const dupes = Object.entries(SMELL_ICON).reduce((acc, [type, icon]) => {
  (acc[icon] = acc[icon] || []).push(type);
  return acc;
}, {});
const collisions = Object.entries(dupes)
  .filter(([, types]) => types.length > 1)
  .filter(([, types]) => !(types.length === 2 && types.includes("LongMethod") && types.includes("LongFunction")));
ok(
  "no two unrelated smell types share a glyph",
  collisions.length === 0,
  collisions.map(([icon, types]) => `${icon} = ${types.join(", ")}`).join(" | "),
);

// ── Agreement with the backend taxonomy ──────────────────────────────────────
const here = dirname(fileURLToPath(import.meta.url));
const taxonomyPath = join(
  here, "..", "..", "..", "..", "..",
  "agents", "orchestration_agent", "backend", "domain", "smell_taxonomy.py",
);

let backendTypes = null;
try {
  const src = readFileSync(taxonomyPath, "utf8");
  const block = src.slice(src.indexOf("FALLBACK_CATEGORY = {"));
  const body = block.slice(0, block.indexOf("\n}"));
  backendTypes = [...body.matchAll(/^\s*"([A-Za-z]+)":/gm)].map((m) => m[1]);
} catch {
  backendTypes = null;
}

if (!backendTypes || backendTypes.length === 0) {
  console.log("[SKIP] backend taxonomy not readable from here");
} else {
  const mine = new Set(Object.keys(SMELL_ICON));
  const missing = backendTypes.filter((t) => !mine.has(t));
  const extra = [...mine].filter((t) => !backendTypes.includes(t));

  ok(
    `every one of the ${backendTypes.length} backend smell types has an icon`,
    missing.length === 0,
    `missing: ${missing.join(", ")}`,
  );
  ok(
    "the icon table invents no smell type the backend does not know",
    extra.length === 0,
    `extra: ${extra.join(", ")}`,
  );

  // Categories must line up too, or Category wise headers go generic.
  const catBlock = readFileSync(taxonomyPath, "utf8");
  const backendCats = [...catBlock.matchAll(/^\s{4}"([^"]+)":\s*"(critical|medium|low)",$/gm)].map((m) => m[1]);
  const missingCats = backendCats.filter((c) => !(c in CATEGORY_ICON));
  ok(
    `every backend category has an icon (${backendCats.length} found)`,
    backendCats.length > 0 && missingCats.length === 0,
    `missing: ${missingCats.join(", ")}`,
  );
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
