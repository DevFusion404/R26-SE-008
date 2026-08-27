#!/bin/sh
RDP_AGENT_API_URL="${VITE_RDP_AGENT_API_URL:-https://rdpagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io}"
CUQA_AGENT_API_URL="${VITE_CUQA_AGENT_API_URL:-${VITE_CUQA_API_URL:-https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io}}"
CUQA_API_URL="${VITE_CUQA_API_URL:-${VITE_CUQA_AGENT_API_URL:-https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io}}"
TRANSFORMATION_AGENT_API_URL="${VITE_TRANSFORMATION_AGENT_API_URL:-https://sctvaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io}"

cat <<EOF > /usr/share/nginx/html/env-config.js
window.__RUNTIME_CONFIG__ = {
  VITE_RDP_AGENT_API_URL: "${RDP_AGENT_API_URL}",
  VITE_CUA_API_URL: "${VITE_CUA_API_URL:-${CUQA_AGENT_API_URL}}",
  VITE_CUQA_AGENT_API_URL: "${CUQA_AGENT_API_URL}",
  VITE_CUQA_API_URL: "${CUQA_API_URL}",
  VITE_TRANSFORMATION_AGENT_API_URL: "${TRANSFORMATION_AGENT_API_URL}",
  VITE_DIWO_API_URL: "${VITE_DIWO_API_URL:-${VITE_API_URL:-}}",
  VITE_API_URL: "${VITE_API_URL:-${VITE_DIWO_API_URL:-}}",
  VITE_USER_MANAGEMENT_API_URL: "${VITE_USER_MANAGEMENT_API_URL:-}",
  VITE_LOG_LEVEL: "${VITE_LOG_LEVEL:-}"
};
EOF

exec "$@"
