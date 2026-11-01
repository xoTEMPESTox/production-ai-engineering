# Lesson 03: Prompting & Structured Output

Learn how to treat a prompt as versioned code, and the model's reply as a contract that is checked before anything uses it.

> **Pillars:** RAG · Harness  |  **Harness layer:** the contract between the model and the code that consumes its output
>
> *Validate every reply as if a stranger wrote it. Repair once, then fail closed.*

## Why does this exist?
In Lesson 02 the final answer was a string that went straight to the user. That works in a demo. In production, code consumes the answer: a UI renders the citations as links, a workflow branches on whether an answer was found, an evaluation job compares it with a reference. Code needs a shape it can rely on, and free text has none.

Two artifacts are involved: the **prompt** that asks for a shape, and the **output** that may or may not have it. Both change over time, because prompts get edited and models get upgraded, so both need to be identifiable and checkable.

The RAG version of this problem is the most common one: "answer from these sources and cite them". A model can return perfect JSON that cites a source it was never given. The schema can't catch that, but a check against the sources this request actually sent can.

---

## What problem does it solve?
1. **Unparseable output**: a friendly preamble ("Sure! Here's the JSON:"), a markdown fence, a reply cut off at the token limit.
2. **Parseable but wrong**: a missing field, a renamed field (`sources` instead of `citations`), `found: true` with nothing cited.
3. **Valid but ungrounded**: the right shape around an invented citation. In RAG this is the hallucination that looks most trustworthy.
4. **Untraceable prompts**: someone edits the prompt text in place, quality drops, and nobody can tell which outputs came from which wording.
5. **Prompt cost**: the same long instructions and sources are re-sent with every request, and their order decides whether the provider's prompt cache can reuse them.

---

## How would beginners implement this?
```python
def answer(question, sources):
    prompt = f"Today is {date.today()}. Question: {question}\nSources:\n{sources}\nReply in JSON."
    raw = llm(prompt)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"answer": raw}  # close enough
```

### Why does that fail?
1. **`json.loads` is not validation.** `{"answer": 42}` and `{"sources": [...]}` both parse. Downstream code fails later with a `KeyError`, far from the cause.
2. **Nothing checks citations against what was retrieved.** An invented source ID goes straight to the UI as a link to nothing.
3. **The fallback dresses unverified text as an answer.** That is failing open: the caller can't tell a checked answer from a guess.
4. **There is no repair, and a naive one wouldn't help.** "ONLY JSON PLEASE" tells the model less than the parser already knows.
5. **The prompt has no identity.** It has no name, version or hash, so after an edit old and new outputs can't be told apart.
6. **The order defeats caching.** With the date first and the question before the sources, no two requests share a prefix, so nothing can be reused.

---

## How is this solved in production?
Step by step through `example.py`:

**1. The output is a schema.** A Pydantic model declares the shape, plus the rules a shape can express:
```python
class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a renamed field fails loudly
    found: bool
    answer: str = Field(min_length=1, max_length=600)
    citations: list[str] = Field(default_factory=list)
    # plus a validator: found=true needs a citation, found=false must cite nothing
```
`found=false` is in the contract on purpose. The model needs a valid way to say "not in the sources", or it will invent something to fill the shape.

**2. Then the checks a schema can't express.** `parse_output` validates the JSON, then rejects any citation that isn't one of the source IDs this request sent.

**3. One repair, with the exact errors.** On failure the model gets its own reply back plus the validator's messages, for example `citations: unknown source id(s) ['pto-09']; cite only ['pto-01', 'pto-03', 'travel-02']`. Specific errors fix most bad replies in a single turn.

**4. Then fail closed.** If the repaired reply is still invalid, the result is `status="failed"` with no answer, and the caller decides what to show ("couldn't verify an answer; routed to a person"). It never receives unverified text in the shape of an answer.

**5. Prompts are versioned and immutable.** A `PromptTemplate` has a name, a version and a content hash, and the registry refuses to re-register `grounded_answer@v2` with different text. Every result carries its `prompt_id` (`grounded_answer@v2#` plus a 12-character hash), so eval runs, traces and bug reports point at the exact wording.

**6. Rendering is cache-friendly.** The system prompt and schema come first (identical for every request), then the sources (identical for follow-up questions on the same documents), then the question. Prompt caches match on an exact prefix, so anything that varies per request, such as dates, user names or request IDs, goes at the end.

Native structured-output modes (most major providers can constrain decoding to a JSON schema) reduce the work in step 3. They don't remove steps 2 and 4: a constrained reply can still cite a source that wasn't provided, be cut off at the token limit, or be a refusal.

---

## Design Tradeoffs

### Strict schema vs. lenient parsing
* **Strict** (`extra="forbid"`, required fields): drift surfaces at once, as a repair or a failure you can count. It costs extra repairs when a model adds a harmless field.
* **Lenient** (ignore extras, default missing fields): fewer failures on paper, but a renamed field silently becomes an empty default. Prefer strict for anything downstream code acts on.

### Repair with errors vs. fresh re-ask vs. no retry
* **Repair with errors** (this lesson): the cheapest fix for format slips. It keeps the context, so it's one extra call on a prompt that is mostly cached.
* **Fresh re-ask**: helps if the first reply anchored on a wrong idea, but tends to repeat the same mistake at the same rate.
* **No retry**: lowest latency, and right when a fallback is cheap. Whichever you choose, cap it. A second repair rarely succeeds where the first failed, and every attempt adds a full model call of latency.

### Relevance order vs. stable order for sources
* **Relevance order**: the best source first, which can help answer quality on long contexts.
* **Stable order** (for example by document ID): the same set of sources gives the same prefix even when retrieval ranks them differently, so follow-up questions hit the cache. Measure both on your own eval set before choosing.

---

## Failure Cases to Watch For
1. **Failing open**: returning raw text or a partial parse when validation fails. Downstream code can't tell it from a checked answer.
2. **Fabricated citations**: schema-valid output citing IDs that weren't in the context. Always check citations against the request's own sources.
3. **Silent defaults**: a renamed or missing field filled in with a default value. Forbid extras and require the fields that matter.
4. **No way to abstain**: a schema with no "not found" option pushes the model to invent an answer.
5. **Unbounded repair loops**: retrying "until it works" multiplies latency and cost on exactly the inputs that are already going badly.
6. **Prompts edited in place**: quality changes with no version change, and outputs can't be traced to the wording that produced them.
7. **Cache-busting prompts**: a timestamp or user name near the top of the system prompt turns every request into a cache miss.
8. **Truncated JSON**: a reply cut off at the output-token limit is invalid however good the model is. Size the limit for the schema's largest valid output, and count truncation as its own error type.

---

## Try It
```sh
cd part-1-foundations/03-prompting-structured-output
python example.py            # three scripted replies; no API key needed
python -m pytest tests.py -q
```
What to watch for: in the second case the reply is valid JSON inside a markdown fence, yet it is rejected for citing `pto-09`, a source that was never provided, and the repair message lists the allowed IDs. The third case fails closed, so the user sees a hand-off message instead of the model's prose.

Experiment: add a sentence to the system prompt of `GROUNDED_ANSWER_V2` and print its `prompt_id`. The hash changes even though the version label didn't. That is the in-place edit the registry refuses.

**Next:** Lesson 04 turns tools into contracts too: JSON-schema specs, argument validation, timeouts and permission tiers.
