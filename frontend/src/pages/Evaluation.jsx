/**
 * Evaluation.jsx — RefactorIQ evaluation results
 *
 * Measured results only, each with its source:
 *   - CUQA ML smell model: live from GET /api/ml-model, recorded values as fallback
 *   - ML model against rule baselines (F1)
 *   - automated test suites
 *   - processing time against the NFR targets
 *   - user feedback survey
 */

import { useEffect, useState } from 'react';
import CUQAAgentService from '../services/cuqaAgentService';
import { ML_MODEL, ML_VS_BASELINES, TEST_SUITES, PERFORMANCE, SURVEY, AGENT_COLOR } from '../data/evaluationEvidence';
import './pages.css';

const TABS = [
  { id: 'ml',     label: '🤖 Smell detection' },
  { id: 'tests',  label: '🧪 Tests' },
  { id: 'perf',   label: '⏱ Performance' },
  { id: 'survey', label: '💬 User feedback' },
];

const METRICS = [
  { id: 'f1',        label: 'F1' },
  { id: 'precision', label: 'Precision' },
  { id: 'recall',    label: 'Recall' },
  { id: 'roc_auc',   label: 'ROC-AUC' },
];

const pct = (v) => (v == null ? '—' : v.toFixed(2));
const secs = (ms) => `${(ms / 1000).toFixed(1)} s`;

function Source({ children }) {
  return <p className="ui-source" style={{ marginTop: 4 }}>Source: {children}</p>;
}

/** Turn the live /api/ml-model answer into the shape of ML_MODEL.smells. */
function fromLive(info) {
  if (!info?.available || !info.smells) return null;
  return Object.entries(info.smells).map(([key, s]) => ({
    key,
    name: s.display_name || key,
    enabled: Boolean(s.enabled),
    precision: s.metrics?.test_precision ?? null,
    recall: s.metrics?.test_recall ?? null,
    f1: s.metrics?.test_f1 ?? null,
    roc_auc: s.metrics?.test_roc_auc ?? null,
  }));
}

export default function Evaluation() {
  const [tab, setTab]       = useState('ml');
  const [metric, setMetric] = useState('f1');
  const [repo, setRepo]     = useState('ant');
  const [live, setLive]     = useState({ state: 'loading', info: null });

  async function loadModel() {
    setLive({ state: 'loading', info: null });
    try {
      const info = await CUQAAgentService.getMlModel();
      setLive({ state: fromLive(info) ? 'live' : 'unavailable', info });
    } catch {
      setLive({ state: 'offline', info: null });
    }
  }

  useEffect(() => {
    let alive = true;
    CUQAAgentService.getMlModel()
      .then((info) => { if (alive) setLive({ state: fromLive(info) ? 'live' : 'unavailable', info }); })
      .catch(() => { if (alive) setLive({ state: 'offline', info: null }); });
    return () => { alive = false; };
  }, []);

  const smells = fromLive(live.info) || ML_MODEL.smells;
  const longMethod = smells.find((s) => s.key === 'LongMethod');
  const totalTests = TEST_SUITES.rows.reduce((a, r) => a + r.tests, 0);
  const passedTests = TEST_SUITES.rows.reduce((a, r) => a + r.passed, 0);
  const overall = SURVEY.rows.find((r) => r.part === 'Overall ratings');
  const dataClass = ML_VS_BASELINES.rows.find((r) => r.smell === 'Data Class');

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">🧪</div>
          <div>
            <div className="page-title">Evaluation</div>
            <div className="page-subtitle">Measured results only. Each figure says where it comes from.</div>
          </div>
        </div>
        <div className="page-header-actions">
          <span className={`ui-chip ${live.state === 'live' ? 'ok' : live.state === 'loading' ? '' : 'warn'}`}>
            <span className={`ui-dot ${live.state === 'live' ? 'ok' : live.state === 'loading' ? 'wait' : 'down'}`} />
            {live.state === 'live' ? 'ML metrics live from CUQA'
              : live.state === 'loading' ? 'Reading CUQA…'
              : 'CUQA offline — recorded values'}
          </span>
          <button className="btn btn-outline btn-sm" onClick={loadModel} disabled={live.state === 'loading'}>⟳ Reload</button>
        </div>
      </div>

      {/* Headline figures */}
      <div className="rg-4" style={{ gap: 14 }}>
        <div className="ui-stat" style={{ '--c': 'var(--color-cuqa)' }}>
          <span className="k">ML Long Method F1</span>
          <span className="v">{pct(longMethod?.f1)}</span>
          <span className="s">on {ML_MODEL.testRepos} unseen repositories</span>
        </div>
        <div className="ui-stat" style={{ '--c': 'var(--color-rdp)' }}>
          <span className="k">ML gain on Data Class</span>
          <span className="v">+{(dataClass.ml - dataClass.threshold).toFixed(2)}</span>
          <span className="s">F1 vs best simple threshold (95% CI +0.05 to +0.23)</span>
        </div>
        <div className="ui-stat" style={{ '--c': 'var(--color-ok)' }}>
          <span className="k">Tests passed</span>
          <span className="v">{passedTests.toLocaleString()}</span>
          <span className="s">of {totalTests.toLocaleString()} across 5 suites</span>
        </div>
        <div className="ui-stat" style={{ '--c': 'var(--color-transform)' }}>
          <span className="k">User rating</span>
          <span className="v">{overall.mean.toFixed(2)}<small style={{ fontSize: 15, color: 'var(--text-muted)' }}> / 5</small></span>
          <span className="s">{SURVEY.date}</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="ui-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`ui-tab ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Smell detection ─────────────────────────────── */}
      {tab === 'ml' && (
        <div className="rg-2" style={{ gap: 16, alignItems: 'start' }}>
          <div className="card">
            <div className="card-header">
              <span className="card-title">CUQA ML model per smell</span>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {METRICS.map((m) => (
                  <button key={m.id} className={`btn btn-sm ${metric === m.id ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMetric(m.id)}>{m.label}</button>
                ))}
              </div>
            </div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="ui-bars">
                {smells.map((s) => (
                  <div className="ui-bar-row" key={s.key}>
                    <span className="name">{s.name}<small>{s.enabled ? 'used in CUQA' : 'not used (too weak)'}</small></span>
                    <span className="ui-bar-track">
                      <i style={{ width: `${(s[metric] ?? 0) * 100}%`, background: s.enabled ? 'var(--color-cuqa)' : 'var(--text-muted)' }} />
                    </span>
                    <span className="val">{pct(s[metric])}</span>
                  </div>
                ))}
              </div>
              <Source>
                {live.state === 'live' ? <>live <code>GET /api/ml-model</code> (model {live.info.model_version})</> : <code>{ML_MODEL.source}</code>}
                {' '}· gradient-boosted trees. MLCQ smells: {ML_MODEL.trainRepos} training / {ML_MODEL.testRepos} test repositories.
                {' '}DACOS smells: 8 training / 2 test repositories, scored on the human-annotated test samples.
              </Source>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><span className="card-title">ML model against simple detectors (F1, MLCQ)</span></div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {ML_VS_BASELINES.rows.map((r) => (
                <div key={r.smell}>
                  <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 6 }}>{r.smell}</div>
                  {[
                    { label: 'CUQA rule', v: r.rule, c: 'var(--text-muted)' },
                    { label: `Threshold: ${r.thresholdName}`, v: r.threshold, c: 'var(--color-medium)' },
                    { label: 'ML model', v: r.ml, c: 'var(--color-cuqa)' },
                  ].map((b) => (
                    <div className="ui-bar-row" key={b.label} style={{ marginBottom: 4 }}>
                      <span className="name" style={{ fontWeight: 500 }}>{b.label}</span>
                      <span className="ui-bar-track">{b.v != null && <i style={{ width: `${b.v * 100}%`, background: b.c }} />}</span>
                      <span className="val">{b.v == null ? 'no rule' : pct(b.v)}</span>
                    </div>
                  ))}
                </div>
              ))}
              <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                The model scores higher than the CUQA rules for Long Method and Large Class, but the test set is too small to call
                those gaps significant, and a plain <code>loc ≥ 30</code> threshold does as well for Long Method. The clear gain is
                Data Class, where the model beats both the Lanza–Marinescu rule and the best single threshold. Feature Envy stays
                off in the model because no detector did well on it; in hybrid mode the rule covers it.
              </p>
              <Source><code>{ML_VS_BASELINES.source}</code></Source>
            </div>
          </div>
        </div>
      )}

      {/* ── Tests ───────────────────────────────────────── */}
      {tab === 'tests' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Automated test suites</span>
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{TEST_SUITES.date}</span>
          </div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="ui-legend">
              <span><i style={{ background: 'var(--color-ok)' }} />passed</span>
              <span><i style={{ background: 'var(--color-critical)' }} />failed</span>
              <span><i style={{ background: 'var(--text-muted)' }} />skipped</span>
            </div>
            <div className="ui-bars">
              {TEST_SUITES.rows.map((r) => (
                <div className="ui-bar-row" key={r.suite}>
                  <span className="name" style={{ color: r.agent ? AGENT_COLOR[r.agent] : 'var(--text-primary)' }}>
                    {r.suite}<small>{r.tests} tests · {r.seconds} s</small>
                  </span>
                  <span className="ui-bar-track" style={{ display: 'flex', overflow: 'hidden' }}>
                    <i style={{ position: 'static', width: `${(r.passed / r.tests) * 100}%`, background: 'var(--color-ok)', borderRadius: 0 }} />
                    <i style={{ position: 'static', width: `${(r.failed / r.tests) * 100}%`, background: 'var(--color-critical)', borderRadius: 0 }} />
                    <i style={{ position: 'static', width: `${(r.skipped / r.tests) * 100}%`, background: 'var(--text-muted)', borderRadius: 0 }} />
                  </span>
                  <span className="val" style={{ color: r.failed ? 'var(--color-critical)' : 'var(--color-ok)' }}>{r.passed}/{r.tests}</span>
                </div>
              ))}
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
              SCTVA had 21 failing tests in this run; the other suites passed fully.
            </p>
            <Source><code>{TEST_SUITES.source}</code></Source>
          </div>
        </div>
      )}

      {/* ── Performance ─────────────────────────────────── */}
      {tab === 'perf' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">P95 processing time against the target</span>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {PERFORMANCE.repos.map((r) => (
                <button key={r.id} className={`btn btn-sm ${repo === r.id ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setRepo(r.id)}>{r.name}</button>
              ))}
            </div>
          </div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="ui-legend">
              <span><i style={{ background: 'var(--accent)' }} />P95 of 5 runs</span>
              <span><i style={{ background: 'var(--text-secondary)', width: 3 }} />NFR target</span>
            </div>
            <div className="ui-bars">
              {PERFORMANCE.rows.map((r) => {
                const scale = 300000; // longest target, so bars are comparable
                const v = r.p95[repo];
                const ok = v <= r.target;
                const success = r.success[repo];
                return (
                  <div className="ui-bar-row" key={r.agent} style={{ gridTemplateColumns: 'minmax(120px, 190px) minmax(0, 1fr) 120px' }}>
                    <span className="name" style={{ color: AGENT_COLOR[r.agent] }}>
                      {r.name}<small>target {secs(r.target)} · runs ok {Math.round(success * 100)}%</small>
                    </span>
                    <span className="ui-bar-track">
                      <i style={{ width: `${(v / scale) * 100}%`, background: ok ? AGENT_COLOR[r.agent] : 'var(--color-critical)' }} />
                      <span className="mark" style={{ left: `${(r.target / scale) * 100}%` }} title={`Target ${secs(r.target)}`} />
                    </span>
                    <span className="val">{secs(v)}</span>
                  </div>
                );
              })}
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Every agent's P95 time is under its target on both repositories. Not every run succeeded: on curl, SCTVA and DIWO
              completed 2 of 5 runs, so reliability is the weaker result here.
            </p>
            <Source><code>{PERFORMANCE.source}</code></Source>
          </div>
        </div>
      )}

      {/* ── User feedback ───────────────────────────────── */}
      {tab === 'survey' && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">User feedback survey</span>
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{SURVEY.date}</span>
          </div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="ui-legend">
              <span><i style={{ background: 'var(--color-ok)' }} />agree (4–5)</span>
              <span><i style={{ background: 'var(--color-medium)' }} />neutral (3)</span>
              <span><i style={{ background: 'var(--color-critical)' }} />disagree (1–2)</span>
            </div>
            <div className="ui-bars">
              {SURVEY.rows.map((r) => {
                const total = r.agree + r.neutral + r.disagree;
                return (
                  <div className="ui-bar-row" key={r.part} style={{ gridTemplateColumns: 'minmax(140px, 240px) minmax(0, 1fr) 64px' }}>
                    <span className="name" style={{ color: r.agent ? AGENT_COLOR[r.agent] : 'var(--text-primary)' }}>
                      {r.part}<small>{total} ratings</small>
                    </span>
                    <span className="ui-bar-track" style={{ display: 'flex', overflow: 'hidden' }} title={`${r.agree} agree · ${r.neutral} neutral · ${r.disagree} disagree`}>
                      <i style={{ position: 'static', width: `${(r.agree / total) * 100}%`, background: 'var(--color-ok)', borderRadius: 0 }} />
                      <i style={{ position: 'static', width: `${(r.neutral / total) * 100}%`, background: 'var(--color-medium)', borderRadius: 0 }} />
                      <i style={{ position: 'static', width: `${(r.disagree / total) * 100}%`, background: 'var(--color-critical)', borderRadius: 0 }} />
                    </span>
                    <span className="val">{r.mean.toFixed(2)}</span>
                  </div>
                );
              })}
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>Right column: mean rating out of 5.</p>
            <Source><code>{SURVEY.source}</code></Source>
          </div>
        </div>
      )}
    </div>
  );
}
