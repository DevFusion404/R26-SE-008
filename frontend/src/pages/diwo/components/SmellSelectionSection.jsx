/**
 * SmellSelectionSection.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Smell Selection section of the Code Smell Review: the toolbar (views,
 * search, sort, filters, summary tiles, bulk actions) and the list of group
 * cards, each holding big selectable boxes.
 *
 *     File wise      card = file        box = one smell type in that file
 *     Smell wise     card = smell type  box = one file it appears in
 *     Category wise  card = category    box = one smell type in it (all files)
 *
 * Every box and every card resolves to a set of finding rows, and every click
 * goes through the page's one `onToggleRows`, so the three views share one
 * selection (`selectedIds`) and switching tabs never loses a tick.
 *
 * Effort and Auto-fixable / Advisory come from the backend impact records.
 * Without them the badges and effort figures are omitted, never shown as zero.
 */

import { useMemo, useState } from "react";
import "../smellSelection.css";
import StrokeIcon from "./StrokeIcon.jsx";
import {
  capabilityOf, effortMinutes, formatEffort, selectionState, severitySpread, worstSeverity,
} from "../utils/smellGrouping";

// ─── Copy ────────────────────────────────────────────────────────────────────

const SMELL_DESCRIPTIONS = {
  LongMethod: "Methods that do too much to read in one pass.",
  LongFunction: "Functions that do too much to read in one pass.",
  LargeClass: "Classes carrying too many responsibilities.",
  TooManyParameters: "Signatures with more inputs than a caller can track.",
  PrimitiveObsession: "Raw primitives standing in for small domain types.",
  DataClumps: "The same group of values passed around together.",
  SwitchStatements: "Type switches that polymorphism could replace.",
  RefusedBequest: "Subclasses that ignore what they inherit.",
  TemporaryField: "Fields that are only set some of the time.",
  AlternativeClassesWithDifferentInterfaces: "Classes doing the same job behind different APIs.",
  DuplicateCode: "The same logic copied in more than one place.",
  DivergentChange: "One class changed for many unrelated reasons.",
  ShotgunSurgery: "One change forcing edits across many places.",
  ParallelInheritanceHierarchies: "Hierarchies that must grow in lockstep.",
  DeadCode: "Code that is never executed or used.",
  UnreachableCode: "Statements no execution path can reach.",
  UnusedVariable: "Variables assigned but never read.",
  LazyClass: "Classes that do too little to justify existing.",
  Comments: "Comments compensating for unclear code.",
  SpeculativeGenerality: "Abstractions built for needs that never came.",
  DataClass: "Classes holding data with no behaviour.",
  FeatureEnvy: "Methods more interested in another class's data.",
  InappropriateIntimacy: "Classes that know too much about each other.",
  MessageChains: "Long chains of calls through other objects.",
  MiddleMan: "Classes that only delegate to another.",
  UnsafeFunctionUsage: "Calls to functions known to be unsafe.",
  DeepNesting: "Logic buried under many levels of nesting.",
  GlobalVariable: "Shared mutable state at module level.",
  LargeHeaderFile: "Headers that pull in far more than needed.",
  BareExcept: "Exception handlers that catch everything.",
  MagicNumber: "Unnamed numbers that hide what they mean.",
};

const CATEGORY_DESCRIPTIONS = {
  Bloaters: "Code that has grown too large to work with easily.",
  "Object-Orientation Abusers": "Incomplete or incorrect use of OO design.",
  "Change Preventers": "Structure that makes one change ripple everywhere.",
  Dispensables: "Code that could be removed without losing anything.",
  Couplers: "Classes tied too tightly to one another.",
  "Security / Language-Specific": "Risky or language-specific constructs.",
};

const VIEWS = [
  { key: "file", label: "File wise" },
  { key: "smell", label: "Smell wise" },
  { key: "category", label: "Category wise" },
];

const HINTS = {
  file: "Tick a file to select everything in it, or click single smell boxes inside.",
  smell: "Tick a smell to fix it everywhere, or click the files you want inside.",
  category: "Tick a category to select all its smells, or pick smell boxes inside.",
};

const SORTS = [
  { key: "severity", label: "Severity" },
  { key: "findings", label: "Most findings" },
  { key: "name", label: "Name" },
];

const KICKER = { file: "File", smell: "Smell type", category: "Category" };
const ICON = { file: "fileCode", smell: "alert", category: "grid" };

const SEV_RANK = { high: 0, medium: 1, low: 2, unknown: 3 };
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ─── Aggregation ─────────────────────────────────────────────────────────────

/** Everything a card or box renders, derived from its own rows. */
function aggregate(base, rows, impacts, selectedIds) {
  return {
    ...base,
    rows,
    count: rows.length,
    spread: severitySpread(rows),
    worst: worstSeverity(rows),
    capability: capabilityOf(rows, impacts),
    effort: effortMinutes(rows, impacts),
    selection: selectionState(rows, selectedIds),
    files: Array.from(new Set(rows.map((r) => r.file))),
    types: Array.from(new Set(rows.map((r) => r.smell?.type || "Unknown"))),
  };
}

/** Bucket rows by a key, keeping first-seen order. */
function bucket(rows, keyOf) {
  const map = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return map;
}

/**
 * Severity: highest severity first, then most high, then most medium, then
 * most findings. Applied to the cards and to the boxes inside them.
 */
function sortItems(items, sort) {
  const byName = (a, b) => a.name.localeCompare(b.name);
  const compare = {
    severity: (a, b) =>
      (SEV_RANK[a.worst] ?? 9) - (SEV_RANK[b.worst] ?? 9)
      || b.spread.high - a.spread.high
      || b.spread.medium - a.spread.medium
      || b.count - a.count
      || byName(a, b),
    findings: (a, b) => b.count - a.count || byName(a, b),
    name: byName,
  }[sort] || byName;
  return [...items].sort(compare);
}

const typeOf = (row) => row.smell?.type || "Unknown";

/** Card + box structure for the active view. */
function buildCards(mode, rows, { impacts, selectedIds, sort }) {
  const agg = (base, r) => aggregate(base, r, impacts, selectedIds);

  const outer = mode === "file" ? (r) => r.file : mode === "smell" ? typeOf : (r) => r.category;
  const inner = mode === "smell" ? (r) => r.file : typeOf;

  const cards = Array.from(bucket(rows, outer), ([name, cardRows]) => {
    const boxes = Array.from(bucket(cardRows, inner), ([boxName, boxRows]) =>
      agg({ key: `${name}|${boxName}`, name: boxName }, boxRows));
    return {
      ...agg({ key: name, name }, cardRows),
      category: cardRows[0]?.category,
      language: cardRows[0]?.language,
      boxes: sortItems(boxes, sort),
    };
  });
  return sortItems(cards, sort);
}

const pressedOf = (selection) => (selection.all ? "true" : selection.partial ? "mixed" : "false");

// ─── Small pieces ────────────────────────────────────────────────────────────

function CheckMark({ state, size }) {
  if (state === "true") return <StrokeIcon name="check" size={size} strokeWidth={3} />;
  if (state === "mixed") return <StrokeIcon name="minus" size={size} strokeWidth={3} />;
  return null;
}

function SeverityPill({ level, count }) {
  if (!level || level === "unknown") return null;
  return (
    <span className={`ss-pill ss-pill--${level}`}>
      {count !== undefined ? `${count} ${level}` : level}
    </span>
  );
}

const FIX_CLASS = { "Auto-fixable": "auto", Advisory: "advisory", Mixed: "mixed" };

function FixBadge({ capability }) {
  if (!capability) return null;
  return (
    <span
      className={`ss-pill ss-pill--${FIX_CLASS[capability.label]}`}
      title={`${capability.executable} auto-fixable · ${capability.advisory} advisory`}
    >
      {capability.label}
    </span>
  );
}

function Effort({ minutes }) {
  if (minutes === null || minutes === undefined) return null;
  return (
    <span className="ss-effort">
      <StrokeIcon name="clock" size={15} />
      {formatEffort(minutes)}
    </span>
  );
}

function FileName({ path }) {
  const cut = path.lastIndexOf("/");
  return (
    <>
      {cut >= 0 && <span className="ss-dir">{path.slice(0, cut + 1)}</span>}
      {path.slice(cut + 1)}
    </>
  );
}

const SEV_COLOR = { high: "var(--ss-red)", medium: "var(--ss-amber)", low: "var(--ss-blue)" };

function SeverityMix({ spread }) {
  const levels = ["high", "medium", "low"].filter((l) => spread[l] > 0);
  const label = levels.map((l) => `${spread[l]} ${l}`).join(", ");
  return (
    <div className="ss-mix">
      <span className="ss-mix-label">Severity mix</span>
      <div className="ss-bar" role="img" aria-label={`Severity mix: ${label || "none"}`}>
        {levels.map((l) => (
          <span key={l} style={{ flex: spread[l], background: SEV_COLOR[l] }} />
        ))}
      </div>
      <div className="ss-legend">
        {levels.map((l) => (
          <span key={l}>
            <span className="ss-dot" style={{ background: SEV_COLOR[l] }} aria-hidden="true" />
            {spread[l]} {l}
          </span>
        ))}
      </div>
    </div>
  );
}

function qualityBand(score) {
  if (score >= 90) return { label: "Good", color: "var(--ss-teal-text)" };
  if (score >= 75) return { label: "Fair", color: "var(--ss-blue)" };
  return { label: "Needs work", color: "var(--ss-amber)" };
}

function QualityRing({ score }) {
  if (typeof score !== "number") return null;
  const r = 27;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score));
  const band = qualityBand(pct);
  return (
    <div className="ss-figure">
      <svg width="60" height="60" viewBox="0 0 60 60" role="img" aria-label={`Quality ${pct.toFixed(0)}%`}>
        <circle cx="30" cy="30" r={r} fill="none" stroke="var(--ss-track)" strokeWidth="6" />
        <circle
          cx="30" cy="30" r={r} fill="none" stroke={band.color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`} transform="rotate(-90 30 30)"
        />
        <text x="30" y="34.5" textAnchor="middle" className="ss-ring-text">{Math.round(pct)}%</text>
      </svg>
      <div>
        <div className="ss-figure-label">Quality</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: band.color }}>{band.label}</div>
      </div>
    </div>
  );
}

function Figure({ value, label }) {
  return (
    <div className="ss-figure">
      <span className="ss-figure-num">{value}</span>
      <span className="ss-figure-label">{label}</span>
    </div>
  );
}

/** A filter dropdown: label and value in one control, highlighted when set. */
function FilterSelect({ label, value, onChange, allLabel, options }) {
  const active = value !== "all";
  return (
    <div className={`ss-filter${active ? " ss-filter--active" : ""}`}>
      <label className="ss-filter-field">
        <span className="ss-filter-label">{label}</span>
        <span className="ss-filter-control">
          <select value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="all">{allLabel}</option>
            {options.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <StrokeIcon name="chevronDown" size={16} />
        </span>
      </label>
      {active && (
        <button
          type="button"
          className="ss-filter-clear"
          aria-label={`Clear the ${label} filter`}
          onClick={() => onChange("all")}
        >
          <StrokeIcon name="x" size={14} />
        </button>
      )}
    </div>
  );
}

// ─── Card ────────────────────────────────────────────────────────────────────

function GroupCard({ mode, card, open, onToggleOpen, onToggleRows, onViewFile, qualityOf }) {
  const pressed = pressedOf(card.selection);
  const isFile = mode === "file";
  const description = mode === "smell"
    ? SMELL_DESCRIPTIONS[card.name]
    : mode === "category" ? CATEGORY_DESCRIPTIONS[card.name] : null;

  const meta = [
    plural(card.count, "finding"),
    mode === "file" && plural(card.types.length, "smell type"),
    mode === "file" && card.language,
    mode === "smell" && card.category,
    mode === "category" && plural(card.files.length, "file"),
    card.effort !== null && formatEffort(card.effort),
  ].filter(Boolean).join(" · ");

  const gridId = `ss-grid-${mode}-${card.key}`.replace(/[^\w-]/g, "_");

  return (
    <article className={`ss-card${card.selection.all ? " ss-card--all" : ""}`}>
      <header className={`ss-head ss-head--${mode}`}>
        <div className="ss-head-main">
          <button
            type="button"
            className="ss-check"
            aria-pressed={pressed}
            aria-label={`Select all in ${card.name}`}
            onClick={() => onToggleRows(card.rows)}
          >
            <CheckMark state={pressed} size={16} />
          </button>

          <span className={`ss-icon ss-icon--${mode}`}>
            <StrokeIcon name={ICON[mode]} size={22} />
          </span>

          <div className="ss-title-block">
            <span className="ss-kicker">{KICKER[mode]}</span>
            <h3 className={`ss-title${isFile ? " ss-title--mono" : ""}`}>
              {isFile ? <FileName path={card.name} /> : card.name}
            </h3>
            {description && <span className="ss-desc">{description}</span>}
            <span className="ss-meta">{meta}</span>
          </div>
        </div>

        <SeverityMix spread={card.spread} />

        {/* Fixed-width columns from here on, so every card's mix, figure and
            actions start and end at the same x whatever the badge says. */}
        <div className="ss-figure-slot">
          {isFile && <QualityRing score={qualityOf(card.name)} />}
          {mode === "smell" && <Figure value={card.files.length} label={card.files.length === 1 ? "file affected" : "files affected"} />}
          {mode === "category" && <Figure value={card.types.length} label={card.types.length === 1 ? "smell type" : "smell types"} />}
        </div>

        <div className="ss-actions">
          <span className="ss-badge-slot"><FixBadge capability={card.capability} /></span>
          {isFile && (
            <button type="button" className="ss-btn ss-btn--sm" onClick={() => onViewFile(card.name)}>
              <StrokeIcon name="eye" size={16} />
              View file
            </button>
          )}
          <span className="ss-counter" aria-label={`${card.selection.selected} of ${card.selection.total} selected`}>
            {card.selection.selected} / {card.selection.total}
          </span>
          <button
            type="button"
            className="ss-collapse"
            aria-expanded={open}
            aria-controls={gridId}
            aria-label={`${open ? "Collapse" : "Expand"} ${card.name}`}
            onClick={onToggleOpen}
          >
            <StrokeIcon name="chevronDown" size={20} />
          </button>
        </div>
      </header>

      {open && (
        <div className="ss-grid" id={gridId}>
          {card.boxes.map((box) => (
            <SmellBox key={box.key} mode={mode} box={box} onToggleRows={onToggleRows} onViewFile={onViewFile} qualityOf={qualityOf} />
          ))}
        </div>
      )}
    </article>
  );
}

// ─── Box ─────────────────────────────────────────────────────────────────────

function SmellBox({ mode, box, onToggleRows, onViewFile, qualityOf }) {
  const pressed = pressedOf(box.selection);
  const isSmellView = mode === "smell";

  let sub;
  if (mode === "file") sub = SMELL_DESCRIPTIONS[box.name];
  else if (isSmellView) {
    const q = qualityOf(box.name);
    sub = typeof q === "number" ? `File quality ${Math.round(q)}%` : null;
  } else sub = `in ${plural(box.files.length, "file")}`;

  // A box spans one severity in the usual case; when it spans several, show each.
  const levels = ["high", "medium", "low"].filter((l) => box.spread[l] > 0);

  // Smell and Category wise: open each file this box covers in the source
  // viewer, focused on this smell's first finding there. File wise already has
  // View file on the card header.
  const viewFiles = mode === "file" ? [] : box.files.map((file) => ({
    file,
    focusId: box.rows.find((r) => r.file === file)?.id ?? null,
  }));
  const baseName = (path) => path.slice(path.lastIndexOf("/") + 1);

  return (
    <div className="ss-box" data-state={pressed}>
    <button
      type="button"
      className="ss-box-select"
      aria-pressed={pressed}
      aria-label={`Select ${box.name}, ${plural(box.count, "finding")}`}
      onClick={() => onToggleRows(box.rows)}
    >
      <span className="ss-box-top">
        <span className="ss-box-titles">
          <span className={`ss-box-title${isSmellView ? " ss-box-title--mono" : ""}`}>{box.name}</span>
          {sub && <span className="ss-box-sub">{sub}</span>}
        </span>
        <span className="ss-box-check" aria-hidden="true">
          <CheckMark state={pressed} size={14} />
        </span>
      </span>

      <span className="ss-count">
        <b>{box.count}</b>
        <span>{box.count === 1 ? "finding" : "findings"}</span>
      </span>

      <span className="ss-chips">
        {levels.length === 1
          ? <SeverityPill level={levels[0]} />
          : levels.map((l) => <SeverityPill key={l} level={l} count={box.spread[l]} />)}
        <FixBadge capability={box.capability} />
        <Effort minutes={box.effort} />
      </span>
    </button>

    {viewFiles.length > 0 && (
      <div className="ss-box-files">
        {viewFiles.map(({ file, focusId }) => (
          <button
            key={file}
            type="button"
            className="ss-view"
            title={file}
            aria-label={`View ${box.name === file ? "" : `${box.name} in `}${file}`}
            onClick={() => onViewFile(file, focusId)}
          >
            <StrokeIcon name="eye" size={14} />
            {isSmellView ? "View file" : <span>{baseName(file)}</span>}
          </button>
        ))}
      </div>
    )}
    </div>
  );
}

// ─── Section ─────────────────────────────────────────────────────────────────

export default function SmellSelectionSection({
  mode, onModeChange,
  rows, allRows,
  selectedIds, onToggleRows, onSelectShown, onClearSelection,
  selectedCount,
  search, onSearch, sort, onSort,
  smellTypeFilter, onSmellTypeFilter, categoryFilter, onCategoryFilter,
  filtersActive, onClearFilters, emptyMessage,
  impacts, qualityOf, onViewFile,
}) {
  // Expanded cards, keyed "<view>|<card>": collapsed is the default, and each
  // view remembers its own state.
  const [expanded, setExpanded] = useState(() => new Set());

  const cards = useMemo(
    () => buildCards(mode, rows, { impacts, selectedIds, sort }),
    [mode, rows, impacts, selectedIds, sort]
  );

  const counts = useMemo(() => ({
    file: new Set(rows.map((r) => r.file)).size,
    smell: new Set(rows.map(typeOf)).size,
    category: new Set(rows.map((r) => r.category)).size,
  }), [rows]);

  // Dropdown options come from every row, so a filter can always be undone
  // from the dropdown it was set in.
  const typeOptions = useMemo(
    () => Array.from(new Set(allRows.map(typeOf))).sort(),
    [allRows]
  );
  const categoryOptions = useMemo(
    () => Array.from(new Set(allRows.map((r) => r.category))).sort(),
    [allRows]
  );

  const keyOf = (card) => `${mode}|${card.key}`;
  const anyOpen = cards.some((c) => expanded.has(keyOf(c)));
  const anyClosed = cards.some((c) => !expanded.has(keyOf(c)));

  const toggleOpen = (card) => setExpanded((prev) => {
    const next = new Set(prev);
    const key = keyOf(card);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

  const setAll = (close) => setExpanded((prev) => {
    const next = new Set(prev);
    cards.forEach((c) => (close ? next.delete(keyOf(c)) : next.add(keyOf(c))));
    return next;
  });

  return (
    <div className="ss">
      <div className="ss-toolbar">
        {/* Row 1 — view, search, sort */}
        <div className="ss-row">
          <div className="ss-tabs" role="group" aria-label="Group findings by">
            {VIEWS.map((v) => (
              <button
                key={v.key}
                type="button"
                className="ss-tab"
                aria-pressed={mode === v.key}
                onClick={() => onModeChange(v.key)}
              >
                {v.label}
                <span className="ss-tab-count">{counts[v.key]}</span>
              </button>
            ))}
          </div>

          <label className="ss-search">
            <StrokeIcon name="search" size={18} />
            <input
              type="search"
              className="ss-input"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              placeholder="Search files, smell types, categories…"
              aria-label="Search files, smell types, categories"
            />
          </label>

          <label className="ss-field">
            <span className="ss-field-label">Sort by</span>
            <select className="ss-select" value={sort} onChange={(e) => onSort(e.target.value)}>
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        </div>

        {/* Row 2 — filters */}
        <div className="ss-row">
          <span className="ss-filters-title">Filter by</span>
          <FilterSelect
            label="Smell type"
            value={smellTypeFilter}
            onChange={onSmellTypeFilter}
            allLabel="All smell types"
            options={typeOptions}
          />
          <FilterSelect
            label="Category"
            value={categoryFilter}
            onChange={onCategoryFilter}
            allLabel="All categories"
            options={categoryOptions}
          />
        </div>

        {/* Row 3 — actions */}
        <div className="ss-row">
          <button
            type="button"
            className="ss-btn ss-btn--primary"
            onClick={onSelectShown}
            disabled={rows.length === 0}
          >
            <StrokeIcon name="check" size={18} strokeWidth={2.5} />
            Select shown ({rows.length})
          </button>
          <button
            type="button"
            className="ss-btn"
            onClick={onClearSelection}
            disabled={selectedCount === 0}
          >
            <StrokeIcon name="x" size={18} />
            Clear selection
          </button>
          <span className="ss-hint">{HINTS[mode]}</span>
          <span style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
            <button type="button" className="ss-btn ss-btn--sm" onClick={() => setAll(false)} disabled={!anyClosed}>
              Expand all
            </button>
            <button type="button" className="ss-btn ss-btn--sm" onClick={() => setAll(true)} disabled={!anyOpen}>
              Collapse all
            </button>
          </span>
        </div>
      </div>

      {cards.length === 0 ? (
        <div className="ss-empty">
          {filtersActive ? "No findings match your search." : emptyMessage}
          {filtersActive && (
            <button type="button" className="ss-link" onClick={onClearFilters}>Clear filters</button>
          )}
        </div>
      ) : (
        <div className="ss-cards">
          {cards.map((card) => (
            <GroupCard
              key={keyOf(card)}
              mode={mode}
              card={card}
              open={expanded.has(keyOf(card))}
              onToggleOpen={() => toggleOpen(card)}
              onToggleRows={onToggleRows}
              onViewFile={onViewFile}
              qualityOf={qualityOf}
            />
          ))}
        </div>
      )}
    </div>
  );
}
