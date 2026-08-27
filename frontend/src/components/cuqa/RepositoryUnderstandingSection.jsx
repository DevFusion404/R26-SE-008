/**
 * RepositoryUnderstandingSection.jsx
 * ----------------------------------
 * Main orchestrator component for the Repository Understanding / Newcomer Guide layer.
 * Placed at the top of CUQAAgentPage.jsx.
 *
 * Includes:
 * - Beginner View toggle [ON/OFF]
 * - Search / Filter bar
 * - Repository at a Glance summary cards
 * - Language breakdown & Technology badges
 * - Entry point panel
 * - Recommended learning path
 * - Key files to know & Important directories
 * - Static module dependency graph
 * - Architectural pattern clues
 * - Beginner glossary
 */

import { useState, useEffect } from 'react';
import CUQAAgentService from '../../services/cuqaAgentService';
import RepositoryAtAGlance from './RepositoryAtAGlance';
import LanguageBreakdown from './LanguageBreakdown';
import TechnologyBadges from './TechnologyBadges';
import EntryPointPanel from './EntryPointPanel';
import ImportantFilesPanel from './ImportantFilesPanel';
import DirectoryRoleExplorer from './DirectoryRoleExplorer';
import RecommendedReadingPath from './RecommendedReadingPath';
import DependencyOverview from './DependencyOverview';
import ArchitecturalClues from './ArchitecturalClues';
import BeginnerGlossary from './BeginnerGlossary';

export default function RepositoryUnderstandingSection({ repoLoaded, onFileSelect }) {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [beginnerMode, setBeginnerMode] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'reading_path' | 'dependencies' | 'structure'

  useEffect(() => {
    if (repoLoaded) {
      fetchOverview();
    }
  }, [repoLoaded]);

  async function fetchOverview() {
    setLoading(true);
    setError(null);
    try {
      const data = await CUQAAgentService.getRepositoryOverview();
      setOverview(data);
    } catch (err) {
      setError(err.message || 'Failed to analyze repository structure.');
    } finally {
      setLoading(false);
    }
  }

  if (!repoLoaded) return null;

  if (loading) {
    return (
      <div className="card" style={{ padding: 32, marginBottom: 24 }}>
        <div className="loading-state">
          <div className="spinner" />
          <span style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 12 }}>
            Understanding repository structure…
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
            Detecting entry points, mapping local dependencies, and inferring directory roles…
          </span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ padding: 20, marginBottom: 24, borderLeft: '4px solid #ef4444' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#ef4444', marginBottom: 4 }}>
              ⚠ Repository Understanding Warning
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{error}</div>
          </div>
          <button className="btn btn-sm btn-outline" onClick={fetchOverview}>
            ⟳ Retry Analysis
          </button>
        </div>
      </div>
    );
  }

  if (!overview) return null;

  const {
    repository = {},
    language_breakdown = [],
    project_artifacts = {},
    entry_points = [],
    important_directories = [],
    important_files = [],
    recommended_reading_path = [],
    technologies = [],
    dependency_graph = {},
    dependency_summary = {},
    architectural_clues = [],
    subprojects = [],
    analysis_notes = [],
  } = overview;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, marginBottom: 28 }}>
      {/* Section Header & Beginner Toggle */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '14px 20px',
        background: 'linear-gradient(135deg, rgba(0,212,232,0.08) 0%, rgba(59,130,246,0.04) 100%)',
        border: '1px solid rgba(0,212,232,0.25)',
        borderRadius: 12,
        flexWrap: 'wrap', gap: 12,
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18 }}>🧭</span>
            <span style={{ fontSize: 15, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '0.3px' }}>
              Repository Understanding &amp; Newcomer Guide
            </span>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 9999,
              background: 'rgba(0,212,232,0.15)', color: '#00d4e8',
              border: '1px solid rgba(0,212,232,0.3)', fontWeight: 700,
            }}>
              Static Evidence Model
            </span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3 }}>
            Orientation guide for developers unfamiliar with this legacy repository.
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {/* Search Box */}
          <input
            type="text"
            placeholder="🔍 Search files, roles, dirs…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{
              background: 'var(--bg-base)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              padding: '6px 12px',
              fontSize: 11,
              color: 'var(--text-primary)',
              width: 180,
              outline: 'none',
            }}
          />

          {/* Beginner Mode Toggle */}
          <label style={{
            display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
            userSelect: 'none', fontSize: 11, fontWeight: 600, color: 'var(--text-primary)',
          }}>
            <span>Beginner View</span>
            <div
              onClick={() => setBeginnerMode(v => !v)}
              style={{
                width: 36, height: 20, borderRadius: 10,
                background: beginnerMode ? '#00d4e8' : 'var(--bg-hover)',
                border: '1px solid var(--border)',
                position: 'relative', transition: 'background 0.2s ease',
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: '50%', background: '#fff',
                position: 'absolute', top: 1, left: beginnerMode ? 17 : 1,
                transition: 'left 0.2s ease',
                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
              }} />
            </div>
          </label>
        </div>
      </div>

      {/* Analysis Notes / Warnings if any */}
      {analysis_notes.length > 0 && (
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)',
          fontSize: 11, color: '#f59e0b', display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          {analysis_notes.map((note, idx) => (
            <div key={idx} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
              <span>ℹ</span>
              <span>{note}</span>
            </div>
          ))}
        </div>
      )}

      {/* Top Level Stat Cards */}
      <RepositoryAtAGlance
        repository={repository}
        languageBreakdown={language_breakdown}
        entryPoints={entry_points}
        dependencySummary={dependency_summary}
      />

      {/* Tab Navigation */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', gap: 4 }}>
        {[
          { id: 'overview', label: '📊 Overview & Entry Points' },
          { id: 'reading_path', label: '🚀 Recommended Reading Path' },
          { id: 'dependencies', label: '🕸 Static Dependency View' },
          { id: 'structure', label: '📁 Folder & File Roles' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '9px 16px',
              border: 'none',
              borderBottom: activeTab === tab.id ? '2px solid #00d4e8' : '2px solid transparent',
              background: activeTab === tab.id ? 'rgba(0,212,232,0.08)' : 'transparent',
              color: activeTab === tab.id ? '#00d4e8' : 'var(--text-secondary)',
              fontSize: 12,
              fontWeight: activeTab === tab.id ? 700 : 500,
              cursor: 'pointer',
              borderRadius: '6px 6px 0 0',
              transition: 'all 0.15s ease',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab 1: Overview & Entry Points */}
      {activeTab === 'overview' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
          {/* Entry Points & Architectural Clues */}
          <div className="card" style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="card-header" style={{ padding: 0, marginBottom: 8, border: 'none' }}>
              <span className="card-title">🚀 Likely Entry Points</span>
            </div>
            <EntryPointPanel entryPoints={entry_points} onFileSelect={onFileSelect} />

            {architectural_clues.length > 0 && (
              <>
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10, color: 'var(--text-primary)' }}>
                    🏛 Architectural Clues
                  </div>
                  <ArchitecturalClues clues={architectural_clues} />
                </div>
              </>
            )}
          </div>

          {/* Languages & Technologies */}
          <div className="card" style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="card-header" style={{ padding: 0, marginBottom: 8, border: 'none' }}>
              <span className="card-title"> Language &amp; Tooling Overview</span>
            </div>

            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase' }}>
                Language Breakdown
              </div>
              <LanguageBreakdown languages={language_breakdown} />
            </div>

            {technologies.length > 0 && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase' }}>
                  Detected Technologies &amp; Tooling
                </div>
                <TechnologyBadges technologies={technologies} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Recommended Reading Path */}
      {activeTab === 'reading_path' && (
        <div className="card" style={{ padding: 20 }}>
          <div className="card-header" style={{ padding: 0, marginBottom: 16, border: 'none' }}>
            <span className="card-title">📍 Recommended Newcomer Learning Path</span>
          </div>
          <RecommendedReadingPath steps={recommended_reading_path} />
        </div>
      )}

      {/* Tab 3: Static Dependency View */}
      {activeTab === 'dependencies' && (
        <div className="card" style={{ padding: 20 }}>
          <div className="card-header" style={{ padding: 0, marginBottom: 16, border: 'none' }}>
            <span className="card-title">🕸 Static Module Dependency View</span>
          </div>
          <DependencyOverview graph={dependency_graph} summary={dependency_summary} />
        </div>
      )}

      {/* Tab 4: Folder & File Roles */}
      {activeTab === 'structure' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16 }}>
          {/* Key Files to Know */}
          <div className="card" style={{ padding: 18 }}>
            <div className="card-header" style={{ padding: 0, marginBottom: 12, border: 'none' }}>
              <span className="card-title">⭐ Key Files to Know</span>
            </div>
            <ImportantFilesPanel files={important_files} onFileSelect={onFileSelect} searchQuery={searchQuery} />
          </div>

          {/* Important Directories */}
          <div className="card" style={{ padding: 18 }}>
            <div className="card-header" style={{ padding: 0, marginBottom: 12, border: 'none' }}>
              <span className="card-title">📁 Directory Role Explorer</span>
            </div>
            <DirectoryRoleExplorer directories={important_directories} searchQuery={searchQuery} />
          </div>
        </div>
      )}

      {/* Beginner Glossary (Collapsible Footer) */}
      {beginnerMode && <BeginnerGlossary />}
    </div>
  );
}
