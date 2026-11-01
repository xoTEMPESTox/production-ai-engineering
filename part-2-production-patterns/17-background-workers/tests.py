import threading
import time
import unittest
from collections import Counter

from example import InMemoryQueue, SingleFlight, Worker, run_pool


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_queue(clock: Clock, **kwargs) -> InMemoryQueue:
    return InMemoryQueue(clock=clock, visibility_timeout_s=30, max_receives=3, **kwargs)


class TestQueueSemantics(unittest.TestCase):
    def test_received_message_is_hidden_until_the_timeout(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        queue.send({"doc_id": "d1"})
        first = queue.receive()
        self.assertIsNone(queue.receive())  # another worker can't see it
        clock.now = 31
        again = queue.receive()  # never acked: redelivered
        self.assertEqual((again.id, again.receive_count), (first.id, 2))

    def test_ack_removes_the_message(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        queue.send({"doc_id": "d1"})
        queue.ack(queue.receive().id)
        clock.now = 100
        self.assertIsNone(queue.receive())

    def test_long_job_extends_its_visibility(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        queue.send({"doc_id": "d1"})
        message = queue.receive()
        clock.now = 25
        queue.extend(message.id, 30)
        clock.now = 40  # past the original timeout
        self.assertIsNone(queue.receive())

    def test_poison_message_goes_to_the_dead_letter_queue(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        def always_fails(doc_id: str) -> None:
            raise ValueError("corrupt")

        worker = Worker(queue, SingleFlight(), always_fails)
        queue.send({"doc_id": "bad"})
        for _ in range(4):
            message = queue.receive()
            if message:
                worker.handle(message)
            clock.now += 31
        self.assertEqual(len(queue.dead_letters), 1)
        self.assertEqual(queue.dead_letters[0].last_error, "ValueError: corrupt")
        self.assertEqual(queue.messages, {})

    def test_poison_message_does_not_block_others(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        done: list[str] = []

        def process(doc_id: str) -> None:
            if doc_id == "bad":
                raise ValueError("corrupt")
            done.append(doc_id)

        worker = Worker(queue, SingleFlight(), process)
        queue.send({"doc_id": "bad"})
        queue.send({"doc_id": "good"})
        while (message := queue.receive()) is not None:
            worker.handle(message)
        self.assertEqual(done, ["good"])  # processed while "bad" waits out its timeout


class TestSingleFlight(unittest.TestCase):
    def test_second_start_is_refused_and_marks_a_follow_up(self) -> None:
        flight = SingleFlight()
        self.assertTrue(flight.try_start("d1"))
        self.assertFalse(flight.try_start("d1"))
        self.assertFalse(flight.try_start("d1"))  # many duplicates, still one follow-up
        self.assertTrue(flight.finish("d1", succeeded=True))
        self.assertTrue(flight.try_start("d1"))
        self.assertFalse(flight.finish("d1", succeeded=True))

    def test_no_follow_up_after_a_failure(self) -> None:
        flight = SingleFlight()
        flight.try_start("d1")
        flight.try_start("d1")
        self.assertFalse(flight.finish("d1", succeeded=False))  # the redelivery will run it

    def test_duplicates_during_a_run_become_one_follow_up_run(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        runs: Counter = Counter()

        def process(doc_id: str) -> None:
            runs[doc_id] += 1
            if runs[doc_id] == 1:  # the two duplicates are delivered while the first run is in progress
                for _ in range(2):
                    worker.handle(queue.receive())

        worker = Worker(queue, SingleFlight(), process)
        for _ in range(3):
            queue.send({"doc_id": "d1"})
        while (message := queue.receive()) is not None:
            worker.handle(message)
        self.assertEqual(runs["d1"], 2)  # one run plus one follow-up, not three
        self.assertEqual(sum("coalesced" in line for line in worker.log), 2)
        self.assertEqual(queue.messages, {})


class TestWorkerPool(unittest.TestCase):
    def test_same_document_never_runs_twice_at_once(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        active, peak, runs, lock = Counter(), Counter(), Counter(), threading.Lock()

        def process(doc_id: str) -> None:
            with lock:
                active[doc_id] += 1
                peak[doc_id] = max(peak[doc_id], active[doc_id])
                runs[doc_id] += 1
            time.sleep(0.03)
            with lock:
                active[doc_id] -= 1

        for doc_id in ["d1"] * 5 + ["d2"] * 5:
            queue.send({"doc_id": doc_id})
        run_pool(Worker(queue, SingleFlight(), process), threads=4)

        self.assertEqual(peak, Counter({"d1": 1, "d2": 1}))
        self.assertLessEqual(runs["d1"], 5)
        self.assertEqual(queue.messages, {})

    def test_crashed_worker_message_is_redelivered(self) -> None:
        clock = Clock()
        queue = make_queue(clock)
        done: list[str] = []
        queue.send({"doc_id": "d1"})
        queue.receive()  # a worker took it and died: no ack, no fail
        clock.now = 31
        run_pool(Worker(queue, SingleFlight(), done.append), threads=2)
        self.assertEqual(done, ["d1"])


if __name__ == "__main__":
    unittest.main()
