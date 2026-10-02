"""The support policy, as text for Jev and as a reference function.

The reference function is the ground truth for what the support agent should
do. It is used to build the expected answers and, during a run, to check that
each ticket's expected answer still matches the order data it was written
against (a stale fixture breaks that).
"""

from datetime import date

TODAY = date(2026, 10, 1)
ACTIONS = ["refund", "reship", "escalate", "reply"]
REFUND_LIMIT = 500.0

POLICY_V1 = """Support policy. Today is 2026-10-01.
1. If the order has not shipped yet (status Processing), or it shipped 14 days ago or less and has not been delivered, reply with the order status. Take no other action.
2. Damaged or wrong item: if the order was delivered within the last 30 days, refund it. If it was delivered more than 30 days ago, escalate.
3. Never arrived: if the order is Lost, or it shipped more than 14 days ago and has not been delivered, reship it if the item is in stock, otherwise refund it.
4. Any refund over $500 must be escalated instead of refunded.
5. The customer changed their mind about a delivered order: reply with return instructions. Take no other action."""

# An older policy that predates the refund limit (rule 4). It is what the code
# falls back to when POLICY_VERSION is not set, which is a realistic way for a
# stale prompt to sneak into a run.
POLICY_V0 = "\n".join(l for l in POLICY_V1.splitlines() if not l.startswith("4."))

POLICIES = {"v0": POLICY_V0, "v1": POLICY_V1}


def days_ago(d):
    return (TODAY - date.fromisoformat(d)).days if d else None


def resolve(issue, order, stock, refund_limit=True):
    """The correct action for a ticket under the v1 policy."""
    if order is None:
        return None
    status = order["status"]

    def refund():
        return "escalate" if refund_limit and order["total"] > REFUND_LIMIT else "refund"

    if status == "Processing":
        return "reply"
    if issue in ("damaged", "wrong_item"):
        if status != "Delivered":
            return "reply"
        return refund() if days_ago(order["delivered_on"]) <= 30 else "escalate"
    if issue in ("not_arrived", "where_is_it"):
        if status == "Delivered":
            return "reply"
        if status == "Lost" or (status == "Shipped" and days_ago(order["shipped_on"]) > 14):
            return "reship" if stock > 0 else refund()
        return "reply"
    if issue == "changed_mind":
        return "reply"
    raise ValueError(f"unknown issue {issue}")
