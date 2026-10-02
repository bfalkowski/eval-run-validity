"""Build the fixed dataset: 60 support tickets, the orders fixture they refer
to, and each ticket's expected action under the v1 policy.

Run once; the outputs are committed and checksummed. Deterministic (seed 7).

  python data/make_data.py
"""

import hashlib
import json
import os
import random
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.policy import TODAY, resolve  # noqa: E402

HERE = os.path.dirname(__file__)
rng = random.Random(7)

NAMES = ["Alice Chen", "Marcus Webb", "Priya Nair", "Diego Ramirez", "Hannah Okafor", "Tom Lindqvist",
         "Grace Liu", "Omar Haddad", "Sofia Rossi", "Ben Carter", "Mei Tanaka", "Luca Moretti",
         "Ada Nwosu", "Kenji Sato", "Nora Byrne", "Ravi Menon", "Elena Petrova", "Sam Whitaker",
         "Iris Nakamura", "Jonah Feld", "Leila Karimi", "Owen Price", "Zara Ali", "Felix Braun",
         "Maya Cohen", "Noah Grant", "Chloe Martin", "Arjun Rao", "Lena Fischer", "Theo Laurent"]
PRODUCTS = {"KB-100": "mechanical keyboard", "MS-210": "wireless mouse", "HD-330": "noise-cancelling headphones",
            "MN-450": "27-inch monitor", "DK-500": "standing desk", "CH-610": "office chair",
            "LP-720": "laptop stand", "WB-830": "webcam", "SP-940": "bluetooth speaker", "TB-050": "drawing tablet"}
STOCK = {"KB-100": 12, "MS-210": 40, "HD-330": 0, "MN-450": 3, "DK-500": 0,
         "CH-610": 7, "LP-720": 25, "WB-830": 0, "SP-940": 9, "TB-050": 2}
PRICE = {"KB-100": 129.0, "MS-210": 39.0, "HD-330": 299.0, "MN-450": 389.0, "DK-500": 649.0,
         "CH-610": 549.0, "LP-720": 59.0, "WB-830": 89.0, "SP-940": 119.0, "TB-050": 899.0}

TEMPLATES = {
    "damaged": [
        "Hi, my {p} (order #{id}) showed up with a cracked casing. Can you sort this out?",
        "Order #{id}: the {p} arrived broken. The box was crushed. What can you do?",
        "The {p} from order #{id} doesn't work out of the box, it looks damaged in shipping.",
    ],
    "wrong_item": [
        "I ordered a {p} (order #{id}) but you sent me something completely different.",
        "Wrong item in order #{id}. I was expecting a {p}.",
        "Order #{id} contained the wrong product, not the {p} I paid for.",
    ],
    "not_arrived": [
        "My order #{id} ({p}) still hasn't arrived and it's been weeks.",
        "Where is order #{id}? The {p} never showed up.",
        "I never received my {p}, order #{id}. Tracking hasn't moved in ages.",
    ],
    "where_is_it": [
        "Hi, just checking on order #{id}, when will my {p} ship?",
        "Can you tell me the status of order #{id}? Excited for the {p}.",
        "Any update on order #{id}? Wondering when the {p} will get here.",
    ],
    "changed_mind": [
        "I got my {p} (order #{id}) but I don't need it anymore. Can I send it back?",
        "Order #{id}: the {p} is fine, I just changed my mind. How do I return it?",
        "I'd like to return the {p} from order #{id}, it's not what I want after all.",
    ],
}
ISSUES = ["damaged"] * 14 + ["wrong_item"] * 10 + ["not_arrived"] * 16 + ["where_is_it"] * 10 + ["changed_mind"] * 10


def d(days):
    return (TODAY - timedelta(days=days)).isoformat()


def make_order(oid, issue):
    sku = rng.choice(list(PRODUCTS))
    o = {"id": oid, "customer": rng.choice(NAMES), "sku": sku, "product": PRODUCTS[sku],
         "total": PRICE[sku], "status": None, "shipped_on": None, "delivered_on": None}
    if issue in ("damaged", "wrong_item", "changed_mind"):
        age = rng.choice([3, 6, 10, 15, 21, 27, 36, 44, 58]) if issue != "changed_mind" else rng.randint(2, 25)
        o.update(status="Delivered", shipped_on=d(age + 4), delivered_on=d(age))
    elif issue == "not_arrived":
        if rng.random() < 0.5:
            o.update(status="Lost", shipped_on=d(rng.randint(16, 35)))
        else:
            o.update(status="Shipped", shipped_on=d(rng.randint(15, 30)))
    else:  # where_is_it
        if rng.random() < 0.5:
            o.update(status="Processing")
        else:
            o.update(status="Shipped", shipped_on=d(rng.randint(2, 10)))
    return o


def main():
    issues = ISSUES[:]
    rng.shuffle(issues)
    orders, tickets = {}, []
    for i, issue in enumerate(issues):
        oid = 1001 + i
        o = make_order(oid, issue)
        orders[str(oid)] = o
        text = rng.choice(TEMPLATES[issue]).format(id=oid, p=o["product"])
        tickets.append({"ticket_id": f"T{i + 1:03d}", "order_id": oid, "customer": o["customer"],
                        "text": text, "issue": issue,
                        "expected": resolve(issue, o, STOCK[o["sku"]])})
    fixture = {"orders": orders, "stock": STOCK}
    with open(os.path.join(HERE, "orders.json"), "w") as f:
        json.dump(fixture, f, indent=1)
    with open(os.path.join(HERE, "tickets.jsonl"), "w") as f:
        for t in tickets:
            f.write(json.dumps(t) + "\n")
    sums = {name: hashlib.sha256(open(os.path.join(HERE, name), "rb").read()).hexdigest()
            for name in ("orders.json", "tickets.jsonl")}
    with open(os.path.join(HERE, "CHECKSUMS.json"), "w") as f:
        json.dump(sums, f, indent=1)
    from collections import Counter
    print(Counter(t["expected"] for t in tickets), Counter(t["issue"] for t in tickets))


if __name__ == "__main__":
    main()
