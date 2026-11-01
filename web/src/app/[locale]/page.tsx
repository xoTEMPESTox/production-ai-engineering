"use client";

import Link from "next/link";
import { useTranslations, useLocale } from "@/lib/i18n";
import { LEARNING_PATH, VERSION_META, LAYERS } from "@/lib/constants";
import { LayerBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import versionsData from "@/data/generated/versions.json";
import { MessageFlow } from "@/components/architecture/message-flow";
import { RunReplay } from "@/components/architecture/run-replay";

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

// Lessons where each pillar is taught most directly (from each README's "Pillars" line).
const PILLARS = [
  { key: "agent", accent: "bg-indigo-500", lessons: ["f02", "f04", "f06", "f07", "f08", "f13", "c04"] },
  { key: "rag", accent: "bg-teal-500", lessons: ["f05", "f03", "f09", "p23", "p27", "c03"] },
  { key: "harness", accent: "bg-rose-500", lessons: ["f01", "f14", "f15", "p16", "p17", "p22", "p28", "p29"] },
];

// Placeholders until they are written; shown as "planned" instead of a line count.
const PLANNED = new Set(["c01", "i01"]);

const LESSON_FILES = [
  { name: "README.md", key: "kit_readme" },
  { name: "architecture.md", key: "kit_architecture" },
  { name: "example.py", key: "kit_example" },
  { name: "tests.py", key: "kit_tests" },
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

// Slow-drifting glows behind the hero, one per pillar colour (the .drift animation is in globals.css).
const GLOWS = [
  { className: "-top-40 -left-32 h-[32rem] w-[32rem] bg-indigo-500/20", style: { "--dx": "60px", "--dy": "40px", "--drift-duration": "16s" } },
  { className: "-top-24 -right-32 h-[28rem] w-[28rem] bg-teal-500/15", style: { "--dx": "-50px", "--dy": "50px", "--drift-duration": "19s" } },
  { className: "top-56 left-[35%] h-[24rem] w-[24rem] bg-rose-500/10", style: { "--dx": "40px", "--dy": "-40px", "--drift-duration": "22s" } },
];

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
      <section className="isolate grid items-center gap-10 pt-8 sm:pt-16 lg:grid-cols-2">
        {/* No positioned ancestor, so this layer spans the full page width from the very top
            (behind the header) instead of the hero's box, and fades out instead of ending in an edge. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[46rem] overflow-hidden [mask-image:linear-gradient(to_bottom,black_55%,transparent)]"
        >
          {GLOWS.map((glow, i) => (
            <div
              key={i}
              className={cn("drift absolute rounded-full blur-3xl", glow.className)}
              style={glow.style as React.CSSProperties}
            />
          ))}
        </div>
        <div className="fade-up">
          <p className="font-mono text-sm tracking-wide text-[var(--color-text-secondary)]">
            {t("eyebrow")}
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
              {t("browse_parts")}
            </Link>
          </div>
          <p className="mt-6 text-sm text-[var(--color-text-secondary)]">
            {LEARNING_PATH.length} {t("stat_lessons")} · {LAYERS.length} {t("stat_parts")} · {t("stat_offline")}
          </p>
        </div>
        <div className="min-w-0 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
          <div className="border-b border-zinc-800 px-4 py-2 font-mono text-xs text-zinc-400">
            {t("kit_title")}: part-1-foundations/02-agent-loop/
          </div>
          <ul className="divide-y divide-zinc-800/70">
            {LESSON_FILES.map((file, i) => (
              <li
                key={file.name}
                className="fade-up flex flex-col gap-1 px-4 py-3 sm:flex-row sm:gap-4"
                style={{ animationDelay: `${0.3 + i * 0.15}s` }}
              >
                <span className="w-36 shrink-0 font-mono text-sm text-zinc-100">{file.name}</span>
                <span className="text-sm text-zinc-400">{t(file.key)}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Three pillars */}
      <section className="reveal">
        <div className="mb-6">
          <h2 className="text-2xl font-bold sm:text-3xl">{t("pillars_title")}</h2>
          <p className="mt-2 max-w-3xl text-[var(--color-text-secondary)]">{t("pillars_desc")}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {PILLARS.map((pillar) => (
            <div
              key={pillar.key}
              className="flex flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-5 transition-all duration-200 hover:-translate-y-1 hover:shadow-lg"
            >
              <div className="flex items-center gap-2">
                <span className={cn("h-2.5 w-2.5 rounded-full", pillar.accent)} />
                <h3 className="text-lg font-semibold">{t(`pillar_${pillar.key}`)}</h3>
              </div>
              <p className="mt-2 flex-1 text-sm text-[var(--color-text-secondary)]">
                {t(`pillar_${pillar.key}_desc`)}
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
      <section className="reveal">
        <div className="mb-6">
          <h2 className="text-2xl font-bold sm:text-3xl">{t("core_pattern")}</h2>
          <p className="mt-2 max-w-3xl text-[var(--color-text-secondary)]">
            {t("core_pattern_desc")}
          </p>
          <p className="mt-2 max-w-3xl text-sm text-[var(--color-text-secondary)]">{t("replay_hint")}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-4">
            <CodePane title={t("demo_label")} lines={DEMO_LOOP} />
            <div>
              <p className="mb-2 text-sm text-[var(--color-text-secondary)]">{t("demo_keeps")}</p>
              <MessageFlow />
            </div>
          </div>
          <RunReplay />
        </div>
        <p className="mt-4 text-sm">
          <Link href={`/${locale}/f02`} className="underline underline-offset-4 hover:opacity-80">
            {t("gap_lesson")} &rarr;
          </Link>
        </p>
      </section>

      {/* Curriculum, grouped by part */}
      <section className="reveal">
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
                        "group block rounded-lg border bg-[var(--color-bg)] p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
                        LAYER_BORDER_COLORS[layer.id]
                      )}
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="font-mono text-xs text-[var(--color-text-secondary)]">{vid}</span>
                        {PLANNED.has(vid) ? (
                          <span className="rounded bg-[var(--color-bg-secondary)] px-1.5 py-0.5 text-xs text-[var(--color-text-secondary)]">
                            {t("planned")}
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
      <section className="reveal rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-6 sm:p-8">
        <h2 className="text-xl font-bold sm:text-2xl">{t("credits_title")}</h2>
        <p className="mt-3 max-w-3xl text-sm text-[var(--color-text-secondary)] sm:text-base">
          {t("credits_body_1")}
        </p>
        <p className="mt-3 max-w-3xl text-sm text-[var(--color-text-secondary)] sm:text-base">
          {t("credits_body_2")}
        </p>
        <a
          href="https://github.com/shareAI-lab/learn-claude-code"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-block text-sm font-medium underline underline-offset-4 hover:opacity-80"
        >
          {t("credits_link")} &rarr;
        </a>
      </section>
    </div>
  );
}
