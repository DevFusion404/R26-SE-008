/**
 * BeginnerGlossary.jsx
 * --------------------
 * Collapsible glossary of technical terms used throughout CUQA,
 * written in plain English for developers new to static analysis tools.
 */

import { useState } from 'react';

const GLOSSARY = [
  {
    term: 'AST (Abstract Syntax Tree)',
    definition:
      'A structured representation of source code. CUQA uses ASTs to understand functions, classes, statements and relationships without executing the code.',
  },
  {
    term: 'Entry Point',
    definition:
      'A location where application execution may begin. For example, a Python file containing if __name__ == \'__main__\', or a Java class with public static void main(String[] args).',
  },
  {
    term: 'Dependency',
    definition:
      'A relationship where one file or module uses (imports) another. If file A imports file B, then A depends on B.',
  },
  {
    term: 'Fan-In',
    definition:
      'The number of other files that depend on (import) a given file. High fan-in suggests the file is a shared or core module.',
  },
  {
    term: 'Fan-Out',
    definition:
      'The number of files a given file imports. High fan-out may indicate a complex orchestrator or a file with many responsibilities.',
  },
  {
    term: 'Code Smell',
    definition:
      'A code pattern that may make maintenance harder — for example, an excessively long function or duplicated code. It does not necessarily mean the program is incorrect.',
  },
  {
    term: 'Cyclomatic Complexity',
    definition:
      'Estimates how many different decision paths exist through the code. Higher values usually indicate code that is harder to understand and test.',
  },
  {
    term: 'LOC (Lines of Code)',
    definition:
      'Approximate size of the source code, measured by counting raw lines including comments and blanks.',
  },
  {
    term: 'Static Analysis',
    definition:
      'Examining source code without executing it. CUQA uses static analysis to detect patterns, measure complexity, and map dependencies.',
  },
  {
    term: 'Header File (.h)',
    definition:
      'A C/C++ file that typically contains declarations and interfaces used by multiple implementation files.',
  },
  {
    term: 'Polyglot Repository',
    definition:
      'A repository containing source files written in more than one programming language.',
  },
  {
    term: 'Monorepo',
    definition:
      'A single repository that contains multiple independent projects or modules, each with its own build configuration.',
  },
  {
    term: 'Confidence Level',
    definition:
      'How certain CUQA is about an inference. HIGH means strong static evidence. MEDIUM means partial evidence. LOW means a name-based guess only.',
  },
];

export default function BeginnerGlossary() {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  const filtered = search.trim()
    ? GLOSSARY.filter(
        g =>
          g.term.toLowerCase().includes(search.toLowerCase()) ||
          g.definition.toLowerCase().includes(search.toLowerCase())
      )
    : GLOSSARY;

  return (
    <div style={{
      borderRadius: 10,
      border: '1px solid var(--border)',
      overflow: 'hidden',
      background: 'var(--bg-card)',
    }}>
      {/* Header toggle */}
      <div
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '11px 16px',
          cursor: 'pointer',
          background: open ? 'rgba(0,212,232,0.06)' : 'transparent',
          transition: 'background 0.15s ease',
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 16 }}>📘</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', flex: 1 }}>
          Beginner Glossary
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          {open ? 'Click to collapse' : 'New to code analysis? Click to expand.'}
        </span>
        <span style={{
          fontSize: 10, color: 'var(--accent)',
          transform: open ? 'rotate(90deg)' : 'none',
          transition: 'transform 0.2s ease',
        }}>▸</span>
      </div>

      {/* Content */}
      {open && (
        <div style={{ borderTop: '1px solid var(--border)', animation: 'fadeIn 0.15s ease' }}>
          {/* Search */}
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
            <input
              type="text"
              placeholder="Search terms…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', boxSizing: 'border-box',
                background: 'var(--bg-base)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11,
                color: 'var(--text-primary)',
                outline: 'none',
              }}
            />
          </div>

          {/* Term list */}
          <div style={{ maxHeight: 300, overflowY: 'auto', padding: '8px 0' }}>
            {filtered.length === 0 ? (
              <div style={{ padding: '12px 16px', color: 'var(--text-muted)', fontSize: 11, textAlign: 'center' }}>
                No terms matching "{search}".
              </div>
            ) : (
              filtered.map((item, i) => (
                <div key={i} style={{
                  padding: '8px 16px',
                  borderBottom: i < filtered.length - 1 ? '1px solid var(--border)' : 'none',
                }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 3 }}>
                    {item.term}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {item.definition}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
