/**
 * Documentation.jsx — RefactorIQ System & Agent Documentation
 * Comprehensive documentation for the multi-agent refactoring assistant.
 */

import { useState } from 'react';

const SECTIONS = [
  { id: 'overview', icon: '🚀', label: 'Platform Overview' },
  { id: 'architecture', icon: '🏗️', label: 'Agent Architecture' },
  { id: 'quickstart', icon: '⚡', label: 'Quick Start Guide' },
  { id: 'smells', icon: '🦨', label: 'Code Smell Catalogue' },
  { id: 'api', icon: '🔌', label: 'Backend API Reference' },
  { id: 'cli', icon: '💻', label: 'CLI & Headless Execution' },
];

export default function Documentation() {
  const [activeTab, setActiveTab] = useState('overview');
  const [search, setSearch] = useState('');

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">📖</div>
          <div>
            <div className="page-title">Documentation</div>
            <div className="page-subtitle">
              Comprehensive setup guides, architecture details, and API documentation for <strong style={{ color: 'var(--accent)' }}>RefactorIQ</strong>.
            </div>
          </div>
        </div>
        <div className="page-header-actions">
          <a
            href="http://localhost:8080/docs"
            target="_blank"
            rel="noreferrer"
            className="btn btn-outline btn-sm"
          >
            ⚡ Swagger OpenAPI →
          </a>
        </div>
      </div>

      {/* Main Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16, alignItems: 'start' }}>
        {/* Navigation Sidebar */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ marginBottom: 12, padding: '0 4px' }}>
            <input
              className="input"
              placeholder="Search docs..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ fontSize: 11, padding: '6px 10px' }}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {SECTIONS.filter(s => s.label.toLowerCase().includes(search.toLowerCase())).map(s => (
              <button
                key={s.id}
                onClick={() => setActiveTab(s.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '9px 12px',
                  borderRadius: 'var(--r-sm)',
                  border: 'none',
                  background: activeTab === s.id ? 'var(--accent-muted)' : 'transparent',
                  color: activeTab === s.id ? 'var(--accent)' : 'var(--text-secondary)',
                  fontWeight: activeTab === s.id ? 600 : 500,
                  fontSize: 12,
                  textAlign: 'left',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <span>{s.icon}</span>
                <span>{s.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Content Area */}
        <div className="card card-body" style={{ padding: 24, minHeight: 600 }}>
          {activeTab === 'overview' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                🚀 RefactorIQ Platform Overview
              </h2>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 16 }}>
                <strong>RefactorIQ (R26-SE-008)</strong> is an autonomous, developer-in-the-loop multi-agent code modernisation platform designed for legacy Java, Python, and C/C++ codebases. It moves beyond traditional static analysis by pairing deep AST understanding with intelligent refactoring planning, safe code transformations, and interactive developer feedback loops.
              </p>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 20 }}>
                <div style={{ background: 'var(--bg-elevated)', padding: 14, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>Key Problem Addressed</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    Legacy enterprise systems accumulate architectural technical debt, circular dependencies, and monolithic code smells that are risky and time-consuming to refactor manually.
                  </p>
                </div>
                <div style={{ background: 'var(--bg-elevated)', padding: 14, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: '#22c55e', marginBottom: 6 }}>The Agentic Solution</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    A pipeline of four coordinated AI agents that continuously assess quality, design surgical transformations, validate safety against unit tests, and incorporate human feedback.
                  </p>
                </div>
              </div>

              <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Supported Polyglot Stack</h3>
              <ul style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.8, paddingLeft: 20, marginBottom: 20 }}>
                <li><strong>Python (3.9+):</strong> Native Python AST parsing, PEP8 violation detection, cyclomatic complexity profiling, function decomposition.</li>
                <li><strong>Java (SE 8+):</strong> Javalang AST tree parser, OOP class structure analysis, method length & parameter list inspection.</li>
                <li><strong>C / Header files:</strong> Tree-sitter powered AST extraction for <code>.c</code> and <code>.h</code> files, memory leak patterns, pointer safety checks.</li>
              </ul>
            </div>
          )}

          {activeTab === 'architecture' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                🏗️ Multi-Agent Architecture
              </h2>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 20 }}>
                The RefactorIQ architecture splits complex code refactoring into four distinct agent roles:
              </p>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {[
                  {
                    name: '1. CUQA Agent (Code Understanding & Quality Assessment)',
                    color: 'var(--color-cuqa)',
                    icon: '🔍',
                    desc: 'Accepts ZIP uploads or GitHub repositories, parses AST structures across supported languages, identifies smell severity levels, and emits standardized JSON quality reports.',
                  },
                  {
                    name: '2. RDP Agent (Refactoring Decision & Planning)',
                    color: 'var(--color-rdp)',
                    icon: '🧠',
                    desc: 'Consumes CUQA quality JSON and formulates prioritized transformation plans. Maps detected smells directly to safe refactoring strategies (Extract Method, Rename, Break Dependency).',
                  },
                  {
                    name: '3. Transformation Agent (Safe Code Transformation & Validation)',
                    color: 'var(--color-transform)',
                    icon: '⚡',
                    desc: 'Executes AST-level code edits using SCTVA logic. Verifies code integrity pre- and post-transformation, ensuring zero compilation or syntax regressions.',
                  },
                  {
                    name: '4. DIWO Agent (Developer-in-the-Loop Workflow Orchestrator)',
                    color: 'var(--color-orchestrate)',
                    icon: '🎛️',
                    desc: 'Coordinates human approval checkpoints, tracks developer feedback (accepted vs rejected suggestions), and persists audit telemetry into the database.',
                  },
                ].map(agent => (
                  <div key={agent.name} style={{ background: 'var(--bg-elevated)', padding: 16, borderRadius: 'var(--r-sm)', borderLeft: `4px solid ${agent.color}` }}>
                    <div style={{ fontWeight: 700, fontSize: 13, color: agent.color, marginBottom: 4 }}>
                      {agent.icon} {agent.name}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                      {agent.desc}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'quickstart' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                ⚡ Quick Start Guide
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{ background: 'var(--bg-elevated)', padding: 16, borderRadius: 'var(--r-sm)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>Step 1: Connect Repository</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    Navigate to <strong>Repository Input</strong>. Paste a public GitHub URL (e.g. <code>https://github.com/pallets/flask</code>) or drag-and-drop a local <code>.zip</code> archive. Set your preferred Quality Threshold (e.g., 75%).
                  </p>
                </div>

                <div style={{ background: 'var(--bg-elevated)', padding: 16, borderRadius: 'var(--r-sm)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>Step 2: Inspect AST & Code Smells in CUQA</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    The CUQA Agent automatically analyzes the codebase. Browse files in the interactive file explorer, visualize AST graphs, and inspect detected critical and style code smells.
                  </p>
                </div>

                <div style={{ background: 'var(--bg-elevated)', padding: 16, borderRadius: 'var(--r-sm)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>Step 3: Generate Refactoring Plan in RDP</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    Click <strong>"Send to RDP Agent"</strong>. The Refactoring Decision & Planning agent will analyze the quality report and output step-by-step refactoring proposals.
                  </p>
                </div>

                <div style={{ background: 'var(--bg-elevated)', padding: 16, borderRadius: 'var(--r-sm)' }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>Step 4: Execute & Review in DIWO</div>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    Approve or reject individual refactoring actions in the DIWO workflow panel. Accepted changes are saved and recorded in audit logs.
                  </p>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'smells' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                🦨 Detectable Code Smell Catalogue
              </h2>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Smell Type</th>
                      <th>Language</th>
                      <th>Severity</th>
                      <th>Detection Mechanism</th>
                      <th>Recommended Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { smell: 'Long Method', lang: 'Python / Java / C', sev: 'HIGH', desc: 'Function > 50 lines of code', action: 'Extract Method' },
                      { smell: 'Too Many Parameters', lang: 'Java / C', sev: 'MEDIUM', desc: 'Parameter list > 5 arguments', action: 'Introduce Parameter Object' },
                      { smell: 'Deep Nesting', lang: 'Python / C', sev: 'HIGH', desc: 'Indentation / control flow depth > 4', action: 'Guard Clauses / Early Return' },
                      { smell: 'Naming Convention Violation', lang: 'Java / Python', sev: 'LOW', desc: 'Non-PEP8 or non-CamelCase identifiers', action: 'Rename Identifier' },
                      { smell: 'Circular Dependency', lang: 'Java', sev: 'HIGH', desc: 'Cyclic class import topology', action: 'Dependency Inversion' },
                      { smell: 'Unused Memory Allocation', lang: 'C', sev: 'HIGH', desc: 'malloc/calloc without matching free', action: 'Safely Add Deallocation' },
                    ].map((row, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{row.smell}</td>
                        <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{row.lang}</td>
                        <td>
                          <span className={`badge badge-${row.sev === 'HIGH' ? 'critical' : row.sev === 'MEDIUM' ? 'medium' : 'success'}`}>
                            {row.sev}
                          </span>
                        </td>
                        <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{row.desc}</td>
                        <td style={{ fontSize: 11, color: 'var(--accent)' }}>{row.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {activeTab === 'api' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                🔌 CUQA Agent API Endpoints
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {[
                  { method: 'GET', path: '/api/health', desc: 'Check backend status and workspace loaded status.' },
                  { method: 'POST', path: '/api/upload-zip', desc: 'Upload local .zip file containing source code.' },
                  { method: 'POST', path: '/api/github-repo', desc: 'Clone and parse public GitHub repository.' },
                  { method: 'GET', path: '/api/project-structure', desc: 'Retrieve full directory tree for file explorer.' },
                  { method: 'POST', path: '/api/parse-ast', desc: 'Parse specific source file into enriched AST JSON.' },
                  { method: 'POST', path: '/api/quality-report', desc: 'Generate repository-wide or single-file quality report.' },
                  { method: 'GET', path: '/api/files', desc: 'List all discovered source files in active workspace.' },
                ].map(ep => (
                  <div key={ep.path} style={{ background: 'var(--bg-elevated)', padding: 12, borderRadius: 'var(--r-sm)', display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{
                      padding: '3px 8px', borderRadius: 4, fontWeight: 700, fontSize: 10,
                      background: ep.method === 'GET' ? '#22c55e20' : '#3b82f620',
                      color: ep.method === 'GET' ? '#22c55e' : '#3b82f6',
                    }}>
                      {ep.method}
                    </span>
                    <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-primary)' }}>{ep.path}</code>
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 'auto' }}>{ep.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'cli' && (
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
                💻 CLI & Headless Execution
              </h2>
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 16 }}>
                RefactorIQ can be integrated directly into CI/CD pipelines (GitHub Actions, GitLab CI, Jenkins) using headless agent execution mode.
              </p>
              <div style={{ background: 'var(--bg-base)', padding: 16, borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}># Run CUQA Agent quality analysis directly in terminal</div>
                <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent)', display: 'block', whiteSpace: 'pre-wrap' }}>
                  cd agents/cuqa_agent/src{"\n"}
                  python main.py --cli --repo-dir /path/to/project --threshold 75 --output report.json
                </code>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
