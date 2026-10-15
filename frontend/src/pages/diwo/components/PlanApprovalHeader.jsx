/**
 * PlanApprovalHeader.jsx
 * ======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The top of the Refactoring Plan Approval stage:
 *
 *     plan_xxx · target
 *     Extract Method · Move Method · …
 *     Steps | Files | Approved | Rejected | Manual | Pending | Will be sent
 *     Decision · DIWO recommendation · Method & impact             (children)
 *     ⚠ status                        [← Back to Smell Review] [Send N steps to SCTVA]
 *
 * Every figure is counted from the plan and the developer's decisions already
 * held by the page. The send button only OPENS the confirmation; the page's
 * submit handler is still the one path to the Transformation stage.
 */

import { T, tint, eyebrow, primaryButton } from "../codeSmellTheme.js";
import { Chip } from "../codeSmellUi.jsx";

export default function PlanApprovalHeader({
  plan, totalSteps, fileCount, counts, willSend, forcedManual,
  canProceed, blockedReason, onSend, onFallback, children,
}) {
  const methods = [...new Set((plan.steps || []).map((s) => s.refactoring).filter(Boolean))];
  const skipped = plan.skipped_smells || [];

  return (
    <section
      aria-label="Refactoring plan"
      style={{
        background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12,
        boxShadow: T.shadowSm, marginBottom: 16, overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap", padding: "14px 18px" }}>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div style={eyebrow}>Refactoring plan from RDP</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginTop: 3 }}>
            <span style={{ fontSize: 16, fontWeight: 800, color: T.text, fontFamily: T.mono }}>
              {plan.plan_id}
            </span>
            {plan.target && (
              <span style={{ fontSize: 12.5, color: T.text2, fontFamily: T.mono }}>{plan.target}</span>
            )}
          </div>
          {methods.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {methods.map((m) => <Chip key={m}>{m}</Chip>)}
            </div>
          )}
          {(plan.reordered || skipped.length > 0) && (
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 8, fontSize: 11.5, color: T.text3 }}>
              {plan.reordered && <span>↕ Steps were resequenced by the dependency analyzer.</span>}
              {skipped.length > 0 && (
                <span title={skipped.map((s) => `${s.smell_id} (${s.smell_type}): ${s.reason}`).join("\n")}>
                  ⚠ {skipped.length} smell{skipped.length > 1 ? "s" : ""} skipped — no viable refactoring (hover for detail)
                </span>
              )}
            </div>
          )}
        </div>

      </div>

      <dl style={{
        margin: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(104px, 1fr))",
        borderTop: `1px solid ${T.border}`, background: T.sunken,
      }}>
        <Counter label="Plan steps" value={totalSteps} />
        <Counter label="Affected files" value={fileCount} />
        <Counter label="Approved" value={counts.approved} tone={counts.approved ? T.accentText : undefined} />
        <Counter label="Rejected" value={counts.rejected} tone={counts.rejected ? T.high : undefined} />
        <Counter label="Manual" value={counts.manual} tone={counts.manual ? T.info : undefined} />
        <Counter label="Pending" value={counts.pending} tone={counts.pending ? T.medium : undefined} />
        <Counter
          label="Will be sent to SCTVA"
          value={willSend}
          tone={willSend ? T.accentText : undefined}
          highlight
          hint={forcedManual > 0 ? `${forcedManual} forced — no automatic SCTVA form` : null}
        />
      </dl>

      {/* How the plan below is arranged and filtered (PlanFilters). */}
      {children && (
        <div style={{ padding: "12px 18px 14px", borderTop: `1px solid ${T.border}` }}>
          {children}
        </div>
      )}

      {/* ── The two ways out of this stage, at the bottom of the panel ──── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        padding: "10px 18px", borderTop: `1px solid ${T.border}`,
        background: !canProceed && blockedReason ? tint(T.medium, 6) : T.sunken,
      }}>
        <span style={{ flex: "1 1 260px", fontSize: 11.5, color: canProceed ? T.accentText : T.medium }}>
          {canProceed
            ? `${willSend} approved step${willSend === 1 ? "" : "s"} ready to send to SCTVA.`
            : blockedReason ? `⚠ ${blockedReason}` : null}
        </span>
        <button
          type="button"
          onClick={onFallback}
          className="dw-focus"
          style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "12px 22px", borderRadius: 10, fontSize: 14, fontWeight: 700,
            cursor: "pointer", background: "#dc2626", color: "#ffffff",
            border: "1px solid #dc2626", boxShadow: T.shadowSm,
          }}
        >
          ← Back to Smell Review
        </button>
        <button
          type="button"
          onClick={onSend}
          disabled={!canProceed}
          title={canProceed ? undefined : blockedReason}
          className="dw-focus"
          style={primaryButton(canProceed, { padding: "12px 24px", borderRadius: 10, fontSize: 14 })}
        >
          Send {willSend} approved step{willSend === 1 ? "" : "s"} to SCTVA →
        </button>
      </div>
    </section>
  );
}

function Counter({ label, value, tone, highlight = false, hint }) {
  return (
    <div style={{
      padding: "9px 14px", minWidth: 0,
      boxShadow: `inset -1px 0 0 ${T.border}`,
      background: highlight ? tint(T.accent, 6) : "transparent",
    }}>
      <dt style={{ ...eyebrow, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</dt>
      <dd style={{ margin: "2px 0 0", fontSize: 19, fontWeight: 800, fontFamily: T.mono, color: tone || T.text, lineHeight: 1.2 }}>
        {value}
      </dd>
      {hint && <div style={{ fontSize: 10.5, color: T.medium, marginTop: 1 }}>{hint}</div>}
    </div>
  );
}
