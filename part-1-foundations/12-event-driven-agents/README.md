# Lesson 12: Event-Driven Agents

Learn how to start agents from things that happen, not just from requests: a transactional outbox, at-least-once delivery, and consumers that are safe to run twice.

> **Pillars:** Agent · Harness · RAG  |  **Harness layer:** triggers, meaning how work starts and how often it really runs
>
> *Write the event with the change, deliver it at least once, and make handling it twice harmless.*

## Why does this exist?
Many agents don't wait to be asked. A document changes, so an agent re-indexes and re-summarises it, which keeps RAG answers fresh (Lesson 05's stale-chunk failure). A ticket is created, so a triage agent drafts a first reply. A payment fails, so an agent prepares a message for a person to approve.

Events decouple the producer from the agent: the service that saves the document doesn't wait twenty seconds for a model call, and several consumers can react to one change. The hard part is delivery. Events get lost (the index goes silently stale), duplicated (the model is paid twice, the email sent twice) or reordered (an old version overwrites a new one).

---

## What problem does it solve?
1. **The dual write**: saving the change and publishing the event touch two systems, and a crash between them loses or invents an event.
2. **At-least-once delivery**: real brokers redeliver after crashes and timeouts, so consumers see duplicates.
3. **Ordering**: events for the same document can arrive out of order, because of retries, partitions or parallel consumers.
4. **Expensive consumers**: an agent consumer calls a model, so a duplicate costs money and can repeat a side effect.

---

## How would beginners implement this?
```python
def update_document(doc_id, text):
    db.execute("UPDATE documents SET text = ? WHERE id = ?", (text, doc_id))
    db.commit()
    broker.publish({"type": "document.updated", "doc_id": doc_id, "text": text})  # crash here: lost

def on_document_updated(event):
    summary = llm(f"Summarise: {event['text']}")
    index.upsert(event["doc_id"], event["text"], summary)  # runs again on every redelivery
```

### Why does that fail?
1. **A crash after the commit loses the event.** The change is saved but never announced, and the index stays stale with nobody noticing.
2. **Publishing first invents events.** If the commit then fails, consumers react to a change that never happened.
3. **Every redelivery repeats the work.** The model call and the upsert run again each time the broker resends.
4. **There is no version.** A delayed older event overwrites the newer document in the index.
5. **There is no event ID**, so there is nothing to deduplicate on.

---

## How is this solved in production?
**1. A transactional outbox.** The change and its event are written in **one** database transaction, so they commit together or not at all:
```python
with self.db:  # one transaction
    self.db.execute("INSERT INTO documents ... ON CONFLICT DO UPDATE ...", (doc_id, text, version))
    self.db.execute("INSERT INTO outbox (event_id, payload) VALUES (?, ?)", (event_id, payload))
```
In the demo, an update that fails before commit leaves neither a new document version nor an event.

**2. A relay publishes the outbox.** `relay_once` reads unsent rows in order, publishes them, then marks them sent. If it crashes between those two steps, the next run publishes the same rows again. That is **at-least-once** delivery, and it is the right trade, because duplicates can be handled and losses can't. (Change-data-capture tools that tail the database log are another way to publish an outbox.)

**3. Stable event IDs are the dedup key.** The ID is derived from the change itself (`doc-refund-policy-v2`), not generated freshly at each publish attempt, so every redelivery of the same change carries the same ID.

**4. The consumer is idempotent.** In one transaction on its own database, `ReindexAgent.handle` checks `processed_events`, records the event ID, and applies the effect. The marker and the effect commit together. If the consumer crashes mid-way, both roll back and the redelivery applies the event exactly once.

**5. Versions defeat reordering.** An event older than the version already indexed is recorded as `stale` and ignored. A late v1 can never overwrite v2.

**6. Side effects outside the database are the exception.** The model call can't be rolled back. If the consumer crashes after summarising but before committing, the redelivery pays for the summary again (`test_crash_mid_handling_rolls_back_so_redelivery_applies` pins this down). For cheap calls, accept it. For expensive ones, cache results by event ID. For real-world actions (email, payment), pass the event ID downstream as an idempotency key (Lesson 04).

The demo publishes two versions, crashes the relay once, delivers v2 before v1 and everything twice: one `applied`, one `stale`, three `duplicate`, and one paid summary.

---

## Design Tradeoffs

### Fat events vs. thin events
* **Fat** (this lesson: the event carries the text): the consumer needs no call back to the producer. But payloads grow, the content may be stale by the time it's processed, and sensitive text sits in the broker.
* **Thin** (just the ID and version): the consumer fetches the current state, which is always fresh and handles reordering naturally. It costs a read per event and couples the consumer to the producer's API.

### Outbox + relay vs. publishing directly
* **Publish directly**: one less component and lower latency, but every crash window between the database and the broker loses or invents events.
* **Outbox + relay** (this lesson): no lost or phantom events. It adds a table, a relay process, a little latency, and duplicates the consumers must handle.

### How long to remember processed IDs
* **Forever**: the simplest dedup, but the table grows without bound.
* **With a TTL**: bounded, but the TTL must be longer than the broker's longest redelivery window. Versions are a second line of defence for anything older.

---

## Failure Cases to Watch For
1. **Dual writes**: commit, then publish (losing events), or publish, then commit (inventing them).
2. **Random event IDs per attempt**: duplicates become impossible to recognise.
3. **Check-then-act across transactions**: two consumer instances both see "not processed" and both apply the event. Put the check, the marker and the effect in one transaction, and let a unique key decide.
4. **Version-less updates**: a late event overwrites newer data.
5. **Non-idempotent external effects**: an email or payment repeated on redelivery.
6. **Poison events**: an event that always fails is retried forever and blocks everything behind it. Send it to a dead-letter queue after N attempts (Lessons 17 and 20).
7. **Marking before publishing**: the relay becomes at-most-once, and a crash loses events.
8. **Unbounded dedup tables**: fine in month one, a problem in month twelve.

---

## Try It
```sh
cd part-1-foundations/12-event-driven-agents
python example.py            # SQLite in memory; no services needed
python -m pytest tests.py -q
```
What to watch for: the failed update leaves exactly one outbox row (the first update's). The relay crash makes every event arrive twice. v2 is processed before v1, so v1 is `stale`. And `summaries paid for: 1`.

Experiment: in `ReindexAgent.handle`, remove the version check (`if row and row[0] >= event.version:`). Run the demo again and the late v1 overwrites v2: the index now says 30 days instead of 45, and a second summary is paid for.

**Next:** Lesson 13 makes the agent's own flow explicit: states, guarded transitions, and merge rules that stop one branch from overwriting another's data.
