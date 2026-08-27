/**
 * LanguageBreakdown.jsx
 * ---------------------
 * Renders a visual language distribution breakdown with
 * coloured progress bars and file/LOC counts.
 */

const LANG_COLORS = {
  Python: { bar: '#3b82f6', glow: 'rgba(59,130,246,0.35)', bg: 'rgba(59,130,246,0.12)', icon: '🐍' },
  Java:   { bar: '#f59e0b', glow: 'rgba(245,158,11,0.35)', bg: 'rgba(245,158,11,0.12)', icon: '☕' },
  C:      { bar: '#8b5cf6', glow: 'rgba(139,92,246,0.35)', bg: 'rgba(139,92,246,0.12)', icon: '⚙️' },
};
const DEFAULT_LANG = { bar: '#00d4e8', glow: 'rgba(0,212,232,0.35)', bg: 'rgba(0,212,232,0.12)', icon: '📄' };

export default function LanguageBreakdown({ languages = [] }) {
  if (!languages || languages.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {languages.map((lang) => {
        const cfg = LANG_COLORS[lang.language] || DEFAULT_LANG;
        const pct = lang.percentage || 0;
        return (
          <div key={lang.language}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span style={{ fontSize: 14 }}>{cfg.icon}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: cfg.bar }}>{lang.language}</span>
              </div>
              <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--text-muted)' }}>
                <span><strong style={{ color: 'var(--text-secondary)' }}>{lang.files}</strong> files</span>
                {lang.lines > 0 && (
                  <span><strong style={{ color: 'var(--text-secondary)' }}>{lang.lines.toLocaleString()}</strong> LOC</span>
                )}
                <span style={{ color: cfg.bar, fontWeight: 700 }}>{pct}%</span>
              </div>
            </div>
            <div style={{ height: 7, borderRadius: 4, background: 'var(--bg-hover)', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${pct}%`,
                borderRadius: 4,
                background: `linear-gradient(90deg, ${cfg.bar}, ${cfg.bar}cc)`,
                boxShadow: `0 0 8px ${cfg.glow}`,
                transition: 'width 0.8s cubic-bezier(0.4,0,0.2,1)',
              }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
