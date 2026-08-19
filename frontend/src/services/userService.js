/**
 * User Management Service
 * Handles registration, login, logout, profile updates, token storage, and admin operations.
 */

import API_CONFIG, { buildApiUrl } from '../config/api.config';

const TOKEN_KEY = 'user_auth_token';
const USER_KEY = 'current_user_profile';

class UserService {
  // -- Token & User Session Helpers ------------------------------------------

  /**
   * Get the active access token from local storage
   * @returns {string|null} Access token
   */
  static getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  /**
   * Store the access token in local storage
   * @param {string} token - The access token from backend
   */
  static setToken(token) {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  }

  /**
   * Get the stored user profile details
   * @returns {object|null} User profile object
   */
  static getUser() {
    try {
      const user = localStorage.getItem(USER_KEY);
      return user ? JSON.parse(user) : null;
    } catch {
      return null;
    }
  }

  /**
   * Store the user profile details in local storage
   * @param {object} user - User profile data
   */
  static setUser(user) {
    if (user) {
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(USER_KEY);
    }
  }

  /**
   * Clear all authentication data (logout)
   */
  static clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  /**
   * Check if a user is currently logged in
   * @returns {boolean} True if token exists
   */
  static isAuthenticated() {
    return !!this.getToken();
  }

  /**
   * Check if the logged in user is an admin
   * @returns {boolean} True if role is admin
   */
  static isAdmin() {
    const user = this.getUser();
    return user && user.role === 'admin';
  }

  /**
   * Build authorization headers with Bearer token
   * @private
   * @returns {object} HTTP headers object
   */
  static _getAuthHeaders() {
    const token = this.getToken();
    return {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    };
  }

  // -- API Authentication Endpoints ------------------------------------------

  /**
   * Check if the user management backend is healthy
   * @returns {Promise<boolean>} True if backend is healthy
   */
  static async checkHealth() {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'health');
      const response = await fetch(url, {
        method: 'GET',
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });
      return response.ok;
    } catch (error) {
      console.error('User Management health check failed:', error);
      return false;
    }
  }

  /**
   * Register a new user
   * @param {string} email - User email
   * @param {string} password - User password
   * @param {string} fullName - Full name of the user
   * @param {string} [role='user'] - Desired role ('user' | 'admin')
   * @returns {Promise<object>} { success, error, data }
   */
  static async register(email, password, fullName, role = 'user') {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'register');
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, full_name: fullName, role }),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Registration failed' };
      }

      return { success: true, data: resJson.data, error: null };
    } catch (error) {
      console.error('Registration API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  /**
   * Login user and initialize session
   * @param {string} email - User email
   * @param {string} password - User password
   * @returns {Promise<object>} { success, error, user, token }
   */
  static async login(email, password) {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'login');
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Login failed' };
      }

      const data = resJson.data || {};
      const access_token = data.session?.access_token;
      const profile = data.profile;

      this.setToken(access_token);
      this.setUser(profile);

      return { success: true, user: profile, token: access_token, error: null };
    } catch (error) {
      console.error('Login API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  /**
   * Logout user and terminate backend + local session
   * @returns {Promise<object>} { success, error }
   */
  static async logout() {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'logout');
      const response = await fetch(url, {
        method: 'POST',
        headers: this._getAuthHeaders(),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json().catch(() => ({}));
      this.clearSession(); // always clear locally even if network request fails

      if (!response.ok && response.status !== 401) {
        return { success: false, error: resJson.error || 'Logout failed' };
      }

      return { success: true, error: null };
    } catch (error) {
      console.error('Logout API error:', error);
      this.clearSession(); // ensure local session is cleared
      return { success: true, error: null }; // treat as success for frontend flow
    }
  }

  // -- Profile Management Endpoints ------------------------------------------

  /**
   * Fetch current user's profile details
   * @returns {Promise<object>} { success, profile, error }
   */
  static async getProfile() {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'profile');
      const response = await fetch(url, {
        method: 'GET',
        headers: this._getAuthHeaders(),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Failed to fetch profile' };
      }

      const profile = resJson.data;
      this.setUser(profile); // update profile cache

      return { success: true, profile, error: null };
    } catch (error) {
      console.error('Get profile API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  /**
   * Update current user's profile details
   * @param {object} data - Updated details (full_name, email, etc.)
   * @returns {Promise<object>} { success, profile, error }
   */
  static async updateProfile(data) {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'profile');
      const response = await fetch(url, {
        method: 'PUT',
        headers: this._getAuthHeaders(),
        body: JSON.stringify(data),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Failed to update profile' };
      }

      const profile = resJson.data;
      this.setUser(profile); // update profile cache

      return { success: true, profile, error: null };
    } catch (error) {
      console.error('Update profile API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  /**
   * Delete current user's account completely
   * @returns {Promise<object>} { success, error }
   */
  static async deleteAccount() {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'account');
      const response = await fetch(url, {
        method: 'DELETE',
        headers: this._getAuthHeaders(),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Failed to delete account' };
      }

      this.clearSession(); // delete local data

      return { success: true, error: null };
    } catch (error) {
      console.error('Delete account API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  // -- Admin Endpoints ------------------------------------------

  /**
   * List all user profiles in the system (Admin only)
   * @returns {Promise<object>} { success, users, error }
   */
  static async getAllUsers() {
    try {
      const url = buildApiUrl('USER_MANAGEMENT', 'users');
      const response = await fetch(url, {
        method: 'GET',
        headers: this._getAuthHeaders(),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Failed to fetch user list' };
      }

      return { success: true, users: resJson.data, error: null };
    } catch (error) {
      console.error('Get all users API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }

  /**
   * Update any user's role (Admin only)
   * @param {string} userId - User ID to update
   * @param {string} role - New role ('user' | 'admin')
   * @returns {Promise<object>} { success, user, error }
   */
  static async updateUserRole(userId, role) {
    try {
      const baseURL = API_CONFIG.USER_MANAGEMENT.baseURL;
      const url = `${baseURL}/api/auth/users/${userId}/role`;
      const response = await fetch(url, {
        method: 'PUT',
        headers: this._getAuthHeaders(),
        body: JSON.stringify({ role }),
        timeout: API_CONFIG.USER_MANAGEMENT.timeout,
      });

      const resJson = await response.json();
      if (!response.ok) {
        return { success: false, error: resJson.error || 'Failed to update user role' };
      }

      return { success: true, user: resJson.data, error: null };
    } catch (error) {
      console.error('Update user role API error:', error);
      return { success: false, error: `Connection error: ${error.message}` };
    }
  }
}

export default UserService;
