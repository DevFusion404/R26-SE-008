/**
 * analysisHistory.js
 * ------------------
 * The list of repositories loaded on Repository Input (localStorage
 * `cuqa_analysis_history`). Each entry gets its quality score from CUQA's
 * own quality report once the CUQA page has analysed it; until then the
 * score is null and pages show "—". Older entries saved a placeholder score
 * (a file-count formula), so only entries marked `score_source: 'cuqa'`
 * count as measured.
 */

export const HISTORY_KEY = 'cuqa_analysis_history';
export const MAX_HISTORY = 20;

export function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function saveHistory(entries) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
  } catch { /* storage full or blocked — history is a convenience */ }
}

export function scoreColor(score) {
  if (score == null) return 'var(--text-muted)';
  if (score >= 80) return 'var(--color-ok)';
  if (score >= 60) return 'var(--color-medium)';
  return 'var(--color-critical)';
}

/** The measured CUQA score of an entry, or null when it was never measured. */
export const measuredScore = (entry) =>
  entry?.score_source === 'cuqa' && typeof entry.score === 'number' ? entry.score : null;

/** Store CUQA's real result on the entry the repository was loaded as. */
export function recordCuqaScore(entryId, report) {
  if (!entryId || !report?.summary) return;
  const avg = report.summary.average_quality_score;
  if (typeof avg !== 'number') return;
  const list = loadHistory();
  const i = list.findIndex((e) => e.id === entryId);
  if (i < 0) return;
  list[i] = {
    ...list[i],
    score: Math.round(avg),
    score_source: 'cuqa',
    smells: report.summary.total_code_smells ?? null,
    files_analyzed: report.summary.files_analyzed ?? null,
  };
  saveHistory(list);
}
