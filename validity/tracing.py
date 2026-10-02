"""OpenTelemetry setup: every span is written to <run dir>/traces.jsonl as soon
as it ends, so a run that is killed part way still leaves its traces.

Span attributes follow the OpenTelemetry GenAI conventions where one exists
(gen_ai.*, error.type, http.response.status_code) plus a few for validity:
  eval.run_id, eval.item_index, eval.ticket_id, eval.role (decision | judge)
  jev.attempts, jev.parse_ok, tool.name, tool.status, eval.item.consistent
"""

import json

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class JsonlExporter(SpanExporter):
    def __init__(self, path):
        self.f = open(path, "a")

    def export(self, spans):
        for s in spans:
            self.f.write(json.dumps({
                "name": s.name,
                "trace_id": f"{s.context.trace_id:032x}",
                "span_id": f"{s.context.span_id:016x}",
                "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                "start": s.start_time, "end": s.end_time,
                "status": s.status.status_code.name,
                "status_description": s.status.description,
                "attributes": dict(s.attributes or {}),
                "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in s.events],
            }) + "\n")
        self.f.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.f.close()


_provider = None
_tracer = trace.NoOpTracer()


def setup(path, run_id):
    global _provider, _tracer
    _provider = TracerProvider(resource=Resource.create({"service.name": "eval-run-validity",
                                                         "eval.run_id": run_id}))
    _provider.add_span_processor(SimpleSpanProcessor(JsonlExporter(path)))
    _tracer = _provider.get_tracer("eval-run-validity")
    return _tracer


def tracer():
    return _tracer


def shutdown():
    global _provider, _tracer
    if _provider:
        _provider.shutdown()
    _provider, _tracer = None, trace.NoOpTracer()


def current():
    return trace.get_current_span()
