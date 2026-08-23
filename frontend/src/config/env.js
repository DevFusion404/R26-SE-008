/**
 * Helper to resolve environment variables.
 * Priority:
 * 1. window.__RUNTIME_CONFIG__[key] (injected at runtime via /env-config.js by Nginx container entrypoint)
 * 2. import.meta.env[key] (Vite build-time injection or local dev)
 * 3. fallback value
 */
export const getEnv = (key, fallback = '') => {
  if (
    typeof window !== 'undefined' &&
    window.__RUNTIME_CONFIG__ &&
    window.__RUNTIME_CONFIG__[key] &&
    typeof window.__RUNTIME_CONFIG__[key] === 'string' &&
    window.__RUNTIME_CONFIG__[key].trim() !== '' &&
    !window.__RUNTIME_CONFIG__[key].includes('${')
  ) {
    return window.__RUNTIME_CONFIG__[key].trim();
  }

  if (
    typeof import.meta !== 'undefined' &&
    import.meta.env &&
    import.meta.env[key] &&
    typeof import.meta.env[key] === 'string' &&
    import.meta.env[key].trim() !== ''
  ) {
    return import.meta.env[key].trim();
  }

  return fallback;
};

export default getEnv;