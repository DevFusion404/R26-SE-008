/**
 * Stage 1 grouping and aggregation
 * ================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The three ways the Code Smell Review arranges ONE set of findings:
 *
 *     File wise      file  -> the smell types inside it
 *     Smell wise     smell type (repository-wide) -> its occurrences
 *     Category wise  CUQA category -> smell type -> occurrences
 *
 * The arrangement is the only thing that differs. Every mode reads the same
 * flattened `smellRows`, and Smell/Category wise write to the same `selectedIds`
 * set, so switching between them can never lose a selection.
 *
 * The bug this shape exists to prevent
 * ------------------------------------
 * Smell wise used to group by file first, so a Magic Number appearing 53 times
 * across 5 files produced FIVE separate "Magic Number" groups and a reader
 * counting them got five. Here a smell type is one row, repository-wide,
 * carrying `findingCount` (53) and `fileCount` (5) as separate numbers —
 * because "53 findings" and "5 files" are different facts and the old UI
 * collapsed them into one.
 *
 * `fileCount` is always a Set size over the group's own rows. Summing the file
 * counts of a category's smell types would double-count any file that carries
 * two of them, which is the normal case rather than the edge case.
 *
 * Pure functions over rows already in memory: the panel and the checkboxes have
 * to agree on the same frame, so nothing here fetches. Impact records are
 * optional everywhere — absent records mean a field is omitted, never zero.
 */

export const EXECUTABLE = "executable";
export const ADVISORY = "advisory";

const SEVERITY_RANK = { high: 0, medium: 1, low: 2, unknown: 3 };

/** "high" if any high, else "medium" if any medium, else "low". */
export function worstSeverity(rows) {
  let worst = "unknown";
  for (const row of rows || []) {
    const severity = (row?.smell?.severity || "unknown").toLowerCase();
    if ((SEVERITY_RANK[severity] ?? 99) < (SEVERITY_RANK[worst] ?? 99)) worst = severity;
  }
  return worst;
}

/** { high, medium, low } counts — never just the first row's severity. */
export function severitySpread(rows) {
  const spread = { high: 0, medium: 0, low: 0 };
  for (const row of rows || []) {
    const severity = (row?.smell?.severity || "").toLowerCase();
    if (severity in spread) spread[severity] += 1;
  }
  return spread;
}

/**
 * What SCTVA could do with a group, from the capability probe — never from
 * severity. A group is rarely uniform, so "Mixed" is a real answer rather than
 * a rounding of the majority.
 */
export function capabilityOf(rows, impacts) {
  if (!impacts) return null;

  let executable = 0;
  let advisory = 0;
  let known = 0;

  for (const row of rows || []) {
    const status = impacts.get(row.id)?.capability?.status;
    if (!status) continue;
    known += 1;
    if (status === EXECUTABLE) executable += 1;
    else if (status === ADVISORY) advisory += 1;
  }

  if (known === 0) return null;
  return {
    executable,
    advisory,
    label: advisory === 0 ? "Auto-fixable" : executable === 0 ? "Advisory" : "Mixed",
  };
}

/**
 * Summed review minutes for a group.
 *
 * Summed over the group's OWN occurrences, each counted once. A category total
 * is the sum over its occurrences, not the sum of its smell types' totals —
 * those are the same numbers, and adding pre-aggregated figures is how effort
 * estimates end up doubled.
 *
 * Returns null when no occurrence has a record, so the caller can hide the
 * field instead of printing a confident "0 min".
 */
export function effortMinutes(rows, impacts) {
  if (!impacts) return null;
  let total = 0;
  let seen = false;
  for (const row of rows || []) {
    const minutes = impacts.get(row.id)?.if_selected?.effort_minutes;
    if (typeof minutes === "number" && Number.isFinite(minutes)) {
      total += minutes;
      seen = true;
    }
  }
  return seen ? total : null;
}

/** "~25 min" / "~1h 10m" / "—". */
export function formatEffort(minutes) {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `~${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return rest ? `~${hours}h ${rest}m` : `~${hours}h`;
}

/** How much of a group the developer has ticked. */
export function selectionState(rows, selectedIds) {
  const total = (rows || []).length;
  const selected = (rows || []).filter((row) => selectedIds?.has(row.id)).length;
  return {
    total,
    selected,
    all: total > 0 && selected === total,
    partial: selected > 0 && selected < total,
    none: selected === 0,
  };
}

/** Unique files touched by a set of rows. Always a Set size, never a sum. */
export const fileCountOf = (rows) => new Set((rows || []).map((row) => row.file)).size;

/** The shared per-group aggregate every view renders from. */
function decorate(group, impacts, selectedIds) {
  const rows = group.rows;
  return {
    ...group,
    findingCount: rows.length,
    fileCount: fileCountOf(rows),
    files: Array.from(new Set(rows.map((row) => row.file))),
    severity: severitySpread(rows),
    worstSeverity: worstSeverity(rows),
    capability: capabilityOf(rows, impacts),
    effort: effortMinutes(rows, impacts),
    selection: selectionState(rows, selectedIds),
  };
}

// ─── Smell wise ──────────────────────────────────────────────────────────────

/**
 * One row per smell TYPE, across the whole repository.
 *
 * Ordered worst-severity first, then by how often it occurs: the type most
 * likely to be worth a bulk decision sits at the top.
 */
export function groupBySmellType(rows, { impacts, selectedIds } = {}) {
  const byType = new Map();
  const groups = [];

  for (const row of rows || []) {
    const type = row?.smell?.type || "Unknown";
    let group = byType.get(type);
    if (!group) {
      group = { key: type, type, rows: [] };
      byType.set(type, group);
      groups.push(group);
    }
    group.rows.push(row);
  }

  return groups
    .map((group) => decorate(group, impacts, selectedIds))
    .sort((a, b) =>
      (SEVERITY_RANK[a.worstSeverity] ?? 99) - (SEVERITY_RANK[b.worstSeverity] ?? 99)
      || b.findingCount - a.findingCount
      || a.type.localeCompare(b.type));
}

/**
 * Split one smell type's occurrences by the file they live in.
 *
 * The level between "53 Magic Numbers" and the 53 rows themselves. Without it,
 * expanding a repository-wide smell type dumps every occurrence into one flat
 * list where the only thing distinguishing neighbours is a path repeated on
 * every line; with it the same list reads as five files, each stating how many
 * it carries.
 *
 * The file bucket keeps its own rows, so ticking a file inside a smell type
 * selects exactly that type's findings in that file — not the file's other
 * smells, which belong to other types.
 */
export function groupRowsByFile(rows, { impacts, selectedIds } = {}) {
  const byFile = new Map();
  const groups = [];

  for (const row of rows || []) {
    const file = row.file || "(unknown file)";
    let group = byFile.get(file);
    if (!group) {
      group = { key: file, file, rows: [] };
      byFile.set(file, group);
      groups.push(group);
    }
    group.rows.push(row);
  }

  return groups
    .map((group) => ({
      ...group,
      findingCount: group.rows.length,
      severity: severitySpread(group.rows),
      worstSeverity: worstSeverity(group.rows),
      capability: capabilityOf(group.rows, impacts),
      effort: effortMinutes(group.rows, impacts),
      selection: selectionState(group.rows, selectedIds),
      // Line order inside a file is the order a developer reads it in.
      lines: group.rows.map((r) => r.smell?.line).filter(Boolean),
    }))
    .sort((a, b) =>
      (SEVERITY_RANK[a.worstSeverity] ?? 99) - (SEVERITY_RANK[b.worstSeverity] ?? 99)
      || b.findingCount - a.findingCount
      || a.file.localeCompare(b.file));
}

// ─── Category wise ───────────────────────────────────────────────────────────

/**
 * CUQA category -> smell type -> occurrences.
 *
 * `categoryOf` is injected rather than derived here: the category is CUQA's,
 * carried on the smell and backed by the orchestrator's taxonomy, and this
 * module has no business inventing one.
 *
 * `order` is the taxonomy's own category order, so the accordion and the
 * overview chips list categories in the same sequence.
 */
export function groupByCategory(rows, { impacts, selectedIds, categoryOf, order = [] } = {}) {
  const resolve = categoryOf || ((row) => row?.smell?.category || "Uncategorized");
  const byCategory = new Map();
  const groups = [];

  for (const row of rows || []) {
    const category = resolve(row) || "Uncategorized";
    let group = byCategory.get(category);
    if (!group) {
      group = { key: category, category, rows: [] };
      byCategory.set(category, group);
      groups.push(group);
    }
    group.rows.push(row);
  }

  const rank = new Map(order.map((name, i) => [name, i]));

  return groups
    .map((group) => ({
      ...decorate(group, impacts, selectedIds),
      // The types inside a category are the same shape as a top-level smell
      // group, so one row component renders both levels.
      types: groupBySmellType(group.rows, { impacts, selectedIds }),
    }))
    .map((group) => ({ ...group, typeCount: group.types.length }))
    .sort((a, b) => {
      const ra = rank.has(a.category) ? rank.get(a.category) : Number.MAX_SAFE_INTEGER;
      const rb = rank.has(b.category) ? rank.get(b.category) : Number.MAX_SAFE_INTEGER;
      return ra - rb || b.findingCount - a.findingCount || a.category.localeCompare(b.category);
    });
}

// ─── File wise ───────────────────────────────────────────────────────────────

/**
 * One row per FILE, holding the smell types inside it.
 *
 * File wise selects whole files, so these groups carry no per-occurrence
 * checkbox — `selection` here reports how much of the file the CURRENT
 * selection covers, which is what the header badge shows.
 */
export function groupByFile(rows, { impacts, selectedIds, selectedFiles } = {}) {
  const byFile = new Map();
  const groups = [];

  for (const row of rows || []) {
    const file = row.file || "(unknown file)";
    let group = byFile.get(file);
    if (!group) {
      group = { key: file, file, language: row.language, rows: [] };
      byFile.set(file, group);
      groups.push(group);
    }
    group.rows.push(row);
  }

  return groups
    .map((group) => ({
      ...decorate(group, impacts, selectedIds),
      types: groupBySmellType(group.rows, { impacts, selectedIds }),
      fileSelected: Boolean(selectedFiles?.has(group.file)),
    }))
    .map((group) => ({ ...group, typeCount: group.types.length }))
    .sort((a, b) =>
      (SEVERITY_RANK[a.worstSeverity] ?? 99) - (SEVERITY_RANK[b.worstSeverity] ?? 99)
      || b.findingCount - a.findingCount
      || a.file.localeCompare(b.file));
}

// ─── Quick-select dropdown options ───────────────────────────────────────────

/**
 * Options for the Smell Type dropdown.
 *
 * Built from ALL rows, not the filtered ones: the dropdown is how a developer
 * finds something the current filter is hiding, so an option list that shrank
 * with the filter would defeat its own purpose.
 */
export function smellTypeOptions(allRows, { impacts, selectedIds } = {}) {
  return groupBySmellType(allRows, { impacts, selectedIds }).map((group) => ({
    key: group.type,
    label: group.type,
    rows: group.rows,
    findingCount: group.findingCount,
    fileCount: group.fileCount,
    worstSeverity: group.worstSeverity,
    capability: group.capability,
    selection: group.selection,
  }));
}

/** Options for the Category dropdown, each expandable to its smell types. */
export function categoryOptions(allRows, { impacts, selectedIds, categoryOf, order } = {}) {
  return groupByCategory(allRows, { impacts, selectedIds, categoryOf, order }).map((group) => ({
    key: group.category,
    label: group.category,
    rows: group.rows,
    findingCount: group.findingCount,
    fileCount: group.fileCount,
    typeCount: group.typeCount,
    worstSeverity: group.worstSeverity,
    selection: group.selection,
    children: group.types.map((type) => ({
      key: `${group.category}:${type.type}`,
      label: type.type,
      rows: type.rows,
      findingCount: type.findingCount,
      fileCount: type.fileCount,
      worstSeverity: type.worstSeverity,
      selection: type.selection,
    })),
  }));
}

// ─── Selection summary ───────────────────────────────────────────────────────

/**
 * The sidebar's quick view of the current selection.
 *
 * Capability and effort are omitted — not zeroed — when no impact records
 * exist, so the panel never shows "0 auto-fixable" for a session where
 * auto-fixability was simply never computed.
 */
export function selectionSummary(selectedRows, { impacts, totalFindings } = {}) {
  const rows = selectedRows || [];
  const spread = severitySpread(rows);
  const capability = capabilityOf(rows, impacts);
  const effort = effortMinutes(rows, impacts);

  return {
    selected: rows.length,
    total: totalFindings ?? 0,
    fileCount: fileCountOf(rows),
    high: spread.high,
    medium: spread.medium,
    low: spread.low,
    autoFixable: capability ? capability.executable : null,
    advisory: capability ? capability.advisory : null,
    effortMinutes: effort,
    hasImpacts: Boolean(impacts),
  };
}

// ─── Expand all / hide all ───────────────────────────────────────────────────

/**
 * Every accordion key a view would use if the developer expanded all of it.
 *
 * The keys are BUILT THE SAME WAY THE VIEWS BUILD THEM, and that is the whole
 * reason this lives beside the grouping rather than in the page: a key composed
 * differently here would produce an "Expand all" that opens the top level and
 * silently leaves every nested level shut. The nesting per view is
 *
 *     smell      type                 ->  "<type>:<file>"
 *     category   category             ->  "<category>:<type>"  ->  "…:<file>"
 *     file       file                 ->  "<file>:<type>"
 *
 * File wise stops one level earlier on purpose: its findings hang directly off
 * the smell type, because the file is already the group.
 *
 * Returns a Set, ready to become `openKeys` as it stands.
 */
export function expandAllKeys(mode, groups) {
  const keys = new Set();
  const filesIn = (rows) => new Set((rows || []).map((r) => r.file || "(unknown file)"));

  for (const group of groups || []) {
    keys.add(group.key);

    if (mode === "file") {
      for (const type of group.types || []) keys.add(`${group.key}:${type.type}`);
      continue;
    }

    if (mode === "category") {
      for (const type of group.types || []) {
        const typeKey = `${group.key}:${type.type}`;
        keys.add(typeKey);
        for (const file of filesIn(type.rows)) keys.add(`${typeKey}:${file}`);
      }
      continue;
    }

    // Smell wise: the group IS the type, and its files hang off it.
    for (const file of filesIn(group.rows)) keys.add(`${group.key}:${file}`);
  }

  return keys;
}
