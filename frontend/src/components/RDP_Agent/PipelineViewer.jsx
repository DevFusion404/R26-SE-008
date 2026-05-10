/**
 * PipelineViewer.jsx — Pipeline Trace Visualization
 * Displays the RDP Agent's 7-step pipeline with detailed trace information
 */

import { useState } from 'react';

const getSeverityColor = (severity) => {
  if (severity === 'high' || severity === 'critical') return 'rgba(239,68,68,1)';
  if (severity === 'medium') return 'rgba(234,179,8,1)';
  return 'rgba(34,197,94,1)';
};

const getSeverityIcon = (severity) => {
  if (severity === 'high' || severity === 'critical') return '🔴';
  if (severity === 'medium') return '🟡';
  return '🟢';
};

export default function PipelineViewer({ trace, id = 'pipeline-section' }) {
  const [activeStep, setActiveStep] = useState('input');

  const steps = [
    { id: 'input', label: 'Input', icon: '📥' },
    { id: 'interpreter', label: 'Problem Interpreter', icon: '🔍' },
    { id: 'candidates', label: 'Candidate Generation', icon: '💡' },
    { id: 'impact', label: 'Impact Prediction', icon: '📊' },
    { id: 'ml', label: 'ML Scoring', icon: '🤖' },
    { id: 'mcda', label: 'MCDA Selection', icon: '⚖️' },
    { id: 'dependencies', label: 'Dependencies', icon: '🔗' },
    { id: 'plan', label: 'Plan Generation', icon: '📋' },
  ];

  /**
   * Render input summary panel
   */
  function renderInputPanel() {
    const input = trace.input_summary || {};
    const smells = input.smells || [];
    
    const severityDist = smells.reduce((acc, s) => {
      acc[s.severity] = (acc[s.severity] || 0) + 1;
      return acc;
    }, {});
    
    const smellTypes = [...new Set(smells.map(s => s.type))];

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Target</div>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>{input.target || 'unknown'}</div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Code Smells Detected</div>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>{input.total_smells || 0}</div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Severity Breakdown</div>
            <div style={{ fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
              🔴 {severityDist.high || severityDist.critical || 0} high<br />
              🟡 {severityDist.medium || 0} medium<br />
              🟢 {severityDist.low || 0} low
            </div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Smell Types Found</div>
            <div style={{ fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
              {smellTypes.length > 0 ? smellTypes.join(', ') : 'N/A'}
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
    const data = trace.problem_interpretation || {};
    
    // Handle both old array format and new object format for backwards compatibility
    if (Array.isArray(data)) {
      if (data.length === 0) {
        return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>No interpretation data available</p></div>;
      }
      // Old format handling
      return (
        <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Evaluates preconditions for each candidate refactoring to determine applicability.</p>
          
          {data.map((item, idx) => (
            <div key={idx} style={{ padding: '12px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)', marginBottom: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>
                  {item.smell_type || item.smell_id || `Smell ${idx}`}
                </div>
                {item.severity && (
                  <span style={{ fontSize: '12px', fontWeight: '600', color: getSeverityColor(item.severity) }}>
                    [{getSeverityIcon(item.severity)} {item.severity.toUpperCase()}]
                  </span>
                )}
              </div>
              
              {item.preconditions_evaluated && item.preconditions_evaluated.length > 0 ? (
                <div>
                  {item.preconditions_evaluated.map((pe, i) => (
                    <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px', fontSize: '12px' }}>
                      <span style={{ color: pe.passed ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '700', fontSize: '14px' }}>
                        {pe.passed ? '✓' : '✗'}
                      </span>
                      <span style={{ fontWeight: '600', color: 'var(--text-secondary)' }}>{pe.candidate}</span>
                      {pe.preconditions && pe.preconditions.length > 0 && (
                        <span style={{ opacity: 0.7, fontSize: '11px' }}>({pe.preconditions.join(', ')})</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No preconditions evaluated</div>
              )}
            </div>
          ))}
        </div>
      );
    }

    // New object format
    if (!data.target) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>No interpretation data available</p></div>;
    }

    const severitySummary = data.severity_summary || {};
    const typeSummary = data.type_summary || {};
    const problemGroups = data.problem_groups || [];
    const criticalIssues = data.critical_issues || [];
    const recommendations = data.recommendations || [];

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Analyzes code smells: classifies severity, groups by type, builds risk factors, and generates recommendations.</p>
        
        {/* Summary Overview */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px' }}>
          <div style={{ padding: '12px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Severity Breakdown</div>
            <div style={{ fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
              🔴 {severitySummary.critical || 0} critical<br />
              🔴 {severitySummary.high || 0} high<br />
              🟡 {severitySummary.medium || 0} medium<br />
              🟢 {severitySummary.low || 0} low
            </div>
          </div>
          
          <div style={{ padding: '12px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Smell Types</div>
            <div style={{ fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
              {Object.entries(typeSummary).map(([type, count]) => (
                <div key={type}>{type}: <span style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{count}</span></div>
              ))}
            </div>
          </div>
        </div>

        {/* Problem Groups */}
        {problemGroups.length > 0 && (
          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase' }}>Problem Groups</div>
            {problemGroups.map((group, idx) => (
              <div key={idx} style={{ padding: '12px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)', marginBottom: '10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text-primary)' }}>{group.description}</div>
                  <span style={{ fontSize: '11px', fontWeight: '600', color: getSeverityColor(group.severity_level) }}>
                    [{getSeverityIcon(group.severity_level)} {group.severity_level.toUpperCase()}]
                  </span>
                </div>
                {Object.keys(group.collective_metrics).length > 0 && (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                    {Object.entries(group.collective_metrics).map(([metric, value]) => (
                      <div key={metric} style={{ opacity: 0.8 }}><span style={{ fontWeight: '600' }}>{metric}:</span> {typeof value === 'number' ? value.toFixed(1) : value}</div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Critical Issues */}
        {criticalIssues.length > 0 && (
          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '12px', fontWeight: '600', color: 'rgba(239,68,68,1)', marginBottom: '10px', textTransform: 'uppercase' }}>🔴 Critical Issues</div>
            {criticalIssues.map((issue, idx) => (
              <div key={idx} style={{ padding: '10px', background: 'rgba(239,68,68,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(239,68,68,0.3)', marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                {issue}
              </div>
            ))}
          </div>
        )}

        {/* Recommendations */}
        {recommendations.length > 0 && (
          <div>
            <div style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase' }}>Recommendations</div>
            {recommendations.map((rec, idx) => (
              <div key={idx} style={{ padding: '10px', background: 'rgba(34,197,94,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(34,197,94,0.3)', marginBottom: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                💡 {rec}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  /**
   * Render candidate generation panel
   */
  function renderCandidatesPanel() {
    const data = trace.candidate_generation || [];
    
    if (data.length === 0) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No candidate generation data available</p></div>;
    }

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Retrieves candidates, filters by preconditions, scores using weighted formula, and selects best.</p>
        
        {data.map((item, idx) => (
          <div key={idx} style={{ padding: '14px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)', marginBottom: '16px' }}>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '12px' }}>
              {item.smell_type || item.smell_id || `Smell ${idx}`}
            </div>
            
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              Candidates available: <span style={{ fontWeight: '700', color: 'var(--text-primary)', fontSize: '15px' }}>{item.candidates_from_catalog || 0}</span>
            </div>
            
            {item.candidates && item.candidates.length > 0 && (
              <div style={{ marginTop: '12px' }}>
                {item.candidates.map((c, ci) => (
                  <div key={ci} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px', fontSize: '13px', lineHeight: '1.5' }}>
                    <span style={{ color: c.preconditions_met ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '700', fontSize: '16px', minWidth: '20px' }}>
                      {c.preconditions_met ? '✓' : '✗'}
                    </span>
                    <span style={{ color: 'var(--text-secondary)', flex: 1, fontSize: '13px' }}>{c.name}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }

  /**
   * Render impact prediction panel
   */
  function renderImpactPanel() {
    const data = trace.impact_prediction || [];
    
    if (data.length === 0) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No impact predictions available</p></div>;
    }

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Predicts expected improvement in quality metrics for each candidate before scoring.</p>
        
        {data.map((item, idx) => (
          <div key={idx} style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '12px' }}>
              {item.smell_type || item.smell_id || `Smell ${idx}`}
            </div>
            
            {!item.predictions || item.predictions.length === 0 ? (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No viable candidates</div>
            ) : (
              <div style={{ overflowX: 'auto', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
                <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                  <colgroup>
                    <col style={{ width: '25%' }} />
                    <col style={{ width: '15%' }} />
                    <col style={{ width: '15%' }} />
                    <col style={{ width: '15%' }} />
                    <col style={{ width: '15%' }} />
                    <col style={{ width: '15%' }} />
                  </colgroup>
                  <thead>
                    <tr style={{ background: 'rgba(139,92,246,0.08)', borderBottom: '2px solid rgba(139,92,246,0.2)' }}>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'left', color: 'var(--text-primary)', fontSize: '13px' }}>Refactoring</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Complexity</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Coupling Δ</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Cohesion Δ</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Maintainability</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.predictions.map((pred, pi) => (
                      <tr key={pi} style={{ borderBottom: '1px solid rgba(139,92,246,0.08)', background: pi % 2 === 0 ? 'transparent' : 'rgba(139,92,246,0.02)' }}>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', fontWeight: '500', textAlign: 'left' }}>{pred.refactoring}</td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center' }}>{pred.predicted_complexity_after?.toFixed(1) ?? 'N/A'}</td>
                        <td style={{ padding: '12px', color: (pred.coupling_change || 0) <= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '600', fontSize: '12px', textAlign: 'center' }}>
                          {(pred.coupling_change || 0) > 0 ? '+' : ''}{pred.coupling_change?.toFixed(2) ?? 'N/A'}
                        </td>
                        <td style={{ padding: '12px', color: (pred.cohesion_change || 0) >= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)', fontWeight: '600', fontSize: '12px', textAlign: 'center' }}>
                          {(pred.cohesion_change || 0) > 0 ? '+' : ''}{pred.cohesion_change?.toFixed(2) ?? 'N/A'}
                        </td>
                        <td style={{ padding: '12px', color: 'rgba(34,197,94,1)', fontWeight: '600', fontSize: '12px', textAlign: 'center' }}>
                          +{(pred.maintainability_improvement * 100)?.toFixed(1) ?? 'N/A'}%
                        </td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center' }}>{(pred.risk_score * 100)?.toFixed(1) ?? 'N/A'}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }

  /**
   * Render ML scoring panel
   */
  function renderMLPanel() {
    const data = trace.ml_prediction || [];
    
    if (data.length === 0) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No ML predictions available</p></div>;
    }

    const allUnavailable = data.every(ml => !ml.ml_available);

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>CodeBERT embeddings score candidates based on contextual suitability and quality improvement.</p>
        
        {allUnavailable && (
          <div style={{ fontSize: '12px', color: 'rgba(234,179,8,1)', marginBottom: '16px', padding: '10px', background: 'rgba(234,179,8,0.08)', borderRadius: '6px' }}>
            ⚠️ ML scorer not available — using heuristic scoring
          </div>
        )}
        
        {data.map((item, idx) => (
          <div key={idx} style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>
                {item.smell_type || item.smell_id || `Smell ${idx}`}
              </div>
              <span style={{ fontSize: '12px', fontWeight: '600', color: item.ml_available ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)' }}>
                {item.ml_available ? '● ML Active' : '● Heuristic'}
              </span>
            </div>
            
            {!item.ml_available && (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic', marginBottom: '12px' }}>ML unavailable for this smell</div>
            )}
            
            {!item.predictions || item.predictions.length === 0 ? (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No predictions available</div>
            ) : (
              <div style={{ overflowX: 'auto', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
                <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                  <colgroup>
                    <col style={{ width: '35%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '21%' }} />
                  </colgroup>
                  <thead>
                    <tr style={{ background: 'rgba(139,92,246,0.08)', borderBottom: '2px solid rgba(139,92,246,0.2)' }}>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'left', color: 'var(--text-primary)', fontSize: '13px' }}>Refactoring</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Suitability</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Quality</th>
                      <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.predictions.map((pred, pi) => (
                      <tr key={pi} style={{ borderBottom: '1px solid rgba(139,92,246,0.08)', background: pi % 2 === 0 ? 'transparent' : 'rgba(139,92,246,0.02)' }}>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', fontWeight: '500', textAlign: 'left' }}>{pred.refactoring}</td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center' }}>{pred.contextual_suitability != null ? (pred.contextual_suitability * 100).toFixed(1) + '%' : 'N/A'}</td>
                        <td style={{ padding: '12px', fontWeight: '600', color: 'rgba(34,197,94,1)', fontSize: '12px', textAlign: 'center' }}>
                          +{pred.quality_improvement != null ? (pred.quality_improvement * 100).toFixed(1) + '%' : 'N/A'}
                        </td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center' }}>{pred.confidence != null ? (pred.confidence * 100).toFixed(1) + '%' : 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }

  /**
   * Render MCDA (Multi-Criteria Decision Making) selection panel
   */
  function renderMCDAPanel() {
    const mcda_data = trace.mcda_selection || [];
    const candidate_data = trace.candidate_generation || [];
    
    if (mcda_data.length === 0) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No MCDA analysis available</p></div>;
    }

    // Build map of smell_id → selected refactoring from candidate_generation
    const selectedMap = {};
    candidate_data.forEach((cand, idx) => {
      const smell_id = cand.smell_id;
      if (cand.selected) {
        selectedMap[smell_id] = cand.selected;
      }
    });

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Multi-Criteria Decision Making evaluates candidates using weighted criteria: Quality (40%) + Complexity (25%) + Risk (20%) + Dependency (15%).</p>
        
        {mcda_data.map((item, idx) => {
          const selected = selectedMap[item.smell_id];
          return (
            <div key={idx} style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                <div style={{ fontSize: '16px', fontWeight: '700', color: 'rgba(139,92,246,1)', background: 'rgba(139,92,246,0.1)', width: '32px', height: '32px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {idx + 1}
                </div>
                <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>
                  {item.smell_type || item.smell_id || `Smell ${idx}`}
                </div>
              </div>
              
              {!item.predictions || item.predictions.length === 0 ? (
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>No MCDA predictions available</div>
              ) : (
                <>
                  <div style={{ overflowX: 'auto', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)', marginBottom: '12px' }}>
                    <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                      <colgroup>
                        <col style={{ width: '18%' }} />
                        <col style={{ width: '13%' }} />
                        <col style={{ width: '13%' }} />
                        <col style={{ width: '13%' }} />
                        <col style={{ width: '13%' }} />
                        <col style={{ width: '15%' }} />
                      </colgroup>
                      <thead>
                        <tr style={{ background: 'rgba(139,92,246,0.08)', borderBottom: '2px solid rgba(139,92,246,0.2)' }}>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'left', color: 'var(--text-primary)', fontSize: '13px' }}>Refactoring</th>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Quality</th>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Complexity</th>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Risk</th>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Dependency</th>
                          <th style={{ padding: '12px', fontWeight: '700', textAlign: 'center', color: 'var(--text-primary)', fontSize: '13px' }}>Final Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {item.predictions.map((pred, pi) => {
                          const isSelected = selected === pred.refactoring;
                          return (
                            <tr key={pi} style={{ 
                              borderBottom: '1px solid rgba(139,92,246,0.08)',
                              background: isSelected ? 'rgba(34,197,94,0.12)' : (pi % 2 === 0 ? 'transparent' : 'rgba(139,92,246,0.02)'),
                              borderLeft: isSelected ? '4px solid rgba(34,197,94,1)' : 'none',
                              paddingLeft: isSelected ? '8px' : '0px'
                            }}>
                              <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', fontWeight: isSelected ? '600' : '500', textAlign: 'left', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                {isSelected && <span style={{ fontSize: '14px', fontWeight: '700', color: 'rgba(34,197,94,1)' }}>✓</span>}
                                {pred.refactoring}
                              </td>
                              <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center', fontWeight: isSelected ? '600' : '400' }}>{(pred.quality || 0).toFixed(2)}</td>
                              <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center', fontWeight: isSelected ? '600' : '400' }}>{(pred.complexity || 0).toFixed(2)}</td>
                              <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center', fontWeight: isSelected ? '600' : '400' }}>{(pred.risk || 0).toFixed(2)}</td>
                              <td style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '12px', textAlign: 'center', fontWeight: isSelected ? '600' : '400' }}>{(pred.dependency || 0).toFixed(2)}</td>
                              <td style={{ padding: '12px', fontWeight: isSelected ? '700' : '700', color: isSelected ? 'rgba(34,197,94,1)' : 'rgba(139,92,246,1)', fontSize: '12px', textAlign: 'center' }}>{(pred.final_score || 0).toFixed(3)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  /**
   * Render dependency analysis panel
   */
  function renderDependenciesPanel() {
    const dep = trace.dependency_analysis || {};

    if (!dep || Object.keys(dep).length === 0) {
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No dependency analysis data available</p></div>;
    }

    const hasRules = Object.keys(dep.rules_applied || {}).length > 0;
    const orderBefore = dep.order_before || [];
    const orderAfter = dep.order_after || [];

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Analyzes refactoring dependencies and determines safe execution order.</p>
        
        {hasRules && (
          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '12px' }}>Rules Applied</div>
            {Object.entries(dep.rules_applied).map(([rule, count]) => (
              <div key={rule} style={{ fontSize: '12px', padding: '10px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)', marginBottom: '6px' }}>
                <strong style={{ color: 'var(--text-secondary)' }}>{rule}:</strong> <span style={{ color: 'var(--text-secondary)' }}>{count} application(s)</span>
              </div>
            ))}
          </div>
        )}
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
          <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>Execution Order</div>
          <span style={{ fontSize: '12px', fontWeight: '600', color: dep.reordered ? 'rgba(234,179,8,1)' : 'rgba(34,197,94,1)' }}>
            {dep.reordered ? '🔄 REORDERED' : '✓ UNCHANGED'}
          </span>
        </div>
        
        {orderBefore.length > 0 || orderAfter.length > 0 ? (
          <div style={{ display: 'flex', alignItems: 'stretch', gap: '20px', justifyContent: 'space-between' }}>
            {/* Before Column */}
            <div style={{ flex: 1 }}>
              <h5 style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Before (by severity)</h5>
              <ol style={{ fontSize: '13px', paddingLeft: '24px', lineHeight: '1.8', color: 'var(--text-secondary)', listStyle: 'decimal' }}>
                {orderBefore.map((ref, idx) => (
                  <li key={idx} style={{ marginBottom: '8px', paddingLeft: '4px' }}>
                    <strong style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{typeof ref === 'string' ? ref : ref.refactoring || 'Refactoring'}</strong>
                    {(typeof ref === 'object' && ref.smell_id) && <span style={{ fontSize: '12px', color: 'var(--text-secondary)', marginLeft: '6px' }}>({ref.smell_id})</span>}
                  </li>
                ))}
              </ol>
            </div>
            
            {/* Arrow/Connector */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px', color: 'rgba(139,92,246,0.4)', userSelect: 'none' }}>
              →
            </div>
            
            {/* After Column */}
            <div style={{ flex: 1 }}>
              <h5 style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>After (dependency-resolved)</h5>
              <ol style={{ fontSize: '13px', paddingLeft: '24px', lineHeight: '1.8', color: 'var(--text-secondary)', listStyle: 'decimal' }}>
                {orderAfter.map((ref, idx) => (
                  <li key={idx} style={{ marginBottom: '8px', paddingLeft: '4px' }}>
                    <strong style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{typeof ref === 'string' ? ref : ref.refactoring || 'Refactoring'}</strong>
                    {(typeof ref === 'object' && ref.smell_id) && <span style={{ fontSize: '12px', color: 'var(--text-secondary)', marginLeft: '6px' }}>({ref.smell_id})</span>}
                  </li>
                ))}
              </ol>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', fontStyle: 'italic', padding: '16px', textAlign: 'center' }}>No ordering data available</div>
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
      return <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}><p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No plan generation data available</p></div>;
    }

    return (
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.5' }}>Assembles the final machine-executable refactoring plan with explanations and parameters.</p>
        
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' }}>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Total Steps Generated</div>
            <div style={{ fontSize: '18px', fontWeight: '700', color: 'var(--text-primary)' }}>{pg.total_steps || 0}</div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Smells Addressed</div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>{pg.smells_addressed ?? pg.total_steps ?? 0}</div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Smells Skipped</div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: (pg.smells_skipped || 0) > 0 ? 'rgba(234,179,8,1)' : 'rgba(34,197,94,1)' }}>
              {pg.smells_skipped || 0}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: '600' }}>Plan ID</div>
            <div style={{ fontSize: '12px', fontWeight: '700', fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{pg.plan_id || '—'}</div>
          </div>
        </div>
        
        {pg.summary && (
          <div style={{ padding: '12px', background: 'rgba(139,92,246,0.05)', borderRadius: '6px', borderLeft: '4px solid rgba(139,92,246,0.3)', fontSize: '13px', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
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
      case 'mcda':
        return renderMCDAPanel();
      case 'dependencies':
        return renderDependenciesPanel();
      case 'plan':
        return renderPlanPanel();
      default:
        return null;
    }
  }

  return (
    <section style={{ background: 'linear-gradient(135deg, rgba(139,92,246,0.02), rgba(168,85,247,0.02))', border: '1px solid rgba(139,92,246,0.15)', borderRadius: 'var(--r-md)', overflow: 'hidden', marginTop: 'var(--sp-4)' }} id={id}>
      <h2 style={{ fontSize: '16px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '8px', padding: 'var(--sp-5)', paddingBottom: '0' }}>Pipeline Trace</h2>
      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '20px', padding: 'var(--sp-5)', paddingTop: '0' }}>
        See how each module processed your quality report step by step.
      </p>

      {/* Step Navigation */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '24px', overflowX: 'auto', paddingBottom: '8px', paddingLeft: 'var(--sp-5)', paddingRight: 'var(--sp-5)', flexWrap: 'nowrap' }}>
        {steps.map((step, idx) => (
          <div key={step.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
            <button
              style={{
                padding: '8px 12px',
                borderRadius: 'var(--r-sm)',
                border: activeStep === step.id ? '1px solid rgba(139,92,246,0.3)' : '1px solid rgba(139,92,246,0.1)',
                background: activeStep === step.id ? 'rgba(139,92,246,0.15)' : 'transparent',
                color: activeStep === step.id ? 'rgba(139,92,246,1)' : 'rgba(139,92,246,0.5)',
                fontSize: '11px',
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                whiteSpace: 'nowrap'
              }}
              onClick={() => setActiveStep(step.id)}
              onMouseEnter={(e) => {
                if (activeStep !== step.id) {
                  e.target.style.background = 'rgba(139,92,246,0.08)';
                  e.target.style.borderColor = 'rgba(139,92,246,0.2)';
                }
              }}
              onMouseLeave={(e) => {
                if (activeStep !== step.id) {
                  e.target.style.background = 'transparent';
                  e.target.style.borderColor = 'rgba(139,92,246,0.1)';
                }
              }}
            >
              <span>{step.icon}</span>
              {step.label}
            </button>
            {idx < steps.length - 1 && (
              <div style={{ width: '16px', height: '1px', background: 'rgba(139,92,246,0.2)', flexShrink: 0 }} />
            )}
          </div>
        ))}
      </div>

      {/* Active Panel Content */}
      <div style={{ padding: '12px', background: 'rgba(139,92,246,0.02)', borderRadius: '6px', border: '1px solid rgba(139,92,246,0.1)', marginLeft: 'var(--sp-5)', marginRight: 'var(--sp-5)', marginBottom: 'var(--sp-5)' }}>
        {getActivePanel()}
      </div>
    </section>
  );
}

