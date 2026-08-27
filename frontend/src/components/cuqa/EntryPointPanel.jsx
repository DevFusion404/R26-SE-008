/**
 * EntryPointPanel.jsx
 * -------------------
 * Displays detected application entry points with confidence badges,
 * evidence explanations, and language indicators.
 */

const CONFIDENCE_CFG = {
  high:   { label: 'HIGH',   color: '#22c55e', bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.35)' },
  medium: { label: 'MEDIUM', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.35)' },
  low:    { label: 'LOW',    color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' },
};

const LANG_ICON = { Python: '🐍', Java: '☕', C: '⚙️' };

function ConfidenceBadge({ confidence }) {
  const cfg = CONFIDENCE_CFG[confidence] || CONFIDENCE_CFG.low;
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '0.5px',
      padding: '2px 7px', borderRadius: 'var(--r-full)',
      background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
    }}>
      {cfg.label}
    </span>
  );
}

export default function EntryPointPanel({ entryPoints = [], onFileSelect }) {
  if (!entryPoints || entryPoints.length === 0) {
    return (
      <div style={{ padding: '16px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
        <span style={{ fontSize: 18, display: 'block', marginBottom: 6 }}>🔍</span>
        No identifiable entry point was detected.
        <div style={{ fontSize: 10, marginTop: 4, color: 'var(--text-muted)', fontStyle: 'italic' }}>
          The application may use an unconventional entry pattern.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {entryPoints.map((ep, i) => (
        <div
          key={i}
          style={{
            display: 'flex', alignItems: 'flex-start', gap: 12,
            padding: '10px 14px',
            borderRadius: 8,
            background: 'rgba(34,197,94,0.06)',
            border: '1px solid rgba(34,197,94,0.2)',
            cursor: onFileSelect ? 'pointer' : 'default',
            transition: 'background 0.15s ease',
          }}
          onClick={() => onFileSelect && onFileSelect({ path: ep.path, name: ep.path.split('/').pop() })}
          onMouseEnter={e => { if (onFileSelect) e.currentTarget.style.background = 'rgba(34,197,94,0.1)'; }}
          onMouseLeave={e => { if (onFileSelect) e.currentTarget.style.background = 'rgba(34,197,94,0.06)'; }}
        >
          <span style={{ fontSize: 18, flexShrink: 0, marginTop: 1 }}>🚀</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3, flexWrap: 'wrap' }}>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700,
                color: '#22c55e', wordBreak: 'break-all',
              }}>
                {ep.path}
              </span>
              <ConfidenceBadge confidence={ep.confidence} />
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                {LANG_ICON[ep.language] || '📄'} {ep.language}
              </span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
              {ep.evidence}
            </div>
          </div>
          {onFileSelect && (
            <span style={{ fontSize: 10, color: 'var(--accent)', flexShrink: 0, marginTop: 2 }}>
              View →
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
