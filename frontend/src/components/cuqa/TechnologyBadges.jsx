/**
 * TechnologyBadges.jsx
 * --------------------
 * Displays detected technologies grouped by category
 * (Build Tools, Dependency Managers, Testing, Deployment, CI/CD, Languages).
 */

const CAT_ICONS = {
  'Languages':          '🌐',
  'Build Tools':        '🔨',
  'Dependency Managers':'📦',
  'Testing':            '🧪',
  'Deployment':         '🚀',
  'Ci Cd':              '⚙️',
  'Ci/Cd':              '⚙️',
};

const CAT_COLORS = {
  'Languages':          { color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.3)' },
  'Build Tools':        { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)' },
  'Dependency Managers':{ color: '#00d4e8', bg: 'rgba(0,212,232,0.12)', border: 'rgba(0,212,232,0.3)' },
  'Testing':            { color: '#22c55e', bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.3)' },
  'Deployment':         { color: '#a855f7', bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.3)' },
  'Ci Cd':              { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.3)' },
};
const DEFAULT_CFG = { color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' };

function normaliseCategory(cat) {
  return cat
    .replace(/_/g, ' ')
    .split(' ')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export default function TechnologyBadges({ technologies = [] }) {
  if (!technologies || technologies.length === 0) return null;

  // Group by category
  const groups = {};
  for (const tech of technologies) {
    const cat = normaliseCategory(tech.category || 'Other');
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(tech);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {Object.entries(groups).map(([cat, techs]) => {
        const cfg = CAT_COLORS[cat] || DEFAULT_CFG;
        const icon = CAT_ICONS[cat] || '🔧';
        return (
          <div key={cat}>
            <div style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.6px',
              color: cfg.color, textTransform: 'uppercase', marginBottom: 6,
              display: 'flex', alignItems: 'center', gap: 5,
            }}>
              <span>{icon}</span>
              <span>{cat}</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {techs.map((tech) => (
                <div
                  key={tech.name}
                  title={`Evidence: ${(tech.evidence || []).join(', ')}`}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 'var(--r-full)',
                    fontSize: 11,
                    fontWeight: 600,
                    background: cfg.bg,
                    color: cfg.color,
                    border: `1px solid ${cfg.border}`,
                    cursor: 'default',
                    transition: 'transform 0.15s ease',
                  }}
                  onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-1px)'}
                  onMouseLeave={e => e.currentTarget.style.transform = 'translateY(0)'}
                >
                  {tech.name}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
