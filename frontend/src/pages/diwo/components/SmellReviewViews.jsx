/**
 * SmellReviewViews.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The three arrangements of one set of findings. Every one of them ends at the
 * same leaf — an individual finding, in a named file, at a line, with its
 * impact and a way into the source:
 *
 *     FileWiseView       file -> smell type -> the findings inside     (file select)
 *     SmellWiseView      smell type -> file -> the findings inside
 *     CategoryWiseView   category -> smell type -> file -> the findings
 *
 * The FILE level inside Smell wise and Category wise is what makes a
 * repository-wide smell type readable. "53 Magic Numbers" expanded into 53 flat
 * rows is a list whose neighbours differ only by a repeated path; expanded into
 * five files that each say how many they carry, it is a list you can navigate.
 *
 * Selection
 * ---------
 * Smell wise and Category wise select individual findings into `selectedIds`,
 * at any level: a category, a smell type, a file inside a smell type, or one
 * finding. All four route through one `toggleRows`, so the levels can never
 * disagree and switching views keeps every tick.
 *
 * File wise selects whole FILES into `selected`. Its findings are shown in full
 * and can be opened in the source, but cannot be ticked individually: doing so
 * would promise a partial-file selection the mode cannot express or send.
 *
 * CHECKBOXES LIVE ON GROUPS ONLY. A finding line is selected by clicking it,
 * and says so with a tinted background and an accent stripe down its left
 * edge. The line already carries a severity, an entity, a path, a message, a
 * capability and two figures; a checkbox and an icon in front of all that made
 * the one thing a developer scans for — which line is this — the hardest thing
 * on the row to find.
 *
 * Impact
 * ------
 * Every finding states its impact on the line itself — capability, quality
 * points, risk, effort — with no click required, and an Impact button opens the
 * full counterfactual in a dialog: what selecting it buys, what skipping it
 * costs, and why the number is what it is.
 */

import { C, severityColor } from "../diwoTheme.jsx";
import { TriCheckbox } from "./QuickSelectDropdown.jsx";
import { formatEffort, groupRowsByFile } from "../utils/smellGrouping";
import { categoryIcon, FILE_ICON, smellIcon } from "../utils/smellIcons";

const stateOf = (selection) =>
  selection?.all ? "all" : selection?.partial ? "partial" : "none";

const CAPABILITY_TONE = {
  "Auto-fixable": C.accent,
  Advisory: C.warn,
  Mixed: C.textSub,
};

/** "2 high · 4 medium · 47 low" — only the levels actually present. */
function SeveritySpread({ spread }) {
  const parts = ["high", "medium", "low"].filter((level) => spread?.[level] > 0);
  if (parts.length === 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
      {parts.map((level) => (
        <span key={level} style={{ fontSize: 10.5, fontWeight: 700, color: severityColor(level) }}>
          {spread[level]} {level}
        </span>
      ))}
    </span>
  );
}

/** Capability, in words, from the probe — never inferred from severity. */
function CapabilityTag({ capability }) {
  if (!capability) return null;
  const tone = CAPABILITY_TONE[capability.label] || C.textSub;
  return (
    <span
      title={`${capability.executable} auto-fixable · ${capability.advisory} advisory`}
      style={{
        fontSize: 10, fontWeight: 700, color: tone,
        border: `1px solid ${tone}40`, background: `${tone}12`,
        padding: "1px 7px", borderRadius: 20, whiteSpace: "nowrap",
      }}
    >
      {capability.label}
    </span>
  );
}

function Metric({ value, label, tone = C.textSub }) {
  return (
    <span style={{ fontSize: 11, color: C.textMuted, whiteSpace: "nowrap" }}>
      <b style={{ color: tone, fontFamily: "monospace", fontWeight: 800 }}>{value}</b> {label}
    </span>
  );
}

// ─── Impact ──────────────────────────────────────────────────────────────────

/**
 * The one-line impact every finding carries, with no interaction needed.
 *
 * Capability is the lead fact because it decides whether any of the rest is
 * actionable: an advisory finding's points are real but nobody is going to
 * collect them automatically.
 */
function ImpactLine({ record }) {
  if (!record) return null;

  const status = record.capability?.status || "unknown";
  const gain = record.if_selected?.quality_gain || {};
  const risk = record.if_selected?.risk || {};
  const minutes = record.if_selected?.effort_minutes;
  const executable = status === "executable";
  const tone = executable ? C.accent : status === "advisory" ? C.warn : C.textMuted;

  const facts = executable
    ? [
        typeof gain.automated_points === "number" ? `+${gain.automated_points} pts` : null,
        risk.band ? `${risk.band} risk` : null,
        minutes ? formatEffort(minutes) : null,
      ]
    : [
        typeof gain.potential_points === "number" ? `${gain.potential_points} pts by hand` : null,
        "no auto-fix",
      ];

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
      <span style={{
        fontSize: 10, fontWeight: 800, color: tone,
        background: `${tone}12`, border: `1px solid ${tone}40`,
        padding: "1px 7px", borderRadius: 20, whiteSpace: "nowrap",
      }}>
        {executable ? "Auto-fixable" : status === "advisory" ? "Advisory" : "Unknown"}
      </span>
      {facts.filter(Boolean).map((fact) => (
        <span key={fact} style={{ fontSize: 10.5, color: C.textMuted }}>{fact}</span>
      ))}
    </span>
  );
}

/**
 * The glyph that identifies a smell type, a category or a file.
 *
 * Bare — no tile, no border. The icon was drawn inside a tinted, outlined box,
 * which put a second bordered rectangle inside every already-bordered row and
 * made the header read as two nested boxes rather than one line. The icon says
 * WHICH smell this is on its own.
 *
 * GroupHeader still computes a per-row `accent` and hands it down, and Glyph
 * now ignores it — severity is already carried by the severity badge and the
 * row's own left stripe, so tinting the glyph too spent colour on a signal
 * that was on screen twice. The prop is left at the call site because that is
 * the one line to change if the tile is ever wanted back.
 */
function Glyph({ icon, label, size = 22 }) {
  if (!icon) return null;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      style={{
        width: size, height: size, flexShrink: 0, lineHeight: 1,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: size * 0.72,
      }}
    >
      {icon}
    </span>
  );
}

// ─── Shared structure ────────────────────────────────────────────────────────

/**
 * One accordion header. `onToggleSelect` is optional — a level that must not be
 * selectable simply omits it and gets no checkbox rather than a disabled one.
 * The TITLE toggles the accordion, so clicking a file path opens it.
 */
function GroupHeader({
  title, icon, iconLabel, mono = false, metrics, selection, onToggleSelect,
  open, onToggleOpen, accent, right, level = 0,
}) {
  const state = stateOf(selection);
  const indent = [0, 22, 40][level] || 0;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 9,
      padding: `${level ? 8 : 11}px 14px ${level ? 8 : 11}px ${14 + indent}px`,
      background: state === "none" ? "transparent" : `${C.accent}0a`,
      borderLeft: `3px solid ${state === "none" ? "transparent" : C.accent}`,
    }}>
      {onToggleSelect && (
        <button
          type="button"
          onClick={onToggleSelect}
          aria-pressed={state === "all"}
          aria-label={`Select ${title}`}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", display: "flex", flexShrink: 0 }}
        >
          <TriCheckbox state={state} size={level ? 14 : 16} />
        </button>
      )}

      {/* The whole title row is the expander, so the file path is clickable. */}
      <button
        type="button"
        onClick={onToggleOpen}
        aria-expanded={open}
        title={mono ? title : undefined}
        style={{
          flex: 1, minWidth: 0, background: "none", border: "none", padding: 0,
          textAlign: "left", cursor: "pointer", color: "inherit",
          display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap",
        }}
      >
        <span aria-hidden="true" style={{ color: C.textMuted, fontSize: 10, flexShrink: 0 }}>
          {open ? "▾" : "▸"}
        </span>
        <Glyph icon={icon} tone={accent} label={iconLabel || title} size={level ? 19 : 22} />
        <span style={{
          fontSize: level ? 12 : 13.5, fontWeight: 700, color: C.text,
          fontFamily: mono ? "ui-monospace, Menlo, Consolas, monospace" : "inherit",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          minWidth: 0, maxWidth: "100%",
        }}>
          {title}
        </span>
        <span style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          {metrics}
        </span>
      </button>

      <span style={{ display: "flex", alignItems: "center", gap: 9, flexShrink: 0 }}>
        {right}
        {selection && (
          <span style={{
            fontSize: 11, fontFamily: "monospace",
            color: selection.selected ? C.accent : C.textMuted,
          }}>
            {selection.selected}/{selection.total}
          </span>
        )}
        {/* An explicit expander beside the row, for anyone who does not think
            of a heading as a button. */}
        <button
          type="button"
          onClick={onToggleOpen}
          aria-expanded={open}
          aria-label={open ? `Collapse ${title}` : `Expand ${title}`}
          style={{
            padding: "3px 9px", borderRadius: 7, cursor: "pointer",
            background: C.bg, color: C.textSub, border: `1px solid ${C.border}`,
            fontSize: 10, fontWeight: 700, whiteSpace: "nowrap",
          }}
        >
          {open ? "Hide" : "Expand"}
        </button>
      </span>
    </div>
  );
}

/**
 * One finding. The leaf of all three views.
 *
 * Carries the full path and line, so the developer can see WHERE it is without
 * opening anything, and a View action that opens the file at that exact line.
 * `selectable` is false in File wise, where the row is evidence rather than a
 * control.
 *
 * Marked with a plain dot rather than the smell's glyph. The glyph belongs on
 * the GROUP heading, where it names a type once for the whole list; repeating
 * it on every occurrence of that same type said nothing new forty lines
 * running, and competed with the severity word next to it.
 */
function FindingRow({
  row, selected, selectable, onToggle, onView, onShowImpact, record, indent = 54,
}) {
  const { smell } = row;

  return (
    <div
      onClick={selectable ? () => onToggle(row.id) : undefined}
      style={{
        padding: `8px 14px 8px ${indent}px`,
        borderTop: `1px solid ${C.border}`,
        background: selected ? `${C.accent}12` : "transparent",
        // With no checkbox on the row, this stripe is what says "selected", so
        // it is louder than the tint it used to sit beside.
        boxShadow: selected ? `inset 3px 0 0 ${C.accent}` : "none",
        cursor: selectable ? "pointer" : "default",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 9 }}>
        {/* A bullet, not a grade. The severity sits in words immediately after
            it, in its own colour, so this marks the line without competing to
            describe it. */}
        <span
          aria-hidden="true"
          style={{
            width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
            background: C.danger, marginTop: 6,
          }}
        />

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: severityColor(smell.severity) }}>
              {smell.severity || "unknown"}
            </span>
            {smell.entity && (
              <span style={{
                fontSize: 11, color: C.textSub,
                fontFamily: "ui-monospace, Menlo, Consolas, monospace",
              }}>
                {smell.entity}
              </span>
            )}
            {/* Path + line together: the address of the finding, always shown
                so "where is this" never needs a click to answer. */}
            <span
              title={row.file}
              style={{
                fontSize: 10.5, color: C.textMuted,
                fontFamily: "ui-monospace, Menlo, Consolas, monospace",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                maxWidth: 320,
              }}
            >
              {row.file}{smell.line ? `:${smell.line}` : ""}
            </span>
          </div>

          {smell.message && (
            <div style={{ fontSize: 11, color: C.textMuted, marginTop: 3, lineHeight: 1.5 }}>
              {smell.message}
            </div>
          )}

          <div style={{ marginTop: 5, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <ImpactLine record={record} />
            {record && (
              <button
                type="button"
                /* Opens the dialog. Deliberately NOT tied to the checkbox:
                   ticking a finding is a decision, and unfolding a panel of
                   figures underneath every one of them turned a list of
                   choices into a wall of text nobody asked for. */
                onClick={(e) => { e.stopPropagation(); onShowImpact?.(row); }}
                title={`Open the full impact of this ${smell.type || "finding"}`}
                style={{
                  padding: "1px 8px", borderRadius: 20, cursor: "pointer",
                  // Amber, tinted the same way every other chip on the row is,
                  // so it reads as one of them rather than as a stray colour.
                  background: `${C.warn}14`, color: C.warn,
                  border: `1px solid ${C.warn}55`, fontSize: 9.5, fontWeight: 700,
                }}
              >
                Impact ↗
              </button>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onView?.(row.file, row.id); }}
          title={`Open ${row.file}${smell.line ? ` at line ${smell.line}` : ""} and show the code`}
          style={{
            flexShrink: 0, padding: "4px 11px", borderRadius: 7, cursor: "pointer",
            background: C.bg, color: C.textSub, border: `1px solid ${C.border}`,
            fontSize: 10.5, fontWeight: 700,
          }}
        >
          View code
        </button>
      </div>
    </div>
  );
}

/**
 * A smell type's findings, split by the file they live in.
 *
 * Used inside Smell wise and inside Category wise, so both reach the same
 * file → finding structure and a developer only learns it once.
 */
function FileBuckets({
  rows, impacts, selectedIds, openKeys, keyPrefix, onToggleOpen, onToggleRows,
  onToggleSmell, onView, onShowImpact, selectable = true,
}) {
  const buckets = groupRowsByFile(rows, { impacts, selectedIds });

  return buckets.map((bucket) => {
    const key = `${keyPrefix}:${bucket.file}`;
    const open = openKeys.has(key);
    return (
      <div key={key} style={{ borderTop: `1px solid ${C.border}` }}>
        <GroupHeader
          level={2}
          mono
          title={bucket.file}
          icon={FILE_ICON}
          iconLabel={`File ${bucket.file}`}
          accent={severityColor(bucket.worstSeverity)}
          selection={selectable ? bucket.selection : undefined}
          onToggleSelect={selectable ? () => onToggleRows(bucket.rows) : undefined}
          open={open}
          onToggleOpen={() => onToggleOpen(key)}
          metrics={
            <>
              <Metric value={bucket.findingCount} label={bucket.findingCount === 1 ? "finding" : "findings"} />
              <SeveritySpread spread={bucket.severity} />
              {bucket.effort !== null && (
                <span style={{ fontSize: 10.5, color: C.textMuted }}>{formatEffort(bucket.effort)}</span>
              )}
            </>
          }
        />

        {open && bucket.rows.map((row) => (
          <FindingRow
            key={row.id}
            row={row}
            record={impacts?.get(row.id)}
            selectable={selectable}
            selected={selectedIds.has(row.id)}
            onToggle={onToggleSmell}
            onView={onView}
            onShowImpact={onShowImpact}
            indent={62}
          />
        ))}
      </div>
    );
  });
}

/** The card every top-level group sits in. */
function GroupCard({ active, children }) {
  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${active ? C.accent : C.border}`,
      borderRadius: 11, overflow: "hidden", flexShrink: 0,
    }}>
      {children}
    </div>
  );
}

/** A smell-type level: header, then its files. */
function SmellTypeGroup({
  group, level, keyPrefix, selectedIds, openKeys, onToggleOpen, onToggleRows,
  onToggleSmell, onView, onShowImpact, impacts, selectable = true,
}) {
  const key = `${keyPrefix}${group.type}`;
  const open = openKeys.has(key);

  return (
    <>
      <GroupHeader
        level={level}
        title={group.type}
        icon={smellIcon(group.type, group.rows[0]?.smell?.category)}
        iconLabel={group.type}
        accent={severityColor(group.worstSeverity)}
        selection={selectable ? group.selection : undefined}
        onToggleSelect={selectable ? () => onToggleRows(group.rows) : undefined}
        open={open}
        onToggleOpen={() => onToggleOpen(key)}
        metrics={
          <>
            <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
            <Metric value={group.fileCount} label={group.fileCount === 1 ? "file" : "files"} />
            <SeveritySpread spread={group.severity} />
            {group.effort !== null && (
              <span style={{ fontSize: 10.5, color: C.textMuted }}>{formatEffort(group.effort)}</span>
            )}
          </>
        }
        right={<CapabilityTag capability={group.capability} />}
      />

      {open && (
        <FileBuckets
          rows={group.rows}
          impacts={impacts}
          selectedIds={selectedIds}
          openKeys={openKeys}
          keyPrefix={key}
          onToggleOpen={onToggleOpen}
          onToggleRows={onToggleRows}
          onToggleSmell={onToggleSmell}
          onView={onView}
          onShowImpact={onShowImpact}
          selectable={selectable}
        />
      )}
    </>
  );
}

// ─── Smell wise ──────────────────────────────────────────────────────────────

export function SmellWiseView({
  groups, selectedIds, openKeys, onToggleOpen, onToggleRows, onToggleSmell,
  onView, onShowImpact, impacts,
}) {
  return (
    <>
      {groups.map((group) => (
        <GroupCard key={group.key} active={!group.selection.none}>
          <SmellTypeGroup
            group={group}
            level={0}
            keyPrefix=""
            selectedIds={selectedIds}
            openKeys={openKeys}
            onToggleOpen={onToggleOpen}
            onToggleRows={onToggleRows}
            onToggleSmell={onToggleSmell}
            onView={onView}
            onShowImpact={onShowImpact}
            impacts={impacts}
          />
        </GroupCard>
      ))}
    </>
  );
}

// ─── Category wise ───────────────────────────────────────────────────────────

const PRIORITY_TONE = { critical: C.danger, medium: C.warn, low: C.textMuted };

export function CategoryWiseView({
  groups, priorities, selectedIds, openKeys, onToggleOpen, onToggleRows,
  onToggleSmell, onView, onShowImpact, impacts,
}) {
  return (
    <>
      {groups.map((group) => {
        const priority = priorities?.get(group.category);
        const open = openKeys.has(group.key);
        return (
          <GroupCard key={group.key} active={!group.selection.none}>
            <GroupHeader
              title={group.category}
              icon={categoryIcon(group.category)}
              iconLabel={`${group.category} category`}
              accent={PRIORITY_TONE[priority] || undefined}
              selection={group.selection}
              onToggleSelect={() => onToggleRows(group.rows)}
              open={open}
              onToggleOpen={() => onToggleOpen(group.key)}
              metrics={
                <>
                  <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
                  <Metric value={group.fileCount} label={group.fileCount === 1 ? "file" : "files"} />
                  <Metric value={group.typeCount} label={group.typeCount === 1 ? "smell type" : "smell types"} />
                  {priority && (
                    <span style={{ fontSize: 10, fontWeight: 700, color: PRIORITY_TONE[priority] || C.textMuted }}>
                      {priority} priority
                    </span>
                  )}
                </>
              }
              right={<CapabilityTag capability={group.capability} />}
            />

            {open && group.types.map((type) => (
              <div key={type.key} style={{ borderTop: `1px solid ${C.border}` }}>
                <SmellTypeGroup
                  group={type}
                  level={1}
                  keyPrefix={`${group.key}:`}
                  selectedIds={selectedIds}
                  openKeys={openKeys}
                  onToggleOpen={onToggleOpen}
                  onToggleRows={onToggleRows}
                  onToggleSmell={onToggleSmell}
                  onView={onView}
                  onShowImpact={onShowImpact}
                  impacts={impacts}
                />
              </div>
            ))}
          </GroupCard>
        );
      })}
    </>
  );
}

// ─── File wise ───────────────────────────────────────────────────────────────

/**
 * Whole-file selection.
 *
 * The checkbox belongs to the FILE. Expanding shows the smell types inside it
 * and, under each, the findings themselves with their impact and a way into the
 * source — everything needed to judge the file, without offering a per-finding
 * tick that this mode could not send.
 */
export function FileWiseView({
  groups, openKeys, onToggleOpen, onToggleFile, onViewFile, onView,
  onShowImpact, qualityOf, impacts,
}) {
  return (
    <>
      {groups.map((group) => {
        const open = openKeys.has(group.key);
        const quality = qualityOf?.(group.file);
        return (
          <GroupCard key={group.key} active={group.fileSelected}>
            <GroupHeader
              mono
              title={group.file}
              icon={FILE_ICON}
              iconLabel={`File ${group.file}`}
              accent={severityColor(group.worstSeverity)}
              selection={{
                total: group.findingCount,
                selected: group.fileSelected ? group.findingCount : 0,
                all: group.fileSelected,
                partial: false,
                none: !group.fileSelected,
              }}
              onToggleSelect={() => onToggleFile(group.file)}
              open={open}
              onToggleOpen={() => onToggleOpen(group.key)}
              metrics={
                <>
                  <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
                  <Metric value={group.typeCount} label={group.typeCount === 1 ? "smell type" : "smell types"} />
                  <SeveritySpread spread={group.severity} />
                  {group.language && (
                    <span style={{ fontSize: 10.5, color: C.textMuted }}>{group.language}</span>
                  )}
                  {typeof quality === "number" && (
                    <span style={{ fontSize: 10.5, color: quality >= 95 ? C.accent : C.warn }}>
                      Quality {quality.toFixed(1)}%
                    </span>
                  )}
                </>
              }
              right={
                <>
                  <CapabilityTag capability={group.capability} />
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onViewFile?.(group.file); }}
                    title="Open the original file with every finding marked"
                    style={{
                      padding: "3px 9px", borderRadius: 7, cursor: "pointer",
                      background: C.bg, color: C.textSub, border: `1px solid ${C.border}`,
                      fontSize: 10, fontWeight: 700, whiteSpace: "nowrap",
                    }}
                  >
                    View file
                  </button>
                </>
              }
            />

            {open && group.types.map((type) => {
              const typeKey = `${group.key}:${type.type}`;
              const typeOpen = openKeys.has(typeKey);
              return (
                <div key={type.key} style={{ borderTop: `1px solid ${C.border}` }}>
                  <GroupHeader
                    level={1}
                    title={type.type}
                    icon={smellIcon(type.type, type.rows[0]?.smell?.category)}
                    iconLabel={type.type}
                    accent={severityColor(type.worstSeverity)}
                    open={typeOpen}
                    onToggleOpen={() => onToggleOpen(typeKey)}
                    metrics={
                      <>
                        <Metric value={type.findingCount} label={type.findingCount === 1 ? "finding" : "findings"} />
                        <SeveritySpread spread={type.severity} />
                        {type.effort !== null && (
                          <span style={{ fontSize: 10.5, color: C.textMuted }}>{formatEffort(type.effort)}</span>
                        )}
                      </>
                    }
                    right={<CapabilityTag capability={type.capability} />}
                  />

                  {/* No per-finding checkbox: this mode selects whole files. */}
                  {typeOpen && type.rows.map((row) => (
                    <FindingRow
                      key={row.id}
                      row={row}
                      record={impacts?.get(row.id)}
                      selectable={false}
                      // Never highlighted here: this mode's unit of selection
                      // is the file, and a row lit up from a previous
                      // smell-wise session would claim a state File wise
                      // neither owns nor can change.
                      selected={false}
                      onView={onView}
                      onShowImpact={onShowImpact}
                      indent={44}
                    />
                  ))}
                </div>
              );
            })}
          </GroupCard>
        );
      })}
    </>
  );
}
