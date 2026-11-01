# System: Customer Support

A complete support agent across chat, email and messaging: duplicate webhooks ignored, rules-first routing with the model only for what rules can't place, conversations that resume across channels, refunds within limits, and a clean hand-off to people.

> **Pillars:** Agent · Harness · RAG  |  **Built from:** Lessons 03, 04, 05, 08, 12, 13, 14, 15, 19
>
> *Deterministic where the path is known, the model where it isn't, a person where it matters, and never two voices in one conversation.*

## Why does this exist?
Support is where agents meet customers who have real money and real patience limits. The engineering problems are mostly not about the model:
- the same customer writes on chat, then by email;
- webhooks get delivered twice;
- a refund must happen exactly once, and above some amount, not without a person;
- after hand-off, the bot must stop talking.

Each of those failures is visible to the customer, often within seconds.

---

## What problem does it solve?
1. **Omnichannel identity**: a conversation that started in chat and continues by email is one conversation.
2. **Duplicate deliveries**: channels retry webhooks, and a redelivered message must not trigger a second reply or a second refund.
3. **Routing cost and reliability**: most messages are easy to classify. Paying a model for each is wasteful, and trusting a model's free-text label is risky.
4. **Money**: refunds must be idempotent, and large ones need human approval.
5. **Hand-off**: the person taking over needs the context, and the customer must not hear from both the bot and the person.

---

## How would beginners implement this?
```python
@app.post("/webhook/{channel}")
def inbound(channel, payload):
    history = sessions.get(payload["session_id"], [])        # per channel session
    intent = llm(f"What does the customer want? {payload['text']}")
    if "refund" in intent.lower():
        billing.refund(payload["customer_id"], amount=find_duplicate(payload["customer_id"]))
    reply = llm(f"Reply to: {payload['text']}\nHistory: {history}")
    send(channel, reply)
```

### Why does that fail?
1. **History is per channel session**, so the email after the chat starts from nothing.
2. **A redelivered webhook** refunds twice and replies twice.
3. **The model is called for every message**, and its free-text label is matched with `in`. "Not a refund question" contains "refund".
4. **Any refund, any amount, automatically**, with no idempotency key.
5. **No hand-off path.** A frustrated customer keeps getting bot replies, and if a person does step in, the bot keeps answering alongside them.

---

## How is this solved in production?
| Concern | How | Lesson |
|---|---|---|
| Duplicate webhooks | `processed(message_id)` checked first, and written in the **same transaction** as the conversation | 12 |
| One conversation per customer | `ConversationStore.open_for(customer_id)` across all channels; JSON body + `version` column | 15, 19 |
| Concurrent writers | `UPDATE ... WHERE version = ?`, so a stale save raises instead of overwriting | 19 |
| Routing | regex rules first (`handoff`, `billing`, `account`); the model only for the rest; an unknown label goes to `handoff` | 03, 14 |
| Answers | grounded help-centre text with its source ID | 05 |
| Refunds | idempotency key `refund:<conversation>:<order>`; at or below the limit, automatic; above it, a **draft** for a person | 04, 08 |
| Hand-off | the conversation status becomes `human`; a packet goes to the queue with the reason, draft, channels and last messages; the bot stays silent from then on | 08, 13 |
| Frustration | two frustrated customer messages trigger a hand-off, however the latest one routes | — |

In the demo, 7 messages need just **2** model calls, because the rules handle the rest. The redelivered WhatsApp message gets `None` and no second refund. acct-17's 180.00 duplicate charge becomes a **draft** in the human queue, and when acct-17 follows up by **email**, the message joins the same conversation and the bot stays quiet. A message the model can't place ("SHIPPING-ISH" isn't an intent) goes to a person instead of being forced into the nearest bucket.

---

## Design Tradeoffs

### Rules first vs. model for everything
* **Rules first** (this system): free, instant, testable and predictable for the clear cases. Rules need maintenance, and they miss phrasings nobody wrote down, which is why the model handles the remainder.
* **Model for everything**: handles any phrasing, at a cost and latency for every message, with a classification that can drift whenever the model changes. Evaluate it (Lesson 09) if you go this way.

### Automatic refunds vs. human approval
* **Automatic up to a limit**: fast for the common small case, with the risk bounded by the limit. Pair it with per-customer caps (Lesson 08's threshold-splitting failure).
* **Approval above it**: slower, but a person checks the expensive cases, and the draft makes their job a click instead of a re-investigation.

### Per-customer conversation vs. per-channel session
* **Per customer** (this system): continuity across channels. Customers with several unrelated issues get one thread, so close and reopen conversations per issue.
* **Per channel**: simple, with no identity matching, but customers repeat themselves every time they switch channels.

---

## Failure Cases to Watch For
1. **Double replies or double refunds** from webhook redeliveries.
2. **Two voices**: the bot replying after a person has taken over.
3. **Lost context at hand-off**: the person asks the customer to repeat everything.
4. **Free-text model labels** matched loosely, or forced into a category.
5. **Refund limits without idempotency**, or without per-customer totals.
6. **Identity mismatches**: email and chat accounts not linked, so two conversations exist, or the wrong customer's conversation is resumed.
7. **Conversations that never close**: old issues resurface in new ones.
8. **Frustration ignored**: the bot keeps answering an angry customer with help-centre text.

---

## Try It
```sh
cd part-3-complete-systems/customer-support
python example.py            # SQLite in memory, keyed fake classifier
python -m pytest tests.py -q
```
What to watch for: the second identical WhatsApp delivery returns `None`. acct-3's 20.00 refund is automatic, while acct-17's 180.00 becomes a draft in the human queue. acct-17's email returns `None` because a person owns the conversation now. Routing makes only 2 model calls for 7 messages.

Experiment: in `SupportAgent.handle`, always call `self._bot_reply` (drop the `conv.status == "human"` check) and run the tests. `test_bot_stays_silent_after_handoff` fails: the customer gets bot replies in a conversation a person is handling.
