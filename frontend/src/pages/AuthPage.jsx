import { useState } from 'react';
import UserService from '../services/userService';

export default function AuthPage({ onAuthSuccess, onGuestLogin }) {
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [role, setRole] = useState('user'); // default role selection
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const resetForm = () => {
    setEmail('');
    setPassword('');
    setFullName('');
    setRole('user');
    setError('');
    setMessage('');
  };

  const handleToggleMode = () => {
    setIsRegister(!isRegister);
    resetForm();
  };

  const validateInputs = () => {
    if (!email || !password) {
      setError('Email and password are required.');
      return false;
    }
    if (isRegister) {
      if (!fullName) {
        setError('Full name is required.');
        return false;
      }
      if (password.length < 8) {
        setError('Password must be at least 8 characters long.');
        return false;
      }
      if (!/\d/.test(password)) {
        setError('Password must contain at least one number.');
        return false;
      }
    }
    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');

    if (!validateInputs()) return;

    setLoading(true);
    try {
      if (isRegister) {
        // Register flow
        const result = await UserService.register(email, password, fullName, role);
        if (!result.success) {
          setError(result.error);
        } else {
          setMessage('Registration successful! Please log in.');
          setIsRegister(false);
          setPassword('');
        }
      } else {
        // Login flow
        const result = await UserService.login(email, password);
        if (!result.success) {
          setError(result.error);
        } else {
          onAuthSuccess(result.user);
        }
      }
    } catch (err) {
      setError('An unexpected error occurred. Please check backend connection.');
    } finally {
      setLoading(false);
    }
  };

  const handleGuest = () => {
    localStorage.setItem('is_guest', 'true');
    onGuestLogin();
  };

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '100vh',
      background: 'var(--bg-root)',
      padding: '24px',
      fontFamily: 'var(--font-body)',
    }}>
      <div style={{
        width: '100%',
        maxWidth: '420px',
        background: 'var(--bg-sidebar)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--r-md)',
        padding: '32px 24px',
        boxShadow: 'var(--shadow-lg)',
      }}>
        {/* Brand Header */}
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <h1 style={{
            fontSize: 'var(--text-3xl)',
            fontWeight: 800,
            color: 'var(--accent)',
            letterSpacing: '0.5px',
          }}>
            RefactorIQ
          </h1>
          <p style={{
            fontSize: 'var(--text-xs)',
            color: 'var(--text-secondary)',
            marginTop: '4px',
            textTransform: 'uppercase',
            letterSpacing: '1px',
          }}>
            Research Prototype Portal
          </p>
        </div>

        {/* Title */}
        <h2 style={{
          fontSize: 'var(--text-xl)',
          fontWeight: 600,
          color: 'var(--text-primary)',
          marginBottom: '16px',
          textAlign: 'center',
        }}>
          {isRegister ? 'Create Account' : 'Sign In'}
        </h2>

        {/* Feedback Messages */}
        {error && (
          <div style={{
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.25)',
            color: 'var(--color-critical)',
            borderRadius: 'var(--r-sm)',
            padding: '10px 12px',
            fontSize: 'var(--text-sm)',
            marginBottom: '16px',
            lineHeight: 1.4,
          }}>
            ?? {error}
          </div>
        )}

        {message && (
          <div style={{
            background: 'rgba(34, 197, 94, 0.08)',
            border: '1px solid rgba(34, 197, 94, 0.25)',
            color: 'var(--color-ok)',
            borderRadius: 'var(--r-sm)',
            padding: '10px 12px',
            fontSize: 'var(--text-sm)',
            marginBottom: '16px',
          }}>
            ? {message}
          </div>
        )}

        {/* Main Auth Form */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {isRegister && (
            <div>
              <label style={{
                display: 'block',
                fontSize: 'var(--text-xs)',
                color: 'var(--text-secondary)',
                marginBottom: '6px',
              }}>Full Name</label>
              <input
                type="text"
                placeholder="John Doe"
                value={fullName}
                onChange={e => setFullName(e.target.value)}
                required
                style={{
                  width: '100%',
                  background: 'var(--bg-input)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)',
                  color: 'var(--text-primary)',
                  padding: '8px 12px',
                  fontSize: 'var(--text-sm)',
                }}
              />
            </div>
          )}

          <div>
            <label style={{
              display: 'block',
              fontSize: 'var(--text-xs)',
              color: 'var(--text-secondary)',
              marginBottom: '6px',
            }}>Email Address</label>
            <input
              type="email"
              placeholder="name@domain.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              style={{
                width: '100%',
                background: 'var(--bg-input)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--r-sm)',
                color: 'var(--text-primary)',
                padding: '8px 12px',
                fontSize: 'var(--text-sm)',
              }}
            />
          </div>

          <div>
            <label style={{
              display: 'block',
              fontSize: 'var(--text-xs)',
              color: 'var(--text-secondary)',
              marginBottom: '6px',
            }}>Password</label>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              style={{
                width: '100%',
                background: 'var(--bg-input)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--r-sm)',
                color: 'var(--text-primary)',
                padding: '8px 12px',
                fontSize: 'var(--text-sm)',
              }}
            />
          </div>

          {isRegister && (
            <div>
              <label style={{
                display: 'block',
                fontSize: 'var(--text-xs)',
                color: 'var(--text-secondary)',
                marginBottom: '6px',
              }}>Account Role</label>
              <select
                value={role}
                onChange={e => setRole(e.target.value)}
                style={{
                  width: '100%',
                  background: 'var(--bg-input)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)',
                  color: 'var(--text-primary)',
                  padding: '8px 12px',
                  fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              >
                <option value="user">User (Standard Access)</option>
                <option value="admin">Admin (Full Access + User Management)</option>
              </select>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              background: 'var(--accent)',
              border: 'none',
              borderRadius: 'var(--r-sm)',
              color: '#000',
              fontWeight: 700,
              padding: '10px 14px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: 'var(--text-sm)',
              marginTop: '8px',
              transition: 'opacity var(--transition-fast)',
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? 'Processing…' : isRegister ? 'Register Account' : 'Sign In'}
          </button>
        </form>

        {/* Separator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          textAlign: 'center',
          margin: '20px 0',
          color: 'var(--text-muted)',
          fontSize: 'var(--text-xs)',
        }}>
          <div style={{ flex: 1, height: '1px', background: 'var(--border)' }} />
          <span style={{ padding: '0 10px' }}>OR</span>
          <div style={{ flex: 1, height: '1px', background: 'var(--border)' }} />
        </div>

        {/* Guest Login Option */}
        <button
          onClick={handleGuest}
          style={{
            width: '100%',
            background: 'transparent',
            border: '1px dashed var(--border-accent)',
            borderRadius: 'var(--r-sm)',
            color: 'var(--accent)',
            fontWeight: 500,
            padding: '10px 14px',
            cursor: 'pointer',
            fontSize: 'var(--text-sm)',
            transition: 'background var(--transition-fast)',
          }}
          onMouseOver={e => e.currentTarget.style.background = 'var(--accent-muted)'}
          onMouseOut={e => e.currentTarget.style.background = 'transparent'}
        >
          ?? Login as Guest
        </button>

        {/* Mode Switcher */}
        <p style={{
          fontSize: 'var(--text-xs)',
          color: 'var(--text-secondary)',
          marginTop: '20px',
          textAlign: 'center',
        }}>
          {isRegister ? 'Already have an account?' : "Don't have an account?"}{' '}
          <button
            onClick={handleToggleMode}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--accent)',
              cursor: 'pointer',
              textDecoration: 'underline',
              fontWeight: 600,
              padding: 0,
            }}
          >
            {isRegister ? 'Sign In Here' : 'Create One Here'}
          </button>
        </p>
      </div>
    </div>
  );
}
