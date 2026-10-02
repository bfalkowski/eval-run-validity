"""Attribution: give every item a cause, using only what a real pipeline sees.

Inputs: the run's manifest, preflight result, traces and item records, the
committed dataset, and optionally a baseline manifest. This module never reads
faultd's ground-truth log (tests/test_no_peeking.py enforces that).

Categories (taxonomy in the plan):
  infrastructure  transport errors on Jev, the judge or a tool (after retries)
  config_drift    the run's fingerprint differs from the baseline, undeclared
  judge_failure   judge verdict unparseable, or its two repeats disagree
  task_defect     the item's expected answer doesn't fit its fixture, the
                  record is missing, or the item is duplicated
  harness_bug     the scorer's view disagrees with the trace, or scored the
                  item against the wrong expected answer
  model_failure   none of the above, and the agent got it wrong
  ok              correct, nothing wrong

Each item gets a category for the primary score (did the agent take the right
action?) and one for the judge score.
"""

import json
import os
from collections import defaultdict

TRANSPORT = {"timeout", "connection_error"}
EXCLUDED = {"infrastructure", "config_drift", "judge_failure", "task_defect", "harness_bug"}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a run killed mid-write can leave a torn last line
    return out


def is_transport(err):
    return err in TRANSPORT or (err or "").startswith("http_")


def spans_by_item(spans):
    by = defaultdict(list)
    for s in spans:
        i = s["attributes"].get("eval.item_index")
        if i is not None:
            by[int(i)].append(s)
    return by


def attribute_item(rec, spans, dataset_expected, run_drift, seen_tickets):
    reasons = []
    dec = [s for s in spans if s["name"] == "jev.call" and s["attributes"].get("eval.role") == "decision"]
    jud = [s for s in spans if s["name"] == "jev.call" and s["attributes"].get("eval.role") == "judge"]
    tools = [s for s in spans if s["name"].startswith("tool.")]
    item_span = next((s for s in spans if s["name"] == "eval.item"), None)
    err = lambda s: s["attributes"].get("error.type") if s["status"] == "ERROR" else None  # noqa: E731

    dec_ok = any(s["status"] != "ERROR" for s in dec)
    judge_ok = [s for s in jud if s["status"] != "ERROR"]
    missed = not dec_ok
    tool_errs = [err(s) for s in tools if err(s)]
    trace_effects = sorted(s["attributes"]["tool.name"] for s in tools if s["status"] != "ERROR"
                           and s["attributes"].get("tool.name") in ("refund", "reship", "escalate"))
    correct = rec.get("correct")

    # --- primary score
    primary = None
    if rec["ticket_id"] in seen_tickets:
        primary, _ = "task_defect", reasons.append("duplicate item")
    elif "not_found" in tool_errs:
        primary, _ = "task_defect", reasons.append("order record missing")
    elif item_span and item_span["attributes"].get("eval.item.consistent") is False:
        primary, _ = "task_defect", reasons.append("expected answer inconsistent with fixture")
    elif rec.get("expected") is not None and rec["expected"] != dataset_expected.get(rec["ticket_id"]):
        primary, _ = "harness_bug", reasons.append("scored against the wrong expected answer")
    elif not rec.get("errored") and rec.get("observed_effects") is not None \
            and rec["observed_effects"] != trace_effects:
        primary, _ = "harness_bug", reasons.append("scorer saw effects the trace does not show")
    elif any(is_transport(err(s)) or err(s) == "parse_error" for s in dec) and not dec_ok:
        primary, _ = "infrastructure", reasons.append(f"decision call failed: {[err(s) for s in dec]}")
    elif tool_errs:
        primary, _ = "infrastructure", reasons.append(f"tool failed: {tool_errs}")
    elif rec.get("errored"):
        primary, _ = "infrastructure", reasons.append(f"item errored: {rec.get('error_type')}")
    elif not correct and run_drift:
        primary, _ = "config_drift", reasons.append(f"undeclared drift: {run_drift}")
    elif not correct:
        primary = "model_failure"
    else:
        primary = "ok"

    # --- judge score
    votes = rec.get("judge_votes") or []
    judge = None
    if primary in ("task_defect", "harness_bug") or missed:
        judge = primary if primary != "ok" else "infrastructure"
    elif any(is_transport(err(s)) for s in jud):
        judge, _ = "infrastructure", reasons.append("judge call failed")
    elif any(err(s) == "parse_error" for s in jud):
        judge, _ = "judge_failure", reasons.append("judge answer unparseable")
    elif len(judge_ok) < len(jud) or not jud:
        judge, _ = "infrastructure", reasons.append("judge missing")
    elif len(set(votes)) > 1:
        judge, _ = "judge_failure", reasons.append("judge repeats disagree")
    else:
        judge = "ok"

    retries = sum(max(0, int(s["attributes"].get("jev.attempts", 1)) - 1) for s in dec + jud)
    return {"item_index": rec["item_index"], "ticket_id": rec["ticket_id"], "primary": primary,
            "judge": judge, "missed": missed, "judge_missed": not judge_ok, "retries": retries,
            "reasons": reasons}


def attribute_run(run_dir, run_drift_fields, dataset="v1"):
    from agents.datasets import tickets_path
    # A changed dataset is a task/dataset problem, reported at run level by the
    # gate; it is not a reason to blame individual failures on config drift.
    run_drift_fields = [f for f in run_drift_fields if f != "dataset_hash"]
    items = load_jsonl(os.path.join(run_dir, "items.jsonl"))
    spans = load_jsonl(os.path.join(run_dir, "traces.jsonl"))
    dataset_expected = {t["ticket_id"]: t["expected"] for t in load_jsonl(tickets_path(dataset))}
    by = spans_by_item(spans)
    seen, out = set(), []
    for rec in sorted(items, key=lambda r: r["item_index"]):
        out.append(attribute_item(rec, by.get(rec["item_index"], []), dataset_expected, run_drift_fields, seen))
        seen.add(rec["ticket_id"])
    return out, items, spans
