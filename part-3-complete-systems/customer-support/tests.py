import unittest

from example import ConversationStore, InboundMessage, KeyedLLM, SupportAgent


def agent(replies: dict | None = None) -> SupportAgent:
    return SupportAgent(ConversationStore(), KeyedLLM(replies or {}))


def msg(mid: str, customer: str, text: str, channel: str = "chat") -> InboundMessage:
    return InboundMessage(mid, channel, customer, text)


class TestIntake(unittest.TestCase):
    def test_redelivered_webhook_is_handled_once(self) -> None:
        a = agent()
        self.assertIsNotNone(a.handle(msg("m1", "acct-3", "I was charged twice")))
        self.assertIsNone(a.handle(msg("m1", "acct-3", "I was charged twice")))
        self.assertEqual(len(a.store.open_for("acct-3").transcript), 2)  # one customer message, one reply
        self.assertEqual(a.refunds, {"refund:conv-acct-3:ord-2": 2000})

    def test_conversation_resumes_across_channels(self) -> None:
        a = agent()
        a.handle(msg("c1", "acct-40", "I'm locked out", "chat"))
        a.handle(msg("e1", "acct-40", "still locked out of my login", "email"))
        conv = a.store.open_for("acct-40")
        self.assertEqual([t["channel"] for t in conv.transcript if t["from"] == "customer"], ["chat", "email"])
        self.assertEqual(conv.version, 2)

    def test_stale_write_is_refused(self) -> None:
        a = agent()
        a.handle(msg("c1", "acct-40", "I'm locked out"))
        first, second = a.store.open_for("acct-40"), a.store.open_for("acct-40")
        first.transcript.append({"from": "human", "channel": "chat", "text": "on it"})
        a.store.commit(first, "x1")
        with self.assertRaises(RuntimeError):
            a.store.commit(second, "x2")


class TestRouting(unittest.TestCase):
    def test_rules_route_without_calling_the_model(self) -> None:
        a = agent()
        self.assertEqual(a.route("I need a refund"), ("billing", "rule"))
        self.assertEqual(a.route("can't log in"), ("account", "rule"))
        self.assertEqual(a.llm.calls, 0)

    def test_model_handles_what_rules_cannot(self) -> None:
        a = agent({"my thing broke": "account"})
        self.assertEqual(a.route("hmm my thing broke"), ("account", "model"))

    def test_unknown_model_label_goes_to_a_person(self) -> None:
        a = agent({"weird": "SHIPPING-ISH"})
        self.assertEqual(a.route("a weird question")[0], "handoff")


class TestRefundsAndHandoff(unittest.TestCase):
    def test_small_refund_is_automatic_and_idempotent(self) -> None:
        a = agent()
        a.handle(msg("m1", "acct-3", "I was charged twice"))
        a.handle(msg("m2", "acct-3", "please refund the duplicate"))  # asks again
        self.assertEqual(a.refunds, {"refund:conv-acct-3:ord-2": 2000})

    def test_large_refund_becomes_a_draft_for_a_person(self) -> None:
        a = agent()
        reply = a.handle(msg("m1", "acct-17", "I was charged twice"))
        self.assertIn("member of our team", reply)
        self.assertEqual(a.refunds, {})
        item = a.human_queue[0]
        self.assertEqual((item["reason"], item["draft"]["cents"]), ("refund above the automatic limit", 18000))

    def test_bot_stays_silent_after_handoff(self) -> None:
        a = agent()
        a.handle(msg("m1", "acct-17", "talk to a human please"))
        self.assertIsNone(a.handle(msg("m2", "acct-17", "hello?", "email")))
        conv = a.store.open_for("acct-17")
        self.assertEqual(conv.status, "human")
        self.assertEqual(conv.transcript[-1]["text"], "hello?")  # kept for the person who takes over

    def test_repeated_frustration_hands_off(self) -> None:
        a = agent()
        a.handle(msg("m1", "acct-40", "this is ridiculous, I'm locked out"))
        reply = a.handle(msg("m2", "acct-40", "third time asking, can't log in"))
        self.assertIn("member of our team", reply)
        self.assertEqual(a.human_queue[0]["reason"], "customer frustrated")

    def test_handoff_packet_carries_context(self) -> None:
        a = agent()
        a.handle(msg("m1", "acct-40", "I'm locked out"))
        a.handle(msg("m2", "acct-40", "I want a person", "email"))
        packet = a.human_queue[0]
        self.assertEqual(packet["channels"], ["chat", "email"])
        self.assertEqual(packet["reason"], "customer asked for a person")
        self.assertIn("I'm locked out", packet["last_messages"])


if __name__ == "__main__":
    unittest.main()
