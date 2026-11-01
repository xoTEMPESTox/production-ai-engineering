# Lesson 04: Tool Calling

Learn how to give a model hands without giving it the keys: typed tool specs, one dispatcher, and a fixed pipeline that every call goes through.

> **Pillars:** Agent · Harness  |  **Harness layer:** the tool pipeline between the model's request and the real world
>
> *The model asks. The dispatcher validates, checks permission, and only then acts.*

## Why does this exist?
In Lesson 02 a tool was a Python function that the loop called with whatever arguments the model produced. That is fine for `search_docs`. Now give the agent `issue_refund` and `close_account`. The model's arguments are generated text: they can have the wrong type, be out of range, or name the wrong account. The model may repeat a call because the first one was slow. And some actions can't be undone.

Tool calling is where an agent touches the real world, so it is where most of the risk lives. Every call should go through the same pipeline, whichever tool it is and whoever wrote it.

---

## What problem does it solve?
1. **The model must know which tools exist and how to call them.** That needs a machine-readable spec: a name, a description and a JSON Schema for the arguments.
2. **Arguments are untrusted input.** `amount_cents: -5` and `account_id: "17"` are both things a model will produce.
3. **Side effects must happen once.** A repeated or retried `issue_refund` must not refund twice.
4. **Some actions need a human.** Closing an account should never depend on the model's judgement alone.
5. **Slow tools must not hang the run, and huge results must not flood the context window.**

---

## How would beginners implement this?
```python
TOOLS = {"issue_refund": issue_refund, "close_account": close_account, "search_docs": search_docs}
SPEC = "Tools: issue_refund(account_id, amount), close_account(account_id), search_docs(query)"

def run_tool(name, args):
    return str(TOOLS[name](**args))
```

### Why does that fail?
1. **The spec is prose.** The model guesses argument names and types, and the provider's native tool calling, which expects JSON Schema, goes unused.
2. **Nothing is validated.** `amount=-5`, `amount="19.99"` or an invented `force=True` go straight to the billing API.
3. **Every tool has the same power.** `close_account` runs as freely as `search_docs`.
4. **A repeat doubles the side effect.** After a slow response the model calls `issue_refund` again, and the customer gets two refunds.
5. **There is no timeout.** One hung API call blocks the whole run.
6. **There is no size limit.** A search that returns 3,000 lines fills the context window and pushes out the task itself.

---

## How is this solved in production?
Each tool is a `ToolSpec`: a name, a description, a **Pydantic argument model**, a handler, a **permission tier** and a **timeout**. The argument model does two jobs. It generates the JSON Schema the model sees (`model_json_schema()`), and it validates what the model sends back. Because both come from one class, they can't drift apart.

Every call goes through `ToolRegistry.call()` in a fixed order:
```python
spec = tools.get(name)                                # 0. unknown tool -> list the real ones
args = spec.args.model_validate(raw_args)             # 1. validate: type, range, extra fields
if spec.tier is Tier.DESTRUCTIVE and not approver(name, args):
    return DENIED                                     # 2. permission, on the validated args
key = idempotency_key(run_id, name, args)             # 3. writes: same run+tool+args, same key
if key in completed:
    return REPLAYED                                   #    a repeat returns the first result
output = pool.submit(handler, args, ctx).result(timeout=spec.timeout_s)  # 4. bounded time
content, truncated = truncate(output, max_chars)      # 5. bounded size
```
(Shortened from `example.py`. Each step returns a `ToolResult` holding an `Outcome` and the exact text the model will see.)

Why each step sits where it does:
1. **Validate before anything else.** Errors come back as observations that name the field ("amount_cents: Input should be greater than 0"), so the model can fix its call, just like the tool errors in Lesson 02. `extra="forbid"` rejects invented arguments.
2. **Check permission after validation.** The approver sees the exact validated arguments that will run, not the model's description of them. `READ` tools run freely, `WRITE` tools run with an idempotency key, and `DESTRUCTIVE` tools need explicit approval; the default approver denies everything. Lesson 08 turns the approver into a real pause-and-resume flow.
3. **Give writes idempotency keys.** The key is a hash of the run ID, the tool name and the canonical (sorted-key) validated arguments. A completed write stores its output under the key, and a repeat returns `REPLAYED` without calling the handler. The key is also passed to the handler in `ToolContext` so it can be forwarded to the downstream API. If the process crashes after the API acted but before the result was stored, only the downstream API can recognise the duplicate.
4. **Bound the time, and say what a timeout means.** A timeout ends the wait, not the work: Python can't kill a running thread, and a remote API may already have acted. For a write, a timeout means "outcome unknown", and the observation says to check status before retrying. Failures and timeouts are **never cached** as completions, so a real retry still runs. Reads are never replayed, so a status check always returns fresh data.
5. **Truncate, with a hint.** A result over the limit is cut and ends with a note that says what happened and how to get less ("Narrow the query to see more").

**Where MCP fits.** The Model Context Protocol standardises how tools are described and called across process boundaries. An MCP server lists its tools (each with a name, a description and a JSON Schema for its input) and executes calls; a client inside your agent connects to it over stdio or HTTP using JSON-RPC. That removes the custom glue for each integration. It doesn't replace this pipeline: a tool from an MCP server is registered like any other, and your harness still validates its arguments, assigns its tier, enforces its timeout and truncates its results. Servers can annotate tools with hints such as read-only or destructive, but those are claims made by the server. The tier is your decision.

**Hooks.** Because every call passes through one function, that function is the natural place for pre- and post-execution hooks: audit logging, redacting PII from results, metrics per tool and outcome. Add behaviour around the pipeline, not inside each tool.

---

## Design Tradeoffs

### Derived vs. caller-supplied idempotency keys
* **Derived from run + tool + arguments** (this lesson): no extra work for the model, and repeats within a run are caught automatically. But two *intended* identical actions in one run (the same refund amount for two different charges) collapse into one, so put the distinguishing value, such as a charge ID, in the schema.
* **Supplied by the caller or the model**: explicit, but the model can forget or reuse keys, and a resumed run has to regenerate exactly the same key.

### Truncation vs. result handles
* **Truncate** (this lesson): simple and bounded, but the part that was cut is gone for this step.
* **Store the full result and return a handle plus a preview** ("1,240 rows saved as result-7; first 20 shown; call `read_result(id, page)`"): nothing is lost and the context stays small, at the cost of one more tool and storage to clean up.

### Static tool list vs. on-demand tool loading
* **Every tool in every request**: simplest, and the tool definitions form a stable, cacheable prefix. But each definition costs tokens, and a long list of similar tools makes the model choose worse.
* **Tools loaded on demand** (by task type, or through a "search the tools" tool): a smaller, sharper toolset per request, but the prefix changes and caching suffers.

---

## Failure Cases to Watch For
1. **Trusting arguments**: the schema exists in the prompt but is not enforced in code.
2. **Approving the wrong thing**: showing the approver the model's summary of the action instead of the validated arguments that will run.
3. **Double side effects**: a repeated or retried write with no idempotency key.
4. **Caching failures as completions**: storing an error or a timeout under the idempotency key, so the real retry never runs.
5. **Treating a write timeout as a failure**: retrying blindly when the first attempt may have succeeded.
6. **Context flooding**: one huge tool result pushes the system prompt or the task out of the model's attention.
7. **Over-trusting remote tool metadata**: assuming an MCP server's "read-only" hint makes a tool safe.

---

## Try It
```sh
cd part-1-foundations/04-tool-calling
python example.py            # no API key needed
python -m pytest tests.py -q
```
What to watch for: the first refund fails validation with the exact field errors and the second one runs. The third is the model repeating itself; it comes back `replayed`, and the fake billing API reports a single refund. `close_account` is denied by the default approver and runs only through the registry whose approver says yes.

Experiment: change the `run_id` of the third call to `"run-002"`. It's no longer a repeat within one run, so it executes, and the billing API now holds two refunds. Decide whether that's the behaviour you want, then read the first tradeoff above.

**Next:** Lesson 05 builds what `search_docs` has been faking: memory and retrieval, scoped per tenant and kept inside a token budget.
