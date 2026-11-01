import asyncio
import json
import unittest

from example import (
    REPLY, AnswerExtractor, FakeStreamingLLM, StreamStats, answer_events, create_app, parse_sse,
)


async def collect(llm, question: str = "q", stats: StreamStats | None = None) -> list[tuple[str, dict]]:
    frames = [f async for f in answer_events(llm, question, stats or StreamStats())]
    return parse_sse("".join(frames))


async def call_asgi(app, query: str, disconnect_after_chunks: int | None = None) -> tuple[dict, str]:
    """Drive the ASGI app directly: no server, no HTTP client library."""
    started, body, gone = {}, [], asyncio.Event()

    async def receive() -> dict:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            started.update(message)
        elif message["type"] == "http.response.body" and message.get("body"):
            body.append(message["body"].decode())
            if disconnect_after_chunks is not None and len(body) >= disconnect_after_chunks:
                gone.set()  # the user closed the tab

    scope = {"type": "http", "method": "GET", "path": "/answer", "raw_path": b"/answer",
             "query_string": query.encode(), "headers": [], "http_version": "1.1",
             "scheme": "http", "server": ("test", 80), "client": ("test", 1234), "root_path": ""}
    await app(scope, receive, send)
    return started, "".join(body)


class TestExtractor(unittest.TestCase):
    def test_answer_text_streams_out_of_partial_json(self) -> None:
        extractor, out = AnswerExtractor(), []
        for chunk in ['{"ans', 'wer": "Ref', 'unds within', ' 30 days."', ', "citations": ["b-4"]}']:
            out.append(extractor.feed(chunk))
        self.assertEqual(out, ["", "Ref", "unds within", " 30 days.", ""])

    def test_escapes_split_across_chunks(self) -> None:
        extractor = AnswerExtractor()
        text = "".join(extractor.feed(c) for c in ['{"answer": "line one\\', 'nline "', ', "x": 1}'])
        self.assertEqual(text, "line one\nline ")
        unicode = AnswerExtractor()
        self.assertEqual("".join(unicode.feed(c) for c in ['{"answer": "caf\\u0', '0e9"}']), "café")

    def test_streamed_text_equals_the_final_answer(self) -> None:
        events = asyncio.run(collect(FakeStreamingLLM(REPLY, chunk_chars=5)))
        streamed = "".join(d["text"] for e, d in events if e == "delta")
        self.assertEqual(streamed, json.loads(REPLY)["answer"])


class TestEvents(unittest.TestCase):
    def test_meta_first_then_deltas_then_done(self) -> None:
        stats = StreamStats()
        events = asyncio.run(collect(FakeStreamingLLM(REPLY), stats=stats))
        kinds = [e for e, _ in events]
        self.assertEqual((kinds[0], kinds[-1]), ("meta", "done"))
        self.assertTrue(set(kinds[1:-1]) == {"delta"})
        self.assertEqual(events[-1][1]["citations"], ["billing-04"])

    def test_time_to_first_token_is_measured_separately(self) -> None:
        stats = StreamStats()
        asyncio.run(collect(FakeStreamingLLM(REPLY, first_token_s=0.1, per_chunk_s=0.01), stats=stats))
        self.assertGreaterEqual(stats.ttft_ms, 90)
        self.assertGreater(stats.total_ms, stats.ttft_ms + 50)

    def test_invalid_final_json_tells_the_client_to_retract(self) -> None:
        events = asyncio.run(collect(FakeStreamingLLM('{"answer": "Probably 90 days')))
        self.assertIn("delta", [e for e, _ in events])  # the text was shown...
        self.assertEqual(events[-1], ("error", {"message": "the answer failed validation", "retract": True}))

    def test_upstream_failure_mid_stream_becomes_an_error_event(self) -> None:
        events = asyncio.run(collect(FakeStreamingLLM(REPLY, fail_after_chunks=4)))
        self.assertEqual(events[-1][0], "error")
        self.assertIn("ConnectionError", events[-1][1]["message"])


class TestCancellation(unittest.TestCase):
    def test_closing_the_stream_stops_the_model_immediately(self) -> None:
        async def go() -> tuple[bool, int]:
            llm = FakeStreamingLLM(REPLY)
            stream = answer_events(llm, "q", StreamStats())
            for _ in range(3):
                await stream.__anext__()
            await stream.aclose()
            # Check now, inside the loop: without aclosing, the upstream generator is only
            # finalised later by the event loop, and keeps the model call open until then.
            return llm.stopped_early, llm.chunks_sent

        stopped_early, chunks_sent = asyncio.run(go())
        self.assertTrue(stopped_early)
        self.assertLess(chunks_sent, 5)


class TestHttpEndpoint(unittest.TestCase):
    def test_endpoint_streams_server_sent_events(self) -> None:
        started, body = asyncio.run(call_asgi(create_app(lambda: FakeStreamingLLM(REPLY)), "q=refunds"))
        headers = {k.decode(): v.decode() for k, v in started["headers"]}
        self.assertEqual(started["status"], 200)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(headers["cache-control"], "no-cache")
        self.assertEqual(parse_sse(body)[-1][0], "done")

    def test_client_disconnect_cancels_generation(self) -> None:
        llms: list[FakeStreamingLLM] = []

        def make() -> FakeStreamingLLM:
            llms.append(FakeStreamingLLM(REPLY, per_chunk_s=0.02))
            return llms[-1]

        _, body = asyncio.run(call_asgi(create_app(make), "q=refunds", disconnect_after_chunks=3))
        self.assertTrue(llms[0].stopped_early)
        self.assertNotIn("event: done", body)


if __name__ == "__main__":
    unittest.main()
