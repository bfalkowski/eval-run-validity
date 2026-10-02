"""Run manifest (fingerprint) and drift check against a baseline manifest.

Fields are grouped by how much a change matters:
  critical  a change can move the score by itself; undeclared drift makes the run invalid
  warn      a change usually doesn't, but can; undeclared drift makes the run degraded
  info      recorded for debugging, never compared
"""

import glob
import hashlib
import importlib.metadata as md
import json
import os
import platform
import subprocess
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")

CRITICAL = ["model", "policy_hash", "decision_instructions_hash", "judge_instructions_hash",
            "context_fields", "dataset_hash", "agent_code_hash", "scorer_code_hash"]
WARN = ["packages", "lockfile_hash", "python_version"]
PACKAGES = ["opentelemetry-api", "opentelemetry-sdk"]


def sha(text):
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()[:16]


def files_hash(patterns):
    h = hashlib.sha256()
    for p in sorted(f for pat in patterns for f in glob.glob(os.path.join(ROOT, pat))):
        h.update(os.path.basename(p).encode())
        h.update(open(p, "rb").read())
    return h.hexdigest()[:16]


def git_commit():
    sha_ = os.environ.get("GITHUB_SHA")
    if sha_:
        return sha_
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def build(run_id, cfg, tickets, declared=()):
    from agents import jev, support
    pkgs = {}
    for p in PACKAGES:
        try:
            pkgs[p] = md.version(p)
        except md.PackageNotFoundError:
            pkgs[p] = None
    lock = os.path.join(ROOT, "requirements.lock")
    m = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "planned_items": len(tickets),
        "model": jev.model(),
        "policy_version": cfg["policy_version"],
        "policy_hash": sha(cfg["policy_text"]),
        "decision_instructions_hash": sha(cfg["decision_instructions"]),
        "judge_instructions_hash": sha(support.JUDGE_INSTRUCTIONS),
        "context_fields": sorted(cfg["context_fields"]),
        "dataset_hash": sha(json.dumps([{k: t[k] for k in ("ticket_id", "order_id", "text", "expected")}
                                        for t in tickets], sort_keys=True)),
        "agent_code_hash": files_hash(["agents/jev.py", "agents/orders.py", "agents/support.py",
                                       "agents/policy.py"]),
        "scorer_code_hash": files_hash(["agents/scorer.py"]),
        "packages": pkgs,
        "lockfile_hash": sha(open(lock, "rb").read()) if os.path.exists(lock) else None,
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "error_policy": cfg["error_policy"],
        "declared_changes": sorted(declared),
        "git_commit": git_commit(),
        "runner": {"platform": platform.platform(), "ci": bool(os.environ.get("CI")),
                   "github_run_id": os.environ.get("GITHUB_RUN_ID"),
                   "runner_name": os.environ.get("RUNNER_NAME")},
        "jev_backend": os.environ.get("JEV_BACKEND", "api"),
    }
    m["manifest_hash"] = sha(json.dumps({k: m[k] for k in CRITICAL + WARN}, sort_keys=True))
    return m


def drift(m, baseline):
    """Fields that differ from the baseline and were not declared."""
    if not baseline:
        return {"critical": [], "warn": [], "baseline": None}
    declared = set(m.get("declared_changes", []))
    diff = lambda keys: [k for k in keys if k not in declared and m.get(k) != baseline.get(k)]  # noqa: E731
    return {"critical": diff(CRITICAL), "warn": diff(WARN), "baseline": baseline.get("run_id")}
