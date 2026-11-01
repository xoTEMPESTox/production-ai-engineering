# Lesson 19: PostgreSQL State Store

Learn how to keep durable run state in a relational database: runs, steps and events tables, JSON state with a version column for optimistic concurrency, and job claiming that never hands the same job to two workers.

> **Pillars:** Harness · Agent  |  **Harness layer:** the system of record, meaning where a run's truth lives and how concurrent writers are kept honest
>
> *Read the version, write only if it hasn't changed, and let the database pick who gets each job.*

## Why does this exist?
Redis (Lesson 18) holds the hot state of runs in progress. Something has to hold the **truth**: every run's current state, the history of its steps, an audit trail of what changed, and the queue of work, all durable, queryable and transactional with the rest of your data. That's a relational database, usually PostgreSQL.

Once several workers, an approval UI (Lesson 08) and an event consumer (Lesson 12) can all touch the same run, the database must also stop them from overwriting each other. That's Lesson 13's lost-update bug again, one layer down.

---

## What problem does it solve?
1. **Durability and history**: current state, every step and every change, surviving any process.
2. **Lost updates**: two writers read version 1, both write, and the first write disappears.
3. **Job claiming**: many workers pulling from one table, with no job taken twice and no worker waiting on another.
4. **Querying inside state**: "all of this tenant's runs for customer cus-17" without loading every run.
5. **Audit**: who changed what, and when, in a record that's only ever appended to.

---

## How would beginners implement this?
```python
def save_run(run):
    db.execute("UPDATE runs SET state = %s WHERE id = %s", (json.dumps(run.state), run.id))

def next_job():
    job = db.fetchone("SELECT id FROM jobs WHERE status = 'queued' LIMIT 1")
    db.execute("UPDATE jobs SET status = 'claimed' WHERE id = %s", (job.id,))
    return job
```

### Why does that fail?
1. **Last write wins.** Worker A adds "refund approved", worker B (holding an older copy) adds "customer called", and B's write erases A's note. Nothing errors.
2. **Select-then-update isn't atomic.** Two workers select the same queued job and both claim it.
3. **The usual "fix", a lock on the table**, makes every worker wait in line.
4. **No history.** The `UPDATE` destroys the previous state, and there's no record of who changed it.
5. **State as opaque text** means every query loads and parses every row.

---

## How is this solved in production?
**1. Three tables plus a job queue.** `runs` holds the current state as JSON (`jsonb` on PostgreSQL) with a `version` column. `steps` has one row per `(run_id, step)`, and its primary key makes each step write-once (Lesson 15's rule). `events` is append-only: `run.created`, `run.updated` and so on. `jobs` holds `status` and `claimed_by`.

**2. Optimistic concurrency with a version column:**
```sql
UPDATE runs SET state = $1, status = $2, version = version + 1
WHERE id = $3 AND version = $4          -- the version you read
```
If no row matched, someone else updated the run since you read it, and you get `StaleWrite` instead of a silent overwrite. The event for the update is inserted in the **same transaction**, and only if the update happened, so the audit trail never records a write that didn't occur.

**3. The retry loop re-applies the change, not the old result.** `update_with_retry(store, run_id, change)` reads the latest state, applies `change` (a function), and writes with the version it read. On `StaleWrite` it re-reads and applies `change` again. In the demo the second worker's note ends up next to the first one's instead of replacing it. A test runs ten threads adding notes at once and finds all ten at the end.

**4. Claiming jobs with `SKIP LOCKED`:**
```sql
UPDATE jobs SET status = 'claimed', claimed_by = $1
WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY id
            FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, run_id
```
`FOR UPDATE` locks the chosen row, and `SKIP LOCKED` makes other workers skip rows that are already locked instead of waiting for them. Many workers claim *different* jobs at the same time, each exactly once. SQLite has no row locks, so the default store takes the database's write lock (`BEGIN IMMEDIATE`) around the select and update. Same guarantee, less concurrency. Add a claim time and a timeout so jobs from dead workers become claimable again (Lessons 10 and 17).

**5. Query inside the JSON, tenant first.** `find_runs(tenant, field, value)` filters by tenant and then by a field inside the state: `state @> '{"customer_id": "cus-17"}'` on PostgreSQL, served by a GIN index, and `json_extract` on SQLite. The tenant condition is always there (Lesson 05's rule, in SQL).

**6. SQLite by default, PostgreSQL when configured.** `SqliteStateStore` uses the same tables and statements, which keeps the lesson runnable offline. `PostgresStateStore` (asyncpg behind a small synchronous wrapper, its own schema per test session) runs the shared tests only when `PAIE_POSTGRES_URL` is set.

---

## Design Tradeoffs

### Optimistic vs. pessimistic concurrency
* **Optimistic** (version column, this lesson): no locks held while a worker thinks, and conflicts are rare and cheap. Under heavy contention on one run, writers keep retrying.
* **Pessimistic** (`SELECT ... FOR UPDATE` held while working): no retries, but the lock is held for the whole step, including any model call, which is terrible when a step takes seconds.

### JSON state vs. normalised columns
* **JSON** (this lesson): the state's shape can evolve without migrations (version it, Lesson 15), and one row is the whole state. Constraints and types inside it aren't enforced by the database.
* **Columns**: types, constraints, joins and plain indexes. Every shape change is a migration. A common mix: columns for what you filter and join on (tenant, status, customer), JSON for the rest.

### The database as a queue vs. a dedicated broker
* **Table + `SKIP LOCKED`**: one system, and a transaction can update state and enqueue work atomically, which is an outbox for free (Lesson 12). Polling adds load, and very high throughput strains it.
* **A dedicated broker** (Lesson 20): built for throughput, fan-out and consumer groups. It's another system, and atomic "update and enqueue" needs an outbox.

---

## Failure Cases to Watch For
1. **Blind `UPDATE`s**: last writer wins, silently.
2. **Retrying with the stale result** instead of re-applying the change to fresh state.
3. **Select-then-update job claims**, or table locks that serialise every worker.
4. **Holding row locks across model calls**: one slow call blocks everyone behind it.
5. **Audit events written outside the transaction**: records of writes that never happened, or writes with no record.
6. **Queries without the tenant filter**: the cross-tenant leak, in SQL this time.
7. **JSON with no index for what you query**: sequential scans as runs pile up. Index containment with GIN, or a specific field with an expression index.
8. **Claimed jobs that never expire**: a crashed worker's job stays claimed forever.

---

## Try It
```sh
cd part-2-production-patterns/19-postgresql-state-store
python example.py            # SQLite in memory
python -m pytest tests.py -q # PostgreSQL tests are skipped unless PAIE_POSTGRES_URL is set
```
What to watch for: the second worker's blind overwrite is refused, and the retry loop keeps both notes at version 3. A duplicate step is rejected. The events show one `run.updated` per successful write. The JSON lookup finds `run-7` for acme and nothing for globex. Four workers claim three jobs: each job once, and the fourth worker gets `None`.

Experiment: in `SqliteStateStore.update_run`, change `WHERE id = ? AND version = ?` so the version isn't checked (`AND ? > 0` keeps the parameter count), and run the tests. `test_stale_write_is_refused_and_leaves_no_trace` fails: the stale write goes through and erases the first worker's note.

**Next:** Lesson 20 looks at queues in their own right: at-least-once delivery, ordering by partition key, dead-letter queues and idempotent consumers.
