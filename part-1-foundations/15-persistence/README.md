# Lesson 15: Persistence

Learn how to make an agent run outlive the process running it: a checkpoint after every step, restore and rewind, and state schemas that are versioned and migrated.

> **Pillars:** Harness · Agent  |  **Harness layer:** durability, meaning a run's state survives crashes, deploys and code changes
>
> *Checkpoint every step, version every checkpoint, and refuse what you don't understand.*

## Why does this exist?
Lessons 08 and 10 paused and resumed work, and both need somewhere to keep a run's state while no process holds it. A deploy, a crash or a scale-down kills the process. A run fourteen steps in, with real money in model calls behind it, shouldn't start over.

The code that reads a checkpoint next week also isn't the code that wrote it. Fields get renamed, added and restructured. Persistence is half about surviving crashes and half about surviving your own changes.

---

## What problem does it solve?
1. **Crash recovery** without repeating completed work, or paying for it twice.
2. **Inspection and rewind**: debug from step N, or re-run from a known good point.
3. **Schema evolution**: old checkpoints must load in new code, and new checkpoints must not be quietly mangled by old code during a rollback.
4. **Safety**: atomic writes, one writer per step, bounded size.

---

## How would beginners implement this?
```python
def save(state):
    with open(f"runs/{state['run_id']}.pkl", "wb") as f:
        pickle.dump(state, f)  # overwrites the only copy

def load(run_id):
    with open(f"runs/{run_id}.pkl", "rb") as f:
        return pickle.load(f)  # in whatever shape it had when written
```

### Why does that fail?
1. **A crash mid-write corrupts the only copy.** Overwriting in place isn't atomic.
2. **There is no history.** One file per run, overwritten each time, means you can't rewind or see how the state evolved.
3. **pickle is the wrong format.** It's tied to class definitions, so renaming a class or field breaks loading, and unpickling data you don't fully trust can execute code.
4. **There is no version.** New code reads an old shape and either crashes deep inside the agent or silently fills in defaults.
5. **Two workers can write the same run**, and the last write wins.
6. **Big tool outputs and file bytes end up in the state.** Files grow, and every save gets slower.

---

## How is this solved in production?
**1. Append a row per step, never overwrite.** The table is `checkpoints(run_id, step, schema_version, state, created_at)` with `PRIMARY KEY (run_id, step)`. Each step inserts one row inside a transaction, so a crash leaves the previous checkpoints intact.

**2. Resume from the latest checkpoint.** `run_agent` loads the latest row and continues from `state.step`. In the demo, the process dies after step 3, and the new process finishes steps 4 and 5: five model calls in total, not eight. There is one window to know about: a crash *after* a model call but *before* its checkpoint repeats that step, so steps with side effects still need idempotency keys (Lessons 04 and 10).

**3. Rewind to any step.** `load(run_id, step=2)` returns the state as it was after step 2, for debugging or for re-running from a known point.

**4. Use JSON and Pydantic, not pickle.** Checkpoints are readable and language-neutral, and they're validated on load: `extra="forbid"` turns an unexpected field into an error rather than a silent drop.

**5. Every row records its schema version, and loading migrates it:**
```python
MIGRATIONS = {1: _v1_to_v2,   # "history" strings -> messages with roles
              2: _v2_to_v3}   # "todo" -> "plan"; start counting tokens_used

def migrate(data, version):
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(...)          # written by newer code: don't guess
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)  # pure functions, one per version bump
        version += 1
    return data
```
The migrated data is then validated against the current `RunState`. In the demo, a checkpoint written by v1 code loads as a valid v3 state with its conversation intact.

**6. Refuse schemas from the future.** During a rollback, old code meets checkpoints written by the new version. Stripping the fields it doesn't know and saving the result back would damage the run, so `SchemaTooNew` stops it.

**7. One write per run and step.** The primary key turns a second writer for the same step into a `CheckpointConflict`, a minimal form of Lesson 10's fencing. Lesson 19 does this with a version column in Postgres.

**8. Bound the size.** State over 64 KB is refused. Large artifacts (documents, tool dumps, images) go to blob storage and are referenced by ID, which avoids Lesson 01's state-bloat failure.

---

## Design Tradeoffs

### Full snapshot per step vs. deltas
* **Snapshots** (this lesson): any step loads with one read, and rewind is trivial. Storage grows with state size times steps.
* **Deltas (event sourcing)**: compact, with a perfect audit of every change. Loading a step means replaying from the start or from a periodic snapshot.

### Migrate on read vs. migrate in a batch
* **On read** (this lesson): no downtime, and old rows stay as they were written. Every load pays the migration cost, and the migration code must be kept forever.
* **Batch**: rewrite every row to the new version once, then delete the old migrations. It needs a job, and care with runs that are in flight while it executes.

### SQLite vs. Postgres vs. Redis for checkpoints
* **SQLite**: no server, one file, one writer at a time. Ideal locally and for single-process services, and the default here.
* **Postgres**: concurrent writers, strong durability, and queries across runs (Lesson 19).
* **Redis**: fast, with built-in TTLs. Durability depends on its persistence settings (Lesson 18).

---

## Failure Cases to Watch For
1. **Overwriting the only copy**: a crash mid-save corrupts the run.
2. **pickle for state**: brittle across code changes, and unsafe on untrusted input.
3. **No schema version**: every format change becomes a guessing game.
4. **Misleading migration defaults**: `tokens_used = 0` for migrated runs understates their spend. Make defaults honest, or mark migrated values.
5. **Old code writing after a rollback**: it strips new fields and saves the damaged state back.
6. **Checkpointing too rarely** (work repeated after a crash) or **storing blobs** (slow saves, huge rows).
7. **Concurrent writers** to the same run without a uniqueness or version check.
8. **Keeping everything forever**: transcripts contain personal data, so define a retention period and delete on schedule.

---

## Try It
```sh
cd part-1-foundations/15-persistence
python example.py            # SQLite in a temp folder; no services needed
python -m pytest tests.py -q
```
What to watch for: the run dies after step 3, and a new `CheckpointStore` on the same file finishes it with `model calls 5 (not 8)`. Rewinding to step 2 shows exactly two messages. A v1-shaped checkpoint comes back as v3, with roles assigned and `todo` renamed to `plan`. A v4 checkpoint is refused.

Experiment: in `CheckpointStore.save`, change `INSERT INTO` to `INSERT OR REPLACE INTO` and run the tests. `test_a_step_can_only_be_written_once` fails: a second writer silently replaces step 1, and the first writer's state is gone.

**Next:** Part 2 starts with Lesson 16, which does retries properly: exponential backoff with full jitter, retry budgets, `Retry-After`, and retrying only what is retryable.
