from __future__ import annotations

import logging
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

from app.core.config import get_settings

log = logging.getLogger("agentos")

HTTP_REQUESTS = Counter("agentos_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("agentos_http_request_seconds", "HTTP latency", ["method", "route"])
TASKS_TOTAL = Counter("agentos_tasks_total", "Task state transitions", ["status", "agent"])
TASK_DURATION = Histogram(
    "agentos_task_duration_seconds", "Task attempt duration", ["agent"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
TOOL_CALLS = Counter("agentos_tool_calls_total", "Tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("agentos_tool_seconds", "Tool latency", ["tool"])
MODEL_CALLS = Counter("agentos_model_calls_total", "Model calls", ["provider", "model", "status"])
MODEL_LATENCY = Histogram("agentos_model_seconds", "Model latency", ["provider", "model"])
MODEL_TOKENS = Counter("agentos_model_tokens_total", "Tokens", ["provider", "model", "direction"])
MODEL_COST = Counter("agentos_model_cost_usd_total", "Estimated model cost (USD)", ["provider", "model"])
QUEUE_DEPTH = Gauge("agentos_queue_depth", "Jobs waiting in queue")
ACTIVE_WORKERS = Gauge("agentos_active_workers", "Workers currently executing a task")
APPROVALS_PENDING = Gauge("agentos_approvals_pending", "Pending approvals")
POLICY_DECISIONS = Counter("agentos_policy_decisions_total", "Policy outcomes", ["effect"])
INJECTION_FLAGS = Counter("agentos_prompt_injection_flags_total", "Untrusted content flagged", ["source"])
RECOVERIES = Counter("agentos_recoveries_total", "Recovery actions", ["kind"])

_tracer = None


def setup_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def setup_tracing(service_name: str = "agentos") -> None:
    """OpenTelemetry tracing. Exports via OTLP when AGENTOS_OTEL_EXPORTER_ENDPOINT is set."""
    global _tracer
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = get_settings().otel_exporter_endpoint
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:  # pragma: no cover
            log.warning("OTLP exporter not installed; traces kept in-process only")
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("agentos")


@contextmanager
def span(name: str, **attrs):
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s
