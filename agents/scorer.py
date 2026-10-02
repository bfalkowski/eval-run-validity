"""Deterministic scorer: did the agent's side effects match the expected action?

`reply` is always sent and is not scored. Expected `reply` means no other side
effect. The scorer asks faultd (target "scorer") before scoring, so a scorer
bug can be switched on for a share of items.
"""

from faultd import client as faultd

ACTION_EFFECTS = {"refund": ["refund"], "reship": ["reship"], "escalate": ["escalate"], "reply": []}


def observed_effects(svc):
    return sorted(e["action"] for e in svc.effects if e["action"] != "reply")


def score(item, svc, expected_by_index):
    """expected_by_index: {item index: expected action}."""
    fault = faultd.check("scorer", item)
    idx = item + 1 if fault and fault["kind"] == "off_by_one" else item
    expected = expected_by_index.get(idx, expected_by_index[item])
    observed = observed_effects(svc)
    return {"expected": expected, "observed_effects": observed,
            "correct": observed == sorted(ACTION_EFFECTS[expected])}
