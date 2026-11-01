"use client";

import Link from "next/link";
import { useTranslations, useLocale } from "@/lib/i18n";
import { LEARNING_PATH, VERSION_META, LAYERS } from "@/lib/constants";
import { LayerBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import versionsData from "@/data/generated/versions.json";
import { MessageFlow } from "@/components/architecture/message-flow";

const LAYER_DOT_COLORS: Record<string, string> = {
  foundations: "bg-blue-500",
  patterns: "bg-emerald-500",
  systems: "bg-purple-500",
  infrastructure: "bg-amber-500",
};

const LAYER_BORDER_COLORS: Record<string, string> = {
  foundations: "border-blue-500/30 hover:border-blue-500/60",
  patterns: "border-emerald-500/30 hover:border-emerald-500/60",
  systems: "border-purple-500/30 hover:border-purple-500/60",
  infrastructure: "border-amber-500/30 hover:border-amber-500/60",
};

// English only for now: zh.json and ja.json have no translations for these sections yet.
const COPY = {
  eyebrow: "Agent · RAG · Harness",
  browse_parts: "Browse the parts",
  stat_lessons: "lessons",
  stat_parts: "parts",
  stat_offline: "every example and test runs offline, no API key",
  kit_title: "Every lesson folder",
  pillars_title: "Three pillars",
  pillars_desc:
    "Every lesson is tagged with the pillars it teaches. The hard production bugs tend to sit where they meet: an agent that retrieves, a retriever inside a loop, and both inside a harness.",
  demo_label: "the demo loop",
  harness_label: "the harnessed loop (simplified from lesson 02)",
  demo_keeps: "What the demo leaves behind: a messages[] list that only grows.",
  harness_keeps: "What the harness leaves behind: a typed event log that ends in a stop reason (real output from lesson 02).",
  gap_lesson: "Lesson 02 builds this loop step by step",
  credits_title: "Where this comes from",
  credits_body_1:
    "This site's step-by-step format comes from Learn Claude Code, which teaches how an agent harness is built one mechanism at a time. If you have never written an agent loop, start there.",
  credits_body_2:
    "This curriculum picks up where a working demo ends. It expands on that foundation with the production side, drawn from hands-on experience building and running agent and retrieval systems: each lesson shows how the naive version breaks, then ships code and tests that catch it. All lesson content and code here is original.",
  credits_link: "Learn Claude Code on GitHub",
};

// Lessons where each pillar is taught most directly (from each README's "Pillars" line).
const PILLARS = [
  {
    title: "Agent",
    accent: "bg-indigo-500",
    desc: "The model chooses the next step. These lessons keep that loop bounded, its tools permissioned and idempotent, and its plans, hand-offs and approvals as plain data you can inspect and resume.",
    lessons: ["f02", "f04", "f06", "f07", "f08", "f13", "c04"],
  },
  {
    title: "RAG",
    accent: "bg-teal-500",
    desc: "The model answers from your documents. These lessons filter by tenant before ranking, combine keyword and vector search, require answers to cite the sources they were given, and measure retrieval with Recall@k and MRR.",
    lessons: ["f05", "f03", "f09", "p23", "p27", "c03"],
  },
  {
    title: "Harness",
    accent: "bg-rose-500",
    desc: "Everything around the model that makes it safe to run: state, retries, checkpoints, queues, traces, cost limits, security and deploys.",
    lessons: ["f01", "f14", "f15", "p16", "p17", "p22", "p28", "p29"],
  },
];

// Placeholders until they are written; shown as "planned" instead of a line count.
const PLANNED = new Set(["c01", "i01"]);

const LESSON_FILES = [
  { name: "README.md", desc: "why it exists, the naive version and why it fails, the production version, tradeoffs, failure cases" },
  { name: "architecture.md", desc: "mermaid diagrams of the flow, and the naive and production designs side by side" },
  { name: "example.py", desc: "a runnable demo with a scripted model: python example.py" },
  { name: "tests.py", desc: "the README's failure cases, written as unittest tests" },
];

const DEMO_LOOP = [
  "while True:                          # no step, token or time limit",
  "    msg = llm.complete(messages, tools)",
  "    messages.append(msg)",
  "    if not msg.tool_calls:",
  "        break                        # stops only when the model does",
  "    for call in msg.tool_calls:",
  "        result = run_tool(call)      # one exception ends the run",
  "        messages.append(result)",
];

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

// Real output of `python example.py` in lesson 02 (first scenario, details shortened).
const RUN_LOG = [
  ["1", "model_turn", "Look up the plan."],
  ["1", "tool_error", "get_plan -> ERROR: KeyError: 'unknown account acct-71'"],
  ["2", "model_turn", "Typo in the ID; retry."],
  ["2", "tool_result", "get_plan -> annual plan, purchased 2026-10-20"],
  ["3", "model_turn", "Find the refund policy."],
  ["3", "tool_result", "search_docs -> [billing-04] Annual plans can be refunded..."],
  ["4", "model_turn", "Purchased 12 days ago; inside the window."],
  ["4", "stopped", "final_answer"],
];

const RUN_LOG_COLORS: Record<string, string> = {
  tool_error: "text-red-400",
  stopped: "text-emerald-400",
};

function getVersionData(id: string) {
  return versionsData.versions.find((v) => v.id === id);
}

function CodePane({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
      <div className="border-b border-zinc-800 px-4 py-2 font-mono text-xs text-zinc-400">
        {title}
      </div>
      <pre className="overflow-x-auto p-4 text-xs leading-relaxed">
        <code>
          {lines.map((line, i) => {
            const at = line.indexOf("#");
            const code = at === -1 ? line : line.slice(0, at);
            return (
              <span key={i} className="block">
                <span className="text-zinc-200">{code || " "}</span>
                {at !== -1 && <span className="text-zinc-500">{line.slice(at)}</span>}
              </span>
            );
          })}
        </code>
      </pre>
    </div>
  );
}

export default function HomePage() {
  const t = useTranslations("home");
  const tl = useTranslations("layers");
  const locale = useLocale();

  return (
    <div className="flex flex-col gap-20 pb-16">
      {/* Hero: what this is, and what one lesson folder contains */}
      <section className="grid items-center gap-10 pt-8 sm:pt-16 lg:grid-cols-2">
        <div>
          <p className="font-mono text-sm tracking-wide text-[var(--color-text-secondary)]">
            {COPY.eyebrow}
          </p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight sm:text-5xl">
            {t("hero_title")}
          </h1>
          <p className="mt-4 max-w-xl text-base text-[var(--color-text-secondary)] sm:text-lg">
            {t("hero_subtitle")}
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href={`/${locale}/timeline`}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-zinc-900 px-6 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              {t("start")}
              <span aria-hidden="true">&rarr;</span>
            </Link>
            <Link
              href={`/${locale}/layers`}
              className="inline-flex min-h-[44px] items-center rounded-lg border border-[var(--color-border)] px-6 py-3 text-sm font-medium transition-colors hover:bg-[var(--color-bg-secondary)]"
            >
              {COPY.browse_parts}
            </Link>
          </div>
          <p className="mt-6 text-sm text-[var(--color-text-secondary)]">
            {LEARNING_PATH.length} {COPY.stat_lessons} · {LAYERS.length} {COPY.stat_parts} · {COPY.stat_offline}
          </p>
        </div>
        <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
          <div className="border-b border-zinc-800 px-4 py-2 font-mono text-xs text-zinc-400">
            {COPY.kit_title}: part-1-foundations/02-agent-loop/
          </div>
          <ul className="divide-y divide-zinc-800/70">
            {LESSON_FILES.map((file) => (
              <li key={file.name} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:gap-4">
                <span className="w-36 shrink-0 font-mono text-sm text-zinc-100">{file.name}</span>
                <span className="text-sm text-zinc-400">{file.desc}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Three pillars */}
      <section>
        <div className="mb-6">
          <h2 className="text-2xl font-bold sm:text-3xl">{COPY.pillars_title}</h2>
          <p className="mt-2 max-w-3xl text-[var(--color-text-secondary)]">{COPY.pillars_desc}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {PILLARS.map((pillar) => (
            <div
              key={pillar.title}
              className="flex flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-5"
            >
              <div className="flex items-center gap-2">
                <span className={cn("h-2.5 w-2.5 rounded-full", pillar.accent)} />
                <h3 className="text-lg font-semibold">{pillar.title}</h3>
              </div>
              <p className="mt-2 flex-1 text-sm text-[var(--color-text-secondary)]">
                {pillar.desc}
              </p>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {pillar.lessons.map((vid) => (
                  <Link key={vid} href={`/${locale}/${vid}`}>
                    <LayerBadge
                      layer={VERSION_META[vid].layer}
                      className="cursor-pointer transition-opacity hover:opacity-80"
                    >
                      {vid}: {VERSION_META[vid].title}
                    </LayerBadge>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Demo loop vs harnessed loop */}
      <section>
        <div className="mb-6">
          <h2 className="text-2xl font-bold sm:text-3xl">{t("core_pattern")}</h2>
          <p className="mt-2 max-w-3xl text-[var(--color-text-secondary)]">
            {t("core_pattern_desc")}
          </p>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-4">
            <CodePane title={COPY.demo_label} lines={DEMO_LOOP} />
            <div>
              <p className="mb-2 text-sm text-[var(--color-text-secondary)]">{COPY.demo_keeps}</p>
              <MessageFlow />
            </div>
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <CodePane title={COPY.harness_label} lines={HARNESSED_LOOP} />
            <div>
              <p className="mb-2 text-sm text-[var(--color-text-secondary)]">{COPY.harness_keeps}</p>
              <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 p-4 font-mono text-xs leading-relaxed">
                {RUN_LOG.map(([step, kind, detail], i) => (
                  <div key={i} className="whitespace-nowrap">
                    <span className="text-zinc-500">step {step} </span>
                    <span className={cn("inline-block w-24", RUN_LOG_COLORS[kind] ?? "text-zinc-300")}>{kind}</span>
                    <span className="text-zinc-400">{detail}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        <p className="mt-4 text-sm">
          <Link href={`/${locale}/f02`} className="underline underline-offset-4 hover:opacity-80">
            {COPY.gap_lesson} &rarr;
          </Link>
        </p>
      </section>

      {/* Curriculum, grouped by part */}
      <section>
        <div className="mb-6">
          <h2 className="text-2xl font-bold sm:text-3xl">{t("learning_path")}</h2>
          <p className="mt-2 max-w-3xl text-[var(--color-text-secondary)]">
            {t("learning_path_desc")}
          </p>
        </div>
        <div className="flex flex-col gap-10">
          {LAYERS.map((layer) => (
            <div key={layer.id}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className={cn("h-2.5 w-2.5 rounded-full", LAYER_DOT_COLORS[layer.id])} />
                <h3 className="text-lg font-semibold">{layer.label}</h3>
                <span className="text-xs text-[var(--color-text-secondary)]">
                  {layer.versions.length} {t("versions_in_layer")}
                </span>
              </div>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{tl(layer.id)}</p>
              <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {layer.versions.map((vid) => {
                  const meta = VERSION_META[vid];
                  const data = getVersionData(vid);
                  if (!meta) return null;
                  return (
                    <Link
                      key={vid}
                      href={`/${locale}/${vid}`}
                      className={cn(
                        "group block rounded-lg border bg-[var(--color-bg)] p-4 transition-colors",
                        LAYER_BORDER_COLORS[layer.id]
                      )}
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="font-mono text-xs text-[var(--color-text-secondary)]">{vid}</span>
                        {PLANNED.has(vid) ? (
                          <span className="rounded bg-[var(--color-bg-secondary)] px-1.5 py-0.5 text-xs text-[var(--color-text-secondary)]">
                            planned
                          </span>
                        ) : (
                          data && (
                            <span className="text-xs tabular-nums text-[var(--color-text-secondary)]">
                              {data.loc} {t("loc")}
                            </span>
                          )
                        )}
                      </div>
                      <h4 className="mt-1 text-sm font-semibold group-hover:underline">{meta.title}</h4>
                      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{meta.keyInsight}</p>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Credits */}
      <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-6 sm:p-8">
        <h2 className="text-xl font-bold sm:text-2xl">{COPY.credits_title}</h2>
        <p className="mt-3 max-w-3xl text-sm text-[var(--color-text-secondary)] sm:text-base">
          {COPY.credits_body_1}
        </p>
        <p className="mt-3 max-w-3xl text-sm text-[var(--color-text-secondary)] sm:text-base">
          {COPY.credits_body_2}
        </p>
        <a
          href="https://github.com/shareAI-lab/learn-claude-code"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block text-sm font-medium underline underline-offset-4 hover:opacity-80"
        >
          {COPY.credits_link} &rarr;
        </a>
      </section>
    </div>
  );
}
