/**
 * Helper to resolve environment variables.
 * Priority:
 * 1. window.__RUNTIME_CONFIG__[key] (injected at runtime via /env-config.js by Nginx container entrypoint)
 * 2. import.meta.env[key] (Vite build-time injection or local dev)
 * 3. fallback value
 */
export const getEnv = (key, fallback = '') => {
  if (typeof window !== 'undefined' && window.__RUNTIME_CONFIG__ && window.__RUNTIME_CONFIG__[key]) {
    return window.__RUNTIME_CONFIG__[key];
  }
  if (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env[key]) {
    return import.meta.env[key];
  }
  return fallback;
};

export default getEnv;