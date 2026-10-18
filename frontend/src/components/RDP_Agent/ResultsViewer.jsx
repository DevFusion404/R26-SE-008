/**
 * ResultsViewer.jsx — Refactoring Plan Display
 * Displays the generated refactoring plan with categorization, filtering, search, and export options.
 * Optimized for high-contrast light mode, scaled typography, and clear visual hierarchy for viva presentations.
 */

import { useState, useMemo } from 'react';
import './rdpAgent.css';

// ── C language & Security helpers ──────────────────────────────────────────
const C_EXTENSIONS = ['.c', '.h'];
const UNSAFE_SMELL_REFACTORINGS = new Set(['Replace Unsafe Function']);
const GLOBAL_VAR_REFACTORINGS   = new Set(['Encapsulate Variable']);

function isCFile(step) {
  const file = step?.target?.file || '';
  return C_EXTENSIONS.some(ext => file.toLowerCase().endsWith(ext))
    || (step?.target?.language || '').toLowerCase() === 'c';
}

function LangBadge({ step }) {
  if (!isCFile(step)) return null;
  return <span className="rdp-tag-c">C</span>;
}

function SecurityBadge({ step }) {
  if (!UNSAFE_SMELL_REFACTORINGS.has(step.refactoring)) return null;
  return <span className="rdp-tag-security">⚠ Security</span>;
}

export default function ResultsViewer({ plan, stats, onDownload, onCopy }) {
  const [showRawJson, setShowRawJson]                 = useState(false);
  const [searchQuery, setSearchQuery]                 = useState('');
  const [groupBy, setGroupBy]                         = useState('none'); // 'none' | 'refactoring' | 'file' | 'risk'
  const [selectedRefactoring, setSelectedRefactoring] = useState('ALL');
  const [selectedFile, setSelectedFile]               = useState('ALL');
  const [collapsedGroups, setCollapsedGroups]         = useState({});

  const allSteps = plan?.steps || [];

  // Extract unique refactoring types & target files for dropdown filters
  const refactoringTypes = useMemo(() => {
    return ['ALL', ...new Set(allSteps.map(s => s.refactoring).filter(Boolean))];
  }, [allSteps]);

  const fileList = useMemo(() => {
    const files = new Set();
    allSteps.forEach(s => {
      const f = s.target?.file || s.target?.class || s.target?.method;
      if (f) files.add(f);
    });
    return ['ALL', ...files];
  }, [allSteps]);

  // Filter steps based on active search & dropdown filters
  const filteredSteps = useMemo(() => {
    return allSteps.filter(step => {
      const q = searchQuery.toLowerCase().trim();
      if (q) {
        const refName   = (step.refactoring || '').toLowerCase();
        const expl      = (step.explanation || '').toLowerCase();
        const targetStr = JSON.stringify(step.target || {}).toLowerCase();
        const paramStr  = JSON.stringify(step.parameters || {}).toLowerCase();
        if (!refName.includes(q) && !expl.includes(q) && !targetStr.includes(q) && !paramStr.includes(q)) {
          return false;
        }
      }

      if (selectedRefactoring !== 'ALL' && step.refactoring !== selectedRefactoring) {
        return false;
      }

      if (selectedFile !== 'ALL') {
        const f = step.target?.file || step.target?.class || step.target?.method || '';
        if (f !== selectedFile) return false;
      }

      return true;
    });
  }, [allSteps, searchQuery, selectedRefactoring, selectedFile]);

  // Group steps when groupBy is set
  const groupedSteps = useMemo(() => {
    if (groupBy === 'none') return null;

    const groups = {};
    filteredSteps.forEach(step => {
      let key = 'Other Refactorings';
      if (groupBy === 'refactoring') {
        key = step.refactoring || 'Uncategorized';
      } else if (groupBy === 'file') {
        key = step.target?.file || step.target?.class || 'Module Level';
      } else if (groupBy === 'risk') {
        if (UNSAFE_SMELL_REFACTORINGS.has(step.refactoring)) {
          key = '🔴 Security Vulnerability Fixes';
        } else if (step.refactoring === 'Extract Class' || step.refactoring === 'Extract Subclass') {
          key = '🟠 High Impact Structural Changes';
        } else if (step.refactoring === 'Remove Dead Code') {
          key = '🟢 Cleanup & Dead Code Removal';
        } else {
          key = '🟡 Standard Refactorings';
        }
      }

      if (!groups[key]) groups[key] = [];
      groups[key].push(step);
    });

    return groups;
  }, [filteredSteps, groupBy]);

  const isFiltered = searchQuery || selectedRefactoring !== 'ALL' || selectedFile !== 'ALL';

  function resetFilters() {
    setSearchQuery('');
    setGroupBy('none');
    setSelectedRefactoring('ALL');
    setSelectedFile('ALL');
  }

  function toggleGroup(groupKey) {
    setCollapsedGroups(prev => ({
      ...prev,
      [groupKey]: !prev[groupKey]
    }));
  }

  // Render an individual step card cleanly with strong visual hierarchy
  function renderStepCard(step, originalIndex) {
    const stepNumber = originalIndex !== undefined ? originalIndex + 1 : (step.step_id || '•');
    return (
      <div key={step.step_id || originalIndex} className="rdp-step-card">
        {/* Step number badge */}
        <div className="rdp-step-badge">
          {stepNumber}
        </div>

        {/* Step details */}
        <div className="rdp-step-main">
          {/* Header: Name + Badges */}
          <div className="rdp-step-header">
            <span className="rdp-step-name">
              {step.refactoring || 'Unnamed Refactoring'}
            </span>
            <LangBadge step={step} />
            <SecurityBadge step={step} />
          </div>

          {/* Explanation */}
          <div className="rdp-step-explanation">
            {step.explanation || 'No explanation provided'}
          </div>

          {/* Target & Smell ID metadata box */}
          <div className="rdp-meta-box">
            <div className="rdp-meta-item">
              <span className="rdp-meta-label">Target:</span>
              <span className="rdp-meta-target-val">
                {step.target?.class || step.target?.method
                  ? `${step.target.class || ''}${step.target.method ? `.${step.target.method}` : ''}`
                  : (step.target?.file || '(module scope)')}
                {step.target?.lines && step.target.lines.length > 0 ? ` [Lines ${step.target.lines.join('–')}]` : ''}
              </span>
            </div>

            {step.target?.file && (
              <div className="rdp-meta-item">
                <span className="rdp-meta-label">File:</span>
                <span className="rdp-meta-file-val">{step.target.file}</span>
              </div>
            )}

            <div className="rdp-meta-item">
              <span className="rdp-meta-label">Smell ID:</span>
              <span className="rdp-meta-smell-val">{step.smell_id || '—'}</span>
            </div>
          </div>

          {/* Parameters display as styled chips */}
          {step.parameters && Object.keys(step.parameters).length > 0 && (
            <div className="rdp-params-row">
              <span className="rdp-params-label">Parameters:</span>
              {Object.entries(step.parameters).map(([k, v]) => (
                <span key={k} className="rdp-param-chip">
                  <strong>{k}:</strong> {Array.isArray(v) ? v.join('–') : String(v)}
                </span>
              ))}
            </div>
          )}

          {/* C Unsafe Function security hint */}
          {UNSAFE_SMELL_REFACTORINGS.has(step.refactoring) && step.parameters?.safe_alternative && (
            <div className="rdp-alert-callout rdp-alert-security">
              <strong>Security Fix: </strong>
              Replace unsafe buffer call with <code>{step.parameters.safe_alternative}()</code>
            </div>
          )}

          {/* C Global Variable encapsulation plan */}
          {GLOBAL_VAR_REFACTORINGS.has(step.refactoring) && step.parameters?.variable_name && (
            <div className="rdp-alert-callout rdp-alert-encapsulate">
              <strong>Encapsulation Plan: </strong>
              Create <code>{step.parameters.getter_name}()</code> and <code>{step.parameters.setter_name}()</code> for global variable <code>{step.parameters.variable_name}</code>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!plan) return null;

  return (
    <section className="rdp-plan-card">
      {/* Header */}
      <div className="rdp-plan-header">
        <div>
          <h2 className="rdp-plan-title">Refactoring Plan</h2>
          <div className="rdp-plan-target-subtitle">
            Target Scope: <span className="rdp-plan-target-highlight">{plan.target || 'Project Source'}</span>
          </div>
        </div>
        <div className="rdp-header-actions">
          <button
            id="copy-btn"
            onClick={onCopy}
            title="Copy plan to clipboard"
            className="rdp-btn-action rdp-btn-copy"
          >
            📋 Copy JSON
          </button>
          <button
            id="download-btn"
            onClick={onDownload}
            title="Download plan as JSON file"
            className="rdp-btn-action rdp-btn-download"
          >
            ⬇ Download Plan
          </button>
        </div>
      </div>

      {/* Summary Metrics Grid */}
      {stats && (
        <div className="rdp-metrics-grid">
          <div className="rdp-metric-item">
            <div className="rdp-metric-label">Total Steps</div>
            <div className="rdp-metric-val" style={{ color: 'var(--metric-green)' }}>
              {stats.totalSteps || allSteps.length || 0}
            </div>
          </div>
          <div className="rdp-metric-item">
            <div className="rdp-metric-label">Refactoring Types</div>
            <div className="rdp-metric-val" style={{ color: 'var(--metric-purple)' }}>
              {Object.keys(stats.refactoringTypes || {}).length}
            </div>
          </div>
          <div className="rdp-metric-item">
            <div className="rdp-metric-label">Classes / Modules</div>
            <div className="rdp-metric-val" style={{ color: 'var(--metric-blue)' }}>
              {stats.uniqueClasses || 0}
            </div>
          </div>
          <div className="rdp-metric-item">
            <div className="rdp-metric-label">Methods Affected</div>
            <div className="rdp-metric-val" style={{ color: 'var(--metric-orange)' }}>
              {stats.uniqueMethods || 0}
            </div>
          </div>
        </div>
      )}

      {/* Plan Summary Banner */}
      {plan.summary && (
        <div className="rdp-summary-banner">
          {plan.summary}
        </div>
      )}

      {/* Categorization, Search & Filter Toolbar */}
      <div className="rdp-filter-toolbar">
        <div className="rdp-filter-toolbar-header">
          <div className="rdp-filter-title">
            <span>🛠 Categorization &amp; Filtering</span>
            <span className="rdp-filter-count">
              (Showing {filteredSteps.length} of {allSteps.length} steps)
            </span>
          </div>

          {isFiltered && (
            <button onClick={resetFilters} className="rdp-btn-reset-filter">
              ✕ Reset Filters
            </button>
          )}
        </div>

        {/* Search input + Dropdowns row */}
        <div className="rdp-filter-controls">
          {/* Search box */}
          <input
            type="text"
            className="rdp-input"
            placeholder="🔍 Search steps, files, methods..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />

          {/* Group By selector */}
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value)}
            className="rdp-select"
          >
            <option value="none">Group By: None (Sequential)</option>
            <option value="refactoring">Group By: Refactoring Type</option>
            <option value="file">Group By: Target File / Class</option>
            <option value="risk">Group By: Risk &amp; Impact Category</option>
          </select>

          {/* Refactoring Type filter */}
          <select
            value={selectedRefactoring}
            onChange={(e) => setSelectedRefactoring(e.target.value)}
            className="rdp-select"
          >
            {refactoringTypes.map(type => (
              <option key={type} value={type}>
                {type === 'ALL' ? 'Technique: All Types' : type}
              </option>
            ))}
          </select>

          {/* Target File filter */}
          <select
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
            className="rdp-select"
          >
            {fileList.map(f => (
              <option key={f} value={f}>
                {f === 'ALL' ? 'Target: All Files' : f}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Refactoring Steps List (Grouped or Flat) */}
      <div className="rdp-steps-container">
        {filteredSteps.length === 0 ? (
          <div style={{
            padding: '36px',
            textAlign: 'center',
            background: 'var(--rdp-meta-bg)',
            borderRadius: '10px',
            border: '1px dashed var(--border)',
            color: 'var(--text-secondary)',
          }}>
            <p style={{ fontSize: '15px', fontWeight: 600, marginBottom: '10px' }}>
              🔍 No refactoring steps match your selected filters.
            </p>
            <button onClick={resetFilters} className="rdp-btn-reset-filter" style={{ padding: '7px 16px', fontSize: '13px' }}>
              Reset Filters
            </button>
          </div>
        ) : groupedSteps ? (
          /* GROUPED VIEW */
          <div style={{ display: 'grid', gap: '20px' }}>
            {Object.entries(groupedSteps).map(([groupName, groupSteps]) => {
              const isCollapsed = !!collapsedGroups[groupName];
              return (
                <div key={groupName} className="rdp-group-container">
                  {/* Group Header */}
                  <div onClick={() => toggleGroup(groupName)} className="rdp-group-header">
                    <div className="rdp-group-header-left">
                      <span style={{ fontSize: '13px', color: 'var(--color-rdp)' }}>{isCollapsed ? '▶' : '▼'}</span>
                      <strong className="rdp-group-title">{groupName}</strong>
                    </div>
                    <span className="rdp-group-badge">
                      {groupSteps.length} {groupSteps.length === 1 ? 'step' : 'steps'}
                    </span>
                  </div>

                  {/* Group Step List */}
                  {!isCollapsed && (
                    <div className="rdp-group-body">
                      {groupSteps.map((step) => {
                        const originalIndex = allSteps.indexOf(step);
                        return renderStepCard(step, originalIndex >= 0 ? originalIndex : undefined);
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          /* FLAT SEQUENTIAL LIST VIEW */
          <div style={{ display: 'grid', gap: '14px' }}>
            {filteredSteps.map((step) => {
              const originalIndex = allSteps.indexOf(step);
              return renderStepCard(step, originalIndex >= 0 ? originalIndex : undefined);
            })}
          </div>
        )}
      </div>

      {/* Raw JSON Accordion */}
      <details
        style={{
          borderTop: '1px solid var(--border)',
          paddingTop: '16px',
        }}
      >
        <summary
          onClick={() => setShowRawJson(!showRawJson)}
          style={{
            cursor: 'pointer',
            fontSize: '13px',
            fontWeight: '600',
            color: 'var(--color-rdp)',
            padding: '10px 14px',
            background: 'var(--rdp-pill-bg)',
            borderRadius: '6px',
            userSelect: 'none',
            transition: 'all 0.2s',
          }}
        >
          {showRawJson ? '▼' : '▶'} View Raw Machine-Executable JSON Plan
        </summary>
        {showRawJson && (
          <pre
            id="json-output"
            style={{
              marginTop: '12px',
              padding: '16px',
              background: 'var(--bg-code)',
              borderRadius: '8px',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              overflow: 'auto',
              maxHeight: '400px',
              lineHeight: '1.5',
              color: 'var(--text-code)',
              border: '1px solid var(--border)',
            }}
          >
            {JSON.stringify(plan, null, 2)}
          </pre>
        )}
      </details>
    </section>
  );
}
