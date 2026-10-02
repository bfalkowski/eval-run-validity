"""The validity gate: valid, degraded or invalid, decided before any score is
reported. Writes verdict.json in the run folder.

  python -m validity.gate runs/<run_id> [--baseline auto | path]

With --baseline auto (the default), the baseline is baselines/manifest_<dataset>.json
for the run's dataset.

An invalid run reports no score, only why. A degraded run reports the score
on valid items with the exclusion rate beside it. The naive score (what a
pipeline without these checks would report) is always kept, for comparison.
"""

import argparse
import json
import os
from collections import Counter

from validity import attribution, manifest

CONFIG = json.load(open(os.path.join(os.path.dirname(__file__), "gate_config.json")))


def _rate(xs):
    return sum(xs) / len(xs) if xs else None


def evaluate(run_dir, baseline=None):
    reasons, level = [], "valid"

    def worse(new, why):
        nonlocal level
        order = ["valid", "degraded", "invalid"]
        if order.index(new) > order.index(level):
            level = new
        reasons.append(f"{new}: {why}")

    mpath = os.path.join(run_dir, "manifest.json")
    if not os.path.exists(mpath):
        return {"verdict": "invalid", "reasons": ["invalid: manifest missing"], "run_dir": run_dir}
    m = json.load(open(mpath))
    pre = json.load(open(os.path.join(run_dir, "preflight.json"))) \
        if os.path.exists(os.path.join(run_dir, "preflight.json")) else None
    d = manifest.drift(m, baseline)

    if pre is None:
        worse("invalid", "preflight result missing")
    elif not pre["ok"]:
        worse("invalid", "preflight failed: " + ", ".join(f"{c['name']} ({c['detail']})"
                                                         for c in pre["checks"] if not c["ok"]))
    if "dataset_hash" in d["critical"]:
        worse("invalid", "dataset differs from baseline (task/dataset defect)")
    cfg_drift = [f for f in d["critical"] if f != "dataset_hash"]
    if cfg_drift:
        worse("invalid", f"undeclared change from baseline in {cfg_drift}")
    if d["warn"]:
        worse("degraded", f"environment differs from baseline in {d['warn']}")

    attrs, items, spans = attribution.attribute_run(run_dir, d["critical"], m.get("dataset", "v1"))
    planned = m["planned_items"]
    idx = Counter(r["item_index"] for r in items)
    if any(n > 1 for n in idx.values()):
        worse("invalid", "the same item appears more than once in the results (runs mixed or merged twice)")
    foreign = {r.get("run_id") for r in items} - {m["run_id"]}
    hashes = {r.get("manifest_hash") for r in items} - {m["manifest_hash"]}
    if foreign or hashes:
        worse("invalid", f"results from another run or config mixed in: {sorted(foreign | hashes)}")
    if pre and pre["ok"]:
        done = len(idx)
        if done < planned:
            share = done / planned if planned else 0
            worse("invalid" if share < CONFIG["min_completed_share"] else "degraded",
                  f"partial run: {done} of {planned} items finished")
        if not spans:
            worse("invalid", "no traces found")

    excl = [a for a in attrs if a["primary"] in attribution.EXCLUDED]
    excl_rate = len(excl) / len(attrs) if attrs else 0.0
    if attrs:
        if excl_rate > CONFIG["max_excluded_degraded"]:
            worse("invalid", f"{len(excl)} of {len(attrs)} items excluded ({excl_rate:.0%})")
        elif excl_rate > CONFIG["max_excluded_valid"]:
            worse("degraded", f"{len(excl)} of {len(attrs)} items excluded ({excl_rate:.0%})")

    rec = {r["item_index"]: r for r in items}
    naive = _rate([bool(r.get("correct")) for r in items])
    kept = [a for a in attrs if a["primary"] not in attribution.EXCLUDED]
    valid_score = _rate([bool(rec[a["item_index"]].get("correct")) for a in kept])
    first_vote = [bool((r.get("judge_votes") or [False])[0]) for r in items]
    jkept = [a for a in attrs if a["judge"] == "ok"]
    judge_excl_rate = (len(attrs) - len(jkept)) / len(attrs) if attrs else 0.0  # not 1 - x: 1 - 51/60 > 0.15
    judge_valid = _rate([all(rec[a["item_index"]]["judge_votes"]) for a in jkept])
    if attrs and judge_excl_rate > CONFIG["max_excluded_degraded"]:
        worse("degraded", f"judge score withheld: {judge_excl_rate:.0%} of judge results excluded")

    v = {
        "run_id": m["run_id"], "verdict": level, "reasons": reasons,
        "planned_items": planned, "completed_items": len(idx),
        "score": {
            "naive": naive,
            "valid_items": valid_score if level != "invalid" else None,
            "n_valid_items": len(kept),
            "excluded_rate": excl_rate,
        },
        "judge_score": {
            "naive": _rate(first_vote),
            "valid_items": judge_valid if level != "invalid" and judge_excl_rate <= CONFIG["max_excluded_degraded"]
            else None,
            "excluded_rate": judge_excl_rate,
        },
        "categories": dict(Counter(a["primary"] for a in attrs)),
        "judge_categories": dict(Counter(a["judge"] for a in attrs)),
        "missed_items": [a["ticket_id"] for a in attrs if a["missed"]],
        "missed_judge_items": [a["ticket_id"] for a in attrs if a["judge_missed"]],
        "retries_absorbed": sum(a["retries"] for a in attrs),
        "drift": d,
        "items": attrs,
    }
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--baseline", default="auto")
    a = ap.parse_args()
    path = a.baseline
    if path == "auto":
        from agents.datasets import baseline_path
        ds = json.load(open(os.path.join(a.run_dir, "manifest.json"))).get("dataset", "v1") \
            if os.path.exists(os.path.join(a.run_dir, "manifest.json")) else "v1"
        path = baseline_path(ds)
    base = json.load(open(path)) if path and os.path.exists(path) else None
    v = evaluate(a.run_dir, base)
    json.dump(v, open(os.path.join(a.run_dir, "verdict.json"), "w"), indent=1)
    print(summary(v))


def summary(v):
    s = v.get("score", {})
    lines = [f"run {v.get('run_id')}: {v['verdict'].upper()}"]
    lines += [f"  - {r}" for r in v["reasons"]]
    if s:
        fmt = lambda x: "n/a" if x is None else f"{x:.3f}"  # noqa: E731
        lines.append(f"  naive score {fmt(s['naive'])} | valid-items score {fmt(s['valid_items'])} "
                     f"on {s['n_valid_items']} items | excluded {s['excluded_rate']:.0%}")
        js = v["judge_score"]
        lines.append(f"  judge naive {fmt(js['naive'])} | judge valid-items {fmt(js['valid_items'])} "
                     f"| judge excluded {js['excluded_rate']:.0%}")
        lines.append(f"  categories {v['categories']}  judge {v['judge_categories']}")
        if v["missed_items"]:
            lines.append(f"  missed evals: {len(v['missed_items'])} ({', '.join(v['missed_items'][:12])}"
                         f"{' ...' if len(v['missed_items']) > 12 else ''})")
        if v["retries_absorbed"]:
            lines.append(f"  retries absorbed: {v['retries_absorbed']}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
