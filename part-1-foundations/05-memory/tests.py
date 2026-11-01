import unittest

from example import HANDBOOK, ConceptEmbedder, MemoryStore, Turn, chunk_document, compact, estimate_tokens

DAY = 86_400.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def acme_store(clock: FakeClock | None = None) -> MemoryStore:
    store = MemoryStore(ConceptEmbedder(), clock=clock or FakeClock())
    store.add_document("acme", "billing-faq", HANDBOOK)
    return store


class TestChunking(unittest.TestCase):
    def test_chunks_carry_section_and_stable_ids(self) -> None:
        chunks = chunk_document("faq", HANDBOOK)
        self.assertEqual([c[0] for c in chunks], ["faq#0", "faq#1", "faq#2"])
        self.assertEqual([c[1] for c in chunks], ["Refunds", "Payment errors", "Invoices"])

    def test_heading_is_indexed_with_the_chunk(self) -> None:
        # "refunds" appears only in the heading; the body says "refunded"/"refundable".
        hits = acme_store().search("refunds", "acme")
        self.assertEqual(hits[0].record.section, "Refunds")
        self.assertEqual(hits[0].lexical_rank, 1)

    def test_long_lines_are_split_to_the_word_limit(self) -> None:
        chunks = chunk_document("long", "# Notes\n" + " ".join(f"w{i}" for i in range(25)), max_words=10)
        self.assertEqual([len(c[2].split()) for c in chunks], [10, 10, 5])
        self.assertTrue(all(c[1] == "Notes" for c in chunks))


class TestRetrieval(unittest.TestCase):
    def test_paraphrase_found_by_vectors_alone(self) -> None:
        hits = acme_store().search("How long is the refund window?", "acme")
        self.assertEqual(hits[0].record.section, "Refunds")
        self.assertIsNone(hits[0].lexical_rank)  # no exact word in common
        self.assertEqual(hits[0].vector_rank, 1)

    def test_exact_identifier_found_by_bm25_alone(self) -> None:
        hits = acme_store().search("What does E4012 mean?", "acme")
        self.assertEqual(hits[0].record.section, "Payment errors")
        self.assertEqual(hits[0].lexical_rank, 1)
        self.assertIsNone(hits[0].vector_rank)  # the toy embedder can't see the code

    def test_other_tenants_documents_are_never_returned(self) -> None:
        store = acme_store()
        store.add_document("globex", "refund-policy", "# Refunds\nRefund refund refund window window days.")
        for hit in store.search("refund window", "acme", k=10):
            self.assertEqual(hit.record.tenant, "acme")

    def test_same_document_id_in_two_tenants_does_not_collide(self) -> None:
        store = acme_store()
        store.add_document("globex", "billing-faq", "# Refunds\nGlobex refunds every plan within 90 days.")
        acme_hits = store.search("refund", "acme")
        self.assertIn("30 days", acme_hits[0].record.text)

    def test_filtering_before_ranking_still_fills_k(self) -> None:
        store = acme_store()
        for i in range(20):  # another tenant with many better-matching chunks
            store.add_document("globex", f"doc-{i}", "# Invoices\nInvoice invoice invoices emailed bill receipt.")
        hits = store.search("invoice emailed", "acme", k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].record.tenant, "acme")

    def test_user_memory_is_private_to_its_owner(self) -> None:
        store = acme_store()
        store.remember("acme", "user-42", "Wants invoices sent to the finance team.", ttl_s=DAY)

        mine = [h.record.kind for h in store.search("where are invoices sent", "acme", "user-42")]
        theirs = [h.record.kind for h in store.search("where are invoices sent", "acme", "user-7")]
        self.assertIn("memory", mine)
        self.assertNotIn("memory", theirs)


class TestWritePolicy(unittest.TestCase):
    def test_pii_is_redacted_before_storage(self) -> None:
        store = acme_store()
        store.remember("acme", "u1", "Email ana@example.com or call +91 98765 43210 about invoices", ttl_s=DAY)
        text = store.search("invoices", "acme", "u1", k=5)
        stored = [h.record.text for h in text if h.record.kind == "memory"][0]
        self.assertNotIn("ana@example.com", stored)
        self.assertNotIn("98765", stored)
        self.assertIn("[EMAIL]", stored)
        self.assertIn("[PHONE]", stored)

    def test_duplicate_memories_are_stored_once(self) -> None:
        store = acme_store()
        first = store.remember("acme", "u1", "Prefers  invoices by email.", ttl_s=DAY)
        second = store.remember("acme", "u1", "prefers invoices by email.", ttl_s=DAY)
        self.assertEqual(first, second)

    def test_expired_memories_are_invisible_then_purged(self) -> None:
        clock = FakeClock()
        store = acme_store(clock)
        store.remember("acme", "u1", "Prefers invoices by email.", ttl_s=DAY)
        clock.now += 2 * DAY

        kinds = [h.record.kind for h in store.search("invoices email", "acme", "u1", k=5)]
        self.assertNotIn("memory", kinds)
        self.assertEqual(store.purge_expired(), 1)

    def test_forget_removes_only_that_user(self) -> None:
        store = acme_store()
        store.remember("acme", "u1", "Prefers invoices by email.", ttl_s=DAY)
        store.remember("acme", "u2", "Prefers invoices by post.", ttl_s=DAY)
        self.assertEqual(store.forget("acme", "u1"), 1)
        newer = store.remember("acme", "u2", "Prefers invoices in euros.", ttl_s=DAY)
        texts = [h.record.text for h in store.search("invoices", "acme", "u2", k=5)]
        self.assertIn("Prefers invoices by post.", texts)  # not overwritten by the new memory
        self.assertTrue(newer.startswith("mem-u2-"))


def conversation(tool_output: str) -> list[Turn]:
    return [Turn("system", "You are a billing assistant."), Turn("user", "Why two charges?"),
            Turn("tool", tool_output), Turn("assistant", "One was a pre-authorisation."),
            Turn("user", "And the refund?"), Turn("tool", "pending"),
            Turn("assistant", "Pending."), Turn("user", "How long?")]


class TestCompaction(unittest.TestCase):
    def test_under_budget_is_untouched(self) -> None:
        turns = conversation("short log")
        self.assertEqual(compact(turns, 500, summarize=lambda ts: "unused"), turns)

    def test_short_tool_output_is_not_replaced_by_a_longer_stub(self) -> None:
        turns = conversation("ok")
        turns[1] = Turn("user", "Why two charges? " + "context " * 100)
        result = compact(turns, 120, summarize=lambda ts: "s")
        self.assertNotIn("old tool output removed", " ".join(t.content for t in result))

    def test_stubbing_old_tool_output_comes_before_summarising(self) -> None:
        calls: list[int] = []
        turns = conversation("log line\n" * 200)
        result = compact(turns, 200, summarize=lambda ts: calls.append(len(ts)) or "s")

        self.assertEqual(calls, [])  # no model call was needed
        self.assertIn("old tool output removed", result[2].content)
        self.assertEqual(result[-4:], turns[-4:])  # recent turns untouched
        self.assertEqual(result[0], turns[0])      # system prompt untouched

    def test_summary_replaces_old_turns_when_stubs_are_not_enough(self) -> None:
        turns = conversation("x")
        turns[1] = Turn("user", "Why two charges? " + "context " * 300)
        result = compact(turns, 120, summarize=lambda ts: f"{len(ts)} turns about a double charge")

        self.assertEqual(len(result), 1 + 1 + 4)
        self.assertTrue(result[1].content.startswith("Summary of 3 earlier turns"))
        self.assertEqual(result[-1].content, "How long?")
        self.assertLessEqual(sum(estimate_tokens(t.content) for t in result), 120)

    def test_impossible_budget_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            compact(conversation("x"), 5, summarize=lambda ts: "s")


if __name__ == "__main__":
    unittest.main()
