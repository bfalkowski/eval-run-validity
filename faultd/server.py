"""faultd: the fault API.

A small HTTP service that decides, call by call, whether a component should
misbehave. Every component that can fail (Jev calls, the judge, tools, the
fixtures, the scorer, the run config) asks faultd through faultd.client before
it acts. faultd answers with a fault to apply, or none, and writes every
fault it hands out to a ground-truth log that the validity code never reads.

API
  POST   /faults          body: one fault or {"faults": [...]}; returns ids
  GET    /faults          list active faults
  DELETE /faults          clear all faults
  POST   /check           body: {"run_id", "target", "item", "call"}
                          returns {"fault": {...}} or {"fault": null}
  GET    /health

A fault:
  {"kind": "http_503", "target": "jev", "rate": 0.25,
   "start_item": 0, "end_item": null, "seed": 1, "params": {}}

`target` matches exactly, or by prefix when it ends in "*" ("tool.*"); a
list of targets matches any of them.
`start_item`/`end_item` bound the item index (inclusive); null means no bound,
so preflight canaries (negative indexes) are covered too. The decision is a
hash of (seed, fault id, target, item, call), so a faulted run replays exactly.

Run:  python -m faultd.server --port 8787 --truth-dir ground_truth
"""

import argparse
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KINDS = {
    # Jev and judge transport
    "down", "http_503", "http_429", "http_401", "timeout", "slow", "wrong_key",
    # Jev and judge response
    "malformed", "empty", "missing_confidence", "out_of_range", "flip",
    # tools
    "tool_timeout", "tool_500",
    # fixtures (environment) and dataset
    "stale_fixture", "removed_record", "duplicate_item",
    # harness and scorer
    "off_by_one", "no_reset",
    # config, applied once at run start
    "prompt_edit", "context_strip", "template_edit",
}


class State:
    def __init__(self, truth_dir):
        self.truth_dir = truth_dir
        self.faults = []
        self.lock = threading.Lock()
        self.next_id = 1

    def add(self, f):
        if f.get("kind") not in KINDS:
            raise ValueError(f"unknown fault kind: {f.get('kind')}")
        if not f.get("target"):
            raise ValueError("fault needs a target")
        with self.lock:
            f = {"rate": 1.0, "start_item": None, "end_item": None, "seed": 0, "params": {}, **f,
                 "id": f"f{self.next_id}"}
            self.next_id += 1
            self.faults.append(f)
            return f

    def check(self, run_id, target, item, call):
        with self.lock:
            faults = list(self.faults)
        for f in faults:
            if not any(t == target or (t.endswith("*") and target.startswith(t[:-1]))
                       for t in (f["target"] if isinstance(f["target"], list) else [f["target"]])):
                continue
            if f["start_item"] is not None and item < f["start_item"]:
                continue
            if f["end_item"] is not None and item > f["end_item"]:
                continue
            if _draw(f, target, item, call) >= f["rate"]:
                continue
            self._log(run_id, f, target, item, call)
            return {k: f[k] for k in ("id", "kind", "target", "params")}
        return None

    def _log(self, run_id, f, target, item, call):
        d = os.path.join(self.truth_dir, run_id or "unknown")
        os.makedirs(d, exist_ok=True)
        rec = {"run_id": run_id, "fault_id": f["id"], "kind": f["kind"], "target": target,
               "item": item, "call": call, "t": time.time()}
        with self.lock, open(os.path.join(d, "injections.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")


def _draw(f, target, item, call):
    h = hashlib.sha256(f"{f['seed']}|{f['id']}|{target}|{item}|{call}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True})
            if self.path == "/faults":
                return self._send(200, {"faults": state.faults})
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            if self.path == "/faults":
                with state.lock:
                    state.faults.clear()
                return self._send(200, {"cleared": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                b = self._body()
                if self.path == "/faults":
                    added = [state.add(f) for f in b.get("faults", [b])]
                    return self._send(200, {"faults": added})
                if self.path == "/check":
                    return self._send(200, {"fault": state.check(b.get("run_id"), b["target"],
                                                                   int(b["item"]), int(b.get("call", 0)))})
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

    return H


def serve(port=8787, truth_dir="ground_truth", background=False):
    state = State(truth_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    if background:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    srv.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--truth-dir", default="ground_truth")
    a = ap.parse_args()
    print(f"faultd on http://127.0.0.1:{a.port}, ground truth in {a.truth_dir}/")
    serve(a.port, a.truth_dir)
