/**
 * TraceDebugger.jsx — Debug Component
 * Shows the raw trace structure for debugging
 */

export default function TraceDebugger({ trace }) {
  if (!trace) return null;

  return (
    <details
      style={{
        marginTop: '20px',
        padding: '12px',
        background: 'rgba(59,130,246,0.05)',
        borderRadius: '6px',
        border: '1px solid rgba(59,130,246,0.15)',
      }}
    >
      <summary style={{ cursor: 'pointer', fontSize: '12px', fontWeight: '600', color: 'rgba(59,130,246,1)' }}>
        🔍 Trace Structure (Debug)
      </summary>
      <pre
        style={{
          marginTop: '12px',
          fontSize: '10px',
          fontFamily: 'monospace',
          background: 'var(--bg-secondary)',
          padding: '12px',
          borderRadius: '4px',
          overflow: 'auto',
          maxHeight: '300px',
          color: 'var(--text-secondary)',
        }}
      >
        {JSON.stringify(trace, null, 2)}
      </pre>
    </details>
  );
}
