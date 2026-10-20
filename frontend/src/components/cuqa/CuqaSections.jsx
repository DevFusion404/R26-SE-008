/**
 * CuqaSections.jsx
 * ----------------
 * The five functions of the CUQA agent, the sticky bar that jumps between
 * them, and the section frame each one is shown in. Every section states what
 * the function does and what it outputs, so the page reads as a list of the
 * agent's capabilities rather than one long dashboard.
 */

import { useEffect, useRef, useState } from 'react';
import { CUQA_FUNCTIONS } from './cuqaFunctions.js';

/** Sticky F1–F5 bar; highlights the section currently on screen. */
export function CuqaFunctionNav() {
  const [active, setActive] = useState(CUQA_FUNCTIONS[0].id);
  const barRef = useRef(null);

  // On a narrow screen the bar scrolls sideways; keep the active chip in view.
  useEffect(() => {
    const bar = barRef.current;
    const chip = bar?.querySelector('.cuqa-fn.active');
    if (bar && chip && bar.scrollWidth > bar.clientWidth) {
      bar.scrollTo({ left: chip.offsetLeft - 8, behavior: 'smooth' });
    }
  }, [active]);

  useEffect(() => {
    const sections = CUQA_FUNCTIONS
      .map((f) => document.getElementById(f.id))
      .filter(Boolean);
    if (sections.length === 0 || typeof IntersectionObserver === 'undefined') return undefined;

    const visible = new Set();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => (e.isIntersecting ? visible.add(e.target.id) : visible.delete(e.target.id)));
        const first = CUQA_FUNCTIONS.find((f) => visible.has(f.id));
        if (first) setActive(first.id);
      },
      {
        root: document.querySelector('.main-content'),
        rootMargin: '-90px 0px -55% 0px',
      },
    );
    sections.forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, []);

  function jump(id) {
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  return (
    <nav ref={barRef} className="cuqa-fnbar" aria-label="CUQA functions">
      {CUQA_FUNCTIONS.map((f) => (
        <button
          key={f.id}
          type="button"
          className={`cuqa-fn ${active === f.id ? 'active' : ''}`}
          onClick={() => jump(f.id)}
          title={f.title}
        >
          <span className="fn-no">{f.no}</span>
          <span>{f.short}</span>
        </button>
      ))}
    </nav>
  );
}

/** Frame for one CUQA function: number, name, what it does, what it outputs. */
export function CuqaSection({ fn, tools, children }) {
  const def = CUQA_FUNCTIONS.find((f) => f.no === fn);
  if (!def) return children;

  return (
    <section id={def.id} className="cuqa-section" aria-labelledby={`${def.id}-title`}>
      <div className="cuqa-section-head">
        <div className="cuqa-section-no" aria-hidden="true">{def.no}</div>
        <div className="cuqa-section-text">
          <div className="cuqa-section-kicker">CUQA · Function {def.no.slice(1)}</div>
          <h2 id={`${def.id}-title`} className="cuqa-section-title">{def.icon} {def.title}</h2>
          <p className="cuqa-section-what">{def.what}</p>
          {tools && <div className="cuqa-section-tools" style={{ marginTop: 10 }}>{tools}</div>}
        </div>
        <div className="cuqa-section-output">
          <span className="k">Output</span>
          <span className="v">{def.output}</span>
        </div>
      </div>
      {children}
    </section>
  );
}
