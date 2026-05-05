/**
 * ASTVisualization.jsx
 * --------------------
 * Parses a selected source file via the CUQA backend and renders
 * the returned AST as an interactive, collapsible node tree.
 *
 * Supports Python and Java files.
 */

import { useState, useEffect } from 'react';

const API = 'http://localhost:8001';

// ── Colour mapping per node type ────────────────────────────────────────────

const NODE_COLORS = {
  // Python
  Module:             '#d2a8ff',
  ClassDef:           '#39d0d8',
  FunctionDef:        '#ffa94d',
  AsyncFunctionDef:   '#ffa94d',
  Return:             '#ff6b6b',
  Assign:             '#74c0fc',
  AugAssign:          '#74c0fc',
  AnnAssign:          '#74c0fc',
  Import:             '#a5d6ff',
  ImportFrom:         '#a5d6ff',
  Call:               '#69db7c',
  If:                 '#e8c468',
  For:                '#e8c468',
  While:              '#e8c468',
  Try:                '#ffb3c1',
  ExceptHandler:      '#ff9a9a',
  // Java
  CompilationUnit:    '#d2a8ff',
  ClassDeclaration:   '#39d0d8',
  MethodDeclaration:  '#ffa94d',
  ConstructorDeclaration: '#ffa94d',
  FieldDeclaration:   '#74c0fc',
  ImportDeclaration:  '#a5d6ff',
  Parameter:          '#69db7c',
  InterfaceDeclaration: '#e8c468',
};

const DEFAULT_COLOR = '#8b949e';

// ── Single AST node row ─────────────────────────────────────────────────────

function ASTNode({ node, depth = 0 }) {
  const [expanded, setExpanded] = useState(depth < 3);

  if (!node || typeof node !== 'object') return null;

  const hasChildren = Array.isArray(node.children) && node.children.length > 0;
  const typeColor = NODE_COLORS[node.type] || DEFAULT_COLOR;

  return (
    <div>
      <div
        className="ast-node-row"
        style={{ paddingLeft: `${depth * 18}px` }}
        onClick={() => hasChildren && setExpanded(v => !v)}
      >
        {/* Toggle */}
        <span className="ast-toggle">
          {hasChildren ? (expanded ? '▾' : '▸') : '·'}
        </span>

        {/* Type chip */}
        <span
          className="ast-node-type"
          style={{ color: typeColor, backgroundColor: `${typeColor}18`, padding: '0 5px', borderRadius: 3 }}
        >
          {node.type}
        </span>

        {/* Name */}
        {node.name && (
          <span className="ast-node-name">{node.name}</span>
        )}

        {/* Param type for Java parameters */}
        {node.paramType && (
          <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>: {node.paramType}</span>
        )}

        {/* Line number */}
        {node.line && (
          <span className="ast-node-line">L{node.line}</span>
        )}

        {/* Children count badge */}
        {hasChildren && (
          <span style={{
            marginLeft: 'auto',
            fontSize: 10,
            color: 'var(--text-muted)',
            background: 'var(--bg-elevated)',
            padding: '1px 5px',
            borderRadius: 10,
          }}>
            {node.children.length}
          </span>
        )}
      </div>

      {/* Children */}
      {expanded && hasChildren && (
        <div className="ast-children">
          {node.children.map((child, i) => (
            <ASTNode key={child.id || i} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Summary stats bar ───────────────────────────────────────────────────────

function ASTSummary({ summary }) {
  if (!summary) return null;
  const { total_nodes, max_depth, node_type_counts, language } = summary;

  const topTypes = Object.entries(node_type_counts || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div className="metrics-grid" style={{ marginBottom: 16 }}>
      <div className="metric-card">
        <div className="metric-value">{total_nodes ?? '—'}</div>
        <div className="metric-label">Total Nodes</div>
      </div>
      <div className="metric-card">
        <div className="metric-value">{max_depth ?? '—'}</div>
        <div className="metric-label">Tree Depth</div>
      </div>
      <div className="metric-card">
        <div className="metric-value">
          {language === 'python' ? '🐍' : language === 'java' ? '☕' : '?'}
        </div>
        <div className="metric-label">{language}</div>
      </div>
      {topTypes.map(([type, count]) => (
        <div className="metric-card" key={type}>
          <div className="metric-value" style={{ fontSize: 18, color: NODE_COLORS[type] || DEFAULT_COLOR }}>
            {count}
          </div>
          <div className="metric-label">{type}</div>
        </div>
      ))}
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function ASTVisualization({ selectedFile, repoLoaded }) {
  const [parsedData, setParsedData] = useState(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState(null);
  const [rawJson, setRawJson]       = useState(false);

  // Auto-parse when selectedFile changes
  useEffect(() => {
    if (selectedFile) parseFile(selectedFile.path);
  }, [selectedFile]);

  async function parseFile(filePath) {
    setLoading(true);
    setError(null);
    setParsedData(null);

    try {
      const res = await fetch(`${API}/api/parse-ast`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to parse file.');
      setParsedData(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ── Empty / Loading / Error states ───────────────────────────────────────

  if (!repoLoaded) {
    return (
      <div className="empty-state">
        <span className="empty-icon">🌲</span>
        <p>Load a repository first, then select a Python or Java file to visualise its AST.</p>
      </div>
    );
  }

  if (!selectedFile) {
    return (
      <div className="empty-state">
        <span className="empty-icon">👈</span>
        <p>
          Select a <span style={{ color: '#79c0ff' }}>.py</span> or{' '}
          <span style={{ color: '#ffa94d' }}>.java</span> file from the{' '}
          <strong>Project Structure</strong> tab.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="loading-state">
        <div className="spinner" />
        <span>Parsing AST for <code style={{ fontFamily: 'var(--font-mono)' }}>{selectedFile.name}</code>…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="alert alert-error">⚠ {error}</div>
        <button className="btn btn-ghost" onClick={() => parseFile(selectedFile.path)}>
          ↺ Retry
        </button>
      </div>
    );
  }

  const ast    = parsedData?.parsed?.ast;
  const summary = parsedData?.summary;
  const parseError = parsedData?.parsed?.error;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Header bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--accent-primary)' }}>
          {selectedFile.name}
        </span>
        <span className={`pill pill-${selectedFile.language}`}>{selectedFile.language}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button
            className={`btn ${rawJson ? 'btn-primary' : 'btn-ghost'}`}
            style={{ padding: '4px 12px' }}
            onClick={() => setRawJson(v => !v)}
          >
            {rawJson ? '🌲 Tree View' : '{ } Raw JSON'}
          </button>
          <button
            className="btn btn-ghost"
            style={{ padding: '4px 12px' }}
            onClick={() => parseFile(selectedFile.path)}
          >
            ↺ Re-parse
          </button>
        </div>
      </div>

      {/* Parse error inside response */}
      {parseError && (
        <div className="alert alert-error">⚠ {parseError}</div>
      )}

      {/* Summary metrics */}
      {summary && <ASTSummary summary={summary} />}

      {/* AST tree / raw JSON */}
      {ast && Object.keys(ast).length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">
              {rawJson ? '{ } AST JSON' : '🌲 AST Tree'}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              {rawJson ? 'Raw output sent to RDP Agent' : 'Click nodes to expand / collapse'}
            </span>
          </div>
          <div className="card-body" style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            {rawJson ? (
              <div className="json-viewer">
                {JSON.stringify(parsedData?.parsed, null, 2)}
              </div>
            ) : (
              <div className="ast-tree">
                <ASTNode node={ast} depth={0} />
              </div>
            )}
          </div>
        </div>
      )}

      {(!ast || Object.keys(ast).length === 0) && !parseError && (
        <div className="alert alert-info">
          AST is empty — the file may have no parseable content.
        </div>
      )}
    </div>
  );
}
