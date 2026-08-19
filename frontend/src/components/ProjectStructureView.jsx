/**
 * ProjectStructureView.jsx
 * ------------------------
 * Renders the file-tree of the loaded/analysed repository.
 * Scoped to CUQA's concern: show what was ingested and let the user
 * click a file to trigger AST parsing.
 *
 * Supports Python (.py), Java (.java), and C (.c, .h) source files.
 */

import { useState, useEffect } from 'react';
import CUQAAgentService from '../services/cuqaAgentService';

const LANG_ICON = { python: '🐍', java: '☕', c: '⚙️', directory: '📁', file: '📄' };

// ── Recursive tree node ─────────────────────────────────────────────────────

function TreeNode({ node, depth = 0, onFileSelect, selectedPath }) {
  const [expanded, setExpanded] = useState(depth < 2);

  const isDir = node.type === 'directory';
  const isSelected = node.path === selectedPath;

  const icon = isDir
    ? (expanded ? '📂' : '📁')
    : node.language === 'python'
      ? '🐍'
      : node.language === 'java'
        ? '☕'
        : node.language === 'c'
          ? (node.name?.endsWith('.h') ? '🔩' : '⚙️')
          : '📄';

  const langClass = !isDir
    ? (node.language === 'python' ? 'file-py'
      : node.language === 'java' ? 'file-java'
      : node.language === 'c' ? 'file-c'
      : '')
    : 'dir';

  function handleClick(e) {
    e.stopPropagation();
    if (isDir) {
      setExpanded(v => !v);
    } else {
      onFileSelect?.(node);
    }
  }

  return (
    <div>
      <div
        className={`tree-node ${langClass} ${isSelected ? 'selected' : ''}`}
        style={{ paddingLeft: `${depth * 12}px` }}
        onClick={handleClick}
        title={node.path}
      >
        <span className="node-icon">{icon}</span>
        <span className="node-label">{node.name}</span>
        {node.language && (
          <span className={`pill pill-${node.language}`} style={{ marginLeft: 6, fontSize: 10 }}>
            {node.language}
          </span>
        )}
      </div>

      {isDir && expanded && node.children?.length > 0 && (
        <div className="tree-children">
          {node.children.map((child, i) => (
            <TreeNode
              key={i}
              node={child}
              depth={depth + 1}
              onFileSelect={onFileSelect}
              selectedPath={selectedPath}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export default function ProjectStructureView({ repoLoaded, onFileSelect }) {
  const [tree, setTree] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null);

  useEffect(() => {
    if (repoLoaded) fetchStructure();
  }, [repoLoaded]);

  async function fetchStructure() {
    setLoading(true);
    setError(null);
    try {
      const data = await CUQAAgentService.getProjectStructure();
      setTree(data.tree);
      setMeta({ repo_name: data.repo_name, source: data.source, total: data.total_source_files });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleFileSelect(node) {
    setSelectedPath(node.path);
    onFileSelect?.(node);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (!repoLoaded) {
    return (
      <div className="empty-state">
        <span className="empty-icon">🗂️</span>
        <p>Load a repository using the panel above to explore its structure.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="loading-state">
        <div className="spinner" />
        <span>Scanning repository…</span>
      </div>
    );
  }

  if (error) {
    return <div className="alert alert-error">⚠ {error}</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Meta bar */}
      {meta && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
            {meta.repo_name}
          </span>
          <span className="pill pill-accent">
            {meta.source === 'github' ? '🐙 GitHub' : '📦 ZIP'}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {meta.total} source file{meta.total !== 1 ? 's' : ''} detected
          </span>
          <button
            className="btn btn-ghost"
            style={{ marginLeft: 'auto', padding: '4px 10px' }}
            onClick={fetchStructure}
          >
            ↺ Refresh
          </button>
        </div>
      )}

      {/* Instructions */}
      {selectedPath && (
        <div className="alert alert-info" style={{ padding: '8px 14px' }}>
          📌 Selected: <code style={{ fontFamily: 'var(--font-mono)' }}>{selectedPath}</code>
          &nbsp;— switch to <strong>AST Visualization</strong> tab to parse it.
        </div>
      )}

      {/* Tree */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">📂 Repository File Tree</span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Click a file to select it for AST analysis
          </span>
        </div>
        <div className="card-body" style={{ maxHeight: '55vh', overflowY: 'auto' }}>
          {tree ? (
            <div className="file-tree">
              <TreeNode
                node={tree}
                depth={0}
                onFileSelect={handleFileSelect}
                selectedPath={selectedPath}
              />
            </div>
          ) : (
            <div className="empty-state" style={{ padding: 32 }}>
              <p>No structure data available.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
