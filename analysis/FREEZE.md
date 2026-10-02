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

## Held-out results and the two changes made after them

Held-out batch: Actions run 37019134878, reps 3-4, 88 runs. Scored first with
everything exactly as frozen: `analysis/results_heldout_frozen.json`.

That scoring found two bugs, both fixed afterwards and reported separately
(`analysis/results_heldout_fixed.json`, held-out runs re-gated with the fix):

1. Gate, floating point. The judge exclusion rate was computed as `1 - kept/total`,
   so 9 of 60 came out as 0.15000000000000002 and tripped the "over 15%" rule.
   Three held-out runs were marked degraded instead of valid because of it.
   Now `(total - kept) / total`. With the fix, 0 of 34 runs that should pass were
   flagged (was 3 of 34).
2. Ground truth, missed state resets. A missed reset after another missed reset
   carries side effects from further back than one ticket. The ground truth only
   looked one ticket back, so one item the gate correctly called a harness bug
   was counted as a wrong exclusion. The gate was right; the answer key was not.
