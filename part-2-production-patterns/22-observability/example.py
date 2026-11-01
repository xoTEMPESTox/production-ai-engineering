"""
Lesson 22: Observability.

One trace per agent run, one span per step: retrieval, model calls, tool
calls. Spans nest through contextvars (so concurrent runs don't mix), carry
OpenTelemetry-GenAI-style attributes (operation, model, token usage) plus
latency and cost, and record failures without swallowing them. Prompt and
response text is captured only for a deterministic sample of traces, and is
PII-redacted before it's stored. A finished trace can be turned into a
candidate eval case, closing the loop with Lesson 27.
"""
from __future__ import annotations

import contextvars
import hashlib
import itertools
import json
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator, Optional

# Illustrative prices in USD per million tokens (input, output). Not real list prices.
PRICES = {"small-model": (0.15, 0.60), "large-model": (3.00, 15.00)}


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: Optional[str]
    name: str
    start: float
    end: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"


_current: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar("current_span", default=None)


class Tracer:
    def __init__(self, clock: Callable[[], float] = time.perf_counter, content_sample_rate: float = 0.0) -> None:
        self.finished: list[Span] = []  # stands in for an exporter (OTLP, a JSONL file, a vendor)
        self._clock = clock
        self._ids = itertools.count(1)
        self.content_sample_rate = content_sample_rate

    @contextmanager
    def span(self, name: str, trace_id: Optional[str] = None, **attributes: Any) -> Iterator[Span]:
        parent = _current.get()
        span = Span(trace_id or (parent.trace_id if parent else f"trace-{next(self._ids)}"),
                    f"span-{next(self._ids)}", parent.span_id if parent else None, name, self._clock(),
                    attributes=dict(attributes))
        token = _current.set(span)
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.attributes["error.type"] = type(exc).__name__
            raise  # observe the failure, never hide it
        finally:
            span.end = self._clock()
            _current.reset(token)
            self.finished.append(span)

    def capture_content(self, span: Span, key: str, text: str) -> None:
        """Prompt/response text only for sampled traces, and always redacted first."""
        if sampled(span.trace_id, self.content_sample_rate):
            span.attributes[key] = redact(text)

    def export_jsonl(self) -> str:
        return "\n".join(json.dumps({**asdict(s), "duration_ms": round((s.end - s.start) * 1000, 3)})
                         for s in self.finished)


def sampled(trace_id: str, rate: float) -> bool:
    """Deterministic per trace: either every span of a trace has content, or none does."""
    return int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
       (re.compile(r"\b\d(?:[ -]?\d){12,15}\b"), "[CARD]"),
       (re.compile(r"\+?\d[\d -]{8,}\d"), "[PHONE]")]


def redact(text: str) -> str:
    for pattern, label in PII:
        text = pattern.sub(label, text)
    return text


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


# --- An instrumented RAG agent run ---

DOCS = {"billing-04": "Annual plans are refunded in full within 30 days.",
        "billing-07": "Monthly plans are not refundable."}


def retrieve(tracer: Tracer, query: str, k: int = 2) -> list[str]:
    with tracer.span("retrieval", **{"retrieval.top_k": k}) as span:
        hits = [doc_id for doc_id, text in DOCS.items() if set(query.lower().split()) & set(text.lower().split())]
        span.attributes["retrieval.doc_ids"] = hits[:k]  # ids, not document text
        return hits[:k]


def chat(tracer: Tracer, model: str, prompt: str, reply: str, input_tokens: int, output_tokens: int,
         fail: bool = False) -> str:
    with tracer.span("gen_ai.chat", **{"gen_ai.operation.name": "chat", "gen_ai.request.model": model,
                                       "app.prompt_version": "grounded_answer@v2"}) as span:
        tracer.capture_content(span, "app.prompt", prompt)
        if fail:
            raise TimeoutError("provider timed out")
        span.attributes.update({"gen_ai.usage.input_tokens": input_tokens,
                                "gen_ai.usage.output_tokens": output_tokens,
                                "app.cost_usd": cost_usd(model, input_tokens, output_tokens)})
        tracer.capture_content(span, "app.completion", reply)
        return reply


def answer(tracer: Tracer, question: str, user: str, fail_first_model: bool = False) -> str:
    with tracer.span("agent.run", **{"app.user_hash": hashlib.sha256(user.encode()).hexdigest()[:12]}) as root:
        tracer.capture_content(root, "app.question", question)
        doc_ids = retrieve(tracer, question)
        prompt = f"Sources: {doc_ids}\nQuestion: {question} (account email {user})"
        try:
            reply = chat(tracer, "large-model", prompt, "", 900, 0, fail=fail_first_model)
        except TimeoutError:
            reply = chat(tracer, "small-model", prompt, "Refunds within 30 days [billing-04].", 850, 40)
        root.attributes["app.answer_citations"] = re.findall(r"\[([\w-]+)\]", reply)
        return reply


def trace_summary(spans: list[Span], trace_id: str) -> dict[str, Any]:
    mine = [s for s in spans if s.trace_id == trace_id]
    chats = [s for s in mine if s.name == "gen_ai.chat"]
    return {"spans": len(mine), "errors": sum(s.status == "error" for s in mine),
            "tokens": sum(s.attributes.get("gen_ai.usage.input_tokens", 0) +
                          s.attributes.get("gen_ai.usage.output_tokens", 0) for s in chats),
            "cost_usd": round(sum(s.attributes.get("app.cost_usd", 0.0) for s in chats), 6)}


def to_eval_case(spans: list[Span], trace_id: str) -> Optional[dict[str, Any]]:
    """A sampled trace becomes a candidate golden case (Lesson 27) for a person to label."""
    mine = {s.name: s for s in spans if s.trace_id == trace_id}
    root, retrieval = mine.get("agent.run"), mine.get("retrieval")
    if root is None or "app.question" not in root.attributes:
        return None  # content wasn't captured for this trace
    return {"source_trace": trace_id, "question": root.attributes["app.question"],
            "retrieved_ids": retrieval.attributes["retrieval.doc_ids"] if retrieval else [],
            "answer_citations": root.attributes.get("app.answer_citations", []),
            "label": None}  # filled in by a reviewer


if __name__ == "__main__":
    tracer = Tracer(content_sample_rate=1.0)  # capture everything for the demo
    answer(tracer, "Can I get a refund on annual plans? Call me on +91 98765 43210", "ana@example.com",
           fail_first_model=True)
    trace_id = tracer.finished[-1].trace_id
    for s in sorted(tracer.finished, key=lambda span: span.start):  # exported in finish order
        indent = "  " if s.parent_id else ""
        print(f"{indent}{s.name:<12} status={s.status:<5} {json.dumps(s.attributes)[:150]}")
    print("\nsummary:", trace_summary(tracer.finished, trace_id))
    print("eval case:", to_eval_case(tracer.finished, trace_id))
    exported = tracer.export_jsonl()
    print("raw email or phone in export?", "ana@example.com" in exported or "98765" in exported)
