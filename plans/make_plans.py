"""Write the fault-plan catalog: one JSON file per (fault, dose).

  python plans/make_plans.py
"""
import json
import os

HERE = os.path.dirname(__file__)
LOW, HIGH = 0.05, 0.25

CATALOG = [
    # name, category, kind, target, extra
    ("jev_503", "infrastructure", "http_503", "jev", {}),
    ("jev_429", "infrastructure", "http_429", "jev", {}),
    ("jev_timeout", "infrastructure", "timeout", "jev", {"params": {"delay_s": 0.2}}),
    ("jev_down", "infrastructure", "down", "jev", {}),
    ("jev_malformed", "infrastructure", "malformed", "jev", {}),
    ("jev_out_of_range", "infrastructure", "out_of_range", "jev", {}),
    ("tool_500", "infrastructure", "tool_500", "tool.*", {}),
    ("judge_503", "infrastructure", "http_503", "judge", {}),
    ("judge_malformed", "judge_failure", "malformed", "judge", {}),
    ("judge_flip", "judge_failure", "flip", "judge", {}),
    ("stale_fixture", "task_defect", "stale_fixture", "fixtures", {}),
    ("removed_record", "task_defect", "removed_record", "fixtures", {}),
    ("duplicate_item", "task_defect", "duplicate_item", "dataset", {}),
    ("scorer_off_by_one", "harness_bug", "off_by_one", "scorer", {"start_item": 0}),
    ("no_reset", "harness_bug", "no_reset", "harness", {}),
]
RUN_LEVEL = [  # applied once per run, no dose
    ("prompt_edit", "config_drift", "prompt_edit", "config"),
    ("context_strip", "config_drift", "context_strip", "config"),
    ("template_edit", "config_drift", "template_edit", "config"),
]
WINDOWS = [  # whole-window faults: a share of the run is affected, the rest is fine
    ("jev_down_mid", "infrastructure", {"kind": "down", "target": "jev", "start_item": 20, "end_item": 39}),
    ("jev_down_all", "infrastructure", {"kind": "down", "target": "jev"}),
    ("jev_401_mid", "infrastructure", {"kind": "http_401", "target": "jev", "start_item": 30}),
    ("jev_flap", "infrastructure", {"kind": "http_503", "target": ["jev", "judge"], "start_item": 10, "end_item": 17}),
    ("jev_slow", "infrastructure", {"kind": "slow", "target": "jev", "rate": 0.25, "start_item": 0,
                                     "params": {"delay_s": 0.3}}),
    ("scorer_off_by_one_all", "harness_bug", {"kind": "off_by_one", "target": "scorer", "rate": 1.0}),
    ("wrong_key_first20", "infrastructure", {"kind": "wrong_key", "target": ["jev", "judge"], "start_item": 0, "end_item": 19}),
    ("wrong_key_last20", "infrastructure", {"kind": "wrong_key", "target": ["jev", "judge"], "start_item": 40}),
]


def write(name, desc, category, faults):
    with open(os.path.join(HERE, f"{name}.json"), "w") as f:
        json.dump({"description": desc, "category": category, "faults": faults}, f, indent=1)


for name, cat, kind, target, extra in CATALOG:
    for dose, label in ((LOW, "05"), (HIGH, "25")):
        fault = {"kind": kind, "target": target, "rate": dose, "start_item": 0, "seed": 1, **extra}
        write(f"{name}_{label}", f"{kind} on {target}, {int(dose * 100)}% of calls", cat, [fault])
for name, cat, kind, target in RUN_LEVEL:
    write(name, f"{kind}, undeclared", cat, [{"kind": kind, "target": target}])
for name, cat, fault in WINDOWS:
    write(name, f"{fault['kind']} on {'+'.join(fault['target']) if isinstance(fault['target'], list) else fault['target']}, items {fault.get('start_item')}..{fault.get('end_item')}",
          cat, [{"seed": 1, **fault}])
print(len(os.listdir(HERE)) - 1, "plans")
