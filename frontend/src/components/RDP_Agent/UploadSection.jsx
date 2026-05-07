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
        marginTop: '16px',
        background: 'linear-gradient(135deg, rgba(139,92,246,0.04) 0%, rgba(168,85,247,0.04) 100%)',
        borderColor: 'rgba(139,92,246,0.15)',
      }}
    >
      <form onSubmit={handleSubmit} encType="multipart/form-data">
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
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
              border: '2px dashed rgba(139,92,246,0.3)',
              borderRadius: '8px',
              padding: '32px 24px',
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.2s',
              background: 'rgba(139,92,246,0.02)',
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

            <div style={{ fontSize: '32px', marginBottom: '12px' }}>📄</div>
            <div style={{ fontSize: '14px', fontWeight: '500', color: 'var(--text-primary)' }}>
              Drag & drop quality report
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
              or click to browse
            </div>

            {selectedFile && (
              <div
                style={{
                  marginTop: '12px',
                  padding: '8px 12px',
                  background: 'rgba(34,197,94,0.1)',
                  borderRadius: '4px',
                  fontSize: '12px',
                  color: 'var(--color-ok)',
                  fontWeight: '500',
                }}
              >
                ✓ {selectedFile.name}
              </div>
            )}
          </div>

          {/* Right: Instructions & Button */}
          <div>
            <h3 style={{ fontSize: '16px', fontWeight: '600', marginBottom: '12px' }}>
              Generate Refactoring Plan
            </h3>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.5', marginBottom: '16px' }}>
              Upload a quality report from the Code Understanding Agent. The RDP Agent will:
            </p>
            <ul
              style={{
                fontSize: '13px',
                color: 'var(--text-secondary)',
                lineHeight: '1.6',
                marginBottom: '20px',
                paddingLeft: '20px',
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
                padding: '10px 20px',
                background: loading ? 'rgba(139,92,246,0.5)' : 'rgba(139,92,246,1)',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: '600',
                cursor: loading ? 'not-allowed' : 'pointer',
                opacity: loading ? 0.6 : 1,
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
