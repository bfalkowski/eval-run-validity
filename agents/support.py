"""The support agent and the judge. Jev makes every decision.

Agent: look up the order and stock (tools), ask Jev which action the policy
calls for (a typed `choice` question), take that action (tool), reply.

Judge: ask Jev a yes/no (`noul`) question, "does this resolution follow the
policy?", twice per ticket.

ERROR_POLICY controls what happens when a Jev call fails:
  loud    the failure is raised; the item is recorded as errored
  silent  the code falls back to a default (agent: reply only; judge: "fail"),
          which is the common real-world bug: the item looks like an ordinary
          wrong answer
"""

from agents import jev
from agents.orders import ToolError

DECISION_INSTRUCTIONS = "Which action does the support policy call for on this ticket?"
EDITED_DECISION_INSTRUCTIONS = "Pick the action that best keeps this customer happy."
JUDGE_INSTRUCTIONS = "Does the agent's resolution follow the support policy for this ticket and order?"

CRITERIA = {
    "0": "refund: refund the order",
    "1": "reship: send a replacement",
    "2": "escalate: hand the ticket to a supervisor",
    "3": "reply: reply to the customer and take no other action",
}
KEY_TO_ACTION = {k: v.split(":")[0] for k, v in CRITERIA.items()}
SILENT_DEFAULT_ACTION = "reply"


class ItemError(Exception):
    def __init__(self, stage, error_type):
        super().__init__(f"{stage}: {error_type}")
        self.stage, self.error_type = stage, error_type


def agent_state(cfg, ticket, order, stock):
    st = {"policy": cfg["policy_text"], "today": "2026-10-01", "ticket": ticket["text"], "order": order}
    if "stock" in cfg["context_fields"]:
        st["stock"] = stock
    return st


def handle(ticket, item, svc, cfg):
    """Work one ticket. Returns (action, order, stock). Raises ItemError in loud mode."""
    silent = cfg["error_policy"] == "silent"
    try:
        order = svc.call("lookup_order", item, order_id=ticket["order_id"])
        stock = svc.call("check_stock", item, sku=order["sku"])
    except ToolError as e:
        if not silent:
            raise ItemError("tool", e.error_type)
        try:
            svc.call("escalate", item, ticket_id=ticket["ticket_id"])
        except ToolError:
            pass
        return "escalate", None, None

    question = {"type": "choice", "instructions": cfg["decision_instructions"], "criteria": CRITERIA}
    try:
        ans, _ = jev.ask(agent_state(cfg, ticket, order, stock), "action", question, role="decision", item=item)
        action = KEY_TO_ACTION[str(ans["choice"])]
    except jev.JevCallError as e:
        if not silent:
            raise ItemError("decision", e.error_type)
        action = SILENT_DEFAULT_ACTION

    try:
        if action == "refund":
            svc.call("refund", item, order_id=ticket["order_id"])
        elif action == "reship":
            svc.call("reship", item, order_id=ticket["order_id"])
        elif action == "escalate":
            svc.call("escalate", item, ticket_id=ticket["ticket_id"])
        svc.call("reply", item, ticket_id=ticket["ticket_id"], text=f"Resolution: {action}")
    except ToolError as e:
        if not silent:
            raise ItemError("tool", e.error_type)
    return action, order, stock


def judge(ticket, item, cfg, order, stock, action, repeats=2):
    """Returns a list of votes (True pass, False fail, None errored)."""
    state = {"policy": cfg["policy_text"], "today": "2026-10-01", "ticket": ticket["text"],
             "order": order, "stock": stock, "agent_resolution": action}
    q = {"type": "noul", "instructions": JUDGE_INSTRUCTIONS}
    votes = []
    for r in range(repeats):
        try:
            ans, _ = jev.ask(state, "follows_policy", q, role="judge", item=item, call=r + 1)
            votes.append(bool(ans["noul"] >= 0.5))
        except jev.JevCallError:
            votes.append(False if cfg["error_policy"] == "silent" else None)
    return votes
