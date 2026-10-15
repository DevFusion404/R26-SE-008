/**
 * SmellReviewViews.jsx
 * ====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The three arrangements of one set of findings. Every one of them ends at the
 * same leaf — an individual finding card, in a named file, at a line, with its
 * impact and a way into the source:
 *
 *     FileWiseView       file -> smell type -> the findings inside     (file select)
 *     SmellWiseView      smell type -> file -> the findings inside
 *     CategoryWiseView   category -> smell type -> file -> the findings
 *
 * The FILE level inside Smell wise and Category wise is what makes a
 * repository-wide smell type readable: "53 Magic Numbers" expanded into five
 * files that each say how many they carry is a list you can navigate.
 *
 * Selection
 * ---------
 * Smell wise and Category wise select individual findings into `selectedIds`,
 * at any level: a category, a smell type, a file inside a smell type, or one
 * finding. All four route through one `toggleRows`, so the levels can never
 * disagree and switching views keeps every tick.
 *
 * File wise selects whole FILES into `selected`. Its findings are shown in full
 * and can be opened in the source, but carry no checkbox: offering one would
 * promise a partial-file selection the mode cannot express or send.
 *
 * The finding card shows severity, type, entity, path:line range, category,
 * CUQA's message and the backend impact line (capability, points, risk,
 * effort) without a click. The full counterfactual stays in ImpactDrawer.
 *
 * Accordion keys are composed exactly as utils/smellGrouping::expandAllKeys
 * composes them — change one and Expand all silently stops opening a level.
 */

import { T, tint, sevTone, riskTone } from "../codeSmellTheme.js";
import { SeverityTag, Chip } from "../codeSmellUi.jsx";
import { TriCheckbox } from "./QuickSelectDropdown.jsx";
import { formatEffort, groupRowsByFile } from "../utils/smellGrouping";
import { smellLines } from "../utils/smellLines";
import { categoryIcon, FILE_ICON, smellIcon } from "../utils/smellIcons";

const stateOf = (selection) =>
  selection?.all ? "all" : selection?.partial ? "partial" : "none";

const CAPABILITY_TONE = {
  "Auto-fixable": T.accentText,
  Advisory: T.medium,
  Mixed: T.text2,
};

/** "2 high · 4 medium · 47 low" — only the levels actually present. */
function SeveritySpread({ spread }) {
  const parts = ["high", "medium", "low"].filter((level) => spread?.[level] > 0);
  if (parts.length === 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
      {parts.map((level) => (
        <span key={level} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 600, color: T.text2 }}>
          <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: sevTone(level) }} />
          {spread[level]} {level}
        </span>
      ))}
    </span>
  );
}

/** Capability, in words, from the probe — never inferred from severity. */
function CapabilityTag({ capability }) {
  if (!capability) return null;
  const tone = CAPABILITY_TONE[capability.label] || T.text2;
  return (
    <Chip tone={tone} title={`${capability.executable} auto-fixable · ${capability.advisory} advisory (SCTVA capability model)`}>
      {capability.label}
    </Chip>
  );
}

function Metric({ value, label }) {
  return (
    <span style={{ fontSize: 11, color: T.text3, whiteSpace: "nowrap" }}>
      <b style={{ color: T.text2, fontFamily: T.mono, fontWeight: 700 }}>{value}</b> {label}
    </span>
  );
}

// ─── Impact ──────────────────────────────────────────────────────────────────

/**
 * The impact chips every finding carries, with no interaction needed.
 *
 * Capability leads because it decides whether any of the rest is actionable:
 * an advisory finding's points are real but nobody collects them automatically.
 */
function ImpactLine({ record }) {
  if (!record) return null;

  const status = record.capability?.status || "unknown";
  const gain = record.if_selected?.quality_gain || {};
  const risk = record.if_selected?.risk || {};
  const minutes = record.if_selected?.effort_minutes;
  const executable = status === "executable";

  return (
    <>
      <Chip
        tone={executable ? T.accentText : status === "advisory" ? T.medium : T.text3}
        title={record.capability?.refactoring ? `SCTVA refactoring: ${record.capability.refactoring}` : undefined}
      >
        {executable ? "Auto-fixable" : status === "advisory" ? "Advisory · no auto-fix" : "Capability unknown"}
      </Chip>
      {executable && typeof gain.automated_points === "number" && (
        <Chip title="Expected quality gain if SCTVA applies the fix">+{gain.automated_points} pts</Chip>
      )}
      {!executable && typeof gain.potential_points === "number" && (
        <Chip title="Quality points available only if fixed by hand">{gain.potential_points} pts by hand</Chip>
      )}
      {executable && risk.band && (
        <Chip tone={riskTone(risk.band)} title="Transformation risk">{risk.band} risk</Chip>
      )}
      {executable && typeof minutes === "number" && minutes > 0 && (
        <Chip title="Estimated developer review effort">{formatEffort(minutes)}</Chip>
      )}
    </>
  );
}

/** The identifying glyph for a smell type, category or file. */
function Glyph({ icon, label, size = 20 }) {
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
 * selectable omits it and gets no checkbox rather than a disabled one. The
 * title row is itself the expander, so clicking a path opens it.
 */
function GroupHeader({
  title, icon, iconLabel, mono = false, metrics, selection, onToggleSelect,
  open, onToggleOpen, right, level = 0, selectLabel,
}) {
  const state = stateOf(selection);
  const indent = [0, 18, 34][level] || 0;
  const on = state !== "none";

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: `${level ? 8 : 12}px 14px ${level ? 8 : 12}px ${14 + indent}px`,
      background: on ? tint(T.accent, level ? 5 : 7) : level ? "transparent" : T.surface,
    }}>
      {onToggleSelect && (
        <button
          type="button"
          onClick={onToggleSelect}
          aria-pressed={state === "all"}
          aria-label={selectLabel || `Select ${title}`}
          title={state === "all" ? `Deselect all of ${title}` : `Select all of ${title}`}
          className="dw-focus"
          style={{ background: "none", border: "none", padding: 2, cursor: "pointer", display: "flex", flexShrink: 0, borderRadius: 4 }}
        >
          <TriCheckbox state={state} size={level ? 15 : 17} />
        </button>
      )}

      <button
        type="button"
        onClick={onToggleOpen}
        aria-expanded={open}
        title={mono ? title : undefined}
        className="dw-focus"
        style={{
          flex: 1, minWidth: 0, background: "none", border: "none", padding: 0,
          textAlign: "left", cursor: "pointer", color: "inherit", borderRadius: 4,
          display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap",
        }}
      >
        <span aria-hidden="true" style={{
          color: T.text3, fontSize: 10, flexShrink: 0, width: 10,
          display: "inline-block", transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s",
        }}>
          ▶
        </span>
        <Glyph icon={icon} label={iconLabel || title} size={level ? 17 : 20} />
        <span style={{
          fontSize: level ? 12.5 : 14, fontWeight: level === 2 ? 600 : 700, color: T.text,
          fontFamily: mono ? T.mono : "inherit",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          minWidth: 0, maxWidth: "100%",
        }}>
          {title}
        </span>
        <span style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          {metrics}
        </span>
      </button>

      <span style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        {right}
        {selection && (
          <span
            title={`${selection.selected} of ${selection.total} selected`}
            style={{
              fontSize: 11, fontFamily: T.mono, fontWeight: 700,
              padding: "1px 7px", borderRadius: 999,
              background: on ? tint(T.accent, 14) : T.sunken,
              color: on ? T.accentText : T.text3,
            }}
          >
            {selection.selected}/{selection.total}
          </span>
        )}
      </span>
    </div>
  );
}

/**
 * One finding — the leaf of all three views.
 *
 * Carries the full path and line range, so WHERE is answered without opening
 * anything. `selectable` is false in File wise, where the card is evidence
 * rather than a control. The checkbox, Impact and View code buttons stop
 * propagation, so none of them also toggles the card.
 */
function FindingCard({
  row, selected, selectable, onToggle, onView, onShowImpact, record, indent = 20,
}) {
  const { smell } = row;
  const { start, end } = smellLines(smell);
  const range = start ? (end > start ? `:${start}–${end}` : `:${start}`) : "";
  const category = row.category || smell.category;

  return (
    <div
      onClick={selectable ? () => onToggle(row.id) : undefined}
      style={{
        margin: `6px 10px 6px ${indent}px`,
        padding: "10px 12px",
        borderRadius: 10,
        background: selected ? tint(T.accent, 8) : T.surface,
        border: `1px solid ${selected ? tint(T.accent, 55) : T.border}`,
        boxShadow: selected ? `inset 3px 0 0 ${T.accent}` : T.shadowSm,
        cursor: selectable ? "pointer" : "default",
        transition: "background 0.12s, border-color 0.12s",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        {selectable && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggle(row.id); }}
            aria-pressed={selected}
            aria-label={`${selected ? "Deselect" : "Select"} ${smell.type || "finding"} at ${row.file}${smell.line ? ` line ${smell.line}` : ""}`}
            className="dw-focus"
            style={{ background: "none", border: "none", padding: 2, cursor: "pointer", display: "flex", flexShrink: 0, marginTop: 1, borderRadius: 4 }}
          >
            <TriCheckbox state={selected ? "all" : "none"} size={16} />
          </button>
        )}

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <SeverityTag severity={smell.severity} compact />
            <span style={{ fontSize: 12.5, fontWeight: 700, color: T.text }}>
              {smell.type || "Finding"}
            </span>
            {smell.entity && (
              <span style={{
                fontSize: 12, color: T.text2, fontFamily: T.mono, fontWeight: 600,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%",
              }}>
                {smell.entity}
              </span>
            )}
            {row.suggested && selected && (
              <Chip tone={T.violet} title="This finding was proposed by DIWO's selection optimiser (not by CUQA)">
                DIWO optimised
              </Chip>
            )}
          </div>

          <div
            title={`${row.file}${range}`}
            style={{
              marginTop: 4, fontSize: 11.5, color: T.text3, fontFamily: T.mono,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
          >
            {row.file}<span style={{ color: T.text2, fontWeight: 700 }}>{range}</span>
          </div>

          {smell.message && (
            <div style={{ fontSize: 12, color: T.text2, marginTop: 6, lineHeight: 1.55 }}>
              {smell.message}
            </div>
          )}

          <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {category && (
              <Chip title="CUQA category">
                <span aria-hidden="true">{categoryIcon(category)}</span>
                {category}
              </Chip>
            )}
            <ImpactLine record={record} />
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onView?.(row.file, row.id); }}
            title={`Open ${row.file}${smell.line ? ` at line ${smell.line}` : ""}`}
            className="dw-focus dw-hover"
            style={actionButton}
          >
            View code
          </button>
          {record && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onShowImpact?.(row); }}
              title="What selecting this buys, and what skipping it costs"
              className="dw-focus dw-hover"
              style={actionButton}
            >
              Impact details
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const actionButton = {
  padding: "5px 10px", borderRadius: 7, cursor: "pointer",
  background: T.surface, color: T.text2, border: `1px solid ${T.border}`,
  fontSize: 11, fontWeight: 700, whiteSpace: "nowrap",
};

/**
 * A smell type's findings, split by the file they live in. Used inside Smell
 * wise and Category wise, so both reach the same file -> finding structure.
 */
function FileBuckets({
  rows, impacts, selectedIds, openKeys, keyPrefix, onToggleOpen, onToggleRows,
  onToggleSmell, onView, onShowImpact, selectable = true, level = 2,
}) {
  const buckets = groupRowsByFile(rows, { impacts, selectedIds });

  return buckets.map((bucket) => {
    const key = `${keyPrefix}:${bucket.file}`;
    const open = openKeys.has(key);
    return (
      <div key={key} style={{ borderTop: `1px solid ${T.border}` }}>
        <GroupHeader
          level={level}
          mono
          title={bucket.file}
          icon={FILE_ICON}
          iconLabel={`File ${bucket.file}`}
          selection={selectable ? bucket.selection : undefined}
          onToggleSelect={selectable ? () => onToggleRows(bucket.rows) : undefined}
          open={open}
          onToggleOpen={() => onToggleOpen(key)}
          metrics={
            <>
              <Metric value={bucket.findingCount} label={bucket.findingCount === 1 ? "finding" : "findings"} />
              <SeveritySpread spread={bucket.severity} />
              {bucket.effort !== null && (
                <span style={{ fontSize: 11, color: T.text3 }}>{formatEffort(bucket.effort)}</span>
              )}
            </>
          }
        />

        {open && (
          <div style={{ paddingBottom: 6 }}>
            {bucket.rows.map((row) => (
              <FindingCard
                key={row.id}
                row={row}
                record={impacts?.get(row.id)}
                selectable={selectable}
                selected={selectedIds.has(row.id)}
                onToggle={onToggleSmell}
                onView={onView}
                onShowImpact={onShowImpact}
                indent={level === 2 ? 30 : 20}
              />
            ))}
          </div>
        )}
      </div>
    );
  });
}

/** The card every top-level group sits in. */
function GroupCard({ active, children }) {
  return (
    <div style={{
      background: T.sunken,
      border: `1px solid ${active ? tint(T.accent, 60) : T.border}`,
      borderRadius: 12, overflow: "hidden", flexShrink: 0,
      boxShadow: T.shadowSm,
    }}>
      {children}
    </div>
  );
}

/** A smell-type level: header, then its files. */
function SmellTypeGroup({
  group, level, keyPrefix, selectedIds, openKeys, onToggleOpen, onToggleRows,
  onToggleSmell, onView, onShowImpact, impacts,
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
        selection={group.selection}
        onToggleSelect={() => onToggleRows(group.rows)}
        selectLabel={`Select all ${group.findingCount} ${group.type} findings`}
        open={open}
        onToggleOpen={() => onToggleOpen(key)}
        metrics={
          <>
            <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
            <Metric value={group.fileCount} label={group.fileCount === 1 ? "file" : "files"} />
            <SeveritySpread spread={group.severity} />
            {group.effort !== null && (
              <span style={{ fontSize: 11, color: T.text3 }}>{formatEffort(group.effort)}</span>
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
          level={level === 0 ? 1 : 2}
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

const PRIORITY_TONE = { critical: T.high, high: T.high, medium: T.medium, low: T.text3 };

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
              selection={group.selection}
              onToggleSelect={() => onToggleRows(group.rows)}
              selectLabel={`Select all ${group.findingCount} findings in ${group.category}`}
              open={open}
              onToggleOpen={() => onToggleOpen(group.key)}
              metrics={
                <>
                  <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
                  <Metric value={group.fileCount} label={group.fileCount === 1 ? "file" : "files"} />
                  <Metric value={group.typeCount} label={group.typeCount === 1 ? "smell type" : "smell types"} />
                  {priority && (
                    <span style={{
                      fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
                      color: PRIORITY_TONE[priority] || T.text3,
                    }}>
                      {priority} priority
                    </span>
                  )}
                </>
              }
              right={<CapabilityTag capability={group.capability} />}
            />

            {open && group.types.map((type) => (
              <div key={type.key} style={{ borderTop: `1px solid ${T.border}` }}>
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
 * Whole-file selection. The checkbox belongs to the FILE; expanding shows the
 * smell types inside and, under each, the findings with their impact and a
 * way into the source — everything needed to judge the file, without a
 * per-finding tick this mode could not send.
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
              selection={{
                total: group.findingCount,
                selected: group.fileSelected ? group.findingCount : 0,
                all: group.fileSelected,
                partial: false,
                none: !group.fileSelected,
              }}
              onToggleSelect={() => onToggleFile(group.file)}
              selectLabel={`Select the whole file ${group.file} (${group.findingCount} findings)`}
              open={open}
              onToggleOpen={() => onToggleOpen(group.key)}
              metrics={
                <>
                  <Metric value={group.findingCount} label={group.findingCount === 1 ? "finding" : "findings"} />
                  <Metric value={group.typeCount} label={group.typeCount === 1 ? "smell type" : "smell types"} />
                  <SeveritySpread spread={group.severity} />
                  {group.language && <Chip>{group.language}</Chip>}
                  {typeof quality === "number" && (
                    <span style={{ fontSize: 11, color: quality >= 95 ? T.accentText : T.medium, fontWeight: 600 }}>
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
                    className="dw-focus dw-hover"
                    style={actionButton}
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
                <div key={type.key} style={{ borderTop: `1px solid ${T.border}` }}>
                  <GroupHeader
                    level={1}
                    title={type.type}
                    icon={smellIcon(type.type, type.rows[0]?.smell?.category)}
                    iconLabel={type.type}
                    open={typeOpen}
                    onToggleOpen={() => onToggleOpen(typeKey)}
                    metrics={
                      <>
                        <Metric value={type.findingCount} label={type.findingCount === 1 ? "finding" : "findings"} />
                        <SeveritySpread spread={type.severity} />
                        {type.effort !== null && (
                          <span style={{ fontSize: 11, color: T.text3 }}>{formatEffort(type.effort)}</span>
                        )}
                      </>
                    }
                    right={<CapabilityTag capability={type.capability} />}
                  />

                  {/* No per-finding checkbox, and never highlighted: this
                      mode's unit of selection is the file. */}
                  {typeOpen && (
                    <div style={{ paddingBottom: 6 }}>
                      {type.rows.map((row) => (
                        <FindingCard
                          key={row.id}
                          row={row}
                          record={impacts?.get(row.id)}
                          selectable={false}
                          selected={false}
                          onView={onView}
                          onShowImpact={onShowImpact}
                          indent={20}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </GroupCard>
        );
      })}
    </>
  );
}
