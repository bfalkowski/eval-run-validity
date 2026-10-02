"""Build the index for the run review page (bfalkowski.github.io/review/).

Reads a checkout of the `data` branch, re-gates every run with the current
gate so all verdicts come from the same code, and writes one compact JSON file
the page loads. Traces are not copied; the page fetches a run's traces from the
data branch only when someone opens a ticket.

  git clone --branch data --depth 1 https://github.com/bfalkowski/eval-run-validity.git data-branch
  python analysis/build_review_index.py data-branch ../bfalkowski.github.io/review/index.json

Experiment labels (which fault was injected) come from ground_truth/ and are
stored separately under "experiment"; the page hides them by default, because
the gate never sees them.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from validity import gate  # noqa: E402

BATCHES = {
    "gh37010661003": "baseline (v1)", "gh37011788500": "baseline (v2, first policy)",
    "gh37012503737": "baseline (v2)", "gh37014038090": "noise floor", "gh37014574123": "development",
    "gh37019134878": "held-out", "gh37022301998": "silent fallback", "gh37023643915": "pipeline",
}
EXCLUDED = {"infrastructure", "config_drift", "judge_failure", "task_defect", "harness_bug"}


def load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def baseline_for(m, root):
    ds = (m or {}).get("dataset", "v1")
    return load(os.path.join(root, "baselines", f"manifest_{ds}.json"))


def main():
    data, out = sys.argv[1], sys.argv[2]
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    runs = []
    for rid in sorted(os.listdir(os.path.join(data, "runs"))):
        rd, gd = os.path.join(data, "runs", rid), os.path.join(data, "ground_truth", rid)
        m = load(os.path.join(rd, "manifest.json"))
        if m is None:
            continue
        v = gate.evaluate(rd, baseline_for(m, repo))
        pipe = load(os.path.join(gd, "pipeline.json")) or {}
        plan = load(os.path.join(gd, "plan.json")) or {}
        coll = load(os.path.join(gd, "collect.json")) or {}
        cf = coll.get("collect_fault") if "source_run" in coll else None
        prefix = rid.split("-")[0]
        batch = "collect fault" if "-cf-" in rid else BATCHES.get(prefix, prefix)
        s, js = v.get("score") or {}, v.get("judge_score") or {}
        items = v.get("items", [])
        recs = {}
        if os.path.exists(os.path.join(rd, "items.jsonl")):
            for line in open(os.path.join(rd, "items.jsonl")):
                try:
                    x = json.loads(line)
                except json.JSONDecodeError:
                    continue
                recs[x["item_index"]] = {"a": x.get("action"), "v": x.get("judge_votes"),
                                         "err": x.get("error_type"), "x": x.get("expected"),
                                         "o": x.get("observed_effects")}
        runs.append({
            "id": rid, "batch": batch, "dataset": m.get("dataset", "v1"),
            "created_at": m.get("created_at"), "verdict": v["verdict"], "reasons": v["reasons"],
            "planned": v.get("planned_items"), "completed": v.get("completed_items"),
            "naive": s.get("naive"), "valid_items": s.get("valid_items"), "excluded_rate": s.get("excluded_rate"),
            "judge_naive": js.get("naive"), "judge_valid": js.get("valid_items"),
            "judge_excluded_rate": js.get("excluded_rate"),
            "categories": v.get("categories", {}), "judge_categories": v.get("judge_categories", {}),
            "missed": v.get("missed_items", []), "retries": v.get("retries_absorbed", 0),
            "drift": v.get("drift", {}),
            "fingerprint": {k: m.get(k) for k in ("model", "policy_version", "policy_hash", "context_fields",
                                                   "dataset_hash", "agent_code_hash", "scorer_code_hash",
                                                   "python_version", "git_commit", "error_policy",
                                                   "declared_changes", "manifest_hash")},
            "flagged_items": [{"i": a["item_index"], "t": a["ticket_id"], "p": a["primary"], "j": a["judge"],
                               "r": a["reasons"], **recs.get(a["item_index"], {})}
                              for a in items if a["primary"] in EXCLUDED or a["judge"] in EXCLUDED],
            "model_failures": [{"i": a["item_index"], "t": a["ticket_id"], **recs.get(a["item_index"], {})}
                               for a in items
                               if a["primary"] == "model_failure"],
            "has_traces": os.path.exists(os.path.join(rd, "traces.jsonl")),
            "experiment": {"plan": pipe.get("plan") or ("clean" if "-clean-" in rid else None),
                           "category": (plan.get("plan") or {}).get("category"),
                           "faults": [f.get("kind") for f in (plan.get("plan") or {}).get("faults", [])],
                           "pipeline_fault": cf or pipe.get("pipeline_fault"),
                           "error_policy": pipe.get("error_policy") or m.get("error_policy")},
        })
    tickets = {}
    for ds, path in (("v1", "data/tickets.jsonl"), ("v2", "data/v2/tickets.jsonl")):
        for line in open(os.path.join(repo, path)):
            t = json.loads(line)
            tickets[t["ticket_id"]] = {"text": t["text"], "expected": t["expected"], "order": t["order_id"]}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump({"runs": runs, "tickets": tickets,
               "source": "https://github.com/bfalkowski/eval-run-validity/tree/data"},
              open(out, "w"), separators=(",", ":"))
    print(f"{len(runs)} runs -> {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
