# Lesson 08: Human Approval

Learn how to put a person in the loop without blocking the system: pause the run, persist the request, and resume on approve, edit or reject, with escalation, expiry and an audit trail.

> **Pillars:** Agent · Harness  |  **Harness layer:** the approval gate between a proposed action and its execution
>
> *Pause the run, not the thread. And expiry means no.*

## Why does this exist?
Some actions are too consequential for a model to take alone: Lesson 04's destructive tools, and writes above a risk threshold, such as a large refund. Some outputs are drafts by nature: a reply to a customer, or a summary that a professional must sign before it counts. In regulated work the rule is often explicit: the AI prepares a draft, a qualified person reviews, edits and signs it, and only the signed version is used.

People take minutes, hours or days to decide. The process that asked may have restarted twice by then. So approval is not a `y/n` prompt. It is a small workflow of its own, with state, deadlines and a record.

---

## What problem does it solve?
1. **Waiting**: a decision can take hours, and holding a thread or connection open for it loses the request at the next deploy.
2. **Deciding on the real thing**: the person must see the exact action that will run, and must be able to change it. An edit is input too.
3. **Accountability**: who approved what, when, and what they changed.
4. **No answer**: approvers are busy, so requests must escalate and eventually expire, safely.
5. **Exactly once**: a double-click, two approvers, or a retry after a crash must not run the action twice.

---

## How would beginners implement this?
```python
def refund_with_approval(args, draft):
    answer = input(f"Approve refund {args}? [y/n] ")  # or: poll a flag in a loop
    if answer == "y":
        issue_refund(args)
        send_email(draft)
```

### Why does that fail?
1. **It blocks a process.** A web server can't do this, and a restart loses the pending request.
2. **Nothing is persisted.** There is no record that a request exists, so nobody else can pick it up.
3. **There is no edit path.** Approvers must accept or reject the exact amount and text, so they approve wrong values "to fix later".
4. **There is no record** of who decided, or what was actually executed.
5. **There is no timeout**, or a worse one: a timeout that treats silence as yes.
6. **There is no role check and no decide-once rule.** Anyone can approve, and approving twice refunds twice.

---

## How is this solved in production?
**1. Policy decides what needs a person.** Refunds up to `auto_approve_cents` run immediately, and the audit records `policy` as the signer. Larger ones go to a person.

**2. `propose()` persists a request and returns.** An `ApprovalRequest` is stored as JSON: the validated `RefundArgs`, the AI-written draft, the required role, `escalate_at`, `expires_at` and an audit list. The call returns `awaiting_approval`, so the agent run can checkpoint and stop (Lesson 15). Nothing has executed. The approver is notified.

**3. `decide()` can run anywhere, later.** It loads the request from the store, so a different process works just as well, and applies checks in order:
```python
request = load(request_id)
if pending and now >= request.expires_at:
    request.status = "expired"                     # don't rely on the sweeper having run
if request.status != "pending":
    return refused("already " + request.status)    # decided at most once
if rank(decision.role) < rank(request.required_role):
    return refused("needs " + request.required_role)
if verdict == "reject":
    ...                                            # nothing executes; the agent is told why
if verdict == "edit":
    args = RefundArgs.model_validate({...})        # a person's edit is validated too
result = execute(args, idempotency_key=request.id)
record(signed_by=decision.actor, final_draft=draft, changes=["amount 18000 -> 12000", ...])
```
(Shortened from `ApprovalGate.decide`.)

**4. Edits are validated like model output.** An edited amount goes through the same `RefundArgs` model. An invalid edit is refused, and the request stays pending for a correct decision.

**5. Execution is idempotent.** The request ID is passed to the executor as the idempotency key (Lesson 04), so a crash between executing and saving can't produce a second refund downstream.

**6. A sweeper handles silence.** Run periodically, it escalates a pending request once after four hours (notifying `billing_manager`) and expires it after 24 hours. **Expired means rejected:** nothing executes. `decide()` checks expiry itself, so a late click is refused even if the sweeper is down.

**7. The audit trail is append-only.** Every event (`requested`, `escalated`, `edited`, `rejected`, `executed`, `expired`) is appended with the actor, a timestamp and the detail, including the exact change. Nothing is ever rewritten.

**8. Draft → sign-off.** The stored result is the signed final draft plus the signer, not the AI's draft. `draft_similarity` measures how much the person changed it. Averaged over many requests, one minus similarity is the **human edit rate**, a direct quality signal for the model (Lesson 26).

When the agent resumes, it gets `Outcome.observation`, for example "Refund edited by a person: 120.00 refunded to acct-17", as its next observation, just like a tool result in Lesson 02.

---

## Design Tradeoffs

### Synchronous approval vs. pause-and-resume
* **Synchronous** (a prompt while the user waits): simple and right when the requester is the approver and is present, as when a coding agent asks "run this command?".
* **Pause and resume** (this lesson): needed when the approver is someone else or works asynchronously. It costs persistence, notifications and a sweeper.

### Approve everything vs. a risk threshold
* **Approve everything**: the safest per action, but people start rubber-stamping. Approval fatigue turns review into a click.
* **Threshold** (this lesson): people see fewer, more meaningful requests. Thresholds can be gamed (two refunds of 99.00 instead of one of 198.00), so pair them with aggregate limits per account and per day, and log the policy as a signer.

### Expire as reject vs. never expire
* **Expire as reject** (this lesson): fail-safe and predictable. The customer may have to ask again.
* **Never expire**: nothing is lost, but a request approved three days later is approved on three-day-old information.

---

## Failure Cases to Watch For
1. **Blocking waits**: `input()` or a long poll that dies with the process.
2. **Silence as consent**: a timeout that approves.
3. **Approving a description**: the approver sees "refund the duplicate charge" instead of the exact validated arguments.
4. **Unvalidated edits**: a person types `-500` or `1800000`, and it runs.
5. **Double execution**: a double-click, two approvers at once, or a retry after a crash. Decide-once plus an idempotency key covers the first and last. Two concurrent approvers also need compare-and-set on a version column (Lesson 19).
6. **Stale preconditions**: the account was refunded by another channel while the request waited. Re-check preconditions at execution time.
7. **Approval fatigue**: hundreds of requests a day, each approved in two seconds. Measure time-to-decision and the edit rate.
8. **An editable audit trail**, or one that records the request but not the final executed values.

---

## Try It
```sh
cd part-1-foundations/08-human-approval
python example.py            # fake clock; no API key needed
python -m pytest tests.py -q
```
What to watch for: the 40.00 refund runs immediately, signed by `policy`. The 180.00 refund pauses. After a simulated restart, a new gate applies the lead's edit (120.00 plus a corrected draft), and a second click is refused. `run-3` is never decided: it escalates at five hours, expires at 25, and never executes. The audit trail shows the exact change and its similarity score.

Experiment: pass `auto_approve_cents=20_000` when the demo builds its gate and run it again. The 180.00 refund now runs without anyone seeing it, and the audit says `policy` signed. A threshold is a product decision, so record and review it like any other signature.

**Next:** Lesson 09 measures quality: golden sets, deterministic scorers, LLM-as-judge done carefully, and checks outside the model for what it silently leaves out.
