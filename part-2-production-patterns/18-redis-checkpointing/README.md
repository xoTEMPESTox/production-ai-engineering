# Lesson 18: Redis Checkpointing

Learn how to use Redis as the fast, short-lived layer for agent runs: key design, checkpoints with TTLs, and per-run locks with `SET NX PX` and an ownership token. An in-memory fake is the default, and real Redis is optional.

> **Pillars:** Harness · Agent  |  **Harness layer:** hot state, meaning the latest checkpoint and who is allowed to work on a run right now
>
> *Build every key in one place, expire everything, and release only what you own.*

## Why does this exist?
Lessons 10 and 15 made runs resumable with leases and checkpoints. With several workers on several machines, that state needs a shared home that's fast enough to touch on every step. Redis is the common choice: single-key operations are atomic, keys can expire on their own, and reads and writes take well under a millisecond.

It also has sharp edges. A lock released by the wrong worker, keys built from user input, checkpoints that never expire, or memory eviction quietly deleting a lock: each turns a fast store into a source of duplicate runs and lost state. This lesson builds the safe version against an in-memory fake with the same semantics, and runs the same lock tests on a real server when `PAIE_REDIS_URL` is set.

---

## What problem does it solve?
1. **A shared, fast checkpoint** for runs that move between workers.
2. **One worker per run**, enforced across machines.
3. **Safe hand-over** when a worker stalls past its lock's expiry.
4. **Bounded memory**: hot state that cleans itself up.
5. **Key hygiene**: one naming scheme, safe in cluster mode, impossible to inject into.

---

## How would beginners implement this?
```python
def work_on(run_id):
    if r.get(f"lock_{run_id}"):            # check...
        return
    r.set(f"lock_{run_id}", "1")           # ...then set: two workers both pass
    state = json.loads(r.get(run_id) or "{}")
    ...
    r.set(run_id, json.dumps(state))       # no TTL: lives forever
    r.delete(f"lock_{run_id}")             # deletes whoever's lock is there now
```

### Why does that fail?
1. **Check-then-set isn't atomic.** Two workers both see no lock and both take it.
2. **The lock never expires.** A crashed worker holds the run forever.
3. **`delete` releases whoever's lock is there.** If worker A stalled past an expiry and worker B took over, A's `delete` removes **B's** lock, and a third worker walks in.
4. **No TTL on state.** Memory grows forever, until the eviction policy starts deleting things for you, possibly including locks.
5. **Ad-hoc key names** (`lock_x`, `x`, `x_state`) collide across environments and features, and a `run_id` taken from user input can contain anything.

---

## How is this solved in production?
**1. Every key is built in one place.** `Keys` produces names like `paie:dev:v1:run:{run-7}:lock`:
* **`paie`, `dev`**: application and environment, so staging can never touch production keys on a shared server;
* **`v1`**: the state-format version, so a new format can coexist with the old one during a deploy (Lesson 15's migrations, for Redis);
* **`{run-7}`**: a Redis Cluster hash tag, so every key of one run lands in the same slot and multi-key operations on it stay possible;
* **run IDs are validated** against a strict pattern, never pasted in from user input.

**2. A lock is `SET key token NX PX ttl`.** One atomic command: set only if absent (`NX`), with an expiry (`PX`). The value is a random token that only the acquiring worker knows.
```python
token = secrets.token_hex(16)
acquired = store.set(keys.lock(run_id), token, px=ttl_ms, nx=True)
```

**3. Release and refresh are compare-and-act.** `release` deletes the lock only if it still holds *your* token, and `refresh` extends it only on the same condition. On real Redis each is a small Lua script, so the check and the action are one atomic step:
```lua
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
```
In the demo, worker A stalls for 31 seconds, its 30-second lock expires, and worker B takes over. When A wakes and releases with its old token, nothing happens, and B still holds the lock.

**4. Checkpoints expire.** Each save writes the step's checkpoint and the `latest` pointer, both with a TTL (seven days by default). Redis holds the *hot* state of runs in progress. Durable history and audit belong in a database (Lesson 19). On a real server, write both keys in one `MULTI` transaction or one Lua script, so a crash can't leave `latest` pointing at nothing.

**5. A lock protects efficiency; correctness needs more.** A lock with a TTL can expire while its owner is merely slow, and after that two workers believe they own the run. That's acceptable when the lock only prevents duplicate *work*. When duplicate *writes* would be wrong, also use fencing tokens on the durable store (Lesson 10) or a database constraint (Lesson 19).

**6. Fake by default, real when configured.** `FakeRedis` implements the same five operations (including expiry) on an injectable clock, so the tests run offline and can move time forward. `RedisAdapter` wraps redis-py, and `TestRealRedisLocks` runs the same lock tests against a server only when `PAIE_REDIS_URL` is set.

---

## Design Tradeoffs

### Redis vs. the database for checkpoints
* **Redis**: sub-millisecond, TTLs built in, natural for "the latest state of runs in progress". Its durability depends on persistence settings (snapshots and append-only-file fsync), and memory is finite.
* **Database** (Lesson 19): durable, queryable, transactional with your business data, but slower per write. A common split is Redis for the hot latest checkpoint and locks, and the database for the full history.

### Lock TTL: short vs. long
* **Short**: a crashed worker's run is picked up quickly, but a slow step outlives the lock unless it refreshes.
* **Long**: fewer false hand-overs, and a long wait after a crash. Refresh at well under the TTL (for example every third of it), like Lesson 10's heartbeat.

### A single-instance lock vs. a multi-node lock algorithm
* **Single instance** (this lesson): simple and fast. If that Redis fails over, a lock can be lost, which is acceptable for efficiency locks.
* **Multi-node algorithms**: more resilient to one node failing, but more complex, and still dependent on timing assumptions. When correctness matters, prefer fencing tokens or database constraints over a stronger lock.

---

## Failure Cases to Watch For
1. **`GET` then `SET` for locks**: a race that gives two owners.
2. **Locks without expiry**: one crash locks a run forever.
3. **Plain `DEL` on release**: deletes the next owner's lock.
4. **No TTL on state**, and an eviction policy that then removes keys under memory pressure, possibly including locks. Run state on an instance configured not to evict (and separate from your cache), or at least know which keys the policy can remove.
5. **Treating Redis as the system of record**: a crash between persistence flushes can lose recent writes.
6. **Key collisions across environments or features**: no namespace, no environment in the key.
7. **Keys built from user input**: wildcards, separators and unbounded lengths.
8. **Multi-key writes without a transaction**: a `latest` pointer to a checkpoint that was never written.

---

## Try It
```sh
cd part-2-production-patterns/18-redis-checkpointing
python example.py            # in-memory fake with a controllable clock
python -m pytest tests.py -q # real-Redis tests are skipped unless PAIE_REDIS_URL is set
# optional, with a local Redis:  PAIE_REDIS_URL=redis://localhost:6379/0 python -m pytest tests.py -q
```
What to watch for: worker B can't take the lock while A holds it, but can after A's TTL, and it resumes from A's latest checkpoint (step 2). A's late release and refresh both return `False`, and B keeps the lock. A week later the checkpoint has expired on its own.

Experiment: in `FakeRedis.delete_if_equals`, drop the `entry[0] != value` check, which makes release a plain `DEL`, and run the tests. `test_release_with_a_stale_token_does_nothing` fails: a wrong token now deletes the real owner's lock.

**Next:** Lesson 19 moves durable run state to PostgreSQL: runs, steps and events tables, JSONB, optimistic concurrency with a version column, and `SKIP LOCKED` job claiming.
