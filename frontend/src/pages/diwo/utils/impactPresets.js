/**
 * Optimiser presets
 * =================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Mirrors backend/domain/selection_optimizer.py::PRESETS so the buttons can be
 * labelled before the first request. The backend remains authoritative about
 * what each preset actually does — these are captions, not behaviour.
 *
 * Kept out of TradeOffPanel.jsx so that file exports only a component and
 * keeps its fast-refresh boundary.
 */

export const PRESETS = [
  {
    key: "best_value",
    label: "Best value",
    description: "Most quality points inside the time budget.",
  },
  {
    key: "safe_wins",
    label: "Safe wins",
    description: "Low-risk fixes only — nothing that needs careful review.",
  },
  {
    key: "stop_bleeding",
    label: "Stop the bleeding",
    description: "Targets debt in the files the team edits most.",
  },
];

export const DEFAULT_BUDGET_MINUTES = 60;
