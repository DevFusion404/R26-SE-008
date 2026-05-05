/**
 * Overview.jsx — Landing / Home page
 * Shows the project pitch, pipeline flow, and telemetry summary.
 */

import { useState } from 'react';

const PIPELINE_STEPS = [
  { id: 'repo',    icon: '🔗', label: 'Repository\nInput',       color: 'var(--text-secondary)' },
  { id: 'cuqa',   icon: '🔍', label: 'CUQA\nAgent',             color: 'var(--color-cuqa)',     sub: 'CODE QUALITY\nANALYST' },
  { id: 'rdp',    icon: '🧠', label: 'RDP Agent',               color: 'var(--color-rdp)',      sub: 'REFACTORING\nPLANNER' },
  { id: 'trans',  icon: '⚡', label: 'Transformation\nAgent',   color: 'var(--color-transform)' },
  { id: 'orch',   icon: '🎛️', label: 'Orchestration\nAgent',    color: 'var(--color-orchestrate)' },
  { id: 'out',    icon: '✅', label: 'Refactored\nCode',        color: 'var(--color-ok)' },
];

function PipelineCard({ step, active }) {
  return (
    <div style={{
      background: 'var(--bg-card)',
      border: `1px solid ${active ? step.color : 'var(--border)'}`,
      borderRadius: 'var(--r-md)',
      padding: '20px 16px',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 8,
      minWidth: 100,
      textAlign: 'center',
      boxShadow: active ? `0 0 16px ${step.color}22` : 'none',
      transition: 'all 0.2s',
      cursor: 'default',
    }}>
      <div style={{
        width: 44, height: 44,
        borderRadius: 'var(--r-md)',
        background: `${step.color}18`,
        border: `1px solid ${step.color}40`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 20,
      }}>
        {step.icon}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'pre-line', lineHeight: 1.3 }}>
        {step.label}
      </div>
      {step.sub && (
        <div style={{ fontSize: 9, letterSpacing: '0.8px', color: step.color, fontWeight: 600, whiteSpace: 'pre-line', lineHeight: 1.3 }}>
          {step.sub}
        </div>
      )}
    </div>
  );
}

const BAR_DATA = [
  { label: 'Redux Store Migration',     height: 55 },
  { label: 'Legacy Python Wrapper',     height: 75 },
  { label: 'Orchestrator ID-Slice',     height: 40 },
  { label: 'Core API Refactor',         height: 85 },
  { label: 'Service Decomposition',     height: 62 },
];

export default function Overview({ onNavigate }) {
  return (
    <div className="page-container" style={{ maxWidth: 980 }}>

      {/* ── Hero ────────────────────────────────────────────── */}
      <div style={{ padding: '12px 0 8px' }}>
        <span className="badge badge-accent" style={{ marginBottom: 20, display: 'inline-flex', gap: 6 }}>
          ⚠ RESEARCH PROTOTYPE R26-SE-008
        </span>

        <h1 style={{
          fontSize: 'clamp(24px, 3.5vw, 38px)',
          fontWeight: 800,
          lineHeight: 1.2,
          color: 'var(--text-primary)',
          marginTop: 16,
          maxWidth: 700,
        }}>
          An{' '}
          <span style={{ color: 'var(--accent)' }}>Agentic Intelligent</span>
          {' '}Code Refactoring Assistant for Legacy Java and Python Systems
        </h1>

        <p style={{
          marginTop: 16,
          fontSize: 14,
          color: 'var(--text-secondary)',
          maxWidth: 560,
          lineHeight: 1.7,
        }}>
          AI-powered multi-agent refactoring assistant for improving legacy Java and Python
          systems. Modernize technical debt with surgical precision using academic-grade
          orchestration logic.
        </p>

        <div style={{ display: 'flex', gap: 12, marginTop: 28 }}>
          <button className="btn btn-primary" onClick={() => onNavigate('repository')}>
            ▶ Start Analysis
          </button>
          <button className="btn btn-outline" onClick={() => onNavigate('cuqa')}>
            View Workflow →
          </button>
        </div>
      </div>

      {/* ── Pipeline ─────────────────────────────────────────── */}
      <div style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>
          Autonomous Multi-Agent Pipeline
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20, maxWidth: 520 }}>
          The system operates as a self-correcting loop of specialised agents, each handling
          a critical phase of the modernisation journey.
        </p>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {PIPELINE_STEPS.map((step, i) => (
            <div key={step.id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <PipelineCard step={step} active={step.id === 'cuqa'} />
              {i < PIPELINE_STEPS.length - 1 && (
                <span style={{ fontSize: 16, color: 'var(--text-muted)', flexShrink: 0 }}>→</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Two-col section ──────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 8 }}>

        {/* Agentic Orchestration */}
        <div className="card" style={{ padding: 24 }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>
            Agentic Orchestration
          </h3>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 16 }}>
            Unlike static scripts, our agents negotiate refactoring strategies, validate
            changes against unit tests in real-time, and roll back regressions automatically.
            This 'agentic' feedback loop ensures high-fidelity modernisation for critical
            enterprise systems.
          </p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <span className="badge badge-ok">✔ Java SE 8+</span>
            <span className="badge badge-python">✔ Python 3.9+</span>
          </div>
        </div>

        {/* Telemetry */}
        <div className="card" style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 4 }}>
            <div>
              <div style={{ fontSize: 10, letterSpacing: '1px', color: 'var(--accent)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 4 }}>
                Active Analysis
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
                Real-time Refactoring Telemetry
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 10, letterSpacing: '1px', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>System Health</div>
              <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--color-ok)' }}>99.4%</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>Refactoring Validation Rate</div>
            </div>
          </div>

          {/* Mini bar chart */}
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 60, marginTop: 16 }}>
            {BAR_DATA.map((b, i) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                <div style={{
                  width: '100%',
                  height: `${b.height}%`,
                  background: i === 3 ? 'var(--accent)' : 'var(--bg-elevated)',
                  border: `1px solid ${i === 3 ? 'var(--accent)' : 'var(--border)'}`,
                  borderRadius: '3px 3px 0 0',
                  transition: 'height 0.5s ease',
                }} />
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 8, justifyContent: 'space-between' }}>
            {BAR_DATA.map((b, i) => (
              <div key={i} style={{ fontSize: 9, color: 'var(--text-muted)', textAlign: 'center', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {b.label.split(' ')[0]}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, textAlign: 'right' }}>
            <a href="#" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>
              Detailed Analytics ↗
            </a>
          </div>
        </div>
      </div>

      {/* ── Enterprise Ready ─────────────────────────────────── */}
      <div className="card" style={{ padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 24 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 'var(--r-md)',
            background: 'var(--accent-muted)', border: '1px solid var(--border-accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 20, flexShrink: 0,
          }}>💻</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 6 }}>CLI Integrated</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Run agentic workflows directly from your CI/CD pipeline with our headless orchestrator.
            </div>
          </div>
          <div style={{ flexShrink: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Enterprise Ready</div>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: 280, marginBottom: 12 }}>
              Supports massive monorepos and complex dependency trees, providing human-in-the-loop
              controls for sensitive refactoring tasks.
            </p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span className="badge badge-accent">SAM-OD</span>
              <span className="badge badge-purple">VPC PEERING</span>
              <span className="badge badge-ok">ON-PREM DEPLOY</span>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
