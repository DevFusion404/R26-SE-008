/**
 * DependencyOverview.jsx
 * ----------------------
 * Static Module Dependency View — renders an SVG graph of
 * inter-file import relationships derived from static analysis.
 *
 * Clearly labelled as STATIC (not runtime execution flow).
 * Capped at 50 nodes from the backend to stay renderable.
 */

import { useState, useRef } from 'react';

const NODE_TYPE_CFG = {
  entry_point:   { color: '#22c55e', stroke: '#22c55e', glow: 'rgba(34,197,94,0.4)',  label: '🚀' },
  hub:           { color: '#f59e0b', stroke: '#f59e0b', glow: 'rgba(245,158,11,0.4)', label: '⭐' },
  high_fan_out:  { color: '#3b82f6', stroke: '#3b82f6', glow: 'rgba(59,130,246,0.4)', label: '↗' },
  module:        { color: '#00d4e8', stroke: '#00d4e8', glow: 'rgba(0,212,232,0.25)', label: '◆' },
};
const DEFAULT_NODE = { color: '#4b6a8a', stroke: '#4b6a8a', glow: 'rgba(75,106,138,0.2)', label: '·' };

const NODE_W = 140, NODE_H = 40, H_GAP = 30, V_GAP = 80;

function layoutNodes(nodes, edges) {
  if (!nodes || nodes.length === 0) return { positioned: [], edgeCoords: [] };

  // Topological-ish sort: entry points first, then by fan_in desc
  const ep = nodes.filter(n => n.type === 'entry_point');
  const hubs = nodes.filter(n => n.type === 'hub');
  const rest = nodes.filter(n => n.type !== 'entry_point' && n.type !== 'hub');

  const sorted = [...ep, ...hubs, ...rest];
  const COLS = Math.min(5, Math.ceil(Math.sqrt(sorted.length)));

  const positioned = sorted.map((n, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    const totalInRow = Math.min(COLS, sorted.length - row * COLS);
    const rowWidth = totalInRow * NODE_W + (totalInRow - 1) * H_GAP;
    return {
      ...n,
      x: col * (NODE_W + H_GAP) - rowWidth / 2 + NODE_W / 2 + 480,
      y: row * (NODE_H + V_GAP) + 30,
    };
  });

  const posById = {};
  positioned.forEach(n => { posById[n.id] = n; });

  const edgeCoords = (edges || []).map(e => {
    const s = posById[e.source], t = posById[e.target];
    if (!s || !t) return null;
    return { x1: s.x, y1: s.y + NODE_H / 2, x2: t.x - NODE_W / 2, y2: t.y + NODE_H / 2, id: `${e.source}-${e.target}` };
  }).filter(Boolean);

  return { positioned, edgeCoords };
}

export default function DependencyOverview({ graph = {}, summary = {} }) {
  const { nodes = [], edges = [] } = graph;
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragging = useRef(false);
  const last = useRef({ x: 0, y: 0 });
  const [hoveredNode, setHoveredNode] = useState(null);

  const { local_relationships = 0, external_dependencies = 0, high_fan_in = [], high_fan_out = [] } = summary;

  if (nodes.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--text-muted)', fontSize: 12 }}>
        <span style={{ fontSize: 24, display: 'block', marginBottom: 8 }}>🕸</span>
        No local dependency relationships detected.
        <div style={{ fontSize: 10, marginTop: 4, fontStyle: 'italic' }}>
          This may indicate isolated files or a repository where imports could not be resolved.
        </div>
      </div>
    );
  }

  const { positioned, edgeCoords } = layoutNodes(nodes, edges);
  const ROWS = Math.ceil(nodes.length / Math.min(5, Math.ceil(Math.sqrt(nodes.length))));
  const svgH = ROWS * (NODE_H + V_GAP) + 80;
  const svgW = 960;

  function onMouseDown(e) { dragging.current = true; last.current = { x: e.clientX, y: e.clientY }; }
  function onMouseMove(e) {
    if (!dragging.current) return;
    setPan(p => ({ x: p.x + (e.clientX - last.current.x), y: p.y + (e.clientY - last.current.y) }));
    last.current = { x: e.clientX, y: e.clientY };
  }
  function onMouseUp() { dragging.current = false; }

  return (
    <div>
      {/* Disclaimer */}
      <div style={{
        fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic',
        marginBottom: 10, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8,
      }}>
        <span>🔗 Static Module Dependency View — derived from import/include statements, not runtime execution.</span>
        <div style={{ display: 'flex', gap: 12 }}>
          {local_relationships > 0 && (
            <span>{local_relationships} local relationship{local_relationships > 1 ? 's' : ''}</span>
          )}
          {external_dependencies > 0 && (
            <span>{external_dependencies} external imports</span>
          )}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 10, flexWrap: 'wrap' }}>
        {Object.entries(NODE_TYPE_CFG).map(([type, cfg]) => (
          <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--text-secondary)' }}>
            <div style={{
              width: 10, height: 10, borderRadius: 2,
              background: cfg.color, opacity: 0.8,
            }} />
            <span style={{ textTransform: 'capitalize' }}>{type.replace('_', ' ')}</span>
          </div>
        ))}
      </div>

      {/* Zoom controls */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.min(z + 0.2, 2.5))}>+ Zoom</button>
        <button className="btn btn-ghost btn-sm" onClick={() => setZoom(z => Math.max(z - 0.2, 0.3))}>− Zoom</button>
        <button className="btn btn-ghost btn-sm" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>⟳ Reset</button>
        <span style={{ fontSize: 10, color: 'var(--text-muted)', alignSelf: 'center', marginLeft: 6 }}>
          {nodes.length} node{nodes.length > 1 ? 's' : ''} · Drag to pan
        </span>
      </div>

      {/* SVG Graph */}
      <div
        style={{
          width: '100%', height: Math.min(svgH, 420),
          background: 'var(--bg-base)', borderRadius: 8,
          border: '1px solid var(--border)', overflow: 'hidden',
          cursor: 'grab',
        }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <svg width="100%" height="100%" viewBox={`0 0 ${svgW} ${Math.min(svgH, 420)}`}
          style={{ userSelect: 'none' }}>
          <g transform={`translate(${pan.x - 350},${pan.y}) scale(${zoom})`}>

            {/* Edges */}
            <defs>
              <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#1e3a5f" />
              </marker>
            </defs>
            {edgeCoords.map(e => (
              <line
                key={e.id}
                x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                stroke="#1e3a5f"
                strokeWidth={1.5}
                markerEnd="url(#arrowhead)"
                opacity={0.7}
              />
            ))}

            {/* Nodes */}
            {positioned.map(n => {
              const cfg = NODE_TYPE_CFG[n.type] || DEFAULT_NODE;
              const isHovered = hoveredNode === n.id;
              const label = n.label || n.id.split('/').pop();
              const truncated = label.length > 18 ? label.slice(0, 17) + '…' : label;

              return (
                <g
                  key={n.id}
                  transform={`translate(${n.x - NODE_W / 2}, ${n.y})`}
                  onMouseEnter={() => setHoveredNode(n.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  style={{ cursor: 'default' }}
                >
                  <rect
                    width={NODE_W} height={NODE_H}
                    rx={6} ry={6}
                    fill={isHovered ? `${cfg.color}22` : '#0f1f35'}
                    stroke={cfg.stroke}
                    strokeWidth={isHovered ? 2 : 1.5}
                    style={{ filter: isHovered ? `drop-shadow(0 0 6px ${cfg.glow})` : 'none' }}
                  />
                  <text
                    x={NODE_W / 2} y={NODE_H / 2 - 3}
                    textAnchor="middle"
                    fill={isHovered ? cfg.color : '#cbd5e1'}
                    fontSize={9.5}
                    fontFamily="'JetBrains Mono', 'Fira Code', monospace"
                    fontWeight={600}
                  >
                    {truncated}
                  </text>
                  <text
                    x={NODE_W / 2} y={NODE_H / 2 + 10}
                    textAnchor="middle"
                    fill={cfg.color}
                    fontSize={8}
                    opacity={0.7}
                  >
                    {n.type === 'entry_point' ? '● Entry Point'
                      : n.type === 'hub' ? `↙ ${n.fan_in} dependants`
                      : n.type === 'high_fan_out' ? `↗ ${n.fan_out} imports`
                      : ''}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* High fan-in / fan-out summary */}
      {(high_fan_in.length > 0 || high_fan_out.length > 0) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
          {high_fan_in.length > 0 && (
            <div style={{ background: 'var(--bg-card)', borderRadius: 8, padding: '10px 12px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 10, color: '#f59e0b', fontWeight: 700, marginBottom: 6 }}>
                ⭐ High Fan-In (most depended-upon)
              </div>
              {high_fan_in.slice(0, 5).map((p, i) => (
                <div key={i} style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginBottom: 2 }}>
                  {p.split('/').pop()}
                </div>
              ))}
            </div>
          )}
          {high_fan_out.length > 0 && (
            <div style={{ background: 'var(--bg-card)', borderRadius: 8, padding: '10px 12px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 10, color: '#3b82f6', fontWeight: 700, marginBottom: 6 }}>
                ↗ High Fan-Out (most dependencies)
              </div>
              {high_fan_out.slice(0, 5).map((p, i) => (
                <div key={i} style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginBottom: 2 }}>
                  {p.split('/').pop()}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
