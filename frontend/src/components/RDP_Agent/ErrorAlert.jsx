/**
 * ErrorAlert.jsx — Error Message Display
 * Shows error messages with dismiss functionality
 */

export default function ErrorAlert({ message, onClose }) {
  return (
    <div
      className="alert alert-error"
      style={{
        marginTop: '16px',
        display: 'flex',
        alignItems: 'flex-start',
        gap: '12px',
        padding: '12px 16px',
        background: 'rgba(239,68,68,0.08)',
        border: '1px solid rgba(239,68,68,0.2)',
        borderRadius: '6px',
        fontSize: '13px',
        color: 'rgba(220,38,38,1)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      <span style={{ marginTop: '2px', fontSize: '16px', flexShrink: 0 }}>⚠️</span>
      <span style={{ flex: 1 }}>{message}</span>
      <button
        onClick={onClose}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'rgba(220,38,38,0.7)',
          cursor: 'pointer',
          fontSize: '16px',
          flexShrink: 0,
          padding: 0,
        }}
      >
        ✕
      </button>
    </div>
  );
}
