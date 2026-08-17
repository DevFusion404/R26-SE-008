/**
 * ResultsViewer.jsx — Refactoring Plan Display
 * Shows the generated refactoring plan with steps and export options
 */

import { useState } from 'react';

// ── C language helpers ──────────────────────────────────────────────────────
const C_EXTENSIONS = ['.c', '.h'];

function isCFile(step) {
  const file = step?.target?.file || '';
  return C_EXTENSIONS.some(ext => file.toLowerCase().endsWith(ext))
    || (step?.target?.language || '').toLowerCase() === 'c';
}

const UNSAFE_SMELL_REFACTORINGS = new Set(['Replace Unsafe Function']);
const GLOBAL_VAR_REFACTORINGS   = new Set(['Encapsulate Variable']);

function LangBadge({ step }) {
  if (!isCFile(step)) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: '1px 7px', borderRadius: 99,
      background: 'rgba(249,115,22,0.12)', border: '1px solid rgba(249,115,22,0.3)',
      fontSize: 10, fontWeight: 700, color: '#f97316',
      marginLeft: 8, verticalAlign: 'middle',
    }}>C</span>
  );
}

function SecurityBadge({ step }) {
  if (!UNSAFE_SMELL_REFACTORINGS.has(step.refactoring)) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: '1px 7px', borderRadius: 99,
      background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.30)',
      fontSize: 10, fontWeight: 700, color: '#ef4444',
      marginLeft: 6, verticalAlign: 'middle',
    }}>⚠ Security</span>
  );
}

export default function ResultsViewer({ plan, stats, onDownload, onCopy }) {
  const [showRawJson, setShowRawJson] = useState(false);

  return (
    <section
      className="card"
      style={{
        marginTop: '24px',
        background: 'linear-gradient(135deg, rgba(34,197,94,0.02) 0%, rgba(74,222,128,0.02) 100%)',
        borderColor: 'rgba(34,197,94,0.15)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2 style={{ fontSize: '18px', fontWeight: '700' }}>Refactoring Plan</h2>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            id="copy-btn"
            onClick={onCopy}
            title="Copy to clipboard"
            style={{
              padding: '8px 12px',
              background: 'rgba(59,130,246,0.1)',
              border: '1px solid rgba(59,130,246,0.2)',
              borderRadius: '6px',
              fontSize: '12px',
              fontWeight: '500',
              color: 'rgba(59,130,246,1)',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              e.target.style.background = 'rgba(59,130,246,0.15)';
              e.target.style.borderColor = 'rgba(59,130,246,0.3)';
            }}
            onMouseLeave={(e) => {
              e.target.style.background = 'rgba(59,130,246,0.1)';
              e.target.style.borderColor = 'rgba(59,130,246,0.2)';
            }}
          >
            📋 Copy
          </button>
          <button
            id="download-btn"
            onClick={onDownload}
            title="Download as JSON"
            style={{
              padding: '8px 12px',
              background: 'rgba(34,197,94,0.1)',
              border: '1px solid rgba(34,197,94,0.2)',
              borderRadius: '6px',
              fontSize: '12px',
              fontWeight: '500',
              color: 'rgba(34,197,94,1)',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              e.target.style.background = 'rgba(34,197,94,0.15)';
              e.target.style.borderColor = 'rgba(34,197,94,0.3)';
            }}
            onMouseLeave={(e) => {
              e.target.style.background = 'rgba(34,197,94,0.1)';
              e.target.style.borderColor = 'rgba(34,197,94,0.2)';
            }}
          >
            ⬇️ Download
          </button>
        </div>
      </div>

      {/* Summary Card */}
      {stats && (
        <div
          style={{
            padding: '16px',
            background: 'rgba(34,197,94,0.05)',
            borderRadius: '8px',
            border: '1px solid rgba(34,197,94,0.15)',
            marginBottom: '20px',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '16px',
          }}
        >
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Total Steps
            </div>
            <div style={{ fontSize: '20px', fontWeight: '700', color: 'rgba(34,197,94,1)' }}>
              {stats.totalSteps || 0}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Refactoring Types
            </div>
            <div style={{ fontSize: '20px', fontWeight: '700', color: 'rgba(34,197,94,1)' }}>
              {Object.keys(stats.refactoringTypes || {}).length}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Classes Affected
            </div>
            <div style={{ fontSize: '20px', fontWeight: '700', color: 'rgba(34,197,94,1)' }}>
              {stats.uniqueClasses || 0}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Methods Affected
            </div>
            <div style={{ fontSize: '20px', fontWeight: '700', color: 'rgba(34,197,94,1)' }}>
              {stats.uniqueMethods || 0}
            </div>
          </div>
        </div>
      )}

      {/* Plan Summary */}
      {plan.summary && (
        <div
          style={{
            padding: '12px 16px',
            background: 'rgba(139,92,246,0.05)',
            borderLeft: '3px solid rgba(139,92,246,0.3)',
            borderRadius: '4px',
            marginBottom: '20px',
            fontSize: '13px',
            color: 'var(--text-primary)',
            lineHeight: '1.6',
          }}
        >
          {plan.summary}
        </div>
      )}

      {/* Refactoring Steps */}
      <div style={{ marginBottom: '20px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '12px' }}>
          Refactoring Steps ({plan.steps?.length || 0})
        </h3>
        <div style={{ display: 'grid', gap: '12px' }}>
          {plan.steps && plan.steps.length > 0 ? (
            plan.steps.map((step, idx) => (
              <div
                key={idx}
                style={{
                  padding: '12px 16px',
                  background: 'rgba(139,92,246,0.04)',
                  border: '1px solid rgba(139,92,246,0.15)',
                  borderRadius: '6px',
                  display: 'grid',
                  gridTemplateColumns: '40px 1fr',
                  gap: '12px',
                  alignItems: 'start',
                }}
              >
                {/* Step number */}
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: 'rgba(139,92,246,0.2)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '12px',
                    fontWeight: '700',
                    color: 'rgba(139,92,246,1)',
                    flexShrink: 0,
                  }}
                >
                  {idx + 1}
                </div>

                {/* Step details */}
                <div>
                  <div style={{ fontSize: '13px', fontWeight: '600', marginBottom: '4px' }}>
                    {step.refactoring || 'Unnamed Refactoring'}
                    <LangBadge step={step} />
                    <SecurityBadge step={step} />
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                    {step.explanation || 'No explanation provided'}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '11px' }}>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>Target:</span>{' '}
                      <span style={{ fontWeight: '500' }}>
                        {step.target?.class || step.target?.method
                          ? `${step.target.class || ''}${step.target.method ? `.${step.target.method}` : ''}`
                          : (step.target?.file || '(module level)')}
                        {step.target?.lines && step.target.lines.length > 0 ? ` (Lines: ${step.target.lines.join('-')})` : ''}
                      </span>
                    </div>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>Smell:</span>{' '}
                      <span style={{ fontWeight: '500' }}>{step.smell_id || '—'}</span>
                    </div>
                  </div>
                  {step.parameters && Object.keys(step.parameters).length > 0 && (
                    <div style={{ fontSize: '11px', marginTop: '8px', color: 'var(--text-secondary)' }}>
                      Parameters: {Object.entries(step.parameters).map(([k, v]) => `${k}: ${v}`).join(', ')}
                    </div>
                  )}

                  {/* C Unsafe Function — security hint showing safe replacement */}
                  {UNSAFE_SMELL_REFACTORINGS.has(step.refactoring) && step.parameters?.safe_alternative && (
                    <div style={{
                      marginTop: 8, padding: '6px 10px',
                      background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)',
                      borderRadius: 4, fontSize: 11,
                    }}>
                      <span style={{ color: '#ef4444', fontWeight: 600 }}>Replace with: </span>
                      <code style={{ color: '#fca5a5' }}>{step.parameters.safe_alternative}()</code>
                    </div>
                  )}

                  {/* C Global Variable — show getter/setter that will be created */}
                  {GLOBAL_VAR_REFACTORINGS.has(step.refactoring) && step.parameters?.variable_name && (
                    <div style={{
                      marginTop: 8, padding: '6px 10px',
                      background: 'rgba(249,115,22,0.06)', border: '1px solid rgba(249,115,22,0.2)',
                      borderRadius: 4, fontSize: 11,
                    }}>
                      <span style={{ color: '#f97316', fontWeight: 600 }}>Encapsulation plan: </span>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        Create{' '}
                        <code style={{ color: '#fb923c' }}>{step.parameters.getter_name}()</code>
                        {' '}and{' '}
                        <code style={{ color: '#fb923c' }}>{step.parameters.setter_name}()</code>
                        {' '}for global <code style={{ color: '#fb923c' }}>{step.parameters.variable_name}</code>
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))
          ) : (
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', textAlign: 'center', padding: '20px' }}>
              No steps in plan
            </div>
          )}
        </div>
      </div>

      {/* Raw JSON Toggle */}
      <details
        style={{
          borderTop: '1px solid rgba(139,92,246,0.1)',
          paddingTop: '16px',
        }}
      >
        <summary
          onClick={() => setShowRawJson(!showRawJson)}
          style={{
            cursor: 'pointer',
            fontSize: '13px',
            fontWeight: '600',
            color: 'rgba(139,92,246,1)',
            padding: '8px 12px',
            background: 'rgba(139,92,246,0.05)',
            borderRadius: '6px',
            userSelect: 'none',
            transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'rgba(139,92,246,0.1)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'rgba(139,92,246,0.05)';
          }}
        >
          {showRawJson ? '▼' : '▶'} View Raw JSON
        </summary>
        {showRawJson && (
          <pre
            id="json-output"
            style={{
              marginTop: '12px',
              padding: '12px',
              background: 'var(--bg-secondary)',
              borderRadius: '6px',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              overflow: 'auto',
              maxHeight: '400px',
              lineHeight: '1.4',
              color: 'var(--text-secondary)',
            }}
          >
            {JSON.stringify(plan, null, 2)}
          </pre>
        )}
      </details>
    </section>
  );
}
