"""Client every component uses to ask faultd whether to misbehave.

With FAULTD_URL unset, every check returns None and nothing is injected.
The client never records anything itself: what was injected is known only to
faultd's ground-truth log.
"""

import json
import os
import urllib.request


def _url():
    return os.environ.get("FAULTD_URL", "").rstrip("/")


def _post(path, body):
    req = urllib.request.Request(_url() + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def check(target, item, call=0):
    if not _url():
        return None
    return _post("/check", {"run_id": os.environ.get("RUN_ID", ""), "target": target,
                            "item": item, "call": call})["fault"]


def add_faults(faults):
    return _post("/faults", {"faults": faults})["faults"]


def clear():
    if _url():
        req = urllib.request.Request(_url() + "/faults", method="DELETE")
        urllib.request.urlopen(req, timeout=5).read()
