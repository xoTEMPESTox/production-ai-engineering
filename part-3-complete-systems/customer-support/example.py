"""
System: Customer Support.

One support agent across chat, email and messaging. Inbound messages are
normalised, and a redelivered webhook is ignored by message id (Lesson 12).
Routing is rules first and the model only for what the rules can't place;
an answer that isn't a known intent goes to a person (Lessons 03, 14). The
conversation is persisted per customer, so a chat that continues by email
resumes where it left off (Lessons 15, 19). Refunds under a threshold run
with an idempotency key; larger ones become a draft for a person to approve
(Lessons 04, 08). A hand-off sends a context packet to the human queue, and
from then on the bot stays silent in that conversation.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol

Channel = Literal["chat", "email", "whatsapp"]
INTENTS = ("billing", "account", "handoff")


@dataclass(frozen=True)
class InboundMessage:
    message_id: str  # the channel's own id: the dedup key for webhook redeliveries
    channel: Channel
    customer_id: str
    text: str


@dataclass
class Conversation:
    id: str
    customer_id: str
    status: Literal["bot", "human", "closed"] = "bot"
    transcript: list[dict] = field(default_factory=list)
    handoff: Optional[dict] = None
    version: int = 0


class ConversationStore:
    """SQLite: one open conversation per customer, versioned; processed message ids in the same database."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE conversations (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, status TEXT NOT NULL,
                                        body TEXT NOT NULL, version INTEGER NOT NULL);
            CREATE TABLE processed (message_id TEXT PRIMARY KEY);
        """)

    def seen(self, message_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM processed WHERE message_id = ?", (message_id,)).fetchone() is not None

    def open_for(self, customer_id: str) -> Optional[Conversation]:
        row = self.db.execute("SELECT id, status, body, version FROM conversations WHERE customer_id = ? "
                              "AND status != 'closed'", (customer_id,)).fetchone()
        if row is None:
            return None
        body = json.loads(row[2])
        return Conversation(row[0], customer_id, row[1], body["transcript"], body["handoff"], row[3])

    def commit(self, conv: Conversation, message_id: str) -> None:
        """Save the conversation and mark the message processed in one transaction, with a version check."""
        body = json.dumps({"transcript": conv.transcript, "handoff": conv.handoff})
        with self.db:
            if conv.version == 0:
                self.db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, 1)", (conv.id, conv.customer_id,
                                                                                 conv.status, body))
            elif self.db.execute("UPDATE conversations SET status = ?, body = ?, version = version + 1 "
                                 "WHERE id = ? AND version = ?", (conv.status, body, conv.id, conv.version)).rowcount != 1:
                raise RuntimeError(f"{conv.id} changed since it was read; reload and retry")
            self.db.execute("INSERT INTO processed VALUES (?)", (message_id,))
        conv.version += 1


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class KeyedLLM:
    """Deterministic stand-in for the intent classifier: reply chosen by a phrase in the prompt."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies, self.calls = replies, 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return next((reply for key, reply in self.replies.items() if key in prompt), "unsure")


RULES = [(re.compile(r"\b(human|person|agent|representative)\b", re.I), "handoff"),
         (re.compile(r"\b(refund|charged twice|money back|invoice)\b", re.I), "billing"),
         (re.compile(r"\b(password|log ?in|locked out|2fa)\b", re.I), "account")]
FRUSTRATED = re.compile(r"\b(useless|ridiculous|terrible|angry|third time)\b", re.I)
ORDERS = {"acct-17": {"order": "ord-9", "duplicate_charge_cents": 18_000},
          "acct-3": {"order": "ord-2", "duplicate_charge_cents": 2_000}}
HELP = {"account": "You can reset your password from Settings > Security; reset links expire after 1 hour. [account-07]"}


@dataclass
class SupportAgent:
    store: ConversationStore
    llm: LLMClient
    auto_refund_limit_cents: int = 5_000
    refunds: dict[str, int] = field(default_factory=dict)  # idempotency key -> cents (the billing API, faked)
    human_queue: list[dict] = field(default_factory=list)

    def route(self, text: str) -> tuple[str, str]:
        for pattern, intent in RULES:
            if pattern.search(text):
                return intent, "rule"
        label = self.llm.complete(f"Classify as one of {INTENTS}: {text}").strip().lower()
        return (label, "model") if label in INTENTS else ("handoff", "model output not a known intent")

    def handle(self, msg: InboundMessage) -> Optional[str]:
        if self.store.seen(msg.message_id):
            return None  # webhook redelivery: already handled
        conv = self.store.open_for(msg.customer_id) or Conversation(f"conv-{msg.customer_id}", msg.customer_id)
        conv.transcript.append({"from": "customer", "channel": msg.channel, "text": msg.text})
        reply = None if conv.status == "human" else self._bot_reply(conv, msg)  # a person owns it now
        if reply:
            conv.transcript.append({"from": "bot", "channel": msg.channel, "text": reply})
        self.store.commit(conv, msg.message_id)
        return reply

    def _bot_reply(self, conv: Conversation, msg: InboundMessage) -> str:
        frustrated = sum(bool(FRUSTRATED.search(t["text"])) for t in conv.transcript if t["from"] == "customer")
        intent, source = self.route(msg.text)
        if intent == "handoff" or frustrated >= 2:
            reason = ("customer frustrated" if frustrated >= 2 else
                      {"rule": "customer asked for a person", "model": "model routed to a person"}.get(source, source))
            return self._hand_off(conv, reason=reason)
        if intent == "account":
            return HELP["account"]
        order = ORDERS.get(msg.customer_id)
        if order is None:
            return self._hand_off(conv, reason="billing question without a matching order")
        cents, key = order["duplicate_charge_cents"], f"refund:{conv.id}:{order['order']}"
        if cents <= self.auto_refund_limit_cents:
            self.refunds.setdefault(key, cents)  # idempotent: a repeat request doesn't refund twice
            return f"I've refunded the duplicate charge of {cents / 100:.2f} on {order['order']}."
        draft = {"action": "issue_refund", "order": order["order"], "cents": cents, "idempotency_key": key}
        return self._hand_off(conv, reason="refund above the automatic limit", draft=draft)

    def _hand_off(self, conv: Conversation, reason: str, draft: Optional[dict] = None) -> str:
        conv.status = "human"
        conv.handoff = {"reason": reason, "draft": draft, "channels": sorted({t["channel"] for t in conv.transcript}),
                        "last_messages": [t["text"] for t in conv.transcript[-3:]]}
        self.human_queue.append({"conversation": conv.id, **conv.handoff})
        return "I'm passing this to a member of our team with everything so far; they'll reply here."


if __name__ == "__main__":
    llm = KeyedLLM({"my thing broke": "account", "weird question": "SHIPPING-ISH"})
    agent = SupportAgent(ConversationStore(), llm)
    inbound = [
        InboundMessage("wa-1", "whatsapp", "acct-3", "I was charged twice, please refund"),
        InboundMessage("wa-1", "whatsapp", "acct-3", "I was charged twice, please refund"),  # redelivered webhook
        InboundMessage("ch-1", "chat", "acct-17", "I was charged twice for my order"),
        InboundMessage("em-1", "email", "acct-17", "Any update on my refund?"),  # same customer, new channel
        InboundMessage("ch-2", "chat", "acct-40", "I'm locked out, password reset not working"),
        InboundMessage("ch-3", "chat", "acct-41", "hmm my thing broke"),
        InboundMessage("ch-4", "chat", "acct-42", "a weird question about something"),
    ]
    for msg in inbound:
        print(f"{msg.channel:<8} {msg.customer_id:<8} {msg.text[:38]:<40} -> {agent.handle(msg)}")
    print(f"\nrefunds issued: {agent.refunds}")
    print(f"model calls for routing: {llm.calls} of {len(inbound)} messages")
    for item in agent.human_queue:
        print("human queue:", item)
    resumed = agent.store.open_for("acct-17")
    print(f"acct-17 conversation: status={resumed.status}, channels={resumed.handoff['channels']}, "
          f"messages={len(resumed.transcript)}")
