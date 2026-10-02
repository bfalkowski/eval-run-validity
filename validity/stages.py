"""The validity gate as separate pipeline stages.

Each stage reads a run folder, prints what it found, appends a short Markdown
section to $GITHUB_STEP_SUMMARY when that is set, and exits non-zero when the
run must not go further. `.github/workflows/eval.yml` runs them as jobs.

  python -m validity.stages preflight   runs/<id>   # canaries, checksums (from preflight.json)
  python -m validity.stages fingerprint runs/<id>   # manifest against the baseline
  python -m validity.stages integrity   runs/<id>   # every planned item, once, from this run
  python -m validity.stages coverage    runs/<id>   # every item has a good model and judge call
  python -m validity.stages gate        runs/<id>   # attribution and the verdict
  python -m validity.stages report      runs/<id>   # valid-items score, exclusions, comparison

Exit codes: 0 pass, 1 fail. `fingerprint` fails on undeclared critical drift
and passes with a warning on environment drift. `gate` fails on an invalid
verdict and passes degraded runs, which `report` labels as such.
"""

import argparse
import json
import os
import sys
from collections import Counter

from validity import attribution, gate, manifest


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _baseline(run_dir, path):
    if path != "auto":
        return _load(path)
    from agents.datasets import baseline_path
    m = _load(os.path.join(run_dir, "manifest.json")) or {}
    return _load(baseline_path(m.get("dataset", "v1")))


def _summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")


def preflight(run_dir, _):
    pre = _load(os.path.join(run_dir, "preflight.json"))
    if pre is None:
        _summary(["### Preflight: FAIL", "No preflight result."])
        return False
    rows = [f"| {c['name']} | {'pass' if c['ok'] else '**fail**'} | {c['detail']} |" for c in pre["checks"]]
    _summary([f"### Preflight: {'pass' if pre['ok'] else 'FAIL'}", "| check | result | detail |", "|---|---|---|", *rows])
    return pre["ok"]


def fingerprint(run_dir, baseline):
    m = _load(os.path.join(run_dir, "manifest.json"))
    if m is None:
        _summary(["### Fingerprint: FAIL", "No manifest."])
        return False
    if baseline is None:
        _summary(["### Fingerprint: pass (no baseline to compare)"])
        return True
    d = manifest.drift(m, baseline)
    lines = [f"### Fingerprint: {'FAIL' if d['critical'] else 'warn' if d['warn'] else 'pass'}",
             f"Compared with baseline `{d['baseline']}`."]
    for k in d["critical"]:
        lines.append(f"- **{k}** changed and was not declared (`{baseline.get(k)}` to `{m.get(k)}`)")
    for k in d["warn"]:
        lines.append(f"- {k} differs (environment): `{baseline.get(k)}` to `{m.get(k)}`")
    if m.get("declared_changes"):
        lines.append(f"- declared changes: {', '.join(m['declared_changes'])}")
    _summary(lines)
    return not d["critical"]


def integrity(run_dir, _):
    m = _load(os.path.join(run_dir, "manifest.json")) or {}
    items = attribution.load_jsonl(os.path.join(run_dir, "items.jsonl"))
    idx = Counter(r["item_index"] for r in items)
    dup = sorted(i for i, n in idx.items() if n > 1)
    foreign = sorted({r.get("run_id") for r in items} - {m.get("run_id")})
    other_cfg = sorted({r.get("manifest_hash") for r in items} - {m.get("manifest_hash")})
    planned, done = m.get("planned_items", 0), len(idx)
    traces = os.path.exists(os.path.join(run_dir, "traces.jsonl"))
    problems = []
    if done < planned:
        problems.append(f"partial run: {done} of {planned} items finished")
    if dup:
        problems.append(f"{len(dup)} items appear more than once")
    if foreign or other_cfg:
        problems.append(f"records from another run or config: {foreign + other_cfg}")
    if not traces:
        problems.append("no traces")
    _summary([f"### Integrity: {'FAIL' if problems else 'pass'}",
              f"{done} of {planned} planned items, {len(items)} records."] + [f"- {p}" for p in problems])
    return not problems


def coverage(run_dir, _):
    items = attribution.load_jsonl(os.path.join(run_dir, "items.jsonl"))
    spans = attribution.spans_by_item(attribution.load_jsonl(os.path.join(run_dir, "traces.jsonl")))
    missed, missed_judge = [], []
    for r in items:
        ss = spans.get(r["item_index"], [])
        calls = [s for s in ss if s["name"] == "jev.call"]
        if not any(s["attributes"].get("eval.role") == "decision" and s["status"] != "ERROR" for s in calls):
            missed.append(r["ticket_id"])
        jud = [s for s in calls if s["attributes"].get("eval.role") == "judge"]
        if not jud or any(s["status"] == "ERROR" for s in jud):
            missed_judge.append(r["ticket_id"])
    lines = [f"### Coverage: {'FAIL' if missed else 'warn' if missed_judge else 'pass'}",
             f"{len(items) - len(missed)} of {len(items)} items have a successful model call; "
             f"{len(items) - len(missed_judge)} have every judge call succeed."]
    if missed:
        lines.append(f"- missed evals (no successful model call): {', '.join(missed[:20])}"
                     f"{' ...' if len(missed) > 20 else ''}")
    if missed_judge:
        lines.append(f"- judge calls failed on: {', '.join(missed_judge[:20])}{' ...' if len(missed_judge) > 20 else ''}")
    _summary(lines)
    return not missed


def run_gate(run_dir, baseline):
    v = gate.evaluate(run_dir, baseline)
    json.dump(v, open(os.path.join(run_dir, "verdict.json"), "w"), indent=1)
    lines = [f"### Gate: {v['verdict'].upper()}"] + [f"- {r}" for r in v["reasons"]]
    if v.get("categories"):
        lines.append(f"Causes: {', '.join(f'{k} {n}' for k, n in sorted(v['categories'].items()))}")
    _summary(lines)
    return v["verdict"] != "invalid"


def report(run_dir, _):
    v = _load(os.path.join(run_dir, "verdict.json")) or gate.evaluate(run_dir, None)
    m = _load(os.path.join(run_dir, "manifest.json")) or {}
    ref = _load(os.path.join(os.path.dirname(__file__), "..", "baselines", f"score_{m.get('dataset', 'v1')}.json"))
    s = v["score"]
    if v["verdict"] == "invalid":
        _summary(["### Report: no score", "The run is invalid, so no score is published."])
        return False
    lines = [f"### Report ({v['verdict']})",
             f"Score on valid items: **{s['valid_items']:.1%}** on {s['n_valid_items']} items "
             f"({s['excluded_rate']:.0%} excluded). Plain score for reference: {s['naive']:.1%}."]
    excl = [a for a in v["items"] if a["primary"] in attribution.EXCLUDED]
    if excl:
        lines.append("Excluded items: " + ", ".join(f"{a['ticket_id']} ({a['primary']})" for a in excl[:30]))
    if ref:
        lo, hi = ref["mean"] - 2 * ref["sd"], ref["mean"] + 2 * ref["sd"]
        x = s["valid_items"]
        call = "within the clean range" if lo <= x <= hi else "below the clean range: a regression" \
            if x < lo else "above the clean range: an improvement"
        lines.append(f"Compared with {ref['n']} clean baseline runs ({ref['mean']:.1%}, range {lo:.1%} to {hi:.1%}): {call}.")
    _summary(lines)
    return True


STAGES = {"preflight": preflight, "fingerprint": fingerprint, "integrity": integrity,
          "coverage": coverage, "gate": run_gate, "report": report}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=list(STAGES))
    ap.add_argument("run_dir")
    ap.add_argument("--baseline", default="auto")
    a = ap.parse_args()
    ok = STAGES[a.stage](a.run_dir, _baseline(a.run_dir, a.baseline))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
