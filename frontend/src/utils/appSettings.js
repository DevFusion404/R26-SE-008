/**
 * appSettings.js
 * --------------
 * User defaults kept in this browser (localStorage `refactoriq_settings`).
 * Repository Input starts from these values.
 */

export const SETTINGS_KEY = 'refactoriq_settings';

export const DEFAULT_SETTINGS = {
  defaultThreshold: 75,
  enableCriticalFilter: true,
  enableNamingFilter: true,
  defaultLanguage: 'All',
  defaultMode: 'Comprehensive Refactoring',
};

export function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    return { ...DEFAULT_SETTINGS, ...stored };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    return true;
  } catch {
    return false;
  }
}
