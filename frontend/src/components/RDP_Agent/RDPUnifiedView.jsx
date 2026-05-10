/**
 * RDPUnifiedView.jsx — Unified RDP Agent Interface
 * Single cohesive view matching HTML template clarity with 8-step pipeline visualization
 * Combines: Upload → Pipeline Trace → Results
 */

import { useState, useRef } from 'react';
import PipelineViewer from './PipelineViewer';

const PIPELINE_STEPS = [
  { id: 'input', number: '0', label: 'Input', icon: '📥' },
  { id: 'interpreter', number: '1', label: 'Problem Interpreter', icon: '🔍' },
  { id: 'candidates', number: '2-4', label: 'Candidate Generation & Scoring', icon: '💡' },
  { id: 'impact', number: '3b', label: 'Impact Prediction', icon: '📊' },
  { id: 'ml', number: '3c', label: 'ML Scoring', icon: '🤖' },
  { id: 'dependencies', number: '5-6', label: 'Dependency Analysis', icon: '🔗' },
  { id: 'plan', number: '7', label: 'Plan Generation', icon: '📋' },
];

export default function RDPUnifiedView({
  onFileSelect,
  onSubmit,
  loading = false,
  selectedFile = null,
  plan = null,
  trace = null,
  error = null,
  onDownload,
  onCopy,
}) {
  const [activeStep, setActiveStep] = useState('input');
  const fileInputRef = useRef(null);
  const dropZoneRef = useRef(null);

  // ============================================================================
  // UPLOAD HANDLERS
  // ============================================================================

  function handleDragOver(e) {
    e.preventDefault();
    if (dropZoneRef.current) dropZoneRef.current.classList.add('drag-over');
  }

  function handleDragLeave(e) {
    e.preventDefault();
    if (dropZoneRef.current) dropZoneRef.current.classList.remove('drag-over');
  }

  function handleDrop(e) {
    e.preventDefault();
    if (dropZoneRef.current) dropZoneRef.current.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      if (file.type === 'application/json' || file.name.endsWith('.json')) {
        onFileSelect(file);
      } else {
        alert('Please upload a JSON file');
      }
    }
  }

  function handleFileInputChange(e) {
    const files = e.target.files;
    if (files.length > 0) onFileSelect(files[0]);
  }

  function handleDropZoneClick() {
    fileInputRef.current?.click();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!selectedFile) return;
    await onSubmit(selectedFile);
  }

  // ============================================================================
  // RENDER: UPLOAD SECTION
  // ============================================================================

  function renderUploadSection() {
    return (
      <section
        style={{
          marginTop: '24px',
          background: 'linear-gradient(135deg, rgba(139,92,246,0.02) 0%, rgba(168,85,247,0.02) 100%)',
          border: '1px solid rgba(139,92,246,0.15)',
          borderRadius: '10px',
          overflow: 'hidden',
          padding: '20px',
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '8px' }}>Upload Quality Report</h2>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
          Select a JSON quality report from the Code Understanding Agent to generate a structured refactoring plan with full pipeline visualization.
        </p>

        <form
          onSubmit={handleSubmit}
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '24px',
            alignItems: 'stretch',
            minHeight: '240px',
          }}
        >
          {/* Left: Description & Button */}
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: '12px', padding: '16px 0' }}>
            <div>
              <h3 style={{ fontSize: '16px', fontWeight: '600', marginBottom: '8px' }}>Ready to Generate?</h3>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0' }}>
                Click the button to process your quality report and generate a detailed refactoring plan.
              </p>
            </div>
            <button
              type="submit"
              disabled={!selectedFile || loading}
              style={{
                padding: '12px 20px',
                background: 'rgba(139,92,246,1)',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: '600',
                cursor: selectedFile && !loading ? 'pointer' : 'not-allowed',
                opacity: selectedFile && !loading ? 1 : 0.5,
                transition: 'all 0.2s',
                width: 'fit-content',
              }}
              onMouseEnter={(e) => {
                if (selectedFile && !loading) e.target.style.background = 'rgba(139,92,246,0.9)';
              }}
              onMouseLeave={(e) => {
                if (selectedFile && !loading) e.target.style.background = 'rgba(139,92,246,1)';
              }}
            >
              {loading ? '⏳ Processing...' : '🚀 Generate Refactoring Plan'}
            </button>
          </div>

          {/* Right: Drop Zone */}
          <div
            ref={dropZoneRef}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={handleDropZoneClick}
            style={{
              padding: '32px',
              border: '2px dashed rgba(139,92,246,0.3)',
              borderRadius: '8px',
              background: 'rgba(139,92,246,0.02)',
              cursor: 'pointer',
              textAlign: 'center',
              transition: 'all 0.2s',
              fontSize: '13px',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
            }}
          >
            <div style={{ fontSize: '32px', marginBottom: '8px' }}>📂</div>
            <p style={{ fontWeight: '600', marginBottom: '4px' }}>Drag &amp; drop a <strong>.json</strong> file here</p>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '8px' }}>or</p>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
              style={{
                padding: '6px 12px',
                background: 'rgba(139,92,246,0.1)',
                border: '1px solid rgba(139,92,246,0.3)',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                color: 'rgba(139,92,246,1)',
                cursor: 'pointer',
              }}
            >
              Browse Files
            </button>
            {selectedFile && (
              <p style={{ marginTop: '8px', color: 'rgba(34,197,94,1)', fontWeight: '500' }}>
                ✓ {selectedFile.name}
              </p>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            onChange={handleFileInputChange}
            hidden
          />
        </form>
      </section>
    );
  }

  // ============================================================================
  // RENDER: PIPELINE TRACE SECTION
  // ============================================================================

  function renderPipelineSection() {
    if (!trace) {
      return null;
    }

    return (
      <section
        style={{
          marginTop: '24px',
          background: 'linear-gradient(135deg, rgba(59,130,246,0.02) 0%, rgba(96,165,250,0.02) 100%)',
          border: '1px solid rgba(59,130,246,0.15)',
          borderRadius: '10px',
          overflow: 'hidden',
          padding: '20px',
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '16px' }}>Pipeline Trace</h2>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
          See how each module processed your quality report step by step.
        </p>

        {/* Step Navigation */}
        <div
          style={{
            display: 'flex',
            gap: '8px',
            marginBottom: '24px',
            overflowX: 'auto',
            paddingBottom: '8px',
          }}
        >
          {PIPELINE_STEPS.map((step) => (
            <button
              key={step.id}
              onClick={() => setActiveStep(step.id)}
              style={{
                padding: '8px 12px',
                background: activeStep === step.id ? 'rgba(139,92,246,0.15)' : 'transparent',
                border: `1px solid ${activeStep === step.id ? 'rgba(139,92,246,0.5)' : 'rgba(139,92,246,0.2)'}`,
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                color: activeStep === step.id ? 'rgba(139,92,246,1)' : 'var(--text-secondary)',
                cursor: 'pointer',
                transition: 'all 0.2s',
                whiteSpace: 'nowrap',
              }}
            >
              {step.icon} [{step.number}] {step.label}
            </button>
          ))}
        </div>

        {/* Active Step Panel */}
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '6px', padding: '16px' }}>
          <PipelineViewer trace={trace} id={`pipeline-${activeStep}`} />
        </div>
      </section>
    );
  }

  // ============================================================================
  // RENDER: RESULTS SECTION
  // ============================================================================

  function renderResultsSection() {
    if (!plan) {
      return null;
    }

    return (
      <section
        style={{
          marginTop: '24px',
          background: 'linear-gradient(135deg, rgba(34,197,94,0.02) 0%, rgba(74,222,128,0.02) 100%)',
          border: '1px solid rgba(34,197,94,0.15)',
          borderRadius: '10px',
          overflow: 'hidden',
          padding: '20px',
        }}
      >
        <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '16px' }}>Refactoring Plan</h2>

        {/* Plan Actions */}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          {onDownload && (
            <button
              onClick={onDownload}
              style={{
                padding: '8px 16px',
                background: 'rgba(34,197,94,0.1)',
                border: '1px solid rgba(34,197,94,0.3)',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                color: 'rgba(34,197,94,1)',
                cursor: 'pointer',
              }}
            >
              ⬇️ Download Plan
            </button>
          )}
          {onCopy && (
            <button
              onClick={onCopy}
              style={{
                padding: '8px 16px',
                background: 'rgba(34,197,94,0.1)',
                border: '1px solid rgba(34,197,94,0.3)',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                color: 'rgba(34,197,94,1)',
                cursor: 'pointer',
              }}
            >
              📋 Copy Plan
            </button>
          )}
        </div>

        {/* Plan Content */}
        <div
          style={{
            background: 'rgba(0,0,0,0.1)',
            padding: '16px',
            borderRadius: '6px',
            fontSize: '12px',
            fontFamily: 'monospace',
            maxHeight: '600px',
            overflowY: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {typeof plan === 'string' ? plan : JSON.stringify(plan, null, 2)}
        </div>
      </section>
    );
  }

  // ============================================================================
  // RENDER: ERROR SECTION
  // ============================================================================

  function renderErrorSection() {
    if (!error) {
      return null;
    }

    return (
      <section
        style={{
          marginTop: '24px',
          background: 'linear-gradient(135deg, rgba(239,68,68,0.02) 0%, rgba(248,113,113,0.02) 100%)',
          border: '1px solid rgba(239,68,68,0.15)',
          borderRadius: '10px',
          overflow: 'hidden',
          padding: '20px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
          <span style={{ fontSize: '16px' }}>⚠️</span>
          <h3 style={{ fontSize: '14px', fontWeight: '600', margin: '0', color: 'rgba(239,68,68,1)' }}>Error</h3>
        </div>
        <p style={{ fontSize: '12px', color: 'rgba(239,68,68,0.9)', margin: '0', whiteSpace: 'pre-wrap' }}>
          {error}
        </p>
      </section>
    );
  }

  // ============================================================================
  // MAIN RENDER
  // ============================================================================

  return (
    <div style={{ paddingBottom: '24px' }}>
      {renderUploadSection()}
      {renderErrorSection()}
      {renderPipelineSection()}
      {renderResultsSection()}
    </div>
  );
}
