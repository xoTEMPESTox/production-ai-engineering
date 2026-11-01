"""
Lesson 07: Multi-Agent Coordination.

An orchestrator fans independent sub-questions out to sub-agents and fans
their results back in. Each sub-agent starts with a fresh context and sees
only its own task and its own document scope. It hands back a typed,
size-limited Finding, not its transcript. The harness bounds parallelism
and wall-clock time, records which tasks failed (so the final answer can't
silently skip them), and accounts tokens per agent.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user"]
    content: str


class LLMClient(Protocol):
    def complete(self, messages: list[Message]) -> str: ...


class KeyedLLM:
    """Deterministic fake for concurrent callers: the reply is chosen by which
    key appears in the last message (call order isn't stable across threads)."""

    def __init__(self, replies: dict[str, str], delays: Optional[dict[str, float]] = None) -> None:
        self._replies = replies
        self._delays = delays or {}
        self._lock = threading.Lock()
        self.seen: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        with self._lock:
            self.seen.append(list(messages))
        key = next(k for k in self._replies if k in messages[-1].content)
        time.sleep(self._delays.get(key, 0.0))
        return self._replies[key]


# --- Hand-off contracts ---

class SubTask(BaseModel):
    id: str
    question: str
    scope: str  # which document collection this sub-agent may read


class Finding(BaseModel):
    """What a sub-agent hands back: a short, cited summary, not its transcript."""
    model_config = ConfigDict(extra="forbid")
    task_id: str
    answer: str = Field(min_length=1, max_length=400)
    citations: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class SubResult:
    task: SubTask
    finding: Optional[Finding]
    error: str = ""
    tokens: int = 0


@dataclass(frozen=True)
class Report:
    answer: Optional[str]
    results: tuple[SubResult, ...]
    missing: tuple[str, ...]         # task ids without a verified finding
    tokens_by_agent: dict[str, int]  # "orchestrator" + one entry per task
    unsupported: tuple[str, ...] = ()  # cited in the answer, but no verified finding cited it

    @property
    def complete(self) -> bool:
        return self.answer is not None and not self.missing and not self.unsupported


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


SUBAGENT_SYSTEM = ("Answer one task using only the sources given. Reply with JSON only: "
                   '{"task_id": ..., "answer": ..., "citations": [source ids]}. At most 400 characters.')
SYNTH_SYSTEM = ("Combine the findings into one answer for the user. Use only the findings. "
                "If a part is listed as missing, say plainly that it could not be verified.")


class Orchestrator:
    def __init__(self, llm: LLMClient, corpora: dict[str, dict[str, str]],
                 max_parallel: int = 3, deadline_s: float = 10.0) -> None:
        self._llm = llm
        self._corpora = corpora
        self._max_parallel = max_parallel
        self._deadline_s = deadline_s

    def _sub_agent(self, task: SubTask) -> SubResult:
        """Runs in a worker thread with a fresh context: this task, this scope, nothing else."""
        corpus = self._corpora.get(task.scope, {})
        sources = {doc_id: text for doc_id, text in corpus.items() if _words(task.question) & _words(text)}
        block = "\n".join(f"[{doc_id}] {text}" for doc_id, text in sources.items()) or "(no matching sources)"
        messages = [Message("system", SUBAGENT_SYSTEM),
                    Message("user", f"Task {task.id}: {task.question}\nSources:\n{block}")]
        reply = self._llm.complete(messages)
        tokens = sum(estimate_tokens(m.content) for m in messages) + estimate_tokens(reply)
        try:
            finding = Finding.model_validate_json(reply)
        except ValidationError as exc:
            return SubResult(task, None, f"invalid hand-off: {exc.error_count()} error(s)", tokens)
        if finding.task_id != task.id:
            return SubResult(task, None, f"hand-off is for task {finding.task_id}, expected {task.id}", tokens)
        unknown = sorted(set(finding.citations) - set(sources))
        if unknown:
            return SubResult(task, None, f"cites sources it was not given: {unknown}", tokens)
        return SubResult(task, finding, "", tokens)

    def run(self, goal: str, tasks: list[SubTask]) -> Report:
        # Fan out: bounded parallelism and one deadline for the whole batch.
        pool = ThreadPoolExecutor(max_workers=self._max_parallel)
        futures = {task.id: pool.submit(self._sub_agent, task) for task in tasks}
        wait(futures.values(), timeout=self._deadline_s)
        pool.shutdown(wait=False, cancel_futures=True)  # don't let stragglers hold the response

        results = []
        for task in tasks:
            future = futures[task.id]
            if not future.done():
                results.append(SubResult(task, None, f"no result within {self._deadline_s}s"))
            elif future.exception() is not None:
                results.append(SubResult(task, None, f"{type(future.exception()).__name__}: {future.exception()}"))
            else:
                results.append(future.result())

        # Fan in: the harness, not the model, decides what is missing.
        found = [r for r in results if r.finding is not None]
        missing = tuple(r.task.id for r in results if r.finding is None)
        tokens = {r.task.id: r.tokens for r in results}
        if not found:
            return Report(None, tuple(results), missing, {**tokens, "orchestrator": 0})

        findings = "\n".join(f"- {r.task.id} ({r.task.question}): {r.finding.answer} {r.finding.citations}"
                             for r in found)
        gaps = "\n".join(f"- {r.task.id} ({r.task.question}): {r.error}" for r in results if r.finding is None)
        messages = [Message("system", SYNTH_SYSTEM),
                    Message("user", f"SYNTHESIZE for goal: {goal}\nFindings:\n{findings}\nMissing:\n{gaps or '- none'}")]
        answer = self._llm.complete(messages)
        tokens["orchestrator"] = sum(estimate_tokens(m.content) for m in messages) + estimate_tokens(answer)
        # The synthesiser may still claim the missing part: check its citations against verified findings.
        verified = {c for r in found for c in r.finding.citations}
        unsupported = tuple(sorted(set(re.findall(r"\[([\w-]+)\]", answer)) - verified))
        return Report(answer, tuple(results), missing, tokens, unsupported)


CORPORA = {
    "billing": {"billing-04": "Annual plans can be refunded in full within 30 days of purchase.",
                "billing-07": "Monthly plans are not refundable; cancel any time before renewal."},
    "privacy": {"privacy-02": "Customers can export all workspace data from Settings > Export before closing.",
                "privacy-05": "Data exports stay available for 7 days after they are created."},
}
TASKS = [SubTask(id="t1", question="Can an annual plan bought 20 days ago be refunded?", scope="billing"),
         SubTask(id="t2", question="Can the customer export workspace data before closing?", scope="privacy")]

if __name__ == "__main__":
    replies = {
        "Task t1": '{"task_id": "t1", "answer": "Yes: annual plans are refundable in full for 30 days.", '
                   '"citations": ["billing-04"]}',
        "Task t2": '{"task_id": "t2", "answer": "Yes, from Settings > Export.", "citations": ["privacy-02"]}',
        "SYNTHESIZE": "You can get a full refund (within 30 days) [billing-04]. Export your data first "
                      "from Settings > Export [privacy-02].",
    }
    goal = "Refund acct-17's annual plan, but let them export their data first"
    for title, delays in [("all sub-agents answer", {}), ("t2 misses the deadline", {"Task t2": 1.0})]:
        llm = KeyedLLM(replies, delays)
        report = Orchestrator(llm, CORPORA, deadline_s=0.3).run(goal, TASKS)
        print(f"\n== {title}: complete={report.complete}, missing={list(report.missing)}, "
              f"unsupported citations={list(report.unsupported)}")
        for result in report.results:
            print(f"  {result.task.id}: {result.finding.answer if result.finding else 'FAILED: ' + result.error}")
        print(f"  tokens by agent: {report.tokens_by_agent}")
        print(f"  answer: {report.answer}")
        if not report.complete:
            print("  -> the synthesis still claims the export part. The harness flags it as partial and "
                  "unsupported; it doesn't rely on the model to admit the gap.")
