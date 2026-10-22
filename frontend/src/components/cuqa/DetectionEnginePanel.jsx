/**
 * DetectionEnginePanel.jsx
 * ------------------------
 * Shows the two smell detectors CUQA has — the rule engine and the trained
 * ML model — and lets the user pick how they are combined for this report.
 * The model facts come from GET /api/ml-model, not from the frontend.
 */

const MODES = [
  { id: 'rules',  label: 'Rules (default)', help: 'Rule smells only. The ML findings are shown as a second opinion.' },
  { id: 'hybrid', label: 'Hybrid',          help: 'The ML model decides the Java smells it learned; rules find every other smell.' },
  { id: 'ml',     label: 'ML model only',   help: 'Only the smells the ML model finds (Java).' },
];

export default function DetectionEnginePanel({ mode, onModeChange, modelInfo, report, loading }) {
  const smells = (report?.files || []).flatMap((f) => f.code_smells || []);
  const mlInReport = smells.filter((s) => s.detected_by === 'model').length;
  const ruleInReport = smells.length - mlInReport;
  const mlSummary = report?.summary?.ml_code_smells || null;
  const learned = Object.entries(modelInfo?.smells || {}).filter(([, s]) => s.enabled);
  const modelReady = Boolean(modelInfo?.available);

  return (
    <div className="cuqa-engine">
      <div className="cuqa-engine-card">
        <h5>⚙️ Rule engine <span className="tag">Java · Python · C</span></h5>
        <p>
          Static rules on the AST and code metrics, for example Long Method (over 30 lines),
          Too Many Parameters (over 5), Complex Method (complexity over 10), Deep Nesting, Feature Envy,
          Message Chains, Switch Statements, Dead Code, Duplicate Code, Magic Number and Large Class.
        </p>
        <dl className="cuqa-kv">
          <dt>Smells in this report</dt><dd>{report ? ruleInReport : '—'}</dd>
          <dt>Languages</dt><dd>Java, Python, C</dd>
        </dl>
      </div>

      <div className="cuqa-engine-card">
        <h5>🤖 ML model <span className="tag">Java</span></h5>
        {modelInfo === null ? (
          <p>Reading model details…</p>
        ) : !modelReady ? (
          <p>The model is not available on this CUQA server, so only the rule engine is used.</p>
        ) : (
          <>
            <p>
              Gradient-boosted trees trained on two labelled Java datasets: MLCQ (expert reviews) and
              DACOS (human annotations). One model per smell; a smell is reported when its probability
              passes the tuned threshold.
            </p>
            <dl className="cuqa-kv">
              <dt>Version</dt><dd>{modelInfo.model_version}</dd>
              <dt>Learned smells</dt>
              <dd>{learned.map(([, s]) => `${s.display_name} (${s.dataset || 'MLCQ'}, test F1 ${s.metrics?.test_f1?.toFixed(2) ?? '—'})`).join(', ') || '—'}</dd>
              <dt>{mode === 'rules' ? 'Second opinion' : 'ML smells in report'}</dt>
              <dd>
                {mode === 'rules'
                  ? (mlSummary ? `${mlSummary.total} smell(s) in ${mlSummary.files_scored} Java file(s)` : '—')
                  : (report ? mlInReport : '—')}
              </dd>
            </dl>
          </>
        )}
      </div>

      <div className="cuqa-engine-card">
        <h5>🎚 Detection mode</h5>
        <div className="cuqa-modes" role="radiogroup" aria-label="Detection mode">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={mode === m.id}
              className={`cuqa-mode ${mode === m.id ? 'on' : ''}`}
              onClick={() => mode !== m.id && onModeChange(m.id)}
              disabled={loading || (m.id !== 'rules' && !modelReady)}
            >
              <span className="dot" />
              <span><b>{m.label}</b><small>{m.help}</small></span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
