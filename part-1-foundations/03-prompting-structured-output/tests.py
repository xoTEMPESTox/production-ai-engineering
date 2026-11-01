import json
import unittest
from dataclasses import replace

from example import (
    GROUNDED_ANSWER_V2, PromptRegistry, ScriptedLLM, Source, answer_question, parse_output, render,
)

SOURCES = [
    Source("pto-01", "Full-time employees accrue 1.5 days of paid leave per month."),
    Source("pto-03", "Up to 10 unused leave days carry over to the next calendar year."),
]
ALLOWED = {s.id for s in SOURCES}


def reply(found: bool, answer: str, citations: list[str]) -> str:
    return json.dumps({"found": found, "answer": answer, "citations": citations})


GOOD = reply(True, "Up to 10 days carry over.", ["pto-03"])


class TestValidation(unittest.TestCase):
    def test_valid_reply_is_accepted_in_one_call(self) -> None:
        llm = ScriptedLLM([GOOD])
        result = answer_question(llm, GROUNDED_ANSWER_V2, SOURCES, "carry over?")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer.citations, ["pto-03"])
        self.assertEqual(result.model_calls, 1)

    def test_code_fenced_json_is_accepted(self) -> None:
        answer, errors = parse_output(f"```json\n{GOOD}\n```", ALLOWED)
        self.assertIsNotNone(answer)
        self.assertEqual(errors, [])

    def test_schema_valid_but_invented_citation_is_rejected(self) -> None:
        answer, errors = parse_output(reply(True, "10 days.", ["pto-09"]), ALLOWED)
        self.assertIsNone(answer)
        self.assertIn("unknown source id(s) ['pto-09']", errors[0])
        self.assertIn("cite only ['pto-01', 'pto-03']", errors[0])

    def test_claiming_found_without_citations_is_rejected(self) -> None:
        answer, errors = parse_output(reply(True, "10 days.", []), ALLOWED)
        self.assertIsNone(answer)
        self.assertIn("requires at least one citation", errors[0])

    def test_renamed_field_fails_instead_of_defaulting(self) -> None:
        renamed = json.dumps({"found": False, "answer": "Not covered.", "sources": ["pto-01"]})
        answer, errors = parse_output(renamed, ALLOWED)
        self.assertIsNone(answer)
        self.assertTrue(any(e.startswith("sources:") for e in errors))

    def test_abstaining_is_a_valid_answer(self) -> None:
        answer, _ = parse_output(reply(False, "The sources don't cover travel insurance.", []), ALLOWED)
        self.assertFalse(answer.found)


class TestRepairAndFailClosed(unittest.TestCase):
    def test_one_repair_with_the_exact_error(self) -> None:
        llm = ScriptedLLM(["Sure! 10 days.", GOOD])
        result = answer_question(llm, GROUNDED_ANSWER_V2, SOURCES, "carry over?")

        self.assertEqual(result.status, "repaired")
        self.assertEqual(result.model_calls, 2)
        repair_turn = llm.seen[1]
        # The model sees its own bad reply, then the validator's error message.
        self.assertEqual(repair_turn[-2].role, "assistant")
        self.assertEqual(repair_turn[-2].content, "Sure! 10 days.")
        self.assertIn("Invalid JSON", repair_turn[-1].content)
        # The first attempt's error is kept on the result, for monitoring repair rates.
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Invalid JSON", result.errors[0])

    def test_still_invalid_after_repair_fails_closed(self) -> None:
        llm = ScriptedLLM(["prose", reply(True, "10 days.", ["made-up"]), GOOD])
        result = answer_question(llm, GROUNDED_ANSWER_V2, SOURCES, "carry over?")

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.answer)
        self.assertEqual(len(llm.seen), 2)  # no third attempt, even though it would have worked
        self.assertEqual(len(result.errors), 2)


class TestPrompts(unittest.TestCase):
    def test_stable_prefix_and_question_last(self) -> None:
        a = render(GROUNDED_ANSWER_V2, SOURCES, "How much leave accrues?")
        b = render(GROUNDED_ANSWER_V2, SOURCES, "Does leave carry over?")

        self.assertEqual(a[0], b[0])  # system prompt identical across requests
        prefix_a, question_a = a[1].content.split("Question:")
        prefix_b, question_b = b[1].content.split("Question:")
        self.assertEqual(prefix_a, prefix_b)  # sources block identical too
        self.assertTrue(a[1].content.endswith("How much leave accrues?"))

    def test_schema_is_part_of_the_prompt(self) -> None:
        self.assertIn('"citations"', GROUNDED_ANSWER_V2.system)

    def test_editing_a_registered_version_in_place_is_refused(self) -> None:
        registry = PromptRegistry()
        registry.register(GROUNDED_ANSWER_V2)
        edited = replace(GROUNDED_ANSWER_V2, system=GROUNDED_ANSWER_V2.system + "\nBe concise.")

        self.assertNotEqual(edited.fingerprint, GROUNDED_ANSWER_V2.fingerprint)
        with self.assertRaises(ValueError):
            registry.register(edited)
        registry.register(replace(edited, version="v3"))  # a new version is fine
        self.assertEqual(registry.get("grounded_answer", "v3").fingerprint, edited.fingerprint)

    def test_result_records_the_exact_prompt(self) -> None:
        result = answer_question(ScriptedLLM([GOOD]), GROUNDED_ANSWER_V2, SOURCES, "q")
        self.assertEqual(result.prompt_id, f"grounded_answer@v2#{GROUNDED_ANSWER_V2.fingerprint}")


if __name__ == "__main__":
    unittest.main()
