import API_CONFIG, { buildApiUrl } from '../config/api.config';
import { getSessionHeaders } from './cuqaAgentService';

const DEFAULT_OPTIONS = {
  strict_mode: true,
  enable_behavior_tests: true,
  timeout_seconds: 10,
  require_compilation: false,
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

  getExecuteUrl() {
    const endpointUrl = buildApiUrl('TRANSFORMATION_AGENT', 'execute');
    if (endpointUrl) return endpointUrl;
    return `${API_CONFIG.TRANSFORMATION_AGENT.baseURL}/sctva/execute`;
  },

  async execute(payload) {
    const response = await fetch(this.getExecuteUrl(), {
      method: 'POST',
      headers: getSessionHeaders({ 'Content-Type': 'application/json' }),
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
