# Lesson 13: State Machines

Learn how to give an agent workflow an explicit shape: declared states, guarded transitions, and merge rules that stop one parallel branch from silently overwriting another's results.

> **Pillars:** Agent · Harness  |  **Harness layer:** control flow and state merging, meaning what may happen next and how parallel results combine
>
> *Declare the edges, guard them, and merge on purpose.*

## Why does this exist?
The agent loop (Lesson 02) lets the model choose any next step, and a plan (Lesson 06) orders the steps. But many production agents are **workflows with a few model decisions inside a fixed shape**: support tickets, intake forms, document processing. That shape has rules. A ticket can't be resolved before a person approves the reply. A high-risk case must escalate. Drafting needs an assessment first. Writing those rules into the prompt ("never resolve without approval") makes them probabilistic. A state machine makes them structural.

Once parts of the workflow run in parallel (a rules check, an account lookup, a model call), their results have to merge into shared state. The default, where each update replaces the key, is the bug that silently drops one branch's findings. It is one of the most common production bugs in graph-based agents. It usually gets blamed on the model ("it ignores part of the history"), when the cause is the merge.

---

## What problem does it solve?
1. **Illegal flows**: skipping review, resolving an escalated case, reopening a closed one.
2. **Hard rules hidden in prompts**: enforced most of the time, which is not the same as always.
3. **Merging parallel results**: two branches writing the same key in the same step.
4. **Debugging**: "why didn't the model consider X?" can only be answered with a record of which step changed which part of the state.

---

## How would beginners implement this?
```python
state = {"status": "received", "flags": [], "context": {}}

def handle(ticket):
    for result in run_in_parallel(billing_check, account_check):
        state.update(result)            # the last branch to finish wins
    if llm(f"Can we resolve this? Flags: {state['flags']}") == "yes":
        state["status"] = "resolved"    # any status, at any time
```

### Why does that fail?
1. **`state.update(result)` replaces `flags` wholesale.** `account_check`'s `["locked_account"]` erases `billing_check`'s `["duplicate_charge"]`, so the model decides without the billing finding. Nothing errors. Which finding survives depends on which branch finished last, so the bug is flaky too.
2. **Status is a string anyone can set.** The model's "yes" resolves an unreviewed ticket, whatever its risk.
3. **There is no record of who changed what**, so the investigation starts in the prompt, which is the wrong place.
4. **Parallel branches share one mutable dict**, which invites races (Lesson 01).

---

## How is this solved in production?
**1. States and edges are declared.** `Phase` is an enum, and `TRANSITIONS` lists every allowed edge with a guard: a pure function that returns `None` (allowed) or the reason the move isn't allowed.
```python
def _ready_to_draft(s):
    if s.risk is None:
        return "risk not assessed"
    if s.risk == "high":
        return "high risk must be escalated"
    return None

TRANSITIONS = {
    Phase.ASSESSING: {
        Phase.DRAFTING:  _ready_to_draft,
        Phase.ESCALATED: lambda s: None if s.risk == "high" else "only high-risk tickets escalate",
    },
    Phase.AWAITING_REVIEW: {
        Phase.RESOLVED: lambda s: None if s.approved_by else "a person must approve the draft",
        Phase.DRAFTING: lambda s: None,  # sent back by the reviewer
    },
    ...
}
```

**2. Illegal moves fail loudly, with a reason.** `transition()` raises `InvalidTransition` for an undeclared edge, listing the allowed ones, and `GuardFailed` when the guard says no, giving its reason. Terminal states have no outgoing edges at all.

**3. The model proposes, and the machine decides.** The model's output (`risk`) is data that guards read. It never sets the phase. A branch that tries to set `phase` through a merge is rejected.

**4. Branches return only what they changed.** Each check returns a partial update such as `{"flags": [...], "context": {...}}`, never a full copy of the state.

**5. Merge semantics are chosen per key:**

| Key | Rule | Why |
|---|---|---|
| `flags` | union (order kept, no duplicates) | every branch's findings must survive |
| `context` | merge keys; the same key with different values is a `MergeConflict` | combine facts, refuse contradictions |
| `log` | append | an audit trail only grows |
| `risk`, `draft`, `approved_by` | plain: one writer per step, or `MergeConflict` | a scalar with two writers is a design error, not a race to win |

**6. Every merge leaves a trace.** States are frozen, and each merge appends lines like `billing_check changed ['context', 'flags']`. With that log, "the billing finding vanished between gathering and assessing" takes minutes to find.

Graph-based agent frameworks such as LangGraph use the same ideas: typed state, nodes that return partial updates, per-key reducers with overwrite as the default, and an error when parallel nodes write a key that has no reducer in the same step. The lesson is to choose each key's rule deliberately, especially for collections.

---

## Design Tradeoffs

### Explicit state machine vs. free agent loop
* **State machine**: the rules are enforced, the possible paths can be listed and tested, and auditors can read the transition table. New behaviour means a new edge, which needs a code change.
* **Free loop**: handles open-ended tasks with no fixed shape. Its rules are only as strong as the prompt and whatever checks the harness adds.
* A common middle ground: a state machine for the outer workflow, with an agent loop inside one state (for example, research during `GATHERING`).

### Overwrite by default vs. a reducer on every collection
* **Overwrite** is right for scalars and dangerous for collections that parallel branches add to.
* **Accumulating reducers** keep everything, but append-only data can't be corrected. A wrong flag stays forever unless you add an explicit removal operation or replace the collection by version.

### Conflict error vs. a priority rule
* **Conflict error** (this lesson): forces the overlap to be designed. A run can fail on an overlap nobody anticipated, and you find out in testing rather than in production data.
* **Priority rule** (for example, "the deterministic rules beat the model"): always resolves, deterministically. It must be designed per key, and it hides disagreement unless you log it.

---

## Failure Cases to Watch For
1. **Last-writer-wins on shared collections**: one branch's findings vanish, and which ones depends on timing.
2. **Status as a free string**: any code path, or the model, can jump to any state.
3. **Hard rules only in prompts**: "always escalate high risk" holds most of the time.
4. **The model choosing transitions directly**, without a guard between its output and the state change.
5. **Append-only data that can't be corrected**: stale or wrong flags accumulate.
6. **Guards with side effects**: a guard must be a pure check, or evaluating it changes the outcome.
7. **No per-step diff**: the model gets blamed for a merge bug.
8. **Edge explosion**: a single machine with hundreds of edges. Split it into nested machines per stage.

---

## Try It
```sh
cd part-1-foundations/13-state-machines
python example.py            # scripted model; no API key needed
python -m pytest tests.py -q
```
What to watch for: the naive merge keeps only `locked_account`, while the reducer merge keeps both findings, and the model's prompt shows both. Jumping from `assessing` to `resolved` is refused with the allowed edges. Escalating a medium-risk ticket is refused by its guard. The log shows every transition and which branch changed which keys.

Experiment: in `merge`, change `elif key in writers:` to `elif False:` and run the tests. `test_two_branches_writing_a_plain_key_is_a_conflict` fails, because the model's "high" now silently overwrites the rules' "low" (or the other way round), and the run carries on as if nothing happened.

**Next:** Lesson 14 sorts errors into kinds (transient, permanent, format, policy), adds fallbacks that never pollute the cache, and a circuit breaker.
