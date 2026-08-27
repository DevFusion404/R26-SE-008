/**
 * ImportantFilesPanel.jsx
 * -----------------------
 * "Key Files to Know" panel — shows structurally important files
 * with role badges, reasons, and optional file selection actions.
 */

import { useState } from 'react';

const IMPORTANCE_CFG = {
  high:   { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)', dot: '⭐' },
  medium: { color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.3)', dot: '◆' },
  low:    { color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.2)', dot: '·' },
};

const ROLE_ICONS = {
  'Entry Point':         '🚀',
  'Documentation':       '📖',
  'Dependency Manifest': '📦',
  'Build Configuration': '🔨',
  'Service':             '⚙️',
  'Controller':          '🎛',
  'Repository':          '🗄',
  'Data Model':          '🏗',
  'Tests':               '🧪',
  'Configuration':       '⚙',
  'CI/CD':               '🔄',
  'Deployment':          '🐳',
  'License':             '📜',
  'Utility':             '🔧',
  'Header / Interface':  '📎',
  'Schema':              '📋',
  'Application Source':  '📄',
  'Unknown':             '📄',
};

export default function ImportantFilesPanel({ files = [], onFileSelect, searchQuery = '' }) {
  const [hoveredIdx, setHoveredIdx] = useState(null);

  const filtered = searchQuery
    ? files.filter(f =>
        f.path?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        f.role?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : files;

  if (filtered.length === 0) {
    return (
      <div style={{ padding: '16px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
        {searchQuery ? `No files matching "${searchQuery}".` : 'No key files identified.'}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      {filtered.map((file, i) => {
        const cfg = IMPORTANCE_CFG[file.importance] || IMPORTANCE_CFG.low;
        const roleIcon = ROLE_ICONS[file.role] || '📄';
        const isHovered = hoveredIdx === i;

        return (
          <div
            key={i}
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 12,
              padding: '10px 12px',
              borderRadius: 8,
              background: isHovered ? 'var(--bg-hover)' : 'transparent',
              cursor: onFileSelect ? 'pointer' : 'default',
              transition: 'background 0.12s ease',
              borderLeft: `3px solid ${isHovered ? cfg.color : 'transparent'}`,
            }}
            onClick={() => onFileSelect && onFileSelect({ path: file.path, name: file.name })}
          >
            {/* Role icon */}
            <span style={{ fontSize: 16, flexShrink: 0, marginTop: 1 }}>{roleIcon}</span>

            {/* Content */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3, flexWrap: 'wrap' }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700,
                  color: 'var(--text-primary)', wordBreak: 'break-all',
                }}>
                  {file.path}
                </span>
                {/* Role badge */}
                <span style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.4px',
                  padding: '1px 7px', borderRadius: 'var(--r-full)',
                  background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
                  flexShrink: 0,
                }}>
                  {file.role || 'Unknown'}
                </span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                {file.reason}
              </div>
            </div>

            {/* Importance star */}
            <span style={{
              fontSize: 12, color: cfg.color, flexShrink: 0,
              opacity: file.importance === 'high' ? 1 : 0.5,
            }}>
              {cfg.dot}
            </span>
          </div>
        );
      })}

      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6, paddingLeft: 12, fontStyle: 'italic' }}>
        Importance: ⭐ High  ◆ Medium  · Low
      </div>
    </div>
  );
}
