# Lesson 01: State

Learn how to represent, update, and manage agent state in production AI systems.

> **Pillars:** Harness · Agent  |  **Harness layer:** state, meaning the record of what the agent has done and what it knows
>
> The **harness** is all the code around the model call that makes an agent safe to run: the loop, tools, state, limits, retries and logs. Each lesson tags the pillars it teaches (Agent, RAG or retrieval-augmented generation, and Harness) and the harness layer it builds.
>
> *Never change state in place: every update is a new version you can audit and roll back.*

## Why does this exist?
Agentic applications are dynamic. Unlike standard CRUD applications that transition through highly predictable pathways, an AI agent takes actions based on LLM outputs, executes tools, retrieves memory, and continuously re-evaluates its progress. Throughout this execution loop, the agent needs to maintain a coherent record of what it has done, what it has discovered, and what steps remain.

This record is called the **agent state**.

---

## What problem does it solve?
An AI agent needs to track:
1. **Conversation History**: Messages exchanged with the user and external tools.
2. **Execution Context**: Extracted parameters, variables, files, or system prompts.
3. **Execution Plan**: Subtasks remaining, completed tasks, and current goals.
4. **Harness Metadata**: Token count, latency, retry counts, and system flags.

Without a well-defined state mechanism, agent code quickly becomes a spaghetti of global variables or complex mutable dictionaries, making debugging, testing, state recovery, and concurrent operations impossible.

---

## How would beginners implement this?
Beginners typically implement state using simple in-memory, mutable Python dictionaries or lists passed around between functions:

```python
# The naive approach
state = {
    "messages": [],
    "current_task": "find_bugs",
    "retries": 0
}

def add_message(content):
    state["messages"].append({"role": "user", "content": content})
    # State is mutated in-place with no history or safety locks
```

### Why does that fail?
1. **No Immutability**: Any component can modify the state anywhere, leading to race conditions in concurrent/multi-agent environments.
2. **No Undo/Rollback Capabilities**: If an agent takes a wrong path or a tool fails, it is extremely difficult to "roll back" the state to a previous checkpoint.
3. **No Audit Trails**: In production, developers must trace *exactly* why an agent took a certain action. If state is mutated in-place, the history of how the state evolved is lost.
4. **Poor Serialization**: Passing arbitrary, nested mutable objects makes persisting the state to databases (like PostgreSQL or Redis) complex and error-prone.

---

## How is this solved in production?
Production systems treat state as a sequence of versions that are never edited. `StateStore` in `example.py` shows the whole pattern.

**1. A typed, frozen snapshot.** `AgentState` is a frozen dataclass holding the messages, the current task, the execution plan, metadata and a `version` number. Every field is declared, so a typo is an error rather than a new key, and the state serialises cleanly when it has to be stored (Lesson 15).

**2. Every update creates a new version.** `update(**changes)` never edits the current state. It builds a new `AgentState` from the old one plus the changes, and bumps the version:
```python
with self._lock:                                      # one writer at a time
    execution_plan = self._current_state.execution_plan
    if "execution_plan" in kwargs:
        execution_plan = copy.deepcopy(kwargs["execution_plan"])  # the caller keeps its own list
    new_state = AgentState(..., execution_plan=execution_plan,
                           version=self._current_state.version + 1)
    self._current_state = new_state
    self._history.append(new_state)                   # every version is kept
    return copy.deepcopy(new_state)                   # callers never get the stored object
```

**3. Metadata merges instead of overwriting.** `update(metadata={"tokens": 1200})` adds or replaces that one key and keeps the rest. Replacing the whole dict would silently drop what another step wrote; Lesson 13 comes back to this as merge reducers.

**4. The history is the audit trail.** Every version stays in the history, so "what did the agent know at step 3?" has an exact answer. `get_history()` returns copies of all of them.

**5. Rollback is a new version, not an erasure.** `rollback(1)` creates version 3 with version 1's content. Version 2 stays in the history, so the record shows that the agent took a wrong path and when it came back.

**6. One lock around every read-modify-write.** Without it, two threads both read version 7 and both write version 8, and one update is lost. A test runs 50 threads making 500 updates and checks that the final version is exactly 500. Across processes, the lock becomes a database transaction or a version check (Lessons 18 and 19).

**7. The store never hands out its own objects.** A frozen dataclass stops `state.execution_plan = [...]` but not `state.execution_plan.append(...)`, so `StateStore` returns deep copies. The stricter alternative is tuples and read-only mappings, so a mutation raises an error instead of being silently ignored.

---

## Design Tradeoffs

### In-Memory vs. Database-backed State
* **In-Memory**: Extremely fast, simple to implement. However, state is lost if the process crashes, and horizontal scaling is impossible.
* **Database-backed (PostgreSQL/Redis)**: Restarts and scaling work seamlessly, and history is persisted. However, it introduces serialization overhead and network latency.

### Complete Copy vs. Event Sourcing
* **Complete Copy**: Creating a deep copy of the state on every update is simple and safe for small states.
* **Event Sourcing (Deltas)**: Storing only changes (deltas) is memory-efficient and perfect for large conversation histories. However, compiling the state requires replaying the history of transitions.

---

## Failure Cases to Watch For
1. **Unintentional Mutations**: Modifying nested lists (like `messages.append()`) directly. Always use copy-on-write.
2. **State Bloat**: Storing raw images, PDF bytes, or long file contents directly in the state. Instead, store file paths or object storage references.
3. **Out-of-Order Updates**: In multi-agent or asynchronous settings, delayed responses might overwrite newer state transitions. Always use version numbers or transaction tokens.

---

## Try It
```sh
cd part-1-foundations/01-state
python example.py
python -m pytest tests.py -q
```
What to watch for: the demo prints four versions, and v3 (the rollback) has the same plan as v1 while v2 stays in the history. The tests check that an update returns a *new* state and leaves the old one unchanged, that changing a state the store handed out (`store.get_state().execution_plan.append(...)`) can't rewrite history, that rolling back to version 1 is recorded as a new version (3) rather than erasing history, and that 50 threads making 500 updates end at exactly version 500.

Experiment: in `StateStore.update`, replace `copy.deepcopy(kwargs["execution_plan"])` with `kwargs["execution_plan"]` and run the tests. `test_copy_on_write_leak_prevention` fails: the caller's later `plan.append(...)` now changes the stored state behind the store's back.

**Next:** Lesson 02 puts this state in motion: a bounded think → act → observe loop with budgets, loop detection and a typed event log.
