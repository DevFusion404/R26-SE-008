import API_CONFIG, { buildApiUrl } from '../config/api.config';

const DEFAULT_OPTIONS = {
  qwen_runtime_profile: 'openvino-fp32-accuracy-v3',
  strict_mode: true,
  enable_behavior_tests: true,
  timeout_seconds: 10,
  require_compilation: false,
  // The instruction-tuned local model is the primary candidate generator.
  // SCTVA's existing validators
  // still decide whether to retain the candidate and the deterministic
  // transformers remain the safety fallback.
  enable_qwen: true,
  qwen_mode: 'primary',
  qwen_device: 'auto',
  qwen_backend: 'auto',
  qwen_openvino_model_path: '',
  qwen_model_id: 'Qwen/Qwen2.5-Coder-0.5B-Instruct',
  qwen_max_input_tokens: 512,
  qwen_max_new_tokens: 256,
  // Keep multi-action generations compact. Oversized bundles return to the
  // existing tensor-batched single-action path without dropping any action.
  qwen_max_bundle_output_tokens: 192,
  qwen_cpu_threads: 6,
  // Attempts include initial generations and bounded repairs. Keep the file
  // ceiling aligned with the request ceiling so a complete RDP plan can run.
  qwen_max_actions_per_request: 100,
  qwen_max_actions_per_file: 100,
  // SCTVA uses one model candidate per RDP action. Grouped prompts and
  // tensor batches are disabled because they reduced candidate acceptance.
  qwen_enable_file_bundling: false,
  // Retained for legacy request compatibility; the backend keeps grouped
  // generation disabled even if an old browser session changes this value.
  qwen_enable_generic_bundling: false,
  qwen_max_actions_per_bundle: 5,
  qwen_max_generic_actions_per_bundle: 2,
  qwen_generation_timeout_seconds: 30,
  // Zero means every eligible RDP action may reach the model. Per-attempt
  // limits and compact bundle outputs still prevent a single generation from
  // growing without bound.
  qwen_max_generation_seconds_per_request: 0,
  qwen_runtime_cache: true,
  qwen_allow_download: false,
};

function parseJson(value, fallback = {}) {
  try {
    return JSON.parse(value || '{}');
  } catch (error) {
    throw new Error(error.message || 'Invalid JSON input.');
  }
}

const SCTVAAgentService = {
  defaultExecutionOptionsJson: JSON.stringify(DEFAULT_OPTIONS),

  parseJson,

  parseExecutionOptions(value) {
    // Merge defaults at request time as well as in the editor while still
    // allowing an explicit `enable_qwen: false` override.
    const parsed = parseJson(value, {});
    const options = {
      ...DEFAULT_OPTIONS,
      ...parsed,
    };
    // Migrate browser-saved options from the earlier long-source bundle
    // profile once. A user can still explicitly re-enable bundling after the
    // current profile has been saved.
    if (parsed.qwen_runtime_profile !== DEFAULT_OPTIONS.qwen_runtime_profile) {
      options.qwen_runtime_profile = DEFAULT_OPTIONS.qwen_runtime_profile;
      options.qwen_enable_file_bundling = false;
    }
    // Upgrade request budgets shipped by earlier UI versions so persisted
    // browser sessions do not silently retain the old 20/30/50 limits.
    if ([20, 30, 50].includes(Number(parsed.qwen_max_actions_per_request))) {
      options.qwen_max_actions_per_request = DEFAULT_OPTIONS.qwen_max_actions_per_request;
    }
    if ([5, 10].includes(Number(parsed.qwen_max_actions_per_file))) {
      options.qwen_max_actions_per_file = DEFAULT_OPTIONS.qwen_max_actions_per_file;
    }
    if (Number(parsed.qwen_cpu_threads) === 2) {
      options.qwen_cpu_threads = DEFAULT_OPTIONS.qwen_cpu_threads;
    }
    if ([15, 90].includes(Number(parsed.qwen_generation_timeout_seconds))) {
      options.qwen_generation_timeout_seconds = DEFAULT_OPTIONS.qwen_generation_timeout_seconds;
    }
    if (Number(parsed.qwen_max_generation_seconds_per_request) === 45) {
      options.qwen_max_generation_seconds_per_request = (
        DEFAULT_OPTIONS.qwen_max_generation_seconds_per_request
      );
    }
    options.qwen_model_id = DEFAULT_OPTIONS.qwen_model_id;
    return options;
  },

  getExecuteUrl() {
    const endpointUrl = buildApiUrl('TRANSFORMATION_AGENT', 'execute');
    if (endpointUrl) return endpointUrl;
    return `${API_CONFIG.TRANSFORMATION_AGENT.baseURL}/sctva/execute`;
  },

  async execute(payload) {
    const response = await fetch(this.getExecuteUrl(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }

    if (!response.ok) {
      throw new Error(data.error || 'Failed to execute transformation.');
    }

    return data;
  },
};

export default SCTVAAgentService;
