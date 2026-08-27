/**
 * ResultsViewer.jsx — Refactoring Plan Display
 * Displays the generated refactoring plan with categorization, filtering, search, and export options.
 */

import { useState, useMemo } from 'react';

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
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: '2px 8px', borderRadius: 99,
      background: 'rgba(249,115,22,0.12)', border: '1px solid rgba(249,115,22,0.3)',
      fontSize: 10, fontWeight: 700, color: '#f97316',
      marginLeft: 6, verticalAlign: 'middle',
    }}>C</span>
  );
}

function SecurityBadge({ step }) {
  if (!UNSAFE_SMELL_REFACTORINGS.has(step.refactoring)) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: '2px 8px', borderRadius: 99,
      background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.30)',
      fontSize: 10, fontWeight: 700, color: '#ef4444',
      marginLeft: 6, verticalAlign: 'middle',
    }}>⚠ Security</span>
  );
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

  // Render an individual step card cleanly
  function renderStepCard(step, originalIndex) {
    const stepNumber = originalIndex !== undefined ? originalIndex + 1 : (step.step_id || '•');
    return (
      <div
        key={step.step_id || originalIndex}
        style={{
          padding: '14px 16px',
          background: 'rgba(139,92,246,0.03)',
          border: '1px solid rgba(139,92,246,0.15)',
          borderRadius: '8px',
          display: 'grid',
          gridTemplateColumns: '36px 1fr',
          gap: '14px',
          alignItems: 'start',
          transition: 'all 0.15s ease-in-out',
        }}
      >
        {/* Step number badge */}
        <div
          style={{
            width: '34px',
            height: '34px',
            borderRadius: '50%',
            background: 'rgba(139,92,246,0.18)',
            border: '1px solid rgba(139,92,246,0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '12px',
            fontWeight: '700',
            color: '#a855f7',
            flexShrink: 0,
            marginTop: '2px',
          }}
        >
          {stepNumber}
        </div>

        {/* Step details */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px', marginBottom: '6px' }}>
            <span style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)' }}>
              {step.refactoring || 'Unnamed Refactoring'}
            </span>
            <LangBadge step={step} />
            <SecurityBadge step={step} />
          </div>

          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: '1.5' }}>
            {step.explanation || 'No explanation provided'}
          </div>

          {/* Target & Smell ID metadata grid */}
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '12px',
            fontSize: '11px',
            background: 'rgba(0,0,0,0.15)',
            padding: '8px 12px',
            borderRadius: '6px',
            border: '1px solid rgba(255,255,255,0.05)',
            alignItems: 'center',
          }}>
            <div>
              <span style={{ color: 'var(--text-muted)' }}>Target: </span>
              <strong style={{ color: '#00d4e8' }}>
                {step.target?.class || step.target?.method
                  ? `${step.target.class || ''}${step.target.method ? `.${step.target.method}` : ''}`
                  : (step.target?.file || '(module scope)')}
                {step.target?.lines && step.target.lines.length > 0 ? ` (Lines: ${step.target.lines.join('-')})` : ''}
              </strong>
            </div>

            {step.target?.file && (
              <div>
                <span style={{ color: 'var(--text-muted)' }}>File: </span>
                <span style={{ color: 'var(--text-secondary)' }}>{step.target.file}</span>
              </div>
            )}

            <div>
              <span style={{ color: 'var(--text-muted)' }}>Smell ID: </span>
              <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>{step.smell_id || '—'}</span>
            </div>
          </div>

          {/* Parameters display as styled pills */}
          {step.parameters && Object.keys(step.parameters).length > 0 && (
            <div style={{ marginTop: '10px', display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 600 }}>Parameters:</span>
              {Object.entries(step.parameters).map(([k, v]) => (
                <span
                  key={k}
                  style={{
                    fontSize: '11px',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    background: 'rgba(139,92,246,0.1)',
                    border: '1px solid rgba(139,92,246,0.2)',
                    color: 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  <strong style={{ color: '#c4b5fd' }}>{k}:</strong> {Array.isArray(v) ? v.join('-') : String(v)}
                </span>
              ))}
            </div>
          )}

          {/* C Unsafe Function security hint */}
          {UNSAFE_SMELL_REFACTORINGS.has(step.refactoring) && step.parameters?.safe_alternative && (
            <div style={{
              marginTop: 10, padding: '8px 12px',
              background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: 6, fontSize: 11,
            }}>
              <span style={{ color: '#ef4444', fontWeight: 700 }}>Security Fix: </span>
              <span style={{ color: 'var(--text-secondary)' }}>Replace unsafe buffer call with </span>
              <code style={{ color: '#fca5a5', fontWeight: 700 }}>{step.parameters.safe_alternative}()</code>
            </div>
          )}

          {/* C Global Variable encapsulation plan */}
          {GLOBAL_VAR_REFACTORINGS.has(step.refactoring) && step.parameters?.variable_name && (
            <div style={{
              marginTop: 10, padding: '8px 12px',
              background: 'rgba(249,115,22,0.08)', border: '1px solid rgba(249,115,22,0.25)',
              borderRadius: 6, fontSize: 11,
            }}>
              <span style={{ color: '#f97316', fontWeight: 700 }}>Encapsulation Plan: </span>
              <span style={{ color: 'var(--text-secondary)' }}>
                Create <code style={{ color: '#fb923c' }}>{step.parameters.getter_name}()</code> and <code style={{ color: '#fb923c' }}>{step.parameters.setter_name}()</code> for global variable <code style={{ color: '#fb923c' }}>{step.parameters.variable_name}</code>
              </span>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!plan) return null;

  return (
    <section
      className="card"
      style={{
        marginTop: '24px',
        background: 'linear-gradient(135deg, rgba(34,197,94,0.02) 0%, rgba(74,222,128,0.02) 100%)',
        borderColor: 'rgba(34,197,94,0.18)',
        borderRadius: '12px',
        padding: '24px',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: '700', color: 'var(--text-primary)' }}>Refactoring Plan</h2>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
            Target: <strong style={{ color: '#4ade80' }}>{plan.target || 'Project Source'}</strong>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            id="copy-btn"
            onClick={onCopy}
            title="Copy plan to clipboard"
            style={{
              padding: '8px 14px',
              background: 'rgba(59,130,246,0.1)',
              border: '1px solid rgba(59,130,246,0.25)',
              borderRadius: '6px',
              fontSize: '12px',
              fontWeight: '600',
              color: '#60a5fa',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            📋 Copy JSON
          </button>
          <button
            id="download-btn"
            onClick={onDownload}
            title="Download plan as JSON file"
            style={{
              padding: '8px 14px',
              background: 'rgba(34,197,94,0.15)',
              border: '1px solid rgba(34,197,94,0.3)',
              borderRadius: '6px',
              fontSize: '12px',
              fontWeight: '600',
              color: '#4ade80',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            ⬇ Download Plan
          </button>
        </div>
      </div>

      {/* Summary Metrics Grid */}
      {stats && (
        <div
          style={{
            padding: '16px 20px',
            background: 'rgba(34,197,94,0.04)',
            borderRadius: '10px',
            border: '1px solid rgba(34,197,94,0.15)',
            marginBottom: '24px',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
            gap: '16px',
          }}
        >
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Total Steps
            </div>
            <div style={{ fontSize: '22px', fontWeight: '700', color: '#4ade80' }}>
              {stats.totalSteps || allSteps.length || 0}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Refactoring Types
            </div>
            <div style={{ fontSize: '22px', fontWeight: '700', color: '#a855f7' }}>
              {Object.keys(stats.refactoringTypes || {}).length}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Classes / Modules
            </div>
            <div style={{ fontSize: '22px', fontWeight: '700', color: '#00d4e8' }}>
              {stats.uniqueClasses || 0}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              Methods Affected
            </div>
            <div style={{ fontSize: '22px', fontWeight: '700', color: '#fb923c' }}>
              {stats.uniqueMethods || 0}
            </div>
          </div>
        </div>
      )}

      {/* Plan Summary Banner */}
      {plan.summary && (
        <div
          style={{
            padding: '14px 18px',
            background: 'rgba(139,92,246,0.06)',
            borderLeft: '4px solid #a855f7',
            borderRadius: '6px',
            marginBottom: '24px',
            fontSize: '13px',
            color: 'var(--text-primary)',
            lineHeight: '1.6',
          }}
        >
          {plan.summary}
        </div>
      )}

      {/* Categorization, Search & Filter Toolbar */}
      <div
        style={{
          background: 'rgba(0,0,0,0.2)',
          border: '1px solid rgba(139,92,246,0.15)',
          borderRadius: '10px',
          padding: '16px',
          marginBottom: '24px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
          <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🛠 Categorization &amp; Filtering</span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 400 }}>
              (Showing {filteredSteps.length} of {allSteps.length} steps)
            </span>
          </div>

          {isFiltered && (
            <button
              onClick={resetFilters}
              style={{
                fontSize: '11px',
                padding: '4px 10px',
                borderRadius: '4px',
                background: 'rgba(239,68,68,0.12)',
                border: '1px solid rgba(239,68,68,0.3)',
                color: '#ef4444',
                cursor: 'pointer',
                fontWeight: 600,
              }}
            >
              ✕ Reset Filters
            </button>
          )}
        </div>

        {/* Search input + Dropdowns row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '10px' }}>
          {/* Search box */}
          <input
            type="text"
            placeholder="🔍 Search steps, files, methods..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid rgba(139,92,246,0.35)',
              background: '#1e1b2e',
              color: '#e2e8f0',
              fontSize: '12px',
              outline: 'none',
            }}
          />

          {/* Group By selector */}
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid rgba(139,92,246,0.35)',
              background: '#1e1b2e',
              color: '#e2e8f0',
              fontSize: '12px',
              outline: 'none',
              cursor: 'pointer',
              colorScheme: 'dark',
            }}
          >
            <option value="none" style={{ background: '#1e1b2e', color: '#f8fafc' }}>Group By: None (Sequential)</option>
            <option value="refactoring" style={{ background: '#1e1b2e', color: '#f8fafc' }}>Group By: Refactoring Type</option>
            <option value="file" style={{ background: '#1e1b2e', color: '#f8fafc' }}>Group By: Target File / Class</option>
            <option value="risk" style={{ background: '#1e1b2e', color: '#f8fafc' }}>Group By: Risk &amp; Impact Category</option>
          </select>

          {/* Refactoring Type filter */}
          <select
            value={selectedRefactoring}
            onChange={(e) => setSelectedRefactoring(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid rgba(139,92,246,0.35)',
              background: '#1e1b2e',
              color: '#e2e8f0',
              fontSize: '12px',
              outline: 'none',
              cursor: 'pointer',
              colorScheme: 'dark',
            }}
          >
            {refactoringTypes.map(type => (
              <option key={type} value={type} style={{ background: '#1e1b2e', color: '#f8fafc' }}>
                {type === 'ALL' ? 'Technique: All Types' : type}
              </option>
            ))}
          </select>

          {/* Target File filter */}
          <select
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid rgba(139,92,246,0.35)',
              background: '#1e1b2e',
              color: '#e2e8f0',
              fontSize: '12px',
              outline: 'none',
              cursor: 'pointer',
              colorScheme: 'dark',
            }}
          >
            {fileList.map(f => (
              <option key={f} value={f} style={{ background: '#1e1b2e', color: '#f8fafc' }}>
                {f === 'ALL' ? 'Target: All Files' : f}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Refactoring Steps List (Grouped or Flat) */}
      <div style={{ marginBottom: '24px' }}>
        {filteredSteps.length === 0 ? (
          <div style={{
            padding: '32px',
            textAlign: 'center',
            background: 'rgba(0,0,0,0.1)',
            borderRadius: '8px',
            border: '1px border-dashed rgba(139,92,246,0.2)',
            color: 'var(--text-secondary)',
          }}>
            <p style={{ fontSize: '14px', marginBottom: '8px' }}>🔍 No refactoring steps match your selected filters.</p>
            <button
              onClick={resetFilters}
              style={{
                fontSize: '12px',
                padding: '6px 14px',
                borderRadius: '6px',
                background: 'rgba(139,92,246,0.2)',
                border: '1px solid rgba(139,92,246,0.4)',
                color: '#c4b5fd',
                cursor: 'pointer',
                fontWeight: '600',
              }}
            >
              Reset Filters
            </button>
          </div>
        ) : groupedSteps ? (
          /* GROUPED VIEW */
          <div style={{ display: 'grid', gap: '20px' }}>
            {Object.entries(groupedSteps).map(([groupName, groupSteps]) => {
              const isCollapsed = !!collapsedGroups[groupName];
              return (
                <div
                  key={groupName}
                  style={{
                    background: 'rgba(0,0,0,0.15)',
                    border: '1px solid rgba(139,92,246,0.2)',
                    borderRadius: '10px',
                    overflow: 'hidden',
                  }}
                >
                  {/* Group Header */}
                  <div
                    onClick={() => toggleGroup(groupName)}
                    style={{
                      padding: '12px 18px',
                      background: 'rgba(139,92,246,0.08)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      cursor: 'pointer',
                      userSelect: 'none',
                      borderBottom: isCollapsed ? 'none' : '1px solid rgba(139,92,246,0.15)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '12px', color: '#a855f7' }}>{isCollapsed ? '▶' : '▼'}</span>
                      <strong style={{ fontSize: '14px', color: 'var(--text-primary)' }}>{groupName}</strong>
                    </div>
                    <span style={{
                      fontSize: '11px',
                      fontWeight: 700,
                      padding: '2px 10px',
                      borderRadius: 99,
                      background: 'rgba(139,92,246,0.18)',
                      color: '#c4b5fd',
                    }}>
                      {groupSteps.length} {groupSteps.length === 1 ? 'step' : 'steps'}
                    </span>
                  </div>

                  {/* Group Step List */}
                  {!isCollapsed && (
                    <div style={{ padding: '16px', display: 'grid', gap: '12px' }}>
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
          <div style={{ display: 'grid', gap: '12px' }}>
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
          borderTop: '1px solid rgba(139,92,246,0.15)',
          paddingTop: '16px',
        }}
      >
        <summary
          onClick={() => setShowRawJson(!showRawJson)}
          style={{
            cursor: 'pointer',
            fontSize: '13px',
            fontWeight: '600',
            color: '#a855f7',
            padding: '10px 14px',
            background: 'rgba(139,92,246,0.06)',
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
              padding: '14px',
              background: 'var(--bg-secondary)',
              borderRadius: '6px',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              overflow: 'auto',
              maxHeight: '400px',
              lineHeight: '1.4',
              color: 'var(--text-secondary)',
              border: '1px solid rgba(139,92,246,0.15)',
            }}
          >
            {JSON.stringify(plan, null, 2)}
          </pre>
        )}
      </details>
    </section>
  );
}
