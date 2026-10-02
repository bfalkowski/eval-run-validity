# Frozen before held-out runs

Frozen on 2026-10-02 after the development batch (Actions run 37014574123, reps 1-2,
88 runs on dataset v2 against the real Jev API). Nothing below changes before the
held-out batch (reps 3-4, different fault seeds) is scored.

- Attribution rules: `validity/attribution.py` at this commit.
- Gate thresholds: `validity/gate_config.json` (2% / 15% excluded, 90% completed).
- Ground-truth definitions: `analysis/analyze.py` at this commit. One change was
  made while looking at dev results: a stale fixture only counts as a defect when
  the changed order no longer fits the expected answer (some status changes leave
  the right action the same). That change is to the ground truth, not the gate.

Development results: `analysis/results_dev.json`.
