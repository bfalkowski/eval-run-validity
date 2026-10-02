"""The local orders service and the tools the support agent uses on it.

Deterministic and in-process. Each tool call is one span; before acting, the
tool asks faultd whether to fail (target "tool.<name>").
"""

import copy

from opentelemetry.trace import Status, StatusCode

from faultd import client as faultd
from validity import tracing


class ToolError(Exception):
    def __init__(self, error_type, message=""):
        super().__init__(message or error_type)
        self.error_type = error_type


class OrdersService:
    def __init__(self, fixture):
        self.orders = copy.deepcopy(fixture["orders"])
        self.stock = dict(fixture["stock"])
        self.effects = []  # side effects since the last reset

    def reset(self):
        self.effects = []

    # each tool: (name, item, args...) -> result
    def call(self, name, item, call=0, **args):
        with tracing.tracer().start_as_current_span(f"tool.{name}") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("eval.item_index", item)
            for k, v in args.items():
                span.set_attribute(f"tool.arg.{k}", v)
            try:
                fault = faultd.check(f"tool.{name}", item, call)
                if fault and fault["kind"] == "tool_timeout":
                    raise ToolError("timeout", "tool call timed out")
                if fault and fault["kind"] == "tool_500":
                    raise ToolError("http_500", "orders service returned 500")
                result = getattr(self, f"_{name}")(**args)
                span.set_attribute("tool.status", "ok")
                return result
            except ToolError as e:
                span.set_attribute("tool.status", "error")
                span.set_attribute("error.type", e.error_type)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    def _lookup_order(self, order_id):
        o = self.orders.get(str(order_id))
        if o is None:
            raise ToolError("not_found", f"order {order_id} not found")
        return dict(o)

    def _check_stock(self, sku):
        return self.stock.get(sku, 0)

    def _refund(self, order_id):
        self.effects.append({"action": "refund", "order_id": order_id})

    def _reship(self, order_id):
        self.effects.append({"action": "reship", "order_id": order_id})

    def _escalate(self, ticket_id):
        self.effects.append({"action": "escalate", "ticket_id": ticket_id})

    def _reply(self, ticket_id, text):
        self.effects.append({"action": "reply", "ticket_id": ticket_id})
