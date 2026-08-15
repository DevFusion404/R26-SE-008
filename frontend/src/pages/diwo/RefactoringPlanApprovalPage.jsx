/**
 * RefactoringPlanApprovalPage.jsx
 * ===============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 2 of the DIWO workflow: render the plan produced by the Refactoring
 * Decision & Planning (RDP) agent and let the developer approve or reject each
 * step before it reaches the Safe Transformation Agent.
 *
 * Data source, in priority order:
 *   1. Live RDP plan — POST http://localhost:5000/generate with the report the
 *      developer approved in Stage 1 (see rdpApi.generateRefactoringPlan).
 *      This is the authoritative plan: once it loads it stays on screen, so a
 *      backend preference update cannot swap the plan out mid-review.
 *   2. `planData` prop — the plan the DIWO backend returned with the smell
 *      selection. Used when the RDP agent is unreachable or has no report.
 *   3. Bundled sample plan (diwoData.PLAN_DATA) — only if the developer opts in
 *      after a failure, and it is labelled as sample data in the UI.
 *
 * The displayed plan travels back up on approve (`onApprove({ plan })`) so the
 * parent forwards the exact steps the developer saw, not whichever copy its own
 * state happens to hold.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { PLAN_DATA } from "./data/diwoData";
import { C, Card, Badge, Pill, impactColor, riskColor, severityColor } from "./diwoTheme.jsx";
import { generateRefactoringPlan, RDP_GENERATE_URL, RDP_BASE } from "./rdpApi";

export default function RefactoringPlanApprovalPage({
  onApprove,
  onFallback,
  planData,
  report,
  fullReport,
  onDecisionChange,
  onPlanLoaded,
}) {
  const [decisions, setDecisions] = useState({});
  const [opinion, setOpinion] = useState("");
  const [showOpinion, setShowOpinion] = useState(false);
  const [filter, setFilter] = useState("all");
  const [riskTolerance, setRiskTolerance] = useState("balanced");
  const [impactFocus, setImpactFocus] = useState("high");
  const [expanded, setExpanded] = useState(() => new Set());

  // ── RDP plan loading ───────────────────────────────────────────────────────
  const [generated, setGenerated] = useState(null);   // { plan, trace, rdpUrl, ... }
  const [loading, setLoading] = useState(Boolean(report));
  const [loadError, setLoadError] = useState(null);
  const [useSample, setUseSample] = useState(false);

  // Kept in a ref so an inline parent callback cannot re-trigger the request.
  const onPlanLoadedRef = useRef(onPlanLoaded);
  useEffect(() => {
    onPlanLoadedRef.current = onPlanLoaded;
  });

  const applyPlan = useCallback((result) => {
    setGenerated(result);
    setUseSample(false);
    setLoadError(null);
    setLoading(false);
    onPlanLoadedRef.current?.(result);
  }, []);

  const applyLoadError = useCallback((error) => {
    if (error.name === "AbortError") return;   // unmounted / superseded
    setLoadError(error);
    setLoading(false);
  }, []);

  /** Manual regenerate — runs from an event handler, so setState is safe here. */
  const reloadPlan = useCallback(() => {
    if (!report) return;
    setLoading(true);
    setLoadError(null);
    generateRefactoringPlan({ report, fullReport }).then(applyPlan).catch(applyLoadError);
  }, [report, fullReport, applyPlan, applyLoadError]);

  // Initial load. `loading` already starts as Boolean(report), so the effect
  // only has to resolve the request — nothing is set synchronously.
  useEffect(() => {
    if (!report) return undefined;
    const controller = new AbortController();
    generateRefactoringPlan({ report, fullReport, signal: controller.signal })
      .then(applyPlan)
      .catch(applyLoadError);
    return () => controller.abort();
  }, [report, fullReport, applyPlan, applyLoadError]);

  // ── Resolve the plan actually being rendered ───────────────────────────────
  const currentPlan = generated?.plan || planData || (useSample ? PLAN_DATA : null);
  const origin = generated ? "rdp" : planData ? "workflow" : useSample ? "sample" : null;
  const steps = currentPlan?.steps || [];

  // Decisions are keyed by step_id, so they must not survive a different plan.
  // Adjusted during render rather than in an effect — no cascading render, no
  // stale first paint of the previous plan's approvals.
  const planKey = currentPlan ? `${origin}:${currentPlan.plan_id}` : null;
  const [prevPlanKey, setPrevPlanKey] = useState(planKey);
  if (planKey !== prevPlanKey) {
    setPrevPlanKey(planKey);
    setDecisions({});
    setExpanded(new Set());
  }

  const decide = (id, val) => setDecisions(prev => {
    const next = { ...prev, [id]: val };
    onDecisionChange?.({
      decisions: next,
      preferences: {
        risk_tolerance: riskTolerance,
        impact_focus: impactFocus,
      },
    });
    return next;
  });

  const toggleExpanded = (id) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const allApproved = steps.length > 0 && steps.every(step => decisions[step.step_id] === "approve");

  const toggleSelectAll = () => {
    if (allApproved) {
      setDecisions({});
      return;
    }
    setDecisions(Object.fromEntries(steps.map(step => [step.step_id, "approve"])));
  };

  // ── Loading / error states ─────────────────────────────────────────────────
  if (loading && !currentPlan) {
    return <LoadingState />;
  }

  if (!currentPlan) {
    return (
      <ErrorState
        error={loadError}
        canRetry={Boolean(report)}
        onRetry={reloadPlan}
        onFallback={onFallback}
        onUseSample={() => {
          setUseSample(true);
          setLoadError(null);
        }}
      />
    );
  }

  const filtered = filter === "all" ? steps : steps.filter(s => {
    if (filter === "approved") return decisions[s.step_id] === "approve";
    if (filter === "rejected") return decisions[s.step_id] === "reject";
    if (filter === "pending") return !decisions[s.step_id];
    return (s.impact || s.expected_impact) === filter;
  });

  const approved = steps.filter(s => decisions[s.step_id] === "approve").length;
  const rejected = steps.filter(s => decisions[s.step_id] === "reject").length;
  const pending = steps.filter(s => !decisions[s.step_id]).length;
  const canProceed = approved > 0 && pending === 0;
  const refactoringTypes = [...new Set(steps.map(s => s.refactoring))];
  const summaryText = typeof currentPlan.summary === "string"
    ? currentPlan.summary
    : `Total steps: ${currentPlan.summary?.total_steps || steps.length} · High impact: ${currentPlan.summary?.high_impact || 0}`;
  const skipped = currentPlan.skipped_smells || [];

  const submit = () => {
    if (!canProceed) return;
    onApprove({
      decisions,
      opinion,
      plan: currentPlan,
      preferences: { risk_tolerance: riskTolerance, impact_focus: impactFocus },
    });
  };

  return (
    <div>
      <SourceBanner
        origin={origin}
        meta={generated}
        loading={loading}
        error={loadError}
        canRefresh={Boolean(report)}
        onRefresh={reloadPlan}
      />

      <Card style={{ marginBottom: 20 }} glow={C.accentGlow}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Refactoring Planning Agent Output</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.text, marginBottom: 6 }}>
              {currentPlan.plan_id}
              {currentPlan.target && (
                <span style={{ fontSize: 12, fontWeight: 500, color: C.textMuted, marginLeft: 10, fontFamily: "monospace" }}>
                  {currentPlan.target}
                </span>
              )}
            </div>
            <div style={{ fontSize: 13, color: C.textSub, maxWidth: 620 }}>{summaryText}</div>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {refactoringTypes.map(t => <Badge key={t} label={t} color={C.info} />)}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginTop: 16 }}>
          {[
            { label: "Total Steps", val: steps.length, color: C.text },
            { label: "Approved", val: approved, color: C.accent },
            { label: "Rejected", val: rejected, color: C.danger },
            { label: "Pending", val: pending, color: C.warn },
          ].map(({ label, val, color }) => (
            <div key={label} style={{ background: C.bg, borderRadius: 8, padding: "12px", textAlign: "center" }}>
              <div style={{ fontSize: 22, fontWeight: 800, color, fontFamily: "monospace" }}>{val}</div>
              <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
            </div>
          ))}
        </div>
        {(skipped.length > 0 || currentPlan.reordered) && (
          <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap", fontSize: 11, color: C.textMuted }}>
            {currentPlan.reordered && (
              <span>↕ Steps were resequenced by the dependency analyzer.</span>
            )}
            {skipped.length > 0 && (
              <span title={skipped.map(s => `${s.smell_id} (${s.smell_type}): ${s.reason}`).join("\n")}>
                ⚠ {skipped.length} smell{skipped.length > 1 ? "s" : ""} skipped — no viable refactoring (hover for detail).
              </span>
            )}
          </div>
        )}
      </Card>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        {["all", "approved", "rejected", "pending", "high", "medium", "low"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, cursor: "pointer", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted, border: `1px solid ${filter === f ? C.accent : C.border}`
          }}>{f}</button>
        ))}
        <button onClick={toggleSelectAll} style={{
          padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700, cursor: "pointer", border: `1px solid ${C.accent}`,
          background: `${C.accent}15`, color: C.accent, textTransform: "uppercase"
        }}>
          {allApproved ? "Deselect All" : "Select All"}
        </button>
        <select value={riskTolerance} onChange={(e) => setRiskTolerance(e.target.value)} style={{
          padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}`,
        }}>
          <option value="conservative">Risk: Conservative</option>
          <option value="balanced">Risk: Balanced</option>
          <option value="aggressive">Risk: Aggressive</option>
        </select>
        <select value={impactFocus} onChange={(e) => setImpactFocus(e.target.value)} style={{
          padding: "5px 10px", borderRadius: 8, fontSize: 11, background: C.panel, color: C.text, border: `1px solid ${C.border}`,
        }}>
          <option value="high">Impact: High</option>
          <option value="medium">Impact: Medium</option>
          <option value="low">Impact: Low</option>
        </select>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 400, overflowY: "auto", paddingRight: 4 }}>
        {filtered.length === 0 && (
          <div style={{ padding: "28px 20px", textAlign: "center", background: C.panel, border: `1px dashed ${C.border}`, borderRadius: 10, color: C.textMuted, fontSize: 13 }}>
            {steps.length === 0
              ? "The Refactoring Planning Agent produced no steps for this report — every smell was skipped. Fall back to Smell Review and select different files."
              : "No steps match the current filter."}
          </div>
        )}

        {filtered.map(step => {
          const dec = decisions[step.step_id];
          const borderColor = dec === "approve" ? C.accent : dec === "reject" ? C.danger : C.border;
          const bgColor = dec === "approve" ? `${C.accent}08` : dec === "reject" ? `${C.danger}08` : C.panel;
          const isOpen = expanded.has(step.step_id);
          const targetLabel =
            [step.target?.class, step.target?.method].filter(Boolean).join(".") ||
            step.target?.file ||
            "(module level)";

          return (
            <div key={step.step_id} style={{ background: bgColor, border: `1px solid ${borderColor}`, borderRadius: 10, padding: "14px 18px", transition: "all 0.2s" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>Step {step.step_id}</span>
                    <Badge label={step.refactoring} color={C.info} />
                    <Pill label={`Impact: ${step.impact || step.expected_impact || "medium"}`} color={impactColor(step.impact || step.expected_impact || "medium")} />
                    <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
                    {step.smell_type && (
                      <Pill label={step.smell_type} color={severityColor(step.severity)} />
                    )}
                    {typeof step.score === "number" && (
                      <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace" }} title={`MCDA score (${step.scoring_method || "mcda"})`}>
                        score {step.score.toFixed(2)}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 4 }}>
                    {targetLabel}
                    {step.target?.file && (
                      <span style={{ fontSize: 11, fontWeight: 400, color: C.textMuted, marginLeft: 8, fontFamily: "monospace" }}>
                        {step.target.file}
                        {Array.isArray(step.target.lines) && step.target.lines.length > 0
                          ? `:${step.target.lines.join("-")}`
                          : ""}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.5 }}>{step.explanation}</div>

                  <button onClick={() => toggleExpanded(step.step_id)} style={{
                    background: "none", border: "none", padding: 0, marginTop: 8, cursor: "pointer",
                    color: C.textMuted, fontSize: 11, fontWeight: 600, display: "flex", alignItems: "center", gap: 5,
                  }}>
                    <span>{isOpen ? "▾" : "▸"}</span> Transformation details
                  </button>

                  {isOpen && <StepDetails step={step} />}
                </div>
                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                  <button onClick={() => decide(step.step_id, "approve")} style={{
                    padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
                    background: dec === "approve" ? C.accent : `${C.accent}18`, color: dec === "approve" ? "#000" : C.accent,
                    transition: "all 0.2s"
                  }}>✓ Approve</button>
                  <button onClick={() => decide(step.step_id, "reject")} style={{
                    padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: "pointer", border: "none",
                    background: dec === "reject" ? C.danger : `${C.danger}18`, color: dec === "reject" ? "#fff" : C.danger,
                    transition: "all 0.2s"
                  }}>✕ Reject</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: 16 }}>
        <button onClick={() => setShowOpinion(!showOpinion)} style={{ background: "none", border: "none", color: C.textSub, cursor: "pointer", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", gap: 6, padding: 0 }}>
          <span style={{ fontSize: 16 }}>{showOpinion ? "▾" : "▸"}</span> Add Developer Opinion / Notes
        </button>
        {showOpinion && (
          <textarea
            value={opinion}
            onChange={e => setOpinion(e.target.value)}
            placeholder="Provide additional context, concerns, or specific guidance for the transformation agent..."
            style={{ width: "100%", marginTop: 8, background: C.panel, border: `1px solid ${C.borderAcc}`, borderRadius: 8, padding: "10px 14px", color: C.text, fontSize: 12, resize: "vertical", minHeight: 80, outline: "none", boxSizing: "border-box" }}
          />
        )}
      </div>

      <div style={{ display: "flex", gap: 12, marginTop: 16, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button onClick={onFallback} style={{
          padding: "10px 22px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer",
          background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30`
        }}>
          ← Fallback to Smell Review
        </button>
        {!canProceed && pending > 0 && (
          <div style={{ display: "flex", alignItems: "center", fontSize: 12, color: C.warn }}>
            ⚠ Review all {pending} pending steps to proceed
          </div>
        )}
        <button onClick={submit} disabled={!canProceed} style={{
          padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: canProceed ? "pointer" : "not-allowed",
          background: canProceed ? C.accent : C.border, color: canProceed ? "#000" : C.textMuted, border: "none",
          boxShadow: canProceed ? `0 0 20px ${C.accentGlow}` : "none", transition: "all 0.2s"
        }}>
          Forward to Transformation Agent → ({approved} approved)
        </button>
      </div>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

/**
 * The parameters block is what the Safe Transformation Agent actually executes,
 * so it is shown verbatim: a placeholder like "<parent>" here is the developer's
 * only warning that a step will not transform cleanly.
 */
function StepDetails({ step }) {
  const params = Object.entries(step.parameters || {});
  const prediction = step.prediction;

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.borderAcc}`, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
          Transformation parameters · smell {step.smell_id}
        </div>
        {params.length === 0 ? (
          <div style={{ fontSize: 11, color: C.textMuted }}>No parameters — the agent inferred nothing to configure.</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {params.map(([key, value]) => (
              <span key={key} style={{
                fontSize: 11, fontFamily: "monospace", padding: "3px 8px", borderRadius: 6,
                background: C.bg, border: `1px solid ${C.border}`, color: C.textSub,
              }}>
                <span style={{ color: C.textMuted }}>{key}:</span>{" "}
                {typeof value === "object" ? JSON.stringify(value) : String(value)}
              </span>
            ))}
          </div>
        )}
      </div>

      {prediction && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Predicted effect
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 11, color: C.textSub }}>
            <span>complexity after: <b style={{ color: C.text }}>{prediction.predicted_complexity_after}</b></span>
            <span>coupling: <b style={{ color: prediction.coupling_change <= 0 ? C.accent : C.warn }}>{prediction.coupling_change}</b></span>
            <span>cohesion: <b style={{ color: prediction.cohesion_change >= 0 ? C.accent : C.warn }}>{prediction.cohesion_change}</b></span>
            <span>maintainability: <b style={{ color: C.accent }}>+{prediction.maintainability_improvement}</b></span>
            <span>risk: <b style={{ color: riskColor(step.risk) }}>{prediction.risk_score}</b></span>
          </div>
        </div>
      )}

      {step.alternatives?.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Alternatives considered
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {step.alternatives.map(alt => (
              <span key={alt.name} style={{ fontSize: 11, color: C.textMuted }}>
                {alt.name}
                {typeof alt.score === "number" ? ` (${alt.score.toFixed(2)})` : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const ORIGIN_LABEL = {
  rdp: "Live plan from the Refactoring Planning Agent",
  workflow: "Plan from the current DIWO workflow",
  sample: "Sample plan (bundled data — RDP agent unavailable)",
};

function SourceBanner({ origin, meta, loading, error, canRefresh, onRefresh }) {
  const color = origin === "rdp" ? C.accent : origin === "workflow" ? C.info : C.warn;

  const details = [];
  if (origin === "rdp") {
    details.push(RDP_BASE);
    if (meta?.filesPlanned) details.push(`${meta.filesPlanned} file(s)`);
    if (meta?.smellsSubmitted) details.push(`${meta.smellsSubmitted} smell(s) submitted`);
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
      marginBottom: 16, padding: "10px 16px", borderRadius: 10,
      background: `${color}0a`, border: `1px solid ${color}40`, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, boxShadow: `0 0 8px ${color}`, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{ORIGIN_LABEL[origin] || "Refactoring plan"}</div>
          {details.length > 0 && (
            <div style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis" }}>
              {details.join(" · ")}
            </div>
          )}
          {error && (
            <div style={{ fontSize: 11, color: C.danger, marginTop: 2 }}>
              RDP agent unavailable: {error.message}
            </div>
          )}
        </div>
      </div>
      {canRefresh && (
        <button
          onClick={onRefresh}
          disabled={loading}
          style={{
            padding: "6px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: C.panel, color: loading ? C.textMuted : C.textSub,
            border: `1px solid ${C.border}`, cursor: loading ? "wait" : "pointer", flexShrink: 0,
          }}
        >
          {loading ? "Planning…" : "↻ Regenerate plan"}
        </button>
      )}
    </div>
  );
}

function LoadingState() {
  return (
    <Card style={{ textAlign: "center", padding: "48px 24px" }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 8 }}>
        Generating the refactoring plan…
      </div>
      <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 20 }}>
        POST {RDP_GENERATE_URL} · interpreting smells, scoring candidates (MCDA), sequencing steps
      </div>
      <div style={{ height: 4, borderRadius: 4, background: C.border, overflow: "hidden", maxWidth: 320, margin: "0 auto" }}>
        <div style={{ height: "100%", width: "40%", background: C.gradient, animation: "diwoSlide 1.1s ease-in-out infinite" }} />
      </div>
      <style>{`@keyframes diwoSlide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }`}</style>
    </Card>
  );
}

function ErrorState({ error, canRetry, onRetry, onUseSample, onFallback }) {
  const noSmells = error?.status === 422;

  const hint = noSmells
    ? "Every selected file came through without smells. Go back to Code Smell Review and approve files that still have detected smells."
    : "Start the Refactoring Planning agent before running the DIWO workflow:  cd agents/rdp_agent && python app.py  (serves http://localhost:5000)";

  return (
    <Card style={{ padding: "32px 28px", borderColor: `${C.danger}50` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <Badge label={noSmells ? "NOTHING TO PLAN" : "RDP UNAVAILABLE"} color={C.danger} />
        <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>
          Could not load the refactoring plan
        </span>
      </div>

      <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.6, marginBottom: 12 }}>
        {error?.message || "No plan was supplied by the workflow and the RDP agent was not contacted."}
      </div>

      <div style={{
        fontSize: 11, color: C.textMuted, fontFamily: "monospace", lineHeight: 1.6,
        background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 14px", marginBottom: 18,
      }}>
        {hint}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {canRetry && (
          <button onClick={onRetry} style={{
            padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 700,
            background: C.accent, color: "#000", border: "none", cursor: "pointer",
            boxShadow: `0 0 16px ${C.accentGlow}`,
          }}>
            ↻ Retry
          </button>
        )}
        <button onClick={onFallback} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30`, cursor: "pointer",
        }}>
          ← Back to Smell Review
        </button>
        <button onClick={onUseSample} style={{
          padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: C.panel, color: C.textSub, border: `1px solid ${C.border}`, cursor: "pointer",
        }}>
          Continue with sample data
        </button>
      </div>
    </Card>
  );
}
