"""End-to-end tests with the stub Jev backend (no key, no network)."""

import ast
import glob
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
ENV = {**os.environ, "JEV_BACKEND": "stub", "POLICY_VERSION": "v1", "JEV_BACKOFF_S": "0"}
ENV.pop("FAULTD_URL", None)
ENV.pop("TYPESAFE_API_KEY", None)


def run(tmp, run_id, *args, env=None):
    cmd = [sys.executable, "run.py", "--run-id", run_id, "--out", str(tmp / "runs"),
           "--truth-dir", str(tmp / "truth"), "--baseline", str(tmp / "baseline.json"), *args]
    p = subprocess.run(cmd, cwd=ROOT, env=env or ENV, capture_output=True, text=True)
    vpath = tmp / "runs" / run_id / "verdict.json"
    assert vpath.exists(), p.stdout + p.stderr
    return json.load(open(vpath))


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    t = tmp_path_factory.mktemp("erv")
    run(t, "baseline", "--tickets", "60")
    shutil.copy(t / "runs" / "baseline" / "manifest.json", t / "baseline.json")
    return t


def test_clean_run_is_valid(tmp):
    v = run(tmp, "clean")
    assert v["verdict"] == "valid", v["reasons"]
    assert v["completed_items"] == 60 and not v["missed_items"]


def test_wrong_key_silent_lists_missed_evals(tmp):
    v = run(tmp, "wk", "--fault-plan", "plans/wrong_key_first20.json", "--error-policy", "silent")
    assert v["verdict"] == "invalid"
    assert v["missed_items"] == [f"T{i:03d}" for i in range(1, 21)]
    assert v["categories"].get("infrastructure") == 20
    # the silent fallback makes the naive score look like a model result
    assert v["score"]["naive"] is not None and v["score"]["valid_items"] is None


def test_declared_change_passes_undeclared_fails(tmp):
    control = run(tmp, "control", "--variant", "context_strip")
    drift = run(tmp, "drift", "--fault-plan", "plans/context_strip.json")
    assert control["verdict"] == "valid"
    assert drift["verdict"] == "invalid"
    assert control["score"]["naive"] == drift["score"]["naive"]  # same change, same outputs


def test_full_outage_stops_at_preflight(tmp):
    v = run(tmp, "down", "--fault-plan", "plans/jev_down_all.json")
    assert v["verdict"] == "invalid" and any("preflight" in r for r in v["reasons"])
    assert v["completed_items"] == 0


def test_missing_env_falls_back_to_stale_prompt(tmp):
    env = {k: v for k, v in ENV.items() if k != "POLICY_VERSION"}
    v = run(tmp, "noenv", env=env)
    assert v["verdict"] == "invalid" and "policy_hash" in v["drift"]["critical"]


def test_gate_does_not_read_ground_truth(tmp):
    """Same verdict with the ground-truth folder deleted."""
    run(tmp, "peek", "--fault-plan", "plans/jev_503_25.json")
    from validity import gate
    base = json.load(open(tmp / "baseline.json"))
    before = gate.evaluate(str(tmp / "runs" / "peek"), base)
    shutil.rmtree(tmp / "truth")
    after = gate.evaluate(str(tmp / "runs" / "peek"), base)
    assert before == after


def test_validity_code_never_touches_faultd():
    """Static check: nothing in validity/ imports faultd or names its log."""
    for path in glob.glob(os.path.join(ROOT, "validity", "*.py")):
        tree = ast.parse(open(path).read())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(n == "faultd" or n.startswith("faultd.") for n in names), path
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert "injections" not in node.value and "ground_truth" not in node.value, path


def test_faultd_is_deterministic():
    from faultd.server import State
    a, b = State("/tmp/erv-truth-a"), State("/tmp/erv-truth-b")
    f = {"kind": "http_503", "target": "jev", "rate": 0.3, "seed": 4}
    a.add(dict(f)), b.add(dict(f))
    hits_a = [a.check("r", "jev", i, c) is not None for i in range(50) for c in range(3)]
    hits_b = [b.check("r", "jev", i, c) is not None for i in range(50) for c in range(3)]
    assert hits_a == hits_b and 0.15 < sum(hits_a) / len(hits_a) < 0.45
    assert a.check("r", "judge", 1, 0) is None


def test_run_outputs_never_contain_the_key(tmp):
    """Traces, manifests and items get committed to the data branch, so the key
    must never be written into them, including on a wrong-key run."""
    secret = "ts-test-" + uuid.uuid4().hex  # made at runtime so no key-like literal is in the code
    env = {**ENV, "TYPESAFE_API_KEY": secret}
    run(tmp, "keycheck", "--fault-plan", "plans/wrong_key_first20.json", env=env)
    for folder in (tmp / "runs" / "keycheck", tmp / "truth" / "keycheck"):
        for path in folder.rglob("*"):
            if path.is_file():
                assert secret not in path.read_text(errors="ignore"), path
