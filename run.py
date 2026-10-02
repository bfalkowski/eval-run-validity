"""Run the support-agent eval once, with optional faults, and gate the result.

  python run.py --run-id clean-01
  python run.py --run-id f-503-25 --fault-plan plans/infra_jev_503_25.json
  python run.py --run-id wrongkey --fault-plan plans/wrong_key_first20.json --error-policy silent

Writes runs/<run_id>/: manifest.json, preflight.json, traces.jsonl,
items.jsonl, verdict.json. faultd's record of what it injected goes to
ground_truth/<run_id>/injections.jsonl, which the gate never reads.

Environment:
  TYPESAFE_API_KEY  Jev key (not needed with JEV_BACKEND=stub)
  JEV_BACKEND       "stub" for the local stand-in, unset for the real API
  POLICY_VERSION    policy prompt version; NOTE the code default is the old
                    "v0" on purpose (a realistic stale default). Set v1.
  FAULTD_URL        use an already running faultd; otherwise one is started
                    in-process when --fault-plan is given
  MAX_SPEND_USD     stop the run (partial) when Jev spend passes this
"""

import argparse
import copy
import json
import os
import socket
import sys
import time

from agents import datasets, jev, scorer, support
from agents.orders import OrdersService
from agents.policy import POLICIES, resolve
from faultd import client as faultd
from validity import gate, manifest, preflight, tracing

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path=os.path.join(ROOT, ".env")):
    """Read KEY=value lines from .env without overriding the environment."""
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())


load_dotenv()
DEFAULT_POLICY_VERSION = "v0"  # deliberately stale; see docstring


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load_data(version):
    return datasets.load(version)


def apply_run_faults(cfg, tickets, fixture):
    """Config, fixture and dataset faults, decided once at the start."""
    f = faultd.check("config", -1)
    if f and f["kind"] == "prompt_edit":
        cfg["policy_text"] = POLICIES["v0"]  # rule 4 silently dropped
    elif f and f["kind"] == "context_strip":
        cfg["context_fields"] = [c for c in cfg["context_fields"] if c != "stock"]
    elif f and f["kind"] == "template_edit":
        cfg["decision_instructions"] = support.EDITED_DECISION_INSTRUCTIONS
    out = []
    for i, t in enumerate(tickets):
        out.append(t)
        f = faultd.check("fixtures", i)
        oid = str(t["order_id"])
        if f and f["kind"] == "stale_fixture" and oid in fixture["orders"]:
            o = fixture["orders"][oid]
            if o["status"] == "Delivered":
                o["status"], o["delivered_on"] = "Shipped", None
            else:
                o["status"], o["delivered_on"] = "Delivered", "2026-09-28"
        elif f and f["kind"] == "removed_record":
            fixture["orders"].pop(oid, None)
        f = faultd.check("dataset", i)
        if f and f["kind"] == "duplicate_item":
            out.append(copy.deepcopy(t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--tickets", type=int, default=None, help="only the first N tickets")
    ap.add_argument("--error-policy", choices=["loud", "silent"], default="loud")
    ap.add_argument("--fault-plan", default=None)
    ap.add_argument("--variant", choices=["context_strip"], default=None,
                    help="a declared change (control runs): the gate should let it through")
    ap.add_argument("--dataset", choices=sorted(datasets.DATASETS),
                    default=os.environ.get("DATASET", datasets.DEFAULT))
    ap.add_argument("--baseline", default=None,
                    help="baseline manifest; default baselines/manifest_<dataset>.json")
    ap.add_argument("--out", default=os.path.join(ROOT, "runs"))
    ap.add_argument("--truth-dir", default=os.path.join(ROOT, "ground_truth"))
    a = ap.parse_args()

    os.environ["RUN_ID"] = a.run_id
    run_dir = os.path.join(a.out, a.run_id)
    if os.path.exists(os.path.join(run_dir, "items.jsonl")):
        sys.exit(f"{run_dir} already has results; pick a new --run-id")
    os.makedirs(run_dir, exist_ok=True)

    if a.fault_plan:
        if not os.environ.get("FAULTD_URL"):
            from faultd import server
            port = free_port()
            server.serve(port, a.truth_dir, background=True)
            os.environ["FAULTD_URL"] = f"http://127.0.0.1:{port}"
        faultd.add_faults(json.load(open(a.fault_plan))["faults"])
        json.dump({"fault_plan": a.fault_plan, "plan": json.load(open(a.fault_plan))},
                  open(_truth(a, "plan.json"), "w"), indent=1)

    version = os.environ.get("POLICY_VERSION", DEFAULT_POLICY_VERSION)
    cfg = {"policy_version": version, "policy_text": POLICIES[version],
           "decision_instructions": support.DECISION_INSTRUCTIONS,
           "context_fields": ["order", "policy", "stock", "ticket", "today"],
           "error_policy": a.error_policy, "dataset": a.dataset}
    declared = []
    if a.variant == "context_strip":
        cfg["context_fields"].remove("stock")
        declared.append("context_fields")

    tickets, fixture = load_data(a.dataset)
    if a.tickets:
        tickets = tickets[: a.tickets]
    tickets = apply_run_faults(cfg, tickets, fixture)

    tracer = tracing.setup(os.path.join(run_dir, "traces.jsonl"), a.run_id)
    m = manifest.build(a.run_id, cfg, tickets, declared)
    json.dump(m, open(os.path.join(run_dir, "manifest.json"), "w"), indent=1)

    with tracer.start_as_current_span("eval.preflight"):
        pre = preflight.run()
    json.dump(pre, open(os.path.join(run_dir, "preflight.json"), "w"), indent=1)

    bpath = a.baseline or datasets.baseline_path(a.dataset)
    baseline = json.load(open(bpath)) if os.path.exists(bpath) else None
    if not pre["ok"]:
        tracing.shutdown()
        finish(run_dir, baseline)
        sys.exit(2)

    svc = OrdersService(fixture)
    expected = {i: t["expected"] for i, t in enumerate(tickets)}
    max_spend = float(os.environ.get("MAX_SPEND_USD", "5"))
    with open(os.path.join(run_dir, "items.jsonl"), "a") as out:
        for i, t in enumerate(tickets):
            if jev.SPENT_USD > max_spend:
                print(f"spend cap ${max_spend} reached; stopping at item {i}")
                break
            rec = run_item(tracer, i, t, svc, cfg, expected, m)
            out.write(json.dumps(rec) + "\n")
            out.flush()
    tracing.shutdown()
    print(f"Jev spend this run: ${jev.SPENT_USD:.4f}")
    finish(run_dir, baseline)


def run_item(tracer, i, t, svc, cfg, expected, m):
    with tracer.start_as_current_span("eval.item") as span:
        span.set_attribute("eval.run_id", m["run_id"])
        span.set_attribute("eval.item_index", i)
        span.set_attribute("eval.ticket_id", t["ticket_id"])
        f = faultd.check("harness", i)
        if not (f and f["kind"] == "no_reset"):
            svc.reset()
        rec = {"run_id": m["run_id"], "manifest_hash": m["manifest_hash"], "item_index": i,
               "ticket_id": t["ticket_id"], "errored": False}
        try:
            action, order, stock = support.handle(t, i, svc, cfg)
        except support.ItemError as e:
            rec.update(errored=True, error_stage=e.stage, error_type=e.error_type, action=None,
                       correct=False, expected=t["expected"], observed_effects=None, judge_votes=[])
            return rec
        if order is not None:
            span.set_attribute("eval.item.consistent", resolve(t["issue"], order, stock) == t["expected"])
        s = scorer.score(i, svc, expected)
        rec.update(action=action, **s)
        rec["judge_votes"] = support.judge(t, i, cfg, order, stock, action)
        return rec


def _truth(a, name):
    d = os.path.join(a.truth_dir, a.run_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def finish(run_dir, baseline):
    v = gate.evaluate(run_dir, baseline)
    json.dump(v, open(os.path.join(run_dir, "verdict.json"), "w"), indent=1)
    print(gate.summary(v))


if __name__ == "__main__":
    main()
