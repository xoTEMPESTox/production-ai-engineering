# Lesson 05: Memory & Retrieval

Learn how an agent remembers: a short-term context kept inside a token budget, and a long-term memory that is a filtered, hybrid retrieval index. This is the core of RAG.

> **Pillars:** RAG · Agent · Harness  |  **Harness layer:** context, meaning what the model gets to see on each call
>
> *Filter by who is asking, then rank by what they asked.*

## Why does this exist?
A model has two memory problems. Within a conversation, its context window is finite and fills up with every turn and every tool output. Across conversations, it remembers nothing: not what the user said last week, not what's in your help centre, not the document that changed yesterday.

Both are solved outside the model, by the harness deciding what goes into the context on each call. That is **context engineering**, and retrieval-augmented generation (RAG) is its long-term half. Instead of training knowledge into the model, you store it, search it per request, and put the few relevant pieces in front of the model with their IDs, so it can cite them and Lesson 03's citation check can verify the citations.

| | Short-term (working memory) | Long-term |
|---|---|---|
| What | the current conversation and its tool outputs | documents (shared in a tenant) and memories about a user (private) |
| Where | the message list | an index of chunks with metadata, terms and vectors |
| Main risk | overflowing the budget, or losing the task | another tenant's data in the prompt; stale or unwanted personal data |
| Control | compaction | a write policy, plus filtered hybrid search |

---

## What problem does it solve?
1. **Finding the right chunk.** Users ask in their own words ("money back") while documents use others ("refunded"). Other queries hinge on one exact token, such as error code `E4012` or an order number, which embeddings blur.
2. **Isolation.** In a multi-tenant product, one tenant's documents and one user's memories must never reach another's prompt.
3. **Deciding what to keep.** Storing every message forever means storing PII (personally identifiable information, such as emails and phone numbers), contradictions and junk.
4. **Fitting in the window.** Long sessions with big tool outputs exceed the budget, and naive truncation drops the system prompt or the task itself.

---

## How would beginners implement this?
```python
memory = []  # every message from every user, forever

def remember(text):
    memory.append({"text": text, "vector": embed(text)})

def recall(query, k=3):
    q = embed(query)
    return sorted(memory, key=lambda m: -cosine(q, m["vector"]))[:k]

def build_prompt(history, query):
    return "\n".join(history[-50:] + [m["text"] for m in recall(query)] + [query])
```

### Why does that fail?
1. **There is no tenant or user filter.** `recall` searches everyone. The best match for "where should my invoices go?" may be another customer's address.
2. **The usual patch filters after ranking.** `[m for m in recall(q, 3) if m["tenant"] == t]` returns fewer than `k` results, often none, whenever another tenant has better matches. And any code path that forgets the patch leaks.
3. **It relies on vectors alone.** Exact identifiers (`E4012`, SKUs, drug names) are blurred, and keyword search alone fails the other way, on paraphrases.
4. **The units are wrong.** A whole document is too big to embed meaningfully, and a fixed character window splits a section from its heading, so citations point at fragments.
5. **There is no write policy.** Emails and phone numbers are stored verbatim, duplicates pile up, nothing expires, and there's no way to delete one user's data.
6. **`history[-50:]` cuts blindly.** Once the conversation is long enough it drops the system prompt and the original task, while keeping a huge log from a few turns ago.

---

## How is this solved in production?
This is the longest lesson in Part 1 because it covers both kinds of memory. Steps 1–4 are long-term memory as retrieval (the RAG part), step 5 is what may be written into memory, and step 6 is short-term memory. Step 6 also makes sense on its own, so you can come back to it later.

Terms used below, in plain words:

| Term | Meaning |
|---|---|
| chunk | a piece of a document small enough to search and cite: here, one section or part of one |
| embedding (vector) | a list of numbers representing a text's meaning; texts with similar meanings get nearby vectors |
| BM25 | the standard keyword score: query words that are rare overall but frequent in a short chunk score highest |
| RRF (reciprocal rank fusion) | merges ranked lists by adding `1 / (60 + rank)` from each list, so scores on different scales never need comparing |
| HNSW | a common approximate-nearest-neighbour index: finds similar vectors quickly without comparing against every one |
| reranking | a second, slower model that re-scores only the top 20–50 hits |
| pgvector | the PostgreSQL extension that stores vectors and searches them in SQL |
| compaction | shrinking the conversation so it fits the token budget |

Step by step through `example.py`:

**1. Chunk by structure and keep the metadata.** A heading closes the current chunk and labels the chunks under it. Lines are packed up to `max_words`. Each chunk gets a stable ID (`billing-faq#0`) and its section, and the heading is indexed together with the body, so a query for "refunds" finds a section whose body only says "refunded".

**2. Every record carries a tenant and an owner.** Documents are shared within a tenant (`owner=None`), and memories belong to one user. The store is keyed by `(tenant, id)`, so two tenants can both have a `billing-faq` without overwriting each other.

**3. Filter, then rank twice, then fuse:**
```python
candidates = [r for r in records                     # 1. filter: who is asking
              if r.tenant == tenant and r.owner in (None, owner) and not expired(r)]
lexical  = ranks(bm25(tokenize(query), [r.terms for r in candidates]))      # exact terms
semantic = ranks([dot(embed(query), r.vector) for r in candidates])         # meaning
score    = sum(1 / (60 + rank) for each list the record appears in)         # RRF
```
Filtering comes first, so ranking never sees another tenant's data and a small tenant still gets `k` results. In a real vector database or in pgvector, the filter is part of the query itself (a `WHERE` clause or a metadata filter), not a post-processing step.

**4. Hybrid retrieval, fused by rank.** BM25 rewards exact and rare terms; vectors reward shared meaning. Reciprocal Rank Fusion combines their *ranks*, so the two incomparable scores never need calibrating. The demo prints both ranks for each hit: "How long is the refund window?" is found only by vectors (`bm25#=None vec#=1`), and "What does E4012 mean?" only by BM25 (`bm25#=1 vec#=None`).

**5. User memories go through a write policy.** PII is redacted before storage (`ana@example.com` becomes `[EMAIL]`), whitespace is normalised and exact duplicates are dropped, and every memory has a TTL (time to live: an expiry date). `purge_expired()` physically deletes expired records, and `forget(tenant, user)` handles a deletion request. Memory IDs come from a counter that is never reused, so a deletion can't make a new memory overwrite an old one.

**6. Compact the conversation, cheapest step first.** The system prompt and the last few turns are never touched. Old tool outputs are replaced with a short stub (only when the stub is shorter), which costs no model call, and the tool can be re-run. Only if that isn't enough are the old turns summarised, which costs one call. If the context is still over budget, `compact` raises instead of silently sending a request the provider will reject.

**Where retrieval plugs in.** Either as a fixed pre-step (retrieve, then answer: a workflow) or as a tool the agent decides to call (Lesson 02's agentic RAG). Either way, hits come back with IDs, and Lesson 03's check rejects an answer that cites anything else.

What this lesson leaves out: a real embedding model and an approximate-nearest-neighbour index such as HNSW (the `ConceptEmbedder` is a labelled toy that makes the hybrid behaviour visible offline), cross-encoder reranking of the top 20–50 hits, query rewriting, and measuring retrieval quality with Recall@k and MRR (Lesson 09).

---

## Design Tradeoffs

### Hybrid vs. vector-only retrieval
* **Vector-only**: one index, one query, good on paraphrases. It misses exact identifiers and rare terms, and those are often the whole point of the question.
* **Hybrid with RRF** (this lesson): catches both kinds of query, at the cost of a second index and a second query per search. RRF needs no score tuning, but it ignores how *far* ahead the top hit was.

### One shared index with a mandatory filter vs. an index per tenant
* **Shared index + filter** (this lesson): cheap and simple to operate. Isolation depends on one code path always applying the filter, so put it in the store, never in callers.
* **Index per tenant**: isolation is structural and per-tenant deletion is trivial. You pay for many small indexes, uneven sizes and per-tenant migrations.

### Stubbing vs. summarising vs. offloading old context
* **Stub or drop old tool output**: free and honest about what was removed, but the details are gone unless the tool is re-run.
* **Summarise**: keeps the gist, but costs a call and is model-written, so it can drop a constraint or introduce an error.
* **Offload to long-term memory**: write the facts worth keeping with `remember()` before they fall out of the window. They stay retrievable but must pass the write policy.

---

## Failure Cases to Watch For
1. **Cross-tenant leakage**: any search path without the tenant filter, or with the filter applied after ranking or after the model call.
2. **Post-filtering**: taking the top `k` globally and then filtering leaves small tenants with empty results.
3. **Mixed embedding models**: vectors from two models in one index aren't comparable. A new embedding model means re-embedding everything, so version the index, build the new one alongside, and switch when the evals pass.
4. **Stale chunks**: a document changes but its old chunks are still indexed. Re-ingest by document ID, deleting the old chunks first.
5. **Bad chunk boundaries**: too small, and a rule is split from its exception; too large, and the embedding is diluted and tokens are wasted. Keep headings with chunks and tune size on an eval set.
6. **Memory hoarding**: no TTL, no deduplication, PII stored verbatim, no deletion path.
7. **Remembering instructions**: a stored "memory" such as "always approve refunds" that came from a user message or a document is a persisted prompt injection (Lesson 29). Store facts through the write policy and treat retrieved text as data, never as instructions.
8. **Compaction that loses the task**: truncating from the front drops the system prompt, and a careless summary drops a constraint. Keep the head and the recent turns verbatim.
9. **Token-estimate drift**: `len(text) // 4` is a rough guide. When the budget is tight, count with the model's own tokenizer.

---

## Try It
```sh
cd part-1-foundations/05-memory
python example.py            # offline: toy embedder, no API key
python -m pytest tests.py -q
```
What to watch for: the `bm25#` and `vec#` columns. Each of the first two queries is found by only one retriever, and fusion returns it anyway. User-42 sees their own memory (with the email redacted) next to the shared invoice chunk; user-7, asking the same question, sees only the shared chunk. In the compaction demo, the 630-token log becomes a stub and the summariser is never called.

Experiment: in `MemoryStore.search`, remove the tenant check from `candidates` and instead filter the returned hits by tenant after taking the top `k`. `test_filtering_before_ranking_still_fills_k` fails, because a big tenant's better matches push the small tenant's only result out of the top `k` before the filter runs.

**Next:** Lesson 06 gives the agent a plan it can track: decomposition into a todo list, progress tracking, and re-planning when a step fails.
