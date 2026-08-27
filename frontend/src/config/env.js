const HOSTED_AGENT_DEFAULTS = {
  VITE_RDP_AGENT_API_URL: 'https://rdpagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io',
  VITE_CUA_API_URL: 'https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io',
  VITE_CUQA_AGENT_API_URL: 'https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io',
  VITE_CUQA_API_URL: 'https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io',
  VITE_TRANSFORMATION_AGENT_API_URL: 'https://sctvaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io',
};

const LOCAL_URL_RE = /^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:[/?#]|$)/i;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]']);

const clean = value => (
  typeof value === 'string' && value.trim() !== '' && !value.includes('${')
    ? value.trim()
    : ''
);

const isHostedBrowser = () => {
  if (typeof window === 'undefined' || !window.location) return false;
  const { hostname } = window.location;
  return Boolean(hostname) && !LOCAL_HOSTS.has(hostname);
};

const shouldIgnoreHostedLocalUrl = (key, value) => (
  isHostedBrowser() &&
  HOSTED_AGENT_DEFAULTS[key] &&
  LOCAL_URL_RE.test(value)
);

/**
 * Helper to resolve environment variables.
 * Priority:
 * 1. window.__RUNTIME_CONFIG__[key] (injected at runtime via /env-config.js by Nginx container entrypoint)
 * 2. import.meta.env[key] (Vite build-time injection or local dev)
 * 3. hosted defaults when the deployed frontend would otherwise call localhost
 * 4. fallback value
 */
export const getEnv = (key, fallback = '') => {
  const runtimeValue = clean(
    typeof window !== 'undefined' &&
      window.__RUNTIME_CONFIG__ &&
      window.__RUNTIME_CONFIG__[key]
  );

  if (runtimeValue && !shouldIgnoreHostedLocalUrl(key, runtimeValue)) {
    return runtimeValue;
  }

  const buildValue = clean(
    typeof import.meta !== 'undefined' &&
      import.meta.env &&
      import.meta.env[key]
  );

  if (buildValue && !shouldIgnoreHostedLocalUrl(key, buildValue)) {
    return buildValue;
  }

  if (isHostedBrowser() && HOSTED_AGENT_DEFAULTS[key]) {
    return HOSTED_AGENT_DEFAULTS[key];
  }

  const fallbackValue = clean(fallback);
  if (fallbackValue && !shouldIgnoreHostedLocalUrl(key, fallbackValue)) {
    return fallbackValue;
  }

  if (isHostedBrowser() && HOSTED_AGENT_DEFAULTS[key]) {
    return HOSTED_AGENT_DEFAULTS[key];
  }

  return fallback;
};

export default getEnv;
