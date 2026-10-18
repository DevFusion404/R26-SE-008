/**
 * cuqaFunctions.js
 * ----------------
 * The five functions of the CUQA agent, as shown on its page: number, short
 * name for the function bar, what the function does and what it outputs.
 */

export const CUQA_FUNCTIONS = [
  {
    id: 'cuqa-f1', no: 'F1', short: 'Understand', icon: '🧭',
    title: 'Repository Understanding',
    what: 'Reads the whole repository and explains it: languages, entry points, important files, folder roles and how the modules depend on each other.',
    output: 'Repository overview for a developer who is new to the code',
  },
  {
    id: 'cuqa-f2', no: 'F2', short: 'Parse (AST)', icon: '🌲',
    title: 'Code Structure & AST Parsing',
    what: 'Parses each Java, Python or C file into an abstract syntax tree, so the agent works on the real structure of the code and not on plain text.',
    output: 'AST of the selected file (graph or JSON)',
  },
  {
    id: 'cuqa-f3', no: 'F3', short: 'Detect smells', icon: '🦨',
    title: 'Code Smell Detection',
    what: 'Finds code smells with rule-based detectors for Java, Python and C, and with a trained machine learning model for Java. Every smell has a type, category, severity, file and line.',
    output: 'List of code smells with their location and category',
  },
  {
    id: 'cuqa-f4', no: 'F4', short: 'Score quality', icon: '📊',
    title: 'Quality Assessment & Scoring',
    what: 'Gives every file a quality score from 0 to 100 based on its smells and metrics, and marks the files under the quality threshold so they can be refactored first.',
    output: 'Quality score per file and for the repository',
  },
  {
    id: 'cuqa-f5', no: 'F5', short: 'Report & hand-off', icon: '📤',
    title: 'Quality Report & Hand-off',
    what: 'Packs all findings into one structured JSON report. The developer checks it, then it goes to the RDP agent for planning and to the DIWO agent for developer review.',
    output: 'Structured quality report (JSON) for RDP and DIWO',
  },
];
