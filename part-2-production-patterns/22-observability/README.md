# Lesson 22: Observability

Learn how to see what every agent run actually did: one trace per run, one span per step with model, tokens, latency and cost, failures recorded rather than hidden, prompt text sampled and redacted, and traces turned into eval cases.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** tracing, the record of what each run did and what it cost
>
> *Trace every step, price every call, store IDs rather than text, and redact whatever you keep.*

## Why does this exist?
When an answer is wrong, slow or expensive, the first question is *which step*. Did retrieval miss the chunk? Did the large model time out, so a smaller fallback answered? Did the prompt version change yesterday? A log line with the final answer can't tell you. A **trace** per run, with a **span** per step recording the model, tokens, latency, cost and outcome, can.

Traces also feed evaluation. A sampled production trace that a person has labelled is the best golden example you can get (Lesson 27), because it is what real users really asked.

---

## What problem does it solve?
1. **Attribution**: latency, cost and errors per step, not just per request.
2. **Correlation**: one run spans retrieval, several model calls and tools, often concurrently, so every record must say which run it belongs to.
3. **Privacy**: prompts contain user data, and logging everything is a leak and a compliance problem.
4. **Cost visibility**: tokens × price, per call and per run.
5. **A feedback loop**: turning production behaviour into test cases.

---

## How would beginners implement this?
```python
def answer(question, user_email):
    logger.info(f"question from {user_email}: {question}")
    docs = retrieve(question)
    logger.info(f"docs: {docs}")    # full document text
    reply = llm(build_prompt(docs, question))
    logger.info(f"reply: {reply}")
    return reply
```

### Why does that fail?
1. **The log lines are unstructured.** You can't compute p95 latency per step or tokens per model from them.
2. **There's no trace or parent ID.** Concurrent runs interleave, and nobody can tell which line belongs to which run.
3. **Personal data and whole documents land in the logs.** Emails, phone numbers and full documents are visible to everyone with log access, and kept for months.
4. **No tokens and no cost are recorded.** The bill arrives as a monthly surprise.
5. **A failed call that fell back disappears.** The log shows a happy answer, and the fallback rate stays invisible.
6. **Nothing turns a bad answer into a test case.**

---

## How is this solved in production?
**1. A span per step, nested by context.**
```python
with tracer.span("agent.run", **{"app.user_hash": user_hash}):    # root: new trace id
    with tracer.span("retrieval", **{"retrieval.top_k": 2}):      # child: same trace, parent = root
        ...
    with tracer.span("gen_ai.chat", **{"gen_ai.request.model": "large-model"}):
        ...
```
The current span lives in a `contextvars.ContextVar`, so concurrent `asyncio` runs each keep their own parent chain (`test_concurrent_runs_do_not_mix_parents`).

**2. Conventional attribute names.** Model spans use OpenTelemetry GenAI-style names: `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`. Your own fields (`app.prompt_version`, `app.cost_usd`) go in a namespace of your own. The GenAI conventions are still evolving, so check the current specification before depending on exact names.

**3. Record failures, then re-raise them.** A span that raises gets `status="error"` and `error.type`, and the exception continues upward. In the demo, the large model times out and the small model answers. The trace shows a failed span followed by a good one, so the fallback is visible and countable.

**4. Store IDs, not text.** The retrieval span records `retrieval.doc_ids`, not document contents, and the user appears as a hash.

**5. Prompt and response text is opt-in, sampled per trace and redacted first.** `capture_content` stores text only if the trace is sampled. Sampling is deterministic per trace ID, so either every span of a trace has content or none does. The text passes through `redact()` (emails, card numbers, phone numbers) **before** it's attached to a span. The demo's question ends up stored as "…Call me on [PHONE]". Regex redaction is a floor, not a guarantee: names and street addresses need entity detection or tokenisation (Lesson 29).

**6. Price every call.** Cost is tokens × price per million from a price table. The prices here are illustrative; real ones change, so version them. `trace_summary` adds up spans, errors, tokens and cost per run. Some providers bill input tokens even when a call fails, so record usage whenever the provider reports it.

**7. Export structured records.** Each span becomes one JSON object (`export_jsonl`). In production the same spans go through an OpenTelemetry SDK exporter to a tracing backend or an LLM-tracing tool. Swap the exporter, keep the spans.

**8. Turn traces into eval cases.** `to_eval_case` converts a sampled trace into an unlabelled candidate (question, retrieved IDs, cited IDs) for a reviewer. Once labelled, it joins Lesson 27's golden set.

---

## Design Tradeoffs

### Capturing content vs. metadata only
* **Metadata only** (model, tokens, latency, IDs): safe to keep for everyone, cheap to store, and enough for performance and cost work. It can't explain a wrong answer.
* **Sampled, redacted content** (this lesson): explains quality problems and feeds evals. It needs redaction, access control and a retention policy, and redaction is never perfect.

### Head sampling vs. tail sampling
* **Head sampling** (decide when the trace starts, as here): cheap and consistent, but it misses rare errors unless they're flagged separately.
* **Tail sampling** (decide after the trace ends; keep every error and every slow trace): catches the interesting traces, but a collector has to buffer whole traces before deciding.

### A small custom tracer vs. OpenTelemetry or a tracing product
* **Custom** (this lesson): no dependencies, and every line is visible. No ecosystem, no standard wire format.
* **OpenTelemetry SDK, or an LLM-tracing tool**: a standard format, vendor-neutral backends, auto-instrumentation of common libraries. More setup, and someone has to understand the configuration.

---

## Failure Cases to Watch For
1. **Raw prompts and completions in logs**, full of personal data.
2. **Redacting too late**: after export the data has already left the process. Redact before attaching.
3. **Broken propagation**: context not passed across threads, tasks or services, so traces fall apart into fragments.
4. **Instrumentation that swallows exceptions**, turning failures into successes.
5. **High-cardinality labels in metrics**: user IDs are fine on spans and ruinous as metric labels (Lesson 26).
6. **Document text in retrieval spans**: storage grows, and private documents spread.
7. **Stale price tables**: cost dashboards that are quietly wrong.
8. **100% content capture in production**: maximum risk for little extra insight.

---

## Try It
```sh
cd part-2-production-patterns/22-observability
python example.py            # in-memory exporter; no services needed
python -m pytest tests.py -q
```
What to watch for: the root `agent.run` span with three children, one of them a failed `gen_ai.chat` on the large model, followed by a successful one on the small model. The question is stored with `[PHONE]` in place of the number. The summary shows four spans, one error, 890 tokens and the run's cost. And the export contains no raw email or phone number.

Experiment: in `Tracer.capture_content`, store `text` instead of `redact(text)` and run the tests. `test_captured_content_is_redacted` fails, because the raw email and phone number are now in the exported spans.

**Next:** Lesson 23 packages the service: multi-stage Docker builds, a non-root user, health checks, and model or index files baked in or mounted.
