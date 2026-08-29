/**
 * RecommendedReadingPath.jsx
 * --------------------------
 * Visual "Start Here" stepper showing the recommended learning path
 * through the repository for a newcomer developer.
 *
 * Clearly labelled as "Recommended Learning Path" (not runtime execution flow).
 */

const STEP_COLORS = [
  '#00d4e8', '#3b82f6', '#8b5cf6',
  '#a855f7', '#ec4899', '#f59e0b',
  '#22c55e', '#34d399', '#6ee7b7',
  '#94a3b8',
];

function getStepColor(idx) {
  return STEP_COLORS[idx % STEP_COLORS.length];
}

function getStepIcon(path) {
  const p = path.toLowerCase();
  if (p.includes('readme')) return '📖';
  if (p.endsWith('.txt') || p.includes('requirement')) return '📦';
  if (p.includes('main') || p.includes('app') || p.includes('run')) return '🚀';
  if (p.includes('service')) return '⚙️';
  if (p.includes('controller') || p.includes('route') || p.includes('api')) return '🎛';
  if (p.includes('model') || p.includes('entity')) return '🏗';
  if (p.includes('test') || p.includes('spec')) return '🧪';
  if (p.includes('config') || p.includes('setting')) return '⚙';
  if (p.includes('util') || p.includes('helper')) return '🔧';
  if (p.endsWith('/')) return '📁';
  return '📄';
}

export default function RecommendedReadingPath({ steps = [] }) {
  if (!steps || steps.length === 0) {
    return (
      <div style={{ padding: '16px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
        Not enough repository information to generate a reading path.
      </div>
    );
  }

  return (
    <div>
      {/* Disclaimer */}
      <div style={{
        fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic',
        marginBottom: 16, paddingLeft: 4,
      }}>
        📌 This is a recommended <em>learning order</em>, not a runtime execution sequence.
      </div>

      <div style={{ position: 'relative' }}>
        {/* Vertical connector line */}
        <div style={{
          position: 'absolute',
          left: 18, top: 20, bottom: 20,
          width: 2,
          background: 'linear-gradient(180deg, var(--border-light) 0%, transparent 100%)',
          zIndex: 0,
        }} />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 0, position: 'relative', zIndex: 1 }}>
          {steps.map((step, i) => {
            const color = getStepColor(i);
            const icon = getStepIcon(step.path);

            return (
              <div key={i} style={{ display: 'flex', gap: 14, alignItems: 'flex-start', paddingBottom: 16 }}>
                {/* Step number bubble */}
                <div style={{
                  width: 36, height: 36, borderRadius: '50%',
                  background: `linear-gradient(135deg, ${color}30, ${color}15)`,
                  border: `2px solid ${color}60`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0,
                  boxShadow: `0 0 10px ${color}30`,
                }}>
                  <span style={{ fontSize: 12, fontWeight: 800, color: color }}>
                    {step.order}
                  </span>
                </div>

                {/* Content */}
                <div style={{
                  flex: 1, paddingTop: 6,
                  borderBottom: i < steps.length - 1 ? '1px solid var(--border)' : 'none',
                  paddingBottom: 10,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
                    <span style={{ fontSize: 13 }}>{icon}</span>
                    <code style={{ fontSize: 12, fontWeight: 700, color: color, wordBreak: 'break-all' }}>
                      {step.path}
                    </code>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {step.reason}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
