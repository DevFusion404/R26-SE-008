import API_CONFIG, { buildApiUrl } from "../config/api.config";
import { getSessionHeaders } from "./cuqaAgentService";

class DIWOAgentService {
  static async request(url, options = {}) {
    const headers = getSessionHeaders({
      "Content-Type": "application/json",
      ...(options.headers || {}),
    });

    const response = await fetch(url, {
      ...options,
      headers,
    });

    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      throw new Error(data?.error || `HTTP ${response.status}`);
    }

    return data;
  }

  static async checkHealth() {
    try {
      const url = buildApiUrl("DIWO_AGENT", "health");
      const data = await this.request(url, { method: "GET" });
      return data?.status === "ok";
    } catch {
      return false;
    }
  }

  static async startWorkflow(payload) {
    const url = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(url, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async getWorkflow(workflowId) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}`, { method: "GET" });
  }

  static async selectSmells(workflowId, payload) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}/select-smells`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async submitPlanDecision(workflowId, payload) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}/plan-decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async submitTransformationDecision(workflowId, payload) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}/transformation-decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  static async getAuditLogs(workflowId) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}/audit-logs`, { method: "GET" });
  }

  static async completeWorkflow(workflowId, payload) {
    const base = buildApiUrl("DIWO_AGENT", "workflows");
    return this.request(`${base}/${workflowId}/complete`, {
      method: "POST",
      body: JSON.stringify(payload || {}),
    });
  }
}

export default DIWOAgentService;
