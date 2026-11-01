# Lesson 07: Multi-Agent Coordination

Learn when to split work across agents and how to do it safely: an orchestrator, sub-agents with isolated context, typed hand-offs, and a fan-in that knows what's missing.

> **Pillars:** Agent · RAG · Harness  |  **Harness layer:** coordination, meaning who sees what, who returns what, and who decides when it's done
>
> *Give each sub-agent less context, and trust its hand-off less.*

## Why does this exist?
A single agent working through a broad question accumulates everything it explores in one context: every search result, every dead end. Its context fills up, earlier mistakes colour later steps, and independent sub-questions run one after another.

Splitting the work helps when the pieces are **independent**. An orchestrator breaks the goal into sub-tasks, and each sub-agent researches one of them with a fresh context and only the documents it needs. The sub-agents run in parallel and hand back short, cited findings, which the orchestrator combines. In RAG terms, each sub-agent retrieves from its own scope, and only the distilled result travels upward.

The price is real. Every sub-agent re-reads its own instructions and sources, so tokens multiply with the number of agents. And there are new ways to fail: a hand-off that doesn't parse, a sub-agent that never answers, a synthesis that covers a gap with invented content. This lesson builds the harness that handles them.

---

## What problem does it solve?
1. **Context pollution**: one agent's context fills with material irrelevant to its current sub-question.
2. **Serial latency**: independent research questions wait for each other.
3. **Scope**: a sub-task about billing has no business reading privacy documents (least privilege applies to retrieval too).
4. **Combining results reliably**: free-text answers from several agents can't be checked, and a failed sub-agent shouldn't sink the whole run or vanish silently.

---

## How would beginners implement this?
```python
def research(goal):
    answers = []
    for question in llm(f"List sub-questions for: {goal}").splitlines():
        answers.append(agent(f"{goal}\nEarlier answers: {answers}\nNow answer: {question}"))
    return llm(f"Combine these answers: {answers}")
```

### Why does that fail?
1. **It runs serially**, so N questions take N times as long.
2. **Every sub-agent sees everything**: the goal, all earlier answers and their mistakes. Contexts grow with each step, and nothing limits which documents an agent can read.
3. **Hand-offs are free text.** There's nothing to validate, so a confused answer flows straight into the synthesis.
4. **Failure handling is all or nothing.** One exception ends the run, or a `try/except` drops the failed part and nobody notices.
5. **The synthesis can claim what nobody verified.** Asked to combine answers, a model fills gaps.
6. **Cost is invisible.** Nothing records how many tokens each agent used.

---

## How is this solved in production?
**1. Isolated context per sub-agent.** `_sub_agent` builds a fresh message list containing only its own task and the sources from its own scope (`CORPORA[task.scope]`). It never sees the goal, the other tasks or the orchestrator's history.

**2. A typed hand-off contract.** A sub-agent must return a `Finding`: its `task_id`, an answer of at most 400 characters (a summary, not a transcript) and at least one citation. The orchestrator validates the schema, checks that the `task_id` matches, and checks that every citation is one of the sources this sub-agent was given (the Lesson 03 check, applied at the hand-off). A failed check becomes a `SubResult` carrying an error. It is never raised and never silently dropped.

**3. Bounded fan-out, one deadline:**
```python
pool = ThreadPoolExecutor(max_workers=max_parallel)       # bounded parallelism
futures = {t.id: pool.submit(sub_agent, t) for t in tasks}
wait(futures.values(), timeout=deadline_s)                # one deadline for the batch
pool.shutdown(wait=False, cancel_futures=True)            # stragglers don't hold the response
results = [future.result() if done else SubResult(task, None, "no result within ...") ...]
```
(Shortened from `Orchestrator.run`.) A `with ThreadPoolExecutor(...)` block would wait for every thread when it exits, which would quietly defeat the deadline.

**4. A fan-in that the harness controls.** The harness, not the model, computes `missing`, the tasks without a verified finding. The synthesiser sees only the findings and their citations, never the raw sources, plus an explicit list of what is missing. If nothing was verified, there is no synthesis call at all.

**5. Check the synthesis too.** Every source ID cited in the final answer must have been cited by a verified finding; any other ID ends up in `unsupported`. In the demo's second run, sub-agent t2 misses the deadline, yet the scripted synthesis still claims the export step `[privacy-02]`. The report flags it as incomplete and unsupported, because the harness doesn't rely on the model to admit the gap.

**6. Account tokens per agent.** `tokens_by_agent` shows where the cost went: each sub-agent plus the orchestrator's synthesis.

The sub-tasks are given here to keep the lesson focused. In a full system they come from a planner (Lesson 06), and a sub-agent's work can itself be an agent loop (Lesson 02) exposed to the orchestrator as a tool.

---

## Design Tradeoffs

### Single agent vs. orchestrator + sub-agents
* **Single agent**: one context, the lowest cost, and no hand-off to get wrong. Best when steps are tightly coupled (edits to the same file, a conversation, a calculation that builds on itself) or when the task is small.
* **Orchestrator + sub-agents**: parallel, with clean contexts and per-scope permissions. Best for breadth: independent sub-questions, separate document collections, exploratory searches whose noise should stay out of the main context. It costs several times the tokens and adds coordination failure modes.

### Summary hand-off vs. full-transcript hand-off
* **Summary + citations** (this lesson): small, checkable, and it keeps the orchestrator's context clean. Detail is lost at every hop, so keep the citations: they let the final answer point back at the evidence.
* **Full transcript**: nothing is lost, but the orchestrator's context fills with everything isolation was meant to keep out.

### Deadline with partial results vs. waiting for everyone
* **Deadline** (this lesson): predictable latency. The answer may be partial, so it has to say so, and the report has to know it.
* **Wait for all**: complete, but the slowest sub-agent sets the response time, and one hung call hangs the request.

---

## Failure Cases to Watch For
1. **Shared context between agents**: passing the whole history to every sub-agent multiplies cost and spreads one agent's mistake to all of them.
2. **Free-text hand-offs**: the orchestrator parses prose, and errors propagate unnoticed.
3. **Silent gaps**: the synthesis covers a failed sub-task with invented content. Compute what is missing in code and check the final citations.
4. **Unbounded fan-out**: fifty sub-agents at once hit provider rate limits (Lesson 30) and the budget.
5. **Stragglers and hidden cost**: one slow sub-agent holds the response. And a sub-agent past the deadline may still be running and spending tokens in the background, so cancel at the provider if you can, and count the cost either way.
6. **Overlapping tasks**: two sub-agents searching for the same thing. Give them disjoint questions or scopes.
7. **The telephone game**: summaries of summaries lose precision. Carry citations through every hop.
8. **Splitting coupled work**: parallel agents editing the same artifact produce conflicts that no merge step can fix.

---

## Try It
```sh
cd part-1-foundations/07-multi-agent-coordination
python example.py            # keyed fake model; no API key needed
python -m pytest tests.py -q
```
What to watch for: in the first run, both findings arrive and `complete=True`. In the second, t2 is delayed past the 0.3-second deadline. The report lists it as missing, and the answer's `[privacy-02]` citation is flagged as unsupported, even though the scripted synthesis reads just as confidently as before.

Experiment: in `_sub_agent`, give every sub-agent all corpora (`corpus = {k: v for c in self._corpora.values() for k, v in c.items()}`) and run the tests. The isolation test fails, and so does the out-of-scope citation test, because a billing sub-agent can now cite privacy documents.

**Next:** Lesson 08 puts a person in the loop: pause before a risky action, persist the state, and resume on approve, edit or reject.
