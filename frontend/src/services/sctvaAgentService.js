import API_CONFIG, { buildApiUrl } from '../config/api.config';

const DEFAULT_OPTIONS = {
  codet5_runtime_profile: 'openvino-throughput-v2',
  strict_mode: true,
  enable_behavior_tests: true,
  timeout_seconds: 10,
  require_compilation: false,
  // The instruction-tuned local model is the primary candidate generator.
  // SCTVA's existing validators
  // still decide whether to retain the candidate and the deterministic
  // transformers remain the safety fallback.
  enable_codet5: true,
  codet5_mode: 'primary',
  codet5_device: 'auto',
  codet5_backend: 'auto',
  codet5_openvino_model_path: '',
  codet5_model_id: 'Qwen/Qwen2.5-Coder-0.5B-Instruct',
  codet5_max_input_tokens: 512,
  codet5_max_new_tokens: 256,
  // Keep multi-action generations compact. Oversized bundles return to the
  // existing tensor-batched single-action path without dropping any action.
  codet5_max_bundle_output_tokens: 192,
  codet5_cpu_threads: 6,
  // Attempts include initial generations and bounded repairs. Keep the file
  // ceiling aligned with the request ceiling so a complete RDP plan can run.
  codet5_max_actions_per_request: 100,
  codet5_max_actions_per_file: 100,
  // Independent target batching is faster and more accurate for the local
  // OpenVINO Qwen profile than asking the model to rewrite a long bundle.
  codet5_enable_file_bundling: false,
  // Keep mixed-action bundles opt-in: reversible constant bundles stay active,
  // while unrelated refactoring kinds retain their stronger single-action prompts.
  codet5_enable_generic_bundling: false,
  codet5_max_actions_per_bundle: 5,
  codet5_max_generic_actions_per_bundle: 2,
  codet5_generation_timeout_seconds: 30,
  // Zero means every eligible RDP action may reach the model. Per-attempt
  // limits and compact bundle outputs still prevent a single generation from
  // growing without bound.
  codet5_max_generation_seconds_per_request: 0,
  codet5_runtime_cache: true,
  codet5_allow_download: false,
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
    // Merge defaults at request time as well as in the editor. This upgrades
    // browser sessions saved before CodeT5+ options were introduced while
    // still allowing an explicit `enable_codet5: false` override.
    const parsed = parseJson(value, {});
    const options = {
      ...DEFAULT_OPTIONS,
      ...parsed,
    };
    // Migrate browser-saved options from the earlier long-source bundle
    // profile once. A user can still explicitly re-enable bundling after the
    // current profile has been saved.
    if (parsed.codet5_runtime_profile !== DEFAULT_OPTIONS.codet5_runtime_profile) {
      options.codet5_runtime_profile = DEFAULT_OPTIONS.codet5_runtime_profile;
      options.codet5_enable_file_bundling = false;
    }
    // Upgrade request budgets shipped by earlier UI versions so persisted
    // browser sessions do not silently retain the old 20/30/50 limits.
    if ([20, 30, 50].includes(Number(parsed.codet5_max_actions_per_request))) {
      options.codet5_max_actions_per_request = DEFAULT_OPTIONS.codet5_max_actions_per_request;
    }
    if ([5, 10].includes(Number(parsed.codet5_max_actions_per_file))) {
      options.codet5_max_actions_per_file = DEFAULT_OPTIONS.codet5_max_actions_per_file;
    }
    if (Number(parsed.codet5_cpu_threads) === 2) {
      options.codet5_cpu_threads = DEFAULT_OPTIONS.codet5_cpu_threads;
    }
    if ([15, 90].includes(Number(parsed.codet5_generation_timeout_seconds))) {
      options.codet5_generation_timeout_seconds = DEFAULT_OPTIONS.codet5_generation_timeout_seconds;
    }
    if (Number(parsed.codet5_max_generation_seconds_per_request) === 45) {
      options.codet5_max_generation_seconds_per_request = (
        DEFAULT_OPTIONS.codet5_max_generation_seconds_per_request
      );
    }
    // Migrate browser-saved defaults from the earlier base CodeT5+ rollout.
    // Those checkpoints load successfully but are not instruction-tuned and
    // commonly emit unrelated code for refactoring prompts.
    if ([
      'Salesforce/codet5p-770m',
      'Salesforce/codet5p-220m',
    ].includes(String(options.codet5_model_id || ''))) {
      options.codet5_model_id = DEFAULT_OPTIONS.codet5_model_id;
    }
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
