import { useEffect, useMemo, useRef, useState } from 'react';
import SCTVAAgentService from '../../services/sctvaAgentService';
import transformBadge from '../../assets/transform-badge.svg';
import { getEnv } from '../../config/env';
import {
  cacheSourceFiles,
  fetchGithubSourceFiles,
  getCachedSourceFiles,
  getSourceContext,
} from '../../utils/sourceCache';
import './SCTVAAgentPage.css';

const CUQA_API = getEnv('VITE_CUQA_AGENT_API_URL', getEnv('VITE_CUA_API_URL', 'http://localhost:8080')).replace(/\/+$/, '');
const CUQA_IMPORT_LIMIT = 1000;
const RDP_AGENT_SESSION_KEY = 'rdp-agent-page-state';
const RDP_AGENT_LOCAL_SESSION_KEY = 'rdp_last_session';
const RDP_AGENT_HISTORY_KEY = 'rdp_plan_history';
const SCTVA_AGENT_SESSION_KEY = 'sctva-agent-page-state';
const SCTVA_ARTIFACT_STORAGE_KEY = 'sctva-agent-final-artifacts';
const SCTVA_ARTIFACT_STORAGE_THRESHOLD_BYTES = 3500000;
const ZIP_CRC32_TABLE = Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = value & 1 ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
  }
  return value >>> 0;
});

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
    : status === 'warn'
      ? 'Warning'
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
  const numericScore = typeof step.score === 'number' ? step.score : Number.NaN;

  if (reportedStatus.toLowerCase() === 'skipped') return 'pending';

  if (Number.isFinite(numericScore)) {
    if (numericScore > 0.9) return 'completed';
    if (numericScore >= 0.5) return 'warn';
    return 'failed';
  }

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
  const transformationApplied = result?.transformation_applied !== false;

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
        : transformationApplied
          ? 'Final output accepted after successful validation.'
          : 'No proven transformation was applied; the original source was preserved.';
    }

    return {
      status,
      label: pipelineLabel(status, index + 1),
      title: stage.title,
      description,
    };
  });
}

function isAppliedFileResult(result) {
  return result?.transformation_applied === true && result?.rollback_occurred !== true;
}

function isAlreadyHandledFileResult(result) {
  const status = String(
    result?.application_status || result?.status || ''
  ).trim().toUpperCase();
  return status === 'ALREADY_HANDLED' || status === 'ALREADY_APPLIED';
}

function fileResultStatus(result) {
  if (result?.rollback_occurred === true) {
    return {
      key: 'rolled-back',
      label: 'ROLLED BACK',
      title: 'Validation rolled this file back to its original source.',
    };
  }

  if (isAppliedFileResult(result)) {
    return {
      key: 'applied',
      label: 'APPLIED',
      title: 'A validated refactoring was applied to this file.',
    };
  }

  if (isAlreadyHandledFileResult(result)) {
    return {
      key: 'already-handled',
      label: 'ALREADY HANDLED',
      title: 'The requested refactoring is already represented safely in this file.',
    };
  }

  return {
    key: 'not-applied',
    label: 'NOT APPLIED',
    title: 'No source-code change was applied to this file.',
  };
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

function sanitizeIdentifier(value) {
  const cleaned = String(value).trim().replace(/[^A-Za-z0-9_]/g, '_');
  if (!cleaned) return 'RenamedSymbol';
  return /^[0-9]/.test(cleaned) ? `R_${cleaned}` : cleaned;
}

function firstNumberValue(values) {
  const found = values.find(value => Number.isFinite(Number(value)));
  return found === undefined ? null : Number(found);
}

function extractSourceLineFromPlanItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null;

  const params = isPlainObject(item.parameters) ? item.parameters : {};
  const target = isPlainObject(item.target) ? item.target : {};
  const location = isPlainObject(item.location) ? item.location : {};
  const sourceLines = Array.isArray(params.source_lines) ? params.source_lines : [];
  const targetLines = Array.isArray(target.lines) ? target.lines : [];
  const locationLines = Array.isArray(location.lines) ? location.lines : [];

  return firstNumberValue([
    item.source_line,
    item.sourceLine,
    params.source_line,
    params.sourceLine,
    sourceLines[0],
    target.source_line,
    target.sourceLine,
    targetLines[0],
    location.source_line,
    location.sourceLine,
    locationLines[0],
  ]);
}

function extractSourceRangeFromPlanItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null;

  const params = isPlainObject(item.parameters) ? item.parameters : {};
  const target = isPlainObject(item.target) ? item.target : {};
  const location = isPlainObject(item.location) ? item.location : {};
  const sources = [params, target, location, item];
  const toInt = value => {
    if (Number.isFinite(value)) return Math.trunc(value);
    if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number.parseInt(value.trim(), 10);
    return null;
  };

  for (const source of sources) {
    if (!isPlainObject(source)) continue;
    const start = ['start_line', 'startLine', 'source_line', 'sourceLine', 'line']
      .map(key => toInt(source[key]))
      .find(value => value !== null);
    const end = ['end_line', 'endLine', 'target_line', 'targetLine']
      .map(key => toInt(source[key]))
      .find(value => value !== null);
    if (start !== undefined && end !== undefined) {
      return { start_line: Math.min(start, end), end_line: Math.max(start, end) };
    }

    for (const key of ['source_lines', 'sourceLines', 'lines', 'line_range', 'lineRange']) {
      const values = source[key];
      if (Array.isArray(values)) {
        const parsed = values.map(toInt).filter(value => value !== null);
        if (parsed.length >= 2) return { start_line: Math.min(...parsed), end_line: Math.max(...parsed) };
        if (parsed.length === 1) return { start_line: parsed[0], end_line: parsed[0] };
      }
      if (isPlainObject(values)) {
        const nestedStart = toInt(values.start ?? values.from);
        const nestedEnd = toInt(values.end ?? values.to);
        if (nestedStart !== null && nestedEnd !== null) {
          return { start_line: Math.min(nestedStart, nestedEnd), end_line: Math.max(nestedStart, nestedEnd) };
        }
      }
    }
  }

  return null;
}

function renameAction(step, oldName, newName) {
  const params = isPlainObject(step?.parameters) ? step.parameters : {};
  const target = isPlainObject(step?.target) ? step.target : {};
  const sourceLine = extractSourceLineFromPlanItem(step);

  return {
    action_type: 'rename_symbol',
    parameters: {
      old_name: String(oldName),
      new_name: sanitizeIdentifier(String(newName)),
      source_line: sourceLine,
      target_class: target.class || params.source_class,
      target_method: target.method || params.method,
    },
    source_step_id: step.step_id ?? null,
    source_refactoring: step.refactoring ?? null,
    warnings: [],
  };
}

function unsupportedSemanticAction(step, reason) {
  return {
    action_type: 'noop',
    parameters: {
      reason,
      refactoring: step?.refactoring,
    },
    source_step_id: step?.step_id ?? null,
    source_refactoring: step?.refactoring ?? null,
    warnings: [`${step?.refactoring || 'Refactoring'} needs richer semantic edits and was not simulated with a rename.`],
  };
}

function payloadHasActionType(payload, actionType) {
  return Array.isArray(payload?.refactoring_plan?.actions)
    && payload.refactoring_plan.actions.some(action => action?.action_type === actionType);
}

function getSctvaHealthUrl() {
  return SCTVAAgentService.getExecuteUrl().replace(/\/sctva\/execute(?:_from_rdp)?$/, '/sctva/health');
}

function getSctvaApiBaseUrl() {
  return SCTVAAgentService.getExecuteUrl().replace(/\/sctva\/execute(?:_from_rdp)?$/, '');
}

async function readBackendSupport() {
  try {
    const response = await fetch(getSctvaHealthUrl());
    if (!response.ok) return null;
    const data = await response.json();
    // An older SCTVA health endpoint may return only {status, service}.
    // Treat that response as unknown instead of interpreting missing metadata
    // as an empty action list and rewriting real actions to noop.
    if (!Array.isArray(data.supported_actions)) return null;
    return {
      actions: data.supported_actions.map(String),
      capabilities: Array.isArray(data.supported_capabilities) ? data.supported_capabilities.map(String) : [],
    };
  } catch {
    return null;
  }
}

function payloadHasLineOnlyDeadCodeAction(payload) {
  return Array.isArray(payload?.refactoring_plan?.actions)
    && payload.refactoring_plan.actions.some(action => {
      if (action?.action_type !== 'remove_dead_code') return false;
      const params = isPlainObject(action.parameters) ? action.parameters : {};
      return Boolean(params.source_line) && !params.method && !params.method_name && !params.target_method;
    });
}

async function buildBackendCompatiblePayload(payload) {
  const backendSupport = await readBackendSupport();
  const supportedActions = backendSupport?.actions || null;
  const supportedCapabilities = backendSupport?.capabilities || [];
  const newActionTypes = [
    'extract_method',
    'extract_class',
    'extract_python_class',
    'extract_java_class',
    'extract_c_component',
    'replace_unsafe_function',
    'encapsulate_variable',
    'normalize_multiline_statement',
  ];
  const unsupportedActions = supportedActions
    ? newActionTypes.filter(actionType => payloadHasActionType(payload, actionType) && !supportedActions.includes(actionType))
    : [];

  const unsupportedCapabilities = [];
  if (
    backendSupport
    && payloadHasLineOnlyDeadCodeAction(payload)
    && !supportedCapabilities.includes('line_based_remove_dead_code')
  ) {
    unsupportedCapabilities.push('line_based_remove_dead_code');
  }

  return {
    payload,
    unsupportedActions,
    unsupportedCapabilities,
  };
}

function unsupportedActionTypeFromError(error) {
  const message = String(error?.message || '').toLowerCase();
  if (message.includes("remove_dead_code requires 'method'")) return 'line_based_remove_dead_code';
  if (!message.includes('unsupported action_type')) return '';
  if (message.includes('extract_method')) return 'extract_method';
  if (message.includes('extract_python_class')) return 'extract_python_class';
  if (message.includes('extract_java_class')) return 'extract_java_class';
  if (message.includes('extract_c_component')) return 'extract_c_component';
  if (message.includes('extract_class')) return 'extract_class';
  if (message.includes('replace_unsafe_function')) return 'replace_unsafe_function';
  if (message.includes('encapsulate_variable')) return 'encapsulate_variable';
  if (message.includes('normalize_multiline_statement')) return 'normalize_multiline_statement';
  return '';
}

function normalizePathForMatch(value) {
  return String(value || '')
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/\/+/g, '/')
    .trim()
    .toLowerCase();
}

function pathBaseName(value) {
  const normalized = normalizePathForMatch(value);
  return normalized.split('/').filter(Boolean).pop() || normalized;
}

function inferParallelFileWorkers(fileCount) {
  const count = Math.max(1, Number(fileCount) || 1);
  const cores = Math.max(1, Number(window.navigator?.hardwareConcurrency) || 4);
  return Math.max(1, Math.min(4, cores, count));
}

function firstStringValue(values) {
  const found = values.find(value => typeof value === 'string' && value.trim());
  return found ? found.trim() : '';
}

function extractSourceFileFromPlanItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return '';

  const params = isPlainObject(item.parameters) ? item.parameters : {};
  const target = isPlainObject(item.target) ? item.target : {};
  const location = isPlainObject(item.location) ? item.location : {};

  return firstStringValue([
    typeof item.target === 'string' ? item.target : '',
    item.source_file,
    item.sourceFile,
    item.file,
    item.file_name,
    item.fileName,
    item.file_path,
    item.filePath,
    item.relative_path,
    item.relativePath,
    params.source_file,
    params.sourceFile,
    params.file,
    params.file_name,
    params.fileName,
    params.file_path,
    params.filePath,
    params.relative_path,
    params.relativePath,
    target.source_file,
    target.file,
    target.file_name,
    target.file_path,
    target.relative_path,
    location.source_file,
    location.file,
    location.file_name,
    location.file_path,
    location.relative_path,
  ]);
}

function attachSourceFileToAction(action, sourceItem) {
  if (!action || !sourceItem) return action;
  const sourceFile = extractSourceFileFromPlanItem(sourceItem);
  if (!sourceFile) return action;

  let actionType = action.action_type;
  const normalizedSource = normalizePathForMatch(sourceFile);
  if (actionType === 'extract_class') {
    if (normalizedSource.endsWith('.java')) actionType = 'extract_java_class';
    else if (normalizedSource.endsWith('.py')) actionType = 'extract_python_class';
    else if (normalizedSource.endsWith('.c') || normalizedSource.endsWith('.h')) {
      actionType = 'extract_c_component';
    }
  }
  if (actionType === 'introduce_parameter_object') {
    if (normalizedSource.endsWith('.java')) actionType = 'introduce_java_parameter_object';
    else if (normalizedSource.endsWith('.py')) actionType = 'introduce_python_parameter_object';
  }

  const parameters = {
    ...(action.parameters || {}),
    source_file: action.parameters?.source_file || sourceFile,
  };
  if (
    ['extract_class', 'extract_python_class', 'extract_java_class', 'extract_c_component'].includes(actionType)
    && !parameters.source_class_origin
  ) {
    const sourceStem = pathBaseName(sourceFile).replace(/\.[^.]+$/, '');
    if (String(parameters.source_class || '').trim().toLowerCase() === sourceStem.toLowerCase()) {
      parameters.source_class_origin = 'file_stem_fallback';
    }
  }

  return {
    ...action,
    action_type: actionType,
    parameters,
  };
}

function normalizeAction(action) {
  if (!action || typeof action !== 'object' || Array.isArray(action)) {
    throw new Error('Each action must be an object.');
  }

  if (!action.action_type) {
    throw new Error('Each action must include action_type.');
  }

  let actionType = String(action.action_type).trim().toLowerCase();
  const parameters = isPlainObject(action.parameters) ? { ...action.parameters } : {};
  const target = isPlainObject(action.target) ? action.target : {};
  const sourceRefactoring = String(action.source_refactoring || '').trim();
  const isInlineClass = ['inline_class', 'inline_python_class'].includes(actionType)
    || sourceRefactoring.toLowerCase() === 'inline class';

  if (isInlineClass) {
    const targetClass = firstStringValue([
      parameters.class_to_inline,
      parameters.target_class,
      parameters.source_class,
      parameters.class_name,
      action.class_to_inline,
      action.target_class,
      target.class,
    ]);
    const sourceFile = extractSourceFileFromPlanItem(action);
    actionType = 'inline_python_class';
    parameters.class_to_inline = targetClass;
    parameters.target_class = targetClass;
    if (sourceFile) parameters.source_file = parameters.source_file || sourceFile;
    parameters.requested_target = {
      class_to_inline: targetClass,
      source_file: parameters.source_file || sourceFile || '',
    };
    if (!targetClass) parameters.target_resolution_error = 'INLINE_CLASS_TARGET_MISSING';
  }

  return {
    action_type: actionType,
    parameters,
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
    const methodName = target.method || params.method;
    const range = extractSourceRangeFromPlanItem(step);
    if (!methodName || !range) return null;
    return {
      action_type: 'extract_method',
      parameters: {
        method: String(methodName),
        new_method_name: sanitizeIdentifier(params.new_method_name || params.extracted_method_name || `${methodName}Core`),
        start_line: range.start_line,
        end_line: range.end_line,
        target_class: target.class || params.source_class,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'extract class') {
    const sourceFile = extractSourceFileFromPlanItem(step);
    const explicitSourceClass = params.source_class || params.class_name || target.class || params.class;
    const inferredSourceClass = pathBaseName(sourceFile).replace(/\.[^.]+$/, '');
    const sourceClass = explicitSourceClass || inferredSourceClass;
    const explicitNewClassName = params.new_class_name || params.extracted_class_name || params.destination_class;
    const newClassName = explicitNewClassName || (sourceClass ? `${sourceClass}Helper` : '');
    if (!sourceClass || !newClassName) return null;
    return {
      action_type: 'extract_class',
      parameters: {
        source_class: String(sourceClass),
        new_class_name: sanitizeIdentifier(String(newClassName)),
        source_class_origin: explicitSourceClass ? 'rdp_explicit' : 'file_stem_fallback',
        new_class_name_origin: explicitNewClassName ? 'rdp_explicit' : 'generated',
        methods_to_extract: Array.isArray(params.methods_to_extract) ? params.methods_to_extract.map(String) : [],
        fields_to_extract: Array.isArray(params.fields_to_extract) ? params.fields_to_extract.map(String) : [],
        required_public_methods: Array.isArray(params.required_public_methods) ? params.required_public_methods.map(String) : [],
        required_public_fields: Array.isArray(params.required_public_fields) ? params.required_public_fields.map(String) : [],
        preserve_public_api: params.preserve_public_api !== false,
        delegation_strategy: params.delegation_strategy || 'wrapper',
        target_file: params.destination_file || params.extracted_file || params.output_file || 'same_file',
        smell: step.smell || step.smell_type || 'Large Class',
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'move method') {
    return unsupportedSemanticAction(step, 'move_method_requires_source_and_destination_edits');
  }

  if (refactoring === 'replace conditional with polymorphism') {
    const sourceFile = extractSourceFileFromPlanItem(step);
    const range = extractSourceRangeFromPlanItem(step);
    const methodName = target.method
      || target.function
      || params.method
      || params.method_name
      || params.target_method
      || '';
    const sourceClass = target.class || params.source_class || params.class_name || '';
    if (!methodName && !sourceClass && !sourceFile && !range) return null;
    return {
      action_type: 'replace_conditional_with_polymorphism',
      parameters: {
        method: String(methodName),
        source_class: String(sourceClass),
        source_file: sourceFile,
        source_line: range?.start_line ?? null,
        start_line: range?.start_line ?? null,
        end_line: range?.end_line ?? null,
        base_class_name: String(params.base_class_name || ''),
        smell: step.smell || step.smell_type || 'Switch Statements',
        semantic_recovery_required: !methodName,
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'introduce parameter object') {
    const sourceFile = extractSourceFileFromPlanItem(step);
    const normalizedSource = normalizePathForMatch(sourceFile);
    const methodName = target.method
      || target.function
      || params.method
      || params.method_name
      || params.function
      || params.function_name;
    const objectName = params.parameter_object_name
      || params.new_class_name
      || params.parameter_class_name;
    if (!methodName || !objectName) return null;

    let actionType = 'introduce_parameter_object';
    if (normalizedSource.endsWith('.java')) actionType = 'introduce_java_parameter_object';
    else if (normalizedSource.endsWith('.py')) actionType = 'introduce_python_parameter_object';

    return {
      action_type: actionType,
      parameters: {
        method: String(methodName),
        parameter_object_name: sanitizeIdentifier(String(objectName)),
        parameter_name: sanitizeIdentifier(String(params.parameter_name || 'params')),
        source_class: String(target.class || params.source_class || params.class_name || ''),
        source_class_origin: sourceFile
          && String(target.class || params.source_class || params.class_name || '').trim().toLowerCase()
            === pathBaseName(sourceFile).replace(/\.[^.]+$/, '').toLowerCase()
          ? 'file_stem_fallback'
          : 'rdp_explicit',
        smell: step.smell || step.smell_type || 'Long Parameter List',
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'inline class') {
    const sourceFile = extractSourceFileFromPlanItem(step);
    const classToInline = firstStringValue([
      params.class_to_inline,
      params.target_class,
      params.source_class,
      params.class_name,
      target.class,
    ]);
    return {
      action_type: 'inline_python_class',
      parameters: {
        class_to_inline: classToInline,
        target_class: classToInline,
        source_file: sourceFile,
        source_line: extractSourceLineFromPlanItem(step),
        smell: step.smell || step.smell_type || 'Lazy Class',
        requested_target: {
          class_to_inline: classToInline,
          source_file: sourceFile,
        },
        ...(classToInline ? {} : { target_resolution_error: 'INLINE_CLASS_TARGET_MISSING' }),
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: classToInline ? [] : ['Inline Class target is missing from the RDP step.'],
    };
  }

  if ([
    'hide delegate',
    'replace data value with object',
    'collapse hierarchy',
    'pull up method',
    'replace parameter with method call',
  ].includes(refactoring)) {
    return unsupportedSemanticAction(step, `${refactoring.replace(/ /g, '_')}_requires_semantic_multi_location_edits`);
  }

  if (refactoring === 'extract constant' || refactoring === 'replace magic number with symbolic constant') {
    if (!Object.prototype.hasOwnProperty.call(params, 'literal_value')) return null;
    return {
      action_type: 'extract_constant',
      parameters: {
        literal_value: params.literal_value,
        constant_name: params.constant_name || 'EXTRACTED_CONSTANT',
        source_line: extractSourceLineFromPlanItem(step),
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
        source_line: extractSourceLineFromPlanItem(step),
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
        source_line: extractSourceLineFromPlanItem(step),
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'replace unsafe function') {
    const unsafeFunction = params.unsafe_function || target.method;
    const safeAlternative = params.safe_alternative;
    if (!unsafeFunction || !safeAlternative) return null;
    return {
      action_type: 'replace_unsafe_function',
      parameters: {
        unsafe_function: String(unsafeFunction),
        safe_alternative: String(safeAlternative),
        source_line: extractSourceLineFromPlanItem(step),
      },
      source_step_id: step.step_id ?? null,
      source_refactoring: step.refactoring ?? null,
      warnings: [],
    };
  }

  if (refactoring === 'encapsulate variable') {
    const variableName = params.variable_name || target.variable;
    if (!variableName) return null;
    return {
      action_type: 'encapsulate_variable',
      parameters: {
        variable_name: String(variableName),
        getter_name: sanitizeIdentifier(params.getter_name || `get_${variableName}`),
        setter_name: sanitizeIdentifier(params.setter_name || `set_${variableName}`),
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
    const methodName = params.method
      || params.method_name
      || params.function
      || params.function_name
      || params.target_method
      || params.target_function
      || target.method
      || target.function
      || target.target_method
      || target.target_function;
    const sourceLine = extractSourceLineFromPlanItem(step);
    if (!methodName && !sourceLine) return null;
    return {
      action_type: 'remove_dead_code',
      parameters: {
        method: methodName || '',
        class_name: target.class || params.source_class,
        source_line: sourceLine,
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

  if (input.plan && typeof input.plan === 'object' && !Array.isArray(input.plan)) return unwrapPlanPayload(input.plan);
  if (input.data?.plan && typeof input.data.plan === 'object' && !Array.isArray(input.data.plan)) return unwrapPlanPayload(input.data.plan);
  if (input.result?.plan && typeof input.result.plan === 'object' && !Array.isArray(input.result.plan)) return unwrapPlanPayload(input.result.plan);
  if (input.refactoring_plan && typeof input.refactoring_plan === 'object') return input.refactoring_plan;
  if (input.rdp_sample && typeof input.rdp_sample === 'object') return input.rdp_sample;
  return input;
}

function normalizeRefactoringPlan(input, fallbackPlanId) {
  const source = unwrapPlanPayload(input);
  const planId = String(source.plan_id || source.planId || fallbackPlanId || `plan_${Date.now()}`).trim();

  let actions = [];
  if (Array.isArray(source.actions)) {
    actions = source.actions
      .map(action => attachSourceFileToAction(normalizeAction(action), action))
      .filter(Boolean);
  } else if (Array.isArray(source.steps)) {
    actions = source.steps
      .map(step => {
        const action = normalizeStep(step);
        return action ? attachSourceFileToAction(action, step) : null;
      })
      .filter(Boolean);
  }

  const planLevelSourceFile = extractSourceFileFromPlanItem(source);
  if (planLevelSourceFile) {
    actions = actions.map(action => (
      action.parameters?.source_file
        ? action
        : attachSourceFileToAction(action, { parameters: { source_file: planLevelSourceFile } })
    ));
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

function getPlanTargetFiles(refactoringPlan) {
  const files = [];
  (refactoringPlan?.actions || []).forEach(action => {
    const sourceFile = extractSourceFileFromPlanItem(action);
    if (sourceFile) files.push(sourceFile);
  });

  const seen = new Set();
  return files
    .map(normalizePathForMatch)
    .filter(file => {
      if (!file || seen.has(file)) return false;
      seen.add(file);
      return true;
    });
}

function planHasExecutableRefactoring(refactoringPlan) {
  return Array.isArray(refactoringPlan?.actions)
    && refactoringPlan.actions.some(action => action?.action_type && action.action_type !== 'noop');
}

function formatFileList(files, limit = 5) {
  const names = files.map(file => file.name || file.file_name || String(file)).filter(Boolean);
  if (names.length <= limit) return names.join(', ');
  return `${names.slice(0, limit).join(', ')} and ${names.length - limit} more`;
}

function replaceSourceFilesByRawImport(currentFiles, rawImport) {
  if (!rawImport?.filesByPath?.size) return currentFiles;

  return currentFiles.map(file => {
    const imported = findRawSourceForPath(rawImport, file.name);
    if (!imported) return file;

    return {
      ...file,
      code: String(imported.source_code || ''),
      language: imported.language || file.language || chooseLanguageFromName(file.name),
      origin: imported.origin || file.origin || 'cuqa',
      sourceMode: imported.source_mode || 'raw',
    };
  });
}

function buildRawSourceImport(files, source = 'workspace') {
  const normalizedFiles = (files || [])
    .filter(file => file?.file_name && typeof file.source_code === 'string')
    .map(file => ({
      ...file,
      source_mode: file.source_mode || 'raw',
      origin: file.origin || source,
    }));

  return {
    filesByPath: new Map(normalizedFiles.map(file => [normalizePathForMatch(file.file_name), file])),
    files: normalizedFiles,
    imported: normalizedFiles.length,
    source,
  };
}

function findRawSourceForPath(rawImport, filePath) {
  const rawSources = rawImport?.filesByPath || new Map();
  if (!rawSources.size) return null;

  const fileKey = normalizePathForMatch(filePath);
  const fileBase = pathBaseName(fileKey);
  const exact = rawSources.get(fileKey);
  if (exact) return exact;

  const values = [...rawSources.values()];
  const pathMatch = values.find(file => {
    const sourceKey = normalizePathForMatch(file.file_name);
    return sourceKey.endsWith(`/${fileKey}`) || fileKey.endsWith(`/${sourceKey}`);
  });
  if (pathMatch) return pathMatch;

  const baseMatches = values.filter(file => pathBaseName(file.file_name) === fileBase);
  return baseMatches.length === 1 ? baseMatches[0] : null;
}

function mergeRawSourceImports(primary, fallback) {
  const merged = buildRawSourceImport(primary?.files || [...(primary?.filesByPath?.values?.() || [])], primary?.source || 'workspace');
  const existingKeys = new Set(merged.filesByPath.keys());

  (fallback?.files || [...(fallback?.filesByPath?.values?.() || [])]).forEach(file => {
    if (!file?.file_name || typeof file.source_code !== 'string') return;
    const key = normalizePathForMatch(file.file_name);
    if (existingKeys.has(key) || findRawSourceForPath(merged, file.file_name)) return;
    existingKeys.add(key);
    merged.files.push(file);
    merged.filesByPath.set(key, file);
  });

  merged.imported = merged.files.length;
  return {
    ...primary,
    ...merged,
    missing: (fallback?.missing || primary?.missing || []).filter(path => !findRawSourceForPath(merged, path)),
    error: primary?.error || fallback?.error || '',
    endpointMissing: Boolean(primary?.endpointMissing && !fallback?.imported),
    source: primary?.source || fallback?.source || merged.source,
  };
}

async function fetchBrowserFallbackSources(filePaths, context) {
  const cached = getCachedSourceFiles(filePaths);
  if (!cached.missing.length || !context?.repoUrl) return cached;

  try {
    const github = await fetchGithubSourceFiles(cached.missing, context.repoUrl);
    if (github.imported) {
      cacheSourceFiles(github.files, {
        repoName: context.repoName,
        repoUrl: context.repoUrl,
        origin: 'github_raw',
      });
    }
    return mergeRawSourceImports(cached, github);
  } catch {
    return cached;
  }
}

function fileMatchesPlanTarget(fileName, targetFiles) {
  const filePath = normalizePathForMatch(fileName);
  const fileBase = pathBaseName(filePath);

  return targetFiles.some(targetFile => {
    const targetPath = normalizePathForMatch(targetFile);
    const targetBase = pathBaseName(targetPath);
    return (
      filePath === targetPath
      || filePath.endsWith(`/${targetPath}`)
      || targetPath.endsWith(`/${filePath}`)
      || fileBase === targetBase
    );
  });
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
        const numericScore = typeof item.score === 'number' ? item.score : Number.NaN;
        const status = label === 'Behavioral Preservation'
          ? String(details.fingerprint_status || (item.passed ? 'passed' : 'failed'))
          : label === 'Invariant Mining'
            ? String(details.status || (item.passed ? 'passed' : 'failed'))
            : (item.passed ? 'passed' : 'failed');

        let cls = status === 'passed' ? 'pass' : status === 'skipped' ? 'neutral' : 'fail';
        let icon = status === 'passed' ? '' : status === 'skipped' ? 'o' : 'x';

        if (status !== 'skipped' && Number.isFinite(numericScore)) {
          if (numericScore > 0.9) {
            cls = 'pass';
            icon = '';
          } else if (numericScore >= 0.5) {
            cls = 'warn';
            icon = '!';
          } else {
            cls = 'fail';
            icon = 'x';
          }
        }

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
  if (fileName.toLowerCase().endsWith('.c') || fileName.toLowerCase().endsWith('.h')) return 'c';
  return 'java';
}

function isSupportedSourcePath(filePath) {
  const normalized = String(filePath || '').toLowerCase();
  return normalized.endsWith('.java') || normalized.endsWith('.py') || normalized.endsWith('.c') || normalized.endsWith('.h');
}

function collectSourcePathsFromTree(node, result = []) {
  if (!node || typeof node !== 'object') return result;

  if (node.type === 'file' && isSupportedSourcePath(node.path || node.name)) {
    result.push(String(node.path || node.name));
    return result;
  }

  (node.children || []).forEach(child => collectSourcePathsFromTree(child, result));
  return result;
}

function uniqueSourcePaths(paths) {
  const seen = new Set();
  return paths
    .map(path => String(path || '').replace(/\\/g, '/').trim())
    .filter(path => {
      if (!path || !isSupportedSourcePath(path) || seen.has(path)) return false;
      seen.add(path);
      return true;
    });
}

function extractSourceFromCuqaPayload(data) {
  const candidates = [
    data?.source_code,
    data?.source,
    data?.content,
    data?.file_content,
    data?.text,
    data?.data?.source_code,
    data?.data?.source,
    data?.data?.content,
    data?.data?.file_content,
    data?.data?.text,
    data?.parsed?.source_code,
    data?.parsed?.source,
    data?.parsed?.content,
    data?.parsed?.file_content,
    data?.parsed?.text,
  ];
  const source = candidates.find(value => typeof value === 'string');
  return source || '';
}

function isCuqaReconstructedSource(file) {
  return file?.origin === 'cuqa' && file?.sourceMode === 'ast_reconstructed';
}

function walkAst(node, visitor) {
  if (!node || typeof node !== 'object') return;
  visitor(node);
  (node.children || []).forEach(child => walkAst(child, visitor));
}

function findAstNodes(ast, type) {
  const nodes = [];
  walkAst(ast, node => {
    if (node.type === type) nodes.push(node);
  });
  return nodes;
}

function findAstNodesByTypeNames(ast, typeNames) {
  const expected = new Set(typeNames.map(type => String(type).toLowerCase()));
  const nodes = [];

  walkAst(ast, node => {
    const normalized = String(node.type || '').toLowerCase();
    if (expected.has(normalized)) nodes.push(node);
  });

  return nodes;
}

function guessJavaType(name) {
  const normalized = String(name || '').toLowerCase();
  if (normalized.includes('id') || normalized.includes('count') || normalized.includes('quantity') || normalized.includes('number')) {
    return 'int';
  }
  if (normalized.startsWith('is') || normalized.startsWith('has')) return 'boolean';
  return 'String';
}

function javaDefaultReturn(type) {
  if (type === 'int' || type === 'long' || type === 'double' || type === 'float') return '0';
  if (type === 'boolean') return 'false';
  return 'null';
}

function uniqueByName(nodes) {
  const seen = new Set();
  return nodes.filter(node => {
    const name = String(node?.name || '').trim();
    if (!name || seen.has(name)) return false;
    seen.add(name);
    return true;
  });
}

function buildJavaSourceFromAst(parsed, filePath) {
  const ast = parsed?.ast;
  const imports = uniqueByName(findAstNodes(ast, 'ImportDeclaration'));
  const classNodes = uniqueByName([
    ...findAstNodes(ast, 'ClassDeclaration'),
    ...findAstNodes(ast, 'ClassOrInterfaceDeclaration'),
    ...findAstNodes(ast, 'InterfaceDeclaration'),
  ]);
  const fallbackClassName = String(parsed?.file || filePath || 'CuqaRecoveredSource')
    .split(/[\\/]/)
    .pop()
    .replace(/\.java$/i, '') || 'CuqaRecoveredSource';
  const classNode = classNodes[0] || { name: fallbackClassName, children: [] };
  const className = sanitizeIdentifier(classNode.name || fallbackClassName);
  const directChildren = Array.isArray(classNode.children) ? classNode.children : [];
  const fields = uniqueByName(directChildren.filter(node => node.type === 'FieldDeclaration'));
  const methods = uniqueByName(directChildren.filter(node => node.type === 'MethodDeclaration'));
  const ctors = uniqueByName(directChildren.filter(node => node.type === 'ConstructorDeclaration'));
  const lines = [];

  imports.forEach(item => {
    if (item.name) lines.push(`import ${item.name};`);
  });
  if (imports.length) lines.push('');

  lines.push(`public class ${className} {`);

  fields.forEach(field => {
    const fieldName = sanitizeIdentifier(field.name || 'field');
    lines.push(`    private ${guessJavaType(fieldName)} ${fieldName};`);
  });
  if (fields.length) lines.push('');

  if (!ctors.length) {
    lines.push(`    public ${className}() {`);
    lines.push('    }');
    lines.push('');
  } else {
    ctors.forEach(ctor => {
      lines.push(`    public ${className}() {`);
      lines.push('    }');
      if (ctor !== ctors[ctors.length - 1] || methods.length) lines.push('');
    });
  }

  methods.forEach((method, index) => {
    const methodName = sanitizeIdentifier(method.name || `method${index + 1}`);
    const parameters = uniqueByName((method.children || []).filter(node => node.type === 'Parameter'));
    const params = parameters.map(param => {
      const paramName = sanitizeIdentifier(param.name || 'value');
      return `${param.paramType || guessJavaType(paramName)} ${paramName}`;
    });
    const getterFieldName = methodName.startsWith('get')
      ? methodName.charAt(3).toLowerCase() + methodName.slice(4)
      : '';
    const setterFieldName = methodName.startsWith('set')
      ? methodName.charAt(3).toLowerCase() + methodName.slice(4)
      : '';
    const matchingGetterField = fields.find(field => field.name === getterFieldName);
    const matchingSetterField = fields.find(field => field.name === setterFieldName);
    const returnType = methodName.startsWith('get')
      ? guessJavaType(getterFieldName || methodName)
      : methodName === 'toString'
        ? 'String'
        : 'void';

    lines.push(`    public ${returnType} ${methodName}(${params.join(', ')}) {`);
    if (matchingGetterField) {
      lines.push(`        return ${sanitizeIdentifier(matchingGetterField.name)};`);
    } else if (matchingSetterField && parameters[0]) {
      lines.push(`        this.${sanitizeIdentifier(matchingSetterField.name)} = ${sanitizeIdentifier(parameters[0].name)};`);
    } else if (methodName === 'toString') {
      lines.push(`        return "${className}";`);
    } else if (returnType !== 'void') {
      lines.push(`        return ${javaDefaultReturn(returnType)};`);
    }
    lines.push('    }');
    if (index < methods.length - 1) lines.push('');
  });

  lines.push('}');
  return `${lines.join('\n')}\n`;
}

function buildPythonSourceFromAst(parsed, filePath) {
  const ast = parsed?.ast;
  const classes = uniqueByName(findAstNodes(ast, 'ClassDef'));
  const functions = uniqueByName(findAstNodes(ast, 'FunctionDef'));
  const asyncFunctions = uniqueByName(findAstNodes(ast, 'AsyncFunctionDef'));
  const lines = [
    `# Reconstructed from CUQA AST for ${String(filePath || parsed?.file || 'source.py')}`,
    '',
  ];

  if (classes.length) {
    classes.forEach((cls, classIndex) => {
      const className = sanitizeIdentifier(cls.name || `RecoveredClass${classIndex + 1}`);
      const classMethods = uniqueByName((cls.children || []).filter(node => node.type === 'FunctionDef' || node.type === 'AsyncFunctionDef'));
      lines.push(`class ${className}:`);
      if (!classMethods.length) {
        lines.push('    pass');
      } else {
        classMethods.forEach((method, methodIndex) => {
          const keyword = method.type === 'AsyncFunctionDef' ? 'async def' : 'def';
          const methodName = sanitizeIdentifier(method.name || `method_${methodIndex + 1}`);
          const params = uniqueByName((method.children || []).filter(node => node.type === 'arg'))
            .map(node => sanitizeIdentifier(node.name || node.arg || 'value'));
          const finalParams = params.length ? params : ['self'];
          lines.push(`    ${keyword} ${methodName}(${finalParams.join(', ')}):`);
          lines.push('        pass');
          if (methodIndex < classMethods.length - 1) lines.push('');
        });
      }
      if (classIndex < classes.length - 1 || functions.length || asyncFunctions.length) lines.push('');
    });
  }

  [...functions, ...asyncFunctions].forEach((fn, index, list) => {
    const keyword = fn.type === 'AsyncFunctionDef' ? 'async def' : 'def';
    const functionName = sanitizeIdentifier(fn.name || `function_${index + 1}`);
    const params = uniqueByName((fn.children || []).filter(node => node.type === 'arg'))
      .map(node => sanitizeIdentifier(node.name || node.arg || 'value'));
    lines.push(`${keyword} ${functionName}(${params.join(', ')}):`);
    lines.push('    pass');
    if (index < list.length - 1) lines.push('');
  });

  if (!classes.length && !functions.length && !asyncFunctions.length) {
    lines.push('pass');
  }

  return `${lines.join('\n')}\n`;
}

function cDefaultReturnValue(functionName) {
  const normalized = String(functionName || '').toLowerCase();
  if (normalized.startsWith('is') || normalized.startsWith('has')) return '0';
  return '0';
}

function normalizeCInclude(includeNode) {
  const name = String(includeNode?.name || '').trim();
  if (/^#\s*include\b/.test(name) && !name.includes('[')) return name;
  return '';
}

function buildCSourceFromAst(parsed, filePath) {
  const ast = parsed?.ast;
  const fileLabel = String(filePath || parsed?.file || 'source.c');
  const includeNodes = findAstNodesByTypeNames(ast, ['IncludeDirective', 'preproc_include']);
  const functionNodes = uniqueByName(findAstNodesByTypeNames(ast, ['FunctionDefinition', 'function_definition']));
  const includeLines = includeNodes
    .map(normalizeCInclude)
    .filter(Boolean);
  const lines = [
    `/* Reconstructed from CUQA AST for ${fileLabel} */`,
  ];

  if (includeLines.length) {
    includeLines.forEach(includeLine => lines.push(includeLine));
  } else {
    lines.push('#include <stdio.h>');
  }
  lines.push('');

  if (!functionNodes.length) {
    lines.push('int sctva_recovered_entry(void) {');
    lines.push('    return 0;');
    lines.push('}');
    return `${lines.join('\n')}\n`;
  }

  functionNodes.forEach((fn, index) => {
    const functionName = sanitizeIdentifier(fn.name || `recovered_function_${index + 1}`);
    lines.push(`int ${functionName}(void) {`);
    lines.push(`    return ${cDefaultReturnValue(functionName)};`);
    lines.push('}');
    if (index < functionNodes.length - 1) lines.push('');
  });

  return `${lines.join('\n')}\n`;
}

function buildSourceFromCuqaAst(data, filePath) {
  const parsed = data?.parsed || {};
  const language = String(parsed.language || chooseLanguageFromName(filePath)).toLowerCase();
  if (language === 'java') return buildJavaSourceFromAst(parsed, filePath);
  if (language === 'python') return buildPythonSourceFromAst(parsed, filePath);
  if (language === 'c') return buildCSourceFromAst(parsed, filePath);
  return '';
}

async function fetchCuqaJson(path, options = {}) {
  const response = await fetch(`${CUQA_API}${path}`, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    throw new Error(data.detail || data.error || `CUQA request failed: ${path}`);
  }

  return data;
}

async function fetchSctvaCuqaWorkspaceSources(filePaths) {
  try {
    const response = await fetch(`${getSctvaApiBaseUrl()}/sctva/cuqa-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_paths: filePaths }),
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }

    if (!response.ok || !Array.isArray(data.files)) {
      return {
        filesByPath: new Map(),
        imported: 0,
        missing: [],
        error: data.error || `SCTVA CUQA source import failed with HTTP ${response.status}.`,
        endpointMissing: response.status === 404,
      };
    }

    const sourceImport = buildRawSourceImport(data.files, 'sctva_cuqa_sources');
    return {
      ...sourceImport,
      missing: Array.isArray(data.missing) ? data.missing : [],
      error: '',
      endpointMissing: false,
    };
  } catch {
    return {
      filesByPath: new Map(),
      imported: 0,
      missing: [],
      error: 'SCTVA CUQA source import endpoint is unavailable.',
      endpointMissing: true,
    };
  }
}

async function fetchCuqaWorkspaceSources(filePaths) {
  const context = getSourceContext();
  try {
    const data = await fetchCuqaJson('/api/source-files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_paths: filePaths }),
    });

    if (!Array.isArray(data.files)) {
      throw new Error('CUQA source-files response did not include files.');
    }

    const sourceImport = buildRawSourceImport(data.files, data.source || 'cuqa_workspace');
    if (sourceImport.imported) {
      cacheSourceFiles(sourceImport.files, {
        repoName: context.repoName,
        repoUrl: context.repoUrl,
        origin: 'cuqa_workspace',
      });
    }

    return mergeRawSourceImports({
      ...sourceImport,
      missing: Array.isArray(data.missing) ? data.missing : [],
      error: '',
      endpointMissing: false,
      source: data.source || 'cuqa_workspace',
    }, await fetchBrowserFallbackSources(filePaths, context));
  } catch (error) {
    const fallback = await fetchSctvaCuqaWorkspaceSources(filePaths);
    if (fallback.imported) {
      cacheSourceFiles(fallback.files || [...fallback.filesByPath.values()], {
        repoName: context.repoName,
        repoUrl: context.repoUrl,
        origin: 'sctva_cuqa_sources',
      });
    }
    return mergeRawSourceImports({
      ...fallback,
      error: fallback.error || error.message || 'CUQA raw source import endpoint is unavailable.',
      endpointMissing: fallback.endpointMissing,
    }, await fetchBrowserFallbackSources(filePaths, context));
  }
}

async function fetchCuqaParsedFile(filePath, rawSourceOverride = '') {
  let data = {};
  try {
    data = await fetchCuqaJson('/api/parse-ast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath }),
    });
  } catch (error) {
    if (rawSourceOverride) {
      return {
        source: rawSourceOverride,
        language: chooseLanguageFromName(filePath),
        summary: null,
        sourceMode: 'raw',
      };
    }
    throw error;
  }
  const rawSource = extractSourceFromCuqaPayload(data);
  // CUQA exposes AST parsing but no raw-source route. Raw files are recovered
  // through SCTVA's CUQA workspace endpoint before transformation.
  const sourceFromEndpoint = rawSourceOverride || rawSource;
  const reconstructedSource = sourceFromEndpoint ? '' : buildSourceFromCuqaAst(data, filePath);

  return {
    source: sourceFromEndpoint || reconstructedSource,
    language: String(data?.parsed?.language || chooseLanguageFromName(filePath)).toLowerCase(),
    summary: data?.summary || null,
    sourceMode: sourceFromEndpoint ? 'raw' : 'ast_reconstructed',
  };
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

function parseStoredJson(rawValue) {
  try {
    return rawValue ? JSON.parse(rawValue) : null;
  } catch {
    return null;
  }
}

function isPlanObject(value) {
  return Boolean(
    value
    && typeof value === 'object'
    && !Array.isArray(value)
    && (
      Array.isArray(value.steps)
      || Array.isArray(value.actions)
      || value.plan_id
      || value.planId
    )
  );
}

function extractRdpPlan(value) {
  const storedValue = typeof value === 'string' ? parseStoredJson(value) : value;
  if (!storedValue || typeof storedValue !== 'object') return null;
  if (isPlanObject(storedValue)) return storedValue;

  const wrappedPlans = [
    storedValue.plan,
    storedValue.refactoring_plan,
    storedValue.rdp_sample,
    storedValue.generatedPlan,
    storedValue.latestPlan,
  ];

  for (const wrappedPlan of wrappedPlans) {
    const plan = extractRdpPlan(wrappedPlan);
    if (plan) return plan;
  }

  return null;
}

function readStorageJson(storage, key) {
  try {
    return parseStoredJson(storage.getItem(key));
  } catch {
    return null;
  }
}

function readRdpSessionPlan() {
  if (typeof window === 'undefined') return null;

  const storageSources = [
    [window.localStorage, RDP_AGENT_LOCAL_SESSION_KEY],
    [window.sessionStorage, RDP_AGENT_SESSION_KEY],
    [window.sessionStorage, RDP_AGENT_LOCAL_SESSION_KEY],
    [window.localStorage, RDP_AGENT_SESSION_KEY],
  ];

  for (const [storage, key] of storageSources) {
    const plan = extractRdpPlan(readStorageJson(storage, key));
    if (plan) return plan;
  }

  const history = readStorageJson(window.localStorage, RDP_AGENT_HISTORY_KEY);
  if (Array.isArray(history)) {
    for (const entry of history) {
      const plan = extractRdpPlan(entry);
      if (plan) return plan;
    }
  }

  return null;
}

function buildRdpPlanFileName(plan) {
  const planId = String(plan?.plan_id || plan?.planId || 'latest').trim() || 'latest';
  return `RDP Agent plan: ${planId}.json`;
}

function isBrowserRefreshNavigation() {
  if (typeof window === 'undefined' || !window.performance) return false;

  const navigationEntry = window.performance.getEntriesByType?.('navigation')?.[0];
  if (navigationEntry?.type) return navigationEntry.type === 'reload';

  return window.performance.navigation?.type === 1;
}

function readSctvaSessionState() {
  if (typeof window === 'undefined') return null;

  try {
    if (isBrowserRefreshNavigation()) {
      window.sessionStorage.removeItem(SCTVA_AGENT_SESSION_KEY);
      return null;
    }

    const rawState = window.sessionStorage.getItem(SCTVA_AGENT_SESSION_KEY);
    return rawState ? JSON.parse(rawState) : null;
  } catch {
    return null;
  }
}

function writeSctvaSessionState(state) {
  if (typeof window === 'undefined') return;

  try {
    window.sessionStorage.setItem(SCTVA_AGENT_SESSION_KEY, JSON.stringify(state));
  } catch {
    // Ignore storage quota or browser privacy mode failures.
  }
}

function clearSctvaSessionState() {
  if (typeof window === 'undefined') return;

  try {
    window.sessionStorage.removeItem(SCTVA_AGENT_SESSION_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
}

function clearSctvaArtifactStorage() {
  if (typeof window === 'undefined') return;

  try {
    window.sessionStorage.removeItem(SCTVA_ARTIFACT_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }

  try {
    window.localStorage.removeItem(SCTVA_ARTIFACT_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
}

function storageByteSize(value) {
  try {
    return new Blob([value]).size;
  } catch {
    return String(value || '').length;
  }
}

function crc32(bytes) {
  let crc = 0xffffffff;
  bytes.forEach(byte => {
    crc = ZIP_CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  });
  return (crc ^ 0xffffffff) >>> 0;
}

function writeUint16(buffer, offset, value) {
  buffer[offset] = value & 0xff;
  buffer[offset + 1] = (value >>> 8) & 0xff;
}

function writeUint32(buffer, offset, value) {
  buffer[offset] = value & 0xff;
  buffer[offset + 1] = (value >>> 8) & 0xff;
  buffer[offset + 2] = (value >>> 16) & 0xff;
  buffer[offset + 3] = (value >>> 24) & 0xff;
}

function concatUint8Arrays(chunks) {
  const size = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const combined = new Uint8Array(size);
  let offset = 0;
  chunks.forEach(chunk => {
    combined.set(chunk, offset);
    offset += chunk.length;
  });
  return combined;
}

function getZipDateTime(date = new Date()) {
  const year = Math.max(1980, date.getFullYear());
  return {
    time: (
      (date.getHours() << 11)
      | (date.getMinutes() << 5)
      | Math.floor(date.getSeconds() / 2)
    ),
    date: (
      ((year - 1980) << 9)
      | ((date.getMonth() + 1) << 5)
      | date.getDate()
    ),
  };
}

function encodeUtf8(value) {
  return new TextEncoder().encode(String(value ?? ''));
}

function normalizeZipPath(value, fallback) {
  const normalized = normalizeArtifactPath(value, fallback)
    .replace(/^[A-Za-z]:\//, '')
    .split('/')
    .filter(part => part && part !== '.' && part !== '..')
    .join('/');

  return normalized || normalizeArtifactPath(fallback, 'source_code');
}

function makeUniqueZipPath(path, usedPaths) {
  const normalized = normalizeZipPath(path, 'source_code');
  const lower = normalized.toLowerCase();
  if (!usedPaths.has(lower)) {
    usedPaths.add(lower);
    return normalized;
  }

  const slashIndex = normalized.lastIndexOf('/');
  const folder = slashIndex >= 0 ? normalized.slice(0, slashIndex + 1) : '';
  const name = slashIndex >= 0 ? normalized.slice(slashIndex + 1) : normalized;
  const dotIndex = name.lastIndexOf('.');
  const stem = dotIndex > 0 ? name.slice(0, dotIndex) : name;
  const extension = dotIndex > 0 ? name.slice(dotIndex) : '';
  let counter = 2;

  while (true) {
    const nextPath = `${folder}${stem}_${counter}${extension}`;
    const nextLower = nextPath.toLowerCase();
    if (!usedPaths.has(nextLower)) {
      usedPaths.add(nextLower);
      return nextPath;
    }
    counter += 1;
  }
}

function createStoredZipBlob(entries) {
  const timestamp = getZipDateTime();
  const localChunks = [];
  const centralChunks = [];
  let offset = 0;

  entries.forEach(entry => {
    const nameBytes = encodeUtf8(entry.path);
    const contentBytes = encodeUtf8(entry.content);
    const checksum = crc32(contentBytes);
    const localHeader = new Uint8Array(30 + nameBytes.length);

    writeUint32(localHeader, 0, 0x04034b50);
    writeUint16(localHeader, 4, 20);
    writeUint16(localHeader, 6, 0x0800);
    writeUint16(localHeader, 8, 0);
    writeUint16(localHeader, 10, timestamp.time);
    writeUint16(localHeader, 12, timestamp.date);
    writeUint32(localHeader, 14, checksum);
    writeUint32(localHeader, 18, contentBytes.length);
    writeUint32(localHeader, 22, contentBytes.length);
    writeUint16(localHeader, 26, nameBytes.length);
    writeUint16(localHeader, 28, 0);
    localHeader.set(nameBytes, 30);

    localChunks.push(localHeader, contentBytes);

    const centralHeader = new Uint8Array(46 + nameBytes.length);
    writeUint32(centralHeader, 0, 0x02014b50);
    writeUint16(centralHeader, 4, 20);
    writeUint16(centralHeader, 6, 20);
    writeUint16(centralHeader, 8, 0x0800);
    writeUint16(centralHeader, 10, 0);
    writeUint16(centralHeader, 12, timestamp.time);
    writeUint16(centralHeader, 14, timestamp.date);
    writeUint32(centralHeader, 16, checksum);
    writeUint32(centralHeader, 20, contentBytes.length);
    writeUint32(centralHeader, 24, contentBytes.length);
    writeUint16(centralHeader, 28, nameBytes.length);
    writeUint16(centralHeader, 30, 0);
    writeUint16(centralHeader, 32, 0);
    writeUint16(centralHeader, 34, 0);
    writeUint16(centralHeader, 36, 0);
    writeUint32(centralHeader, 38, 0);
    writeUint32(centralHeader, 42, offset);
    centralHeader.set(nameBytes, 46);

    centralChunks.push(centralHeader);
    offset += localHeader.length + contentBytes.length;
  });

  const centralDirectory = concatUint8Arrays(centralChunks);
  const endRecord = new Uint8Array(22);
  writeUint32(endRecord, 0, 0x06054b50);
  writeUint16(endRecord, 4, 0);
  writeUint16(endRecord, 6, 0);
  writeUint16(endRecord, 8, entries.length);
  writeUint16(endRecord, 10, entries.length);
  writeUint32(endRecord, 12, centralDirectory.length);
  writeUint32(endRecord, 16, offset);
  writeUint16(endRecord, 20, 0);

  return new Blob([...localChunks, centralDirectory, endRecord], { type: 'application/zip' });
}

function findResultForSource(source, results) {
  const sourcePath = normalizePathForMatch(source?.name);
  const sourceBase = pathBaseName(sourcePath);

  return results.find(result => {
    const resultPath = normalizePathForMatch(result?.file_name);
    return resultPath && sourcePath && resultPath === sourcePath;
  }) || results.find(result => {
    const resultPath = normalizePathForMatch(result?.file_name);
    const resultBase = pathBaseName(resultPath);
    return (
      resultPath
      && sourcePath
      && (
        resultPath.endsWith(`/${sourcePath}`)
        || sourcePath.endsWith(`/${resultPath}`)
        || resultBase === sourceBase
      )
    );
  }) || null;
}

function buildTransformedProjectZipEntries(sourceFiles, results) {
  const usedPaths = new Set();
  const consumedResults = new Set();
  const entries = sourceFiles.flatMap((source, index) => {
    const result = findResultForSource(source, results);
    if (result) consumedResults.add(result);
    if (
      isCuqaReconstructedSource(source)
      && (!result || result.transformation_applied === false)
    ) {
      return [];
    }

    return [{
      path: makeUniqueZipPath(source?.name || `file_${index + 1}`, usedPaths),
      content: result ? String(result.refactored_code ?? source?.code ?? '') : String(source?.code ?? ''),
    }];
  });

  results.forEach((result, index) => {
    if (consumedResults.has(result)) return;
    entries.push({
      path: makeUniqueZipPath(result?.file_name || `transformed_file_${index + 1}`, usedPaths),
      content: String(result?.refactored_code ?? ''),
    });
  });

  return entries.filter(entry => entry.path);
}

function slugifyFileName(value, fallback) {
  return String(value || fallback || 'sctva_transformed_project')
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    || 'sctva_transformed_project';
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function getBrowserStorage(name) {
  if (typeof window === 'undefined') return null;

  try {
    return window[name] || null;
  } catch {
    return null;
  }
}

function normalizeArtifactPath(value, fallback) {
  const normalized = String(value || fallback || '')
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/^\/+/, '')
    .replace(/\/+/g, '/')
    .trim();

  return normalized || 'source_code';
}

function insertArtifactTreeNode(nodes, parts, file) {
  const [head, ...tail] = parts;
  if (!head) return;

  if (!tail.length) {
    nodes.push({
      type: 'file',
      name: head,
      path: file.path,
      language: file.language,
      success: file.success,
      rollback_occurred: file.rollback_occurred,
      transformation_applied: file.transformation_applied,
    });
    return;
  }

  let folder = nodes.find(node => node.type === 'folder' && node.name === head);
  if (!folder) {
    folder = {
      type: 'folder',
      name: head,
      path: parts.slice(0, parts.length - tail.length).join('/'),
      children: [],
    };
    nodes.push(folder);
  }

  insertArtifactTreeNode(folder.children, tail, file);
}

function buildArtifactTree(files) {
  const tree = [];
  files.forEach(file => {
    insertArtifactTreeNode(tree, file.path.split('/').filter(Boolean), file);
  });
  return tree;
}

function buildStoredFileArtifact({ result, source, index }) {
  const path = normalizeArtifactPath(result?.file_name || source?.name, `file_${index + 1}`);

  return {
    path,
    file_name: path.split('/').pop() || path,
    language: result?.language || source?.language || chooseLanguageFromName(path),
    origin: result?.origin || source?.origin || 'sctva',
    source_mode: result?.source_mode || source?.sourceMode || 'raw',
    original_code: String(source?.code || ''),
    transformed_code: String(result?.refactored_code || ''),
    success: Boolean(result?.success),
    rollback_occurred: Boolean(result?.rollback_occurred),
    transformation_applied: result?.transformation_applied !== false,
    status: result?.status || '',
    application_status: result?.application_status || '',
    confidence_score: typeof result?.confidence_score === 'number' ? result.confidence_score : null,
    validation_score: typeof result?.validation_score === 'number' ? result.validation_score : null,
    confidence_applicable: result?.confidence_applicable !== false,
    validation: result?.validation || {},
    safety_report: result?.safety_report || {},
  };
}

function buildSctvaStorageArtifact({
  data,
  results,
  sourceFiles,
  requestId,
  cuqaWorkspaceMeta,
}) {
  const files = results.map((result, index) => {
    const path = normalizeArtifactPath(result?.file_name, `file_${index + 1}`);
    const source = sourceFiles.find(item => normalizeArtifactPath(item.name) === path)
      || sourceFiles.find(item => pathBaseName(item.name) === pathBaseName(path))
      || null;

    return buildStoredFileArtifact({ result, source, index });
  });

  return {
    schema_version: 1,
    artifact_type: 'sctva_final_transformation',
    saved_at: new Date().toISOString(),
    request_id: data?.request_id || requestId || '',
    folder_structure_source: cuqaWorkspaceMeta ? 'cuqa' : 'uploaded_source',
    workspace: cuqaWorkspaceMeta || null,
    tree: buildArtifactTree(files),
    files,
    safety_report: {
      request_id: data?.request_id || requestId || '',
      success: Boolean(data?.success),
      rollback_occurred: Boolean(data?.rollback_occurred),
      confidence_score: typeof data?.confidence_score === 'number' ? data.confidence_score : null,
      file_reports: files.map(file => ({
        path: file.path,
        success: file.success,
        rollback_occurred: file.rollback_occurred,
        transformation_applied: file.transformation_applied,
        status: file.status,
        application_status: file.application_status,
        confidence_score: file.confidence_score,
        validation_score: file.validation_score,
        report: file.safety_report,
        validation: file.validation,
      })),
    },
  };
}

function persistSctvaStorageArtifact(artifact) {
  if (typeof window === 'undefined') {
    return {
      ok: false,
      storage: 'unavailable',
      key: SCTVA_ARTIFACT_STORAGE_KEY,
      message: 'Browser storage is not available.',
    };
  }

  const serialized = JSON.stringify(artifact);
  const sizeBytes = storageByteSize(serialized);
  const preferLocalStorage = sizeBytes > SCTVA_ARTIFACT_STORAGE_THRESHOLD_BYTES;
  const sessionStore = getBrowserStorage('sessionStorage');
  const localStore = getBrowserStorage('localStorage');
  const preferred = preferLocalStorage
    ? { name: 'localStorage', storage: localStore }
    : { name: 'sessionStorage', storage: sessionStore };
  const fallback = preferLocalStorage
    ? { name: 'sessionStorage', storage: sessionStore }
    : { name: 'localStorage', storage: localStore };

  try {
    if (!preferred.storage) throw new Error(`${preferred.name} is not available.`);
    preferred.storage.setItem(SCTVA_ARTIFACT_STORAGE_KEY, serialized);
    fallback.storage?.removeItem(SCTVA_ARTIFACT_STORAGE_KEY);
    return {
      ok: true,
      storage: preferred.name,
      key: SCTVA_ARTIFACT_STORAGE_KEY,
      sizeBytes,
      fileCount: artifact.files.length,
    };
  } catch (preferredError) {
    try {
      if (!fallback.storage) throw new Error(`${fallback.name} is not available.`);
      fallback.storage.setItem(SCTVA_ARTIFACT_STORAGE_KEY, serialized);
      preferred.storage?.removeItem(SCTVA_ARTIFACT_STORAGE_KEY);
      return {
        ok: true,
        storage: fallback.name,
        key: SCTVA_ARTIFACT_STORAGE_KEY,
        sizeBytes,
        fileCount: artifact.files.length,
        fallback: true,
      };
    } catch (fallbackError) {
      return {
        ok: false,
        storage: 'unavailable',
        key: SCTVA_ARTIFACT_STORAGE_KEY,
        sizeBytes,
        fileCount: artifact.files.length,
        message: fallbackError.message || preferredError.message || 'Browser storage quota exceeded.',
      };
    }
  }
}

function buildRunSignature({
  requestId,
  language,
  sourceFiles,
  refactoringPlanText,
  executionOptionsText,
}) {
  return JSON.stringify({
    requestId: String(requestId || '').trim(),
    language: String(language || '').trim().toLowerCase(),
    sourceFiles: sourceFiles.map(file => ({
      name: file.name,
      language: file.language,
      origin: file.origin,
      sourceMode: file.sourceMode,
      code: file.code,
    })),
    refactoringPlanText,
    executionOptionsText,
  });
}

export default function SCTVAAgentPage() {
  const sourceFileInputRef = useRef(null);
  const planFileInputRef = useRef(null);
  const autoRdpPlanAttemptedRef = useRef(false);
  const autoCuqaImportAttemptedRef = useRef(false);
  const savedState = useMemo(() => readSctvaSessionState() || {}, []);

  const [requestId, setRequestId] = useState(savedState.requestId || '');
  const [language, setLanguage] = useState(savedState.language || 'java');
  const [sourceFiles, setSourceFiles] = useState(Array.isArray(savedState.sourceFiles) ? savedState.sourceFiles : []);
  const [activeFileName, setActiveFileName] = useState(savedState.activeFileName || '');
  const [fileResults, setFileResults] = useState(Array.isArray(savedState.fileResults) ? savedState.fileResults : []);
  const [refactoringPlanText, setRefactoringPlanText] = useState(savedState.refactoringPlanText || '');
  const [executionOptionsText, setExecutionOptionsText] = useState(savedState.executionOptionsText || SCTVAAgentService.defaultExecutionOptionsJson);

  const [sourceFileName, setSourceFileName] = useState(savedState.sourceFileName || 'No source files selected.');
  const [planFileName, setPlanFileName] = useState(savedState.planFileName || 'No plan file selected.');
  const [planLoaded, setPlanLoaded] = useState(Boolean(savedState.planLoaded));
  const [planStepCount, setPlanStepCount] = useState(savedState.planStepCount || 0);

  const [isRunning, setIsRunning] = useState(false);
  const [lastRunSignature, setLastRunSignature] = useState(savedState.lastRunSignature || '');
  const [errorMessage, setErrorMessage] = useState('');
  const [statusTone, setStatusTone] = useState('info');
  const [statusMessage, setStatusMessage] = useState('');

  const [timeline, setTimeline] = useState(Array.isArray(savedState.timeline) ? savedState.timeline : DEFAULT_TIMELINE);
  const [timelineCount, setTimelineCount] = useState(savedState.timelineCount || `${PIPELINE_STAGES.length} Stages`);

  const [validation, setValidation] = useState(savedState.validation || null);
  const [safetyMessages, setSafetyMessages] = useState(Array.isArray(savedState.safetyMessages) ? savedState.safetyMessages : []);
  const [rawResponse, setRawResponse] = useState(savedState.rawResponse || '{}');

  const [metricSuccess, setMetricSuccess] = useState(savedState.metricSuccess || '--');
  const [metricRollback, setMetricRollback] = useState(savedState.metricRollback || '--');
  const [metricConfidence, setMetricConfidence] = useState(savedState.metricConfidence || '--');
  const [metricLanguage, setMetricLanguage] = useState(savedState.metricLanguage || '--');
  const [confidenceLabel, setConfidenceLabel] = useState(savedState.confidenceLabel || 'Idle');
  const [confidenceCopy, setConfidenceCopy] = useState(savedState.confidenceCopy || 'Model analysis will appear after execution.');
  const [confidenceScore, setConfidenceScore] = useState(savedState.confidenceScore || 0);
  const [confidenceApplicable, setConfidenceApplicable] = useState(savedState.confidenceApplicable !== false);

  const [additions, setAdditions] = useState(savedState.additions || 0);
  const [deletions, setDeletions] = useState(savedState.deletions || 0);

  const [logs, setLogs] = useState(Array.isArray(savedState.logs) ? savedState.logs : [
    { level: 'READY', message: 'Transformation Agent initialized.' },
    { level: 'INFO', message: 'Upload source and refactoring plan to begin.' },
  ]);

  const [sourceDragOver, setSourceDragOver] = useState(false);
  const [planDragOver, setPlanDragOver] = useState(false);
  const [isImportingCuqa, setIsImportingCuqa] = useState(false);
  const [cuqaWorkspaceMeta, setCuqaWorkspaceMeta] = useState(savedState.cuqaWorkspaceMeta || null);
  const [cuqaImportWarning, setCuqaImportWarning] = useState(savedState.cuqaImportWarning || '');

  const [diffRows, setDiffRows] = useState(Array.isArray(savedState.diffRows) ? savedState.diffRows : []);
  const [finalCode, setFinalCode] = useState(savedState.finalCode || '');
  const [artifactStorageInfo, setArtifactStorageInfo] = useState(savedState.artifactStorageInfo || null);

  useEffect(() => {
    if (autoRdpPlanAttemptedRef.current) return;
    autoRdpPlanAttemptedRef.current = true;
    importRdpPlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (autoCuqaImportAttemptedRef.current) return;
    autoCuqaImportAttemptedRef.current = true;
    importCuqaWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    writeSctvaSessionState({
      requestId,
      language,
      sourceFiles,
      activeFileName,
      fileResults,
      refactoringPlanText,
      executionOptionsText,
      sourceFileName,
      planFileName,
      planLoaded,
      planStepCount,
      lastRunSignature,
      timeline,
      timelineCount,
      validation,
      safetyMessages,
      rawResponse,
      metricSuccess,
      metricRollback,
      metricConfidence,
      metricLanguage,
      confidenceLabel,
      confidenceCopy,
      confidenceScore,
      confidenceApplicable,
      additions,
      deletions,
      logs,
      cuqaWorkspaceMeta,
      cuqaImportWarning,
      diffRows,
      finalCode,
      artifactStorageInfo,
    });
  }, [
    requestId,
    language,
    sourceFiles,
    activeFileName,
    fileResults,
    refactoringPlanText,
    executionOptionsText,
    sourceFileName,
    planFileName,
    planLoaded,
    planStepCount,
    lastRunSignature,
    timeline,
    timelineCount,
    validation,
    safetyMessages,
    rawResponse,
    metricSuccess,
    metricRollback,
    metricConfidence,
    metricLanguage,
    confidenceLabel,
    confidenceCopy,
    confidenceScore,
    confidenceApplicable,
    additions,
    deletions,
    logs,
    cuqaWorkspaceMeta,
    cuqaImportWarning,
    diffRows,
    finalCode,
    artifactStorageInfo,
  ]);

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

  const activeFileResult = fileResults.find(item => item.file_name === activeFileName) || null;
  const activeSource = sourceFiles.find(file => file.name === activeFileName) || sourceFiles[0];
  const activeSourceName = activeSource ? activeSource.name : '';
  const activeSourceCode = activeSource ? activeSource.code : '';
  const activeFileDisplay = fileResults.length
    ? activeFileResult?.file_name || activeFileName || 'No file selected'
    : activeFileName || activeSourceName || 'No file selected';
  const confidenceFileDisplay = activeFileDisplay === 'No file selected'
    ? activeFileDisplay
    : activeFileDisplay.split(/[\\/]/).filter(Boolean).pop() || activeFileDisplay;
  const fileTabs = fileResults.length
    ? fileResults.map(item => {
      const status = fileResultStatus(item);
      return {
        name: item.file_name,
        displayName: String(item.file_name || '').split(/[\\/]/).filter(Boolean).pop() || item.file_name,
        statusKey: status.key,
        statusLabel: status.label,
        statusTitle: status.title,
      };
    })
    : sourceFiles.map(file => ({
      name: file.name,
      displayName: String(file.name || '').split(/[\\/]/).filter(Boolean).pop() || file.name,
      statusKey: '',
      statusLabel: '',
      statusTitle: '',
    }));

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
          name: file.webkitRelativePath || file.name,
          language: chooseLanguageFromName(file.name),
          code: await readFileAsText(file),
          origin: 'local',
          sourceMode: 'raw',
        }))
      );
      cacheSourceFiles(loaded, { origin: 'manual_upload' });

      setSourceFiles(loaded);
      setSourceFileName(summarizeSourceFiles(loaded));
      setActiveFileName(loaded[0]?.name || '');
      setLanguage(loaded[0]?.language || 'java');
      setCuqaImportWarning('');
      setFileResults([]);
      setDiffRows([]);
      setFinalCode('');
      setRawResponse('{}');
      setValidation(null);
      setSafetyMessages([]);
      setAdditions(0);
      setDeletions(0);
      setArtifactStorageInfo(null);
      pushLog('INFO', `Loaded ${loaded.length} source file${loaded.length === 1 ? '' : 's'} from your computer.`);
    } catch (error) {
      pushLog('ERROR', error.message || 'Failed to load source files.');
    }
  }

  async function importCuqaWorkspace({ silent = false } = {}) {
    setIsImportingCuqa(true);
    if (!silent) {
      setErrorMessage('');
      setCuqaImportWarning('');
      showStatus('Importing source files from CUQA workspace...', 'info');
      pushLog('INFO', 'Reading current CUQA workspace metadata...');
    }

    try {
      const [structure, fileList] = await Promise.all([
        fetchCuqaJson('/api/project-structure'),
        fetchCuqaJson('/api/files').catch(() => null),
      ]);

      const treePaths = collectSourcePathsFromTree(structure.tree);
      const apiPaths = Array.isArray(fileList?.files) ? fileList.files : [];
      const paths = uniqueSourcePaths([...apiPaths, ...treePaths]);

      if (!paths.length) {
        throw new Error('CUQA workspace is loaded, but no Java, Python, or C source files were found.');
      }

      const selectedPaths = paths.slice(0, CUQA_IMPORT_LIMIT);
      const cuqaRawImport = await fetchCuqaWorkspaceSources(selectedPaths);
      if (cuqaRawImport.imported) {
        pushLog(
          'INFO',
          `Imported ${cuqaRawImport.imported} raw source file${cuqaRawImport.imported === 1 ? '' : 's'} from the CUQA workspace.`
        );
      } else if (cuqaRawImport.endpointMissing) {
        pushLog(
          'WARN',
          'No raw source endpoint or browser-cached source was available for the current CUQA workspace.'
        );
      }
      const rawSourceFor = filePath => findRawSourceForPath(cuqaRawImport, filePath)?.source_code || '';
      const firstParsed = await fetchCuqaParsedFile(selectedPaths[0], rawSourceFor(selectedPaths[0]));
      let loaded;

      if (firstParsed.source) {
        const remaining = selectedPaths.slice(1);
        const remainingParsed = await Promise.all(
          remaining.map(async filePath => {
            try {
              return { filePath, parsed: await fetchCuqaParsedFile(filePath, rawSourceFor(filePath)), error: null };
            } catch (error) {
              return { filePath, parsed: null, error };
            }
          })
        );

        loaded = [
          {
            name: selectedPaths[0],
            language: firstParsed.language,
            code: firstParsed.source,
            origin: 'cuqa',
            astSummary: firstParsed.summary,
            sourceMode: firstParsed.sourceMode,
          },
          ...remainingParsed.map(({ filePath, parsed }) => ({
            name: filePath,
            language: parsed?.language || chooseLanguageFromName(filePath),
            code: parsed?.source || '',
            origin: 'cuqa',
            astSummary: parsed?.summary || null,
            sourceMode: parsed?.sourceMode || 'unavailable',
          })),
        ];
      } else {
        loaded = selectedPaths.map(filePath => ({
          name: filePath,
          language: chooseLanguageFromName(filePath),
          code: '',
          origin: 'cuqa',
          astSummary: null,
          sourceMode: 'unavailable',
        }));
      }

      const filesWithSource = loaded.filter(file => String(file.code || '').trim()).length;
      const reconstructedCount = loaded.filter(file => file.sourceMode === 'ast_reconstructed').length;
      const limitedCopy = paths.length > selectedPaths.length
        ? ` Showing first ${selectedPaths.length} of ${paths.length} files.`
        : '';
      const warning = filesWithSource
        ? reconstructedCount
          ? `${reconstructedCount} CUQA file${reconstructedCount === 1 ? '' : 's'} only had AST-reconstructed placeholder source. SCTVA can preview those files, but real refactoring needs raw source text from CUQA or a manual upload.`
          : ''
        : 'CUQA workspace was found, but SCTVA could not prepare raw source code from the CUQA payload.';

      setCuqaWorkspaceMeta({
        repoName: structure.repo_name || fileList?.repo_name || 'CUQA workspace',
        source: structure.source || 'workspace',
        total: paths.length,
        imported: selectedPaths.length,
        filesWithSource,
        reconstructedCount,
      });
      setCuqaImportWarning(warning);
      setSourceFiles(loaded);
      setSourceFileName(`CUQA workspace: ${selectedPaths.length} file${selectedPaths.length === 1 ? '' : 's'} selected.${limitedCopy}`);
      setActiveFileName(loaded[0]?.name || '');
      setLanguage(loaded.find(file => file.code)?.language || loaded[0]?.language || 'java');
      setFileResults([]);
      setDiffRows([]);
      setFinalCode('');
      setRawResponse('{}');
      setValidation(null);
      setSafetyMessages([]);
      setAdditions(0);
      setDeletions(0);
      setArtifactStorageInfo(null);

      if (warning) {
        if (!silent) showStatus(warning, 'warn');
        pushLog('WARN', warning);
      } else {
        const suffix = reconstructedCount
          ? ` (${reconstructedCount} reconstructed from AST)`
          : '';
        showStatus(`Imported ${filesWithSource} source file${filesWithSource === 1 ? '' : 's'} from CUQA${suffix}.`, 'success');
        pushLog('INFO', `Imported ${filesWithSource} source file${filesWithSource === 1 ? '' : 's'} from CUQA workspace${suffix}.`);
      }
    } catch (error) {
      if (!silent) {
        const message = error.message || 'Unable to import from CUQA workspace.';
        setCuqaImportWarning(message);
        showStatus(message, 'warn');
        pushLog('WARN', message);
      }
    } finally {
      setIsImportingCuqa(false);
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

  function importRdpPlan({ silent = false } = {}) {
    const plan = readRdpSessionPlan();

    if (!plan) {
      if (!silent) {
        const message = 'No RDP refactoring plan found. Generate a plan in the RDP Agent first.';
        setErrorMessage(message);
        showStatus(message, 'warn');
        pushLog('WARN', message);
      }
      return false;
    }

    const planText = JSON.stringify(plan, null, 2);
    setRefactoringPlanText(planText);
    setPlanFileName(buildRdpPlanFileName(plan));
    renderPlanTimeline(plan);
    setErrorMessage('');

    if (!silent) {
      showStatus('Imported refactoring plan from RDP Agent.', 'success');
    }
    pushLog('INFO', 'Loaded refactoring plan from RDP Agent.');
    return true;
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

  async function recoverRawSourcesForPlan(refactoringPlan, currentFiles) {
    const planTargetFiles = getPlanTargetFiles(refactoringPlan);
    const targets = planTargetFiles.length
      ? currentFiles.filter(file => fileMatchesPlanTarget(file.name, planTargetFiles))
      : currentFiles;
    const reconstructedTargets = targets.filter(isCuqaReconstructedSource);

    if (!reconstructedTargets.length) return currentFiles;

    pushLog(
      'INFO',
      `Recovering raw CUQA source for ${reconstructedTargets.length} RDP target file${reconstructedTargets.length === 1 ? '' : 's'} before transformation.`
    );

    const rawImport = await fetchCuqaWorkspaceSources(reconstructedTargets.map(file => file.name));
    if (rawImport.imported) {
      const updatedFiles = replaceSourceFilesByRawImport(currentFiles, rawImport);
      setSourceFiles(updatedFiles);
      setCuqaImportWarning('');
      pushLog(
        'INFO',
        `Recovered ${rawImport.imported} raw RDP target source file${rawImport.imported === 1 ? '' : 's'} from CUQA.`
      );
      return updatedFiles;
    }

    const message = rawImport.endpointMissing
      ? [
        'SCTVA could not recover raw source for the RDP target files from CUQA, browser cache, or the remembered GitHub URL.',
        'Upload the target source files manually in SCTVA and run again.',
      ].join(' ')
      : [
        'SCTVA could not recover raw CUQA source for the RDP target files.',
        rawImport.error || '',
        rawImport.missing?.length ? `Missing: ${rawImport.missing.slice(0, 5).join(', ')}${rawImport.missing.length > 5 ? ` and ${rawImport.missing.length - 5} more` : ''}.` : '',
        'Load the same ZIP/GitHub repository in the Repository Input page, or upload the target files manually in SCTVA.',
      ].filter(Boolean).join(' ');
    throw new Error(message);
  }

  function buildPayload({ sourceFilesOverride = null, refactoringPlanOverride = null } = {}) {
    const finalRequestId = requestId.trim() || `sctva_${Date.now()}`;
    const fallbackLanguage = language.trim().toLowerCase();
    const workingSourceFiles = Array.isArray(sourceFilesOverride) ? sourceFilesOverride : sourceFiles;

    const executableFiles = workingSourceFiles.filter(file => String(file.code || '').trim());

    if (!executableFiles.length) {
      throw new Error('No source code is available. Import from CUQA again or upload source files manually.');
    }

    let uploadedJson;
    let executionOptions;

    try {
      uploadedJson = refactoringPlanOverride || SCTVAAgentService.parseJson(refactoringPlanText || '{}');
    } catch (error) {
      throw new Error(`Refactoring plan JSON is invalid: ${error.message}`);
    }

    const refactoringPlan = refactoringPlanOverride || normalizeRefactoringPlan(uploadedJson, finalRequestId);
    const planTargetFiles = getPlanTargetFiles(refactoringPlan);
    let selectedExecutableFiles = planTargetFiles.length
      ? executableFiles.filter(file => fileMatchesPlanTarget(file.name, planTargetFiles))
      : executableFiles;

    if (planTargetFiles.length && !selectedExecutableFiles.length) {
      throw new Error(`No loaded source file matches the refactoring plan target file(s): ${planTargetFiles.join(', ')}`);
    }

    const actionablePlan = planHasExecutableRefactoring(refactoringPlan);
    const reconstructedTargets = selectedExecutableFiles.filter(isCuqaReconstructedSource);
    if (actionablePlan && reconstructedTargets.length) {
      const rawTargets = selectedExecutableFiles.filter(file => !isCuqaReconstructedSource(file));
      if (!rawTargets.length) {
        throw new Error(
          [
            'SCTVA cannot apply the RDP refactoring plan because the matching CUQA files are still AST-reconstructed placeholder code after raw-source recovery.',
            `Affected file${reconstructedTargets.length === 1 ? '' : 's'}: ${formatFileList(reconstructedTargets)}.`,
            'Restart the SCTVA backend, keep the CUQA workspace active, refresh this page, and the automatic CUQA/RDP import plus transformation run will continue.',
          ].join(' ')
        );
      }
      pushLog(
        'WARN',
        `Skipped ${reconstructedTargets.length} CUQA AST-reconstructed placeholder file${reconstructedTargets.length === 1 ? '' : 's'} without raw source: ${formatFileList(reconstructedTargets)}.`
      );
      selectedExecutableFiles = rawTargets;
    }

    try {
      executionOptions = {
        enable_sctva_auto_refactoring: true,
        max_parallel_files: inferParallelFileWorkers(selectedExecutableFiles.length),
        ...SCTVAAgentService.parseJson(executionOptionsText || '{}'),
      };
    } catch (error) {
      throw new Error(`Execution options JSON is invalid: ${error.message}`);
    }

    return {
      request_id: finalRequestId,
      language: (selectedExecutableFiles[0]?.language || fallbackLanguage || 'java').toLowerCase(),
      source_code: selectedExecutableFiles[0]?.code || '',
      source_files: selectedExecutableFiles.map(file => ({
        file_name: file.name,
        source_code: file.code,
        language: file.language,
        source_mode: file.sourceMode || 'raw',
        origin: file.origin || 'local',
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
      setConfidenceApplicable(true);
      setConfidenceLabel('Idle');
      setConfidenceCopy('Model analysis will appear after execution.');
      renderValidation(null);
      renderSafetyReport(null);
      setFinalCode('');
      renderDiff('', '');
      renderPipelineTimeline({ phase: 'idle' });
      return;
    }

    const notApplied = result.transformation_applied === false && !result.rollback_occurred;
    setMetricSuccess(notApplied ? 'NO CHANGE' : result.success ? 'YES' : 'NO');
    setMetricRollback(result.rollback_occurred ? 'YES' : 'NO');
    setMetricLanguage(result.language || '--');

    const validationScore = typeof result.validation_score === 'number' ? result.validation_score : null;
    const confidenceScoreValue = result.confidence_applicable === false || notApplied
      ? null
      : typeof result.confidence_score === 'number'
        ? result.confidence_score
        : null;
    const score = notApplied ? validationScore : confidenceScoreValue;
    const scoreApplicable = typeof score === 'number';
    setMetricConfidence(score === null ? 'N/A' : `${Math.round(score * 100)}%`);
    setConfidenceScore(score === null ? 0 : Math.max(0, Math.min(100, score * 100)));
    setConfidenceApplicable(scoreApplicable);
    setConfidenceLabel(
      result.rollback_occurred
        ? 'Rolled Back'
        : notApplied
          ? scoreApplicable ? 'Validated' : 'Not Applied'
          : score !== null && score >= 0.8
            ? 'Highly Safe'
            : score !== null && score >= 0.6
              ? 'Review'
              : 'Risky'
    );
    setConfidenceCopy(
      result.rollback_occurred
        ? 'Validation detected unsafe transformation and rollback was triggered.'
        : notApplied
          ? scoreApplicable
            ? 'Validation passed, but no source-code change was applied. This is a validation score, not a transformation acceptance score.'
            : 'No source-code change was applied, so transformation confidence is not available.'
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
      transformation_applied: data.transformation_applied,
      status: data.status,
      application_status: data.application_status,
      confidence_applicable: data.confidence_applicable,
      confidence_score: data.confidence_score,
      validation_score: data.validation_score,
      source_mode: data.source_mode,
      origin: data.origin,
      refactored_code: data.refactored_code,
      validation: data.validation,
      safety_report: data.safety_report,
    }];
  }

  function renderResult(data) {
    setRawResponse(JSON.stringify(data, null, 2));

    const results = normalizeFileResults(data);
    setFileResults(results);
    const appliedResults = results.filter(isAppliedFileResult);

    const artifact = buildSctvaStorageArtifact({
      data,
      results,
      sourceFiles,
      requestId,
      cuqaWorkspaceMeta,
    });
    const storageInfo = persistSctvaStorageArtifact(artifact);
    setArtifactStorageInfo(storageInfo);
    if (storageInfo.ok) {
      pushLog(
        'INFO',
        `Stored ${storageInfo.fileCount} final file${storageInfo.fileCount === 1 ? '' : 's'} and safety report in ${storageInfo.storage}.`
      );
    } else {
      pushLog('WARN', `Could not store final artifacts in browser storage: ${storageInfo.message}`);
    }

    const preferredName = activeFileName && results.some(item => item.file_name === activeFileName)
      ? activeFileName
      : appliedResults[0]?.file_name || results[0]?.file_name;

    if (preferredName) {
      setActiveFileName(preferredName);
    } else {
      setActiveFileName('');
    }

    const selected = appliedResults.find(item => item.file_name === preferredName) || results[0];
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

  async function runTransformation({ trigger = 'manual' } = {}) {
    setErrorMessage('');

    let payload;
    try {
      const finalRequestId = requestId.trim() || `sctva_${Date.now()}`;
      const uploadedJson = SCTVAAgentService.parseJson(refactoringPlanText || '{}');
      const refactoringPlan = normalizeRefactoringPlan(uploadedJson, finalRequestId);
      const preparedSourceFiles = await recoverRawSourcesForPlan(refactoringPlan, sourceFiles);
      payload = buildPayload({
        sourceFilesOverride: preparedSourceFiles,
        refactoringPlanOverride: refactoringPlan,
      });
    } catch (error) {
      setErrorMessage(error.message);
      showStatus(error.message, 'error');
      pushLog('ERROR', error.message);
      return;
    }

    const runSignature = buildRunSignature({
      requestId,
      language,
      sourceFiles,
      refactoringPlanText,
      executionOptionsText,
    });
    setLastRunSignature(runSignature);
    setIsRunning(true);
    showStatus(trigger === 'auto' ? 'Auto-running transformation...' : 'Running transformation...', 'info');
    if (trigger === 'auto') {
      pushLog('INFO', 'Source files and refactoring plan detected. Starting transformation automatically.');
    }
    pushLog('INFO', `Targeting ${payload.source_files?.length || 1} source file${(payload.source_files?.length || 1) === 1 ? '' : 's'} from the refactoring plan.`);
    pushLog('DEBUG', 'Applying refactoring plan and validation pipeline...');
    renderPipelineTimeline({ phase: 'running', overridePlanLoaded: true });

    try {
      const compatible = await buildBackendCompatiblePayload(payload);
      const executionPayload = compatible.payload;
      const unsupported = [
        ...compatible.unsupportedActions,
        ...compatible.unsupportedCapabilities,
      ];
      if (unsupported.length) {
        const message = [
          `The running SCTVA backend does not support: ${unsupported.join(', ')}.`,
          'Restart the SCTVA backend from agents/transformation_agent/safe_code_transformation_agent and run again.',
          'The refactoring actions were preserved; no noop fallback was sent.',
        ].join(' ');
        throw new Error(message);
      }
      const data = await SCTVAAgentService.execute(executionPayload);
      renderResult(data);
      const resultFiles = Array.isArray(data.file_results) && data.file_results.length
        ? data.file_results
        : [data];
      const rolledBackCount = resultFiles.filter(item => item?.rollback_occurred).length;
      const appliedCount = resultFiles.filter(item => item?.transformation_applied && !item?.rollback_occurred).length;
      const notAppliedCount = resultFiles.length - rolledBackCount - appliedCount;
      const noChange = appliedCount === 0 && rolledBackCount === 0;
      const failed = data.success === false;
      const doneMessage = rolledBackCount > 0
        ? `Execution completed: ${rolledBackCount} of ${resultFiles.length} file${resultFiles.length === 1 ? '' : 's'} rolled back${notAppliedCount ? `; ${notAppliedCount} had no proven change` : ''}.`
        : noChange
          ? `Execution completed: no proven source-code change was applied to ${resultFiles.length} file${resultFiles.length === 1 ? '' : 's'}.`
          : failed
            ? 'Execution completed with validation warnings.'
            : 'Execution completed successfully.';
      const doneTone = rolledBackCount > 0 || noChange || failed ? 'warn' : 'success';
      showStatus(doneMessage, doneTone);
      pushLog(
        rolledBackCount > 0 || noChange || failed ? 'WARN' : 'VALID',
        rolledBackCount > 0
          ? `Validation rolled back ${rolledBackCount} file${rolledBackCount === 1 ? '' : 's'}; unaffected files were preserved independently.`
          : noChange
            ? 'No source-code change was applied; inspect the safety report and action log.'
            : failed
              ? 'The API returned a failed transformation result; inspect the safety report.'
              : 'All safety checks completed.'
      );
    } catch (error) {
      const unsupportedActionType = unsupportedActionTypeFromError(error);
      if (unsupportedActionType) {
        const message = [
          `The running SCTVA backend rejected ${unsupportedActionType}.`,
          'Restart the SCTVA backend from agents/transformation_agent/safe_code_transformation_agent and run again.',
          'No compatibility noop or inaccurate rename fallback was applied.',
        ].join(' ');
        setErrorMessage(message);
        showStatus('Execution stopped: backend is outdated.', 'error');
        pushLog('ERROR', message);
        return;
      }
      setErrorMessage(error.message);
      showStatus('Execution failed.', 'error');
      pushLog('ERROR', error.message);
    } finally {
      setIsRunning(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    await runTransformation();
  }

  useEffect(() => {
    const hasExecutableSource = sourceFiles.some(file => String(file.code || '').trim());
    const hasPlan = Boolean(refactoringPlanText.trim());

    if (!hasExecutableSource || !hasPlan || isRunning) return;

    const runSignature = buildRunSignature({
      requestId,
      language,
      sourceFiles,
      refactoringPlanText,
      executionOptionsText,
    });

    if (runSignature === lastRunSignature) return;

    const autoRunTimer = window.setTimeout(() => {
      runTransformation({ trigger: 'auto' });
    }, 0);

    return () => window.clearTimeout(autoRunTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sourceFiles,
    refactoringPlanText,
    executionOptionsText,
    requestId,
    language,
    isRunning,
    lastRunSignature,
  ]);

  function handleClear() {
    clearSctvaSessionState();
    clearSctvaArtifactStorage();
    setRequestId('');
    if (sourceFileInputRef.current) sourceFileInputRef.current.value = '';
    if (planFileInputRef.current) planFileInputRef.current.value = '';

    setSourceFiles([]);
    setActiveFileName('');
    setFileResults([]);
    setCuqaWorkspaceMeta(null);
    setCuqaImportWarning('');
    setRefactoringPlanText('');
    setExecutionOptionsText(SCTVAAgentService.defaultExecutionOptionsJson);
    setSourceFileName('No source files selected.');
    setPlanFileName('No plan file selected.');
    setPlanLoaded(false);
    setPlanStepCount(0);
    setLastRunSignature('');

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
    setConfidenceApplicable(true);
    setAdditions(0);
    setDeletions(0);
    setValidation(null);
    setTimeline(DEFAULT_TIMELINE);
    setTimelineCount(`${PIPELINE_STAGES.length} Stages`);
    setSafetyMessages([]);
    setArtifactStorageInfo(null);
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

  function handleDownloadTransformedProject() {
    if (!fileResults.length) {
      const message = 'Run the transformation before downloading the transformed project ZIP.';
      setErrorMessage(message);
      showStatus(message, 'warn');
      pushLog('WARN', message);
      return;
    }

    const entries = buildTransformedProjectZipEntries(sourceFiles, fileResults);
    if (!entries.length) {
      const message = 'No transformed files are available to download.';
      setErrorMessage(message);
      showStatus(message, 'warn');
      pushLog('WARN', message);
      return;
    }

    const zipBlob = createStoredZipBlob(entries);
    const projectName = slugifyFileName(
      cuqaWorkspaceMeta?.repoName || requestId,
      'sctva_transformed_project'
    );
    downloadBlob(zipBlob, `${projectName}_transformed_code.zip`);
    setErrorMessage('');
    showStatus(`Downloaded transformed project ZIP with ${entries.length} file${entries.length === 1 ? '' : 's'}.`, 'success');
    pushLog('INFO', `Downloaded transformed project ZIP with ${entries.length} file${entries.length === 1 ? '' : 's'} preserving project paths.`);
  }

  function handleDownloadResult() {
    const blob = new Blob([rawResponse || '{}'], { type: 'application/json' });
    downloadBlob(blob, 'sctva_result.json');
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
              <button
                className="sctva-mini-btn"
                type="button"
                onClick={() => importCuqaWorkspace()}
                disabled={isImportingCuqa}
              >
                {isImportingCuqa ? 'Importing CUQA...' : 'Import from CUQA'}
              </button>
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
                  <option value="c">C</option>
                </select>
              </label> */}
            </div>

            {cuqaWorkspaceMeta ? (
              <div className="sctva-cuqa-strip">
                <div>
                  <strong>{cuqaWorkspaceMeta.repoName}</strong>
                  <span>{`${cuqaWorkspaceMeta.source} workspace`}</span>
                </div>
                <div>
                  <strong>{cuqaWorkspaceMeta.imported}</strong>
                  <span>{`of ${cuqaWorkspaceMeta.total} files selected`}</span>
                </div>
                <div>
                  <strong>{cuqaWorkspaceMeta.filesWithSource}</strong>
                  <span>files with source text</span>
                </div>
              </div>
            ) : null}

            <div
              className={`sctva-upload-zone ${sourceDragOver ? 'drag-over' : ''}`}
              onDragEnter={e => { e.preventDefault(); setSourceDragOver(true); }}
              onDragOver={e => { e.preventDefault(); setSourceDragOver(true); }}
              onDragLeave={e => { e.preventDefault(); setSourceDragOver(false); }}
              onDrop={onSourceDrop}
            >
              <div>
                <strong>Upload Source Code Files</strong>
                <p>Files are auto-imported from the CUQA workspace. If CUQA does not send raw text, SCTVA reconstructs code from the AST.</p>
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
            {cuqaImportWarning ? <div className="sctva-alert sctva-alert-warn">{cuqaImportWarning}</div> : null}

            <div
              className={`sctva-upload-zone ${planDragOver ? 'drag-over' : ''}`}
              onDragEnter={e => { e.preventDefault(); setPlanDragOver(true); }}
              onDragOver={e => { e.preventDefault(); setPlanDragOver(true); }}
              onDragLeave={e => { e.preventDefault(); setPlanDragOver(false); }}
              onDrop={onPlanDrop}
            >
              <div>
                <strong>Upload Refactoring Plan JSON</strong>
                <p>Import the latest RDP Agent plan automatically, or drag and drop a JSON file here.</p>
              </div>
              <div className="sctva-hero-actions">
                <button className="sctva-mini-btn" type="button" onClick={() => importRdpPlan()}>
                  Import from RDP
                </button>
                <label className="sctva-mini-btn" htmlFor="sctva-plan-file-input">Browse JSON</label>
              </div>
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
            {statusMessage && statusMessage !== errorMessage
              ? <div className={`sctva-alert sctva-alert-${statusTone}`}>{statusMessage}</div>
              : null}
          </div>

          <div className="sctva-hero-actions" style={{ marginTop: 16 }}>
            <button className="sctva-btn sctva-btn-primary" type="submit" disabled={isRunning}>
              <span></span>
              {isRunning ? 'Running...' : 'Run Transformation'}
            </button>
            {/* <button
              className="sctva-btn sctva-btn-secondary"
              type="button"
              onClick={handleDownloadTransformedProject}
              disabled={isRunning || !fileResults.length}
            >
              <span></span>
              Download ZIP
            </button> */}
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
                  className={`sctva-file-tab ${item.name === activeFileName ? 'active' : ''} ${item.statusKey || ''}`}
                  onClick={() => handleSelectFile(item.name)}
                  title={item.statusTitle ? `${item.name} - ${item.statusTitle}` : item.name}
                >
                  <span>{item.displayName}</span>
                  {item.statusLabel ? <em>{item.statusLabel}</em> : null}
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
          <h2>{`Confidence Score · ${confidenceFileDisplay}`}</h2>

          <div
            className={`sctva-confidence-ring ${confidenceApplicable ? '' : 'not-applicable'}`}
            style={{ ['--score']: `${confidenceScore}%` }}
          >
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
            <button className="sctva-mini-btn" type="button" onClick={handleCopyFinalCode}>Copy Final Code</button>
            <button className="sctva-mini-btn" type="button" style={{ marginLeft: 8 }} onClick={handleDownloadResult}>Download Result JSON</button>
          </div>
        </div>
        {artifactStorageInfo ? (
          <div className={`sctva-storage-status ${artifactStorageInfo.ok ? 'saved' : 'failed'}`}>
            <strong>{artifactStorageInfo.ok ? 'Browser artifact saved' : 'Browser artifact not saved'}</strong>
            <span>
              {artifactStorageInfo.ok
                ? `${artifactStorageInfo.fileCount} file${artifactStorageInfo.fileCount === 1 ? '' : 's'} + safety report in ${artifactStorageInfo.storage} (${artifactStorageInfo.key})`
                : artifactStorageInfo.message}
            </span>
          </div>
        ) : null}
        <pre className="sctva-json-output">{rawResponse}</pre>
      </section>
    </div>
  );
}
