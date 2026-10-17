/**
 * CodeSmellContextBar.jsx
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The compact strip that opens the Code Smell Review stage. The DIWO shell
 * above it already prints the stage title, so this does not repeat one; it
 * says which report is on screen and what is in it:
 *
 *   ● Live CUQA quality report · repo-name · via DIWO        [Re-analyze]
 *     Files 42 | Smells 57 | High 4 | Avg quality 91.2% | Categories 5 | Java
 *
 * Every figure comes from the report or the taxonomy the page already holds.
 * Nothing here is hardcoded — a figure that is missing is left out.
 */

import { T, tint, eyebrow, ghostButton } from "../codeSmellTheme.js";
import { Chip, Spinner } from "../codeSmellUi.jsx";

// Section labels a step larger than the shared eyebrow, to match the
// Smell Selection section beside this panel.
const eyebrowLg = { ...eyebrow, fontSize: 12 };


const VIA_LABEL = { "diwo-proxy": "via DIWO orchestration backend" };

export default function CodeSmellContextBar({
  origin, report, meta, loading, error, onRefresh, reseeds, stats,
}) {
  const isSample = origin === "sample";
  const tone = isSample ? T.medium : origin === "workflow" ? T.info : T.accent;

  const label = isSample
    ? "Sample report — bundled data, CUQA agent unavailable"
    : origin === "workflow"
      ? "Report from the current DIWO workflow"
      : "Live CUQA quality report";

  const status = loading
    ? { text: "Analyzing…", tone: T.info }
    : error
      ? { text: "Refresh failed", tone: T.high }
      : isSample
        ? { text: "Sample data", tone: T.medium }
        : { text: "Analysis complete", tone: T.low };

  const details = [report?.report_type, meta?.via && VIA_LABEL[meta.via]].filter(Boolean);

  return (
    <section
      aria-label="Analysis context"
      style={{
        background: T.surface, borderRadius: 12, marginBottom: 16, boxShadow: T.shadowSm,
        border: `1px solid ${isSample ? tint(T.medium, 50) : T.border}`,
        overflow: "hidden",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        padding: "12px 16px",
      }}>
        <span aria-hidden="true" style={{
          width: 9, height: 9, borderRadius: "50%", background: tone, flexShrink: 0,
          boxShadow: `0 0 0 3px ${tint(tone, 20)}`,
        }} />
        <div style={{ minWidth: 0, flex: "1 1 260px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {report?.repo_name && (
              <span style={{ fontSize: 16, fontWeight: 800, color: T.text, fontFamily: T.mono }}>
                {report.repo_name}
              </span>
            )}
            <span style={{ fontSize: 14, color: T.text2, fontWeight: 600 }}>{label}</span>
            <Chip tone={status.tone}>{status.text}</Chip>
          </div>
          {details.length > 0 && (
            <div style={{ fontSize: 13, color: T.text3, marginTop: 2 }}>
              {details.join(" · ")}
            </div>
          )}
          {error && (
            <div role="alert" style={{ fontSize: 13, color: T.high, marginTop: 3 }}>
              Refresh failed: {error.message}
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="dw-focus dw-hover"
          title={
            reseeds
              ? "Re-read the repository currently loaded in CUQA and start a fresh workflow for it"
              : "Re-read the quality report from the CUQA agent"
          }
          style={ghostButton(!loading, { fontSize: 13, cursor: loading ? "wait" : "pointer" })}
        >
          {loading ? <Spinner size={12} /> : <span aria-hidden="true">↻</span>}
          {loading ? "Analyzing…" : isSample ? "Retry CUQA" : "Re-analyze repository"}
        </button>
      </div>

      <dl style={{
        margin: 0, display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))",
        borderTop: `1px solid ${T.border}`, background: T.sunken,
      }}>
        <Metric label="Files analysed" value={stats.files} />
        <Metric label="Detected smells" value={stats.smells} />
        <Metric label="High severity" value={stats.high} tone={stats.high > 0 ? T.high : undefined} />
        {stats.quality !== null && <Metric label="Avg quality" value={stats.quality} />}
        {typeof stats.categories === "number" && <Metric label="Categories" value={stats.categories} />}
        {stats.languages.length > 0 && (
          <div style={metricCell}>
            <dt style={eyebrowLg}>Languages</dt>
            <dd style={{ margin: "5px 0 0", display: "flex", gap: 4, flexWrap: "wrap" }}>
              {stats.languages.map((l) => <Chip key={l}>{l}</Chip>)}
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}

const metricCell = {
  padding: "10px 16px", minWidth: 0,
  boxShadow: `inset -1px 0 0 ${T.border}`,
};

function Metric({ label, value, tone }) {
  return (
    <div style={metricCell}>
      <dt style={eyebrowLg}>{label}</dt>
      <dd style={{ margin: "3px 0 0", fontSize: 22, fontWeight: 800, fontFamily: T.mono, color: tone || T.text, lineHeight: 1.2 }}>
        {value}
      </dd>
    </div>
  );
}
