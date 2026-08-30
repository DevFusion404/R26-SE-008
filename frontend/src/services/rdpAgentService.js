/**
 * RDP Agent Service
 * Handles all API calls to the Refactoring Decision & Planning Agent
 */

import API_CONFIG, { buildApiUrl } from '../config/api.config';
import { getSessionHeaders } from './cuqaAgentService';

class RDPAgentService {
  /**
   * Check if RDP Agent backend is healthy
   * @returns {Promise<boolean>} True if backend is healthy
   */
  static async checkHealth() {
    try {
      const url = buildApiUrl('RDP_AGENT', 'health');
      const response = await fetch(url, {
        method: 'GET',
        headers: getSessionHeaders(),
        timeout: API_CONFIG.RDP_AGENT.timeout,
      });
      return response.ok;
    } catch (error) {
      console.error('RDP Agent health check failed:', error);
      return false;
    }
  }

  /**
   * Generate refactoring plan from quality report
   * @param {object} qualityReport - Quality report from Code Understanding Agent
   * @param {File} [file] - Optional file upload (FormData)
   * @returns {Promise<object>} { plan, trace, success, error }
   */
  static async generatePlan(qualityReport, file = null) {
    try {
      const url = buildApiUrl('RDP_AGENT', 'generate');

      let response;

      if (file) {
        // Upload as FormData if file provided
        const formData = new FormData();
        formData.append('file', file);

        response = await fetch(url, {
          method: 'POST',
          headers: getSessionHeaders(),
          body: formData,
          timeout: API_CONFIG.RDP_AGENT.timeout,
        });
      } else {
        // Send as JSON
        response = await fetch(url, {
          method: 'POST',
          headers: getSessionHeaders({
            'Content-Type': 'application/json',
          }),
          body: JSON.stringify(qualityReport),
          timeout: API_CONFIG.RDP_AGENT.timeout,
        });
      }

      if (!response.ok) {
        const errorData = await response.json();
        return {
          success: false,
          error: errorData.error || `HTTP ${response.status}`,
          plan: null,
          trace: null,
        };
      }

      const data = await response.json();
      return {
        success: true,
        error: null,
        plan: data.plan,
        trace: data.trace,
      };
    } catch (error) {
      console.error('Failed to generate refactoring plan:', error);
      return {
        success: false,
        error: `Network error: ${error.message}`,
        plan: null,
        trace: null,
      };
    }
  }

  /**
   * Get RDP Agent configuration (weights, severity order, etc.)
   * @returns {Promise<object>} Configuration object
   */
  static async getConfig() {
    try {
      const url = buildApiUrl('RDP_AGENT', 'config');
      const response = await fetch(url, {
        method: 'GET',
        timeout: API_CONFIG.GLOBAL.defaultTimeout,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Failed to fetch RDP Agent config:', error);
      return null;
    }
  }

  /**
   * Update RDP Agent configuration
   * @param {object} config - New configuration object
   * @returns {Promise<boolean>} True if successful
   */
  static async updateConfig(config) {
    try {
      const url = buildApiUrl('RDP_AGENT', 'config');
      const response = await fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config),
        timeout: API_CONFIG.GLOBAL.defaultTimeout,
      });

      return response.ok;
    } catch (error) {
      console.error('Failed to update RDP Agent config:', error);
      return false;
    }
  }

  /**
   * Validate a quality report format
   * @param {object} qualityReport - Quality report to validate
   * @returns {Promise<object>} { valid, errors }
   */
  static async validateQualityReport(qualityReport) {
    try {
      // Client-side validation first
      const errors = this._validateQualityReportFormat(qualityReport);
      if (errors.length > 0) {
        return {
          valid: false,
          errors,
          clientSide: true,
        };
      }

      // Could add server-side validation here in future
      return {
        valid: true,
        errors: [],
        clientSide: true,
      };
    } catch (error) {
      console.error('Quality report validation error:', error);
      return {
        valid: false,
        errors: [error.message],
        clientSide: true,
      };
    }
  }

  /**
   * Client-side validation of quality report format
   * Accepts both CUQA format { files: [...] } and native RDP format { target, smells: [...] }
   * @private
   * @param {object} report - Quality report
   * @returns {array} Array of error messages
   */
  static _validateQualityReportFormat(report) {
    const errors = [];

    if (!report || typeof report !== 'object') {
      errors.push('Report must be a JSON object');
      return errors;
    }

    // --- CUQA format: { files: [{ file, code_smells, metrics }], summary } ---
    if (Array.isArray(report.files)) {
      const totalSmells = report.files.reduce(
        (sum, f) => sum + (f.code_smells?.length || 0),
        0
      );
      if (report.files.length === 0) {
        errors.push('CUQA report has no files');
      } else if (totalSmells === 0) {
        errors.push('CUQA report has no code smells detected across all files');
      }
      // Valid CUQA format — backend will translate it
      return errors;
    }

    // --- Native RDP format: { target, smells: [...] } ---
    if (!report.target) {
      errors.push('Missing required field: target (file/module name)');
    }
    if (!Array.isArray(report.smells)) {
      errors.push('Missing required field: smells (array)');
    } else if (report.smells.length === 0) {
      errors.push('Smells array is empty — nothing to plan');
    } else {
      report.smells.forEach((smell, index) => {
        if (!smell.id)       errors.push(`Smell ${index}: Missing field 'id'`);
        if (!smell.type)     errors.push(`Smell ${index}: Missing field 'type'`);
        if (!smell.severity) errors.push(`Smell ${index}: Missing field 'severity'`);
      });
    }

    return errors;
  }

  /**
   * Download refactoring plan as JSON
   * @param {object} plan - Refactoring plan object
   * @param {string} filename - Output filename
   */
  static downloadPlan(plan, filename = 'refactoring_plan.json') {
    try {
      const jsonStr = JSON.stringify(plan, null, 2);
      const blob = new Blob([jsonStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename || plan.plan_id + '.json';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to download plan:', error);
    }
  }

  /**
   * Copy plan to clipboard
   * @param {object} plan - Refactoring plan object
   * @returns {Promise<boolean>} True if successful
   */
  static async copyPlanToClipboard(plan) {
    try {
      const jsonStr = JSON.stringify(plan, null, 2);
      await navigator.clipboard.writeText(jsonStr);
      return true;
    } catch (error) {
      console.error('Failed to copy plan to clipboard:', error);
      return false;
    }
  }

  /**
   * Export plan metrics/statistics
   * @param {object} plan - Refactoring plan
   * @returns {object} Statistics object
   */
  static getPlanStatistics(plan) {
    if (!plan || !plan.steps) {
      return null;
    }

    const stats = {
      planId: plan.plan_id,
      target: plan.target,
      totalSteps: plan.steps.length,
      refactoringTypes: {},
      targetClasses: new Set(),
      targetMethods: new Set(),
    };

    plan.steps.forEach((step) => {
      // Count refactoring types
      stats.refactoringTypes[step.refactoring] =
        (stats.refactoringTypes[step.refactoring] || 0) + 1;

      // Collect targets
      if (step.target.class) {
        stats.targetClasses.add(step.target.class);
      }
      if (step.target.method) {
        stats.targetMethods.add(step.target.method);
      }
    });

    // Convert sets to arrays for serialization
    stats.uniqueClasses = Array.from(stats.targetClasses).length;
    stats.uniqueMethods = Array.from(stats.targetMethods).length;
    delete stats.targetClasses;
    delete stats.targetMethods;

    return stats;
  }
}

export default RDPAgentService;
