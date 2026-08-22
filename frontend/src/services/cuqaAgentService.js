/**
 * cuqaAgentService.js
 * -------------------
 * Frontend Service for CUQA Agent (Code Understanding & Quality Assessment).
 * Encapsulates all backend API calls to the CUQA FastAPI server (port 8080).
 */

import API_CONFIG, { buildApiUrl } from '../config/api.config';

class CUQAAgentService {
  /**
   * Helper to resolve the endpoint URL using buildApiUrl with fallback
   * @param {string} endpoint 
   * @returns {string}
   */
  static getEndpointUrl(endpoint) {
    const url = buildApiUrl('CUQA_AGENT', endpoint);
    if (url) return url;
    const path = API_CONFIG.CUQA_AGENT.endpoints[endpoint] || `/api/${endpoint}`;
    return `${API_CONFIG.CUQA_AGENT.baseURL}${path}`;
  }

  /**
   * Check health status of the CUQA Agent backend
   * @returns {Promise<{status: string, workspace_loaded: boolean}>}
   */
  static async checkHealth() {
    const url = this.getEndpointUrl('health');
    const response = await fetch(url, {
      method: 'GET',
    });
    if (!response.ok) {
      throw new Error(`Health check failed with status ${response.status}`);
    }
    return response.json();
  }

  /**
   * Upload a ZIP file containing source code to CUQA Agent
   * @param {File} file - ZIP File object
   * @returns {Promise<object>} Result containing repo_name, files_found, language_breakdown, etc.
   */
  static async uploadZip(file) {
    if (!file) throw new Error('No file provided.');
    const url = this.getEndpointUrl('uploadZip');
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to upload ZIP file.');
    }
    return data;
  }

  /**
   * Clone/Load a public GitHub repository into CUQA Agent
   * @param {string} repoUrl - Public GitHub repository URL
   * @returns {Promise<object>} Result containing repo_name, files_found, language_breakdown, etc.
   */
  static async loadGithubRepo(repoUrl) {
    if (!repoUrl || !repoUrl.trim()) {
      throw new Error('Repository URL is required.');
    }
    const url = this.getEndpointUrl('githubRepo');
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: repoUrl.trim() }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to load GitHub repository.');
    }
    return data;
  }

  /**
   * Get the directory tree / project structure of the loaded workspace
   * @returns {Promise<{repo_name: string, source: string, total_source_files: number, tree: object}>}
   */
  static async getProjectStructure() {
    const url = this.getEndpointUrl('projectStructure');
    const response = await fetch(url, {
      method: 'GET',
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to fetch project structure.');
    }
    return data;
  }

  /**
   * Parse AST for a specific source file in the workspace
   * @param {string} filePath - Relative file path (e.g. "src/main.py")
   * @returns {Promise<{parsed: object, summary: object}>}
   */
  static async parseAst(filePath) {
    if (!filePath) throw new Error('file_path is required.');
    const url = this.getEndpointUrl('parseAst');
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || `Failed to parse AST for '${filePath}'.`);
    }
    return data;
  }

  /**
   * Generate quality report for single file or full repository
   * @param {string|null} [filePath=null] - Optional relative path for single file report
   * @returns {Promise<{type: string, report: object}>}
   */
  static async getQualityReport(filePath = null) {
    const url = this.getEndpointUrl('qualityReport');
    const body = filePath ? { file_path: filePath } : {};

    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to generate quality report.');
    }
    return data;
  }

  /**
   * List all discovered source files in the workspace
   * @returns {Promise<{repo_name: string, files: string[], total: number}>}
   */
  static async listFiles() {
    const url = this.getEndpointUrl('files');
    const response = await fetch(url, {
      method: 'GET',
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to list files.');
    }
    return data;
  }
}

export default CUQAAgentService;
