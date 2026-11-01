# Lesson 02: The Agent Loop

Learn how a model becomes an agent: a bounded think → act → observe loop in which the model proposes and the harness decides.

> **Pillars:** Agent · Harness (with agentic RAG)  |  **Harness layer:** the loop that runs the model
>
> *The model proposes the next action. The harness decides whether it runs.*

## Why does this exist?
A single LLM call can only answer from what is already in its prompt. Agents work on tasks where the information isn't there yet: they have to look something up, act, see what happened and decide what to do next. The mechanism is a loop. The model reads the conversation so far and proposes one action (call a tool, or give the final answer); the harness runs the tool and appends the result as an **observation**; repeat.

Coding agents, research agents and support bots that look up your order all run a version of this loop. The loop itself is about twenty lines. What makes it safe to run unattended is the **harness** around it: budgets, stop conditions, error handling and a record of every step. That is what this lesson builds.

Retrieval fits here too. When search is a tool, the model decides whether to search, what to search for and whether to search again. This is **agentic RAG**, as opposed to a fixed retrieve-then-generate pipeline. Lesson 05 builds the retrieval itself; this lesson builds the loop that calls it.

---

## What problem does it solve?
Each step has to answer four questions, and the model should not be the only one answering them:
1. **Should the run continue?** The model believes it is making progress. It has no idea what the run has cost so far.
2. **What happens when a tool fails?** APIs time out and IDs get mistyped. One failure shouldn't end a run the model could have recovered.
3. **Is the agent stuck?** Models repeat themselves: the same search, the same arguments, the same empty result.
4. **What happened?** When a run returns a wrong answer, you need its sequence of thoughts, calls and observations. The final answer alone tells you nothing.

---

## How would beginners implement this?
```python
def run_agent(task):
    messages = [{"role": "user", "content": task}]
    while True:
        reply = llm(messages, tools=TOOLS)
        if reply.tool_call:
            result = TOOLS[reply.tool_call.name](**reply.tool_call.args)
            messages.append({"role": "tool", "content": result})
        else:
            return reply.text
```

### Why does that fail?
1. **The only stop condition is the model's own decision.** If the model never says it's done, the run loops until the context window overflows or the bill arrives.
2. **One tool exception kills the run.** A timeout from a flaky API or a `KeyError` from a mistyped ID propagates up and the user gets a 500, even though the model would have retried with the right ID.
3. **A hallucinated tool name crashes it.** `TOOLS[name]` raises for a tool that doesn't exist.
4. **Nothing notices a stuck agent.** Five identical `search("enterprise discount")` calls cost five steps and teach the model nothing new.
5. **There is no record.** When the answer is wrong, you can't see that step 3's lookup failed and the model guessed.
6. **It can't be tested.** It calls the real model and the real clock, so a test needs network access, an API key and luck.

---

## How is this solved in production?
The loop stays simple, and the harness wraps every step. The core of `AgentLoop.run()` in `example.py`, in the order the checks happen:
```python
for step in range(1, budget.max_steps + 1):
    if clock() - started > budget.max_seconds:           # time: checked before each call
        return stop(step - 1, StopReason.TIME_BUDGET)
    turn = llm.complete(messages, tool_names)            # the model proposes
    tokens += turn.input_tokens + turn.output_tokens
    if turn.final_answer is not None:                    # done
        return stop(step, StopReason.FINAL_ANSWER, turn.final_answer)
    if tokens > budget.max_tokens:                       # cost: checked after each call
        return stop(step, StopReason.TOKEN_BUDGET)
    if call_counts[fingerprint] >= budget.max_identical_calls:
        return stop(step, StopReason.LOOP_DETECTED)      # stuck: never runs a third time
    observation = execute(turn.tool_call)                # errors come back as text
    messages += [call, observation]                      # observe, then loop
return stop(budget.max_steps, StopReason.MAX_STEPS)
```
(Shortened: the real method also handles model exceptions and replies with neither a tool call nor an answer, and records an event at every branch.)

What each piece is for:
1. **Budgets checked by the harness on every step**: maximum steps, maximum tokens (summed from the usage the provider reports) and maximum wall-clock seconds. Stopping is decided from numbers, not by asking the model nicely.
2. **The stop reason is data.** Every run ends with exactly one `StopReason`: `final_answer`, `max_steps`, `token_budget`, `time_budget`, `loop_detected` or `model_error`. Callers branch on it and dashboards count it. "What share of runs end on a budget?" is one of the first production metrics worth having.
3. **Tool errors become observations.** The exception is caught, formatted as `ERROR: KeyError: 'unknown account acct-71'` and given back to the model, which usually fixes its own mistake on the next step. An unknown tool name gets the list of tools that do exist.
4. **Loops are detected by fingerprint.** Each call is fingerprinted as the tool name plus canonical JSON arguments (sorted keys, so argument order can't hide a repeat). Once a fingerprint has run the allowed number of times (two here), the run stops before the tool runs again.
5. **A malformed turn gets a correction, not a crash.** A reply with neither a tool call nor an answer gets a fixed corrective message and costs one step.
6. **Every step goes into a typed event log.** Model turns, tool results, tool errors and the stop are appended as `Event` records carrying the step number, cumulative tokens and elapsed time, and they serialise to JSON lines. This log is the seed of tracing (Lesson 22) and of evaluation datasets (Lesson 27).
7. **Dependencies are injected.** The model sits behind an `LLMClient` Protocol, with a scripted fake in tests and the demo. The clock is a function. So every stop condition can be tested deterministically and offline.

What this loop deliberately leaves out for now: per-call timeouts (a hanging tool blocks its step; Lessons 04 and 11), retrying model errors (Lessons 14 and 16), compacting the context as messages grow (Lesson 05) and surviving a crash mid-run (Lesson 15).

---

## Design Tradeoffs

### Agent loop vs. fixed workflow
* **Fixed workflow** (code decides the steps, the model fills them in): predictable, cheap, easy to test and audit. Right when the path is known, such as "extract fields, validate, summarise". A fixed retrieve-then-answer RAG pipeline is a workflow.
* **Agent loop** (the model picks the next step): handles tasks whose path depends on what turns up, such as a failed lookup or a follow-up search. It costs more steps and tokens, and runs vary more from one to the next.
* Rule of thumb: use a workflow wherever you can write the steps down, and make only the open-ended part agentic.

### Hard stop vs. graceful wrap-up on budget
* **Hard stop** (this lesson): return no answer, only a reason. Simple, with a predictable cost; the caller decides what the user sees.
* **Wrap-up turn**: when the budget is nearly spent, give the model one last turn without tools ("budget exhausted: answer with what you have and say what is missing"). Partial answers become more useful, but you must reserve part of the budget for that turn and mark the answer as partial.

### Exact-repeat detection vs. progress detection
* **Exact fingerprint** (this lesson): cheap, and it never flags two genuinely different calls. It misses near-duplicates such as `"refund policy"` vs `"refund policies"`.
* **No-progress detection**: stop when N consecutive steps add no new information (identical observations, no new IDs). It catches paraphrased loops, but each tool needs its own notion of "new information", and it can stop a slow but productive run.

---

## Failure Cases to Watch For
1. **Runaway runs**: no step, token or time limit, or limits checked only after the run ends. One bad prompt becomes a large bill.
2. **Exceptions escaping the loop**: a tool error ends the run instead of being shown to the model.
3. **The same call over and over**: the model re-runs a search with identical arguments and keeps getting an empty result.
4. **Indistinguishable failures**: returning `None` for "budget exhausted", "stuck" and "model error" alike makes them look the same on a dashboard.
5. **Budget overshoot within a step**: tokens are counted after each call, so a run can exceed `max_tokens` by up to one turn. Size the budget with that in mind, or estimate before calling.
6. **Wall-clock budgets don't interrupt a hanging call.** The check runs between steps, so a tool that never returns needs its own timeout (Lesson 04).
7. **Logging only the final answer**: without the event log, a wrong answer can't be traced back to the step that caused it.

---

## Try It
```sh
cd part-1-foundations/02-agent-loop
python example.py            # two scripted runs; no API key needed
python -m pytest tests.py -q
```
What to watch for: in the first run, step 1's `KeyError` shows up as a `tool_error` event and the model fixes the ID on step 2. The run keeps going instead of crashing. In the second run, the model proposes the same search three times, but the tool only runs twice before `loop_detected` ends the run.

Experiment: pass `Budget(max_steps=3)` to the first `AgentLoop` and run it again. The run ends with `max_steps` one step before the answer, which is what a budget set too tight looks like on a dashboard.

**Next:** Lesson 03 makes the final answer machine-checkable: a schema, citations validated against the sources, one repair attempt, and fail closed.
