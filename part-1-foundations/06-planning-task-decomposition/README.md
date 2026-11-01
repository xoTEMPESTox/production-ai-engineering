# Lesson 06: Planning & Task Decomposition

Learn how to turn a multi-step goal into a plan the harness can validate, execute, track and repair.

> **Pillars:** Agent · Harness  |  **Harness layer:** the plan, a todo list the harness owns and the model edits
>
> *A plan is data, not a paragraph. Validate it, track it, and change it only on purpose.*

## Why does this exist?
Lesson 02's loop decides one step at a time. That works for short tasks. On longer ones, agents drift: they forget a sub-goal, repeat finished work, or stop at the first interesting result. Writing the plan down first, as a list of steps, gives the model and the harness a shared record of what is left.

"Refund the duplicate charge on acct-17 and tell the customer" sounds like one request, but it is three dependent actions: find the charge, refund it, notify the customer. The second needs the first one's result, and the third shouldn't happen if the second failed.

The key design choice in this lesson is that the plan is **data the harness owns**, not text in the model's head. The harness can check it before anything runs, report progress from it, and decide what happens when a step fails.

---

## What problem does it solve?
1. **Dependencies**: step 2 needs step 1's result, and a step must not run before what it depends on.
2. **Visible progress**: what is done, what failed and what is left. The model needs this when it re-plans, and people need it in a UI.
3. **Failure in the middle**: a late step fails after earlier steps already had side effects. Recovering must not redo the refund.
4. **Bad plans**: unknown tools, steps that use results that don't exist yet, forty-step plans.

---

## How would beginners implement this?
```python
def run(goal):
    plan_text = llm(f"Make a numbered plan for: {goal}")
    for line in plan_text.splitlines():
        result = agent(f"Do this step: {line}")  # each step is a fresh agent run
        if "error" in result.lower():
            return run(goal)                     # something failed: start over
```

### Why does that fail?
1. **The plan is prose.** There are no tools, arguments or dependencies to check, and "Step 3: verify everything" can't be validated or executed deterministically.
2. **Steps can't see each other's results**, except by luck, through whatever ended up in the prose.
3. **Starting over repeats finished side effects.** The customer gets a second refund.
4. **Failure is detected by string matching.** A step whose output mentions "error" is treated as failed, and a real failure phrased differently is not.
5. **Nothing is bounded.** A plan can be any length, and start-over can recurse forever.
6. **There is no progress record**, so nobody can tell how far a run got before it stopped.

---

## How is this solved in production?
**1. The planner returns a typed plan.** `PlanSpec` is a Pydantic model: at most eight steps, each with an `id`, a `description`, a `tool`, its `args` and its `depends_on` list, with extra fields forbidden. To pass a result forward, an argument contains a placeholder: `{"charge_id": "{s1}"}`.

**2. The harness validates what the schema can't.** `validate_plan` rejects unknown tools, dependencies that aren't a finished step or an earlier step in the same plan (which also rules out cycles and makes list order the execution order), placeholders that aren't declared dependencies, and reused IDs. Every problem is listed at once, so the model could fix them all in one repair (Lesson 03). Here an invalid plan simply fails the run before anything executes.

**3. Execution is driven by the plan's own state:**
```python
while (step := plan.next_ready()) is not None:          # first pending step whose deps are done
    args = substitute_placeholders(step.args, plan.results())
    try:
        plan.mark(step.id, DONE, tools[step.tool](**args))
    except Exception as exc:
        plan.mark(step.id, FAILED, f"{type(exc).__name__}: {exc}")
        if replans == max_replans:
            return RunReport("failed", ...)              # bounded
        supersede_pending_steps(plan)                     # the old tail is dropped
        replan(plan, failure=...)                         # planner sees the checklist + error
```
(Shortened from `PlanExecutor.run` in `example.py`.)

**4. Re-plan only the remaining work.** The failed step stays in the plan, marked `[!]` with its error, and pending steps become `superseded`. The planner receives the rendered checklist:
```
Goal: Refund the duplicate charge on acct-17 and tell the customer  (plan v1: 2 done, 1 failed, 0 pending)
[x] s1 find_charges: Find the duplicate charge -> ch_2
[x] s2 issue_refund: Refund it -> refund queued for ch_2
[!] s3 send_email: Email the customer -> LookupError: acct-17 has no email address on file
```
It returns new steps with new IDs, which may depend on finished ones (`"note": "Tell them: {s2}"`). Finished steps are never re-run, so the refund happens exactly once.

**5. Everything is bounded.** Plan size is capped by the schema, and re-plans by `max_replans`. The run ends with a `RunReport` holding the status, the reason and the full plan history, including the version that added each step.

Two simplifications to know about. First, each step here is a single tool call, to keep the mechanics visible. In real agents a step is often a small agent loop (Lesson 02) or a sub-agent (Lesson 07), and the plan structure stays the same. Second, this executor re-plans on any exception. In production, a **transient** error such as a timeout or a 503 should be retried (Lessons 14 and 16). Re-planning is for errors that mean the approach doesn't work, like "no email on file".

---

## Design Tradeoffs

### Plan-and-execute vs. step-by-step
* **Step-by-step** (Lesson 02): adapts to every observation, with no up-front cost. On long tasks it drifts, and progress is implicit in the transcript.
* **Plan-and-execute** (this lesson): progress is explicit, the plan can be validated and shown to a person, and execution needs no model call per step when each step is a tool call. The model commits to steps before seeing any data, so the plan can be confidently wrong.

### Re-plan on failure vs. re-plan after every step
* **On failure only** (this lesson): cheap and predictable. But a plan can go stale without anything failing, for example when a search succeeds but returns nothing useful.
* **After every step**: adapts to surprises, but costs a model call per step, and plans churn.
* **In between**: re-plan on failure, or when a step's result fails a check (an empty result, a validation rule).

### When planning isn't worth it
* For one or two tool calls, latency-sensitive chat, or a path you already know, skip it. When the path is known, write it as a workflow: the code is the plan.
* A plan costs at least one extra model call and an extra failure mode (an invalid or wrong plan). It pays off when tasks have several dependent steps, side effects worth protecting, or a person who needs to see progress.

---

## Failure Cases to Watch For
1. **Redoing side effects on re-plan**: "start over" re-runs the refund. Keep finished steps done and plan only what remains.
2. **Phantom inputs**: a step uses a result that doesn't exist yet, so it either crashes or the model invents the value. Validate placeholders against declared dependencies.
3. **Re-planning forever**: no re-plan budget, or a planner that keeps producing the same failing step.
4. **Re-planning on transient errors**: a timeout replaced by a different plan, when a retry would have worked.
5. **"Done" isn't "achieved"**: a step is marked done because its tool returned, not because the goal was met. Check outcomes outside the model (Lesson 09).
6. **Over-planning**: a planner call and an eight-step plan for a one-call question.
7. **Wrong granularity**: "do the task" is not a step, and thirty micro-steps waste calls and invite drift.

---

## Try It
```sh
cd part-1-foundations/06-planning-task-decomposition
python example.py            # scripted planner; no API key needed
python -m pytest tests.py -q
```
What to watch for: `s3 send_email` fails because the customer has no email address. The re-plan adds `s4 create_callback`, which reuses `s2`'s result, and `refunds issued` shows a single refund.

Experiment: in the second scripted plan, change `s4`'s id to `s2`, or remove `"depends_on": ["s2"]`. The run now fails with `invalid plan` and the reason, and no new step executes.

**Next:** Lesson 07 splits work across several agents: an orchestrator, sub-agents with isolated context, typed hand-offs, and fan-out/fan-in.
