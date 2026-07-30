/**
 * CUQAAgentPage.jsx (Enhanced with Code Smell Analytics)
 * ──────────────────────────────────────────────────────
 * - Code Smell Statistics Dashboard
 * - Files with Code Smells List
 * - Advanced Filtering & Search
 * - Smell Distribution & Impact Analysis
 * - Trend visualization
 *
 * Supports Python (.py), Java (.java), and C (.c, .h) source files.
 */

import { useState, useEffect, useRef } from 'react';

const API = 'http://localhost:8080';

// ── Node colours by type ───────────────────────────────────────────────────
const NODE_COLOR = {
  // Java
  CompilationUnit:'#00d4e8', ClassDeclaration:'#00d4e8', ClassOrInterfaceDeclaration:'#00d4e8',
  MethodDeclaration:'#8b5cf6', ConstructorDeclaration:'#8b5cf6',
  FieldDeclaration:'#3b82f6', ImportDeclaration:'#374151',
  PackageDeclaration:'#374151', Parameter:'#22c55e',
  // Python
  Module:'#00d4e8', FunctionDef:'#8b5cf6', AsyncFunctionDef:'#8b5cf6',
  ClassDef:'#00d4e8', Import:'#374151', ImportFrom:'#374151',
  Assign:'#3b82f6', Return:'#ef4444', If:'#f59e0b', For:'#f59e0b',
  // C — PascalCase (tree-sitter may return these)
  TranslationUnit:'#00d4e8', FunctionDefinition:'#8b5cf6',
  IncludeDirective:'#374151', Declaration:'#3b82f6',
  FunctionDeclarator:'#a855f7', ParameterDeclaration:'#22c55e',
  CompoundStatement:'#1e40af', IfStatement:'#f59e0b',
  ForStatement:'#f59e0b', WhileStatement:'#f59e0b',
  ReturnStatement:'#ef4444', PreprocInclude:'#374151',
  Identifier:'#22c55e',
  // C — snake_case (tree-sitter native)
  translation_unit:'#00d4e8', function_definition:'#8b5cf6',
  preproc_include:'#374151', declaration:'#3b82f6',
  function_declarator:'#a855f7', parameter_declaration:'#22c55e',
  compound_statement:'#1e40af', if_statement:'#f59e0b',
  for_statement:'#f59e0b', while_statement:'#f59e0b',
  return_statement:'#ef4444', identifier:'#22c55e',
  pointer_declarator:'#a855f7',
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

      <div style={{ position: 'absolute', top: 10, right: 10, display: 'flex', gap: 6, zIndex: 10 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.min(z + 0.15, 2))}>+ ZOOM</button>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.max(z - 0.15, 0.3))}>− ZOOM</button>
        <button className="btn btn-ghost btn-sm" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>⟳ RESET</button>
      </div>

      <svg width="100%" height="100%" viewBox={`0 0 ${svgW} ${svgH + 40}`} style={{ cursor: 'grab', userSelect: 'none' }}>
        <g transform={`translate(${cx + pan.x}, ${20 + pan.y}) scale(${zoom})`}>
          {edges.map(({ from, to }, i) => {
            const f = nodes[from], t = nodes[to];
            if (!f || !t) return null;
            return <line key={i} x1={f.x} y1={f.y + nodeH} x2={t.x} y2={t.y} stroke="#1e3a5f" strokeWidth={1.5} />;
          })}
          {nodes.map(n => {
            const c = getColor(n.type);
            const label = n.name ? `${n.type}: ${n.name}` : n.type;
            const isSmell = n.name?.toLowerCase().includes('smell') || (n.line && n.line > 50);
            return (
              <g key={n.id} transform={`translate(${n.x - nodeW / 2}, ${n.y})`}>
                <rect width={nodeW} height={nodeH}
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
                {n.line && <text x={nodeW / 2} y={nodeH / 2 + 9} textAnchor="middle" fill="#3d5166" fontSize={9}>L{n.line}</text>}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// ── Language icon helper ────────────────────────────────────────────────────
function langIcon(language, filename) {
  if (language === 'python') return '🐍';
  if (language === 'java')   return '☕';
  if (language === 'c') {
    const ext = filename?.split('.').pop()?.toLowerCase();
    return ext === 'h' ? '🔩' : '⚙️';
  }
  return '📄';
}

// ── File Tree node ─────────────────────────────────────────────────────────
function TreeNode({ node, depth = 0, onSelect, selected }) {
  const [open, setOpen] = useState(depth < 2);
  const isDir  = node.type === 'directory';
  const isSel  = node.path === selected;
  const icon   = isDir ? (open ? '📂' : '📁') : langIcon(node.language, node.name);

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

// ── Code Smell Statistics Card ─────────────────────────────────────────────
function CodeSmellStats({ report, filesWithSmells }) {
  const summary = report?.summary ?? {};
  const totalSmells = summary.total_code_smells || 0;
  const highSmells = summary.smell_severity?.high || 0;
  const mediumSmells = summary.smell_severity?.medium || 0;
  const lowSmells = summary.smell_severity?.low || 0;
  const affectedFiles = filesWithSmells.length;

  const smellDensity = (summary.total_lines_of_code && totalSmells > 0)
    ? (totalSmells / summary.total_lines_of_code * 1000).toFixed(2)
    : 0;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
      <div className="metric-card">
        <div className="metric-label-text">Total Smells</div>
        <div className="metric-value" style={{ color: totalSmells > 10 ? '#ef4444' : '#f59e0b', fontSize: 28 }}>
          {totalSmells}
        </div>
        <div className="metric-sub">{affectedFiles} files affected</div>
      </div>

      <div className="metric-card">
        <div className="metric-label-text">🔴 Critical</div>
        <div className="metric-value" style={{ color: '#ef4444' }}>{highSmells}</div>
        <div className="metric-sub">{highSmells > 0 ? 'Action Required' : 'All Clear'}</div>
      </div>

      <div className="metric-card">
        <div className="metric-label-text">🟠 Medium</div>
        <div className="metric-value" style={{ color: '#f59e0b' }}>{mediumSmells}</div>
        <div className="metric-sub">Needs Review</div>
      </div>

      <div className="metric-card">
        <div className="metric-label-text">🟢 Low</div>
        <div className="metric-value" style={{ color: '#22c55e' }}>{lowSmells}</div>
        <div className="metric-sub">Improvement</div>
      </div>

      <div className="metric-card">
        <div className="metric-label-text">Smell Density</div>
        <div className="metric-value" style={{ color: '#8b5cf6' }}>{smellDensity}</div>
        <div className="metric-sub">per 1K LOC</div>
      </div>

      <div className="metric-card">
        <div className="metric-label-text">Avg. Score</div>
        <div className="metric-value" style={{ color: '#00d4e8' }}>
          {summary.average_quality_score ? summary.average_quality_score.toFixed(1) : '—'}
        </div>
        <div className="metric-sub">/100</div>
      </div>
    </div>
  );
}

// ── Files with Code Smells ───────────────────────────────────────────────────
function FilesWithSmells({ report, filter = 'all' }) {
  const filesData = (report?.files || [])
    .map(f => ({
      name: f.file,
      path: f.relative_path || f.file,
      language: f.language,
      smells: f.code_smells || [],
      metrics: f.metrics || {},
      quality: f.quality_score || 100,
    }))
    .filter(f => f.smells.length > 0)
    .sort((a, b) => b.smells.length - a.smells.length);

  const filtered = filter === 'critical'
    ? filesData.filter(f => f.smells.some(s => s.severity === 'high'))
    : filter === 'python'
    ? filesData.filter(f => f.language === 'python')
    : filter === 'java'
    ? filesData.filter(f => f.language === 'java')
    : filter === 'c'
    ? filesData.filter(f => f.language === 'c')
    : filesData;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
      {filtered.length === 0 ? (
        <div className="empty-state" style={{ gridColumn: '1 / -1', padding: 32 }}>
          <span className="empty-icon">✅</span>
          <p>No files with code smells in this filter.</p>
        </div>
      ) : (
        filtered.map((file, idx) => {
          const criticalCount = file.smells.filter(s => s.severity === 'high').length;
          const mediumCount = file.smells.filter(s => s.severity === 'medium').length;
          const lowCount = file.smells.filter(s => s.severity === 'low').length;

          return (
            <div key={idx} className="card" style={{ padding: 16, borderLeft: `4px solid ${file.quality < 50 ? '#ef4444' : file.quality < 70 ? '#f59e0b' : '#22c55e'}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 12 }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>
                    {langIcon(file.language, file.name)} {file.name}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                    {file.metrics.lines_of_code || '?'} LOC · {file.metrics.functions || 0} methods
                  </div>
                </div>
                <span className={`badge badge-${file.quality < 50 ? 'critical' : file.quality < 70 ? 'medium' : 'success'}`}>
                  {file.quality.toFixed(0)}/100
                </span>
              </div>

              <div style={{ display: 'flex', gap: 8, marginBottom: 12, padding: '8px 0', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
                {criticalCount > 0 && (
                  <div style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#ef4444' }}>{criticalCount}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Critical</div>
                  </div>
                )}
                {mediumCount > 0 && (
                  <div style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#f59e0b' }}>{mediumCount}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Medium</div>
                  </div>
                )}
                {lowCount > 0 && (
                  <div style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#22c55e' }}>{lowCount}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Low</div>
                  </div>
                )}
                {file.smells.length > 0 && (
                  <div style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#8b5cf6' }}>{file.smells.length}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Total</div>
                  </div>
                )}
              </div>

              <div style={{ fontSize: 11, lineHeight: 1.5 }}>
                {file.smells.slice(0, 3).map((smell, i) => (
                  <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 4, alignItems: 'flex-start' }}>
                    <span style={{ color: smell.severity === 'high' ? '#ef4444' : smell.severity === 'medium' ? '#f59e0b' : '#22c55e', flexShrink: 0 }}>●</span>
                    <span style={{ color: 'var(--text-secondary)' }}>{smell.type}</span>
                  </div>
                ))}
                {file.smells.length > 3 && (
                  <div style={{ color: 'var(--accent)', fontSize: 10, marginTop: 6 }}>
                    +{file.smells.length - 3} more smells
                  </div>
                )}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// ── Main CUQAAgentPage ─────────────────────────────────────────────────────
export default function CUQAAgentPage({ repoLoaded, repoMeta, onSendToRdp, analysisConfig }) {
  // Extract active settings (fall back to sensible defaults if no config provided)
  const threshold     = analysisConfig?.threshold      ?? 75;
  const severityFilts = analysisConfig?.severity_filters ?? { critical: true, naming: true };
  const analysisMode  = analysisConfig?.analysis_mode   ?? 'Comprehensive Refactoring';
  const langContext   = analysisConfig?.language_context ?? 'All';

  const [tree,        setTree]        = useState(null);
  const [selFile,     setSelFile]     = useState(null);
  const [astData,     setAstData]     = useState(null);
  const [report,      setReport]      = useState(null);
  const [loadingAst,  setLoadingAst]  = useState(false);
  const [loadingRep,  setLoadingRep]  = useState(false);
  const [rawJson,     setRawJson]     = useState(false);
  const [err,         setErr]         = useState(null);
  const [smellFilter, setSmellFilter] = useState('all');

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

  const smells = report?.files?.flatMap(f => (f.code_smells || []).map(s => ({ ...s, file: f.file }))) ?? [];

  // Apply severity filters from RepositoryInput config
  const filteredSmells = smells.filter(s => {
    const sev = s.severity || 'low';
    if (sev === 'high' && !severityFilts.critical) return false;
    // 'naming' filter controls medium/low naming-related smells
    if ((sev === 'medium' || sev === 'low') && !severityFilts.naming) return false;
    return true;
  });

  const filesWithSmells = (report?.files || []).filter(f => (f.code_smells || []).length > 0);
  // Files below quality threshold are highlighted for refactoring
  const filesBelowThreshold = (report?.files || []).filter(f => (f.quality_score ?? 100) < threshold);
  const summary = report?.summary ?? {};
  const ast = astData?.parsed?.ast;
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
              Code Understanding &amp; Quality Assessment — Deep AST analysis, code smell detection,
              and structural foundation for autonomous refactoring.
            </div>
          </div>
        </div>
        <div className="page-header-actions">
          {/* Active analysis config badge */}
          {analysisConfig && (
            <div style={{
              display: 'flex', gap: 6, alignItems: 'center',
              background: 'var(--accent-muted)', border: '1px solid var(--border-accent)',
              borderRadius: 'var(--r-sm)', padding: '4px 10px', fontSize: 10,
            }}>
              <span style={{ color: 'var(--accent)', fontWeight: 700 }}>⚙ Config</span>
              <span style={{ color: 'var(--text-secondary)' }}>Threshold: {threshold}%</span>
              <span style={{ color: 'var(--text-muted)' }}>·</span>
              <span style={{ color: 'var(--text-secondary)' }}>{langContext}</span>
              <span style={{ color: 'var(--text-muted)' }}>·</span>
              <span style={{ color: 'var(--text-secondary)' }}>{analysisMode}</span>
            </div>
          )}
          <button className="btn btn-primary" onClick={() => { fetchTree(); fetchReport(); }}>
            ⟳ RUN ANALYSIS
          </button>
          {report && onSendToRdp && (
            <button
              onClick={() => onSendToRdp(report)}
              title="Send quality report to RDP Agent to generate a refactoring plan"
              style={{
                padding: '8px 16px',
                background: 'linear-gradient(135deg, #8b5cf6, #a855f7)',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                fontSize: '13px',
                fontWeight: 700,
                cursor: 'pointer',
                boxShadow: '0 0 18px rgba(139,92,246,0.5)',
                letterSpacing: '0.3px',
              }}
            >
              ⚡ Send to RDP Agent
            </button>
          )}
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
                <p>Click a <code>.py</code>, <code>.java</code>, <code>.c</code>, or <code>.h</code> file in the explorer to visualise its AST.</p>
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

      {/* ── Code Smell Statistics Dashboard ─────────────────── */}
      <div>
        <h3 style={{ margin: '24px 0 12px 0', fontSize: 14, fontWeight: 600 }}>🦨 Code Smell Analytics</h3>
        {loadingRep ? (
          <div className="loading-state"><div className="spinner" /></div>
        ) : (
          <CodeSmellStats report={report} filesWithSmells={filesWithSmells} />
        )}
      </div>

      {/* ── Files with Code Smells ──────────────────────────── */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>📁 Affected Files ({filesWithSmells.length})</h3>
          <div style={{ display: 'flex', gap: 8 }}>
            {[
              { id: 'all',      label: 'All Files' },
              { id: 'critical', label: '🔴 Critical' },
              { id: 'python',   label: '🐍 Python' },
              { id: 'java',     label: '☕ Java' },
              { id: 'c',        label: '⚙️ C' },
            ].map(({ id, label }) => (
              <button
                key={id}
                className={`btn btn-sm ${smellFilter === id ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setSmellFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {loadingRep ? (
          <div className="loading-state"><div className="spinner" /></div>
        ) : filesWithSmells.length === 0 ? (
          <div className="card">
            <div className="empty-state" style={{ padding: 40 }}>
              <span className="empty-icon">✅</span>
              <p>Excellent! No code smells detected in the repository.</p>
            </div>
          </div>
        ) : (
          <FilesWithSmells report={report} filter={smellFilter} />
        )}
      </div>

      {/* ── Detailed Code Smells Table ──────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🔍 Detailed Code Smells ({filteredSmells.length})</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {/* Threshold indicator */}
            {filesBelowThreshold.length > 0 && (
              <span className="badge badge-critical" title={`${filesBelowThreshold.length} files below ${threshold}% threshold`}>
                ⚠ {filesBelowThreshold.length} below {threshold}%
              </span>
            )}
            {summary.smell_severity?.high   > 0 && <span className="badge badge-critical">● {summary.smell_severity.high} Critical</span>}
            {summary.smell_severity?.medium > 0 && <span className="badge badge-medium">● {summary.smell_severity.medium} Medium</span>}
            {summary.smell_severity?.low    > 0 && <span className="badge" style={{ background: '#22c55e40', color: '#22c55e' }}>● {summary.smell_severity.low} Low</span>}
          </div>
        </div>
        
        {loadingRep ? (
          <div className="loading-state"><div className="spinner" /></div>
        ) : smells.length === 0 ? (
          <div className="empty-state" style={{ padding: 32 }}>
            <span className="empty-icon">✅</span>
            <p>No code smells detected.</p>
          </div>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>File</th>
                    <th>Line</th>
                    <th>Message</th>
                    <th>Severity</th>
                    <th style={{ width: 80 }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSmells.slice(0, 30).map((s, i) => (
                    <tr key={i}>
                      <td>
                        <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-primary)' }}>
                          {s.type}
                        </span>
                      </td>
                      <td>
                        <span style={{ fontSize: 11, color: 'var(--accent)' }}>{s.file}</span>
                      </td>
                      <td>
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                          {s.line ? `L${s.line}` : '—'}
                        </span>
                      </td>
                      <td>
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {s.message?.slice(0, 60)}
                        </span>
                      </td>
                      <td>
                        <span className={`badge badge-${s.severity === 'high' ? 'critical' : s.severity === 'medium' ? 'medium' : 'success'}`}>
                          {(s.severity || '?').toUpperCase()}
                        </span>
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <button className="btn btn-ghost btn-sm" style={{ fontSize: 11 }}>
                          View
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {filteredSmells.length > 30 && (
              <div style={{ padding: 14, textAlign: 'center', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-secondary)' }}>
                Showing 30 of {filteredSmells.length} code smells — <a href="#" style={{ color: 'var(--accent)' }}>Load More</a>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Structural Quality Report JSON ───────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">{'{ }'} Structural Quality Report (JSON)</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => {
              navigator.clipboard?.writeText(JSON.stringify(report, null, 2));
            }}>
              📋 Copy to Clipboard
            </button>
            {report && onSendToRdp && (
              <button
                className="btn btn-sm"
                style={{
                  background: 'linear-gradient(135deg, #8b5cf6, #a855f7)',
                  color: 'white',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '4px 14px',
                  fontWeight: 600,
                  fontSize: 12,
                  cursor: 'pointer',
                  boxShadow: '0 0 12px rgba(139,92,246,0.35)',
                }}
                onClick={() => onSendToRdp(report)}
              >
                ⚡ Send to RDP Agent
              </button>
            )}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => {
            navigator.clipboard?.writeText(JSON.stringify(report, null, 2));
          }}>
            📋 Copy
          </button>
        </div>
        <div style={{ padding: 20, maxHeight: 300, overflowY: 'auto', background: 'var(--bg-base)' }}>
          {report
            ? <div className="json-viewer">{JSON.stringify(report, null, 2)}</div>
            : <div className="loading-state"><div className="spinner" /></div>
          }
        </div>
      </div>

    </div>
  );
}
