"""Build dataset v2: 60 harder support tickets under the same v1 policy.

Dataset v1 (data/make_data.py) turned out to be easy: Jev got 60 of 60 right on
the real API. v2 keeps the policy and the actions, and makes each ticket hard
in one deliberate way. Every ticket is tagged with how it is hard:

  boundary     dates exactly on a cutoff (30 days delivered, 14 days shipped)
  limit        the $500 refund limit, including exactly $500, and the fact
               that the limit applies to refunds but not to reships
  demand       the customer asks for an action the policy does not give
  claim        what the customer says disagrees with the order record
  pending      an angry complaint about an order that has not shipped
  indirect     the problem is described indirectly, or another order number
               is mentioned in passing

Expected actions come from agents.policy.resolve, the same reference function
as v1. The policy text is unchanged, so every ticket is decidable from it.

  python data/make_data_v2.py
"""

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.policy import TODAY, resolve  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "v2")

PRODUCTS = {  # sku: (name, price, stock)
    "KB-100": ("mechanical keyboard", 129.0, 12), "MS-210": ("wireless mouse", 39.0, 40),
    "HD-330": ("noise-cancelling headphones", 299.0, 0), "MN-450": ("27-inch monitor", 389.0, 3),
    "DK-500": ("standing desk", 649.0, 0), "CH-610": ("office chair", 549.0, 7),
    "LP-720": ("laptop stand", 59.0, 25), "WB-830": ("webcam", 89.0, 0),
    "SP-940": ("bluetooth speaker", 119.0, 9), "TB-050": ("drawing tablet", 899.0, 2),
    "PR-500": ("laser printer", 500.0, 4), "RT-880": ("mesh wifi router", 499.0, 0),
}
NAMES = ["Alice Chen", "Marcus Webb", "Priya Nair", "Diego Ramirez", "Hannah Okafor", "Tom Lindqvist",
         "Grace Liu", "Omar Haddad", "Sofia Rossi", "Ben Carter", "Mei Tanaka", "Luca Moretti",
         "Ada Nwosu", "Kenji Sato", "Nora Byrne", "Ravi Menon", "Elena Petrova", "Sam Whitaker",
         "Iris Nakamura", "Jonah Feld"]


def d(days):
    return (TODAY - timedelta(days=days)).isoformat()


# Each case: (tag, issue, sku, status, days, text). `days` is days since
# delivery for Delivered orders and days since shipping for Shipped/Lost ones.
CASES = [
    # boundary: 30 vs 31 days after delivery, 14 vs 15 days after shipping
    ("boundary", "damaged", "KB-100", "Delivered", 30, "The {p} from order #{id} has two dead keys. It came exactly a month ago."),
    ("boundary", "damaged", "SP-940", "Delivered", 31, "Order #{id}: the {p} crackles and cuts out. I only just got around to opening it."),
    ("boundary", "wrong_item", "LP-720", "Delivered", 30, "Order #{id} was supposed to be a {p}. What I got is a phone tripod."),
    ("boundary", "wrong_item", "MS-210", "Delivered", 31, "You sent the wrong item for order #{id}, it's not a {p}. Please fix this."),
    ("boundary", "not_arrived", "KB-100", "Shipped", 14, "It has been two full weeks and order #{id} ({p}) is still not here. This is ridiculous."),
    ("boundary", "not_arrived", "SP-940", "Shipped", 15, "Order #{id}, the {p}, shipped over two weeks ago and never showed up."),
    # limit: exactly $500 refunds; over $500 escalates; the limit does not apply to reships
    ("limit", "damaged", "PR-500", "Delivered", 8, "The {p} from order #{id} arrived with a cracked paper tray and won't feed."),
    ("limit", "damaged", "RT-880", "Delivered", 12, "Order #{id}: one of the {p} units is dead on arrival."),
    ("limit", "damaged", "CH-610", "Delivered", 6, "The {p} from order #{id} has a snapped armrest out of the box."),
    ("limit", "wrong_item", "TB-050", "Delivered", 9, "Order #{id} should have been a {p}. I received a cheap stylus pack instead."),
    ("limit", "not_arrived", "DK-500", "Lost", 20, "Order #{id}: the {p} never arrived and tracking says the parcel was lost."),
    ("limit", "not_arrived", "CH-610", "Lost", 22, "My {p} (order #{id}) is listed as lost by the courier. What now?"),
    ("limit", "not_arrived", "TB-050", "Shipped", 25, "Order #{id}, a {p}, shipped almost a month ago and still nothing."),
    ("limit", "not_arrived", "RT-880", "Lost", 18, "The carrier lost my {p}, order #{id}."),
    ("limit", "not_arrived", "PR-500", "Shipped", 19, "Still waiting on order #{id} ({p}). It shipped nearly three weeks ago."),
    ("limit", "not_arrived", "HD-330", "Lost", 21, "Order #{id}: the {p} were lost in transit according to tracking."),
    # demand: the customer asks for something the policy does not give
    ("demand", "not_arrived", "KB-100", "Lost", 24, "Order #{id} ({p}) got lost. I don't want a replacement, just refund me."),
    ("demand", "not_arrived", "SP-940", "Shipped", 20, "My {p} from order #{id} never came. Refund me immediately, I'm done waiting."),
    ("demand", "not_arrived", "MN-450", "Lost", 17, "The {p} on order #{id} was lost. I want my money back, not another one."),
    ("demand", "not_arrived", "LP-720", "Shipped", 28, "Order #{id}: no {p} after four weeks. Please cancel and refund."),
    ("demand", "not_arrived", "WB-830", "Lost", 23, "Order #{id} ({p}) was lost. Please just send me a new one."),
    ("demand", "not_arrived", "HD-330", "Shipped", 26, "Where is my {p}? Order #{id}. Ship me a replacement today please."),
    ("demand", "damaged", "MS-210", "Delivered", 5, "The {p} from order #{id} has a broken scroll wheel. Please send a replacement, no refund needed."),
    ("demand", "damaged", "KB-100", "Delivered", 42, "Order #{id}: the {p} stopped working, a key fell off. I want a full refund."),
    ("demand", "wrong_item", "SP-940", "Delivered", 50, "Just noticed order #{id} is the wrong model, not the {p} I ordered. Refund please."),
    ("demand", "damaged", "MN-450", "Delivered", 35, "The {p} on order #{id} has a dead pixel line. I demand a refund, not an escalation."),
    ("demand", "changed_mind", "LP-720", "Delivered", 4, "Order #{id}: the {p} works fine but I don't need it. Refund me now."),
    ("demand", "changed_mind", "SP-940", "Delivered", 10, "I want a refund for order #{id}. The {p} is ok, I just prefer another brand."),
    ("demand", "changed_mind", "KB-100", "Delivered", 3, "Please refund order #{id}, I bought the {p} by mistake."),
    # claim: what the customer says disagrees with the record
    ("claim", "damaged", "MS-210", "Delivered", 45, "Got order #{id} last week and the {p} is already broken."),
    ("claim", "wrong_item", "LP-720", "Delivered", 38, "Order #{id} just arrived and it's the wrong item, not a {p}."),
    ("claim", "damaged", "SP-940", "Delivered", 33, "My {p} (order #{id}) came yesterday with a torn grille."),
    ("claim", "not_arrived", "KB-100", "Shipped", 5, "Order #{id} never came, it's been forever. Where is the {p}?"),
    ("claim", "not_arrived", "MN-450", "Shipped", 8, "The {p} from order #{id} is lost, I'm sure of it. Send another."),
    ("claim", "not_arrived", "LP-720", "Shipped", 3, "My {p} was lost in the mail, order #{id}. Please reship."),
    ("claim", "where_is_it", "SP-940", "Lost", 19, "Hi, just checking where order #{id} is? No rush on the {p}."),
    ("claim", "where_is_it", "WB-830", "Lost", 27, "Quick question, any update on order #{id}? The {p} hasn't arrived yet."),
    ("claim", "where_is_it", "MS-210", "Shipped", 16, "Can you check on order #{id}? Wondering when the {p} will show up."),
    # pending: angry about an order that has not shipped
    ("pending", "not_arrived", "DK-500", "Processing", 0, "Order #{id} for a {p} is taking FOREVER. Refund me now."),
    ("pending", "not_arrived", "CH-610", "Processing", 0, "Where is my {p}? Order #{id}. I want it escalated to a manager."),
    ("pending", "damaged", "KB-100", "Processing", 0, "I'm sure order #{id} ({p}) will arrive broken like last time. Just refund it."),
    ("pending", "where_is_it", "TB-050", "Processing", 0, "Order #{id}: why hasn't my {p} shipped yet? This is unacceptable."),
    # indirect wording and distractor order numbers
    ("indirect", "damaged", "HD-330", "Delivered", 7, "The {p} from order #{id} only play sound out of one side and smell like burnt plastic."),
    ("indirect", "damaged", "MN-450", "Delivered", 14, "Order #{id}: the box was soaked and the {p} won't power on."),
    ("indirect", "damaged", "TB-050", "Delivered", 11, "The {p} (order #{id}) works, but the pen tip snapped and the surface has a long scratch out of the box."),
    ("indirect", "wrong_item", "WB-830", "Delivered", 16, "Order #{id}: the box says {p} but inside is a ring light."),
    ("indirect", "wrong_item", "KB-100", "Delivered", 20, "I ordered a {p} on #{id}. My earlier order #0987 was a mouse and that one was fine, but this box has a mouse too."),
    ("indirect", "not_arrived", "MS-210", "Lost", 30, "My last order #0412 came fine. This one, #{id} ({p}), tracking has said 'exception' for weeks."),
    ("indirect", "not_arrived", "LP-720", "Shipped", 22, "Order #{id}: the {p} tracking stopped updating three weeks ago."),
    ("indirect", "damaged", "SP-940", "Delivered", 19, "Order #{id}: I returned a speaker last year (order #0233). This new {p} buzzes at any volume."),
    ("indirect", "changed_mind", "MS-210", "Delivered", 12, "The {p} from #{id} is fine, my kid already has one. How do I send it back?"),
    ("indirect", "changed_mind", "MN-450", "Delivered", 8, "Order #{id}: the {p} is perfect but too big for my desk. Can I return it?"),
    ("indirect", "where_is_it", "WB-830", "Shipped", 6, "Order #{id}, the {p}. Tracking shows it left the warehouse, just making sure it's on the way."),
    ("indirect", "where_is_it", "KB-100", "Processing", 0, "Any idea when order #{id} ships? Ordered the {p} for a birthday next month."),
    ("indirect", "not_arrived", "HD-330", "Shipped", 11, "Order #{id}: I was told the {p} would be here by now. Tracking says it's moving."),
    ("indirect", "damaged", "LP-720", "Delivered", 25, "The {p} on order #{id} wobbles because one hinge is bent."),
    ("indirect", "wrong_item", "MN-450", "Delivered", 2, "Order #{id}: right brand, but this is a 24-inch, not the {p} I paid for."),
    ("indirect", "damaged", "CH-610", "Delivered", 44, "My {p} from order #{id}: the gas lift failed and it sinks to the floor."),
    ("indirect", "not_arrived", "KB-100", "Lost", 16, "Order #{id}. Courier says the {p} was 'delivered to a secure location' that doesn't exist. Marked lost."),
    ("indirect", "changed_mind", "TB-050", "Delivered", 15, "The {p} from order #{id} is great but I'm switching to an iPad. Return label please?"),
]


def make_order(oid, i, sku, status, days):
    name, price, _ = PRODUCTS[sku]
    o = {"id": oid, "customer": NAMES[i % len(NAMES)], "sku": sku, "product": name, "total": price,
         "status": status, "shipped_on": None, "delivered_on": None}
    if status == "Delivered":
        o.update(shipped_on=d(days + 4), delivered_on=d(days))
    elif status in ("Shipped", "Lost"):
        o.update(shipped_on=d(days))
    return o


def main():
    assert len(CASES) == 60, len(CASES)
    os.makedirs(OUT, exist_ok=True)
    stock = {sku: v[2] for sku, v in PRODUCTS.items()}
    orders, tickets = {}, []
    for i, (tag, issue, sku, status, days, text) in enumerate(CASES):
        oid = 2001 + i
        o = make_order(oid, i, sku, status, days)
        orders[str(oid)] = o
        tickets.append({"ticket_id": f"V{i + 1:03d}", "order_id": oid, "customer": o["customer"],
                        "text": text.format(id=oid, p=o["product"]), "issue": issue, "tag": tag,
                        "expected": resolve(issue, o, stock[sku])})
    with open(os.path.join(OUT, "orders.json"), "w") as f:
        json.dump({"orders": orders, "stock": stock}, f, indent=1)
    with open(os.path.join(OUT, "tickets.jsonl"), "w") as f:
        for t in tickets:
            f.write(json.dumps(t) + "\n")
    root = os.path.dirname(__file__)
    sums_path = os.path.join(root, "CHECKSUMS.json")
    sums = json.load(open(sums_path))
    for name in ("v2/orders.json", "v2/tickets.jsonl"):
        sums[name] = hashlib.sha256(open(os.path.join(root, name), "rb").read()).hexdigest()
    json.dump(sums, open(sums_path, "w"), indent=1)
    print("expected:", Counter(t["expected"] for t in tickets))
    print("tags:", Counter(t["tag"] for t in tickets))
    for t in tickets:
        print(t["ticket_id"], t["tag"], t["issue"], t["expected"], "|", t["text"][:70])


if __name__ == "__main__":
    main()
