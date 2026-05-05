/** Reports.jsx — placeholder */
export default function Reports() {
  return (
    <div className="page-container">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">📋</div>
          <div>
            <div className="page-title">Reports</div>
            <div className="page-subtitle">Exportable quality reports generated across all pipeline runs.</div>
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-outline">↓ Export All Reports</button>
        </div>
      </div>
      <div className="empty-state" style={{ flex: 1 }}>
        <span className="empty-icon">📋</span>
        <p>Run an analysis via the CUQA Agent to generate reports here.</p>
      </div>
    </div>
  );
}
