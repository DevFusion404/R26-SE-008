/**
 * agentHealth.js
 * --------------
 * Pings the health endpoint of every service the frontend talks to and
 * reports up / down and the response time. Used by the Overview, Dashboard
 * and Settings pages, so all three show the same live status.
 */

import API_CONFIG from '../config/api.config';

export const SERVICES = [
  { id: 'cuqa',  key: 'CUQA_AGENT',           name: 'CUQA Agent',  role: 'Understand & assess quality', color: 'var(--color-cuqa)',        page: 'cuqa' },
  { id: 'rdp',   key: 'RDP_AGENT',            name: 'RDP Agent',   role: 'Plan refactorings',           color: 'var(--color-rdp)',         page: 'rdp' },
  { id: 'sctva', key: 'TRANSFORMATION_AGENT', name: 'SCTVA Agent', role: 'Transform & validate',        color: 'var(--color-transform)',   page: 'transform' },
  { id: 'diwo',  key: 'DIWO_AGENT',           name: 'DIWO Agent',  role: 'Developer review',            color: 'var(--color-orchestrate)', page: 'orchestrate' },
  { id: 'users', key: 'USER_MANAGEMENT',      name: 'User service', role: 'Sign-in and profiles',       color: 'var(--text-secondary)',    page: null },
];

export const healthUrl = (service) => {
  const cfg = API_CONFIG[service.key];
  return `${String(cfg.baseURL).replace(/\/+$/, '')}${cfg.endpoints.health}`;
};

/** One service: { id, ok, ms, detail } — never throws. */
export async function checkService(service, timeoutMs = 6000) {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(healthUrl(service), { signal: controller.signal });
    const ms = Math.round(performance.now() - started);
    let body = null;
    try { body = await res.json(); } catch { /* some agents answer with text */ }
    const ok = res.ok && (!body?.status || ['ok', 'healthy', 'up'].includes(String(body.status).toLowerCase()));
    return { id: service.id, ok, ms, detail: ok ? null : `HTTP ${res.status}`, body };
  } catch (e) {
    return {
      id: service.id, ok: false, ms: null,
      detail: e.name === 'AbortError' ? `No answer in ${timeoutMs / 1000}s` : 'Not reachable',
    };
  } finally {
    clearTimeout(timer);
  }
}

/** All services in parallel, as a map keyed by service id. */
export async function checkAllServices(services = SERVICES) {
  const results = await Promise.all(services.map((s) => checkService(s)));
  return Object.fromEntries(results.map((r) => [r.id, r]));
}
