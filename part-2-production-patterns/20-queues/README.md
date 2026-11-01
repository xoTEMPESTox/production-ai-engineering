# Lesson 20: Queues

Learn how event streams keep order and survive failures: partition keys for per-conversation ordering, consumer groups that share partitions, offsets committed after processing, idempotent consumers, and dead letters that don't stall a partition.

> **Pillars:** Harness · Agent  |  **Harness layer:** delivery, meaning in what order work arrives, how often, and who handles it
>
> *Order per key, parallel across keys, commit after you've done the work, and never let one bad record stop the line.*

## Why does this exist?
Lesson 17's work queue treats every job as independent. Many agent workloads aren't. The messages in one conversation, the edits to one document, the events of one order all have to be applied **in order**. "Cancel my order", then "actually, don't" means something very different the other way round. Yet different conversations should still be processed in parallel.

A **partitioned log** (the model behind Kafka-style streams, and close to FIFO queues with a group key) solves both. A key decides the partition, a partition is processed in order by one consumer at a time, and many partitions run in parallel. This lesson builds one and handles its failure modes: crashes between processing and committing, consumers leaving the group, and records that never succeed.

---

## What problem does it solve?
1. **Per-key ordering**: one conversation's events applied in the order they happened.
2. **Parallelism**: different conversations processed at the same time, on different consumers.
3. **Crash recovery**: a consumer that dies mid-stream mustn't lose events or apply them twice.
4. **Membership changes**: consumers come and go (deploys, autoscaling), and their work moves without losing its place.
5. **Poison records**: one unparseable event mustn't block every later event behind it.

---

## How would beginners implement this?
```python
# Producer: any queue, any worker
queue.send({"conversation": conv_id, "action": "remove", "item": "laptop"})

# Several workers, each:
while True:
    event = queue.receive()
    queue.ack(event)    # acknowledge first, so it "doesn't get stuck"
    apply(event)        # if this raises, the event is gone
```

### Why does that fail?
1. **No ordering.** With several workers, "remove laptop" can be applied before "add laptop", so the remove does nothing, and the laptop stays in the cart.
2. **Ack before processing** is at-most-once: a crash in `apply` loses the event.
3. **Ack after processing, with nothing else**, is at-least-once with no deduplication: a crash after `apply` but before the ack applies the event twice.
4. **A poison event** either disappears (acked first) or is retried forever. In an ordered stream, retrying forever blocks the whole conversation.
5. **Nobody owns anything.** When workers come and go, nothing says who should continue a conversation, or from where.

---

## How is this solved in production?
**1. Partition by key.** `partition_for(key)` is a **stable** hash (`zlib.crc32`, never Python's `hash()`, which changes between processes) modulo the partition count. Every event of `conv-a` lands in the same partition, at increasing offsets, so order within a key is guaranteed and order *across* keys isn't promised (nor needed).

**2. A consumer group owns partitions, not records.** `rebalance(members)` gives every partition to exactly one member. Progress lives in the group (`committed`: partition → next offset), not in the consumer, so when a member leaves, its partitions move to another member, which resumes from the committed offsets. In the demo, `consumer-2` dies, and after rebalancing `consumer-1` picks up its partitions where they left off.

**3. Commit after processing:**
```python
for record in log.read(partition, committed[partition]):
    handle(record)                          # do the work first
    group.commit(partition, record.offset + 1)  # then record that it's done
```
A crash between the two means the record is delivered again. That's **at-least-once**, the right default, because duplicates can be handled and losses can't.

**4. Idempotent consumers.** `ConversationAgent` records each `record_id` (`partition:offset`, unique and stable) and skips any it has seen. In production, insert that ID in the same transaction as the effect (Lesson 12). In the demo, `consumer-2` handled "add dock" but died before committing; the record came back to `consumer-1`, which skipped it, and the cart holds one dock, not two. Replaying a partition from offset zero after a bug fix changes nothing that was already applied correctly.

**5. Dead-letter, then move on.** A record that fails `max_attempts` times is parked in `dead_letters` with its error, and its offset is committed so the partition continues. In an ordered stream this is a real trade: the rest of `conv-e` proceeds without the poison event. When later events depend on it, send the whole key to review instead of skipping one record.

---

## Design Tradeoffs

### More partitions vs. fewer
* **More**: more parallelism (one active consumer per partition per group) and smaller per-partition backlogs. More overhead, and rebalances move more state.
* **Fewer**: simpler, with less overhead, but parallelism is capped and one busy key can dominate a partition.
* Changing the count later re-maps keys to new partitions, which breaks per-key ordering during the switch. Choose generously at the start.

### Choosing the partition key
* **Fine-grained** (conversation, document): good spread, and ordering exactly where it's needed.
* **Coarse** (tenant): ordering across a tenant's events, but one large tenant becomes a hot partition.
* The key should be the smallest unit whose events must stay ordered, and nothing coarser.

### A partitioned log vs. a work queue
* **Log** (this lesson): per-key ordering, replay from any offset, many consumer groups reading the same events independently (indexing, analytics, an agent). A poison record affects its key.
* **Work queue** (Lesson 17): no ordering, but each message is retried and dead-lettered on its own, and scaling consumers is simpler. Use it when jobs are independent.

---

## Failure Cases to Watch For
1. **Ack or commit before processing**: at-most-once, and events are lost on a crash.
2. **No idempotency**: at-least-once redelivery applies effects twice.
3. **Unstable partitioning**: a process-randomised hash (or a changed partition count) sends a key's events to different partitions, so order breaks.
4. **Hot partitions**: one huge key (or a coarse key) saturates one consumer while the others idle.
5. **Infinite retries in an ordered stream**: one poison record blocks every later event for that key, or for the whole partition.
6. **Silently skipping a record later events depend on**: dead-letter it, alert, and decide per key.
7. **Progress kept in the consumer**: a rebalance loses its place, and the replacement starts from scratch or skips ahead.
8. **Assuming global order**: only per-partition order exists. Don't build logic that needs order across keys.

---

## Try It
```sh
cd part-2-production-patterns/20-queues
python example.py            # in-memory log, two consumers, one crash
python -m pytest tests.py -q
```
What to watch for: three conversations spread across partitions owned by two consumers. `consumer-2` handles conv-e's first event and dies before committing it. After the rebalance, `consumer-1` owns every partition, re-receives that event and skips it. The order seen per conversation matches the order sent. The corrupt event is dead-lettered, and conv-e's later "add cable" still goes through.

Experiment: in `ConversationAgent.__call__`, remove the `if record.record_id in self.seen: return` check and run the tests. `test_crash_before_commit_redelivers_and_idempotency_absorbs_it` fails, because the redelivered "add dock" puts a second dock in the cart.

**Next:** Lesson 21 streams answers to users as they're generated: server-sent events, partial structured output, cancellation, and time to first token.
