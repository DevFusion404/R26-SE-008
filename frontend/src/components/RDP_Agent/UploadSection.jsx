/**
 * UploadSection.jsx — File Upload with Drag & Drop
 * Component for uploading quality report JSON files
 */

import { useRef } from 'react';

export default function UploadSection({
  onFileSelect,
  onSubmit,
  loading = false,
  selectedFile = null,
}) {
  const fileInputRef = useRef(null);
  const dropZoneRef = useRef(null);

  /**
   * Handle drag over
   */
  function handleDragOver(e) {
    e.preventDefault();
    if (dropZoneRef.current) {
      dropZoneRef.current.classList.add('drag-over');
    }
  }

  /**
   * Handle drag leave
   */
  function handleDragLeave(e) {
    e.preventDefault();
    if (dropZoneRef.current) {
      dropZoneRef.current.classList.remove('drag-over');
    }
  }

  /**
   * Handle drop
   */
  function handleDrop(e) {
    e.preventDefault();
    if (dropZoneRef.current) {
      dropZoneRef.current.classList.remove('drag-over');
    }

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

  /**
   * Handle file input change
   */
  function handleFileInputChange(e) {
    const files = e.target.files;
    if (files.length > 0) {
      onFileSelect(files[0]);
    }
  }

  /**
   * Handle form submit
   */
  async function handleSubmit(e) {
    e.preventDefault();
    if (!selectedFile) return;
    await onSubmit(selectedFile);
  }

  /**
   * Handle click on drop zone
   */
  function handleDropZoneClick() {
    fileInputRef.current?.click();
  }

  return (
    <section
      className="card"
      style={{
        marginTop: '20px',
        background: 'var(--rdp-card-bg)',
        border: '1px solid var(--rdp-card-border)',
        borderRadius: '12px',
        padding: '24px',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <form onSubmit={handleSubmit} encType="multipart/form-data">
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '24px',
            alignItems: 'center',
          }}
        >
          {/* Left: Drop Zone */}
          <div
            ref={dropZoneRef}
            onClick={handleDropZoneClick}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            style={{
              position: 'relative',
              border: '2px dashed var(--color-rdp)',
              borderRadius: '10px',
              padding: '36px 24px',
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.2s',
              background: 'var(--rdp-meta-bg)',
            }}
            className="drop-zone"
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleFileInputChange}
              style={{ display: 'none' }}
            />

            <div style={{ fontSize: '36px', marginBottom: '12px' }}>📄</div>
            <div style={{ fontSize: '16px', fontWeight: '700', color: 'var(--text-primary)' }}>
              Drag &amp; drop quality report
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '6px' }}>
              or click to browse (.json file)
            </div>

            {selectedFile && (
              <div
                style={{
                  marginTop: '14px',
                  padding: '10px 14px',
                  background: 'var(--rdp-winner-bg)',
                  border: '1px solid var(--rdp-winner-border)',
                  borderRadius: '6px',
                  fontSize: '13px',
                  color: 'var(--rdp-winner-text)',
                  fontWeight: '700',
                }}
              >
                ✓ {selectedFile.name}
              </div>
            )}
          </div>

          {/* Right: Instructions & Button */}
          <div>
            <h3 style={{ fontSize: '18px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '12px' }}>
              Generate Refactoring Plan
            </h3>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: '1.6', marginBottom: '16px' }}>
              Upload a quality report from the Code Understanding Agent. The RDP Agent will:
            </p>
            <ul
              style={{
                fontSize: '14px',
                color: 'var(--text-secondary)',
                lineHeight: '1.7',
                marginBottom: '20px',
                paddingLeft: '22px',
              }}
            >
              <li>Analyze code smells and quality metrics</li>
              <li>Generate refactoring candidates</li>
              <li>Score using ML + impact prediction</li>
              <li>Plan safe transformation order</li>
            </ul>

            <button
              type="submit"
              disabled={!selectedFile || loading}
              style={{
                padding: '12px 24px',
                background: loading ? 'rgba(124, 58, 237, 0.5)' : 'var(--color-rdp)',
                color: '#ffffff',
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '700',
                cursor: loading ? 'not-allowed' : 'pointer',
                opacity: loading ? 0.6 : 1,
                boxShadow: '0 2px 10px rgba(124, 58, 237, 0.3)',
                transition: 'all 0.2s',
              }}
            >
              {loading ? (
                <>
                  <span style={{ display: 'inline-block', marginRight: '8px' }}>⏳</span>
                  Generating Plan...
                </>
              ) : (
                <>
                  <span style={{ display: 'inline-block', marginRight: '8px' }}>🚀</span>
                  Generate Plan
                </>
              )}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
