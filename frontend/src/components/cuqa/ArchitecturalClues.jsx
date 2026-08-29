/**
 * ArchitecturalClues.jsx
 * ----------------------
 * Displays structural pattern indicators detected from the repository.
 *
 * Uses cautious language ("appears to", "possible", "structural indication").
 * Never presents inferences as certain facts.
 */

const CONFIDENCE_CFG = {
  high:   { color: '#22c55e', bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.3)',   icon: '●' },
  medium: { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)', icon: '◐' },
  low:    { color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)', icon: '○' },
};

const PATTERN_ICONS = {
  'Possible Layered Architecture':   '🏛',
  'Possible MVC Structure':          '🎭',
  'Possible Multi-Module or Monorepo':'📦',
  'Possible Hexagonal Architecture': '⬡',
};

export default function ArchitecturalClues({ clues = [] }) {
  if (!clues || clues.length === 0) {
    return (
      <div style={{ padding: '12px 0', textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>
        No recognisable architectural patterns detected from directory structure.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Disclaimer */}
      <div style={{
        fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic',
        padding: '6px 10px',
        background: 'var(--bg-hover)', borderRadius: 6,
        borderLeft: '3px solid var(--border-light)',
      }}>
        ⚠ These are structural clues, not confirmed architecture declarations.
        Verify by inspecting actual code dependencies.
      </div>

      {clues.map((clue, i) => {
        const cfg = CONFIDENCE_CFG[clue.confidence] || CONFIDENCE_CFG.low;
        const icon = PATTERN_ICONS[clue.pattern] || '🏗';

        return (
          <div key={i} style={{
            padding: '12px 14px',
            borderRadius: 8,
            background: cfg.bg,
            border: `1px solid ${cfg.border}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 6 }}>
              <span style={{ fontSize: 18 }}>{icon}</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: cfg.color }}>{clue.pattern}</span>
              <span style={{
                fontSize: 9, fontWeight: 700, padding: '1px 7px',
                borderRadius: 'var(--r-full)', marginLeft: 'auto',
                background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
              }}>
                {clue.confidence.toUpperCase()} CONFIDENCE
              </span>
            </div>

            <p style={{ margin: '0 0 8px 0', fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {clue.description}
            </p>

            {clue.evidence && clue.evidence.length > 0 && (
              <div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>Supporting evidence:</div>
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {clue.evidence.map((ev, j) => (
                    <li key={j} style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 2 }}>
                      {ev}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
