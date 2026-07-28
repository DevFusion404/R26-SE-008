/**
 * Settings.jsx — RefactorIQ System & Agent Settings
 * Configures API endpoints, refactoring thresholds, safety defaults,
 * and clears application storage.
 */

import { useState, useEffect } from 'react';

const API_DEFAULT = 'http://localhost:8080';
const SETTINGS_KEY = 'refactoriq_settings';
const HISTORY_KEY = 'cuqa_analysis_history';

const DEFAULT_SETTINGS = {
  apiUrl: API_DEFAULT,
  defaultThreshold: 75,
  enableCriticalFilter: true,
  enableNamingFilter: true,
  autoRollbackOnFailure: true,
  dryRunMode: false,
  exportFormat: 'JSON',
  astEngine: 'Tree-Sitter / Javalang / Python AST',
};

export default function Settings() {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [connectionStatus, setConnectionStatus] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [savedMessage, setSavedMessage] = useState(null);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(SETTINGS_KEY);
      if (stored) setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(stored) });
    } catch {}
  }, []);

  async function testConnection() {
    setTestingConnection(true);
    setConnectionStatus(null);
    try {
      const res = await fetch(`${settings.apiUrl}/api/health`);
      const data = await res.json();
      if (res.ok && data.status === 'ok') {
        setConnectionStatus({ success: true, msg: '✔ Connection Successful! CUQA Agent API is online.' });
      } else {
        setConnectionStatus({ success: false, msg: '⚠ Endpoint reached but returned unhealthy status.' });
      }
    } catch (e) {
      setConnectionStatus({ success: false, msg: `✖ Connection Failed: ${e.message}` });
    } finally {
      setTestingConnection(false);
    }
  }

  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
      setSavedMessage('✔ Settings saved successfully!');
      setTimeout(() => setSavedMessage(null), 3000);
    } catch (e) {
      setSavedMessage(`✖ Failed to save settings: ${e.message}`);
    }
  }

  function clearHistory() {
    if (window.confirm('Are you sure you want to clear all analysis history from local storage?')) {
      localStorage.removeItem(HISTORY_KEY);
      setSavedMessage('✔ Analysis history cleared.');
      setTimeout(() => setSavedMessage(null), 3000);
    }
  }

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">⚙️</div>
          <div>
            <div className="page-title">Settings &amp; Configuration</div>
            <div className="page-subtitle">
              Manage agent endpoints, default quality thresholds, safety preferences, and storage for <strong style={{ color: 'var(--accent)' }}>RefactorIQ</strong>.
            </div>
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={saveSettings}>
            💾 Save Settings
          </button>
        </div>
      </div>

      {savedMessage && (
        <div className={`alert ${savedMessage.startsWith('✔') ? 'alert-success' : 'alert-error'}`}>
          {savedMessage}
        </div>
      )}

      {/* Main Settings Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Backend & API Section */}
        <div className="card card-body" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 14 }}>
            🔌 Agent API Server Connection
          </h3>

          <div style={{ marginBottom: 16 }}>
            <label className="field-label">CUQA Agent Backend API Base URL</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                className="input"
                value={settings.apiUrl}
                onChange={e => setSettings({ ...settings, apiUrl: e.target.value })}
                placeholder="http://localhost:8080"
              />
              <button
                className="btn btn-outline"
                onClick={testConnection}
                disabled={testingConnection}
              >
                {testingConnection ? 'Testing...' : 'Test'}
              </button>
            </div>
          </div>

          {connectionStatus && (
            <div className={`alert ${connectionStatus.success ? 'alert-success' : 'alert-error'}`} style={{ marginTop: 10, fontSize: 11 }}>
              {connectionStatus.msg}
            </div>
          )}

          <div style={{ marginTop: 16 }}>
            <label className="field-label">AST Parser Engine</label>
            <select
              className="input"
              value={settings.astEngine}
              onChange={e => setSettings({ ...settings, astEngine: e.target.value })}
            >
              <option>Tree-Sitter / Javalang / Python AST (Default Polyglot)</option>
              <option>Strict Single-Threaded AST Mode</option>
              <option>Parallel Worker AST Extraction</option>
            </select>
          </div>
        </div>

        {/* Refactoring Engine Defaults */}
        <div className="card card-body" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 14 }}>
            🎯 Analysis &amp; Refactoring Defaults
          </h3>

          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <label className="field-label" style={{ margin: 0 }}>Default Quality Threshold</label>
              <span className="badge badge-accent">{settings.defaultThreshold}%</span>
            </div>
            <input
              type="range"
              min={50}
              max={95}
              value={settings.defaultThreshold}
              onChange={e => setSettings({ ...settings, defaultThreshold: parseInt(e.target.value) })}
              style={{ width: '100%' }}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <label className="field-label">Default Severity Filter Toggles</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={settings.enableCriticalFilter}
                  onChange={e => setSettings({ ...settings, enableCriticalFilter: e.target.checked })}
                />
                <span>Critical Structural Issues (Memory Leaks, Circular Deps)</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={settings.enableNamingFilter}
                  onChange={e => setSettings({ ...settings, enableNamingFilter: e.target.checked })}
                />
                <span>Naming &amp; Style Violations (PEP8, Java Conventions)</span>
              </label>
            </div>
          </div>
        </div>

        {/* Safety & Validation */}
        <div className="card card-body" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 14 }}>
            🛡️ Transformation Safety &amp; Rollback
          </h3>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={settings.autoRollbackOnFailure}
                onChange={e => setSettings({ ...settings, autoRollbackOnFailure: e.target.checked })}
              />
              <span>Automatic Rollback on Test Suite Failure</span>
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={settings.dryRunMode}
                onChange={e => setSettings({ ...settings, dryRunMode: e.target.checked })}
              />
              <span>Dry-Run Preview Mode (Do not write modified files directly)</span>
            </label>
          </div>
        </div>

        {/* Data & Storage Management */}
        <div className="card card-body" style={{ padding: 20 }}>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 14 }}>
            🗑️ Storage &amp; Cache Management
          </h3>

          <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 14 }}>
            Clear local analysis history, cached AST trees, and stored session telemetry.
          </p>

          <button className="btn btn-outline" style={{ borderColor: '#ef4444', color: '#ef4444' }} onClick={clearHistory}>
            🗑️ Clear Analysis History
          </button>
        </div>
      </div>
    </div>
  );
}
