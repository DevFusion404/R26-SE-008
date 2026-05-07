/**
 * API Error Handler
 * Centralized error handling and formatting for API responses
 */

export class APIError extends Error {
  constructor(message, status = null, data = null) {
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.data = data;
  }
}

/**
 * Format API error response
 * @param {Error|Response|object} error - Error object or response
 * @returns {object} Formatted error object { message, status, details }
 */
export const formatApiError = (error) => {
  if (error instanceof APIError) {
    return {
      message: error.message,
      status: error.status,
      details: error.data,
      type: 'APIError',
    };
  }

  if (error instanceof TypeError) {
    return {
      message: 'Network error - Unable to connect to backend',
      status: 0,
      details: error.message,
      type: 'NetworkError',
    };
  }

  if (error instanceof SyntaxError) {
    return {
      message: 'Invalid JSON response from server',
      status: null,
      details: error.message,
      type: 'ParseError',
    };
  }

  return {
    message: error.message || 'Unknown error occurred',
    status: null,
    details: error,
    type: 'UnknownError',
  };
};

/**
 * Retry failed API request with exponential backoff
 * @param {function} apiCall - Async function to retry
 * @param {number} maxRetries - Maximum retry attempts
 * @param {number} baseDelay - Initial delay in ms
 * @returns {Promise} Result of successful API call
 */
export const retryApiCall = async (
  apiCall,
  maxRetries = 3,
  baseDelay = 1000
) => {
  let lastError;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await apiCall();
    } catch (error) {
      lastError = error;
      console.warn(
        `API call failed (attempt ${attempt + 1}/${maxRetries}):`,
        error
      );

      if (attempt < maxRetries - 1) {
        const delay = baseDelay * Math.pow(2, attempt); // Exponential backoff
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  throw lastError;
};

/**
 * Create abort controller with timeout
 * @param {number} timeoutMs - Timeout in milliseconds
 * @returns {AbortController} Abort controller
 */
export const createTimeoutAbortController = (timeoutMs) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  // Store timeoutId for cleanup if needed
  controller.timeoutId = timeoutId;

  return controller;
};

/**
 * Log API call for debugging
 * @param {string} method - HTTP method
 * @param {string} url - Request URL
 * @param {object} data - Request data
 * @param {boolean} logEnabled - Whether logging is enabled
 */
export const logApiCall = (method, url, data = null, logEnabled = false) => {
  if (!logEnabled) return;

  console.log(`[API] ${method} ${url}`, data);
};

/**
 * Log API response for debugging
 * @param {string} method - HTTP method
 * @param {string} url - Request URL
 * @param {Response} response - Response object
 * @param {boolean} logEnabled - Whether logging is enabled
 */
export const logApiResponse = (method, url, response, logEnabled = false) => {
  if (!logEnabled) return;

  console.log(
    `[API] ${method} ${url} → ${response.status} ${response.statusText}`
  );
};
