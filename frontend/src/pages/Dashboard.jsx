/** Dashboard.jsx — placeholder for the metrics dashboard */
export default function Dashboard() {
  return (
    <div className="page-container">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">📊</div>
          <div>
            <div className="page-title">Dashboard</div>
            <div className="page-subtitle">Aggregate view of all agent analysis runs and system metrics.</div>
          </div>
        </div>
      </div>
      <div className="empty-state" style={{ flex: 1 }}>
        <span className="empty-icon">🚧</span>
        <p>Dashboard widgets will appear here once an analysis has been completed via the CUQA Agent.</p>
      </div>
    </div>
  );
}
