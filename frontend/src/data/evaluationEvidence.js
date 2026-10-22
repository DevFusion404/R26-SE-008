/**
 * evaluationEvidence.js
 * ---------------------
 * Recorded evaluation results shown on the Evaluation page. Every block
 * names the file it was copied from and when it was produced. Nothing here
 * is estimated; when a result is not available it is left out.
 */

// CUQA ML smell model — test split of unseen repositories (MLCQ, 80/20 by repository).
// The page replaces these with the live values from GET /api/ml-model when CUQA answers.
export const ML_MODEL = {
  source: 'agents/cuqa_agent/training/results/evaluation_tables.md',
  date: 'model version 2026-10-08',
  trainRepos: 398,
  testRepos: 100,
  smells: [
    { key: 'LongMethod',  name: 'Long Method',        enabled: true,  precision: 0.829, recall: 0.659, f1: 0.734, roc_auc: 0.982 },
    { key: 'LargeClass',  name: 'Blob (Large Class)', enabled: true,  precision: 0.458, recall: 0.614, f1: 0.524, roc_auc: 0.904 },
    { key: 'DataClass',   name: 'Data Class',         enabled: true,  precision: 0.702, recall: 0.714, f1: 0.708, roc_auc: 0.941 },
    { key: 'FeatureEnvy', name: 'Feature Envy',       enabled: false, precision: 0.154, recall: 0.333, f1: 0.211, roc_auc: 0.871 },
  ],
};

// F1 on the same test repositories: CUQA rule, best single-metric threshold, ML model.
// The Data Class and Feature Envy rules (Lanza and Marinescu strategies, java_rules.py) were
// added after the model; their F1 comes from results/combined_detector.md.
export const ML_VS_BASELINES = {
  source: 'agents/cuqa_agent/training/results/evaluation_tables.md, results/combined_detector.md',
  rows: [
    { smell: 'Long Method',        rule: 0.684, threshold: 0.750, thresholdName: 'loc ≥ 30',       ml: 0.734 },
    { smell: 'Blob (Large Class)', rule: 0.404, threshold: 0.515, thresholdName: 'sloc ≥ 156',     ml: 0.524 },
    { smell: 'Data Class',         rule: 0.442, threshold: 0.571, thresholdName: 'accessor ratio ≥ 0.52', ml: 0.708 },
    { smell: 'Feature Envy',       rule: 0.129, threshold: 0.263, thresholdName: 'calls on locals ≥ 7', ml: 0.211 },
  ],
};

// Automated test suites, one run on the main branch.
export const TEST_SUITES = {
  source: 'Common_Report_R26-SE-008/evidence/test_summary.csv',
  date: '8 Oct 2026, main branch',
  rows: [
    { suite: 'CUQA',       agent: 'cuqa',  tests: 297, passed: 297, failed: 0,  skipped: 0, seconds: 87.8 },
    { suite: 'RDP',        agent: 'rdp',   tests: 65,  passed: 65,  failed: 0,  skipped: 0, seconds: 47.3 },
    { suite: 'SCTVA',      agent: 'sctva', tests: 637, passed: 615, failed: 21, skipped: 1, seconds: 335.6 },
    { suite: 'DIWO',       agent: 'diwo',  tests: 506, passed: 506, failed: 0,  skipped: 0, seconds: 24.8 },
    { suite: 'Evaluation', agent: null,    tests: 9,   passed: 9,   failed: 0,  skipped: 0, seconds: 0.2 },
  ],
};

// Processing time, five runs per repository (P95 in milliseconds) against the NFR targets.
export const PERFORMANCE = {
  source: 'Common_Report_R26-SE-008/evidence/nfr_performance_summary.csv',
  repos: [
    { id: 'ant',  name: 'Apache Ant 1.8.4 (Java)' },
    { id: 'curl', name: 'curl 7.29.0 (C)' },
  ],
  rows: [
    { agent: 'cuqa',  name: 'CUQA',  target: 60000,  p95: { ant: 22088,  curl: 18946 },  success: { ant: 1.0, curl: 1.0 } },
    { agent: 'rdp',   name: 'RDP',   target: 60000,  p95: { ant: 25391,  curl: 58089 },  success: { ant: 1.0, curl: 1.0 } },
    { agent: 'sctva', name: 'SCTVA', target: 180000, p95: { ant: 145905, curl: 68094 },  success: { ant: 1.0, curl: 0.4 } },
    { agent: 'diwo',  name: 'DIWO',  target: 300000, p95: { ant: 244893, curl: 196635 }, success: { ant: 0.6, curl: 0.4 } },
  ],
};

// User feedback survey, 21 responses, 1 = strongly disagree … 5 = strongly agree.
export const SURVEY = {
  source: 'Common_Report_R26-SE-008/evidence/survey_group_parts.csv',
  date: '21 responses, 27–30 Aug 2026',
  rows: [
    { part: 'CUQA: analysis and quality report',   agent: 'cuqa',  mean: 3.91, agree: 65,  neutral: 37, disagree: 3 },
    { part: 'RDP: refactoring plan',               agent: 'rdp',   mean: 4.03, agree: 76,  neutral: 27, disagree: 2 },
    { part: 'SCTVA: transformation and validation', agent: 'sctva', mean: 3.90, agree: 58,  neutral: 23, disagree: 3 },
    { part: 'DIWO: workflow and approval',         agent: 'diwo',  mean: 3.91, agree: 70,  neutral: 34, disagree: 1 },
    { part: 'Complete system',                     agent: null,    mean: 3.96, agree: 114, neutral: 45, disagree: 9 },
    { part: 'Overall ratings',                     agent: null,    mean: 4.08, agree: 80,  neutral: 23, disagree: 2 },
  ],
};

export const AGENT_COLOR = {
  cuqa: 'var(--color-cuqa)',
  rdp: 'var(--color-rdp)',
  sctva: 'var(--color-transform)',
  diwo: 'var(--color-orchestrate)',
};
