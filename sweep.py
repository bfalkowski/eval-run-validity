"""Run every fault plan once (plus clean runs) and print the gate's verdicts.

  JEV_BACKEND=stub POLICY_VERSION=v1 python sweep.py --prefix stub
"""
import argparse
import glob
import json
import os
import subprocess
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--prefix", default="sweep")
ap.add_argument("--clean", type=int, default=2)
ap.add_argument("--plans", default="plans/*.json")
ap.add_argument("--error-policy", default="loud")
a = ap.parse_args()

jobs = [(f"{a.prefix}-clean-{i:02d}", None) for i in range(a.clean)]
jobs += [(f"{a.prefix}-control-context_strip", "control")]
jobs += [(f"{a.prefix}-{os.path.basename(p)[:-5]}", p) for p in sorted(glob.glob(a.plans))]
rows = []
for run_id, plan in jobs:
    cmd = [sys.executable, "run.py", "--run-id", run_id, "--error-policy", a.error_policy]
    if plan == "control":
        cmd += ["--variant", "context_strip"]
    elif plan:
        cmd += ["--fault-plan", plan]
    subprocess.run(cmd, capture_output=True, text=True)
    vpath = f"runs/{run_id}/verdict.json"
    v = json.load(open(vpath)) if os.path.exists(vpath) else {"verdict": "NO VERDICT", "score": {}}
    truth = {None: "clean", "control": "control"}.get(plan) or json.load(open(plan))["category"]
    s = v.get("score") or {}
    cats = {k: n for k, n in (v.get("categories") or {}).items() if k not in ("ok",)}
    jcats = {k: n for k, n in (v.get("judge_categories") or {}).items() if k != "ok"}
    rows.append((run_id, truth, v["verdict"], s.get("naive"), s.get("valid_items"), cats, jcats))
    print(f"{run_id:38s} {truth:15s} {v['verdict']:9s} naive={s.get('naive') or 0:.3f} "
          f"valid={s.get('valid_items') if s.get('valid_items') is None else round(s['valid_items'], 3)} "
          f"{cats} judge={jcats}", flush=True)
