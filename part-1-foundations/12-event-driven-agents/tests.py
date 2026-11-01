import unittest

from example import Broker, DocumentService, Event, ReindexAgent, relay_once


def outbox_count(service: DocumentService) -> int:
    return service.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> str:
        self.calls.append(text)
        return f"summary of {text}"


class TestOutbox(unittest.TestCase):
    def test_change_and_event_commit_together(self) -> None:
        service = DocumentService()
        service.update_document("d1", "v1 text")
        self.assertEqual(outbox_count(service), 1)

    def test_failed_write_leaves_no_phantom_event(self) -> None:
        service = DocumentService()
        with self.assertRaises(RuntimeError):
            service.update_document("d1", "draft", fail_after_write=True)
        self.assertEqual(outbox_count(service), 0)
        self.assertIsNone(service.db.execute("SELECT * FROM documents").fetchone())

    def test_relay_publishes_in_order_and_only_once_when_healthy(self) -> None:
        service, broker = DocumentService(), Broker()
        service.update_document("d1", "a")
        service.update_document("d1", "b")
        self.assertEqual(relay_once(service, broker), 2)
        self.assertEqual(relay_once(service, broker), 0)
        self.assertEqual([e.version for e in broker.drain()], [1, 2])

    def test_relay_crash_means_redelivery_not_loss(self) -> None:
        service, broker = DocumentService(), Broker()
        service.update_document("d1", "a")
        relay_once(service, broker, crash_before_marking=True)
        relay_once(service, broker)
        self.assertEqual([e.event_id for e in broker.drain()], ["doc-d1-v1", "doc-d1-v1"])


class TestIdempotentConsumer(unittest.TestCase):
    def event(self, version: int, text: str = "t") -> Event:
        return Event(f"doc-d1-v{version}", "document.updated", "d1", version, text)

    def test_duplicate_delivery_is_applied_once(self) -> None:
        summarize = Recorder()
        agent = ReindexAgent(summarize)
        self.assertEqual(agent.handle(self.event(1, "hello")), "applied")
        self.assertEqual(agent.handle(self.event(1, "hello")), "duplicate")
        self.assertEqual(summarize.calls, ["hello"])  # the model was paid once

    def test_older_version_arriving_late_is_ignored(self) -> None:
        agent = ReindexAgent(Recorder())
        agent.handle(self.event(2, "new"))
        self.assertEqual(agent.handle(self.event(1, "old")), "stale")
        self.assertEqual(agent.indexed("d1"), ("summary of new", 2))

    def test_crash_mid_handling_rolls_back_so_redelivery_applies(self) -> None:
        summarize = Recorder()
        agent = ReindexAgent(summarize)
        with self.assertRaises(RuntimeError):
            agent.handle(self.event(1, "hello"), crash_before_commit=True)
        self.assertIsNone(agent.indexed("d1"))                   # effect rolled back...
        self.assertEqual(agent.handle(self.event(1, "hello")), "applied")  # ...and marker too
        self.assertEqual(agent.indexed("d1"), ("summary of hello", 1))
        # The model call is outside the transaction: it was paid for twice.
        self.assertEqual(len(summarize.calls), 2)


class TestEndToEnd(unittest.TestCase):
    def test_duplicates_and_reordering_converge_to_the_latest_version(self) -> None:
        service, broker, summarize = DocumentService(), Broker(), Recorder()
        agent = ReindexAgent(summarize)
        service.update_document("d1", "first")
        service.update_document("d1", "second")
        relay_once(service, broker, crash_before_marking=True)
        relay_once(service, broker)
        events = broker.drain()

        outcomes = [agent.handle(e) for e in reversed(events)]  # newest first, everything twice
        self.assertEqual(outcomes.count("applied"), 1)
        self.assertEqual(agent.indexed("d1"), ("summary of second", 2))
        self.assertEqual(summarize.calls, ["second"])


if __name__ == "__main__":
    unittest.main()
