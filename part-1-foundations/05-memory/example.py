"""
Lesson 05: Memory & Retrieval.

Long-term memory is a retrieval index: documents are chunked with their
section headings, user memories pass a write policy (PII redaction,
deduplication, TTL), and every search filters by tenant and owner before
ranking. Ranking is hybrid: BM25 for exact terms plus vector similarity for
paraphrases, fused with Reciprocal Rank Fusion. Short-term memory (the
conversation) is kept inside a token budget, cheapest reduction first.
"""
from __future__ import annotations

import itertools
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Literal, Optional, Protocol

STOPWORDS = {"a", "an", "and", "are", "be", "can", "do", "does", "for", "get", "how", "i", "in",
             "is", "it", "my", "of", "on", "or", "the", "to", "what", "where", "with"}
RRF_K = 60  # the usual constant: dampens the gap between rank 1 and rank 2


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


class Embedder(Protocol):
    def embed(self, text: str) -> tuple[float, ...]: ...


class ConceptEmbedder:
    """Toy stand-in for an embedding model, so the demo runs offline.

    It maps words to a small vocabulary of concepts, so paraphrases land near
    each other ("money back" ~ "refunded"). Words outside the vocabulary, such
    as the error code E4012, are invisible to it, much as rare identifiers are
    blurred by real embedding models. That's the gap BM25 fills.
    """
    CONCEPTS = {
        "refund": "refund", "refunds": "refund", "refunded": "refund", "refundable": "refund",
        "reimburse": "refund", "reimbursement": "refund", "money": "refund",
        "cancel": "cancel", "cancelled": "cancel", "terminate": "cancel", "stop": "cancel",
        "invoice": "invoice", "invoices": "invoice", "receipt": "invoice", "bill": "invoice",
        "plan": "plan", "plans": "plan", "subscription": "plan", "renewal": "plan",
        "days": "time", "window": "time", "long": "time", "deadline": "time",
        "charge": "payment", "card": "payment", "declined": "payment", "bank": "payment",
        "email": "email", "emailed": "email", "sent": "email",
    }

    def __init__(self) -> None:
        self._axis = {c: i for i, c in enumerate(sorted(set(self.CONCEPTS.values())))}

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * len(self._axis)
        for token in tokenize(text):
            if token in self.CONCEPTS:
                vector[self._axis[self.CONCEPTS[token]]] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return tuple(v / norm for v in vector)


@dataclass(frozen=True)
class Record:
    id: str
    tenant: str
    owner: Optional[str]  # None = shared with the whole tenant (documents)
    kind: Literal["chunk", "memory"]
    text: str
    section: str = ""
    expires_at: Optional[float] = None
    terms: tuple[str, ...] = ()
    vector: tuple[float, ...] = ()


@dataclass(frozen=True)
class Hit:
    record: Record
    score: float
    lexical_rank: Optional[int]  # None = no exact term matched
    vector_rank: Optional[int]   # None = no semantic overlap


def chunk_document(doc_id: str, text: str, max_words: int = 80) -> list[tuple[str, str, str]]:
    """Structure-aware chunking -> (chunk_id, section, text). A heading closes
    the current chunk and labels the ones under it; lines are packed up to
    max_words and split only when a single line is longer than that."""
    chunks: list[tuple[str, str, str]] = []
    section, words = "", []

    def flush() -> None:
        if words:
            chunks.append((f"{doc_id}#{len(chunks)}", section, " ".join(words)))
            words.clear()

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            section = line.lstrip("# ").strip()
            continue
        line_words = line.split()
        if len(words) + len(line_words) > max_words:
            flush()
        while len(line_words) > max_words:
            words.extend(line_words[:max_words])
            flush()
            line_words = line_words[max_words:]
        words.extend(line_words)
    flush()
    return chunks


def bm25(query: list[str], docs: list[tuple[str, ...]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Keyword relevance of each doc. Per query term: idf (rarer terms count more) times the term's
    count in the doc, saturated by k1 (the 5th mention adds less than the 1st) and scaled by the doc's
    length against the average (b: how much a long document is penalised)."""
    avg_len = sum(map(len, docs)) / len(docs) or 1.0
    doc_freq = Counter(term for doc in docs for term in set(doc))
    scores = []
    for doc in docs:
        tf = Counter(doc)
        score = 0.0
        for term in query:
            if term in tf:
                idf = math.log(1 + (len(docs) - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(doc) / avg_len))
        scores.append(score)
    return scores


def _ranks(scores: list[float]) -> dict[int, int]:
    """Position -> rank (1 = best), for positive scores only."""
    order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])
    return {i: rank for rank, i in enumerate(order, start=1)}


PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
       (re.compile(r"\+?\d[\d -]{8,}\d"), "[PHONE]")]


def redact_pii(text: str) -> str:
    for pattern, label in PII:
        text = pattern.sub(label, text)
    return text


class MemoryStore:
    def __init__(self, embedder: Embedder, clock: Callable[[], float] = time.time) -> None:
        self._embedder = embedder
        self._clock = clock
        self._records: dict[tuple[str, str], Record] = {}  # keyed by (tenant, id): IDs can repeat across tenants
        self._memory_ids = itertools.count()  # never reused, even after deletions

    def _put(self, record: Record) -> None:
        indexed = f"{record.section}. {record.text}"  # the heading travels with the chunk
        self._records[(record.tenant, record.id)] = replace(
            record, terms=tuple(tokenize(indexed)), vector=self._embedder.embed(indexed))

    def add_document(self, tenant: str, doc_id: str, text: str, max_words: int = 80) -> list[str]:
        chunks = chunk_document(doc_id, text, max_words)
        for chunk_id, section, body in chunks:
            self._put(Record(chunk_id, tenant, None, "chunk", body, section))
        return [chunk_id for chunk_id, _, _ in chunks]

    def remember(self, tenant: str, owner: str, text: str, ttl_s: float) -> str:
        """Write policy for user memories: redact PII, drop exact duplicates, always expire."""
        clean = redact_pii(" ".join(text.split()))
        if not clean or len(clean) > 300:
            raise ValueError("a memory must be 1-300 characters")
        for record in self._records.values():
            if (record.tenant, record.owner, record.text.lower()) == (tenant, owner, clean.lower()):
                return record.id
        memory_id = f"mem-{owner}-{next(self._memory_ids)}"
        self._put(Record(memory_id, tenant, owner, "memory", clean, expires_at=self._clock() + ttl_s))
        return memory_id

    def _delete(self, should_delete: Callable[[Record], bool]) -> int:
        doomed = [key for key, record in self._records.items() if should_delete(record)]
        for key in doomed:
            del self._records[key]
        return len(doomed)

    def forget(self, tenant: str, owner: str) -> int:
        """Delete everything remembered about one user (a deletion request)."""
        return self._delete(lambda r: r.tenant == tenant and r.owner == owner)

    def purge_expired(self) -> int:
        """Physically delete expired records. Search already ignores them,
        but expired personal data shouldn't stay on disk."""
        now = self._clock()
        return self._delete(lambda r: r.expires_at is not None and r.expires_at <= now)

    def search(self, query: str, tenant: str, owner: Optional[str] = None, k: int = 3) -> list[Hit]:
        now = self._clock()
        # 1. Filter first: this tenant only, shared docs or this user's own
        #    memories, nothing expired. Ranking never sees anything else.
        candidates = [r for r in self._records.values()
                      if r.tenant == tenant and r.owner in (None, owner)
                      and (r.expires_at is None or r.expires_at > now)]
        if not candidates:
            return []
        # 2. Rank twice: exact terms (BM25) and meaning (vector cosine).
        lexical = _ranks(bm25(tokenize(query), [r.terms for r in candidates]))
        q_vec = self._embedder.embed(query)
        semantic = _ranks([sum(a * b for a, b in zip(q_vec, r.vector)) for r in candidates])
        # 3. Fuse by rank, not by score: BM25 and cosine scores aren't comparable.
        hits = []
        for i, record in enumerate(candidates):
            ranks = [r for r in (lexical.get(i), semantic.get(i)) if r is not None]
            if ranks:
                hits.append(Hit(record, sum(1 / (RRF_K + r) for r in ranks), lexical.get(i), semantic.get(i)))
        return sorted(hits, key=lambda h: (-h.score, h.record.id))[:k]


# --- Short-term memory: keep the conversation inside a token budget ---

@dataclass(frozen=True)
class Turn:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # rough rule of thumb; use the model's tokenizer in production


def _stub(turn: Turn) -> Turn:
    """Replace a tool output with a short note, but only if that saves space."""
    stub = f"[old tool output removed ({estimate_tokens(turn.content)} tokens); re-run the tool if needed]"
    return Turn("tool", stub) if turn.role == "tool" and len(stub) < len(turn.content) else turn


def compact(turns: list[Turn], budget: int, summarize: Callable[[list[Turn]], str],
            keep_recent: int = 4) -> list[Turn]:
    """Cheapest reduction first. The system prompt (turns[0]) and the last
    keep_recent turns are never touched.
      1. Replace old tool outputs with stubs (no model call; re-runnable).
      2. Only if still over budget, summarise the old turns (one model call)."""
    def size(ts: list[Turn]) -> int:
        return sum(estimate_tokens(t.content) for t in ts)

    if size(turns) <= budget:
        return turns
    head, old, recent = turns[:1], turns[1:-keep_recent], turns[-keep_recent:]
    old = [_stub(t) for t in old]
    if old and size(head + old + recent) > budget:
        old = [Turn("user", f"Summary of {len(old)} earlier turns (reference only): {summarize(old)}")]
    result = head + old + recent
    if size(result) > budget:
        raise ValueError(f"context is {size(result)} tokens after compaction; budget is {budget}")
    return result


HANDBOOK = """# Refunds
Annual plans are refunded in full within 30 days of purchase.
Monthly plans are not refundable, but you can cancel before renewal.
# Payment errors
Error E4012 means the card issuer declined the charge. Ask the customer to contact their bank.
# Invoices
Invoices are emailed on the first day of each billing cycle."""

if __name__ == "__main__":
    store = MemoryStore(ConceptEmbedder())
    store.add_document("acme", "billing-faq", HANDBOOK)
    store.add_document("globex", "billing-faq", "# Refunds\nGlobex refunds every plan within 90 days.")
    store.remember("acme", "user-42", "Wants invoices sent to ana@example.com, not the account owner.", ttl_s=30 * 86400)

    for query, owner in [("How long is the refund window?", None), ("What does E4012 mean?", None),
                         ("where should my invoices be sent", "user-42"),
                         ("where should my invoices be sent", "user-7")]:
        print(f"\n> {query}  [tenant=acme, user={owner}]")
        for hit in store.search(query, "acme", owner):
            print(f"  {hit.record.id:<16} rrf={hit.score:.4f} bm25#={hit.lexical_rank} vec#={hit.vector_rank}"
                  f"  {hit.record.text[:60]}")

    turns = [Turn("system", "You are a billing assistant.")] + [
        Turn(role, text) for role, text in [("user", "Why was I charged twice?"),
                                            ("tool", "transaction log line\n" * 120),
                                            ("assistant", "One charge was a pre-authorisation."),
                                            ("user", "And the refund?"), ("tool", "refund status: pending"),
                                            ("assistant", "It is pending."), ("user", "How long will it take?")]]
    compacted = compact(turns, budget=200, summarize=lambda ts: "user asked about a double charge")
    print(f"\ncontext: {sum(estimate_tokens(t.content) for t in turns)} -> "
          f"{sum(estimate_tokens(t.content) for t in compacted)} tokens (budget 200)")
    for turn in compacted:
        print(f"  {turn.role:<9} {turn.content[:80]}")
