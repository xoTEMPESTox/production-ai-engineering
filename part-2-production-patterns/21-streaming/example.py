"""
Lesson 21: Streaming.

Answers stream to the user as server-sent events. A meta event goes out at
once; the answer text streams out of a structured JSON reply while it's
still arriving (an incremental extractor reads the "answer" field); the
complete JSON is validated at the end, and if it fails the client is told
to retract what it showed. Time to first token is measured separately from
total time. When the client disconnects, the upstream model stream is
cancelled, so nobody pays for tokens nobody will read.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, Protocol

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError


class StreamingLLM(Protocol):
    def stream(self, prompt: str) -> AsyncIterator[str]: ...


class FakeStreamingLLM:
    """Streams a fixed reply in small chunks after a first-token delay, and records
    how much it generated and whether it was stopped early."""

    def __init__(self, reply: str, chunk_chars: int = 6, first_token_s: float = 0.05,
                 per_chunk_s: float = 0.005, fail_after_chunks: Optional[int] = None) -> None:
        self.reply = reply
        self.chunk_chars = chunk_chars
        self.first_token_s = first_token_s
        self.per_chunk_s = per_chunk_s
        self.fail_after_chunks = fail_after_chunks
        self.chunks_sent = 0
        self.finished = False
        self.stopped_early = False

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        try:
            await asyncio.sleep(self.first_token_s)  # queueing + reading the prompt, before any output
            for i in range(0, len(self.reply), self.chunk_chars):
                if self.chunks_sent == self.fail_after_chunks:
                    raise ConnectionError("upstream stream reset")
                yield self.reply[i:i + self.chunk_chars]
                self.chunks_sent += 1
                await asyncio.sleep(self.per_chunk_s)
            self.finished = True
        finally:  # runs on cancellation and on aclose() too
            self.stopped_early = not self.finished


def sse(event: str, data: dict) -> str:
    """One server-sent event. json.dumps keeps it on one line; raw text with
    newlines would need a separate 'data:' line per line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _decode_prefix(raw: str) -> tuple[str, bool]:
    """Decode a JSON string body that may be cut off mid-way. Returns (text, closed)."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '"':
            return "".join(out), True
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(raw):
            break  # an escape split across chunks: wait for the rest
        if raw[i + 1] != "u":
            out.append(JSON_ESCAPES.get(raw[i + 1], raw[i + 1]))  # an invalid escape fails final validation
            i += 2
            continue
        if i + 6 > len(raw):
            break
        code = int(raw[i + 2:i + 6], 16)
        if 0xD800 <= code < 0xDC00:  # an emoji arrives as two \u escapes (a surrogate pair)
            if i + 12 > len(raw):
                break
            low = int(raw[i + 8:i + 12], 16)
            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
            i += 6
        out.append(chr(code))
        i += 6
    return "".join(out), False


class AnswerExtractor:
    """Reads the value of "answer" out of a JSON object while it's still streaming."""

    def __init__(self) -> None:
        self._buffer, self._start, self._emitted = "", -1, 0

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        if self._start < 0:
            key = self._buffer.find('"answer"')
            colon = self._buffer.find(":", key) if key >= 0 else -1
            quote = self._buffer.find('"', colon + 1) if colon >= 0 else -1
            if quote < 0:
                return ""
            self._start = quote + 1
        text, _ = _decode_prefix(self._buffer[self._start:])
        new, self._emitted = text[self._emitted:], len(text)
        return new


class Answer(BaseModel):
    answer: str
    citations: list[str]


@dataclass
class StreamStats:
    ttft_ms: Optional[float] = None  # time to first token: what the user feels as "is it working?"
    total_ms: Optional[float] = None
    chunks: int = 0


async def answer_events(llm: StreamingLLM, question: str, stats: StreamStats,
                        clock: Callable[[], float] = time.perf_counter) -> AsyncIterator[str]:
    started = clock()
    yield sse("meta", {"question": question})  # sent before the model starts: the request is alive
    extractor, raw = AnswerExtractor(), []
    try:
        # aclosing: if our consumer goes away, close the upstream stream now, not whenever
        # the garbage collector gets to it; that's what actually stops the model.
        async with aclosing(llm.stream(question)) as chunks:
            async for chunk in chunks:
                if stats.ttft_ms is None:
                    stats.ttft_ms = round((clock() - started) * 1000, 1)
                raw.append(chunk)
                stats.chunks += 1
                delta = extractor.feed(chunk)
                if delta:
                    yield sse("delta", {"text": delta})
    except Exception as exc:  # the upstream died mid-answer: say so, don't just drop the connection
        yield sse("error", {"message": f"{type(exc).__name__}: {exc}", "retract": True})
        return
    stats.total_ms = round((clock() - started) * 1000, 1)
    try:
        final = Answer.model_validate_json("".join(raw))  # stream first, validate at the end
    except ValidationError:
        yield sse("error", {"message": "the answer failed validation", "retract": True})
        return
    yield sse("done", {"citations": final.citations, "ttft_ms": stats.ttft_ms, "total_ms": stats.total_ms})


def create_app(make_llm: Callable[[], StreamingLLM]) -> FastAPI:
    app = FastAPI()

    @app.get("/answer")
    async def answer(q: str) -> StreamingResponse:
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # no proxy buffering
        return StreamingResponse(answer_events(make_llm(), q, StreamStats()),
                                 media_type="text/event-stream", headers=headers)

    return app


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", "message"), json.loads(fields.get("data", "{}"))))
    return events


REPLY = json.dumps({"answer": "Annual plans are refunded in full within 30 days.\nAsk billing for help.",
                    "citations": ["billing-04"]})

if __name__ == "__main__":
    async def demo() -> None:
        stats = StreamStats()
        frames = [frame async for frame in answer_events(FakeStreamingLLM(REPLY), "refund window?", stats)]
        for event, data in parse_sse("".join(frames)):
            print(f"{event:<6} {data}")
        print(f"ttft {stats.ttft_ms} ms vs total {stats.total_ms} ms over {stats.chunks} chunks\n")

        llm = FakeStreamingLLM(REPLY)
        stream = answer_events(llm, "refund window?", StreamStats())
        for _ in range(3):  # the user reads three frames, then closes the tab
            await stream.__anext__()
        await stream.aclose()
        print(f"client left: model stopped early={llm.stopped_early} after {llm.chunks_sent} "
              f"of {len(range(0, len(REPLY), llm.chunk_chars))} chunks")

        bad = [f async for f in answer_events(FakeStreamingLLM('{"answer": "Probably 90 days'), "q", StreamStats())]
        print("truncated JSON ends with:", parse_sse("".join(bad))[-1])

    asyncio.run(demo())
