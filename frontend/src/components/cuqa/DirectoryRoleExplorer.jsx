/**
 * DirectoryRoleExplorer.jsx
 * -------------------------
 * Shows important repository directories with role badges and
 * plain-English descriptions for newcomers.
 */

import { useState } from 'react';

const ROLE_CFG = {
  'Source':         { icon: '📁', color: '#00d4e8', bg: 'rgba(0,212,232,0.12)', border: 'rgba(0,212,232,0.3)' },
  'Service':        { icon: '⚙️', color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.3)' },
  'Controller':     { icon: '🎛',  color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)', border: 'rgba(139,92,246,0.3)' },
  'Repository':     { icon: '🗄',  color: '#a855f7', bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.3)' },
  'Data Model':     { icon: '🏗',  color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)' },
  'Tests':          { icon: '🧪',  color: '#22c55e', bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.3)' },
  'Configuration':  { icon: '⚙',   color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' },
  'Documentation':  { icon: '📖',  color: '#34d399', bg: 'rgba(52,211,153,0.12)', border: 'rgba(52,211,153,0.3)' },
  'Utility':        { icon: '🔧',  color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', border: 'rgba(148,163,184,0.25)' },
  'Scripts':        { icon: '📜',  color: '#fb923c', bg: 'rgba(251,146,60,0.12)', border: 'rgba(251,146,60,0.3)' },
  'Deployment':     { icon: '🚀',  color: '#a855f7', bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.3)' },
  'Headers':        { icon: '📎',  color: '#e879f9', bg: 'rgba(232,121,249,0.12)', border: 'rgba(232,121,249,0.3)' },
  'API':            { icon: '🔌',  color: '#00d4e8', bg: 'rgba(0,212,232,0.12)', border: 'rgba(0,212,232,0.3)' },
  'Auth':           { icon: '🔒',  color: '#ef4444', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.3)' },
  'Migration':      { icon: '🔄',  color: '#facc15', bg: 'rgba(250,204,21,0.12)', border: 'rgba(250,204,21,0.3)' },
};
const DEFAULT_DIR_CFG = { icon: '📁', color: '#6b7280', bg: 'rgba(107,114,128,0.08)', border: 'rgba(107,114,128,0.2)' };

const CONFIDENCE_LABEL = { high: null, medium: '~', low: '?' };

export default function DirectoryRoleExplorer({ directories = [], searchQuery = '' }) {
  const [expanded, setExpanded] = useState(null);

  const filtered = searchQuery
    ? directories.filter(d =>
        d.path?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        d.role?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : directories;

  if (filtered.length === 0) {
    return (
      <div style={{ padding: '16px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
        {searchQuery ? `No directories matching "${searchQuery}".` : 'No important directories identified.'}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {filtered.map((dir, i) => {
        const cfg = ROLE_CFG[dir.role] || DEFAULT_DIR_CFG;
        const isOpen = expanded === i;
        const confidenceMark = CONFIDENCE_LABEL[dir.confidence];

        return (
          <div
            key={i}
            style={{
              borderRadius: 8,
              border: `1px solid ${isOpen ? cfg.border : 'var(--border)'}`,
              overflow: 'hidden',
              transition: 'border-color 0.15s ease',
            }}
          >
            {/* Row header */}
            <div
              onClick={() => setExpanded(isOpen ? null : i)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '9px 14px',
                cursor: 'pointer',
                background: isOpen ? cfg.bg : 'transparent',
                transition: 'background 0.15s ease',
                userSelect: 'none',
              }}
            >
              <span style={{ fontSize: 15, flexShrink: 0 }}>{cfg.icon}</span>
              <code style={{
                fontSize: 12, fontWeight: 700, color: cfg.color, flex: 1,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {dir.path}
                {confidenceMark && (
                  <span style={{ fontSize: 9, color: 'var(--text-muted)', marginLeft: 4 }}>{confidenceMark}</span>
                )}
              </code>
              {/* Role badge */}
              <span style={{
                fontSize: 9, fontWeight: 700, letterSpacing: '0.4px',
                padding: '2px 8px', borderRadius: 'var(--r-full)',
                background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
                flexShrink: 0, whiteSpace: 'nowrap',
              }}>
                {dir.role.toUpperCase()}
              </span>
              <span style={{
                fontSize: 10, color: 'var(--text-muted)', flexShrink: 0,
                transform: isOpen ? 'rotate(90deg)' : 'none',
                transition: 'transform 0.2s ease',
              }}>▸</span>
            </div>

            {/* Expanded detail */}
            {isOpen && (
              <div style={{
                padding: '10px 14px 12px 42px',
                borderTop: `1px solid ${cfg.border}`,
                background: `${cfg.bg}`,
                animation: 'fadeIn 0.15s ease',
              }}>
                <p style={{ margin: '0 0 8px 0', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {dir.description}
                </p>
                {dir.evidence && dir.evidence.length > 0 && (
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>Evidence:</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {dir.evidence.map((ev, j) => (
                        <span key={j} style={{
                          fontSize: 10, padding: '1px 7px', borderRadius: 4,
                          background: 'var(--bg-hover)', color: 'var(--text-secondary)',
                          border: '1px solid var(--border)',
                        }}>
                          {ev}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
