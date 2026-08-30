/**
 * AuditSidebar.jsx
 * ================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The running audit trail and pipeline status, docked to the right of the
 * workflow.
 *
 * What it used to show, per entry:
 *
 *     14:32:07   [plan_approval] plan_approved (developer)
 *
 * — which is the name of a stage and the name of an event. It recorded that an
 * agent ran, then another agent ran, and nothing about what any of them did.
 * The backend was already storing the substance in each row's `details`; the
 * sidebar dropped it on the floor.
 *
 * What it shows now:
 *
 *     REFACTORING PLAN
 *     14:32:07  Developer approved 6 steps for transformation      ▸
 *       Approved 6 · Rejected 3 · Manual work 2 · Against advice 1
 *       ▾ Sent to the Transformation agent
 *           Long Method → Extract Method    OrderService.java
 *           God Class   → Extract Class     OrderService.java
 *       ▾ Kept for manual work
 *           Feature Envy → Move Method      PaymentGateway.java
 *
 * Two sources, merged:
 *   - the BACKEND trail (GET /workflows/<id>/audit-logs), which is persistent,
 *     structured, and the authoritative record;
 *   - the SESSION log (addLog), which is ephemeral running commentary and
 *     covers things the backend never sees, such as an offline fallback.
 *
 * Backend rows win where both describe the same thing, because the backend row
 * is the one that will still exist tomorrow. Session lines are marked as such
 * so nobody mistakes browser commentary for the persisted record.
 *
 * All narration lives in utils/auditNarrative.js — this file is layout.
 */

import { useState } from "react";
import { C } from "../diwoTheme.jsx";
import { TONE, groupByStage, narrateAll } from "../utils/auditNarrative";

const toneColor = (tone) =>
  tone === TONE.SUCCESS ? C.accent
    : tone === TONE.DANGER ? C.danger
      : tone === TONE.WARN ? C.warn
        : C.info;

const timeOf = (entry) => {
  if (entry.time) return entry.time;
  if (!entry.timestamp) return "";
  const parsed = new Date(entry.timestamp);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString();
};

/**
 * Session lines wrapped to look like narrated rows, so one list can render
 * both. They carry no `details`, so they never expand — which is honest: a
 * browser log line has no evidence behind it.
 */
const asSessionEntries = (sessionLog) =>
  (sessionLog || []).map((entry, i) => ({
    id: entry.id || `local-${i}`,
    stage: "session",
    stageLabel: "Session",
    action: "session_note",
    actor: "browser",
    time: entry.time,
    title: entry.event,
    tone: entry.type === "success" ? TONE.SUCCESS
      : entry.type === "danger" ? TONE.DANGER
        : entry.type === "warn" ? TONE.WARN : TONE.INFO,
    facts: [],
    groups: [],
    expandable: false,
    session: true,
  }));

export default function AuditSidebar({
  phase,
  auditLog = [],
  backendLog = [],
  onRefresh,
  refreshing = false,
}) {
  // Starts minimised. The trail is a record to consult, not a thing to read
  // alongside the work — and at 360px it was taking a quarter of the width off
  // every stage before the developer had asked for it. The rail keeps the entry
  // count visible, so it still says when there is something to go and look at.
  const [collapsed, setCollapsed] = useState(true);
  // "trail" is the persisted backend record; "session" is browser commentary.
  const [view, setView] = useState("trail");
  const [openIds, setOpenIds] = useState(() => new Set());

  const narrated = narrateAll(backendLog);
  const sessionEntries = asSessionEntries(auditLog);
  const showingTrail = view === "trail" && narrated.length > 0;
  const entries = showingTrail ? narrated : sessionEntries;
  const total = narrated.length + sessionEntries.length;

  const toggle = (id) =>
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // ── Minimised: a rail with the count and a way back ──────────────────────
  if (collapsed) {
    return (
      <div style={{
        width: 46, background: C.panel, borderLeft: `1px solid ${C.border}`,
        display: "flex", flexDirection: "column", alignItems: "center",
        padding: "12px 0", gap: 14, flexShrink: 0,
      }}>
        <button
          onClick={() => setCollapsed(false)}
          title="Expand the audit log"
          style={{
            width: 28, height: 28, borderRadius: 6, cursor: "pointer",
            background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
            fontSize: 12, fontWeight: 900, lineHeight: 1,
          }}
        >
          ‹
        </button>

        <div style={{
          writingMode: "vertical-rl", transform: "rotate(180deg)",
          fontSize: 11, fontWeight: 700, letterSpacing: 1,
          textTransform: "uppercase", color: C.textMuted,
        }}>
          Audit Log
        </div>

        {total > 0 && (
          <div style={{
            fontSize: 10, fontWeight: 800, fontFamily: "monospace",
            color: C.accent, background: `${C.accent}15`,
            border: `1px solid ${C.accent}40`, borderRadius: 10, padding: "2px 6px",
          }}>
            {total}
          </div>
        )}
      </div>
    );
  }

  // ── Expanded ─────────────────────────────────────────────────────────────
  return (
    <div style={{
      width: 360, background: C.panel, borderLeft: `1px solid ${C.border}`,
      display: "flex", flexDirection: "column", flexShrink: 0,
    }}>
      <div style={{
        padding: "14px 16px", borderBottom: `1px solid ${C.border}`,
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
      }}>
        <div style={{
          fontSize: 11, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 1, color: C.textMuted,
        }}>
          Audit Log
          {total > 0 && (
            <span style={{ color: C.textSub, marginLeft: 6, fontFamily: "monospace" }}>
              ({entries.length})
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={refreshing}
              title="Re-read the persisted audit trail from the backend"
              style={{
                width: 24, height: 24, borderRadius: 6,
                cursor: refreshing ? "wait" : "pointer",
                background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
                fontSize: 11, fontWeight: 900, lineHeight: 1,
                opacity: refreshing ? 0.5 : 1,
              }}
            >
              ⟳
            </button>
          )}
          <button
            onClick={() => setCollapsed(true)}
            title="Minimise the audit log"
            style={{
              width: 24, height: 24, borderRadius: 6, cursor: "pointer",
              background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
              fontSize: 12, fontWeight: 900, lineHeight: 1,
            }}
          >
            ›
          </button>
        </div>
      </div>

      {/* The two records are kept distinct rather than interleaved: one
          persists and one does not, and a reader needs to know which they are
          looking at before they trust a line in it. */}
      <div style={{ display: "flex", gap: 6, padding: "10px 16px 0" }}>
        {[
          { value: "trail", label: "Workflow trail", count: narrated.length },
          { value: "session", label: "Session notes", count: sessionEntries.length },
        ].map((tab) => {
          const active = (tab.value === "trail") === showingTrail;
          return (
            <button
              key={tab.value}
              onClick={() => setView(tab.value)}
              disabled={tab.value === "trail" && narrated.length === 0}
              title={
                tab.value === "trail"
                  ? "The persisted, structured record the backend keeps"
                  : "Running commentary from this browser session — not persisted"
              }
              style={{
                flex: 1, padding: "5px 8px", borderRadius: 7,
                fontSize: 10.5, fontWeight: 700, cursor: "pointer",
                background: active ? `${C.accent}18` : C.bg,
                color: active ? C.accent : C.textMuted,
                border: `1px solid ${active ? C.accent : C.border}`,
                opacity: tab.value === "trail" && narrated.length === 0 ? 0.45 : 1,
              }}
            >
              {tab.label} <span style={{ fontFamily: "monospace" }}>{tab.count}</span>
            </button>
          );
        })}
      </div>

      <div style={{
        flex: 1, overflowY: "auto", padding: "12px 14px",
        display: "flex", flexDirection: "column", gap: 12,
      }}>
        {entries.length === 0 && (
          <div style={{ fontSize: 11, color: C.textMuted, lineHeight: 1.6, padding: "8px 2px" }}>
            {showingTrail
              ? "No workflow events recorded yet."
              : "Nothing logged in this session yet."}
          </div>
        )}

        {showingTrail
          ? groupByStage(entries).slice().reverse().map((stage) => (
              <StageBlock
                key={stage.stage}
                stage={stage}
                openIds={openIds}
                onToggle={toggle}
              />
            ))
          : entries.slice().reverse().map((entry) => (
              <AuditEntry key={entry.id} entry={entry} open={false} onToggle={undefined} />
            ))}
      </div>

      <div style={{ padding: "14px 16px", borderTop: `1px solid ${C.border}` }}>
        <div style={{
          fontSize: 10, textTransform: "uppercase", letterSpacing: 1,
          color: C.textMuted, marginBottom: 8,
        }}>
          Agent Pipeline
        </div>
        {[
          { name: "Code Understanding", active: phase >= 0, done: phase > 0 },
          { name: "Refactoring Planning", active: phase >= 1, done: phase > 1 },
          { name: "Workflow Orchestration", active: true, done: false },
          { name: "Transformation", active: phase >= 2, done: phase > 2 },
          { name: "Validation", active: phase >= 2, done: phase >= 3 },
        ].map(({ name, active, done }) => (
          <div key={name} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: done ? C.accent : active ? C.warn : C.border, flexShrink: 0,
            }} />
            <span style={{ fontSize: 11, color: done ? C.accent : active ? C.text : C.textMuted }}>
              {name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────────

/** One workflow stage and the events recorded inside it. */
function StageBlock({ stage, openIds, onToggle }) {
  return (
    <div>
      <div style={{
        fontSize: 9.5, fontWeight: 800, letterSpacing: 1, textTransform: "uppercase",
        color: C.textSub, marginBottom: 6, display: "flex", alignItems: "center", gap: 7,
      }}>
        <span style={{ flex: 1 }}>{stage.label}</span>
        <span style={{ fontFamily: "monospace", color: C.textMuted }}>
          {stage.entries.length}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {stage.entries.slice().reverse().map((entry) => (
          <AuditEntry
            key={entry.id}
            entry={entry}
            open={openIds.has(entry.id)}
            onToggle={onToggle}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * One event: a headline that already says what happened, and — when the
 * backend recorded any — the evidence behind it, one click away.
 *
 * Collapsed by default. Twelve expanded entries listing every smell in a
 * repository is the same wall of text the old one-liner was avoiding; the
 * point is that the detail EXISTS and is reachable, not that it is always on.
 */
function AuditEntry({ entry, open, onToggle }) {
  const color = toneColor(entry.tone);
  const clickable = entry.expandable && typeof onToggle === "function";

  return (
    <div style={{
      borderLeft: `2px solid ${color}`, paddingLeft: 9,
      background: open ? C.bg : "transparent",
      borderRadius: open ? 6 : 0,
      paddingTop: open ? 6 : 0, paddingBottom: open ? 6 : 0,
      paddingRight: open ? 8 : 0,
    }}>
      <button
        type="button"
        onClick={clickable ? () => onToggle(entry.id) : undefined}
        aria-expanded={clickable ? open : undefined}
        disabled={!clickable}
        style={{
          width: "100%", textAlign: "left", background: "none", border: "none",
          padding: 0, cursor: clickable ? "pointer" : "default", color: "inherit",
          display: "flex", flexDirection: "column", gap: 2,
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 9.5, color: C.textMuted,
        }}>
          <span style={{ fontFamily: "monospace" }}>{timeOf(entry)}</span>
          {entry.actor && (
            <span style={{
              padding: "0 5px", borderRadius: 8, fontSize: 9,
              background: entry.actor === "developer" ? `${C.accent}18` : C.border,
              color: entry.actor === "developer" ? C.accent : C.textMuted,
            }}>
              {entry.actor}
            </span>
          )}
          {entry.session && (
            <span style={{ fontSize: 9, fontStyle: "italic" }}>not persisted</span>
          )}
          {clickable && (
            <span style={{ marginLeft: "auto", color: C.textSub, fontWeight: 800 }}>
              {open ? "▾" : "▸"}
            </span>
          )}
        </div>

        <div style={{ fontSize: 11.5, color: C.text, lineHeight: 1.45, fontWeight: 600 }}>
          {entry.title}
        </div>
      </button>

      {open && (
        <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 9 }}>
          {entry.facts.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "3px 10px" }}>
              {entry.facts.map((fact) => (
                <span key={fact.label} style={{ fontSize: 10, color: C.textMuted }}>
                  {fact.label}{" "}
                  <b style={{ color: C.textSub, fontFamily: "monospace" }}>{fact.value}</b>
                </span>
              ))}
            </div>
          )}

          {entry.groups.map((group) => (
            <div key={group.label}>
              <div style={{
                fontSize: 9, textTransform: "uppercase", letterSpacing: 0.8,
                color: C.textMuted, fontWeight: 700, marginBottom: 4,
              }}>
                {group.label}
                <span style={{ fontFamily: "monospace", marginLeft: 5 }}>
                  {group.items.length}
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {group.items.map((item) => (
                  <ItemRow key={item.key} item={item} />
                ))}
              </div>
            </div>
          ))}

          {entry.note && (
            <div style={{ fontSize: 9.5, color: C.textMuted, fontStyle: "italic" }}>
              {entry.note}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** One itemised row: "Long Method → Extract Method   OrderService.java". */
function ItemRow({ item }) {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 6,
      fontSize: 10, lineHeight: 1.45,
      padding: "3px 6px", borderRadius: 5, background: C.panel,
    }}>
      <span style={{ color: C.textSub, fontWeight: 600, minWidth: 0, flex: "1 1 auto" }}>
        {item.primary}
        {item.secondary && (
          <span style={{ color: C.textMuted, fontWeight: 400 }}> · {item.secondary}</span>
        )}
      </span>
      {item.file && (
        <span
          title={item.file}
          style={{
            fontFamily: "monospace", fontSize: 9, color: C.textMuted,
            maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis",
            whiteSpace: "nowrap", flexShrink: 0,
          }}
        >
          {item.file.split(/[\\/]/).pop()}
        </span>
      )}
      {item.tag && (
        <span style={{
          fontSize: 8.5, fontWeight: 700, textTransform: "uppercase",
          padding: "1px 5px", borderRadius: 8, flexShrink: 0,
          background: C.border, color: C.textSub,
        }}>
          {item.tag}
        </span>
      )}
    </div>
  );
}
