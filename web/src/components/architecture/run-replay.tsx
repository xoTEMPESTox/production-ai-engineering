"use client";

import { useEffect, useRef, useState } from "react";
import { useInView, useReducedMotion } from "framer-motion";
import { useTranslations } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { SimulatorControls } from "@/components/simulator/simulator-controls";

// Simplified from part-1-foundations/02-agent-loop/example.py.
const HARNESSED_LOOP = [
  "for step in range(1, budget.max_steps + 1):",
  "    if clock() - started > budget.max_seconds:",
  "        return stop(StopReason.TIME_BUDGET)",
  "    turn = llm.complete(messages, tools)",
  "    tokens += turn.input_tokens + turn.output_tokens",
  "    if turn.final_answer is not None:",
  "        return stop(StopReason.FINAL_ANSWER, turn.final_answer)",
  "    if tokens > budget.max_tokens:",
  "        return stop(StopReason.TOKEN_BUDGET)",
  "    key = turn.tool_call.fingerprint()",
  "    if seen[key] >= budget.max_identical_calls:",
  "        return stop(StopReason.LOOP_DETECTED)",
  "    seen[key] += 1",
  "    observation, ok = execute(turn.tool_call)  # errors come back as text",
  "    messages.append(Message(\"tool\", observation))",
  "    record(step, TOOL_RESULT if ok else TOOL_ERROR, observation)",
  "return stop(StopReason.MAX_STEPS)",
];

interface RunEvent {
  step: number;
  kind: string;
  detail: string;
  line: number; // the HARNESSED_LOOP line that handles this event
}

// Real output of `python example.py` in lesson 02 (details shortened).
const SCENARIOS: { key: string; answer: string | null; events: RunEvent[] }[] = [
  {
    key: "scenario_recover",
    answer: "Yes. acct-17 is on an annual plan bought 2026-10-20, inside the 30-day full-refund window [billing-04].",
    events: [
      { step: 1, kind: "model_turn", detail: "Look up the plan.", line: 3 },
      { step: 1, kind: "tool_error", detail: "get_plan -> ERROR: KeyError: 'unknown account acct-71'", line: 13 },
      { step: 2, kind: "model_turn", detail: "Typo in the ID; retry.", line: 3 },
      { step: 2, kind: "tool_result", detail: "get_plan -> annual plan, purchased 2026-10-20", line: 15 },
      { step: 3, kind: "model_turn", detail: "Find the refund policy.", line: 3 },
      { step: 3, kind: "tool_result", detail: "search_docs -> [billing-04] Annual plans can be refunded...", line: 15 },
      { step: 4, kind: "model_turn", detail: "Purchased 12 days ago; inside the window.", line: 3 },
      { step: 4, kind: "stopped", detail: "final_answer", line: 6 },
    ],
  },
  {
    key: "scenario_loop",
    answer: null,
    events: [
      { step: 1, kind: "model_turn", detail: "Search again.", line: 3 },
      { step: 1, kind: "tool_result", detail: "search_docs -> no matching documents", line: 15 },
      { step: 2, kind: "model_turn", detail: "Search again.", line: 3 },
      { step: 2, kind: "tool_result", detail: "search_docs -> no matching documents", line: 15 },
      { step: 3, kind: "model_turn", detail: "Search again.", line: 3 },
      { step: 3, kind: "stopped", detail: "loop_detected", line: 11 },
    ],
  },
];

function kindColor(event: RunEvent): string {
  if (event.kind === "tool_error") return "text-red-400";
  if (event.kind === "stopped") return event.detail === "final_answer" ? "text-emerald-400" : "text-amber-400";
  return "text-zinc-300";
}

export function RunReplay() {
  const t = useTranslations("home");
  const reduceMotion = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.3 });
  const [scenario, setScenario] = useState(0);
  const [index, setIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  const { events, answer } = SCENARIOS[scenario];
  const complete = index >= events.length - 1;
  const activeLine = index >= 0 ? events[index].line : -1;

  // Start once the replay scrolls into view; with reduced motion, show the finished run instead.
  useEffect(() => {
    if (!inView) return;
    if (reduceMotion) setIndex(SCENARIOS[0].events.length - 1);
    else setPlaying(true);
  }, [inView, reduceMotion]);

  useEffect(() => {
    if (!playing) return;
    if (complete) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setIndex((i) => i + 1), 1100 / speed);
    return () => clearTimeout(timer);
  }, [playing, complete, index, speed]);

  function choose(next: number) {
    setScenario(next);
    setIndex(reduceMotion ? SCENARIOS[next].events.length - 1 : -1);
    setPlaying(!reduceMotion);
  }

  return (
    <div ref={ref} className="flex min-w-0 flex-col gap-4">
      <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
        <div className="border-b border-zinc-800 px-4 py-2 font-mono text-xs text-zinc-400">
          {t("harness_label")}
        </div>
        <pre className="overflow-x-auto py-4 text-xs leading-relaxed">
          <code>
            {HARNESSED_LOOP.map((line, i) => {
              const at = line.indexOf("#");
              const code = at === -1 ? line : line.slice(0, at);
              return (
                <span
                  key={i}
                  className={cn(
                    "block border-l-2 px-4 transition-colors duration-300",
                    i === activeLine ? "border-amber-400 bg-amber-400/15" : "border-transparent"
                  )}
                >
                  <span className="text-zinc-200">{code || " "}</span>
                  {at !== -1 && <span className="text-zinc-500">{line.slice(at)}</span>}
                </span>
              );
            })}
          </code>
        </pre>
      </div>

      <div className="flex flex-wrap gap-2">
        {SCENARIOS.map((s, i) => (
          <button
            key={s.key}
            onClick={() => choose(i)}
            className={cn(
              "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
              i === scenario
                ? "border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900"
                : "border-[var(--color-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
            )}
          >
            {t(s.key)}
          </button>
        ))}
      </div>

      <SimulatorControls
        isPlaying={playing}
        isComplete={complete}
        currentIndex={index}
        totalSteps={events.length}
        speed={speed}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onStep={() => {
          setPlaying(false);
          setIndex((i) => Math.min(i + 1, events.length - 1));
        }}
        onReset={() => {
          setPlaying(false);
          setIndex(-1);
        }}
        onSpeedChange={setSpeed}
      />

      <div>
        <p className="mb-2 text-sm text-[var(--color-text-secondary)]">{t("harness_keeps")}</p>
        <div className="min-h-[13rem] overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 p-4 font-mono text-xs leading-relaxed">
          {events.slice(0, index + 1).map((event, i) => (
            <div key={`${scenario}-${i}`} className="fade-up whitespace-nowrap">
              <span className="text-zinc-500">step {event.step} </span>
              <span className={cn("inline-block w-24", kindColor(event))}>{event.kind}</span>
              <span className="text-zinc-400">{event.detail}</span>
            </div>
          ))}
          {complete && (
            <div className="fade-up mt-2 whitespace-nowrap" style={{ animationDelay: "0.2s" }}>
              <span className="text-zinc-500">answer: </span>
              {answer ? (
                <span className="text-zinc-200">{answer}</span>
              ) : (
                <>
                  <span className="text-zinc-200">None</span>
                  <span className="text-zinc-500">  # {t("replay_no_answer")}</span>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
