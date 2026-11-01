# Lesson 21: Streaming

Learn how to stream answers to users as they're generated: server-sent events, answer text pulled out of structured output while it's still arriving, validation at the end with a way to retract, time to first token, and cancellation that actually stops the model.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** delivery to the user, meaning what they see while the model is still working
>
> *Show the first words fast, validate the last word, and stop the moment nobody's reading.*

## Why does this exist?
A grounded answer can take ten seconds to generate. Shown all at once after ten seconds, it feels broken. Streamed word by word, the user starts reading within half a second. **Time to first token** (TTFT) is the latency people actually feel, and streaming is how you cut it without making the model any faster.

Streaming adds problems that a single response never had:
- the output is often *structured* (Lesson 03's JSON with citations), and half a JSON object isn't valid JSON;
- the answer can fail validation *after* the user has already read most of it;
- when the user closes the tab, the model keeps generating, and you keep paying, unless the cancellation reaches all the way upstream.

---

## What problem does it solve?
1. **Perceived latency**: words on screen within a fraction of a second.
2. **Streaming structured output**: show the `answer` field progressively while the rest of the JSON (citations, flags) is still being generated.
3. **Late failure**: the full answer fails validation, or the upstream connection drops mid-answer.
4. **Wasted generation**: users leave, and the model must stop.
5. **Measuring it**: TTFT and total time, separately.

---

## How would beginners implement this?
```python
@app.get("/answer")
async def answer(q: str):
    async def gen():
        async for token in llm.stream(q):
            yield token                    # raw tokens, no framing
    return StreamingResponse(gen())        # default media type; proxies may buffer it all

# client: append every chunk to the screen, including '{"answer": "' and '", "citations": [...]}'
```

### Why does that fail?
1. **No framing.** The client can't tell answer text from metadata, an error, or the end of the stream.
2. **Raw JSON on screen.** With structured output, the user sees `{"answer": "Refunds...` and the citation array.
3. **Nothing is validated.** If the final JSON is invalid or the citations are wrong (Lesson 03), the user has already read it, and nothing tells the client to take it back.
4. **Errors look like a hang.** If the upstream drops mid-answer, the stream just stops, and the client waits.
5. **Buffering proxies.** Without the right media type and headers, an intermediate proxy may hold the whole response and deliver it at the end, defeating the point.
6. **The model doesn't stop on disconnect.** Nothing closes the upstream stream promptly, so generation, and billing, continue.

---

## How is this solved in production?
**1. Server-sent events with typed frames.** `answer_events` yields `text/event-stream` frames:
```
event: meta    data: {"question": "..."}                         sent immediately
event: delta   data: {"text": " plans are r"}                    the answer, as it arrives
event: done    data: {"citations": [...], "ttft_ms": 61, ...}    after validation
event: error   data: {"message": "...", "retract": true}         instead of done
```
Each frame is `event:` plus one `data:` line of JSON plus a blank line. `json.dumps` keeps the data on one line, because raw text containing newlines would need one `data:` line per line. The endpoint sets `Cache-Control: no-cache` and `X-Accel-Buffering: no` (an nginx convention) so proxies pass frames through as they're produced. Long silences can be bridged with `:` comment lines as keep-alives.

**2. Extract the answer while the JSON streams.** `AnswerExtractor` finds `"answer": "` in the growing buffer and decodes the string value incrementally, so only new characters go out as `delta` events. It handles escapes split across chunks (`\` in one chunk and `n` in the next, or `\u00` then `e9`), and it never emits the JSON punctuation. The streamed text is exactly the final `answer` field, and a test checks that.

**3. Validate at the end, and retract if needed.** When the stream finishes, the whole reply is validated as `Answer` (Lesson 03's contract). If it fails, for example because it was truncated, the last frame is `error` with `retract: true`, and the client replaces the text it showed with a failure message instead of leaving an unverified answer on screen. An upstream error mid-stream is also turned into an `error` frame, never a silently dropped connection.

**4. Measure TTFT separately from total time.** `StreamStats` records when the first chunk arrived and when the stream ended. In the demo, the first token comes at about 60 ms and the whole answer at about 300 ms. TTFT reflects queueing and prompt processing (long prompts and big retrieved contexts push it up). Total time reflects output length.

**5. Cancellation that reaches the model:**
```python
async with aclosing(llm.stream(question)) as chunks:  # closes upstream as soon as we stop
    async for chunk in chunks:
        ...
        yield sse("delta", {"text": delta})
```
When the client disconnects, the server cancels or closes our generator. Without `aclosing`, the *upstream* generator is only closed later, when the event loop finalises it, and until then the model call stays open. This lesson's first draft had exactly that bug: the demo showed the model still running after the client left. With `aclosing` it stops immediately. A test drives the FastAPI endpoint with a raw ASGI `http.disconnect` after three frames and confirms the model stopped early.

---

## Design Tradeoffs

### SSE vs. WebSockets vs. a plain streamed response
* **SSE**: one direction, plain HTTP, works through most proxies, and browsers reconnect automatically (sending `Last-Event-ID`). The browser's `EventSource` only does GET, so many LLM UIs send a POST with `fetch` and read the stream themselves.
* **WebSockets**: two-way (good for voice, or interrupting mid-answer), but stateful connections are harder to load-balance and scale.
* **Plain chunked text**: the simplest, with no framing, so errors, metadata and the end of the answer are indistinguishable.

### Stream, then validate vs. validate, then show
* **Stream, then validate** (this lesson): the best perceived latency. The user may briefly see text that's later retracted, so the UI needs a retract path.
* **Validate, then show**: nothing unverified ever appears, but the user waits for the whole answer. Prefer it where a wrong answer read for three seconds is unacceptable (dosages, legal deadlines).

### Token-level deltas vs. batched deltas
* **Every token**: the smoothest output, and the most frames and overhead.
* **Batched** (every 50 ms or every few tokens): far fewer frames and nearly the same feel. Common in practice.

---

## Failure Cases to Watch For
1. **Buffering proxies or middleware** (compression, logging) that hold the stream until it ends.
2. **Raw structured output on screen**: JSON keys and punctuation shown to users.
3. **No retract path**: invalid or ungrounded answers stay on screen after validation fails.
4. **Silent stream death**: an upstream error ends the connection with no error frame, and the client waits forever.
5. **Upstream not closed on disconnect**: tokens generated and billed for nobody. Close it explicitly.
6. **Measuring only total time**: TTFT regressions (a bigger prompt, a slower retrieval step before generation) go unnoticed.
7. **Escapes split across chunks**: naive extraction shows a stray `\` or garbled characters.
8. **Unbounded connections**: thousands of idle streams with no timeout or keep-alive policy.

---

## Try It
```sh
cd part-2-production-patterns/21-streaming
python example.py            # fake streaming model, real asyncio timing
python -m pytest tests.py -q # includes the FastAPI endpoint, driven through raw ASGI
```
What to watch for: `meta` arrives first, the `delta` frames carry only answer text (including a real newline), and `done` reports TTFT around 60 ms against a total around 300 ms. When the client leaves after three frames, `model stopped early=True` after 3 of 20 chunks. Truncated JSON ends in `error` with `retract: true`.

Experiment: in `answer_events`, replace the `async with aclosing(...) as chunks:` block with a plain `async for chunk in llm.stream(question):` and run the tests. `test_closing_the_stream_stops_the_model_immediately` fails: right after the client goes away, the upstream model stream is still open.

**Next:** Lesson 22 makes every step of a run visible: traces, spans with model, token, latency and cost attributes, redacted prompt sampling, and traces that turn into eval cases.
