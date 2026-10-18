/**
 * DeveloperReviewButton.jsx
 * -------------------------
 * The main next step after CUQA: open the DIWO agent, where the developer
 * picks the smells to fix, approves the plan and checks every change.
 * One look everywhere on the CUQA page so users learn it once.
 */

export default function DeveloperReviewButton({ onClick, disabled = false, compact = false, id }) {
  if (compact) {
    return (
      <button id={id} type="button" className="cuqa-review-btn compact" onClick={onClick} disabled={disabled}
        title={disabled ? 'Available when the analysis has finished' : 'Open the DIWO agent for developer review'}>
        <span className="ic" aria-hidden="true">👤</span>
        <b><span className="long">Start developer review</span><span className="short">Developer review</span></b>
        <span className="arrow" aria-hidden="true">→</span>
      </button>
    );
  }

  return (
    <button id={id} type="button" className={`cuqa-review-btn ${disabled ? '' : 'ready'}`} onClick={onClick} disabled={disabled}
      title={disabled ? 'Available when the analysis has finished' : 'Open the DIWO agent for developer review'}>
      <span className="ic" aria-hidden="true">👤</span>
      <span>
        <b>Start developer review</b>
        <small>{disabled ? 'Available when the analysis finishes' : 'DIWO: pick smells, approve the plan, check changes'}</small>
      </span>
      <span className="arrow" aria-hidden="true">→</span>
    </button>
  );
}
