import { useEffect, useMemo, useRef, useState } from 'react';
import SCTVAAgentService from '../../services/sctvaAgentService';
import transformBadge from '../../assets/transform-badge.svg';
import './SCTVAAgentPage.css';

const CUQA_API = import.meta.env.VITE_CUQA_AGENT_API_URL || 'http://localhost:8001';
const CUQA_IMPORT_LIMIT = 50;
const RDP_AGENT_SESSION_KEY = 'rdp-agent-page-state';
const SCTVA_AGENT_SESSION_KEY = 'sctva-agent-page-state';

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

  return {
    ...action,
    parameters: {
      ...(action.parameters || {}),
      source_file: action.parameters?.source_file || sourceFile,
    },
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
    data?.parsed?.source_code,
    data?.parsed?.source,
    data?.parsed?.content,
    data?.parsed?.file_content,
  ];
  const source = candidates.find(value => typeof value === 'string');
  return source || '';
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

function buildSourceFromCuqaAst(data, filePath) {
  const parsed = data?.parsed || {};
  const language = parsed.language || chooseLanguageFromName(filePath);
  if (language === 'java') return buildJavaSourceFromAst(parsed, filePath);
  if (language === 'python') return buildPythonSourceFromAst(parsed, filePath);
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

async function fetchCuqaParsedFile(filePath) {
  const data = await fetchCuqaJson('/api/parse-ast', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_path: filePath }),
  });
  const rawSource = extractSourceFromCuqaPayload(data);

  return {
    source: rawSource || buildSourceFromCuqaAst(data, filePath),
    language: data?.parsed?.language || chooseLanguageFromName(filePath),
    summary: data?.summary || null,
    sourceMode: rawSource ? 'raw' : 'ast_reconstructed',
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

function readRdpSessionPlan() {
  if (typeof window === 'undefined') return null;

  try {
    const rawState = window.sessionStorage.getItem(RDP_AGENT_SESSION_KEY);
    if (!rawState) return null;

    const savedState = JSON.parse(rawState);
    return savedState?.plan && typeof savedState.plan === 'object' && !Array.isArray(savedState.plan)
      ? savedState.plan
      : null;
  } catch {
    return null;
  }
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
      code: file.code,
    })),
    refactoringPlanText,
    executionOptionsText,
  });
}

export default function SCTVAAgentPage() {
  const sourceFileInputRef = useRef(null);
  const planFileInputRef = useRef(null);
  const autoImportAttemptedRef = useRef(false);
  const autoRdpPlanAttemptedRef = useRef(false);
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

  useEffect(() => {
    if (autoImportAttemptedRef.current) return;
    autoImportAttemptedRef.current = true;
    if (!sourceFiles.length) {
      importCuqaWorkspace({ silent: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (autoRdpPlanAttemptedRef.current) return;
    autoRdpPlanAttemptedRef.current = true;
    if (!refactoringPlanText.trim()) {
      importRdpPlan({ silent: true });
    }
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
      additions,
      deletions,
      logs,
      cuqaWorkspaceMeta,
      cuqaImportWarning,
      diffRows,
      finalCode,
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
    additions,
    deletions,
    logs,
    cuqaWorkspaceMeta,
    cuqaImportWarning,
    diffRows,
    finalCode,
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
          name: file.webkitRelativePath || file.name,
          language: chooseLanguageFromName(file.name),
          code: await readFileAsText(file),
          origin: 'local',
        }))
      );

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
        throw new Error('CUQA workspace is loaded, but no Java or Python source files were found.');
      }

      const selectedPaths = paths.slice(0, CUQA_IMPORT_LIMIT);
      const firstParsed = await fetchCuqaParsedFile(selectedPaths[0]);
      let loaded;

      if (firstParsed.source) {
        const remaining = selectedPaths.slice(1);
        const remainingParsed = await Promise.all(
          remaining.map(async filePath => {
            try {
              return { filePath, parsed: await fetchCuqaParsedFile(filePath), error: null };
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
        ? ''
        : 'CUQA workspace was found, but SCTVA could not prepare source code from the CUQA AST payload.';

      setCuqaWorkspaceMeta({
        repoName: structure.repo_name || fileList?.repo_name || 'CUQA workspace',
        source: structure.source || 'workspace',
        total: paths.length,
        imported: selectedPaths.length,
        filesWithSource,
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

      if (warning) {
        if (!silent) showStatus('CUQA workspace detected, but SCTVA could not prepare source code.', 'warn');
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

  function buildPayload() {
    const finalRequestId = requestId.trim() || `sctva_${Date.now()}`;
    const fallbackLanguage = language.trim().toLowerCase();

    const executableFiles = sourceFiles.filter(file => String(file.code || '').trim());

    if (!executableFiles.length) {
      throw new Error('No source code is available. Import from CUQA again or upload source files manually.');
    }

    let uploadedJson;
    let executionOptions;

    try {
      uploadedJson = SCTVAAgentService.parseJson(refactoringPlanText || '{}');
    } catch (error) {
      throw new Error(`Refactoring plan JSON is invalid: ${error.message}`);
    }

    const refactoringPlan = normalizeRefactoringPlan(uploadedJson, finalRequestId);
    const planTargetFiles = getPlanTargetFiles(refactoringPlan);
    const selectedExecutableFiles = planTargetFiles.length
      ? executableFiles.filter(file => fileMatchesPlanTarget(file.name, planTargetFiles))
      : executableFiles;

    if (planTargetFiles.length && !selectedExecutableFiles.length) {
      throw new Error(`No loaded source file matches the refactoring plan target file(s): ${planTargetFiles.join(', ')}`);
    }

    try {
      executionOptions = SCTVAAgentService.parseJson(executionOptionsText || '{}');
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

  async function runTransformation({ trigger = 'manual' } = {}) {
    setErrorMessage('');

    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      setErrorMessage(error.message);
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
            <button className="sctva-mini-btn" type="button" onClick={handleCopyFinalCode}>Copy Final Code</button>
            <button className="sctva-mini-btn" type="button" style={{ marginLeft: 8 }} onClick={handleDownloadResult}>Download Result JSON</button>
          </div>
        </div>
        <pre className="sctva-json-output">{rawResponse}</pre>
      </section>
    </div>
  );
}
