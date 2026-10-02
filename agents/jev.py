"""Jev transport: every call to TypeSafe's System One API goes through here.

Each call is one OpenTelemetry span. Before each attempt the transport asks
faultd whether to misbehave; an injected fault raises the same exception, or
produces the same bad body, that a real failure would. Nothing in the span
says whether a failure was injected.

JEV_BACKEND=stub swaps the HTTP call for a local deterministic stand-in, so
the whole pipeline can be tested without a key or network. The stub is only
for plumbing tests; results in the paper come from the real API.
"""

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request

from faultd import client as faultd
from validity import tracing

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
PRICE_IN_PER_M = 0.042  # USD per million input tokens; output is free (docs.typesafe.ai/models)
MAX_ATTEMPTS = 3
INVALID_KEY = "ts-invalid-key-for-fault-test"

RETRYABLE_HTTP = {429, 500, 502, 503, 504}
SPENT_USD = 0.0  # running total for this process, for the spend cap


class JevCallError(Exception):
    def __init__(self, error_type, message, status=None):
        super().__init__(message)
        self.error_type = error_type
        self.status = status


def model():
    return os.environ.get("JEV_MODEL", "jev-latest")


def _http_error(code, reason):
    return urllib.error.HTTPError(TYPESAFE_URL, code, reason, {},
                                  io.BytesIO(json.dumps({"error": reason}).encode()))


# ------------------------------------------------------------------ backends

def _real_backend(payload, key):
    req = urllib.request.Request(TYPESAFE_URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    timeout = float(os.environ.get("JEV_TIMEOUT_S", "30"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_stub_tickets = None


def _stub_backend(payload, key):
    """Answers like Jev would if it followed the policy, with a few seeded
    mistakes so a clean run is not a perfect score."""
    from agents.policy import ACTIONS, resolve
    global _stub_tickets
    time.sleep(float(os.environ.get("JEV_STUB_LATENCY_S", "0")))
    if key == INVALID_KEY or not key:
        raise _http_error(401, "Unauthorized")
    if _stub_tickets is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data", "tickets.jsonl")
        _stub_tickets = {t["text"]: t for t in map(json.loads, open(path))}
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


def _backend():
    return _stub_backend if os.environ.get("JEV_BACKEND") == "stub" else _real_backend


# ------------------------------------------------------------------ one attempt

def _attempt(payload, fault, target):
    key = os.environ.get("TYPESAFE_API_KEY", "stub-key" if os.environ.get("JEV_BACKEND") == "stub" else "")
    kind = fault and fault["kind"]
    params = (fault or {}).get("params") or {}
    if kind == "wrong_key":
        key = INVALID_KEY  # sent for real: the 401 comes back from the API
    if kind == "down":
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    if kind in ("http_503", "http_429", "http_401"):
        code = {"http_503": 503, "http_429": 429, "http_401": 401}[kind]
        raise _http_error(code, {503: "Service Unavailable", 429: "Too Many Requests",
                                 401: "Unauthorized"}[code])
    if kind == "timeout":
        time.sleep(float(params.get("delay_s", 0.5)))
        raise TimeoutError("The read operation timed out")
    if kind == "slow":
        time.sleep(float(params.get("delay_s", 1.0)))
    raw = _backend()(payload, key)
    if kind == "malformed":
        raw = raw[: len(raw) // 2]
    return raw


def _mutate(body, kind, qname):
    a = body.get("answers", {}).get(qname)
    if kind == "empty":
        body["answers"] = {}
    elif kind == "missing_confidence" and isinstance(a, dict):
        a.pop("confidence", None)
    elif kind == "out_of_range" and isinstance(a, dict) and "choice" in a:
        a["choice"] = "7"
    elif kind == "flip" and isinstance(a, dict) and "noul" in a:
        a["noul"] = 1.0 - float(a["noul"])
    return body


def _validate(op, ans, criteria):
    """Strict schema check of one answer. Raises ValueError on a bad answer."""
    if not isinstance(ans, dict):
        raise ValueError("missing answer")
    if op == "choice":
        if str(ans.get("choice")) not in criteria:
            raise ValueError(f"choice out of range: {ans.get('choice')!r}")
        if not isinstance(ans.get("confidence"), (int, float)):
            raise ValueError("missing confidence")
    else:
        v = ans.get("noul")
        if not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
            raise ValueError(f"bad noul value: {v!r}")


# ------------------------------------------------------------------ public

def ask(state, question_name, question, *, role, item, call=0):
    """Ask Jev one typed question. Returns (answer dict, meta). Raises
    JevCallError when the call fails after retries or the answer is invalid."""
    target = "jev" if role == "decision" else "judge"
    op = question["type"]
    payload = json.dumps({"state": state, "model": model(), "questions": {question_name: question}}).encode()
    backoff = float(os.environ.get("JEV_BACKOFF_S", "0.5"))
    with tracing.tracer().start_as_current_span("jev.call") as span:
        span.set_attribute("gen_ai.system", "typesafe")
        span.set_attribute("gen_ai.request.model", model())
        span.set_attribute("gen_ai.operation.name", op)
        span.set_attribute("eval.role", role)
        span.set_attribute("eval.item_index", item)
        start = time.perf_counter()
        err = None
        for attempt in range(MAX_ATTEMPTS):
            span.set_attribute("jev.attempts", attempt + 1)
            fault = faultd.check(target, item, call * 10 + attempt)
            try:
                raw = _attempt(payload, fault, target)
                span.set_attribute("http.response.status_code", 200)
                break
            except urllib.error.HTTPError as e:
                err = JevCallError(f"http_{e.code}", f"HTTP {e.code}", e.code)
                span.set_attribute("http.response.status_code", e.code)
                retry = e.code in RETRYABLE_HTTP
            except TimeoutError as e:
                err, retry = JevCallError("timeout", str(e)), True
            except urllib.error.URLError as e:
                err, retry = JevCallError("connection_error", str(e.reason)), True
            span.add_event("jev.attempt_failed", {"error.type": err.error_type, "attempt": attempt + 1})
            if not retry or attempt == MAX_ATTEMPTS - 1:
                return _fail(span, err, start)
            time.sleep(backoff * (2 ** attempt))
        try:
            body = _mutate(json.loads(raw), fault and fault["kind"], question_name)
            ans = body.get("answers", {}).get(question_name)
            _validate(op, ans, question.get("criteria", {}))
        except (ValueError, json.JSONDecodeError) as e:
            span.set_attribute("jev.parse_ok", False)
            return _fail(span, JevCallError("parse_error", str(e)), start)
        span.set_attribute("jev.parse_ok", True)
        usage = body.get("usage") or {}
        tokens = int(usage.get("input_tokens", 0))
        span.set_attribute("gen_ai.usage.input_tokens", tokens)
        latency = time.perf_counter() - start
        span.set_attribute("jev.latency_s", latency)
        cost = tokens * PRICE_IN_PER_M / 1e6
        global SPENT_USD
        SPENT_USD += cost
        return ans, {"latency_s": latency, "input_tokens": tokens, "cost_usd": cost}


def _fail(span, err, start):
    from opentelemetry.trace import Status, StatusCode
    span.set_attribute("error.type", err.error_type)
    span.set_attribute("jev.latency_s", time.perf_counter() - start)
    span.set_status(Status(StatusCode.ERROR, str(err)))
    raise err
