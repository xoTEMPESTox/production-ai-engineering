# Lesson 24: Deployment

Learn how to ship a new prompt or model safely: an offline eval gate, shadow traffic users never see, a sticky canary promoted stage by stage on guardrail metrics, instant rollback, and warm instances so cold starts don't land on users.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** release, meaning how a change reaches users and how fast it can be taken back
>
> *Prove it offline, watch it in the dark, give it to a few, and keep the way back open.*

## Why does this exist?
In an AI service, the riskiest deploys often change no code: a new prompt version, a switch to a cheaper model, a new chunking strategy. They change behaviour everywhere at once, and the failures they cause are quiet: answers that are a little less grounded, JSON that fails validation 8% of the time, latency that creeps up. A release process for AI has to treat **prompt and model changes as deploys** (Lesson 03's `prompt_id`, Lesson 25's flags), and judge them on AI-specific signals as well as errors.

---

## What problem does it solve?
1. **Catching regressions before users see them**: offline evals (Lesson 27), then shadow traffic.
2. **Limiting the blast radius** when something slips through: a small share of users first.
3. **A consistent experience**: a user mid-conversation shouldn't flip between two prompts.
4. **Deciding with enough data**: no verdict on twelve requests.
5. **Fast recovery**: rolling back must take seconds, not a redeploy.
6. **Cold starts**: new instances loading models or indexes mustn't make their first users wait.

---

## How would beginners implement this?
```python
# Friday afternoon
PROMPT_VERSION = "grounded_answer@v3"  # was v2
MODEL = "small-model"                  # was large-model
deploy()                               # 100% of users, immediately
# Monday: support tickets about wrong refund amounts; git revert; redeploy; wait for the build
```

### Why does that fail?
1. **No gate.** Nothing checked v3 against the golden set before it shipped.
2. **Everyone at once.** A regression reaches 100% of users in the first minute.
3. **No comparison.** Without a stable arm running alongside, "is the schema-failure rate high?" has no baseline.
4. **The signal arrives as complaints**, days later, instead of as a dashboard within minutes.
5. **Rollback is a redeploy.** It's slow, it needs a build, and it lands on a weekend.
6. **Cold starts.** Fresh instances load the model and index on their first requests, and those users wait seconds.

---

## How is this solved in production?
**1. An offline eval gate.** `CanaryController.start` refuses a candidate that failed Lesson 27's regression gate. Nothing reaches traffic without passing it.

**2. Shadow traffic.** `serve(..., shadow=CANDIDATE)` runs the candidate on copies of real requests and records its outcomes, but always returns the stable release's answer. In production the shadow call runs asynchronously, off the request path. `start` applies the same guardrails to shadow results. In the demo, the candidate with 9% schema failures is **blocked in shadow**, and no user saw a single one of its answers. Shadow costs a second model call per request, and it can't measure what users *do* with an answer (thumbs-down, edits, escalations). That's why a canary still follows.

**3. A sticky canary.** `bucket(user_id)` is a stable 0–99 hash, so a user is either in the canary or not, on every request and every replica. It's derived from CRC32, never Python's per-process `hash()`. The router sends buckets below `candidate_percent` to the candidate.

**4. Promote on guardrails, with enough data:**
```python
if min(stable.requests, candidate.requests) < self.min_requests:
    return Decision("hold", ...)            # not enough data: keep collecting
if candidate_error_rate - stable_error_rate > self.max_error_increase: rollback
if candidate_schema_failure_rate > self.max_schema_failure_rate:       rollback
if candidate.p95 / stable.p95 > self.max_p95_ratio:                    rollback
otherwise: promote 1% -> 10% -> 50% -> 100%
```
Each stage is compared with the stable release over the *same period*, so time-of-day effects cancel out, and a new stage starts with fresh numbers. Add whatever matters for your product: fallback rate (Lesson 14), thumbs-down rate, human edit rate (Lesson 08), cost per request (Lesson 28).

**5. Instant rollback.** The stable release never stops running during a canary, so rolling back just sets `candidate_percent` to 0. In the demo, the same bad candidate with shadow skipped is rolled back automatically **at 1%**: about one user in a hundred saw it, for minutes.

**6. Warm instances before they take traffic.** `p95_with_cold_starts` shows what a 6-second model or index load does to p95 latency (the time 95% of requests finish within) when new instances serve their first requests cold (6,800 ms), versus when they're warmed first (800 ms). Mitigations: send warm-up requests before marking an instance ready (Lesson 23's `/readyz`), keep a minimum number of warm instances (serverless platforms offer settings such as provisioned concurrency or minimum instances), keep images and artifacts small, and load lazily only what isn't needed for the first request.

---

## Design Tradeoffs

### Shadow vs. canary
* **Shadow**: zero user impact and real inputs. It doubles model spend while it runs, its effects must be read-only (never let a shadow call send an email or issue a refund), and it can't see how users react.
* **Canary**: measures real user outcomes. Some users do get the candidate. Use shadow first for risky changes, then a canary.

### Small fixed stages vs. a statistical test per stage
* **Fixed thresholds with a minimum sample** (this lesson): simple, predictable, and explainable. They can miss small regressions, or flag noise on small stages.
* **A statistical comparison** (Lesson 27's paired bootstrap, applied online): catches smaller differences honestly, at the cost of more data and more complexity. Large differences don't need it.

### Sticky per user vs. random per request
* **Sticky** (this lesson): a conversation stays on one prompt, and user-level metrics (did they come back? did they escalate?) mean something. If the buckets aren't representative, the canary isn't either.
* **Per request**: a cleaner random sample of traffic, but one conversation can mix two prompts or models, which users notice.

---

## Failure Cases to Watch For
1. **Prompt and model changes outside the release process**: edited in a config file and live at 100%.
2. **Shadow calls with side effects**: the candidate sends real emails or issues real refunds.
3. **Verdicts on tiny samples**: promoting (or rolling back) on a dozen requests.
4. **Comparing against yesterday** instead of against the stable release over the same period.
5. **Only generic metrics**: error rate fine, while groundedness or schema validity collapsed.
6. **Rollback that needs a redeploy**: the old release was torn down.
7. **Non-sticky assignment across replicas**: users flip between versions mid-conversation.
8. **Cold instances taking traffic**: the first users of every scale-up wait for model loads.

---

## Try It
```sh
cd part-2-production-patterns/24-deployment
python example.py            # simulated traffic; runs in a second or two
python -m pytest tests.py -q
```
What to watch for: the good candidate goes 1% → 10% → 50% → 100%. The candidate with schema failures is blocked in shadow, having shown users zero answers. The same candidate with shadow skipped is rolled back at 1%. And the cold-start p95 drops from 6,800 ms to 800 ms with warm-up.

Experiment: in `CanaryController.step`, make rollback only set the status (drop `self.router.candidate_percent, ... = 0, ...`) and run the tests. `test_guardrail_breach_rolls_back_instantly` fails: the controller *says* it rolled back while users keep getting the bad release.

**Next:** Lesson 25 types the configuration: validated settings, secrets kept out of git, and feature flags for prompt and model versions.
