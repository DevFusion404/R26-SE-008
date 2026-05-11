import { useMemo, useRef, useState } from 'react';
import SCTVAAgentService from '../../services/sctvaAgentService';
import transformBadge from '../../assets/transform-badge.svg';
import './SCTVAAgentPage.css';

const PIPELINE_STAGES = [
  {
    id: 'plan',
    title: 'Plan Intake',
    description: 'Normalize the refactoring plan and actions.',
  },
  {
    id: 'analyze',
    title: 'Source Analysis',
    description: 'Parse source and map language-specific operations.',
  },
  {
    id: 'transform',
    title: 'Apply Transformations',
    description: 'Execute refactoring actions on the source files.',
  },
  {
    id: 'syntax',
    title: 'Syntax Validation',
    description: 'Check parsing/compilation safety.',
  },
  {
    id: 'structural',
    title: 'Structural Validation',
    description: 'Compare structural similarity and dependencies.',
  },
  {
    id: 'behavioral',
    title: 'Behavioral Validation',
    description: 'Run behavioral fingerprints or static checks.',
  },
  {
    id: 'invariant',
    title: 'Invariant Mining',
    description: 'Confirm invariants match across versions.',
  },
  {
    id: 'finalize',
    title: 'Finalize Output',
    description: 'Accept the refactor or roll back safely.',
  },
];

function pipelineLabel(status, stepNumber) {
  const prefix = status === 'completed'
    ? 'Completed'
    : status === 'active'
      ? 'In Progress'
      : status === 'failed'
        ? 'Failed'
        : 'Pending';
  return `${prefix} · Stage ${stepNumber}`;
}

function validationStageStatus(stepKey, validation, phase) {
  if (phase !== 'done') return 'pending';

  const step = validation?.[stepKey];
  if (!step) return 'failed';

  const details = step.details || {};
  const reportedStatus = stepKey === 'behavioral'
    ? String(details.fingerprint_status || '')
    : stepKey === 'invariant'
      ? String(details.status || '')
      : '';

  if (reportedStatus.toLowerCase() === 'skipped') return 'failed';

  return step.passed ? 'completed' : 'failed';
}

function buildPipelineTimeline({
  phase,
  planLoaded,
  planStepCount,
  result,
}) {
  const validation = result?.validation || null;
  const rollback = Boolean(result?.rollback_occurred);

  return PIPELINE_STAGES.map((stage, index) => {
    let status = 'pending';

    if (stage.id === 'plan') {
      status = planLoaded || phase !== 'idle' ? 'completed' : 'pending';
    } else if (stage.id === 'analyze') {
      status = phase === 'idle' ? 'pending' : 'completed';
    } else if (stage.id === 'transform') {
      status = phase === 'running' ? 'active' : phase === 'done' ? 'completed' : 'pending';
    } else if (['syntax', 'structural', 'behavioral', 'invariant'].includes(stage.id)) {
      status = validationStageStatus(stage.id, validation, phase);
    } else if (stage.id === 'finalize') {
      if (phase === 'done') {
        status = rollback ? 'failed' : 'completed';
      } else {
        status = 'pending';
      }
    }

    let description = stage.description;
    if (stage.id === 'plan' && planLoaded && planStepCount > 0) {
      description = `Loaded ${planStepCount} steps from the refactoring plan.`;
    }
    if (stage.id === 'finalize' && phase === 'done') {
      description = rollback
        ? 'Rollback executed due to failed validation.'
        : 'Final output accepted after successful validation.';
    }

    return {
      status,
      label: pipelineLabel(status, index + 1),
      title: stage.title,
      description,
    };
  });
}

const DEFAULT_TIMELINE = buildPipelineTimeline({
  phase: 'idle',
  planLoaded: false,
  planStepCount: 0,
  result: null,
});

const DEFAULT_VALIDATION = [
  { label: 'Syntax Validation', state: 'neutral', text: 'Waiting for execution' },
  { label: 'Structural Equivalence', state: 'neutral', text: 'Waiting for execution' },
  { label: 'Behavioral Preservation', state: 'neutral', text: 'Waiting for execution' },
  { label: 'Invariant Mining', state: 'neutral', text: 'Waiting for execution' },
];

const EMPTY_CODE_STATE = 'Run the transformation to view code changes.';
const EMPTY_REPORT_STATE = 'Safety messages will appear here after execution.';
const DIFF_CODE_INLINE_STYLE = {
  background: 'transparent',
  padding: 0,
  borderRadius: 0,
  boxShadow: 'none',
  display: 'inline',
};

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function toPascalCase(value) {
  const parts = String(value)
    .replace(/[^A-Za-z0-9]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return 'Target';
  return parts.map(part => part.charAt(0).toUpperCase() + part.slice(1)).join('');
}

function sanitizeIdentifier(value) {
  const cleaned = String(value).trim().replace(/[^A-Za-z0-9_]/g, '_');
  if (!cleaned) return 'RenamedSymbol';
  return /^[0-9]/.test(cleaned) ? `R_${cleaned}` : cleaned;
}

function renameAction(step, oldName, newName) {
  return {
    action_type: 'rename_symbol',
    parameters: {
      old_name: String(oldName),
      new_name: sanitizeIdentifier(String(newName)),
    },
    source_step_id: step.step_id ?? null,
    source_refactoring: step.refactoring ?? null,
    warnings: [],
  };
}

function normalizeAction(action) {
  if (!action || typeof action !== 'object' || Array.isArray(action)) {
    throw new Error('Each action must be an object.');
  }

  if (!action.action_type) {
    throw new Error('Each action must include action_type.');
  }

  return {
    action_type: String(action.action_type).trim().toLowerCase(),
    parameters: isPlainObject(action.parameters) ? action.parameters : {},
    source_step_id: action.source_step_id ?? null,
    source_refactoring: action.source_refactoring ?? null,
    warnings: Array.isArray(action.warnings) ? action.warnings.map(String) : [],
  };
}

function normalizeStep(step) {
  if (!step || typeof step !== 'object' || Array.isArray(step)) return null;

  const refactoringRaw = String(step.refactoring || '').trim();
  const refactoring = refactoringRaw.toLowerCase();
  const params = isPlainObject(step.parameters) ? step.parameters : {};
  const target = isPlainObject(step.target) ? step.target : {};

  if (!refactoring) return null;

  if (refactoring.startsWith('fault injection')) {
    const originalLogic = params.original_logic || params.old_logic;
    const faultyLogic = params.faulty_logic || params.new_logic;

    if (!originalLogic || faultyLogic === undefined || faultyLogic === null) return null;

    return {
      action_type: 'fault_injection',
      parameters: {
        original_logic: String(originalLogic),
        faulty_logic: String(faultyLogic),
        change_type: params.change_type ?? null,
        purpose: params.purpose ?? null,
        target_class: target.class ?? null,
        target_method: target.method ?? null,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  const renameTypes = [
    'rename method',
    'rename variable',
    'rename class',
    'rename parameter',
    'rename field',
    'rename attribute',
  ];

  if (renameTypes.includes(refactoring)) {
    const oldName = params.old_name || target.method || target.class;
    const newName = params.new_name || params.renamed_to;
    if (!oldName || !newName) return null;
    return renameAction(step, oldName, newName);
  }

  if (refactoring === 'extract method') {
    const oldName = target.method || params.method;
    if (!oldName) return null;
    return renameAction(step, oldName, params.new_method_name || `${oldName}Core`);
  }

  if (refactoring === 'extract class') {
    const oldName = params.source_class || target.class;
    if (!oldName) return null;
    return renameAction(step, oldName, params.new_class_name || `${oldName}Extracted`);
  }

  if (refactoring === 'move method') {
    const oldName = params.method || target.method;
    if (!oldName) return null;
    const destination = params.destination_class;
    const suffix = destination && String(destination).trim() !== '<inferred_target_class>'
      ? toPascalCase(String(destination))
      : 'Moved';
    return renameAction(step, oldName, `${oldName}In${suffix}`);
  }

  if (refactoring === 'replace conditional with polymorphism') {
    const oldName = target.method || params.method;
    if (!oldName) return null;
    return renameAction(step, oldName, `${oldName}Polymorphic`);
  }

  if (refactoring === 'introduce parameter object') {
    const oldName = params.method || target.method;
    if (!oldName) return null;
    const suffix = params.parameter_object_name ? toPascalCase(String(params.parameter_object_name)) : 'ParamObject';
    return renameAction(step, oldName, `${oldName}With${suffix}`);
  }

  if ([
    'hide delegate',
    'replace data value with object',
    'inline class',
    'collapse hierarchy',
    'pull up method',
    'replace parameter with method call',
  ].includes(refactoring)) {
    const oldName = target.method || target.class;
    if (!oldName) return null;
    return renameAction(step, oldName, `${oldName}${toPascalCase(refactoring.replace(/ /g, '_'))}`);
  }

  if (refactoring === 'extract constant' || refactoring === 'replace magic number with symbolic constant') {
    if (!Object.prototype.hasOwnProperty.call(params, 'literal_value')) return null;
    return {
      action_type: 'extract_constant',
      parameters: {
        literal_value: params.literal_value,
        constant_name: params.constant_name || 'EXTRACTED_CONSTANT',
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'introduce constant') {
    const literalValue = Object.prototype.hasOwnProperty.call(params, 'literal_value') ? params.literal_value : null;
    const literalValues = Array.isArray(params.literal_values) ? params.literal_values : null;
    if (literalValue === null && !literalValues && !params.hint) return null;
    return {
      action_type: 'introduce_constant',
      parameters: {
        literal_value: literalValue,
        literal_values: literalValues,
        constant_name: params.constant_name || 'EXTRACTED_CONSTANT',
        hint: params.hint,
        source_file: params.source_file,
        source_line: params.source_line,
        target_class: target.class || params.source_class,
        target_method: target.method || params.method,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'replace literal' || refactoring === 'replace temp with query') {
    if (!Object.prototype.hasOwnProperty.call(params, 'old_literal') || !Object.prototype.hasOwnProperty.call(params, 'new_literal')) return null;
    return {
      action_type: 'replace_literal',
      parameters: {
        old_literal: params.old_literal,
        new_literal: params.new_literal,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'inject syntax error') {
    return {
      action_type: 'inject_syntax_error',
      parameters: {},
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'remove dead code') {
    const methodName = params.method || target.method;
    if (!methodName) return null;
    return {
      action_type: 'remove_dead_code',
      parameters: {
        method: methodName,
        class_name: target.class || params.source_class,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  return {
    action_type: 'noop',
    parameters: {
      reason: 'unsupported_refactoring',
      refactoring: refactoringRaw,
    },
    source_step_id: step.step_id ?? null,
    source_refactoring: step.refactoring ?? null,
    warnings: [`Unsupported refactoring '${step.refactoring}' mapped to noop.`],
  };
}

function unwrapPlanPayload(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Refactoring plan JSON must be an object.');
  }

  if (input.refactoring_plan && typeof input.refactoring_plan === 'object') return input.refactoring_plan;
  if (input.rdp_sample && typeof input.rdp_sample === 'object') return input.rdp_sample;
  return input;
}

function normalizeRefactoringPlan(input, fallbackPlanId) {
  const source = unwrapPlanPayload(input);
  const planId = String(source.plan_id || source.planId || fallbackPlanId || `plan_${Date.now()}`).trim();

  let actions = [];
  if (Array.isArray(source.actions)) {
    actions = source.actions.map(normalizeAction).filter(Boolean);
  } else if (Array.isArray(source.steps)) {
    actions = source.steps.map(normalizeStep).filter(Boolean);
  }

  if (!planId) throw new Error('Refactoring plan must include a plan_id.');
  if (!actions.length) throw new Error('Refactoring plan must include a non-empty actions list or convertible steps list.');

  const behaviorTests = Array.isArray(source.behavior_tests)
    ? source.behavior_tests
    : Array.isArray(source.behaviorTests)
      ? source.behaviorTests
      : [];

  const metadata = source.metadata && typeof source.metadata === 'object' && !Array.isArray(source.metadata)
    ? source.metadata
    : {};

  return {
    plan_id: planId,
    actions,
    behavior_tests: behaviorTests,
    metadata,
  };
}

function diffLines(oldText, newText) {
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');
  const rows = oldLines.length;
  const cols = newLines.length;
  const dp = Array.from({ length: rows + 1 }, () => Array(cols + 1).fill(0));

  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      dp[i][j] = oldLines[i] === newLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const result = [];
  let i = 0;
  let j = 0;

  while (i < rows && j < cols) {
    if (oldLines[i] === newLines[j]) {
      result.push({ type: 'same', text: oldLines[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ type: 'del', text: oldLines[i] });
      i += 1;
    } else {
      result.push({ type: 'add', text: newLines[j] });
      j += 1;
    }
  }

  while (i < rows) {
    result.push({ type: 'del', text: oldLines[i] });
    i += 1;
  }

  while (j < cols) {
    result.push({ type: 'add', text: newLines[j] });
    j += 1;
  }

  return result;
}

function buildDiffRows(diff) {
  return diff.map((item, index) => ({
    id: `${item.type}-${index}`,
    type: item.type,
    prefix: item.type === 'add' ? '+' : item.type === 'del' ? '-' : ' ',
    text: item.text,
  }));
}

function invariantExtra(details) {
  const preserved = Array.isArray(details.preserved_invariants) ? details.preserved_invariants.length : 0;
  const violated = Array.isArray(details.violated_invariants) ? details.violated_invariants.length : 0;
  const total = preserved + violated;
  if (!total) return null;

  return (
    <div className="sctva-inline-meta">
      <span>{`${preserved}/${total} invariants preserved`}</span>
      {violated > 0 ? <span className="sctva-danger-text">{`${violated} violated`}</span> : null}
    </div>
  );
}

function ValidationChecklist({ validation }) {
  const entries = [
    ['Syntax Validation', validation.syntax],
    ['Structural Equivalence', validation.structural],
    ['Behavioral Preservation', validation.behavioral],
    ['Invariant Mining', validation.invariant],
  ];

  return (
    <>
      {entries.map(([label, item]) => {
        if (!item) {
          return (
            <div key={label} className="sctva-check-item neutral">
              <span className="sctva-check-icon">o</span>
              <div>
                <strong>{label}</strong>
                <small>No data</small>
              </div>
            </div>
          );
        }

        const details = item.details || {};
        const status = label === 'Behavioral Preservation'
          ? String(details.fingerprint_status || (item.passed ? 'passed' : 'failed'))
          : label === 'Invariant Mining'
            ? String(details.status || (item.passed ? 'passed' : 'failed'))
            : (item.passed ? 'passed' : 'failed');

        const cls = status === 'passed' ? 'pass' : status === 'skipped' ? 'neutral' : 'fail';
        const icon = status === 'passed' ? '' : status === 'skipped' ? 'o' : 'x';

        const message = label === 'Behavioral Preservation'
          ? (details.fingerprint_summary || item.message || '')
          : label === 'Invariant Mining'
            ? (details.summary || details.reason || item.message || '')
            : (item.message || '');

        return (
          <div key={label} className={`sctva-check-item ${cls}`}>
            <span className="sctva-check-icon">{icon}</span>
            <div>
              <strong>{label}</strong>
              <small>
                {`Score: ${typeof item.score === 'number' ? item.score.toFixed(2) : '--'} · ${message}`}
              </small>
              {label === 'Invariant Mining' ? invariantExtra(details) : null}
            </div>
          </div>
        );
      })}
    </>
  );
}

function buildSafetyMessages(report) {
  const messages = [];
  const isNoiseMessage = (value) => {
    const text = String(value || '').trim().toLowerCase();
    return text === 'no replacements were applied.';
  };
  if (report.summary) messages.push({ key: 'summary', title: 'Summary', text: String(report.summary) });
  if (report.rollback_reason) messages.push({ key: 'rollback_reason', title: 'Rollback reason', text: String(report.rollback_reason) });
  (report.risk_flags || []).forEach((flag, index) => messages.push({
    key: `risk-${index}`,
    title: 'Risk',
    text: String(flag),
  }));
  (report.human_messages || []).forEach((message, index) => {
    if (isNoiseMessage(message)) return;
    messages.push({
      key: `message-${index}`,
      title: null,
      text: String(message),
    });
  });
  return messages;
}

function chooseLanguageFromName(fileName) {
  if (fileName.toLowerCase().endsWith('.py')) return 'python';
  return 'java';
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('Failed to read file.'));
    reader.readAsText(file);
  });
}

function summarizeSourceFiles(files) {
  if (!files.length) return 'No source files selected.';
  if (files.length === 1) return files[0].name;
  return `${files.length} files selected.`;
}

export default function SCTVAAgentPage() {
  const sourceFileInputRef = useRef(null);
  const planFileInputRef = useRef(null);

  const [requestId, setRequestId] = useState('');
  const [language, setLanguage] = useState('java');
  const [sourceFiles, setSourceFiles] = useState([]);
  const [activeFileName, setActiveFileName] = useState('');
  const [fileResults, setFileResults] = useState([]);
  const [refactoringPlanText, setRefactoringPlanText] = useState('');
  const [executionOptionsText, setExecutionOptionsText] = useState(SCTVAAgentService.defaultExecutionOptionsJson);

  const [sourceFileName, setSourceFileName] = useState('No source files selected.');
  const [planFileName, setPlanFileName] = useState('No plan file selected.');
  const [planLoaded, setPlanLoaded] = useState(false);
  const [planStepCount, setPlanStepCount] = useState(0);

  const [isRunning, setIsRunning] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [statusTone, setStatusTone] = useState('info');
  const [statusMessage, setStatusMessage] = useState('');

  const [timeline, setTimeline] = useState(DEFAULT_TIMELINE);
  const [timelineCount, setTimelineCount] = useState(`${PIPELINE_STAGES.length} Stages`);

  const [validation, setValidation] = useState(null);
  const [safetyMessages, setSafetyMessages] = useState([]);
  const [rawResponse, setRawResponse] = useState('{}');

  const [metricSuccess, setMetricSuccess] = useState('--');
  const [metricRollback, setMetricRollback] = useState('--');
  const [metricConfidence, setMetricConfidence] = useState('--');
  const [metricLanguage, setMetricLanguage] = useState('--');
  const [confidenceLabel, setConfidenceLabel] = useState('Idle');
  const [confidenceCopy, setConfidenceCopy] = useState('Model analysis will appear after execution.');
  const [confidenceScore, setConfidenceScore] = useState(0);

  const [additions, setAdditions] = useState(0);
  const [deletions, setDeletions] = useState(0);

  const [logs, setLogs] = useState([
    { level: 'READY', message: 'Transformation Agent initialized.' },
    { level: 'INFO', message: 'Upload source and refactoring plan to begin.' },
  ]);

  const [sourceDragOver, setSourceDragOver] = useState(false);
  const [planDragOver, setPlanDragOver] = useState(false);

  const [diffRows, setDiffRows] = useState([]);
  const [finalCode, setFinalCode] = useState('');

  const renderDefaultChecklist = useMemo(
    () => (
      <>
        {DEFAULT_VALIDATION.map(item => (
          <div key={item.label} className="sctva-check-item neutral">
            <span className="sctva-check-icon">o</span>
            <div>
              <strong>{item.label}</strong>
              <small>{item.text}</small>
            </div>
          </div>
        ))}
      </>
    ),
    []
  );

  const activeSource = sourceFiles.find(file => file.name === activeFileName) || sourceFiles[0];
  const activeSourceName = activeSource ? activeSource.name : '';
  const activeSourceCode = activeSource ? activeSource.code : '';
  const activeFileDisplay = activeFileName || activeSourceName || 'No file selected';
  const fileTabs = fileResults.length
    ? fileResults.map(item => ({
      name: item.file_name,
      success: item.success,
      rollback: item.rollback_occurred,
    }))
    : sourceFiles.map(file => ({ name: file.name }));

  function pushLog(level, message) {
    setLogs(prev => [...prev, { level, message }]);
  }

  function showStatus(message, tone) {
    setStatusMessage(message);
    setStatusTone(tone);
  }

  function clearStatus() {
    setStatusMessage('');
    setStatusTone('info');
  }

  function updateSourceFileContent(fileName, nextCode) {
    if (!fileName) return;
    setSourceFiles(prev => prev.map(file => (
      file.name === fileName ? { ...file, code: nextCode } : file
    )));
  }

  async function handleSourceFiles(fileList) {
    if (!fileList || !fileList.length) return;

    try {
      const files = Array.from(fileList);
      const loaded = await Promise.all(
        files.map(async file => ({
          name: file.name,
          language: chooseLanguageFromName(file.name),
          code: await readFileAsText(file),
        }))
      );

      setSourceFiles(loaded);
      setSourceFileName(summarizeSourceFiles(loaded));
      setActiveFileName(loaded[0]?.name || '');
      setLanguage(loaded[0]?.language || 'java');
      setFileResults([]);
      setDiffRows([]);
      setFinalCode('');
      setRawResponse('{}');
      setValidation(null);
      setSafetyMessages([]);
      setAdditions(0);
      setDeletions(0);
      pushLog('INFO', `Loaded ${loaded.length} source file${loaded.length === 1 ? '' : 's'} from your computer.`);
    } catch (error) {
      pushLog('ERROR', error.message || 'Failed to load source files.');
    }
  }

  function handlePlanFile(file) {
    if (!file) return;

    setPlanFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      setRefactoringPlanText(text);
      try {
        const parsed = JSON.parse(text || '{}');
        renderPlanTimeline(parsed);
        pushLog('INFO', `Loaded refactoring plan ${file.name}.`);
      } catch {
        pushLog('WARN', 'Plan loaded, but JSON preview failed.');
      }
    };
    reader.readAsText(file);
  }

  function renderPlanTimeline(plan) {
    const source = unwrapPlanPayload(plan);
    const steps = Array.isArray(source.steps) ? source.steps : Array.isArray(source.actions) ? source.actions : [];
    setPlanLoaded(true);
    setPlanStepCount(steps.length);
    setTimelineCount(`${PIPELINE_STAGES.length} Stages`);
    setTimeline(buildPipelineTimeline({
      phase: 'ready',
      planLoaded: true,
      planStepCount: steps.length,
      result: null,
    }));
  }

  function renderPipelineTimeline({ phase, result, overridePlanLoaded, overridePlanStepCount } = {}) {
    const effectivePlanLoaded = typeof overridePlanLoaded === 'boolean' ? overridePlanLoaded : planLoaded;
    const effectivePlanSteps = Number.isFinite(overridePlanStepCount) ? overridePlanStepCount : planStepCount;

    setTimelineCount(`${PIPELINE_STAGES.length} Stages`);
    setTimeline(buildPipelineTimeline({
      phase: phase || 'idle',
      planLoaded: effectivePlanLoaded,
      planStepCount: effectivePlanSteps,
      result: result || null,
    }));
  }

  function buildPayload() {
    const finalRequestId = requestId.trim() || `sctva_${Date.now()}`;
    const fallbackLanguage = language.trim().toLowerCase();

    if (!sourceFiles.length) {
      throw new Error('No source code files loaded from your computer.');
    }

    let uploadedJson;
    let executionOptions;

    try {
      uploadedJson = SCTVAAgentService.parseJson(refactoringPlanText || '{}');
    } catch (error) {
      throw new Error(`Refactoring plan JSON is invalid: ${error.message}`);
    }

    const refactoringPlan = normalizeRefactoringPlan(uploadedJson, finalRequestId);

    try {
      executionOptions = SCTVAAgentService.parseJson(executionOptionsText || '{}');
    } catch (error) {
      throw new Error(`Execution options JSON is invalid: ${error.message}`);
    }

    return {
      request_id: finalRequestId,
      language: (sourceFiles[0]?.language || fallbackLanguage || 'java').toLowerCase(),
      source_code: sourceFiles[0]?.code || '',
      source_files: sourceFiles.map(file => ({
        file_name: file.name,
        source_code: file.code,
        language: file.language,
      })),
      refactoring_plan: refactoringPlan,
      execution_options: executionOptions,
    };
  }

  function renderDiff(original, updated) {
    if (!original && !updated) {
      setAdditions(0);
      setDeletions(0);
      setDiffRows([]);
      return;
    }
    const diff = diffLines(original, updated);
    let add = 0;
    let del = 0;

    diff.forEach(item => {
      if (item.type === 'add') add += 1;
      if (item.type === 'del') del += 1;
    });

    setAdditions(add);
    setDeletions(del);
    setDiffRows(buildDiffRows(diff));
  }

  function renderValidation(resultValidation) {
    const normalized = resultValidation && Object.keys(resultValidation).length ? resultValidation : null;
    setValidation(normalized);
  }

  function renderSafetyReport(report) {
    setSafetyMessages(buildSafetyMessages(report || {}));
  }

  function applyActiveResult(result, originalSource) {
    if (!result) {
      setMetricSuccess('--');
      setMetricRollback('--');
      setMetricLanguage('--');
      setMetricConfidence('--');
      setConfidenceScore(0);
      setConfidenceLabel('Idle');
      setConfidenceCopy('Model analysis will appear after execution.');
      renderValidation(null);
      renderSafetyReport(null);
      setFinalCode('');
      renderDiff('', '');
      renderPipelineTimeline({ phase: 'idle' });
      return;
    }

    setMetricSuccess(result.success ? 'YES' : 'NO');
    setMetricRollback(result.rollback_occurred ? 'YES' : 'NO');
    setMetricLanguage(result.language || '--');

    const score = typeof result.confidence_score === 'number' ? result.confidence_score : 0;
    setMetricConfidence(`${Math.round(score * 100)}%`);
    setConfidenceScore(Math.max(0, Math.min(100, score * 100)));
    setConfidenceLabel(result.rollback_occurred ? 'Rolled Back' : score >= 0.8 ? 'Highly Safe' : score >= 0.6 ? 'Review' : 'Risky');
    setConfidenceCopy(
      result.rollback_occurred
        ? 'Validation detected unsafe transformation and rollback was triggered.'
        : 'Model analysis shows structural and behavioral validation results.'
    );

    renderValidation(result.validation || {});
    renderSafetyReport(result.safety_report || {});
    setFinalCode(String(result.refactored_code || ''));
    renderDiff(originalSource || '', result.refactored_code || '');
    renderPipelineTimeline({ phase: 'done', result });
  }

  function normalizeFileResults(data) {
    if (Array.isArray(data.file_results) && data.file_results.length) {
      return data.file_results.map((item, index) => ({
        ...item,
        file_name: item.file_name || sourceFiles[index]?.name || `file_${index + 1}`,
      }));
    }

    return [{
      file_name: sourceFiles[0]?.name || activeSourceName || 'source_code',
      language: data.language,
      success: data.success,
      rollback_occurred: data.rollback_occurred,
      confidence_score: data.confidence_score,
      refactored_code: data.refactored_code,
      validation: data.validation,
      safety_report: data.safety_report,
    }];
  }

  function renderResult(data) {
    setRawResponse(JSON.stringify(data, null, 2));

    const results = normalizeFileResults(data);
    setFileResults(results);

    const preferredName = activeFileName && results.some(item => item.file_name === activeFileName)
      ? activeFileName
      : results[0]?.file_name;

    if (preferredName) {
      setActiveFileName(preferredName);
    }

    const selected = results.find(item => item.file_name === preferredName) || results[0];
    const originalSource = sourceFiles.find(file => file.name === selected?.file_name)?.code
      || activeSourceCode
      || '';

    applyActiveResult(selected, originalSource);
  }

  function handleSelectFile(fileName) {
    setActiveFileName(fileName);
    const result = fileResults.find(item => item.file_name === fileName);
    const originalSource = sourceFiles.find(file => file.name === fileName)?.code || '';
    if (result) {
      applyActiveResult(result, originalSource);
    } else {
      applyActiveResult(null, '');
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setErrorMessage('');

    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      setErrorMessage(error.message);
      pushLog('ERROR', error.message);
      return;
    }

    setIsRunning(true);
    showStatus('Running transformation...', 'info');
    pushLog('INFO', 'Targeting source for transformation...');
    pushLog('DEBUG', 'Applying refactoring plan and validation pipeline...');
    renderPipelineTimeline({ phase: 'running', overridePlanLoaded: true });

    try {
      const data = await SCTVAAgentService.execute(payload);
      renderResult(data);
      const doneMessage = data.rollback_occurred ? 'Execution completed with rollback.' : 'Execution completed successfully.';
      showStatus(doneMessage, data.rollback_occurred ? 'warn' : 'success');
      pushLog(data.rollback_occurred ? 'WARN' : 'VALID', data.rollback_occurred ? 'Rollback triggered after validation.' : 'All safety checks completed.');
    } catch (error) {
      setErrorMessage(error.message);
      showStatus('Execution failed.', 'error');
      pushLog('ERROR', error.message);
    } finally {
      setIsRunning(false);
    }
  }

  function handleClear() {
    setRequestId('');
    if (sourceFileInputRef.current) sourceFileInputRef.current.value = '';
    if (planFileInputRef.current) planFileInputRef.current.value = '';

    setSourceFiles([]);
    setActiveFileName('');
    setFileResults([]);
    setRefactoringPlanText('');
    setExecutionOptionsText(SCTVAAgentService.defaultExecutionOptionsJson);
    setSourceFileName('No source files selected.');
    setPlanFileName('No plan file selected.');
    setPlanLoaded(false);
    setPlanStepCount(0);

    setDiffRows([]);
    setRawResponse('{}');
    setFinalCode('');
    setMetricSuccess('--');
    setMetricRollback('--');
    setMetricConfidence('--');
    setMetricLanguage('--');
    setConfidenceLabel('Idle');
    setConfidenceCopy('Model analysis will appear after execution.');
    setConfidenceScore(0);
    setAdditions(0);
    setDeletions(0);
    setValidation(null);
    setTimeline(DEFAULT_TIMELINE);
    setTimelineCount(`${PIPELINE_STAGES.length} Stages`);
    setSafetyMessages([]);
    setLogs([]);
    pushLog('INFO', 'Workspace cleared.');
    setErrorMessage('');
    clearStatus();
  }

  async function handleCopyFinalCode() {
    await navigator.clipboard.writeText(finalCode || '');
    showStatus('Final output copied to clipboard.', 'success');
    pushLog('INFO', 'Final output copied to clipboard.');
  }

  function handleDownloadResult() {
    const blob = new Blob([rawResponse || '{}'], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'sctva_result.json';
    link.click();
    URL.revokeObjectURL(url);
    pushLog('INFO', 'Result JSON downloaded.');
  }

  function onSourceDrop(event) {
    event.preventDefault();
    setSourceDragOver(false);
    const files = event.dataTransfer.files;
    if (files && files.length) {
      if (sourceFileInputRef.current) sourceFileInputRef.current.files = files;
      handleSourceFiles(files);
    }
  }

  function onPlanDrop(event) {
    event.preventDefault();
    setPlanDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      if (planFileInputRef.current) planFileInputRef.current.files = event.dataTransfer.files;
      handlePlanFile(file);
    }
  }

  return (
    <div className="page-container sctva-page">
      <section className="sctva-hero">
        <div className="sctva-hero-left">
          <div className="sctva-hero-icon">
            <img src={transformBadge} alt="Transformation and validation badge" />
          </div>
          <div>
            <h1>Transformation Agent</h1>
            <p>
              Applying automated architectural changes with guaranteed structural integrity, behavioral validation,
              invariant mining, and rollback capabilities.
            </p>
          </div>
        </div>
        <div className="sctva-hero-actions">
          <button className="sctva-btn sctva-btn-secondary" type="button" onClick={handleClear}>
            <span></span>
          Refresh
          </button>
        </div>
      </section>

      <section>
        <form id="sctva-form" onSubmit={handleSubmit}>
          <div className="sctva-card">
            <div className="sctva-control-head">
              <h2>Source & Plan Input</h2>
            </div>

            <div className="sctva-form-grid">
              {/* <label className="sctva-label">
                <span>Request ID</span>
                <input
                  className="sctva-field"
                  value={requestId}
                  onChange={e => setRequestId(e.target.value)}
                  type="text"
                  placeholder="sctva_demo_001"
                />
              </label>

              <label className="sctva-label">
                <span>Language</span>
                <select className="sctva-field" value={language} onChange={e => setLanguage(e.target.value)}>
                  <option value="java">Java</option>
                  <option value="python">Python</option>
                </select>
              </label> */}
            </div>

            <div
              className={`sctva-upload-zone ${sourceDragOver ? 'drag-over' : ''}`}
              onDragEnter={e => { e.preventDefault(); setSourceDragOver(true); }}
              onDragOver={e => { e.preventDefault(); setSourceDragOver(true); }}
              onDragLeave={e => { e.preventDefault(); setSourceDragOver(false); }}
              onDrop={onSourceDrop}
            >
              <div>
                <strong>Upload Source Code Files</strong>
                <p>Drag and drop multiple local source files here, or browse your computer.</p>
              </div>
              <label className="sctva-mini-btn" htmlFor="sctva-source-file-input">Browse File</label>
              <input
                ref={sourceFileInputRef}
                id="sctva-source-file-input"
                type="file"
                accept=".java,.py,.txt,.js,.ts,.cs,.cpp,.c,.h,.hpp"
                hidden
                multiple
                onChange={e => handleSourceFiles(e.target.files)}
              />
            </div>

            <div className="sctva-file-name">{sourceFileName}</div>

            <div
              className={`sctva-upload-zone ${planDragOver ? 'drag-over' : ''}`}
              onDragEnter={e => { e.preventDefault(); setPlanDragOver(true); }}
              onDragOver={e => { e.preventDefault(); setPlanDragOver(true); }}
              onDragLeave={e => { e.preventDefault(); setPlanDragOver(false); }}
              onDrop={onPlanDrop}
            >
              <div>
                <strong>Upload Refactoring Plan JSON</strong>
                <p>Drag and drop your RDP plan JSON here, or browse a file.</p>
              </div>
              <label className="sctva-mini-btn" htmlFor="sctva-plan-file-input">Browse JSON</label>
              <input
                ref={planFileInputRef}
                id="sctva-plan-file-input"
                type="file"
                accept=".json"
                hidden
                onChange={e => handlePlanFile(e.target.files?.[0])}
              />
            </div>

            <div className="sctva-file-name">{planFileName}</div>

            <details className="sctva-toggle">
              <summary>{`View / Edit Selected Source Code · ${activeFileDisplay}`}</summary>
              <textarea
                className="sctva-textarea"
                spellCheck="false"
                value={activeSourceCode}
                onChange={e => updateSourceFileContent(activeSourceName, e.target.value)}
              />
            </details>

            <details className="sctva-toggle" open>
              <summary>View / Edit Refactoring Plan JSON</summary>
              <textarea
                className="sctva-textarea"
                spellCheck="false"
                value={refactoringPlanText}
                onChange={e => setRefactoringPlanText(e.target.value)}
              />
            </details>

            <details className="sctva-toggle">
              <summary>Execution Options JSON</summary>
              <textarea
                className="sctva-textarea"
                spellCheck="false"
                value={executionOptionsText}
                onChange={e => setExecutionOptionsText(e.target.value)}
              />
            </details>

            {errorMessage ? <div className="sctva-alert sctva-alert-error">{errorMessage}</div> : null}
            {statusMessage ? <div className={`sctva-alert sctva-alert-${statusTone}`}>{statusMessage}</div> : null}
          </div>

          <div className="sctva-hero-actions" style={{ marginTop: 16 }}>
            <button className="sctva-btn sctva-btn-primary" type="submit" disabled={isRunning}>
              <span></span>
              {isRunning ? 'Running...' : 'Run Transformation'}
            </button>
          </div>
        </form>
      </section>

      <section className="sctva-agent-grid">
        <div className="sctva-timeline-card">
          <div className="sctva-panel-title-row">
            <h2 className="sctva-panel-title">Transformation Sequence</h2>
            <span className="sctva-step-count">{timelineCount}</span>
          </div>

          <div className="sctva-timeline">
            {timeline.map(item => (
              <div key={`${item.label}-${item.title}`} className={`sctva-timeline-item ${item.status}`}>
                <div className="sctva-timeline-dot" />
                <div>
                  <span>{item.label}</span>
                  <strong>{item.title}</strong>
                  <p>{item.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="sctva-code-card">
          <div className="sctva-code-topbar">
            <div>
              <span className="sctva-chip">{activeFileDisplay}</span>
              <span className="sctva-chip sctva-chip-muted">Diff Highlight</span>
            </div>
            <div className="sctva-diff-stats">
              <span className="sctva-dot sctva-dot-red" />
              <span>{`${deletions} Deletions`}</span>
              <span className="sctva-dot sctva-dot-green" />
              <span>{`${additions} Additions`}</span>
            </div>
          </div>

          {fileTabs.length ? (
            <div className="sctva-file-tabs">
              {fileTabs.map(item => (
                <button
                  key={item.name}
                  type="button"
                  className={`sctva-file-tab ${item.name === activeFileName ? 'active' : ''} ${item.success === true ? 'pass' : item.success === false ? 'fail' : ''}`}
                  onClick={() => handleSelectFile(item.name)}
                >
                  <span>{item.name}</span>
                  {item.rollback ? <em>Rolled Back</em> : null}
                </button>
              ))}
            </div>
          ) : null}

          <div className="sctva-code-diff">
            {diffRows.length ? (
              diffRows.map(row => (
                <div
                  key={row.id}
                  className={row.type === 'add' ? 'sctva-diff-line add' : row.type === 'del' ? 'sctva-diff-line del' : 'sctva-diff-line'}
                >
                  <span className="sctva-line-no">{row.prefix}</span>
                  <code style={DIFF_CODE_INLINE_STYLE}>{row.text || ' '}</code>
                </div>
              ))
            ) : (
              <div className="sctva-empty-state">{EMPTY_CODE_STATE}</div>
            )}
          </div>
        </div>

        <div className="sctva-validation-card">
          <h2>{`Validation Checklist · ${activeFileDisplay}`}</h2>
          <div className="sctva-checklist">
            {validation ? <ValidationChecklist validation={validation} /> : renderDefaultChecklist}
          </div>
        </div>

        <div className="sctva-confidence-card">
          <h2>{`Confidence Score · ${activeFileDisplay}`}</h2>

          <div className="sctva-confidence-ring" style={{ ['--score']: `${confidenceScore}%` }}>
            <div className="sctva-ring-inner">
              <strong>{metricConfidence}</strong>
              <span>{confidenceLabel}</span>
            </div>
          </div>

          <p className="sctva-confidence-copy">{confidenceCopy}</p>

          <div className="sctva-mini-metrics">
            <div>
              <span>Success</span>
              <strong>{metricSuccess}</strong>
            </div>
            <div>
              <span>Rollback</span>
              <strong>{metricRollback}</strong>
            </div>
            <div>
              <span>Language</span>
              <strong>{metricLanguage}</strong>
            </div>
          </div>
        </div>

        <div className="sctva-report-card">
          <h2>{`Safety Report · ${activeFileDisplay}`}</h2>
          <div className="sctva-report-list">
            {safetyMessages.length ? (
              <ul className="sctva-report-list-inner">
                {safetyMessages.map(item => (
                  <li key={item.key}>
                    {item.title ? <strong>{`${item.title}: `}</strong> : null}
                    {item.text}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="sctva-empty-state">{EMPTY_REPORT_STATE}</div>
            )}
          </div>
        </div>

        <div className="sctva-logs-card">
          <div className="sctva-logs-top">
            <div className="sctva-window-dots">
              <span />
              <span />
              <span />
            </div>
            <strong>Agent Logs · Session #R26SE008</strong>
          </div>

          <div className="sctva-logs-output">
            {logs.map((line, index) => (
              <div key={`${line.level}-${line.message}-${index}`}>
                <span>{`[${line.level}]`}</span>
                {line.message}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="sctva-raw-card">
        <div className="sctva-raw-actions">
          <h2>Raw Response JSON</h2>
          <div>
            {/* <button className="sctva-mini-btn" type="button" onClick={handleCopyFinalCode}>Copy Final Code</button> */}
            <button className="sctva-mini-btn" type="button" style={{ marginLeft: 8 }} onClick={handleDownloadResult}>Download Result JSON</button>
          </div>
        </div>
        <pre className="sctva-json-output">{rawResponse}</pre>
      </section>
    </div>
  );
}
