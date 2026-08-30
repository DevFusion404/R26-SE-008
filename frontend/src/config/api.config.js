/**
 * API Configuration
 * Centralized configuration for all agent API endpoints
 */
import { getEnv } from './env';

const API_CONFIG = {
  // === RDP Agent (Refactoring Decision & Planning) ===
  RDP_AGENT: {
    baseURL: getEnv('VITE_RDP_AGENT_API_URL', 'http://localhost:5000'),
    endpoints: {
      generate: '/generate',
      health: '/health',
      config: '/config',
    },
    timeout: 60000, // 60 seconds for plan generation
  },

  // === Code Understanding Agent (CUA) ===
  CUA_AGENT: {
    baseURL: getEnv('VITE_CUA_API_URL', 'http://localhost:5001'),
    endpoints: {
      analyze: '/analyze',
      health: '/health',
    },
    timeout: 120000, // 2 minutes for analysis
  },

  // === CUQA Agent (Code Understanding & Quality Assessment) ===
  CUQA_AGENT: {
    baseURL: getEnv('VITE_CUQA_AGENT_API_URL', getEnv('VITE_CUQA_API_URL', 'http://localhost:8080')),
    endpoints: {
      health: '/api/health',
      uploadZip: '/api/upload-zip',
      githubRepo: '/api/github-repo',
      projectStructure: '/api/project-structure',
      parseAst: '/api/parse-ast',
      qualityReport: '/api/quality-report',
      files: '/api/files',
      repositoryOverview: '/api/repository-overview',
    },
    timeout: 120000, // 2 minutes for analysis
  },

  // === Safe Transformation Agent ===
  TRANSFORMATION_AGENT: {
    baseURL: getEnv('VITE_TRANSFORMATION_AGENT_API_URL', 'http://localhost:8002'),
    endpoints: {
      execute: '/sctva/execute',
      executeFromRdp: '/sctva/execute_from_rdp',
      health: '/health',
    },
    timeout: 90000, // 90 seconds for transformation
  },

  // === DIWO Agent (Developer Interaction & Workflow Orchestration) ===
  DIWO_AGENT: {
    baseURL: getEnv('VITE_DIWO_API_URL', getEnv('VITE_API_URL', 'http://localhost:5001/api')),
    endpoints: {
      workflows: '/workflows',
      health: '/health',
    },
    timeout: 60000,
  },

  // === User Management Service ===
  USER_MANAGEMENT: {
    baseURL: getEnv('VITE_USER_MANAGEMENT_API_URL', 'http://localhost:5005'),
    endpoints: {
      health: '/api/auth/health',
      register: '/api/auth/register',
      login: '/api/auth/login',
      logout: '/api/auth/logout',
      profile: '/api/auth/profile',
      account: '/api/auth/account',
      users: '/api/auth/users',
    },
    timeout: 30000,
  },

  // === Global Configuration ===
  GLOBAL: {
    defaultTimeout: 30000,
    retryAttempts: 3,
    retryDelay: 1000, // milliseconds
    enableLogging: getEnv('VITE_LOG_LEVEL', '') === 'debug',
  },
};

/**
 * Helper function to build full API URL
 * @param {string} agent - Agent name (e.g., 'RDP_AGENT', 'CUA_AGENT')
 * @param {string} endpoint - Endpoint name (e.g., 'generate', 'analyze')
 * @returns {string} Full API URL
 */
export const buildApiUrl = (agent, endpoint) => {
  const agentConfig = API_CONFIG[agent];
  if (!agentConfig) {
    console.error(`Unknown agent: ${agent}`);
    return null;
  }

  const endpointPath = agentConfig.endpoints[endpoint];
  if (!endpointPath) {
    console.error(`Unknown endpoint: ${endpoint} for agent ${agent}`);
    return null;
  }

  return `${agentConfig.baseURL}${endpointPath}`;
};

/**
 * Get configuration for a specific agent
 * @param {string} agent - Agent name
 * @returns {object} Agent configuration
 */
export const getAgentConfig = (agent) => {
  return API_CONFIG[agent] || null;
};

export default API_CONFIG;