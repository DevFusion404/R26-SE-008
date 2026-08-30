import { useEffect, useRef, useState } from 'react';
import UserService from '../services/userService';

// ============================================================================
// WebGL Smokey Shader Background Component (Persistent Animation Loop)
// ============================================================================

const vertexSmokeySource = `
  attribute vec4 a_position;
  void main() {
    gl_Position = a_position;
  }
`;

const fragmentSmokeySource = `
precision mediump float;

uniform vec2 iResolution;
uniform float iTime;
uniform vec2 iMouse;
uniform vec3 u_color;

void mainImage(out vec4 fragColor, in vec2 fragCoord){
    vec2 uv = fragCoord / iResolution;
    vec2 centeredUV = (2.0 * fragCoord - iResolution.xy) / min(iResolution.x, iResolution.y);

    float time = iTime * 0.35;

    // Normalize mouse input
    vec2 mouse = iMouse / iResolution;
    vec2 rippleCenter = 2.0 * mouse - 1.0;

    vec2 distortion = centeredUV;
    // Distortion for wavy, smokey nebula effect
    for (float i = 1.0; i < 7.0; i++) {
        distortion.x += 0.4 / i * cos(i * 2.0 * distortion.y + time + rippleCenter.x * 2.0);
        distortion.y += 0.4 / i * cos(i * 2.0 * distortion.y + time + rippleCenter.y * 2.0);
    }

    // Create glowing wave pattern
    float wave = abs(sin(distortion.x + distortion.y + time));
    float glow = smoothstep(0.92, 0.18, wave);

    // Deep luminous palette
    vec3 baseColor = u_color * glow;
    vec3 ambient = vec3(0.03, 0.05, 0.10);
    fragColor = vec4(baseColor + ambient, 1.0);
}

void main() {
    mainImage(gl_FragColor, gl_FragCoord.xy);
}
`;

function SmokeyBackground({ color = "#3B82F6" }) {
  const canvasRef = useRef(null);
  const mouseRef = useRef({ x: 0, y: 0, targetX: 0, targetY: 0, isHovering: false });

  const hexToRgb = (hex) => {
    const cleanHex = hex.replace('#', '');
    const r = parseInt(cleanHex.substring(0, 2), 16) / 255;
    const g = parseInt(cleanHex.substring(2, 4), 16) / 255;
    const b = parseInt(cleanHex.substring(4, 6), 16) / 255;
    return [r, g, b];
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const gl = canvas.getContext("webgl");
    if (!gl) return;

    const compileShader = (type, source) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vertexShader = compileShader(gl.VERTEX_SHADER, vertexSmokeySource);
    const fragmentShader = compileShader(gl.FRAGMENT_SHADER, fragmentSmokeySource);
    if (!vertexShader || !fragmentShader) return;

    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vertexShader);
    gl.attachShader(program, fragmentShader);
    gl.linkProgram(program);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;

    gl.useProgram(program);

    const positionBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);

    const positionLocation = gl.getAttribLocation(program, "a_position");
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

    const iResolutionLocation = gl.getUniformLocation(program, "iResolution");
    const iTimeLocation = gl.getUniformLocation(program, "iTime");
    const iMouseLocation = gl.getUniformLocation(program, "iMouse");
    const uColorLocation = gl.getUniformLocation(program, "u_color");

    const [r, g, b] = hexToRgb(color);
    gl.uniform3f(uColorLocation, r, g, b);

    let animationFrameId;
    const startTime = performance.now();

    // Initial center coordinates
    mouseRef.current.x = window.innerWidth / 2;
    mouseRef.current.y = window.innerHeight / 2;
    mouseRef.current.targetX = window.innerWidth / 2;
    mouseRef.current.targetY = window.innerHeight / 2;

    const render = (now) => {
      const width = canvas.clientWidth || window.innerWidth;
      const height = canvas.clientHeight || window.innerHeight;
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.viewport(0, 0, width, height);

      const currentTime = (now - startTime) / 1000;

      // Smooth mouse interpolation (lerp)
      const m = mouseRef.current;
      m.x += (m.targetX - m.x) * 0.06;
      m.y += (m.targetY - m.y) * 0.06;

      gl.uniform2f(iResolutionLocation, width, height);
      gl.uniform1f(iTimeLocation, currentTime);
      gl.uniform2f(iMouseLocation, m.x, height - m.y);

      gl.drawArrays(gl.TRIANGLES, 0, 6);
      animationFrameId = requestAnimationFrame(render);
    };

    const handleMouseMove = (event) => {
      mouseRef.current.targetX = event.clientX;
      mouseRef.current.targetY = event.clientY;
      mouseRef.current.isHovering = true;
    };

    const handleMouseLeave = () => {
      mouseRef.current.targetX = window.innerWidth / 2;
      mouseRef.current.targetY = window.innerHeight / 2;
      mouseRef.current.isHovering = false;
    };

    window.addEventListener("mousemove", handleMouseMove, { passive: true });
    document.addEventListener("mouseleave", handleMouseLeave);

    animationFrameId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseleave", handleMouseLeave);
      gl.deleteProgram(program);
      gl.deleteShader(vertexShader);
      gl.deleteShader(fragmentShader);
      gl.deleteBuffer(positionBuffer);
    };
  }, [color]);

  return (
    <div style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'hidden', zIndex: 0 }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
      <div style={{ position: 'absolute', inset: 0, backdropFilter: 'blur(28px)', WebkitBackdropFilter: 'blur(28px)', background: 'rgba(7, 11, 20, 0.45)' }} />
    </div>
  );
}

// ============================================================================
// Lightweight Inline Icons
// ============================================================================

function UserIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function LockIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function MailIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="20" height="16" x="2" y="4" rx="2" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
    </svg>
  );
}

function ShieldIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
    </svg>
  );
}

function ArrowRightIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

function CheckIcon({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

// ============================================================================
// Main AuthPage Component
// ============================================================================

export default function AuthPage({ onAuthSuccess, onGuestLogin }) {
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const resetForm = () => {
    setEmail('');
    setPassword('');
    setFullName('');
    setError('');
    setMessage('');
  };

  const handleToggleMode = () => {
    setIsRegister(!isRegister);
    resetForm();
  };

  const validateInputs = () => {
    if (!email || !password) {
      setError('Please fill in both email and password.');
      return false;
    }
    if (isRegister) {
      if (!fullName || fullName.trim().length < 2) {
        setError('Full name is required (at least 2 characters).');
        return false;
      }
      if (password.length < 8) {
        setError('Password must be at least 8 characters long.');
        return false;
      }
      if (!/[A-Z]/.test(password)) {
        setError('Password must contain at least one uppercase letter (A-Z).');
        return false;
      }
      if (!/\d/.test(password)) {
        setError('Password must contain at least one number (0-9).');
        return false;
      }
    }
    return true;
  };

  const hasMinLength = password.length >= 8;
  const hasUpper = /[A-Z]/.test(password);
  const hasNumber = /\d/.test(password);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');

    if (!validateInputs()) return;

    setLoading(true);
    try {
      if (isRegister) {
        // Automatically register as 'user' role
        const result = await UserService.register(email, password, fullName, 'user');
        if (!result.success) {
          setError(result.error || 'Registration failed. Please check your information.');
        } else {
          setMessage('Registration successful! You can now sign in.');
          setIsRegister(false);
          setPassword('');
        }
      } else {
        const result = await UserService.login(email, password);
        if (!result.success) {
          setError(result.error || 'Invalid email or password.');
        } else {
          onAuthSuccess(result.user);
        }
      }
    } catch (err) {
      setError('Connection error. Please verify the backend service is running.');
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
      position: 'relative',
      minHeight: '100vh',
      width: '100%',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '20px 16px',
      overflow: 'hidden',
      fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      backgroundColor: '#070b14',
    }}>
      {/* Interactive WebGL Smokey Background */}
      <SmokeyBackground color="#3B82F6" />

      {/* Glassmorphism Auth Card */}
      <div style={{
        position: 'relative',
        zIndex: 10,
        width: '100%',
        maxWidth: isRegister ? '720px' : '420px',
        background: 'rgba(15, 23, 42, 0.70)',
        backdropFilter: 'blur(24px)',
        WebkitBackdropFilter: 'blur(24px)',
        border: '1px solid rgba(255, 255, 255, 0.12)',
        borderRadius: '24px',
        padding: isRegister ? '32px 34px' : '36px 30px',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.6), 0 0 35px rgba(59, 130, 246, 0.15)',
        color: '#FFFFFF',
        transition: 'max-width 0.3s cubic-bezier(0.4, 0, 0.2, 1), padding 0.3s ease',
      }}>
        {/* Brand Header */}
        <div style={{ textAlign: 'center', marginBottom: isRegister ? '16px' : '24px' }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '42px',
            height: '42px',
            borderRadius: '12px',
            background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.85), rgba(99, 102, 241, 0.85))',
            boxShadow: '0 8px 16px -4px rgba(59, 130, 246, 0.5)',
            marginBottom: '8px',
          }}>
            <ShieldIcon size={22} />
          </div>
          <h1 style={{
            fontSize: '24px',
            fontWeight: 800,
            letterSpacing: '-0.5px',
            margin: 0,
            background: 'linear-gradient(to right, #FFFFFF, #93C5FD)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}>
            RefactorIQ
          </h1>
          <p style={{
            fontSize: '11px',
            color: '#94A3B8',
            marginTop: '2px',
            textTransform: 'uppercase',
            letterSpacing: '1.5px',
            fontWeight: 600,
          }}>
            Automated Refactoring Platform
          </p>
        </div>

        {/* Title & Subtitle */}
        <div style={{ textAlign: 'center', marginBottom: isRegister ? '16px' : '20px' }}>
          <h2 style={{ fontSize: '19px', fontWeight: 700, margin: 0, color: '#F8FAFC' }}>
            {isRegister ? 'Create Your Account' : 'Welcome Back'}
          </h2>
          <p style={{ fontSize: '13px', color: '#94A3B8', marginTop: '4px' }}>
            {isRegister ? 'Enter your details below to setup your profile' : 'Sign in to access your quality reports and refactoring plans'}
          </p>
        </div>

        {/* Feedback Alerts */}
        {error && (
          <div style={{
            background: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            color: '#FCA5A5',
            borderRadius: '12px',
            padding: '10px 14px',
            fontSize: '13px',
            marginBottom: '16px',
            lineHeight: 1.4,
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}>
            <span style={{ fontSize: '15px' }}>⚠️</span>
            <span>{error}</span>
          </div>
        )}

        {message && (
          <div style={{
            background: 'rgba(34, 197, 94, 0.15)',
            border: '1px solid rgba(34, 197, 94, 0.4)',
            color: '#86EFAC',
            borderRadius: '12px',
            padding: '10px 14px',
            fontSize: '13px',
            marginBottom: '16px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}>
            <span style={{ fontSize: '15px' }}>✓</span>
            <span>{message}</span>
          </div>
        )}

        {/* Main Auth Form */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {isRegister ? (
            /* 2-Column Landscape Layout for Registration */
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: '16px 24px',
            }}>
              {/* Left Column: Full Name & Email Address */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div>
                  <label style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '12px',
                    color: '#CBD5E1',
                    marginBottom: '5px',
                    fontWeight: 500,
                  }}>
                    <UserIcon size={14} />
                    <span>Full Name</span>
                  </label>
                  <input
                    type="text"
                    placeholder="Enter your full name"
                    value={fullName}
                    onChange={e => setFullName(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      background: 'rgba(255, 255, 255, 0.06)',
                      border: '1px solid rgba(255, 255, 255, 0.15)',
                      borderRadius: '10px',
                      color: '#FFFFFF',
                      padding: '10px 12px',
                      fontSize: '13px',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                    onFocus={e => {
                      e.target.style.borderColor = '#3B82F6';
                      e.target.style.boxShadow = '0 0 0 3px rgba(59, 130, 246, 0.25)';
                    }}
                    onBlur={e => {
                      e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)';
                      e.target.style.boxShadow = 'none';
                    }}
                  />
                </div>

                <div>
                  <label style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '12px',
                    color: '#CBD5E1',
                    marginBottom: '5px',
                    fontWeight: 500,
                  }}>
                    <MailIcon size={14} />
                    <span>Email Address</span>
                  </label>
                  <input
                    type="email"
                    placeholder="Enter your email address"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      background: 'rgba(255, 255, 255, 0.06)',
                      border: '1px solid rgba(255, 255, 255, 0.15)',
                      borderRadius: '10px',
                      color: '#FFFFFF',
                      padding: '10px 12px',
                      fontSize: '13px',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                    onFocus={e => {
                      e.target.style.borderColor = '#3B82F6';
                      e.target.style.boxShadow = '0 0 0 3px rgba(59, 130, 246, 0.25)';
                    }}
                    onBlur={e => {
                      e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)';
                      e.target.style.boxShadow = 'none';
                    }}
                  />
                </div>
              </div>

              {/* Right Column: Password + Live Requirements Card */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div>
                  <label style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '12px',
                    color: '#CBD5E1',
                    marginBottom: '5px',
                    fontWeight: 500,
                  }}>
                    <LockIcon size={14} />
                    <span>Password</span>
                  </label>
                  <input
                    type="password"
                    placeholder="Enter your password"
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      background: 'rgba(255, 255, 255, 0.06)',
                      border: '1px solid rgba(255, 255, 255, 0.15)',
                      borderRadius: '10px',
                      color: '#FFFFFF',
                      padding: '10px 12px',
                      fontSize: '13px',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                    onFocus={e => {
                      e.target.style.borderColor = '#3B82F6';
                      e.target.style.boxShadow = '0 0 0 3px rgba(59, 130, 246, 0.25)';
                    }}
                    onBlur={e => {
                      e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)';
                      e.target.style.boxShadow = 'none';
                    }}
                  />
                </div>

                {/* Password Requirements Guide */}
                <div style={{
                  padding: '10px 12px',
                  background: 'rgba(0, 0, 0, 0.28)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  borderRadius: '10px',
                  fontSize: '12px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '5px',
                }}>
                  <div style={{ fontWeight: 600, color: '#94A3B8', marginBottom: '2px' }}>
                    Password Requirements:
                  </div>
                  <div style={{
                    color: hasMinLength ? '#4ADE80' : '#64748B',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    transition: 'color 0.2s',
                  }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                      {hasMinLength ? <CheckIcon size={13} /> : '○'}
                    </span>
                    <span>At least 8 characters</span>
                  </div>
                  <div style={{
                    color: hasUpper ? '#4ADE80' : '#64748B',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    transition: 'color 0.2s',
                  }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                      {hasUpper ? <CheckIcon size={13} /> : '○'}
                    </span>
                    <span>At least one uppercase letter (A-Z)</span>
                  </div>
                  <div style={{
                    color: hasNumber ? '#4ADE80' : '#64748B',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    transition: 'color 0.2s',
                  }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                      {hasNumber ? <CheckIcon size={13} /> : '○'}
                    </span>
                    <span>At least one number (0-9)</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            /* Single-Column Focused Layout for Sign In */
            <>
              <div>
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '12px',
                  color: '#CBD5E1',
                  marginBottom: '5px',
                  fontWeight: 500,
                }}>
                  <MailIcon size={14} />
                  <span>Email Address</span>
                </label>
                <input
                  type="email"
                  placeholder="Enter your email address"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  style={{
                    width: '100%',
                    background: 'rgba(255, 255, 255, 0.06)',
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    borderRadius: '10px',
                    color: '#FFFFFF',
                    padding: '11px 13px',
                    fontSize: '14px',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                  onFocus={e => {
                    e.target.style.borderColor = '#3B82F6';
                    e.target.style.boxShadow = '0 0 0 3px rgba(59, 130, 246, 0.25)';
                  }}
                  onBlur={e => {
                    e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)';
                    e.target.style.boxShadow = 'none';
                  }}
                />
              </div>

              <div>
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '12px',
                  color: '#CBD5E1',
                  marginBottom: '5px',
                  fontWeight: 500,
                }}>
                  <LockIcon size={14} />
                  <span>Password</span>
                </label>
                <input
                  type="password"
                  placeholder="Enter your password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  style={{
                    width: '100%',
                    background: 'rgba(255, 255, 255, 0.06)',
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    borderRadius: '10px',
                    color: '#FFFFFF',
                    padding: '11px 13px',
                    fontSize: '14px',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                  onFocus={e => {
                    e.target.style.borderColor = '#3B82F6';
                    e.target.style.boxShadow = '0 0 0 3px rgba(59, 130, 246, 0.25)';
                  }}
                  onBlur={e => {
                    e.target.style.borderColor = 'rgba(255, 255, 255, 0.15)';
                    e.target.style.boxShadow = 'none';
                  }}
                />
              </div>
            </>
          )}

          {/* Primary Action Button */}
          <button
            type="submit"
            disabled={loading}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              width: '100%',
              background: 'linear-gradient(135deg, #2563EB 0%, #3B82F6 100%)',
              border: 'none',
              borderRadius: '12px',
              color: '#FFFFFF',
              fontWeight: 600,
              padding: '12px 18px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: '14px',
              marginTop: '4px',
              boxShadow: '0 8px 20px -4px rgba(37, 99, 235, 0.5)',
              transition: 'transform 0.15s ease, box-shadow 0.15s ease, opacity 0.2s',
              opacity: loading ? 0.7 : 1,
            }}
            onMouseOver={e => {
              if (!loading) {
                e.currentTarget.style.transform = 'translateY(-1px)';
                e.currentTarget.style.boxShadow = '0 12px 24px -4px rgba(37, 99, 235, 0.6)';
              }
            }}
            onMouseOut={e => {
              if (!loading) {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 8px 20px -4px rgba(37, 99, 235, 0.5)';
              }
            }}
          >
            <span>{loading ? 'Processing...' : isRegister ? 'Create Account' : 'Sign In'}</span>
            {!loading && <ArrowRightIcon size={16} />}
          </button>
        </form>

        {/* Divider */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          textAlign: 'center',
          margin: '18px 0 14px',
          color: '#64748B',
          fontSize: '11px',
          fontWeight: 600,
          letterSpacing: '0.5px',
        }}>
          <div style={{ flex: 1, height: '1px', background: 'rgba(255, 255, 255, 0.1)' }} />
          <span style={{ padding: '0 12px' }}>OR CONTINUE WITH</span>
          <div style={{ flex: 1, height: '1px', background: 'rgba(255, 255, 255, 0.1)' }} />
        </div>

        {/* Guest Login Option */}
        <button
          onClick={handleGuest}
          style={{
            width: '100%',
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid rgba(255, 255, 255, 0.15)',
            borderRadius: '12px',
            color: '#E2E8F0',
            fontWeight: 500,
            padding: '10px 16px',
            cursor: 'pointer',
            fontSize: '13px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'background 0.2s, border-color 0.2s',
          }}
          onMouseOver={e => {
            e.currentTarget.style.background = 'rgba(255, 255, 255, 0.1)';
            e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.3)';
          }}
          onMouseOut={e => {
            e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)';
            e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.15)';
          }}
        >
          <span>🚀</span>
          <span>Explore as Guest</span>
        </button>

        {/* Mode Switcher */}
        <p style={{
          fontSize: '13px',
          color: '#94A3B8',
          marginTop: '18px',
          textAlign: 'center',
          marginBottom: 0,
        }}>
          {isRegister ? 'Already have an account?' : "Don't have an account?"}{' '}
          <button
            onClick={handleToggleMode}
            style={{
              background: 'none',
              border: 'none',
              color: '#60A5FA',
              cursor: 'pointer',
              fontWeight: 600,
              padding: 0,
              fontSize: '13px',
              textDecoration: 'underline',
              marginLeft: '4px',
            }}
          >
            {isRegister ? 'Sign In Here' : 'Create One Here'}
          </button>
        </p>
      </div>
    </div>
  );
}
