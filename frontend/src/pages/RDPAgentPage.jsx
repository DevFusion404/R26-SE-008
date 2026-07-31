/**
 * RDPAgentPage.jsx — RDP Agent (Refactoring Decision & Planning)
 * Main page component integrating upload, pipeline trace, and results visualization
 */

import { useState, useRef, useEffect } from 'react';
import RDPAgentService from '../services/rdpAgentService';
import { formatApiError, retryApiCall } from '../utils/apiErrorHandler';
import UploadSection from '../components/RDP_Agent/UploadSection';
import PipelineViewer from '../components/RDP_Agent/PipelineViewer';
import ResultsViewer from '../components/RDP_Agent/ResultsViewer';
import ErrorAlert from '../components/RDP_Agent/ErrorAlert';
import ErrorBoundary from '../components/common/ErrorBoundary';

// ── RDP plan history — localStorage persistence (mirrors CUQA's cuqa_analysis_history) ──
const RDP_HISTORY_KEY = 'rdp_plan_history';
const MAX_HISTORY = 20;

function loadRdpHistory() {
  try { return JSON.parse(localStorage.getItem(RDP_HISTORY_KEY) || '[]'); }
  catch { return []; }
}

function saveRdpHistory(entries) {
  localStorage.setItem(RDP_HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
}

function appendRdpHistory(plan, sourceFileName) {
  const entry = {
    id:               plan.plan_id || `rdp_${Date.now()}`,
    plan_id:          plan.plan_id,
    target:           plan.target || 'unknown',
    date:             new Date().toISOString(),
    step_count:       plan.steps?.length || 0,
    smell_count:      plan.steps?.length || 0,
    refactorings:     [...new Set((plan.steps || []).map(s => s.refactoring))].slice(0, 3),
    source_file:      sourceFileName || 'unknown',
    summary:          plan.summary || '',
    plan,             // full plan stored for export / view
  };
  const updated = [entry, ...loadRdpHistory()].slice(0, MAX_HISTORY);
  saveRdpHistory(updated);
  return entry;
}

// ── Session persistence — save/restore last plan+trace across page refreshes ──
const RDP_SESSION_KEY = 'rdp_last_session';

function loadSession() {
  try {
    const raw = localStorage.getItem(RDP_SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveSession(plan, trace, sourceFileName) {
  try {
    localStorage.setItem(
      RDP_SESSION_KEY,
      JSON.stringify({ plan, trace, sourceFileName, savedAt: new Date().toISOString() })
    );
  } catch { /* storage quota exceeded — silently ignore */ }
}

function clearRdpSession() {
  localStorage.removeItem(RDP_SESSION_KEY);
}

export default function RDPAgentPage({ repoLoaded, repoMeta, preloadedReport, onClearPreloaded }) {
  // Load last session once on mount (lazy initializer so it only runs once)
  const [loading,           setLoading]           = useState(false);
  const [error,             setError]             = useState(null);
  const [plan,              setPlan]              = useState(() => loadSession()?.plan  ?? null);
  const [trace,             setTrace]             = useState(() => loadSession()?.trace ?? null);
  const [selectedFile,      setSelectedFile]      = useState(null);
  const [cuqaBannerVisible, setCuqaBannerVisible] = useState(false);
  // sessionRestored: true when plan was loaded from localStorage (not freshly generated)
  const [sessionRestored,   setSessionRestored]   = useState(() => !!loadSession()?.plan);
  const fileInputRef = useRef(null);

  /**
   * When a preloaded report is piped in from CUQA, auto-generate the plan
   */
  useEffect(() => {
    if (preloadedReport) {
      setCuqaBannerVisible(true);
      handleGeneratePlan(preloadedReport, null).finally(() => {
        // Clear the preloaded report after processing
        onClearPreloaded?.();
      });
    }
  }, [preloadedReport, onClearPreloaded]);

  /**
   * Handle file selection from upload
   */
  function handleFileSelect(file) {
    setSelectedFile(file);
    setError(null);
  }

  /**
   * Handle plan generation from quality report
   */
  async function handleGeneratePlan(qualityReport, file) {
    try {
      setLoading(true);
      setError(null);
      setPlan(null);
      setTrace(null);

      // Validate quality report format
      const validation = await RDPAgentService.validateQualityReport(qualityReport);
      if (!validation.valid) {
        setError(`Invalid quality report:\n${validation.errors.join('\n')}`);
        return;
      }

      // Generate plan with retry logic
      const result = await retryApiCall(
        () => RDPAgentService.generatePlan(qualityReport, file),
        3, // max 3 attempts
        1000 // 1s initial delay, exponential backoff
      );

      if (!result.success) {
        setError(result.error || 'Failed to generate refactoring plan');
        return;
      }

      // Display results
      setPlan(result.plan);
      setTrace(result.trace);

      // Persist plan+trace to localStorage history and session
      const sourceFileName = file?.name || (result.plan?.target) || 'piped-from-cuqa';
      appendRdpHistory(result.plan, sourceFileName);
      saveSession(result.plan, result.trace, sourceFileName);  // ← restores on refresh
      setSessionRestored(false);  // this is a fresh generation, not a restored one

      // Scroll to pipeline
      setTimeout(() => {
        document.getElementById('pipeline-section')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        });
      }, 100);
    } catch (err) {
      const formatted = formatApiError(err);
      setError(formatted.message);
    } finally {
      setLoading(false);
    }
  }

  /**
   * Handle file upload form submission
   */
  async function handleUploadSubmit(file) {
    try {
      // Read file as JSON
      const text = await file.text();
      const qualityReport = JSON.parse(text);

      // Generate plan from the report
      await handleGeneratePlan(qualityReport, file);
    } catch (err) {
      setError(`Failed to read file: ${err.message}`);
    }
  }

  /**
   * Handle download of plan
   */
  function handleDownloadPlan() {
    if (!plan) return;
    RDPAgentService.downloadPlan(plan, `${plan.plan_id || 'refactoring_plan'}.json`);
  }

  /**
   * Handle copy plan to clipboard
   */
  async function handleCopyPlan() {
    if (!plan) return;
    const success = await RDPAgentService.copyPlanToClipboard(plan);
    if (success) {
      // Visual feedback
      const btn = document.getElementById('copy-btn');
      if (btn) {
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1500);
      }
    }
  }

  /**
   * Get plan statistics
   */
  function getPlanStats() {
    if (!plan) return null;
    return RDPAgentService.getPlanStatistics(plan);
  }

  return (
    <div className="page-container">
      {/* Page Header */}
      <div className="page-header">
        <div className="page-header-left">
          <div
            className="page-header-icon"
            style={{ background: 'rgba(139,92,246,0.08)', borderColor: 'rgba(139,92,246,0.25)' }}
          >
            🧠
          </div>
          <div>
            <div className="page-title">RDP Agent</div>
            <div className="page-subtitle">Refactoring Decision &amp; Planning</div>
          </div>
        </div>
      </div>

      {/* CUQA pipeline banner — shown when report was piped from CUQA */}
      {cuqaBannerVisible && preloadedReport && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '12px 18px',
            marginTop: 12,
            background: 'linear-gradient(135deg, rgba(139,92,246,0.12), rgba(168,85,247,0.08))',
            border: '1px solid rgba(139,92,246,0.35)',
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          <span style={{ fontSize: 18 }}>⚡</span>
          <div style={{ flex: 1 }}>
            <strong style={{ color: '#a78bfa' }}>Quality report piped from CUQA Agent</strong>
            <span style={{ color: 'var(--text-secondary)', marginLeft: 8 }}>
              — generating refactoring plan automatically…
            </span>
          </div>
          <button
            onClick={() => {
              setCuqaBannerVisible(false);
              onClearPreloaded?.();
            }}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              fontSize: 16,
              lineHeight: 1,
            }}
            title="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      {/* Error Alert */}
      {error && <ErrorAlert message={error} onClose={() => setError(null)} />}

      {/* Restored session banner — shown when plan came from localStorage after a refresh */}
      {plan && sessionRestored && !loading && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '10px 18px', marginTop: 12,
          background: 'linear-gradient(135deg, rgba(34,197,94,0.08), rgba(74,222,128,0.04))',
          border: '1px solid rgba(34,197,94,0.25)',
          borderRadius: 8, fontSize: 12,
        }}>
          <span style={{ fontSize: 16 }}>💾</span>
          <div style={{ flex: 1, color: 'var(--text-secondary)' }}>
            <strong style={{ color: '#22c55e' }}>Session restored</strong>
            {' '}— showing your last generated plan. Upload a new report to replace it.
          </div>
          <button
            onClick={() => {
              clearRdpSession();
              setPlan(null);
              setTrace(null);
              setSessionRestored(false);
            }}
            style={{
              background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: 4, color: '#ef4444', fontSize: 11, padding: '4px 10px', cursor: 'pointer',
            }}
          >
            ✕ Clear
          </button>
        </div>
      )}

      {/* Upload Section — always visible so the user can generate a new plan at any time */}
      {!preloadedReport && (
        <UploadSection
          onFileSelect={handleFileSelect}
          onSubmit={handleUploadSubmit}
          loading={loading}
          selectedFile={selectedFile}
        />
      )}

      {/* Loading spinner while auto-generating from preloaded report */}
      {loading && preloadedReport && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '24px',
            marginTop: 16,
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            borderRadius: 8,
          }}
        >
          <div className="spinner" style={{ width: 28, height: 28, borderWidth: 3 }} />
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
              Generating refactoring plan…
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
              Running pipeline: interpretation → candidate generation → ML scoring → dependency analysis
            </div>
          </div>
        </div>
      )}

      {/* Pipeline Viewer */}
      {trace && (
        <ErrorBoundary>
          <PipelineViewer trace={trace} id="pipeline-section" />
        </ErrorBoundary>
      )}

      {/* Results Viewer */}
      {plan && (
        <ErrorBoundary>
          <ResultsViewer
            plan={plan}
            stats={getPlanStats()}
            onDownload={handleDownloadPlan}
            onCopy={handleCopyPlan}
          />
        </ErrorBoundary>
      )}
    </div>
  );
}

