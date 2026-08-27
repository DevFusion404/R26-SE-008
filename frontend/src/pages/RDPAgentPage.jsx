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

// ── localStorage & sessionStorage key constants ─────────────────────────────
const RDP_SESSION_KEY = 'rdp_last_session';   // stores last plan
const RDP_TRACE_KEY   = 'rdp_last_trace';     // stores last trace in sessionStorage across tab navigation
const RDP_HISTORY_KEY = 'rdp_plan_history';   // stores list of all past plans
const MAX_HISTORY     = 20;

// ── Session helpers (save/restore plan + trace across tab navigation and refresh) ────
function loadSession() {
  try {
    const rawPlan  = sessionStorage.getItem(RDP_SESSION_KEY) || localStorage.getItem(RDP_SESSION_KEY);
    const rawTrace = sessionStorage.getItem(RDP_TRACE_KEY);
    const parsedPlan  = rawPlan ? JSON.parse(rawPlan) : null;
    const parsedTrace = rawTrace ? JSON.parse(rawTrace) : null;
    return {
      plan: parsedPlan?.plan || null,
      sourceFileName: parsedPlan?.sourceFileName || null,
      trace: parsedTrace || null,
    };
  } catch {
    return { plan: null, trace: null, sourceFileName: null };
  }
}

function saveSession(plan, trace, sourceFileName) {
  const payload = JSON.stringify({
    plan,
    sourceFileName,
    savedAt: new Date().toISOString(),
  });

  try {
    sessionStorage.setItem(RDP_SESSION_KEY, payload);
    localStorage.setItem(RDP_SESSION_KEY, payload);
  } catch {
    try {
      localStorage.removeItem(RDP_SESSION_KEY);
      sessionStorage.setItem(RDP_SESSION_KEY, payload);
    } catch { /* ignore */ }
  }

  if (trace) {
    try {
      sessionStorage.setItem(RDP_TRACE_KEY, JSON.stringify(trace));
    } catch {
      // Fallback: store clean essential trace if sessionStorage quota is tight
      try {
        const compactTrace = {
          input_summary: trace.input_summary,
          problem_interpretation: trace.problem_interpretation,
          candidate_generation: trace.candidate_generation,
          impact_prediction: trace.impact_prediction,
          ml_prediction: trace.ml_prediction,
          mcda_selection: trace.mcda_selection,
          dependency_analysis: trace.dependency_analysis,
          plan_generation: trace.plan_generation,
        };
        sessionStorage.setItem(RDP_TRACE_KEY, JSON.stringify(compactTrace));
      } catch { /* ignore */ }
    }
  }
}

function clearRdpSession() {
  localStorage.removeItem(RDP_SESSION_KEY);
  sessionStorage.removeItem(RDP_SESSION_KEY);
  sessionStorage.removeItem(RDP_TRACE_KEY);
}

// ── Plan history helpers (mirrors CUQA cuqa_analysis_history pattern) ────────
function loadRdpHistory() {
  try { return JSON.parse(localStorage.getItem(RDP_HISTORY_KEY) || '[]'); }
  catch { return []; }
}

function saveRdpHistory(entries) {
  localStorage.setItem(RDP_HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
}

function appendRdpHistory(plan, sourceFileName) {
  const entry = {
    id:           plan.plan_id || `rdp_${Date.now()}`,
    plan_id:      plan.plan_id,
    target:       plan.target || 'unknown',
    date:         new Date().toISOString(),
    step_count:   plan.steps?.length || 0,
    refactorings: [...new Set((plan.steps || []).map(s => s.refactoring))].slice(0, 3),
    source_file:  sourceFileName || 'unknown',
    summary:      plan.summary || '',
  };
  const updated = [entry, ...loadRdpHistory()].slice(0, MAX_HISTORY);
  saveRdpHistory(updated);
  return entry;
}

export default function RDPAgentPage({ repoLoaded, repoMeta, preloadedReport, onClearPreloaded, onPlanGenerated }) {
  // Restore session (plan + trace) so tab switching or page refresh doesn't lose the results
  const session = loadSession();
  const [loading,           setLoading]           = useState(false);
  const [error,             setError]             = useState(null);
  const [plan,              setPlan]              = useState(() => session.plan);
  const [trace,             setTrace]             = useState(() => session.trace);
  const [selectedFile,      setSelectedFile]      = useState(null);
  const [cuqaBannerVisible, setCuqaBannerVisible] = useState(() => !!session.plan);
  const [sessionRestored,   setSessionRestored]   = useState(() => !!session.plan);
  const fileInputRef = useRef(null);

  /**
   * When a preloaded report is piped in from CUQA, auto-generate the plan
   */
  useEffect(() => {
    if (preloadedReport) {
      setCuqaBannerVisible(true);
      handleGeneratePlan(preloadedReport, null).finally(() => {
        // Clear the preloaded report after processing — but keep banner visible
        onClearPreloaded?.();
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preloadedReport]);

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

      // Persist to localStorage: session only (plan without trace — trace is too large)
      // History stores metadata only (no full plan) to avoid quota errors.
      const sourceFileName = file?.name || (result.plan?.target) || 'piped-from-cuqa';
      saveSession(result.plan, result.trace, sourceFileName);
      appendRdpHistory(result.plan, sourceFileName);
      setSessionRestored(false); // this is a fresh generation

      // Notify parent that RDP has finished (updates pipeline state / sidebar dot)
      onPlanGenerated?.();

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
      {cuqaBannerVisible && (
        <div
          style={{
            marginTop: 12,
            borderRadius: 10,
            border: plan
              ? '1px solid rgba(34,197,94,0.4)'
              : loading
              ? '1px solid rgba(139,92,246,0.5)'
              : '1px solid rgba(0,212,232,0.35)',
            background: plan
              ? 'linear-gradient(135deg, rgba(34,197,94,0.08), rgba(16,185,129,0.04))'
              : loading
              ? 'linear-gradient(135deg, rgba(139,92,246,0.10), rgba(168,85,247,0.06))'
              : 'linear-gradient(135deg, rgba(0,212,232,0.08), rgba(6,182,212,0.04))',
            overflow: 'hidden',
          }}
        >
          {/* Banner header */}
          <div style={{
            padding: '12px 18px',
            display: 'flex', alignItems: 'center', gap: 12,
            borderBottom: '1px solid rgba(139,92,246,0.15)',
          }}>
            <span style={{ fontSize: 18 }}>
              {plan ? '✅' : loading ? '⚡' : '🔗'}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: 13, fontWeight: 700,
                color: plan ? '#4ade80' : loading ? '#c4b5fd' : '#00d4e8',
              }}>
                {plan
                  ? 'Refactoring Plan Generated Successfully'
                  : loading
                  ? 'Automated Pipeline Active — Generating Plan…'
                  : 'Quality Report Received from CUQA Agent'}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                {plan
                  ? 'The RDP Agent has completed its analysis. Review the results below.'
                  : loading
                  ? 'Running pipeline: problem interpretation → candidate generation → ML scoring → dependency analysis'
                  : 'Ready to generate refactoring plan automatically.'}
              </div>
            </div>
            {/* Pipeline stage indicator */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
              <span style={{
                padding: '3px 10px', borderRadius: 'var(--r-full)', fontSize: 10, fontWeight: 700,
                background: 'rgba(0,212,232,0.15)', color: '#00d4e8', border: '1px solid rgba(0,212,232,0.3)',
              }}>① CUQA ✓</span>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>→</span>
              <span style={{
                padding: '3px 10px', borderRadius: 'var(--r-full)', fontSize: 10, fontWeight: 700,
                background: plan
                  ? 'rgba(34,197,94,0.2)'
                  : loading
                  ? 'rgba(139,92,246,0.25)'
                  : 'rgba(139,92,246,0.10)',
                color: plan ? '#4ade80' : loading ? '#c4b5fd' : '#7c6aaa',
                border: `1px solid ${plan ? 'rgba(34,197,94,0.4)' : loading ? 'rgba(139,92,246,0.5)' : 'rgba(139,92,246,0.2)'}`,
                animation: loading ? 'pulse 1.2s infinite' : 'none',
              }}>② RDP {plan ? '✓' : loading ? '…' : '—'}</span>
            </div>
            <button
              onClick={() => {
                // Bug fix: closing the CUQA banner also clears the session so the
                // Transformation Agent won't pick up this plan again on a future run.
                setCuqaBannerVisible(false);
                clearRdpSession();
                setPlan(null);
                setTrace(null);
              }}
              style={{
                background: 'none', border: 'none',
                color: 'var(--text-muted)', cursor: 'pointer', fontSize: 16, lineHeight: 1,
              }}
              title="Dismiss"
            >✕</button>
          </div>

          {/* Loading progress bar */}
          {loading && (
            <div style={{ height: 3, background: 'rgba(139,92,246,0.15)', position: 'relative', overflow: 'hidden' }}>
              <div style={{
                position: 'absolute', top: 0, left: '-100%',
                width: '60%', height: '100%',
                background: 'linear-gradient(90deg, transparent, #a855f7, transparent)',
                animation: 'shimmer 1.5s infinite',
              }} />
            </div>
          )}
        </div>
      )}

      {/* Error Alert */}
      {error && <ErrorAlert message={error} onClose={() => setError(null)} />}

      {/* Restored session banner — appears after a page refresh when a saved plan is shown */}
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
              borderRadius: 4, color: '#ef4444', fontSize: 11,
              padding: '4px 10px', cursor: 'pointer',
            }}
          >
            ✕ Clear
          </button>
        </div>
      )}

      {/* Upload Section — always visible so the user can generate a new plan at any time */}
      {!cuqaBannerVisible && (
        <UploadSection
          onFileSelect={handleFileSelect}
          onSubmit={handleUploadSubmit}
          loading={loading}
          selectedFile={selectedFile}
        />
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

