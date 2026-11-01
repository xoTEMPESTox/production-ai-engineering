import unittest
import zlib

from example import Consumer, ConsumerGroup, ConversationAgent, PartitionedLog


def seeded_log() -> PartitionedLog:
    log = PartitionedLog(partitions=4)
    for seq, (conv, action, item) in enumerate([("conv-a", "add", "laptop"), ("conv-e", "add", "dock"),
                                                ("conv-a", "add", "bag"), ("conv-d", "add", "mouse"),
                                                ("conv-a", "remove", "laptop"), ("conv-e", "add", "cable")]):
        log.append(conv, {"seq": seq, "action": action, "item": item})
    return log


class TestPartitioning(unittest.TestCase):
    def test_same_key_same_partition_in_append_order(self) -> None:
        log = seeded_log()
        p = log.partition_for("conv-a")
        records = log.read(p, 0)
        self.assertEqual([r.value["seq"] for r in records if r.key == "conv-a"], [0, 2, 4])
        self.assertEqual([r.offset for r in records], list(range(len(records))))

    def test_partitioning_is_stable_across_processes(self) -> None:
        # zlib.crc32 doesn't change between runs, unlike Python's randomised hash()
        self.assertEqual(PartitionedLog(4).partition_for("conv-a"), zlib.crc32(b"conv-a") % 4)

    def test_keys_spread_across_partitions(self) -> None:
        log = PartitionedLog(partitions=4)
        used = {log.partition_for(f"conv-{i}") for i in range(50)}
        self.assertEqual(used, {0, 1, 2, 3})


class TestConsumerGroup(unittest.TestCase):
    def test_every_partition_has_exactly_one_owner(self) -> None:
        group = ConsumerGroup(PartitionedLog(partitions=5))
        assignment = group.rebalance(["c2", "c1", "c3"])
        owned = sorted(p for parts in assignment.values() for p in parts)
        self.assertEqual(owned, [0, 1, 2, 3, 4])

    def test_rebalance_after_a_member_leaves_keeps_progress(self) -> None:
        log, agent = seeded_log(), ConversationAgent()
        group = ConsumerGroup(log)
        group.rebalance(["c1", "c2"])
        Consumer("c1", group, agent).poll()
        Consumer("c2", group, agent).poll()
        group.rebalance(["c1"])
        self.assertEqual(Consumer("c1", group, agent).poll(), 0)  # nothing reprocessed
        self.assertEqual(sum(group.committed.values()), 6)


class TestDelivery(unittest.TestCase):
    def test_crash_before_commit_redelivers_and_idempotency_absorbs_it(self) -> None:
        log, agent = seeded_log(), ConversationAgent()
        group = ConsumerGroup(log)
        group.rebalance(["c1"])
        calls: list[str] = []

        def counting(record):
            calls.append(record.record_id)
            agent(record)

        Consumer("c1", group, counting).poll(crash_after=2)
        Consumer("c1", group, counting).poll()
        self.assertEqual(len(calls), 7)                # one record delivered twice
        self.assertEqual(agent.carts["conv-a"], ["bag"])
        self.assertEqual(agent.carts["conv-e"], ["dock", "cable"])  # not dock twice

    def test_events_of_one_conversation_are_applied_in_order(self) -> None:
        log, agent = seeded_log(), ConversationAgent()
        group = ConsumerGroup(log)
        group.rebalance(["c1", "c2"])
        for name in ("c2", "c1"):  # consumers run in any order; keys stay ordered
            Consumer(name, group, agent).poll()
        self.assertEqual(agent.order_by_key, {"conv-a": [0, 2, 4], "conv-e": [1, 5], "conv-d": [3]})
        self.assertEqual(agent.carts["conv-a"], ["bag"])  # remove came after add

    def test_replaying_from_zero_changes_nothing(self) -> None:
        log, agent = seeded_log(), ConversationAgent()
        group = ConsumerGroup(log)
        group.rebalance(["c1"])
        Consumer("c1", group, agent).poll()
        before = {k: list(v) for k, v in agent.carts.items()}
        group.committed.clear()  # e.g. a deliberate replay after a bug fix
        Consumer("c1", group, agent).poll()
        self.assertEqual(agent.carts, before)

    def test_poison_record_is_dead_lettered_and_the_partition_moves_on(self) -> None:
        log, agent = PartitionedLog(partitions=1), ConversationAgent()
        log.append("conv-a", {"seq": 0, "action": "add", "item": "x"})
        log.append("conv-a", {"seq": 1, "action": "add", "corrupt": True})
        log.append("conv-a", {"seq": 2, "action": "add", "item": "y"})
        group = ConsumerGroup(log)
        group.rebalance(["c1"])
        consumer = Consumer("c1", group, agent, max_attempts=3)
        consumer.poll()
        self.assertEqual(agent.carts["conv-a"], ["x", "y"])
        self.assertEqual([(d.record.offset, d.attempts) for d in consumer.dead_letters], [(1, 3)])
        self.assertEqual(group.committed[0], 3)


if __name__ == "__main__":
    unittest.main()
