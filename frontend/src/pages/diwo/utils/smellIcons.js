/**
 * smellIcons.js
 * =============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * One glyph per code smell type, and one per CUQA category.
 *
 * Stage 1 used to mark every group with the same coloured dot. A dot carries
 * exactly one bit — the severity colour — and repeats it on the row that
 * already states the severity in words, so scanning a list of thirty groups
 * meant reading thirty names with no visual anchor to tell them apart.
 *
 * These glyphs are chosen to say something about the smell rather than to
 * decorate it: nesting is a matryoshka, shotgun surgery is an explosion, a
 * temporary field is an hourglass, dead code is a coffin. A developer who has
 * seen a type once should recognise it by shape the next time.
 *
 * THE KEYS ARE CUQA'S OWN TYPE NAMES. They mirror SMELL_CATEGORY_MAP in
 * cuqa_agent/src/report_generator.py and FALLBACK_CATEGORY in
 * orchestration_agent/backend/domain/smell_taxonomy.py — the same 31 types,
 * spelled the same way. A type CUQA adds later still renders: it falls back to
 * its category's glyph, and then to a neutral one, so a new detector shows up
 * as an unfamiliar smell rather than as a crash or a blank column.
 *
 * Severity is NOT encoded here. It stays the colour behind the glyph, decided
 * by the caller from `severityColor`, because a glyph that changed with
 * severity would make the same smell look like two different things.
 */

/** Type -> glyph. Keys are CUQA's type names, verbatim. */
export const SMELL_ICON = {
  // ── Bloaters ──────────────────────────────────────────────────────────────
  LongMethod: "📏",
  LongFunction: "📏",
  LargeClass: "🧱",
  TooManyParameters: "🎛",
  PrimitiveObsession: "🔤",
  DataClumps: "🧺",

  // ── Object-Orientation Abusers ────────────────────────────────────────────
  SwitchStatements: "🔀",
  RefusedBequest: "🧬",
  TemporaryField: "⏳",
  AlternativeClassesWithDifferentInterfaces: "🔌",

  // ── Change Preventers ─────────────────────────────────────────────────────
  DuplicateCode: "👯",
  DivergentChange: "🔱",
  ShotgunSurgery: "💥",
  ParallelInheritanceHierarchies: "🪜",

  // ── Dispensables ──────────────────────────────────────────────────────────
  DeadCode: "⚰️",
  UnreachableCode: "🚧",
  UnusedVariable: "🗑️",
  LazyClass: "💤",
  Comments: "💬",
  SpeculativeGenerality: "🔮",
  DataClass: "📦",

  // ── Couplers ──────────────────────────────────────────────────────────────
  FeatureEnvy: "👀",
  InappropriateIntimacy: "💞",
  MessageChains: "⛓️",
  MiddleMan: "📮",

  // ── Security / Language-Specific ──────────────────────────────────────────
  UnsafeFunctionUsage: "☠️",
  DeepNesting: "🪆",
  GlobalVariable: "🌐",
  LargeHeaderFile: "📚",
  BareExcept: "🕳️",
  MagicNumber: "🔢",
};

/** Category -> glyph, for the Category wise headers and the overview chips. */
export const CATEGORY_ICON = {
  Bloaters: "🎈",
  "Object-Orientation Abusers": "🧬",
  "Change Preventers": "🔒",
  Dispensables: "🗑️",
  Couplers: "🔗",
  "Security / Language-Specific": "🛡️",
  Uncategorized: "❓",
};

/** What an unmapped type or category gets. Never an empty string. */
export const DEFAULT_ICON = "🧩";

/** A file with findings in it — File wise headers. */
export const FILE_ICON = "📄";

/**
 * The glyph for one smell type.
 *
 * `category` is optional and only consulted for a type this module does not
 * know. That ordering matters: a specific glyph always beats the category's
 * generic one, and the category glyph beats nothing at all.
 */
export function smellIcon(type, category) {
  return SMELL_ICON[type]
    || (category ? CATEGORY_ICON[category] : undefined)
    || DEFAULT_ICON;
}

/** The glyph for one CUQA category. */
export function categoryIcon(category) {
  return CATEGORY_ICON[category] || DEFAULT_ICON;
}
