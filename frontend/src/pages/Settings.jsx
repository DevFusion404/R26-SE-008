/**
 * Settings.jsx — RefactorIQ settings
 * Everything on this page does something:
 *   - agent connections: the configured URL of each service and a live test
 *   - analysis defaults: used by Repository Input (saved in this browser)
 *   - appearance: dark / light theme
 *   - data in this browser: what is stored and a way to clear it
 * Safety rules that the agents enforce themselves are shown read-only.
 */

import { useState } from 'react';
import { useTheme } from '../context/ThemeContext.jsx';
import { SERVICES, checkService, healthUrl } from '../utils/agentHealth';
import { loadSettings, saveSettings } from '../utils/appSettings';
import { HISTORY_KEY } from '../utils/analysisHistory';
import './pages.css';

const STORES = [
  { key: HISTORY_KEY,             label: 'Analysis history',  sub: 'Repositories loaded on Repository Input' },
  { key: 'rfiq_session_reports',  label: 'Session reports',   sub: 'Reports saved when a DIWO workflow is completed' },
  { key: 'rdp_plan_history',      label: 'RDP plan history',  sub: 'Plans generated on the RDP page' },
];

const countOf = (key) => {
  try {
    const v = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(v) ? v.length : 0;
  } catch {
    return 0;
  }
};

const MODES = ['Comprehensive Refactoring', 'Quick Scan', 'Security Focus', 'Performance Focus'];

export default function Settings() {
  const { theme, toggleTheme } = useTheme();
  const [settings, setSettings] = useState(() => loadSettings());
  const [savedAt,  setSavedAt]  = useState(null);
  const [status,   setStatus]   = useState({});   // id -> result | 'testing'
  const [counts,   setCounts]   = useState(() => Object.fromEntries(STORES.map(s => [s.key, countOf(s.key)])));

  function update(patch) {
    const next = { ...settings, ...patch };
    setSettings(next);
    if (saveSettings(next)) setSavedAt(new Date());
  }

  async function test(service) {
    setStatus(s => ({ ...s, [service.id]: 'testing' }));
    const r = await checkService(service);
    setStatus(s => ({ ...s, [service.id]: r }));
  }

  function testAll() {
    SERVICES.forEach(test);
  }

  function clearStore(store) {
    if (!window.confirm(`Clear ${store.label.toLowerCase()} from this browser? This cannot be undone.`)) return;
    try { localStorage.removeItem(store.key); } catch { /* blocked storage */ }
    setCounts(c => ({ ...c, [store.key]: 0 }));
  }

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">⚙️</div>
          <div>
            <div className="page-title">Settings</div>
            <div className="page-subtitle">Agent connections, analysis defaults, appearance and the data this browser keeps.</div>
          </div>
        </div>
        <div className="page-header-actions">
          {savedAt && <span className="ui-chip ok">✓ Saved {savedAt.toLocaleTimeString()}</span>}
        </div>
      </div>

      {/* ── Agent connections ─────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🔌 Agent connections</span>
          <button className="btn btn-outline btn-sm" onClick={testAll}>Test all</button>
        </div>
        <div>
          {SERVICES.map(s => {
            const r = status[s.id];
            return (
              <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                <span className={`ui-dot ${r === 'testing' ? 'wait' : r ? (r.ok ? 'ok' : 'down') : ''}`} />
                <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {s.name} <span style={{ fontWeight: 400, color: 'var(--text-muted)', fontSize: 12.5 }}>· {s.role}</span>
                  </div>
                  <code style={{ fontSize: 12, padding: '1px 6px', marginTop: 4, display: 'inline-block', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {healthUrl(s)}
                  </code>
                </div>
                {r && r !== 'testing' && (
                  <span className={`ui-chip ${r.ok ? 'ok' : 'bad'}`}>{r.ok ? `online · ${r.ms} ms` : r.detail}</span>
                )}
                <button className="btn btn-ghost btn-sm" onClick={() => test(s)} disabled={r === 'testing'}>
                  {r === 'testing' ? 'Testing…' : 'Test'}
                </button>
              </div>
            );
          })}
          <p className="ui-source" style={{ padding: '10px 16px' }}>
            The URLs come from the <code>VITE_*_URL</code> environment variables (or <code>env-config.js</code> in the deployed container), so they are changed at deployment, not here.
          </p>
        </div>
      </div>

      <div className="rg-2" style={{ gap: 16 }}>
        {/* ── Analysis defaults ─────────────────────────── */}
        <div className="card">
          <div className="card-header"><span className="card-title">🎯 Analysis defaults</span></div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <p className="ui-source">Repository Input starts with these values. Changes save automatically.</p>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <label className="field-label" htmlFor="def-threshold" style={{ margin: 0 }}>Quality threshold</label>
                <span className="badge badge-accent">{settings.defaultThreshold}%</span>
              </div>
              <input id="def-threshold" type="range" min={0} max={100} value={settings.defaultThreshold}
                onChange={e => update({ defaultThreshold: +e.target.value })} style={{ width: '100%' }} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <label className="ui-switch">
                <input type="checkbox" checked={settings.enableCriticalFilter} onChange={e => update({ enableCriticalFilter: e.target.checked })} />
                <span className="track" />
                <span><b>Show high-severity smells</b><small>Long Method, Large Class, Deep Nesting…</small></span>
              </label>
              <label className="ui-switch">
                <input type="checkbox" checked={settings.enableNamingFilter} onChange={e => update({ enableNamingFilter: e.target.checked })} />
                <span className="track" />
                <span><b>Show medium and low smells</b><small>Magic Number, Too Many Parameters, naming…</small></span>
              </label>
            </div>
            <div>
              <label className="field-label">Language context</label>
              <div className="toggle-group">
                {['All', 'Java', 'Python', 'C'].map(l => (
                  <button key={l} className={`toggle-item ${settings.defaultLanguage === l ? 'active' : ''}`} onClick={() => update({ defaultLanguage: l })}>{l}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="field-label" htmlFor="def-mode">Analysis mode</label>
              <select id="def-mode" className="input" value={settings.defaultMode} onChange={e => update({ defaultMode: e.target.value })}>
                {MODES.map(m => <option key={m}>{m}</option>)}
              </select>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* ── Appearance ────────────────────────────── */}
          <div className="card">
            <div className="card-header"><span className="card-title">🎨 Appearance</span></div>
            <div className="rg-2" style={{ gap: 10, padding: 16 }}>
              {[
                { id: 'dark',  label: 'Dark',  icon: '🌙', bg: '#0a0f1a', fg: '#e2eaf4' },
                { id: 'light', label: 'Light', icon: '☀️', bg: '#f5f7fa', fg: '#0f172a' },
              ].map(t => (
                <button
                  key={t.id}
                  type="button"
                  className="ui-click"
                  onClick={() => theme !== t.id && toggleTheme()}
                  aria-pressed={theme === t.id}
                  style={{
                    border: `2px solid ${theme === t.id ? 'var(--accent)' : 'var(--border)'}`,
                    borderRadius: 'var(--r-md)', padding: 12, background: 'var(--bg-base)',
                    display: 'flex', flexDirection: 'column', gap: 8, textAlign: 'left',
                    fontFamily: 'var(--font-body)', color: 'var(--text-primary)',
                  }}
                >
                  <span style={{ height: 46, borderRadius: 8, background: t.bg, border: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px' }}>
                    <i style={{ width: 22, height: 6, borderRadius: 3, background: '#00a8c0' }} />
                    <i style={{ width: 34, height: 6, borderRadius: 3, background: t.fg, opacity: 0.5 }} />
                  </span>
                  <b style={{ fontSize: 13.5 }}>{t.icon} {t.label}{theme === t.id ? ' · in use' : ''}</b>
                </button>
              ))}
            </div>
          </div>

          {/* ── Safety (read-only) ────────────────────── */}
          <div className="card">
            <div className="card-header"><span className="card-title">🛡️ Safety rules (always on)</span></div>
            <ul style={{ padding: '12px 16px 14px 34px', display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              <li>SCTVA checks every change in order: syntax, then structure, then behaviour.</li>
              <li>If any check fails, SCTVA rolls the file back automatically.</li>
              <li>Nothing is transformed until the developer approves the plan steps in DIWO, and each changed file is accepted or rejected by the developer.</li>
            </ul>
          </div>
        </div>
      </div>

      {/* ── Data in this browser ─────────────────────────── */}
      <div className="card">
        <div className="card-header"><span className="card-title">🗂 Data stored in this browser</span></div>
        <div className="rg-3" style={{ gap: 12, padding: 16 }}>
          {STORES.map(s => (
            <div key={s.key} style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)', padding: 14, background: 'var(--bg-base)', display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
                <b style={{ fontSize: 14, color: 'var(--text-primary)' }}>{s.label}</b>
                <span style={{ fontSize: 22, fontWeight: 800, color: 'var(--accent)' }}>{counts[s.key]}</span>
              </div>
              <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{s.sub}</span>
              <button
                className="btn btn-outline btn-sm"
                style={{ alignSelf: 'flex-start', marginTop: 4, borderColor: 'color-mix(in srgb, var(--color-critical) 45%, transparent)', color: 'var(--color-critical)' }}
                onClick={() => clearStore(s)}
                disabled={!counts[s.key]}
              >
                Clear
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
