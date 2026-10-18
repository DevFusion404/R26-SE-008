/**
 * PipelineViewer.jsx — Pipeline Trace Visualization
 * Displays the RDP Agent's multi-step pipeline with detailed trace information.
 * Optimized for high-contrast light mode, scaled typography, and clear visual hierarchy for viva presentations.
 */

import { useState } from 'react';
import './rdpAgent.css';

const getSeverityColor = (severity) => {
  if (severity === 'high' || severity === 'critical') return 'var(--color-critical)';
  if (severity === 'medium') return 'var(--color-high)';
  return 'var(--color-ok)';
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
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">
          Summary of input quality report received from Code Understanding Agent.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Target Scope</div>
            <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--rdp-target-color)', fontFamily: 'var(--font-mono)' }}>{input.target || 'unknown'}</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Code Smells Detected</div>
            <div style={{ fontSize: '20px', fontWeight: '800', color: 'var(--text-primary)' }}>{input.total_smells || 0}</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Severity Breakdown</div>
            <div style={{ fontSize: '14px', lineHeight: '1.7', color: 'var(--text-secondary)' }}>
              🔴 <strong>{severityDist.high || severityDist.critical || 0}</strong> high / critical<br />
              🟡 <strong>{severityDist.medium || 0}</strong> medium<br />
              🟢 <strong>{severityDist.low || 0}</strong> low
            </div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Smell Types Found</div>
            <div style={{ fontSize: '14px', lineHeight: '1.7', color: 'var(--text-secondary)' }}>
              {smellTypes.length > 0 ? smellTypes.join(', ') : 'None'}
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
        return <div className="rdp-panel-card"><p className="rdp-panel-desc">No interpretation data available</p></div>;
      }
      return (
        <div className="rdp-panel-card">
          <p className="rdp-panel-desc">Evaluates preconditions for each candidate refactoring to determine applicability.</p>
          {data.map((item, idx) => (
            <div key={idx} style={{ padding: '14px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)', borderLeft: '4px solid var(--color-rdp)', marginBottom: '14px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>
                  {item.smell_type || item.smell_id || `Smell ${idx}`}
                </div>
                {item.severity && (
                  <span style={{ fontSize: '12px', fontWeight: '700', color: getSeverityColor(item.severity) }}>
                    [{getSeverityIcon(item.severity)} {item.severity.toUpperCase()}]
                  </span>
                )}
              </div>
              
              {item.preconditions_evaluated && item.preconditions_evaluated.length > 0 ? (
                <div>
                  {item.preconditions_evaluated.map((pe, i) => (
                    <div key={i} style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '8px', fontSize: '13px' }}>
                      <span style={{ color: pe.passed ? 'var(--rdp-success-text)' : 'var(--rdp-danger-text)', fontWeight: '800', fontSize: '16px' }}>
                        {pe.passed ? '✓' : '✗'}
                      </span>
                      <span style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{pe.candidate}</span>
                      {pe.preconditions && pe.preconditions.length > 0 && (
                        <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>({pe.preconditions.join(', ')})</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No preconditions evaluated</div>
              )}
            </div>
          ))}
        </div>
      );
    }

    // New object format
    if (!data.target) {
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No interpretation data available</p></div>;
    }

    const severitySummary = data.severity_summary || {};
    const typeSummary = data.type_summary || {};
    const problemGroups = data.problem_groups || [];
    const criticalIssues = data.critical_issues || [];
    const recommendations = data.recommendations || [];

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">Analyzes code smells: classifies severity, groups by type, builds risk factors, and generates recommendations.</p>
        
        {/* Summary Overview */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '14px', marginBottom: '20px' }}>
          <div style={{ padding: '14px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)', borderLeft: '4px solid var(--color-rdp)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px', fontWeight: '700', textTransform: 'uppercase' }}>Severity Breakdown</div>
            <div style={{ fontSize: '14px', lineHeight: '1.7', color: 'var(--text-secondary)' }}>
              🔴 <strong>{severitySummary.critical || 0}</strong> critical<br />
              🔴 <strong>{severitySummary.high || 0}</strong> high<br />
              🟡 <strong>{severitySummary.medium || 0}</strong> medium<br />
              🟢 <strong>{severitySummary.low || 0}</strong> low
            </div>
          </div>
          
          <div style={{ padding: '14px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)', borderLeft: '4px solid var(--color-rdp)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px', fontWeight: '700', textTransform: 'uppercase' }}>Smell Types</div>
            <div style={{ fontSize: '14px', lineHeight: '1.7', color: 'var(--text-secondary)' }}>
              {Object.entries(typeSummary).map(([type, count]) => (
                <div key={type}>{type}: <strong style={{ color: 'var(--text-primary)' }}>{count}</strong></div>
              ))}
            </div>
          </div>
        </div>

        {/* Problem Groups */}
        {problemGroups.length > 0 && (
          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.4px' }}>Problem Groups</div>
            {problemGroups.map((group, idx) => (
              <div key={idx} style={{ padding: '14px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)', borderLeft: '4px solid var(--color-rdp)', marginBottom: '10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>{group.description}</div>
                  <span style={{ fontSize: '12px', fontWeight: '700', color: getSeverityColor(group.severity_level) }}>
                    [{getSeverityIcon(group.severity_level)} {group.severity_level.toUpperCase()}]
                  </span>
                </div>
                {Object.keys(group.collective_metrics).length > 0 && (
                  <div style={{ fontSize: '13px', color: 'var(--text-secondary)', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '8px', marginTop: '6px' }}>
                    {Object.entries(group.collective_metrics).map(([metric, value]) => (
                      <div key={metric}><span style={{ fontWeight: '600', color: 'var(--text-muted)' }}>{metric}:</span> <strong>{typeof value === 'number' ? value.toFixed(1) : value}</strong></div>
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
            <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--color-critical)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.4px' }}>🔴 Critical Issues</div>
            {criticalIssues.map((issue, idx) => (
              <div key={idx} style={{ padding: '12px 14px', background: 'rgba(239,68,68,0.08)', borderRadius: '8px', border: '1px solid rgba(239,68,68,0.3)', borderLeft: '4px solid var(--color-critical)', marginBottom: '8px', fontSize: '13px', color: 'var(--text-primary)', lineHeight: '1.6' }}>
                {issue}
              </div>
            ))}
          </div>
        )}

        {/* Recommendations */}
        {recommendations.length > 0 && (
          <div>
            <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.4px' }}>Recommendations</div>
            {recommendations.map((rec, idx) => (
              <div key={idx} style={{ padding: '12px 14px', background: 'rgba(34,197,94,0.08)', borderRadius: '8px', border: '1px solid rgba(34,197,94,0.3)', borderLeft: '4px solid var(--color-ok)', marginBottom: '8px', fontSize: '13px', color: 'var(--text-primary)', lineHeight: '1.6' }}>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No candidate generation data available</p></div>;
    }

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">Retrieves candidates, filters by preconditions, scores using weighted formula, and selects best.</p>
        
        {data.map((item, idx) => (
          <div key={idx} style={{ padding: '16px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)', borderLeft: '4px solid var(--color-rdp)', marginBottom: '16px' }}>
            <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '8px' }}>
              {item.smell_type || item.smell_id || `Smell ${idx}`}
            </div>
            
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              Candidates available: <strong style={{ color: 'var(--text-primary)', fontSize: '15px' }}>{item.candidates_from_catalog || 0}</strong>
            </div>
            
            {item.candidates && item.candidates.length > 0 && (
              <div style={{ marginTop: '10px' }}>
                {item.candidates.map((c, ci) => (
                  <div key={ci} style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '8px', fontSize: '14px', lineHeight: '1.5' }}>
                    <span style={{ color: c.preconditions_met ? 'var(--rdp-success-text)' : 'var(--rdp-danger-text)', fontWeight: '800', fontSize: '16px', minWidth: '20px' }}>
                      {c.preconditions_met ? '✓' : '✗'}
                    </span>
                    <span style={{ color: 'var(--text-primary)', fontWeight: '500', flex: 1 }}>{c.name}</span>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No impact predictions available</p></div>;
    }

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">Predicts expected improvement in quality metrics for each candidate before scoring.</p>
        
        {data.map((item, idx) => (
          <div key={idx} style={{ marginBottom: '22px' }}>
            <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '10px' }}>
              {item.smell_type || item.smell_id || `Smell ${idx}`}
            </div>
            
            {!item.predictions || item.predictions.length === 0 ? (
              <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No viable candidates</div>
            ) : (
              <div className="rdp-table-wrap">
                <table className="rdp-trace-table">
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left', width: '28%' }}>Refactoring</th>
                      <th style={{ textAlign: 'center', width: '14%' }}>Complexity</th>
                      <th style={{ textAlign: 'center', width: '14%' }}>Coupling Δ</th>
                      <th style={{ textAlign: 'center', width: '14%' }}>Cohesion Δ</th>
                      <th style={{ textAlign: 'center', width: '15%' }}>Maintainability</th>
                      <th style={{ textAlign: 'center', width: '15%' }}>Risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.predictions.map((pred, pi) => (
                      <tr key={pi}>
                        <td style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{pred.refactoring}</td>
                        <td style={{ textAlign: 'center' }}>{pred.predicted_complexity_after?.toFixed(1) ?? 'N/A'}</td>
                        <td style={{ textAlign: 'center', color: (pred.coupling_change || 0) <= 0 ? 'var(--rdp-success-text)' : 'var(--rdp-danger-text)', fontWeight: '700' }}>
                          {(pred.coupling_change || 0) > 0 ? '+' : ''}{pred.coupling_change?.toFixed(2) ?? 'N/A'}
                        </td>
                        <td style={{ textAlign: 'center', color: (pred.cohesion_change || 0) >= 0 ? 'var(--rdp-success-text)' : 'var(--rdp-danger-text)', fontWeight: '700' }}>
                          {(pred.cohesion_change || 0) > 0 ? '+' : ''}{pred.cohesion_change?.toFixed(2) ?? 'N/A'}
                        </td>
                        <td style={{ textAlign: 'center', color: 'var(--rdp-success-text)', fontWeight: '700' }}>
                          +{(pred.maintainability_improvement * 100)?.toFixed(1) ?? 'N/A'}%
                        </td>
                        <td style={{ textAlign: 'center' }}>{(pred.risk_score * 100)?.toFixed(1) ?? 'N/A'}%</td>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No ML predictions available</p></div>;
    }

    const allUnavailable = data.every(ml => !ml.ml_available);

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">CodeBERT embeddings score candidates based on contextual suitability and quality improvement.</p>
        
        {allUnavailable && (
          <div style={{ fontSize: '13px', color: 'var(--color-medium)', marginBottom: '16px', padding: '12px 14px', background: 'rgba(234,179,8,0.1)', border: '1px solid rgba(234,179,8,0.3)', borderRadius: '8px', fontWeight: '600' }}>
            ⚠️ ML scorer not available — using heuristic scoring
          </div>
        )}
        
        {data.map((item, idx) => (
          <div key={idx} style={{ marginBottom: '22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>
                {item.smell_type || item.smell_id || `Smell ${idx}`}
              </div>
              <span style={{ fontSize: '12px', fontWeight: '700', padding: '3px 10px', borderRadius: '99px', background: item.ml_available ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)', border: `1px solid ${item.ml_available ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)'}`, color: item.ml_available ? 'var(--rdp-success-text)' : 'var(--rdp-danger-text)' }}>
                {item.ml_available ? '● ML Active' : '● Heuristic'}
              </span>
            </div>
            
            {!item.ml_available && (
              <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic', marginBottom: '10px' }}>ML unavailable for this smell</div>
            )}
            
            {!item.predictions || item.predictions.length === 0 ? (
              <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No predictions available</div>
            ) : (
              <div className="rdp-table-wrap">
                <table className="rdp-trace-table">
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left', width: '38%' }}>Refactoring</th>
                      <th style={{ textAlign: 'center', width: '20%' }}>Suitability</th>
                      <th style={{ textAlign: 'center', width: '20%' }}>Quality Gain</th>
                      <th style={{ textAlign: 'center', width: '22%' }}>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.predictions.map((pred, pi) => (
                      <tr key={pi}>
                        <td style={{ fontWeight: '600', color: 'var(--text-primary)' }}>{pred.refactoring}</td>
                        <td style={{ textAlign: 'center' }}>{pred.contextual_suitability != null ? (pred.contextual_suitability * 100).toFixed(1) + '%' : 'N/A'}</td>
                        <td style={{ textAlign: 'center', fontWeight: '700', color: 'var(--rdp-success-text)' }}>
                          +{pred.quality_improvement != null ? (pred.quality_improvement * 100).toFixed(1) + '%' : 'N/A'}
                        </td>
                        <td style={{ textAlign: 'center' }}>{pred.confidence != null ? (pred.confidence * 100).toFixed(1) + '%' : 'N/A'}</td>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No MCDA analysis available</p></div>;
    }

    // Build map of smell_id → selected refactoring from candidate_generation
    const selectedMap = {};
    candidate_data.forEach((cand) => {
      const smell_id = cand.smell_id;
      if (cand.selected) {
        selectedMap[smell_id] = cand.selected;
      }
    });

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">
          Multi-Criteria Decision Making evaluates candidates using Quality, Complexity, Risk, and Dependency.
          When ML scoring is available, Final Score includes the confidence-weighted ML adjustment.
        </p>
        
        {mcda_data.map((item, idx) => {
          const selected = selectedMap[item.smell_id];
          return (
            <div key={idx} style={{ marginBottom: '24px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                <div style={{
                  fontSize: '14px',
                  fontWeight: '700',
                  color: 'var(--rdp-badge-text)',
                  background: 'var(--rdp-badge-bg)',
                  border: '1px solid var(--rdp-badge-border)',
                  width: '30px',
                  height: '30px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center'
                }}>
                  {idx + 1}
                </div>
                <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>
                  {item.smell_type || item.smell_id || `Smell ${idx}`}
                </div>
              </div>
              
              {!item.predictions || item.predictions.length === 0 ? (
                <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No MCDA predictions available</div>
              ) : (
                <div className="rdp-table-wrap">
                  <table className="rdp-trace-table">
                    <thead>
                      <tr>
                        <th style={{ textAlign: 'left', width: '26%' }}>Refactoring</th>
                        <th style={{ textAlign: 'center', width: '13%' }}>Quality</th>
                        <th style={{ textAlign: 'center', width: '13%' }}>Complexity</th>
                        <th style={{ textAlign: 'center', width: '13%' }}>Risk</th>
                        <th style={{ textAlign: 'center', width: '13%' }}>Dependency</th>
                        <th style={{ textAlign: 'center', width: '22%' }}>Final Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {item.predictions.map((pred, pi) => {
                        const isSelected = selected === pred.refactoring;
                        return (
                          <tr key={pi} className={isSelected ? 'rdp-mcda-winner' : ''}>
                            <td style={{ textAlign: 'left' }}>
                              {isSelected && <span className="rdp-mcda-winner-check">✓</span>}
                              <span>{pred.refactoring}</span>
                            </td>
                            <td style={{ textAlign: 'center' }}>{(pred.quality || 0).toFixed(2)}</td>
                            <td style={{ textAlign: 'center' }}>{(pred.complexity || 0).toFixed(2)}</td>
                            <td style={{ textAlign: 'center' }}>{(pred.risk || 0).toFixed(2)}</td>
                            <td style={{ textAlign: 'center' }}>{(pred.dependency || 0).toFixed(2)}</td>
                            <td style={{ textAlign: 'center' }}>
                              <div style={{ fontSize: '15px', fontWeight: '800', color: isSelected ? 'var(--rdp-winner-text)' : 'var(--text-primary)' }}>
                                {(pred.final_score || 0).toFixed(3)}
                              </div>
                              {pred.scoring_method && (
                                <div className="rdp-scoring-method-chip">
                                  {pred.scoring_method}
                                </div>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No dependency analysis data available</p></div>;
    }

    const hasRules = Object.keys(dep.rules_applied || {}).length > 0;
    const orderBefore = dep.order_before || [];
    const orderAfter = dep.order_after || [];

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">Analyzes refactoring dependencies and determines safe execution order.</p>
        
        {hasRules && (
          <div style={{ marginBottom: '22px' }}>
            <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '10px' }}>Rules Applied</div>
            {Object.entries(dep.rules_applied).map(([rule, count]) => (
              <div key={rule} style={{ fontSize: '13px', padding: '10px 14px', background: 'var(--bg-card)', borderRadius: '6px', border: '1px solid var(--border)', marginBottom: '6px' }}>
                <strong style={{ color: 'var(--text-primary)' }}>{rule}:</strong> <span style={{ color: 'var(--text-secondary)' }}>{count} application(s)</span>
              </div>
            ))}
          </div>
        )}
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
          <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>Execution Order</div>
          <span style={{ fontSize: '12px', fontWeight: '700', padding: '3px 10px', borderRadius: '99px', background: dep.reordered ? 'rgba(234,179,8,0.12)' : 'rgba(34,197,94,0.12)', border: `1px solid ${dep.reordered ? 'rgba(234,179,8,0.35)' : 'rgba(34,197,94,0.35)'}`, color: dep.reordered ? 'var(--color-medium)' : 'var(--rdp-success-text)' }}>
            {dep.reordered ? '🔄 REORDERED' : '✓ UNCHANGED'}
          </span>
        </div>
        
        {orderBefore.length > 0 || orderAfter.length > 0 ? (
          <div className="rdp-exec-grid">
            {/* Before Column */}
            <div className="rdp-exec-column">
              <h5 className="rdp-exec-col-title">Before (by severity)</h5>
              <ol className="rdp-exec-list">
                {orderBefore.map((ref, idx) => (
                  <li key={idx}>
                    <strong>{typeof ref === 'string' ? ref : ref.refactoring || 'Refactoring'}</strong>
                    {(typeof ref === 'object' && ref.smell_id) && <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: '6px' }}>({ref.smell_id})</span>}
                  </li>
                ))}
              </ol>
            </div>
            
            {/* Arrow Connector */}
            <div className="rdp-exec-arrow">
              →
            </div>
            
            {/* After Column */}
            <div className="rdp-exec-column">
              <h5 className="rdp-exec-col-title">After (dependency-resolved)</h5>
              <ol className="rdp-exec-list">
                {orderAfter.map((ref, idx) => (
                  <li key={idx}>
                    <strong style={{ color: 'var(--color-rdp)' }}>{typeof ref === 'string' ? ref : ref.refactoring || 'Refactoring'}</strong>
                    {(typeof ref === 'object' && ref.smell_id) && <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: '6px' }}>({ref.smell_id})</span>}
                  </li>
                ))}
              </ol>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', fontStyle: 'italic', padding: '16px', textAlign: 'center' }}>No ordering data available</div>
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
      return <div className="rdp-panel-card"><p className="rdp-panel-desc">No plan generation data available</p></div>;
    }

    return (
      <div className="rdp-panel-card">
        <p className="rdp-panel-desc">Assembles the final machine-executable refactoring plan with explanations and parameters.</p>
        
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', marginBottom: '20px' }}>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Total Steps Generated</div>
            <div style={{ fontSize: '20px', fontWeight: '800', color: 'var(--text-primary)' }}>{pg.total_steps || 0}</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Smells Addressed</div>
            <div style={{ fontSize: '20px', fontWeight: '800', color: 'var(--rdp-success-text)' }}>{pg.smells_addressed ?? pg.total_steps ?? 0}</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Smells Skipped</div>
            <div style={{ fontSize: '20px', fontWeight: '800', color: (pg.smells_skipped || 0) > 0 ? 'var(--color-high)' : 'var(--rdp-success-text)' }}>
              {pg.smells_skipped || 0}
            </div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: '700', textTransform: 'uppercase' }}>Plan ID</div>
            <div style={{ fontSize: '13px', fontWeight: '700', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{pg.plan_id || '—'}</div>
          </div>
        </div>
        
        {pg.summary && (
          <div style={{ padding: '14px 18px', background: 'var(--rdp-pill-bg)', borderRadius: '8px', borderLeft: '4px solid var(--color-rdp)', fontSize: '14px', lineHeight: '1.6', color: 'var(--text-primary)' }}>
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
    <section className="rdp-pipeline-section" id={id}>
      <h2 className="rdp-pipeline-title">Pipeline Trace</h2>
      <p className="rdp-pipeline-subtitle">
        See how each module processed your quality report step by step.
      </p>

      {/* Step Navigation */}
      <div className="rdp-pipeline-nav">
        {steps.map((step, idx) => (
          <div key={step.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
            <button
              className={`rdp-pipeline-tab ${activeStep === step.id ? 'active' : ''}`}
              onClick={() => setActiveStep(step.id)}
            >
              <span>{step.icon}</span>
              <span>{step.label}</span>
            </button>
            {idx < steps.length - 1 && (
              <div className="rdp-pipeline-connector" />
            )}
          </div>
        ))}
      </div>

      {/* Active Panel Content */}
      <div>
        {getActivePanel()}
      </div>
    </section>
  );
}
