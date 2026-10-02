"""Paper numbers: join the gate's verdicts with faultd's ground truth.

This is the only code that reads the ground truth. It runs after the fact, on
the collected `data` branch, and never feeds anything back into the gate.

  git clone --branch data --depth 1 https://github.com/bfalkowski/eval-run-validity.git data-branch
  python analysis/analyze.py data-branch --split dev
  python analysis/analyze.py data-branch --split heldout --out analysis/heldout.json

Splits: dev = reps 1-2, heldout = reps 3-4 (different fault seeds). Clean runs
on the current v2 baseline (same policy hash) set the noise floor.

Item-level ground truth is rebuilt from the injection log alone:
  - Jev and judge transport faults (503, 429, timeout, connection refused)
    only count when all three attempts of a call were hit; otherwise the
    retry absorbed them and the item was not affected.
  - Non-retried faults (401, wrong key, malformed or out-of-range answers) and
    tool failures affect the item they hit.
  - A scorer off-by-one only matters when the neighbouring expected answer differs.
  - A missed state reset only matters when the previous ticket had a side effect.
  - Config faults are judged at run level only.
"""

import argparse
import copy
import sys
import glob
import json
import os
import re
import statistics
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agents.policy import resolve  # noqa: E402

RETRYABLE = {"http_503", "http_429", "timeout", "down"}
ATTEMPTS = 3
EXCLUDED = {"infrastructure", "config_drift", "judge_failure", "task_defect", "harness_bug"}
LEVEL = {"valid": 0, "degraded": 1, "invalid": 2}
THRESH = {"valid": 0.02, "degraded": 0.15}  # same starting values as validity/gate_config.json
PIPELINE_TRUTH = {"none": None, "kill_runner": "invalid", "job_timeout": "invalid", "partial_upload": "invalid",
                  "dep_drift": "degraded", "python_drift": "degraded", "missing_env": "invalid",
                  "wrong_commit": "invalid", "duplicate_merge": "invalid", "mixed_runs": "invalid"}


def jl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def load_run(root, rid):
    rd, gd = os.path.join(root, "runs", rid), os.path.join(root, "ground_truth", rid)
    j = lambda p: json.load(open(p)) if os.path.exists(p) else None  # noqa: E731
    r = {"id": rid, "manifest": j(os.path.join(rd, "manifest.json")), "verdict": j(os.path.join(rd, "verdict.json")),
         "items": jl(os.path.join(rd, "items.jsonl")), "plan": j(os.path.join(gd, "plan.json")),
         "pipeline": j(os.path.join(gd, "pipeline.json")) or {}, "injections": jl(os.path.join(gd, "injections.jsonl")),
         "collect": j(os.path.join(gd, "collect.json"))}
    m = re.search(r"-r(\d+)(?:-cf-|$)", rid)
    r["rep"] = int(m.group(1)) if m else None
    r["plan_name"] = r["pipeline"].get("plan") or ("clean" if "-clean-" in rid else None)
    r["pf"] = (r["collect"] or {}).get("collect_fault") or r["pipeline"].get("pipeline_fault", "none")
    r["error_policy"] = r["pipeline"].get("error_policy", "loud")
    r["category"] = (r["plan"] or {}).get("plan", {}).get("category") or \
        {"clean": "clean", "control": "control"}.get(r["plan_name"])
    return r


def stale_changes_answer(ticket, fixture):
    """Mirror run.py's stale_fixture mutation; True if the expected answer no
    longer fits the changed order (otherwise the stale record is harmless)."""
    o = copy.deepcopy(fixture["orders"][str(ticket["order_id"])])
    if o["status"] == "Delivered":
        o["status"], o["delivered_on"] = "Shipped", None
    else:
        o["status"], o["delivered_on"] = "Delivered", "2026-09-28"
    return resolve(ticket["issue"], o, fixture["stock"][o["sku"]]) != ticket["expected"]


def item_truth(run, expected_by_ticket, tickets=None, fixture=None):
    """{item_index: (primary_cause, judge_cause)} for items a fault actually affected."""
    items = sorted(run["items"], key=lambda i: i["item_index"])
    by_idx = {i["item_index"]: i for i in items}
    inj = [x for x in run["injections"] if x["item"] >= 0]
    truth = defaultdict(lambda: [None, None])
    calls = defaultdict(set)
    # duplicates shift later indexes; map original ticket index -> output index
    dups = sorted(x["item"] for x in inj if x["kind"] == "duplicate_item")
    def out_idx(i):
        return i + sum(1 for d in dups if d < i)
    for x in inj:
        k, tgt, i, c = x["kind"], x["target"], x["item"], x["call"]
        if k in RETRYABLE and tgt in ("jev", "judge"):
            calls[(tgt, i, c // 10)].add(c % 10)
            continue
        if k == "slow":
            continue
        if tgt == "jev":
            truth[i][0] = "infrastructure"
        elif tgt == "judge":
            truth[i][1] = "judge_failure" if k in ("malformed", "flip", "empty") else "infrastructure"
        elif tgt.startswith("tool."):
            truth[i][0] = "infrastructure"
        elif tgt == "fixtures":
            t = by_idx.get(out_idx(i))
            if k == "removed_record" or (t and tickets and stale_changes_answer(tickets[t["ticket_id"]], fixture)):
                truth[out_idx(i)][0] = "task_defect"
        elif tgt == "dataset":
            truth[out_idx(i) + 1][0] = "task_defect"
        elif tgt == "scorer" and k == "off_by_one":
            a, b = by_idx.get(i), by_idx.get(i + 1)
            if a and b and expected_by_ticket.get(a["ticket_id"]) != expected_by_ticket.get(b["ticket_id"]):
                truth[i][0] = "harness_bug"
        elif tgt == "harness" and k == "no_reset":
            # effects pile up across consecutive missed resets, so look back to the last real reset
            no_reset = {y["item"] for y in inj if y["kind"] == "no_reset"}
            j = i - 1
            while True:
                prev = by_idx.get(j)
                if prev and prev.get("action") in ("refund", "reship", "escalate"):
                    truth[i][0] = "harness_bug"
                    break
                if j not in no_reset or j < 0:
                    break
                j -= 1
    for (tgt, i, _), attempts in calls.items():
        if len(attempts) >= ATTEMPTS:
            if tgt == "jev":
                truth[i][0] = "infrastructure"
            else:
                truth[i][1] = truth[i][1] or "infrastructure"
    for i, (p, j) in truth.items():
        if p and not j:
            truth[i][1] = p  # an item that failed upstream has no valid judge result either
    return {i: tuple(v) for i, v in truth.items()}


def run_truth(run, truth, planned):
    pf = PIPELINE_TRUTH.get(run["pf"])
    cat = run["category"]
    if cat in ("clean", "control") and not pf:
        return "valid"
    if cat == "config_drift":
        return "invalid"
    if run["plan_name"] == "jev_down_all" or run["plan_name"] == "scorer_off_by_one_all":
        return "invalid"  # preflight should stop it
    share = sum(1 for p, _ in truth.values() if p) / planned if planned else 0
    jshare = sum(1 for _, j in truth.values() if j) / planned if planned else 0
    lvl = "invalid" if share > THRESH["degraded"] else "degraded" if share > THRESH["valid"] else "valid"
    if jshare > THRESH["degraded"] and lvl == "valid":
        lvl = "degraded"
    if run["plan_name"] == "duplicate_item_05" or run["plan_name"] == "duplicate_item_25":
        lvl = "invalid"  # the dataset itself changed
    if pf and LEVEL[pf] > LEVEL[lvl]:
        lvl = pf
    return lvl


def pct(x):
    return None if x is None else round(100 * x, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_root")
    ap.add_argument("--split", choices=["dev", "heldout", "silent", "pipeline", "all"], default="dev")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = a.data_root
    tickets = {t["ticket_id"]: t for t in jl(os.path.join(os.path.dirname(__file__), "..", "data", "v2", "tickets.jsonl"))}
    expected = {k: t["expected"] for k, t in tickets.items()}
    fixture = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "v2", "orders.json")))
    baseline = json.load(open(os.path.join(os.path.dirname(__file__), "..", "baselines", "manifest_v2.json")))

    runs = [load_run(root, os.path.basename(p)) for p in sorted(glob.glob(os.path.join(root, "runs", "*")))]
    missing = [r["id"] for r in runs if r["category"] is None]  # ground truth not collected (yet)
    runs = [r for r in runs if r["manifest"] and r["manifest"].get("dataset") == "v2" and r["category"]]
    current = [r for r in runs if r["manifest"]["policy_hash"] == baseline["policy_hash"]
               or r["category"] == "config_drift" or r["pf"] != "none"]
    reps = {"dev": {1, 2}, "heldout": {3, 4}}.get(a.split)

    # noise floor: every clean run on the current baseline, any split
    clean = [r for r in current if r["category"] == "clean" and r["pf"] == "none" and r["error_policy"] == "loud"]
    clean_scores = [r["verdict"]["score"]["naive"] for r in clean]
    noise = {"n": len(clean_scores), "mean": statistics.mean(clean_scores),
             "sd": statistics.stdev(clean_scores) if len(clean_scores) > 1 else 0.0,
             "min": min(clean_scores), "max": max(clean_scores)}
    lo, hi = noise["mean"] - 2 * noise["sd"], noise["mean"] + 2 * noise["sd"]

    def in_split(r):
        if a.split == "all":
            return True
        if a.split == "silent":
            return r["error_policy"] == "silent" and r["pf"] == "none"
        if a.split == "pipeline":
            return r["pf"] != "none"
        return r["rep"] in reps and r["pf"] == "none" and r["error_policy"] == "loud"
    sel = [r for r in current if r["rep"] is not None and in_split(r)]
    rows, conf_run = [], Counter()
    item_conf, unaffected_pred = Counter(), Counter()
    judge_conf = Counter()
    for r in sel:
        v = r["verdict"]
        planned = r["manifest"]["planned_items"]
        truth = item_truth(r, expected, tickets, fixture)
        t_level = run_truth(r, truth, planned)
        g_level = v["verdict"]
        conf_run[(t_level, g_level)] += 1
        naive = v["score"]["naive"]
        pred = {x["item_index"]: x for x in v.get("items", [])}
        rec = {i["item_index"]: i for i in r["items"]}
        kept = [i for i, x in pred.items() if x["primary"] not in EXCLUDED]
        valid_items = (sum(1 for i in kept if rec[i].get("correct")) / len(kept)) if kept else None
        rows.append({"run": r["id"], "plan": r["plan_name"], "category": r["category"], "pf": r["pf"],
                     "policy": r["error_policy"], "rep": r["rep"], "truth": t_level, "gate": g_level,
                     "naive": naive, "valid_items": valid_items,
                     "affected": sum(1 for p, _ in truth.values() if p),
                     "false_regression": naive is not None and naive < lo and r["category"] not in ("clean", "control"),
                     "false_improvement": naive is not None and naive > hi and r["category"] not in ("clean", "control"),
                     "reasons": v["reasons"]})
        if r["category"] in ("config_drift",) or r["pf"] != "none":
            continue
        for i, x in pred.items():
            tp, tj = truth.get(i, (None, None))
            if tp:
                item_conf[(tp, x["primary"])] += 1
            else:
                unaffected_pred[x["primary"]] += 1
            if tj:
                judge_conf[(tj, x["judge"])] += 1

    faulted = [x for x in rows if x["category"] not in ("clean", "control")]
    flagged = lambda x: x["gate"] != "valid"  # noqa: E731
    should = [x for x in rows if x["truth"] != "valid"]
    shouldnt = [x for x in rows if x["truth"] == "valid"]
    cats = sorted({c for c, _ in item_conf})
    per_cat = {}
    for c in cats:
        tp = item_conf[(c, c)]
        fn = sum(n for (t, p), n in item_conf.items() if t == c and p != c)
        fp = sum(n for (t, p), n in item_conf.items() if p == c and t != c) + unaffected_pred.get(c, 0)
        per_cat[c] = {"recall": tp / (tp + fn) if tp + fn else None, "precision": tp / (tp + fp) if tp + fp else None,
                      "n": tp + fn}
    total_aff = sum(item_conf.values())
    S = {
        "split": a.split, "runs": len(rows), "faulted_runs": len(faulted),
        "noise_floor": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in noise.items()},
        "rq1": {"false_regression_rate": sum(x["false_regression"] for x in faulted) / len(faulted) if faulted else None,
                "false_improvement_rate": sum(x["false_improvement"] for x in faulted) / len(faulted) if faulted else None,
                "by_category": {c: {"runs": len(g), "false_regression": sum(x["false_regression"] for x in g),
                                    "flagged_by_gate": sum(flagged(x) for x in g)}
                                for c in sorted({x["category"] for x in faulted})
                                for g in [[x for x in faulted if x["category"] == c]]}},
        "rq2": {"affected_items": total_aff,
                "attribution_accuracy": sum(n for (t, p), n in item_conf.items() if t == p) / total_aff if total_aff else None,
                "per_category": per_cat,
                "confusion": {f"{t} -> {p}": n for (t, p), n in sorted(item_conf.items())},
                "unaffected_items_excluded": sum(n for p, n in unaffected_pred.items() if p in EXCLUDED),
                "unaffected_items": sum(unaffected_pred.values()),
                "judge_confusion": {f"{t} -> {p}": n for (t, p), n in sorted(judge_conf.items())}},
        "rq3": {"should_flag": len(should), "flagged": sum(flagged(x) for x in should),
                "should_pass": len(shouldnt), "wrongly_flagged": sum(flagged(x) for x in shouldnt),
                "exact_level_match": sum(x["truth"] == x["gate"] for x in rows),
                "level_confusion": {f"{t} -> {g}": n for (t, g), n in sorted(conf_run.items())}},
        "rq4": {"clean_mean": noise["mean"],
                "valid_items_gap": {x["run"]: round(x["valid_items"] - noise["mean"], 4)
                                    for x in faulted if x["valid_items"] is not None},
                },
        "runs_detail": rows,
    }
    # RQ4 covers item-level faults only: config drift and a changed dataset are run-level problems
    item_level = [x for x in faulted if x["category"] in ("infrastructure", "judge_failure", "task_defect", "harness_bug")
                  and not (x["plan"] or "").startswith("duplicate_item") and x["pf"] == "none"]
    S["rq4"]["valid_items_gap"] = {x["run"]: round(x["valid_items"] - noise["mean"], 4)
                                   for x in item_level if x["valid_items"] is not None}
    gaps = list(S["rq4"]["valid_items_gap"].values())
    naive_gaps = [x["naive"] - noise["mean"] for x in item_level if x["naive"] is not None]
    S["rq4"]["median_abs_gap_valid_items"] = statistics.median(abs(g) for g in gaps) if gaps else None
    S["rq4"]["median_abs_gap_naive"] = statistics.median(abs(g) for g in naive_gaps) if naive_gaps else None

    if a.out:
        json.dump(S, open(a.out, "w"), indent=1, default=str)
    print(f"split {a.split}: {len(rows)} runs ({len(faulted)} faulted)"
          + (f"; skipped {len(missing)} runs with no ground truth" if missing else ""))
    n = S["noise_floor"]
    print(f"noise floor: {n['n']} clean runs, mean {pct(n['mean'])}%, sd {pct(n['sd'])}, range {pct(n['min'])}-{pct(n['max'])}%")
    q = S["rq1"]
    print(f"RQ1 false regressions {pct(q['false_regression_rate'])}%  false improvements {pct(q['false_improvement_rate'])}%")
    for c, d in q["by_category"].items():
        print(f"    {c:15s} runs {d['runs']:3d}  naive false regression {d['false_regression']:3d}  gate flagged {d['flagged_by_gate']:3d}")
    q = S["rq2"]
    print(f"RQ2 attribution accuracy {pct(q['attribution_accuracy'])}% on {q['affected_items']} affected items; "
          f"unaffected items wrongly excluded {q['unaffected_items_excluded']} of {q['unaffected_items']}")
    for c, d in q["per_category"].items():
        print(f"    {c:15s} n {d['n']:4d}  recall {pct(d['recall'])}  precision {pct(d['precision'])}")
    for k, v in q["confusion"].items():
        print(f"      {k}: {v}")
    print("    judge:", q["judge_confusion"])
    q = S["rq3"]
    print(f"RQ3 gate flagged {q['flagged']} of {q['should_flag']} runs that should be flagged; "
          f"wrongly flagged {q['wrongly_flagged']} of {q['should_pass']}; exact level {q['exact_level_match']} of {len(rows)}")
    for k, v in q["level_confusion"].items():
        print(f"      truth {k}: {v}")
    q = S["rq4"]
    print(f"RQ4 median |gap to clean mean|: naive {pct(q['median_abs_gap_naive'])} pts, valid items {pct(q['median_abs_gap_valid_items'])} pts")
    print("\nmismatches (truth vs gate):")
    for x in rows:
        if x["truth"] != x["gate"]:
            print(f"  {x['run']:60s} truth {x['truth']:8s} gate {x['gate']:8s} affected {x['affected']:2d} | {'; '.join(x['reasons'])[:120]}")


if __name__ == "__main__":
    main()
