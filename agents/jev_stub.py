"""Local stand-in for the Jev API, for plumbing tests only (JEV_BACKEND=stub).

Kept out of agents/jev.py so that changes to the stand-in do not change the
agent code hash in the run fingerprint. Paper results come from the real API.
"""

import hashlib
import json
import os
import time

from agents.jev import INVALID_KEY, _http_error

_stub_tickets = None


def stub_backend(payload, key):
    """Answers like Jev would if it followed the policy, with a few seeded
    mistakes so a clean run is not a perfect score."""
    from agents.policy import ACTIONS, resolve
    global _stub_tickets
    time.sleep(float(os.environ.get("JEV_STUB_LATENCY_S", "0")))
    if key == INVALID_KEY or not key:
        raise _http_error(401, "Unauthorized")
    if _stub_tickets is None:
        from agents.datasets import DATASETS, tickets_path
        _stub_tickets = {t["text"]: t for v in DATASETS for t in map(json.loads, open(tickets_path(v)))}
    req = json.loads(payload)
    st, (qname, q) = req["state"], next(iter(req["questions"].items()))
    usage = {"input_tokens": len(payload) // 4}
    t = _stub_tickets.get(st.get("ticket"))
    if t is None:  # canary or unknown ticket: answer the canary's known answer
        if q["type"] == "choice":
            return json.dumps({"answers": {qname: {"choice": "0", "confidence": 0.99}}, "usage": usage}).encode()
        return json.dumps({"answers": {qname: {"noul": 0.97}}, "usage": usage}).encode()
    stock = st.get("stock", 1)
    right = resolve(t["issue"], st.get("order"), stock, refund_limit="$500" in st.get("policy", ""))

    def h(tag):
        return int(hashlib.sha256(f"{t['ticket_id']}|{tag}".encode()).hexdigest()[:8], 16) / 16 ** 8

    if q["type"] == "choice":
        action = right
        if h("decision") < 0.06 or action is None:
            action = ACTIONS[(ACTIONS.index(right or "reply") + 1) % len(ACTIONS)]
        key_ = next(k for k, v in q["criteria"].items() if v.startswith(action))
        ans = {"choice": key_, "confidence": 0.9 if action == right else 0.6}
    else:
        ok = st.get("agent_resolution") == right
        if h("judge") < 0.04:
            ok = not ok
        ans = {"noul": 0.93 if ok else 0.08}
    return json.dumps({"answers": {qname: ans}, "usage": usage}).encode()
