import { useState, useEffect, useCallback } from 'react';
import UserService from '../services/userService';
import { loadHistory } from '../utils/analysisHistory';
import { loadSessionReports } from '../utils/sessionReports';
import './pages.css';

const initialsOf = (name) =>
  (name || '').trim().split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() || '').join('') || 'U';

/** What this browser has recorded — the same for guests and signed-in users. */
function ActivityCard({ onNavigate }) {
  const analyses = loadHistory().length;
  const sessions = loadSessionReports().length;
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">Your activity in this browser</span></div>
      <div className="rg-2" style={{ gap: 10, padding: 16 }}>
        <button type="button" className="ui-stat ui-click" style={{ '--c': 'var(--accent)', textAlign: 'left', fontFamily: 'var(--font-body)' }} onClick={() => onNavigate?.('dashboard')}>
          <span className="k">Analyses <span>→</span></span>
          <span className="v">{analyses}</span>
          <span className="s">open the dashboard</span>
        </button>
        <button type="button" className="ui-stat ui-click" style={{ '--c': 'var(--color-orchestrate)', textAlign: 'left', fontFamily: 'var(--font-body)' }} onClick={() => onNavigate?.('reports')}>
          <span className="k">Session reports <span>→</span></span>
          <span className="v">{sessions}</span>
          <span className="s">open the reports</span>
        </button>
      </div>
    </div>
  );
}

export default function ProfilePage({ currentUser, isGuest, onLogout, onProfileUpdate, onNavigate }) {
  const [fullName, setFullName] = useState(currentUser?.full_name || '');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [adminUsers, setAdminUsers] = useState([]);
  const [adminError, setAdminError] = useState('');
  const [adminQuery, setAdminQuery] = useState('');

  const isAdmin = currentUser?.role === 'admin';
  const dirty = fullName.trim() !== (currentUser?.full_name || '').trim();

  const fetchAdminUsers = useCallback(async () => {
    try {
      const result = await UserService.getAllUsers();
      if (result.success) {
        setAdminUsers(result.users);
      } else {
        setAdminError(result.error);
      }
    } catch {
      setAdminError('Failed to connect to admin endpoints.');
    }
  }, []);

  // Fetch admin panel data if current user is admin (after the first render)
  useEffect(() => {
    if (!isAdmin) return undefined;
    let alive = true;
    (async () => {
      await Promise.resolve();
      if (alive) await fetchAdminUsers();
    })();
    return () => { alive = false; };
  }, [isAdmin, fetchAdminUsers]);

  const handleUpdate = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');

    if (!fullName.trim()) {
      setError('Name field cannot be empty.');
      return;
    }

    setLoading(true);
    try {
      const result = await UserService.updateProfile({ full_name: fullName });
      if (result.success) {
        setMessage('Profile updated.');
        onProfileUpdate(result.profile);
      } else {
        setError(result.error);
      }
    } catch {
      setError('Connection failure updating profile.');
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteAccount = async () => {
    setError('');
    setLoading(true);
    try {
      const result = await UserService.deleteAccount();
      if (result.success) {
        onLogout();
      } else {
        setError(result.error);
        setShowDeleteConfirm(false);
      }
    } catch {
      setError('Connection error deleting account.');
      setShowDeleteConfirm(false);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleRole = async (userId, currentRole) => {
    const newRole = currentRole === 'admin' ? 'user' : 'admin';
    try {
      const result = await UserService.updateUserRole(userId, newRole);
      if (result.success) {
        fetchAdminUsers();
      } else {
        alert(`Error: ${result.error}`);
      }
    } catch {
      alert('Failed to change role.');
    }
  };

  // ── Guest ────────────────────────────────────────────────────────────────
  if (isGuest) {
    return (
      <div className="page-container">
        <section className="ui-hero">
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <div className="ui-avatar" style={{ background: 'var(--bg-hover)', color: 'var(--text-secondary)' }}>G</div>
            <div style={{ flex: '1 1 260px' }}>
              <span className="ui-kicker">Guest session</span>
              <h1 className="ui-title" style={{ marginTop: 2 }}>You are exploring as a guest</h1>
              <p className="ui-lead" style={{ marginTop: 4 }}>Every agent works. Your analyses and reports are kept in this browser only.</p>
            </div>
            <button className="btn btn-primary" onClick={onLogout}>Sign in or register</button>
          </div>
        </section>

        <div className="rg-2" style={{ gap: 16, alignItems: 'start' }}>
          <div className="card">
            <div className="card-header"><span className="card-title">Guest and account compared</span></div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13.5 }}>
              {[
                ['Run all four agents', true, true],
                ['Session reports in this browser', true, true],
                ['Saved profile and name', false, true],
                ['Manage user roles (admin)', false, true],
              ].map(([label, guest, account]) => (
                <div key={label} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 70px 70px', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: 'var(--text-primary)' }}>{label}</span>
                  <span style={{ textAlign: 'center', color: guest ? 'var(--color-ok)' : 'var(--text-muted)' }}>{guest ? '✓' : '—'}</span>
                  <span style={{ textAlign: 'center', color: 'var(--color-ok)' }}>{account ? '✓' : '—'}</span>
                </div>
              ))}
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 70px 70px', gap: 8, fontSize: 12, color: 'var(--text-muted)', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                <span />
                <span style={{ textAlign: 'center' }}>Guest</span>
                <span style={{ textAlign: 'center' }}>Account</span>
              </div>
            </div>
          </div>
          <ActivityCard onNavigate={onNavigate} />
        </div>
      </div>
    );
  }

  // ── Signed-in user ───────────────────────────────────────────────────────
  const shownUsers = adminUsers.filter((u) =>
    `${u.full_name} ${u.email}`.toLowerCase().includes(adminQuery.trim().toLowerCase()));

  return (
    <div className="page-container">
      <section className="ui-hero">
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="ui-avatar">{initialsOf(currentUser?.full_name)}</div>
          <div style={{ flex: '1 1 260px', minWidth: 0 }}>
            <span className="ui-kicker">Account</span>
            <h1 className="ui-title" style={{ marginTop: 2 }}>{currentUser?.full_name || 'Your profile'}</h1>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              <span className="ui-chip">✉ <b>{currentUser?.email}</b></span>
              <span className={`ui-chip ${isAdmin ? 'ok' : ''}`}>{isAdmin ? '🛡 Admin' : '👤 User'}</span>
            </div>
          </div>
          <button className="btn btn-outline" onClick={onLogout}>Log out</button>
        </div>
      </section>

      {error && <div className="alert alert-error">⚠ {error}</div>}
      {message && <div className="alert alert-success">✓ {message}</div>}

      <div className="rg-split" style={{ gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Profile form */}
          <div className="card">
            <div className="card-header"><span className="card-title">Profile details</span></div>
            <form onSubmit={handleUpdate} style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label className="field-label" htmlFor="pf-name">Full name</label>
                <input id="pf-name" className="input" type="text" value={fullName} onChange={e => setFullName(e.target.value)} />
              </div>
              <div>
                <label className="field-label" htmlFor="pf-email">Email (cannot be changed)</label>
                <input id="pf-email" className="input" type="text" value={currentUser?.email || ''} disabled style={{ opacity: 0.7, cursor: 'not-allowed' }} />
              </div>
              <button type="submit" className="btn btn-primary" disabled={loading || !dirty} style={{ alignSelf: 'flex-start' }}>
                {loading ? 'Saving…' : dirty ? 'Save changes' : 'No changes'}
              </button>
            </form>
          </div>

          <ActivityCard onNavigate={onNavigate} />

          {/* Danger zone */}
          <div className="card" style={{ borderColor: 'color-mix(in srgb, var(--color-critical) 35%, transparent)' }}>
            <div className="card-header"><span className="card-title" style={{ color: 'var(--color-critical)' }}>Delete account</span></div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <p style={{ fontSize: 13.5, color: 'var(--text-secondary)' }}>Deletes your profile and sign-in permanently. Reports in this browser are not affected.</p>
              {!showDeleteConfirm ? (
                <button className="btn btn-outline" style={{ alignSelf: 'flex-start', color: 'var(--color-critical)', borderColor: 'color-mix(in srgb, var(--color-critical) 45%, transparent)' }} onClick={() => setShowDeleteConfirm(true)}>
                  Delete my account…
                </button>
              ) : (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: 13.5, color: 'var(--text-primary)', fontWeight: 600 }}>Are you sure?</span>
                  <button className="btn btn-sm" style={{ background: 'var(--color-critical)', color: '#fff' }} onClick={handleDeleteAccount} disabled={loading}>Yes, delete</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setShowDeleteConfirm(false)}>Cancel</button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Admin directory */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">🛡 User directory</span>
            {isAdmin && (
              <input className="input" style={{ maxWidth: 200, padding: '6px 12px', fontSize: 13 }} placeholder="Search users…" value={adminQuery} onChange={e => setAdminQuery(e.target.value)} />
            )}
          </div>
          {!isAdmin ? (
            <div className="empty-state" style={{ padding: 32 }}>
              <span className="empty-icon">🔒</span>
              <p>Only admin accounts can see and change user roles.</p>
            </div>
          ) : adminError ? (
            <div className="alert alert-error" style={{ margin: 16 }}>{adminError}</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr><th>Name</th><th>Email</th><th>Role</th><th style={{ textAlign: 'right' }}>Action</th></tr>
                </thead>
                <tbody>
                  {shownUsers.map(u => (
                    <tr key={u.id}>
                      <td style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{u.full_name}</td>
                      <td>{u.email}</td>
                      <td><span className={`ui-chip ${u.role === 'admin' ? 'ok' : ''}`}>{u.role}</span></td>
                      <td style={{ textAlign: 'right' }}>
                        {u.id !== currentUser.id ? (
                          <button className="btn btn-ghost btn-sm" onClick={() => handleToggleRole(u.id, u.role)}>
                            Make {u.role === 'admin' ? 'user' : 'admin'}
                          </button>
                        ) : (
                          <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>You</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
