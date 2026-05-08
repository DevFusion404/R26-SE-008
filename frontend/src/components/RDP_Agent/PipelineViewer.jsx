/**
 * PipelineViewer.jsx — Pipeline Trace Visualization
 * Displays the RDP Agent's 7-step pipeline with detailed trace information
 */

import { useState } from 'react';

export default function PipelineViewer({ trace, id = 'pipeline-section' }) {
  const [activeStep, setActiveStep] = useState('input');

  const steps = [
    { id: 'input', label: 'Input', icon: '📥' },
    { id: 'interpreter', label: 'Problem Interpreter', icon: '🔍' },
    { id: 'candidates', label: 'Candidate Generation', icon: '💡' },
    { id: 'impact', label: 'Impact Prediction', icon: '📊' },
    { id: 'ml', label: 'ML Scoring', icon: '🤖' },
    { id: 'dependencies', label: 'Dependencies', icon: '🔗' },
    { id: 'plan', label: 'Plan Generation', icon: '📋' },
  ];

  /**
   * Render input summary panel
   */
  function renderInputPanel() {
    const input = trace.input_summary || {};

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Quality Report Summary</h4>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '16px',
            fontSize: '13px',
          }}
        >
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Target
            </div>
            <div style={{ fontWeight: '600' }}>{input.target || 'unknown'}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Code Smells
            </div>
            <div style={{ fontWeight: '600' }}>{input.total_smells || 0}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Severity Breakdown
            </div>
            <div style={{ fontSize: '12px' }}>
              {(input.smells || []).reduce((acc, s) => {
                acc[s.severity] = (acc[s.severity] || 0) + 1;
                return acc;
              }, {}) && (() => {
                const dist = (input.smells || []).reduce((acc, s) => { acc[s.severity] = (acc[s.severity] || 0) + 1; return acc; }, {});
                return <>{`🔴 ${dist.high||dist.critical||0} high, 🟡 ${dist.medium||0} med, 🟢 ${dist.low||0} low`}</>;
              })()}
            </div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Smell Types
            </div>
            <div style={{ fontSize: '12px' }}>
              {[...new Set((input.smells || []).map(s => s.type))].join(', ') || 'N/A'}
            </div>
          </div>
        </div>
      </div>
    );
  }

  /**
   * Render problem interpretation panel
   */
  function renderInterpreterPanel() {
    if (!trace.problem_interpretation || trace.problem_interpretation.length === 0) {
      return (
        <div className="trace-section">
          <h4 className="trace-subtitle">Problem Interpretation</h4>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            Evaluates preconditions for each candidate refactoring to determine applicability.
          </p>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No interpretation data available
          </div>
        </div>
      );
    }

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Problem Interpretation</h4>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Evaluates preconditions for each candidate refactoring to determine applicability.
        </p>
        {trace.problem_interpretation.map((pi, idx) => (
          <div
            key={idx}
            style={{
              padding: '12px',
              background: 'rgba(139,92,246,0.05)',
              borderRadius: '6px',
              borderLeft: '3px solid rgba(139,92,246,0.3)',
              marginBottom: '8px',
            }}
          >
            <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '6px' }}>
              {pi.smell_type || pi.smell_id || `Smell ${idx}`}
              {pi.severity && (
                <span style={{ marginLeft: '8px', fontSize: '10px', fontWeight: '500',
                  color: pi.severity === 'high' || pi.severity === 'critical' ? 'rgba(239,68,68,1)' : pi.severity === 'medium' ? 'rgba(234,179,8,1)' : 'rgba(34,197,94,1)' }}>
                  [{pi.severity}]
                </span>
              )}
            </div>
            {(pi.preconditions_evaluated || []).map((pe, i) => (
              <div key={i} style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '3px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                <span style={{ color: pe.passed ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '700' }}>
                  {pe.passed ? '✓' : '✗'}
                </span>
                <span style={{ fontWeight: '500' }}>{pe.candidate}</span>
                {pe.preconditions && pe.preconditions.length > 0 && (
                  <span style={{ opacity: 0.7 }}>— {pe.preconditions.join(', ')}</span>
                )}
              </div>
            ))}
            {(!pi.preconditions_evaluated || pi.preconditions_evaluated.length === 0) && (
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No precondition data</div>
            )}
          </div>
        ))}
      </div>
    );
  }

  /**
   * Render candidate generation panel
   */
  function renderCandidatesPanel() {
    if (!trace.candidate_generation || trace.candidate_generation.length === 0) {
      return (
        <div className="trace-section">
          <h4 className="trace-subtitle">Candidate Generation</h4>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            Retrieves candidates from the catalog and filters by preconditions. Scoring happens in the next stages.
          </p>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No candidate generation data available
          </div>
        </div>
      );
    }

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Candidate Generation</h4>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Retrieves candidates from the catalog and filters by preconditions. Scoring happens in the next stages.
        </p>
        <div style={{ display: 'grid', gap: '12px' }}>
          {trace.candidate_generation.map((cg, idx) => (
            <div
              key={idx}
              style={{
                padding: '12px',
                background: 'rgba(139,92,246,0.05)',
                borderRadius: '6px',
                borderLeft: '3px solid rgba(139,92,246,0.3)',
              }}
            >
              <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '8px' }}>
                {cg.smell_type || cg.smell_id || `Smell ${idx}`}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                Candidates from catalog: <span style={{ fontWeight: '600' }}>{cg.candidates_from_catalog || 0}</span>
              </div>

              {/* Candidates with precondition status only (no scores) */}
              {(cg.candidates || []).length > 0 && (
                <div style={{ marginBottom: '8px' }}>
                  <div style={{ fontSize: '11px', fontWeight: '500', color: 'rgba(139,92,246,0.8)', marginBottom: '4px' }}>
                    Viable Candidates:
                  </div>
                  <div style={{ display: 'grid', gap: '3px' }}>
                    {cg.candidates.map((c, ci) => (
                      <div key={ci} style={{ fontSize: '10px', color: 'var(--text-secondary)',
                        display: 'flex', gap: '6px', alignItems: 'center' }}>
                        <span style={{ color: c.preconditions_met ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '700' }}>
                          {c.preconditions_met ? '✓' : '✗'}
                        </span>
                        <span>{c.name}</span>
                        {c.complexity && <span style={{ fontSize: '9px', opacity: 0.6 }}>(complexity: {c.complexity})</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Ranking & Selection (shown separately) */}
              {cg.selected && (
                <div style={{
                  marginTop: '12px',
                  paddingTop: '12px',
                  borderTop: '1px solid rgba(139,92,246,0.2)',
                }}>
                  <div style={{ fontSize: '11px', fontWeight: '500', color: 'rgba(139,92,246,0.8)', marginBottom: '4px' }}>
                    Selected After Ranking:
                  </div>
                  <div style={{ fontSize: '12px', fontWeight: '600', color: 'rgba(34,197,94,1)' }}>
                    ⭐ {cg.selected}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                    Score: <span style={{ fontWeight: '600', color: 'rgba(139,92,246,1)' }}>
                      {cg.selected_score ?? 'N/A'}
                    </span>
                  </div>
                  {cg.scoring_method && (
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                      Scoring Method: <span style={{
                        fontWeight: '600',
                        color: cg.scoring_method === 'ml_enhanced' ? 'rgba(168,85,247,1)' :
                          cg.scoring_method === 'impact_aware' ? 'rgba(34,197,94,1)' : 'rgba(107,114,128,1)',
                        padding: '2px 6px',
                        background: cg.scoring_method === 'ml_enhanced' ? 'rgba(168,85,247,0.1)' :
                          cg.scoring_method === 'impact_aware' ? 'rgba(34,197,94,0.1)' : 'rgba(107,114,128,0.1)',
                        borderRadius: '3px',
                        display: 'inline-block',
                      }}>
                        {cg.scoring_method === 'ml_enhanced' && '🤖 ML Enhanced'}
                        {cg.scoring_method === 'impact_aware' && '📊 Impact-Aware'}
                        {cg.scoring_method === 'base' && '📋 Base Heuristic'}
                      </span>
                    </div>
                  )}
                </div>
              )}

              {!cg.selected && (
                <div style={{ fontSize: '11px', color: 'rgba(239,68,68,1)', fontStyle: 'italic', marginTop: '8px' }}>
                  No candidate selected for this smell
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  /**
   * Render impact prediction panel
   */
  function renderImpactPanel() {
    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Impact Prediction</h4>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Predicts expected improvement in quality metrics for each candidate before scoring.
        </p>
        {trace.impact_prediction && trace.impact_prediction.length > 0 ? (
          <div style={{ display: 'grid', gap: '12px' }}>
            {trace.impact_prediction.map((ip, idx) => (
              <div key={idx} style={{ padding: '12px', background: 'rgba(139,92,246,0.05)',
                borderRadius: '6px', borderLeft: '3px solid rgba(139,92,246,0.3)' }}>
                <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '8px' }}>
                  {ip.smell_type || ip.smell_id || `Smell ${idx}`}
                </div>
                {(ip.predictions || []).length === 0 && (
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No viable candidates</div>
                )}
                {(ip.predictions || []).map((pred, pi) => (
                  <div key={pi} style={{ marginBottom: '8px', paddingLeft: '8px',
                    borderLeft: '2px solid rgba(139,92,246,0.2)' }}>
                    <div style={{ fontSize: '11px', fontWeight: '600', marginBottom: '4px', color: 'rgba(139,92,246,0.9)' }}>
                      {pred.refactoring}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      <div>Complexity after: <span style={{ fontWeight: '500' }}>{pred.predicted_complexity_after?.toFixed(1) ?? 'N/A'}</span></div>
                      <div>Coupling Δ: <span style={{ fontWeight: '500', color: (pred.coupling_change || 0) <= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)' }}>{pred.coupling_change > 0 ? '+' : ''}{pred.coupling_change?.toFixed(2) ?? 'N/A'}</span></div>
                      <div>Cohesion Δ: <span style={{ fontWeight: '500', color: (pred.cohesion_change || 0) >= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)' }}>{pred.cohesion_change > 0 ? '+' : ''}{pred.cohesion_change?.toFixed(2) ?? 'N/A'}</span></div>
                      <div>Maintainability: <span style={{ fontWeight: '500', color: 'rgba(34,197,94,1)' }}>+{(pred.maintainability_improvement * 100)?.toFixed(1) ?? 'N/A'}%</span></div>
                      <div>Risk: <span style={{ fontWeight: '500' }}>{(pred.risk_score * 100)?.toFixed(1) ?? 'N/A'}%</span></div>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>No impact predictions available</div>
        )}
      </div>
    );
  }

  /**
   * Render ML scoring panel
   */
  function renderMLPanel() {
    if (!trace.ml_prediction || trace.ml_prediction.length === 0) {
      return (
        <div className="trace-section">
          <h4 className="trace-subtitle">ML Scoring (CodeBERT)</h4>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            CodeBERT embeddings score candidates based on contextual suitability and quality improvement.
          </p>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No ML predictions available
          </div>
        </div>
      );
    }

    const allUnavailable = trace.ml_prediction.every(ml => !ml.ml_available);

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">ML Scoring (CodeBERT)</h4>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          CodeBERT embeddings score candidates based on contextual suitability and quality improvement.
        </p>
        {allUnavailable && (
          <div style={{ fontSize: '12px', color: 'rgba(234,179,8,1)', marginBottom: '12px',
            padding: '8px 12px', background: 'rgba(234,179,8,0.08)', borderRadius: '6px' }}>
            ⚠️ ML scorer not available — using heuristic scoring
          </div>
        )}
        <div style={{ display: 'grid', gap: '12px' }}>
          {trace.ml_prediction.map((ml, idx) => (
            <div key={idx} style={{ padding: '12px', background: 'rgba(139,92,246,0.05)',
              borderRadius: '6px', borderLeft: '3px solid rgba(139,92,246,0.3)' }}>
              <div style={{ fontSize: '12px', fontWeight: '600', marginBottom: '8px' }}>
                {ml.smell_type || ml.smell_id || `Smell ${idx}`}
                <span style={{ marginLeft: '8px', fontSize: '10px', fontWeight: '400',
                  color: ml.ml_available ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)' }}>
                  {ml.ml_available ? '● ML Active' : '● Heuristic'}
                </span>
              </div>
              {!ml.ml_available && (
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>ML unavailable for this smell</div>
              )}
              {(ml.predictions || []).map((pred, pi) => (
                <div key={pi} style={{ marginBottom: '6px', paddingLeft: '8px',
                  borderLeft: '2px solid rgba(139,92,246,0.2)' }}>
                  <div style={{ fontSize: '11px', fontWeight: '600', color: 'rgba(139,92,246,0.9)', marginBottom: '3px' }}>
                    {pred.refactoring}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                    <div>Suitability: <span style={{ fontWeight: '600' }}>{pred.contextual_suitability != null ? (pred.contextual_suitability * 100).toFixed(1) + '%' : 'N/A'}</span></div>
                    <div>Quality: <span style={{ fontWeight: '600', color: 'rgba(34,197,94,1)' }}>+{pred.quality_improvement != null ? (pred.quality_improvement * 100).toFixed(1) + '%' : 'N/A'}</span></div>
                    <div>Confidence: <span style={{ fontWeight: '600' }}>{pred.confidence != null ? (pred.confidence * 100).toFixed(1) + '%' : 'N/A'}</span></div>
                  </div>
                </div>
              ))}
              {(!ml.predictions || ml.predictions.length === 0) && ml.ml_available && (
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No ML predictions for this smell</div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  /**
   * Render dependency analysis panel
   */
  function renderDependenciesPanel() {
    const dep = trace.dependency_analysis || {};

    if (!dep || Object.keys(dep).length === 0) {
      return (
        <div className="trace-section">
          <h4 className="trace-subtitle">Dependency Analysis</h4>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            Analyzes refactoring dependencies and determines safe execution order.
          </p>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No dependency analysis data available
          </div>
        </div>
      );
    }

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Dependency Analysis</h4>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Analyzes refactoring dependencies and determines safe execution order.
        </p>

        {Object.keys(dep.rules_applied || {}).length > 0 && (
          <>
            <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '8px' }}>Rules Applied:</h5>
            <div style={{ display: 'grid', gap: '8px', marginBottom: '16px' }}>
              {Object.entries(dep.rules_applied).map(([rule, count]) => (
                <div
                  key={rule}
                  style={{
                    fontSize: '11px',
                    padding: '8px',
                    background: 'rgba(139,92,246,0.05)',
                    borderRadius: '4px',
                  }}
                >
                  {rule}: {count} application(s)
                </div>
              ))}
            </div>
          </>
        )}

        <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '8px' }}>
          Execution Order{' '}
          <span
            style={{
              fontSize: '11px',
              fontWeight: '500',
              color: dep.reordered ? 'var(--color-warn)' : 'var(--color-ok)',
            }}
          >
            {dep.reordered ? '🔄 REORDERED' : '✓ UNCHANGED'}
          </span>
        </h5>
        {(dep.refactoring_order || dep.order_after || dep.order_before) && (
          <ol style={{ fontSize: '11px', paddingLeft: '16px' }}>
            {(dep.refactoring_order || dep.order_after || dep.order_before || []).map((ref, idx) => (
              <li key={idx}>{typeof ref === 'string' ? ref : ref.refactoring || JSON.stringify(ref)}</li>
            ))}
          </ol>
        )}
        {!dep.refactoring_order && !dep.order_after && !dep.order_before && (
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No ordering data available
          </div>
        )}
      </div>
    );
  }

  /**
   * Render plan generation panel
   */
  function renderPlanPanel() {
    const pg = trace.plan_generation || {};

    if (!pg || Object.keys(pg).length === 0) {
      return (
        <div className="trace-section">
          <h4 className="trace-subtitle">Plan Generation</h4>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            No plan generation data available
          </div>
        </div>
      );
    }

    return (
      <div className="trace-section">
        <h4 className="trace-subtitle">Plan Generation</h4>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '16px',
            fontSize: '12px',
          }}
        >
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Total Steps Generated
            </div>
            <div style={{ fontWeight: '600', fontSize: '18px' }}>{pg.total_steps || 0}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Smells Addressed
            </div>
            <div style={{ fontWeight: '500' }}>{pg.smells_addressed ?? pg.total_steps ?? 0}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Smells Skipped
            </div>
            <div style={{ fontWeight: '500', color: (pg.smells_skipped || 0) > 0 ? 'rgba(234,179,8,1)' : 'rgba(34,197,94,1)' }}>
              {pg.smells_skipped || 0}
            </div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '11px', marginBottom: '4px' }}>
              Plan ID
            </div>
            <div style={{ fontWeight: '500', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>{pg.plan_id || '—'}</div>
          </div>
        </div>
        {pg.summary && (
          <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
            {pg.summary}
          </div>
        )}
      </div>
    );
  }

  /**
   * Get active panel renderer
   */
  function getActivePanel() {
    switch (activeStep) {
      case 'input':
        return renderInputPanel();
      case 'interpreter':
        return renderInterpreterPanel();
      case 'candidates':
        return renderCandidatesPanel();
      case 'impact':
        return renderImpactPanel();
      case 'ml':
        return renderMLPanel();
      case 'dependencies':
        return renderDependenciesPanel();
      case 'plan':
        return renderPlanPanel();
      default:
        return null;
    }
  }

  return (
    <section
      className="card"
      id={id}
      style={{
        marginTop: '24px',
        background: 'linear-gradient(135deg, rgba(139,92,246,0.02) 0%, rgba(168,85,247,0.02) 100%)',
        borderColor: 'rgba(139,92,246,0.15)',
      }}
    >
      <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '8px' }}>Pipeline Trace</h2>
      <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
        See how each module processed your quality report step by step.
      </p>

      {/* Pipeline Steps */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: '24px',
          overflowX: 'auto',
          paddingBottom: '8px',
        }}
      >
        {steps.map((step, idx) => (
          <div key={step.id} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              onClick={() => setActiveStep(step.id)}
              style={{
                padding: '8px 12px',
                background: activeStep === step.id ? 'rgba(139,92,246,0.15)' : 'transparent',
                border: `1px solid ${activeStep === step.id ? 'rgba(139,92,246,0.3)' : 'rgba(139,92,246,0.1)'}`,
                borderRadius: '6px',
                fontSize: '12px',
                fontWeight: activeStep === step.id ? '600' : '500',
                color: activeStep === step.id ? 'rgba(139,92,246,1)' : 'var(--text-secondary)',
                cursor: 'pointer',
                transition: 'all 0.2s',
                whiteSpace: 'nowrap',
              }}
            >
              <span style={{ marginRight: '4px' }}>{step.icon}</span>
              {step.label}
            </button>
            {idx < steps.length - 1 && (
              <div
                style={{
                  width: '16px',
                  height: '1px',
                  background: 'rgba(139,92,246,0.2)',
                }}
              />
            )}
          </div>
        ))}
      </div>

      {/* Active Panel */}
      <div
        style={{
          padding: '16px',
          background: 'rgba(139,92,246,0.02)',
          borderRadius: '6px',
          border: '1px solid rgba(139,92,246,0.1)',
        }}
      >
        {getActivePanel()}
      </div>
    </section>
  );
}
