/**
 * CUQAAgentPage.jsx — Full CUQA Agent Dashboard
 * Matches the screenshot: File Explorer | AST Graph | Metrics | Smells | JSON Report
 */

import { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8001';

// ── Node colours by type ───────────────────────────────────────────────────
const NODE_COLOR = {
  CompilationUnit:'#00d4e8', ClassDeclaration:'#00d4e8', ClassOrInterfaceDeclaration:'#00d4e8',
  MethodDeclaration:'#8b5cf6', ConstructorDeclaration:'#8b5cf6',
  FieldDeclaration:'#3b82f6',  ImportDeclaration:'#374151',
  PackageDeclaration:'#374151', Parameter:'#22c55e',
  Module:'#00d4e8', FunctionDef:'#8b5cf6', AsyncFunctionDef:'#8b5cf6',
  ClassDef:'#00d4e8', Import:'#374151', ImportFrom:'#374151',
  Assign:'#3b82f6', Return:'#ef4444', If:'#f59e0b', For:'#f59e0b',
};
const getColor = t => NODE_COLOR[t] || '#1e3a4f';

// ── Simple tree layout (BFS levels) ───────────────────────────────────────
function layoutTree(root, nodeW = 150, nodeH = 44, hGap = 20, vGap = 70) {
  if (!root) return { nodes: [], edges: [] };
  const nodes = [], edges = [];
  let id = 0;

  function assignIds(node, parentId = null) {
    const nid = id++;
    nodes.push({ id: nid, type: node.type || '?', name: node.name || '', line: node.line, parentId, x: 0, y: 0, children: [] });
    (node.children || []).forEach(c => {
      const cid = assignIds(c, nid);
      nodes[nid].children.push(cid);
      edges.push({ from: nid, to: cid });
    });
    return nid;
  }
  assignIds(root);

  // BFS level assignment
  const levels = [];
  const queue = [0];
  const levelOf = { 0: 0 };
  while (queue.length) {
    const cur = queue.shift();
    const lv = levelOf[cur];
    if (!levels[lv]) levels[lv] = [];
    levels[lv].push(cur);
    nodes[cur].children.forEach(c => { levelOf[c] = lv + 1; queue.push(c); });
  }

  // Assign x,y
  levels.forEach((lvNodes, lv) => {
    const totalW = lvNodes.length * nodeW + (lvNodes.length - 1) * hGap;
    lvNodes.forEach((nid, i) => {
      nodes[nid].x = i * (nodeW + hGap) - totalW / 2 + nodeW / 2;
      nodes[nid].y = lv * (nodeH + vGap);
    });
  });

  const height = levels.length * (nodeH + vGap) + 40;
  return { nodes, edges, width: 900, height, nodeW, nodeH };
}

// ── AST SVG Graph ──────────────────────────────────────────────────────────
function ASTGraph({ ast }) {
  const [zoom, setZoom] = useState(1);
  const [pan,  setPan]  = useState({ x: 0, y: 0 });
  const dragging = useRef(false);
  const last     = useRef({ x: 0, y: 0 });

  const layout = layoutTree(ast);
  const { nodes, edges, width, height, nodeW, nodeH } = layout;
  const svgW = width || 900, svgH = height || 300;

  function onMouseDown(e) { dragging.current = true; last.current = { x: e.clientX, y: e.clientY }; }
  function onMouseMove(e) {
    if (!dragging.current) return;
    setPan(p => ({ x: p.x + (e.clientX - last.current.x), y: p.y + (e.clientY - last.current.y) }));
    last.current = { x: e.clientX, y: e.clientY };
  }
  function onMouseUp() { dragging.current = false; }

  const cx = svgW / 2 + 20;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: 'var(--bg-base)', overflow: 'hidden' }}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp}>

      {/* Controls */}
      <div style={{ position: 'absolute', top: 10, right: 10, display: 'flex', gap: 6, zIndex: 10 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.min(z + 0.15, 2))}>+ ZOOM</button>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.max(z - 0.15, 0.3))}>− ZOOM</button>
        <button className="btn btn-ghost btn-sm" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>⟳ RESET</button>
      </div>

      <svg
        width="100%" height="100%"
        viewBox={`0 0 ${svgW} ${svgH + 40}`}
        style={{ cursor: 'grab', userSelect: 'none' }}
      >
        <g transform={`translate(${cx + pan.x}, ${20 + pan.y}) scale(${zoom})`}>
          {/* Edges */}
          {edges.map(({ from, to }, i) => {
            const f = nodes[from], t = nodes[to];
            if (!f || !t) return null;
            return (
              <line key={i}
                x1={f.x} y1={f.y + nodeH}
                x2={t.x} y2={t.y}
                stroke="#1e3a5f" strokeWidth={1.5}
              />
            );
          })}
          {/* Nodes */}
          {nodes.map(n => {
            const c = getColor(n.type);
            const label = n.name ? `${n.type}: ${n.name}` : n.type;
            const isSmell = n.name?.toLowerCase().includes('smell') || (n.line && n.line > 50);
            return (
              <g key={n.id} transform={`translate(${n.x - nodeW / 2}, ${n.y})`}>
                <rect
                  width={nodeW} height={nodeH}
                  rx={5} ry={5}
                  fill={isSmell ? '#2d0a0a' : '#0f1f35'}
                  stroke={isSmell ? '#ef4444' : c}
                  strokeWidth={1.5}
                />
                <text
                  x={nodeW / 2} y={nodeH / 2 - 4}
                  textAnchor="middle"
                  fill={isSmell ? '#f87171' : '#e2eaf4'}
                  fontSize={9.5}
                  fontFamily="'JetBrains Mono', monospace"
                  fontWeight={500}
                >
                  {label.length > 22 ? label.slice(0, 22) + '…' : label}
                </text>
                {n.line && (
                  <text x={nodeW / 2} y={nodeH / 2 + 9} textAnchor="middle" fill="#3d5166" fontSize={9}>
                    L{n.line}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// ── File Tree node ─────────────────────────────────────────────────────────
function TreeNode({ node, depth = 0, onSelect, selected }) {
  const [open, setOpen] = useState(depth < 2);
  const isDir  = node.type === 'directory';
  const isSel  = node.path === selected;
  const icon   = isDir ? (open ? '📂' : '📁') : node.language === 'python' ? '🐍' : node.language === 'java' ? '☕' : '📄';

  return (
    <div>
      <div
        className={`tree-node ${isDir ? 'dir' : ''} ${isSel ? 'selected' : ''}`}
        style={{ paddingLeft: depth * 10 }}
        onClick={() => isDir ? setOpen(v => !v) : onSelect(node)}
      >
        <span style={{ fontSize: 12, flexShrink: 0 }}>{icon}</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 11 }}>{node.name}</span>
      </div>
      {isDir && open && (node.children || []).map((c, i) => (
        <div key={i} className="tree-children" style={{ marginLeft: 8, paddingLeft: 8 }}>
          <TreeNode node={c} depth={depth + 1} onSelect={onSelect} selected={selected} />
        </div>
      ))}
    </div>
  );
}

// ── Main CUQAAgentPage ─────────────────────────────────────────────────────
export default function CUQAAgentPage({ repoLoaded, repoMeta }) {
  const [tree,        setTree]        = useState(null);
  const [selFile,     setSelFile]     = useState(null);
  const [astData,     setAstData]     = useState(null);
  const [report,      setReport]      = useState(null);
  const [loadingAst,  setLoadingAst]  = useState(false);
  const [loadingRep,  setLoadingRep]  = useState(false);
  const [rawJson,     setRawJson]     = useState(false);
  const [err,         setErr]         = useState(null);

  useEffect(() => {
    if (repoLoaded) { fetchTree(); fetchReport(); }
  }, [repoLoaded]);

  useEffect(() => {
    if (selFile) parseAst(selFile.path);
  }, [selFile]);

  async function fetchTree() {
    try {
      const res = await fetch(`${API}/api/project-structure`);
      const d   = await res.json();
      if (res.ok) setTree(d.tree);
    } catch {}
  }

  async function parseAst(path) {
    setLoadingAst(true); setAstData(null); setErr(null);
    try {
      const res = await fetch(`${API}/api/parse-ast`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: path }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail);
      setAstData(d);
    } catch (e) { setErr(e.message); }
    finally { setLoadingAst(false); }
  }

  async function fetchReport() {
    setLoadingRep(true);
    try {
      const res = await fetch(`${API}/api/quality-report`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const d = await res.json();
      if (res.ok) setReport(d.report);
    } catch {}
    finally { setLoadingRep(false); }
  }

  const smells   = report?.files?.flatMap(f => (f.code_smells || []).map(s => ({ ...s, file: f.file }))) ?? [];
  const summary  = report?.summary ?? {};
  const ast      = astData?.parsed?.ast;
  const astSummary = astData?.summary;

  if (!repoLoaded) return (
    <div className="page-container">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">🔍</div>
          <div>
            <div className="page-title">CUQA Agent</div>
            <div className="page-subtitle">Code Understanding &amp; Quality Assessment</div>
          </div>
        </div>
      </div>
      <div className="empty-state" style={{ flex: 1, justifyContent: 'center' }}>
        <span className="empty-icon">📂</span>
        <p>Load a repository from <strong>Repository Input</strong> to begin analysis.</p>
      </div>
    </div>
  );

  return (
    <div className="page-container">

      {/* ── Header ──────────────────────────────────────────── */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">🔍</div>
          <div>
            <div className="page-title">CUQA Agent</div>
            <div className="page-subtitle">
              Code Understanding &amp; Quality Assessment. Performing deep AST analysis,
              identifying technical debt through code smell detection, and establishing the
              structural foundation for autonomous refactoring.
            </div>
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={() => { fetchTree(); fetchReport(); }}>
            ⟳ RUN ANALYSIS
          </button>
          <button className="btn btn-outline" onClick={() => {
            const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
            const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
            a.download = 'cuqa_report.json'; a.click();
          }}>
            ↓ EXPORT DATA
          </button>
        </div>
      </div>

      {/* ── Explorer + AST ──────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16, height: 380 }}>

        {/* File Explorer */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div className="card-header">
            <span className="card-title">📁 File Explorer</span>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '8px 4px' }}>
            {tree
              ? <div className="file-tree"><TreeNode node={tree} depth={0} onSelect={setSelFile} selected={selFile?.path} /></div>
              : <div className="loading-state" style={{ padding: 20 }}><div className="spinner" /></div>
            }
          </div>
        </div>

        {/* AST Visualizer */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div className="card-header">
            <span className="card-title">
              🌲 AST Visualizer
              {selFile && <span style={{ color: 'var(--accent)', marginLeft: 8, textTransform: 'none', fontWeight: 500, fontSize: 11 }}>{selFile.name}</span>}
            </span>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {astSummary && (
                <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  {astSummary.total_nodes} nodes · depth {astSummary.max_depth}
                </span>
              )}
              <button className={`btn btn-sm ${rawJson ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setRawJson(v => !v)}>
                {rawJson ? '🌲 Graph' : '{ } JSON'}
              </button>
            </div>
          </div>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            {loadingAst && <div className="loading-state"><div className="spinner" /><span>Parsing AST…</span></div>}
            {err        && <div className="alert alert-error" style={{ margin: 16 }}>⚠ {err}</div>}
            {!selFile && !loadingAst && (
              <div className="empty-state">
                <span className="empty-icon">🌲</span>
                <p>Click a .py or .java file in the explorer to visualise its AST.</p>
              </div>
            )}
            {ast && !loadingAst && !rawJson && <ASTGraph ast={ast} />}
            {ast && !loadingAst && rawJson && (
              <div style={{ padding: 16, overflowY: 'auto', height: '100%' }}>
                <div className="json-viewer">{JSON.stringify(astData?.parsed, null, 2)}</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Metrics Row ─────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {[
          { label: 'Total LOC',       value: summary.total_lines_of_code?.toLocaleString() ?? '—', sub: 'Lines of code', subClass: '' },
          { label: 'Total Methods',   value: report?.files?.reduce((a,f) => a + (f.metrics?.functions||0), 0) ?? '—', sub: 'Optimised density', subClass: '' },
          { label: 'Avg. Complexity', value: summary.average_quality_score ? (10 - summary.average_quality_score / 10).toFixed(1) : '—', sub: '↑ High cyclomatic stress', subClass: 'warn' },
          { label: 'Maintainability', value: summary.average_quality_score ? Math.round(summary.average_quality_score * 0.35) : '—', sub: '⚠ Action required', subClass: 'danger', suffix: '/100' },
        ].map(({ label, value, sub, subClass, suffix }) => (
          <div className="metric-card" key={label}>
            <div className="metric-label-text">{label}</div>
            <div className="metric-value">{value}{suffix && <span style={{ fontSize: 16, color: 'var(--text-muted)' }}>{suffix}</span>}</div>
            <div className={`metric-sub ${subClass}`}>{sub}</div>
          </div>
        ))}
      </div>

      {/* ── Code Smells Table ────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🦨 Detected Code Smells</span>
          <div style={{ display: 'flex', gap: 8 }}>
            {summary.smell_severity?.high   > 0 && <span className="badge badge-critical">● {summary.smell_severity.high} Critical</span>}
            {summary.smell_severity?.medium > 0 && <span className="badge badge-medium">● {summary.smell_severity.medium} Medium</span>}
          </div>
        </div>
        {loadingRep
          ? <div className="loading-state"><div className="spinner" /></div>
          : smells.length === 0
            ? <div className="empty-state" style={{ padding: 32 }}><span className="empty-icon">✅</span><p>No code smells detected.</p></div>
            : (
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Smell Type</th>
                      <th>Entity</th>
                      <th>Severity</th>
                      <th>Impact</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {smells.slice(0, 20).map((s, i) => (
                      <tr key={i}>
                        <td>
                          <div style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: 12 }}>{s.type}</div>
                          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{s.message?.slice(0, 50)}</div>
                        </td>
                        <td>
                          <div className="entity-name">{s.file}</div>
                          {s.line && <div className="entity-sub">Line {s.line}</div>}
                        </td>
                        <td>
                          <span className={`badge badge-${s.severity === 'high' ? 'critical' : s.severity}`}>
                            {s.severity?.toUpperCase()}
                          </span>
                        </td>
                        <td style={{ fontSize: 12 }}>
                          {s.severity === 'high' ? 'High maintenance risk' : s.severity === 'medium' ? 'Code quality degradation' : 'Minor concern'}
                        </td>
                        <td>
                          <button className="btn btn-ghost btn-sm"
                            onClick={() => { const f = report?.files?.find(r => r.file === s.file); if (f) { /* open file */ } }}>
                            View AST
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
        }
      </div>

      {/* ── Structural Quality Report JSON ───────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">{'{ }'} Structural Quality Report (JSON)</span>
          <button className="btn btn-ghost btn-sm" onClick={() => {
            navigator.clipboard?.writeText(JSON.stringify(report, null, 2));
          }}>
            📋 Copy to Clipboard
          </button>
        </div>
        <div style={{ padding: 20, maxHeight: 400, overflowY: 'auto', background: 'var(--bg-base)' }}>
          {report
            ? <div className="json-viewer">{JSON.stringify(report, null, 2)}</div>
            : <div className="loading-state"><div className="spinner" /></div>
          }
        </div>
      </div>

    </div>
  );
}
