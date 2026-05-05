/** Settings.jsx — placeholder */
export default function Settings() {
  return (
    <div className="page-container">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">⚙️</div>
          <div>
            <div className="page-title">Settings</div>
            <div className="page-subtitle">Configure pipeline behaviour, agent parameters, and integrations.</div>
          </div>
        </div>
      </div>
      <div className="empty-state" style={{ flex: 1 }}>
        <span className="empty-icon">⚙️</span>
        <p>Agent configuration settings will be available here in the next release.</p>
      </div>
    </div>
  );
}
