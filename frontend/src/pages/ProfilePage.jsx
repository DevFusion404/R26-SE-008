import { useState, useEffect } from 'react';
import UserService from '../services/userService';

export default function ProfilePage({ currentUser, isGuest, onLogout, onProfileUpdate }) {
  const [fullName, setFullName] = useState(currentUser?.full_name || '');
  const [email, setEmail] = useState(currentUser?.email || '');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [adminUsers, setAdminUsers] = useState([]);
  const [adminError, setAdminError] = useState('');

  // Fetch admin panel data if current user is admin
  useEffect(() => {
    if (currentUser?.role === 'admin') {
      fetchAdminUsers();
    }
  }, [currentUser]);

  const fetchAdminUsers = async () => {
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
  };

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
        setMessage('Profile updated successfully!');
        onProfileUpdate(result.profile);
      } else {
        setError(result.error);
      }
    } catch (err) {
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
    } catch (err) {
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
        // Refresh users list
        fetchAdminUsers();
      } else {
        alert(`Error: ${result.error}`);
      }
    } catch (err) {
      alert('Failed to change role.');
    }
  };

  if (isGuest) {
    return (
      <div className="page-container">
        <div className="page-header">
          <div className="page-header-left">
            <div className="page-header-icon" style={{ background: 'var(--accent-muted)', borderColor: 'var(--border-accent)' }}>
              ??
            </div>
            <div>
              <div className="page-title">Guest Profile</div>
              <div className="page-subtitle">Standard read-only sandbox mode</div>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '24px', marginTop: '20px' }}>
          <div style={{
            background: 'var(--bg-sidebar)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-md)',
            padding: '24px',
          }}>
            <h3 style={{ fontSize: 'var(--text-lg)', color: 'var(--text-primary)', marginBottom: '12px' }}>
              Account Type: Guest
            </h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)', lineHeight: 1.5, marginBottom: '20px' }}>
              You are currently logged in as a Guest. Guest accounts do not have profiles stored in Supabase, and cannot update details or list other users. 
              Register an account to save plans, manage permissions, or utilize admin features.
            </p>

            <button
              onClick={onLogout}
              style={{
                background: 'var(--accent)',
                color: '#000',
                border: 'none',
                borderRadius: 'var(--r-sm)',
                padding: '10px 18px',
                fontWeight: 700,
                fontSize: 'var(--text-sm)',
                cursor: 'pointer',
              }}
            >
              Sign In or Register
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container" style={{ overflowY: 'auto', maxHeight: 'calc(100vh - var(--topbar-height) - 40px)' }}>
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon" style={{ background: 'var(--accent-muted)', borderColor: 'var(--border-accent)' }}>
            ??
          </div>
          <div>
            <div className="page-title">User Account</div>
            <div className="page-subtitle">Manage your credentials and view server access level</div>
          </div>
        </div>
      </div>

      {/* Main Alert notifications */}
      {(error || message) && (
        <div style={{ marginTop: '16px' }}>
          {error && (
            <div className="alert alert-error" style={{ fontSize: 'var(--text-sm)', padding: '10px 14px', borderRadius: 'var(--r-sm)' }}>
              ?? {error}
            </div>
          )}
          {message && (
            <div className="alert alert-success" style={{ fontSize: 'var(--text-sm)', padding: '10px 14px', borderRadius: 'var(--r-sm)' }}>
              ? {message}
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: '24px', marginTop: '20px' }}>
        
        {/* Left Side: Profile Form */}
        <div style={{
          background: 'var(--bg-sidebar)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-md)',
          padding: '24px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
        }}>
          <div>
            <h3 style={{ fontSize: 'var(--text-lg)', color: 'var(--text-primary)', marginBottom: '18px' }}>
              Update Profile Details
            </h3>

            <form onSubmit={handleUpdate} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div>
                <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  Full Name
                </label>
                <input
                  type="text"
                  value={fullName}
                  onChange={e => setFullName(e.target.value)}
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
                <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  Email (Read-Only)
                </label>
                <input
                  type="text"
                  value={email}
                  disabled
                  style={{
                    width: '100%',
                    background: 'var(--bg-root)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--r-sm)',
                    color: 'var(--text-secondary)',
                    padding: '8px 12px',
                    fontSize: 'var(--text-sm)',
                    cursor: 'not-allowed',
                  }}
                />
              </div>

              <div>
                <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                  Role Permission
                </label>
                <div style={{
                  display: 'inline-block',
                  background: currentUser?.role === 'admin' ? 'rgba(16,185,129,0.15)' : 'rgba(59,130,246,0.15)',
                  border: `1px solid ${currentUser?.role === 'admin' ? 'rgba(16,185,129,0.3)' : 'rgba(59,130,246,0.3)'}`,
                  color: currentUser?.role === 'admin' ? 'var(--color-orchestrate)' : 'var(--color-low)',
                  fontSize: 'var(--text-xs)',
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  padding: '3px 10px',
                  borderRadius: 'var(--r-full)',
                }}>
                  {currentUser?.role || 'user'}
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                style={{
                  background: 'var(--accent)',
                  color: '#000',
                  border: 'none',
                  borderRadius: 'var(--r-sm)',
                  padding: '10px 14px',
                  fontWeight: 700,
                  fontSize: 'var(--text-sm)',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  marginTop: '10px',
                  opacity: loading ? 0.7 : 1,
                }}
              >
                {loading ? 'Saving…' : 'Save Changes'}
              </button>
            </form>
          </div>

          {/* Danger zone / Logout */}
          <div style={{ marginTop: '32px', paddingTop: '20px', borderTop: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', gap: '12px' }}>
              <button
                onClick={onLogout}
                style={{
                  flex: 1,
                  background: 'transparent',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--r-sm)',
                  color: 'var(--text-primary)',
                  padding: '10px 14px',
                  fontSize: 'var(--text-sm)',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Logout
              </button>

              <button
                onClick={() => setShowDeleteConfirm(true)}
                style={{
                  flex: 1,
                  background: 'rgba(239,68,68,0.1)',
                  border: '1px solid rgba(239,68,68,0.3)',
                  borderRadius: 'var(--r-sm)',
                  color: 'var(--color-critical)',
                  padding: '10px 14px',
                  fontSize: 'var(--text-sm)',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Delete Account
              </button>
            </div>

            {showDeleteConfirm && (
              <div style={{
                marginTop: '16px',
                padding: '12px',
                background: 'rgba(239,68,68,0.05)',
                border: '1px solid rgba(239,68,68,0.25)',
                borderRadius: 'var(--r-sm)',
              }}>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-primary)', marginBottom: '10px' }}>
                  Are you absolutely sure? This will delete your profile and account credentials permanently.
                </p>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    onClick={handleDeleteAccount}
                    disabled={loading}
                    style={{
                      background: 'var(--color-critical)',
                      border: 'none',
                      borderRadius: 'var(--r-xs)',
                      color: '#fff',
                      padding: '4px 10px',
                      fontSize: 'var(--text-xs)',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    Confirm Delete
                  </button>
                  <button
                    onClick={() => setShowDeleteConfirm(false)}
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--r-xs)',
                      color: 'var(--text-secondary)',
                      padding: '4px 10px',
                      fontSize: 'var(--text-xs)',
                      cursor: 'pointer',
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Side: Admin User Management Panel (Visible only to Admin Role) */}
        <div style={{
          background: 'var(--bg-sidebar)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--r-md)',
          padding: '24px',
        }}>
          {currentUser?.role !== 'admin' ? (
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              textAlign: 'center',
              color: 'var(--text-muted)',
              padding: '20px',
            }}>
              <span style={{ fontSize: '32px', marginBottom: '8px' }}>??</span>
              <h4 style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-md)', fontWeight: 600 }}>
                Admin Portal Restricted
              </h4>
              <p style={{ fontSize: 'var(--text-xs)', marginTop: '4px', maxWidth: '280px', lineHeight: 1.4 }}>
                Only accounts with an 'admin' privilege level can view or alter client authorization roles.
              </p>
            </div>
          ) : (
            <div>
              <h3 style={{ fontSize: 'var(--text-lg)', color: 'var(--text-primary)', marginBottom: '8px' }}>
                System Users Directory
              </h3>
              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                Promote or demote user permission levels across the RefactorIQ network.
              </p>

              {adminError ? (
                <div style={{ color: 'var(--color-critical)', fontSize: 'var(--text-sm)' }}>
                  {adminError}
                </div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                        <th style={{ padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Name</th>
                        <th style={{ padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Email</th>
                        <th style={{ padding: '8px', color: 'var(--text-secondary)', fontWeight: 500 }}>Role</th>
                        <th style={{ padding: '8px', color: 'var(--text-secondary)', fontWeight: 500, textAlign: 'right' }}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {adminUsers.map(u => (
                        <tr key={u.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.02)' }}>
                          <td style={{ padding: '8px 4px', color: 'var(--text-primary)' }}>{u.full_name}</td>
                          <td style={{ padding: '8px 4px', color: 'var(--text-secondary)', fontSize: 'var(--text-xs)' }}>{u.email}</td>
                          <td style={{ padding: '8px 4px' }}>
                            <span style={{
                              background: u.role === 'admin' ? 'rgba(16,185,129,0.1)' : 'rgba(59,130,246,0.1)',
                              color: u.role === 'admin' ? 'var(--color-orchestrate)' : 'var(--color-low)',
                              fontSize: '10px',
                              fontWeight: 700,
                              textTransform: 'uppercase',
                              padding: '2px 6px',
                              borderRadius: 'var(--r-xs)',
                            }}>
                              {u.role}
                            </span>
                          </td>
                          <td style={{ padding: '8px 4px', textAlign: 'right' }}>
                            {u.id !== currentUser.id ? (
                              <button
                                onClick={() => handleToggleRole(u.id, u.role)}
                                style={{
                                  background: 'transparent',
                                  border: '1px solid var(--border)',
                                  borderRadius: 'var(--r-xs)',
                                  color: 'var(--accent)',
                                  fontSize: '11px',
                                  padding: '2px 8px',
                                  cursor: 'pointer',
                                }}
                              >
                                Toggle Role
                              </button>
                            ) : (
                              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Self</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
