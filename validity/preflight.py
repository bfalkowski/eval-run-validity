"""Preflight: checks run before the first scored item. A failed preflight stops
the run before it spends anything on the eval itself.

  data_checksums  the committed data files match CHECKSUMS.json
  jev_canary      Jev answers a trivial choice question correctly
  judge_canary    the judge answers a trivial yes/no question correctly
  scorer_canary   the scorer gets known-answer cases right
"""

import hashlib
import json
import os

from agents import jev, scorer

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

SCORER_GOLDENS = [  # (effects the agent produced, expected action, correct?)
    (["refund"], "refund", True), ([], "reply", True), (["escalate"], "refund", False),
    (["reship"], "reship", True), ([], "escalate", False), (["escalate"], "escalate", True),
    (["refund"], "reship", False), ([], "reply", True), (["reship"], "refund", False),
    (["escalate"], "reply", False), (["refund"], "refund", True), ([], "reship", False),
]


class _FakeSvc:
    def __init__(self, effects):
        self.effects = [{"action": a} for a in effects] + [{"action": "reply"}]


def run():
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    sums = json.load(open(os.path.join(DATA, "CHECKSUMS.json")))
    bad = [f for f, h in sums.items()
           if hashlib.sha256(open(os.path.join(DATA, f), "rb").read()).hexdigest() != h]
    add("data_checksums", not bad, f"mismatch: {bad}" if bad else "")

    try:
        ans, _ = jev.ask({"note": "preflight canary"}, "canary",
                         {"type": "choice", "instructions": "Which of these is a fruit?",
                          "criteria": {"0": "apple", "1": "hammer"}}, role="decision", item=-1)
        add("jev_canary", str(ans["choice"]) == "0", f"answered {ans['choice']}")
    except jev.JevCallError as e:
        add("jev_canary", False, e.error_type)

    try:
        ans, _ = jev.ask({"note": "preflight canary"}, "canary",
                         {"type": "noul", "instructions": "Is the sky blue on a clear day?"},
                         role="judge", item=-1)
        add("judge_canary", ans["noul"] >= 0.5, f"noul {ans['noul']:.2f}")
    except jev.JevCallError as e:
        add("judge_canary", False, e.error_type)

    expected = [g[1] for g in SCORER_GOLDENS]
    wrong = [i for i, (effects, _, ok) in enumerate(SCORER_GOLDENS)
             if scorer.score(-100 + i, _FakeSvc(effects),
                             {-100 + j: e for j, e in enumerate(expected)})["correct"] != ok]
    add("scorer_canary", not wrong, f"wrong on goldens {wrong}" if wrong else "")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
