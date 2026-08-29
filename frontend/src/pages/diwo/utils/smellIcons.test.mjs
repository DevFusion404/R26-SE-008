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
// Asserted against the TABLE rather than against particular glyphs. Which
// glyph a type carries is a design choice that gets revised; that the lookup
// returns exactly what the table holds is the contract.
eq("a known type returns its table entry", smellIcon("MagicNumber"), SMELL_ICON.MagicNumber);
eq("and so does another", smellIcon("DeepNesting"), SMELL_ICON.DeepNesting);
eq("the C alias matches its Java twin", smellIcon("LongFunction"), smellIcon("LongMethod"));

eq(
  "an unknown type falls back to its category",
  smellIcon("SomeNewDetector", "Bloaters"),
  CATEGORY_ICON.Bloaters,
);
eq("an unknown type with no category gets the neutral glyph", smellIcon("SomeNewDetector"), DEFAULT_ICON);
eq("an unknown CATEGORY also degrades", smellIcon("X", "Not A Category"), DEFAULT_ICON);
eq("a known type beats the category it was given",
   smellIcon("MagicNumber", "Bloaters"), SMELL_ICON.MagicNumber);
eq("a missing type is not a crash", smellIcon(undefined), DEFAULT_ICON);
eq("nor is a null one", smellIcon(null, null), DEFAULT_ICON);

eq("categories resolve", categoryIcon("Couplers"), CATEGORY_ICON.Couplers);
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

// Smell types are free to share a glyph — the table currently marks all of
// them the same way on purpose, with the type NAME doing the identifying. The
// categories are what must stay distinguishable, since their chips sit side by
// side in the overview bar with nothing else to separate them.
const catDupes = Object.entries(CATEGORY_ICON).reduce((acc, [name, icon]) => {
  (acc[icon] = acc[icon] || []).push(name);
  return acc;
}, {});
const catCollisions = Object.entries(catDupes).filter(([, names]) => names.length > 1);
ok(
  "no two categories share a glyph",
  catCollisions.length === 0,
  catCollisions.map(([icon, names]) => `${icon} = ${names.join(", ")}`).join(" | "),
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
