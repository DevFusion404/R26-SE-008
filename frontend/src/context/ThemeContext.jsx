/**
 * ThemeContext.jsx
 * ----------------
 * Global light/dark theme context for the R26-SE-008 frontend.
 *
 * Usage:
 *   import { useTheme } from '../context/ThemeContext';
 *   const { theme, toggleTheme } = useTheme();
 *
 * Persists preference to localStorage under key 'rfiq_theme'.
 * Applies  data-theme="light" | "dark"  on <html> — all CSS
 * variables in theme.css flip automatically, no component edits needed.
 */

import { createContext, useContext, useEffect, useState } from 'react';

const STORAGE_KEY = 'rfiq_theme';
const DEFAULT_THEME = 'dark';

const ThemeContext = createContext({
  theme: DEFAULT_THEME,
  toggleTheme: () => {},
  isDark: true,
});

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  });

  // Apply data-theme attribute on <html> whenever theme changes
  useEffect(() => {
    const html = document.documentElement;
    if (theme === 'light') {
      html.setAttribute('data-theme', 'light');
    } else {
      html.removeAttribute('data-theme');
    }
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore storage errors (e.g., private browsing)
    }
  }, [theme]);

  function toggleTheme() {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  }

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, isDark: theme === 'dark' }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
