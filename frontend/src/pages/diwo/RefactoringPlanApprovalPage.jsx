import { useState } from "react";
import { PLAN_DATA } from "./data/diwoData";
import { C, Card, Badge, Pill, impactColor, riskColor } from "./diwoTheme.jsx";

export default function RefactoringPlanApprovalPage({ onApprove, onFallback, planData }) {
  const [decisions, setDecisions] = useState({});
  const [opinion, setOpinion] = useState("");
  const [showOpinion, setShowOpinion] = useState(false);
  const [filter, setFilter] = useState("all");

  const decide = (id, val) => setDecisions(prev => ({ ...prev, [id]: val }));
  const currentPlan = planData || PLAN_DATA;
  const allApproved = currentPlan.steps.length > 0 && currentPlan.steps.every(step => decisions[step.step_id] === "approve");

  const toggleSelectAll = () => {
    if (allApproved) {
      setDecisions({});
      return;
    }
    setDecisions(Object.fromEntries(currentPlan.steps.map(step => [step.step_id, "approve"])));
  };

  const steps = currentPlan.steps;
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

  return (
    <div>
      <Card style={{ marginBottom: 20 }} glow={C.accentGlow}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>Refactoring Planning Agent Output</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.text, marginBottom: 6 }}>{currentPlan.plan_id}</div>
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
      </Card>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        {["all", "approved", "rejected", "pending", "high", "medium", "low"].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, cursor: "pointer", border: "none", textTransform: "capitalize",
            background: filter === f ? C.accent : C.panel, color: filter === f ? "#000" : C.textMuted, border: `1px solid ${filter === f ? C.accent : C.border}`
          }}>{f}</button>
        ))}
        <button onClick={toggleSelectAll} style={{
          padding: "5px 12px", borderRadius: 20, fontSize: 11, fontWeight: 700, cursor: "pointer", border: `1px solid ${C.accent}`,
          background: `${C.accent}15`, color: C.accent, textTransform: "uppercase"
        }}>
          {allApproved ? "Deselect All" : "Select All"}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 400, overflowY: "auto", paddingRight: 4 }}>
        {filtered.map(step => {
          const dec = decisions[step.step_id];
          const borderColor = dec === "approve" ? C.accent : dec === "reject" ? C.danger : C.border;
          const bgColor = dec === "approve" ? `${C.accent}08` : dec === "reject" ? `${C.danger}08` : C.panel;
          return (
            <div key={step.step_id} style={{ background: bgColor, border: `1px solid ${borderColor}`, borderRadius: 10, padding: "14px 18px", transition: "all 0.2s" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>Step {step.step_id}</span>
                    <Badge label={step.refactoring} color={C.info} />
                    <Pill label={`Impact: ${step.impact || step.expected_impact || "medium"}`} color={impactColor(step.impact || step.expected_impact || "medium")} />
                    <Pill label={`Risk: ${step.risk}`} color={riskColor(step.risk)} />
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 4 }}>
                    {step.target.class}{step.target.method ? `.${step.target.method}` : ""}
                  </div>
                  <div style={{ fontSize: 12, color: C.textSub, lineHeight: 1.5 }}>{step.explanation}</div>
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
        <button onClick={() => canProceed && onApprove({ decisions, opinion })} disabled={!canProceed} style={{
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
