"""The datasets. Same policy and actions; v2 is the harder set used for the paper.

  v1  60 straightforward tickets (data/tickets.jsonl). Jev scored 60/60.
  v2  60 tickets, each hard in one deliberate way (data/v2/). See make_data_v2.py.
"""

import json
import os

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
ROOT = os.path.join(os.path.dirname(__file__), "..")
DATASETS = {"v1": ("tickets.jsonl", "orders.json"), "v2": ("v2/tickets.jsonl", "v2/orders.json")}
DEFAULT = "v2"


def tickets_path(version):
    return os.path.join(DATA, DATASETS[version][0])


def load(version):
    tickets = [json.loads(line) for line in open(tickets_path(version))]
    fixture = json.load(open(os.path.join(DATA, DATASETS[version][1])))
    return tickets, fixture


def baseline_path(version):
    return os.path.join(ROOT, "baselines", f"manifest_{version}.json")
